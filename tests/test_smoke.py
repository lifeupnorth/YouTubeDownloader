import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "packaging"))
import smoke_test

import download_engine as engine
import youtube_downloader as ytd


class FakeJob:
    def __init__(self, url, **options):
        self.url, self.options = url, options
        self.outcome = self.error = self.last_file = None

    def run(self):
        self.options["on_log"]("✔ Saved: x.mp3")
        self.outcome, self.last_file = "done", "x.mp3"


class CrashingJob(FakeJob):
    def run(self):
        raise RuntimeError("boom")


class SmokeDownloadTest(unittest.TestCase):
    def smoke(self, job_class):
        with (tempfile.TemporaryDirectory() as tmp,
              mock.patch.object(engine, "DownloadJob", job_class),
              mock.patch.object(engine, "find_ffmpeg", return_value="ffmpeg")):
            report = os.path.join(tmp, "report.json")
            ytd.smoke_download("https://x", "mp3", tmp, report)
            with open(report, encoding="utf-8") as f:
                return json.load(f)

    def test_writes_the_outcome(self):
        self.assertEqual(self.smoke(FakeJob), {"outcome": "done", "error": None,
                                               "file": "x.mp3", "log": ["✔ Saved: x.mp3"]})

    def test_a_crash_is_reported(self):
        report = self.smoke(CrashingJob)
        self.assertEqual(report["outcome"], "failed")
        self.assertEqual(report["error"], "RuntimeError: boom")


class SmokeCheckTest(unittest.TestCase):
    def test_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            mp3 = os.path.join(tmp, "a.mp3")
            with open(mp3, "wb") as f:
                f.write(b"x" * smoke_test.MIN_SIZE)
            done = {"outcome": "done", "file": mp3}
            self.assertIsNone(smoke_test.problem(done, "mp3"))
            self.assertIn("no .mp4 file", smoke_test.problem(done, "mp4_best"))
            self.assertIn("ended with failed: offline", smoke_test.problem(
                {"outcome": "failed", "error": "offline"}, "mp3"))
            with open(mp3, "wb") as f:
                f.write(b"x")
            self.assertIn("only 1 bytes", smoke_test.problem(done, "mp3"))


if __name__ == "__main__":
    unittest.main()
