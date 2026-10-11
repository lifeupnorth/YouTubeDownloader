# 8 Glossary

Terms used in the code and in this wiki. For the overall picture, see [Overview](01-overview.md).

| Term | Meaning |
|---|---|
| **yt-dlp** | The Python library that extracts video information and downloads streams. The engine imports it as `yt_dlp` and drives it through `YoutubeDL`; it's pinned in `requirements.txt` and can be installed from inside the app. |
| **ffmpeg** | External command-line tool yt-dlp uses to merge, remux and convert files. Located by `find_ffmpeg` and passed as `ffmpeg_location`. |
| **Engine** | `download_engine.py`: everything about downloading, without Tk. The window (`youtube_downloader.py`) uses it through callbacks. |
| **DownloadJob** | Downloads one link (a video, playlist or channel) and keeps the result: outcome, counts, error, last file, summary. |
| **DownloadQueue** | Runs one `DownloadJob` per link, in order; a failed link doesn't stop the rest, Cancel skips the remaining ones. One link is a queue of one. |
| **Outcome** | How a job or queue ended: `done`, `failed`, `declined` (a playlist wasn't confirmed) or `canceled`. |
| **Stream** | One downloaded part of a video. "Best MP4" and the resolution choices download separate video and audio streams (`requested_formats`) and merge them. |
| **Merge / remux** | Combining video and audio into one file (`merge_output_format`), or changing the container without re-encoding (`FFmpegVideoRemuxer`). |
| **Format sorting** | yt-dlp's `format_sort` (e.g. `res:720`, `ext:mp4:m4a`): ranks formats by preference instead of filtering them out. |
| **`res:N`** | A sort key limiting the *shorter* side of the video to N, matching YouTube's "720p"-style labels. |
| **Post-processor (PP)** | A yt-dlp hook with `run(info)`, attached to a stage. A job adds two `CallbackPP`s: `_video_starting` (`before_dl`) and `_file_done` (`after_move`). |
| **`before_dl` / `after_move`** | yt-dlp stages: before a video's streams download, and after its final file is in place. |
| **`__real_download`** | yt-dlp flag in the info dict: `True` when this run actually downloaded the file, `False` when it already existed. |
| **Probe** | `extract_info(url, download=False, process=False)`: finds out what a link points to without processing it, used before asking about a playlist and for previews. |
| **`url` / `url_transparent` / `playlist`** | `_type` values in yt-dlp results: a pointer to another page, a pointer whose fields get merged, or a list of entries (playlist, channel or tab). |
| **Whole playlist** | The checkbox (`playlist_var`). Ticked: download every video from a `watch?v=…&list=…` link without asking. Unticked: only that video, and ask before downloading any playlist or channel. |
| **`ignoreerrors="only_download"`** | yt-dlp option that turns download errors into logged messages instead of exceptions, so one unavailable playlist entry doesn't stop the rest. |
| **Cancel** | Sets an event that the jobs check (`_check_canceled`), which raises yt-dlp's `DownloadCancelled`; the unfinished video's files are then removed. |
| **Preview** | The thumbnail, title and length shown under the URL box, looked up without downloading (`preview_link`). |
| **Main thread / worker** | The Tk thread that owns all widgets, and the daemon threads that run downloads, previews and installs. |
| **`_post`** | Queues a callable for the main thread; the only way workers trigger UI changes besides progress. |
| **Coalesced progress** | `_latest_progress`: one slot the worker overwrites; the main thread shows only its newest value every 50 ms. |
| **Settings file** | JSON with the last folder and format (`settings_path`, `load_settings`, `save_settings`). |
| **Pin** | An exact version in `requirements.txt` (`yt-dlp[default]==…`); the in-app installer uses the same one, and Dependabot proposes updates. |

Sources: [download_engine.py:1-7](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/download_engine.py#L1-L7), [download_engine.py:102-133](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/download_engine.py#L102-L133), [download_engine.py:174-221](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/download_engine.py#L174-L221), [download_engine.py:295-630](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/download_engine.py#L295-L630), [youtube_downloader.py:60-89](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/youtube_downloader.py#L60-L89), [youtube_downloader.py:457-490](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/youtube_downloader.py#L457-L490)
