import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        os.environ["MEDIA_ROOT"] = str(root / "media")
        os.environ["DATA_ROOT"] = str(root / "data")
        os.environ["APP_PASSWORD"] = "unit-test-password"
        Path(os.environ["MEDIA_ROOT"]).mkdir()
        spec = importlib.util.spec_from_file_location("ffmpeg_app", Path(__file__).parents[1] / "app.py")
        cls.app = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_password_hash_is_stable_and_salted(self):
        one = self.app.password_hash("hello", "00" * 16)
        two = self.app.password_hash("hello", "11" * 16)
        self.assertEqual(one, self.app.password_hash("hello", "00" * 16))
        self.assertNotEqual(one, two)

    def test_safe_path_rejects_escape(self):
        with self.assertRaises(ValueError):
            self.app.safe_path("../outside")

    def test_safe_path_and_file_info(self):
        media = Path(os.environ["MEDIA_ROOT"])
        target = media / "inside.mp4"
        target.write_bytes(b"media")
        self.assertEqual(self.app.relative_path(media), "")
        self.assertEqual(self.app.safe_path("inside.mp4"), target.resolve())
        self.assertEqual(self.app.safe_path(str(media / "inside.mp4")), target.resolve())
        self.assertEqual(self.app.file_info(target)["kind"], "video")

    def test_all_lossless_presets_are_labelled(self):
        for preset in self.app.PRESETS.values():
            if preset["lossless"]:
                self.assertIn("无损", preset["label"])

    def test_m4a_remux_copies_audio_without_reencoding(self):
        preset = self.app.PRESETS["m4a_remux"]
        self.assertTrue(preset["remux"])
        self.assertIn("copy", preset["args"])
        self.assertEqual(preset["ext"], ".m4a")

    def test_same_m4a_with_empty_suffix_is_safely_skipped(self):
        media = Path(os.environ["MEDIA_ROOT"])
        source = media / "same.m4a"
        source.write_bytes(b"test")
        job = {"preset": "m4a_remux", "output_dir": "", "suffix": "", "overwrite": "skip"}
        target, reason = self.app.JOBS._target_path(job, source)
        self.assertIsNone(target)
        self.assertIn("跳过", reason)

    def test_job_rejects_non_media(self):
        media = Path(os.environ["MEDIA_ROOT"])
        (media / "note.txt").write_text("hello")
        with self.assertRaises(ValueError):
            self.app.JOBS.create({"files": ["note.txt"], "preset": "mp3"})


if __name__ == "__main__":
    unittest.main()
