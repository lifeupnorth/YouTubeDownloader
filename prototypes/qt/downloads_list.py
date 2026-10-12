#!/usr/bin/env python3
"""
Prototype: the app with a Downloads list, in PySide6 (Qt), to compare with
the Tkinter window. It uses the same engine (download_engine.py) and the
same colors (THEMES); nothing in the Tk app changes.

Each link becomes a row with its thumbnail, title, format, progress and
status. Links are looked up as soon as they're added (up to 3 at a time)
and download one after the other; each row can be canceled, its file shown
in its folder, or the row removed from the list (the file stays).

Run:  prototypes/qt/.venv/Scripts/python prototypes/qt/downloads_list.py
      --demo fills the list with sample rows whose progress is simulated.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import ClassVar

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QObject,
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
    QLinearGradient,
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
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTableView,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import download_engine as engine  # noqa: E402
import youtube_downloader as tk_app  # noqa: E402  (colors, link parsing, settings)

THEMES = tk_app.THEMES
THEME_MODES = tk_app.THEME_MODES

# Format and quality are two choices here; quality only applies to video.
FORMATS = [("MP4 video", "video"), ("MP3 audio", "mp3"), ("M4A audio", "m4a")]
QUALITIES = [("Best quality", "mp4_best"), ("1080p", "1080p"),
             ("720p", "720p"), ("480p", "480p")]

THUMB = QSize(128, 72)   # in the list, at 96 DPI (Qt scales it)
# Heights at 96 DPI; a pill's corner radius in the style sheet is half of it.
SEARCH, PILL, PRIMARY, CHIP = 40, 36, 44, 30
ROW_HEIGHT = 92

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
}
ACTIVE = {"downloading", "processing"}
FINISHED = {"done", "failed", "canceled", "declined"}


@dataclass(eq=False)
class Row:
    """One link in the list. Changed only on the main thread, except
    `latest` (the worker's newest progress) and `job`."""
    url: str
    fmt: str                 # for engine.format_opts, e.g. "1080p" or "mp3"
    folder: str
    whole_playlist: bool
    title: str = ""
    details: str = ""        # length · channel
    thumb: QPixmap | None = None
    state: str = "waiting"
    pct: float = 0
    message: str = ""        # speed and ETA, or why it failed
    file: str | None = None
    log: list[str] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    job: engine.DownloadJob | None = None
    latest: tuple[float, str] | None = None

    @property
    def format_label(self):
        return "MP4" if self.fmt not in ("mp3", "m4a") else self.fmt.upper()

    @property
    def quality_label(self):
        return dict((v, k) for k, v in QUALITIES).get(self.fmt, "—")


# ── The list ────────────────────────────────────────────────────────────────

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
        if role == Qt.ToolTipRole and col in (self.VIDEO, self.STATUS):
            lines = [row.url] if col == self.VIDEO else []
            if col == self.STATUS:
                lines += [row.message] if row.state == "failed" else []
                lines += row.log[-6:]
            return "\n".join(filter(None, lines)) or None
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

    ICON = 30   # an action icon's round hover area, at 96 DPI
    LABELS: ClassVar[dict[str, str]] = {"cancel": "Cancel", "folder": "Show in folder",
              "remove": "Remove from the list (the file stays)"}

    def __init__(self, view, model):
        super().__init__(view)
        self.view, self.model = view, model
        self._hover = None   # (row, action) under the mouse

    def colors(self):
        return self.model.colors

    # Which icons a row has, and where.
    def icons(self, rect, row):
        names = []
        if row.state not in FINISHED:
            names.append("cancel")
        if row.file:
            names.append("folder")
        if row.state in FINISHED:
            names.append("remove")
        size = self.ICON
        x = rect.left() + 8
        y = rect.center().y() - size / 2
        return [(name, QRectF(x + i * (size + 4), y, size, size)) for i, name in enumerate(names)]

    def paint(self, painter, option, index):
        c = self.colors()
        row = index.data(Qt.UserRole)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = QRectF(option.rect)
        if option.state & QStyle.State_MouseOver:
            painter.fillRect(rect, QColor(c["surface"]))
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
            color = index.data(Qt.ForegroundRole) or QColor(c["text"])
            painter.setPen(color)
            painter.setFont(option.font)
            painter.drawText(rect.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft,
                             index.data(Qt.DisplayRole) or "")
        painter.restore()

    def _paint_video(self, painter, rect, row, font):
        c = self.colors()
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
            painter.fillPath(shape, QColor(c["surface" if row.state != "looking" else "button"]))
            play_icon(painter, thumb.center(), 10, QColor(c["text_dim"]))

        left = thumb.right() + 14
        width = rect.right() - left - 8
        title_font = QFont(font)
        title_font.setPointSizeF(font.pointSizeF() * 1.1)
        title_font.setWeight(QFont.DemiBold)
        title = row.title or row.url
        metrics = QFontMetrics(title_font)
        painter.setFont(title_font)
        painter.setPen(QColor(c["text"]))
        top = rect.center().y() - metrics.height() - 1
        painter.drawText(QRectF(left, top, width, metrics.height()), Qt.AlignLeft,
                         metrics.elidedText(title, Qt.ElideRight, int(width)))
        painter.setFont(font)
        painter.setPen(QColor(c["text_dim"]))
        details = row.details or ("Looking up…" if row.state == "looking" else "")
        painter.drawText(QRectF(left, rect.center().y() + 3, width, metrics.height()),
                         Qt.AlignLeft, QFontMetrics(font).elidedText(
                             details, Qt.ElideRight, int(width)))

    def _paint_progress(self, painter, rect, row, font):
        c = self.colors()
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
        message = row.message if row.state in ACTIVE | {"failed"} else ""
        color = c["error"] if row.state == "failed" else c["text_dim"]
        painter.setPen(QColor(color))
        painter.drawText(below, Qt.AlignLeft | Qt.AlignTop, QFontMetrics(small).elidedText(
            message, Qt.ElideRight, int(below.width())))

    def _paint_actions(self, painter, rect, row):
        c = self.colors()
        for name, area in self.icons(rect, row):
            hovered = self._hover == (row, name)
            if hovered:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(c["button_hover"]))
                painter.drawEllipse(area)
            color = QColor(c["error"] if hovered and name == "remove" else
                           c["text"] if hovered else c["text_dim"])
            draw_icon(painter, name, area, color)

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
                    QToolTip.showText(event.globalPos(), self.LABELS[name], view)
                    return True
            QToolTip.hideText()
            return True
        return super().helpEvent(event, view, option, index)

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width(), ROW_HEIGHT)


class DownloadsView(QTableView):
    """The table, with a hint in place of the rows while there are none."""

    def __init__(self, model):
        super().__init__()
        self.setModel(model)
        self.setMouseTracking(True)
        self.setShowGrid(False)
        self.setSelectionMode(QTableView.NoSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWordWrap(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.setVerticalScrollMode(QTableView.ScrollPerPixel)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        widths = {DownloadsModel.FORMAT: 80, DownloadsModel.QUALITY: 110,
                  DownloadsModel.PROGRESS: 220, DownloadsModel.STATUS: 130,
                  DownloadsModel.ACTIONS: 120}
        header.setSectionResizeMode(DownloadsModel.VIDEO, QHeaderView.Stretch)
        for col, width in widths.items():
            header.setSectionResizeMode(col, QHeaderView.Fixed)
            header.resizeSection(col, width)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.model().rowCount() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(QColor(self.model().colors["text_dim"]))
            painter.drawText(self.viewport().rect(), Qt.AlignCenter,
                             "Paste a link and click Download.\n"
                             "Your downloads show up here.")


# ── Drawing helpers ─────────────────────────────────────────────────────────

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
    """The action icons, drawn so they take the theme's colors."""
    painter.save()
    pen = QPen(color, 1.6)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    x, y = area.center().x(), area.center().y()
    if name == "cancel":
        s = 5
        painter.drawLine(QPointF(x - s, y - s), QPointF(x + s, y + s))
        painter.drawLine(QPointF(x - s, y + s), QPointF(x + s, y - s))
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(x - 8, y + 6)
        path.lineTo(x - 8, y - 6)
        path.lineTo(x - 3, y - 6)
        path.lineTo(x - 1, y - 4)
        path.lineTo(x + 8, y - 4)
        path.lineTo(x + 8, y + 6)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "remove":
        painter.drawLine(QPointF(x - 7, y - 5), QPointF(x + 7, y - 5))
        painter.drawLine(QPointF(x - 2, y - 7), QPointF(x + 2, y - 7))
        body = QPainterPath()
        body.moveTo(x - 5.5, y - 5)
        body.lineTo(x - 4.5, y + 7)
        body.lineTo(x + 4.5, y + 7)
        body.lineTo(x + 5.5, y - 5)
        painter.drawPath(body)
    painter.restore()


def logo_pixmap(height, ratio):
    """The red play logo, as in the Tk app."""
    width = round(height * 1.4)
    pixmap = QPixmap(round(width * ratio), round(height * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
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
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
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
    """A Pillow image as a QImage (a copy, so it owns its pixels)."""
    image = image.convert("RGB")
    data = image.tobytes("raw", "RGB")
    return QImage(data, image.width, image.height, 3 * image.width,
                  QImage.Format_RGB888).copy()


def write_svgs(folder, c):
    """Small images the style sheet needs (a combo box's arrow, a switch),
    in this theme's colors."""
    svgs = {
        "arrow": f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12">'
                 f'<path d="M2 4 L6 8 L10 4" fill="none" stroke="{c["text_dim"]}" '
                 f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        "switch_off": f'<svg xmlns="http://www.w3.org/2000/svg" width="34" height="20">'
                      f'<rect x="0" y="0" width="34" height="20" rx="10" fill="{c["scroll"]}"/>'
                      f'<circle cx="10" cy="10" r="7" fill="white"/></svg>',
        "switch_on": f'<svg xmlns="http://www.w3.org/2000/svg" width="34" height="20">'
                     f'<rect x="0" y="0" width="34" height="20" rx="10" fill="{c["blue"]}"/>'
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
    QLineEdit#url {{ border-radius: 20px; border-top-right-radius: 0; border-bottom-right-radius: 0;
                     padding: 0 18px; font-size: 11pt; }}
    QPushButton#search {{ border-radius: 20px; border-top-left-radius: 0; border-bottom-left-radius: 0;
                          border: 1px solid {c["field_border"]}; border-left: none;
                          padding: 0 22px; }}

    /* Heights are fixed in code (PILL and friends): radius = half of it. */
    QPushButton {{ background: {c["button"]}; border: none; border-radius: 18px;
                   padding: 0 18px; font-weight: 500; }}
    QPushButton:hover {{ background: {c["button_hover"]}; }}
    QPushButton:disabled {{ color: {c["disabled"]}; }}
    QPushButton#primary {{ background: {c["accent"]}; color: {c["accent_text"]};
                           font-weight: 700; font-size: 11pt; padding: 0 28px; border-radius: 22px; }}
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
                            border-bottom: 1px solid {c["divider"]}; padding: 8px 8px;
                            font-weight: 600; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {c["scroll"]}; border-radius: 4px;
                                   min-height: 30px; margin: 2px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QToolTip {{ background: {c["surface"]}; color: {c["text"]};
                border: 1px solid {c["divider"]}; padding: 4px 6px; }}
    """


# ── Downloading ─────────────────────────────────────────────────────────────

class Bridge(QObject):
    """Signals from the worker threads; Qt delivers them on the main thread."""
    looked_up = Signal(object, str, object, bool)   # row, text, QImage or None, found
    logged = Signal(object, str)
    finished = Signal(object, object)                # row, its DownloadJob or None
    asked = Signal(object, str, object)              # row, question, (answer, event)


class Window(QMainWindow):
    def __init__(self, demo=False):
        super().__init__()
        self.setWindowTitle("YT Downloader (Qt prototype)")
        self.model = DownloadsModel()
        self.bridge = Bridge()
        self.bridge.looked_up.connect(self._looked_up)
        self.bridge.logged.connect(lambda row, msg: row.log.append(msg))
        self.bridge.finished.connect(self._finished)
        self.bridge.asked.connect(self._ask)
        self._lookups = ThreadPoolExecutor(max_workers=3)
        self._running = None          # the row downloading now
        self._thread = None
        self._closing = False
        self._js_runtime = None
        self._js_found = False
        self._svg_dir = tempfile.mkdtemp(prefix="ytd-qt-")
        self._requested_scheme = None
        settings = tk_app.load_settings()
        self.theme_mode = settings.get("theme") if settings.get("theme") in dict(
            (m, m) for _, m in THEME_MODES) else "system"
        folder = settings.get("folder")
        self.folder = folder if isinstance(folder, str) and os.path.isdir(folder) else \
            os.path.normpath(os.path.expanduser("~/Downloads"))

        self._build()
        self.apply_theme()
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            lambda _scheme: self.apply_theme())

        self._timer = QTimer(self, interval=100, timeout=self._show_progress)
        self._timer.start()
        if demo:
            self._demo()
        self.resize(1120, 760)

    # ── Layout ──
    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        page = QVBoxLayout(root)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)

        # Header: logo and name, the theme chips on the right.
        header = QHBoxLayout()
        header.setContentsMargins(20, 12, 20, 12)
        ratio = self.devicePixelRatioF()
        logo = QLabel()
        logo.setPixmap(logo_pixmap(30, ratio))
        self.setWindowIcon(QIcon(logo_pixmap(32, 2)))
        header.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        title = QLabel("YT Downloader", objectName="title")
        version = QLabel(self._powered_by(), objectName="dim")
        names.addWidget(title)
        names.addWidget(version)
        header.addSpacing(8)
        header.addLayout(names)
        header.addStretch()
        header.setSpacing(4)
        header.addWidget(QLabel("Theme", objectName="dim"))
        header.addSpacing(6)
        group = QButtonGroup(self)   # one checked at a time
        self.theme_chips = {}
        for label, mode in THEME_MODES:
            chip = QPushButton(label, objectName="chip", checkable=True,
                               cursor=Qt.PointingHandCursor)
            chip.setFixedHeight(CHIP)
            chip.clicked.connect(lambda _c=False, m=mode: self._choose_theme(m))
            group.addButton(chip)
            header.addWidget(chip)
            self.theme_chips[mode] = chip
        page.addLayout(header)
        page.addWidget(QFrame(objectName="divider"))

        body = QVBoxLayout()
        body.setContentsMargins(24, 16, 24, 8)
        body.setSpacing(10)
        page.addLayout(body, 1)

        # Links, in a box like YouTube's search box.
        body.addWidget(QLabel("YouTube URLs", objectName="heading"))
        search = QHBoxLayout()
        search.setSpacing(0)
        self.url = QLineEdit(objectName="url",
                             placeholderText="Paste one or more links: https://www.youtube.com/watch?v=...")
        self.url.returnPressed.connect(self._download)
        self.url.setFixedHeight(SEARCH)
        paste = QPushButton("Paste", objectName="search", cursor=Qt.PointingHandCursor)
        paste.setFixedHeight(SEARCH)
        paste.clicked.connect(self._paste)
        search.addWidget(self.url, 1)
        search.addWidget(paste)
        body.addLayout(search)

        # Options: format, quality, folder; whole playlist and Download.
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
        saved = tk_app.load_settings().get("format")
        if saved in ("mp3", "m4a"):
            self.format.setCurrentIndex(self.format.findData(saved))
        elif saved in dict(QUALITIES).values():
            self.quality.setCurrentIndex(self.quality.findData(saved))
        self.path = QLineEdit(self.folder)
        browse = QPushButton("Browse", cursor=Qt.PointingHandCursor)
        for widget in (self.format, self.quality, self.path, browse):
            widget.setFixedHeight(PILL)
        browse.clicked.connect(self._browse)
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

        actions = QHBoxLayout()
        self.whole_playlist = QCheckBox("Whole playlist", cursor=Qt.PointingHandCursor)
        self.download_btn = QPushButton(" Download", objectName="primary",
                                        cursor=Qt.PointingHandCursor)
        self.download_btn.setFixedHeight(PRIMARY)
        self.download_btn.setIcon(QIcon(download_icon(18, 3)))
        self.download_btn.setIconSize(QSize(18, 18))
        self.download_btn.clicked.connect(self._download)
        actions.addWidget(self.whole_playlist)
        actions.addStretch()
        actions.addWidget(self.download_btn)
        body.addLayout(actions)

        # The list.
        list_head = QHBoxLayout()
        list_head.setContentsMargins(0, 8, 0, 0)
        list_head.addWidget(QLabel("Downloads", objectName="heading"))
        list_head.addStretch()
        clear = QPushButton("Clear finished", cursor=Qt.PointingHandCursor)
        clear.setFixedHeight(PILL)
        clear.clicked.connect(self._clear_finished)
        list_head.addWidget(clear)
        body.addLayout(list_head)

        self.view = DownloadsView(self.model)
        self.delegate = RowDelegate(self.view, self.model)
        self.delegate.action.connect(self._row_action)
        self.view.setItemDelegate(self.delegate)
        body.addWidget(self.view, 1)

        footer = QHBoxLayout()
        self.count = QLabel(objectName="dim")
        self.speed = QLabel(objectName="dim")
        footer.addWidget(self.count)
        footer.addStretch()
        footer.addWidget(self.speed)
        body.addLayout(footer)
        self.model.rowsInserted.connect(self._update_footer)
        self.model.rowsRemoved.connect(self._update_footer)
        self.model.dataChanged.connect(self._update_footer)
        self._update_footer()

    def _powered_by(self):
        version = engine.yt_dlp_version()
        return f"Powered by yt-dlp {version}" if version else "yt-dlp isn't installed"

    # ── Themes ──
    def _choose_theme(self, mode):
        self.theme_mode = mode
        tk_app.save_settings({**tk_app.load_settings(), "theme": mode})
        self.apply_theme()

    def apply_theme(self):
        hints = QGuiApplication.styleHints()
        # Telling Qt the scheme also makes Windows draw a matching title bar;
        # Unknown hands it back to the system.
        wanted = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(
            self.theme_mode, Qt.ColorScheme.Unknown)
        if wanted != self._requested_scheme:
            self._requested_scheme = wanted
            hints.setColorScheme(wanted)   # may call apply_theme again
        name = self.theme_mode if self.theme_mode in THEMES else (
            "light" if hints.colorScheme() == Qt.ColorScheme.Light else "dark")
        self.model.colors = colors = THEMES[name]
        self.theme_chips[self.theme_mode].setChecked(True)
        folder = os.path.join(self._svg_dir, name)
        os.makedirs(folder, exist_ok=True)
        QApplication.instance().setStyleSheet(style_sheet(colors, write_svgs(folder, colors)))
        self.view.viewport().update()

    # ── Adding links ──
    def _paste(self):
        links = tk_app.split_links(QGuiApplication.clipboard().text())[0]
        if links:
            current = tk_app.split_links(self.url.text())[0]
            self.url.setText(" ".join(dict.fromkeys(current + links)))
            self.url.setFocus()

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Save to", self.path.text())
        if folder:
            self.path.setText(os.path.normpath(folder))

    def _download(self):
        if not engine.YT_DLP_AVAILABLE:
            QMessageBox.critical(self, "yt-dlp not found",
                                 f"Install it first:  {tk_app.pip_install_command()}")
            return
        self.ffmpeg = engine.find_ffmpeg()
        if not self.ffmpeg:
            QMessageBox.critical(self, "ffmpeg not found", tk_app.ffmpeg_help())
            return
        links = tk_app.split_links(self.url.text())[0]
        if not links:
            QMessageBox.warning(self, "No URL", "Please enter a YouTube URL.")
            return
        folder = os.path.expanduser(self.path.text().strip())
        if not folder or not os.path.isabs(folder):
            QMessageBox.warning(self, "Choose a folder",
                                "Please choose the folder to save to (Browse).")
            return
        fmt = self.format.currentData()
        fmt = self.quality.currentData() if fmt == "video" else fmt
        tk_app.save_settings({**tk_app.load_settings(), "folder": folder, "format": fmt})
        ratio = self.devicePixelRatioF()   # read here: workers mustn't touch widgets
        thumb = (round(THUMB.width() * ratio), round(THUMB.height() * ratio))
        for url in links:
            row = Row(url, fmt, os.path.normpath(folder), self.whole_playlist.isChecked())
            self.model.add(row)
            self._lookups.submit(self._look_up, row, thumb)
        self.url.clear()
        self.view.scrollToBottom()
        self._run_next()

    # Runs on a lookup thread.
    def _look_up(self, row, thumb):
        if row.cancel_event.is_set() or self._closing:
            return
        self.bridge.looked_up.emit(row, "", None, False)   # "Looking up…"
        result = engine.preview_link(row.url, row.whole_playlist, row.cancel_event,
                                     self._find_js_runtime(), thumb)
        if result is not None:
            text, image, found = result
            self.bridge.looked_up.emit(row, text, image and to_qimage(image), found)

    def _looked_up(self, row, text, image, found):
        if not text:   # the lookup is starting
            if row.state == "waiting":
                row.state = "looking"
        else:
            if row.state == "looking":
                row.state = "waiting"
            if found:
                row.title, _, row.details = text.partition("\n")
            elif not row.title:
                row.details = text
            if image is not None:
                row.thumb = QPixmap.fromImage(image)
        self.model.changed(row)

    # The JavaScript runtime is looked up once, on whichever worker needs it.
    _js_lock = threading.Lock()

    def _find_js_runtime(self):
        with self._js_lock:
            if not self._js_found:
                self._js_runtime, self._js_found = engine.find_js_runtime(), True
            return self._js_runtime

    # ── Downloading, one link at a time ──
    def _run_next(self):
        if self._running is not None or self._closing:
            return
        row = next((r for r in self.model.rows if r.state in ("waiting", "looking")), None)
        if row is None:
            return
        self._running = row
        row.state, row.pct, row.message = "downloading", 0, "Starting…"
        self.model.changed(row)
        self._thread = threading.Thread(target=self._run, args=(row,), daemon=True)
        self._thread.start()

    # Runs on the download thread.
    def _run(self, row):
        job = None
        try:
            if not row.cancel_event.is_set():
                job = engine.DownloadJob(
                    row.url, fmt=row.fmt, folder=row.folder, ffmpeg=self.ffmpeg,
                    whole_playlist=row.whole_playlist,
                    on_progress=lambda pct, msg: setattr(row, "latest", (pct, msg)),
                    on_log=lambda msg: self.bridge.logged.emit(row, msg),
                    ask=lambda question: self._ask_on_main(row, question),
                    cancel_event=row.cancel_event, js_runtime=self._find_js_runtime())
                row.job = job
                job.run()
        except Exception as exc:   # the row says why
            if job is not None:
                job.outcome, job.error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            self.bridge.finished.emit(row, job)

    # Progress comes often; the newest is shown 10 times a second.
    def _show_progress(self):
        row = self._running
        if row is None or row.latest is None or row.cancel_event.is_set():
            return
        pct, message = row.latest
        row.latest = None
        row.pct = pct
        row.state = "processing" if "Processing" in message else "downloading"
        # "Video 2/5 · Downloading… 42.0%  3.4 MB/s · ETA 0:07": the bar and
        # the percentage show the rest.
        row.message = re.sub(r"(Downloading… [\d.]+%|Processing…)\s*", "", message).strip(" ·")
        self.model.changed(row)

    def _finished(self, row, job):
        self._running = None
        row.file = job.last_file if job else None
        if job is None or job.outcome == "canceled":
            row.state, row.message = "canceled", ""
        elif job.outcome == "failed":
            row.state, row.message = "failed", job.error or "See the log."
        elif job.outcome == "declined":
            row.state, row.message = "declined", ""
        else:
            row.state = "done"
            summary = job.summary()
            row.message = "" if summary == "Download complete!" else summary
            if job.skipped:   # some of a playlist failed: say so in the details
                row.details = " · ".join(filter(None, [row.details, summary]))
        self.model.changed(row)
        self._update_footer()
        if self._closing:
            return
        self._run_next()

    # Runs on the download thread; blocks it until the user answers.
    def _ask_on_main(self, row, question):
        answer, done = [], threading.Event()
        self.bridge.asked.emit(row, question, (answer, done))
        while not done.wait(0.1):
            if self._closing or row.cancel_event.is_set():
                return False
        return bool(answer and answer[0])

    def _ask(self, row, question, reply):
        answer, done = reply
        try:
            if not row.cancel_event.is_set():
                answer.append(QMessageBox.question(self, "Playlist", question)
                              == QMessageBox.Yes)
        finally:
            done.set()

    # ── Row actions ──
    def _row_action(self, row, action):
        if action == "cancel":
            row.cancel_event.set()
            if row.job is not None:
                row.job.stop_processes()   # a running ffmpeg
            if row is not self._running:   # still waiting: just mark it
                row.state, row.message = "canceled", ""
            else:
                row.message = "Canceling…"
            self.model.changed(row)
        elif action == "folder":
            if row.file and os.path.isdir(os.path.dirname(row.file)):
                tk_app.reveal_in_file_manager(row.file)
            else:
                QMessageBox.critical(self, "Folder not found",
                                     "The download folder doesn't exist anymore.")
        elif action == "remove":
            self.model.remove(row)

    def _clear_finished(self):
        for row in [r for r in self.model.rows if r.state in FINISHED]:
            self.model.remove(row)

    def _update_footer(self, *_):
        rows = self.model.rows
        done = sum(r.state == "done" for r in rows)
        self.count.setText(f"{len(rows)} download{'s' * (len(rows) != 1)}"
                           + (f" · {done} completed" if done else ""))
        running = next((r for r in rows if r.state in ACTIVE), None)
        speed = re.search(r"[\d.]+ MB/s", running.message) if running else None
        self.speed.setText(f"⚡ {speed.group()}" if speed else "")

    def closeEvent(self, event):
        if self._running is not None:
            if QMessageBox.question(self, "Download in progress",
                                    "A download is still running.\n\n"
                                    "Cancel it and close the app?") != QMessageBox.Yes:
                event.ignore()
                return
        self._closing = True
        for row in self.model.rows:
            row.cancel_event.set()
            if row.job is not None:
                row.job.stop_processes()
        self.hide()
        if self._thread is not None:
            self._thread.join(10)   # so the partial file gets removed
        self._lookups.shutdown(wait=False, cancel_futures=True)
        event.accept()

    # ── Demo ──
    def _demo(self):
        """Sample rows, for a look at the list without downloading."""
        samples = [
            ("Exploring the Northern Lights", "18:42 · Sky Diaries", "1080p", "downloading", 64),
            ("Weekend Camping Tips for Beginners", "12:05 · Trail Notes", "720p", "done", 100),
            ("Lo-fi Morning Mix", "1:02:10 · Quiet Beats", "mp3", "done", 100),
            ("How to Tie 5 Fishing Knots", "8:17 · Riverside", "mp4_best", "failed", 0),
            ("Boat Walkthrough: 25 ft Cruiser", "11:31 · Harbor Life", "1080p", "waiting", 0),
        ]
        hues = [200, 25, 280, 140, 210]
        for (title, details, fmt, state, pct), hue in zip(samples, hues, strict=True):
            row = Row("https://www.youtube.com/watch?v=demo", fmt, self.folder, False,
                      title=title, details=details, state=state, pct=pct,
                      thumb=sample_thumb(hue, self.devicePixelRatioF()))
            if state == "failed":
                row.message = "Video unavailable: this video is private"
            if state == "done":
                row.file = os.path.join(self.folder, title + ".mp4")
            self.model.add(row)
        self._demo_row = self.model.rows[0]
        self._demo_timer = QTimer(self, interval=150, timeout=self._demo_tick)
        self._demo_timer.start()

    def _demo_tick(self):
        row = self._demo_row
        if row.state != "downloading":
            return
        row.pct = row.pct + 0.4 if row.pct < 99 else 0
        left = (100 - row.pct) * 0.12
        row.message = f"3.4 MB/s · ETA {engine.format_eta(left)}"
        self.model.changed(row)


def sample_thumb(hue, ratio):
    """A gradient standing in for a thumbnail in --demo."""
    w, h = round(THUMB.width() * ratio), round(THUMB.height() * ratio)
    pixmap = QPixmap(w, h)
    painter = QPainter(pixmap)
    gradient = QLinearGradient(0, 0, w, h)
    gradient.setColorAt(0, QColor.fromHsv(hue, 160, 230))
    gradient.setColorAt(1, QColor.fromHsv((hue + 40) % 360, 200, 90))
    painter.fillRect(0, 0, w, h, gradient)
    painter.end()
    return pixmap


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")   # the style sheet then looks the same everywhere
    app.setFont(QFont(tk_app.UI_FONT, 10))
    window = Window(demo="--demo" in sys.argv)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
