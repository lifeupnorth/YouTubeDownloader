# YouTubeDownloader
A small app to download YouTube videos written in Python.

## Requirements

- **Python 3.10 or newer**, with Tkinter. The python.org installers for Windows and macOS include it; on Linux you may need a separate package, e.g. `sudo apt install python3-tk`.
- **yt-dlp**, pinned to a tested version in `requirements.txt`.
- **Pillow** (optional, also in `requirements.txt`), for thumbnails in the link preview.
- **ffmpeg**, used to merge video and audio and to convert to MP3/M4A.
- **A JavaScript runtime** for YouTube: deno (yt-dlp's default; `winget install DenoLand.Deno`), node or bun, plus **yt-dlp-ejs** (in `requirements.txt`), the challenge solver it runs. Without them some formats may be missing. The .exe includes both.

## Install

1. Install the Python dependency:
   ```
   pip install -r requirements.txt
   ```
2. Install ffmpeg:
   - Windows: press WIN+R, type `winget install ffmpeg`, and wait for it to finish.
   - macOS: `brew install ffmpeg`
   - Linux: your package manager, e.g. `sudo apt install ffmpeg`

The app checks for ffmpeg at startup and before each download. On Windows it
also finds an install made while the app is open.

## Run

From the project folder:
```
python youtube_downloader.py
```
On Windows, `pythonw youtube_downloader.py` starts it without a console window.

If yt-dlp isn't installed, the app offers to install it for you, and you can
start downloading right after. The header shows which yt-dlp version is in use.

## Windows app (one file)

`YT-Downloader.exe` is the whole app in one file: Python, yt-dlp, Pillow and
ffmpeg are built in, so there's nothing else to install. Each
[release](https://github.com/lifeupnorth/YouTubeDownloader/releases) will have
it attached, and the "Windows app" GitHub Actions workflow builds it on demand.
A new yt-dlp version (merged into `requirements.txt`, usually by Dependabot) is
released automatically as the next patch version. At startup the .exe asks
GitHub (api.github.com) for the latest release, at most once a day, and logs a
line when a newer one is out; untick **Check for updates at startup** in its
header to stop it. The request carries nothing about you or your downloads.
Running from source never checks. v1.0.0 can't tell it's out of date: it was
built before the app knew its own version. The .exe starts a little
slower than the script, because it unpacks itself first, and some antivirus
programs are wary of this kind of .exe.

To build it yourself on Windows:
```
pip install -r requirements.txt -r packaging/requirements-build.txt
python packaging/build_exe.py
python packaging/build_exe.py --check
```
This writes `dist/YT-Downloader.exe`. The build downloads ffmpeg (gyan.dev's
GPL "essentials" build) and checks it against a pinned checksum; its license and
the other third-party notices are in `packaging/THIRD-PARTY-NOTICES.txt` and go
next to the .exe.

The .exe is unsigned unless you add a code-signing certificate, so Windows
shows its publisher as unknown. Signing names you as the publisher; SmartScreen
may still warn until the certificate (or the file) has built up reputation. To
sign it in the workflow, create an environment named `code-signing` (Settings →
Environments), limit its deployment branches to `main` and `v*` tags, and add two
secrets to it: `WINDOWS_SIGNING_CERT` (the certificate's `.pfx` file,
base64-encoded; in PowerShell:
`[Convert]::ToBase64String([IO.File]::ReadAllBytes('cert.pfx'))`) and
`WINDOWS_SIGNING_PASSWORD`. Pull requests are never signed. A timestamp server
other than DigiCert's can be set in the `SIGNING_TIMESTAMP_URL` variable. This
needs a certificate you can export as a `.pfx`; publicly trusted code-signing
certificates issued since June 2023 keep the key on a hardware token or a cloud
service (Azure Trusted Signing, SSL.com eSigner, DigiCert KeyLocker), which need
that provider's own signing step instead. Locally, set the
same environment variables and run `python packaging/sign_exe.py dist/YT-Downloader.exe`
(it needs `signtool.exe` from the Windows SDK).

## Use

1. Paste a link into **YouTube URLs**, or click **Paste**. A preview with the
   thumbnail, title and length appears under it.
2. Pick a **Format**: Best MP4, 1080p, 720p, 480p, MP3 or M4A.
3. Choose where to save under **Save To** (default: your Downloads folder).
4. Click **Download**, or press Enter. Progress and saved files show in the status line and the log.
5. When it's done, click **Open folder** to see the file.

The app remembers the folder and format you used last time.

To download several videos in a row, put one link per line (Shift+Enter adds
a line, and pasting several links puts each on its own line). If one link
fails, the others still download, and the summary says how many worked.

A link to a video opened from a playlist downloads just that video. Tick
**Whole playlist** to download the whole list instead. For a playlist or
channel link, the app asks before downloading all its videos, and saves them
to a folder named after the playlist.

While a download runs, the button turns into **Cancel**. Canceling stops within
moments in most cases, also while ffmpeg converts or merges a video or while a big playlist is
being read. It removes the partial file of the video in progress, keeps the
ones that finished, and skips any links still waiting.

## Run the tests

```
python -m unittest discover -s tests -v
```

A nightly workflow (`.github/workflows/smoke.yml`) also downloads one short
video for real, from source and with the .exe. Run it locally with
`python packaging/smoke_test.py` (add `--exe dist/YT-Downloader.exe` for the
.exe).

Check the code style (the rules are in `ruff.toml`):
```
pip install -r requirements-dev.txt
ruff check .
```

The tests and the style check also run on GitHub Actions for every push to
`main` and every pull request. The [wiki](docs/wiki/README.md) explains how the app works.

## Disclaimer

This app is a personal tool and isn't affiliated with or endorsed by YouTube or Google. Downloading videos may go against [YouTube's Terms of Service](https://www.youtube.com/t/terms) and copyright law. You're responsible for only downloading content you have the right to, such as your own uploads or videos whose license allows it.

## License

MIT. See [LICENSE](LICENSE).
