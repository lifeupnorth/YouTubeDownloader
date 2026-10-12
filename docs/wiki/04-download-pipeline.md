# 4 Download Pipeline

<details>
<summary>Relevant source files</summary>

- [download_engine.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py)
- [youtube_downloader.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py)

</details>

This page follows one click of **Download** from validation to the final summary: how links become rows in the Downloads list that download one at a time, how the yt-dlp options are built, how playlists are confirmed, how progress and saved files are reported, how Cancel works, and how the result is shown. Everything between adding the rows and showing the result is in `download_engine.py` and runs without Qt. The threading it relies on (`_post`, `Row.latest`, the progress timer) is in [Architecture and Threading](03-architecture.md); format and ffmpeg choices are in [Formats and ffmpeg](05-formats-ffmpeg.md).

## End-to-end flow

```mermaid
sequenceDiagram
    participant U as User
    participant M as Window (main thread)
    participant L as Lookup threads
    participant D as Download thread
    participant J as DownloadJob
    participant Y as yt_dlp.YoutubeDL
    U->>M: click Download or press Enter
    M->>M: _start_download checks, adds a Row per link
    M->>L: _look_up(row) for each row, three at a time
    L->>M: _post(_looked_up, row, ...): title, details, thumbnail
    loop each waiting row, one at a time (_run_next)
        M->>D: _start_worker(_run_row, row, ...)
        D->>J: DownloadJob(row.url, ...).run()
        J->>Y: YoutubeDL(options), add before_dl and after_move hooks
        alt Whole playlist on
            J->>Y: download([url])
        else off
            J->>Y: extract_info(process=False), follow url results
            J->>M: ask(...) if it is a playlist
            J->>Y: process_ie_result(info, download=True)
        end
        Y-->>J: _progress_hook, _file_done, logger
        D->>M: _post(_download_finished, row, job)
    end
    M->>U: each row's state, a summary in the status line, the taskbar flashes
```

Sources: [youtube_downloader.py:651-713](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L651-L713), [download_engine.py:599-633](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L599-L633), [download_engine.py:788-809](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L788-L809), [youtube_downloader.py:752-780](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L752-L780)

## 1. Starting: `_start_download`

`_start_download` runs on the main thread (Download button or Enter in the URL box) and checks, in order:

1. yt-dlp is available (`engine.YT_DLP_AVAILABLE`), otherwise it shows the install prompt (or, while the in-app install runs, asks the user to wait).
2. ffmpeg can be found (`engine.find_ffmpeg()`, once per click), otherwise it shows `ffmpeg_help()`. The path found is kept in `_ffmpeg` and given to each download.
3. At least one link was entered (`split_links`: separated by spaces, `https://` added where it's missing, repeats dropped), otherwise "No URL". Words that aren't links are left out, and the log says which.
4. **Save to** is a full path, otherwise "Choose a folder" (a relative one would save next to wherever the app was started).

There's no check for a running download: new links wait their turn. It then saves the settings and reads the format (`_chosen_format`), the folder and **Whole playlist** here, on the main thread. For each link it adds a `Row` with those values to the list, unless the same link with the same format is already waiting or downloading, and starts the row's lookup (see [User Interface](06-user-interface.md#looking-links-up)). It clears the URL box and calls `_run_next`; if every link was already in the list, the status line says so instead.

Sources: [youtube_downloader.py:483-489](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L483-L489), [youtube_downloader.py:651-713](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L651-L713)

## 2. Several links: the Downloads list

The window does the queueing: a `DownloadJob` downloads one link, and the window runs one job at a time. `_run_next` does nothing while a row is downloading (`_running`) or the window is closing. Otherwise it takes the first row, in list order, that's waiting or still being looked up, marks it Downloading with "Starting…", sets the status line to "Downloading 2 of 3…" (counting the rows not finished yet), logs `▶ <url>`, starts the progress timer and runs `_run_row` on a worker. If no JavaScript runtime was known yet, `_run_row` looks one up with `engine.find_js_runtime()` (it may have been installed since startup). It creates one `DownloadJob` for the row, with the row's link, format, folder and **Whole playlist** setting, the ffmpeg found, and the row's own cancel event. When it ends, `_download_finished` shows the result in the row and calls `_run_next` again, so a link that fails, or whose playlist isn't confirmed, doesn't stop the others. A `_run_row` that raises (a bug, not a download problem) fails only its row. When no row is left waiting, `_queue_finished` sums up (section 8). Links can be added at any time, also while others download.

Sources: [download_engine.py:762-809](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L762-L809)

## 3. yt-dlp options: `DownloadJob.options`

| Option | Value | Why |
|---|---|---|
| `outtmpl` | `<folder>/%(playlist_title\|)s/%(playlist_index&{:03d} - \|)s%(title)s.%(ext)s` | Playlist entries go to a folder named after the playlist, with a 3-digit index that keeps same-title videos apart. For a single video both fields are empty and the path collapses to `<folder>/<title>.<ext>`. A `%` in the folder's name is doubled, so yt-dlp doesn't read it as a field. |
| `progress_hooks` | `[_progress_hook]` | Progress reporting |
| `noplaylist` | `not whole_playlist` | Only affects `watch?v=…&list=…` links: off means just that video |
| `quiet`, `no_warnings` | `True`, `False` | Nothing goes to the console; warnings still reach the logger |
| `ignoreerrors` | `"only_download"` | Like the yt-dlp CLI: skip unavailable playlist entries instead of stopping |
| `logger` | `_YdlLogger(_entry_failed, _entry_warning, _check_canceled)` | Errors are logged with ✘ and counted as skipped; warnings are logged with ⚠ (except the missing-JavaScript-runtime warning, which the window shows once at startup). Debug and info messages aren't logged, but each one runs the cancel check (section 7). |
| `color` | `never` for stdout and stderr | Keeps ANSI color codes out of error text when started from a terminal |
| format keys | from `format_opts(fmt)`: `format`, `postprocessors`, `final_ext`, and for video `format_sort` and `merge_output_format` | See [Formats and ffmpeg](05-formats-ffmpeg.md). `final_ext` is the extension after conversion; it lets yt-dlp see that a converted file is already there (section 6). |
| `ffmpeg_location` | the path `find_ffmpeg()` found | Works even when ffmpeg isn't on this process's `PATH` |
| `js_runtimes` | `{name: {"path": path}}` from `find_js_runtime()` | yt-dlp needs a JavaScript runtime for YouTube, or some formats are missing. The bundled deno in the .exe, else the first new enough deno, node or bun on `PATH`. Left out when none was found, so yt-dlp uses its own default. |

Sources: [download_engine.py:171-210](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L171-L210), [download_engine.py:295-302](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L295-L302), [download_engine.py:310-337](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L310-L337), [download_engine.py:572-597](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L572-L597), [download_engine.py:718-721](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L718-L721)

## 4. Playlist detection and confirmation

The **Whole playlist** box decides whether the app may download many videos without asking. A playlist is detected from what yt-dlp extracts, not from the URL text, so playlists, channels and channel tabs are all recognized.

- **Box ticked:** `run` calls `ydl.download([url])` directly, with no probe and no question.
- **Box unticked:** `_download_unless_declined` calls `extract_info(url, download=False, process=False)`, then `_follow_url_results` follows any `url`/`url_transparent` results to their target (at most 3 hops, so a link that keeps redirecting can't loop), and it checks the target's `_type`. If it's `playlist`, it asks *"\<title\>" is a playlist or channel. Download all its videos?* through the `ask` callback. No makes the job's outcome `"declined"`. To avoid extracting twice, it then calls `process_ie_result` on the result it already has (except for `url_transparent`, which is passed through so yt-dlp can merge its fields). If extraction returned nothing, yt-dlp has already logged the error and nothing is processed; with nothing saved, the job ends as failed.

```mermaid
flowchart TD
    start["DownloadJob.run"] --> ticked{"Whole playlist ticked?"}
    ticked -- yes --> dl["ydl.download([url])"]
    ticked -- no --> probe["extract_info(process=False)"]
    probe --> follow["_follow_url_results (at most 3 hops)"]
    follow --> isList{"_type == playlist?"}
    isList -- no --> proc["process_ie_result(info)"]
    isList -- yes --> ask["ask: download all?"]
    ask -- yes --> proc
    ask -- no --> declined["outcome: declined"]
```

A `watch?v=…&list=…` link with the box unticked resolves to a `url` result pointing to the single video, so it downloads that video without asking.

Sources: [download_engine.py:405-413](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L405-L413), [download_engine.py:610-614](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L610-L614), [download_engine.py:643-658](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L643-L658)

## 5. Progress: streams and playlist entries

Formats that merge video and audio download two streams, and yt-dlp reports each from 0 to 100%. To show one bar that only moves forward:

- `_video_starting`, a `before_dl` post-processor that runs once per video, resets the stream index and sets the stream count from `len(info["requested_formats"])` (the progress hook's own `info_dict` doesn't include that list). It also checks for a cancel and starts a new list of the video's files (section 7).
- `_progress_hook` clamps each stream's percentage to 100, combines the streams as `(stream_index × 100 + pct) / streams` (the index capped at the last stream), and scales the result by 0.99. After the last stream it reports `99, "Processing…"` while ffmpeg merges or converts, which shows the row as Processing…; the row's bar only reaches 100% once `_download_finished` marks it Completed.
- The message shows speed and time left as m:ss (h:mm:ss past an hour, `format_eta`), and in a playlist starts with `Video {playlist_index}/{n_entries} · `. The row leaves out the "Downloading… 42.0%" part, since its bar and percentage show it, so it reads e.g. "Video 2/5 · 3.4 MB/s · ETA 0:07".

Sources: [download_engine.py:213-217](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L213-L217), [download_engine.py:660-696](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L660-L696)

## 6. Saved, already downloaded and skipped

The progress hook only sees temporary per-stream files, which are deleted after merging, so it isn't used to report results. Instead:

- `_file_done` runs as an **after_move** post-processor, once per video with the final path. If yt-dlp's `__real_download` flag is set it logs `✔ Saved: <path>` and counts it as saved; otherwise the file already existed, and it logs `• Already downloaded: <path>`. Because the options include `final_ext`, this also works for converted files: a second MP3 download of the same video finds the MP3 instead of downloading and converting again.
- `_YdlLogger.error` → `_entry_failed` strips the `ERROR: ` prefix, adds the message to `skipped` and logs `✘ <message>`. yt-dlp also sends its "Deprecated Feature" notices to `error`; the logger drops those. After a cancel, `_entry_failed` ignores errors (such as the stopped ffmpeg's).

Sources: [download_engine.py:325-331](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L325-L331), [download_engine.py:698-716](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L698-L716)

## 7. Canceling

Each row that isn't finished has a Cancel icon (`_cancel_row`). It sets the row's `cancel_event`, which is also its job's cancel event. A row that's still waiting is just marked Canceled; `_run_next` skips it, and its lookup stops. For the running row, it also calls `row.job.stop_processes()`, which stops the job's ffmpeg, and the row shows "Canceling…" until the job ends. A job checks the event in `_check_canceled` and raises yt-dlp's `DownloadCancelled`, which stops yt-dlp even with `ignoreerrors` set. It checks at every progress update, before each video starts, and at each of yt-dlp's status messages (the `check` that `_YdlLogger.debug` and `info` run), so a download stops almost at once, a playlist stops between two videos, and reading a big channel's pages stops at the next page. Canceling one row doesn't affect the others: the next waiting row starts as usual.

A running ffmpeg conversion or merge is stopped too. `run` calls `_track_processes`, which swaps the `Popen` that yt-dlp's ffmpeg post-processors and external downloaders use for `_tracked_popen_class`. Each process it starts reports itself to the job running on that thread (`_current.job`, via `_process_started`), together with the file it is about to create: `_new_ffmpeg_output` takes the last argument, `file:<path>`, but only for ffmpeg writing a file that isn't on disk yet (not ffprobe, not a run that only reads a file). `stop_processes` notes those files and kills the processes; one that starts just after a cancel is stopped at once. yt-dlp treats the killed ffmpeg as a failed post-processing step and only logs it, so `run` checks for a cancel after a normal return as well, and the job still ends as "canceled".

yt-dlp leaves the unfinished video's `.part` file behind, plus any of its streams that finished but weren't merged. The progress hook records the files this run writes for the current video, and after a cancel `_remove_partial_files` deletes them, the files the stopped ffmpeg was creating, and their `.ytdl` and `-Frag*` files. Files that were already on disk never get a "downloading" event and are never an ffmpeg's new output, so they're never removed, and `_file_done` clears the list once a video is complete, so finished videos are kept.

Link lookups stop the same way: `preview_link` gives its `_YdlLogger` a `check` that raises `DownloadCancelled` once the row's cancel event is set (its Cancel was clicked, or the window is closing), and it stops the thumbnail download between chunks.

Sources: [download_engine.py:356-403](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L356-L403), [download_engine.py:447-503](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L447-L503), [download_engine.py:543-566](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L543-L566), [download_engine.py:599-641](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L599-L641), [download_engine.py:669-674](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L669-L674), [download_engine.py:723-738](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L723-L738), [download_engine.py:779-782](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L779-L782), [youtube_downloader.py:737-745](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L737-L745)

## 8. Finishing: the summary

Each job ends with an outcome (`done`, `failed`, `declined` or `canceled`), counts and a `summary()`. A job where nothing was saved but entries failed (e.g. a single private video) counts as `failed`, with the reason already in the log. Before it runs, or if it crashed, the outcome is `None`.

| Situation | Summary |
|---|---|
| One video, no problems | `Download complete!` |
| Nothing saved and nothing skipped (e.g. an empty playlist) | `Nothing to download: the link has no videos.` |
| Several videos or problems | `Done: 3 saved, 1 already downloaded, 2 skipped` (only the parts that apply) |
| Failed | `Error: <reason>` |
| Playlist declined | `Playlist not downloaded.` |
| Canceled | `Download canceled (1 saved, 2 partial files removed).` |

`_download_finished` turns the job's result into the row's state, and remembers `job.last_file` in the row, which shows the Show in folder icon:

| Outcome | Row | Log |
|---|---|---|
| `done` | Completed. If the summary isn't `Download complete!` (a playlist, say), it replaces the row's details line | `✔ Done!`, or `✔ <summary>` |
| `done`, but nothing to download (e.g. an empty playlist) | Nothing found (amber), with the summary under the progress bar | `• Nothing to download: the link has no videos.` |
| `failed` | Failed, with `job.error` under the progress bar | `✘ <summary>`, unless the reason is already there (`error_logged`) |
| `declined` | Not downloaded | `✘ Playlist not downloaded.` |
| `canceled` | Canceled | `✘ Download canceled (…)` |
| `None` (`_run_row` crashed) | Failed, with "An unexpected error (see the log)", or Canceled if the row was canceled | The error, from `_worker_crashed` |

When no row is left waiting, `_queue_finished` sums up the finished rows still in the list in the status line (`queue_summary`), and flashes the taskbar button (`QApplication.alert`) if the window isn't in front. There's no dialog for success or failure. The summary counts each outcome, in the order completed, failed, canceled, not downloaded, empty, leaving out those with none:

| Finished rows | Status line |
|---|---|
| All Completed | `✔ All downloads complete.` (green) |
| Some Completed, none Failed | e.g. `✔ Done: 2 completed, 1 canceled, 1 not downloaded.` (green) |
| Some Completed, some Failed | e.g. `✔ Done: 2 completed, 1 failed.` (orange) |
| Some Failed, none Completed | e.g. `✘ Nothing downloaded: 1 failed, 1 canceled.` (red) |
| Only Canceled, Not downloaded or Nothing found | e.g. `Nothing downloaded: 1 canceled.` (gray) |

Sources: [download_engine.py:619-633](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L619-L633), [download_engine.py:740-759](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L740-L759), [download_engine.py:811-897](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L811-L897), [youtube_downloader.py:705-713](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L705-L713), [youtube_downloader.py:752-786](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L752-L786)

## Limitations

- Only one download runs at a time (`_running`); up to three lookups run alongside it.
- Closing the window during a download asks first; Yes cancels it, the waiting rows and the lookups, and the window closes once the download has stopped and its partial files are removed. Closing again while it stops, or a crash, leaves the partial file.
- An ffmpeg that is a `.bat`/`.cmd` wrapper runs under `cmd.exe /C`, so Cancel stops only `cmd.exe`.
- A row's output folder is what **Save to** said when the link was added (default `~/Downloads`); yt-dlp creates missing folders.
- The Downloads list isn't saved: it starts empty each time the app starts.

Sources: [youtube_downloader.py:183](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L183), [youtube_downloader.py:616-643](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L616-L643), [download_engine.py:554-560](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L554-L560)
