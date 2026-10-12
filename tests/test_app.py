"""App-level helpers and checks: settings, ffmpeg help, Open folder, the
update check, the .exe's self-check.

Run with:  python -m unittest discover -s tests -v
"""
import gc
import json
import os
import sys
import tempfile
import unittest
from typing import ClassVar
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # no window shows up

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import download_engine as engine
import youtube_downloader as ytd

REAL_SETTINGS_PATH = ytd.settings_path   # before any test patches it


class SettingsFileTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, "sub", "settings.json")
        patcher = mock.patch.object(ytd, "settings_path", return_value=self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_round_trip(self):
        self.assertEqual(ytd.load_settings(), {})   # no file yet
        ytd.save_settings({"folder": "x", "format": "mp3"})
        self.assertEqual(ytd.load_settings(), {"folder": "x", "format": "mp3"})
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_bad_files_are_ignored(self):
        os.makedirs(os.path.dirname(self.path))
        for content in ("not json", "[1, 2]", ""):
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(content)
            self.assertEqual(ytd.load_settings(), {}, content)

    def test_default_locations(self):
        with mock.patch.object(ytd.sys, "platform", "win32"), \
                mock.patch.dict(os.environ, {"APPDATA": "appdata"}):
            self.assertEqual(REAL_SETTINGS_PATH(), os.path.join(
                "appdata", "YT Downloader", "settings.json"))
        with mock.patch.object(ytd.sys, "platform", "linux"), \
                mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "cfg"}):
            self.assertEqual(REAL_SETTINGS_PATH(), os.path.join(
                "cfg", "YT Downloader", "settings.json"))

    def test_saved_formats_are_the_engines(self):
        for value in ytd.FORMAT_VALUES:
            self.assertTrue(engine.format_opts(value), value)


class RevealTest(unittest.TestCase):
    def test_reveal_selects_the_file_on_windows(self):
        path = os.path.join("C:" + os.sep, "dl", "a b.mp4")
        with mock.patch.object(ytd.sys, "platform", "win32"), \
                mock.patch.object(ytd.os.path, "isfile", return_value=True), \
                mock.patch.object(ytd.subprocess, "Popen") as popen:
            ytd.reveal_in_file_manager(path)
        command = popen.call_args.args[0]
        self.assertTrue(command.startswith('explorer /select,"'), command)
        self.assertTrue(command.endswith('a b.mp4"'), command)

    def test_reveal_opens_the_folder_on_linux(self):
        with mock.patch.object(ytd.sys, "platform", "linux"), \
                mock.patch.object(ytd.subprocess, "Popen") as popen:
            ytd.reveal_in_file_manager("/home/me/dl/a.mp4")
        popen.assert_called_once_with(["xdg-open", "/home/me/dl"])


class FfmpegHelpTest(unittest.TestCase):
    COMMANDS: ClassVar[dict[str, str]] = {"win32": "winget install ffmpeg",
                                         "darwin": "brew install ffmpeg",
                                         "linux": "sudo apt install ffmpeg"}

    def test_hint_matches_platform(self):
        for platform, command in self.COMMANDS.items():
            with mock.patch.object(ytd.sys, "platform", platform):
                text = ytd.ffmpeg_help()
            self.assertIn(command, text)
            for other in set(self.COMMANDS.values()) - {command}:
                self.assertNotIn(other, text)

    def test_startup_text(self):
        self.assertIn("click Download again", ytd.ffmpeg_help())
        self.assertNotIn("again", ytd.ffmpeg_help(startup=True))


class ExeBuildTest(unittest.TestCase):
    """What the app does differently as the one-file .exe."""

    def setUp(self):
        gc.collect()
        settings_dir = tempfile.TemporaryDirectory()
        self.addCleanup(settings_dir.cleanup)
        self.folder = settings_dir.name
        patcher = mock.patch.object(ytd, "settings_path", return_value=os.path.join(
            self.folder, "settings.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_pip_install_inside_the_exe(self):
        app = mock.Mock()
        with mock.patch.object(ytd.sys, "frozen", True, create=True), \
                mock.patch.object(ytd.QMessageBox, "critical") as error, \
                mock.patch.object(ytd.QMessageBox, "question") as ask:
            ytd.YouTubeDownloader._prompt_install(app)
        self.assertIn("missing yt-dlp", error.call_args.args[2])
        ask.assert_not_called()
        app._install_ytdlp.assert_not_called()

    def test_self_check_report(self):
        report = os.path.join(self.folder, "report.json")
        with mock.patch.object(engine, "find_ffmpeg", return_value=None), \
                mock.patch.object(engine, "find_js_runtime", return_value=None), \
                mock.patch.object(engine, "YT_DLP_AVAILABLE", True), \
                mock.patch.object(engine, "yt_dlp_version", return_value="2026.08.19"):
            ytd.self_check(report)
        with open(report, encoding="utf-8") as f:
            result = json.load(f)
        self.assertFalse(result["frozen"])
        self.assertIsNone(result["app_version"])
        self.assertEqual(result["yt_dlp"], "2026.08.19")
        self.assertFalse(result["ffmpeg_bundled"])
        self.assertIsNone(result["ffmpeg_version"])
        self.assertIsNone(result["js_runtime"])
        self.assertFalse(result["deno_bundled"])
        self.assertEqual(result["thumbnails"], engine.Image is not None)
        self.assertTrue(result["svg_images"])
        self.assertEqual(result["window"], "YT Downloader")
        self.assertFalse(os.path.exists(os.path.join(self.folder, "settings.json")),
                         "the self-check saved settings")


class UpdateCheckTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.object(ytd, "settings_path", return_value=os.path.join(
            folder.name, "settings.json"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def checks(self, on=True, version="1.0.3"):
        app = mock.Mock(_check_updates=on)
        with mock.patch.object(engine, "app_version", return_value=version):
            ytd.YouTubeDownloader._maybe_check_for_update(app)
        return app._start_worker.call_count

    def test_at_most_once_a_day(self):
        self.assertEqual(self.checks(), 1)
        ytd.YouTubeDownloader._update_checked(mock.Mock(), None, asked=True)
        self.assertEqual(self.checks(), 0)   # again the same day
        settings = ytd.load_settings()
        settings["last_update_check"] -= ytd.UPDATE_CHECK_INTERVAL + 1
        ytd.save_settings(settings)
        self.assertEqual(self.checks(), 1)

    def test_an_offline_check_doesnt_count(self):
        self.assertEqual(self.checks(), 1)
        ytd.YouTubeDownloader._update_checked(mock.Mock(), None, asked=False)
        self.assertNotIn("last_update_check", ytd.load_settings())
        self.assertEqual(self.checks(), 1, "skipped for a day after being offline")

    def test_update_thread_tells_offline_apart(self):
        app = mock.Mock()
        with mock.patch.object(engine, "fetch_update_notice", side_effect=OSError("offline")):
            ytd.YouTubeDownloader._update_thread(app)
        app._post.assert_called_once_with(app._update_checked, None, False)
        app = mock.Mock()
        with mock.patch.object(engine, "fetch_update_notice", return_value=None):
            ytd.YouTubeDownloader._update_thread(app)
        app._post.assert_called_once_with(app._update_checked, None, True)

    def test_not_when_switched_off_or_from_source(self):
        self.assertEqual(self.checks(on=False), 0)
        self.assertEqual(self.checks(version=None), 0)
        self.assertNotIn("last_update_check", ytd.load_settings())

    def test_saving_keeps_the_last_check(self):
        ytd.save_settings({"last_update_check": 5})
        app = mock.Mock(_unavailable_folder=None, _check_updates=False, theme_mode="light")
        app.path.text.return_value = "C:/x"
        app._chosen_format.return_value = "mp3"
        ytd.YouTubeDownloader._save_settings(app)
        self.assertEqual(ytd.load_settings(), {"last_update_check": 5, "folder": "C:/x",
                                               "format": "mp3", "check_updates": False,
                                               "theme": "light"})


class SplitLinksTest(unittest.TestCase):
    def test_links_and_other_words(self):
        cases = {
            "https://a.com/x http://b.org": (["https://a.com/x", "http://b.org"], []),
            "youtu.be/abc www.youtube.com/watch?v=x":
                (["https://youtu.be/abc", "https://www.youtube.com/watch?v=x"], []),
            'Watch this: "youtu.be/abc", thanks!':
                (["https://youtu.be/abc"], ["Watch", "this:", "thanks!"]),
            "(https://a.com/x) https://a.com/x": (["https://a.com/x"], []),
            "hello world": ([], ["hello", "world"]),
            "   ": ([], []),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(ytd.split_links(text), expected)


if __name__ == "__main__":
    unittest.main()
