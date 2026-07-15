from __future__ import annotations

import unittest

from photometry_app.core.benchmarking import BenchmarkRecorder, reset_global_benchmark_recorder


class BenchmarkRecorderTest(unittest.TestCase):
    def test_records_nested_sections_and_frame_summary(self) -> None:
        recorder = BenchmarkRecorder(enabled=True)
        recorder.set_scenario("unit", metadata={"purpose": "test"})
        frame_number = recorder.begin_frame(phase="measured", frame_index=3, interaction_state="drag")
        self.assertEqual(frame_number, 1)

        outer = recorder.start_section("outer")
        inner = recorder.start_section("inner")
        recorder.stop_section(inner, metadata={"cache_status": "miss"})
        recorder.stop_section(outer)
        recorder.finish_frame(total_seconds=0.042, interaction_state="drag", metadata={"visible_objects": 12})

        payload = recorder.to_dict(include_samples=True)
        section_paths = {row["path"] for row in payload["section_summaries"]}
        self.assertIn("outer", section_paths)
        self.assertIn("outer/inner", section_paths)
        frame_summaries = payload["frame_summaries"]
        self.assertEqual(frame_summaries[0]["scenario"], "unit")
        self.assertEqual(frame_summaries[0]["count"], 1)
        self.assertAlmostEqual(frame_summaries[0]["avg_milliseconds"], 42.0)
        self.assertEqual(payload["frame_records"][0]["interaction_state"], "drag")
        self.assertEqual(payload["frame_records"][0]["metadata"]["visible_objects"], 12)

    def test_disabled_global_recorder_is_noop(self) -> None:
        recorder = reset_global_benchmark_recorder(enabled=False)
        self.assertFalse(recorder.enabled)
        token = recorder.start_section("ignored")
        recorder.stop_section(token)
        recorder.begin_frame(phase="measured")
        recorder.finish_frame(total_seconds=1.0)
        self.assertEqual(recorder.section_summaries(), [])
        self.assertEqual(recorder.frame_records(), ())


if __name__ == "__main__":
    unittest.main()