# YouTubeDownloader
A small app to download YouTube videos written in Python.

## Requirements

- **Python 3.10 or newer**.
- **PySide6** (Qt for Python), for the window: `PySide6-Essentials`, pinned in `requirements.txt`.
- **yt-dlp** with its recommended extras (`yt-dlp[default]`), pinned to a tested version in `requirements.txt`. The extras include **yt-dlp-ejs**, the solver for YouTube's JavaScript challenges.
- **Pillow** (optional, also in `requirements.txt`), for thumbnails in the Downloads list. Without it the list shows titles only.
- **ffmpeg**, used to merge video and audio and to convert to MP3/M4A.
- **A JavaScript runtime** for YouTube: deno (yt-dlp's default; `winget install DenoLand.Deno`), node or bun, which runs yt-dlp-ejs. Without it some formats may be missing. The .exe includes deno.

## Install

1. Install the Python dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Install ffmpeg:
   - Windows: press WIN+R, type `winget install ffmpeg`, and wait for it to finish.
   - macOS: `brew install ffmpeg`
   - Linux: your package manager, e.g. `sudo apt install ffmpeg`
3. Install deno, the JavaScript runtime yt-dlp uses for YouTube:
   - Windows: press WIN+R, type `winget install DenoLand.Deno`, and wait for it to finish.
   - macOS: `brew install deno`
   - Linux: `curl -fsSL https://deno.land/install.sh | sh`

   Node or bun work too, if you already have one of them.

The app checks for ffmpeg at startup and before each download. On Windows it
also finds an install made while the app is open. Without a JavaScript runtime
it says so in its log at startup, and picks one up on the next download once
it's installed.

## Run

From the project folder:
```
python youtube_downloader.py
```
On Windows, `pythonw youtube_downloader.py` starts it without a console window.

If yt-dlp isn't installed, the app offers to install it for you, and you can
start downloading right after. The header shows which yt-dlp version is in use.

## Windows app (one file)

`YT-Downloader.exe` is the whole app in one file: Python, Qt, yt-dlp, Pillow,
ffmpeg and deno are built in, so there's nothing else to install. Each
[release](https://github.com/lifeupnorth/YouTubeDownloader/releases) will have
it attached, and the "Windows app" GitHub Actions workflow builds it for every
pull request and on demand. A new yt-dlp version (merged into
`requirements.txt`, usually by Dependabot) is released automatically as the
next patch version, once the repository owner approves the release build.
Each release lists the .exe's SHA-256 (and `SHA256SUMS.txt` for all its files),
and you can check that a downloaded .exe was built by this repository's
workflow with `gh attestation verify YT-Downloader.exe --repo
lifeupnorth/YouTubeDownloader`. At startup the .exe asks
GitHub (api.github.com) for the latest release, at most once a day, and logs a
line when a newer one is out; untick **Check for updates at startup** in its
header to stop it. The request carries nothing about you or your downloads.
Running from source never checks. v1.0.0 can't tell it's out of date: it was
built before the app knew its own version. The .exe starts a little
slower than the script, because it unpacks itself first, and some antivirus
programs are wary of this kind of .exe.

To build it yourself on Windows:
```
pip install -r requirements.txt
pip install --require-hashes -r packaging/requirements-build.txt
python packaging/build_exe.py
python packaging/build_exe.py --check
```
This writes `dist/YT-Downloader.exe`. The build downloads ffmpeg (gyan.dev's
GPL "essentials" build) and deno, and checks them against pinned checksums (a
weekly workflow opens an issue when either has a newer release); its license and
the other third-party notices are in `packaging/THIRD-PARTY-NOTICES.txt`. The
build also writes `THIRD-PARTY-LICENSES.txt`, the full license texts of
everything else it bundles (Python and the libraries built into it, Qt and
PySide6 under the LGPL v3, and each Python package); all of them go into the
.exe, next to it and into each release.

Releases are signed through SignPath Foundation, which signs open-source
projects for free (see [Code signing policy](#code-signing-policy)). Until the
project's SignPath setup is complete, the .exe is unsigned and Windows shows
its publisher as unknown; signed, it names SignPath Foundation. SmartScreen may
still warn about a new release until it has built up reputation. A release
build uploads the .exe to SignPath, which checks that this repository's
workflow built it and waits for an approver; the signed .exe's signature is
verified, and `--check` runs it before it's released. To turn it on, add to the
`code-signing` environment (Settings → Environments) the secret
`SIGNPATH_API_TOKEN` and the variable `SIGNPATH_ORGANIZATION_ID`, and, if the
SignPath project and policy aren't named `YouTubeDownloader` and
`release-signing`, the variables `SIGNPATH_PROJECT_SLUG` and
`SIGNPATH_SIGNING_POLICY_SLUG`. Pull requests and other builds are never signed.

## Use

1. Paste a link into **YouTube URLs**, or click **Paste**. For several videos,
   put several links in the box, separated by spaces. **Paste** adds to the
   links already there and keeps only the links from copied text; `https://`
   can be left out (`youtu.be/...` works). You can also drag links onto the
   window, e.g. from your browser's address bar: they download right away,
   with the Format and Quality chosen. Drag a folder onto it to save there.
2. Pick a **Format**: MP4 video, or MP3 or M4A (audio only). For video, pick a
   **Quality**: Best quality, 2160p (4K), 1440p, 1080p, 720p or 480p. If a
   video doesn't have that quality, you get the closest one below it.
3. Choose where to save under **Save to** (default: your Downloads folder). If
   the last folder used isn't there (e.g. on an unplugged drive), the app saves
   to Downloads for now but remembers it.
4. Click **Download**, or press Enter. Each link becomes a row in the
   **Downloads** list, with its thumbnail, title and length, its own progress
   bar and its status. The rows download one at a time, and you can add more
   links while they do. If one link fails, the others still download, and the
   row says why.
5. Each row has icons on the right: **Cancel** while it waits or downloads,
   then **Show in folder** and **Remove from the list** (which never deletes
   the file). **Clear finished** removes all finished rows.
6. The same actions work from the keyboard on the selected row (arrow keys or
   a click select one): **Esc** cancels, **Enter** shows the file in its folder,
   and **Delete** removes the row. Right-click a row for a menu with all
   three, or double-click it to show its file.

The status line at the bottom sums up when the list is done. Click **Log** next
to it to see the details; it opens by itself when there's something to read,
e.g. when ffmpeg is missing.

The app remembers the folder, format and theme you used last time. It keeps
the Downloads list too (the last 200 rows, with their thumbnails), so your
downloads are still listed the next time you start it. Rows that hadn't
finished when it closed come back as Canceled. **Remove from the list** and
**Clear finished** take rows out of the saved list as well.

**Theme** (top right) switches between a light and a dark look, both in
YouTube's colors. **System**, the default, follows your system's light or dark
setting and changes along with it.

A link to a video opened from a playlist downloads just that video. Switch on
**Whole playlist** to download the whole list instead. For a playlist or
channel link, the app asks before downloading all its videos, and saves them
to a folder named after the playlist.

Canceling stops within moments in most cases, also while ffmpeg converts or
merges a video or while a big playlist is being read. It removes the partial
file of the video in progress and keeps the ones that finished; the other
links still download.

## Run the tests

From the project folder, after `pip install -r requirements.txt` (without
yt-dlp the tests that need it are skipped):
```
python -m unittest discover -s tests -v
```
The window tests open real Qt windows on Qt's "offscreen" platform, so they
need no display and nothing shows up on screen. The real yt-dlp tests download from a local server, so they need no internet;
the MP3 one needs ffmpeg and is skipped without it.

Check the code style (the rules are in `ruff.toml`):
```
pip install -r requirements-dev.txt
ruff check .
```

The tests and the style check also run on GitHub Actions for every push to
`main` and every pull request. The [wiki](docs/wiki/README.md) explains how the app works.

## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io/),
certificate by [SignPath Foundation](https://signpath.org/).

Only release builds are signed: the .exe built by this repository's "Windows
app" workflow on GitHub-hosted runners, from `main` or a version tag. Each
signing request is approved by hand.

Team roles:
- Committers and reviewers: [lifeupnorth](https://github.com/lifeupnorth), the
  repository's owner. Changes from anyone else, including bots such as
  Dependabot, are reviewed by the owner before they're merged.
- Approvers: [lifeupnorth](https://github.com/lifeupnorth).

### Privacy policy

This program doesn't send information to other networked systems unless you
ask it to, with one exception, the update check:
- Downloads and link lookups (title and thumbnail) contact YouTube (or the site of a link you
  enter), through yt-dlp, only for the links you enter.
- The release .exe asks GitHub (api.github.com) for the latest release at most
  once a day. The request carries nothing about you or your downloads beyond
  what any web request does (your IP address, and "YT-Downloader" as the user
  agent). Untick **Check for updates at startup** in the app's header to turn
  it off. Running from source never checks.
- Run from source without yt-dlp, the app can install it with pip (from PyPI)
  if you agree.

The settings and the Downloads list are saved on your computer only. The
bundled ffmpeg and deno run on your computer only: ffmpeg converts and
merges files, and deno runs yt-dlp's solver for YouTube's JavaScript challenges.

## Disclaimer

This app is a personal tool and isn't affiliated with or endorsed by YouTube or Google. Downloading videos may go against [YouTube's Terms of Service](https://www.youtube.com/t/terms) and copyright law. You're responsible for only downloading content you have the right to, such as your own uploads or videos whose license allows it.

## License

MIT. See [LICENSE](LICENSE).
