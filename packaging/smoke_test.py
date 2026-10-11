"""Downloads one short video for real, as MP4 and as MP3, so a YouTube change
that breaks the app shows up before users report it. The nightly "Smoke test"
workflow runs it from source and against the built .exe.

    python packaging/smoke_test.py                                # from source
    python packaging/smoke_test.py --exe dist/YT-Downloader.exe   # the .exe

SMOKE_URL picks another video. Downloads go to a temporary folder, deleted
afterwards.
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"   # "Me at the zoo", 19 s
EXTENSIONS = {"mp4_best": ".mp4", "mp3": ".mp3"}
MIN_SIZE = 50_000    # bytes; the 19 s video is several hundred KB either way
ATTEMPTS = 2         # one network hiccup shouldn't fail the night


def problem(report, fmt):
    """Why the download in report (from --smoke-download) failed, or None."""
    if report.get("outcome") != "done":
        return f"ended with {report.get('outcome')}: {report.get('error')}"
    path = report.get("file") or ""
    if not path.endswith(EXTENSIONS[fmt]) or not os.path.isfile(path):
        return f"no {EXTENSIONS[fmt]} file was saved (got {path or 'nothing'})"
    if os.path.getsize(path) < MIN_SIZE:
        return f"{path} is only {os.path.getsize(path)} bytes"
    return None


def download(app, url, fmt):
    """One --smoke-download run; returns why it failed, or None."""
    with tempfile.TemporaryDirectory() as tmp:
        report_path = os.path.join(tmp, "report.json")
        folder = os.path.join(tmp, "downloads")
        try:
            subprocess.run([*app, "--smoke-download", url, fmt, folder, report_path],
                           timeout=600, check=False)
        except subprocess.TimeoutExpired:
            return "took over 10 minutes"
        try:
            with open(report_path, encoding="utf-8") as f:
                report = json.load(f)
        except (OSError, ValueError):
            return "the app wrote no report"
        why = problem(report, fmt)
        if why:
            print("\n".join(report.get("log") or []))
        else:
            print(f"  saved {os.path.basename(report['file'])} "
                  f"({os.path.getsize(report['file']) / 1000:.0f} KB)")
        return why


def main(argv):
    if argv[:1] == ["--exe"] and len(argv) == 2:
        app = [os.path.abspath(argv[1])]
    elif not argv:
        app = [sys.executable, os.path.join(ROOT, "youtube_downloader.py")]
    else:
        sys.exit(__doc__)
    url = os.environ.get("SMOKE_URL") or URL
    failures = []
    for fmt in EXTENSIONS:
        for attempt in range(1, ATTEMPTS + 1):
            print(f"{fmt}, attempt {attempt}: {url}", flush=True)
            why = download(app, url, fmt)
            if why is None:
                break
            print(f"  failed: {why}", flush=True)
        else:
            failures.append(f"{fmt}: {why}")
    if failures:
        sys.exit("Smoke test failed:\n" + "\n".join(failures))
    print("Smoke test passed.")


if __name__ == "__main__":
    main(sys.argv[1:])
