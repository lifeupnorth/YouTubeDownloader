"""Format selection and ffmpeg detection.

Run with:  python -m unittest discover -s tests -v
"""
import os
import sys
import tkinter
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import youtube_downloader as ytd  # noqa: E402

try:
    import yt_dlp
except ImportError:
    yt_dlp = None


def fmt(format_id, ext, height=None, vcodec="none", acodec="none", **extra):
    return {"format_id": format_id, "ext": ext, "height": height,
            "width": height and height * 16 // 9, "vcodec": vcodec,
            "acodec": acodec, "url": f"https://example.invalid/{format_id}",
            "protocol": "https", **extra}


def mp4(height):
    return fmt(f"mp4-{height}", "mp4", height, vcodec="avc1.640028")


def webm(height):
    return fmt(f"webm-{height}", "webm", height, vcodec="vp9")


M4A = fmt("audio-m4a", "m4a", acodec="mp4a.40.2", abr=128)
OPUS = fmt("audio-opus", "webm", acodec="opus", abr=160)
COMBINED = fmt("18", "mp4", 360, vcodec="avc1.42001E", acodec="mp4a.40.2")


def select(opts, formats):
    opts = {**opts, "quiet": True, "no_warnings": True}
    opts.pop("postprocessors", None)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.process_ie_result({
            "id": "x", "title": "x", "extractor": "test",
            "extractor_key": "Test", "webpage_url": "https://example.invalid/x",
            "formats": [dict(f) for f in formats]}, download=False)
    return info["format_id"]


@unittest.skipIf(yt_dlp is None, "yt-dlp not installed")
class FormatSelectionTest(unittest.TestCase):
    def pick(self, choice, formats):
        return select(ytd.format_opts(choice), formats)

    def test_caps_height_and_prefers_mp4(self):
        formats = [mp4(480), mp4(720), mp4(1080), mp4(2160),
                   webm(1080), webm(2160), M4A, OPUS]
        self.assertEqual(self.pick("1080p", formats), "mp4-1080+audio-m4a")
        self.assertEqual(self.pick("720p", formats), "mp4-720+audio-m4a")
        self.assertEqual(self.pick("480p", formats), "mp4-480+audio-m4a")
        self.assertEqual(self.pick("mp4_best", formats), "mp4-2160+audio-m4a")

    def test_falls_back_instead_of_failing(self):
        # The old height filters raised "Requested format is not available" for
        # the first two.
        self.assertEqual(self.pick("1080p", [webm(1440), OPUS]),
                         "webm-1440+audio-opus")
        self.assertEqual(self.pick("480p", [webm(720), webm(1080), OPUS]),
                         "webm-720+audio-opus")
        self.assertEqual(self.pick("720p", [COMBINED]), "18")

    def test_video_choices_end_up_as_mp4(self):
        for choice in ytd.VIDEO_MAX_HEIGHT:
            opts = ytd.format_opts(choice)
            self.assertEqual(opts["merge_output_format"], "mp4")
            self.assertIn({"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
                          opts["postprocessors"])

    def test_audio_choices_unchanged(self):
        formats = [mp4(720), M4A, OPUS]
        self.assertEqual(self.pick("mp3", formats), "audio-opus")
        self.assertEqual(self.pick("m4a", formats), "audio-m4a")


class FindFfmpegTest(unittest.TestCase):
    def test_prefers_path(self):
        with mock.patch.object(ytd.shutil, "which", return_value="/bin/ffmpeg"):
            self.assertEqual(ytd.find_ffmpeg(), "/bin/ffmpeg")

    def test_falls_back_to_winget_links(self):
        links = os.path.join("appdata", "Microsoft", "WinGet", "Links")

        def which(name, path=None):
            if path and links in path.split(os.pathsep):
                return os.path.join(links, "ffmpeg.exe")
            return None

        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "appdata"}), \
                mock.patch.object(ytd.shutil, "which", side_effect=which):
            self.assertEqual(ytd.find_ffmpeg(),
                             os.path.join(links, "ffmpeg.exe"))

    def test_not_found(self):
        with mock.patch.object(ytd.shutil, "which", return_value=None):
            self.assertIsNone(ytd.find_ffmpeg())


class FfmpegCheckTest(unittest.TestCase):
    def make_app(self, ffmpeg):
        for patcher in (
                mock.patch.object(ytd, "YT_DLP_AVAILABLE", True),
                mock.patch.object(ytd, "find_ffmpeg", return_value=ffmpeg),
                mock.patch.object(ytd.messagebox, "showerror"),
                mock.patch.object(ytd, "yt_dlp", None, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        try:
            app = ytd.YouTubeDownloader()
        except tkinter.TclError as exc:
            self.skipTest(f"no display: {exc}")
        self.addCleanup(app.destroy)
        app.url_entry.delete(0, "end")
        app.url_entry.insert(0, "https://www.youtube.com/watch?v=test")
        return app

    def test_missing_ffmpeg_warns_and_blocks_download(self):
        app = self.make_app(None)
        self.assertIn("ffmpeg not found", app.status_var.get())
        self.assertIn("winget install ffmpeg", app.log_box.get("1.0", "end"))
        app._start_download()
        ytd.messagebox.showerror.assert_called_once_with(
            "ffmpeg not found", ytd.FFMPEG_HELP)
        self.assertFalse(app.is_running)

    def test_found_ffmpeg_is_passed_to_ytdlp(self):
        app = self.make_app("/bin/ffmpeg")
        self.assertNotIn("ffmpeg", app.status_var.get())
        opts = app._get_ydl_opts("https://www.youtube.com/watch?v=test")
        self.assertEqual(opts["ffmpeg_location"], "/bin/ffmpeg")


if __name__ == "__main__":
    unittest.main()
