from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from services.cases.contract import (
    ProcessContractError,
    collect_process_capabilities,
    compile_process_contract,
)


VALID_PROCESS = """# Demo process

## What this process does

Compiles a minimal process contract for tests.

---

## Steps

### Step 1 — Load source

**Input:** `request_id`

**Processing:** Load and normalize the source request.

**Output (process state):**
```json
{
  "source_text": "..."
}
```

---

### Step 2 — Save file

**Input:** `source_text`

**Resource:** `vault`

**Processing:** Write the rendered file to the vault.

---

## Variables

| Variable | Type | Description |
|---|---|---|
| `request_id` | string | Unique request identifier |
| `source_text` | string | Normalized source text from Step 1 |
"""

PATH_STATE_PROCESS = """# Path state process

## What this process does

Validates that path-typed variables remain process-state IO.

---

## Steps

### Step 1 — Write review

**Input:** `request_id`

**Processing:** Write the initial review note path.

**Output (process state):**
```json
{
  "review_note_path": "~/vault/review.md"
}
```

---

### Step 2 — Resolve review

**Input:** `review_note_path`

**Resource:** `vault`

**Processing:** Update the existing review note in place.

---

## Variables

| Variable | Type | Description |
|---|---|---|
| `request_id` | string | Unique request identifier |
| `review_note_path` | string (path) | Vault path to the generated review note |
"""

CAPABILITY_PROCESS = """# Capability process

## What this process does

Validates explicit skills, resources, and tools.

---

## Steps

### Step 1 — Prepare request

**Executor:** `frank`

**Skill:** `prepare-request`

**Resource:** `vault`

**Tool:** `echo_tool`

**Toolset:** `browser`

**Input:** `request_id`

**Processing:** Prepare the request payload.

**Output (process state):**
```json
{
  "source_text": "..."
}
```

---

## Variables

| Variable | Type | Description |
|---|---|---|
| `request_id` | string | Unique request identifier |
| `source_text` | string | Normalized source text from Step 1 |
"""

FRONTMATTER_PROCESS = """---
title: "Frontmatter process"
dispatch_profile: frank
---

# Frontmatter process

## What this process does

Validates process-level dispatch profile defaults.

---

## Steps

### Step 1 — Prepare source

**Input:** `request_id`

**Processing:** Prepare the source request.

**Output (process state):**
```json
{
  "source_text": "..."
}
```

---

## Variables

| Variable | Type | Description |
|---|---|---|
| `request_id` | string | Unique request identifier |
| `source_text` | string | Normalized source text from Step 1 |
"""


class ProcessContractTests(unittest.TestCase):
    def test_compile_process_contract_builds_slots_roots_and_edges(self) -> None:
        contract = compile_process_contract(VALID_PROCESS, process_path="demo-process")
        self.assertEqual(contract["process_path"], "demo-process")
        self.assertEqual(contract["slot_names"], ["request_id", "source_text"])
        self.assertEqual(contract["root_inputs"], ["request_id"])
        self.assertEqual(contract["producer_map"], {"source_text": 1})
        self.assertEqual(contract["consumer_map"], {"request_id": [1], "source_text": [2]})
        self.assertEqual(
            contract["dag_edges"],
            [{"from": 0, "to": 1, "label": "source_text", "is_skip": False, "variables": ["source_text"]}],
        )

    def test_compile_process_contract_rejects_undeclared_inputs(self) -> None:
        invalid = VALID_PROCESS.replace("`source_text`", "`unknown_input`", 1)
        with self.assertRaisesRegex(ProcessContractError, "unknown_input"):
            compile_process_contract(invalid)

    def test_compile_process_contract_rejects_undeclared_outputs(self) -> None:
        invalid = VALID_PROCESS.replace('"source_text"', '"unknown_output"', 1)
        with self.assertRaisesRegex(ProcessContractError, "unknown_output"):
            compile_process_contract(invalid)

    def test_compile_process_contract_rejects_duplicate_variable_rows(self) -> None:
        invalid = VALID_PROCESS + "| `request_id` | string | Duplicate row |\n"
        with self.assertRaisesRegex(ProcessContractError, "Duplicate variable row"):
            compile_process_contract(invalid)

    def test_compile_process_contract_rejects_multiple_producers(self) -> None:
        invalid = VALID_PROCESS.replace(
            "### Step 2 — Save file\n\n**Input:** `source_text`\n\n**Resource:** `vault`\n\n**Processing:** Write the rendered file to the vault.\n",
            "### Step 2 — Save file\n\n**Input:** `source_text`\n\n**Processing:** Write the rendered file to the vault.\n\n**Output (process state):**\n```json\n{\n  \"source_text\": \"again\"\n}\n```\n",
        )
        with self.assertRaisesRegex(ProcessContractError, "produced by both Step 1 and Step 2"):
            compile_process_contract(invalid)

    def test_compile_process_contract_keeps_path_variables_in_graph_edges(self) -> None:
        contract = compile_process_contract(PATH_STATE_PROCESS, process_path="path-state-process")
        self.assertEqual(contract["consumer_map"], {"request_id": [1], "review_note_path": [2]})
        self.assertEqual(
            contract["dag_edges"],
            [{"from": 0, "to": 1, "label": "review_note_path", "is_skip": False, "variables": ["review_note_path"]}],
        )

    def test_compile_process_contract_preserves_explicit_resources_and_tools(self) -> None:
        tool_dir = str(Path(__file__).resolve().parents[1] / "libs/tools/examples")
        with patch.dict("os.environ", {"TOOL_DIR": tool_dir}, clear=False):
            contract = compile_process_contract(CAPABILITY_PROCESS, process_path="capability-process")
        self.assertEqual(contract["steps"][0]["executor"], "frank")
        self.assertEqual(contract["steps"][0]["skills"], ["prepare-request"])
        self.assertEqual(contract["steps"][0]["action"], "prepare-request")
        self.assertEqual(contract["steps"][0]["resources"], ["vault"])
        self.assertEqual(contract["steps"][0]["tools"], ["echo_tool"])
        self.assertEqual(contract["steps"][0]["toolsets"], ["browser"])

    def test_collect_process_capabilities_includes_process_environment_without_tool_dir(self) -> None:
        process = VALID_PROCESS.replace(
            "---\n\n## Steps",
            "## Required capabilities\n\n### Environment\n- `ELEVENLABS_API_KEY`\n\n---\n\n## Steps",
            1,
        )
        missing_tool_dir = str(Path(__file__).resolve().parents[1] / ".tmp" / "missing-tools")
        with patch.dict("os.environ", {"TOOL_DIR": missing_tool_dir}, clear=False):
            contract = compile_process_contract(process, process_path="env-process")
            capabilities = collect_process_capabilities(contract)

        self.assertEqual(contract["capabilities"]["env_vars"], ["ELEVENLABS_API_KEY"])
        self.assertIn("ELEVENLABS_API_KEY", capabilities["env_vars"])

    def test_collect_process_capabilities_includes_tool_env_requirements(self) -> None:
        process = CAPABILITY_PROCESS.replace("`echo_tool`", "`elevenlabs_stt`")
        tool_dir = str(Path(__file__).resolve().parents[1] / "libs/tools")
        with patch.dict("os.environ", {"TOOL_DIR": tool_dir}, clear=False):
            contract = compile_process_contract(process, process_path="capability-process")
            capabilities = collect_process_capabilities(contract)

        self.assertEqual(capabilities["tools"], ["elevenlabs_stt"])
        self.assertEqual(capabilities["toolsets"], ["browser"])
        self.assertIn("vault", capabilities["resources"])
        self.assertEqual(capabilities["env_vars"], ["ELEVENLABS_API_KEY"])

    def test_review_process_uses_local_whisper_without_api_key_requirement_when_tool_registry_missing(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "base/ops/processes/process-queued-review.md").read_text(encoding="utf-8")
        with patch.dict("os.environ", {"TOOL_DIR": str(Path(self.id()) / "missing-tools")}, clear=False):
            contract = compile_process_contract(source, process_path="base/ops/processes/process-queued-review")
            capabilities = collect_process_capabilities(contract)

        self.assertIn("local_whisper", capabilities["tools"])
        self.assertNotIn("OPENAI_API_KEY", capabilities["env_vars"])

    def test_review_process_local_whisper_requires_internal_stt_url_not_openai_key(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "base/ops/processes/process-queued-review.md").read_text(encoding="utf-8")
        tool_dir = str(Path(__file__).resolve().parents[1] / "libs/tools")
        with patch.dict("os.environ", {"TOOL_DIR": tool_dir}, clear=False):
            contract = compile_process_contract(source, process_path="base/ops/processes/process-queued-review")
            capabilities = collect_process_capabilities(contract)

        self.assertIn("local_whisper", capabilities["tools"])
        self.assertIn("STT_HTTP_URL", capabilities["env_vars"])
        self.assertNotIn("OPENAI_API_KEY", capabilities["env_vars"])
        self.assertNotIn("OpenAI Whisper", source)

    def test_review_transcription_skill_points_workers_at_local_whisper_tool(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "base/ops/skills/transcribe-review-audio.md").read_text(encoding="utf-8")

        self.assertIn("tool: local_whisper", source)
        self.assertIn("from libs.tools.local_whisper import tool", source)
        self.assertIn("STT_HTTP_URL", source)
        self.assertIn("Do not install Whisper/Torch", source)
        self.assertNotIn("tool: openai_whisper", source)
        self.assertNotIn("OpenAI Whisper", source)

    def test_mock_review_process_tracks_current_nine_step_profile_migration(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "base/ops/processes/mock-review-submitted.md").read_text(encoding="utf-8")
        contract = compile_process_contract(source, process_path="base/ops/processes/mock-review-submitted")

        self.assertEqual(contract["dispatch_profile"], "frank")
        self.assertEqual([step["title"] for step in contract["steps"]], [
            "Load review record",
            "Transcribe audio",
            "Resolve component names",
            "Annotate transcript",
            "Extract observations",
            "Bind feedback to codebase context",
            "Write review document",
            "Update review status",
            "Log in daily note",
        ])
        self.assertEqual({step["executor"] for step in contract["steps"]}, {"frank"})
        self.assertEqual([step["assignee"] for step in contract["steps"]], ["frank", *(["worker"] * 8)])
        self.assertNotIn("sophia", {step["executor"].lower() for step in contract["steps"]})
        self.assertNotIn("sophia", {step["assignee"].lower() for step in contract["steps"]})

    def test_review_process_declares_native_capabilities_and_truthful_dependencies(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "base/ops/processes/process-queued-review.md").read_text(encoding="utf-8")
        tool_dir = str(Path(__file__).resolve().parents[1] / "libs/tools")
        with patch.dict("os.environ", {"TOOL_DIR": tool_dir}, clear=False):
            contract = compile_process_contract(source, process_path="base/ops/processes/process-queued-review")
            capabilities = collect_process_capabilities(contract)

        self.assertIn("local_whisper", capabilities["tools"])
        self.assertIn("update_review_status", capabilities["tools"])
        self.assertIn("browser", capabilities["toolsets"])
        self.assertNotIn("OPENAI_API_KEY", capabilities["env_vars"])

        forbidden_resources = {"hub repo", "browser"}
        self.assertTrue(forbidden_resources.isdisjoint(set(capabilities["resources"])))

        self.assertEqual(
            contract["dispatch_profile"],
            "frank",
        )
        self.assertNotIn("sophia", {step["executor"].lower() for step in contract["steps"]})
        self.assertEqual({step["executor"] for step in contract["steps"]}, {"frank"})
        self.assertEqual([step["assignee"] for step in contract["steps"]], ["frank", *(["worker"] * 8)])

        step_1 = contract["steps"][0]
        self.assertEqual(
            step_1["output_variables"],
            [
                "review_id_short",
                "audio_asset_path",
                "events",
            ],
        )
        self.assertEqual(
            contract["root_inputs"],
            [
                "review_id",
                "audio_asset_id",
                "events_asset_id",
                "subject_id",
                "submitted_by",
                "reviewed_at",
                "duration_ms",
            ],
        )
        self.assertFalse([edge for edge in contract["dag_edges"] if edge["from"] == edge["to"]])

        self.assertEqual([step["title"] for step in contract["steps"]], [
            "Load review record",
            "Transcribe audio",
            "Resolve component names",
            "Annotate transcript",
            "Extract observations",
            "Bind feedback to codebase context",
            "Write review document",
            "Update review status",
            "Log in daily note",
        ])
        self.assertEqual(contract["steps"][2]["output_variables"], [
            "component_names",
        ])
        self.assertEqual(contract["steps"][3]["input_items"][4]["name"], "component_names")
        self.assertEqual(contract["steps"][3]["output_variables"], [
            "transcript_note_path",
            "resolved_transcript",
        ])
        self.assertEqual(contract["steps"][4]["input_items"][3]["name"], "component_names")
        self.assertEqual(contract["steps"][4]["output_variables"], [
            "observations",
            "target_events",
            "review_packet_path",
            "review_packet_status",
            "actionability",
            "negative_evidence",
            "implementation_handoff",
            "silent_annotations",
            "filtered_points",
        ])
        self.assertEqual(contract["steps"][5]["input_items"][0]["name"], "observations")
        self.assertEqual(contract["steps"][5]["input_items"][4]["name"], "component_names")
        self.assertEqual(contract["steps"][5]["output_variables"], ["codebase_context"])
        self.assertEqual(contract["steps"][6]["output_variables"], ["review_note_path"])
        self.assertEqual(contract["steps"][6]["input_items"][3]["name"], "component_names")
        self.assertEqual(contract["steps"][6]["input_items"][4]["name"], "codebase_context")
        self.assertEqual(contract["steps"][7]["input_items"][1]["name"], "review_note_path")
        self.assertEqual(contract["steps"][7]["output_variables"], ["review_status_updated"])
        self.assertEqual(contract["steps"][8]["input_items"][3]["name"], "review_note_path")
        self.assertEqual(contract["steps"][8]["input_items"][6]["name"], "review_status_updated")
        self.assertEqual(contract["producer_map"]["component_names"], 3)
        self.assertEqual(contract["producer_map"]["transcript_note_path"], 4)
        self.assertEqual(contract["producer_map"]["observations"], 5)
        self.assertEqual(contract["producer_map"]["codebase_context"], 6)
        self.assertEqual(contract["producer_map"]["review_note_path"], 7)
        self.assertEqual(contract["producer_map"]["review_status_updated"], 8)
        self.assertIn(
            {"from": 7, "to": 8, "label": "review_status_updated", "is_skip": False, "variables": ["review_status_updated"]},
            contract["dag_edges"],
        )

    def test_compile_process_contract_does_not_infer_executor_from_skill(self) -> None:
        process = VALID_PROCESS.replace(
            "### Step 1 — Load source\n\n**Input:** `request_id`\n",
            "### Step 1 — Load source\n\n**Skill:** `prepare-request`\n\n**Input:** `request_id`\n",
            1,
        ).replace(
            "**Output (process state):**\n",
            "**Processing:** Normalize the request.\n\n**Output (process state):**\n",
            1,
        ).replace(
            "### Step 2 — Save file\n\n**Input:** `source_text`\n",
            "### Step 2 — Save file\n\n**Processing:** Write the rendered file.\n\n**Input:** `source_text`\n",
            1,
        )
        contract = compile_process_contract(process, process_path="skill-no-executor")
        self.assertIsNone(contract["steps"][0]["executor"])
        self.assertEqual(contract["steps"][0]["action"], "prepare-request")

    def test_compile_process_contract_rejects_deprecated_non_state_outputs(self) -> None:
        invalid = VALID_PROCESS.replace(
            "**Resource:** `vault`\n\n**Processing:** Write the rendered file to the vault.\n",
            "**Output (vault):** `~/vault/demo.md`\n",
        )
        with self.assertRaisesRegex(ProcessContractError, "Deprecated non-process-state output"):
            compile_process_contract(invalid)

    def test_compile_process_contract_rejects_unknown_tools_when_registry_is_available(self) -> None:
        tool_dir = str(Path(__file__).resolve().parents[1] / "libs/tools/examples")
        invalid = CAPABILITY_PROCESS.replace("`echo_tool`", "`missing_tool`")
        with patch.dict("os.environ", {"TOOL_DIR": tool_dir}, clear=False):
            with self.assertRaisesRegex(ProcessContractError, "unknown tool"):
                compile_process_contract(invalid)

    def test_compile_process_contract_parses_process_dispatch_profile(self) -> None:
        contract = compile_process_contract(FRONTMATTER_PROCESS, process_path="frontmatter-process")
        self.assertEqual(contract["dispatch_profile"], "frank")


if __name__ == "__main__":
    unittest.main()
