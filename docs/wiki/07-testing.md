# 7 Testing

<details>
<summary>Relevant source files</summary>

- [tests/test_engine.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_engine.py)
- [tests/test_real_yt_dlp.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_real_yt_dlp.py)
- [tests/test_window.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_window.py)
- [tests/test_app.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_app.py)
- [tests/test_requirements.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_requirements.py)
- [tests/test_build_exe.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_build_exe.py)
- [tests/test_check_pins.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_check_pins.py)
- [tests/fakes.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/fakes.py)
- [.github/workflows/tests.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/.github/workflows/tests.yml)
- [.github/dependabot.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/.github/dependabot.yml)

</details>

The tests use the standard library `unittest`, and nothing touches the internet: most downloads run against a fake yt-dlp, and `test_real_yt_dlp.py` runs the real one against a local server. There are 224 tests in seven files. The window's tests open real Qt windows, but on Qt's "offscreen" platform: `test_window.py` and `test_app.py` set `QT_QPA_PLATFORM=offscreen` before importing Qt (unless it's already set), so no display is needed and nothing shows up on screen. GitHub Actions runs them on every push to `main` and every pull request, together with a ruff lint check.

```
python -m unittest discover -s tests -v
```

| File | Tests | What it covers | Needs |
|---|---|---|---|
| `test_engine.py` | 75 | `download_engine.py` on its own: formats, ffmpeg and JavaScript runtime lookup, the update check, yt-dlp version and loading, preview helpers and thumbnail sizes, the logger, `DownloadJob` | Nothing (some tests use real yt-dlp or Pillow and skip without them) |
| `test_real_yt_dlp.py` | 3 | The engine with the real yt-dlp, downloading from a local HTTP server: a download then "Already downloaded", an MP3 converted only once, Cancel removing the `.part` file | yt-dlp; ffmpeg for the MP3 test |
| `test_window.py` | 102 | The real window driven with a fake yt-dlp: the Downloads list and its saved copy, the queue, several downloads at once, Cancel, Pause and Resume, drag and drop, the keyboard and right-click menu, closing, playlists, lookups, settings and themes; worker threads must never touch Qt or show a dialog | PySide6 |
| `test_app.py` | 18 | The Python version check (`main()` refuses 3.11 before Qt starts), settings file, the saved list's file (next to it, capped, bad files ignored), Show in folder (`reveal_in_file_manager`), ffmpeg help text, the update check's once-a-day limit and its setting, `split_links`, what the .exe does differently (no pip install, the `--self-check` report) | PySide6 |
| `test_requirements.py` | 10 | The yt-dlp pin in `requirements.txt` is exact (`yt-dlp[default]==…`) and is what the in-app installer uses; the `default` extra brings yt-dlp-ejs; every requirement is pinned; Dependabot watches the file | PySide6 (it imports the window module) |
| `test_build_exe.py` | 12 | `packaging/build_exe.py`: checksum-checked downloads and unpacking, the Windows version information it embeds, the `--check` verdict on the .exe's self-check (including Qt's SVG support), and `THIRD-PARTY-LICENSES.txt` (the bundled packages match what `requirements.txt` installs; Qt and PySide6 get the LGPL and GPL texts, not their packages' commercial-license file; no Tcl/Tk) | Nothing |
| `test_check_pins.py` | 4 | `packaging/check_pins.py` with GitHub's API faked: newer ffmpeg and deno releases, version comparison, an issue opened only once | Nothing |

Sources: [README.md:157-175](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/README.md#L157-L175), [tests/test_engine.py:1-4](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_engine.py#L1-L4), [tests/test_real_yt_dlp.py:1-10](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_real_yt_dlp.py#L1-L10), [tests/test_window.py:1-9](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_window.py#L1-L9), [tests/test_window.py:21](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_window.py#L21), [tests/test_app.py:15](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_app.py#L15)

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
| `files_dir` | Writes real stream files there (`.part` while downloading, a byte per 5%, then renamed, then "merged" into `v<N>.mp4`). Like yt-dlp, it continues a `.part` file that's there (recorded in `FakeYDL.resumed`), and skips a stream or video whose file is there |
| `merge` | With `files_dir`: a function that merges a video's streams into its final file, e.g. through a real process that pause or cancel stops; `False` makes the fake log a failed post-processing step, as yt-dlp does |
| `on_progress` | A callback before each progress event and before each video starts, used to cancel at an exact point |
| `warn` | A yt-dlp warning |
| `delay` | Seconds between progress events (default 0) |

Sources: [tests/fakes.py:8-9](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/fakes.py#L8-L9), [tests/fakes.py:12-142](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/fakes.py#L12-L142), [tests/fakes.py:145-150](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/fakes.py#L145-L150)

## Engine tests (`test_engine.py`, `test_real_yt_dlp.py`)

| Class | Tests |
|---|---|
| `FormatSelectionTest` | Runs yt-dlp's real format selector on synthetic format lists: resolution caps, MP4 preference, fallback instead of failure, vertical videos, that the remux and extract-audio post-processors are registered, and `final_ext` (needs yt-dlp) |
| `UpdateCheckTest` | Version comparison, the app's version only in the .exe, the notice when a newer release is out, never from source, no notice without a network or on an unexpected answer |
| `FindFfmpegTest` | The bundled ffmpeg only in the .exe, `PATH` first, registry fallback on Windows only, not found, `%VAR%` expansion of the real registry `Path` (Windows); the JavaScript runtime lookup: the bundled deno first, too-old versions skipped, yt-dlp's version check |
| `YtDlpVersionTest` | Version comparison, the pin, loading yt-dlp after an install and its failure case |
| `HelpersTest` | `format_eta`, `describe_info`, `thumbnail_url` (the smallest at least as wide as asked), `fetch_thumbnail` (only from the web, shrunk to `THUMB_SIZE` or a size asked for, stopped when canceled), thumbnails kept as small JPEGs and read back (not without Pillow, nor from damaged bytes), the logger's warning/error split |
| `ErrorColorTest` | Error text has no ANSI codes; "Deprecated Feature" notices, including a real one from yt-dlp, aren't errors |
| `DownloadJobTest` | A single video, options, progress per stream and per playlist video, skipped/failed/already downloaded, playlist confirmation, `url` results followed at most three times, the JavaScript runtime option and its hidden warning, Cancel (with real files in a temp folder, while a playlist is being read, and stopping an ffmpeg without removing files from an earlier run), Pause (the partial files kept and continued, a cancel after a resume removing what the pause kept, a paused playlist resuming at its video, the earlier files dropped once their video is saved, a playlist question cut short, a merge stopped by a pause and run again on resume), two jobs at once each stopping only their own ffmpeg, the `Popen` swap done once when jobs start together, which ffmpeg outputs count as new, warnings, previews (a thumbnail as big as the window asks for) and a canceled preview |
| `RealYtDlpTest` | In `test_real_yt_dlp.py`: the real yt-dlp downloading from `127.0.0.1` (its generic extractor treats it as a direct link): a download then "Already downloaded", an MP3 converted once and then found (needs ffmpeg), Cancel removing the `.part` file |

`make_job` builds a `DownloadJob` whose callbacks record into `job.progress` and `job.log`, so the tests check exactly what the window would receive. The ffmpeg-stopping tests run a slow Python process through yt-dlp's real `Popen`, so they need yt-dlp.

Sources: [tests/test_engine.py:86-420](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_engine.py#L86-L420), [tests/test_engine.py:423-461](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_engine.py#L423-L461), [tests/test_engine.py:474-484](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_engine.py#L474-L484), [tests/test_engine.py:487-1034](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_engine.py#L487-L1034), [tests/test_real_yt_dlp.py:53-138](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_real_yt_dlp.py#L53-L138)

## Window tests (`test_window.py`)

These check the window's behavior and its central rule: **worker threads never touch Qt**. `setUp` wraps the window's main-thread-only methods (`GUARDED`: `_log`, `_set_status`, `_looked_up`, `_download_finished`, `_show_progress`, `_run_next`, and the model's `add`, `changed` and `remove`) and records any call from another thread. The dialogs are `QMessageBox`'s static functions (`question`, `critical`, `warning`, `information`), patched so they answer at once (questions with the test's `answer`), record their titles and texts, and count a call from a worker as a violation too. After every test, a cleanup asserts there were no such calls and no exceptions on any thread (`threading.excepthook`). The window starts with one download at a time (saved in its settings first), so the queue tests see the links in order; the "Downloads at a time" tests choose more. The engine's lookups go through `fake_preview`, which makes a title from the link without yt-dlp; ffmpeg, the JavaScript runtime and the update check are patched, and settings go to a temporary file, never the real one.

`run_download` puts the fake in place of the engine's `yt_dlp`, types the links into the URL box, calls `_start_download`, and processes Qt's events until the window is idle (nothing running, no workers, no unfinished rows); `wait` fails the test after 10 seconds. `cancel_at` (and `pause_at`) clicks a row's Cancel (or Pause) at an exact point of the fake download and waits on the download thread until the main thread has. `idle` counts paused rows as done.

| Area | Tests |
|---|---|
| Downloading | Success (progress never goes backwards, ≤ 99 until done; the row's title, file and state; the summary and the count; the progress timer stops; no dialog); failure; a single video's error reported once; whole playlist; an empty playlist finds nothing; skipped and already-downloaded entries; a playlist where nothing was saved fails; a queue of links, one at a time; a failed link doesn't stop the rest; the summary counts every outcome; only a running or finished download fills its bar; a link already waiting isn't added again; audio has no quality; yt-dlp's warnings reach the log |
| Pause and resume | The partial files are kept and Resume continues them (the finished stream isn't downloaded again); "Pausing…" stays until it has stopped; Cancel on a paused row removes its files, and after a resume also those the pause kept; a paused waiting link is held back and not counted; a paused link isn't added again; a restored paused row resumes and counts again; no playlist question after a pause; Space pauses and resumes |
| Downloads at a time | Two download at once (both reach the middle together) and the third after; the speeds add up; the same link and folder doesn't download twice at once; more at a time starts waiting links now, fewer lets them finish, and it's saved; pausing one leaves the other; closing pauses every download; one playlist question at a time, also while a dialog runs Qt's events |
| Cancel | Only the unfinished video's files are removed; canceling between playlist videos; canceling a waiting link; downloading again after a cancel; progress after Cancel keeps "Canceling…"; no playlist question after a cancel |
| Closing | Closing during a download asks first, Yes pauses and closes (and the next run resumes it), No keeps going; closing again while it pauses doesn't cancel; a download that finished while the question was open; settings saved on close |
| Links and lookups | Paste adds links and leaves out other words; pasted lines go in one line; words that aren't links are left out; no link and a relative folder are refused; Enter downloads; lookups of a video, of `url` results to a playlist and of a bad link; the thumbnail, asked for at the screen's pixel ratio; closing cancels the lookups |
| Playlists | A single video doesn't ask; a playlist asks; a dialog that fails doesn't hang; `url` results are followed before asking |
| Row actions | Show in folder and Remove; a missing folder is reported; which icons each state has; a running row can't be removed and Clear finished keeps it; the list paints every state (offscreen) |
| Drag and drop, qualities | What a drop holds (links, links in text, a folder, not a file); the overlay says what a drop does and how; dropped links download with the chosen format, leaving the URL box alone; a dropped folder becomes Save to, and links dropped with one download into it; drops need ffmpeg like Download; the boxes leave drops to the window; the 4K and 1440p choices, saved and restored |
| Keyboard, menu, double-click | Space, Esc, Enter (and the keypad's), Delete and Backspace act on the selected row, only when the row has that action and without Ctrl; the arrow keys move and tabbing in selects a row; the right-click menu grays out what the row can't do; a double-click shows the file, but not on the icons; the tooltips name the keys |
| Crashes | A crashed download fails its row and opens the log; a crashing worker still finishes; errors in calls on the main thread reach the log |
| yt-dlp, ffmpeg, JavaScript runtime | The runtime is looked up on a worker; a missing one is explained; missing ffmpeg warns and blocks Download; coming back to the window finds it; the version in the header and the mismatch note; install with and without a restart; Download during an install doesn't install again; the update notice is logged |
| Saved list | The list comes back at the next start, without lookups and with Show in folder working; removing rows and Clear finished remove them from the saved list; rows that hadn't finished come back Paused; only the newest 200 rows are kept; bad entries are left out; thumbnails come back without a lookup; restored rows aren't in the summary |
| Settings and themes | Folder and format remembered; bad saved settings (also "at a time") fall back to the defaults; an unavailable saved folder is kept; the update switch only in a release, and saved; System by default; choosing a theme repaints and is saved; a saved theme is used at startup |

Each test builds its own window and closes it in a cleanup: it cancels the rows, waits for the workers, stops the saved list's timer (so it can't write into the next test's settings folder), then hides the window and deletes it with `deleteLater`. `setUp` collects garbage on the main thread first, so objects of an earlier test's window aren't collected on a worker thread.

Sources: [tests/test_window.py:43-57](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_window.py#L43-L57), [tests/test_window.py:60-151](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_window.py#L60-L151), [tests/test_window.py:155-201](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_window.py#L155-L201), [tests/test_window.py:1227-1229](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/tests/test_window.py#L1227-L1229)

## CI

`.github/workflows/tests.yml` runs on every push to `main` and every pull request, with read-only permissions:

- **Tests** on Windows and Ubuntu, each with Python 3.12 (the oldest the app supports, `MIN_PYTHON`) and 3.14, with `QT_QPA_PLATFORM=offscreen` set for the job. On Ubuntu it installs the libraries Qt's GUI module links to (`libegl1`, `libgl1`, `libxkbcommon0`, `libfontconfig1`, `libdbus-1-3`) and `ffmpeg` (for the real yt-dlp MP3 test, which skips where ffmpeg is missing); no virtual display is needed. Each job runs the suite twice: normally, and with yt-dlp blocked, so the tests that need it must skip rather than fail.
- **Lint**: `ruff check .` with the rules in `ruff.toml` (ruff is pinned in `requirements-dev.txt`); findings show up on the PR's lines.

Other workflows check what the unit tests can't. **Windows app** (`build-exe.yml`) builds the .exe on every pull request and push to `main` and checks it with `build_exe.py --check`; it also makes the releases. **Bundled programs** (`pins.yml`) runs `packaging/check_pins.py` every Monday and opens an issue when ffmpeg or deno, which the .exe bundles and Dependabot can't see, has a newer release.

Dependabot keeps the pins current, and its PRs run the same checks:

- `requirements.txt` (`yt-dlp[default]`, PySide6-Essentials and Pillow) and `requirements-dev.txt` (ruff): daily, since a new yt-dlp is often the fix for a YouTube change.
- `packaging/requirements-build.txt` (PyInstaller and its dependencies, pinned with their hashes): weekly.
- The GitHub Actions, which the workflows pin by commit SHA: weekly.

Sources: [.github/workflows/tests.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/.github/workflows/tests.yml), [ruff.toml](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/ruff.toml), [.github/workflows/build-exe.yml:1-20](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/.github/workflows/build-exe.yml#L1-L20), [.github/workflows/build-exe.yml:163-165](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/.github/workflows/build-exe.yml#L163-L165), [.github/workflows/pins.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/.github/workflows/pins.yml), [.github/dependabot.yml](https://github.com/lifeupnorth/YouTubeDownloader/blob/47e016f109accff408446b290ced9684d980c667/.github/dependabot.yml)
