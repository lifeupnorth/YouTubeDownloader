"""Downloading with yt-dlp, without any UI.

DownloadJob downloads one link (a video, playlist or channel) and
DownloadQueue runs several in a row. They run on a worker thread and report
through plain callbacks, so they can be used and tested without Tk. The
window (youtube_downloader.py) turns those callbacks into screen updates.
"""

import functools
import glob
import importlib
import io
import json
import os
import re
import shutil
import site
import sys
import threading
import urllib.request

# ── yt-dlp: optional at import time, the app offers to install it ──────────
try:
    import yt_dlp
    YT_DLP_AVAILABLE = True
except ImportError:
    yt_dlp = None   # load_yt_dlp() can import it later, after an install
    YT_DLP_AVAILABLE = False

# Optional: without Pillow, link previews show text but no thumbnail
# (YouTube thumbnails are JPEG/WebP, which Tk can't show by itself).
try:
    from PIL import Image
except ImportError:
    Image = None


def _yt_dlp_requirement():
    # requirements.txt next to this file is the only place the yt-dlp pin is
    # kept (Dependabot bumps it). Unpinned if the app was copied on its own.
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


def load_yt_dlp():
    """Imports yt-dlp while the app is running, right after the built-in
    installer ran, so no restart is needed. True if it's usable now."""
    global yt_dlp, YT_DLP_AVAILABLE
    importlib.invalidate_caches()
    # pip may have just created the per-user site-packages folder (when it
    # can't write to Python's own); Python only adds it at startup.
    user_site = site.getusersitepackages()
    if site.ENABLE_USER_SITE and os.path.isdir(user_site):
        site.addsitedir(user_site)
    try:
        yt_dlp = importlib.import_module("yt_dlp")
    except ImportError:
        return False
    YT_DLP_AVAILABLE = True
    return True


def yt_dlp_version():
    """The installed yt-dlp's version, e.g. "2026.08.19", or None."""
    if not YT_DLP_AVAILABLE:
        return None
    return getattr(getattr(yt_dlp, "version", None), "__version__", None)


def pinned_yt_dlp_version():
    """The version requirements.txt pins, e.g. "2026.8.19", or None."""
    _name, sep, version = YT_DLP_REQUIREMENT.partition("==")
    return version if sep else None


def same_version(a, b):
    """yt-dlp calls itself "2026.08.19" where pip writes "2026.8.19"."""
    def parts(version):
        return tuple(int(p) if p.isdigit() else p for p in version.split("."))
    return parts(a) == parts(b)


# ── Update check ─────────────────────────────────────────────────────────────

RELEASES_URL = "https://github.com/lifeupnorth/YouTubeDownloader/releases/latest"
LATEST_RELEASE_API = "https://api.github.com/repos/lifeupnorth/YouTubeDownloader/releases/latest"


def app_version():
    """The release this .exe was built as (e.g. "1.0.3", from the VERSION file
    packaging/build_exe.py bundles); None from source or in a test build."""
    if not getattr(sys, "frozen", False):
        return None
    try:
        with open(os.path.join(getattr(sys, "_MEIPASS", ""), "VERSION"),
                  encoding="utf-8") as f:
            return f.read().strip().removeprefix("v") or None
    except OSError:
        return None


def newer_version(latest, current):
    """Whether latest is newer than current: "v1.0.10" > "1.0.9",
    "2026.10.01" > "2026.8.19"."""
    def parts(version):
        return tuple(int(p) for p in re.findall("[0-9]+", version))
    return parts(latest) > parts(current)


def _get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "YT-Downloader"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def check_for_update():
    """A one-line notice when a newer release of the app is out, else None.
    Asks api.github.com; None without a network. Only a release .exe checks
    (each release bundles a newer yt-dlp). From source it never does: the
    app notes a yt-dlp that doesn't match the pin in requirements.txt."""
    current = app_version()
    if current is None:   # from source, or a test build
        return None
    try:
        release = _get_json(LATEST_RELEASE_API)
        latest = release.get("tag_name") or ""
        if latest and newer_version(latest, current):
            return (f"• YT Downloader {latest.removeprefix('v')} is out (this is "
                    f"{current}). Download it from {release.get('html_url') or RELEASES_URL}")
    except Exception:
        return None
    return None


# ── Formats ──────────────────────────────────────────────────────────────────

# res:N caps the shorter side (as YouTube's labels do), not the height: for a
# vertical 1080x1920 video, "480p" gives 480x854.
VIDEO_MAX_RES = {"mp4_best": None, "1080p": 1080, "720p": 720, "480p": 480}


def format_opts(fmt):
    """yt-dlp options for one of the Format choices ("mp4_best", "mp3", ...)."""
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


def format_eta(seconds):
    """138 -> "2:18", 3725 -> "1:02:05"."""
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


# ── ffmpeg ───────────────────────────────────────────────────────────────────

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


def bundled_ffmpeg():
    """The ffmpeg packed into the one-file .exe (packaging/build_exe.py), when
    running from it; PyInstaller unpacks it into sys._MEIPASS."""
    if not getattr(sys, "frozen", False):
        return None
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    path = os.path.join(getattr(sys, "_MEIPASS", ""), name)
    return path if os.path.isfile(path) else None


def find_ffmpeg():
    found = bundled_ffmpeg() or shutil.which("ffmpeg")
    if found is None and sys.platform == "win32":
        found = shutil.which("ffmpeg", path=_registry_path())
    return found


# ── yt-dlp plumbing ──────────────────────────────────────────────────────────

_ANSI_CODES = re.compile(r"\x1b\[[0-9;]*m")


class _YdlLogger:
    """Hands yt-dlp's errors to on_error (with ignoreerrors they don't raise)
    and its warnings to on_warning, so both reach the app's log, also when
    there's no console (pythonw).

    check(), if given, runs for each of yt-dlp's status messages and may
    raise to stop it. yt-dlp sends them all through the way, also while it
    reads a playlist's pages, where no progress hook runs.
    """

    def __init__(self, on_error, on_warning=lambda msg: None, check=None):
        self._on_error = on_error
        self.warning = on_warning
        self._check = check

    def error(self, msg):
        # yt-dlp also sends its "Deprecated Feature: ..." notices (e.g. for an
        # old Python version) here, after already passing them to warning().
        # They aren't failed downloads.
        if _ANSI_CODES.sub("", msg).startswith("Deprecated Feature:"):
            return
        self._on_error(msg)

    def debug(self, msg):
        if self._check:
            self._check()

    info = debug


@functools.cache
def _callback_pp_class(base):
    # A yt-dlp post-processor that calls fn(info). Defined lazily, because
    # yt_dlp may be missing at import time; tests pass object as the base.
    class CallbackPP(base):
        def __init__(self, fn):
            super().__init__()
            self._fn = fn

        def run(self, info):
            self._fn(info)
            return [], info

    return CallbackPP


# The job running on this thread, for _tracked_popen_class.
_current = threading.local()


@functools.cache
def _tracked_popen_class(base):
    # yt-dlp's Popen, telling the job on this thread about each process it
    # starts (ffmpeg converting or merging), so cancel() can stop it.
    class TrackedPopen(base):
        def __init__(self, args, *a, **k):
            # Checked before it starts: only a file this run creates counts.
            output = _new_ffmpeg_output(args)
            super().__init__(args, *a, **k)
            job = getattr(_current, "job", None)
            if job is not None:
                job._process_started(self, output)

    return TrackedPopen


def _new_ffmpeg_output(args):
    """The file an ffmpeg run is about to create, or None. yt-dlp passes the
    output last, as "file:<path>". None for ffprobe, for ffmpeg only reading a
    file (["ffmpeg", "-i", "file:<path>"]), for anything not written to a file
    (a URL, "-" for stdout), and for a file that's already there: files on
    disk before this run are never removed."""
    if not isinstance(args, (list, tuple)) or len(args) < 2:
        return None
    exe = os.path.basename(str(args[0])).lower()
    last = args[-1]
    if exe.startswith("ffprobe") or args[-2] == "-i":
        return None
    if not isinstance(last, str) or not last.startswith("file:"):
        return None
    path = last.removeprefix("file:")
    return None if os.path.exists(path) else path


def _track_processes():
    """Makes yt-dlp's ffmpeg runs go through _tracked_popen_class."""
    base = getattr(sys.modules.get("yt_dlp.utils"), "Popen", None)
    if base is None:
        return
    for name in ("yt_dlp.postprocessor.ffmpeg", "yt_dlp.downloader.external"):
        module = sys.modules.get(name)
        if module is not None and getattr(module, "Popen", None) is base:
            module.Popen = _tracked_popen_class(base)


def _follow_url_results(ydl, info):
    """Follows "url" results (e.g. a video opened from a playlist) to what
    they point to, without processing it."""
    for _ in range(3):
        if not info or info.get("_type") not in ("url", "url_transparent"):
            break
        info = ydl.extract_info(info["url"], download=False, process=False,
                                ie_key=info.get("ie_key"))
    return info


# ── Link preview ─────────────────────────────────────────────────────────────

THUMB_SIZE = (96, 54)


def describe_info(info):
    """The preview's two lines: the title, then the length (or size of a
    playlist) and the channel."""
    title = info.get("title") or "Untitled"
    if len(title) > 100:
        title = title[:99] + "…"
    if info.get("_type") == "playlist":
        count = info.get("playlist_count")
        what = f"Playlist · {count} videos" if count else "Playlist or channel"
    else:
        duration = info.get("duration")
        what = format_eta(duration) if duration else ""
    channel = info.get("channel") or info.get("uploader") or ""
    details = " · ".join(filter(None, [what, channel]))
    return f"{title}\n{details}" if details else title


def thumbnail_url(info):
    """The smallest thumbnail at least 160 px wide, else the default one."""
    thumbs = [t for t in info.get("thumbnails") or ()
              if t.get("url") and (t.get("width") or 0) >= 160]
    if thumbs:
        return min(thumbs, key=lambda t: t["width"])["url"]
    return info.get("thumbnail")


def fetch_thumbnail(url, canceled=lambda: False):
    """Downloads and shrinks a thumbnail; None without Pillow, on errors, or
    once canceled() is true."""
    if Image is None or not url or not url.startswith(("https://", "http://")):
        return None
    try:
        data = b""
        with urllib.request.urlopen(url, timeout=10) as response:
            while chunk := response.read(64 * 1024):
                data += chunk
                if canceled() or len(data) > 2_000_000:
                    return None
        image = Image.open(io.BytesIO(data)).convert("RGB")
        image.thumbnail(THUMB_SIZE)
        return image
    except Exception:   # a preview isn't worth an error message
        return None


def preview_link(url, whole_playlist, cancel_event=None):
    """Looks a link up without downloading: (text, PIL image or None, found).

    Like a download, it follows "url" results, but it doesn't page through a
    playlist's entries (process=False), so even big playlists take a second
    or two. Runs on a worker thread. Once cancel_event is set (a newer link
    was typed), it stops at yt-dlp's next status message or the thumbnail's
    next chunk and returns None.
    """
    canceled = cancel_event.is_set if cancel_event else lambda: False

    # yt-dlp's own cancel exception: it stops even with ignoreerrors set and
    # isn't caught by its broad "except Exception" handlers.
    def check():
        if canceled():
            raise yt_dlp.utils.DownloadCancelled()

    try:
        opts = {"quiet": True, "skip_download": True,
                "noplaylist": not whole_playlist,
                "logger": _YdlLogger(lambda msg: None, check=check),
                "color": {"stdout": "never", "stderr": "never"}}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = _follow_url_results(
                ydl, ydl.extract_info(url, download=False, process=False))
    except Exception:   # e.g. not a video page; the preview just says so
        info = None
    if canceled():
        return None
    if not info:
        return "Couldn't load a preview for this link.", None, False
    image = fetch_thumbnail(thumbnail_url(info), canceled)
    if canceled():
        return None
    return describe_info(info), image, True


# ── Downloading ──────────────────────────────────────────────────────────────

class DownloadJob:
    """Downloads one link: a video, or a whole playlist or channel.

    Call run() on a worker thread; cancel() may be called from any thread.
    The job reports through callbacks, which also run on the worker thread:

    on_progress(percent, message): often; only the newest call matters.
    on_log(message): a line for the log.
    ask(question): returns True or False; used to confirm a playlist.
    """

    def __init__(self, url, *, fmt, folder, ffmpeg, whole_playlist,
                 on_progress, on_log, ask, cancel_event=None):
        self.url = url
        self.fmt, self.folder, self.ffmpeg = fmt, folder, ffmpeg
        self.whole_playlist = whole_playlist
        self.on_progress, self.on_log, self.ask = on_progress, on_log, ask
        self._cancel = cancel_event or threading.Event()

        # The result, filled in while it runs.
        self.outcome = None         # "done", "failed", "declined", "canceled"
        self.saved = self.already = self.removed = 0
        self.skipped = []           # why entries failed (e.g. private videos)
        self.error = None           # why the whole job failed
        self.error_logged = False   # whether that reason is already in the log
        self.last_file = None       # the last file saved or already there

        self._stream_index = 0      # which stream of the current video
        self._stream_count = 1      # how many it has (video + audio = 2)
        self._entry_files = set()   # files written for the unfinished video
        self._processes = []        # (process, file it creates or None)
        self._killed_outputs = []   # files the stopped ones were creating
        self._lock = threading.Lock()

    def cancel(self):
        self._cancel.set()
        self.stop_processes()

    def stop_processes(self):
        """Stops the ffmpeg this job is running, if any (after a cancel)."""
        # The outputs are noted before the kill, so the worker waking up from
        # it always finds them in _remove_partial_files.
        with self._lock:
            running = [(p, out) for p, out in self._processes if p.poll() is None]
            self._killed_outputs.extend(out for _p, out in running if out)
        for proc, _out in running:
            # An ffmpeg that is a .bat/.cmd wrapper runs under "cmd.exe /C";
            # kill() then stops only cmd.exe. Rare enough to leave.
            try:
                proc.kill()
            except OSError:
                pass

    def _process_started(self, proc, output):
        with self._lock:
            self._processes.append((proc, output))
        if self.canceled:   # canceled just before it started
            self.stop_processes()

    @property
    def canceled(self):
        return self._cancel.is_set()

    def options(self):
        """The yt-dlp options for this download."""
        opts = {
            # Playlist entries go to "<playlist>/<NNN> - <title>.<ext>" (the
            # index keeps same-title videos apart); single videos to
            # "<title>.<ext>", as an empty folder name collapses.
            "outtmpl": os.path.join(self.folder, "%(playlist_title|)s",
                                    "%(playlist_index&{:03d} - |)s%(title)s.%(ext)s"),
            "progress_hooks": [self._progress_hook],
            # Only affects links to a video in a playlist (watch?v=…&list=…).
            "noplaylist": not self.whole_playlist,
            "quiet": True,
            "no_warnings": False,
            # Like the yt-dlp CLI: skip unavailable entries instead of stopping.
            "ignoreerrors": "only_download",
            "logger": _YdlLogger(self._entry_failed, self._entry_warning,
                                 self._check_canceled),
            # "auto" colors the ERROR: prefix when started from a terminal.
            "color": {"stdout": "never", "stderr": "never"},
        }
        opts.update(format_opts(self.fmt))
        opts["ffmpeg_location"] = self.ffmpeg
        return opts

    def run(self):
        """Downloads, and returns the outcome (also kept in .outcome)."""
        _track_processes()
        _current.job = self
        try:
            with yt_dlp.YoutubeDL(self.options()) as ydl:
                base = yt_dlp.postprocessor.PostProcessor
                ydl.add_post_processor(_callback_pp_class(base)(self._video_starting),
                                       when="before_dl")
                ydl.add_post_processor(_callback_pp_class(base)(self._file_done),
                                       when="after_move")
                if self.whole_playlist:
                    ydl.download([self.url])
                elif not self._download_unless_declined(ydl):
                    self.outcome = "declined"
                    return self.outcome
        except Exception as exc:
            if not self.canceled:
                self.outcome, self.error = "failed", str(exc)
                return self.outcome
        finally:
            _current.job = None
        # Also after a normal return: a stopped ffmpeg is a failed
        # post-processing step for yt-dlp, which it only logs.
        if self.canceled:
            self.removed = self._remove_partial_files()
            self.outcome = "canceled"
            return self.outcome
        if self.skipped and not (self.saved or self.already):
            # Nothing worked, e.g. a single private video: that's a failure,
            # and its reason is already in the log.
            self.outcome, self.error, self.error_logged = "failed", self.skipped[0], True
        else:
            self.outcome = "done"
        return self.outcome

    # Raising this is how yt-dlp lets a caller stop a download. It leaves the
    # .part file behind, which _remove_partial_files() then deletes. Runs in
    # the progress hook, before each video, and for each of yt-dlp's status
    # messages (see _YdlLogger).
    def _check_canceled(self):
        if self.canceled:
            raise yt_dlp.utils.DownloadCancelled()

    # With "whole playlist" off, ask before downloading a playlist, channel
    # or tab (decided from what yt-dlp extracts, not from the URL).
    def _download_unless_declined(self, ydl):
        info = ydl.extract_info(self.url, download=False, process=False)
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
            if not self.ask(f"\"{title}\" is a playlist or channel.\n\n"
                            "Download all its videos?"):
                return False
        ydl.process_ie_result(info, download=True)
        return True

    # before_dl post-processor: once per video (also per playlist entry).
    # Merged formats download video and audio as separate streams, and the
    # progress hook's info_dict doesn't list them, so count them here.
    def _video_starting(self, info):
        self._check_canceled()   # also stops a playlist between two videos
        self._stream_index = 0
        self._stream_count = len(info.get("requested_formats") or ()) or 1
        self._entry_files = set()

    def _progress_hook(self, d):
        if d["status"] == "downloading":
            # Only files this run is writing; already-downloaded files never
            # get a "downloading" event, so canceling can't remove them.
            self._entry_files.update(
                p for p in (d.get("tmpfilename"), d.get("filename")) if p)
        self._check_canceled()
        streams = self._stream_count
        entry = d.get("info_dict") or {}
        index, count = entry.get("playlist_index"), entry.get("n_entries")
        video = f"Video {index}/{count} · " if index and count else ""
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            downloaded = d.get("downloaded_bytes", 0)
            speed = d.get("speed", 0) or 0
            eta = d.get("eta", 0) or 0

            pct = min(downloaded / total * 100, 100) if total else 0
            # The streams share one bar, which tops out at 99 until the end.
            pct = (min(self._stream_index, streams - 1) * 100 + pct) / streams * 0.99
            speed_str = f"{speed/1024/1024:.1f} MB/s" if speed > 0 else ""
            eta_str = f"ETA {format_eta(eta)}" if eta > 0 else ""
            info = " · ".join(filter(None, [speed_str, eta_str]))
            self.on_progress(pct, f"{video}Downloading… {pct:.1f}%  {info}")
        elif d["status"] == "finished":
            self._stream_index += 1
            if self._stream_index >= streams:
                self.on_progress(99, f"{video}Processing…")

    # after_move post-processor: once per video, with its final file (the
    # progress hook only sees the temporary per-stream files).
    def _file_done(self, info):
        self._entry_files.clear()   # finished: nothing for cancel to remove
        path = info.get("filepath", "")
        self.last_file = path
        if info.get("__real_download"):
            self.saved += 1
            self.on_log(f"✔ Saved: {path}")
        else:
            self.already += 1
            self.on_log(f"• Already downloaded: {path}")

    def _entry_failed(self, msg):
        if self.canceled:   # e.g. the ffmpeg that cancel() stopped
            return
        msg = msg.removeprefix("ERROR: ")
        self.skipped.append(msg)
        self.on_log(f"✘ {msg}")

    def _entry_warning(self, msg):
        self.on_log(f"⚠ {msg.removeprefix('WARNING: ')}")

    # After a cancel: removes what this run wrote for the unfinished video
    # (its .part file, fragments, any stream that finished but wasn't merged
    # yet, and the file a stopped ffmpeg was writing). Videos that finished
    # are kept.
    def _remove_partial_files(self):
        removed = 0
        with self._lock:
            killed = list(self._killed_outputs)
        for path in {*self._entry_files, *killed}:
            for p in [path, path + ".ytdl", *glob.glob(glob.escape(path) + "-Frag*")]:
                try:
                    os.remove(p)
                    removed += 1
                except OSError:
                    pass
        return removed

    def summary(self):
        """How it went, for the status line, e.g. "Done: 2 saved, 1 skipped"."""
        if self.outcome == "canceled":
            parts = [f"{self.saved} saved"] if self.saved else []
            if self.removed:
                parts.append(f"{self.removed} partial file"
                             f"{'s' * (self.removed != 1)} removed")
            return "Download canceled" + (f" ({', '.join(parts)})" if parts else "") + "."
        if self.outcome == "declined":
            return "Playlist not downloaded."
        if self.outcome == "failed":
            return f"Error: {self.error}"
        if not self.skipped and self.saved + self.already <= 1:
            return "Download complete!"
        parts = [f"{self.saved} saved"]
        if self.already:
            parts.append(f"{self.already} already downloaded")
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        return "Done: " + ", ".join(parts)


class DownloadQueue:
    """Downloads several links one after the other (also used for just one).

    A link that fails, or whose playlist isn't confirmed, doesn't stop the
    others; cancel() stops the current link and skips the rest. It takes the
    same callbacks and options as DownloadJob, and with several links its
    progress messages start with "Link 2/3 · ". Its result has the same
    shape as a job's: outcome, summary(), counts, error, last_file.
    """

    def __init__(self, urls, *, on_progress, on_log, **job_options):
        self.urls = list(urls)
        self.jobs = []
        self._on_progress, self._on_log = on_progress, on_log
        self.job_options = job_options   # passed to each DownloadJob
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()
        for job in self.jobs[-1:]:   # the running one
            job.stop_processes()

    @property
    def canceled(self):
        return self._cancel.is_set()

    def run(self):
        total = len(self.urls)
        for number, url in enumerate(self.urls, 1):
            if self.canceled:
                break
            prefix = f"Link {number}/{total} · " if total > 1 else ""
            if total > 1:
                self._on_log(f"▶ Link {number}/{total}: {url}")
            job = DownloadJob(
                url, cancel_event=self._cancel, on_log=self._on_log,
                on_progress=lambda pct, msg, prefix=prefix: self._on_progress(
                    pct, prefix + msg),
                **self.job_options)
            self.jobs.append(job)
            job.run()
            if total > 1 and job.outcome == "failed" and not job.error_logged:
                self._on_log(f"✘ {job.summary()}")
        return self.outcome

    # ── The combined result ──

    @property
    def outcome(self):
        if self.canceled:
            return "canceled"
        outcomes = [job.outcome for job in self.jobs]
        if len(outcomes) == 1:
            return outcomes[0]
        if "done" in outcomes:
            return "done"
        return "declined" if set(outcomes) == {"declined"} else "failed"

    def _total(self, name):
        return sum(getattr(job, name) for job in self.jobs)

    @property
    def saved(self):
        return self._total("saved")

    @property
    def already(self):
        return self._total("already")

    @property
    def removed(self):
        return self._total("removed")

    @property
    def skipped(self):
        # Entries skipped within links that worked. A link where nothing
        # worked counts as a failed link instead, not also as skipped entries.
        return [msg for job in self.jobs if job.outcome != "failed"
                for msg in job.skipped]

    @property
    def last_file(self):
        files = [job.last_file for job in self.jobs if job.last_file]
        return files[-1] if files else None

    def _with(self, outcome):
        return [job for job in self.jobs if job.outcome == outcome]

    @property
    def error(self):
        failed = self._with("failed")
        return failed[0].error if failed else None

    @property
    def error_logged(self):
        # With several links, each failure is logged as it happens.
        if len(self.jobs) == 1:
            return self.jobs[0].error_logged
        return True

    @property
    def had_problems(self):
        """Something didn't work, though the outcome may still be "done"."""
        return bool(self.skipped or self._with("failed") or self._with("declined"))

    def summary(self):
        """How it went, for the status line, e.g. "Done: 3 saved, 1 link failed"."""
        if len(self.urls) == 1 and self.jobs:
            return self.jobs[0].summary()
        if self.outcome == "canceled":
            parts = [f"{self.saved} saved"] if self.saved else []
            if self.removed:
                parts.append(f"{self.removed} partial file"
                             f"{'s' * (self.removed != 1)} removed")
            return "Download canceled" + (f" ({', '.join(parts)})" if parts else "") + "."
        parts = [f"{self.saved} saved"] if self.saved or self.outcome == "done" else []
        if self.already:
            parts.append(f"{self.already} already downloaded")
        if self.skipped:
            parts.append(f"{len(self.skipped)} skipped")
        for outcome, what in (("failed", "link{} failed"),
                              ("declined", "playlist{} not downloaded")):
            count = len(self._with(outcome))
            if count:
                parts.append(f"{count} " + what.format("s" * (count != 1)))
        head = "Done" if self.outcome == "done" else "Nothing downloaded"
        return f"{head}: " + ", ".join(parts)
