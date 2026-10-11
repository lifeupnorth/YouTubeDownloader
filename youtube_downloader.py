#!/usr/bin/env python3
"""
YouTube Video Downloader — Desktop App
Requirements: pip install yt-dlp
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import queue
import functools
import os
import sys
import subprocess

# ── Try importing yt-dlp; offer to install if missing ──────────────────────
try:
    import yt_dlp
    YT_DLP_AVAILABLE = True
except ImportError:
    YT_DLP_AVAILABLE = False


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
            self._app._stream_index = 0
            self._app._stream_count = len(info.get("requested_formats") or ()) or 1
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

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if not YT_DLP_AVAILABLE:
            self.after(300, self._prompt_install)

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
                                cursor="hand2",
                                command=self._on_format_change)
            rb.grid(row=rrow, column=col, sticky="w", padx=(0, 20), pady=2)
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

    def _on_format_change(self):
        pass

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

    def _get_ydl_opts(self, url):
        fmt = self.format_var.get()
        out_dir = self.download_path.get()
        outtmpl = os.path.join(out_dir, "%(title)s.%(ext)s")

        opts = {
            "outtmpl": outtmpl,
            "progress_hooks": [self._progress_hook],
            "quiet": True,
            "no_warnings": False,
        }

        if fmt == "mp4_best":
            opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            opts["merge_output_format"] = "mp4"
        elif fmt == "1080p":
            opts["format"] = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]"
            opts["merge_output_format"] = "mp4"
        elif fmt == "720p":
            opts["format"] = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]"
            opts["merge_output_format"] = "mp4"
        elif fmt == "480p":
            opts["format"] = "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]"
            opts["merge_output_format"] = "mp4"
        elif fmt == "mp3":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        elif fmt == "m4a":
            opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
            }]

        return opts

    # Runs on the download thread.
    def _progress_hook(self, d):
        streams = self._stream_count
        if d["status"] == "downloading":
            total   = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            downloaded = d.get("downloaded_bytes", 0)
            speed   = d.get("speed", 0) or 0
            eta     = d.get("eta", 0) or 0

            pct = min(downloaded / total * 100, 100) if total else 0
            pct = (min(self._stream_index, streams - 1) * 100 + pct) / streams
            speed_str = f"{speed/1024/1024:.1f} MB/s" if speed > 0 else ""
            eta_str   = f"ETA {eta}s" if eta > 0 else ""
            info      = " · ".join(filter(None, [speed_str, eta_str]))

            self._latest_progress = (pct, f"Downloading… {pct:.1f}%  {info}",
                                     WARNING)

        elif d["status"] == "finished":
            self._stream_index += 1
            if self._stream_index >= streams:
                self._latest_progress = (100, "Processing…", WARNING)
            self._post(self._log, f"✔ Saved: {d.get('filename', '')}")

    def _start_download(self):
        if not YT_DLP_AVAILABLE:
            self._prompt_install(); return

        url = self.url_entry.get().strip()
        if not url or url.startswith("https://www.youtube.com/watch?v=..."):
            messagebox.showwarning("No URL", "Please enter a YouTube URL.")
            return

        if self.is_running:
            messagebox.showinfo("Busy", "A download is already in progress.")
            return

        opts = self._get_ydl_opts(url)
        self.is_running = True
        self.dl_btn.config(state="disabled", text="Downloading…")
        self.progress_var.set(0)
        self._set_status("Starting download…", WARNING)
        self._log(f"▶ {url}")

        self._stream_index = 0
        self._stream_count = 1
        self._latest_progress = self._shown_progress = None
        self._start_worker(self._download_thread, url, opts)

    def _download_thread(self, url, opts):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                counter = _stream_counter_class(yt_dlp.postprocessor.PostProcessor)(self)
                ydl.add_post_processor(counter, when="before_dl")
                ydl.download([url])
            self._post(self._download_done, True, None)
        except Exception as exc:
            self._post(self._download_done, False, str(exc))

    def _download_done(self, success, error):
        self._worker_finished()
        self.is_running = False
        self.dl_btn.config(state="normal", text="⬇  Download")

        if success:
            self.progress_var.set(100)
            self._set_status("✔ Download complete!", SUCCESS)
            self._log("✔ Done!\n")
            messagebox.showinfo("Done", "Download complete!")
        else:
            self.progress_var.set(0)
            self._set_status(f"✘ Error: {error}", ACCENT)
            self._log(f"✘ Error: {error}\n")
            messagebox.showerror("Download Failed", str(error))

    # ── Install prompt ───────────────────────────────────────────────────────

    def _prompt_install(self):
        ans = messagebox.askyesno(
            "yt-dlp not found",
            "yt-dlp is required but not installed.\n\n"
            "Install it now with pip?\n\n"
            "(You can also run:  pip install yt-dlp)"
        )
        if ans:
            self._install_ytdlp()

    def _install_ytdlp(self):
        self._set_status("Installing yt-dlp…", WARNING)
        self._log("Installing yt-dlp via pip…")

        def run():
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "yt-dlp"],
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
            self._set_status("Install failed. Try: pip install yt-dlp", ACCENT)


# ── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = YouTubeDownloader()
    app.mainloop()
