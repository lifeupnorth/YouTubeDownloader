"""Builds the one-file Windows app, dist/YT-Downloader.exe.

The .exe bundles Python, the app, yt-dlp with its "default" extras (minus
mutagen) and Pillow (the versions pinned in requirements.txt), ffmpeg and deno (the JavaScript runtime yt-dlp uses for
YouTube), so users don't need to install anything.
Run it on Windows, from the repository's root folder:

    pip install -r requirements.txt
    pip install --require-hashes -r packaging/requirements-build.txt
    python packaging/build_exe.py            # builds dist/YT-Downloader.exe
    python packaging/build_exe.py --check    # runs the .exe's self-check

GitHub Actions does the same (.github/workflows/build-exe.yml).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.join(ROOT, "packaging")
CACHE = os.path.join(HERE, ".cache")    # the downloads, kept between builds
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")
NAME = "YT-Downloader"
# The .exe's version information (Properties → Details). SignPath, which signs
# releases, checks the product name.
PRODUCT_NAME = "YT Downloader"
COPYRIGHT = "Copyright (c) 2026 Alexey Kuprienko (MIT License)"

# gyan.dev's "essentials" build (GPL), from its GitHub mirror. The checksum is
# the one gyan.dev publishes; a download that doesn't match is refused.
FFMPEG_VERSION = "9.0.2"
FFMPEG_ZIP = f"ffmpeg-{FFMPEG_VERSION}-essentials_build.zip"
FFMPEG_URL = ("https://github.com/GyanD/codexffmpeg/releases/download/"
              f"{FFMPEG_VERSION}/{FFMPEG_ZIP}")
FFMPEG_SHA256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"

# deno's official Windows build. The checksum is the one in the release's
# deno-x86_64-pc-windows-msvc.zip.sha256sum. Its MIT license is in
# packaging/DENO-LICENSE.txt (LICENSE.md at the same tag).
DENO_VERSION = "2.9.7"
DENO_ZIP = "deno-x86_64-pc-windows-msvc.zip"
DENO_URL = f"https://github.com/denoland/deno/releases/download/v{DENO_VERSION}/{DENO_ZIP}"
DENO_SHA256 = "a0c3101b4158d1dfb7d6a78a7bf0f3de80c96bb423c152beec8beb22786f2238"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def unpack(url, zip_name, checksum, wanted, folder):
    """Downloads (once) and checks a zip, then unpacks the files in wanted
    ({path in the zip, without its top-level folder: file name}) into
    build/<folder>. Returns {path in the zip: unpacked path}."""
    os.makedirs(CACHE, exist_ok=True)
    zip_path = os.path.join(CACHE, zip_name)
    if not os.path.isfile(zip_path) or sha256(zip_path) != checksum:
        print(f"Downloading {url}")
        with urllib.request.urlopen(url, timeout=120) as response, \
                open(zip_path + ".part", "wb") as f:
            shutil.copyfileobj(response, f)
        os.replace(zip_path + ".part", zip_path)
    actual = sha256(zip_path)
    if actual != checksum:
        os.remove(zip_path)
        sys.exit(f"{zip_name} has the wrong checksum ({actual}); not using it.")

    out = os.path.join(BUILD, folder)
    os.makedirs(out, exist_ok=True)
    found = {}
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            inner = member.split("/", 1)[-1]   # drop the top-level folder, if any
            if inner in wanted:
                target = os.path.join(out, wanted[inner])
                with z.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                found[inner] = target
    missing = set(wanted) - set(found)
    if missing:
        sys.exit(f"{zip_name} doesn't contain {', '.join(sorted(missing))}")
    return found


def get_ffmpeg():
    """ffmpeg.exe and its license, unpacked into the build folder."""
    found = unpack(FFMPEG_URL, FFMPEG_ZIP, FFMPEG_SHA256,
                   {"bin/ffmpeg.exe": "ffmpeg.exe", "LICENSE": "FFMPEG-LICENSE.txt"},
                   "ffmpeg")
    return found["bin/ffmpeg.exe"], found["LICENSE"]


def get_deno():
    """deno.exe, unpacked into the build folder."""
    return unpack(DENO_URL, DENO_ZIP, DENO_SHA256, {"deno.exe": "deno.exe"},
                  "deno")["deno.exe"]


def version_resource(version):
    """The .exe's Windows version information, as the text PyInstaller's
    --version-file reads. version is e.g. "1.0.3", or "" for a test build."""
    numbers = [int(part) for part in version.split(".") if part.isdigit()][:3]
    numbers = tuple(numbers + [0] * (4 - len(numbers)))
    text = version or "0.0.0"
    strings = {
        "FileDescription": PRODUCT_NAME,
        "FileVersion": text,
        "InternalName": NAME,
        "LegalCopyright": COPYRIGHT,
        "OriginalFilename": f"{NAME}.exe",
        "ProductName": PRODUCT_NAME,
        "ProductVersion": text,
    }
    entries = ",\n          ".join(f"StringStruct({k!r}, {v!r})" for k, v in strings.items())
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
          {entries}])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def product_info(exe):
    """The version information strings of a built .exe ({} without any)."""
    import pefile  # installed with PyInstaller
    pe = pefile.PE(exe)
    info = {}
    for group in getattr(pe, "FileInfo", None) or []:
        for entry in group:
            for table in getattr(entry, "StringTable", []):
                info.update({k.decode(): v.decode() for k, v in table.entries.items()})
    pe.close()
    return info


def build():
    if sys.platform != "win32":
        sys.exit("The .exe has to be built on Windows.")
    import PyInstaller.__main__

    ffmpeg, ffmpeg_license = get_ffmpeg()
    deno = get_deno()
    deno_license = os.path.join(HERE, "DENO-LICENSE.txt")
    # The release workflow sets APP_VERSION (e.g. "v1.0.3"); the app compares
    # it with the latest release. Empty in test builds: no update check.
    version = os.environ.get("APP_VERSION", "").removeprefix("v")
    version_file = os.path.join(BUILD, "VERSION")
    os.makedirs(BUILD, exist_ok=True)
    with open(version_file, "w", encoding="utf-8") as f:
        f.write(version)
    resource_file = os.path.join(BUILD, "version_resource.txt")
    with open(resource_file, "w", encoding="utf-8") as f:
        f.write(version_resource(version))
    notices = os.path.join(HERE, "THIRD-PARTY-NOTICES.txt")
    sep = os.pathsep   # PyInstaller's "source;destination" separator
    PyInstaller.__main__.run([
        "--onefile", "--windowed", "--noconfirm", "--clean",
        "--name", NAME,
        "--version-file", resource_file,
        "--distpath", DIST, "--workpath", os.path.join(BUILD, "pyinstaller"),
        "--specpath", BUILD,
        # Next to the code inside the .exe: ffmpeg and deno (download_engine
        # looks for them there), and requirements.txt for the yt-dlp version
        # check.
        "--add-binary", f"{ffmpeg}{sep}.",
        "--add-binary", f"{deno}{sep}.",
        "--add-data", f"{os.path.join(ROOT, 'requirements.txt')}{sep}.",
        "--add-data", f"{notices}{sep}.",
        "--add-data", f"{ffmpeg_license}{sep}.",
        "--add-data", f"{deno_license}{sep}.",
        "--add-data", f"{version_file}{sep}.",
        # yt-dlp[default] installs mutagen (GPL) to embed thumbnails and
        # metadata, which the app doesn't do; keep it out of the .exe.
        "--exclude-module", "mutagen",
        os.path.join(ROOT, "youtube_downloader.py"),
    ])
    # The notices and licenses also go next to the .exe for users.
    for path in (notices, ffmpeg_license, deno_license):
        shutil.copy2(path, DIST)
    exe = os.path.join(DIST, f"{NAME}.exe")
    print(f"Built {exe} ({os.path.getsize(exe) / 1048576:.0f} MB)")


def check():
    """Runs the built .exe's self-check and fails unless everything it needs
    was bundled and works."""
    exe = os.path.join(DIST, f"{NAME}.exe")
    with tempfile.TemporaryDirectory() as tmp:
        report = os.path.join(tmp, "self-check.json")
        subprocess.run([exe, "--self-check", report], timeout=300, check=True)
        with open(report, encoding="utf-8") as f:
            result = json.load(f)
    print(json.dumps(result, indent=2))
    problems = []
    if not result.get("frozen"):
        problems.append("not running as the bundled .exe")
    if not result.get("yt_dlp") or not result.get("yt_dlp_pin"):
        problems.append("yt-dlp or its pinned version is missing")
    if not result.get("ffmpeg_bundled"):
        problems.append("the bundled ffmpeg wasn't found")
    if f"ffmpeg version {FFMPEG_VERSION}" not in (result.get("ffmpeg_version") or ""):
        problems.append("the bundled ffmpeg didn't run")
    if not result.get("deno_bundled"):
        problems.append("the bundled deno wasn't found")
    if not (result.get("js_runtime_version") or "").startswith(f"deno {DENO_VERSION}"):
        problems.append("the bundled deno didn't run")
    if not result.get("js_solver"):
        problems.append("yt-dlp-ejs (YouTube's challenge solver) wasn't bundled")
    missing = result.get("yt_dlp_extras_missing")
    if missing is None or missing:
        problems.append(f"yt-dlp's extras weren't all bundled: {missing}")
    version = os.environ.get("APP_VERSION", "").removeprefix("v") or None
    if result.get("app_version") != version:
        problems.append(f"the version is {result.get('app_version')}, not {version}")
    if not result.get("thumbnails"):
        problems.append("Pillow (thumbnails) is missing")
    if result.get("window") != "YT Downloader":
        problems.append(f"the window didn't open: {result.get('window')}")
    info = product_info(exe)
    wanted = {"ProductName": PRODUCT_NAME, "ProductVersion": version or "0.0.0"}
    if any(info.get(key) != value for key, value in wanted.items()):
        problems.append(f"the version information is {info}, not {wanted}")
    if problems:
        sys.exit("Self-check failed: " + "; ".join(problems))
    print("Self-check passed.")


if __name__ == "__main__":
    check() if sys.argv[1:] == ["--check"] else build()
