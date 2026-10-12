# 7 Testing

<details>
<summary>Relevant source files</summary>

- [tests/test_engine.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_engine.py)
- [tests/test_real_yt_dlp.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_real_yt_dlp.py)
- [tests/test_thread_safety.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_thread_safety.py)
- [tests/test_app.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_app.py)
- [tests/test_requirements.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_requirements.py)
- [tests/test_build_exe.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_build_exe.py)
- [tests/test_check_pins.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_check_pins.py)
- [tests/test_smoke.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_smoke.py)
- [tests/fakes.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/fakes.py)
- [packaging/smoke_test.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/packaging/smoke_test.py)
- [.github/workflows/tests.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/.github/workflows/tests.yml)
- [.github/workflows/smoke.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/.github/workflows/smoke.yml)
- [.github/dependabot.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/.github/dependabot.yml)

</details>

The tests use the standard library `unittest`, and nothing touches the internet: most downloads run against a fake yt-dlp, and `test_real_yt_dlp.py` runs the real one against a local server. There are 190 tests in eight files. The engine's tests need no display; the window's tests open real Tk windows. GitHub Actions runs them on every push to `main` and every pull request, together with a ruff lint check.

```
python -m unittest discover -s tests -v
```

| File | Tests | What it covers | Needs |
|---|---|---|---|
| `test_engine.py` | 75 | `download_engine.py` on its own: formats, ffmpeg and JavaScript runtime lookup, the update check, yt-dlp version and loading, preview helpers, the logger, `DownloadJob`, `DownloadQueue` | Nothing (some tests use real yt-dlp or Pillow and skip without them) |
| `test_real_yt_dlp.py` | 3 | The engine with the real yt-dlp, downloading from a local HTTP server: a download then "Already downloaded", an MP3 converted only once, Cancel removing the `.part` file | yt-dlp; ffmpeg for the MP3 test |
| `test_thread_safety.py` | 58 | The real window driven through simulated downloads; worker threads must never call Tk | A display |
| `test_app.py` | 18 | Settings file, Open folder, ffmpeg help text and checks in the window, the update check's once-a-day limit and its setting, what the .exe does differently (no pip install, the `--self-check` report) | A display for the window checks |
| `test_requirements.py` | 10 | The yt-dlp pin in `requirements.txt` is exact (`yt-dlp[default]==…`) and is what the in-app installer uses; the `default` extra brings yt-dlp-ejs; every requirement is pinned; Dependabot watches the file | Tkinter (it imports the window) |
| `test_build_exe.py` | 12 | `packaging/build_exe.py`: checksum-checked downloads and unpacking, the Windows version information it embeds, and the `--check` verdict on the .exe's self-check | Nothing |
| `test_check_pins.py` | 4 | `packaging/check_pins.py` with GitHub's API faked: newer ffmpeg and deno releases, version comparison, an issue opened only once | Nothing |
| `test_smoke.py` | 10 | `packaging/smoke_test.py` and `--smoke-download` with a fake job: the report, retries, timeouts, the bot check | Tkinter (it imports the window) |

### The nightly smoke test

The unit tests never touch the network, so they can't see YouTube breaking yt-dlp. `.github/workflows/smoke.yml` does, every night, by hand, and on pull requests that change it: `packaging/smoke_test.py` downloads one 19-second video as MP4 and as MP3, from source on Linux and with a freshly built .exe on Windows. Each download runs `youtube_downloader.py --smoke-download URL FORMAT FOLDER REPORT` (no window; the log also streams to `REPORT.log`), gets two attempts of 4 minutes, and passes when the file has the right extension and size. If YouTube only answers with its "Sign in to confirm you're not a bot" check, which says nothing about the app, the job warns and passes. `SMOKE_URL` (a repository variable) picks another video.

Sources: [README.md:113-131](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/README.md#L113-L131), [tests/test_engine.py:1-4](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_engine.py#L1-L4), [tests/test_real_yt_dlp.py:1-10](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_real_yt_dlp.py#L1-L10), [tests/test_thread_safety.py:1-4](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_thread_safety.py#L1-L4), [packaging/smoke_test.py:1-25](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/packaging/smoke_test.py#L1-L25), [.github/workflows/smoke.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/.github/workflows/smoke.yml)

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
| `probe` | The `_type` returned by each `extract_info(process=False)` call (with a `url` to follow); `None` logs an error and returns nothing, like a video that can't be found |
| `files_dir` | Writes real stream files there (`.part` while downloading, then renamed, then "merged" into `v<N>.mp4`) |
| `on_progress` | A callback before each progress event and before each video starts, used to cancel at an exact point |
| `warn` | A yt-dlp warning |
| `delay` | Seconds between progress events (default 0) |

Sources: [tests/fakes.py:8-124](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/fakes.py#L8-L124)

## Engine tests (`test_engine.py`, `test_real_yt_dlp.py`)

| Class | Tests |
|---|---|
| `FormatSelectionTest` | Runs yt-dlp's real format selector on synthetic format lists: resolution caps, MP4 preference, fallback instead of failure, vertical videos, that the remux and extract-audio post-processors are registered, and `final_ext` (needs yt-dlp) |
| `UpdateCheckTest` | Version comparison, the app's version only in the .exe, the notice when a newer release is out, never from source, no notice without a network or on an unexpected answer |
| `FindFfmpegTest` | The bundled ffmpeg only in the .exe, `PATH` first, registry fallback on Windows only, not found, `%VAR%` expansion of the real registry `Path` (Windows); the JavaScript runtime lookup: the bundled deno first, too-old versions skipped, yt-dlp's version check |
| `YtDlpVersionTest` | Version comparison, the pin, loading yt-dlp after an install and its failure case |
| `HelpersTest` | `format_eta`, `describe_info`, `thumbnail_url`, `fetch_thumbnail` (only from the web, shrunk, stopped when canceled), the logger's warning/error split |
| `ErrorColorTest` | Error text has no ANSI codes; "Deprecated Feature" notices, including a real one from yt-dlp, aren't errors |
| `DownloadJobTest` | A single video, options, progress per stream and per playlist video, skipped/failed/already downloaded, playlist confirmation, `url` results followed at most three times, the JavaScript runtime option and its hidden warning, Cancel (with real files in a temp folder, while a playlist is being read, and stopping an ffmpeg without removing files from an earlier run), which ffmpeg outputs count as new, warnings, previews and a canceled preview |
| `DownloadQueueTest` | Links in order with "Link n/N", a failed link doesn't stop the rest, an unavailable video counts once, all failed, declined playlists, Cancel skips the remaining links and stops the running link's ffmpeg, a cancel after the last link finished, a crashing link is a failed link, no outcome before it runs |
| `RealYtDlpTest` | In `test_real_yt_dlp.py`: the real yt-dlp downloading from `127.0.0.1` (its generic extractor treats it as a direct link): a download then "Already downloaded", an MP3 converted once and then found (needs ffmpeg), Cancel removing the `.part` file |

`make_job` builds a `DownloadJob` whose callbacks record into `job.progress` and `job.log`, so the tests check exactly what the window would receive. The ffmpeg-stopping tests run a slow Python process through yt-dlp's real `Popen`, so they need yt-dlp.

Sources: [tests/test_engine.py:85-420](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_engine.py#L85-L420), [tests/test_engine.py:422-898](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_engine.py#L422-L898), [tests/test_real_yt_dlp.py:45-127](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_real_yt_dlp.py#L45-L127)

## Window tests (`test_thread_safety.py`)

These check the app's central rule: **worker threads never call Tk**. `setUp` records any call from a non-main thread at three levels: the Tk methods in `GUARDED`, the Tcl interpreter itself (`GuardedTcl`, which every widget, variable and image goes through), and the dialogs (`GuardedDialogs`, which also wraps a mock a test puts in a dialog's place). After every test, a cleanup asserts there were no such calls, no exceptions on any thread, and no worker crashes (unless the test sets `expect_crash`). `run_download` puts the fake in place of the engine's `yt_dlp`, starts `_start_download`, and runs `mainloop()`; it fails the test if the download hasn't finished after 10 seconds; `_download_finished` and the patched dialogs end the loop. Settings go to a temporary file, never the real one.

| Area | Tests |
|---|---|
| Downloads | Success (progress never goes backwards, ≤ 99 until done, then 100; the log shows the final file); failure dialog; a single video's error reported once; whole playlist; skipped and already-downloaded entries; a playlist where nothing was saved fails |
| Several links | One per line, repeats dropped, the box grows to 4 lines; Enter downloads; a queue of three; a failed link doesn't stop the rest |
| Playlist question | A single video doesn't ask; a playlist asks; declining downloads nothing; `url` results are followed first; Yes doesn't tick the box; a dialog that fails counts as No instead of hanging |
| Cancel and closing | Only the unfinished video's files are removed; canceling between playlist videos; downloading again after a cancel; closing during a download asks first, Yes cancels and closes, No keeps going; a download that finished while the question was open; closing with the error dialog open |
| Preview | Video, `url` results to a playlist, bad link, an older link's late answer is dropped, a newer link cancels the older lookup, closing cancels it, No to the close question keeps it, thumbnail |
| Crashes | A crashed download still ends (button back, error in the log); a crashing worker still finishes; errors in Tk callbacks reach the log |
| Settings, version, install | Folder and format remembered, saved on close, bad settings ignored; version in the header and the mismatch note; install with and without a restart; Download during an install doesn't install again; the update notice is logged |
| Polish | Placeholder isn't a URL, Paste, warnings in the log, the status wraps, Open folder, Shift+Tab, the JavaScript runtime is looked up on a worker |

Each test builds and destroys its own window. If a destroyed window's objects were garbage-collected on a worker thread, each `Variable.__del__` would block for about a second against the dead Tcl interpreter, so `setUp` collects garbage on the main thread first.

Sources: [tests/test_thread_safety.py:25-90](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_thread_safety.py#L25-L90), [tests/test_thread_safety.py:93-131](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_thread_safety.py#L93-L131), [tests/test_thread_safety.py:213-236](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/tests/test_thread_safety.py#L213-L236)

## CI

`.github/workflows/tests.yml` runs on every push to `main` and every pull request, with read-only permissions:

- **Tests** on Windows and Ubuntu, each with Python 3.10 (the oldest the pinned yt-dlp supports) and 3.14. On Ubuntu it installs `xvfb` and `ffmpeg` (for the real yt-dlp MP3 test, which skips where ffmpeg is missing) and runs under a virtual display (`xvfb-run`). Each job runs the suite twice: normally, and with yt-dlp blocked, so the tests that need it must skip rather than fail.
- **Lint**: `ruff check .` with the rules in `ruff.toml` (ruff is pinned in `requirements-dev.txt`); findings show up on the PR's lines.

Other workflows check what the unit tests can't. **Windows app** (`build-exe.yml`) builds the .exe on every pull request and push to `main` and checks it with `build_exe.py --check`; it also makes the releases. **Bundled programs** (`pins.yml`) runs `packaging/check_pins.py` every Monday and opens an issue when ffmpeg or deno, which the .exe bundles and Dependabot can't see, has a newer release. The nightly smoke test is described above.

Dependabot keeps the pins current, and its PRs run the same checks:

- `requirements.txt` (`yt-dlp[default]` and Pillow) and `requirements-dev.txt` (ruff): daily, since a new yt-dlp is often the fix for a YouTube change.
- `packaging/requirements-build.txt` (PyInstaller and its dependencies, pinned with their hashes): weekly.
- The GitHub Actions, which the workflows pin by commit SHA: weekly.

Sources: [.github/workflows/tests.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/.github/workflows/tests.yml), [ruff.toml](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/ruff.toml), [.github/workflows/build-exe.yml:1-20](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/.github/workflows/build-exe.yml#L1-L20), [.github/workflows/pins.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/.github/workflows/pins.yml), [.github/dependabot.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/.github/dependabot.yml)
