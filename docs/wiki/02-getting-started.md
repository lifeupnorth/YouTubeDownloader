# 2 Getting Started

<details>
<summary>Relevant source files</summary>

- [README.md](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/README.md)
- [requirements.txt](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/requirements.txt)
- [youtube_downloader.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/youtube_downloader.py)
- [download_engine.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/download_engine.py)

</details>

How to install, run, configure and test the app. There's no installer or packaging: the app is two Python files, the pinned packages in `requirements.txt`, and ffmpeg. For what happens once it's running, see [Overview](01-overview.md).

## Requirements

| Requirement | Why | How it's handled |
|---|---|---|
| Python 3.10+ with Tkinter | The UI is `tkinter`/`ttk`; the pinned yt-dlp needs 3.10 | Bundled with python.org installers on Windows and macOS; some Linux distros need a separate `python3-tk` package |
| `yt-dlp` | Does the extraction and downloading | Pinned in `requirements.txt`. Optional at import time: if it's missing, the app offers to install that same version with pip and loads it right away, without a restart. The header shows the version in use, and the log notes when it differs from the pin. |
| `ffmpeg` | Merging video and audio, remuxing to MP4, converting to MP3/M4A | Checked at startup and before every download; on Windows it's also found after an install made while the app is open |
| `Pillow` (optional) | Thumbnails in the link preview | Pinned in `requirements.txt`; without it the preview shows text only |

Sources: [README.md:4-10](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/README.md#L4-L10), [requirements.txt](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/requirements.txt), [download_engine.py:22-92](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/download_engine.py#L22-L92), [youtube_downloader.py:22-25](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/youtube_downloader.py#L22-L25)

## Install and run

```
pip install -r requirements.txt
winget install ffmpeg          (Windows; macOS: brew install ffmpeg)
python youtube_downloader.py
```

The entry point creates `YouTubeDownloader()` and runs Tk's `mainloop()`. On Windows, `pythonw` instead of `python` starts it without a console window; yt-dlp's warnings and errors go to the app's log (marked ⚠ and ✘), so nothing is lost without a console.

Sources: [README.md:11-35](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/README.md#L11-L35), [youtube_downloader.py:761-764](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/youtube_downloader.py#L761-L764), [download_engine.py:174-194](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/download_engine.py#L174-L194)

## Using it

1. Paste one or more links into **YouTube URLs** (one per line), or click **Paste**. A preview of the first link appears below.
2. Pick a **Format** and a folder under **Save To**.
3. Click **Download** or press Enter. Progress, saved files and errors appear in the status line and the log; the button turns into **Cancel** while it runs.
4. When it's done, **Open folder** shows the saved file.

With **Whole playlist** unticked, a link to a video opened from a playlist downloads just that video, and a playlist, channel or tab link asks before downloading everything. Playlist videos go to `<Save To>/<playlist title>/<NNN> - <title>.<ext>`, single videos to `<Save To>/<title>.<ext>`.

Sources: [README.md:36-59](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/README.md#L36-L59), [download_engine.py:333-354](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/download_engine.py#L333-L354)

## The one-file Windows app

`packaging/build_exe.py` builds `dist/YT-Downloader.exe` with PyInstaller (pinned in `packaging/requirements-build.txt`). It contains Python, the app, the pinned yt-dlp, yt-dlp-ejs and Pillow, gyan.dev's ffmpeg "essentials" build, and deno's official Windows build (the JavaScript runtime yt-dlp uses for YouTube). The script downloads both and checks them against pinned SHA-256s. Inside the .exe, `engine.bundled_ffmpeg()` and `engine.bundled_deno()` find them in PyInstaller's unpack folder (`sys._MEIPASS`) before looking anywhere else, and the yt-dlp install prompt is replaced by an error, since there's no pip. `build_exe.py --check` runs the .exe with `--self-check`, which writes what the build contains to a JSON file (yt-dlp version, the bundled ffmpeg and deno and their versions, the challenge solver, Pillow, whether the window opens). The "Windows app" workflow (`.github/workflows/build-exe.yml`) builds and checks it on demand, on pull requests that change the build or the pins, for version tags, and for pushes to `main` that change the yt-dlp pin. For those two it also creates a GitHub release: the tag's, or the next patch version (`v1.0.0` → `v1.0.1`), so a merged Dependabot bump reaches users of the .exe. The release build gets that version through `APP_VERSION`, which `build_exe.py` writes to a bundled `VERSION` file (`engine.app_version()`, shown in the header). At startup a release .exe runs `engine.check_for_update()` on a worker and logs a line if GitHub's latest release is newer. It checks at most once a day (`last_update_check` in settings.json) and only while "Check for updates at startup" (`check_updates`) is ticked. From source, and in `--self-check`, it never checks; from source the app instead notes a yt-dlp that doesn't match the pin. Without a network it says nothing. `v1.0.0` was built before the `VERSION` file existed, so it can't tell that it's out of date. ffmpeg is GPL-licensed, so its license, deno's (`packaging/DENO-LICENSE.txt`) and the other notices (`packaging/THIRD-PARTY-NOTICES.txt`) go inside the .exe and next to it.

From source, `engine.find_js_runtime()` looks for deno, node or bun on `PATH` (and in the registry's `Path` on Windows) and asks yt-dlp whether the version is new enough. If none is found, the window logs an install hint once at startup instead of yt-dlp's warning on every download, and looks again before each download until one is found.

Signing is optional: `packaging/sign_exe.py` signs and verifies the .exe with `signtool` (SHA-256, RFC 3161 timestamp) when `WINDOWS_SIGNING_CERT` (a base64-encoded `.pfx`) and `WINDOWS_SIGNING_PASSWORD` are set, and does nothing without them. The secrets live in the `code-signing` GitHub environment, which pull-request runs never use; the workflow passes them only to that step, skips it for pull requests, and signs before `--check`, so the check runs the signed file. If the certificate is set but the password is missing, the certificate isn't base64, `signtool` is missing, or signing fails, the build fails (leaving out any line that contains the password) instead of quietly releasing an unsigned .exe.

## Settings

There are no environment variables or command-line options. The last **Save To** folder and **Format** are remembered in a small JSON file:

| System | File |
|---|---|
| Windows | `%APPDATA%\YT Downloader\settings.json` |
| macOS | `~/Library/Application Support/YT Downloader/settings.json` |
| Elsewhere | `$XDG_CONFIG_HOME` or `~/.config`, then `YT Downloader/settings.json` |

It's saved when a download starts and when the app closes, through a temporary file so a crash can't leave half of it. A missing folder, an unknown format or a broken file falls back to the defaults. **Whole playlist** is never remembered, so it always starts unticked.

Sources: [youtube_downloader.py:60-89](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/youtube_downloader.py#L60-L89), [youtube_downloader.py:356-366](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/youtube_downloader.py#L356-L366)

## Running the tests and the lint check

```
python -m unittest discover -s tests -v
pip install -r requirements-dev.txt
ruff check .
```

The engine tests need nothing but Python; the window tests need a display (they open real Tk windows). Tests that need yt-dlp or Pillow skip when they're missing. See [Testing](07-testing.md).

Sources: [README.md:60-73](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/README.md#L60-L73)
