from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.frank.review_packet import (
    _group_source_reference_files,
    build_review_packet,
    build_source_bindings,
    build_transcript_segments,
    extract_feedback_items,
    normalize_review_events,
    packet_quality,
    write_review_packet,
)


class ReviewPacketTests(unittest.TestCase):
    def test_build_review_packet_includes_review_metadata(self) -> None:
        packet = build_review_packet(
            {
                "review_id": "fd6269ef-cc22-45f8-8c2c-68e41279f68c",
                "review_id_short": "fd6269ef",
                "subject_id": "http://localhost:3000/?reviewMode=on",
                "submitted_by": "Franklin22",
                "reviewed_at": "2026-05-06T05:11:59.722Z",
                "duration_ms": 49492,
                "transcript": "this X is not centered.",
                "audio_offset_ms": 0,
                "words": [],
                "audio_asset_path": "/tmp/audio.webm",
                "transcript_note_path": "/tmp/transcript.md",
                "review_note_path": "/tmp/review.md",
            },
            case_dir=Path("/tmp/case"),
            target_candidates=[{"target_ref": "button.x"}],
            segments=[],
            feedback_items=[],
        )

        self.assertEqual(packet["schema_version"], 2)
        self.assertIn("actionability", packet)
        self.assertIn("negative_evidence", packet)
        self.assertIn("implementation_handoff", packet)
        self.assertEqual(packet["review"]["review_id_short"], "fd6269ef")
        self.assertEqual(packet["transcript"]["text"], "this X is not centered.")
        self.assertEqual(packet["events"]["target_candidates"][0]["target_ref"], "button.x")

    def test_packet_quality_transcript_only_when_no_feedback_items(self) -> None:
        packet = {"transcript": {"text": "hello"}, "feedback_items": [], "events": {"target_candidates": []}}

        quality = packet_quality(packet)

        self.assertEqual(quality["status"], "transcript_only")
        self.assertIn("no feedback items extracted", quality["warnings"])

    def test_packet_quality_ready_when_feedback_items_exist(self) -> None:
        packet = {
            "transcript": {"text": "hello"},
            "feedback_items": [{"id": "fb_001", "target_refs": ["button.x"]}],
            "source_bindings": [{"feedback_item_id": "fb_001", "status": "verified", "target_refs": ["button.x"]}],
        }

        quality = packet_quality(packet)

        self.assertEqual(quality["status"], "review_packet_ready")
        self.assertEqual(quality["feedback_item_count"], 1)

    def test_write_review_packet_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "review_packet.json"
            write_review_packet(path, {"schema_version": 1})

            self.assertTrue(path.exists())
            self.assertIn('"schema_version": 1', path.read_text())

    def test_normalize_events_reads_target_field(self) -> None:
        events = [
            {"id": 1, "type": "click", "target": "button.zh-notification-card__dismiss", "elapsedMs": 100, "x": 73, "y": 51},
            {"id": 2, "type": "pointer-move", "target": "span.zh-notification-dot", "elapsedMs": 300, "x": 42, "y": 15},
        ]

        normalized = normalize_review_events(events)

        refs = [item["target_ref"] for item in normalized["target_candidates"]]
        self.assertIn("button.zh-notification-card__dismiss", refs)
        self.assertIn("span.zh-notification-dot", refs)

    def test_normalize_events_dedupes_target_candidates_and_counts_types(self) -> None:
        events = [
            {"id": 1, "type": "click", "target": "button.x", "elapsedMs": 100, "x": 1, "y": 2},
            {"id": 2, "type": "pointer-move", "target": "button.x", "elapsedMs": 200, "x": 3, "y": 4},
        ]

        normalized = normalize_review_events(events)

        self.assertEqual(len(normalized["target_candidates"]), 1)
        self.assertEqual(normalized["target_candidates"][0]["event_count"], 2)
        self.assertEqual(normalized["type_counts"], {"click": 1, "pointer-move": 1})

    def test_normalize_events_preserves_stroke_groups(self) -> None:
        events = [
            {"id": 3, "type": "stroke-started", "strokeId": "s1", "elapsedMs": 400},
            {"id": 4, "type": "stroke-point", "strokeId": "s1", "elapsedMs": 410, "x": 70, "y": 50},
            {"id": 5, "type": "stroke-ended", "strokeId": "s1", "elapsedMs": 500},
        ]

        normalized = normalize_review_events(events)

        self.assertEqual(normalized["stroke_groups"][0]["stroke_id"], "s1")
        self.assertEqual(normalized["stroke_groups"][0]["point_count"], 1)

    def test_segment_transcript_attaches_nearby_targets(self) -> None:
        words = [
            {"text": "This", "start_ms": 0, "end_ms": 200},
            {"text": "X", "start_ms": 200, "end_ms": 400},
            {"text": "is", "start_ms": 400, "end_ms": 600},
            {"text": "not", "start_ms": 600, "end_ms": 800},
            {"text": "centered.", "start_ms": 800, "end_ms": 1000},
        ]
        normalized = normalize_review_events([
            {"id": 1, "type": "click", "target": "button.x", "elapsedMs": 500, "x": 10, "y": 12}
        ])

        segments = build_transcript_segments("This X is not centered.", words, normalized)

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["id"], "seg_001")
        self.assertEqual(segments[0]["nearby_target_refs"], ["button.x"])
        self.assertEqual(segments[0]["nearby_event_ids"], [1])

    def test_segment_transcript_does_not_attach_target_when_only_aggregate_span_overlaps(self) -> None:
        words = [
            {"text": "Middle", "start_ms": 10000, "end_ms": 10100},
            {"text": "statement.", "start_ms": 10100, "end_ms": 10200},
        ]
        normalized = normalize_review_events(
            [
                {"id": 1, "type": "click", "target": "button.repeated", "elapsedMs": 0, "x": 1, "y": 1},
                {"id": 2, "type": "click", "target": "button.repeated", "elapsedMs": 20000, "x": 1, "y": 1},
            ]
        )

        segments = build_transcript_segments("Middle statement.", words, normalized, window_ms=1000)

        self.assertEqual(segments[0]["nearby_target_refs"], [])
        self.assertEqual(segments[0]["nearby_event_ids"], [])

    def test_extract_feedback_items_detects_not_centered_and_attaches_target(self) -> None:
        segments = [
            {
                "id": "seg_001",
                "text": "This X is not centered.",
                "nearby_target_refs": ["button.zh-notification-card__dismiss"],
                "nearby_event_ids": [1],
            }
        ]

        items = extract_feedback_items(segments, [])

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "layout")
        self.assertEqual(items[0]["target_refs"], ["button.zh-notification-card__dismiss"])
        self.assertIn("not centered", items[0]["reviewer_quote"])

    def test_extract_feedback_items_detects_notification_number_state(self) -> None:
        segments = [
            {
                "id": "seg_001",
                "text": "When this tray is full, this needs to show a number.",
                "nearby_target_refs": ["span.zh-notification-dot"],
                "nearby_event_ids": [2],
            },
            {
                "id": "seg_002",
                "text": "The problem right now is it only shows the number when we exit everything.",
                "nearby_target_refs": ["button.zh-notification-trigger"],
                "nearby_event_ids": [3],
            },
        ]

        items = extract_feedback_items(segments, [])

        self.assertGreaterEqual(len(items), 2)
        self.assertIn("number", items[0]["normalized_claim"].lower())
        self.assertEqual(items[0]["type"], "missing_state")

    def test_extract_feedback_items_detects_deictic_rejection_with_stroke_evidence(self) -> None:
        segments = [
            {
                "id": "seg_001",
                "text": "Okay, see, like we don't want that or these little blur is here.",
                "nearby_target_refs": ["canvas"],
                "nearby_event_ids": [157, 158, 160],
                "nearby_stroke_ids": ["stroke_1"],
            }
        ]

        items = extract_feedback_items(segments, [])

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "visual_artifact")
        self.assertEqual(items[0]["target_refs"], ["canvas"])
        self.assertEqual(items[0]["evidence"]["stroke_ids"], ["stroke_1"])
        self.assertIn("don't want", items[0]["reviewer_quote"])

    def test_normalized_events_preserve_page_scroll_and_stroke_timeline(self) -> None:
        normalized = normalize_review_events(
            [
                {
                    "id": 1,
                    "type": "session-start",
                    "elapsedMs": 0,
                    "url": "https://swrl-ui.vercel.app/",
                    "title": "swirl-ui",
                    "scrollX": 0,
                    "scrollY": 10,
                    "viewportWidth": 1512,
                    "viewportHeight": 801,
                },
                {
                    "id": 2,
                    "type": "navigation",
                    "elapsedMs": 1200,
                    "trigger": "pushstate",
                    "fromUrl": "https://swrl-ui.vercel.app/",
                    "fromTitle": "swirl-ui",
                    "toUrl": "https://swrl-ui.vercel.app/grading",
                    "toTitle": "grading",
                    "scrollX": 0,
                    "scrollY": 0,
                },
                {"id": 3, "type": "scroll", "elapsedMs": 1800, "scrollX": 0, "scrollY": 320, "url": "https://swrl-ui.vercel.app/grading"},
                {"id": 4, "type": "stroke-point", "strokeId": "s1", "elapsedMs": 2000, "x": 10, "y": 20},
            ]
        )

        self.assertEqual(normalized["page_events"][0]["url"], "https://swrl-ui.vercel.app/")
        self.assertEqual(normalized["page_events"][1]["to_url"], "https://swrl-ui.vercel.app/grading")
        self.assertEqual(normalized["scroll_events"][0]["scroll_y"], 320)
        self.assertEqual(normalized["stroke_groups"][0]["event_ids"], [4])

    def test_extract_feedback_items_returns_empty_without_feedback_language(self) -> None:
        items = extract_feedback_items(
            [{"id": "seg_001", "text": "I am looking at the page.", "nearby_target_refs": [], "nearby_event_ids": []}],
            [],
        )

        self.assertEqual(items, [])


    def test_build_review_packet_flags_missing_source_binding_for_delegation(self) -> None:
        packet = build_review_packet(
            {"review_id": "r1", "transcript": "The X should move.", "words": []},
            case_dir=Path("/tmp/case"),
            feedback_items=[
                {
                    "id": "fb_001",
                    "type": "layout",
                    "reviewer_quote": "The X should move.",
                    "normalized_claim": "The X should move.",
                    "target_refs": ["button.x"],
                    "evidence": {"transcript_segment_ids": ["seg_001"], "event_ids": [1]},
                    "severity": "medium",
                    "confidence": 0.72,
                }
            ],
            source_bindings=[],
        )

        self.assertEqual(packet["schema_version"], 2)
        self.assertEqual(packet["quality"]["status"], "needs_source_binding")
        self.assertIn("source binding missing", " ".join(packet["quality"]["must_fix_before_delegation"]))
        self.assertEqual(packet["actionability"]["design_preference"][0]["feedback_item_id"], "fb_001")
        self.assertEqual(packet["implementation_handoff"]["implementation_tasks"][0]["source_binding_status"], "missing")

    def test_build_deferred_source_bindings_preserves_feedback_ids_and_reasons(self) -> None:
        bindings = build_source_bindings(
            feedback_items=[{"id": "fb_001", "target_refs": ["button.x"]}],
            component_names=[{"component": "button.x", "selectors": ["button.x"], "source": "event.target"}],
            subject_id="http://localhost:3000/?reviewMode=on",
            codebase_root=None,
        )

        self.assertEqual(bindings[0]["feedback_item_id"], "fb_001")
        self.assertEqual(bindings[0]["status"], "deferred")
        self.assertIn("source binding unavailable", bindings[0]["reason"])
        self.assertIn("button.x", bindings[0]["selectors"])
        self.assertTrue(bindings[0]["open_questions"])

    def test_verified_source_bindings_rank_source_files_and_dedupe_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src/components").mkdir(parents=True)
            (root / "src/styles").mkdir(parents=True)
            (root / "README.md").write_text("zh-notification-card__dismiss should not rank first")
            (root / "src/styles/LandingPage.css").write_text(".zh-notification-card__dismiss {}\n.zh-notification-card__dismiss:hover {}\n")
            (root / "src/components/ReviewCapturePanel.tsx").write_text(
                "export function ReviewCapturePanel() { return <button className=\"zh-notification-card__dismiss\" /> }\n"
            )

            bindings = build_source_bindings(
                feedback_items=[{"id": "fb_001", "target_refs": ["button.zh-notification-card__dismiss"]}],
                component_names=[{"component": "dismiss", "selectors": ["button.zh-notification-card__dismiss"], "source": "event.target"}],
                subject_id="http://localhost:3000/?reviewMode=on",
                codebase_root=str(root),
            )

        binding = bindings[0]
        self.assertEqual(binding["status"], "verified")
        files = [Path(path).name for path in binding["files_to_inspect_first"]]
        self.assertEqual(files[0], "ReviewCapturePanel.tsx")
        self.assertIn("LandingPage.css", files)
        self.assertNotIn("README.md", files[:2])
        self.assertEqual(files.count("LandingPage.css"), 1)

    def test_source_reference_roles_are_grouped_for_handoff(self) -> None:
        refs = [
            {"path": "/repo/src/components/Button.tsx", "relative_path": "src/components/Button.tsx"},
            {"path": "/repo/src/components/Button.css", "relative_path": "src/components/Button.css"},
            {"path": "/repo/README.md", "relative_path": "README.md"},
            {"path": "/repo/src/components/Button.tsx", "relative_path": "src/components/Button.tsx"},
        ]

        roles = _group_source_reference_files(refs)

        self.assertEqual(roles["primary_files"], ["/repo/src/components/Button.tsx"])
        self.assertEqual(roles["style_files"], ["/repo/src/components/Button.css"])
        self.assertEqual(roles["supporting_files"], ["/repo/README.md"])

    def test_verified_source_bindings_include_file_role_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src/components").mkdir(parents=True)
            (root / "src/styles").mkdir(parents=True)
            (root / "README.md").write_text("zh-notification-card__dismiss should not rank first")
            (root / "src/styles/LandingPage.css").write_text(".zh-notification-card__dismiss {}\n")
            (root / "src/components/ReviewCapturePanel.tsx").write_text(
                "export function ReviewCapturePanel() { return <button className=\"zh-notification-card__dismiss\" /> }\n"
            )

            bindings = build_source_bindings(
                feedback_items=[{"id": "fb_001", "target_refs": ["button.zh-notification-card__dismiss"]}],
                component_names=[{"component": "dismiss", "selectors": ["button.zh-notification-card__dismiss"], "source": "event.target"}],
                subject_id="http://localhost:3000/?reviewMode=on",
                codebase_root=str(root),
            )

        binding = bindings[0]
        self.assertEqual([Path(path).name for path in binding["primary_files"]], ["ReviewCapturePanel.tsx"])
        self.assertEqual([Path(path).name for path in binding["style_files"]], ["LandingPage.css"])
        self.assertEqual([Path(path).name for path in binding["supporting_files"]], ["README.md"])
        self.assertEqual(binding["files_to_inspect_first"][0], binding["primary_files"][0])
        self.assertEqual(Path(binding["recommended_first_file"]).name, "ReviewCapturePanel.tsx")

    def test_verified_source_bindings_make_packet_ready(self) -> None:
        binding = {
            "feedback_item_id": "fb_001",
            "status": "verified",
            "target_refs": ["button.x"],
            "selectors": ["button.x"],
            "references": [
                {"path": "src/Button.tsx", "relative_path": "src/Button.tsx", "lines": "1-5"},
                {"path": "src/Button.css", "relative_path": "src/Button.css", "lines": "1-5"},
            ],
            "files_to_inspect_first": ["src/Button.tsx", "src/Button.css"],
            "primary_files": ["src/Button.tsx"],
            "style_files": ["src/Button.css"],
            "supporting_files": [],
        }
        packet = build_review_packet(
            {"review_id": "r1", "transcript": "The X should move.", "words": []},
            case_dir=Path("/tmp/case"),
            feedback_items=[
                {
                    "id": "fb_001",
                    "type": "layout",
                    "reviewer_quote": "The X should move.",
                    "normalized_claim": "The X should move.",
                    "target_refs": ["button.x"],
                    "evidence": {"transcript_segment_ids": ["seg_001"], "event_ids": [1]},
                    "severity": "medium",
                    "confidence": 0.72,
                }
            ],
            source_bindings=[binding],
        )

        self.assertEqual(packet["quality"]["status"], "review_packet_ready")
        self.assertEqual(packet["actionability"]["actionable_now"][0]["feedback_item_id"], "fb_001")
        task = packet["implementation_handoff"]["implementation_tasks"][0]
        self.assertEqual(task["feedback_item_id"], "fb_001")
        self.assertIn("The X should move", task["problem"])
        self.assertTrue(any("Do not create ISS notes" in rule for rule in task["do_not_do"]))
        self.assertEqual(task["source_binding_status"], "verified")
        self.assertEqual(task["files_to_inspect_first"], ["src/Button.tsx", "src/Button.css"])
        self.assertEqual(task["primary_files"], ["src/Button.tsx"])
        self.assertEqual(task["style_files"], ["src/Button.css"])
        self.assertEqual(task["supporting_files"], [])
        self.assertEqual(task["recommended_first_file"], "src/Button.tsx")

    def test_feedback_without_target_needs_human_clarification(self) -> None:
        packet = build_review_packet(
            {"review_id": "r1", "transcript": "This should move.", "words": []},
            case_dir=Path("/tmp/case"),
            feedback_items=[
                {
                    "id": "fb_001",
                    "type": "layout",
                    "reviewer_quote": "This should move.",
                    "normalized_claim": "This should move.",
                    "target_refs": [],
                    "evidence": {"transcript_segment_ids": ["seg_001"], "event_ids": []},
                    "severity": "medium",
                    "confidence": 0.55,
                }
            ],
        )

        self.assertEqual(packet["quality"]["status"], "needs_human_review")
        self.assertEqual(packet["actionability"]["needs_human_clarification"][0]["feedback_item_id"], "fb_001")
        self.assertFalse(packet["actionability"]["actionable_now"])

    def test_silent_and_filtered_points_are_negative_evidence_not_tasks(self) -> None:
        packet = build_review_packet(
            {"review_id": "r1", "transcript": "I am looking around.", "words": []},
            case_dir=Path("/tmp/case"),
            feedback_items=[],
            silent_annotations=[{"stroke_id": "s1", "start_ms": 100, "end_ms": 200}],
            filtered_points=[{"id": "frag_1", "reason": "fragment", "text": "this guy"}],
        )

        self.assertEqual(len(packet["negative_evidence"]["silent_annotations"]), 1)
        self.assertEqual(len(packet["negative_evidence"]["filtered_points"]), 1)
        self.assertEqual(len(packet["actionability"]["discarded_or_filtered"]), 2)
        self.assertEqual(packet["implementation_handoff"]["implementation_tasks"], [])

    def test_repeated_target_gap_adds_no_task_when_no_feedback_target_evidence(self) -> None:
        words = [
            {"text": "Middle", "start_ms": 10000, "end_ms": 10100},
            {"text": "should", "start_ms": 10100, "end_ms": 10200},
            {"text": "move.", "start_ms": 10200, "end_ms": 10300},
        ]
        normalized = normalize_review_events(
            [
                {"id": 1, "type": "click", "target": "button.repeated", "elapsedMs": 0},
                {"id": 2, "type": "click", "target": "button.repeated", "elapsedMs": 20000},
            ]
        )
        segments = build_transcript_segments("Middle should move.", words, normalized, window_ms=1000)
        items = extract_feedback_items(segments, [])
        packet = build_review_packet({"review_id": "r1", "transcript": "Middle should move.", "words": words}, case_dir=Path("/tmp/case"), normalized_events=normalized, segments=segments, feedback_items=items)

        self.assertEqual(segments[0]["nearby_target_refs"], [])
        self.assertEqual(packet["quality"]["status"], "needs_human_review")
        self.assertTrue(packet["implementation_handoff"]["open_questions"] or packet["quality"]["must_fix_before_delegation"])


if __name__ == "__main__":
    unittest.main()
