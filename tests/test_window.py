"""The window, driven with fake yt-dlp downloads: the Downloads list, the
queue, cancel, closing, playlists, lookups, settings, themes, and that
worker threads never touch Qt.

Qt runs on its "offscreen" platform, so no display is needed and no window
shows up.

Run: python -m unittest discover -s tests -v
"""
import gc
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(TESTS), TESTS]   # the app, and tests/fakes.py
from fakes import fake_yt_dlp, make_fake_ydl  # noqa: E402
from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtGui import QFocusEvent, QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import download_engine as engine  # noqa: E402
import youtube_downloader as ytd  # noqa: E402

MAIN = threading.main_thread()
REAL_PREVIEW = engine.preview_link   # setUp replaces it with fake_preview
VIDEO = "https://www.youtube.com/watch?v=test"

# The window's methods that only the main thread may call: each touches
# widgets or the list.
GUARDED = [
    (ytd.YouTubeDownloader, "_log"), (ytd.YouTubeDownloader, "_set_status"),
    (ytd.YouTubeDownloader, "_looked_up"), (ytd.YouTubeDownloader, "_download_finished"),
    (ytd.YouTubeDownloader, "_show_progress"), (ytd.YouTubeDownloader, "_run_next"),
    (ytd.DownloadsModel, "add"), (ytd.DownloadsModel, "changed"),
    (ytd.DownloadsModel, "remove"),
]
DIALOGS = ("question", "critical", "warning", "information")


def fake_preview(url, whole_playlist, cancel_event=None, js_runtime=None, thumb_size=None):
    """engine.preview_link without yt-dlp: a title made from the link."""
    return f"Title of {url.rsplit('/', 1)[-1]}\n0:42 · Channel", None, True


class WindowTest(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.violations = []
        self.statuses = []
        for cls, name in GUARDED:
            self._guard(cls, name)

        # Dialogs are answered at once: questions with self.answer.
        self.dialogs = []
        self.answer = QMessageBox.No
        for kind in DIALOGS:
            patcher = mock.patch.object(ytd.QMessageBox, kind,
                                        side_effect=self._dialog(kind))
            patcher.start()
            self.addCleanup(patcher.stop)

        # An exception on any thread fails the test, even one the window
        # handled (it logs worker crashes; see _run_worker).
        self.thread_errors = []
        hook = threading.excepthook
        threading.excepthook = lambda args: self.thread_errors.append(args.exc_value)
        self.addCleanup(setattr, threading, "excepthook", hook)
        self.addCleanup(self._check_no_errors)

        # Remembered settings go to a temporary file, never the real one.
        settings_dir = tempfile.TemporaryDirectory()
        self.addCleanup(settings_dir.cleanup)
        self.settings_file = os.path.join(settings_dir.name, "settings.json")
        for patcher in (
                mock.patch.object(engine, "YT_DLP_AVAILABLE", True),
                mock.patch.object(engine, "find_ffmpeg", return_value="ffmpeg"),
                mock.patch.object(engine, "find_js_runtime", return_value=("deno", "deno")),
                mock.patch.object(engine, "check_for_update", return_value=None),
                mock.patch.object(engine, "preview_link", side_effect=fake_preview),
                mock.patch.object(ytd, "settings_path", return_value=self.settings_file)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app = self.new_window()

    def new_window(self):
        window = ytd.YouTubeDownloader(update_check=False)
        self.addCleanup(self._close, window)
        return window

    def _guard(self, cls, name):
        original = getattr(cls, name)
        violations, statuses = self.violations, self.statuses

        def guarded(*args, **kwargs):
            if threading.current_thread() is not MAIN:
                violations.append(name)
            if name == "_set_status":
                statuses.append(args[1])
            return original(*args, **kwargs)

        patcher = mock.patch.object(cls, name, guarded)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _dialog(self, kind):
        def show(parent, title, text, *args, **kwargs):
            if threading.current_thread() is not MAIN:
                self.violations.append(f"QMessageBox.{kind}")
            self.dialogs.append(kind)
            self.titles.append(title)
            self.texts.append(text)
            return self.answer if kind == "question" else QMessageBox.Ok
        self.titles, self.texts = [], []
        return show

    def _close(self, window):
        for row in window.model.rows:
            row.cancel_event.set()
        deadline = time.monotonic() + 5
        while window._active_workers and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.005)
        window._closing = True
        window.hide()
        window.deleteLater()
        QApplication.processEvents()

    def _check_no_errors(self):
        self.assertEqual(self.violations, [], "Qt used from a worker thread")
        self.assertEqual(self.thread_errors, [], "an exception on a worker thread")

    # ── Helpers ──

    def wait(self, condition, timeout=10, what="the window to get there"):
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                self.fail(f"timed out waiting for {what}")
            QApplication.processEvents()
            time.sleep(0.005)
        QApplication.processEvents()

    def idle(self, window=None):
        window = window or self.app
        return (window._running is None and window._active_workers == 0
                and not [r for r in window.model.rows if r.state not in ytd.FINISHED])

    def run_download(self, fake, text=VIDEO):
        with mock.patch.object(engine, "yt_dlp", fake_yt_dlp(fake)):
            self.app.url.setText(text)
            self.app._start_download()
            self.wait(self.idle, what="the downloads to finish")

    def rows(self):
        return self.app.model.rows

    def states(self):
        return [r.state for r in self.rows()]

    def log(self):
        return self.app.log_box.toPlainText()

    def files(self, folder):
        return sorted(os.listdir(folder))

    def cancel_at(self, point, row_number=0):
        """An on_progress for the fake that clicks a row's Cancel when the
        download reaches point, and waits (on the download thread) until the
        main thread has."""
        def on_progress(*where):
            if where == point:
                row = self.app.model.rows[row_number]
                self.app._post(self.app._row_action, row, "cancel")
                row.cancel_event.wait(5)
        return on_progress

    # ── Downloading ──

    def test_successful_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.app.path.setText(tmp)
            seen = []
            changed = ytd.DownloadsModel.changed

            def record(model, row):
                if row.state in ytd.ACTIVE:
                    seen.append(row.pct)
                changed(model, row)

            with mock.patch.object(ytd.DownloadsModel, "changed", record):
                self.run_download(make_fake_ydl(streams=2, files_dir=tmp, delay=0.002))
            self.assertEqual(self.files(tmp), ["v0.mp4"])
        row, = self.rows()
        self.assertEqual(row.state, "done")
        self.assertEqual(row.file, os.path.join(tmp, "v0.mp4"))
        self.assertEqual((row.title, row.details), ("Title of watch?v=test", "0:42 · Channel"))
        self.assertEqual(seen, sorted(seen), "progress went backwards between streams")
        self.assertLessEqual(max(seen), 99, "the bar reached 100 before the end")
        self.assertIn("Saved: ", self.log())
        self.assertIn("✔ Done!", self.log())
        self.assertNotIn(".f0.webm", self.log(), "logged a temporary stream file")
        self.assertEqual(self.statuses[-1], "✔ All downloads complete.")
        self.assertIn("Downloading…", self.statuses, "one link has no \"1 of 1\"")
        self.assertEqual(self.app._status_role, ytd.SUCCESS)
        self.assertEqual(self.app.count_lbl.text(), "1 · 1 completed")
        self.assertFalse(self.app._progress_timer.isActive(), "the timer keeps running")
        self.assertEqual(self.dialogs, [])

    def test_failed_download(self):
        self.run_download(make_fake_ydl(streams=1, error=RuntimeError("boom")))
        row, = self.rows()
        self.assertEqual(row.state, "failed")
        self.assertEqual(row.message, "boom")
        self.assertEqual(self.dialogs, [], "a failure needs no pop-up: the row says why")
        self.assertEqual(self.statuses[-1], "✘ Nothing downloaded: 1 failed.")
        self.assertIn("✘ Error: boom", self.log())

    def test_single_video_error_is_reported_once(self):
        self.run_download(make_fake_ydl(streams=1, fail={0}))
        row, = self.rows()
        self.assertEqual(row.state, "failed")
        self.assertIn("Private video", row.message)
        self.assertEqual(self.log().count("Private video"), 1)

    def test_whole_playlist(self):
        self.app.whole_playlist.setChecked(True)
        fake = make_fake_ydl(streams=2, videos=3)
        self.run_download(fake, "https://www.youtube.com/watch?v=test&list=x")
        self.assertEqual(fake.calls, ["download"], "Whole playlist shouldn't ask")
        self.assertEqual(self.dialogs, [])
        for video in range(3):
            self.assertEqual(self.log().count(f"Saved: v{video}.mp4"), 1)
        row, = self.rows()
        self.assertEqual((row.state, row.details), ("done", "Done: 3 saved"))

    def test_playlist_skips_unavailable_entry(self):
        self.app.whole_playlist.setChecked(True)
        self.run_download(make_fake_ydl(streams=1, videos=3, fail={1}))
        self.assertIn("Saved: v2.mp4", self.log())
        self.assertIn("✘ [youtube] v1: Private video", self.log())
        self.assertEqual(self.rows()[0].details, "Done: 2 saved, 1 skipped")
        self.assertIn("Private video", self.app.model.data(
            self.app.model.index(0, ytd.DownloadsModel.STATUS), ytd.Qt.ToolTipRole))

    def test_playlist_with_nothing_saved_fails(self):
        self.app.whole_playlist.setChecked(True)
        self.run_download(make_fake_ydl(streams=1, videos=2, fail={0, 1}))
        self.assertEqual(self.rows()[0].state, "failed")
        self.assertIn("Private video", self.rows()[0].message)

    def test_an_empty_playlist_finds_nothing(self):
        self.app.whole_playlist.setChecked(True)
        self.run_download(make_fake_ydl(streams=1, videos=0), "https://e.com/empty")
        row, = self.rows()
        self.assertEqual((row.state, row.message), ("empty", engine.NOTHING_TO_DOWNLOAD))
        self.assertEqual(ytd.STATES["empty"], ("Nothing found", "warning"))
        self.assertEqual(self.statuses[-1], "Nothing downloaded: 1 empty.")
        self.assertIn("remove", ytd.row_actions(row))

    def test_already_downloaded_is_not_saved(self):
        self.app.whole_playlist.setChecked(True)
        self.run_download(make_fake_ydl(streams=1, videos=2, already={0}))
        self.assertIn("Already downloaded: v0.mp4", self.log())
        self.assertNotIn("Saved: v0.mp4", self.log())
        self.assertEqual(self.rows()[0].details, "Done: 1 saved, 1 already downloaded")

    def test_queue_of_links(self):
        self.app.whole_playlist.setChecked(True)   # downloads without asking
        fake = make_fake_ydl(streams=1)
        self.run_download(fake, "https://e.com/a https://e.com/b\nhttps://e.com/a https://e.com/c")
        self.assertEqual([r.url for r in self.rows()],
                         ["https://e.com/a", "https://e.com/b", "https://e.com/c"])
        self.assertEqual(self.states(), ["done"] * 3)
        log = self.log()
        self.assertLess(log.index("▶ https://e.com/a"), log.index("▶ https://e.com/b"))
        self.assertLess(log.index("▶ https://e.com/b"), log.index("▶ https://e.com/c"))
        self.assertIn("Downloading 1 of 3…", self.statuses)
        self.assertEqual(self.app.url.text(), "", "the box isn't emptied")
        self.assertEqual(self.dialogs, [])

    def test_queue_goes_on_after_a_failed_link(self):
        fake = make_fake_ydl(streams=1)

        class FailsOnB(fake):
            def download(self, urls):
                if urls == ["https://e.com/b"]:
                    raise RuntimeError("no such video")
                super().download(urls)

        self.app.whole_playlist.setChecked(True)
        self.run_download(FailsOnB, "https://e.com/a https://e.com/b https://e.com/c")
        self.assertEqual(self.states(), ["done", "failed", "done"])
        self.assertEqual(self.rows()[1].message, "no such video")
        self.assertEqual(self.statuses[-1], "✔ Done: 2 completed, 1 failed.")
        self.assertEqual(self.app._status_role, ytd.WARNING)
        self.assertEqual(self.dialogs, [])

    def test_a_link_already_waiting_isnt_added_again(self):
        release = threading.Event()
        fake = make_fake_ydl(streams=1, on_progress=lambda *where: release.wait(5))
        with mock.patch.object(engine, "yt_dlp", fake_yt_dlp(fake)):
            self.app.url.setText("https://e.com/a")
            self.app._start_download()
            self.app.url.setText("https://e.com/a https://e.com/b")
            self.app._start_download()
            self.assertEqual([r.url for r in self.rows()], ["https://e.com/a", "https://e.com/b"])
            self.app.url.setText("https://e.com/a")
            self.app._start_download()
            self.assertEqual(self.statuses[-1], "Those links are already in the list.")
            release.set()
            self.wait(self.idle)
        # Once it's finished, the same link can be downloaded again.
        self.run_download(make_fake_ydl(streams=1), "https://e.com/a")
        self.assertEqual(len(self.rows()), 3)

    def test_audio_has_no_quality(self):
        self.app.format.setCurrentIndex(self.app.format.findData("mp3"))
        self.assertFalse(self.app.quality.isEnabled())
        self.assertEqual(self.app._chosen_format(), "mp3")
        self.app.format.setCurrentIndex(self.app.format.findData("video"))
        self.app.quality.setCurrentIndex(self.app.quality.findData("720p"))
        self.assertTrue(self.app.quality.isEnabled())
        self.assertEqual(self.app._chosen_format(), "720p")
        self.run_download(make_fake_ydl(streams=1))
        row = self.rows()[0]
        self.assertEqual((row.fmt, row.format_label, row.quality_label), ("720p", "MP4", "720p"))
        self.assertEqual(ytd.Row("u", "m4a", "f", False).quality_label, "—")

    def test_the_summary_counts_every_outcome(self):
        cases = [
            (["done", "done"], "✔ All downloads complete.", ytd.SUCCESS),
            (["done", "done", "declined", "canceled"],
             "✔ Done: 2 completed, 1 canceled, 1 not downloaded.", ytd.SUCCESS),
            (["done", "failed", "empty"], "✔ Done: 1 completed, 1 failed, 1 empty.",
             ytd.WARNING),
            (["failed", "canceled"], "✘ Nothing downloaded: 1 failed, 1 canceled.", ytd.ERROR),
            (["canceled", "canceled"], "Nothing downloaded: 2 canceled.", ytd.TEXT_DIM),
        ]
        for states, text, role in cases:
            with self.subTest(states=states):
                self.assertEqual(ytd.queue_summary(states), (text, role))

    def test_only_a_running_or_finished_download_fills_its_bar(self):
        for state, pct, expected in (("downloading", 40, 40), ("processing", 99, 99),
                                     ("done", 0, 100), ("canceled", 5, None),
                                     ("failed", 30, None), ("waiting", 0, None),
                                     ("declined", 0, None), ("empty", 0, None)):
            with self.subTest(state=state):
                row = ytd.Row(VIDEO, "mp4_best", "f", False, state=state, pct=pct)
                self.assertEqual(ytd.bar_percent(row), expected)

    def test_yt_dlp_warnings_reach_the_log(self):
        self.run_download(make_fake_ydl(streams=1, warn="[youtube] Some formats are missing"))
        self.assertIn("⚠ [youtube] Some formats are missing", self.log())

    # ── Cancel ──

    def test_cancel_removes_only_the_unfinished_video(self):
        self.app.whole_playlist.setChecked(True)
        with tempfile.TemporaryDirectory() as tmp:
            self.app.path.setText(tmp)
            open(os.path.join(tmp, "unrelated.mp4"), "wb").close()
            # Video 1's first stream has finished, its second is half done.
            self.run_download(make_fake_ydl(streams=2, videos=2, files_dir=tmp,
                                            on_progress=self.cancel_at((1, 1, 50))))
            self.assertEqual(self.files(tmp), ["unrelated.mp4", "v0.mp4"])
        row, = self.rows()
        self.assertEqual(row.state, "canceled")
        self.assertEqual(row.file, os.path.join(tmp, "v0.mp4"), "the saved video")
        self.assertIn("Download canceled (1 saved, 2 partial files removed).", self.log())
        self.assertEqual(self.dialogs, [])

    def test_cancel_between_playlist_videos(self):
        self.app.whole_playlist.setChecked(True)
        with tempfile.TemporaryDirectory() as tmp:
            self.app.path.setText(tmp)
            self.run_download(make_fake_ydl(streams=2, videos=3, files_dir=tmp,
                                            on_progress=self.cancel_at((1, -1, 0))))
            self.assertEqual(self.files(tmp), ["v0.mp4"])
        self.assertIn("Download canceled (1 saved).", self.log())

    def test_cancel_a_waiting_link(self):
        self.app.whole_playlist.setChecked(True)
        fake = make_fake_ydl(streams=1, on_progress=self.cancel_at((0, 0, 30), row_number=1))
        self.run_download(fake, "https://e.com/a https://e.com/b https://e.com/c")
        # b was canceled while a was downloading; c still downloaded.
        self.assertEqual(self.states(), ["done", "canceled", "done"])
        self.assertNotIn("▶ https://e.com/b", self.log())

    def test_cancel_then_download_again(self):
        self.run_download(make_fake_ydl(streams=1, on_progress=self.cancel_at((0, 0, 30))))
        self.assertEqual(self.states(), ["canceled"])
        self.assertEqual(self.statuses[-1], "Nothing downloaded: 1 canceled.")
        self.run_download(make_fake_ydl(streams=1))
        self.assertEqual(self.states(), ["canceled", "done"])

    def test_progress_after_cancel_keeps_canceling(self):
        row = ytd.Row(VIDEO, "mp4_best", "f", False, state="downloading")
        self.app.model.add(row)
        self.app._running = row
        try:
            self.app._cancel_row(row)
            self.assertEqual(row.message, "Canceling…")
            row.latest = (50, "Downloading… 50.0%  1.0 MB/s")
            self.app._show_progress()
            self.assertEqual(row.message, "Canceling…")
        finally:
            self.app._running = None

    def test_no_playlist_question_after_cancel(self):
        row = ytd.Row(VIDEO, "mp4_best", "f", False)
        row.cancel_event.set()
        answers = []
        worker = threading.Thread(target=lambda: answers.append(
            self.app._ask_on_main(row, "Playlist", "All of it?")))
        worker.start()
        self.wait(lambda: not worker.is_alive())
        self.assertEqual(answers, [False])
        self.assertEqual(self.dialogs, [], "the question still showed")

    # ── Closing ──

    def test_close_during_download_asks_then_cancels_and_closes(self):
        self.answer = QMessageBox.Yes
        with tempfile.TemporaryDirectory() as tmp:
            self.app.path.setText(tmp)

            def on_progress(*where):
                if where == (0, 1, 50):
                    self.app._post(self.app.close)
                    self.app.model.rows[0].cancel_event.wait(5)   # until Yes was answered
            self.run_download(make_fake_ydl(streams=2, files_dir=tmp, on_progress=on_progress))
            self.assertEqual(self.files(tmp), [], "partial files left behind")
        self.assertEqual(self.titles, ["Download in progress"])
        self.assertTrue(self.app._closing, "the app should have closed")
        self.assertEqual(self.states(), ["canceled"])

    def test_close_during_download_no_keeps_downloading(self):
        def on_progress(*where):
            if where == (0, 0, 50):
                self.app._post(self.app.close)
                time.sleep(0.2)
        self.run_download(make_fake_ydl(streams=1, on_progress=on_progress))
        self.assertEqual(self.titles, ["Download in progress"])
        self.assertFalse(self.app._closing)
        self.assertEqual(self.states(), ["done"])

    def test_close_after_the_download_finished_during_the_question(self):
        # The download can end while the question is open; Yes must then
        # close, not wait for a stop that already happened.
        row = ytd.Row(VIDEO, "mp4_best", "f", False, state="downloading")
        self.app._running = row

        def finished_meanwhile(*args, **kwargs):
            self.app._running = None
            return QMessageBox.Yes

        with mock.patch.object(ytd.QMessageBox, "question", side_effect=finished_meanwhile):
            self.app.close()
        self.assertTrue(self.app._closing)
        self.assertFalse(row.cancel_event.is_set())

    def test_settings_are_saved_on_close(self):
        self.app.format.setCurrentIndex(self.app.format.findData("m4a"))
        self.app.close()
        with open(self.settings_file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["format"], "m4a")

    # ── Links and lookups ──

    def test_paste_adds_links_and_leaves_out_other_words(self):
        clipboard = QApplication.clipboard()
        self.app.url.setText("https://e.com/a")
        clipboard.setText('Watch this: "youtu.be/b", and https://e.com/a\nhttps://e.com/c')
        self.app._paste_url()
        self.assertEqual(self.app.url.text(),
                         "https://e.com/a https://youtu.be/b https://e.com/c")
        clipboard.setText("no links here")
        self.app._paste_url()
        self.assertEqual(self.statuses[-1], "The clipboard has no link to paste.")

    def test_pasted_lines_go_in_one_line(self):
        self.app.url.insert("https://e.com/a\nhttps://e.com/b\r\n")
        self.assertEqual(self.app.url.text(), "https://e.com/a https://e.com/b")

    def test_words_that_arent_links_are_left_out(self):
        self.run_download(make_fake_ydl(streams=1), f"look {VIDEO} here")
        self.assertIn("• Not links, so left out: look here", self.log())
        self.assertEqual([r.url for r in self.rows()], [VIDEO])

    def test_no_link_and_relative_folder_are_refused(self):
        self.app.url.setText("not a link")
        self.app._start_download()
        self.assertEqual(self.titles, ["No URL"])
        for folder in ("", "   ", "videos"):
            with self.subTest(folder=folder):
                self.titles.clear()
                self.app.url.setText(VIDEO)
                self.app.path.setText(folder)
                self.app._start_download()
                self.assertEqual(self.titles, ["Choose a folder"])
        self.assertEqual(self.rows(), [])

    def test_enter_downloads(self):
        with mock.patch.object(ytd.YouTubeDownloader, "_start_download") as start:
            window = self.new_window()
            window.url.returnPressed.emit()
        start.assert_called_once_with()

    def look_up(self, url, extract, thumbnail=None, playlist=False):
        """Adds url to the list with downloads held back, with a fake
        YoutubeDL whose extract_info(url) returns extract(url); waits for
        the lookup and returns the row."""
        class FakeYDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=True, process=True, ie_key=None):
                return extract(url)

        fake = types.SimpleNamespace(YoutubeDL=FakeYDL)
        with mock.patch.object(engine, "preview_link", REAL_PREVIEW), \
                mock.patch.object(engine, "yt_dlp", fake), \
                mock.patch.object(engine, "fetch_thumbnail", return_value=thumbnail), \
                mock.patch.object(self.app, "_run_next"):
            self.app.whole_playlist.setChecked(playlist)
            self.app.url.setText(url)
            self.app._start_download()
            self.wait(lambda: self.app._active_workers == 0)
        return self.rows()[-1]

    def test_lookup_of_a_video(self):
        row = self.look_up(VIDEO, lambda url: {
            "title": "Me at the zoo", "duration": 19, "channel": "jawed"})
        self.assertEqual((row.title, row.details, row.state),
                         ("Me at the zoo", "0:19 · jawed", "waiting"))

    def test_lookup_follows_url_results_to_a_playlist(self):
        def extract(url):
            if url == "https://www.youtube.com/watch?v=x&list=y":
                return {"_type": "url", "url": "https://list", "ie_key": "Tab"}
            return {"_type": "playlist", "title": "List", "playlist_count": 12,
                    "uploader": "me"}
        row = self.look_up("https://www.youtube.com/watch?v=x&list=y", extract, playlist=True)
        self.assertEqual((row.title, row.details), ("List", "Playlist · 12 videos · me"))

    def test_lookup_of_a_bad_link(self):
        def extract(url):
            raise RuntimeError("Unsupported URL")
        row = self.look_up("https://example.com/nothing", extract)
        self.assertEqual((row.title, row.details),
                         ("", "Couldn't load a preview for this link."))

    @unittest.skipIf(engine.Image is None, "Pillow not installed")
    def test_lookup_shows_the_thumbnail(self):
        image = engine.Image.new("RGB", engine.THUMB_SIZE, "red")
        row = self.look_up(VIDEO, lambda url: {"title": "T", "duration": 5}, thumbnail=image)
        self.assertEqual((row.thumb.width(), row.thumb.height()), engine.THUMB_SIZE)

    def test_lookups_ask_for_a_sharp_thumbnail(self):
        with mock.patch.object(self.app, "devicePixelRatioF", return_value=2.0):
            self.assertEqual(self.app._thumb_pixels(),
                             tuple(2 * n for n in engine.THUMB_SIZE))

    def test_closing_cancels_the_lookups(self):
        started, events = threading.Event(), []

        def slow_preview(url, whole_playlist, cancel_event, *args):
            events.append(cancel_event)
            started.set()
            cancel_event.wait(5)

        with mock.patch.object(engine, "preview_link", side_effect=slow_preview), \
                mock.patch.object(self.app, "_run_next"):
            self.app.url.setText(VIDEO)
            self.app._start_download()
            self.assertTrue(started.wait(5))
            self.app.close()
            self.wait(lambda: self.app._active_workers == 0)
        self.assertTrue(events[0].is_set())

    # ── Playlists ──

    def test_single_video_does_not_ask(self):
        fake = make_fake_ydl(streams=1)
        self.run_download(fake)
        self.assertEqual(fake.calls, ["probe", "download"])
        self.assertEqual(self.dialogs, [])

    def test_asks_before_downloading_a_playlist(self):
        fake = make_fake_ydl(streams=1, videos=2, probe=("playlist",))
        self.run_download(fake, "https://e.com/list")
        self.assertEqual(self.titles, ["Playlist"])
        self.assertIn('"Fake list" is a playlist or channel', self.texts[0])
        self.assertEqual(fake.calls, ["probe"])
        self.assertEqual(self.states(), ["declined"])
        self.assertIn("Playlist not downloaded.", self.log())

        self.answer = QMessageBox.Yes
        fake = make_fake_ydl(streams=1, videos=2, probe=("playlist",))
        self.run_download(fake, "https://e.com/list")
        self.assertEqual(fake.calls, ["probe", "download"])
        self.assertEqual((self.rows()[-1].state, self.rows()[-1].details), ("done", "Done: 2 saved"))
        # Yes is for that link only: the next one is asked about again.
        self.assertFalse(self.app.whole_playlist.isChecked())
        fake = make_fake_ydl(streams=1)
        self.run_download(fake, "https://www.youtube.com/watch?v=t&list=RDx")
        self.assertIs(fake.last_opts["noplaylist"], True)

    def test_failed_playlist_dialog_does_not_hang(self):
        fake = make_fake_ydl(streams=1, videos=2, probe=("playlist",))
        with mock.patch.object(ytd.QMessageBox, "question",
                               side_effect=RuntimeError("dialog failed")), \
                mock.patch.object(sys, "stderr", None):
            self.run_download(fake, "https://e.com/list")
        self.assertEqual(fake.calls, ["probe"])
        self.assertEqual(self.states(), ["declined"])
        self.assertIn("✘ Couldn't ask about the playlist: dialog failed", self.log())

    def test_follows_url_results_before_asking(self):
        # e.g. a channel's home page redirecting to its Videos tab.
        self.answer = QMessageBox.Yes
        fake = make_fake_ydl(streams=1, videos=2, probe=("url", "playlist"))
        self.run_download(fake, "https://e.com/channel")
        self.assertEqual(self.titles, ["Playlist"])
        self.assertEqual(fake.calls, ["probe", "probe", "download"])

    # ── Row actions ──

    def test_open_folder_and_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.app.path.setText(tmp)
            self.run_download(make_fake_ydl(streams=2, files_dir=tmp))
            row, = self.rows()
            rect = ytd.QRectF(0, 0, 120, ytd.ROW_HEIGHT)
            self.assertEqual([name for name, _ in self.app.delegate.icons(rect, row)],
                             ["folder", "remove"])
            with mock.patch.object(ytd, "reveal_in_file_manager") as reveal:
                self.app._row_action(row, "folder")
            reveal.assert_called_once_with(os.path.join(tmp, "v0.mp4"))
            self.app._row_action(row, "remove")
            self.assertEqual(self.rows(), [])
            self.assertEqual(self.files(tmp), ["v0.mp4"], "removing a row deletes nothing")

    def test_a_missing_folder_is_reported(self):
        row = ytd.Row(VIDEO, "mp4_best", "f", False, state="done",
                      file=os.path.join(tempfile.gettempdir(), "no such folder", "v.mp4"))
        with mock.patch.object(ytd, "reveal_in_file_manager") as reveal:
            self.app._row_action(row, "folder")
        reveal.assert_not_called()
        self.assertEqual(self.titles, ["Folder not found"])

    def test_icons_follow_the_state(self):
        rect = ytd.QRectF(0, 0, 120, ytd.ROW_HEIGHT)
        for state, icons in (("waiting", ["cancel"]), ("downloading", ["cancel"]),
                             ("failed", ["remove"]), ("canceled", ["remove"])):
            row = ytd.Row(VIDEO, "mp4_best", "f", False, state=state)
            self.assertEqual([n for n, _ in self.app.delegate.icons(rect, row)], icons)

    # ── Keyboard, right-click menu, double-click ──

    def add_rows(self, *states):
        rows = [ytd.Row(f"https://e.com/{n}", "mp4_best", "f", False, state=state)
                for n, state in enumerate(states)]
        for row in rows:
            self.app.model.add(row)
        return rows

    def select(self, row):
        self.app.view.setCurrentIndex(self.app.model.index(self.rows().index(row), 0))

    def key(self, key, modifiers=ytd.Qt.NoModifier):
        self.app.view.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, modifiers))

    def test_keys_act_on_the_selected_row(self):
        waiting, done = self.add_rows("waiting", "done")
        done.file = os.path.join(TESTS, "v.mp4")
        self.select(waiting)
        self.key(ytd.Qt.Key_Delete)
        self.assertIn(waiting, self.rows(), "removed a row that's still to download")
        self.key(ytd.Qt.Key_Escape)
        self.assertEqual(waiting.state, "canceled")
        self.key(ytd.Qt.Key_Delete)
        self.assertNotIn(waiting, self.rows())
        self.select(done)
        with mock.patch.object(ytd, "reveal_in_file_manager") as reveal:
            self.key(ytd.Qt.Key_Return)
            self.key(ytd.Qt.Key_Enter, ytd.Qt.KeypadModifier)   # the keypad's Enter
        self.assertEqual(reveal.call_args_list, [mock.call(done.file)] * 2)
        self.key(ytd.Qt.Key_Delete, ytd.Qt.ControlModifier)   # not a shortcut
        self.assertIn(done, self.rows())
        self.key(ytd.Qt.Key_Backspace)   # the Mac's Delete key
        self.assertEqual(self.rows(), [])
        self.key(ytd.Qt.Key_Delete)   # no row selected: nothing happens

    def test_arrow_keys_and_tab_select_rows(self):
        first, second = self.add_rows("done", "failed")
        self.app.view.focusInEvent(QFocusEvent(QEvent.FocusIn))
        self.assertIs(self.app.view.current_row(), first, "Tab into the list selects a row")
        self.key(ytd.Qt.Key_Down)
        self.assertIs(self.app.view.current_row(), second)

    def test_right_click_menu(self):
        row, = self.add_rows("done")
        row.file = os.path.join(TESTS, "v.mp4")
        menu = self.app.view.row_menu(row)
        items = menu.actions()
        self.assertEqual([(item.text(), item.isEnabled()) for item in items],
                         [("Cancel\tEsc", False), ("Show in folder\tEnter", True),
                          ("Remove from the list (the file stays)\tDelete", True)])
        items[2].trigger()
        self.assertEqual(self.rows(), [])

    def test_double_click_shows_the_file(self):
        row, = self.add_rows("done")
        row.file = os.path.join(TESTS, "v.mp4")
        model = self.app.model
        with mock.patch.object(ytd, "reveal_in_file_manager") as reveal:
            self.app.view.doubleClicked.emit(model.index(0, ytd.DownloadsModel.ACTIONS))
            reveal.assert_not_called()   # a double-clicked icon is just two clicks
            self.app.view.doubleClicked.emit(model.index(0, ytd.DownloadsModel.VIDEO))
        reveal.assert_called_once_with(row.file)

    def test_tooltips_name_the_keys(self):
        self.assertEqual([key for _, key, _ in ytd.ROW_ACTIONS.values()],
                         ["Esc", "Enter", "Delete"])
        for name, (_, _, keys) in ytd.ROW_ACTIONS.items():
            for key in keys:
                self.assertEqual(ytd.ACTION_KEYS[key], name)

    def test_a_running_row_cant_be_removed_and_clear_keeps_it(self):
        rows = [ytd.Row(VIDEO, "mp4_best", "f", False, state=s)
                for s in ("downloading", "done", "failed", "waiting")]
        for row in rows:
            self.app.model.add(row)
        self.app._row_action(rows[0], "remove")
        self.assertIn(rows[0], self.rows())
        self.app._clear_finished()
        self.assertEqual(self.states(), ["downloading", "waiting"])

    def test_the_list_draws_every_state(self):
        # Paints the rows (offscreen), so the drawing code runs.
        for state in ytd.STATES:
            row = ytd.Row(VIDEO, "mp3", "f", False, state=state, pct=40,
                          title="A title", details="1:00 · Channel", message="1.0 MB/s",
                          file="x.mp4")
            self.app.model.add(row)
        self.app.show()
        self.app.view.viewport().grab()

    # ── Crashes ──

    def test_crashed_download_fails_the_row(self):
        def crash(job):
            raise RuntimeError("boom")

        with mock.patch.object(engine.DownloadJob, "run", crash), \
                mock.patch.object(sys, "stderr", None):
            self.run_download(make_fake_ydl(streams=1))
        row, = self.rows()
        self.assertEqual((row.state, row.message),
                         ("failed", "An unexpected error (see the log)"))
        self.assertIn("✘ Unexpected error: RuntimeError: boom", self.log())
        self.assertTrue(self.app.log_btn.isChecked(), "the log didn't open")
        self.assertIsNone(self.app._running)

    def test_worker_that_crashes_still_finishes(self):
        with mock.patch.object(sys, "stderr", None):
            self.app._start_worker(lambda: 1 / 0)
            self.wait(lambda: self.app._active_workers == 0)
        self.assertIn("✘ Unexpected error: ZeroDivisionError", self.log())

    def test_errors_in_calls_on_the_main_thread_reach_the_log(self):
        def broken():
            raise ValueError("bad value")
        with mock.patch.object(sys, "stderr", None):
            self.app._post(broken)
            self.wait(lambda: "bad value" in self.log())
        self.assertIn("✘ Unexpected error: ValueError: bad value", self.log())

    # ── yt-dlp, ffmpeg and the JavaScript runtime ──

    def test_js_runtime_is_looked_up_on_a_worker(self):
        threads = []

        def find():
            threads.append(threading.current_thread())
            return ("deno", "/bin/deno")

        with mock.patch.object(engine, "find_js_runtime", side_effect=find):
            window = self.new_window()
            self.wait(lambda: window._active_workers == 0)
        self.assertNotIn(MAIN, threads)
        self.assertEqual(window._js_runtime, ("deno", "/bin/deno"))

    def test_missing_js_runtime_is_explained(self):
        with mock.patch.object(engine, "find_js_runtime", return_value=None):
            window = self.new_window()
            self.wait(lambda: window._active_workers == 0)
        self.assertIn("No JavaScript runtime found", window.log_box.toPlainText())
        self.assertTrue(window.log_btn.isChecked())

    def test_missing_ffmpeg_warns_and_blocks_download(self):
        with mock.patch.object(engine, "find_ffmpeg", return_value=None):
            window = self.new_window()
            self.assertEqual(window.status_lbl.text(), ytd.FFMPEG_MISSING_STATUS)
            self.assertIn(ytd.ffmpeg_help(startup=True), window.log_box.toPlainText())
            self.assertTrue(window.log_btn.isChecked(), "the log should open")
            window.url.setText(VIDEO)
            window._start_download()
        self.assertEqual(self.titles, ["ffmpeg not found"])
        self.assertEqual(window.model.rows, [])

    def test_coming_back_to_the_window_finds_ffmpeg(self):
        with mock.patch.object(engine, "find_ffmpeg", return_value=None) as find:
            window = self.new_window()
            find.return_value = "/bin/ffmpeg"
            with mock.patch.object(window, "isActiveWindow", return_value=True):
                window.changeEvent(QEvent(QEvent.ActivationChange))
            self.assertEqual(window.status_lbl.text(), "Ready")
            self.assertIn("✔ ffmpeg found: /bin/ffmpeg", window.log_box.toPlainText())
            calls = find.call_count
            window._recheck_ffmpeg()
            self.assertEqual(find.call_count, calls, "rechecked after it was found")

    def test_header_shows_the_yt_dlp_version(self):
        with mock.patch.object(engine, "yt_dlp_version", return_value="2026.08.19"):
            self.assertEqual(self.app._powered_by_text(), "Powered by yt-dlp 2026.08.19")
            with mock.patch.object(engine, "app_version", return_value="1.0.3"):
                self.assertEqual(self.app._powered_by_text(),
                                 "v1.0.3 · Powered by yt-dlp 2026.08.19")
        with mock.patch.object(engine, "yt_dlp_version", return_value=None):
            self.assertEqual(self.app._powered_by_text(), "Powered by yt-dlp (not installed)")

    def test_note_when_another_yt_dlp_is_installed(self):
        with mock.patch.object(engine, "yt_dlp_version", return_value="2026.02.21"), \
                mock.patch.object(engine, "pinned_yt_dlp_version", return_value="2026.8.19"):
            self.app._note_yt_dlp_version()
        self.assertIn("yt-dlp 2026.02.21 is installed; this app is tested with "
                      "2026.8.19", self.log())

    def test_install_without_restart(self):
        self.app._installing = True
        with mock.patch.object(engine, "yt_dlp_version", return_value="2026.08.19"), \
                mock.patch.object(self.app, "_start_worker") as start:
            self.app._install_done(True, None, loaded=True)
        self.assertEqual(self.app.powered_lbl.text(), "Powered by yt-dlp 2026.08.19")
        self.assertEqual(self.statuses[-1], "✔ yt-dlp installed. You can download now.")
        self.assertIn("yt-dlp 2026.08.19 installed and ready", self.log())
        self.assertEqual(self.dialogs, [])   # no "please restart"
        self.assertFalse(self.app._installing)
        start.assert_called_once_with(self.app._find_js_runtime_thread)

    def test_install_that_still_needs_a_restart(self):
        self.app._install_done(True, None, loaded=False)
        self.assertEqual(self.titles, ["Installed"])
        self.assertIn("Please restart", self.log())

    def test_download_while_installing_doesnt_install_again(self):
        self.app._installing = True
        with mock.patch.object(engine, "YT_DLP_AVAILABLE", False), \
                mock.patch.object(self.app, "_prompt_install") as prompt:
            self.app._start_download()
        prompt.assert_not_called()
        self.assertEqual(self.statuses[-1], "Installing yt-dlp, please wait…")

    def test_update_notice_is_logged(self):
        with mock.patch.object(engine, "fetch_update_notice",
                               return_value="• yt-dlp 2099.1.1 is out"):
            self.app._start_worker(self.app._update_thread)
            self.wait(lambda: self.app._active_workers == 0)
        self.assertIn("• yt-dlp 2099.1.1 is out", self.log())

    # ── Settings ──

    def test_folder_and_format_are_remembered(self):
        with tempfile.TemporaryDirectory() as folder:
            self.app.path.setText(folder)
            self.app.format.setCurrentIndex(self.app.format.findData("mp3"))
            self.run_download(make_fake_ydl(streams=1))
            again = self.new_window()   # the next time the app starts
            self.assertEqual(again.path.text(), folder)
            self.assertEqual(again._chosen_format(), "mp3")
            self.assertFalse(again.whole_playlist.isChecked())

    def test_bad_saved_settings_fall_back_to_defaults(self):
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump({"folder": 5, "format": "bogus", "theme": "purple",
                       "check_updates": "yes"}, f)
        again = self.new_window()
        self.assertEqual(again.path.text(), os.path.normpath(os.path.expanduser("~/Downloads")))
        self.assertEqual(again._chosen_format(), "mp4_best")
        self.assertEqual(again.theme_mode, "system")
        self.assertTrue(again._check_updates)

    def test_an_unavailable_saved_folder_is_kept(self):
        # e.g. on a USB drive that isn't plugged in this time.
        gone = os.path.join(os.path.dirname(self.settings_file), "unplugged", "Videos")
        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump({"folder": gone}, f)
        again = self.new_window()
        self.assertIn(f"The saved folder {gone} isn't available", again.log_box.toPlainText())
        self.assertEqual(again.path.text(), again._default_folder)
        again._save_settings()
        self.assertEqual(ytd.load_settings()["folder"], gone, "forgotten")
        with tempfile.TemporaryDirectory() as chosen, \
                mock.patch.object(ytd.QFileDialog, "getExistingDirectory", return_value=chosen):
            again._browse_folder()
            again._save_settings()
            self.assertEqual(ytd.load_settings()["folder"], os.path.normpath(chosen))

    def test_the_update_switch_only_in_a_release_and_is_saved(self):
        self.assertIsNone(self.app.update_switch)
        with mock.patch.object(engine, "app_version", return_value="1.0.3"):
            window = self.new_window()
        self.assertTrue(window.update_switch.isChecked())
        window.update_switch.setChecked(False)
        self.assertIs(ytd.load_settings()["check_updates"], False)

    # ── Themes ──

    def test_the_system_theme_by_default(self):
        self.assertEqual(self.app.theme_mode, "system")
        self.assertTrue(self.app.theme_chips["system"].isChecked())
        with mock.patch.object(ytd.QGuiApplication, "styleHints") as hints:
            hints.return_value.colorScheme.return_value = ytd.Qt.ColorScheme.Light
            self.assertEqual(self.app._theme_name(), "light")
            hints.return_value.colorScheme.return_value = ytd.Qt.ColorScheme.Unknown
            self.assertEqual(self.app._theme_name(), "dark", "the fallback")

    def test_choosing_a_theme_repaints_and_is_saved(self):
        self.app._set_status("Downloading…", ytd.WARNING)
        self.app.theme_chips["light"].click()
        light = ytd.THEMES["light"]
        self.assertEqual(ytd.load_settings()["theme"], "light")
        self.assertIs(self.app.colors, light)
        self.assertIs(self.app.model.colors, light)
        self.assertIn(f"background: {light['bg']}", self.app.styleSheet())
        self.assertIn(light["warning"], self.app.status_lbl.styleSheet(), "same role")
        self.assertTrue(self.app.theme_chips["light"].isChecked())
        self.assertFalse(self.app.theme_chips["system"].isChecked())
        self.app.theme_chips["dark"].click()
        self.assertIs(self.app.colors, ytd.THEMES["dark"])
        self.assertIn(ytd.THEMES["dark"]["warning"], self.app.status_lbl.styleSheet())

    def test_saved_theme_is_used_at_startup(self):
        ytd.save_settings({"theme": "light"})
        self.assertIs(self.new_window().colors, ytd.THEMES["light"])


if __name__ == "__main__":
    unittest.main()
