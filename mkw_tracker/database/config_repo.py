"""Config key/value storage backed by SQLite."""
import json
from typing import Any, Optional
from .connection import get_connection


def get_config(key: str, default: Any = None) -> Any:
    """Return the stored value for *key*, or *default* if not found."""
    conn = get_connection()
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except Exception:
        return row[0]


def set_config(key: str, value: Any):
    """Store *value* for *key* (JSON-encoded)."""
    conn = get_connection()
    encoded = json.dumps(value)
    conn.execute(
        "INSERT INTO config(key, value, updated_at) VALUES(?,?,datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, encoded),
    )
    conn.commit()


def get_all_config() -> dict:
    """Return all config rows as a plain dict."""
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM config").fetchall()
    result = {}
    for key, value in rows:
        try:
            result[key] = json.loads(value)
        except Exception:
            result[key] = value
    return result


def ensure_defaults(defaults: dict):
    """Insert default values for any keys not already in the DB."""
    conn = get_connection()
    for key, value in defaults.items():
        existing = conn.execute("SELECT 1 FROM config WHERE key=?", (key,)).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO config(key, value) VALUES(?,?)",
                (key, json.dumps(value)),
            )
    conn.commit()
