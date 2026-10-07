import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from app.jobs.stages.scenes import (
    _build_uniform_timestamps,
    _merge_nearby_timestamps,
    _sharpness_score,
)


class SceneExtractionTests(unittest.TestCase):
    def test_uniform_sampling_covers_long_video(self):
        timestamps = _build_uniform_timestamps(813, 68)

        self.assertGreaterEqual(len(timestamps), 67)
        self.assertLessEqual(max(b - a for a, b in zip(timestamps, timestamps[1:])), 13)

    def test_nearby_scene_and_uniform_candidates_are_collapsed(self):
        self.assertEqual(
            _merge_nearby_timestamps([0.0, 12.0, 12.4, 25.0]),
            [0.0, 12.0, 25.0],
        )

    def test_sharpness_score_prefers_clear_screen(self):
        with tempfile.TemporaryDirectory() as tmp:
            clear_path = Path(tmp) / "clear.jpg"
            blurry_path = Path(tmp) / "blurry.jpg"
            image = Image.new("RGB", (640, 360), "white")
            draw = ImageDraw.Draw(image)
            for y in range(30, 330, 20):
                draw.line((20, y, 620, y), fill="black", width=2)
            image.save(clear_path)
            image.filter(ImageFilter.GaussianBlur(8)).save(blurry_path)

            self.assertGreater(_sharpness_score(clear_path), _sharpness_score(blurry_path))


if __name__ == "__main__":
    unittest.main()
