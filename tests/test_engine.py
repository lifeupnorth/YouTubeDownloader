"""The download engine (download_engine.py), without a window.

Run with:  python -m unittest discover -s tests -v
"""
import io
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(TESTS), TESTS]   # the app, and tests/fakes.py
from fakes import fake_yt_dlp, make_fake_ydl  # noqa: E402

import download_engine as engine  # noqa: E402

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
        return select(engine.format_opts(choice), formats)

    def test_caps_resolution_and_prefers_mp4(self):
        formats = [mp4(480), mp4(720), mp4(1080), mp4(2160),
                   webm(1080), webm(2160), M4A, OPUS]
        self.assertEqual(self.pick("1080p", formats), "mp4-1080+audio-m4a")
        self.assertEqual(self.pick("720p", formats), "mp4-720+audio-m4a")
        self.assertEqual(self.pick("480p", formats), "mp4-480+audio-m4a")
        self.assertEqual(self.pick("mp4_best", formats), "mp4-2160+audio-m4a")

    def test_vertical_video_caps_the_shorter_side(self):
        # The old [height<=480] filter gave 270x480 here.
        shorts = [*SHORTS, M4A]
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
        for choice in engine.VIDEO_MAX_RES:
            opts = engine.format_opts(choice)
            self.assertEqual(opts["merge_output_format"], "mp4")
            remuxers = registered_pps(opts, FFmpegVideoRemuxerPP)
            self.assertEqual([mapping(pp) for pp in remuxers], ["mp4"], choice)

    def test_audio_choices(self):
        formats = [mp4(720), M4A, OPUS]
        self.assertEqual(self.pick("mp3", formats), "audio-opus")
        self.assertEqual(self.pick("m4a", formats), "audio-m4a")
        for choice in ("mp3", "m4a"):
            extractors = registered_pps(engine.format_opts(choice),
                                        FFmpegExtractAudioPP)
            self.assertEqual([mapping(pp) for pp in extractors], [choice])

    def test_final_ext_is_the_converted_extension(self):
        # Without it yt-dlp doesn't see that "T.mp3" (made from "T.webm") is
        # already there, and downloads and converts it again.
        for choice in [*engine.VIDEO_MAX_RES, "mp3", "m4a"]:
            expected = choice if choice in ("mp3", "m4a") else "mp4"
            self.assertEqual(engine.format_opts(choice)["final_ext"], expected, choice)


class UpdateCheckTest(unittest.TestCase):
    def test_newer_version(self):
        self.assertTrue(engine.newer_version("v1.0.10", "1.0.9"))
        self.assertTrue(engine.newer_version("2026.10.01", "2026.8.19"))
        self.assertFalse(engine.newer_version("v1.0.0", "1.0.0"))
        self.assertFalse(engine.newer_version("2026.08.19", "2026.8.19"))

    def test_app_version_only_in_the_exe(self):
        self.assertIsNone(engine.app_version())   # running from source
        with (tempfile.TemporaryDirectory() as unpacked,
              mock.patch.object(engine.sys, "frozen", True, create=True),
              mock.patch.object(engine.sys, "_MEIPASS", unpacked, create=True)):
            self.assertIsNone(engine.app_version(), "no VERSION file")
            version_file = os.path.join(unpacked, "VERSION")
            with open(version_file, "w", encoding="utf-8") as f:
                f.write("")
            self.assertIsNone(engine.app_version(), "a test build")
            with open(version_file, "w", encoding="utf-8") as f:
                f.write("1.0.3")
            self.assertEqual(engine.app_version(), "1.0.3")

    def test_exe_says_when_a_newer_release_is_out(self):
        release = {"tag_name": "v1.0.4", "html_url": "https://github.com/r/v1.0.4"}
        with (mock.patch.object(engine.sys, "frozen", True, create=True),
              mock.patch.object(engine, "app_version", return_value="1.0.3"),
              mock.patch.object(engine, "_get_json", return_value=release) as get):
            notice = engine.check_for_update()
            self.assertIn("1.0.4 is out (this is 1.0.3)", notice)
            self.assertIn("https://github.com/r/v1.0.4", notice)
            get.assert_called_once_with(engine.LATEST_RELEASE_API)
            release["tag_name"] = "v1.0.3"
            self.assertIsNone(engine.check_for_update())
        with (mock.patch.object(engine.sys, "frozen", True, create=True),
              mock.patch.object(engine, "app_version", return_value=None),
              mock.patch.object(engine, "_get_json") as get):
            self.assertIsNone(engine.check_for_update())   # a test build
        get.assert_not_called()

    def test_source_never_checks(self):
        with mock.patch.object(engine, "_get_json") as get:
            self.assertIsNone(engine.check_for_update())
        get.assert_not_called()

    def test_no_network_no_notice(self):
        # As a release .exe (from source it never asks, see above).
        with (mock.patch.object(engine, "app_version", return_value="1.0.3"),
              mock.patch.object(engine.urllib.request, "urlopen",
                                side_effect=OSError("offline")) as urlopen):
            self.assertIsNone(engine.check_for_update())
        urlopen.assert_called_once()

    def test_unexpected_answer_no_notice(self):
        for body in (b"<html>rate limited</html>", b"[]", b'{"tag_name": null}'):
            with (self.subTest(body=body),
                  mock.patch.object(engine, "app_version", return_value="1.0.3"),
                  mock.patch.object(engine.urllib.request, "urlopen",
                                    return_value=io.BytesIO(body))):
                self.assertIsNone(engine.check_for_update())


class FindFfmpegTest(unittest.TestCase):
    def test_bundled_ffmpeg_only_in_the_exe(self):
        self.assertIsNone(engine.bundled_ffmpeg())   # running from source
        with tempfile.TemporaryDirectory() as unpacked:
            name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
            with mock.patch.object(engine.sys, "frozen", True, create=True), \
                    mock.patch.object(engine.sys, "_MEIPASS", unpacked, create=True):
                self.assertIsNone(engine.bundled_ffmpeg(), "not in the bundle")
                open(os.path.join(unpacked, name), "wb").close()
                self.assertEqual(engine.bundled_ffmpeg(), os.path.join(unpacked, name))
                with mock.patch.object(engine.shutil, "which") as which:
                    self.assertEqual(engine.find_ffmpeg(), os.path.join(unpacked, name))
                which.assert_not_called()   # the bundled one comes first

    def test_prefers_path(self):
        with mock.patch.object(engine.shutil, "which", return_value="/bin/ffmpeg"):
            self.assertEqual(engine.find_ffmpeg(), "/bin/ffmpeg")

    def test_rereads_registry_path_on_windows(self):
        def which(name, path=None):
            return "/new/ffmpeg.exe" if path == "/new" else None

        with mock.patch.object(engine.sys, "platform", "win32"), \
                mock.patch.object(engine, "_registry_path", return_value="/new"), \
                mock.patch.object(engine.shutil, "which", side_effect=which):
            self.assertEqual(engine.find_ffmpeg(), "/new/ffmpeg.exe")

    def test_not_found(self):
        with mock.patch.object(engine.sys, "platform", "linux"), \
                mock.patch.object(engine, "_registry_path") as registry, \
                mock.patch.object(engine.shutil, "which", return_value=None):
            self.assertIsNone(engine.find_ffmpeg())
        registry.assert_not_called()

    def test_js_runtime_prefers_the_bundled_deno(self):
        with tempfile.TemporaryDirectory() as unpacked:
            name = "deno.exe" if sys.platform == "win32" else "deno"
            open(os.path.join(unpacked, name), "wb").close()
            with mock.patch.object(engine.sys, "frozen", True, create=True), \
                    mock.patch.object(engine.sys, "_MEIPASS", unpacked, create=True), \
                    mock.patch.object(engine, "_js_runtime_supported", return_value=True), \
                    mock.patch.object(engine.shutil, "which") as which:
                self.assertEqual(engine.find_js_runtime(),
                                 ("deno", os.path.join(unpacked, name)))
            which.assert_not_called()

    def test_js_runtime_skips_old_versions(self):
        found = {"node": "/bin/node", "bun": "/bin/bun"}
        with mock.patch.object(engine.sys, "platform", "linux"), \
                mock.patch.object(engine.shutil, "which", side_effect=found.get), \
                mock.patch.object(engine, "_js_runtime_supported",
                                  side_effect=lambda name, path: name == "bun"):
            self.assertEqual(engine.find_js_runtime(), ("bun", "/bin/bun"))
        with mock.patch.object(engine.sys, "platform", "linux"), \
                mock.patch.object(engine.shutil, "which", return_value=None):
            self.assertIsNone(engine.find_js_runtime())

    @unittest.skipUnless(engine.YT_DLP_AVAILABLE, "needs yt-dlp")
    def test_js_runtime_version_check(self):
        # yt-dlp's check: a program that isn't there isn't supported.
        self.assertFalse(engine._js_runtime_supported("deno", os.path.join("no", "deno")))
        with mock.patch.object(engine, "yt_dlp", None):   # can't check: let yt-dlp try
            self.assertTrue(engine._js_runtime_supported("deno", "deno"))

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_registry_path_is_expanded(self):
        path = engine._registry_path().lower()
        self.assertIn("system32", path)
        self.assertNotIn("%systemroot%", path)


class YtDlpVersionTest(unittest.TestCase):
    def test_same_version_ignores_zero_padding(self):
        self.assertTrue(engine.same_version("2026.08.19", "2026.8.19"))
        self.assertFalse(engine.same_version("2026.02.21", "2026.8.19"))

    def test_pinned_version(self):
        with mock.patch.object(engine, "YT_DLP_REQUIREMENT", "yt-dlp==2026.8.19"):
            self.assertEqual(engine.pinned_yt_dlp_version(), "2026.8.19")
        with mock.patch.object(engine, "YT_DLP_REQUIREMENT", "yt-dlp"):
            self.assertIsNone(engine.pinned_yt_dlp_version())

    def load(self, import_module):
        """Runs load_yt_dlp as if yt-dlp was missing at startup."""
        with mock.patch.object(engine, "YT_DLP_AVAILABLE", False), \
                mock.patch.object(engine, "yt_dlp", None, create=True), \
                mock.patch.object(engine.site, "addsitedir") as addsitedir, \
                mock.patch.object(engine.importlib, "import_module", import_module):
            loaded = engine.load_yt_dlp()
            return loaded, engine.YT_DLP_AVAILABLE, engine.yt_dlp, addsitedir

    def test_load_after_install(self):
        fake = mock.Mock(version=mock.Mock(__version__="2026.08.19"))
        loaded, available, module, _ = self.load(lambda name: fake)
        self.assertEqual((loaded, available, module), (True, True, fake))

    def test_load_fails_until_a_restart(self):
        def import_module(name):
            raise ImportError(name)
        loaded, available, module, _ = self.load(import_module)
        self.assertEqual((loaded, available, module), (False, False, None))

    def test_version_needs_yt_dlp(self):
        with mock.patch.object(engine, "YT_DLP_AVAILABLE", False):
            self.assertIsNone(engine.yt_dlp_version())
        fake = mock.Mock(version=mock.Mock(__version__="2026.08.19"))
        with mock.patch.object(engine, "YT_DLP_AVAILABLE", True), \
                mock.patch.object(engine, "yt_dlp", fake, create=True):
            self.assertEqual(engine.yt_dlp_version(), "2026.08.19")


class HelpersTest(unittest.TestCase):
    def test_eta_as_minutes_and_seconds(self):
        self.assertEqual(engine.format_eta(5), "0:05")
        self.assertEqual(engine.format_eta(138), "2:18")
        self.assertEqual(engine.format_eta(138.7), "2:18")
        self.assertEqual(engine.format_eta(3725), "1:02:05")

    def test_describe_info(self):
        self.assertEqual(engine.describe_info(
            {"title": "Me at the zoo", "duration": 19, "channel": "jawed"}),
            "Me at the zoo\n0:19 · jawed")
        self.assertEqual(engine.describe_info(
            {"_type": "playlist", "title": "L", "playlist_count": 3, "uploader": "u"}),
            "L\nPlaylist · 3 videos · u")
        self.assertEqual(engine.describe_info({"_type": "playlist", "title": "C"}),
                         "C\nPlaylist or channel")
        self.assertEqual(engine.describe_info({"title": "Live"}), "Live")
        title = engine.describe_info({"title": "x" * 300}).split("\n")[0]
        self.assertEqual(len(title), 100)
        self.assertTrue(title.endswith("…"))

    def test_thumbnail_url_picks_a_small_one(self):
        info = {"thumbnail": "https://t/max.jpg", "thumbnails": [
            {"url": "https://t/120.jpg", "width": 120},
            {"url": "https://t/480.jpg", "width": 480},
            {"url": "https://t/320.jpg", "width": 320},
            {"url": "https://t/nowidth.jpg"}]}
        self.assertEqual(engine.thumbnail_url(info), "https://t/320.jpg")
        self.assertEqual(engine.thumbnail_url({"thumbnail": "https://t/max.jpg"}),
                         "https://t/max.jpg")
        self.assertIsNone(engine.thumbnail_url({}))

    def test_fetch_thumbnail_only_from_the_web(self):
        with mock.patch.object(engine.urllib.request, "urlopen") as urlopen:
            self.assertIsNone(engine.fetch_thumbnail("file:///etc/passwd"))
            self.assertIsNone(engine.fetch_thumbnail(None))
        urlopen.assert_not_called()

    @unittest.skipIf(engine.Image is None, "Pillow not installed")
    def test_fetch_thumbnail_shrinks_it(self):
        png = io.BytesIO()
        engine.Image.new("RGB", (1280, 720), "blue").save(png, "PNG")
        with mock.patch.object(engine.urllib.request, "urlopen",
                               return_value=io.BytesIO(png.getvalue())):
            image = engine.fetch_thumbnail("https://t/x.png")
        self.assertEqual(image.size, engine.THUMB_SIZE)
        with mock.patch.object(engine.urllib.request, "urlopen",
                               return_value=io.BytesIO(b"not an image")):
            self.assertIsNone(engine.fetch_thumbnail("https://t/x.png"))

    @unittest.skipIf(engine.Image is None, "Pillow not installed")
    def test_fetch_thumbnail_stops_when_canceled(self):
        reads = []

        class Response(io.BytesIO):
            def read(self, size=-1):
                reads.append(size)
                return super().read(size)

        with mock.patch.object(engine.urllib.request, "urlopen",
                               return_value=Response(b"x" * 1_000_000)):
            self.assertIsNone(engine.fetch_thumbnail("https://t/x.png", lambda: True))
        self.assertEqual(len(reads), 1)

    def test_logger_sends_warnings_and_errors_apart(self):
        errors, warnings = [], []
        logger = engine._YdlLogger(errors.append, warnings.append)
        logger.warning("[youtube] No supported JavaScript runtime")
        logger.error("ERROR: Private video")
        self.assertEqual(warnings, ["[youtube] No supported JavaScript runtime"])
        self.assertEqual(errors, ["ERROR: Private video"])


class ErrorColorTest(unittest.TestCase):
    def test_errors_reach_the_log_without_color_codes(self):
        try:
            import yt_dlp
        except ImportError:
            self.skipTest("yt-dlp not installed")
        # The second run checks that color can be forced on at all.
        for color, colored in ((None, False), ({"stderr": "always"}, True)):
            job = make_job()
            opts = job.options()
            if color:
                opts["color"] = color
            yt_dlp.YoutubeDL(opts).report_error("Private video")
            self.assertEqual(len(job.skipped), 1)
            self.assertEqual(chr(27) in job.skipped[0], colored, repr(job.skipped[0]))
            if not colored:
                self.assertEqual(job.skipped[0], "Private video")

    def test_deprecation_notices_are_not_errors(self):
        # yt-dlp sends these to logger.error too, e.g. on Python 3.10.
        errors = []
        logger = engine._YdlLogger(errors.append)
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
        opts = {"logger": engine._YdlLogger(errors.append),
                "color": {"stdout": "never", "stderr": "never"}}
        with mock.patch("builtins.print"):
            yt_dlp.YoutubeDL(opts).deprecated_feature("Test notice")
        self.assertEqual(errors, [])


def cancel_when(job, ready, timeout=10):
    """Cancels job (or a DownloadQueue) from another thread as soon as
    ready() is true."""
    def wait():
        deadline = time.monotonic() + timeout
        while not ready() and time.monotonic() < deadline:
            time.sleep(0.01)
        job.cancel()
    threading.Thread(target=wait, daemon=True).start()


def make_job(url="https://www.youtube.com/watch?v=x", *, whole_playlist=False,
             ask=None, folder="dl", fmt="mp4_best"):
    """A DownloadJob whose callbacks record into job.progress and job.log."""
    progress, log = [], []
    job = engine.DownloadJob(
        url, fmt=fmt, folder=folder, ffmpeg="ffmpeg", whole_playlist=whole_playlist,
        on_progress=lambda pct, message: progress.append((pct, message)),
        on_log=log.append, ask=ask or (lambda question: True))
    job.progress, job.log = progress, log
    return job


class DownloadJobTest(unittest.TestCase):
    """The download logic on its own, with a fake yt-dlp: no window, no Tk."""

    def run_job(self, fake, job=None, **job_args):
        job = job or make_job(**job_args)
        with mock.patch.object(engine, "yt_dlp", fake_yt_dlp(fake)):
            job.run()
        return job

    def test_single_video(self):
        job = self.run_job(make_fake_ydl(streams=2))
        self.assertEqual(job.outcome, "done")
        self.assertEqual((job.saved, job.already, job.skipped), (1, 0, []))
        self.assertEqual(job.last_file, "v0.mp4")
        self.assertEqual(job.summary(), "Download complete!")
        self.assertIn("✔ Saved: v0.mp4", job.log)
        percents = [pct for pct, _ in job.progress]
        self.assertEqual(percents, sorted(percents), "went backwards between streams")
        self.assertEqual(percents[-1], 99, "100 only once the window says done")

    def test_options(self):
        opts = make_job(folder="dl", fmt="mp3").options()
        self.assertIs(opts["noplaylist"], True)
        self.assertEqual(opts["ffmpeg_location"], "ffmpeg")
        self.assertTrue(opts["outtmpl"].startswith("dl"))
        self.assertEqual(opts["postprocessors"][0]["preferredcodec"], "mp3")
        self.assertIs(make_job(whole_playlist=True).options()["noplaylist"], False)

    def test_progress_restarts_for_each_playlist_video(self):
        job = self.run_job(make_fake_ydl(streams=2, videos=2, overshoot=15),
                           whole_playlist=True)
        per_video = {}
        for pct, message in job.progress:
            per_video.setdefault(message.split(" · ")[0], []).append(pct)
        self.assertEqual(sorted(per_video), ["Video 1/2", "Video 2/2"])
        for video, values in per_video.items():
            self.assertEqual(values, sorted(values), f"{video} went backwards")
            self.assertEqual(values[0], 0, f"{video} didn't restart")
            self.assertEqual(max(values), 99)
        self.assertEqual(job.summary(), "Done: 2 saved")

    def test_status_names_the_playlist_entry(self):
        job = make_job()
        entry = {"playlist_index": 3, "n_entries": 30}
        job._progress_hook({"status": "downloading", "total_bytes": 100,
                            "downloaded_bytes": 45, "speed": 1e6, "eta": 138,
                            "info_dict": entry})
        self.assertEqual(job.progress[-1][1],
                         "Video 3/30 · Downloading… 44.5%  1.0 MB/s · ETA 2:18")
        job._progress_hook({"status": "finished", "info_dict": entry})
        self.assertEqual(job.progress[-1], (99, "Video 3/30 · Processing…"))

    def test_unavailable_entry_is_skipped(self):
        job = self.run_job(make_fake_ydl(streams=1, videos=3, fail=(1,)),
                           whole_playlist=True)
        self.assertEqual(job.outcome, "done")
        self.assertEqual(job.summary(), "Done: 2 saved, 1 skipped")
        self.assertIn("✘ [youtube] v1: Private video", job.log)

    def test_nothing_saved_is_a_failure(self):
        job = self.run_job(make_fake_ydl(streams=1, fail=(0,)))
        self.assertEqual((job.outcome, job.error_logged), ("failed", True))
        self.assertEqual(job.summary(), "Error: [youtube] v0: Private video")

    def test_exception_is_a_failure(self):
        job = self.run_job(make_fake_ydl(streams=1, error=RuntimeError("boom")))
        self.assertEqual((job.outcome, job.error, job.error_logged),
                         ("failed", "boom", False))

    def test_already_downloaded_is_counted_apart(self):
        job = self.run_job(make_fake_ydl(streams=1, videos=2, already=(0,)),
                           whole_playlist=True)
        self.assertEqual(job.summary(), "Done: 1 saved, 1 already downloaded")
        self.assertIn("• Already downloaded: v0.mp4", job.log)

    def test_playlist_needs_a_yes(self):
        questions = []
        fake = make_fake_ydl(probe=("playlist",))
        job = self.run_job(fake, ask=lambda q: questions.append(q) or False)
        self.assertEqual(job.outcome, "declined")
        self.assertEqual(fake.calls, ["probe"])
        self.assertIn('"Fake list" is a playlist or channel', questions[0])
        self.assertEqual(job.summary(), "Playlist not downloaded.")
        fake = make_fake_ydl(probe=("playlist",))
        self.assertEqual(self.run_job(fake, ask=lambda q: True).outcome, "done")
        self.assertEqual(fake.calls, ["probe", "download"])

    def test_a_video_doesnt_ask(self):
        def ask(question):
            raise AssertionError("asked about a single video")
        fake = make_fake_ydl(probe=("url", "video"))
        self.assertEqual(self.run_job(fake, ask=ask).outcome, "done")
        self.assertEqual(fake.calls, ["probe", "probe", "download"])

    def cancel_at(self, point, tmp, videos):
        job = make_job(whole_playlist=True, folder=tmp)

        def on_progress(*where):
            if where == point:
                job.cancel()
        self.run_job(make_fake_ydl(streams=2, videos=videos, files_dir=tmp,
                                   on_progress=on_progress), job=job)
        return job

    def test_cancel_removes_only_the_unfinished_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "unrelated.mp4"), "wb").close()
            # Video 1's first stream has finished, its second is half done.
            job = self.cancel_at((1, 1, 50), tmp, videos=2)
            self.assertEqual(sorted(os.listdir(tmp)), ["unrelated.mp4", "v0.mp4"])
        self.assertEqual(job.outcome, "canceled")
        self.assertEqual(job.summary(),
                         "Download canceled (1 saved, 2 partial files removed).")

    def test_cancel_between_playlist_videos(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self.cancel_at((1, -1, 0), tmp, videos=3)
            self.assertEqual(os.listdir(tmp), ["v0.mp4"])
        self.assertEqual(job.summary(), "Download canceled (1 saved).")

    def test_a_video_that_cant_be_found(self):
        # yt-dlp logs why and returns nothing (e.g. a private or removed video).
        fake = make_fake_ydl(streams=1, probe=(None,))
        job = self.run_job(fake)
        self.assertEqual(fake.calls, ["probe"])
        self.assertEqual(job.outcome, "failed")
        self.assertIn("Video unavailable", job.error)
        self.assertTrue(job.error_logged)
        self.assertEqual(job.log, [f"✘ [youtube] {job.url}: Video unavailable"])

    def test_url_results_are_followed_at_most_three_times(self):
        # A link that keeps redirecting must not be looked up forever.
        fake = make_fake_ydl(streams=1, probe=("url",) * 10)
        job = self.run_job(fake)
        self.assertEqual(fake.calls, ["probe"] * 4 + ["download"])
        self.assertEqual(job.outcome, "done")

    def test_job_uses_the_js_runtime(self):
        job = engine.DownloadJob("https://x", fmt="mp3", folder="dl", ffmpeg="ffmpeg",
                                 whole_playlist=False, on_progress=None, on_log=None,
                                 ask=None, js_runtime=("deno", "C:/deno.exe"))
        self.assertEqual(job.options()["js_runtimes"], {"deno": {"path": "C:/deno.exe"}})
        self.assertNotIn("js_runtimes", make_job().options())   # yt-dlp's default

    def test_missing_js_runtime_warning_isnt_logged(self):
        job = self.run_job(make_fake_ydl(
            streams=1, warn="[youtube] No supported JavaScript runtime could be found."))
        self.assertFalse([line for line in job.log if "JavaScript" in line])
    def test_cancel_while_reading_a_playlist(self):
        # yt-dlp reads a big channel's pages before the first progress
        # update; each page is a status message, where cancel is checked.
        pages = []

        class Fake(make_fake_ydl(streams=1)):
            def _run(self):
                for page in range(100):
                    pages.append(page)
                    if page == 2:
                        job.cancel()
                    self.opts["logger"].debug(f"[youtube:tab] Downloading page {page}")
                super()._run()

        job = make_job(whole_playlist=True)
        self.run_job(Fake, job=job)
        self.assertEqual(pages, [0, 1, 2])
        self.assertEqual(job.outcome, "canceled")
        self.assertEqual(job.summary(), "Download canceled.")

    @unittest.skipUnless(engine.YT_DLP_AVAILABLE, "needs yt-dlp")
    def test_cancel_stops_ffmpeg(self):
        import yt_dlp.postprocessor.ffmpeg as ffmpeg_pp

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "v0.mp3")

            class Fake(make_fake_ydl(streams=1)):
                def _run(self):
                    # Like a conversion: yt-dlp's Popen.run, output file
                    # last. Here a process that would take 60 s; canceled
                    # once it has created its output (not after a fixed
                    # time, which a slow runner may not meet).
                    cancel_when(job, lambda: os.path.exists(out))
                    ffmpeg_pp.Popen.run([sys.executable, "-c",
                                         "import sys, time; open(sys.argv[1][5:], 'wb');"
                                         " time.sleep(60)", "file:" + out])
                    # yt-dlp logs the failed conversion; not a skipped video.
                    self.opts["logger"].error("ERROR: Postprocessing: ffmpeg failed")

            job = make_job()
            start = time.monotonic()
            self.run_job(Fake, job=job)
            self.assertLess(time.monotonic() - start, 10)
            self.assertEqual(os.listdir(tmp), [])
        self.assertEqual(job.outcome, "canceled")
        self.assertEqual(job.skipped, [])
        self.assertEqual(job.summary(), "Download canceled (1 partial file removed).")

    @unittest.skipUnless(engine.YT_DLP_AVAILABLE, "needs yt-dlp")
    def test_cancel_keeps_a_file_from_an_earlier_run(self):
        # e.g. ffprobe-like runs reading a finished .m4a, or ffmpeg writing to
        # a file that's already there: canceling them removes nothing.
        import yt_dlp.postprocessor.ffmpeg as ffmpeg_pp

        with tempfile.TemporaryDirectory() as tmp:
            old = os.path.join(tmp, "old.m4a")
            open(old, "wb").close()

            class Fake(make_fake_ydl(streams=1)):
                def _run(self):
                    cancel_when(job, lambda: job._processes)   # once it runs
                    ffmpeg_pp.Popen.run([sys.executable, "-c",
                                         "import time; time.sleep(60)", "file:" + old])
                    self.opts["logger"].error("ERROR: Postprocessing: ffmpeg failed")

            job = make_job()
            self.run_job(Fake, job=job)
            self.assertEqual(os.listdir(tmp), ["old.m4a"])
        self.assertEqual(job.outcome, "canceled")
        self.assertEqual(job.summary(), "Download canceled.")

    def test_only_new_ffmpeg_outputs_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            new, old = os.path.join(tmp, "new.mp3"), os.path.join(tmp, "old.m4a")
            open(old, "wb").close()
            cases = {
                ("ffmpeg", "-i", "file:in.webm", "-vn", "file:" + new): new,
                ("C:/x/ffmpeg.exe", "-y", "file:" + new): new,
                ("ffmpeg", "-i", "file:in.webm", "file:" + old): None,     # already there
                ("ffprobe", "-show_streams", "file:" + new): None,
                ("ffprobe.exe", "-show_streams", "file:" + new): None,
                ("ffmpeg", "-i", "file:" + new): None,                    # only reads it
                ("ffmpeg", "-i", "file:in.webm", "-"): None,              # stdout
                ("aria2c", "https://example.com/v"): None,
                ("ffmpeg",): None,
            }
            for args, expected in cases.items():
                with self.subTest(args):
                    self.assertEqual(engine._new_ffmpeg_output(list(args)), expected)

    def test_yt_dlp_warnings_are_logged(self):
        job = self.run_job(make_fake_ydl(streams=1, warn="[youtube] No JS runtime"))
        self.assertIn("⚠ [youtube] No JS runtime", job.log)

    def test_preview_without_downloading(self):
        class FakeYDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=True, process=True, ie_key=None):
                assert not download and not process
                if url == "https://link":
                    return {"_type": "url", "url": "https://video"}
                return {"title": "T", "duration": 61, "channel": "c"}

        with mock.patch.object(engine, "yt_dlp", fake_yt_dlp(FakeYDL)), \
                mock.patch.object(engine, "fetch_thumbnail", return_value=None):
            self.assertEqual(engine.preview_link("https://link", False),
                             ("T\n1:01 · c", None, True))

    def test_canceled_preview(self):
        import threading

        cancel, pages = threading.Event(), []

        class FakeYDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=True, process=True, ie_key=None):
                for page in range(10):   # yt-dlp's status messages
                    pages.append(page)
                    if url == "https://slow" and page == 1:
                        cancel.set()
                    self.opts["logger"].debug(f"Downloading page {page}")
                if url == "https://late":
                    cancel.set()
                return {"title": "T", "thumbnail": "https://t/x.jpg"}

        with mock.patch.object(engine, "yt_dlp", fake_yt_dlp(FakeYDL)), \
                mock.patch.object(engine, "fetch_thumbnail") as fetch:
            self.assertIsNone(engine.preview_link("https://slow", False, cancel))
            self.assertEqual(pages, [0, 1])
            cancel.clear()
            self.assertIsNone(engine.preview_link("https://late", False, cancel))
        fetch.assert_not_called()


class DownloadQueueTest(unittest.TestCase):
    """Several links in a row, with a fake yt-dlp: no window, no Tk."""

    def run_queue(self, fake, urls, *, ask=None, whole_playlist=True, on_queue=None):
        progress, log = [], []
        queue = engine.DownloadQueue(
            urls, fmt="mp4_best", folder="dl", ffmpeg="ffmpeg",
            whole_playlist=whole_playlist, ask=ask or (lambda question: True),
            on_progress=lambda pct, message: progress.append(message),
            on_log=log.append)
        if on_queue:
            on_queue(queue)
        with mock.patch.object(engine, "yt_dlp", fake_yt_dlp(fake)):
            queue.run()
        queue.progress, queue.log = progress, log
        return queue

    @staticmethod
    def failing_on(*bad_urls):
        fake = make_fake_ydl(streams=1)

        class Fake(fake):
            def download(self, urls):
                if urls[0] in bad_urls:
                    raise RuntimeError(f"no video at {urls[0]}")
                super().download(urls)

        return Fake

    def test_one_link_is_just_a_job(self):
        queue = self.run_queue(make_fake_ydl(streams=2), ["https://a"])
        self.assertEqual(queue.outcome, "done")
        self.assertEqual(queue.summary(), "Download complete!")
        self.assertFalse(any(m.startswith("Link") for m in queue.progress))
        self.assertNotIn("▶ Link 1/1: https://a", queue.log)

    def test_links_download_in_order(self):
        queue = self.run_queue(make_fake_ydl(streams=1), ["https://a", "https://b"])
        self.assertEqual([job.url for job in queue.jobs], ["https://a", "https://b"])
        self.assertEqual(queue.summary(), "Done: 2 saved")
        self.assertEqual(queue.log[0], "▶ Link 1/2: https://a")
        self.assertTrue(queue.progress[-1].startswith("Link 2/2 · "), queue.progress[-1])
        self.assertEqual(queue.last_file, "v0.mp4")
        self.assertFalse(queue.had_problems)

    def test_a_failed_link_doesnt_stop_the_rest(self):
        queue = self.run_queue(self.failing_on("https://b"),
                               ["https://a", "https://b", "https://c"])
        self.assertEqual([job.outcome for job in queue.jobs], ["done", "failed", "done"])
        self.assertEqual(queue.outcome, "done")
        self.assertEqual(queue.summary(), "Done: 2 saved, 1 link failed")
        self.assertIn("✘ Error: no video at https://b", queue.log)
        self.assertTrue(queue.had_problems)

    def test_an_unavailable_video_counts_once(self):
        fake = make_fake_ydl(streams=1)

        class Fake(fake):
            def download(self, urls):
                if urls == ["https://gone"]:   # what yt-dlp does for one video
                    self.opts["logger"].error("ERROR: [youtube] gone: unavailable")
                    return
                super().download(urls)

        queue = self.run_queue(Fake, ["https://a", "https://gone"])
        self.assertEqual(queue.summary(), "Done: 1 saved, 1 link failed")
        self.assertEqual(queue.log.count("✘ [youtube] gone: unavailable"), 1)

    def test_all_links_failed(self):
        queue = self.run_queue(self.failing_on("https://a", "https://b"),
                               ["https://a", "https://b"])
        self.assertEqual(queue.outcome, "failed")
        self.assertEqual(queue.error, "no video at https://a")
        self.assertTrue(queue.error_logged)
        self.assertEqual(queue.summary(), "Nothing downloaded: 2 links failed")

    def test_declined_playlists(self):
        fake = make_fake_ydl(streams=1, probe=("playlist", "video"))
        queue = self.run_queue(fake, ["https://list", "https://video"],
                               whole_playlist=False, ask=lambda q: False)
        self.assertEqual(queue.summary(), "Done: 1 saved, 1 playlist not downloaded")
        fake = make_fake_ydl(streams=1, probe=("playlist", "playlist"))
        queue = self.run_queue(fake, ["https://l1", "https://l2"],
                               whole_playlist=False, ask=lambda q: False)
        self.assertEqual(queue.outcome, "declined")
        self.assertEqual(queue.summary(), "Nothing downloaded: 2 playlists not downloaded")

    def test_cancel_skips_the_remaining_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            holder = {}

            def on_progress(video, stream, done):
                if (stream, done) == (0, 50):
                    holder["queue"].cancel()
            fake = make_fake_ydl(streams=1, files_dir=tmp, on_progress=on_progress)
            queue = self.run_queue(fake, ["https://a", "https://b", "https://c"],
                                   on_queue=lambda q: holder.update(queue=q))
            self.assertEqual(os.listdir(tmp), [], "the partial file is removed")
        self.assertEqual(len(queue.jobs), 1, "links 2 and 3 never started")
        self.assertEqual(queue.outcome, "canceled")
        self.assertEqual(queue.summary(), "Download canceled (1 partial file removed).")

    @unittest.skipUnless(engine.YT_DLP_AVAILABLE, "needs yt-dlp")
    def test_cancel_stops_the_running_links_ffmpeg(self):
        # What the window's Cancel calls: the queue, not the job.
        import yt_dlp.postprocessor.ffmpeg as ffmpeg_pp

        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "v0.mp3")
            holder = {}

            class Fake(make_fake_ydl(streams=1)):
                def _run(self):
                    cancel_when(holder["queue"], lambda: os.path.exists(out))
                    ffmpeg_pp.Popen.run([sys.executable, "-c",
                                         "import sys, time; open(sys.argv[1][5:], 'wb');"
                                         " time.sleep(60)", "file:" + out])

            start = time.monotonic()
            queue = self.run_queue(Fake, ["https://a", "https://b"],
                                   on_queue=lambda q: holder.update(queue=q))
            self.assertLess(time.monotonic() - start, 10)
            self.assertEqual(os.listdir(tmp), [])
        self.assertEqual(len(queue.jobs), 1, "the second link never started")
        self.assertEqual(queue.summary(), "Download canceled (1 partial file removed).")

    def test_cancel_after_the_last_link_finished(self):
        # Clicked in the moment between the end and the window noticing it.
        queue = self.run_queue(make_fake_ydl(streams=1), ["https://a", "https://b"])
        queue.cancel()
        self.assertEqual(queue.outcome, "done")
        self.assertEqual(queue.summary(), "Done: 2 saved")

    def test_a_crashing_link_is_a_failed_link(self):
        run = engine.DownloadJob.run

        def crash_on_b(job):
            if job.url == "https://b":
                raise KeyError("boom")
            return run(job)

        with mock.patch.object(engine.DownloadJob, "run", crash_on_b):
            queue = self.run_queue(make_fake_ydl(streams=1),
                                   ["https://a", "https://b", "https://c"])
        self.assertEqual([job.outcome for job in queue.jobs], ["done", "failed", "done"])
        self.assertIn("✘ Error: Unexpected error: KeyError: 'boom'", queue.log)
        self.assertEqual(queue.summary(), "Done: 2 saved, 1 link failed")

    def test_no_outcome_before_it_runs(self):
        queue = engine.DownloadQueue(["https://a"], on_progress=None, on_log=None)
        self.assertIsNone(queue.outcome)


if __name__ == "__main__":
    unittest.main()
