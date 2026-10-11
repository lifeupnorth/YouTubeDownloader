# 7 Testing

<details>
<summary>Relevant source files</summary>

- [tests/test_engine.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/tests/test_engine.py)
- [tests/test_thread_safety.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/tests/test_thread_safety.py)
- [tests/test_app.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/tests/test_app.py)
- [tests/test_requirements.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/tests/test_requirements.py)
- [tests/fakes.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/tests/fakes.py)
- [.github/workflows/tests.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/.github/workflows/tests.yml)

</details>

The tests use the standard library `unittest`, and nothing touches the internet: most downloads run against a fake yt-dlp, and `test_real_yt_dlp.py` runs the real one against a local server. There are 172 tests in eight files. The engine's tests need no display; the window's tests open real Tk windows. GitHub Actions runs them on every push to `main` and every pull request, together with a ruff lint check.

```
python -m unittest discover -s tests -v
```

| File | Tests | What it covers | Needs |
|---|---|---|---|
| `test_engine.py` | 69 | `download_engine.py` on its own: formats, ffmpeg and JavaScript runtime lookup, the update check, yt-dlp version and loading, preview helpers, the logger, `DownloadJob`, `DownloadQueue` | Nothing (some tests use real yt-dlp or Pillow and skip without them) |
| `test_real_yt_dlp.py` | 3 | The engine with the real yt-dlp, downloading from a local HTTP server: a download then "Already downloaded", an MP3 converted only once, Cancel removing the `.part` file | yt-dlp; ffmpeg for the MP3 test |
| `test_thread_safety.py` | 50 | The real window driven through simulated downloads; worker threads must never call Tk | A display |
| `test_app.py` | 15 | Settings file, Open folder, ffmpeg help text and checks in the window | A display for the window checks |
| `test_requirements.py` | 10 | The yt-dlp pin in `requirements.txt` is what the in-app installer uses; every requirement is pinned | Tkinter (it imports the window) |
| `test_build_exe.py` | 7 | `packaging/build_exe.py`: checksum-checked downloads and unpacking, and the `--check` verdict on the .exe's self-check | Nothing |
| `test_sign_exe.py` | 8 | `packaging/sign_exe.py` with a fake signtool | Nothing |
| `test_smoke.py` | 10 | `packaging/smoke_test.py` and `--smoke-download` with a fake job: the report, retries, timeouts, the bot check | Tkinter (it imports the window) |

### The nightly smoke test

The unit tests never touch the network, so they can't see YouTube breaking yt-dlp. `.github/workflows/smoke.yml` does, every night, by hand, and on pull requests that change it: `packaging/smoke_test.py` downloads one 19-second video as MP4 and as MP3, from source on Linux and with a freshly built .exe on Windows. Each download runs `youtube_downloader.py --smoke-download URL FORMAT FOLDER REPORT` (no window; the log also streams to `REPORT.log`), gets two attempts of 4 minutes, and passes when the file has the right extension and size. If YouTube only answers with its "Sign in to confirm you're not a bot" check, which says nothing about the app, the job warns and passes. `SMOKE_URL` (a repository variable) picks another video.

Sources: [README.md:60-73](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/README.md#L60-L73), [tests/test_engine.py:1-4](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/tests/test_engine.py#L1-L4), [tests/test_thread_safety.py:1-4](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/tests/test_thread_safety.py#L1-L4)

## The fake yt-dlp (`tests/fakes.py`)

`make_fake_ydl` returns a stand-in for `yt_dlp.YoutubeDL` that drives the engine's real progress hook, logger and post-processors (`before_dl`, `after_move`), and `fake_yt_dlp` wraps it as a module (with `PostProcessor=object` and a `FakeCancelled` as `utils.DownloadCancelled`). Its parameters build scenarios:

| Parameter | Simulates |
|---|---|
| `streams` | Streams per video (2 = merged video and audio) |
| `videos` | A playlist with `playlist_index`/`n_entries` |
| `overshoot` | `downloaded_bytes` exceeding `total_bytes_estimate` |
| `fail` | Entries that call `logger.error` (and raise unless `ignoreerrors` is set) |
| `already` | Entries reported with `__real_download=False` |
| `error` | An exception after the run |
| `probe` | The `_type` returned by each `extract_info(process=False)` call; `None` logs an error and returns nothing, like a video that can't be found |
| `files_dir` | Writes real stream files there (`.part` while downloading, then renamed, then "merged" into `v<N>.mp4`) |
| `on_progress` | A callback before each progress event, used to cancel at an exact point |
| `warn` | A yt-dlp warning |
| `delay` | Seconds between progress events (default 0) |

Sources: [tests/fakes.py:8-118](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/tests/fakes.py#L8-L118)

## Engine tests (`test_engine.py`)

| Class | Tests |
|---|---|
| `FormatSelectionTest` | Runs yt-dlp's real format selector on synthetic format lists: resolution caps, MP4 preference, fallback instead of failure, vertical videos, and that the remux and extract-audio post-processors are registered (needs yt-dlp) |
| `FindFfmpegTest` | `PATH` first, registry fallback on Windows only, not found, `%VAR%` expansion of the real registry `Path` (Windows) |
| `YtDlpVersionTest` | Version comparison, the pin, loading yt-dlp after an install and its failure case |
| `HelpersTest` | `format_eta`, `describe_info`, `thumbnail_url`, `fetch_thumbnail`, the logger's warning/error split |
| `ErrorColorTest` | Error text has no ANSI codes; "Deprecated Feature" notices aren't errors |
| `DownloadJobTest` | A single video, options, progress per stream and per playlist video, skipped/failed/already downloaded, playlist confirmation, Cancel (with real files in a temp folder), warnings, previews |
| `DownloadQueueTest` | Links in order with "Link n/N", a failed link doesn't stop the rest, an unavailable video counts once, all failed, declined playlists, Cancel skips the remaining links |

`make_job` builds a `DownloadJob` whose callbacks record into `job.progress` and `job.log`, so the tests check exactly what the window would receive.

Sources: [tests/test_engine.py:82-293](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/tests/test_engine.py#L82-L293), [tests/test_engine.py:296-555](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/tests/test_engine.py#L296-L555)

## Window tests (`test_thread_safety.py`)

These check the app's central rule: **worker threads never call Tk**. `setUp` records any call from a non-main thread at three levels: the Tk methods in `GUARDED`, the Tcl interpreter itself (`GuardedTcl`, which every widget, variable and image goes through), and the dialogs (`GuardedDialogs`, which also wraps a mock a test puts in a dialog's place). After every test, a cleanup asserts there were no such calls, no exceptions on any thread, and no worker crashes (unless the test sets `expect_crash`). `run_download` puts the fake in place of the engine's `yt_dlp`, starts `_start_download`, and runs `mainloop()`; it fails the test if the download hasn't finished after 10 seconds; `_download_finished` and the patched dialogs end the loop. Settings go to a temporary file, never the real one.

| Area | Tests |
|---|---|
| Downloads | Success (progress never goes backwards, ≤ 99 until done, then 100; the log shows the final file); failure dialog; whole playlist; skipped and already-downloaded entries |
| Several links | One per line, repeats dropped, the box grows to 4 lines; Enter downloads; a queue of three; a failed link doesn't stop the rest |
| Playlist question | A single video doesn't ask; a playlist asks; declining downloads nothing; `url` results are followed first; Yes doesn't tick the box |
| Cancel and closing | Only the unfinished video's files are removed; canceling between playlist videos; downloading again after a cancel; closing during a download asks first, Yes cancels and closes, No keeps going; closing with the error dialog open |
| Preview | Video, playlist, bad link, an older link's late answer is dropped, thumbnail |
| Settings, version, install | Folder and format remembered, saved on close, bad settings ignored; version in the header and the mismatch note; install with and without a restart |
| Polish | Placeholder isn't a URL, Paste, warnings in the log, the status wraps, Open folder |

Each test builds and destroys its own window. If a destroyed window's objects were garbage-collected on a worker thread, each `Variable.__del__` would block for about a second against the dead Tcl interpreter, so `setUp` collects garbage on the main thread first.

Sources: [tests/test_thread_safety.py:24-29](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/tests/test_thread_safety.py#L24-L29), [tests/test_thread_safety.py:33-36](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/tests/test_thread_safety.py#L33-L36), [tests/test_thread_safety.py:120-128](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/tests/test_thread_safety.py#L120-L128)

## CI

`.github/workflows/tests.yml` runs on every push to `main` and every pull request, with read-only permissions:

- **Tests** on Windows and Ubuntu, each with Python 3.10 (the oldest the pinned yt-dlp supports) and 3.14. Ubuntu runs under a virtual display (`xvfb-run`). Each job runs the suite twice: normally, and with yt-dlp blocked, so the tests that need it must skip rather than fail.
- **Lint**: `ruff check .` with the rules in `ruff.toml` (ruff is pinned in `requirements-dev.txt`); findings show up on the PR's lines.

Dependabot keeps yt-dlp, Pillow, ruff and the GitHub Actions versions current; its PRs run the same checks.

Sources: [.github/workflows/tests.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/.github/workflows/tests.yml), [ruff.toml](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/ruff.toml), [.github/dependabot.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/fed021846b04c093d045f0ec3de2b53d5ea42060/.github/dependabot.yml)
