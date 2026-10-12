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
        "thumbnails": True, "svg_images": True, "window": "YT Downloader",
    }
    report.update(changes)
    return report


class CheckTest(unittest.TestCase):
    """build_exe.py --check, which decides whether a release .exe is good."""

    def check(self, report, app_version="", product=None, icon=True):
        def run_exe(args, **kwargs):
            with open(args[2], "w", encoding="utf-8") as f:
                json.dump(report, f)

        version = app_version.removeprefix("v") or "0.0.0"
        if product is None:
            product = {"ProductName": build_exe.PRODUCT_NAME, "ProductVersion": version}
        with (mock.patch.object(build_exe.subprocess, "run", side_effect=run_exe),
              mock.patch.object(build_exe, "product_info", return_value=product),
              mock.patch.object(build_exe, "has_app_icon", return_value=icon),
              mock.patch.dict(os.environ, {"APP_VERSION": app_version}),
              contextlib.redirect_stdout(io.StringIO()) as out):
            build_exe.check()
        return out.getvalue()

    def problems(self, report, app_version="", product=None, icon=True):
        with self.assertRaises(SystemExit) as stop:
            self.check(report, app_version, product, icon)
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
            "svg_images": (False, "Qt's SVG support"),
            "window": ("failed: no display", "the window didn't open"),
        }
        for key, (value, message) in cases.items():
            with self.subTest(key):
                self.assertIn(message, self.problems(good_report(**{key: value})))

    def test_an_old_self_check_without_the_extras_fails(self):
        report = good_report()
        del report["yt_dlp_extras_missing"]
        self.assertIn("yt-dlp's extras", self.problems(report))

    def test_the_version_information_must_be_there(self):
        # SignPath checks the product name when it signs a release.
        self.assertIn("the version information is {}",
                      self.problems(good_report(), product={}))
        self.assertIn("the version information",
                      self.problems(good_report(app_version="1.0.3"), app_version="v1.0.3",
                                    product={"ProductName": build_exe.PRODUCT_NAME,
                                             "ProductVersion": "0.0.0"}))

    def test_the_version_must_be_the_release(self):
        self.assertIn("the version is None, not 1.0.3",
                      self.problems(good_report(), app_version="v1.0.3"))
        self.assertIn("the version is 1.0.3, not None",
                      self.problems(good_report(app_version="1.0.3")))

    def test_the_exe_must_have_the_apps_icon(self):
        self.assertIn("the .exe doesn't have the app's icon",
                      self.problems(good_report(), icon=False))


class IconTest(unittest.TestCase):
    """The .exe's icon: the window's, drawn at each size."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.icon = os.path.join(self.dir, "icon.ico")
        build_exe.write_icon(self.icon)

    def test_each_size_is_drawn_for_itself(self):
        from PIL import Image

        import youtube_downloader as ytd
        ico = Image.open(self.icon)
        self.assertEqual(sorted(ico.info["sizes"]), [(s, s) for s in ytd.ICON_SIZES])
        for size in ytd.ICON_SIZES:
            with self.subTest(size=size):
                ico.size = (size, size)
                ico.load()
                drawn = ytd.app_icon_image(size).convertToFormat(
                    ytd.QImage.Format_RGBA8888)
                pixels = drawn.constBits().tobytes()
                self.assertEqual(ico.convert("RGBA").tobytes(), pixels,
                                 "scaled down instead of drawn")

    def exe(self, contents):
        path = os.path.join(self.dir, "app.exe")
        with open(path, "wb") as f:
            f.write(contents)
        return path

    def test_the_exe_must_hold_every_image_of_the_icon(self):
        images = build_exe.ico_images(self.icon)
        self.assertEqual(len(images), 6)
        exe = self.exe(b"MZ" + b"...".join(images) + b"rest")
        self.assertTrue(build_exe.has_app_icon(exe))
        # PyInstaller's own icon, or ours with a size missing, won't do.
        self.assertFalse(build_exe.has_app_icon(self.exe(b"MZ" + b"".join(images[1:]))))


class VersionResourceTest(unittest.TestCase):
    def test_release_and_test_builds(self):
        for version, numbers in (("1.0.3", "(1, 0, 3, 0)"), ("", "(0, 0, 0, 0)")):
            with self.subTest(version=version):
                text = build_exe.version_resource(version)
                self.assertIn(f"filevers={numbers}", text)
                self.assertIn(f"StringStruct('ProductVersion', '{version or '0.0.0'}')", text)
                self.assertIn(f"StringStruct('ProductName', '{build_exe.PRODUCT_NAME}')", text)

    def test_pyinstaller_can_read_it(self):
        try:
            from PyInstaller.utils.win32 import versioninfo
        except ImportError:
            self.skipTest("PyInstaller isn't installed")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "version.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(build_exe.version_resource("1.0.3"))
            info = versioninfo.load_version_info_from_text_file(path)
        self.assertEqual(info.ffi.fileVersionMS, (1 << 16) | 0)
        self.assertEqual(info.ffi.fileVersionLS, 3 << 16)


def _normalize(name):
    return name.lower().replace("_", "-")


class ThirdPartyLicensesTest(unittest.TestCase):
    def test_bundled_packages_are_what_requirements_install(self):
        # What yt-dlp[default], PySide6 and Pillow need, all the way down,
        # without mutagen (kept out of the .exe); plus PyInstaller's bootloader.
        import importlib.metadata
        try:
            from packaging.requirements import Requirement
        except ImportError:
            from pip._vendor.packaging.requirements import Requirement
        try:
            todo = [("yt-dlp", "default"), ("pyside6-essentials", ""), ("pillow", "")]
            found = set()
            while todo:
                name, extra = todo.pop()
                if _normalize(name) in found or _normalize(name) == "mutagen":
                    continue
                found.add(_normalize(name))
                for line in importlib.metadata.requires(name) or []:
                    req = Requirement(line)
                    if req.marker is None or req.marker.evaluate({"extra": extra}):
                        todo.append((req.name, next(iter(req.extras), "")))
        except importlib.metadata.PackageNotFoundError as missing:
            self.skipTest(f"{missing} isn't installed")
        self.assertEqual(found | {"pyinstaller"},
                         {_normalize(name) for name in build_exe.BUNDLED_PACKAGES})

    def test_every_package_is_in_the_file(self):
        import importlib.metadata
        for name in build_exe.BUNDLED_PACKAGES:
            try:
                importlib.metadata.distribution(name)
            except importlib.metadata.PackageNotFoundError:
                self.skipTest(f"{name} isn't installed (PyInstaller only is for builds)")
        if sys.version_info[:2] != build_exe.PYTHON_LICENSES_VERSION:
            self.skipTest("PYTHON-LICENSES.rst is for another Python version")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "licenses.txt")
            build_exe.third_party_licenses(path)
            with open(path, encoding="utf-8") as f:
                text = f.read()
        for heading in ("OpenSSL", "Software incorporated in Python",
                        "Qt and PySide6 (PySide6_Essentials 6.", "GNU LESSER GENERAL PUBLIC",
                        "The GNU General Public License v3"):
            self.assertIn(heading, text)
        self.assertNotIn("Tcl/Tk", text)
        self.assertNotIn("Qt Commercial", text, "the Qt packages' own license file")
        for name in build_exe.BUNDLED_PACKAGES:
            if name in build_exe.QT_PACKAGES:
                continue
            dist = importlib.metadata.distribution(name)
            self.assertIn(f"{dist.metadata['Name']} {dist.version}: ", text)


if __name__ == "__main__":
    unittest.main()
