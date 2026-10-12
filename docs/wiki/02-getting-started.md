# 2 Getting Started

<details>
<summary>Relevant source files</summary>

- [README.md](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/README.md)
- [requirements.txt](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/requirements.txt)
- [youtube_downloader.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py)
- [download_engine.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py)
- [packaging/build_exe.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/packaging/build_exe.py)
- [packaging/check_pins.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/packaging/check_pins.py)
- [.github/workflows/build-exe.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/.github/workflows/build-exe.yml)
- [.github/workflows/pins.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/.github/workflows/pins.yml)

</details>

How to install, run, configure and test the app. From source, the app is two Python files, the pinned packages in `requirements.txt`, ffmpeg and a JavaScript runtime. On Windows there's also a one-file .exe with all of that built in (see [The one-file Windows app](#the-one-file-windows-app)). For what happens once it's running, see [Overview](01-overview.md).

## Requirements

| Requirement | Why | How it's handled |
|---|---|---|
| Python 3.10+ | The pinned yt-dlp needs 3.10 | CI tests 3.10 and 3.14; the .exe is built with 3.14 |
| `PySide6-Essentials` | The window is Qt, through PySide6 ("Essentials" is Qt's core modules, without QML or 3D) | Pinned in `requirements.txt`; required. On a minimal Linux system Qt may also need some system libraries; CI installs `libegl1`, `libgl1`, `libxkbcommon0`, `libfontconfig1` and `libdbus-1-3` |
| `yt-dlp[default]` | Does the extraction and downloading. The `default` extra brings yt-dlp-ejs (the solver for YouTube's JavaScript challenges) plus websockets, requests, brotli, certifi, mutagen and pycryptodomex | Pinned in `requirements.txt`. Optional at import time: if it's missing, the app offers to install that same requirement with pip and loads it right away, without a restart (the .exe shows an error instead, since it has no pip). The header shows the version in use, and the log notes when it differs from the pin. When a JavaScript runtime is found but yt-dlp-ejs isn't installed, the log says so at startup. |
| `ffmpeg` | Merging video and audio, remuxing to MP4, converting to MP3/M4A | Checked at startup and before every download; on Windows it's also found after an install made while the app is open. The .exe bundles it. |
| A JavaScript runtime: deno, node or bun | yt-dlp runs yt-dlp-ejs in it for YouTube; without one some formats may be missing | `find_js_runtime()` looks for one at startup; if none is found, the log shows an install hint once and the app looks again before each download. The .exe bundles deno. |
| `Pillow` (optional) | Thumbnails in the Downloads list | Pinned in `requirements.txt`; without it rows show titles only |

Sources: [README.md:4-10](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/README.md#L4-L10), [requirements.txt](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/requirements.txt), [download_engine.py:23-109](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L23-L109), [download_engine.py:268-302](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L268-L302), [youtube_downloader.py:23-27](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L23-L27), [youtube_downloader.py:150-165](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L150-L165), [youtube_downloader.py:582-591](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L582-L591)

## Install and run

```
pip install -r requirements.txt
winget install ffmpeg          (Windows; macOS: brew install ffmpeg)
winget install DenoLand.Deno   (Windows; macOS: brew install deno)
python youtube_downloader.py
```

`requirements.txt` installs the pinned `yt-dlp[default]`, yt-dlp-ejs included, PySide6-Essentials and Pillow. node or bun work as the JavaScript runtime too; deno is yt-dlp's default. Without arguments, `main()` creates the `QApplication` and `YouTubeDownloader()` and runs Qt's event loop (`app.exec()`). On Windows, `pythonw` instead of `python` starts it without a console window; yt-dlp's warnings and errors go to the app's log (marked ⚠ and ✘), so nothing is lost without a console.

Sources: [README.md:4-35](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/README.md#L4-L35), [youtube_downloader.py:156-165](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L156-L165), [youtube_downloader.py:1027-1034](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L1027-L1034), [download_engine.py:310-337](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L310-L337), [download_engine.py:711-721](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L711-L721)

## Using it

1. Paste one or more links into **YouTube URLs** (separated by spaces), or click **Paste**, which adds the clipboard's links to those already there and leaves out other words. Links work without `https://`.
2. Pick a **Format** (MP4 video, MP3 or M4A audio), a **Quality** for video, and a folder under **Save to**.
3. Click **Download** or press Enter. Each link becomes a row in the **Downloads** list, with its thumbnail and title as soon as they're looked up, and the rows download one at a time, each with its own progress bar and status. More links can be added while others download.
4. Each row has its own icons: Cancel while it waits or downloads, then Show in folder and Remove from the list (which never deletes the file). On the selected row they're also Esc, Enter and Delete, in a right-click menu, and Show in folder on a double-click. **Clear finished** removes all finished rows. The **Log** chip at the bottom shows the log.

With **Whole playlist** off, a link to a video opened from a playlist downloads just that video, and a playlist, channel or tab link asks before downloading everything. Playlist videos go to `<Save to>/<playlist title>/<NNN> - <title>.<ext>`, single videos to `<Save to>/<title>.<ext>`.

Sources: [README.md:88-111](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/README.md#L88-L111), [download_engine.py:574-582](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L574-L582), [download_engine.py:643-658](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L643-L658)

## The one-file Windows app

`packaging/build_exe.py` builds `dist/YT-Downloader.exe` with PyInstaller (pinned, with its dependencies and their hashes, in `packaging/requirements-build.txt`, and installed with `pip install --require-hashes`). It contains Python, the app, Qt (the pinned PySide6-Essentials and shiboken6), the pinned yt-dlp with its `default` extras (yt-dlp-ejs among them; mutagen, which only embeds thumbnails and metadata, is left out) and Pillow, gyan.dev's ffmpeg "essentials" build, and deno's official Windows build (the JavaScript runtime yt-dlp uses for YouTube). The script downloads both programs and checks them against pinned SHA-256s. Inside the .exe, `engine.bundled_ffmpeg()` and `engine.bundled_deno()` find them in PyInstaller's unpack folder (`sys._MEIPASS`) before looking anywhere else, and the yt-dlp install prompt is replaced by an error, since there's no pip. `build_exe.py --check` runs the .exe with `--self-check`, which writes what the build contains to a JSON file (the app and yt-dlp versions, the pin, the bundled ffmpeg and deno and their versions, the challenge solver, any missing yt-dlp extras, Pillow, whether Qt can read SVG images, whether the window opens), and fails unless everything is there. Tkinter is left out of the .exe (`--exclude-module tkinter`), since the window is Qt. Qt makes the .exe bigger than the old Tk window did: a test build without ffmpeg and deno is about 67 MB, against about 40 MB with Tk. The "Windows app" workflow (`.github/workflows/build-exe.yml`) builds and checks it on demand, on every pull request and push to `main`, and for version tags (exactly `vX.Y.Z`). It releases for a version tag, and for a push to `main` whose yt-dlp pin differs from the latest release's (compared with that release, not with the previous commit, so a release can't be lost when pushes queue up): the tag's version, or the next patch version (`v1.0.0` → `v1.0.1`), so a merged Dependabot bump reaches users of the .exe. A release build runs in the `code-signing` environment, so it waits for the owner's approval; other builds don't. The release job records a build provenance attestation (`gh attestation verify YT-Downloader.exe --repo lifeupnorth/YouTubeDownloader`), adds `SHA256SUMS.txt` and the .exe's SHA-256 to the notes, and creates the release as a draft before publishing it, which immutable releases require. The "Bundled programs" workflow (`.github/workflows/pins.yml`, `packaging/check_pins.py`) checks every week whether ffmpeg or deno has a newer release than the pins in `build_exe.py`, and opens an issue for each such release unless one already exists. The release build gets its version through `APP_VERSION`, which `build_exe.py` writes to a bundled `VERSION` file (`engine.app_version()`, shown in the header). At startup a release .exe runs `engine.check_for_update()` on a worker and logs a line if GitHub's latest release is newer. It checks at most once a day (`last_update_check` in settings.json, saved only when the check reached GitHub, so being offline doesn't skip a day) and only while "Check for updates at startup" (`check_updates`) is on. From source, and in `--self-check`, it never checks; from source the app instead notes a yt-dlp that doesn't match the pin. Without a network it says nothing. `v1.0.0` was built before the `VERSION` file existed, so it can't tell that it's out of date. ffmpeg is GPL-licensed, so its license, deno's (`packaging/DENO-LICENSE.txt`) and the other notices (`packaging/THIRD-PARTY-NOTICES.txt`) go inside the .exe and next to it. The build also writes `THIRD-PARTY-LICENSES.txt` with the full license texts of everything else it bundles. Qt and PySide6 are used under the LGPL v3, but their packages only ship the text of Qt's commercial license, so `build_exe.py` puts in the LGPL v3 and the GPL v3 it builds on instead, from `packaging/LGPL-3.0.txt` and `packaging/GPL-3.0.txt`.

Sources: [packaging/build_exe.py:32-46](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/packaging/build_exe.py#L32-L46), [packaging/build_exe.py:105-185](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/packaging/build_exe.py#L105-L185), [download_engine.py:112-161](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L112-L161), [download_engine.py:239-254](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L239-L254), [youtube_downloader.py:869-903](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L869-L903), [youtube_downloader.py:961-997](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L961-L997), [.github/workflows/build-exe.yml:15-108](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/.github/workflows/build-exe.yml#L15-L108), [.github/workflows/build-exe.yml:143-188](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/.github/workflows/build-exe.yml#L143-L188), [.github/workflows/pins.yml:1-36](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/.github/workflows/pins.yml#L1-L36), [packaging/check_pins.py:90-101](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/packaging/check_pins.py#L90-L101)

From source, `engine.find_js_runtime()` looks for deno, node or bun on `PATH` (and in the registry's `Path` on Windows) and asks yt-dlp whether the version is new enough; in the .exe the bundled deno comes first. The lookup runs on a worker at startup. If none is found, the window logs an install hint once instead of yt-dlp's warning on every download, and looks again before each download until one is found.

Sources: [download_engine.py:257-302](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L257-L302), [download_engine.py:718-721](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L718-L721), [youtube_downloader.py:219-221](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L219-L221), [youtube_downloader.py:582-591](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L582-L591), [youtube_downloader.py:705-708](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L705-L708)

Releases are signed through SignPath Foundation, which signs open-source projects for free; the README's [Code signing policy](../../README.md#code-signing-policy) lists the roles and the privacy policy SignPath requires. A release build uploads the unsigned .exe as a single-file artifact, and SignPath's GitHub action submits a signing request. SignPath checks that this repository's workflow built the file on GitHub-hosted runners and waits for an approver; the action then downloads the signed .exe, the workflow verifies its Authenticode signature and swaps it in before `--check`, so the check runs the signed file. It runs only when the `code-signing` environment has the `SIGNPATH_API_TOKEN` secret and the `SIGNPATH_ORGANIZATION_ID` variable (the project and policy slugs default to `YouTubeDownloader` and `release-signing`); pull requests and other builds are never signed. The build also embeds Windows version information (product name "YT Downloader", the release version), which SignPath checks and `--check` verifies.

Sources: [Code signing policy](../../README.md#code-signing-policy), `.github/workflows/build-exe.yml` (the steps after "Build the .exe"), `packaging/build_exe.py` (`version_resource`, `product_info`)

## Settings

There are no environment variables, and the only command-line option is for a check: `--self-check REPORT` (what the build contains). The last **Save to** folder, the **Format** and **Quality** (one value, `format`, e.g. `720p` or `mp3`) and the **Theme** (`theme`: `system`, `light` or `dark`) are remembered in a small JSON file, and in a release .exe also the **Check for updates at startup** switch in the header (`check_updates`) and when it last checked (`last_update_check`):

| System | File |
|---|---|
| Windows | `%APPDATA%\YT Downloader\settings.json` |
| macOS | `~/Library/Application Support/YT Downloader/settings.json` |
| Elsewhere | `$XDG_CONFIG_HOME` or `~/.config`, then `YT Downloader/settings.json` |

It's saved when a download starts, when the app closes, when a theme is chosen and when the update switch is clicked, through a temporary file so a crash can't leave half of it; keys it doesn't set are kept. A missing folder, an unknown format or theme, or a broken file falls back to the defaults. **Whole playlist** is never remembered, so it always starts off.

Sources: [youtube_downloader.py:62-94](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L62-L94), [youtube_downloader.py:242-248](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L242-L248), [youtube_downloader.py:421-438](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L421-L438), [youtube_downloader.py:1027-1034](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L1027-L1034)

## Running the tests and the lint check

```
python -m unittest discover -s tests -v
pip install -r requirements-dev.txt
ruff check .
```

The engine tests need nothing but Python. The window tests open real Qt windows on Qt's "offscreen" platform (they set `QT_QPA_PLATFORM=offscreen`), so they need PySide6 but no display, and nothing shows up on screen. Tests that need yt-dlp, Pillow or ffmpeg skip when they're missing. `tests/test_real_yt_dlp.py` runs the real yt-dlp against a local server, so it needs no internet. See [Testing](07-testing.md).

Sources: [README.md:113-131](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/README.md#L113-L131), [tests/test_real_yt_dlp.py:1-9](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_real_yt_dlp.py#L1-L9)
