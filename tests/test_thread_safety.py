"""Checks that worker threads never touch Tk directly.

Run: python -m unittest discover -s tests -v
"""
import gc
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


def make_fake_ydl(streams=2, videos=1, overshoot=0, error=None, fail=(),
                  already=()):
    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
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

        def download(self, urls):
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
                self.run_pps("before_dl", info)
                for stream in range(streams):
                    # total_bytes_estimate can be exceeded (overshoot).
                    for done in range(0, 101 + overshoot, 5):
                        for hook in hooks:
                            hook({"status": "downloading", "video": video,
                                  "total_bytes_estimate": 100,
                                  "downloaded_bytes": done, "speed": 1e6,
                                  "eta": 1, "info_dict": info})
                        time.sleep(0.002)
                    for hook in hooks:
                        hook({"status": "finished", "video": video,
                              "filename": f"v{video}.f{stream}.webm"})
                self.run_pps("after_move", {
                    **info, "filepath": f"v{video}.mp4",
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
        self.statuses = []
        orig_set_status = self.app._set_status

        def record_status(msg, color=ytd.TEXT_DIM):
            self.statuses.append(msg)
            orig_set_status(msg, color)

        self.app._set_status = record_status
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
        log = self.app.log_box.get("1.0", "end")
        self.assertIn("Saved: v0.mp4", log)
        self.assertNotIn(".f0.webm", log, "logged a temporary stream file")

    def test_playlist_checkbox(self):
        url = "https://www.youtube.com/watch?v=test&list=x"
        self.assertIs(self.app._get_ydl_opts(url, "ffmpeg")["noplaylist"], True)
        self.app.playlist_var.set(True)
        self.assertIs(self.app._get_ydl_opts(url, "ffmpeg")["noplaylist"], False)

    def test_progress_restarts_per_playlist_video(self):
        seen = {}
        url = "https://www.youtube.com/playlist?list=x"
        opts = self.app._get_ydl_opts(url, "ffmpeg")
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

    def start_playlist(self, url="https://www.youtube.com/watch?v=test&list=x"):
        self.app.playlist_var.set(True)
        self.app.url_entry.delete(0, "end")
        self.app.url_entry.insert(0, url)

    def log(self):
        return self.app.log_box.get("1.0", "end")

    def test_whole_playlist(self):
        self.start_playlist()
        self.run_download(make_fake_ydl(streams=2, videos=3))
        self.assertEqual(self.dialogs, ["showinfo"])
        self.assertEqual(self.violations, [])
        for video in range(3):
            self.assertEqual(self.log().count(f"Saved: v{video}.mp4"), 1)
        self.assertEqual(self.statuses[-1], "✔ Done: 3 saved")
        for n in (1, 2, 3):
            self.assertTrue(any(m.startswith(f"Video {n}/3 · ") for m in self.statuses),
                            f"no status for video {n}")
        self.assertEqual(sum(1 for a, b in zip(self.progress, self.progress[1:])
                             if b < a), 2, "progress should restart per video")

    def test_playlist_skips_unavailable_entry(self):
        self.start_playlist()
        self.run_download(make_fake_ydl(streams=1, videos=3, fail={1}))
        self.assertEqual(self.dialogs, ["showinfo"])
        self.assertIn("Saved: v2.mp4", self.log())
        self.assertIn("Skipped: [youtube] v1: Private video", self.log())
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

    def test_playlist_link_asks_first(self):
        url = "https://www.youtube.com/playlist?list=x"
        self.app.url_entry.delete(0, "end")
        self.app.url_entry.insert(0, url)
        self.app._start_worker = mock.Mock()
        with mock.patch.object(ytd.messagebox, "askyesno", return_value=False) as ask:
            self.app._start_download()
        ask.assert_called_once()
        self.app._start_worker.assert_not_called()
        self.assertFalse(self.app.playlist_var.get())
        with mock.patch.object(ytd.messagebox, "askyesno", return_value=True):
            self.app._start_download()
        self.assertTrue(self.app.playlist_var.get())
        opts = self.app._start_worker.call_args.args[2]
        self.assertIs(opts["noplaylist"], False)
        self.assertEqual(opts["ignoreerrors"], "only_download")
        self.assertIn("%(playlist_index)03d - ", opts["outtmpl"])
        # A video in a playlist doesn't ask.
        self.app.playlist_var.set(False)
        self.app.is_running = False
        self.app.url_entry.delete(0, "end")
        self.app.url_entry.insert(0, "https://www.youtube.com/watch?v=t&list=x")
        with mock.patch.object(ytd.messagebox, "askyesno") as ask:
            self.app._start_download()
        ask.assert_not_called()


if __name__ == "__main__":
    unittest.main()
