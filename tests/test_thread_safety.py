"""Checks that worker threads never touch Tk directly.

Run: python -m unittest discover -s tests -v
"""
import os
import sys
import threading
import time
import tkinter
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import youtube_downloader as ytd  # noqa: E402

GUARDED = [
    (tkinter.Variable, "get"), (tkinter.Variable, "set"),
    (tkinter.Misc, "configure"), (tkinter.Misc, "config"),
    (tkinter.Misc, "after"), (tkinter.Misc, "after_cancel"),
    (tkinter.Text, "insert"), (tkinter.Text, "see"),
]


def make_fake_ydl(streams=2, videos=1, overshoot=0, error=None):
    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
            self.pps = []

        def add_post_processor(self, pp, when="post_process"):
            self.pps.append(pp)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            hooks = self.opts["progress_hooks"]
            info = {"requested_formats": [{}] * streams} if streams > 1 else {}
            for video in range(videos):
                for pp in self.pps:
                    pp.run(info)
                for stream in range(streams):
                    # total_bytes_estimate can be exceeded (overshoot).
                    for done in range(0, 101 + overshoot, 5):
                        for hook in hooks:
                            hook({"status": "downloading", "video": video,
                                  "total_bytes_estimate": 100,
                                  "downloaded_bytes": done, "speed": 1e6,
                                  "eta": 1})
                        time.sleep(0.002)
                    for hook in hooks:
                        hook({"status": "finished", "video": video,
                              "filename": f"v{video}.part{stream}"})
            if error:
                raise error

    return FakeYDL


class ThreadSafetyTest(unittest.TestCase):
    def setUp(self):
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
        for patcher in (
                mock.patch.object(ytd, "YT_DLP_AVAILABLE", True),
                mock.patch.object(ytd, "find_ffmpeg", return_value="ffmpeg"),
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
            postprocessor=types.SimpleNamespace(PostProcessor=object))
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
        self.assertEqual(self.dialogs, ["showinfo"])
        self.assertEqual(self.violations, [])
        self.assertEqual(self.callback_errors, [])
        self.assertEqual(self.app.progress_var.get(), 100)
        self.assertEqual(self.progress, sorted(self.progress),
                         "progress went backwards between streams")
        self.assertLessEqual(max(self.progress), 99,
                             "bar reached 100 before the done callback")
        self.assertEqual(self.progress[-1], 99)
        self.assertIsNone(self.app._after_id, "poll should stop when idle")

    def test_progress_restarts_per_playlist_video(self):
        seen = {}
        url = "https://www.youtube.com/playlist?list=x"
        opts = self.app._get_ydl_opts(url)
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

    def test_close_while_done_dialog_open(self):
        self.on_dialog = lambda name: self.app._on_close()
        self.run_download(make_fake_ydl(streams=1))
        self.assertEqual(self.dialogs, ["showinfo"])
        self.assertEqual(self.violations, [])
        self.assertEqual(self.callback_errors, [])


if __name__ == "__main__":
    unittest.main()
