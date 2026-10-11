#!/usr/bin/env python3
"""
YouTube Video Downloader — Desktop App
Requirements: pip install -r requirements.txt
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import queue
import functools
import glob
import os
import re
import shutil
import sys
import subprocess

# ── Try importing yt-dlp; offer to install if missing ──────────────────────
try:
    import yt_dlp
    YT_DLP_AVAILABLE = True
except ImportError:
    YT_DLP_AVAILABLE = False


def _yt_dlp_requirement():
    # requirements.txt next to this script is the only place the yt-dlp pin is
    # kept (Dependabot bumps it). Unpinned if the script was copied on its own.
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "requirements.txt")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line.lower().startswith("yt-dlp"):
                    return line
    except OSError:
        pass
    return "yt-dlp"


# What the in-app installer runs pip with, e.g. "yt-dlp==2026.8.19".
YT_DLP_REQUIREMENT = _yt_dlp_requirement()


# ── Color palette ───────────────────────────────────────────────────────────
BG        = "#0f0f0f"
SURFACE   = "#1a1a1a"
SURFACE2  = "#242424"
ACCENT    = "#ff0000"
ACCENT_HV = "#cc0000"
TEXT      = "#ffffff"
TEXT_DIM  = "#aaaaaa"
SUCCESS   = "#22c55e"
WARNING   = "#f59e0b"
BORDER    = "#333333"

# res:N caps the shorter side (as YouTube's labels do), not the height: for a
# vertical 1080x1920 video, "480p" gives 480x854.
VIDEO_MAX_RES = {"mp4_best": None, "1080p": 1080, "720p": 720, "480p": 480}

FFMPEG_MISSING_STATUS = "ffmpeg not found, see the log below"


def _ffmpeg_install_hint():
    if sys.platform == "win32":
        return "Install it from a terminal:\n    winget install ffmpeg"
    if sys.platform == "darwin":
        return "Install it from a terminal:\n    brew install ffmpeg"
    return "Install it with your package manager, e.g.:\n    sudo apt install ffmpeg"


def ffmpeg_help(startup=False):
    then = ("Once it's installed, the app detects it (restart it if it doesn't)."
            if startup else
            "Then click Download again (restart the app if it still isn't found).")
    return ("ffmpeg is required to merge video and audio and to convert to "
            f"MP3/M4A, but it wasn't found.\n\n{_ffmpeg_install_hint()}\n\n{then}")


def _registry_path():
    # The saved Path, which includes installs made after this process started
    # (winget, Chocolatey, Scoop, manual); os.environ only has the old one.
    import winreg
    parts = []
    # Machine Path before user Path, the order Windows builds PATH in.
    for root, key in ((winreg.HKEY_LOCAL_MACHINE,
                       r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, r"Environment")):
        try:
            with winreg.OpenKey(root, key) as k:
                parts.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
        except OSError:
            pass
    return os.pathsep.join(parts)


def find_ffmpeg():
    found = shutil.which("ffmpeg")
    if found is None and sys.platform == "win32":
        found = shutil.which("ffmpeg", path=_registry_path())
    return found


def format_opts(fmt):
    """yt-dlp options for one of the Format radio button values."""
    if fmt in VIDEO_MAX_RES:
        res = VIDEO_MAX_RES[fmt]
        # Sort instead of filter: when nothing matches exactly, yt-dlp picks
        # the closest format rather than failing with "format not available".
        return {
            "format": "bv*+ba/b",
            "format_sort": ([f"res:{res}"] if res else []) + ["ext:mp4:m4a"],
            "merge_output_format": "mp4",
            # The single-file fallback ("b") is not always MP4.
            "postprocessors": [{"key": "FFmpegVideoRemuxer",
                                "preferedformat": "mp4"}],
        }
    if fmt == "mp3":
        return {
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        }
    if fmt == "m4a":
        return {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
            }],
        }
    raise ValueError(f"unknown format: {fmt}")


_ANSI_CODES = re.compile(r"\x1b\[[0-9;]*m")


class _YdlLogger:
    """Hands yt-dlp's errors to on_error; with ignoreerrors they don't raise."""

    def __init__(self, on_error):
        self._on_error = on_error

    def error(self, msg):
        # yt-dlp also sends its "Deprecated Feature: ..." notices (e.g. for an
        # old Python version) here, after already passing them to warning().
        # They aren't failed downloads.
        if _ANSI_CODES.sub("", msg).startswith("Deprecated Feature:"):
            return
        self._on_error(msg)

    def debug(self, msg):
        pass

    info = debug

    def warning(self, msg):
        print(msg, file=sys.stderr)


@functools.lru_cache(maxsize=None)
def _callback_pp_class(base):
    class CallbackPP(base):
        def __init__(self, fn):
            super().__init__()
            self._fn = fn

        def run(self, info):
            self._fn(info)
            return [], info

    return CallbackPP


@functools.lru_cache(maxsize=None)
def _stream_counter_class(base):
    # Defined lazily because yt_dlp may be missing at import time.
    class StreamCounter(base):
        """before_dl hook, run once per video (also per playlist entry).

        Merged formats download video and audio as separate streams, and the
        progress hook's info_dict no longer lists them, so count them here.
        """

        def __init__(self, app):
            super().__init__()
            self._app = app

        def run(self, info):
            # Also where Cancel stops a playlist between two videos.
            self._app._check_canceled()
            self._app._stream_index = 0
            self._app._stream_count = len(info.get("requested_formats") or ()) or 1
            self._app._entry_files = set()
            return [], info

    return StreamCounter


class YouTubeDownloader(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YT Downloader")
        self.geometry("680x600")
        self.minsize(580, 520)
        self.configure(bg=BG)
        self.resizable(True, True)

        # State
        self.download_path = tk.StringVar(value=os.path.expanduser("~/Downloads"))
        self.format_var    = tk.StringVar(value="mp4_best")
        self.playlist_var  = tk.BooleanVar(value=False)
        self.progress_var  = tk.DoubleVar(value=0)
        self.status_var    = tk.StringVar(value="Ready")
        self.is_running    = False
        self._after_id     = None
        self._ui_queue     = queue.Queue()
        self._active_workers = 0
        self._closing      = False
        self._latest_progress = None
        self._shown_progress  = None
        self._stream_index = 0
        self._stream_count = 1
        self._saved = self._already = 0
        self._skipped = []
        self._cancel = threading.Event()
        self._entry_files = set()

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if not YT_DLP_AVAILABLE:
            self.after(300, self._prompt_install)
        self._ffmpeg_missing = not find_ffmpeg()
        if self._ffmpeg_missing:
            self._set_status(FFMPEG_MISSING_STATUS, WARNING)
            self._log(ffmpeg_help(startup=True))
        self.bind("<FocusIn>", self._recheck_ffmpeg, add="+")

    # ── UI Construction ─────────────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=1)

        # ── Header ──
        hdr = tk.Frame(self, bg=SURFACE, pady=16)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.columnconfigure(0, weight=1)

        tk.Label(hdr, text="▶  YT Downloader", font=("Helvetica", 20, "bold"),
                 bg=SURFACE, fg=ACCENT).grid(row=0, column=0)
        tk.Label(hdr, text="Powered by yt-dlp", font=("Helvetica", 9),
                 bg=SURFACE, fg=TEXT_DIM).grid(row=1, column=0, pady=(2, 0))

        # ── Main card ──
        card = tk.Frame(self, bg=SURFACE, padx=28, pady=24,
                        highlightbackground=BORDER, highlightthickness=1)
        card.grid(row=1, column=0, sticky="nsew", padx=20, pady=16)
        card.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        row = 0

        # URL
        tk.Label(card, text="YouTube URL", font=("Helvetica", 11, "bold"),
                 bg=SURFACE, fg=TEXT).grid(row=row, column=0, columnspan=2,
                                           sticky="w", pady=(0, 6))
        row += 1

        url_frame = tk.Frame(card, bg=SURFACE2, highlightbackground=ACCENT,
                             highlightthickness=1)
        url_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 16))
        url_frame.columnconfigure(0, weight=1)

        self.url_entry = tk.Entry(url_frame, font=("Helvetica", 12),
                                  bg=SURFACE2, fg=TEXT, insertbackground=TEXT,
                                  relief="flat", bd=8)
        self.url_entry.grid(row=0, column=0, sticky="ew")
        self.url_entry.insert(0, "https://www.youtube.com/watch?v=...")
        self.url_entry.bind("<FocusIn>",  self._clear_placeholder)
        self.url_entry.bind("<FocusOut>", self._restore_placeholder)
        self.url_entry.bind("<Return>",   lambda e: self._start_download())

        btn_paste = tk.Button(url_frame, text="Paste", font=("Helvetica", 9),
                              bg=ACCENT, fg=TEXT, relief="flat", bd=0,
                              padx=10, cursor="hand2",
                              command=self._paste_url)
        btn_paste.grid(row=0, column=1, padx=(0, 2), pady=2)
        row += 1

        # Format
        tk.Label(card, text="Format", font=("Helvetica", 11, "bold"),
                 bg=SURFACE, fg=TEXT).grid(row=row, column=0, columnspan=2,
                                           sticky="w", pady=(0, 8))
        row += 1

        fmt_frame = tk.Frame(card, bg=SURFACE)
        fmt_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 16))

        formats = [
            ("Best MP4 (Video+Audio)", "mp4_best"),
            ("1080p MP4",              "1080p"),
            ("720p MP4",               "720p"),
            ("480p MP4",               "480p"),
            ("Audio Only (MP3)",       "mp3"),
            ("Audio Only (M4A)",       "m4a"),
        ]
        for i, (label, val) in enumerate(formats):
            col, rrow = divmod(i, 3)
            rb = tk.Radiobutton(fmt_frame, text=label, variable=self.format_var,
                                value=val, font=("Helvetica", 10),
                                bg=SURFACE, fg=TEXT_DIM, selectcolor=SURFACE2,
                                activebackground=SURFACE, activeforeground=TEXT,
                                cursor="hand2")
            rb.grid(row=rrow, column=col, sticky="w", padx=(0, 20), pady=2)
        # The radio buttons fill columns 0-1; column 2 is free.
        tk.Checkbutton(fmt_frame, text="Whole playlist",
                       variable=self.playlist_var, font=("Helvetica", 10),
                       bg=SURFACE, fg=TEXT_DIM, selectcolor=SURFACE2,
                       activebackground=SURFACE, activeforeground=TEXT,
                       cursor="hand2").grid(row=0, column=2, sticky="w", pady=2)
        row += 1

        # Output folder
        tk.Label(card, text="Save To", font=("Helvetica", 11, "bold"),
                 bg=SURFACE, fg=TEXT).grid(row=row, column=0, columnspan=2,
                                           sticky="w", pady=(0, 8))
        row += 1

        path_frame = tk.Frame(card, bg=SURFACE)
        path_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 20))
        path_frame.columnconfigure(0, weight=1)

        path_entry = tk.Entry(path_frame, textvariable=self.download_path,
                              font=("Helvetica", 10), bg=SURFACE2, fg=TEXT,
                              insertbackground=TEXT, relief="flat", bd=6)
        path_entry.grid(row=0, column=0, sticky="ew")

        tk.Button(path_frame, text="Browse", font=("Helvetica", 9),
                  bg=SURFACE2, fg=TEXT_DIM, relief="flat", bd=0,
                  padx=10, cursor="hand2",
                  command=self._browse_folder).grid(row=0, column=1, padx=(6, 0))
        row += 1

        # Download button
        self.dl_btn = tk.Button(card, text="⬇  Download",
                                font=("Helvetica", 13, "bold"),
                                bg=ACCENT, fg=TEXT, relief="flat", bd=0,
                                pady=12, cursor="hand2",
                                command=self._start_download,
                                activebackground=ACCENT_HV, activeforeground=TEXT)
        self.dl_btn.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        row += 1

        # Progress bar
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Red.Horizontal.TProgressbar",
                        troughcolor=SURFACE2, background=ACCENT,
                        bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)

        self.progress = ttk.Progressbar(card, variable=self.progress_var,
                                        maximum=100, length=400,
                                        style="Red.Horizontal.TProgressbar")
        self.progress.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        row += 1

        # Status label
        self.status_lbl = tk.Label(card, textvariable=self.status_var,
                                   font=("Helvetica", 10), bg=SURFACE, fg=TEXT_DIM,
                                   wraplength=580, justify="left")
        self.status_lbl.grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1

        # ── Log box ──
        log_frame = tk.Frame(self, bg=BG)
        log_frame.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 16))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)

        tk.Label(log_frame, text="Log", font=("Helvetica", 9, "bold"),
                 bg=BG, fg=TEXT_DIM).grid(row=0, column=0, sticky="w")

        self.log_box = tk.Text(log_frame, height=6, font=("Courier", 9),
                               bg=SURFACE2, fg=TEXT_DIM, relief="flat",
                               state="disabled", wrap="word")
        self.log_box.grid(row=1, column=0, sticky="nsew")

        sb = tk.Scrollbar(log_frame, command=self.log_box.yview, bg=SURFACE)
        sb.grid(row=1, column=1, sticky="ns")
        self.log_box.configure(yscrollcommand=sb.set)

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _clear_placeholder(self, _e):
        if self.url_entry.get().startswith("https://www.youtube.com/watch?v=..."):
            self.url_entry.delete(0, "end")
            self.url_entry.config(fg=TEXT)

    def _restore_placeholder(self, _e):
        if not self.url_entry.get().strip():
            self.url_entry.insert(0, "https://www.youtube.com/watch?v=...")
            self.url_entry.config(fg=TEXT_DIM)

    def _paste_url(self):
        try:
            clip = self.clipboard_get()
            self.url_entry.delete(0, "end")
            self.url_entry.insert(0, clip)
            self.url_entry.config(fg=TEXT)
        except Exception:
            pass

    def _browse_folder(self):
        path = filedialog.askdirectory(initialdir=self.download_path.get())
        if path:
            self.download_path.set(path)

    def _log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _set_status(self, msg, color=TEXT_DIM):
        self.status_var.set(msg)
        self.status_lbl.config(fg=color)

    # Tkinter is not thread-safe: worker threads must not touch widgets or
    # Tk variables. They _post() discrete events and overwrite
    # _latest_progress; the main thread applies both in _poll_ui_queue().
    def _post(self, func, *args):
        self._ui_queue.put((func, args))

    def _start_worker(self, target, *args):
        self._active_workers += 1
        if self._after_id is None:
            self._after_id = self.after(50, self._poll_ui_queue)
        threading.Thread(target=target, args=args, daemon=True).start()

    def _worker_finished(self):
        self._active_workers -= 1

    def _poll_ui_queue(self):
        self._after_id = None
        progress = self._latest_progress
        if progress is not None and progress is not self._shown_progress:
            self._shown_progress = progress
            self._set_progress(*progress)

        while not self._closing:
            try:
                func, args = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                func(*args)
            except Exception:
                self.report_callback_exception(*sys.exc_info())

        if not self._closing and (self._active_workers or not self._ui_queue.empty()):
            self._after_id = self.after(50, self._poll_ui_queue)

    def _on_close(self):
        self._closing = True
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
        self.destroy()

    def _set_progress(self, pct, msg, color):
        self.progress_var.set(pct)
        self._set_status(msg, color)

    # ── Download logic ───────────────────────────────────────────────────────

    def _get_ydl_opts(self, url, ffmpeg):
        fmt = self.format_var.get()
        out_dir = self.download_path.get()

        opts = {
            # Playlist entries go to "<playlist>/<NNN> - <title>.<ext>" (the
            # index keeps same-title videos apart); single videos to
            # "<title>.<ext>", as an empty folder name collapses.
            "outtmpl": os.path.join(out_dir, "%(playlist_title|)s",
                                    "%(playlist_index&{:03d} - |)s%(title)s.%(ext)s"),
            "progress_hooks": [self._progress_hook],
            # Only affects links to a video in a playlist (watch?v=…&list=…).
            "noplaylist": not self.playlist_var.get(),
            "quiet": True,
            "no_warnings": False,
            # Like the yt-dlp CLI: skip unavailable entries instead of stopping.
            "ignoreerrors": "only_download",
            "logger": _YdlLogger(self._entry_failed),
            # "auto" colors the ERROR: prefix when started from a terminal.
            "color": {"stdout": "never", "stderr": "never"},
        }

        opts.update(format_opts(fmt))
        opts["ffmpeg_location"] = ffmpeg

        return opts

    # Runs on the download thread. Raising here is how yt-dlp lets a caller
    # stop a download; it leaves the .part file, which Cancel then removes.
    def _check_canceled(self):
        if self._cancel.is_set():
            raise yt_dlp.utils.DownloadCancelled()

    # Runs on the download thread.
    def _progress_hook(self, d):
        if d["status"] == "downloading":
            # Only files this run is writing; already-downloaded files never
            # get a "downloading" event, so Cancel can't remove them.
            self._entry_files.update(
                p for p in (d.get("tmpfilename"), d.get("filename")) if p)
        self._check_canceled()
        streams = self._stream_count
        entry = d.get("info_dict") or {}
        index, count = entry.get("playlist_index"), entry.get("n_entries")
        video = f"Video {index}/{count} · " if index and count else ""
        if d["status"] == "downloading":
            total   = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            downloaded = d.get("downloaded_bytes", 0)
            speed   = d.get("speed", 0) or 0
            eta     = d.get("eta", 0) or 0

            pct = min(downloaded / total * 100, 100) if total else 0
            # Tops out at 99; only _download_done moves the bar to 100.
            pct = (min(self._stream_index, streams - 1) * 100 + pct) / streams * 0.99
            speed_str = f"{speed/1024/1024:.1f} MB/s" if speed > 0 else ""
            eta_str   = f"ETA {eta}s" if eta > 0 else ""
            info      = " · ".join(filter(None, [speed_str, eta_str]))

            self._latest_progress = (
                pct, f"{video}Downloading… {pct:.1f}%  {info}", WARNING)

        elif d["status"] == "finished":
            self._stream_index += 1
            if self._stream_index >= streams:
                self._latest_progress = (99, f"{video}Processing…", WARNING)

    # Runs on the download thread as an after_move post-processor: once per
    # video, with its final file (the progress hook only sees the temporary
    # per-stream files).
    def _file_done(self, info):
        self._entry_files.clear()   # finished: nothing for Cancel to remove
        path = info.get("filepath", "")
        if info.get("__real_download"):
            self._saved += 1
            self._post(self._log, f"✔ Saved: {path}")
        else:
            self._already += 1
            self._post(self._log, f"• Already downloaded: {path}")

    # Runs on the download thread (yt-dlp's logger).
    def _entry_failed(self, msg):
        msg = msg.removeprefix("ERROR: ")
        self._skipped.append(msg)
        self._post(self._log, f"✘ {msg}")

    # Runs on the download thread; blocks it until the user answers.
    def _ask_on_main(self, title, question):
        answer, done = [], threading.Event()

        def ask():
            answer.append(messagebox.askyesno(title, question))
            done.set()

        self._post(ask)
        while not done.wait(0.1):
            if self._closing:
                return False
        return answer[0]

    def _start_download(self):
        if not YT_DLP_AVAILABLE:
            self._prompt_install(); return

        ffmpeg = find_ffmpeg()
        self._ffmpeg_missing = not ffmpeg
        if self._ffmpeg_missing:
            messagebox.showerror("ffmpeg not found", ffmpeg_help())
            return

        url = self.url_entry.get().strip()
        if not url or url.startswith("https://www.youtube.com/watch?v=..."):
            messagebox.showwarning("No URL", "Please enter a YouTube URL.")
            return

        if self.is_running:
            messagebox.showinfo("Busy", "A download is already in progress.")
            return

        opts = self._get_ydl_opts(url, ffmpeg)
        self.is_running = True
        self.dl_btn.config(text="✖  Cancel", command=self._cancel_download)
        self.progress_var.set(0)
        self._set_status("Starting download…", WARNING)
        self._log(f"▶ {url}")

        self._stream_index = 0
        self._stream_count = 1
        self._saved = self._already = 0
        self._skipped = []
        self._cancel = threading.Event()
        self._entry_files = set()
        self._latest_progress = self._shown_progress = None
        self._start_worker(self._download_thread, url, opts,
                           not self.playlist_var.get())

    # The Download button turns into Cancel while a download runs. yt-dlp
    # stops at its next progress update, or before the next playlist video;
    # it can't stop ffmpeg mid-merge, so that video still finishes.
    def _cancel_download(self):
        self._cancel.set()
        # A progress update set just before must not overwrite "Canceling…".
        self._shown_progress = self._latest_progress
        self.dl_btn.config(state="disabled", text="Canceling…")
        self._set_status("Canceling…", WARNING)

    def _download_thread(self, url, opts, ask_playlist):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                base = yt_dlp.postprocessor.PostProcessor
                ydl.add_post_processor(_stream_counter_class(base)(self),
                                       when="before_dl")
                ydl.add_post_processor(_callback_pp_class(base)(self._file_done),
                                       when="after_move")
                if not ask_playlist:
                    ydl.download([url])
                elif not self._download_unless_declined(ydl, url):
                    self._post(self._download_declined)
                    return
            self._post(self._download_done, True, None)
        except Exception as exc:
            if self._cancel.is_set():
                self._post(self._download_canceled, self._remove_partial_files())
            else:
                self._post(self._download_done, False, str(exc))

    # Runs on the download thread after a cancel. Removes what this run wrote
    # for the unfinished video: its .part file, fragments, and any stream that
    # finished but wasn't merged yet. Videos that finished are kept.
    def _remove_partial_files(self):
        removed = 0
        for path in self._entry_files:
            for p in [path, path + ".ytdl", *glob.glob(glob.escape(path) + "-Frag*")]:
                try:
                    os.remove(p)
                    removed += 1
                except OSError:
                    pass
        return removed

    # With the box unticked, ask before downloading a playlist, channel or tab
    # (decided from what yt-dlp extracts, not from the URL).
    def _download_unless_declined(self, ydl, url):
        info = ydl.extract_info(url, download=False, process=False)
        target = info
        while target and target.get("_type") in ("url", "url_transparent"):
            target = ydl.extract_info(target["url"], download=False, process=False,
                                      ie_key=target.get("ie_key"))
        if target is None or info.get("_type") == "url":
            info = target   # what process_ie_result would extract again
        if info is None:
            return True     # the extraction error is already logged
        if target.get("_type") == "playlist":
            title = target.get("title") or "This link"
            if not self._ask_on_main(
                    "Playlist", f"\"{title}\" is a playlist or channel.\n\n"
                                "Download all its videos?"):
                return False
        ydl.process_ie_result(info, download=True)
        return True

    def _end_download(self):
        self._worker_finished()
        self.is_running = False
        self.dl_btn.config(state="normal", text="⬇  Download",
                           command=self._start_download)

    def _download_canceled(self, removed):
        self._end_download()
        self.progress_var.set(0)
        parts = [f"{self._saved} saved"] if self._saved else []
        if removed:
            parts.append(f"{removed} partial file{'s' * (removed != 1)} removed")
        detail = f" ({', '.join(parts)})" if parts else ""
        self._set_status(f"Download canceled{detail}.")
        self._log(f"✘ Download canceled{detail}.\n")

    def _download_declined(self):
        self._end_download()
        self.progress_var.set(0)
        self._set_status("Playlist not downloaded.")
        self._log("✘ Playlist not downloaded.\n")

    def _download_done(self, success, error):
        self._end_download()
        logged = False
        if success and self._skipped and not (self._saved or self._already):
            success, error, logged = False, self._skipped[0], True
        if success and (self._skipped or self._saved + self._already > 1):
            parts = [f"{self._saved} saved"]
            if self._already:
                parts.append(f"{self._already} already downloaded")
            if self._skipped:
                parts.append(f"{len(self._skipped)} skipped")
            summary = "Done: " + ", ".join(parts)
            self.progress_var.set(100)
            self._set_status(f"✔ {summary}", WARNING if self._skipped else SUCCESS)
            self._log(f"✔ {summary}" + chr(10))
            messagebox.showinfo("Done", summary)
        elif success:
            self.progress_var.set(100)
            self._set_status("✔ Download complete!", SUCCESS)
            self._log("✔ Done!\n")
            messagebox.showinfo("Done", "Download complete!")
        else:
            self.progress_var.set(0)
            self._set_status(f"✘ Error: {error}", ACCENT)
            if not logged:
                self._log(f"✘ Error: {error}\n")
            messagebox.showerror("Download Failed", str(error))

    def _recheck_ffmpeg(self, event=None):
        # The root's <FocusIn> binding also fires for each child widget.
        if not self._ffmpeg_missing or (event is not None and event.widget is not self):
            return
        ffmpeg = find_ffmpeg()
        if ffmpeg:
            self._ffmpeg_missing = False
            self._log(f"✔ ffmpeg found: {ffmpeg}")
            if self.status_var.get() == FFMPEG_MISSING_STATUS:
                self._set_status("Ready")

    # ── Install prompt ───────────────────────────────────────────────────────

    def _prompt_install(self):
        ans = messagebox.askyesno(
            "yt-dlp not found",
            "yt-dlp is required but not installed.\n\n"
            "Install it now with pip?\n\n"
            f"(You can also run:  pip install {YT_DLP_REQUIREMENT})"
        )
        if ans:
            self._install_ytdlp()

    def _install_ytdlp(self):
        self._set_status("Installing yt-dlp…", WARNING)
        self._log("Installing yt-dlp via pip…")

        def run():
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", YT_DLP_REQUIREMENT],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                self._post(self._install_done, True)
            except Exception as e:
                self._post(self._install_done, False, str(e))

        self._start_worker(run)

    def _install_done(self, ok, err=None):
        self._worker_finished()
        if ok:
            self._log("✔ yt-dlp installed! Please restart the app.")
            self._set_status("✔ yt-dlp installed — please restart.", SUCCESS)
            messagebox.showinfo("Installed",
                                "yt-dlp was installed successfully.\n"
                                "Please restart the application.")
        else:
            self._log(f"✘ Install failed: {err}")
            self._set_status(f"Install failed. Try: pip install {YT_DLP_REQUIREMENT}",
                             ACCENT)


# ── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = YouTubeDownloader()
    app.mainloop()
