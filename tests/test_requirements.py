"""The app uses the yt-dlp pin from requirements.txt.

Run with:  python -m unittest discover -s tests -v
"""
import importlib.metadata
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import download_engine as engine  # noqa: E402
import youtube_downloader as ytd  # noqa: E402


def requirements():
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
        lines = (line.split("#", 1)[0].strip() for line in f)
        return [line for line in lines if line]


class RequirementsTest(unittest.TestCase):
    def test_requirements_txt_matches_the_app(self):
        self.assertIn(engine.YT_DLP_REQUIREMENT, requirements())

    def test_every_requirement_is_pinned(self):
        for line in requirements():
            self.assertRegex(line, r"^[A-Za-z0-9_.-]+(\[[A-Za-z0-9_,-]+\])?==[0-9][0-9A-Za-z.]*$")

    def test_unpinned_without_requirements_txt(self):
        with mock.patch("builtins.open", side_effect=FileNotFoundError):
            self.assertEqual(engine._yt_dlp_requirement(), "yt-dlp[default]")

    def test_finds_the_yt_dlp_line_not_a_similar_one(self):
        text = "yt-dlp-ejs==0.8.0\n# yt-dlp==1.0\nyt-dlp[default]==2026.8.19\n"
        with mock.patch("builtins.open", mock.mock_open(read_data=text)):
            self.assertEqual(engine._yt_dlp_requirement(), "yt-dlp[default]==2026.8.19")

    def test_dependabot_watches_requirements_txt(self):
        with open(os.path.join(ROOT, ".github", "dependabot.yml"),
                  encoding="utf-8") as f:
            config = f.read()
        self.assertIn('package-ecosystem: "pip"', config)
        self.assertIn('directory: "/"', config)

    def test_default_extra_brings_the_challenge_solver(self):
        # yt-dlp pins it there, e.g. 'yt-dlp-ejs==0.8.0; extra == "default"',
        # so requirements.txt doesn't pin it separately.
        try:
            requires = importlib.metadata.requires("yt-dlp") or []
        except importlib.metadata.PackageNotFoundError:
            self.skipTest("yt-dlp isn't installed")
        self.assertTrue([r for r in requires if r.startswith("yt-dlp-ejs")
                         and 'extra == "default"' in r.replace("'", '"')],
                        "yt-dlp's default extra no longer includes yt-dlp-ejs")
        self.assertFalse([line for line in requirements() if line.startswith("yt-dlp-ejs")])

    def test_pin_is_exact(self):
        name, sep, version = engine.YT_DLP_REQUIREMENT.partition("==")
        self.assertEqual((name, sep), ("yt-dlp[default]", "=="))
        self.assertRegex(version, r"^\d{4}\.\d{1,2}\.\d{1,2}(\.\d+)?$")

    def run_install(self, returncode=0, stderr=""):
        app = mock.Mock()
        ytd.YouTubeDownloader._install_ytdlp(app)
        (run,), _ = app._start_worker.call_args
        done = ytd.subprocess.CompletedProcess([], returncode, "", stderr)
        with mock.patch.object(ytd.subprocess, "run", return_value=done) as pip, \
                mock.patch.object(engine, "load_yt_dlp", return_value=True):
            run()
        return app, pip.call_args.args[0]

    def test_in_app_install_uses_the_pin(self):
        app, args = self.run_install()
        self.assertEqual(args[1:], ["-m", "pip", "install", engine.YT_DLP_REQUIREMENT])
        # Loaded right away: no restart needed.
        app._post.assert_called_once_with(app._install_done, True, None, True)

    def test_failed_install_says_why(self):
        app, _args = self.run_install(1, "Collecting yt-dlp\nERROR: No matching "
                                         "distribution found for yt-dlp\n")
        app._post.assert_called_once_with(
            app._install_done, False,
            "Collecting yt-dlp\nERROR: No matching distribution found for yt-dlp")

    def test_pip_command_is_quoted_for_shells(self):
        self.assertEqual(ytd.pip_install_command(),
                         f'pip install "{engine.YT_DLP_REQUIREMENT}"')


if __name__ == "__main__":
    unittest.main()
