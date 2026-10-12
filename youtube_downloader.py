#!/usr/bin/env python3
"""
YouTube Video Downloader — Desktop App

This file is the window, built with Qt (PySide6). The downloading itself is
in download_engine.py, which doesn't use Qt.
Requirements: pip install -r requirements.txt
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import ClassVar

# The style sheet's switches and arrows are SVG images. Qt's SVG plugin needs
# Qt6Svg.dll, which Windows only finds once it's loaded: importing QtSvg
# loads it (and makes PyInstaller bundle it).
from PySide6 import QtSvg  # noqa: F401
from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QImage,
    QImageReader,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTableView,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

import download_engine as engine

# ── Themes ──────────────────────────────────────────────────────────────────
# YouTube's own colors, dark and light. The style sheet and the drawing code
# use roles ("text", "field"…) rather than colors, so a theme change just
# rebuilds them (see apply_theme); status messages name a role too.
THEMES = {
    "dark": {
        "bg":           "#0f0f0f",
        "surface":      "#1f1f1f",   # the log, a row under the mouse, an empty thumbnail
        "field":        "#121212",   # text boxes
        "field_border": "#303030",
        "blue":         "#3ea6ff",   # the text box being typed in, a switch that's on
        "button":       "#272727",   # secondary buttons and chips
        "button_hover": "#3f3f3f",
        "chip_on":      "#f1f1f1",   # the selected chip
        "chip_on_text": "#0f0f0f",
        "accent":       "#ff0033",   # Download, the progress bars, the logo
        "accent_hover": "#d9002b",
        "accent_text":  "#ffffff",
        "divider":      "#303030",
        "scroll":       "#4d4d4d",   # scroll bars, a switch that's off
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

# The Theme chips: (label, saved value). "system" follows the OS.
THEME_MODES = [("System", "system"), ("Light", "light"), ("Dark", "dark")]

# Colors for the status line, as theme roles.
TEXT     = "text"
TEXT_DIM = "text_dim"
SUCCESS  = "success"
WARNING  = "warning"
ERROR    = "error"

# Segoe UI on Windows, the system font elsewhere.
UI_FONT   = "Segoe UI" if sys.platform == "win32" else ""
MONO_FONT = "Consolas" if sys.platform == "win32" else "monospace"

URL_PLACEHOLDER = "Paste one or more links: https://www.youtube.com/watch?v=..."

FFMPEG_MISSING_STATUS = "ffmpeg not found: see the log"

# Format and quality are two choices; quality only applies to video. The
# value given to the engine (engine.format_opts) is the quality's for video,
# the format's for audio.
FORMATS = [("MP4 video", "video"), ("MP3 audio", "mp3"), ("M4A audio", "m4a")]
QUALITIES = [("Best quality", "mp4_best"), ("1080p", "1080p"),
             ("720p", "720p"), ("480p", "480p")]
FORMAT_VALUES = [value for _, value in QUALITIES] + ["mp3", "m4a"]

# Heights at 96 DPI (Qt scales them); a pill's corner radius in the style
# sheet is half its height, since Qt draws a square corner when it's more.
SEARCH, PILL, PRIMARY, CHIP = 40, 36, 44, 30
THUMB = QSize(*engine.THUMB_SIZE)   # a thumbnail in the list
ROW_HEIGHT = 92


def settings_path():
    """Where the last folder, format and theme are remembered between runs."""
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


def application(argv=None):
    """The QApplication (there's one per process), set up for the app's look."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv or sys.argv[:1])
        app.setStyle("Fusion")   # so the style sheet looks the same everywhere
        if UI_FONT:
            app.setFont(QFont(UI_FONT, 10))
    return app


# ── The Downloads list ──────────────────────────────────────────────────────

# A row's state: (what the Status column says, theme role of its color).
STATES = {
    "waiting":     ("Waiting",        "text_dim"),
    "looking":     ("Looking up…",    "text_dim"),
    "downloading": ("Downloading",    "blue"),
    "processing":  ("Processing…",    "blue"),
    "done":        ("Completed",      "success"),
    "failed":      ("Failed",         "error"),
    "canceled":    ("Canceled",       "text_dim"),
    "declined":    ("Not downloaded", "text_dim"),
    "empty":       ("Nothing found",  "warning"),   # e.g. an empty playlist
}
ACTIVE = {"downloading", "processing"}
FINISHED = {"done", "failed", "canceled", "declined", "empty"}

# What can be done with a row: (what its icon's tooltip and the right-click
# menu say, its key in the list, the Qt keys).
ROW_ACTIONS = {
    "cancel": ("Cancel", "Esc", (Qt.Key_Escape,)),
    "folder": ("Show in folder", "Enter", (Qt.Key_Return, Qt.Key_Enter)),
    "remove": ("Remove from the list (the file stays)", "Delete",
               (Qt.Key_Delete, Qt.Key_Backspace)),   # Backspace: the Mac's Delete key
}
ACTION_KEYS = {key: name for name, (_, _, keys) in ROW_ACTIONS.items() for key in keys}


def row_actions(row):
    """The actions row has now, in the order of its icons."""
    names = []
    if row.state not in FINISHED:
        names.append("cancel")
    if row.file:
        names.append("folder")
    if row.state in FINISHED:
        names.append("remove")
    return names


@dataclass(eq=False)
class Row:
    """One link in the list. Only the main thread changes it, except for
    `latest` (the download thread's newest progress) and `job`."""
    url: str
    fmt: str                 # for engine.format_opts, e.g. "1080p" or "mp3"
    folder: str
    whole_playlist: bool
    title: str = ""
    details: str = ""        # length · channel, or how a playlist went
    thumb: QPixmap | None = None
    state: str = "waiting"
    pct: float = 0
    message: str = ""        # speed and ETA, or why it failed
    file: str | None = None  # the last file it saved (or found already there)
    log: list[str] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    job: engine.DownloadJob | None = None
    latest: tuple[float, str] | None = None

    @property
    def format_label(self):
        return self.fmt.upper() if self.fmt in ("mp3", "m4a") else "MP4"

    @property
    def quality_label(self):
        return dict((value, label) for label, value in QUALITIES).get(self.fmt, "—")


class DownloadsModel(QAbstractTableModel):
    COLUMNS: ClassVar[list[str]] = ["Video", "Format", "Quality", "Progress", "Status", ""]
    VIDEO, FORMAT, QUALITY, PROGRESS, STATUS, ACTIONS = range(6)

    def __init__(self):
        super().__init__()
        self.rows: list[Row] = []
        self.colors = THEMES["dark"]

    def rowCount(self, parent=QModelIndex()):  # noqa: B008  (Qt's own signature)
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: B008
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        row = self.rows[index.row()]
        col = index.column()
        if role == Qt.UserRole:
            return row
        if role == Qt.DisplayRole:
            if col == self.FORMAT:
                return row.format_label
            if col == self.QUALITY:
                return row.quality_label
            if col == self.STATUS:
                return STATES[row.state][0]
        if role == Qt.ForegroundRole and col == self.STATUS:
            return QColor(self.colors[STATES[row.state][1]])
        if role == Qt.ToolTipRole:
            if col == self.VIDEO:
                return row.url
            if col == self.STATUS:   # what the log says about this link
                return "\n".join(row.log[-8:]) or None
        return None

    def add(self, row):
        self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows))
        self.rows.append(row)
        self.endInsertRows()

    def changed(self, row):
        if row in self.rows:
            r = self.rows.index(row)
            self.dataChanged.emit(self.index(r, 0), self.index(r, len(self.COLUMNS) - 1))

    def remove(self, row):
        if row in self.rows:
            r = self.rows.index(row)
            self.beginRemoveRows(QModelIndex(), r, r)
            self.rows.pop(r)
            self.endRemoveRows()


class RowDelegate(QStyledItemDelegate):
    """Draws the Video, Progress and Actions cells, and handles clicks on
    the action icons. The other cells are plain text."""

    action = Signal(object, str)   # (row, "cancel" / "folder" / "remove")

    ICON = 30   # an action icon's round hover area

    def __init__(self, view, model):
        super().__init__(view)
        self.view, self.model = view, model
        self._hover = None   # (row, action) under the mouse

    # Which icons a row has, and where.
    def icons(self, rect, row):
        size = self.ICON
        y = rect.center().y() - size / 2
        return [(name, QRectF(rect.left() + 8 + i * (size + 4), y, size, size))
                for i, name in enumerate(row_actions(row))]

    def paint(self, painter, option, index):
        c = self.model.colors
        row = index.data(Qt.UserRole)
        painter.save()
        rect = QRectF(option.rect)
        # The selected row (the one the keys act on) stands out while the
        # list has the keyboard; otherwise it looks like a row under the mouse.
        # (Filled before antialiasing: on a scaled screen, antialiased edges
        # would leave thin seams between the cells.)
        selected = option.state & QStyle.State_Selected
        if selected and self.view.hasFocus():
            painter.fillRect(option.rect, QColor(c["button"]))
        elif selected or option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, QColor(c["surface"]))
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setPen(QPen(QColor(c["divider"]), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        col = index.column()
        if col == DownloadsModel.VIDEO:
            self._paint_video(painter, rect, row, option.font)
        elif col == DownloadsModel.PROGRESS:
            self._paint_progress(painter, rect, row, option.font)
        elif col == DownloadsModel.ACTIONS:
            self._paint_actions(painter, rect, row)
        else:
            painter.setPen(index.data(Qt.ForegroundRole) or QColor(c["text"]))
            painter.setFont(option.font)
            painter.drawText(rect.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft,
                             index.data(Qt.DisplayRole) or "")
        painter.restore()

    def _paint_video(self, painter, rect, row, font):
        c = self.model.colors
        thumb = QRectF(rect.left() + 12, rect.center().y() - THUMB.height() / 2,
                       THUMB.width(), THUMB.height())
        shape = QPainterPath()
        shape.addRoundedRect(thumb, 8, 8)
        if row.thumb is not None:
            painter.save()
            painter.setClipPath(shape)
            painter.drawPixmap(thumb, row.thumb, cover(row.thumb, thumb))
            painter.restore()
        else:
            painter.fillPath(shape, QColor(c["button" if row.state == "looking" else "surface"]))
            play_icon(painter, thumb.center(), 10, QColor(c["text_dim"]))

        left = thumb.right() + 14
        width = rect.right() - left - 8
        title_font = QFont(font)
        title_font.setPointSizeF(font.pointSizeF() * 1.1)
        title_font.setWeight(QFont.DemiBold)
        metrics = QFontMetrics(title_font)
        painter.setFont(title_font)
        painter.setPen(QColor(c["text"]))
        top = rect.center().y() - metrics.height() - 1
        painter.drawText(QRectF(left, top, width, metrics.height()), Qt.AlignLeft,
                         metrics.elidedText(row.title or row.url, Qt.ElideRight, int(width)))
        painter.setFont(font)
        painter.setPen(QColor(c["text_dim"]))
        details = row.details or ("Looking up…" if row.state == "looking" else "")
        painter.drawText(QRectF(left, rect.center().y() + 3, width, metrics.height()),
                         Qt.AlignLeft, QFontMetrics(font).elidedText(
                             details, Qt.ElideRight, int(width)))

    def _paint_progress(self, painter, rect, row, font):
        c = self.model.colors
        inner = rect.adjusted(8, 0, -12, 0)
        bar = QRectF(inner.left(), rect.center().y() - 2, inner.width() - 44, 4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(c["button"]))
        painter.drawRoundedRect(bar, 2, 2)
        pct = 100 if row.state == "done" else row.pct
        if pct > 0 and row.state != "failed":
            done = QRectF(bar.left(), bar.top(), bar.width() * pct / 100, bar.height())
            painter.setBrush(QColor(c["success" if row.state == "done" else "accent"]))
            painter.drawRoundedRect(done, 2, 2)
        small = QFont(font)
        small.setPointSizeF(font.pointSizeF() * 0.9)
        painter.setFont(small)
        painter.setPen(QColor(c["text_dim"]))
        painter.drawText(QRectF(bar.right() + 6, bar.center().y() - 10, 40, 20),
                         Qt.AlignVCenter | Qt.AlignRight,
                         f"{pct:.0f}%" if row.state in ACTIVE | {"done"} else "")
        below = QRectF(inner.left(), bar.bottom() + 6, inner.width(), 18)
        painter.setPen(QColor(c["error" if row.state == "failed" else "text_dim"]))
        painter.drawText(below, Qt.AlignLeft | Qt.AlignTop, QFontMetrics(small).elidedText(
            row.message, Qt.ElideRight, int(below.width())))

    def _paint_actions(self, painter, rect, row):
        c = self.model.colors
        for name, area in self.icons(rect, row):
            hovered = self._hover == (row, name)
            if hovered:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(c["button_hover"]))
                painter.drawEllipse(area)
            color = (c["error"] if hovered and name == "remove" else
                     c["text"] if hovered else c["text_dim"])
            draw_icon(painter, name, area, QColor(color))

    def editorEvent(self, event, model, option, index):
        if index.column() != DownloadsModel.ACTIONS:
            if self._hover:
                self._hover = None
                self.view.viewport().update()
            return False
        row = index.data(Qt.UserRole)
        if event.type() in (QEvent.MouseMove, QEvent.MouseButtonRelease):
            hit = next((name for name, area in self.icons(QRectF(option.rect), row)
                        if area.contains(event.position())), None)
            hover = (row, hit) if hit else None
            if hover != self._hover:
                self._hover = hover
                self.view.viewport().update()
            if event.type() == QEvent.MouseButtonRelease and hit:
                self.action.emit(row, hit)
                return True
        return False

    def helpEvent(self, event, view, option, index):
        if index.column() == DownloadsModel.ACTIONS:
            row = index.data(Qt.UserRole)
            for name, area in self.icons(QRectF(option.rect), row):
                if area.contains(QPointF(event.pos())):
                    text, key, _ = ROW_ACTIONS[name]
                    QToolTip.showText(event.globalPos(), f"{text} ({key})", view)
                    return True
            QToolTip.hideText()
            return True
        return super().helpEvent(event, view, option, index)

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width(), ROW_HEIGHT)


class DownloadsView(QTableView):
    """The table, with a hint in place of the rows while there are none.

    The selected row's actions also work from the keyboard (ROW_ACTIONS'
    keys: Esc cancels, Enter shows the file, Delete removes the row), from
    a right-click menu, and a double-click shows the file."""

    action = Signal(object, str)   # (row, "cancel" / "folder" / "remove")

    def __init__(self, model):
        super().__init__()
        self.setModel(model)
        self.setMouseTracking(True)
        self.setShowGrid(False)
        self.setSelectionMode(QTableView.SingleSelection)
        self.setSelectionBehavior(QTableView.SelectRows)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setTabKeyNavigation(False)   # Tab leaves the list, as elsewhere
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.doubleClicked.connect(self._double_clicked)
        self.setWordWrap(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.setVerticalScrollMode(QTableView.ScrollPerPixel)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(DownloadsModel.VIDEO, QHeaderView.Stretch)
        widths = {DownloadsModel.FORMAT: 80, DownloadsModel.QUALITY: 110,
                  DownloadsModel.PROGRESS: 220, DownloadsModel.STATUS: 130,
                  DownloadsModel.ACTIONS: 120}
        for col, width in widths.items():
            header.setSectionResizeMode(col, QHeaderView.Fixed)
            header.resizeSection(col, width)

    def current_row(self):
        index = self.currentIndex()
        return self.model().rows[index.row()] if index.isValid() else None

    def _act(self, row, name):
        if row is not None and name in row_actions(row):
            self.action.emit(row, name)

    def keyPressEvent(self, event):
        name = ACTION_KEYS.get(event.key())
        if name and not event.modifiers() & ~Qt.KeypadModifier:
            self._act(self.current_row(), name)
            event.accept()
            return
        super().keyPressEvent(event)

    # Tabbing into the list selects a row, so the keys have one to act on.
    def focusInEvent(self, event):
        super().focusInEvent(event)
        if not self.currentIndex().isValid() and self.model().rowCount():
            self.setCurrentIndex(self.model().index(0, 0))

    def _double_clicked(self, index):
        if index.column() != DownloadsModel.ACTIONS:   # not a double-clicked icon
            self._act(self.model().rows[index.row()], "folder")

    def row_menu(self, row):
        """The right-click menu: every action, with its key; the ones the row
        doesn't have now are grayed out."""
        menu = QMenu(self)
        available = row_actions(row)
        for name, (text, key, _keys) in ROW_ACTIONS.items():
            item = menu.addAction(f"{text}\t{key}")
            item.setEnabled(name in available)
            item.triggered.connect(lambda _checked=False, n=name: self._act(row, n))
        return menu

    def _show_menu(self, pos):
        index = self.indexAt(pos)
        if not index.isValid():
            return
        self.setCurrentIndex(index)
        menu = self.row_menu(self.model().rows[index.row()])
        menu.exec(self.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.model().rowCount() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(QColor(self.model().colors["text_dim"]))
            painter.drawText(self.viewport().rect(), Qt.AlignCenter,
                             "Paste a link and click Download.\n"
                             "Your downloads show up here.")


# ── Drawing ─────────────────────────────────────────────────────────────────

def cover(pixmap, target):
    """The part of pixmap that fills target's shape (cropped, not squeezed)."""
    w, h = pixmap.width(), pixmap.height()
    want = target.width() / target.height()
    if w / h > want:
        crop = h * want
        return QRectF((w - crop) / 2, 0, crop, h)
    crop = w / want
    return QRectF(0, (h - crop) / 2, w, crop)


def play_icon(painter, center, size, color):
    path = QPainterPath()
    path.moveTo(center.x() - size * 0.4, center.y() - size * 0.5)
    path.lineTo(center.x() + size * 0.5, center.y())
    path.lineTo(center.x() - size * 0.4, center.y() + size * 0.5)
    path.closeSubpath()
    painter.fillPath(path, color)


def draw_icon(painter, name, area, color):
    """The rows' action icons, drawn so they take the theme's colors."""
    painter.save()
    pen = QPen(color, 1.6)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    x, y = area.center().x(), area.center().y()
    if name == "cancel":
        painter.drawLine(QPointF(x - 5, y - 5), QPointF(x + 5, y + 5))
        painter.drawLine(QPointF(x - 5, y + 5), QPointF(x + 5, y - 5))
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(x - 8, y + 6)
        for px, py in ((x - 8, y - 6), (x - 3, y - 6), (x - 1, y - 4),
                       (x + 8, y - 4), (x + 8, y + 6)):
            path.lineTo(px, py)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "remove":   # a trash can
        painter.drawLine(QPointF(x - 7, y - 5), QPointF(x + 7, y - 5))
        painter.drawLine(QPointF(x - 2, y - 7), QPointF(x + 2, y - 7))
        body = QPainterPath()
        body.moveTo(x - 5.5, y - 5)
        body.lineTo(x - 4.5, y + 7)
        body.lineTo(x + 4.5, y + 7)
        body.lineTo(x + 5.5, y - 5)
        painter.drawPath(body)
    painter.restore()


def _pixmap(width, height, ratio):
    pixmap = QPixmap(round(width * ratio), round(height * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    return pixmap


def logo_pixmap(height, ratio):
    """The header's logo: a white play triangle on a red rounded rectangle."""
    width = round(height * 1.4)
    pixmap = _pixmap(width, height, ratio)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(THEMES["dark"]["accent"]))
    painter.drawRoundedRect(QRectF(0, 0, width, height), height * 0.28, height * 0.28)
    play_icon(painter, QPointF(width / 2 + height * 0.03, height / 2), height * 0.42,
              QColor("white"))
    painter.end()
    return pixmap


def download_icon(size, ratio):
    """A white arrow onto a line, for the Download button (a "⬇" character
    comes out as a color emoji on Windows)."""
    pixmap = _pixmap(size, size, ratio)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor("white"), size * 0.12)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    x = size / 2
    painter.drawLine(QPointF(x, size * 0.12), QPointF(x, size * 0.68))
    painter.drawPolyline([QPointF(x - size * 0.25, size * 0.45), QPointF(x, size * 0.7),
                          QPointF(x + size * 0.25, size * 0.45)])
    painter.drawLine(QPointF(size * 0.15, size * 0.88), QPointF(size * 0.85, size * 0.88))
    painter.end()
    return pixmap


def to_qimage(image):
    """A Pillow image as a QImage (a copy, so it owns its pixels). Safe on a
    worker thread, unlike a QPixmap."""
    image = image.convert("RGB")
    data = image.tobytes("raw", "RGB")
    return QImage(data, image.width, image.height, 3 * image.width,
                  QImage.Format_RGB888).copy()


def write_svgs(folder, c):
    """The small images the style sheet needs (a drop-down arrow, a switch),
    in a theme's colors. Returns {name: path}."""
    svgs = {
        "arrow": f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12">'
                 f'<path d="M2 4 L6 8 L10 4" fill="none" stroke="{c["text_dim"]}" '
                 f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        "switch_off": f'<svg xmlns="http://www.w3.org/2000/svg" width="34" height="20">'
                      f'<rect width="34" height="20" rx="10" fill="{c["scroll"]}"/>'
                      f'<circle cx="10" cy="10" r="7" fill="white"/></svg>',
        "switch_on": f'<svg xmlns="http://www.w3.org/2000/svg" width="34" height="20">'
                     f'<rect width="34" height="20" rx="10" fill="{c["blue"]}"/>'
                     f'<circle cx="24" cy="10" r="7" fill="white"/></svg>',
    }
    paths = {}
    for name, svg in svgs.items():
        paths[name] = os.path.join(folder, f"{name}.svg").replace("\\", "/")
        with open(paths[name], "w", encoding="utf-8") as f:
            f.write(svg)
    return paths


def style_sheet(c, svg):
    return f"""
    QWidget {{ background: {c["bg"]}; color: {c["text"]}; }}
    QLabel#dim {{ color: {c["text_dim"]}; }}
    QLabel#heading {{ font-size: 11pt; font-weight: 600; }}
    QLabel#title {{ font-size: 15pt; font-weight: 700; }}
    QFrame#divider {{ background: {c["divider"]}; max-height: 1px; }}

    QLineEdit {{ background: {c["field"]}; border: 1px solid {c["field_border"]};
                 border-radius: 8px; padding: 0 12px; selection-background-color: {c["blue"]}; }}
    QLineEdit:focus {{ border-color: {c["blue"]}; }}
    /* The URL box and Paste button join up like YouTube's search box. */
    QLineEdit#url {{ border-radius: 20px; border-top-right-radius: 0;
                     border-bottom-right-radius: 0; padding: 0 18px; font-size: 11pt; }}
    QPushButton#search {{ border-radius: 20px; border-top-left-radius: 0;
                          border-bottom-left-radius: 0; border: 1px solid {c["field_border"]};
                          border-left: none; padding: 0 22px; }}

    QPushButton {{ background: {c["button"]}; border: none; border-radius: 18px;
                   padding: 0 18px; font-weight: 500; }}
    QPushButton:hover {{ background: {c["button_hover"]}; }}
    QPushButton:disabled {{ color: {c["disabled"]}; }}
    QPushButton#primary {{ background: {c["accent"]}; color: {c["accent_text"]};
                           font-weight: 700; font-size: 11pt; padding: 0 28px;
                           border-radius: 22px; }}
    QPushButton#primary:hover {{ background: {c["accent_hover"]}; }}
    QPushButton#chip {{ border-radius: 8px; padding: 0 12px; }}
    QPushButton#chip:checked {{ background: {c["chip_on"]}; color: {c["chip_on_text"]}; }}

    QComboBox {{ background: {c["field"]}; border: 1px solid {c["field_border"]};
                 border-radius: 8px; padding: 0 12px; min-width: 110px; }}
    QComboBox:focus, QComboBox:on {{ border-color: {c["blue"]}; }}
    QComboBox:disabled {{ color: {c["disabled"]}; }}
    QComboBox::drop-down {{ border: none; width: 28px; }}
    QComboBox::down-arrow {{ image: url({svg["arrow"]}); width: 12px; height: 12px; }}
    QComboBox QAbstractItemView {{ background: {c["surface"]}; border: 1px solid {c["divider"]};
                                   selection-background-color: {c["button_hover"]};
                                   selection-color: {c["text"]}; outline: 0; padding: 4px; }}

    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 34px; height: 20px; }}
    QCheckBox::indicator:unchecked {{ image: url({svg["switch_off"]}); }}
    QCheckBox::indicator:checked {{ image: url({svg["switch_on"]}); }}

    QTableView {{ border: none; }}
    QHeaderView::section {{ background: {c["bg"]}; color: {c["text_dim"]}; border: none;
                            border-bottom: 1px solid {c["divider"]}; padding: 8px;
                            font-weight: 600; }}
    QPlainTextEdit#log {{ background: {c["surface"]}; color: {c["text_dim"]}; border: none;
                          border-radius: 8px; padding: 6px 8px; font-family: "{MONO_FONT}";
                          font-size: 9pt; selection-background-color: {c["blue"]}; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {c["scroll"]}; border-radius: 4px;
                                   min-height: 30px; margin: 2px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QToolTip {{ background: {c["surface"]}; color: {c["text"]};
                border: 1px solid {c["divider"]}; padding: 4px 6px; }}
    QMenu {{ background: {c["surface"]}; border: 1px solid {c["divider"]}; padding: 4px; }}
    QMenu::item {{ padding: 6px 16px 6px 12px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {c["button_hover"]}; }}
    QMenu::item:disabled {{ color: {c["disabled"]}; }}
    """


# ── The window ──────────────────────────────────────────────────────────────

class YouTubeDownloader(QMainWindow):
    # Qt isn't thread-safe: worker threads must not touch widgets. They
    # _post() calls, which Qt delivers on the main thread (see _call), and
    # a download's progress goes through Row.latest (see _show_progress).
    _call_on_main = Signal(object, tuple)

    def __init__(self, *, update_check=True):
        application()
        super().__init__()
        self.setWindowTitle("YT Downloader")
        self._call_on_main.connect(self._call, Qt.QueuedConnection)

        # State
        self._default_folder = os.path.normpath(os.path.expanduser("~/Downloads"))
        self._unavailable_folder = None   # the saved folder, if it's missing now
        self._folder = self._default_folder
        self._format = "mp4_best"
        self.theme_mode = "system"
        self._check_updates = True
        self._apply_saved_settings()
        self.model = DownloadsModel()
        self._running = None              # the row downloading now
        self._active_workers = 0
        self._lookup_slots = threading.Semaphore(3)   # lookups at a time
        self._closing = False
        self._close_when_stopped = False
        self._installing = False          # the in-app yt-dlp install is running
        self._status_role = TEXT_DIM
        self._requested_scheme = None
        self._svg_dir = tempfile.TemporaryDirectory(prefix="yt-downloader-")
        self._js_runtime = None
        self._ffmpeg = None

        self._build_ui()
        self.apply_theme()
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            lambda _scheme: self.apply_theme())
        # A download's newest progress is shown 10 times a second.
        self._progress_timer = QTimer(self, interval=100, timeout=self._show_progress)
        self._fit_window()

        if not engine.YT_DLP_AVAILABLE:
            QTimer.singleShot(300, self._prompt_install)
        self._ffmpeg_missing = not engine.find_ffmpeg()
        if self._ffmpeg_missing:
            self._set_status(FFMPEG_MISSING_STATUS, WARNING)
            self._log(ffmpeg_help(startup=True), show=True)
        if engine.YT_DLP_AVAILABLE:   # it runs "<runtime> --version": a worker's job
            self._start_worker(self._find_js_runtime_thread)
        self._note_yt_dlp_version()
        if self._unavailable_folder:
            self._log(f"• The saved folder {self._unavailable_folder} isn't available "
                      f"(an unplugged drive?); saving to {self._default_folder} for now.",
                      show=True)
        if update_check:
            self._maybe_check_for_update()

    # ── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        page = QVBoxLayout(root)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)

        # Header: logo, name and versions; the theme on the right.
        header = QHBoxLayout()
        header.setContentsMargins(20, 12, 20, 12)
        header.setSpacing(4)
        ratio = self.devicePixelRatioF()
        logo = QLabel()
        logo.setPixmap(logo_pixmap(30, ratio))
        self.setWindowIcon(QIcon(logo_pixmap(32, 2)))
        header.addWidget(logo)
        header.addSpacing(8)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(QLabel("YT Downloader", objectName="title"))
        self.powered_lbl = QLabel(self._powered_by_text(), objectName="dim")
        names.addWidget(self.powered_lbl)
        header.addLayout(names)
        header.addStretch()
        right = QVBoxLayout()
        right.setSpacing(6)
        chips = QHBoxLayout()
        chips.setSpacing(4)
        chips.addStretch()
        chips.addWidget(QLabel("Theme", objectName="dim"))
        chips.addSpacing(6)
        group = QButtonGroup(self)   # one checked at a time
        self.theme_chips = {}
        for label, mode in THEME_MODES:
            chip = QPushButton(label, objectName="chip", checkable=True,
                               cursor=Qt.PointingHandCursor)
            chip.setFixedHeight(CHIP)
            chip.clicked.connect(lambda _checked=False, m=mode: self._choose_theme(m))
            group.addButton(chip)
            chips.addWidget(chip)
            self.theme_chips[mode] = chip
        right.addLayout(chips)
        self.update_switch = None
        if engine.app_version():   # only a release .exe checks
            self.update_switch = QCheckBox("Check for updates at startup",
                                           cursor=Qt.PointingHandCursor)
            self.update_switch.setChecked(self._check_updates)
            self.update_switch.toggled.connect(self._update_switch_toggled)
            right.addWidget(self.update_switch, 0, Qt.AlignRight)
        header.addLayout(right)
        page.addLayout(header)
        page.addWidget(QFrame(objectName="divider"))

        body = QVBoxLayout()
        body.setContentsMargins(24, 16, 24, 10)
        body.setSpacing(10)
        page.addLayout(body, 1)

        # Links, in a box like YouTube's search box. Several can go in,
        # separated by spaces; each becomes a row. Enter downloads.
        body.addWidget(QLabel("YouTube URLs", objectName="heading"))
        search = QHBoxLayout()
        search.setSpacing(0)
        self.url = QLineEdit(objectName="url", placeholderText=URL_PLACEHOLDER)
        self.url.setFixedHeight(SEARCH)
        self.url.returnPressed.connect(self._start_download)
        self.url.textChanged.connect(self._one_line)
        paste = QPushButton("Paste", objectName="search", cursor=Qt.PointingHandCursor)
        paste.setFixedHeight(SEARCH)
        paste.clicked.connect(self._paste_url)
        search.addWidget(self.url, 1)
        search.addWidget(paste)
        body.addLayout(search)

        # Format, quality and folder
        options = QGridLayout()
        options.setHorizontalSpacing(12)
        options.setVerticalSpacing(4)
        self.format = QComboBox(cursor=Qt.PointingHandCursor)
        for label, value in FORMATS:
            self.format.addItem(label, value)
        self.quality = QComboBox(cursor=Qt.PointingHandCursor)
        for label, value in QUALITIES:
            self.quality.addItem(label, value)
        self.format.currentIndexChanged.connect(
            lambda: self.quality.setEnabled(self.format.currentData() == "video"))
        self._set_format(self._format)
        self.path = QLineEdit(self._folder)
        browse = QPushButton("Browse", cursor=Qt.PointingHandCursor)
        browse.clicked.connect(self._browse_folder)
        for widget in (self.format, self.quality, self.path, browse):
            widget.setFixedHeight(PILL)
        for col, text in enumerate(("Format", "Quality", "Save to")):
            options.addWidget(QLabel(text, objectName="dim"), 0, col)
        options.addWidget(self.format, 1, 0)
        options.addWidget(self.quality, 1, 1)
        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        folder_row.addWidget(self.path, 1)
        folder_row.addWidget(browse)
        options.addLayout(folder_row, 1, 2)
        options.setColumnStretch(2, 1)
        body.addLayout(options)

        # Whole playlist, and the Download button
        actions = QHBoxLayout()
        self.whole_playlist = QCheckBox("Whole playlist", cursor=Qt.PointingHandCursor)
        self.dl_btn = QPushButton(" Download", objectName="primary",
                                  cursor=Qt.PointingHandCursor)
        self.dl_btn.setFixedHeight(PRIMARY)
        self.dl_btn.setIcon(QIcon(download_icon(18, 3)))
        self.dl_btn.setIconSize(QSize(18, 18))
        self.dl_btn.clicked.connect(self._start_download)
        actions.addWidget(self.whole_playlist)
        actions.addStretch()
        actions.addWidget(self.dl_btn)
        body.addLayout(actions)

        # The list
        list_head = QHBoxLayout()
        list_head.setContentsMargins(0, 8, 0, 0)
        list_head.addWidget(QLabel("Downloads", objectName="heading"))
        list_head.addSpacing(8)
        self.count_lbl = QLabel(objectName="dim")
        list_head.addWidget(self.count_lbl)
        list_head.addStretch()
        clear = QPushButton("Clear finished", cursor=Qt.PointingHandCursor)
        clear.setFixedHeight(PILL)
        clear.clicked.connect(self._clear_finished)
        list_head.addWidget(clear)
        body.addLayout(list_head)

        self.view = DownloadsView(self.model)
        self.delegate = RowDelegate(self.view, self.model)
        self.delegate.action.connect(self._row_action)
        self.view.action.connect(self._row_action)   # keys, menu, double-click
        self.view.setItemDelegate(self.delegate)
        body.addWidget(self.view, 1)

        # The log: hidden until asked for, or until something needs reading.
        self.log_box = QPlainTextEdit(objectName="log", readOnly=True)
        self.log_box.setFixedHeight(130)
        self.log_box.hide()
        body.addWidget(self.log_box)

        # Status line, the download speed and the Log button
        footer = QHBoxLayout()
        self.status_lbl = QLabel("Ready")
        self.status_lbl.setWordWrap(True)
        self.speed_lbl = QLabel(objectName="dim")
        self.log_btn = QPushButton("Log", objectName="chip", checkable=True,
                                   cursor=Qt.PointingHandCursor)
        self.log_btn.setFixedHeight(CHIP)
        self.log_btn.toggled.connect(self.log_box.setVisible)
        footer.addWidget(self.status_lbl, 1)
        footer.addWidget(self.speed_lbl)
        footer.addSpacing(8)
        footer.addWidget(self.log_btn)
        body.addLayout(footer)

        for signal in (self.model.rowsInserted, self.model.rowsRemoved,
                       self.model.dataChanged):
            signal.connect(self._update_counts)
        self._update_counts()

    def _fit_window(self):
        # A comfortable size, but no bigger than the screen without the taskbar.
        screen = self.screen().availableGeometry()
        width, height = min(1060, screen.width()), min(780, screen.height() - 40)
        self.setMinimumSize(min(820, width), min(600, height))
        self.resize(width, height)
        self.move(screen.center().x() - width // 2,
                  screen.top() + max(0, (screen.height() - 40 - height) // 2))

    # ── Settings ────────────────────────────────────────────────────────────

    # The folder, format, theme and the update switch are remembered (when a
    # download starts, a theme is chosen, the switch is clicked, and the app
    # closes). "Whole playlist" always starts off.
    def _apply_saved_settings(self):
        saved = load_settings()
        if saved.get("format") in FORMAT_VALUES:
            self._format = saved["format"]
        folder = saved.get("folder")
        if isinstance(folder, str) and os.path.isdir(folder):
            self._folder = os.path.normpath(folder)
        elif isinstance(folder, str) and folder:
            # Kept, not forgotten: it may be on a drive that's unplugged now.
            self._unavailable_folder = folder
        if isinstance(saved.get("check_updates"), bool):
            self._check_updates = saved["check_updates"]
        if saved.get("theme") in {mode for _, mode in THEME_MODES}:
            self.theme_mode = saved["theme"]

    def _save_settings(self):
        folder = self.path.text().strip()
        if self._unavailable_folder and folder == self._default_folder:
            folder = self._unavailable_folder   # still the stand-in: keep the saved one
        # Keeps the other keys, e.g. when updates were last checked.
        save_settings({**load_settings(),
                       "folder": folder,
                       "format": self._chosen_format(),
                       "check_updates": self._check_updates,
                       "theme": self.theme_mode})

    def _chosen_format(self):
        """The format as engine.format_opts names it, e.g. "720p" or "mp3"."""
        kind = self.format.currentData()
        return self.quality.currentData() if kind == "video" else kind

    def _set_format(self, value):
        if value in ("mp3", "m4a"):
            self.format.setCurrentIndex(self.format.findData(value))
        else:
            self.format.setCurrentIndex(self.format.findData("video"))
            self.quality.setCurrentIndex(self.quality.findData(value))

    def _update_switch_toggled(self, checked):
        self._check_updates = checked
        self._save_settings()

    # ── Themes ──────────────────────────────────────────────────────────────

    def _choose_theme(self, mode):
        self.theme_mode = mode
        self.apply_theme()
        self._save_settings()

    def _theme_name(self):
        """The theme shown: the one chosen, or else the system's (dark, the
        app's original look, if the system doesn't say)."""
        if self.theme_mode in THEMES:
            return self.theme_mode
        scheme = QGuiApplication.styleHints().colorScheme()
        return "light" if scheme == Qt.ColorScheme.Light else "dark"

    def apply_theme(self):
        # Telling Qt the scheme also makes Windows draw a matching title bar;
        # Unknown hands it back to the system.
        wanted = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(
            self.theme_mode, Qt.ColorScheme.Unknown)
        if wanted != self._requested_scheme:
            self._requested_scheme = wanted
            QGuiApplication.styleHints().setColorScheme(wanted)   # may call this again
        self.shown_theme = name = self._theme_name()
        self.colors = self.model.colors = colors = THEMES[name]
        self.theme_chips[self.theme_mode].setChecked(True)
        folder = os.path.join(self._svg_dir.name, name)
        os.makedirs(folder, exist_ok=True)
        self.setStyleSheet(style_sheet(colors, write_svgs(folder, colors)))
        self._paint_status()
        self.view.viewport().update()

    # ── Helpers ─────────────────────────────────────────────────────────────

    # Pasted text with line breaks (several links) goes in one line.
    def _one_line(self, text):
        if "\n" in text or "\r" in text:
            self.url.setText(" ".join(text.split()))

    # Adds the clipboard's links to those already in the box. Other words
    # (e.g. "Watch this:" before a link) are left out.
    def _paste_url(self):
        clip = QGuiApplication.clipboard().text()
        links = split_links(clip)[0]
        if not links:
            self._set_status("The clipboard has no link to paste.", WARNING)
            return
        entered = split_links(self.url.text())[0]
        self.url.setText(" ".join(dict.fromkeys(entered + links)))
        self.url.setFocus()

    def _browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Save to", self.path.text())
        if path:
            self.path.setText(os.path.normpath(path))
            self._unavailable_folder = None   # a deliberate choice replaces it

    def _log(self, msg, show=False):
        self.log_box.appendPlainText(msg)
        bar = self.log_box.verticalScrollBar()
        bar.setValue(bar.maximum())
        if show:   # something to read: open the log
            self.log_btn.setChecked(True)

    def _set_status(self, msg, color=TEXT_DIM):   # color: a theme role
        self.status_lbl.setText(msg)
        self._status_role = color
        self._paint_status()

    def _paint_status(self):
        self.status_lbl.setStyleSheet(f"color: {self.colors[self._status_role]};")

    def _update_counts(self, *_):
        rows = self.model.rows
        done = sum(r.state == "done" for r in rows)
        self.count_lbl.setText(f"{len(rows)} · {done} completed" if rows else "")
        running = next((r for r in rows if r.state in ACTIVE), None)
        speed = re.search(r"[\d.]+ MB/s", running.message) if running else None
        self.speed_lbl.setText(f"⚡ {speed.group()}" if speed else "")

    # ── Worker threads ──────────────────────────────────────────────────────

    def _post(self, func, *args):
        self._call_on_main.emit(func, args)

    def _call(self, func, args):
        try:
            func(*args)
        except Exception:
            self.report_error(*sys.exc_info())

    def _start_worker(self, target, *args):
        self._active_workers += 1
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

    def _worker_finished(self):
        self._active_workers -= 1

    def _worker_crashed(self, details):
        self._log("✘ Unexpected error: " + details.strip().splitlines()[-1], show=True)
        if sys.stderr:   # a console (from source): the full traceback too
            print(details, file=sys.stderr)

    # An error in a call on the main thread (see also main's excepthook): the
    # windowed .exe has no console, so it goes to the log.
    def report_error(self, exc, val, tb):
        self._log("✘ Unexpected error: "
                  + "".join(traceback.format_exception_only(exc, val)).strip(), show=True)
        if sys.stderr:
            traceback.print_exception(exc, val, tb)

    # Runs on a worker thread.
    def _find_js_runtime_thread(self):
        self._post(self._js_runtime_found, engine.find_js_runtime(),
                   engine.js_solver_version())

    def _js_runtime_found(self, runtime, solver):
        self._js_runtime = runtime
        if runtime is None:
            self._log(js_runtime_help(), show=True)
        elif solver is None:
            self._log(js_solver_help(), show=True)

    # ── Adding links ────────────────────────────────────────────────────────

    def _start_download(self):
        if not engine.YT_DLP_AVAILABLE:
            if self._installing:
                self._set_status("Installing yt-dlp, please wait…", WARNING)
            else:
                self._prompt_install()
            return

        ffmpeg = engine.find_ffmpeg()
        self._ffmpeg_missing = not ffmpeg
        if self._ffmpeg_missing:
            QMessageBox.critical(self, "ffmpeg not found", ffmpeg_help())
            return
        self._ffmpeg = ffmpeg

        links, not_links = split_links(self.url.text())
        if not links:
            QMessageBox.warning(self, "No URL", "Please enter a YouTube URL.")
            return

        # A relative folder would save next to wherever the app was started.
        folder = os.path.expanduser(self.path.text().strip())
        if not folder or not os.path.isabs(folder):
            QMessageBox.warning(self, "Choose a folder",
                                "Please choose the folder to save to (Browse).")
            return
        folder = os.path.normpath(folder)
        self.path.setText(folder)
        self._save_settings()

        fmt = self._chosen_format()
        thumb = self._thumb_pixels()   # read here: workers mustn't touch widgets
        added = 0
        for url in links:
            if any(r.url == url and r.fmt == fmt and r.state not in FINISHED
                   for r in self.model.rows):
                continue   # already waiting or downloading
            row = Row(url, fmt, folder, self.whole_playlist.isChecked())
            self.model.add(row)
            self._start_worker(self._look_up, row, thumb, self._js_runtime)
            added += 1
        if not_links:
            self._log("• Not links, so left out: " + " ".join(not_links))
        self.url.clear()
        self.view.scrollToBottom()
        if added:
            self._run_next()
        else:
            self._set_status("Those links are already in the list.")

    def _thumb_pixels(self):
        ratio = self.devicePixelRatioF()
        return round(THUMB.width() * ratio), round(THUMB.height() * ratio)

    # Runs on a worker thread: the title, length, channel and thumbnail for
    # the row, a few links at a time.
    def _look_up(self, row, thumb, js_runtime):
        with self._lookup_slots:
            if row.cancel_event.is_set() or self._closing:
                return
            self._post(self._looked_up, row, None)   # "Looking up…"
            result = engine.preview_link(row.url, row.whole_playlist, row.cancel_event,
                                         js_runtime, thumb)
        if result is not None:   # None: canceled
            text, image, found = result
            self._post(self._looked_up, row, (text, image and to_qimage(image), found))

    def _looked_up(self, row, result):
        if result is None:
            if row.state == "waiting":
                row.state = "looking"
        else:
            text, image, found = result
            if row.state == "looking":
                row.state = "waiting"
            if found:
                row.title, _, details = text.partition("\n")
                row.details = row.details or details
            elif not row.title:
                row.details = text
            if image is not None:
                row.thumb = QPixmap.fromImage(image)
        self.model.changed(row)

    # ── Downloading, one link at a time ─────────────────────────────────────

    def _run_next(self):
        if self._running is not None or self._closing or self._close_when_stopped:
            return
        row = next((r for r in self.model.rows if r.state in ("waiting", "looking")), None)
        if row is None:
            self._queue_finished()
            return
        self._running = row
        row.state, row.pct, row.message = "downloading", 0, "Starting…"
        self.model.changed(row)
        self._set_status(f"Downloading {self._position(row)}…", WARNING)
        self._log(f"▶ {row.url}")
        self._progress_timer.start()
        self._start_worker(self._run_row, row, self._ffmpeg, self._js_runtime)

    def _position(self, row):
        """e.g. "2 of 3": where row is among those not finished yet."""
        todo = [r for r in self.model.rows if r.state not in FINISHED]
        return f"{todo.index(row) + 1} of {len(todo)}" if len(todo) > 1 else "1 link"

    # Runs on the download thread. The window hears that the row ended even
    # if it crashed, after the error is in the log.
    def _run_row(self, row, ffmpeg, js_runtime):
        job = None
        try:
            if js_runtime is None:   # installed since startup?
                js_runtime = engine.find_js_runtime()
            job = engine.DownloadJob(
                row.url, fmt=row.fmt, folder=row.folder, ffmpeg=ffmpeg,
                whole_playlist=row.whole_playlist,
                on_progress=lambda pct, msg: setattr(row, "latest", (pct, msg)),
                on_log=lambda msg: self._post(self._job_log, row, msg),
                ask=lambda question: self._ask_on_main(row, "Playlist", question),
                cancel_event=row.cancel_event, js_runtime=js_runtime)
            row.job = job
            if not row.cancel_event.is_set():
                job.run()
        except Exception:
            self._post(self._worker_crashed, traceback.format_exc())
            job = None
        finally:
            self._post(self._download_finished, row, job)

    def _job_log(self, row, msg):
        row.log.append(msg)
        self._log(msg)

    # Progress comes often; the newest is shown 10 times a second.
    def _show_progress(self):
        row = self._running
        if row is None or row.latest is None or row.cancel_event.is_set():
            return   # after Cancel, "Canceling…" stays
        pct, message = row.latest
        row.latest = None
        row.pct = pct
        row.state = "processing" if "Processing" in message else "downloading"
        # "Video 2/5 · Downloading… 42.0%  3.4 MB/s · ETA 0:07": the bar and
        # the percentage show the rest.
        row.message = re.sub(r"(Downloading… [\d.]+%|Processing…)\s*", "", message).strip(" ·")
        self.model.changed(row)

    def _download_finished(self, row, job):
        self._running = None
        self._progress_timer.stop()
        row.file = job.last_file if job else None
        if job is None or job.outcome is None:   # it crashed, or never started
            if row.cancel_event.is_set():
                row.state, row.message = "canceled", ""
            else:   # the error is in the log
                row.state, row.message = "failed", "An unexpected error (see the log)"
        elif job.outcome == "canceled":
            row.state, row.message = "canceled", ""
            self._log(f"✘ {job.summary()}")
        elif job.outcome == "failed":
            row.state, row.message = "failed", job.error or "See the log."
            if not job.error_logged:
                self._log(f"✘ {job.summary()}")
        elif job.outcome == "declined":
            row.state, row.message = "declined", ""
            self._log(f"✘ {job.summary()}")
        else:
            summary = job.summary()
            if summary == engine.NOTHING_TO_DOWNLOAD:   # e.g. an empty playlist
                row.state, row.message = "empty", summary
                self._log(f"• {summary}")
            else:
                row.state, row.message = "done", ""
                if summary != "Download complete!":   # a playlist: how it went
                    row.details = summary
                self._log("✔ Done!" if summary == "Download complete!" else f"✔ {summary}")
        self.model.changed(row)
        if self._close_when_stopped:   # the user is closing the app
            self.close()
            return
        self._run_next()

    # Says how the downloads went, once the last one has finished.
    def _queue_finished(self):
        finished = [r for r in self.model.rows if r.state in FINISHED]
        if not finished:
            return
        done = sum(r.state == "done" for r in finished)
        failed = sum(r.state == "failed" for r in finished)
        if failed and done:
            self._set_status(f"✔ Done, but {failed} link{'s' * (failed != 1)} failed.",
                             WARNING)
        elif failed:
            self._set_status("✘ The download failed (the row says why).", ERROR)
        elif done:
            self._set_status("✔ All downloads complete.", SUCCESS)
        else:
            self._set_status("Nothing downloaded.")
        QApplication.alert(self)   # flashes the taskbar if the window isn't in front

    # Runs on the download thread; blocks it until the user answers.
    def _ask_on_main(self, row, title, question):
        answer, done = [], threading.Event()

        # If the dialog fails, the error goes to the log and the answer is
        # No; either way the download thread stops waiting.
        def ask():
            try:
                if row.cancel_event.is_set():
                    answer.append(False)   # canceled before the question showed
                    return
                answer.append(QMessageBox.question(self, title, question)
                              == QMessageBox.Yes)
            except Exception as exc:
                self._log(f"✘ Couldn't ask about the playlist: {exc}", show=True)
                raise
            finally:
                done.set()

        self._post(ask)
        while not done.wait(0.1):
            if self._closing or row.cancel_event.is_set():
                return False
        return bool(answer and answer[0])

    # ── Row actions ─────────────────────────────────────────────────────────

    def _row_action(self, row, action):
        if action == "cancel":
            self._cancel_row(row)
        elif action == "folder":
            self._open_folder(row)
        elif action == "remove" and row.state in FINISHED:
            self.model.remove(row)

    # yt-dlp stops at its next progress update or status message (also while
    # it reads a playlist), and a running ffmpeg conversion or merge is
    # stopped; the row's partial file is removed.
    def _cancel_row(self, row):
        if row.state in FINISHED:
            return
        row.cancel_event.set()
        if row is self._running:
            if row.job is not None:
                row.job.stop_processes()
            row.message = "Canceling…"
        else:   # still waiting: nothing to stop
            row.state, row.message = "canceled", ""
        self.model.changed(row)

    def _open_folder(self, row):
        path = row.file
        if not path or not os.path.isdir(os.path.dirname(path)):
            QMessageBox.critical(self, "Folder not found",
                                 "The download folder doesn't exist anymore.")
            return
        try:
            reveal_in_file_manager(path)
        except OSError as exc:
            QMessageBox.critical(self, "Couldn't open the folder", str(exc))

    def _clear_finished(self):
        for row in [r for r in self.model.rows if r.state in FINISHED]:
            self.model.remove(row)

    # ── Closing ─────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        # During a download, ask first, then cancel it and close once it has
        # stopped, so its partial file is removed. Closing again while that
        # runs (e.g. stuck on a slow network request) closes right away.
        if self._running is not None and not self._close_when_stopped:
            if QMessageBox.question(self, "Download in progress",
                                    "A download is still running.\n\n"
                                    "Cancel it and close the app?") != QMessageBox.Yes:
                event.ignore()
                return
            # The download may have finished while the question was open.
            if self._running is not None:
                self._close_when_stopped = True
                for row in self.model.rows:
                    self._cancel_row(row)
                self._set_status("Canceling… the app closes when it has stopped.",
                                 WARNING)
                event.ignore()
                return
        self._closing = True
        for row in self.model.rows:
            row.cancel_event.set()   # stops lookups, and questions waiting
        self._progress_timer.stop()
        self._save_settings()
        event.accept()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._recheck_ffmpeg()

    # Coming back to the window (e.g. after installing ffmpeg) checks again.
    def _recheck_ffmpeg(self):
        if not self._ffmpeg_missing:
            return
        ffmpeg = engine.find_ffmpeg()
        if ffmpeg:
            self._ffmpeg_missing = False
            self._log(f"✔ ffmpeg found: {ffmpeg}")
            if self.status_lbl.text() == FFMPEG_MISSING_STATUS:
                self._set_status("Ready")

    # ── yt-dlp version, update check and install ────────────────────────────

    def _powered_by_text(self):
        version = engine.yt_dlp_version()
        text = f"Powered by yt-dlp {version}" if version else \
            "Powered by yt-dlp (not installed)"
        app = engine.app_version()
        return f"v{app} · {text}" if app else text

    # A release .exe asks GitHub (api.github.com) for its latest release, at
    # most once a day, while "Check for updates at startup" is on.
    def _maybe_check_for_update(self):
        if engine.app_version() is None or not self._check_updates:
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
            self._log(notice, show=True)

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
            QMessageBox.critical(
                self, "yt-dlp not found",
                "This copy of the app is missing yt-dlp. Please download it again.")
            return
        ans = QMessageBox.question(
            self, "yt-dlp not found",
            "yt-dlp is required but not installed.\n\n"
            "Install it now with pip?\n\n"
            f"(You can also run:  {pip_install_command()})")
        if ans == QMessageBox.Yes:
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
            self.powered_lbl.setText(self._powered_by_text())
            self._log(f"✔ yt-dlp {engine.yt_dlp_version()} installed and ready.")
            self._set_status("✔ yt-dlp installed. You can download now.", SUCCESS)
            # What startup skipped without yt-dlp: the JavaScript runtime check.
            self._start_worker(self._find_js_runtime_thread)
        elif ok:   # installed, but this Python can't import it until a restart
            self._log("✔ yt-dlp installed! Please restart the app.")
            self._set_status("✔ yt-dlp installed — please restart.", SUCCESS)
            QMessageBox.information(self, "Installed",
                                    "yt-dlp was installed successfully.\n"
                                    "Please restart the application.")
        else:
            self._log(f"✘ Install failed: {err}", show=True)
            self._set_status(f"Install failed. Try: {pip_install_command()}", ERROR)


def self_check(path):
    """Writes what this build found to a JSON file, then exits. Used to test
    the one-file .exe (packaging/build_exe.py --check), where there's no
    console to print to."""
    application()   # Qt finds its plugins (e.g. SVG) through the application
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
              "thumbnails": engine.Image is not None,
              # The style sheet's switches and arrows are SVG images.
              "svg_images": b"svg" in [bytes(f) for f in
                                       QImageReader.supportedImageFormats()]}
    try:
        window = YouTubeDownloader(update_check=False)   # also checks that Qt works
        window.show()
        application().processEvents()
        result["window"] = window.windowTitle()
        window.hide()   # not close(): that would save the settings
    except Exception as exc:
        result["window"] = f"failed: {exc}"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


# ── Entry point ─────────────────────────────────────────────────────────────
def main():
    if sys.platform == "win32":
        # The taskbar shows the app's own icon, not Python's.
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "lifeupnorth.YTDownloader")
        except (AttributeError, OSError):
            pass
    app = application(sys.argv)
    window = YouTubeDownloader()

    # An error in a Qt callback ends up here: the windowed .exe has no
    # console, so it goes to the log as well.
    def excepthook(exc, val, tb):
        try:
            window.report_error(exc, val, tb)
        except RuntimeError:   # the window is already gone
            sys.__excepthook__(exc, val, tb)

    sys.excepthook = excepthook
    window.show()
    return app.exec()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-check":
        self_check(sys.argv[2])
    else:
        sys.exit(main())
