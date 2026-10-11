# 7 Testing

<details>
<summary>Relevant source files</summary>

- [tests/test_thread_safety.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/tests/test_thread_safety.py)
- [tests/test_formats.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/tests/test_formats.py)
- [README.md](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/README.md)

</details>

The tests use the standard library `unittest`, with no extra dependencies. They're split into three files: one drives a real Tk window through simulated downloads, one covers format selection and ffmpeg detection, and `test_requirements.py` checks that the in-app installer uses the exact yt-dlp pin from `requirements.txt` and that Dependabot watches that file. Nothing touches the network. GitHub Actions (`.github/workflows/tests.yml`) runs them on every push to `main` and every pull request, on Windows and Ubuntu (under a virtual display, `xvfb-run`) with Python 3.10 and 3.14. Each job runs the suite twice: once normally, and once with yt-dlp blocked, so the tests that need it must skip rather than fail.

```
python -m unittest discover -s tests -v
```

Sources: [README.md:13-16](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/README.md#L13-L16)

## Thread-safety tests (`test_thread_safety.py`)

These tests check the app's central rule: **worker threads never call Tk**. `setUp` wraps the Tk methods listed in `GUARDED` (`Variable.get/set`, `Misc.configure/config/after/after_cancel`, `Text.insert/see`) with a function that records any call made from a non-main thread. Each test then asserts that `self.violations == []`.

Downloads use **`make_fake_ydl`**, a stand-in for `yt_dlp.YoutubeDL`. It drives the real progress hooks and the app's post-processors (`before_dl`, `after_move`), and its parameters create specific scenarios:

| Parameter | Simulates |
|---|---|
| `streams` | Number of streams per video (2 = merged video and audio) |
| `videos` | A playlist with `playlist_index`/`n_entries` |
| `overshoot` | `downloaded_bytes` exceeding `total_bytes_estimate` |
| `fail` | Entries that call `logger.error` (and raise unless `ignoreerrors` is set) |
| `already` | Entries reported with `__real_download=False` |
| `error` | An exception after the run |
| `probe` | The `_type` returned by each `extract_info(process=False)` call |
| `files_dir` | Writes real stream files there (`.part` while downloading, then renamed, then "merged" into `v<N>.mp4`) |
| `on_progress` | Callback on the download thread before each progress event; the cancel tests use it to click Cancel at an exact point |

`run_download` replaces the `yt_dlp` module with a namespace containing the fake (plus `PostProcessor=object` and `FakeCancelled` as `utils.DownloadCancelled`), schedules `_start_download`, and runs `mainloop()` with a 10-second safety timeout. The patched dialogs call `quit()` to end the loop.

Sources: [tests/test_thread_safety.py:18-96](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/tests/test_thread_safety.py#L18-L96), [tests/test_thread_safety.py:99-193](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/tests/test_thread_safety.py#L99-L193)

### What is covered

| Area | Tests |
|---|---|
| Single download | Progress never goes backwards, stays ≤ 99 until done, then reaches 100; polling stops when idle; the log shows the final file, not the temporary streams |
| Errors | Failure dialog; a single-video error is logged once; closing the window while the Done dialog is open |
| Playlists | Whole-playlist run with one "Saved" per video; "Video n/N" status; an unavailable entry is skipped; nothing saved means failure; "Already downloaded" is counted separately |
| Closing during a download | Asks first; Yes cancels, removes the partial files and closes; No keeps downloading |
| Polish | Dimmed placeholder isn't a URL; Paste replaces it; yt-dlp warnings reach the log; the status wraps at the window width |
| Cancel | Only the unfinished video's files are removed (finished videos and unrelated files stay); canceling between playlist videos; downloading again after a cancel |
| Playlist prompt | A single video doesn't ask; a playlist asks; declining downloads nothing; `url` results are followed before asking; Yes doesn't tick the box |

Sources: [tests/test_thread_safety.py:195-339](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/tests/test_thread_safety.py#L195-L339)

### The `gc.collect()` in `setUp`

Each test creates and destroys its own Tk app. If a destroyed app's objects are garbage-collected on a worker thread, each `Variable.__del__` blocks for about a second against the dead Tcl interpreter, which can push a test past its timeout. `setUp` therefore collects garbage on the main thread before building the next app.

Sources: [tests/test_thread_safety.py:100-103](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/tests/test_thread_safety.py#L100-L103)

## Format and ffmpeg tests (`test_formats.py`)

| Class | What it checks | Needs |
|---|---|---|
| `FormatSelectionTest` | Runs yt-dlp's real format selector on synthetic format lists: resolution caps, MP4 preference, fallback instead of failure, vertical videos, and that the remux and extract-audio post-processors are registered | yt-dlp (skipped otherwise) |
| `FindFfmpegTest` | `PATH` first, registry fallback on Windows only, not-found case, `%VAR%` expansion of the real registry `Path` | Windows for the last test |
| `SmallHelpersTest` | `format_eta`; the logger keeps warnings and errors apart | |
| `FfmpegHelpTest` | Install hint per platform; startup vs. Download wording | |
| `FfmpegCheckTest` | Startup warning, focus recheck (ignoring child widgets), one `find_ffmpeg` call per download, path passed to yt-dlp | Display |
| `ErrorColorTest` | The app's options give the logger plain `ERROR: …` text without ANSI codes | yt-dlp |

`registered_pps` and `mapping` read yt-dlp internals (`YoutubeDL._pps`, `PP.mapping`). If a yt-dlp update changes them, these tests skip instead of failing.

Sources: [tests/test_formats.py:22-70](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/tests/test_formats.py#L22-L70), [tests/test_formats.py:73-249](https://github.com/lifeupnorth/YouTubeDownloader/blob/bd3fd4242f83ee2cf111812e71b6d2db5aa71bdf/tests/test_formats.py#L73-L249)

## Running without yt-dlp or a display

- **Without yt-dlp:** the thread-safety tests don't need it (they use the fake), while the yt-dlp-dependent format tests skip.
- **Without a display:** tests that build a window catch `TclError` and skip.
