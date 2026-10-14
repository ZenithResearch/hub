from __future__ import annotations

import asyncio
import copy
import importlib
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from services.cases.execution_contract import errors, profile_for, runnable
from services.frank.case_pipeline_runner import CasePipelineRunner

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "base/ops/processes/process-queued-review.md").read_text()


@pytest.fixture
def api(tmp_path, monkeypatch):
    db = tmp_path / "cases.db"
    monkeypatch.setenv("CASES_DB_PATH", str(db))
    monkeypatch.setenv("FRANK_EXECUTION_ROOT", str(tmp_path))
    from services.cases import main

    module = importlib.reload(main)
    with TestClient(module.app) as client:
        yield client, module, db, tmp_path


def create(api, key="request"):
    client, module, _, _ = api
    response = client.post(
        "/cases",
        json={
            "queue_message_id": key,
            "process_name": "personal-review",
            "process_path": "process-queued-review",
            "process_source": SOURCE,
            "title": "review",
            "objective": "review",
            "sender": "test",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    roots = {
        name: (0 if payload["contract"]["variables"][name]["type"] == "number" else "root-" + name)
        for name in payload["contract"]["root_inputs"]
    }
    return payload, roots


def claim(api, case, roots, key="claim", owner="worker"):
    response = api[0].post(
        f"/cases/{case['case_id']}/execution/claim",
        json={"request_key": key, "owner": owner, "lease_seconds": 120, "initial_context": roots},
    )
    assert response.status_code == 200, response.text
    return response.json()


def headers(lease):
    return {"x-legacy-run-id": lease["run_id"], "x-legacy-fence": str(lease["fence"])}


def start_step(api, case, lease, number):
    step = case["steps"][number - 1]
    response = api[0].post(
        f"/case-runs/{lease['run_id']}/steps",
        headers=headers(lease),
        json={
            "case_run_id": lease["run_id"],
            "step_id": step["step_id"],
            "step_db_row_id": step["id"],
            "executor_type": "native",
            "idempotency_key": f"{lease['run_id']}:{step['id']}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def outputs(case, number):
    typ_values = {"array": [], "object": {}, "number": 0, "boolean": False, "integer": 0}
    return {
        name: typ_values.get(case["contract"]["variables"][name]["type"], "fixture-" + name)
        for name in case["contract"]["steps"][number - 1]["output_variables"]
    }


def complete(api, case, lease, attempt, number, values=None):
    return api[0].post(
        f"/cases/{case['case_id']}/steps/{case['steps'][number - 1]['id']}/complete-outputs",
        headers=headers(lease),
        json={
            "agent_run_id": attempt["id"],
            "outputs_json": outputs(case, number) if values is None else values,
        },
    )


def expire(api):
    with sqlite3.connect(api[2]) as conn:
        conn.execute("UPDATE legacy_execution_claims SET expires_at=0")


@pytest.mark.parametrize(
    "mutation",
    [
        "cycle", "self", "duplicate", "unknown_type", "missing_edge", "missing_step", "wrong_type",
        "duplicate_edge", "missing_binding", "duplicate_binding", "extra_binding",
    ],
)
def test_invalid_frozen_graphs_rejected_and_old_snapshots_read_only(api, mutation):
    case, roots = create(api)
    contract = case["contract"]
    if mutation == "cycle":
        contract["dag_edges"].append({"from": 1, "to": 0, "variables": []})
    if mutation == "self":
        contract["dag_edges"].append({"from": 0, "to": 0, "variables": []})
    if mutation == "duplicate":
        contract["steps"][1]["step_id"] = "step_1"
    if mutation == "unknown_type":
        contract["variables"]["review_id"]["type"] = "surprise"
    if mutation == "missing_edge":
        contract["dag_edges"] = []
    if mutation == "missing_step":
        contract["dag_edges"].append({"from": 0, "to": 99, "variables": []})
    if mutation == "wrong_type":
        contract["steps"][0]["input_items"][0]["type"] = "boolean"
    if mutation == "duplicate_edge":
        contract["dag_edges"].append(copy.deepcopy(contract["dag_edges"][0]))
    if mutation == "missing_binding":
        contract["dag_edges"][0]["variables"] = []
    if mutation == "duplicate_binding":
        contract["dag_edges"][0]["variables"] *= 2
    if mutation == "extra_binding":
        contract["dag_edges"][0]["variables"].append("review_id")
    assert errors(contract)
    raw = json.dumps(contract)
    with sqlite3.connect(api[2]) as conn:
        conn.execute("UPDATE cases SET contract_json=? WHERE id=?", (raw, case["case_id"]))
        before = conn.execute("SELECT status,completed_at FROM cases").fetchall()
    read = api[0].get(f"/cases/{case['case_id']}")
    assert read.status_code == 200
    assert read.json()["contract"] == contract
    assert read.json()["execution_eligibility"]["eligible"] is False
    write = api[0].put(f"/cases/{case['case_id']}/status", json={"status": "COMPLETED"})
    assert write.status_code == 409
    with sqlite3.connect(api[2]) as conn:
        assert conn.execute("SELECT contract_json FROM cases").fetchone()[0] == raw
        assert conn.execute("SELECT status,completed_at FROM cases").fetchall() == before


def test_new_invalid_process_does_not_activate(api):
    invalid = SOURCE.replace("### Step 2 — Transcribe audio", "### Step 1 — Transcribe audio")
    response = api[0].post(
        "/cases",
        json={
            "queue_message_id": "bad",
            "process_source": invalid,
            "title": "bad",
            "objective": "bad",
            "sender": "test",
        },
    )
    assert response.status_code == 422
    with sqlite3.connect(api[2]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0


def test_parallel_claims_have_one_winner_and_exact_replay(api):
    case, roots = create(api)

    def acquire(key):
        return api[0].post(
            f"/cases/{case['case_id']}/execution/claim",
            json={"request_key": key, "owner": key, "initial_context": roots, "lease_seconds": 120},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(acquire, [f"worker-{i}" for i in range(8)]))
    assert sorted(r.status_code for r in results) == [200] + [409] * 7
    winner = next(r.json() for r in results if r.status_code == 200)
    replay = acquire(winner["request_key"])
    assert replay.status_code == 200
    assert replay.json()["run_id"] == winner["run_id"]
    with sqlite3.connect(api[2]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM case_runs").fetchone()[0] == 1


def test_expiry_creates_new_attempt_and_fences_every_write_path(api):
    case, roots = create(api)
    old = claim(api, case, roots)
    old_step = start_step(api, case, old, 1)
    expire(api)
    new = claim(api, case, roots, key="retry")
    assert new["run_id"] != old["run_id"] and new["fence"] == old["fence"] + 1
    paths = [
        ("put", f"/cases/{case['case_id']}/status", {"status": "COMPLETED"}),
        (
            "put",
            f"/cases/{case['case_id']}/steps/{case['steps'][0]['id']}",
            {"status": "COMPLETED", "agent_run_id": old_step["id"]},
        ),
        (
            "put",
            f"/cases/{case['case_id']}/steps/{case['steps'][0]['id']}/runtime-state",
            {"runtime_state_json": {"status": "completed"}},
        ),
        ("post", f"/cases/{case['case_id']}/slots", {"name": "review_id_short", "value": "lie"}),
        (
            "post",
            f"/cases/{case['case_id']}/steps/{case['steps'][0]['id']}/complete-outputs",
            {"outputs_json": outputs(case, 1), "agent_run_id": old_step["id"]},
        ),
        ("put", f"/case-runs/{old['run_id']}", {"status": "completed"}),
        ("put", f"/step-runs/{old_step['id']}", {"status": "completed"}),
        ("put", f"/cases/{case['case_id']}/dispatch-packet", {"dispatch_packet_json": {}}),
        ("post", f"/cases/{case['case_id']}/logs", {"type": "false", "message": "stale"}),
        (
            "post",
            f"/case-runs/{old['run_id']}/events",
            {"case_run_id": old["run_id"], "type": "done", "message": "stale"},
        ),
        ("post", f"/cases/{case['case_id']}/execution/heartbeat", {"lease_seconds": 120}),
        ("post", f"/cases/{case['case_id']}/execution/finish", {"outcome": "completed"}),
    ]
    for method, path, body in paths:
        response = getattr(api[0], method)(path, headers=headers(old), json=body)
        assert response.status_code == 409, (path, response.text)
    new_step = start_step(api, case, new, 1)
    assert new_step["id"] != old_step["id"]
    assert complete(api, case, new, new_step, 1).status_code == 200
    assert complete(api, case, new, new_step, 1).status_code == 200
    wrong = api[0].post(
        f"/cases/{case['case_id']}/steps/{case['steps'][0]['id']}/complete-outputs",
        headers=headers(new),
        json={
            "outputs_json": outputs(case, 1),
            "agent_run_id": new_step["id"],
            "terminal_status": "FAILED",
        },
    )
    assert wrong.status_code == 409
    assert api[0].get(f"/case-runs/{old['run_id']}").json()["status"] == "blocked"
    assert (
        api[0].get(f"/case-runs/{old['run_id']}/steps").json()["step_runs"][0]["status"] == "failed"
    )


def test_readiness_and_observed_effect_are_required_for_completion(api):
    case, roots = create(api)
    lease = claim(api, case, roots)
    later = case["steps"][3]
    denied = api[0].post(
        f"/case-runs/{lease['run_id']}/steps",
        headers=headers(lease),
        json={
            "case_run_id": lease["run_id"],
            "step_id": later["step_id"],
            "step_db_row_id": later["id"],
        },
    )
    assert denied.status_code == 409
    first = start_step(api, case, lease, 1)
    assert complete(api, case, lease, first, 1).status_code == 200
    second = start_step(api, case, lease, 2)
    assert complete(api, case, lease, second, 2).status_code == 409
    effects = f"/cases/{case['case_id']}/execution/effects"
    base = {"step_run_id": second["id"], "operation": "transcribe"}
    assert (
        api[0].post(effects, headers=headers(lease), json={**base, "state": "unknown"}).status_code
        == 200
    )
    assert complete(api, case, lease, second, 2).status_code == 409
    assert (
        api[0]
        .post(
            effects,
            headers=headers(lease),
            json={**base, "state": "observed", "evidence": {"response_digest": "sha256-example"}},
        )
        .status_code
        == 200
    )
    assert complete(api, case, lease, second, 2).status_code == 200
    state = api[0].get(f"/cases/{case['case_id']}/execution").json()
    assert state["effects"][0]["state"] == "committed"


@pytest.mark.parametrize("effect_state", ["unknown", "observed"])
def test_restart_never_repeats_ambiguous_or_uncommitted_effect(api, effect_state):
    case, roots = create(api)
    lease = claim(api, case, roots)
    first = start_step(api, case, lease, 1)
    assert complete(api, case, lease, first, 1).status_code == 200
    second = start_step(api, case, lease, 2)
    path = f"/cases/{case['case_id']}/execution/effects"
    body = {"step_run_id": second["id"], "operation": "transcribe", "state": "unknown"}
    assert api[0].post(path, headers=headers(lease), json=body).status_code == 200
    if effect_state == "observed":
        assert (
            api[0]
            .post(
                path,
                headers=headers(lease),
                json={
                    **body,
                    "state": "observed",
                    "evidence": {"provider_reference": "response-1"},
                },
            )
            .status_code
            == 200
        )
    expire(api)
    with TestClient(api[1].app) as restarted:
        response = restarted.post(
            f"/cases/{case['case_id']}/execution/claim",
            json={"request_key": "after-restart", "owner": "new-host", "initial_context": roots},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "external_outcome_unknown"
        assert (
            restarted.get(f"/cases/{case['case_id']}/execution").json()["effects"][0]["state"]
            == effect_state
        )


@pytest.mark.parametrize("crash_point", ["before_commit", "after_commit"])
def test_process_death_output_atomicity_and_restart(api, crash_point):
    case, roots = create(api)
    lease = claim(api, case, roots)
    attempt = start_step(api, case, lease, 1)
    body = {"agent_run_id": attempt["id"], "outputs_json": outputs(case, 1)}
    script = """
import json, os
from fastapi.testclient import TestClient
from services.cases import main
mode=os.environ['CRASH_POINT']
with TestClient(main.app) as client:
    if mode=='before_commit':
        original=main._write_slot_value_once
        def crash(*args,**kwargs):
            original(*args,**kwargs)
            os._exit(73)
        main._write_slot_value_once=crash
    else:
        main._broadcast=lambda *args,**kwargs:os._exit(74)
    client.post(os.environ['TEST_PATH'],headers=json.loads(os.environ['TEST_HEADERS']),json=json.loads(os.environ['TEST_BODY']))
"""
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "CASES_DB_PATH": str(api[2]),
        "CRASH_POINT": crash_point,
        "TEST_PATH": f"/cases/{case['case_id']}/steps/{case['steps'][0]['id']}/complete-outputs",
        "TEST_HEADERS": json.dumps(headers(lease)),
        "TEST_BODY": json.dumps(body),
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,  # Cold imports on the external worktree can exceed 30 seconds.
    )
    assert result.returncode == ({"before_commit": 73, "after_commit": 74}[crash_point]), (
        result.stderr
    )
    with TestClient(api[1].app) as restarted:
        detail = restarted.get(f"/cases/{case['case_id']}").json()
        saved = {slot["name"]: slot["value"] for slot in detail["slots"]}
        expected = crash_point == "after_commit"
        assert (detail["steps"][0]["status"] == "COMPLETED") == expected
        assert all((saved[name] is not None) == expected for name in body["outputs_json"])
        attempts = restarted.get(f"/case-runs/{lease['run_id']}/steps").json()["step_runs"]
        assert (attempts[0]["status"] == "completed") == expected
    expire(api)
    retry = claim(api, case, roots, key="after-crash")
    assert retry["run_id"] != lease["run_id"]
    detail = api[0].get(f"/cases/{case['case_id']}").json()
    ready = runnable(
        detail["contract"],
        detail["steps"],
        {
            slot["name"]: json.loads(slot["value"]) if slot["value"] is not None else None
            for slot in detail["slots"]
        },
        profile=profile_for(detail["contract"]),
    )
    assert ("step_1" in {step["step_id"] for step in ready}) != expected


def test_retired_request_does_not_revive_attempt_and_current_fence_cannot_bypass_outputs(api):
    case, roots = create(api)
    lease = claim(api, case, roots)
    for path, body in [
        (f"/cases/{case['case_id']}/status", {"status": "COMPLETED"}),
        (f"/case-runs/{lease['run_id']}", {"status": "completed"}),
    ]:
        assert api[0].put(path, headers=headers(lease), json=body).status_code == 409
    response = api[0].post(
        f"/cases/{case['case_id']}/execution/finish",
        headers=headers(lease),
        json={"outcome": "blocked", "reason": "test pause"},
    )
    assert response.status_code == 200
    response = api[0].post(
        f"/cases/{case['case_id']}/execution/claim",
        json={
            "request_key": "claim",
            "owner": "worker",
            "initial_context": roots,
            "lease_seconds": 120,
        },
    )
    assert response.status_code == 409
    retry = claim(api, case, roots, key="retry")
    assert retry["run_id"] != lease["run_id"]


@pytest.mark.parametrize(
    "packet",
    [
        None,
        "not json",
        {"transcript": {"text": "speech"}, "feedback_items": []},
        {
            "transcript": {"text": "speech"},
            "feedback_items": ["bad"],
            "quality": {"status": "review_packet_ready"},
        },
        {
            "transcript": {"text": "speech"},
            "feedback_items": [{"id": "f", "target_refs": []}],
            "quality": {"status": "review_packet_ready"},
        },
    ],
)
def test_review_writeback_rejects_missing_malformed_or_degraded_packet(tmp_path, packet):
    directory = tmp_path / "case" / "artifacts"
    directory.mkdir(parents=True)
    path = directory / "review_packet.json"
    if packet is not None:
        path.write_text(packet if isinstance(packet, str) else json.dumps(packet))
    requests = []

    def transport(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"review_id": "review-1", "status": requests[-1]["status"]})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            runner = CasePipelineRunner(
                client=client,
                cases_url="http://cases",
                gateway_url="http://gateway",
                stt_url="http://stt",
                execution_root=tmp_path,
            )
            result = await runner.execute_step_8(
                {
                    "case": {"id": "case"},
                    "slots": [
                        {"name": "review_id", "value": '"review-1"'},
                        {
                            "name": "review_note_path",
                            "value": json.dumps(str(directory / "review.md")),
                        },
                    ],
                }
            )
            assert result["review_status_updated"]["status"] == "failed"

    asyncio.run(check())
    assert requests[0]["status"] == "failed"
    assert requests[0]["automaton_event"] == "packet_failed"


def test_claimed_transcription_does_not_retry_ambiguous_provider_failure(tmp_path):
    async def check():
        async with httpx.AsyncClient() as client:
            runner = CasePipelineRunner(
                client=client,
                cases_url="http://cases",
                gateway_url="http://gateway",
                stt_url="http://stt",
                execution_root=tmp_path,
            )
            with patch(
                "services.frank.stt_client.transcribe_audio",
                new=AsyncMock(side_effect=httpx.ReadTimeout("lost response")),
            ) as provider:
                with pytest.raises(httpx.ReadTimeout):
                    await runner._post_stt_transcribe("audio.wav")
                assert provider.await_count == 1
                assert provider.call_args.kwargs["allow_fallback"] is False

    asyncio.run(check())


@pytest.mark.parametrize(
    "mutation",
    [
        "output_object",
        "input_object",
        "outputs_null",
        "edge_variables_null",
        "roots_object",
        "profile_version",
        "steps_missing",
        "steps_extra",
        "steps_reordered",
    ],
)
def test_malformed_contract_or_row_coverage_remains_inspectable(api, mutation):
    case, roots = create(api)
    contract = case["contract"]
    if mutation == "output_object":
        contract["steps"][0]["output_variables"] = [{}]
    if mutation == "input_object":
        contract["steps"][0]["input_items"][0]["name"] = {}
    if mutation == "outputs_null":
        contract["steps"][0]["outputs"] = None
    if mutation == "edge_variables_null":
        contract["dag_edges"][0]["variables"] = None
    if mutation == "roots_object":
        contract["root_inputs"] = [{}]
    if mutation == "profile_version":
        contract["legacy_execution"]["version"] = 999
    with sqlite3.connect(api[2]) as conn:
        conn.execute(
            "UPDATE cases SET contract_json=? WHERE id=?", (json.dumps(contract), case["case_id"])
        )
        if mutation == "steps_missing":
            conn.execute("DELETE FROM case_steps WHERE id=?", (case["steps"][-1]["id"],))
        if mutation == "steps_extra":
            conn.execute(
                "INSERT INTO case_steps (id,case_id,step_id,idx,name,action,status,created_at,updated_at) VALUES ('extra',?,'step_10',9,'extra','extra','READY','now','now')",
                (case["case_id"],),
            )
        if mutation == "steps_reordered":
            conn.execute("UPDATE case_steps SET idx=100 WHERE id=?", (case["steps"][0]["id"],))
    result = api[0].get(f"/cases/{case['case_id']}")
    assert result.status_code == 200, result.text
    assert not result.json()["execution_eligibility"]["eligible"]
    board = api[0].get(f"/cases/{case['case_id']}/board")
    assert board.status_code == 200, board.text
    response = api[0].post(
        f"/cases/{case['case_id']}/execution/claim",
        json={"request_key": "bad", "owner": "test", "initial_context": roots},
    )
    assert response.status_code == 409, response.text


def test_heartbeat_renews_only_current_live_claim_and_payload_replay_is_exact(api):
    case, roots = create(api)
    lease = claim(api, case, roots)
    with sqlite3.connect(api[2]) as conn:
        conn.execute("UPDATE legacy_execution_claims SET expires_at=expires_at-60")
    renewed = api[0].post(
        f"/cases/{case['case_id']}/execution/heartbeat",
        headers=headers(lease),
        json={"lease_seconds": 120},
    )
    assert renewed.status_code == 200
    assert renewed.json()["expires_at"] >= lease["expires_at"]
    changed = api[0].post(
        f"/cases/{case['case_id']}/execution/claim",
        json={"request_key": "claim", "owner": "other", "initial_context": roots},
    )
    assert changed.status_code == 409
    expire(api)
    assert (
        api[0]
        .post(
            f"/cases/{case['case_id']}/execution/heartbeat",
            headers=headers(lease),
            json={"lease_seconds": 120},
        )
        .status_code
        == 409
    )


def test_runner_stops_when_heartbeat_loses_lease(tmp_path):
    async def run():
        parent = type("Parent", (), {"cancel": lambda self: setattr(self, "cancelled", True)})()
        parent.cancelled = False
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(409, json={"detail": "expired"})
            )
        ) as client:
            runner = CasePipelineRunner(
                client=client,
                cases_url="http://cases",
                gateway_url="http://gateway",
                stt_url="http://stt",
                execution_root=tmp_path,
            )
            runner.claim = {"run_id": "old", "fence": 1}
            with patch("services.frank.case_pipeline_runner.asyncio.sleep", new=AsyncMock()):
                await runner._heartbeat("case", parent)
            assert parent.cancelled
            with pytest.raises(RuntimeError, match="claim renewal failed"):
                await runner._request("patch", "http://gateway/review", json={})

    asyncio.run(run())


@pytest.mark.parametrize("ready", [False, True])
def test_native_runner_actual_cases_api_commits_success_or_failure(api, ready, monkeypatch):
    case, roots = create(api)
    roots.update(
        review_id="review_12345678",
        audio_asset_id="audio_1",
        events_asset_id="events_1",
        subject_id="http://fixture.test",
        duration_ms=1000,
    )
    source = api[3] / "codebase" / "view.tsx"
    source.parent.mkdir()
    source.write_text('<button className="review-dismiss" />')
    monkeypatch.setenv(
        "FRANK_SUBJECT_CODEBASE_MAP", f"http://fixture.test={source.parent}" if ready else ""
    )
    events = [
        {
            "id": 1,
            "type": "click",
            "target": "button.review-dismiss",
            "elapsedMs": 200,
            "x": 10,
            "y": 10,
        }
    ]
    transcript = "This button is not centered."
    transcription = {
        "transcript": transcript,
        "words": [
            {"text": word, "start": i * 0.1, "end": i * 0.1 + 0.1}
            for i, word in enumerate(transcript.split())
        ],
    }
    patches = []

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if request.url.host == "cases":
                return await httpx.ASGITransport(app=api[1].app).handle_async_request(request)
            if request.url.path.endswith("/assets/audio_1"):
                return httpx.Response(200, content=b"audio", headers={"content-type": "audio/webm"})
            if request.url.path.endswith("/assets/events_1"):
                return httpx.Response(200, json=events)
            if request.method == "PATCH":
                patches.append(json.loads(request.content))
                return httpx.Response(
                    200, json={"review_id": roots["review_id"], "status": patches[-1]["status"]}
                )
            raise AssertionError(str(request.url))

    async def run():
        async with httpx.AsyncClient(transport=Transport()) as client:
            runner = CasePipelineRunner(
                client=client,
                cases_url="http://cases",
                gateway_url="http://gateway",
                stt_url="http://stt",
                execution_root=api[3],
            )
            with patch(
                "services.frank.stt_client.transcribe_audio",
                new=AsyncMock(return_value=transcription),
            ) as provider:
                result = await runner.run(case["case_id"], {"initial_context": roots})
                assert provider.await_count == 1
                return result

    result = asyncio.run(run())
    expected = "completed" if ready else "failed"
    assert result.status == expected, result
    detail = api[0].get(f"/cases/{case['case_id']}").json()
    assert detail["case"]["status"] == expected.upper()
    assert detail["steps"][7]["status"] == expected.upper()
    assert detail["steps"][8]["status"] == ("COMPLETED" if ready else "PENDING")
    assert patches[0]["status"] == ("processed" if ready else "failed")
    state = api[0].get(f"/cases/{case['case_id']}/execution").json()
    assert not state["lease_active"]
    assert all(effect["state"] == "committed" for effect in state["effects"])
    steps = api[0].get(f"/case-runs/{result.case_run_id}/steps").json()["step_runs"]
    assert len(steps) == (9 if ready else 8)
    assert all(row["status"] == "completed" for row in steps[:-1])
    assert steps[-1]["status"] == expected
    rejected = api[0].post(
        f"/cases/{case['case_id']}/execution/claim",
        json={"request_key": "cannot-revive", "owner": "test", "initial_context": roots},
    )
    assert rejected.status_code == 409


def test_frozen_handler_prerequisites_do_not_come_from_ordinals(api):
    case, roots = create(api)
    lease = claim(api, case, roots)
    first = start_step(api, case, lease, 1)
    assert complete(api, case, lease, first, 1).status_code == 200
    # Step 3 is runnable while Step 2 is still pending; Step 4 must wait for both.
    third = start_step(api, case, lease, 3)
    assert complete(api, case, lease, third, 3).status_code == 200
    fourth = case["steps"][3]
    blocked = api[0].post(
        f"/case-runs/{lease['run_id']}/steps",
        headers=headers(lease),
        json={
            "case_run_id": lease["run_id"],
            "step_id": fourth["step_id"],
            "step_db_row_id": fourth["id"],
        },
    )
    assert blocked.status_code == 409
    contract = case["contract"]
    for edge in contract["dag_edges"]:
        assert edge["from"] != edge["to"]


def test_invalid_legacy_snapshot_stays_unchanged_across_startup_migration(api):
    case, roots = create(api)
    raw = "{malformed"
    with sqlite3.connect(api[2]) as conn:
        conn.execute(
            "UPDATE cases SET contract_json=?,status='RUNNING' WHERE id=?", (raw, case["case_id"])
        )
    with TestClient(api[1].app) as restarted:
        detail = restarted.get(f"/cases/{case['case_id']}").json()
        assert detail["case"]["status"] == "RUNNING"
        assert detail["case"]["contract_json"] == raw
        assert detail["contract"] is None
        assert not detail["execution_eligibility"]["eligible"]
        assert restarted.get(f"/cases/{case['case_id']}/board").status_code == 200


def test_retained_hash_adapter_keeps_snapshot_and_fences_old_writers(api):
    from services.cases.execution_contract import load_profile

    case, roots = create(api)
    legacy = case["contract"]
    legacy.pop("legacy_execution")
    legacy["process_hash"] = load_profile("personal-review-v1")["legacy_source_hashes"][0]
    raw = json.dumps(legacy)
    with sqlite3.connect(api[2]) as conn:
        conn.execute(
            "UPDATE cases SET contract_json=?,dispatch_packet_json=NULL WHERE id=?",
            (raw, case["case_id"]),
        )
    response = api[0].put(f"/cases/{case['case_id']}/status", json={"status": "COMPLETED"})
    assert response.status_code == 409
    claim(api, case, roots)
    detail = api[0].get(f"/cases/{case['case_id']}").json()
    assert detail["case"]["contract_json"] == raw
    assert "legacy_execution" not in detail["contract"]


@pytest.mark.parametrize(
    "mutation",
    [
        "string_refs", "object_refs", "missing_id", "duplicate_id", "string_candidates",
        "string_actionability", "string_bucket", "object_binding_status", "conflicting_binding",
        "foreign_binding", "foreign_review", "malformed_quality",
    ],
)
def test_malformed_ready_packet_cannot_write_processed(tmp_path, mutation):
    packet = {
        "review": {"review_id": "review-1"},
        "transcript": {"text": "Move the button."},
        "feedback_items": [{"id": "f", "target_refs": ["button"]}],
        "source_bindings": [{"feedback_item_id": "f", "status": "verified", "target_refs": ["button"]}],
        "events": {"target_candidates": [{"target_ref": "button"}]},
        "actionability": {"actionable_now": [{"feedback_item_id": "f"}]},
        "quality": {"status": "review_packet_ready"},
    }
    if mutation == "string_refs":
        packet["feedback_items"][0]["target_refs"] = "button"
    if mutation == "object_refs":
        packet["feedback_items"][0]["target_refs"] = [{}]
    if mutation == "missing_id":
        packet["feedback_items"][0].pop("id")
    if mutation == "duplicate_id":
        packet["feedback_items"].append(copy.deepcopy(packet["feedback_items"][0]))
    if mutation == "string_candidates":
        packet["events"]["target_candidates"] = "button"
    if mutation == "string_actionability":
        packet["actionability"] = "actionable"
    if mutation == "string_bucket":
        packet["actionability"]["actionable_now"] = "actionable"
    if mutation == "object_binding_status":
        packet["source_bindings"][0]["status"] = {}
    if mutation == "conflicting_binding":
        packet["source_bindings"].append({"feedback_item_id": "f", "status": "blocked"})
    if mutation == "foreign_binding":
        packet["source_bindings"][0]["feedback_item_id"] = "other-feedback"
    if mutation == "foreign_review":
        packet["review"]["review_id"] = "other-review"
    if mutation == "malformed_quality":
        packet["quality"] = "review_packet_ready"
    directory = tmp_path / "case" / "artifacts"
    directory.mkdir(parents=True)
    (directory / "review_packet.json").write_text(json.dumps(packet))
    sent = []

    async def run():
        def transport(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"status": sent[-1]["status"]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            runner = CasePipelineRunner(
                client=client, cases_url="http://cases", gateway_url="http://gateway",
                stt_url="http://stt", execution_root=tmp_path,
            )
            result = await runner.execute_step_8({"slots": [
                {"name": "review_id", "value": json.dumps("review-1")},
                {"name": "review_note_path", "value": json.dumps(str(directory / "review.md"))},
            ]})
            assert result["review_status_updated"]["status"] == "failed"

    asyncio.run(run())
    assert len(sent) == 1
    assert sent[0]["status"] == "failed"
    assert sent[0]["automaton_event"] == "packet_failed"


@pytest.mark.parametrize("operation", ["outputs", "heartbeat", "effect", "finish"])
def test_lease_expiry_after_admission_rolls_back_entire_transaction(api, monkeypatch, operation):
    case, roots = create(api)
    lease = claim(api, case, roots)
    attempt = start_step(api, case, lease, 1)
    if operation == "effect":
        assert complete(api, case, lease, attempt, 1).status_code == 200
        attempt = start_step(api, case, lease, 2)
    with sqlite3.connect(api[2]) as conn:
        before = tuple(conn.iterdump())
    clock = [lease["expires_at"] - 5]
    monkeypatch.setattr(api[1].execution_store.time, "time", lambda: clock[0])
    if operation == "outputs":
        original = api[1]._write_slot_value_once

        def expire_after_write(*args, **kwargs):
            result = original(*args, **kwargs)
            clock[0] = lease["expires_at"] + 601
            return result

        monkeypatch.setattr(api[1], "_write_slot_value_once", expire_after_write)
        response = complete(api, case, lease, attempt, 1)
    else:
        original = api[1].now

        def expire_during_mutation():
            result = original()
            clock[0] = lease["expires_at"] + 601
            return result

        monkeypatch.setattr(api[1], "now", expire_during_mutation)
        endpoint, body = {
            "heartbeat": ("heartbeat", {"lease_seconds": 120}),
            "effect": ("effects", {"step_run_id": attempt["id"], "operation": "transcribe", "state": "unknown"}),
            "finish": ("finish", {"outcome": "blocked", "reason": "slow finish"}),
        }[operation]
        response = api[0].post(
            f"/cases/{case['case_id']}/execution/{endpoint}", headers=headers(lease), json=body,
        )
    assert response.status_code == 409, response.text
    with sqlite3.connect(api[2]) as conn:
        assert tuple(conn.iterdump()) == before


def test_compiled_multi_root_join_waits_for_both_committed_parents():
    from services.cases.contract import compile_process_contract

    source = """# Two-root join
## Steps
### Step 1 — Left root
**Input:** `left_input`
**Processing:** Produce left output.
**Output (process state):**
```json
{
  "left": "value"
}
```
### Step 2 — Right root
**Input:** `right_input`
**Processing:** Produce right output.
**Output (process state):**
```json
{
  "right": "value"
}
```
### Step 3 — Join
**Input:** `left`, `right`
**Processing:** Join both committed branches.
## Variables
| Variable | Type | Description |
|---|---|---|
| `left_input` | string | Left root |
| `right_input` | string | Right root |
| `left` | string | Left output |
| `right` | string | Right output |
"""
    contract = compile_process_contract(source)
    steps = [{"step_id": spec["step_id"], "idx": i, "status": "PENDING"} for i, spec in enumerate(contract["steps"])]
    values = {"left_input": "a", "right_input": "b"}
    assert {step["step_id"] for step in runnable(contract, steps, values)} == {"step_1", "step_2"}
    steps[1]["status"] = "COMPLETED"
    values["right"] = "b"
    assert [step["step_id"] for step in runnable(contract, steps, values)] == ["step_1"]
    # Populated output bytes alone cannot satisfy a dependency with an active parent.
    values["left"] = "a"
    steps[0]["status"] = "RUNNING"
    assert runnable(contract, steps, values) == []
    steps[0]["status"] = "COMPLETED"
    assert [step["step_id"] for step in runnable(contract, steps, values)] == ["step_3"]


@pytest.mark.parametrize("mutation", ["version", "bool_version", "dependencies", "handler"])
def test_active_claim_cannot_mutate_an_altered_frozen_handler_resource(api, mutation):
    case, roots = create(api)
    lease = claim(api, case, roots)
    contract = case["contract"]
    if mutation in {"version", "bool_version"}:
        contract["legacy_execution"]["version"] = 999 if mutation == "version" else True
    if mutation == "dependencies":
        contract["legacy_execution"]["dependencies"]["step_9"] = []
    if mutation == "handler":
        contract["legacy_execution"]["handlers"]["step_9"]["handler"] = "unsupported"
    raw = json.dumps(contract)
    with sqlite3.connect(api[2]) as conn:
        conn.execute("UPDATE cases SET contract_json=? WHERE id=?", (raw, case["case_id"]))
        before = tuple(conn.iterdump())
    for endpoint, body in [
        ("heartbeat", {"lease_seconds": 120}),
        ("finish", {"outcome": "blocked"}),
    ]:
        response = api[0].post(
            f"/cases/{case['case_id']}/execution/{endpoint}", headers=headers(lease), json=body,
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "legacy_contract_read_only"
    response = api[0].put(
        f"/cases/{case['case_id']}/steps/{case['steps'][0]['id']}/runtime-state",
        headers=headers(lease), json={"runtime_state_json": {"status": "running"}},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "legacy_contract_read_only"
    with sqlite3.connect(api[2]) as conn:
        assert tuple(conn.iterdump()) == before
    detail = api[0].get(f"/cases/{case['case_id']}").json()
    assert not detail["execution_eligibility"]["eligible"]
    assert detail["case"]["contract_json"] == raw


def test_fence_change_after_admission_cannot_commit_outputs(api, monkeypatch):
    case, roots = create(api)
    lease = claim(api, case, roots)
    attempt = start_step(api, case, lease, 1)
    with sqlite3.connect(api[2]) as conn:
        before = tuple(conn.iterdump())
    original = api[1]._write_slot_value_once

    def change_fence_during_write(conn, *args, **kwargs):
        original(conn, *args, **kwargs)
        conn.execute("UPDATE legacy_execution_claims SET fence=fence+1 WHERE case_id=?", (case["case_id"],))

    monkeypatch.setattr(api[1], "_write_slot_value_once", change_fence_during_write)
    response = complete(api, case, lease, attempt, 1)
    assert response.status_code == 409, response.text
    with sqlite3.connect(api[2]) as conn:
        assert tuple(conn.iterdump()) == before


def test_rejected_native_output_cannot_commit_logs_after_lease_expiry(api, monkeypatch):
    case, roots = create(api)
    lease = claim(api, case, roots)
    attempt = start_step(api, case, lease, 1)
    with sqlite3.connect(api[2]) as conn:
        before = tuple(conn.iterdump())
    clock = [lease["expires_at"] - 5]
    monkeypatch.setattr(api[1].execution_store.time, "time", lambda: clock[0])
    original = api[1]._matches_variable_type

    def expire_during_validation(*args):
        clock[0] = lease["expires_at"] + 1
        return original(*args)

    monkeypatch.setattr(api[1], "_matches_variable_type", expire_during_validation)
    invalid = outputs(case, 1)
    invalid["review_id_short"] = 123
    response = complete(api, case, lease, attempt, 1, invalid)
    assert response.status_code == 422, response.text
    with sqlite3.connect(api[2]) as conn:
        assert tuple(conn.iterdump()) == before
