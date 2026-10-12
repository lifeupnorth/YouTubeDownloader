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
import tkinter.messagebox
import types
import unittest
from unittest import mock

TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(TESTS), TESTS]   # the app, and tests/fakes.py
from fakes import fake_yt_dlp, make_fake_ydl  # noqa: E402

import download_engine as engine  # noqa: E402
import youtube_downloader as ytd  # noqa: E402

GUARDED = [
    (tkinter.Variable, "get"), (tkinter.Variable, "set"),
    (tkinter.Misc, "configure"), (tkinter.Misc, "config"),
    (tkinter.Misc, "after"), (tkinter.Misc, "after_cancel"),
    (tkinter.Text, "insert"), (tkinter.Text, "see"),
]

DIALOGS = ("showinfo", "showerror", "showwarning", "askyesno")


def _in_finalizer():
    # Garbage collection can run a Tk object's __del__ on any thread; that's
    # Python's doing, not the app's.
    frame = sys._getframe(2)
    for _ in range(4):
        if frame is None:
            return False
        if frame.f_code.co_name == "__del__":
            return True
        frame = frame.f_back
    return False


class GuardedTcl:
    """Wraps the Tcl interpreter (what every widget, variable and image calls
    through), so any Tk call from a worker thread is caught, not only the
    methods in GUARDED. Tk's own thread handling would otherwise hide it."""

    def __init__(self, tkapp, on_call):
        self._tkapp, self._on_call = tkapp, on_call

    def __getattr__(self, name):
        attr = getattr(self._tkapp, name)
        if not callable(attr):
            return attr
        on_call = self._on_call

        def guarded(*a, **k):
            on_call(f"tcl.{name}")
            return attr(*a, **k)
        return guarded


class _GuardedDialog:
    def __init__(self, name, dialog, on_call):
        self.name, self.dialog, self.on_call = name, dialog, on_call

    def __call__(self, *a, **k):
        self.on_call(f"messagebox.{self.name}")
        return self.dialog(*a, **k)


class GuardedDialogs:
    """Stands in for the app's messagebox module. Whatever a test puts in
    its place (a mock with an answer, say) still gets the thread check."""

    def __init__(self, on_call):
        object.__setattr__(self, "_on_call", on_call)
        for name in DIALOGS:
            setattr(self, name, getattr(tkinter.messagebox, name))

    def __setattr__(self, name, value):
        # (A check by type: a MagicMock answers any attribute.)
        if not isinstance(value, _GuardedDialog):   # not mock.patch putting one back
            value = _GuardedDialog(name, value, self._on_call)
        object.__setattr__(self, name, value)


class ThreadSafetyTest(unittest.TestCase):
    def setUp(self):
        # Collect destroyed apps from earlier tests here: on a worker thread
        # their Variable.__del__ blocks ~1 s per variable on the dead Tcl.
        gc.collect()
        self.violations = []
        main = threading.main_thread()

        def check_thread(name):
            if threading.current_thread() is not main and not _in_finalizer():
                self.violations.append(name)

        for cls, name in GUARDED:
            orig = getattr(cls, name)

            def wrapper(*a, _orig=orig, _name=f"{cls.__name__}.{name}", **k):
                check_thread(_name)
                return _orig(*a, **k)

            patcher = mock.patch.object(cls, name, wrapper)
            patcher.start()
            self.addCleanup(patcher.stop)

        create = tkinter._tkinter.create
        for patcher in (
                mock.patch.object(tkinter._tkinter, "create",
                                  lambda *a: GuardedTcl(create(*a), check_thread)),
                mock.patch.object(ytd, "messagebox", GuardedDialogs(check_thread))):
            patcher.start()
            self.addCleanup(patcher.stop)

        # An exception on any thread fails the test, even one the window
        # handled (it logs worker crashes; see _run_worker).
        self.thread_errors = []
        self.expect_crash = False
        hook = threading.excepthook
        threading.excepthook = lambda args: self.thread_errors.append(args.exc_value)
        self.addCleanup(setattr, threading, "excepthook", hook)
        self.addCleanup(self._check_no_errors)

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
                mock.patch.object(engine, "YT_DLP_AVAILABLE", True),
                mock.patch.object(engine, "find_ffmpeg", return_value="ffmpeg"),
                mock.patch.object(engine, "find_js_runtime", return_value=("deno", "deno")),
                mock.patch.object(engine, "check_for_update", return_value=None),
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
        self.crashes = []
        orig_crashed = self.app._worker_crashed

        def crashed(details):
            self.crashes.append(details)
            with mock.patch.object(sys, "stderr", None):   # no traceback in the output
                orig_crashed(details)

        self.app._worker_crashed = crashed
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
        orig_finished = self.app._download_finished

        def finished(job):   # a finished download has no pop-up to wait for
            orig_finished(job)
            self.app.quit()

        self.app._download_finished = finished
        self.app._replace_url_text("https://www.youtube.com/watch?v=test")
        self.on_dialog = lambda name: self.app.quit()

    def _on_dialog(self, name):
        self.dialogs.append(name)
        self.on_dialog(name)

    def _destroy(self):
        try:
            self.app.destroy()
        except tkinter.TclError:
            pass

    # After every test, whether or not it checked these itself.
    def _check_no_errors(self):
        self.assertEqual(self.violations, [], "Tk used from a worker thread")
        self.assertEqual(self.thread_errors, [], "an exception on a worker thread")
        if not self.expect_crash:
            self.assertEqual(self.crashes, [], "a worker crashed")

    def run_download(self, fake):
        timed_out = []

        def give_up():
            timed_out.append(True)
            self.app.quit()

        with mock.patch.object(engine, "yt_dlp", fake_yt_dlp(fake)):
            self.app.after(10, self.app._start_download)
            timeout = self.app.after(10000, give_up)
            self.app.mainloop()
            try:
                self.app.after_cancel(timeout)
            except tkinter.TclError:
                pass
        if timed_out:
            self.fail("the download didn't finish within 10 s")

    def test_js_runtime_is_looked_up_on_a_worker(self):
        threads = []

        def find():
            threads.append(threading.current_thread())
            return ("node", "node")

        with mock.patch.object(engine, "find_js_runtime", find):
            self.app._js_runtime = None
            self.app._start_worker(self.app._find_js_runtime_thread)
            deadline = time.monotonic() + 5
            while self.app._js_runtime is None and time.monotonic() < deadline:
                self.app.update()
                time.sleep(0.01)
        self.assertEqual(self.app._js_runtime, ("node", "node"))
        self.assertIsNot(threads[0], threading.main_thread())
        self.assertEqual(self.violations, [])

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
        self.app._replace_url_text(url)

    def log(self):
        return self.app.log_box.get("1.0", "end")

    def wait_until_canceled(self, timeout=5):
        """On the download thread: waits until the job has been canceled."""
        deadline = time.time() + timeout
        while not self.app._queue.canceled and time.time() < deadline:
            time.sleep(0.01)

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
                self.wait_until_canceled()   # until the main thread clicked it
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
                    self.wait_until_canceled()   # until Yes was answered
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
            streams=1, warn="[youtube] Some formats are missing"))
        self.assertIn("⚠ [youtube] Some formats are missing", self.log())
        self.assertEqual(self.violations, [])

    def test_update_notice_is_logged(self):
        with mock.patch.object(engine, "fetch_update_notice",
                               return_value="• yt-dlp 2099.1.1 is out"):
            self.app._start_worker(self.app._update_thread)
            deadline = time.monotonic() + 5
            while "2099.1.1" not in self.log() and time.monotonic() < deadline:
                self.app.update()
                time.sleep(0.01)
        self.assertIn("• yt-dlp 2099.1.1 is out", self.log())
        self.assertEqual(self.violations, [])

    def test_placeholder_is_not_a_url(self):
        entry = self.app.url_entry
        self.app._replace_url_text("")
        self.app._restore_placeholder()
        self.assertEqual(self.app._url_text(), ytd.URL_PLACEHOLDER)
        self.assertEqual(str(entry.cget("fg")), ytd.TEXT_DIM)
        self.assertEqual(self.app._urls(), [])
        self.app._start_download()
        self.assertEqual(self.dialogs, ["showwarning"])
        self.assertFalse(self.app.is_running)
        self.app._clear_placeholder()             # clicking into the box
        self.assertEqual(self.app._url_text(), "")
        self.assertEqual(str(entry.cget("fg")), ytd.TEXT)
        entry.insert("1.0", "https://youtu.be/abc")
        self.assertEqual(self.app._url(), "https://youtu.be/abc")

    def test_paste_replaces_the_placeholder(self):
        self.app._replace_url_text("")
        self.app._restore_placeholder()
        with mock.patch.object(self.app, "clipboard_get",
                               return_value="  https://youtu.be/abc \n"), \
                mock.patch.object(self.app, "_schedule_preview"):
            self.app._paste_url()
        self.assertEqual(self.app._urls(), ["https://youtu.be/abc"])
        self.assertFalse(self.app._placeholder_shown)

    def paste(self, clip):
        with mock.patch.object(self.app, "clipboard_get", return_value=clip), \
                mock.patch.object(self.app, "_schedule_preview"):
            self.app._paste_url()

    def test_several_links_one_per_line(self):
        self.app._replace_url_text("")
        self.paste("https://a https://b\nhttps://a\n\nhttps://c")
        # Pasted links go one per line, without repeats; the box grows.
        self.assertEqual(self.app._urls(), ["https://a", "https://b", "https://c"])
        self.assertEqual(self.app._url_text(), "https://a\nhttps://b\nhttps://c")
        self.assertEqual(int(self.app.url_entry.cget("height")), 3)
        self.app._replace_url_text("https://a\nhttps://b")
        self.assertEqual(int(self.app.url_entry.cget("height")), 2)
        self.app._replace_url_text("\n".join(f"https://{n}" for n in range(9)))
        self.assertEqual(int(self.app.url_entry.cget("height")), 4, "at most 4")

    def test_paste_adds_to_the_links_already_there(self):
        self.app._replace_url_text("https://a")
        self.paste("https://b")
        self.paste("https://a")   # already there
        self.assertEqual(self.app._url_text(), "https://a\nhttps://b")

    def test_paste_keeps_only_the_links(self):
        self.app._replace_url_text("")
        self.paste('Watch this: "youtu.be/abc", and https://e.com/v!')
        self.assertEqual(self.app._urls(), ["https://youtu.be/abc", "https://e.com/v!"])
        before = self.app._url_text()
        self.paste("no links here")
        self.assertEqual(self.app._url_text(), before)
        self.assertEqual(self.statuses[-1], "The clipboard has no link to paste.")

    def test_words_that_arent_links_are_left_out(self):
        self.app._replace_url_text("Watch https://www.youtube.com/watch?v=test now")
        self.run_download(make_fake_ydl(streams=1))
        self.assertIn("• Not links, so left out: Watch now", self.log())
        self.assertEqual([job.url for job in self.app._queue.jobs],
                         ["https://www.youtube.com/watch?v=test"])

    def test_undo_doesnt_bring_back_the_placeholder(self):
        self.app._replace_url_text("")
        self.app._restore_placeholder()
        self.app._clear_placeholder()
        try:
            self.app.url_entry.edit_undo()
        except tkinter.TclError:   # nothing to undo
            pass
        self.assertEqual(self.app._url_text(), "")

    def test_progress_after_cancel_doesnt_replace_canceling(self):
        self.app.is_running = True
        self.app._queue = mock.Mock(canceled=True)
        try:
            self.app._set_status("Canceling…")
            self.app._latest_progress = (50, "Downloading… 50%", ytd.WARNING)
            self.app._poll_ui_queue()
            self.assertEqual(self.app.status_var.get(), "Canceling…")
        finally:
            self.app.is_running = False

    def test_no_playlist_question_after_cancel(self):
        self.app._queue = mock.Mock(canceled=True)
        answers = []
        worker = threading.Thread(
            target=lambda: answers.append(self.app._ask_on_main("Playlist", "All of it?")))
        worker.start()
        deadline = time.monotonic() + 5
        while worker.is_alive() and time.monotonic() < deadline:
            self.app.update()
            time.sleep(0.01)
        self.assertEqual(answers, [False])
        self.assertEqual(self.dialogs, [], "the question still showed")

    def test_enter_downloads_instead_of_adding_a_line(self):
        with mock.patch.object(self.app, "_start_download") as start:
            self.assertEqual(self.app._on_url_enter(), "break")
        start.assert_called_once_with()

    def test_queue_of_links(self):
        self.app.playlist_var.set(True)   # downloads without asking each time
        self.app._replace_url_text("https://a\nhttps://b\nhttps://c")
        self.run_download(make_fake_ydl(streams=1))
        self.assertEqual(self.statuses[-1], "✔ Done: 3 saved")
        for n, url in enumerate(("https://a", "https://b", "https://c"), 1):
            self.assertIn(f"▶ Link {n}/3: {url}", self.log())
        self.assertIn("▶ 3 links", self.log())
        self.assertEqual(self.dialogs, [])
        self.assertEqual(self.violations, [])

    def test_queue_goes_on_after_a_failed_link(self):
        fake = make_fake_ydl(streams=1)

        class FailsOnB(fake):
            def download(self, urls):
                if urls == ["https://b"]:
                    raise RuntimeError("no such video")
                super().download(urls)

        self.app.playlist_var.set(True)
        self.app._replace_url_text("https://a\nhttps://b\nhttps://c")
        self.run_download(FailsOnB)
        self.assertEqual(self.statuses[-1], "✔ Done: 2 saved, 1 link failed")
        self.assertIn("✘ Error: no such video", self.log())
        self.assertEqual(self.dialogs, [], "a partial success has no error pop-up")
        self.assertEqual(str(self.app.status_lbl.cget("fg")), ytd.WARNING)

    def test_layout_scales_on_a_high_dpi_screen(self):
        # At 192 DPI (200%) the pixel sizes double; fonts scale by themselves.
        with mock.patch.object(ytd.YouTubeDownloader, "winfo_fpixels", return_value=192.0):
            big = ytd.YouTubeDownloader(update_check=False)
        try:
            self.assertEqual(big.scale, 2.0)
            for app, factor in ((self.app, 1), (big, 2)):
                thumb = app.preview_img.master
                card = app.dl_btn.master
                self.assertEqual((int(thumb.cget("width")), int(thumb.cget("height"))),
                                 (96 * factor, 54 * factor))
                self.assertEqual(int(str(card.cget("padx"))), 28 * factor)
                self.assertEqual(int(card.grid_info()["padx"]), 20 * factor)
                self.assertEqual(app._px(100), 100 * factor)
            # Sizes in characters aren't pixels: the URL box stays 1 wide.
            self.assertEqual(int(big.url_entry.cget("width")), 1)
        finally:
            big.destroy()

    def test_nearly_96_dpi_isnt_scaled(self):
        self.assertEqual(self.app.scale, 1.0)   # Tk reports e.g. 96.08

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

        with mock.patch.object(engine, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL)), \
                mock.patch.object(engine, "fetch_thumbnail", return_value=thumbnail):
            self.app.playlist_var.set(playlist)
            for url in urls:
                self.app._replace_url_text(url)
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
                         "That doesn't look like a link.")

    def test_preview_drops_answers_for_an_older_link(self):
        def extract(url):
            if url.endswith("old"):
                time.sleep(0.3)   # answers after the newer link
            return {"title": url.rsplit("/", 1)[1], "duration": 60}
        text = self.look_up("https://e.com/old", "https://e.com/new", extract=extract)
        self.assertEqual(text, "new\n1:00")

    def test_late_answer_for_an_older_link_is_dropped(self):
        # A lookup that doesn't stop when canceled (e.g. stuck in a request)
        # answers after the newer one; the token keeps it from showing.
        new_shown = threading.Event()
        done = self.app._preview_done

        def preview_done(text, *rest):
            done(text, *rest)
            if text == "new":
                new_shown.set()

        def preview_link(url, whole_playlist, cancel_event, js_runtime=None, thumb_size=None):
            if url.endswith("old"):
                new_shown.wait(5)   # ignores cancel_event on purpose
                return "old", None, True
            return "new", None, True

        self.app._preview_done = preview_done
        with mock.patch.object(engine, "preview_link", preview_link),                 mock.patch.object(engine, "YT_DLP_AVAILABLE", True):
            for url in ("https://e.com/old", "https://e.com/new"):
                self.app._replace_url_text(url)
                self.app._start_preview()
            deadline = time.time() + 5
            while self.app._active_workers and time.time() < deadline:
                self.app.update()
                time.sleep(0.01)
        self.assertTrue(new_shown.is_set())
        self.assertEqual(self.app.preview_text.get(), "new")
        self.assertEqual(self.violations, [])

    def test_newer_link_cancels_the_older_lookup(self):
        events = {}

        def preview_link(url, whole_playlist, cancel_event, js_runtime=None, thumb_size=None):
            events[url] = cancel_event
            if url.endswith("old") and cancel_event.wait(5):
                return None
            return url.rsplit("/", 1)[1], None, True

        with mock.patch.object(engine, "preview_link", preview_link), \
                mock.patch.object(engine, "YT_DLP_AVAILABLE", True):
            for url in ("https://e.com/old", "https://e.com/new"):
                self.app._replace_url_text(url)
                self.app._start_preview()
            deadline = time.time() + 5
            while self.app._active_workers and time.time() < deadline:
                self.app.update()
                time.sleep(0.01)
        self.assertTrue(events["https://e.com/old"].is_set())
        self.assertFalse(events["https://e.com/new"].is_set())
        self.assertEqual(self.app.preview_text.get(), "new")
        self.assertEqual(self.app._active_workers, 0)
        self.assertEqual(self.violations, [])

    def test_closing_cancels_the_lookup(self):
        cancel = self.app._preview_cancel
        self.app._on_close()
        self.assertTrue(cancel.is_set())

    def test_answering_no_to_close_keeps_the_lookup(self):
        started, release = threading.Event(), threading.Event()

        def preview_link(url, whole_playlist, cancel_event, js_runtime=None, thumb_size=None):
            started.set()
            release.wait(5)
            return None if cancel_event.is_set() else ("T", None, True)

        with mock.patch.object(engine, "preview_link", preview_link), \
                mock.patch.object(engine, "YT_DLP_AVAILABLE", True):
            self.app._replace_url_text("https://e.com/v")
            self.app._start_preview()
            self.assertTrue(started.wait(5))
            cancel = self.app._preview_cancel
            self.app.is_running = True   # a download is running
            try:
                with mock.patch.object(ytd.messagebox, "askyesno",
                                       return_value=False) as ask:
                    self.app._on_close()
            finally:
                self.app.is_running = False
            ask.assert_called_once()
            self.assertFalse(cancel.is_set())
            release.set()
            deadline = time.time() + 5
            while self.app._active_workers and time.time() < deadline:
                self.app.update()
                time.sleep(0.01)
        self.assertEqual(self.app.preview_text.get(), "T")
        self.assertEqual(self.app._active_workers, 0)
        self.assertEqual(self.violations, [])

    @unittest.skipIf(engine.Image is None, "Pillow not installed")
    def test_preview_shows_the_thumbnail(self):
        image = engine.Image.new("RGB", engine.THUMB_SIZE, "red")
        self.look_up("https://www.youtube.com/watch?v=x", thumbnail=image,
                     extract=lambda url: {"title": "T", "duration": 5})
        self.assertIsNotNone(self.app._preview_image)
        self.assertTrue(str(self.app.preview_img.cget("image")))
        self.assertEqual(self.violations, [])

    def test_header_shows_the_yt_dlp_version(self):
        with mock.patch.object(engine, "yt_dlp_version", return_value="2026.08.19"):
            self.assertEqual(self.app._powered_by_text(),
                             "Powered by yt-dlp 2026.08.19")
        with mock.patch.object(engine, "yt_dlp_version", return_value=None):
            self.assertEqual(self.app._powered_by_text(),
                             "Powered by yt-dlp (not installed)")

    def test_note_when_another_yt_dlp_is_installed(self):
        with mock.patch.object(engine, "yt_dlp_version", return_value="2026.02.21"), \
                mock.patch.object(engine, "pinned_yt_dlp_version", return_value="2026.8.19"):
            self.app._note_yt_dlp_version()
        self.assertIn("yt-dlp 2026.02.21 is installed; this app is tested with "
                      "2026.8.19", self.log())
        before = self.log()
        with mock.patch.object(engine, "yt_dlp_version", return_value="2026.08.19"), \
                mock.patch.object(engine, "pinned_yt_dlp_version", return_value="2026.8.19"):
            self.app._note_yt_dlp_version()
        self.assertEqual(self.log(), before)

    def test_install_without_restart(self):
        self.app._installing = True
        self.app._preview_key = (("https://x",), False)
        with mock.patch.object(engine, "yt_dlp_version", return_value="2026.08.19"), \
                mock.patch.object(self.app, "_start_worker") as start:
            self.app._install_done(True, None, loaded=True)
        self.assertEqual(self.app.powered_lbl.cget("text"),
                         "Powered by yt-dlp 2026.08.19")
        self.assertEqual(self.statuses[-1], "✔ yt-dlp installed. You can download now.")
        self.assertIn("yt-dlp 2026.08.19 installed and ready", self.log())
        self.assertEqual(self.dialogs, [])   # no "please restart"
        self.assertFalse(self.app._installing)
        # What startup skipped without yt-dlp happens now.
        start.assert_called_once_with(self.app._find_js_runtime_thread)
        self.assertIsNone(self.app._preview_key, "the preview looks the link up again")

    def test_install_that_still_needs_a_restart(self):
        self.app._install_done(True, None, loaded=False)
        self.assertEqual(self.dialogs, ["showinfo"])
        self.assertIn("Please restart", self.log())

    def test_download_while_installing_doesnt_install_again(self):
        self.app._installing = True
        with mock.patch.object(engine, "YT_DLP_AVAILABLE", False), \
                mock.patch.object(self.app, "_prompt_install") as prompt:
            self.app._start_download()
        prompt.assert_not_called()
        self.assertEqual(self.statuses[-1], "Installing yt-dlp, please wait…")

    def test_close_after_the_download_finished_during_the_question(self):
        # Tk runs timers while the question is open, so the download can end
        # meanwhile; Yes must then close, not wait for a stop that happened.
        self.app.is_running = True
        self.app._queue = mock.Mock(canceled=False)

        def finished_meanwhile(*a, **k):
            self.app._end_download()
            return True

        with mock.patch.object(ytd.messagebox, "askyesno", side_effect=finished_meanwhile):
            self.app._on_close()
        self.assertTrue(self.app._closing)
        self.app._queue.cancel.assert_not_called()

    def test_crashed_download_ends_the_download(self):
        self.expect_crash = True
        def crash(queue):
            raise RuntimeError("boom")

        with mock.patch.object(engine.DownloadQueue, "run", crash), \
                mock.patch.object(sys, "stderr", None):   # no traceback in the test output
            self.run_download(make_fake_ydl(streams=1))
        self.assertFalse(self.app.is_running)
        self.assertEqual(self.app.dl_btn.cget("text"), "⬇  Download")
        self.assertIn("✘ Unexpected error: RuntimeError: boom", self.log())
        self.assertIn("unexpected error", self.statuses[-1])
        self.assertEqual(self.app._active_workers, 0)
        self.assertEqual(self.violations, [])

    def test_worker_that_crashes_still_finishes(self):
        self.expect_crash = True
        with mock.patch.object(sys, "stderr", None):
            self.app._start_worker(lambda: 1 / 0)
            deadline = time.monotonic() + 5
            while self.app._active_workers and time.monotonic() < deadline:
                self.app.update()
                time.sleep(0.01)
        self.assertEqual(self.app._active_workers, 0)
        self.assertIn("✘ Unexpected error: ZeroDivisionError", self.log())

    def test_callback_errors_reach_the_log(self):
        # The windowed .exe has no stderr for Tk's own report.
        try:
            raise ValueError("bad value")
        except ValueError:
            exc = sys.exc_info()
        with mock.patch.object(sys, "stderr", None):
            ytd.YouTubeDownloader.report_callback_exception(self.app, *exc)
        self.assertIn("✘ Unexpected error: ValueError: bad value", self.log())

    def test_shift_tab_moves_focus_back(self):
        self.app.url_entry.focus_force()
        self.app.update()
        self.app.url_entry.event_generate("<Shift-Tab>")
        self.app.update()
        self.assertEqual(self.app.focus_get(), self.app.url_entry.tk_focusPrev())

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
                             os.path.normpath(os.path.expanduser("~/Downloads")))
            self.assertEqual(again.format_var.get(), "mp4_best")
        finally:
            again.destroy()

    def test_an_unavailable_saved_folder_is_kept(self):
        # e.g. on a USB drive that isn't plugged in this time.
        gone = os.path.join(os.path.dirname(self.settings_file), "unplugged", "Videos")
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump({"folder": gone}, f)
        again = ytd.YouTubeDownloader()
        try:
            log = again.log_box.get("1.0", "end")
            self.assertIn(f"The saved folder {gone} isn't available", log)
            self.assertEqual(again.download_path.get(), again._default_folder)
            again._save_settings()
            self.assertEqual(ytd.load_settings()["folder"], gone, "forgotten")
            with tempfile.TemporaryDirectory() as chosen, \
                    mock.patch.object(ytd.filedialog, "askdirectory", return_value=chosen):
                again._browse_folder()
                again._save_settings()
                self.assertEqual(ytd.load_settings()["folder"], os.path.normpath(chosen))
        finally:
            again.destroy()

    def test_a_relative_folder_is_refused(self):
        for folder in ("", "   ", "videos"):
            with self.subTest(folder=folder):
                self.dialogs.clear()
                self.app.download_path.set(folder)
                self.app._start_download()
                self.assertEqual(self.dialogs, ["showwarning"])
                self.assertFalse(self.app.is_running)

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
        self.app._replace_url_text("https://www.youtube.com/watch?v=t&list=RDx")
        fake = make_fake_ydl(streams=1)
        self.run_download(fake)
        self.assertEqual(fake.calls, ["probe", "download"])
        self.assertIs(fake.last_opts["noplaylist"], True)
        self.assertEqual(self.violations, [])
        self.assertEqual(self.callback_errors, [])

    def test_failed_playlist_dialog_does_not_hang(self):
        fake = make_fake_ydl(streams=1, videos=2, probe=("playlist",))
        with mock.patch.object(ytd.messagebox, "askyesno",
                               side_effect=tkinter.TclError("dialog failed")):
            self.run_download(fake)
        self.assertEqual(fake.calls, ["probe"])
        self.assertFalse(self.app.is_running)
        self.assertEqual(self.statuses[-1], "Playlist not downloaded.")
        self.assertIn("✘ Couldn't ask about the playlist: dialog failed", self.log())
        self.assertEqual([exc[0] for exc in self.callback_errors], [tkinter.TclError])
        self.assertEqual(self.violations, [])

    def test_follows_url_results_before_asking(self):
        # e.g. a channel's home page redirecting to its Videos tab.
        fake = make_fake_ydl(streams=1, videos=2, probe=("url", "playlist"))
        with mock.patch.object(ytd.messagebox, "askyesno", return_value=True) as ask:
            self.run_download(fake)
        ask.assert_called_once()
        self.assertEqual(fake.calls, ["probe", "probe", "download"])


if __name__ == "__main__":
    unittest.main()
