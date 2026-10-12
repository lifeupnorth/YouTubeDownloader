# 6 User Interface

<details>
<summary>Relevant source files</summary>

- [youtube_downloader.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py)
- [download_engine.py](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py)

</details>

The window is plain Tkinter plus a ttk progress bar, built once by `_build_ui` with a dark, YouTube-like palette. This page lists the widgets, what each is bound to, how the link preview works, and how status and log messages reach the screen. For what the Download button sets off, see [Download Pipeline](04-download-pipeline.md).

## Layout

The root window has three grid rows: a header, the main card and the log. Only the log row grows and shrinks with the window (`rowconfigure(2, weight=1)`). The URL box and the log are `Text` widgets with `width=1`, so they fill their row and the window's width comes from the other controls rather than from Tk's default of 80 characters. `_fit_window` opens the window 680 px wide, or wider if the controls need it (e.g. with Windows display scaling), and as tall as its contents need. It caps both at the screen's work area without the taskbar (`work_area`), leaving `TITLE_BAR` (40 px) for the title bar and frame, centers the window there, and sets a minimum size that keeps every control, the status line and a couple of log lines visible. Text uses Segoe UI on Windows (Helvetica would fall back to Arial there) and Helvetica elsewhere; the log uses Consolas or Courier.

On Windows the app declares itself DPI aware before the window opens (`enable_dpi_awareness`, "system aware"), so on a scaled display (e.g. 150%) Windows doesn't draw it at 96 DPI and stretch it, which blurs the text. Fonts are in points, so Tk sizes them for the real DPI by itself; the layout's pixel sizes (padding, borders, the thumbnail box, wrap widths, the progress bar, the window's 680/580 px widths) are multiplied by `scale` (the screen's DPI / 96) once, after the window is built (`_scale_layout`, `_px`), and the preview asks the engine for a thumbnail of the scaled size. Below 1.05 the scale counts as 1, since Tk reports e.g. 96.08 DPI for 96.

```mermaid
flowchart TD
    root["YouTubeDownloader (tk.Tk)"] --> hdr["Header: title, version line, update Checkbutton (.exe only)"]
    root --> card["Main card"]
    root --> log["Log: tk.Text and Scrollbar"]
    card --> url["YouTube URLs: multi-line Text and Paste button"]
    card --> preview["Preview: thumbnail, title, length"]
    card --> fmt["Format: 6 Radiobuttons and 'Whole playlist' Checkbutton"]
    card --> save["Save To: Entry and Browse button"]
    card --> btn["Download / Cancel button"]
    card --> bar["ttk.Progressbar"]
    card --> status["Status label and Open folder button"]
```

Sources: [youtube_downloader.py:30-44](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L30-L44), [youtube_downloader.py:99-118](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L99-L118), [youtube_downloader.py:229-419](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L229-L419), [youtube_downloader.py:440-458](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L440-L458)

## Widgets and bindings

| Widget | Bound to | Behavior |
|---|---|---|
| Header label (`powered_lbl`) | `_powered_by_text` | "Powered by yt-dlp 2026.08.19", or "(not installed)"; a release .exe puts its own version first ("v1.0.3 · …"); updated after an in-app install |
| Check for updates at startup `Checkbutton` | `update_check_var` | Only in a release .exe (`engine.app_version()`); on by default, saved as `check_updates` when clicked. While ticked, startup checks GitHub for a newer release at most once a day and says so in the log |
| URL `Text` (`url_entry`) | `<FocusIn>`, `<FocusOut>`, `<KeyRelease>`, `<Return>`, `<Shift-Return>`, `<Tab>`, `<Shift-Tab>` | One link per line; grows from 1 to 4 lines (`_fit_url_box`). Shows a dimmed placeholder (tracked by `_placeholder_shown`) until focused. Enter starts a download, Shift+Enter adds a line, Tab moves to the next widget and Shift+Tab to the previous one. `_urls()` returns the links in order without repeats; `_url()` the first one. |
| Paste `Button` | `_paste_url` | Adds the clipboard's links to those in the box (or replaces the placeholder), one per line and without repeats, leaving out words that aren't links (`split_links`); a clipboard without a link only changes the status |
| Preview (`preview_img`, `preview_lbl`) | `preview_text` | Thumbnail, title, and length or playlist size with the channel, for the first link; "+ 2 more links" when there are several |
| Format `Radiobutton` ×6 | `format_var` | From `FORMATS`, laid out with `divmod(i, 3)` in columns 0–1 |
| Whole playlist `Checkbutton` | `playlist_var` | In column 2 of the format grid, off by default and never remembered; changing it refreshes the preview |
| Save To `Entry` | `download_path` | Defaults to `~/Downloads`, or the last folder used; a saved folder that's missing (an unplugged drive) is kept in the settings and noted in the log. Download refuses an empty or relative path |
| Browse `Button` | `_browse_folder` | `filedialog.askdirectory`, starting at the current folder |
| Download `Button` (`dl_btn`) | `_start_download` | Becomes **✖ Cancel** (`_cancel_download`) while a download runs, then "Canceling…" (disabled) until it stops |
| `ttk.Progressbar` | `progress_var` | `Red.Horizontal.TProgressbar` style on the `clam` theme |
| Status `Label` (`status_lbl`) | `status_var` | Text and color set by `_set_status`; wraps at its current width |
| Open folder `Button` (`open_btn`) | `_open_folder` | Hidden until a download saves a file; shows that file in Explorer, Finder or the Linux file manager (`reveal_in_file_manager`) |
| Log `Text` (`log_box`) | `_log` | Read-only; temporarily enabled to append a line and scroll to it |

Sources: [youtube_downloader.py:121-134](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L121-L134), [youtube_downloader.py:229-419](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L229-L419), [youtube_downloader.py:421-438](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L421-L438), [youtube_downloader.py:462-539](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L462-L539), [youtube_downloader.py:782-797](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L782-L797), [youtube_downloader.py:862-879](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L862-L879)

## The link preview

Typing (after a 600 ms pause), pasting, leaving the URL box, or changing **Whole playlist** schedules `_start_preview`. It skips the lookup if neither the links nor the box changed (`_preview_key`). Otherwise it bumps `_preview_token` and starts `_preview_thread` on a worker, which calls `engine.preview_link` with the JavaScript runtime found at startup. That asks yt-dlp for the first link's details without downloading (`process=False`, following `url` results, not paging through playlist entries), formats them with `describe_info`, and fetches a small thumbnail with `fetch_thumbnail`. The thumbnail needs Pillow; without it the preview is text only. `_preview_done` ignores answers whose token is older than the current one, so typing quickly can't show the wrong video. Each lookup also gets its own `threading.Event` (`_preview_cancel`); starting a newer one, or closing the window, sets the old one. `preview_link` then stops at yt-dlp's next status message (the `check` of `_YdlLogger` raises yt-dlp's `DownloadCancelled`) or the thumbnail's next 64 KB chunk and returns `None`, and `_preview_thread` returns without posting a result. After an in-app install of yt-dlp, `_install_done` clears `_preview_key` so a link already in the box gets its preview.

Sources: [download_engine.py:416-503](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/download_engine.py#L416-L503), [youtube_downloader.py:799-847](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L799-L847), [youtube_downloader.py:945-949](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L945-L949)

## Status, log and colors

Two helpers write to the screen, and both must run on the main thread:

- `_set_status(msg, color)` sets `status_var` and the label's text color.
- `_log(msg)` appends a line to `log_box`.

Worker threads reach them only through `_post` or the coalesced `_latest_progress` (see [Architecture and Threading](03-architecture.md)). Unexpected errors, in a worker or in a Tk callback, also end up in the log as "✘ Unexpected error: …". The status color shows the state:

| Constant | Hex | Used for |
|---|---|---|
| `TEXT_DIM` | `#aaaaaa` | Idle ("Ready", "Playlist not downloaded.", "Download canceled …") |
| `WARNING` | `#f59e0b` | In progress, ffmpeg missing, an install still running, a finished run where something was skipped, failed or declined |
| `SUCCESS` | `#22c55e` | Download complete, yt-dlp installed |
| `ACCENT` | `#ff0000` | Errors (a failed download, a download that crashed, a failed install); also the Download button and progress bar |

Sources: [youtube_downloader.py:30-40](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L30-L40), [youtube_downloader.py:531-539](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L531-L539), [youtube_downloader.py:565-579](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L565-L579), [youtube_downloader.py:752-780](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L752-L780)

## Dialogs

`_start_download` checks, in order: a download already running ("Busy"), yt-dlp missing, ffmpeg missing, no link ("No URL").

| Dialog | When |
|---|---|
| "Busy" (info) | Download clicked while a download is running |
| "yt-dlp not found" (yes/no) | From source: startup without yt-dlp, or Download clicked without it (while an install runs, the status line says to wait instead) |
| "yt-dlp not found" (error) | The same, in the .exe, which has no pip: "This copy of the app is missing yt-dlp" |
| "ffmpeg not found" (error) | Download clicked without ffmpeg |
| "No URL" (warning) | No link entered |
| "Playlist" (yes/no) | Whole playlist unticked and a link turns out to be a playlist or channel |
| "Download Failed" (error) | Nothing could be downloaded; success has no pop-up, just the status line and Open folder |
| "Folder not found" (error) / "Couldn't open the folder" (error) | Open folder clicked after the folder was removed / the file manager couldn't be started |
| "Download in progress" (yes/no) | Closing the window during a download |
| "Installed" (info) | Only if yt-dlp was installed but couldn't be loaded without a restart |

Sources: [youtube_downloader.py:616-643](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L616-L643), [youtube_downloader.py:651-672](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L651-L672), [youtube_downloader.py:715-735](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L715-L735), [youtube_downloader.py:752-780](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L752-L780), [youtube_downloader.py:788-797](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L788-L797), [youtube_downloader.py:897-958](https://github.com/lifeupnorth/YouTubeDownloader/blob/b369bd269cac691e2563e27391dbd3702c986e50/youtube_downloader.py#L897-L958)
