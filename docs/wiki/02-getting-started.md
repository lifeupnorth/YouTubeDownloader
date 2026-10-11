# 2 Getting Started

<details>
<summary>Relevant source files</summary>

- [README.md](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/README.md)
- [youtube_downloader.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/youtube_downloader.py)

</details>

How to install, run and test the app. There is no `pyproject.toml` or installer: the app is a single script, one pinned Python dependency in `requirements.txt`, and ffmpeg. For what happens once it's running, see [Overview](01-overview.md).

## Requirements

| Requirement | Why | How it's handled |
|---|---|---|
| Python 3 with Tkinter | The UI is `tkinter`/`ttk` | Bundled with python.org installers on Windows and macOS; some Linux distros need a separate `python3-tk` package |
| `yt-dlp` | Does the actual extraction and downloading | Pinned to an exact version in `requirements.txt`. Optional at import time: if it's missing, the app offers to install that same version with pip and then asks for a restart. |
| `ffmpeg` | Merging video and audio, remuxing to MP4, converting to MP3/M4A | Checked at startup and before every download; on Windows it's also found after an install made while the app is open |

`str.removeprefix` is used in `_entry_failed`, so Python 3.9 or newer is needed.

Sources: [README.md:1-11](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/README.md#L1-L11), [youtube_downloader.py:1-22](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/youtube_downloader.py#L1-L22), [youtube_downloader.py:516-517](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/youtube_downloader.py#L516-L517)

## Install and run

```
pip install -r requirements.txt
winget install ffmpeg          (Windows; macOS: brew install ffmpeg)
python youtube_downloader.py
```

The script's entry point creates `YouTubeDownloader()` and runs Tk's `mainloop()`. On Windows, starting it with `pythonw` instead avoids the console window. When started from a terminal, yt-dlp warnings are printed to stderr there (`_YdlLogger.warning`); everything else appears in the app's log.

Sources: [youtube_downloader.py:118-130](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/youtube_downloader.py#L118-L130), [youtube_downloader.py:698-701](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/youtube_downloader.py#L698-L701)

## Using it

1. Paste a link into **YouTube URL** (or click **Paste**).
2. Pick a **Format**. Tick **Whole playlist** only if you want every video from a link like `watch?v=…&list=…`. With the box unticked, a playlist, channel or tab link asks before downloading all its videos.
3. Choose the folder under **Save To** (defaults to `~/Downloads`).
4. Click **Download** or press Enter. Progress, saved files and errors appear in the status line and the log.

Playlist videos are saved to `<Save To>/<playlist title>/<NNN> - <title>.<ext>`, single videos to `<Save To>/<title>.<ext>`.

Sources: [youtube_downloader.py:229-311](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/youtube_downloader.py#L229-L311), [youtube_downloader.py:449-469](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/youtube_downloader.py#L449-L469)

## Configuration

There are no settings files, environment variables or command-line options. Everything is chosen in the window each time, and nothing is remembered between runs. The only tunables are constants at the top of the script: the color palette, `VIDEO_MAX_RES`, and the window size in `__init__`.

Sources: [youtube_downloader.py:25-41](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/youtube_downloader.py#L25-L41), [youtube_downloader.py:172-183](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/youtube_downloader.py#L172-L183)

## Running the tests

```
python -m unittest discover -s tests -v
```

The tests need a display (they open real Tk windows) but no network. Tests that depend on yt-dlp skip when it isn't installed. See [Testing](07-testing.md).

Sources: [README.md:13-16](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/README.md#L13-L16)
