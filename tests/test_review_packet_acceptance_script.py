from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_review_packet_acceptance.py"
_spec = importlib.util.spec_from_file_location("run_review_packet_acceptance", SCRIPT_PATH)
assert _spec and _spec.loader
acceptance = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(acceptance)


def _ready_packet() -> dict:
    return {
        "quality": {"status": "review_packet_ready", "must_fix_before_delegation": [], "feedback_item_count": 1},
        "feedback_items": [{"id": "fb_001"}],
        "source_bindings": [
            {
                "feedback_item_id": "fb_001",
                "status": "verified",
                "primary_files": ["src/Button.tsx"],
                "style_files": [],
                "supporting_files": [],
                "recommended_first_file": "src/Button.tsx",
            }
        ],
        "implementation_handoff": {
            "implementation_tasks": [
                {
                    "feedback_item_id": "fb_001",
                    "source_binding_status": "verified",
                    "primary_files": ["src/Button.tsx"],
                    "style_files": [],
                    "supporting_files": [],
                    "recommended_first_file": "src/Button.tsx",
                }
            ],
            "files_to_inspect_first": ["src/Button.tsx"],
        },
    }


def test_resolve_packet_path_maps_container_hub_path_to_repo_root(tmp_path: Path) -> None:
    packet = tmp_path / ".hermes/frank_execution/case_x/artifacts/review_packet.json"
    packet.parent.mkdir(parents=True)
    packet.write_text("{}", encoding="utf-8")

    resolved = acceptance.resolve_packet_path(
        "/hub/.hermes/frank_execution/case_x/artifacts/review_packet.json",
        repo_root=tmp_path,
    )

    assert resolved == packet


def test_validate_packet_ready_accepts_verified_source_bindings_and_file_roles() -> None:
    summary = acceptance.validate_packet(_ready_packet(), expect_status="review_packet_ready")

    assert summary["packet_status"] == "review_packet_ready"
    assert summary["feedback_item_count"] == 1
    assert summary["source_binding_statuses"] == ["verified"]
    assert summary["recommended_first_files"] == ["src/Button.tsx"]


def test_validate_packet_ready_rejects_unverified_binding() -> None:
    packet = _ready_packet()
    packet["source_bindings"][0]["status"] = "deferred"

    with pytest.raises(AssertionError, match="expected verified"):
        acceptance.validate_packet(packet, expect_status="review_packet_ready")


def test_validate_packet_ready_rejects_missing_role_structured_handoff() -> None:
    packet = _ready_packet()
    task = packet["implementation_handoff"]["implementation_tasks"][0]
    task.pop("recommended_first_file")

    with pytest.raises(AssertionError, match="recommended_first_file"):
        acceptance.validate_packet(packet, expect_status="review_packet_ready")


def test_validate_packet_ready_rejects_binding_status_mismatch() -> None:
    packet = _ready_packet()
    packet["implementation_handoff"]["implementation_tasks"][0]["source_binding_status"] = "deferred"

    with pytest.raises(AssertionError, match="does not match source binding status"):
        acceptance.validate_packet(packet, expect_status="review_packet_ready")


def test_validate_packet_ready_rejects_missing_source_binding_role_fields() -> None:
    packet = _ready_packet()
    packet["source_bindings"][0].pop("primary_files", None)

    with pytest.raises(AssertionError, match="source binding fb_001 missing list field primary_files"):
        acceptance.validate_packet(packet, expect_status="review_packet_ready")


def test_validate_packet_ready_rejects_source_binding_without_recommended_first_file() -> None:
    packet = _ready_packet()
    packet["source_bindings"][0]["recommended_first_file"] = None

    with pytest.raises(AssertionError, match="source binding fb_001 missing recommended_first_file"):
        acceptance.validate_packet(packet, expect_status="review_packet_ready")


def test_validate_packet_supports_non_ready_expected_status() -> None:
    packet = {
        "quality": {"status": "needs_source_binding", "must_fix_before_delegation": ["source binding missing"]},
        "feedback_items": [{"id": "fb_001"}],
        "source_bindings": [{"feedback_item_id": "fb_001", "status": "deferred"}],
        "implementation_handoff": {"implementation_tasks": []},
    }

    summary = acceptance.validate_packet(packet, expect_status="needs_source_binding")

    assert summary["packet_status"] == "needs_source_binding"
    assert summary["source_binding_statuses"] == ["deferred"]


def test_parse_args_accepts_packet_mode_without_assets() -> None:
    args = acceptance.parse_args(["--packet-path", "/tmp/review_packet.json"])

    assert args.packet_path == "/tmp/review_packet.json"
    assert args.events_asset_id is None
    assert args.audio_asset_id is None


def test_parse_args_accepts_case_mode_without_assets() -> None:
    args = acceptance.parse_args(["--case-id", "case_x"])

    assert args.case_id == "case_x"
    assert args.events_asset_id is None
    assert args.audio_asset_id is None


def test_parse_args_requires_asset_pair_for_submit_mode() -> None:
    with pytest.raises(SystemExit):
        acceptance.parse_args(["--events-asset-id", "events-only"])


def test_summary_json_packet_mode_prints_json_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    packet_path = tmp_path / "review_packet.json"
    packet_path.write_text(json.dumps(_ready_packet()), encoding="utf-8")

    exit_code = acceptance.main(["--packet-path", str(packet_path), "--summary-json"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["packet_status"] == "review_packet_ready"
    assert "ACCEPTANCE PASS" not in captured.out
