# YouTubeDownloader
A small app to download YouTube videos written in Python.

## Requirements

- **Python 3.10 or newer**, with Tkinter. The python.org installers for Windows and macOS include it; on Linux you may need a separate package, e.g. `sudo apt install python3-tk`.
- **yt-dlp**, pinned to a tested version in `requirements.txt`.
- **ffmpeg**, used to merge video and audio and to convert to MP3/M4A.

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

If yt-dlp isn't installed, the app offers to install it for you, then asks you
to restart it.

## Use

1. Paste a link into **YouTube URL**, or click **Paste**.
2. Pick a **Format**: Best MP4, 1080p, 720p, 480p, MP3 or M4A.
3. Choose where to save under **Save To** (default: your Downloads folder).
4. Click **Download**, or press Enter. Progress and saved files show in the status line and the log.

A link to a video opened from a playlist downloads just that video. Tick
**Whole playlist** to download the whole list instead. For a playlist or
channel link, the app asks before downloading all its videos, and saves them
to a folder named after the playlist.

While a download runs, the button turns into **Cancel**. Canceling removes the
partial file of the video in progress and keeps the ones that finished.

## Run the tests

```
python -m unittest discover -s tests -v
```

The tests also run on GitHub Actions for every push to `main` and every pull
request. The [wiki](docs/wiki/README.md) explains how the app works.

## Disclaimer

This app is a personal tool and isn't affiliated with or endorsed by YouTube or Google. Downloading videos may go against [YouTube's Terms of Service](https://www.youtube.com/t/terms) and copyright law. You're responsible for only downloading content you have the right to, such as your own uploads or videos whose license allows it.

## License

MIT. See [LICENSE](LICENSE).
