"""Says when ffmpeg or deno has a newer release than the one the .exe bundles.

Dependabot can't see these pins (FFMPEG_VERSION and DENO_VERSION in
packaging/build_exe.py), so the "Bundled programs" workflow runs this every
week and opens an issue for each update:

    python packaging/check_pins.py                 # prints what's newer
    python packaging/check_pins.py --open-issues   # also opens issues (needs gh)
"""
import json
import os
import subprocess
import sys
import urllib.request

# build_exe next to this file, and the app's download_engine above it.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_exe

from download_engine import newer_version

# name: (GitHub repository, pinned version, the zip build_exe.py downloads,
#        where the trustworthy checksum is published)
PROGRAMS = {
    "ffmpeg": ("GyanD/codexffmpeg", build_exe.FFMPEG_VERSION,
               "ffmpeg-{version}-essentials_build.zip",
               "https://www.gyan.dev/ffmpeg/builds/ (the .sha256 next to the zip)",
               "FFMPEG_VERSION and FFMPEG_SHA256"),
    "deno": ("denoland/deno", build_exe.DENO_VERSION, build_exe.DENO_ZIP,
             "the release's deno-x86_64-pc-windows-msvc.zip.sha256sum",
             "DENO_VERSION and DENO_SHA256"),
}


def _get_json(url):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "YT-Downloader"}
    if os.environ.get("GH_TOKEN"):   # a higher rate limit in GitHub Actions
        headers["Authorization"] = f"Bearer {os.environ['GH_TOKEN']}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                timeout=30) as response:
        return json.load(response)


def updates():
    """[{name, pinned, latest, url, sha256, ...}] for each program with a
    newer release."""
    found = []
    for name, (repo, pinned, zip_name, checksum_source, constants) in PROGRAMS.items():
        release = _get_json(f"https://api.github.com/repos/{repo}/releases/latest")
        latest = release["tag_name"].removeprefix("v")
        if not newer_version(latest, pinned):
            continue
        wanted = zip_name.format(version=latest)
        asset = next((a for a in release.get("assets", []) if a["name"] == wanted), {})
        found.append({
            "name": name, "pinned": pinned, "latest": latest, "zip": wanted,
            "url": asset.get("browser_download_url") or release["html_url"],
            "sha256": (asset.get("digest") or "").removeprefix("sha256:") or None,
            "release": release["html_url"], "checksum_source": checksum_source,
            "constants": constants,
        })
    return found


def issue(update):
    """The (title, body) of the issue asking to update one program."""
    title = f"Update the bundled {update['name']} to {update['latest']}"
    sha = update["sha256"] or "(GitHub didn't list one; compute it from the download)"
    body = f"""\
{update['name']} {update['latest']} is out; the .exe bundles {update['pinned']}.

- Release: {update['release']}
- Download: {update['url']}
- SHA-256 that GitHub recorded for `{update['zip']}`: `{sha}`

To update, set {update['constants']} in `packaging/build_exe.py`. Check the
SHA-256 against {update['checksum_source']} before using it. The "Windows app"
workflow's `--check` then runs the new build in the .exe. Read the release
notes for changes that affect the app (for deno: yt-dlp's minimum version).

This issue was opened by the "Bundled programs" workflow
(`packaging/check_pins.py`)."""
    return title, body


def open_issues(found):
    for update in found:
        title, body = issue(update)
        existing = subprocess.run(
            ["gh", "issue", "list", "--state", "all", "--search", f'"{title}" in:title',
             "--json", "title", "--jq", ".[].title"],
            capture_output=True, text=True, check=True).stdout.splitlines()
        if title in existing:
            print(f"Already reported: {title}")
            continue
        subprocess.run(["gh", "issue", "create", "--title", title, "--body", body],
                       check=True)


def main(argv):
    found = updates()
    for update in found:
        print(f"{update['name']}: {update['pinned']} -> {update['latest']}")
    if not found:
        print("The bundled ffmpeg and deno are the latest releases.")
    if "--open-issues" in argv:
        open_issues(found)


if __name__ == "__main__":
    main(sys.argv[1:])
