import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "packaging")]   # the app, and smoke_test
import smoke_test  # noqa: E402

import download_engine as engine  # noqa: E402
import youtube_downloader as ytd  # noqa: E402


class FakeJob:
    last = None   # the newest FakeJob

    def __init__(self, url, **options):
        self.url, self.options = url, options
        FakeJob.last = self
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
              mock.patch.object(engine, "find_ffmpeg", return_value="ffmpeg"),
              mock.patch.object(engine, "find_js_runtime",
                                return_value=("deno", "/bin/deno"))):
            report = os.path.join(tmp, "report.json")
            ytd.smoke_download("https://x", "mp3", tmp, report)
            with open(report, encoding="utf-8") as f:
                return json.load(f)

    def test_writes_the_outcome(self):
        self.assertEqual(self.smoke(FakeJob), {"outcome": "done", "error": None,
                                               "file": "x.mp3", "log": ["✔ Saved: x.mp3"]})

    def test_never_asks_and_never_downloads_a_playlist(self):
        self.smoke(FakeJob)
        options = FakeJob.last.options
        self.assertFalse(options["whole_playlist"])
        self.assertFalse(options["ask"]("Download all its videos?"))

    def test_uses_the_js_runtime(self):
        self.smoke(FakeJob)
        self.assertEqual(FakeJob.last.options["js_runtime"], ("deno", "/bin/deno"))

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
        bot = {"outcome": "failed", "error": "ERROR: [youtube] x: Sign in to "
               "confirm you're not a bot", "log": []}
        self.assertIn(smoke_test.BOT_CHECK_FAILURE, smoke_test.problem(bot, "mp3"))


class SmokeRunTest(unittest.TestCase):
    def test_timeout(self):
        timeout = smoke_test.subprocess.TimeoutExpired("app", 1)
        with mock.patch.object(smoke_test.subprocess, "run", side_effect=timeout):
            self.assertIn("took over", smoke_test.download(["app"], "https://x", "mp3"))

    def test_missing_report(self):
        with mock.patch.object(smoke_test.subprocess, "run"):
            self.assertEqual(smoke_test.download(["app"], "https://x", "mp3"),
                             "the app wrote no report")

    def run_main(self, results):
        with (mock.patch.object(smoke_test, "download", side_effect=results) as download,
              mock.patch("builtins.print")):
            try:
                smoke_test.main([])
                exit_message = None
            except SystemExit as exc:
                exit_message = str(exc.code)
        return download.call_count, exit_message

    def test_a_retry_that_works_passes(self):
        self.assertEqual(self.run_main(["offline", None, None]), (3, None))

    def test_failures_are_listed(self):
        calls, message = self.run_main(["offline", "offline", None])
        self.assertEqual(calls, 3)
        self.assertIn("mp4_best: offline", message)
        self.assertNotIn("mp3:", message)

    def test_only_the_bot_check_warns_but_passes(self):
        bot = smoke_test.BOT_CHECK_FAILURE + ": sign in"
        self.assertEqual(self.run_main([bot] * 4), (4, None))
        _calls, message = self.run_main([bot, bot, "offline", "offline"])
        self.assertIn("mp3: offline", message)


if __name__ == "__main__":
    unittest.main()
