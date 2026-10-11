"""Checks that worker threads never touch Tk directly.

Run: python -m unittest discover -s tests -v
"""
import gc
import json
import os
import sys
import tempfile
import threading
import time
import tkinter
import types
import unittest
from typing import ClassVar
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import youtube_downloader as ytd

GUARDED = [
    (tkinter.Variable, "get"), (tkinter.Variable, "set"),
    (tkinter.Misc, "configure"), (tkinter.Misc, "config"),
    (tkinter.Misc, "after"), (tkinter.Misc, "after_cancel"),
    (tkinter.Text, "insert"), (tkinter.Text, "see"),
]


class FakeCancelled(Exception):
    """Stands in for yt_dlp.utils.DownloadCancelled."""


def make_fake_ydl(streams=2, videos=1, overshoot=0, error=None, fail=(),
                  already=(), probe=("video",), files_dir=None, on_progress=None,
                  warn=None):
    """probe: the _type returned by each extract_info(process=False) call.

    files_dir: write real stream files there (.part while downloading, then
    renamed, then "merged" into v<N>.mp4), as yt-dlp does.
    on_progress(video, stream, done): called on the download thread before
    each progress event, and with stream=-1 before each video starts.
    """
    probes = list(probe)
    on_progress = on_progress or (lambda *a: None)

    class FakeYDL:
        calls: ClassVar[list[str]] = []   # shared: one list per fake class

        def __init__(self, opts):
            self.opts = FakeYDL.last_opts = opts
            self.pps = []

        def add_post_processor(self, pp, when="post_process"):
            self.pps.append((pp, when))

        def run_pps(self, when, info):
            for pp, pp_when in self.pps:
                if pp_when == when:
                    pp.run(info)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True, process=True, ie_key=None):
            FakeYDL.calls.append("probe")
            return {"_type": probes.pop(0), "title": "Fake list",
                    "url": "https://example.com/next", "ie_key": "Fake"}

        def process_ie_result(self, info, download=True):
            FakeYDL.calls.append("download")
            self._run()

        def download(self, urls):
            FakeYDL.calls.append("download")
            self._run()

        def _run(self):
            hooks = self.opts["progress_hooks"]
            for video in range(videos):
                info = {"requested_formats": [{}] * streams} if streams > 1 else {}
                if videos > 1:
                    info.update(playlist_index=video + 1, n_entries=videos)
                if video in fail:
                    # What yt-dlp does for an unavailable entry.
                    self.opts["logger"].error(f"ERROR: [youtube] v{video}: Private video")
                    if not self.opts.get("ignoreerrors"):
                        raise RuntimeError("Private video")
                    continue
                if warn and video == 0:
                    self.opts["logger"].warning(warn)
                on_progress(video, -1, 0)
                self.run_pps("before_dl", info)
                names = []
                for stream in range(streams):
                    name = f"v{video}.f{stream}.webm"
                    files = {}
                    if files_dir:
                        name = os.path.join(files_dir, name)
                        files = {"filename": name, "tmpfilename": name + ".part"}
                        open(name + ".part", "wb").close()
                    names.append(name)
                    # total_bytes_estimate can be exceeded (overshoot).
                    for done in range(0, 101 + overshoot, 5):
                        on_progress(video, stream, done)
                        for hook in hooks:
                            hook({"status": "downloading", "video": video,
                                  "total_bytes_estimate": 100,
                                  "downloaded_bytes": done, "speed": 1e6,
                                  "eta": 1, "info_dict": info, **files})
                        time.sleep(0.002)
                    if files_dir:
                        os.replace(name + ".part", name)
                    for hook in hooks:
                        hook({"status": "finished", "video": video,
                              "filename": name})
                final = f"v{video}.mp4"
                if files_dir:
                    final = os.path.join(files_dir, final)
                    open(final, "wb").close()
                    for name in names:
                        os.remove(name)
                self.run_pps("after_move", {
                    **info, "filepath": final,
                    "__real_download": video not in already})
            if error:
                raise error

    return FakeYDL


class ThreadSafetyTest(unittest.TestCase):
    def setUp(self):
        # Collect destroyed apps from earlier tests here: on a worker thread
        # their Variable.__del__ blocks ~1 s per variable on the dead Tcl.
        gc.collect()
        self.violations = []
        main = threading.main_thread()
        for cls, name in GUARDED:
            orig = getattr(cls, name)

            def wrapper(*a, _orig=orig, _name=f"{cls.__name__}.{name}", **k):
                if threading.current_thread() is not main:
                    self.violations.append(_name)
                return _orig(*a, **k)

            patcher = mock.patch.object(cls, name, wrapper)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.dialogs = []
        for name in ("showinfo", "showerror", "showwarning"):
            patcher = mock.patch.object(
                ytd.messagebox, name,
                side_effect=lambda *a, _n=name, **k: self._on_dialog(_n))
            patcher.start()
            self.addCleanup(patcher.stop)

        # The fake yt_dlp module makes the real one unnecessary; without this
        # __init__ would schedule a real (blocking) install prompt.
        # Remembered settings go to a temporary file, never the real one.
        settings_dir = tempfile.TemporaryDirectory()
        self.addCleanup(settings_dir.cleanup)
        self.settings_file = os.path.join(settings_dir.name, "settings.json")
        for patcher in (
                mock.patch.object(ytd, "YT_DLP_AVAILABLE", True),
                mock.patch.object(ytd, "find_ffmpeg", return_value="ffmpeg"),
                mock.patch.object(ytd, "settings_path",
                                  return_value=self.settings_file),
                mock.patch.object(ytd.messagebox, "askyesno",
                                  side_effect=lambda *a, **k:
                                  self._on_dialog("askyesno"))):
            patcher.start()
            self.addCleanup(patcher.stop)

        try:
            self.app = ytd.YouTubeDownloader()
        except tkinter.TclError as exc:
            self.skipTest(f"no display: {exc}")
        self.addCleanup(self._destroy)
        self.callback_errors = []
        self.app.report_callback_exception = (
            lambda *exc: self.callback_errors.append(exc))
        self.progress = []
        orig_set_progress = self.app._set_progress

        def record_progress(pct, msg, color):
            self.progress.append(pct)
            orig_set_progress(pct, msg, color)

        self.app._set_progress = record_progress
        self.statuses = []
        orig_set_status = self.app._set_status

        def record_status(msg, color=ytd.TEXT_DIM):
            self.statuses.append(msg)
            orig_set_status(msg, color)

        self.app._set_status = record_status
        orig_declined = self.app._download_declined

        def declined():
            orig_declined()
            self.app.quit()

        self.app._download_declined = declined
        orig_canceled = self.app._download_canceled

        def canceled(removed):
            orig_canceled(removed)
            self.app.quit()

        self.app._download_canceled = canceled
        orig_done = self.app._download_done

        def done(success, error):   # a finished download has no pop-up
            orig_done(success, error)
            self.app.quit()

        self.app._download_done = done
        self.app.url_entry.delete(0, "end")
        self.app.url_entry.insert(0, "https://www.youtube.com/watch?v=test")
        self.on_dialog = lambda name: self.app.quit()

    def _on_dialog(self, name):
        self.dialogs.append(name)
        self.on_dialog(name)

    def _destroy(self):
        try:
            self.app.destroy()
        except tkinter.TclError:
            pass

    def run_download(self, fake):
        fake_module = types.SimpleNamespace(
            YoutubeDL=fake,
            postprocessor=types.SimpleNamespace(PostProcessor=object),
            utils=types.SimpleNamespace(DownloadCancelled=FakeCancelled))
        with mock.patch.object(ytd, "yt_dlp", fake_module, create=True):
            self.app.after(10, self.app._start_download)
            timeout = self.app.after(10000, self.app.quit)
            self.app.mainloop()
            try:
                self.app.after_cancel(timeout)
            except tkinter.TclError:
                pass

    def test_successful_download(self):
        self.run_download(make_fake_ydl(streams=2))
        self.assertEqual(self.dialogs, [])
        self.assertEqual(self.violations, [])
        self.assertEqual(self.callback_errors, [])
        self.assertEqual(self.app.progress_var.get(), 100)
        self.assertEqual(self.progress, sorted(self.progress),
                         "progress went backwards between streams")
        self.assertLessEqual(max(self.progress), 99,
                             "bar reached 100 before the done callback")
        self.assertEqual(self.progress[-1], 99)
        self.assertIsNone(self.app._after_id, "poll should stop when idle")
        log = self.app.log_box.get("1.0", "end")
        self.assertIn("Saved: v0.mp4", log)
        self.assertNotIn(".f0.webm", log, "logged a temporary stream file")

    def test_playlist_checkbox(self):
        self.assertIs(self.app._get_ydl_opts("ffmpeg")["noplaylist"], True)
        self.app.playlist_var.set(True)
        self.assertIs(self.app._get_ydl_opts("ffmpeg")["noplaylist"], False)

    def test_progress_restarts_per_playlist_video(self):
        seen = {}
        url = "https://www.youtube.com/playlist?list=x"
        opts = self.app._get_ydl_opts("ffmpeg")
        opts["progress_hooks"].append(
            lambda d: seen.setdefault(d["video"], []).append(
                self.app._latest_progress[0]))
        ydl = make_fake_ydl(streams=2, videos=2, overshoot=15)(opts)
        ydl.add_post_processor(ytd._stream_counter_class(object)(self.app),
                               when="before_dl")
        ydl.download([url])
        self.assertEqual(sorted(seen), [0, 1])
        for video, values in seen.items():
            self.assertEqual(values, sorted(values),
                             f"video {video} progress went backwards")
            self.assertEqual(values[0], 0, f"video {video} didn't restart")
            self.assertLessEqual(max(values), 99)
            self.assertEqual(values[-1], 99)

    def test_failed_download(self):
        self.run_download(make_fake_ydl(streams=1, error=RuntimeError("boom")))
        self.assertEqual(self.dialogs, ["showerror"])
        self.assertEqual(self.violations, [])
        self.assertIn("boom", self.app.status_var.get())
        self.assertIsNone(self.app._after_id)

    def test_close_while_error_dialog_open(self):
        self.on_dialog = lambda name: self.app._on_close()
        self.run_download(make_fake_ydl(streams=1, error=RuntimeError("boom")))
        self.assertEqual(self.dialogs, ["showerror"])
        self.assertTrue(self.app._closing)
        self.assertEqual(self.violations, [])
        self.assertEqual(self.callback_errors, [])

    def start_playlist(self, url="https://www.youtube.com/watch?v=test&list=x"):
        self.app.playlist_var.set(True)
        self.app.url_entry.delete(0, "end")
        self.app.url_entry.insert(0, url)

    def log(self):
        return self.app.log_box.get("1.0", "end")

    def test_whole_playlist(self):
        self.start_playlist()
        fake = make_fake_ydl(streams=2, videos=3)
        self.run_download(fake)
        self.assertEqual(fake.calls, ["download"], "ticked box shouldn't probe")
        self.assertEqual(self.dialogs, [])
        self.assertEqual(self.violations, [])
        for video in range(3):
            self.assertEqual(self.log().count(f"Saved: v{video}.mp4"), 1)
        self.assertEqual(self.statuses[-1], "✔ Done: 3 saved")

    # Directly, as coalescing can drop a whole video's updates in the UI.
    def test_status_names_the_playlist_entry(self):
        self.app._stream_index, self.app._stream_count = 0, 1
        entry = {"playlist_index": 3, "n_entries": 30}
        self.app._progress_hook({"status": "downloading", "total_bytes": 100,
                                 "downloaded_bytes": 45, "speed": 1e6, "eta": 1,
                                 "info_dict": entry})
        self.assertTrue(self.app._latest_progress[1].startswith(
            "Video 3/30 · Downloading… "), self.app._latest_progress)
        self.app._progress_hook({"status": "finished", "info_dict": entry})
        self.assertEqual(self.app._latest_progress[1], "Video 3/30 · Processing…")

    def test_playlist_skips_unavailable_entry(self):
        self.start_playlist()
        self.run_download(make_fake_ydl(streams=1, videos=3, fail={1}))
        self.assertEqual(self.dialogs, [])
        self.assertIn("Saved: v2.mp4", self.log())
        self.assertIn("✘ [youtube] v1: Private video", self.log())
        self.assertEqual(self.statuses[-1], "✔ Done: 2 saved, 1 skipped")

    def test_playlist_with_nothing_saved_fails(self):
        self.start_playlist()
        self.run_download(make_fake_ydl(streams=1, videos=2, fail={0, 1}))
        self.assertEqual(self.dialogs, ["showerror"])
        self.assertIn("Private video", self.statuses[-1])

    def test_already_downloaded_is_not_saved(self):
        self.start_playlist()
        self.run_download(make_fake_ydl(streams=1, videos=2, already={0}))
        self.assertIn("Already downloaded: v0.mp4", self.log())
        self.assertNotIn("Saved: v0.mp4", self.log())
        self.assertEqual(self.statuses[-1], "✔ Done: 1 saved, 1 already downloaded")

    def cancel_at(self, point):
        """on_progress callback that clicks Cancel when the fake reaches point."""
        def on_progress(*where):
            if where == point:
                self.app._post(self.app._cancel_download)
                self.app._cancel.wait(5)   # until the main thread clicked it
        return on_progress

    def files(self, folder):
        return sorted(os.listdir(folder))

    def test_cancel_removes_only_the_unfinished_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "unrelated.mp4"), "wb").close()
            # Video 1's first stream has finished, its second is half done.
            self.run_download(make_fake_ydl(
                streams=2, videos=2, files_dir=tmp,
                on_progress=self.cancel_at((1, 1, 50))))
            self.assertEqual(self.files(tmp), ["unrelated.mp4", "v0.mp4"])
        self.assertIn("Canceling…", self.statuses)
        self.assertEqual(self.statuses[-1],
                         "Download canceled (1 saved, 2 partial files removed).")
        self.assertEqual(self.dialogs, [])
        self.assertIn("Saved: ", self.log())
        self.assertEqual(self.app.dl_btn.cget("text"), "⬇  Download")
        self.assertEqual(str(self.app.dl_btn.cget("state")), "normal")
        self.assertFalse(self.app.is_running)
        self.assertIsNone(self.app._after_id, "poll should stop when idle")
        self.assertEqual(self.violations, [])
        self.assertEqual(self.callback_errors, [])

    def test_cancel_between_playlist_videos(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Clicked after video 0 finished, before video 1 starts.
            self.run_download(make_fake_ydl(
                streams=2, videos=3, files_dir=tmp,
                on_progress=self.cancel_at((1, -1, 0))))
            self.assertEqual(self.files(tmp), ["v0.mp4"])
        self.assertEqual(self.statuses[-1], "Download canceled (1 saved).")
        self.assertEqual(self.violations, [])

    def test_cancel_then_download_again(self):
        self.run_download(make_fake_ydl(
            streams=1, on_progress=self.cancel_at((0, 0, 30))))
        self.assertEqual(self.statuses[-1], "Download canceled.")
        # The button is Download again, and a new run isn't canceled.
        self.run_download(make_fake_ydl(streams=1))
        self.assertEqual(self.dialogs, [])
        self.assertEqual(self.statuses[-1], "✔ Download complete!")
        self.assertEqual(self.violations, [])

    def test_close_during_download_asks_then_cancels_and_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            def on_progress(*where):
                if where == (0, 1, 50):
                    self.app._post(self.app._on_close)
                    self.app._cancel.wait(5)   # until Yes was answered
            with mock.patch.object(ytd.messagebox, "askyesno",
                                   return_value=True) as ask:
                self.run_download(make_fake_ydl(
                    streams=2, files_dir=tmp, on_progress=on_progress))
            self.assertEqual(self.files(tmp), [], "partial files left behind")
        self.assertEqual(ask.call_args.args[0], "Download in progress")
        self.assertTrue(self.app._closing, "the app should have closed")
        self.assertEqual(self.dialogs, [])
        self.assertEqual(self.violations, [])
        self.assertEqual(self.callback_errors, [])

    def test_close_during_download_no_keeps_downloading(self):
        asked = threading.Event()

        def answer_no(*a, **k):
            asked.set()
            return False

        def on_progress(*where):
            if where == (0, 0, 50):
                self.app._post(self.app._on_close)
                asked.wait(5)
        with mock.patch.object(ytd.messagebox, "askyesno", side_effect=answer_no):
            self.run_download(make_fake_ydl(streams=1, on_progress=on_progress))
        self.assertTrue(asked.is_set())
        self.assertFalse(self.app._closing)
        self.assertEqual(self.dialogs, [])
        self.assertEqual(self.statuses[-1], "✔ Download complete!")

    def test_yt_dlp_warnings_reach_the_log(self):
        self.run_download(make_fake_ydl(
            streams=1, warn="[youtube] No supported JavaScript runtime"))
        self.assertIn("⚠ [youtube] No supported JavaScript runtime", self.log())
        self.assertEqual(self.violations, [])

    def test_placeholder_is_not_a_url(self):
        entry = self.app.url_entry
        entry.delete(0, "end")
        self.app._restore_placeholder()
        self.assertEqual(entry.get(), ytd.URL_PLACEHOLDER)
        self.assertEqual(str(entry.cget("fg")), ytd.TEXT_DIM)
        self.assertEqual(self.app._url(), "")
        self.app._start_download()
        self.assertEqual(self.dialogs, ["showwarning"])
        self.assertFalse(self.app.is_running)
        self.app._clear_placeholder()             # clicking into the box
        self.assertEqual(entry.get(), "")
        self.assertEqual(str(entry.cget("fg")), ytd.TEXT)
        entry.insert(0, "https://youtu.be/abc")
        self.assertEqual(self.app._url(), "https://youtu.be/abc")

    def test_paste_replaces_the_placeholder(self):
        self.app.url_entry.delete(0, "end")
        self.app._restore_placeholder()
        with mock.patch.object(self.app, "clipboard_get",
                               return_value="  https://youtu.be/abc \n"):
            self.app._paste_url()
        self.assertEqual(self.app._url(), "https://youtu.be/abc")
        self.assertFalse(self.app._placeholder_shown)

    def test_status_wraps_at_the_window_width(self):
        widths = []
        for size in ("900x600", "620x600"):
            self.app.geometry(size)
            self.app.update()
            widths.append(int(self.app.status_lbl.cget("wraplength")))
        self.assertGreater(widths[0], widths[1])
        self.assertLess(widths[1], 620)

    def look_up(self, *urls, extract, thumbnail=None, playlist=False):
        """Starts a preview for each url in turn (without waiting in between),
        with a fake YoutubeDL whose extract_info(url) returns extract(url),
        then waits for the lookups and returns the preview text."""
        class FakeYDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=True, process=True, ie_key=None):
                return extract(url)

        with mock.patch.object(ytd, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL),
                               create=True), \
                mock.patch.object(ytd, "fetch_thumbnail", return_value=thumbnail):
            self.app.playlist_var.set(playlist)
            for url in urls:
                self.app.url_entry.delete(0, "end")
                self.app.url_entry.insert(0, url)
                self.app._start_preview()
            deadline = time.time() + 5
            while self.app._active_workers and time.time() < deadline:
                self.app.update()
                time.sleep(0.01)
            self.app.update()
        return self.app.preview_text.get()

    def test_preview_of_a_video(self):
        text = self.look_up("https://www.youtube.com/watch?v=x", extract=lambda url: {
            "title": "Me at the zoo", "duration": 19, "channel": "jawed"})
        self.assertEqual(text, "Me at the zoo\n0:19 · jawed")
        self.assertEqual(str(self.app.preview_lbl.cget("fg")), ytd.TEXT)
        self.assertEqual(self.violations, [])

    def test_preview_follows_url_results_to_a_playlist(self):
        def extract(url):
            if url == "https://www.youtube.com/watch?v=x&list=y":
                return {"_type": "url", "url": "https://list", "ie_key": "Tab"}
            return {"_type": "playlist", "title": "List", "playlist_count": 12,
                    "uploader": "me"}
        text = self.look_up("https://www.youtube.com/watch?v=x&list=y",
                            extract=extract, playlist=True)
        self.assertEqual(text, "List\nPlaylist · 12 videos · me")

    def test_preview_of_a_bad_link(self):
        def extract(url):
            raise RuntimeError("Unsupported URL")
        text = self.look_up("https://example.com/nothing", extract=extract)
        self.assertEqual(text, "Couldn't load a preview for this link.")
        self.assertEqual(str(self.app.preview_lbl.cget("fg")), ytd.TEXT_DIM)
        self.assertEqual(self.look_up("not a link", extract=extract),
                         "No preview for this link.")

    def test_preview_drops_answers_for_an_older_link(self):
        def extract(url):
            if url.endswith("old"):
                time.sleep(0.3)   # answers after the newer link
            return {"title": url.rsplit("/", 1)[1], "duration": 60}
        text = self.look_up("https://e.com/old", "https://e.com/new", extract=extract)
        self.assertEqual(text, "new\n1:00")

    @unittest.skipIf(ytd.Image is None, "Pillow not installed")
    def test_preview_shows_the_thumbnail(self):
        image = ytd.Image.new("RGB", ytd.THUMB_SIZE, "red")
        self.look_up("https://www.youtube.com/watch?v=x", thumbnail=image,
                     extract=lambda url: {"title": "T", "duration": 5})
        self.assertIsNotNone(self.app._preview_image)
        self.assertTrue(str(self.app.preview_img.cget("image")))
        self.assertEqual(self.violations, [])

    def test_folder_and_format_are_remembered(self):
        with tempfile.TemporaryDirectory() as folder:
            self.app.download_path.set(folder)
            self.app.format_var.set("mp3")
            self.run_download(make_fake_ydl(streams=1))
            again = ytd.YouTubeDownloader()   # the next time the app starts
            try:
                self.assertEqual(again.download_path.get(), folder)
                self.assertEqual(again.format_var.get(), "mp3")
                self.assertFalse(again.playlist_var.get())
            finally:
                again.destroy()

    def test_settings_are_saved_on_close(self):
        self.app.format_var.set("m4a")
        self.app._on_close()
        with open(self.settings_file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["format"], "m4a")

    def test_bad_saved_settings_fall_back_to_defaults(self):
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump({"folder": "/no/such/folder", "format": "bogus"}, f)
        again = ytd.YouTubeDownloader()
        try:
            self.assertEqual(again.download_path.get(),
                             os.path.expanduser("~/Downloads"))
            self.assertEqual(again.format_var.get(), "mp4_best")
        finally:
            again.destroy()

    def test_open_folder_after_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_download(make_fake_ydl(streams=2, files_dir=tmp))
            self.app.update()
            self.assertTrue(self.app.open_btn.winfo_ismapped())
            with mock.patch.object(ytd, "reveal_in_file_manager") as reveal:
                self.app.open_btn.invoke()
            reveal.assert_called_once_with(os.path.join(tmp, "v0.mp4"))
        # The next download hides it again, and a failed one doesn't show it.
        self.run_download(make_fake_ydl(streams=1, error=RuntimeError("boom")))
        self.app.update()
        self.assertFalse(self.app.open_btn.winfo_ismapped())
        self.assertEqual(self.violations, [])

    def test_single_video_error_is_reported_once(self):
        self.run_download(make_fake_ydl(streams=1, fail={0}))
        self.assertEqual(self.dialogs, ["showerror"])
        self.assertEqual(self.log().count("Private video"), 1)
        self.assertIn("Private video", self.statuses[-1])

    def test_single_video_does_not_ask(self):
        fake = make_fake_ydl(streams=1)
        self.run_download(fake)
        self.assertEqual(fake.calls, ["probe", "download"])
        self.assertEqual(self.dialogs, [])

    def test_asks_before_downloading_a_playlist(self):
        fake = make_fake_ydl(streams=1, videos=2, probe=("playlist",))
        with mock.patch.object(ytd.messagebox, "askyesno", return_value=False) as ask:
            self.run_download(fake)
        ask.assert_called_once()
        self.assertIn('"Fake list" is a playlist or channel', ask.call_args.args[1])
        self.assertEqual(fake.calls, ["probe"])
        self.assertFalse(self.app.playlist_var.get())
        self.assertFalse(self.app.is_running)
        self.assertEqual(self.statuses[-1], "Playlist not downloaded.")

        fake = make_fake_ydl(streams=1, videos=2, probe=("playlist",))
        with mock.patch.object(ytd.messagebox, "askyesno", return_value=True):
            self.run_download(fake)
        self.assertEqual(fake.calls, ["probe", "download"])
        self.assertEqual(self.statuses[-1], "✔ Done: 2 saved")
        # Yes is for this download only: a later video-in-a-Mix link must
        # still get just that video, and be probed again.
        self.assertFalse(self.app.playlist_var.get())
        self.app.url_entry.delete(0, "end")
        self.app.url_entry.insert(0, "https://www.youtube.com/watch?v=t&list=RDx")
        fake = make_fake_ydl(streams=1)
        self.run_download(fake)
        self.assertEqual(fake.calls, ["probe", "download"])
        self.assertIs(fake.last_opts["noplaylist"], True)
        self.assertEqual(self.violations, [])
        self.assertEqual(self.callback_errors, [])

    def test_follows_url_results_before_asking(self):
        # e.g. a channel's home page redirecting to its Videos tab.
        fake = make_fake_ydl(streams=1, videos=2, probe=("url", "playlist"))
        with mock.patch.object(ytd.messagebox, "askyesno", return_value=True) as ask:
            self.run_download(fake)
        ask.assert_called_once()
        self.assertEqual(fake.calls, ["probe", "probe", "download"])


if __name__ == "__main__":
    unittest.main()
