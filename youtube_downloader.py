#!/usr/bin/env python3
"""
YouTube Video Downloader — Desktop App

This file is the window. The downloading itself is in download_engine.py,
which doesn't use Tk.
Requirements: pip install -r requirements.txt
"""

import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

import download_engine as engine

# Optional: Pillow draws the rounded buttons and the thumbnails in the link
# preview (see download_engine). Without it buttons are plain rectangles and
# previews show text only.
try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    Image = ImageDraw = ImageTk = None


# ── Themes ──────────────────────────────────────────────────────────────────
# YouTube's own colors, dark and light. Widgets get roles ("text", "field"…)
# rather than colors, so a theme change can repaint them all (see _paint and
# _apply_theme); status messages name a role too.
THEMES = {
    "dark": {
        "bg":           "#0f0f0f",
        "surface":      "#1f1f1f",   # the log, an empty thumbnail
        "field":        "#121212",   # text boxes
        "field_border": "#303030",
        "blue":         "#3ea6ff",   # the text box being typed in, a switch that's on
        "button":       "#272727",   # secondary buttons and chips
        "button_hover": "#3f3f3f",
        "chip_on":      "#f1f1f1",   # the selected chip
        "chip_on_text": "#0f0f0f",
        "accent":       "#ff0033",   # Download, the progress bar, the logo
        "accent_hover": "#d9002b",
        "accent_text":  "#ffffff",
        "divider":      "#303030",
        "scroll":       "#4d4d4d",   # the log's scroll bar, a switch that's off
        "text":         "#f1f1f1",
        "text_dim":     "#aaaaaa",
        "disabled":     "#717171",
        "success":      "#2ba640",
        "warning":      "#f59e0b",
        "error":        "#ff4e45",
    },
    "light": {
        "bg":           "#ffffff",
        "surface":      "#f2f2f2",
        "field":        "#ffffff",
        "field_border": "#d3d3d3",
        "blue":         "#065fd4",
        "button":       "#f2f2f2",
        "button_hover": "#e5e5e5",
        "chip_on":      "#0f0f0f",
        "chip_on_text": "#ffffff",
        "accent":       "#ff0033",
        "accent_hover": "#d9002b",
        "accent_text":  "#ffffff",
        "divider":      "#e5e5e5",
        "scroll":       "#c1c1c1",
        "text":         "#0f0f0f",
        "text_dim":     "#606060",
        "disabled":     "#909090",
        "success":      "#107c41",
        "warning":      "#b45309",
        "error":        "#cc0000",
    },
}

# The Theme chips: (label, value of theme_var). "system" follows the OS.
THEME_MODES = [("System", "system"), ("Light", "light"), ("Dark", "dark")]

# Colors for the status line and the preview, as theme roles.
TEXT     = "text"
TEXT_DIM = "text_dim"
SUCCESS  = "success"
WARNING  = "warning"
ERROR    = "error"

# Helvetica falls back to Arial on Windows, so use the system UI font there.
UI_FONT   = "Segoe UI" if sys.platform == "win32" else "Helvetica"
MONO_FONT = "Consolas" if sys.platform == "win32" else "Courier"

URL_PLACEHOLDER = "https://www.youtube.com/watch?v=..."


FFMPEG_MISSING_STATUS = "ffmpeg not found, see the log below"

# The Format chips, in groups: (group, [(label, value for format_opts)]).
FORMATS = [
    ("Video (MP4)", [("Best quality", "mp4_best"),
                     ("1080p",        "1080p"),
                     ("720p",         "720p"),
                     ("480p",         "480p")]),
    ("Audio only",  [("MP3",          "mp3"),
                     ("M4A",          "m4a")]),
]
FORMAT_VALUES = [value for _, chips in FORMATS for _, value in chips]


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


UPDATE_CHECK_INTERVAL = 24 * 60 * 60   # seconds


PREVIEW_HINT = "Paste a link to see its title and length here."

# Room for the window's title bar and frame, which Tk's sizes don't include.
TITLE_BAR = 40


def enable_dpi_awareness():
    """Asks Windows to let the app draw at the screen's real resolution. By
    default Windows draws it at 96 DPI and stretches the result on a scaled
    display (e.g. 150%), which blurs the text. Must run before the first
    window opens. "System aware": on a second monitor with another scale,
    Windows still stretches it there, but it never comes out the wrong size."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # Windows 8.1 and later
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def work_area(widget):
    """(left, top, width, height) of the screen without the taskbar."""
    if sys.platform == "win32":
        try:
            from ctypes import wintypes
            rect = wintypes.RECT()
            SPI_GETWORKAREA = 0x30
            if ctypes.windll.user32.SystemParametersInfoW(
                    SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
                return (rect.left, rect.top,
                        rect.right - rect.left, rect.bottom - rect.top)
        except (AttributeError, OSError):
            pass
    # Elsewhere, leave room for a panel or dock.
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight() - 40


def system_theme(widget):
    """"dark" or "light": the theme the operating system uses for apps, or
    None if it can't tell."""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows"
                                r"\CurrentVersion\Themes\Personalize") as key:
                light = winreg.QueryValueEx(key, "AppsUseLightTheme")[0]
        except OSError:   # e.g. Windows 7, which has no dark mode
            return None
        return "light" if light else "dark"
    if sys.platform == "darwin":
        try:
            dark = widget.tk.call("::tk::unsupported::MacWindowStyle", "isdark", widget)
        except tk.TclError:   # Tk before 8.6.10
            return None
        return "dark" if widget.tk.getboolean(dark) else "light"
    # GNOME, and the desktops that follow its setting.
    try:
        scheme = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if "dark" in scheme:
        return "dark"
    return "light" if "light" in scheme or "default" in scheme else None


def set_title_bar_theme(window, dark):
    """On Windows 10 and 11, a dark or light title bar to match the window."""
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(int(dark))
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):   # older Windows: the title bar stays light
        pass


# Pillow draws at 4 times the size and shrinks the result, which smooths the
# rounded edges (Tk itself can't draw them smoothly).
SMOOTH = 4


def rounded(image, radius):
    """A copy of a Pillow image with its corners rounded off (transparent)."""
    mask = Image.new("L", (image.width * SMOOTH, image.height * SMOOTH), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, mask.width - 1, mask.height - 1), radius * SMOOTH, fill=255)
    image = image.convert("RGBA")
    image.putalpha(mask.resize(image.size, Image.LANCZOS))
    return image


def pill_image(width, height, color, radius=None):
    """A rounded rectangle; with no radius, round ends (a pill)."""
    return rounded(Image.new("RGB", (width, height), color),
                   height // 2 if radius is None else radius)


def logo_image(height):
    """The header's logo: a white play triangle on a red rounded rectangle."""
    width = round(height * 1.4)
    big = Image.new("RGBA", (width * SMOOTH, height * SMOOTH), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    draw.rounded_rectangle((0, 0, big.width - 1, big.height - 1),
                           round(height * 0.28) * SMOOTH, fill=THEMES["dark"]["accent"])
    x, y, size = big.width / 2, big.height / 2, big.height * 0.42
    draw.polygon([(x - size * 0.38, y - size / 2), (x + size * 0.5, y),
                  (x - size * 0.38, y + size / 2)], fill="white")
    return big.resize((width, height), Image.LANCZOS)


def switch_image(height, on, track, knob, gap):
    """An on/off switch: a knob at one end of a pill-shaped track, then gap
    transparent pixels (between it and its label)."""
    width = round(height * 1.75)
    big = Image.new("RGBA", ((width + gap) * SMOOTH, height * SMOOTH), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    draw.rounded_rectangle((0, 0, width * SMOOTH - 1, height * SMOOTH - 1),
                           height * SMOOTH // 2, fill=track)
    inset = max(2, height // 6) * SMOOTH
    size = height * SMOOTH - 2 * inset
    x = width * SMOOTH - inset - size if on else inset
    draw.ellipse((x, inset, x + size, inset + size), fill=knob)
    return big.resize((width + gap, height), Image.LANCZOS)


# A word that looks like a link without "https://": a host name and,
# optionally, a path ("youtu.be/abc", "www.youtube.com/watch?v=abc").
_BARE_LINK = re.compile(r"(?:[\w-]+\.)+[a-z]{2,}(?::\d+)?(?:[/?#]\S*)?", re.IGNORECASE)


def split_links(text):
    """(links, other words) in text, in order and without repeats. Links get
    "https://" if they have no scheme; quotes and brackets around a word and
    a trailing comma are dropped, so pasted prose keeps only its links."""
    links, other = [], []
    for word in text.split():
        word = word.strip("\"'<>()[]").rstrip(",").strip("\"'<>()[]")
        if word.lower().startswith(("https://", "http://")):
            links.append(word)
        elif _BARE_LINK.fullmatch(word):
            links.append("https://" + word)
        elif word:
            other.append(word)
    return list(dict.fromkeys(links)), list(dict.fromkeys(other))


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


def pip_install_command():
    # Quoted: zsh would treat the "[default]" extra as a file pattern.
    return f'pip install "{engine.YT_DLP_REQUIREMENT}"'


def _ffmpeg_install_hint():
    if sys.platform == "win32":
        return "Install it from a terminal:\n    winget install ffmpeg"
    if sys.platform == "darwin":
        return "Install it from a terminal:\n    brew install ffmpeg"
    return "Install it with your package manager, e.g.:\n    sudo apt install ffmpeg"


def js_solver_help():
    return ("⚠ yt-dlp-ejs isn't installed, so the JavaScript runtime can't solve "
            "YouTube's challenges and some formats may be missing. Install it:  "
            "pip install -r requirements.txt")


def js_runtime_help():
    if sys.platform == "win32":
        install = "winget install DenoLand.Deno"
    elif sys.platform == "darwin":
        install = "brew install deno"
    else:
        install = "curl -fsSL https://deno.land/install.sh | sh"
    return ("⚠ No JavaScript runtime found. yt-dlp uses one (deno) for YouTube; "
            "without it some formats may be missing. Install it from a terminal:"
            f"\n    {install}\nThe app picks it up on the next download.")


def ffmpeg_help(startup=False):
    then = ("Once it's installed, the app detects it (restart it if it doesn't)."
            if startup else
            "Then click Download again (restart the app if it still isn't found).")
    return ("ffmpeg is required to merge video and audio and to convert to "
            f"MP3/M4A, but it wasn't found.\n\n{_ffmpeg_install_hint()}\n\n{then}")


# A Pill's colors, by kind: (fill, fill under the mouse, text) as theme roles.
PILL_KINDS = {
    "primary":   ("accent", "accent_hover", "accent_text"),
    "secondary": ("button", "button_hover", "text"),
    "selected":  ("chip_on", "chip_on", "chip_on_text"),
}


class Pill(tk.Label):
    """A YouTube-style button: its text on a rounded shape that Pillow draws
    with smooth edges (without Pillow, a plain rectangle). Like a tk.Button
    it has a command, invoke() and a "disabled" state, and Space or Enter
    clicks it once Tab has moved to it.

    kind "primary" is red, "secondary" gray; a selected one is inverted, like
    YouTube's chips (select()). radius None gives round ends. With
    stretch=True it's as wide as its grid cell rather than its text. Sizes
    are at 96 DPI; the pill scales them itself (app._px)."""

    def __init__(self, app, master, text, command=None, *, kind="secondary",
                 font=(UI_FONT, 10), padx=16, pady=7, radius=None, stretch=False):
        super().__init__(master, text=text, font=font, compound="center", bd=0,
                         highlightthickness=app._px(2), takefocus=1)
        self.app, self.command, self.kind = app, command, kind
        self.selected = False
        self._pad = (app._px(padx), app._px(pady))
        self._radius = None if radius is None else app._px(radius)
        self._hover = False
        self._state = "normal"   # or "disabled" (see configure)
        self._width = None    # with stretch: the grid cell's width
        self._image = None    # keeps the PhotoImage alive
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._released)
        self.bind("<space>", self._key)
        self.bind("<Return>", self._key)
        if stretch:
            self.bind("<Configure>", self._resized)
        app._pills.append(self)
        self.repaint()

    # config(text=..., state=..., command=...) and cget("state") as for a
    # tk.Button. The pill keeps its state itself: a disabled Tk label would
    # draw its image grayed out with a coarse stipple; a pill turns gray.
    def configure(self, cnf=None, **kw):
        kw = {**(cnf or {}), **kw}
        if not kw:
            return super().configure()
        if "command" in kw:
            self.command = kw.pop("command")
        repaint = {"text", "font"} & kw.keys()
        if "state" in kw:
            self._state = str(kw.pop("state"))
            repaint = True
        result = super().configure(kw) if kw else None
        if repaint:
            self.repaint()
        return result

    config = configure

    def cget(self, key):
        return self._state if key == "state" else super().cget(key)

    __getitem__ = cget

    def invoke(self):
        if self.command and self._state != "disabled":
            return self.command()
        return None

    def select(self, selected=True):
        if selected != self.selected:
            self.selected = selected
            self.repaint()

    def _set_hover(self, hover):
        self._hover = hover
        self.repaint()

    def _released(self, event):
        # Like a button: only if the mouse is still over it.
        if 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    def _key(self, _e):
        self.invoke()
        return "break"

    def _resized(self, event):
        width = event.width - 2 * int(str(self.cget("highlightthickness")))
        if width > 0 and width != self._width:
            self._width = width
            self.repaint()

    def repaint(self):
        colors = self.app.colors
        disabled = self._state == "disabled"
        fill, hover, fg = PILL_KINDS["selected" if self.selected else self.kind]
        fill = colors["button" if disabled else hover if self._hover else fill]
        fg = colors["disabled" if disabled else fg]
        bg = colors["bg"]
        options = {"fg": fg, "disabledforeground": fg, "highlightbackground": bg,
                   "highlightcolor": colors["blue"],
                   "cursor": "arrow" if disabled else "hand2"}
        padx, pady = self._pad
        if ImageTk is None:
            super().configure(bg=fill, padx=padx, pady=pady, **options)
            return
        font, text = self.cget("font"), self.cget("text")
        width = self._width or int(self.tk.call("font", "measure", font, text)) + 2 * padx
        height = int(self.tk.call("font", "metrics", font, "-linespace")) + 2 * pady
        self._image = self.app._photo(pill_image, width, height, fill, self._radius)
        super().configure(image=self._image, bg=bg, padx=0, pady=0, **options)


class Toggle(Pill):
    """A YouTube-style on/off switch, then its label, for a BooleanVar.
    Clicking it (or Space or Enter) flips the variable, then calls command.
    Without Pillow it's a check box character before the label."""

    def __init__(self, app, master, text, variable, command=None, *, font=(UI_FONT, 10)):
        self.variable, self._label, self._changed = variable, text, command
        super().__init__(app, master, text, self._flip, font=font)
        variable.trace_add("write", lambda *_: self.repaint())

    def _flip(self):
        self.variable.set(not self.variable.get())
        if self._changed:
            self._changed()

    def repaint(self):
        colors = self.app.colors
        on = bool(self.variable.get())
        options = {"bg": colors["bg"], "fg": colors["text"], "padx": 0, "pady": 0,
                   "highlightbackground": colors["bg"], "highlightcolor": colors["blue"],
                   "cursor": "hand2"}
        if ImageTk is None:
            tk.Label.configure(self, text=("☑ " if on else "☐ ") + self._label, **options)
            return
        height = round(int(self.tk.call("font", "metrics", self.cget("font"),
                                         "-linespace")) * 0.8)
        self._image = self.app._photo(switch_image, height, on,
                                      colors["blue" if on else "scroll"],
                                      "white", self.app._px(8))
        tk.Label.configure(self, image=self._image, compound="left", **options)


class YouTubeDownloader(tk.Tk):
    def __init__(self, *, update_check=True):
        super().__init__()
        self.title("YT Downloader")
        # How much bigger than 96 DPI the screen is, when the app is DPI aware
        # (1.0 otherwise: Windows then reports 96 DPI and stretches the window).
        # (Tk reports e.g. 96.08 for 96: a difference that small isn't scaling.)
        scale = self.winfo_fpixels("1i") / 96
        self.scale = scale if scale > 1.05 else 1.0

        # State
        self._default_folder = os.path.normpath(os.path.expanduser("~/Downloads"))
        self._unavailable_folder = None   # the saved folder, if it's missing now
        self.download_path = tk.StringVar(value=self._default_folder)
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
        self._installing = False       # the in-app yt-dlp install is running
        self._close_when_stopped = False
        self._placeholder_shown = False
        self._last_file = None
        self.preview_text  = tk.StringVar(value=PREVIEW_HINT)
        self._preview_after = None     # pending lookup (waits for typing to pause)
        self._preview_key   = None     # (links, whole playlist) last looked up
        self._preview_token = 0        # newest lookup; older answers are dropped
        self._preview_cancel = threading.Event()   # set to stop the running lookup
        self._preview_image = None     # keeps the PhotoImage alive
        self._preview_dim   = True     # a hint rather than a video's details
        self._thumb_placeholder = None # the empty thumbnail (with Pillow)
        self.update_check_var = tk.BooleanVar(value=True)
        # Theme: "system", "light" or "dark"; see _apply_theme.
        self.theme_var     = tk.StringVar(value="system")
        self._painted = []             # (widget, {option: theme role})
        self._pills   = []
        self._fields  = []             # the text boxes' border frames
        self._focused_fields = set()
        self._photos  = {}             # Pill and Toggle images (see _photo)
        self._status_role = TEXT_DIM
        self._theme_after = None       # pending check of the system's theme
        self._apply_saved_settings()
        self._shown_theme = self._theme_name()
        self.colors = THEMES[self._shown_theme]

        self._build_ui()
        if self.scale != 1.0:
            self._scale_layout(self)
        self._fit_window()
        set_title_bar_theme(self, self._shown_theme == "dark")   # now that it exists
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if sys.platform in ("win32", "darwin"):   # asking is cheap there
            self._theme_after = self.after(2000, self._watch_system_theme)

        if not engine.YT_DLP_AVAILABLE:
            self.after(300, self._prompt_install)
        self._ffmpeg_missing = not engine.find_ffmpeg()
        if self._ffmpeg_missing:
            self._set_status(FFMPEG_MISSING_STATUS, WARNING)
            self._log(ffmpeg_help(startup=True))
        self._js_runtime = None
        if engine.YT_DLP_AVAILABLE:   # it runs "<runtime> --version": a worker's job
            self._start_worker(self._find_js_runtime_thread)
        self._note_yt_dlp_version()
        if self._unavailable_folder:
            self._log(f"• The saved folder {self._unavailable_folder} isn't available "
                      f"(an unplugged drive?); saving to {self._default_folder} for now.")
        if update_check:
            self._maybe_check_for_update()
        self.bind("<FocusIn>", self._recheck_ffmpeg, add="+")

    # ── UI Construction ─────────────────────────────────────────────────────

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        paint = self._paint

        # ── Header: the logo on the left, the theme on the right ──
        hdr = paint(tk.Frame(self, padx=20, pady=10), bg="bg")
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.columnconfigure(1, weight=1)

        if ImageTk is not None:
            self._logo = ImageTk.PhotoImage(logo_image(self._px(30)), master=self)
            logo = paint(tk.Label(hdr, image=self._logo, bd=0), bg="bg")
        else:
            logo = paint(tk.Label(hdr, text="▶", font=(UI_FONT, 20, "bold")),
                         bg="bg", fg="accent")
        logo.grid(row=0, column=0, rowspan=2, padx=(0, 10))
        paint(tk.Label(hdr, text="YT Downloader", font=(UI_FONT, 15, "bold")),
              bg="bg", fg="text").grid(row=0, column=1, sticky="sw")
        self.powered_lbl = paint(tk.Label(hdr, text=self._powered_by_text(),
                                          font=(UI_FONT, 9)), bg="bg", fg="text_dim")
        self.powered_lbl.grid(row=1, column=1, sticky="nw")

        themes = paint(tk.Frame(hdr), bg="bg")
        themes.grid(row=0, column=2, rowspan=2, sticky="e")
        paint(tk.Label(themes, text="Theme", font=(UI_FONT, 9)),
              bg="bg", fg="text_dim").grid(row=0, column=0, padx=(0, 4))
        self.theme_chips = {}
        for col, (label, mode) in enumerate(THEME_MODES, 1):
            chip = Pill(self, themes, label, lambda m=mode: self._choose_theme(m),
                        font=(UI_FONT, 9), padx=12, pady=4, radius=8)
            chip.grid(row=0, column=col)
            self.theme_chips[mode] = chip
        if engine.app_version():   # only a release .exe checks
            Toggle(self, hdr, "Check for updates at startup", self.update_check_var,
                   self._save_settings, font=(UI_FONT, 8),
                   ).grid(row=2, column=2, sticky="e", pady=(4, 0))

        paint(tk.Frame(self, height=1), bg="divider").grid(row=1, column=0, sticky="ew")

        # ── The controls ──
        body = paint(tk.Frame(self, padx=24, pady=16), bg="bg")
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        row = 0

        # URL, in a box like YouTube's search box
        self._heading(body, "YouTube URLs (one per line)").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(0, 6))
        row += 1

        url_frame = tk.Frame(body, highlightthickness=1)
        url_frame.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        url_frame.columnconfigure(0, weight=1)

        # Several links can go in, one per line; they download in order. The
        # box grows to 4 lines. Enter downloads, Shift+Enter adds a line.
        # width=1: it fills the row, and the window's size comes from the
        # other controls rather than from a default of 80 characters.
        self.url_entry = tk.Text(url_frame, height=1, width=1, wrap="none",
                                 font=(UI_FONT, 12), relief="flat", bd=0,
                                 padx=12, pady=8, highlightthickness=0, undo=True)
        self.url_entry.grid(row=0, column=0, sticky="ew")
        self._restore_placeholder()
        self.url_entry.bind("<FocusIn>",  self._clear_placeholder)
        self.url_entry.bind("<FocusOut>", self._restore_placeholder)
        self.url_entry.bind("<FocusOut>", self._schedule_preview, add="+")
        self.url_entry.bind("<KeyRelease>", self._on_url_typed)
        self.url_entry.bind("<Return>", self._on_url_enter)
        self.url_entry.bind("<Shift-Return>", lambda e: None)   # a new line
        self.url_entry.bind("<Tab>", self._focus_next)
        self.url_entry.bind("<Shift-Tab>", self._focus_prev)
        self._field(url_frame, self.url_entry)

        Pill(self, body, "Paste", self._paste_url, pady=9).grid(
            row=row, column=1, sticky="n", padx=(8, 0))
        row += 1

        # Preview of the link, like a video in a YouTube list: thumbnail,
        # then the title and, dimmer, the length and channel
        preview = paint(tk.Frame(body), bg="bg")
        preview.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 16))
        preview.columnconfigure(1, weight=1)
        preview.rowconfigure(1, weight=1)
        # Without Pillow there's no image, so the box itself shows the shape.
        thumb_bg = "bg" if ImageTk is not None else "surface"
        thumb_box = paint(tk.Frame(preview, width=engine.THUMB_SIZE[0],
                                   height=engine.THUMB_SIZE[1]), bg=thumb_bg)
        thumb_box.grid(row=0, column=0, rowspan=2, sticky="nw")   # fixed size, image or not
        self.preview_img = paint(tk.Label(thumb_box, bd=0), bg=thumb_bg)
        self.preview_img.place(relx=0.5, rely=0.5, anchor="center")
        self.preview_title = tk.StringVar()
        self.preview_details = tk.StringVar()
        self.preview_lbl = paint(tk.Label(preview, textvariable=self.preview_title,
                                          justify="left", anchor="nw", wraplength=400),
                                 bg="bg")
        self.preview_lbl.grid(row=0, column=1, sticky="new", padx=(14, 0))
        details = paint(tk.Label(preview, textvariable=self.preview_details,
                                 font=(UI_FONT, 9), justify="left", anchor="nw",
                                 wraplength=400), bg="bg", fg="text_dim")
        details.grid(row=1, column=1, sticky="new", padx=(14, 0), pady=(2, 0))
        for label in (self.preview_lbl, details):
            label.bind("<Configure>", lambda e: e.widget.config(
                wraplength=max(e.width - self._px(4), self._px(100))))
        self._set_preview(PREVIEW_HINT, dim=True)
        row += 1

        # Format: chips, like those at the top of YouTube's home page
        fmt_head = paint(tk.Frame(body), bg="bg")
        fmt_head.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        fmt_head.columnconfigure(0, weight=1)
        self._heading(fmt_head, "Format").grid(row=0, column=0, sticky="w")
        Toggle(self, fmt_head, "Whole playlist", self.playlist_var,
               self._schedule_preview).grid(row=0, column=1, sticky="e")
        row += 1

        chips = paint(tk.Frame(body), bg="bg")
        chips.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 14))
        self.format_chips = {}
        for group_row, (group, choices) in enumerate(FORMATS):
            paint(tk.Label(chips, text=group, font=(UI_FONT, 9)), bg="bg", fg="text_dim",
                  ).grid(row=group_row, column=0, sticky="w", padx=(0, 12))
            line = paint(tk.Frame(chips), bg="bg")
            line.grid(row=group_row, column=1, sticky="w", pady=1)
            for col, (label, value) in enumerate(choices):
                chip = Pill(self, line, label, lambda v=value: self.format_var.set(v),
                            padx=12, pady=5, radius=8)
                chip.grid(row=0, column=col, padx=(0, 4))
                self.format_chips[value] = chip
        self.format_var.trace_add("write", lambda *_: self._select_format_chip())
        row += 1

        # Output folder
        self._heading(body, "Save to").grid(row=row, column=0, columnspan=2,
                                            sticky="w", pady=(0, 6))
        row += 1

        path_frame = tk.Frame(body, highlightthickness=1)
        path_frame.grid(row=row, column=0, sticky="ew", pady=(0, 20))
        path_frame.columnconfigure(0, weight=1)
        path_entry = paint(tk.Entry(path_frame, textvariable=self.download_path,
                                    font=(UI_FONT, 10), relief="flat", bd=0,
                                    highlightthickness=0), fg="text")
        path_entry.grid(row=0, column=0, sticky="ew", padx=10, pady=8)
        self._field(path_frame, path_entry)

        Pill(self, body, "Browse", self._browse_folder).grid(
            row=row, column=1, sticky="n", padx=(8, 0))
        row += 1

        # Download button
        self.dl_btn = Pill(self, body, "⬇  Download", self._start_download,
                           kind="primary", font=(UI_FONT, 12, "bold"), pady=10,
                           stretch=True)
        self.dl_btn.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        row += 1

        # Progress bar: thin and red, like a YouTube player's (ttk's can't
        # be made this thin)
        self.progress = paint(tk.Frame(body, height=4), bg="button")
        self.progress.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        bar = paint(tk.Frame(self.progress), bg="accent")
        self.progress_var.trace_add("write", lambda *_: bar.place(
            relheight=1, relwidth=min(max(self.progress_var.get(), 0), 100) / 100))
        row += 1

        # Status label, with an "Open folder" button after a download
        status_row = paint(tk.Frame(body), bg="bg")
        status_row.grid(row=row, column=0, columnspan=2, sticky="ew")
        status_row.columnconfigure(0, weight=1)
        self.status_lbl = paint(tk.Label(status_row, textvariable=self.status_var,
                                         font=(UI_FONT, 10), wraplength=580,
                                         justify="left", anchor="w"), bg="bg")
        self.status_lbl.grid(row=0, column=0, sticky="ew")
        self.open_btn = Pill(self, status_row, "📂  Open folder", self._open_folder,
                             font=(UI_FONT, 9), padx=12, pady=4)
        self.open_btn.grid(row=0, column=1, sticky="ne", padx=(8, 0))
        self.open_btn.grid_remove()   # shown once a download has saved a file
        # Wrap long messages at the label's current width, not a fixed one.
        self.status_lbl.bind("<Configure>", lambda e: self.status_lbl.config(
            wraplength=max(e.width - self._px(4), self._px(100))))
        row += 1

        # ── Log box ──
        log_frame = paint(tk.Frame(self, padx=24), bg="bg")
        log_frame.grid(row=3, column=0, sticky="nsew", pady=(0, 16))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)

        paint(tk.Label(log_frame, text="Log", font=(UI_FONT, 9, "bold")),
              bg="bg", fg="text_dim").grid(row=0, column=0, sticky="w", pady=(0, 4))

        self.log_box = paint(tk.Text(log_frame, height=6, width=1, font=(MONO_FONT, 9),
                                     relief="flat", bd=0, padx=10, pady=8,
                                     highlightthickness=0, state="disabled",
                                     wrap="word"), bg="surface", fg="text_dim")
        self.log_box.grid(row=1, column=0, sticky="nsew")

        sb = ttk.Scrollbar(log_frame, command=self.log_box.yview,
                           style="Log.Vertical.TScrollbar")
        sb.grid(row=1, column=1, sticky="ns", padx=(2, 0))
        self.log_box.configure(yscrollcommand=sb.set)

        self._apply_theme()

    def _heading(self, master, text):
        return self._paint(tk.Label(master, text=text, font=(UI_FONT, 11, "bold")),
                           bg="bg", fg="text")

    # Text boxes look like YouTube's search box: a thin border that turns
    # blue while the box has the cursor.
    def _field(self, frame, box):
        self._paint(frame, bg="field")
        self._paint(box, bg="field", insertbackground="text")
        self._fields.append(frame)
        box.bind("<FocusIn>", lambda e: self._paint_field(frame, True), add="+")
        box.bind("<FocusOut>", lambda e: self._paint_field(frame, False), add="+")

    def _paint_field(self, frame, focused):
        if focused:
            self._focused_fields.add(frame)
        else:
            self._focused_fields.discard(frame)
        color = self.colors["blue" if focused else "field_border"]
        frame.config(highlightbackground=color, highlightcolor=color)

    def _select_format_chip(self):
        for value, chip in self.format_chips.items():
            chip.select(value == self.format_var.get())

    # ── Themes ──────────────────────────────────────────────────────────────

    def _paint(self, widget, **roles):
        """Colors widget's options from theme roles, now and after every
        theme change, e.g. _paint(label, bg="bg", fg="text_dim")."""
        self._painted.append((widget, roles))
        widget.configure({option: self.colors[role] for option, role in roles.items()})
        return widget

    def _photo(self, draw, *args):
        """draw(*args) as a PhotoImage. They're kept: Pills and Toggles
        draw the same few shapes again and again (e.g. on hover)."""
        key = (draw, *args)
        if key not in self._photos:
            if len(self._photos) > 200:   # e.g. after much resizing
                self._photos.clear()      # (each Pill keeps its own image)
            self._photos[key] = ImageTk.PhotoImage(draw(*args), master=self)
        return self._photos[key]

    def _theme_name(self):
        """The theme to show: the one chosen, or else the system's (dark,
        the app's original look, if the system doesn't say)."""
        mode = self.theme_var.get()
        return mode if mode in THEMES else system_theme(self) or "dark"

    def _choose_theme(self, mode):
        self.theme_var.set(mode)
        self._apply_theme()
        self._save_settings()

    # Repaints every widget in the theme's colors.
    def _apply_theme(self):
        self._shown_theme = self._theme_name()
        self.colors = colors = THEMES[self._shown_theme]
        self.configure(bg=colors["bg"])
        for widget, roles in self._painted:
            widget.configure({option: colors[role] for option, role in roles.items()})
        for frame in self._fields:
            self._paint_field(frame, frame in self._focused_fields)
        for mode, chip in self.theme_chips.items():
            chip.selected = mode == self.theme_var.get()
        for value, chip in self.format_chips.items():
            chip.selected = value == self.format_var.get()
        for pill in self._pills:
            pill.repaint()
        placeholder = self._placeholder_shown and self._url_text() == URL_PLACEHOLDER
        self.url_entry.config(fg=colors[TEXT_DIM if placeholder else TEXT])
        self.status_lbl.config(fg=colors[self._status_role])
        self._paint_preview()

        style = ttk.Style(self)
        style.theme_use("clam")
        # The log's scroll bar: just a thumb, without arrows.
        style.layout("Log.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {
            "sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure("Log.Vertical.TScrollbar", arrowsize=self._px(10), gripcount=0,
                        troughcolor=colors["surface"], bordercolor=colors["surface"],
                        background=colors["scroll"], lightcolor=colors["scroll"],
                        darkcolor=colors["scroll"])
        style.map("Log.Vertical.TScrollbar",
                  background=[("active", colors["text_dim"])],
                  lightcolor=[("active", colors["text_dim"])],
                  darkcolor=[("active", colors["text_dim"])])
        set_title_bar_theme(self, self._shown_theme == "dark")

    # While the theme follows the system's, notice when that changes. (On
    # Linux asking runs gsettings, too slow to repeat every few seconds; it's
    # read at startup and when System is chosen.)
    def _watch_system_theme(self):
        self._theme_after = self.after(2000, self._watch_system_theme)
        if self.theme_var.get() == "system" and self._theme_name() != self._shown_theme:
            self._apply_theme()

    def destroy(self):
        if self._theme_after is not None:
            self.after_cancel(self._theme_after)
            self._theme_after = None
        super().destroy()

    # The last folder, format and theme are remembered (when a download starts,
    # a theme is chosen, and the app closes). "Whole playlist" always starts
    # unticked.
    def _apply_saved_settings(self):
        saved = load_settings()
        if saved.get("theme") in {mode for _, mode in THEME_MODES}:
            self.theme_var.set(saved["theme"])
        if saved.get("format") in FORMAT_VALUES:
            self.format_var.set(saved["format"])
        folder = saved.get("folder")
        if isinstance(folder, str) and os.path.isdir(folder):
            self.download_path.set(os.path.normpath(folder))
        elif isinstance(folder, str) and folder:
            # Kept, not forgotten: it may be on a drive that's unplugged now.
            self._unavailable_folder = folder
        if isinstance(saved.get("check_updates"), bool):
            self.update_check_var.set(saved["check_updates"])

    def _save_settings(self):
        folder = self.download_path.get()
        if self._unavailable_folder and folder == self._default_folder:
            folder = self._unavailable_folder   # still the stand-in: keep the saved one
        # Keeps the other keys, e.g. when updates were last checked.
        save_settings({**load_settings(),
                       "folder": folder,
                       "format": self.format_var.get(),
                       "check_updates": self.update_check_var.get(),
                       "theme": self.theme_var.get()})

    def _fit_window(self):
        # Open wide and tall enough to show everything, but no bigger than
        # the screen without the taskbar, and centered on it. Only the log
        # grows and shrinks with the window, and the minimum height keeps the
        # controls, the status line and 2 log lines visible (plus one line for
        # a status message that wraps). With larger text (Windows display
        # scaling) the controls need more room than the usual 680 px.
        self.update_idletasks()
        needed, wanted = self.winfo_reqwidth(), self.winfo_reqheight()
        log_height = self.log_box.winfo_reqheight()
        line = log_height // int(self.log_box.cget("height"))
        left, top, area_width, area_height = work_area(self)
        title_bar = self._px(TITLE_BAR)
        width = min(max(self._px(680), needed), area_width)
        height = min(wanted, area_height - title_bar)
        self.minsize(min(max(self._px(580), needed), width),
                     min(wanted - log_height + 3 * line, height))
        x = left + (area_width - width) // 2
        y = top + max(0, (area_height - title_bar - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    # ── High-DPI screens ────────────────────────────────────────────────────

    def _px(self, pixels):
        """pixels at 96 DPI, on this screen."""
        return round(pixels * self.scale)

    # The layout's pixel sizes (padding, borders, the thumbnail box, wrap
    # widths, the progress bar's height) are written for 96 DPI; on a
    # high-DPI screen they're scaled once, after the window is built, so it
    # keeps its proportions. Fonts scale by themselves (they're in points),
    # and so do sizes in characters (e.g. a Text's width and height).
    _PIXEL_OPTIONS = ("padx", "pady", "bd", "highlightthickness", "wraplength")
    _GRID_OPTIONS = ("padx", "pady", "ipadx", "ipady")

    def _scale_layout(self, widget):
        # A Pill scales its own sizes; only its place in the grid scales here.
        for name in () if isinstance(widget, Pill) else self._PIXEL_OPTIONS:
            try:
                value = int(str(widget.cget(name)))
            except (tk.TclError, ValueError):   # not this widget's option, or not pixels
                continue
            if value:
                widget.configure({name: self._px(value)})
        sizes = ("width", "height") if isinstance(widget, tk.Frame) else ()
        for name in sizes:
            value = int(str(widget.cget(name)))
            if value:
                widget.configure({name: self._px(value)})
        if widget.winfo_manager() == "grid":
            info = widget.grid_info()   # e.g. padx 20, or pady (0, 6)
            widget.grid_configure(**{
                name: tuple(self._px(int(v)) for v in (
                    info[name] if isinstance(info[name], (tuple, list))
                    else str(info[name]).split()))
                for name in self._GRID_OPTIONS if name in info})
        for child in widget.winfo_children():
            self._scale_layout(child)

    # ── Helpers ─────────────────────────────────────────────────────────────

    # The URL box shows a dimmed example until the user types or pastes.
    # (edit_reset: Ctrl+Z mustn't bring the placeholder back as text.)
    def _clear_placeholder(self, _e=None):
        if self._placeholder_shown:
            self._placeholder_shown = False
            self._replace_url_text("")
            self.url_entry.edit_reset()

    def _restore_placeholder(self, _e=None):
        if not self._url_text().strip():
            self._replace_url_text(URL_PLACEHOLDER)
            self.url_entry.config(fg=self.colors[TEXT_DIM])
            self._placeholder_shown = True
            self.url_entry.edit_reset()

    def _url_text(self):
        return self.url_entry.get("1.0", "end-1c")

    def _replace_url_text(self, text):
        self.url_entry.delete("1.0", "end")
        self.url_entry.insert("1.0", text)
        self.url_entry.config(fg=self.colors[TEXT])
        self._fit_url_box()

    def _urls(self):
        """The links entered (one per line, or separated by spaces) in order
        and without repeats; none while the placeholder is showing. Words
        that aren't links are left out (see _not_links)."""
        return split_links(self._entered_text())[0]

    def _not_links(self):
        """The words in the box that aren't links."""
        return split_links(self._entered_text())[1]

    def _entered_text(self):
        text = self._url_text().strip()
        if self._placeholder_shown and text == URL_PLACEHOLDER:
            return ""
        return text

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

    # Without its own binding, Shift+Tab would match <Tab> and move forward.
    def _focus_prev(self, event):
        event.widget.tk_focusPrev().focus()
        return "break"

    # Adds the clipboard's links to those already in the box, one per line,
    # so several links can be pasted one after the other. Other words (e.g.
    # "Watch this:" before a link) are left out.
    def _paste_url(self):
        try:
            clip = self.clipboard_get()
        except tk.TclError:   # empty clipboard, or not text
            return
        if not split_links(clip)[0]:
            self._set_status("The clipboard has no link to paste.", WARNING)
            return
        entered = self._entered_text()   # before the placeholder flag changes
        self._placeholder_shown = False
        self._replace_url_text("\n".join(split_links(entered + "\n" + clip)[0]))
        self._schedule_preview(delay=100)

    def _browse_folder(self):
        path = filedialog.askdirectory(initialdir=self.download_path.get())
        if path:
            self.download_path.set(os.path.normpath(path))
            self._unavailable_folder = None   # a deliberate choice replaces it

    def _log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _set_status(self, msg, color=TEXT_DIM):   # color: a theme role
        self.status_var.set(msg)
        self._status_role = color
        self.status_lbl.config(fg=self.colors[color])

    # Tkinter is not thread-safe: worker threads must not touch widgets or
    # Tk variables. They _post() discrete events and overwrite
    # _latest_progress; the main thread applies both in _poll_ui_queue().
    def _post(self, func, *args):
        self._ui_queue.put((func, args))

    def _start_worker(self, target, *args):
        self._active_workers += 1
        if self._after_id is None:
            self._after_id = self.after(50, self._poll_ui_queue)
        threading.Thread(target=self._run_worker, args=(target, *args),
                         daemon=True).start()

    # Runs on a worker thread. Whatever target does, the main thread hears
    # that it finished (after anything target posted), so _active_workers
    # can't leak; a crash goes to the log instead of a console nobody sees.
    def _run_worker(self, target, *args):
        try:
            target(*args)
        except Exception:
            self._post(self._worker_crashed, traceback.format_exc())
        finally:
            self._post(self._worker_finished)

    def _worker_crashed(self, details):
        self._log("✘ Unexpected error: " + details.strip().splitlines()[-1])
        if sys.stderr:   # a console (from source): the full traceback too
            print(details, file=sys.stderr)

    # Tk's default prints the error to stderr, which the windowed .exe doesn't
    # have; show it in the log as well.
    def report_callback_exception(self, exc, val, tb):
        try:
            self._log("✘ Unexpected error: "
                      + "".join(traceback.format_exception_only(exc, val)).strip())
        except tk.TclError:   # the window is already gone
            pass
        if sys.stderr:
            super().report_callback_exception(exc, val, tb)

    # Runs on a worker thread.
    def _find_js_runtime_thread(self):
        self._post(self._js_runtime_found, engine.find_js_runtime(),
                   engine.js_solver_version())

    def _js_runtime_found(self, runtime, solver):
        self._js_runtime = runtime
        if runtime is None:
            self._log(js_runtime_help())
        elif solver is None:
            self._log(js_solver_help())

    def _worker_finished(self):
        self._active_workers -= 1

    def _poll_ui_queue(self):
        self._after_id = None
        progress = self._latest_progress
        if progress is not None and progress is not self._shown_progress:
            self._shown_progress = progress
            # One that arrives after Cancel mustn't overwrite "Canceling…".
            if not (self.is_running and self._queue.canceled):
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
        # runs (e.g. stuck on a slow network request) closes right away.
        if self.is_running and not self._close_when_stopped:
            if not messagebox.askyesno(
                    "Download in progress",
                    "A download is still running.\n\n"
                    "Cancel it and close the app?"):
                return
            # The download may have finished while the question was open
            # (Tk keeps running timers meanwhile); then just close.
            if self.is_running:
                self._close_when_stopped = True
                self._preview_cancel.set()
                if not self._queue.canceled:
                    self._cancel_download()
                self._set_status("Canceling… the app closes when it has stopped.",
                                 WARNING)
                return
        self._preview_cancel.set()
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
        if self.is_running:
            messagebox.showinfo("Busy", "A download is already in progress.")
            return

        if not engine.YT_DLP_AVAILABLE:
            if self._installing:
                self._set_status("Installing yt-dlp, please wait…", WARNING)
            else:
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

        # A relative folder would save next to wherever the app was started.
        folder = os.path.expanduser(self.download_path.get().strip())
        if not folder or not os.path.isabs(folder):
            messagebox.showwarning("Choose a folder",
                                   "Please choose the folder to save to (Browse).")
            return
        folder = os.path.normpath(folder)
        self.download_path.set(folder)

        # Tk variables are read here, on the main thread; the queue gets values.
        self._queue = engine.DownloadQueue(
            urls, fmt=self.format_var.get(), folder=folder,
            ffmpeg=ffmpeg, whole_playlist=self.playlist_var.get(),
            js_runtime=self._js_runtime,
            on_progress=self._job_progress, on_log=self._job_log,
            ask=self._job_ask)
        self._save_settings()
        self.is_running = True
        self.dl_btn.config(state="normal", text="✖  Cancel", command=self._cancel_download)
        self.progress_var.set(0)
        self._set_status("Starting download…", WARNING)
        self._log(f"▶ {urls[0]}" if len(urls) == 1 else f"▶ {len(urls)} links")
        not_links = self._not_links()
        if not_links:
            self._log("• Not links, so left out: " + " ".join(not_links))
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

    # Runs on the download thread. The window hears that the download ended
    # even if it crashed, after the error is in the log.
    def _run_queue(self, download):
        try:
            if download.job_options.get("js_runtime") is None:   # installed since startup?
                download.job_options["js_runtime"] = engine.find_js_runtime()
            download.run()
        except Exception:
            self._post(self._worker_crashed, traceback.format_exc())
        finally:
            self._post(self._download_finished, download)

    # Runs on the download thread; blocks it until the user answers.
    def _ask_on_main(self, title, question):
        answer, done = [], threading.Event()

        # If the dialog fails, the error goes to the log (the windowed .exe has
        # no stderr for Tk's report) and the answer is No; either way the
        # download thread stops waiting.
        def ask():
            try:
                if self._queue is not None and self._queue.canceled:
                    answer.append(False)   # canceled before the question showed
                    return
                answer.append(messagebox.askyesno(title, question))
            except Exception as exc:
                self._log(f"✘ Couldn't ask about the playlist: {exc}")
                raise
            finally:
                done.set()

        self._post(ask)
        while not done.wait(0.1):
            if self._closing:
                return False
        return bool(answer and answer[0])

    # The Download button turns into Cancel while a download runs. yt-dlp
    # stops at its next progress update or status message (also while it
    # reads a playlist), and a running ffmpeg conversion or merge is stopped.
    def _cancel_download(self):
        self._queue.cancel()
        # A progress update set just before must not overwrite "Canceling…".
        self._shown_progress = self._latest_progress
        self.dl_btn.config(state="disabled", text="Canceling…")
        self._set_status("Canceling…", WARNING)

    def _end_download(self):
        self.is_running = False
        self.dl_btn.config(state="normal", text="⬇  Download",
                           command=self._start_download)

    def _download_finished(self, job):   # job: the DownloadQueue
        self._end_download()
        if self._close_when_stopped:   # the user is closing the app
            return self._on_close()
        self._last_file = job.last_file
        if job.outcome is None:   # it crashed; the error is in the log
            self.progress_var.set(0)
            self._set_status("✘ The download stopped because of an unexpected "
                             "error (see the log).", ERROR)
            self._offer_open_folder()
            return
        summary = job.summary()
        if job.outcome == "failed":
            self.progress_var.set(0)
            self._set_status(f"✘ {summary}", ERROR)
            if not job.error_logged:
                self._log(f"✘ {summary}\n")
            messagebox.showerror("Download Failed", job.error or "See the log for details.")
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
        self._preview_cancel.set()
        self._preview_cancel = threading.Event()
        if not url:
            text = "That doesn't look like a link." if self._not_links() else PREVIEW_HINT
            return self._set_preview(text, dim=True)
        if not url.startswith(("https://", "http://")) or not engine.YT_DLP_AVAILABLE:
            return self._set_preview("No preview for this link.", dim=True)
        self._set_preview("Loading preview…", dim=True)
        self._start_worker(self._preview_thread, url, self.playlist_var.get(),
                           self._preview_token, len(urls) - 1, self._preview_cancel,
                           self._js_runtime)

    # Runs on a worker thread.
    def _preview_thread(self, url, whole_playlist, token, more, cancel, js_runtime=None):
        thumb = tuple(self._px(n) for n in engine.THUMB_SIZE)
        result = engine.preview_link(url, whole_playlist, cancel, js_runtime, thumb)
        if result is None:   # a newer link replaced this one
            return
        text, image, found = result
        if more:   # several links: the preview shows the first one
            text += f"\n+ {more} more link{'s' * (more != 1)}"
        self._post(self._preview_done, text, image, found, token)

    def _preview_done(self, text, image, found, token):
        if token == self._preview_token:   # drop answers for an older link
            self._set_preview(text, image, dim=not found)

    # The first line of text is the title; the rest goes under it, dimmer.
    def _set_preview(self, text, image=None, dim=False):
        self.preview_text.set(text)
        title, _, details = text.partition("\n")
        self.preview_title.set(title)
        self.preview_details.set(details)
        self._preview_dim = dim
        # Some Linux distributions package Pillow's ImageTk separately.
        show = image is not None and ImageTk is not None
        self._preview_image = (ImageTk.PhotoImage(rounded(image, self._px(8)), master=self)
                               if show else None)
        self._paint_preview()

    def _paint_preview(self):
        self.preview_lbl.config(fg=self.colors[TEXT_DIM if self._preview_dim else TEXT],
                                font=(UI_FONT, 10) if self._preview_dim
                                else (UI_FONT, 11, "bold"))
        if ImageTk is not None and self._preview_image is None:
            # An empty thumbnail, in this theme's color.
            self._thumb_placeholder = ImageTk.PhotoImage(pill_image(
                *(self._px(n) for n in engine.THUMB_SIZE), self.colors["surface"],
                self._px(8)), master=self)
        self.preview_img.config(image=self._preview_image or self._thumb_placeholder or "")

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
        text = f"Powered by yt-dlp {version}" if version else \
            "Powered by yt-dlp (not installed)"
        app = engine.app_version()
        return f"v{app} · {text}" if app else text

    # A release .exe asks GitHub (api.github.com) for its latest release, at
    # most once a day, while "Check for updates at startup" is ticked.
    def _maybe_check_for_update(self):
        if engine.app_version() is None or not self.update_check_var.get():
            return
        last = load_settings().get("last_update_check")
        if isinstance(last, (int, float)) and 0 <= time.time() - last < UPDATE_CHECK_INTERVAL:
            return
        self._start_worker(self._update_thread)

    # Says once, in the log, when a newer release is out.
    def _update_thread(self):
        try:
            notice, asked = engine.fetch_update_notice(), True
        except Exception:   # e.g. offline: try again at the next start
            notice, asked = None, False
        self._post(self._update_checked, notice, asked)

    def _update_checked(self, notice, asked=True):
        # Only a check that reached GitHub counts toward "once a day".
        if asked:
            save_settings({**load_settings(), "last_update_check": time.time()})
        if notice:
            self._log(notice)

    # The app is tested with the version pinned in requirements.txt; say so
    # if another one is installed (yt-dlp often needs updates for YouTube).
    def _note_yt_dlp_version(self):
        installed, pinned = engine.yt_dlp_version(), engine.pinned_yt_dlp_version()
        if installed and pinned and not engine.same_version(installed, pinned):
            self._log(f"• yt-dlp {installed} is installed; this app is tested "
                      f"with {pinned}. To match: {pip_install_command()}")

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
            f"(You can also run:  {pip_install_command()})"
        )
        if ans:
            self._install_ytdlp()

    def _install_ytdlp(self):
        self._installing = True
        self._set_status("Installing yt-dlp…", WARNING)
        self._log("Installing yt-dlp via pip…")

        def run():
            try:
                done = subprocess.run(
                    [sys.executable, "-m", "pip", "install", engine.YT_DLP_REQUIREMENT],
                    capture_output=True, text=True, errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception as e:
                self._post(self._install_done, False, str(e))
                return
            if done.returncode:
                # pip's last lines say why (e.g. no network, or a Python
                # that its distribution manages).
                why = "\n".join((done.stderr or done.stdout or "").strip().splitlines()[-5:])
                self._post(self._install_done, False,
                           why or f"pip exited with code {done.returncode}")
                return
            # Importing here, on the worker, keeps the window responsive.
            self._post(self._install_done, True, None, engine.load_yt_dlp())

        self._start_worker(run)

    def _install_done(self, ok, err=None, loaded=False):
        self._installing = False
        if ok and loaded:
            self.powered_lbl.config(text=self._powered_by_text())
            self._log(f"✔ yt-dlp {engine.yt_dlp_version()} installed and ready.")
            self._set_status("✔ yt-dlp installed. You can download now.", SUCCESS)
            # What startup skipped without yt-dlp: the JavaScript runtime
            # check, and the preview of a link that's already entered.
            self._start_worker(self._find_js_runtime_thread)
            self._preview_key = None
            self._schedule_preview(delay=100)
        elif ok:   # installed, but this Python can't import it until a restart
            self._log("✔ yt-dlp installed! Please restart the app.")
            self._set_status("✔ yt-dlp installed — please restart.", SUCCESS)
            messagebox.showinfo("Installed",
                                "yt-dlp was installed successfully.\n"
                                "Please restart the application.")
        else:
            self._log(f"✘ Install failed: {err}")
            self._set_status(f"Install failed. Try: {pip_install_command()}", ERROR)


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
    js_runtime = engine.find_js_runtime()
    js_runtime_version = None
    if js_runtime:
        run = subprocess.run([js_runtime[1], "--version"], capture_output=True, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        js_runtime_version = run.stdout.splitlines()[0] if run.stdout else None
    result = {"frozen": bool(getattr(sys, "frozen", False)),
              "app_version": engine.app_version(),
              "yt_dlp": engine.yt_dlp_version(),
              "yt_dlp_pin": engine.pinned_yt_dlp_version(),
              "ffmpeg": ffmpeg, "ffmpeg_bundled": engine.bundled_ffmpeg() is not None,
              "ffmpeg_version": ffmpeg_version,
              "js_runtime": js_runtime and js_runtime[1],
              "js_runtime_version": js_runtime_version,
              "deno_bundled": engine.bundled_deno() is not None,
              "js_solver": engine.js_solver_version(),
              "yt_dlp_extras_missing": engine.missing_yt_dlp_extras(),
              "thumbnails": ImageTk is not None and engine.Image is not None}
    try:
        app = YouTubeDownloader(update_check=False)   # also checks that Tk works
        app.update()
        result["window"] = app.title()
        app.destroy()
    except tk.TclError as exc:
        result["window"] = f"failed: {exc}"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


# ── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-check":
        self_check(sys.argv[2])
    else:
        enable_dpi_awareness()
        app = YouTubeDownloader()
        app.mainloop()
