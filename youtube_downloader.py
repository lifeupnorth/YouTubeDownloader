#!/usr/bin/env python3
"""
YouTube Video Downloader — Desktop App

This file is the window. The downloading itself is in download_engine.py,
which doesn't use Tk.
Requirements: pip install -r requirements.txt
"""

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import download_engine as engine

# Optional: thumbnails in the link preview need Pillow (see download_engine).
try:
    from PIL import ImageTk
except ImportError:
    ImageTk = None


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

# Helvetica falls back to Arial on Windows, so use the system UI font there.
UI_FONT   = "Segoe UI" if sys.platform == "win32" else "Helvetica"
MONO_FONT = "Consolas" if sys.platform == "win32" else "Courier"

URL_PLACEHOLDER = "https://www.youtube.com/watch?v=..."


FFMPEG_MISSING_STATUS = "ffmpeg not found, see the log below"

# The Format radio buttons: (label, value for format_opts).
FORMATS = [
    ("Best MP4 (Video+Audio)", "mp4_best"),
    ("1080p MP4",              "1080p"),
    ("720p MP4",               "720p"),
    ("480p MP4",               "480p"),
    ("Audio Only (MP3)",       "mp3"),
    ("Audio Only (M4A)",       "m4a"),
]


def settings_path():
    """Where the last folder and format are remembered between runs."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "YT Downloader", "settings.json")


def load_settings():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):   # missing, unreadable or not JSON
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(data):
    path = settings_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Write a temporary file first so a crash can't leave half a file.
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(path + ".tmp", path)
    except OSError:
        pass   # not being able to remember settings isn't worth an error


PREVIEW_HINT = "Paste a link to see its title and length here."


def reveal_in_file_manager(path):
    """Open the folder holding path, with the file selected where possible."""
    folder = os.path.dirname(path)
    if sys.platform == "win32":
        if os.path.isfile(path):
            # Windows paths can't contain quotes, so this quoting is safe.
            subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
        else:
            os.startfile(folder)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path] if os.path.isfile(path)
                         else ["open", folder])
    else:
        subprocess.Popen(["xdg-open", folder])


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


class YouTubeDownloader(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YT Downloader")
        self.configure(bg=BG)

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
        self._queue = None             # the running engine.DownloadQueue
        self._close_when_stopped = False
        self._placeholder_shown = False
        self._last_file = None
        self.preview_text  = tk.StringVar(value=PREVIEW_HINT)
        self._preview_after = None     # pending lookup (waits for typing to pause)
        self._preview_key   = None     # (links, whole playlist) last looked up
        self._preview_token = 0        # newest lookup; older answers are dropped
        self._preview_image = None     # keeps the PhotoImage alive
        self._apply_saved_settings()

        self._build_ui()
        self._fit_window()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if not engine.YT_DLP_AVAILABLE:
            self.after(300, self._prompt_install)
        self._ffmpeg_missing = not engine.find_ffmpeg()
        if self._ffmpeg_missing:
            self._set_status(FFMPEG_MISSING_STATUS, WARNING)
            self._log(ffmpeg_help(startup=True))
        self._note_yt_dlp_version()
        self.bind("<FocusIn>", self._recheck_ffmpeg, add="+")

    # ── UI Construction ─────────────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=1)

        # ── Header ──
        hdr = tk.Frame(self, bg=SURFACE, pady=16)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.columnconfigure(0, weight=1)

        tk.Label(hdr, text="▶  YT Downloader", font=(UI_FONT, 20, "bold"),
                 bg=SURFACE, fg=ACCENT).grid(row=0, column=0)
        self.powered_lbl = tk.Label(hdr, text=self._powered_by_text(),
                                    font=(UI_FONT, 9), bg=SURFACE, fg=TEXT_DIM)
        self.powered_lbl.grid(row=1, column=0, pady=(2, 0))

        # ── Main card ──
        card = tk.Frame(self, bg=SURFACE, padx=28, pady=24,
                        highlightbackground=BORDER, highlightthickness=1)
        card.grid(row=1, column=0, sticky="nsew", padx=20, pady=16)
        card.columnconfigure(1, weight=1)

        row = 0

        # URL
        tk.Label(card, text="YouTube URLs (one per line)", font=(UI_FONT, 11, "bold"),
                 bg=SURFACE, fg=TEXT).grid(row=row, column=0, columnspan=2,
                                           sticky="w", pady=(0, 6))
        row += 1

        url_frame = tk.Frame(card, bg=SURFACE2, highlightbackground=ACCENT,
                             highlightthickness=1)
        url_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        url_frame.columnconfigure(0, weight=1)

        # Several links can go in, one per line; they download in order. The
        # box grows to 4 lines. Enter downloads, Shift+Enter adds a line.
        self.url_entry = tk.Text(url_frame, height=1, wrap="none",
                                 font=(UI_FONT, 12), bg=SURFACE2, fg=TEXT,
                                 insertbackground=TEXT, relief="flat", bd=0,
                                 padx=8, pady=8, highlightthickness=0, undo=True)
        self.url_entry.grid(row=0, column=0, sticky="ew")
        self._restore_placeholder()
        self.url_entry.bind("<FocusIn>",  self._clear_placeholder)
        self.url_entry.bind("<FocusOut>", self._restore_placeholder)
        self.url_entry.bind("<FocusOut>", self._schedule_preview, add="+")
        self.url_entry.bind("<KeyRelease>", self._on_url_typed)
        self.url_entry.bind("<Return>", self._on_url_enter)
        self.url_entry.bind("<Shift-Return>", lambda e: None)   # a new line
        self.url_entry.bind("<Tab>", self._focus_next)

        btn_paste = tk.Button(url_frame, text="Paste", font=(UI_FONT, 9),
                              bg=ACCENT, fg=TEXT, relief="flat", bd=0,
                              padx=10, cursor="hand2",
                              command=self._paste_url)
        btn_paste.grid(row=0, column=1, padx=(0, 2), pady=2)
        row += 1

        # Preview of the link: thumbnail, title, length and channel
        preview = tk.Frame(card, bg=SURFACE)
        preview.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        preview.columnconfigure(1, weight=1)
        thumb_box = tk.Frame(preview, bg=SURFACE2, width=engine.THUMB_SIZE[0],
                             height=engine.THUMB_SIZE[1])
        thumb_box.grid(row=0, column=0, sticky="nw")   # fixed size, image or not
        self.preview_img = tk.Label(thumb_box, bg=SURFACE2, bd=0)
        self.preview_img.place(relx=0.5, rely=0.5, anchor="center")
        self.preview_lbl = tk.Label(preview, textvariable=self.preview_text,
                                    font=(UI_FONT, 10), bg=SURFACE, fg=TEXT_DIM,
                                    justify="left", anchor="nw", wraplength=400)
        self.preview_lbl.grid(row=0, column=1, sticky="new", padx=(12, 0))
        self.preview_lbl.bind("<Configure>", lambda e: self.preview_lbl.config(
            wraplength=max(e.width - 4, 100)))
        row += 1

        # Format
        tk.Label(card, text="Format", font=(UI_FONT, 11, "bold"),
                 bg=SURFACE, fg=TEXT).grid(row=row, column=0, columnspan=2,
                                           sticky="w", pady=(0, 8))
        row += 1

        fmt_frame = tk.Frame(card, bg=SURFACE)
        fmt_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 16))

        for i, (label, val) in enumerate(FORMATS):
            col, rrow = divmod(i, 3)
            rb = tk.Radiobutton(fmt_frame, text=label, variable=self.format_var,
                                value=val, font=(UI_FONT, 10),
                                bg=SURFACE, fg=TEXT_DIM, selectcolor=SURFACE2,
                                activebackground=SURFACE, activeforeground=TEXT,
                                cursor="hand2")
            rb.grid(row=rrow, column=col, sticky="w", padx=(0, 20), pady=2)
        # The radio buttons fill columns 0-1; column 2 is free.
        tk.Checkbutton(fmt_frame, text="Whole playlist",
                       variable=self.playlist_var, font=(UI_FONT, 10),
                       bg=SURFACE, fg=TEXT_DIM, selectcolor=SURFACE2,
                       activebackground=SURFACE, activeforeground=TEXT,
                       cursor="hand2", command=self._schedule_preview,
                       ).grid(row=0, column=2, sticky="w", pady=2)
        row += 1

        # Output folder
        tk.Label(card, text="Save To", font=(UI_FONT, 11, "bold"),
                 bg=SURFACE, fg=TEXT).grid(row=row, column=0, columnspan=2,
                                           sticky="w", pady=(0, 8))
        row += 1

        path_frame = tk.Frame(card, bg=SURFACE)
        path_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 20))
        path_frame.columnconfigure(0, weight=1)

        path_entry = tk.Entry(path_frame, textvariable=self.download_path,
                              font=(UI_FONT, 10), bg=SURFACE2, fg=TEXT,
                              insertbackground=TEXT, relief="flat", bd=6)
        path_entry.grid(row=0, column=0, sticky="ew")

        tk.Button(path_frame, text="Browse", font=(UI_FONT, 9),
                  bg=SURFACE2, fg=TEXT_DIM, relief="flat", bd=0,
                  padx=10, cursor="hand2",
                  command=self._browse_folder).grid(row=0, column=1, padx=(6, 0))
        row += 1

        # Download button
        self.dl_btn = tk.Button(card, text="⬇  Download",
                                font=(UI_FONT, 13, "bold"),
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

        # Status label, with an "Open folder" button after a download
        status_row = tk.Frame(card, bg=SURFACE)
        status_row.grid(row=row, column=0, columnspan=2, sticky="ew")
        status_row.columnconfigure(0, weight=1)
        self.status_lbl = tk.Label(status_row, textvariable=self.status_var,
                                   font=(UI_FONT, 10), bg=SURFACE, fg=TEXT_DIM,
                                   wraplength=580, justify="left", anchor="w")
        self.status_lbl.grid(row=0, column=0, sticky="ew")
        self.open_btn = tk.Button(status_row, text="📂  Open folder",
                                  font=(UI_FONT, 9), bg=SURFACE2, fg=TEXT,
                                  relief="flat", bd=0, padx=10, cursor="hand2",
                                  activebackground=BORDER, activeforeground=TEXT,
                                  command=self._open_folder)
        self.open_btn.grid(row=0, column=1, sticky="ne", padx=(8, 0))
        self.open_btn.grid_remove()   # shown once a download has saved a file
        # Wrap long messages at the label's current width, not a fixed one.
        self.status_lbl.bind("<Configure>", lambda e: self.status_lbl.config(
            wraplength=max(e.width - 4, 100)))
        row += 1

        # ── Log box ──
        log_frame = tk.Frame(self, bg=BG)
        log_frame.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 16))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)

        tk.Label(log_frame, text="Log", font=(UI_FONT, 9, "bold"),
                 bg=BG, fg=TEXT_DIM).grid(row=0, column=0, sticky="w")

        self.log_box = tk.Text(log_frame, height=6, font=(MONO_FONT, 9),
                               bg=SURFACE2, fg=TEXT_DIM, relief="flat",
                               state="disabled", wrap="word")
        self.log_box.grid(row=1, column=0, sticky="nsew")

        sb = tk.Scrollbar(log_frame, command=self.log_box.yview, bg=SURFACE)
        sb.grid(row=1, column=1, sticky="ns")
        self.log_box.configure(yscrollcommand=sb.set)

    # The last folder and format are remembered (when a download starts and
    # when the app closes). The "Whole playlist" box always starts unticked.
    def _apply_saved_settings(self):
        saved = load_settings()
        if saved.get("format") in {value for _, value in FORMATS}:
            self.format_var.set(saved["format"])
        folder = saved.get("folder")
        if isinstance(folder, str) and os.path.isdir(folder):
            self.download_path.set(folder)

    def _save_settings(self):
        save_settings({"folder": self.download_path.get(),
                       "format": self.format_var.get()})

    def _fit_window(self):
        # Open tall enough to show everything (but no taller than the screen).
        # Only the log grows and shrinks with the window, and the minimum
        # height keeps the controls, the status line and 2 log lines visible
        # (plus one line for a status message that wraps).
        self.update_idletasks()
        wanted = self.winfo_reqheight()
        log_height = self.log_box.winfo_reqheight()
        line = log_height // int(self.log_box.cget("height"))
        height = min(wanted, self.winfo_screenheight() - 80)
        self.minsize(580, min(wanted - log_height + 3 * line, height))
        self.geometry(f"680x{height}")

    # ── Helpers ─────────────────────────────────────────────────────────────

    # The URL box shows a dimmed example until the user types or pastes.
    def _clear_placeholder(self, _e=None):
        if self._placeholder_shown:
            self._placeholder_shown = False
            self._replace_url_text("")

    def _restore_placeholder(self, _e=None):
        if not self._url_text().strip():
            self._replace_url_text(URL_PLACEHOLDER)
            self.url_entry.config(fg=TEXT_DIM)
            self._placeholder_shown = True

    def _url_text(self):
        return self.url_entry.get("1.0", "end-1c")

    def _replace_url_text(self, text):
        self.url_entry.delete("1.0", "end")
        self.url_entry.insert("1.0", text)
        self.url_entry.config(fg=TEXT)
        self._fit_url_box()

    def _urls(self):
        """The links entered (one per line, or separated by spaces) in order
        and without repeats; none while the placeholder is showing."""
        text = self._url_text().strip()
        if self._placeholder_shown and text == URL_PLACEHOLDER:
            return []
        return list(dict.fromkeys(text.split()))

    def _url(self):
        """The first link, or "" if there's none."""
        urls = self._urls()
        return urls[0] if urls else ""

    def _fit_url_box(self):
        lines = int(self.url_entry.index("end-1c").split(".")[0])
        self.url_entry.config(height=min(max(lines, 1), 4))

    def _on_url_typed(self, _e=None):
        self._fit_url_box()
        self._schedule_preview()

    def _on_url_enter(self, _e=None):
        self._start_download()
        return "break"   # no new line

    def _focus_next(self, event):
        event.widget.tk_focusNext().focus()
        return "break"   # no tab character

    def _paste_url(self):
        try:
            clip = self.clipboard_get()
        except tk.TclError:   # empty clipboard, or not text
            return
        self._placeholder_shown = False
        self._replace_url_text("\n".join(clip.split()))   # one link per line
        self._schedule_preview(delay=100)

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
        # During a download, ask first, then cancel it and close once it has
        # stopped, so its partial files are removed. Closing again while that
        # runs (e.g. ffmpeg can't be interrupted) closes right away.
        if self.is_running and not self._close_when_stopped:
            if not messagebox.askyesno(
                    "Download in progress",
                    "A download is still running.\n\n"
                    "Cancel it and close the app?"):
                return
            self._close_when_stopped = True
            if not self._queue.canceled:
                self._cancel_download()
            self._set_status("Canceling… the app closes when it has stopped.",
                             WARNING)
            return
        self._save_settings()
        self._closing = True
        for pending in (self._after_id, self._preview_after):
            if pending is not None:
                self.after_cancel(pending)
        self._after_id = self._preview_after = None
        self.destroy()

    def _set_progress(self, pct, msg, color):
        self.progress_var.set(pct)
        self._set_status(msg, color)

    # ── Downloading (the work itself is engine.DownloadQueue) ───────────────

    def _start_download(self):
        if not engine.YT_DLP_AVAILABLE:
            self._prompt_install()
            return

        ffmpeg = engine.find_ffmpeg()
        self._ffmpeg_missing = not ffmpeg
        if self._ffmpeg_missing:
            messagebox.showerror("ffmpeg not found", ffmpeg_help())
            return

        urls = self._urls()
        if not urls:
            messagebox.showwarning("No URL", "Please enter a YouTube URL.")
            return

        if self.is_running:
            messagebox.showinfo("Busy", "A download is already in progress.")
            return

        # Tk variables are read here, on the main thread; the queue gets values.
        self._queue = engine.DownloadQueue(
            urls, fmt=self.format_var.get(), folder=self.download_path.get(),
            ffmpeg=ffmpeg, whole_playlist=self.playlist_var.get(),
            on_progress=self._job_progress, on_log=self._job_log,
            ask=self._job_ask)
        self._save_settings()
        self.is_running = True
        self.dl_btn.config(text="✖  Cancel", command=self._cancel_download)
        self.progress_var.set(0)
        self._set_status("Starting download…", WARNING)
        self._log(f"▶ {urls[0]}" if len(urls) == 1 else f"▶ {len(urls)} links")
        self._latest_progress = self._shown_progress = None
        self._last_file = None
        self.open_btn.grid_remove()
        self._start_worker(self._run_queue, self._queue)

    # The queue's callbacks run on the download thread: they only hand things
    # to the main thread (see _post and _poll_ui_queue).
    def _job_progress(self, pct, message):
        self._latest_progress = (pct, message, WARNING)

    def _job_log(self, message):
        self._post(self._log, message)

    def _job_ask(self, question):
        return self._ask_on_main("Playlist", question)

    # Runs on the download thread.
    def _run_queue(self, queue):
        queue.run()
        self._post(self._download_finished, queue)

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

    # The Download button turns into Cancel while a download runs. yt-dlp
    # stops at its next progress update, or before the next playlist video;
    # it can't stop ffmpeg mid-merge, so that video still finishes.
    def _cancel_download(self):
        self._queue.cancel()
        # A progress update set just before must not overwrite "Canceling…".
        self._shown_progress = self._latest_progress
        self.dl_btn.config(state="disabled", text="Canceling…")
        self._set_status("Canceling…", WARNING)

    def _end_download(self):
        self._worker_finished()
        self.is_running = False
        self.dl_btn.config(state="normal", text="⬇  Download",
                           command=self._start_download)

    def _download_finished(self, job):   # job: the DownloadQueue
        self._end_download()
        if self._close_when_stopped:   # the user is closing the app
            return self._on_close()
        self._last_file = job.last_file
        summary = job.summary()
        if job.outcome == "failed":
            self.progress_var.set(0)
            self._set_status(f"✘ {summary}", ACCENT)
            if not job.error_logged:
                self._log(f"✘ {summary}\n")
            messagebox.showerror("Download Failed", str(job.error))
        elif job.outcome in ("canceled", "declined"):
            self.progress_var.set(0)
            self._set_status(summary)
            self._log(f"✘ {summary}\n")
            self._offer_open_folder()
        else:
            self.progress_var.set(100)
            self._set_status(f"✔ {summary}", WARNING if job.had_problems else SUCCESS)
            self._log("✔ Done!\n" if summary == "Download complete!"
                      else f"✔ {summary}\n")
            self._offer_open_folder()

    # Instead of a "Done" pop-up after every download, the status line says
    # how it went and offers a button to open the folder.
    def _offer_open_folder(self):
        if self._last_file:
            self.open_btn.grid()

    def _open_folder(self):
        path = self._last_file
        if not path or not os.path.isdir(os.path.dirname(path)):
            messagebox.showerror("Folder not found",
                                 "The download folder doesn't exist anymore.")
            return
        try:
            reveal_in_file_manager(path)
        except OSError as exc:
            messagebox.showerror("Couldn't open the folder", str(exc))

    # ── Link preview ─────────────────────────────────────────────────────────

    # Waits until typing pauses, so not every keystroke starts a lookup.
    def _schedule_preview(self, _e=None, delay=600):
        if self._preview_after is not None:
            self.after_cancel(self._preview_after)
        self._preview_after = self.after(delay, self._start_preview)

    def _start_preview(self):
        self._preview_after = None
        urls = self._urls()
        url = urls[0] if urls else ""
        key = (tuple(urls), self.playlist_var.get())
        if key == self._preview_key:
            return
        self._preview_key = key
        self._preview_token += 1
        if not url:
            return self._set_preview(PREVIEW_HINT, dim=True)
        if not url.startswith(("https://", "http://")) or not engine.YT_DLP_AVAILABLE:
            return self._set_preview("No preview for this link.", dim=True)
        self._set_preview("Loading preview…", dim=True)
        self._start_worker(self._preview_thread, url, self.playlist_var.get(),
                           self._preview_token, len(urls) - 1)

    # Runs on a worker thread.
    def _preview_thread(self, url, whole_playlist, token, more):
        text, image, found = engine.preview_link(url, whole_playlist)
        if more:   # several links: the preview shows the first one
            text += f"\n+ {more} more link{'s' * (more != 1)}"
        self._post(self._preview_done, text, image, found, token)

    def _preview_done(self, text, image, found, token):
        self._worker_finished()
        if token == self._preview_token:   # drop answers for an older link
            self._set_preview(text, image, dim=not found)

    def _set_preview(self, text, image=None, dim=False):
        self.preview_text.set(text)
        self.preview_lbl.config(fg=TEXT_DIM if dim else TEXT)
        self._preview_image = ImageTk.PhotoImage(image) if image is not None else None
        self.preview_img.config(image=self._preview_image or "")

    def _recheck_ffmpeg(self, event=None):
        # The root's <FocusIn> binding also fires for each child widget.
        if not self._ffmpeg_missing or (event is not None and event.widget is not self):
            return
        ffmpeg = engine.find_ffmpeg()
        if ffmpeg:
            self._ffmpeg_missing = False
            self._log(f"✔ ffmpeg found: {ffmpeg}")
            if self.status_var.get() == FFMPEG_MISSING_STATUS:
                self._set_status("Ready")

    # ── yt-dlp version and install ───────────────────────────────────────────

    def _powered_by_text(self):
        version = engine.yt_dlp_version()
        return f"Powered by yt-dlp {version}" if version else \
            "Powered by yt-dlp (not installed)"

    # The app is tested with the version pinned in requirements.txt; say so
    # if another one is installed (yt-dlp often needs updates for YouTube).
    def _note_yt_dlp_version(self):
        installed, pinned = engine.yt_dlp_version(), engine.pinned_yt_dlp_version()
        if installed and pinned and not engine.same_version(installed, pinned):
            self._log(f"• yt-dlp {installed} is installed; this app is tested "
                      f"with {pinned}. To match: pip install {engine.YT_DLP_REQUIREMENT}")

    def _prompt_install(self):
        if getattr(sys, "frozen", False):
            # The .exe has yt-dlp built in and no pip to install it with.
            messagebox.showerror(
                "yt-dlp not found",
                "This copy of the app is missing yt-dlp. Please download it again.")
            return
        ans = messagebox.askyesno(
            "yt-dlp not found",
            "yt-dlp is required but not installed.\n\n"
            "Install it now with pip?\n\n"
            f"(You can also run:  pip install {engine.YT_DLP_REQUIREMENT})"
        )
        if ans:
            self._install_ytdlp()

    def _install_ytdlp(self):
        self._set_status("Installing yt-dlp…", WARNING)
        self._log("Installing yt-dlp via pip…")

        def run():
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", engine.YT_DLP_REQUIREMENT],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except Exception as e:
                self._post(self._install_done, False, str(e))
                return
            # Importing here, on the worker, keeps the window responsive.
            self._post(self._install_done, True, None, engine.load_yt_dlp())

        self._start_worker(run)

    def _install_done(self, ok, err=None, loaded=False):
        self._worker_finished()
        if ok and loaded:
            self.powered_lbl.config(text=self._powered_by_text())
            self._log(f"✔ yt-dlp {engine.yt_dlp_version()} installed and ready.")
            self._set_status("✔ yt-dlp installed. You can download now.", SUCCESS)
        elif ok:   # installed, but this Python can't import it until a restart
            self._log("✔ yt-dlp installed! Please restart the app.")
            self._set_status("✔ yt-dlp installed — please restart.", SUCCESS)
            messagebox.showinfo("Installed",
                                "yt-dlp was installed successfully.\n"
                                "Please restart the application.")
        else:
            self._log(f"✘ Install failed: {err}")
            self._set_status(f"Install failed. Try: pip install {engine.YT_DLP_REQUIREMENT}",
                             ACCENT)


def self_check(path):
    """Writes what this build found to a JSON file, then exits. Used to test
    the one-file .exe (packaging/build_exe.py --check), where there's no
    console to print to."""
    ffmpeg = engine.find_ffmpeg()
    ffmpeg_version = None
    if ffmpeg:
        run = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ffmpeg_version = run.stdout.splitlines()[0] if run.stdout else None
    result = {"frozen": bool(getattr(sys, "frozen", False)),
              "yt_dlp": engine.yt_dlp_version(),
              "yt_dlp_pin": engine.pinned_yt_dlp_version(),
              "ffmpeg": ffmpeg, "ffmpeg_bundled": engine.bundled_ffmpeg() is not None,
              "ffmpeg_version": ffmpeg_version,
              "thumbnails": ImageTk is not None and engine.Image is not None}
    try:
        app = YouTubeDownloader()   # also checks that Tk works in this build
        app.update()
        result["window"] = app.title()
        app.destroy()
    except tk.TclError as exc:
        result["window"] = f"failed: {exc}"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


# ── Entry point ─────────────────────────────────────────────────────────────
def smoke_download(url, fmt, folder, report_path):
    """--smoke-download: one real download without the window, for the nightly
    smoke test (packaging/smoke_test.py); writes the outcome to report_path."""
    log = []
    job = engine.DownloadJob(url, fmt=fmt, folder=folder, ffmpeg=engine.find_ffmpeg(),
                             whole_playlist=False, on_progress=lambda pct, msg: None,
                             on_log=log.append, ask=lambda question: False)
    try:
        job.run()
    except Exception as exc:   # still write the report, with the reason
        job.outcome, job.error = "failed", f"{type(exc).__name__}: {exc}"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({"outcome": job.outcome, "error": job.error,
                   "file": job.last_file, "log": log}, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-check":
        self_check(sys.argv[2])
    elif len(sys.argv) == 6 and sys.argv[1] == "--smoke-download":
        smoke_download(*sys.argv[2:])
    else:
        app = YouTubeDownloader()
        app.mainloop()
