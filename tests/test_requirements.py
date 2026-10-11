"""The pinned yt-dlp version is the same everywhere it's used.

Run with:  python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import youtube_downloader as ytd  # noqa: E402


def requirements():
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
        lines = (line.split("#", 1)[0].strip() for line in f)
        return [line for line in lines if line]


class RequirementsTest(unittest.TestCase):
    def test_requirements_txt_matches_the_app(self):
        self.assertEqual(requirements(), [ytd.YT_DLP_REQUIREMENT])

    def test_pin_is_exact(self):
        name, sep, version = ytd.YT_DLP_REQUIREMENT.partition("==")
        self.assertEqual((name, sep), ("yt-dlp", "=="))
        self.assertRegex(version, r"^\d{4}\.\d{1,2}\.\d{1,2}(\.\d+)?$")

    def test_in_app_install_uses_the_pin(self):
        app = mock.Mock()
        ytd.YouTubeDownloader._install_ytdlp(app)
        (run,), _ = app._start_worker.call_args
        with mock.patch.object(ytd.subprocess, "check_call") as check_call:
            run()
        args = check_call.call_args.args[0]
        self.assertEqual(args[1:], ["-m", "pip", "install", ytd.YT_DLP_REQUIREMENT])
        app._post.assert_called_once_with(app._install_done, True)


if __name__ == "__main__":
    unittest.main()
