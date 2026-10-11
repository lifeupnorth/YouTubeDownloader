"""The app uses the yt-dlp pin from requirements.txt.

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
        self.assertIn(ytd.YT_DLP_REQUIREMENT, requirements())

    def test_every_requirement_is_pinned(self):
        for line in requirements():
            self.assertRegex(line, r"^[A-Za-z0-9_.-]+==[0-9][0-9A-Za-z.]*$")

    def test_unpinned_without_requirements_txt(self):
        with mock.patch("builtins.open", side_effect=FileNotFoundError):
            self.assertEqual(ytd._yt_dlp_requirement(), "yt-dlp")

    def test_dependabot_watches_requirements_txt(self):
        with open(os.path.join(ROOT, ".github", "dependabot.yml"),
                  encoding="utf-8") as f:
            config = f.read()
        self.assertIn('package-ecosystem: "pip"', config)
        self.assertIn('directory: "/"', config)

    def test_pin_is_exact(self):
        name, sep, version = ytd.YT_DLP_REQUIREMENT.partition("==")
        self.assertEqual((name, sep), ("yt-dlp", "=="))
        self.assertRegex(version, r"^\d{4}\.\d{1,2}\.\d{1,2}(\.\d+)?$")

    def test_in_app_install_uses_the_pin(self):
        app = mock.Mock()
        ytd.YouTubeDownloader._install_ytdlp(app)
        (run,), _ = app._start_worker.call_args
        with mock.patch.object(ytd.subprocess, "check_call") as check_call, \
                mock.patch.object(ytd, "load_yt_dlp", return_value=True):
            run()
        args = check_call.call_args.args[0]
        self.assertEqual(args[1:], ["-m", "pip", "install", ytd.YT_DLP_REQUIREMENT])
        # Loaded right away: no restart needed.
        app._post.assert_called_once_with(app._install_done, True, None, True)


if __name__ == "__main__":
    unittest.main()
