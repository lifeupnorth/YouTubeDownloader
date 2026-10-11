"""App-level helpers and checks: settings, ffmpeg help, Open folder.

Run with:  python -m unittest discover -s tests -v
"""
import gc
import os
import sys
import tempfile
import tkinter
import unittest
from typing import ClassVar
from unittest import mock

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


class FfmpegCheckTest(unittest.TestCase):
    def make_app(self, ffmpeg):
        gc.collect()
        self.find = mock.Mock(return_value=ffmpeg)
        settings_dir = tempfile.TemporaryDirectory()
        self.addCleanup(settings_dir.cleanup)
        for patcher in (
                mock.patch.object(engine, "YT_DLP_AVAILABLE", True),
                mock.patch.object(engine, "find_ffmpeg", self.find),
                mock.patch.object(ytd, "settings_path", return_value=os.path.join(
                    settings_dir.name, "settings.json")),
                mock.patch.object(ytd.messagebox, "showerror"),
                mock.patch.object(engine, "yt_dlp", None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        try:
            app = ytd.YouTubeDownloader()
        except tkinter.TclError as exc:
            self.skipTest(f"no display: {exc}")
        self.addCleanup(gc.collect)
        self.addCleanup(app.destroy)
        app._replace_url_text("https://www.youtube.com/watch?v=test")
        return app

    def log(self, app):
        return app.log_box.get("1.0", "end")

    def test_missing_ffmpeg_warns_and_blocks_download(self):
        app = self.make_app(None)
        self.assertEqual(app.status_var.get(), ytd.FFMPEG_MISSING_STATUS)
        self.assertIn(ytd.ffmpeg_help(startup=True), self.log(app))
        app._start_download()
        ytd.messagebox.showerror.assert_called_once_with(
            "ffmpeg not found", ytd.ffmpeg_help())
        self.assertFalse(app.is_running)

    def test_focus_clears_warning_once_installed(self):
        app = self.make_app(None)
        app.event_generate("<FocusIn>")
        self.assertEqual(app.status_var.get(), ytd.FFMPEG_MISSING_STATUS)
        calls = self.find.call_count
        app._recheck_ffmpeg(mock.Mock(widget=app.url_entry))
        self.assertEqual(self.find.call_count, calls, "rechecked on child focus")
        self.find.return_value = "/bin/ffmpeg"
        app.event_generate("<FocusIn>")
        self.assertEqual(app.status_var.get(), "Ready")
        self.assertIn("ffmpeg found: /bin/ffmpeg", self.log(app))
        calls = self.find.call_count
        app.event_generate("<FocusIn>")
        self.assertEqual(self.find.call_count, calls, "rechecked after found")

    def test_ffmpeg_found_once_and_passed_to_ytdlp(self):
        app = self.make_app("/bin/ffmpeg")
        self.assertNotEqual(app.status_var.get(), ytd.FFMPEG_MISSING_STATUS)
        self.find.reset_mock()
        app._start_worker = mock.Mock()
        app._start_download()
        self.find.assert_called_once_with()
        _target, queue = app._start_worker.call_args.args
        self.assertEqual(queue.job_options["ffmpeg"], "/bin/ffmpeg")


if __name__ == "__main__":
    unittest.main()
