import unittest

from app.jobs.stages.align import align_frames_to_transcript
from app.jobs.stages.transcript import normalize_transcript_segments


class TranscriptNormalizationTests(unittest.TestCase):
    def test_removes_rolling_caption_overlap(self):
        raw = [
            {"start_ms": 0, "end_ms": 2000, "text": "Welcome to our product demo"},
            {"start_ms": 2000, "end_ms": 2010, "text": "our product demo"},
            {"start_ms": 2010, "end_ms": 4000, "text": "our product demo where we create an order"},
        ]

        self.assertEqual(
            [segment["text"] for segment in normalize_transcript_segments(raw)],
            ["Welcome to our product demo", "where we create an order"],
        )

    def test_collapses_same_timestamp_cues(self):
        raw = [
            {"id": "first", "start_ms": 1000, "end_ms": 2000, "text": "Open settings"},
            {"id": "second", "start_ms": 1000, "end_ms": 2000, "text": "Open settings and select tax"},
        ]

        normalized = normalize_transcript_segments(raw)

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["text"], "Open settings and select tax")

    def test_combines_cues_with_same_displayed_second(self):
        raw = [
            {"start_ms": 1100, "end_ms": 1300, "text": "Choose a product"},
            {"start_ms": 1800, "end_ms": 2400, "text": "then add it"},
        ]

        normalized = normalize_transcript_segments(raw)

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["text"], "Choose a product then add it")
        self.assertEqual(normalized[0]["end_ms"], 2400)

    def test_alignment_returns_clean_context(self):
        frames = [{"id": "frame_1", "timestamp_ms": 2500}]
        segments = [
            {"start_ms": 0, "end_ms": 2000, "text": "Click the sales button"},
            {"start_ms": 2000, "end_ms": 4000, "text": "the sales button to start an order"},
        ]

        excerpts = align_frames_to_transcript(frames, segments)

        self.assertEqual(excerpts["frame_1"], "Click the sales button to start an order")


if __name__ == "__main__":
    unittest.main()
