"""The engine with the real yt-dlp, offline: files come from a local server.

The other tests use a fake yt-dlp, so they can't notice when a new yt-dlp
version changes something the engine relies on (option names, its
post-processors, progress hooks, file names). These download for real from
127.0.0.1, which yt-dlp's generic extractor treats as a direct link, so they
need no internet. The MP3 test also needs ffmpeg and is skipped without it.

Run with:  python -m unittest discover -s tests -v
"""
import functools
import http.server
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(TESTS), TESTS]
import download_engine as engine  # noqa: E402

FFMPEG = engine.find_ffmpeg() if engine.YT_DLP_AVAILABLE else None


class _Handler(http.server.SimpleHTTPRequestHandler):
    slow = False   # send slowly, so a test can cancel halfway
    stopping = None   # an Event: the test is over, stop sending

    def log_message(self, *args):   # keep the test output clean
        pass

    def copyfile(self, source, outputfile):
        if not self.slow:
            return super().copyfile(source, outputfile)
        while (chunk := source.read(16 * 1024)) and not self.stopping.is_set():
            try:
                outputfile.write(chunk)
            except OSError:   # the download was canceled
                return
            time.sleep(0.02)


class _Server(http.server.ThreadingHTTPServer):
    # Closing waits for the requests still being answered: on Windows the
    # temporary folder can't be deleted while one has a served file open.
    daemon_threads = False
    block_on_close = True


@unittest.skipUnless(engine.YT_DLP_AVAILABLE, "needs yt-dlp")
class RealYtDlpTest(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.served = os.path.join(root.name, "served")
        self.folder = os.path.join(root.name, "downloads")
        os.makedirs(self.served)
        handler = type("Handler", (_Handler,), {"stopping": threading.Event()})
        self.handler = handler
        self.server = _Server(
            ("127.0.0.1", 0), functools.partial(handler, directory=self.served))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        # Run in reverse: stop sending, stop serving, wait for the requests
        # still running (server_close), then delete the folder.
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(handler.stopping.set)

    def serve(self, name, data):
        with open(os.path.join(self.served, name), "wb") as f:
            f.write(data)
        return f"http://127.0.0.1:{self.server.server_port}/{name}"

    def download(self, url, fmt="mp4_best", on_progress=None):
        log = []
        job = engine.DownloadJob(
            url, fmt=fmt, folder=self.folder, ffmpeg=FFMPEG, whole_playlist=False,
            on_progress=on_progress or (lambda progress: None),
            on_log=log.append, ask=lambda question: True)
        job.run()
        job.log = log
        return job

    def files(self):
        return sorted(os.listdir(self.folder)) if os.path.isdir(self.folder) else []

    def test_download_then_already_downloaded(self):
        url = self.serve("clip.mp4", os.urandom(300_000))
        job = self.download(url)
        self.assertEqual((job.outcome, job.saved, job.already), ("done", 1, 0), job.log)
        self.assertEqual(job.last_file, os.path.join(self.folder, "clip [clip].mp4"))
        self.assertEqual(os.path.getsize(job.last_file), 300_000)
        self.assertEqual(self.files(), ["clip [clip].mp4"], "no .part or temporary files left")

        again = self.download(url)
        self.assertEqual((again.outcome, again.saved, again.already), ("done", 0, 1))
        self.assertIn(f"• Already downloaded: {job.last_file}", again.log)

    @unittest.skipUnless(FFMPEG, "needs ffmpeg")
    def test_mp3_is_converted_once(self):
        # A WebM with one second of audio, made by ffmpeg.
        source = os.path.join(self.served, "tone.webm")
        subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", "sine=duration=1",
                        "-c:a", "libopus", source], check=True)
        url = f"http://127.0.0.1:{self.server.server_port}/tone.webm"
        job = self.download(url, fmt="mp3")
        self.assertEqual((job.outcome, job.saved), ("done", 1), job.log)
        self.assertEqual(self.files(), ["tone [tone].mp3"], "the WebM is deleted after converting")

        # The second run must see the MP3 (yt-dlp's final_ext), not download
        # and convert again.
        again = self.download(url, fmt="mp3")
        self.assertEqual((again.outcome, again.saved, again.already), ("done", 0, 1),
                         again.log)
        self.assertEqual(self.files(), ["tone [tone].mp3"])

    def test_cancel_removes_the_partial_file(self):
        self.handler.slow = True
        url = self.serve("big.mp4", os.urandom(2_000_000))
        job = None

        def on_progress(progress):
            if progress.percent > 5:   # some of it is on disk
                job.cancel()

        log = []
        job = engine.DownloadJob(
            url, fmt="mp4_best", folder=self.folder, ffmpeg=FFMPEG, whole_playlist=False,
            on_progress=on_progress, on_log=log.append, ask=lambda question: True)
        start = time.monotonic()
        job.run()
        self.assertLess(time.monotonic() - start, 20, "the cancel didn't stop it")
        self.assertEqual(job.outcome, "canceled", log)
        self.assertGreaterEqual(job.removed, 1)
        self.assertEqual(self.files(), [], "the .part file is left behind")


if __name__ == "__main__":
    unittest.main()
