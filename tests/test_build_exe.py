"""packaging/build_exe.py: the downloads it checks and the .exe self-check it
judges. Neither needs Windows or PyInstaller: the network and the .exe are
faked.

Run with:  python -m unittest discover -s tests -v
"""
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "packaging")]
import build_exe  # noqa: E402


def zip_bytes(files):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return data.getvalue()


class UnpackTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = os.path.join(tmp.name, "cache")
        self.build = os.path.join(tmp.name, "build")
        for patcher in (mock.patch.object(build_exe, "CACHE", self.cache),
                        mock.patch.object(build_exe, "BUILD", self.build)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def unpack(self, served, checksum, wanted):
        with (mock.patch.object(build_exe.urllib.request, "urlopen",
                                side_effect=lambda *a, **k: io.BytesIO(served)) as urlopen,
              contextlib.redirect_stdout(io.StringIO())):
            found = build_exe.unpack("https://example.com/x.zip", "x.zip", checksum,
                                     wanted, "x")
        return found, urlopen

    def test_unpacks_the_wanted_files_without_the_top_folder(self):
        served = zip_bytes({"tool-1.0/bin/tool.exe": b"exe", "tool-1.0/LICENSE": b"MIT",
                            "tool-1.0/README": b"skip me"})
        checksum = hashlib.sha256(served).hexdigest()
        found, urlopen = self.unpack(served, checksum, {"bin/tool.exe": "tool.exe",
                                                        "LICENSE": "TOOL-LICENSE.txt"})
        self.assertEqual(sorted(os.listdir(os.path.join(self.build, "x"))),
                         ["TOOL-LICENSE.txt", "tool.exe"])
        with open(found["bin/tool.exe"], "rb") as f:
            self.assertEqual(f.read(), b"exe")
        # The next build uses the cached download.
        _found, urlopen = self.unpack(served, checksum, {"LICENSE": "TOOL-LICENSE.txt"})
        urlopen.assert_not_called()

    def test_refuses_a_download_with_the_wrong_checksum(self):
        served = zip_bytes({"tool.exe": b"tampered"})
        with self.assertRaises(SystemExit) as stop:
            self.unpack(served, "0" * 64, {"tool.exe": "tool.exe"})
        self.assertIn("wrong checksum", str(stop.exception))
        self.assertEqual(os.listdir(self.cache), [], "the bad download is kept")
        self.assertFalse(os.path.exists(self.build), "something was unpacked")

    def test_fails_when_a_file_is_missing(self):
        served = zip_bytes({"tool-1.0/other.exe": b"x"})
        with self.assertRaises(SystemExit) as stop:
            self.unpack(served, hashlib.sha256(served).hexdigest(), {"tool.exe": "tool.exe"})
        self.assertIn("doesn't contain tool.exe", str(stop.exception))


def good_report(**changes):
    report = {
        "frozen": True, "app_version": None,
        "yt_dlp": "2026.08.19", "yt_dlp_pin": "2026.8.19",
        "ffmpeg_bundled": True,
        "ffmpeg_version": f"ffmpeg version {build_exe.FFMPEG_VERSION}-essentials_build",
        "deno_bundled": True, "js_runtime_version": f"deno {build_exe.DENO_VERSION} (stable)",
        "js_solver": "0.8.0", "yt_dlp_extras_missing": [],
        "thumbnails": True, "window": "YT Downloader",
    }
    report.update(changes)
    return report


class CheckTest(unittest.TestCase):
    """build_exe.py --check, which decides whether a release .exe is good."""

    def check(self, report, app_version=""):
        def run_exe(args, **kwargs):
            with open(args[2], "w", encoding="utf-8") as f:
                json.dump(report, f)

        with (mock.patch.object(build_exe.subprocess, "run", side_effect=run_exe),
              mock.patch.dict(os.environ, {"APP_VERSION": app_version}),
              contextlib.redirect_stdout(io.StringIO()) as out):
            build_exe.check()
        return out.getvalue()

    def problems(self, report, app_version=""):
        with self.assertRaises(SystemExit) as stop:
            self.check(report, app_version)
        return str(stop.exception)

    def test_a_good_build_passes(self):
        self.assertIn("Self-check passed.", self.check(good_report()))
        self.assertIn("Self-check passed.",
                      self.check(good_report(app_version="1.0.3"), app_version="v1.0.3"))

    def test_each_missing_part_fails(self):
        cases = {
            "frozen": (False, "not running as the bundled .exe"),
            "yt_dlp": (None, "yt-dlp or its pinned version is missing"),
            "ffmpeg_bundled": (False, "the bundled ffmpeg wasn't found"),
            "ffmpeg_version": ("ffmpeg version 6.1", "the bundled ffmpeg didn't run"),
            "deno_bundled": (False, "the bundled deno wasn't found"),
            "js_runtime_version": (None, "the bundled deno didn't run"),
            "js_solver": (None, "yt-dlp-ejs"),
            "yt_dlp_extras_missing": (["websockets"], "yt-dlp's extras weren't all bundled"),
            "thumbnails": (False, "Pillow (thumbnails) is missing"),
            "window": ("failed: no display", "the window didn't open"),
        }
        for key, (value, message) in cases.items():
            with self.subTest(key):
                self.assertIn(message, self.problems(good_report(**{key: value})))

    def test_an_old_self_check_without_the_extras_fails(self):
        report = good_report()
        del report["yt_dlp_extras_missing"]
        self.assertIn("yt-dlp's extras", self.problems(report))

    def test_the_version_must_be_the_release(self):
        self.assertIn("the version is None, not 1.0.3",
                      self.problems(good_report(), app_version="v1.0.3"))
        self.assertIn("the version is 1.0.3, not None",
                      self.problems(good_report(app_version="1.0.3")))


if __name__ == "__main__":
    unittest.main()
