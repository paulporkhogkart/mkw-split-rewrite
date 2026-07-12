from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path


class Db:
    """Synchronous sqlite wrapper. One-call-at-a-time scale: call from async
    code via asyncio.to_thread for writes; never hold the connection across
    awaits."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def init(self) -> None:
        schema = resources.files("hotline").joinpath("schema.sql").read_text()
        self._conn.executescript(schema)
        self._conn.commit()

    def create_call(self, call_id: str, caller_label: str, seconds_bought: int) -> None:
        self._conn.execute(
            "INSERT INTO calls (call_id, caller_label, seconds_bought) VALUES (?,?,?)",
            (call_id, caller_label, seconds_bought))
        self._conn.commit()

    def finish_call(self, call_id: str, outcome: str, seconds_used: int,
                    recording_dir: str) -> None:
        self._conn.execute(
            "UPDATE calls SET outcome=?, seconds_used=?, recording_dir=?, "
            "ended_at=datetime('now') WHERE call_id=?",
            (outcome, seconds_used, recording_dir, call_id))
        self._conn.commit()

    def add_strike(self, call_id: str, at_ms: int, span_ms: int, action: str) -> None:
        self._conn.execute(
            "INSERT INTO strikes (call_id, at_ms, span_ms, action) VALUES (?,?,?,?)",
            (call_id, at_ms, span_ms, action))
        self._conn.commit()

    def get_setting(self, key: str, default: str) -> str:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO settings (key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
