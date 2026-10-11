# 8 Glossary

Terms used in the code and in this wiki. For the overall picture, see [Overview](01-overview.md).

| Term | Meaning |
|---|---|
| **yt-dlp** | The Python library that extracts video information and downloads streams. The app imports it as `yt_dlp` and drives it through `YoutubeDL`. |
| **ffmpeg** | External command-line tool yt-dlp uses to merge, remux and convert files. Located by `find_ffmpeg` and passed as `ffmpeg_location`. |
| **Stream** | One downloaded part of a video. "Best MP4" and the resolution choices download separate video and audio streams (`requested_formats`) and merge them. |
| **Merge / remux** | Combining video and audio into one file (`merge_output_format`), or changing the container without re-encoding (`FFmpegVideoRemuxer`). |
| **Format sorting** | yt-dlp's `format_sort` (e.g. `res:720`, `ext:mp4:m4a`): ranks formats by preference instead of filtering them out. |
| **`res:N`** | A sort key limiting the *shorter* side of the video to N, matching YouTube's "720p"-style labels. |
| **Post-processor (PP)** | A yt-dlp hook class with `run(info)`, attached to a stage. The app adds `StreamCounter` (`before_dl`) and `CallbackPP` (`after_move`). |
| **`before_dl` / `after_move`** | yt-dlp stages: before a video's streams download, and after the final file is in place. |
| **`__real_download`** | yt-dlp flag in the info dict: `True` when this run actually downloaded the file, `False` when it already existed. |
| **Probe** | `extract_info(url, download=False, process=False)` in `_download_unless_declined`: finds out what a link points to before deciding whether to ask. |
| **`url` / `url_transparent` / `playlist`** | `_type` values in yt-dlp results: a pointer to another page, a pointer whose fields get merged, or a list of entries (playlist, channel or tab). |
| **Whole playlist** | The checkbox (`playlist_var`). Ticked: download every video from a `watch?v=…&list=…` link without asking. Unticked: only that video, and ask before downloading any playlist or channel. |
| **`ignoreerrors="only_download"`** | yt-dlp option that turns download errors into logged messages instead of exceptions, so one unavailable playlist entry doesn't stop the rest. |
| **Main thread / worker** | The Tk thread that owns all widgets, and the daemon threads that run downloads and installs. |
| **`_post`** | Queues a callable for the main thread. The only way workers trigger UI changes besides progress. |
| **Coalesced progress** | `_latest_progress`: a single slot the worker overwrites; the main thread shows only its newest value every 50 ms. |
| **Saved / already downloaded / skipped** | The three per-file outcomes counted in `_saved`, `_already` and `_skipped`, and reported in the final "Done: …" summary. |

Sources: [youtube_downloader.py:84-166](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/youtube_downloader.py#L84-L166), [youtube_downloader.py:401-434](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/youtube_downloader.py#L401-L434), [youtube_downloader.py:449-519](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/youtube_downloader.py#L449-L519), [youtube_downloader.py:586-605](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/youtube_downloader.py#L586-L605)
