# Qt prototype: the Downloads list

A trial of the app in [PySide6](https://doc.qt.io/qtforpython-6/) (Qt), to
compare with the Tkinter window before deciding whether to move to it. It's
one file, [`downloads_list.py`](downloads_list.py), and it uses the real
engine (`download_engine.py`) and the Tk app's colors, link parsing and
settings file; nothing in the Tk app changes.

What it has that the Tk window doesn't:

- **A Downloads list**: one row per link with its thumbnail, title, length
  and channel, format, quality, progress (with speed and ETA) and status.
  Links are looked up as soon as they're added (up to 3 at a time), so
  waiting rows already show what they are, and they download one after the
  other.
- **Per-row actions**: cancel (a running download, or one still waiting),
  show the file in its folder, and remove the row from the list. Removing
  never deletes the file.
- **Format and Quality as two choices**; Quality is disabled for MP3 and M4A.
- **System, Light and Dark** themes, as in the Tk app (same `theme`
  setting). Qt follows the system's theme and colors the Windows title bar
  by itself.

Left out for now: the log (a failed row shows its reason, and hovering its
status shows the last log lines), the in-app yt-dlp install, the update
check, and Pause.

## Run it

```
python -m venv --system-site-packages prototypes/qt/.venv
prototypes/qt/.venv/Scripts/python -m pip install -r prototypes/qt/requirements.txt
prototypes/qt/.venv/Scripts/python prototypes/qt/downloads_list.py
```

`--system-site-packages` lets it use the yt-dlp and Pillow already installed
for the app. Add `--demo` to fill the list with sample rows (with simulated
progress) and see it without downloading anything.

## Compared with Tkinter

Built the same way as the release (`--onefile --windowed`, PyInstaller
6.22.3) but without ffmpeg and deno, which either app bundles:

| | .exe size |
|---|---|
| Tk app | 40.5 MB |
| Qt prototype (PySide6-Essentials 6.12) | 71.1 MB |

So a Qt release would grow from about 107 MB to about 138 MB.
