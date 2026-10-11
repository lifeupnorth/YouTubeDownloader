"""Format selection and ffmpeg detection.

Run with:  python -m unittest discover -s tests -v
"""
import gc
import io
import json
import os
import sys
import tempfile
import tkinter
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import youtube_downloader as ytd  # noqa: E402

REAL_SETTINGS_PATH = ytd.settings_path   # before any test patches it

try:
    import yt_dlp
    from yt_dlp.postprocessor import FFmpegExtractAudioPP, FFmpegVideoRemuxerPP
except ImportError:
    yt_dlp = None


def fmt(format_id, ext, height=None, vcodec="none", acodec="none", width=None,
        **extra):
    return {"format_id": format_id, "ext": ext, "height": height,
            "width": width or (height and height * 16 // 9), "vcodec": vcodec,
            "acodec": acodec, "url": f"https://example.invalid/{format_id}",
            "protocol": "https", **extra}


def mp4(height):
    return fmt(f"mp4-{height}", "mp4", height, vcodec="avc1.640028")


def webm(height):
    return fmt(f"webm-{height}", "webm", height, vcodec="vp9")


M4A = fmt("audio-m4a", "m4a", acodec="mp4a.40.2", abr=128)
OPUS = fmt("audio-opus", "webm", acodec="opus", abr=160)
COMBINED = fmt("18", "mp4", 360, vcodec="avc1.42001E", acodec="mp4a.40.2")
SHORTS = [fmt(f"short-{w}x{h}", "mp4", h, width=w, vcodec="avc1.640028")
          for w, h in ((270, 480), (480, 854), (720, 1280), (1080, 1920))]


def select(opts, formats):
    opts = {**opts, "quiet": True, "no_warnings": True}
    opts.pop("postprocessors", None)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.process_ie_result({
            "id": "x", "title": "x", "extractor": "test",
            "extractor_key": "Test", "webpage_url": "https://example.invalid/x",
            "formats": [dict(f) for f in formats]}, download=False)
    return info["format_id"]


def registered_pps(opts, cls):
    # Relies on yt-dlp internals (YoutubeDL._pps, PP.mapping); skips if they
    # change, since that says nothing about downloads.
    with yt_dlp.YoutubeDL({**opts, "quiet": True}) as ydl:
        pps = getattr(ydl, "_pps", None)
        if not isinstance(pps, dict) or "post_process" not in pps:
            raise unittest.SkipTest("yt-dlp internals changed: no YoutubeDL._pps")
        return [pp for pp in pps["post_process"] if isinstance(pp, cls)]


def mapping(pp):
    if not hasattr(pp, "mapping"):
        raise unittest.SkipTest(
            f"yt-dlp internals changed: no {type(pp).__name__}.mapping")
    return pp.mapping


@unittest.skipIf(yt_dlp is None, "yt-dlp not installed")
class FormatSelectionTest(unittest.TestCase):
    def pick(self, choice, formats):
        return select(ytd.format_opts(choice), formats)

    def test_caps_resolution_and_prefers_mp4(self):
        formats = [mp4(480), mp4(720), mp4(1080), mp4(2160),
                   webm(1080), webm(2160), M4A, OPUS]
        self.assertEqual(self.pick("1080p", formats), "mp4-1080+audio-m4a")
        self.assertEqual(self.pick("720p", formats), "mp4-720+audio-m4a")
        self.assertEqual(self.pick("480p", formats), "mp4-480+audio-m4a")
        self.assertEqual(self.pick("mp4_best", formats), "mp4-2160+audio-m4a")

    def test_vertical_video_caps_the_shorter_side(self):
        # The old [height<=480] filter gave 270x480 here.
        shorts = SHORTS + [M4A]
        self.assertEqual(self.pick("480p", shorts), "short-480x854+audio-m4a")
        self.assertEqual(self.pick("720p", shorts), "short-720x1280+audio-m4a")
        self.assertEqual(self.pick("1080p", shorts), "short-1080x1920+audio-m4a")

    def test_falls_back_instead_of_failing(self):
        # The old height filters raised "Requested format is not available" for
        # the first two.
        self.assertEqual(self.pick("1080p", [webm(1440), OPUS]),
                         "webm-1440+audio-opus")
        self.assertEqual(self.pick("480p", [webm(720), webm(1080), OPUS]),
                         "webm-720+audio-opus")
        self.assertEqual(self.pick("720p", [COMBINED]), "18")

    def test_video_choices_register_mp4_remuxer(self):
        for choice in ytd.VIDEO_MAX_RES:
            opts = ytd.format_opts(choice)
            self.assertEqual(opts["merge_output_format"], "mp4")
            remuxers = registered_pps(opts, FFmpegVideoRemuxerPP)
            self.assertEqual([mapping(pp) for pp in remuxers], ["mp4"], choice)

    def test_audio_choices(self):
        formats = [mp4(720), M4A, OPUS]
        self.assertEqual(self.pick("mp3", formats), "audio-opus")
        self.assertEqual(self.pick("m4a", formats), "audio-m4a")
        for choice in ("mp3", "m4a"):
            extractors = registered_pps(ytd.format_opts(choice),
                                        FFmpegExtractAudioPP)
            self.assertEqual([mapping(pp) for pp in extractors], [choice])


class FindFfmpegTest(unittest.TestCase):
    def test_prefers_path(self):
        with mock.patch.object(ytd.shutil, "which", return_value="/bin/ffmpeg"):
            self.assertEqual(ytd.find_ffmpeg(), "/bin/ffmpeg")

    def test_rereads_registry_path_on_windows(self):
        def which(name, path=None):
            return "/new/ffmpeg.exe" if path == "/new" else None

        with mock.patch.object(ytd.sys, "platform", "win32"), \
                mock.patch.object(ytd, "_registry_path", return_value="/new"), \
                mock.patch.object(ytd.shutil, "which", side_effect=which):
            self.assertEqual(ytd.find_ffmpeg(), "/new/ffmpeg.exe")

    def test_not_found(self):
        with mock.patch.object(ytd.sys, "platform", "linux"), \
                mock.patch.object(ytd, "_registry_path") as registry, \
                mock.patch.object(ytd.shutil, "which", return_value=None):
            self.assertIsNone(ytd.find_ffmpeg())
        registry.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_registry_path_is_expanded(self):
        path = ytd._registry_path().lower()
        self.assertIn("system32", path)
        self.assertNotIn("%systemroot%", path)


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


class SmallHelpersTest(unittest.TestCase):
    def test_eta_as_minutes_and_seconds(self):
        self.assertEqual(ytd.format_eta(5), "0:05")
        self.assertEqual(ytd.format_eta(138), "2:18")
        self.assertEqual(ytd.format_eta(138.7), "2:18")
        self.assertEqual(ytd.format_eta(3725), "1:02:05")

    def test_describe_info(self):
        self.assertEqual(ytd.describe_info(
            {"title": "Me at the zoo", "duration": 19, "channel": "jawed"}),
            "Me at the zoo\n0:19 · jawed")
        self.assertEqual(ytd.describe_info(
            {"_type": "playlist", "title": "L", "playlist_count": 3, "uploader": "u"}),
            "L\nPlaylist · 3 videos · u")
        self.assertEqual(ytd.describe_info({"_type": "playlist", "title": "C"}),
                         "C\nPlaylist or channel")
        self.assertEqual(ytd.describe_info({"title": "Live"}), "Live")
        title = ytd.describe_info({"title": "x" * 300}).split("\n")[0]
        self.assertEqual(len(title), 100)
        self.assertTrue(title.endswith("…"))

    def test_thumbnail_url_picks_a_small_one(self):
        info = {"thumbnail": "https://t/max.jpg", "thumbnails": [
            {"url": "https://t/120.jpg", "width": 120},
            {"url": "https://t/480.jpg", "width": 480},
            {"url": "https://t/320.jpg", "width": 320},
            {"url": "https://t/nowidth.jpg"}]}
        self.assertEqual(ytd.thumbnail_url(info), "https://t/320.jpg")
        self.assertEqual(ytd.thumbnail_url({"thumbnail": "https://t/max.jpg"}),
                         "https://t/max.jpg")
        self.assertIsNone(ytd.thumbnail_url({}))

    def test_fetch_thumbnail_only_from_the_web(self):
        with mock.patch.object(ytd.urllib.request, "urlopen") as urlopen:
            self.assertIsNone(ytd.fetch_thumbnail("file:///etc/passwd"))
            self.assertIsNone(ytd.fetch_thumbnail(None))
        urlopen.assert_not_called()

    @unittest.skipIf(ytd.Image is None, "Pillow not installed")
    def test_fetch_thumbnail_shrinks_it(self):
        png = io.BytesIO()
        ytd.Image.new("RGB", (1280, 720), "blue").save(png, "PNG")
        with mock.patch.object(ytd.urllib.request, "urlopen",
                               return_value=io.BytesIO(png.getvalue())):
            image = ytd.fetch_thumbnail("https://t/x.png")
        self.assertEqual(image.size, ytd.THUMB_SIZE)
        with mock.patch.object(ytd.urllib.request, "urlopen",
                               return_value=io.BytesIO(b"not an image")):
            self.assertIsNone(ytd.fetch_thumbnail("https://t/x.png"))

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

    def test_logger_sends_warnings_and_errors_apart(self):
        errors, warnings = [], []
        logger = ytd._YdlLogger(errors.append, warnings.append)
        logger.warning("[youtube] No supported JavaScript runtime")
        logger.error("ERROR: Private video")
        self.assertEqual(warnings, ["[youtube] No supported JavaScript runtime"])
        self.assertEqual(errors, ["ERROR: Private video"])


class FfmpegHelpTest(unittest.TestCase):
    COMMANDS = {"win32": "winget install ffmpeg",
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
                mock.patch.object(ytd, "YT_DLP_AVAILABLE", True),
                mock.patch.object(ytd, "find_ffmpeg", self.find),
                mock.patch.object(ytd, "settings_path", return_value=os.path.join(
                    settings_dir.name, "settings.json")),
                mock.patch.object(ytd.messagebox, "showerror"),
                mock.patch.object(ytd, "yt_dlp", None, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        try:
            app = ytd.YouTubeDownloader()
        except tkinter.TclError as exc:
            self.skipTest(f"no display: {exc}")
        self.addCleanup(gc.collect)
        self.addCleanup(app.destroy)
        app.url_entry.delete(0, "end")
        app.url_entry.insert(0, "https://www.youtube.com/watch?v=test")
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
        _target, _url, opts, _ask_playlist = app._start_worker.call_args.args
        self.assertEqual(opts["ffmpeg_location"], "/bin/ffmpeg")


class ErrorColorTest(unittest.TestCase):
    def test_errors_reach_the_log_without_color_codes(self):
        try:
            import yt_dlp
        except ImportError:
            self.skipTest("yt-dlp not installed")
        messages = []
        app = mock.Mock()
        app.format_var.get.return_value = "mp4_best"
        app.download_path.get.return_value = "dl"
        app.playlist_var.get.return_value = False
        app._entry_failed = messages.append
        opts = ytd.YouTubeDownloader._get_ydl_opts(app, "ffmpeg")
        # The second run checks that color can be forced on at all.
        for o, colored in ((opts, False),
                            ({**opts, "color": {"stderr": "always"}}, True)):
            messages.clear()
            yt_dlp.YoutubeDL(o).report_error("Private video")
            self.assertEqual(len(messages), 1)
            self.assertEqual(chr(27) in messages[0], colored, repr(messages[0]))
            if not colored:
                self.assertEqual(messages[0], "ERROR: Private video")

    def test_deprecation_notices_are_not_errors(self):
        # yt-dlp sends these to logger.error too, e.g. on Python 3.10.
        errors = []
        logger = ytd._YdlLogger(errors.append)
        with mock.patch("builtins.print"):
            logger.error("Deprecated Feature: Support for Python version 3.10")
            logger.error("\x1b[0;31mDeprecated Feature:\x1b[0m Old option")
        logger.error("ERROR: Private video")
        self.assertEqual(errors, ["ERROR: Private video"])

    def test_real_deprecation_notice_is_not_an_error(self):
        try:
            import yt_dlp
        except ImportError:
            self.skipTest("yt-dlp not installed")
        errors = []
        opts = {"logger": ytd._YdlLogger(errors.append),
                "color": {"stdout": "never", "stderr": "never"}}
        with mock.patch("builtins.print"):
            yt_dlp.YoutubeDL(opts).deprecated_feature("Test notice")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
