"""Shared SQLite connection with WAL mode."""
import sqlite3
import threading
from pathlib import Path

from ..utils.paths import data_dir

_DB_FILENAME = "mkw_tracker.db"
_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Return the shared SQLite connection, creating it on first call."""
    global _conn
    with _lock:
        if _conn is None:
            resolved = db_path if db_path is not None else str(data_dir() / _DB_FILENAME)
            _conn = sqlite3.connect(resolved, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.commit()
        return _conn


def close_connection():
    """Close the shared connection (call on shutdown)."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
