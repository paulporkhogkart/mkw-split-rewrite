"""Runtime path resolution for source and PyInstaller frozen environments."""
import os
import sys
from pathlib import Path


def resource_path(relative: str) -> str:
    """Resolve a path to a bundled resource file or directory.

    PyInstaller --onedir: paths are relative to sys._MEIPASS (the _internal dir
                          next to the exe where all data files are extracted).
    Development:          paths are relative to the repository root.
    """
    if getattr(sys, 'frozen', False):
        base = Path(sys._MEIPASS)
    else:
        # __file__ is mkw_tracker/utils/paths.py  →  .parent×3 = repo root
        base = Path(__file__).resolve().parent.parent.parent
    return str(base / relative)


def data_dir() -> Path:
    """Return the user-writable data directory.

    Frozen:      %APPDATA%/mkw-tracker/   (persists across app updates)
    Development: repository root           (keeps existing dev workflow)
    """
    if getattr(sys, 'frozen', False):
        appdata = os.environ.get('APPDATA') or str(Path.home())
        d = Path(appdata) / 'mkw-tracker'
        d.mkdir(parents=True, exist_ok=True)
        return d
    return Path(__file__).resolve().parent.parent.parent
