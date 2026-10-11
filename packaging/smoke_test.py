"""Downloads one short video for real, as MP4 and as MP3, so a YouTube change
that breaks the app shows up before users report it. The nightly "Smoke test"
workflow runs it from source and against the built .exe.

    python packaging/smoke_test.py                                # from source
    python packaging/smoke_test.py --exe dist/YT-Downloader.exe   # the .exe

SMOKE_URL picks another video. Downloads go to a temporary folder, deleted
afterwards. When YouTube only answers with its bot check (it sometimes does
for GitHub's runners), that says nothing about the app: it warns and passes.
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
TIMEOUT = 240        # seconds per attempt; 2 formats x 2 attempts fit the job
BOT_CHECK = ("Sign in to confirm", "not a bot")
BOT_CHECK_FAILURE = "YouTube's bot check"


def problem(report, fmt):
    """Why the download in report (from --smoke-download) failed, or None."""
    if report.get("outcome") != "done":
        text = "\n".join([str(report.get("error")), *(report.get("log") or [])])
        if any(phrase in text for phrase in BOT_CHECK):
            return f"{BOT_CHECK_FAILURE}: {report.get('error')}"
        return f"ended with {report.get('outcome')}: {report.get('error')}"
    path = report.get("file") or ""
    if not path.endswith(EXTENSIONS[fmt]) or not os.path.isfile(path):
        return f"no {EXTENSIONS[fmt]} file was saved (got {path or 'nothing'})"
    if os.path.getsize(path) < MIN_SIZE:
        return f"{path} is only {os.path.getsize(path)} bytes"
    return None


def _print_log(path):
    try:
        with open(path, encoding="utf-8") as f:
            print(f.read(), end="", flush=True)
    except OSError:
        pass


def download(app, url, fmt):
    """One --smoke-download run; returns why it failed, or None."""
    with tempfile.TemporaryDirectory() as tmp:
        report_path = os.path.join(tmp, "report.json")
        folder = os.path.join(tmp, "downloads")
        try:
            subprocess.run([*app, "--smoke-download", url, fmt, folder, report_path],
                           timeout=TIMEOUT, check=False)
        except subprocess.TimeoutExpired:
            _print_log(report_path + ".log")   # the app's log so far
            return f"took over {TIMEOUT} seconds"
        try:
            with open(report_path, encoding="utf-8") as f:
                report = json.load(f)
        except (OSError, ValueError):
            _print_log(report_path + ".log")
            return "the app wrote no report"
        why = problem(report, fmt)
        if why:
            print("\n".join(report.get("log") or []), flush=True)
        else:
            print(f"  saved {os.path.basename(report['file'])} "
                  f"({os.path.getsize(report['file']) / 1000:.0f} KB)", flush=True)
        return why


def main(argv):
    if argv[:1] == ["--exe"] and len(argv) == 2:
        app = [os.path.abspath(argv[1])]
    elif not argv:
        app = [sys.executable, os.path.join(ROOT, "youtube_downloader.py")]
    else:
        sys.exit(__doc__)
    # The app logs ✔/⚠/✘, which a Windows console's code page can't print.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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
    if failures and all(BOT_CHECK_FAILURE in f for f in failures):
        print("::warning::YouTube asked this runner to sign in (its bot check), "
              "so the app wasn't tested tonight:\n" + "\n".join(failures))
        return
    if failures:
        sys.exit("Smoke test failed:\n" + "\n".join(failures))
    print("Smoke test passed.")


if __name__ == "__main__":
    main(sys.argv[1:])
