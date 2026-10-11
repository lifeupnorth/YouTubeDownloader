"""Fake yt-dlp pieces shared by the tests: no network, no real downloads."""
import os
import time
import types
from typing import ClassVar


class FakeCancelled(Exception):
    """Stands in for yt_dlp.utils.DownloadCancelled."""


def make_fake_ydl(streams=2, videos=1, overshoot=0, error=None, fail=(),
                  already=(), probe=("video",), files_dir=None, on_progress=None,
                  warn=None, delay=0):
    """probe: the _type returned by each extract_info(process=False) call.

    files_dir: write real stream files there (.part while downloading, then
    renamed, then "merged" into v<N>.mp4), as yt-dlp does.
    on_progress(video, stream, done): called on the download thread before
    each progress event, and with stream=-1 before each video starts.
    delay: seconds between progress events (a real download takes time).
    """
    probes = list(probe)
    on_progress = on_progress or (lambda *a: None)

    class FakeYDL:
        calls: ClassVar[list[str]] = []   # shared: one list per fake class

        def __init__(self, opts):
            self.opts = FakeYDL.last_opts = opts
            self.pps = []

        def add_post_processor(self, pp, when="post_process"):
            self.pps.append((pp, when))

        def run_pps(self, when, info):
            for pp, pp_when in self.pps:
                if pp_when == when:
                    pp.run(info)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True, process=True, ie_key=None):
            FakeYDL.calls.append("probe")
            kind = probes.pop(0)
            if kind is None:   # what yt-dlp returns once it has logged the error
                self.opts["logger"].error(f"ERROR: [youtube] {url}: Video unavailable")
                return None
            return {"_type": kind, "title": "Fake list",
                    "url": "https://example.com/next", "ie_key": "Fake"}

        def process_ie_result(self, info, download=True):
            FakeYDL.calls.append("download")
            self._run()

        def download(self, urls):
            FakeYDL.calls.append("download")
            self._run()

        def _run(self):
            hooks = self.opts["progress_hooks"]
            for video in range(videos):
                info = {"requested_formats": [{}] * streams} if streams > 1 else {}
                if videos > 1:
                    info.update(playlist_index=video + 1, n_entries=videos)
                if video in fail:
                    # What yt-dlp does for an unavailable entry.
                    self.opts["logger"].error(f"ERROR: [youtube] v{video}: Private video")
                    if not self.opts.get("ignoreerrors"):
                        raise RuntimeError("Private video")
                    continue
                if warn and video == 0:
                    self.opts["logger"].warning(warn)
                on_progress(video, -1, 0)
                self.run_pps("before_dl", info)
                names = []
                for stream in range(streams):
                    name = f"v{video}.f{stream}.webm"
                    files = {}
                    if files_dir:
                        name = os.path.join(files_dir, name)
                        files = {"filename": name, "tmpfilename": name + ".part"}
                        open(name + ".part", "wb").close()
                    names.append(name)
                    # total_bytes_estimate can be exceeded (overshoot).
                    for done in range(0, 101 + overshoot, 5):
                        on_progress(video, stream, done)
                        for hook in hooks:
                            hook({"status": "downloading", "video": video,
                                  "total_bytes_estimate": 100,
                                  "downloaded_bytes": done, "speed": 1e6,
                                  "eta": 1, "info_dict": info, **files})
                        if delay:
                            time.sleep(delay)
                    if files_dir:
                        os.replace(name + ".part", name)
                    for hook in hooks:
                        hook({"status": "finished", "video": video, "info_dict": info,
                              "filename": name})
                final = f"v{video}.mp4"
                if files_dir:
                    final = os.path.join(files_dir, final)
                    open(final, "wb").close()
                    for name in names:
                        os.remove(name)
                self.run_pps("after_move", {
                    **info, "filepath": final,
                    "__real_download": video not in already})
            if error:
                raise error

    return FakeYDL


def fake_yt_dlp(fake_ydl):
    """A stand-in for the yt_dlp module around a fake YoutubeDL class."""
    return types.SimpleNamespace(
        YoutubeDL=fake_ydl,
        postprocessor=types.SimpleNamespace(PostProcessor=object),
        utils=types.SimpleNamespace(DownloadCancelled=FakeCancelled))
