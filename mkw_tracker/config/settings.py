"""Settings dataclass: loads from DB, falls back to Defaults, supports hot-reload."""
import threading
from typing import Any, Optional
from .defaults import Defaults
from ..database.config_repo import get_config, set_config, ensure_defaults


class Settings:
    """
    Thread-safe settings container.

    On startup, populates the DB with any missing defaults, then loads all
    values.  Call reload(keys) to refresh specific keys after an IPC
    update_config command.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._defaults = Defaults()
        self._data: dict = {}
        self._bootstrap()
        self._load()

    # ------------------------------------------------------------------
    def _bootstrap(self):
        """Insert DB defaults for all keys that don't exist yet."""
        ensure_defaults(self._defaults.as_dict())

    # ------------------------------------------------------------------
    def _load(self):
        """Load all config keys from DB into the local cache."""
        d = self._defaults.as_dict()
        with self._lock:
            for key in d:
                self._data[key] = get_config(key, d[key])

    # ------------------------------------------------------------------
    def reload(self, keys: Optional[list] = None):
        """
        Hot-reload specific keys (or all keys if *keys* is None) from the DB.
        Called after an IPC update_config command.
        """
        with self._lock:
            if keys is None:
                self._load()
            else:
                d = self._defaults.as_dict()
                for key in keys:
                    self._data[key] = get_config(key, d.get(key))

    # ------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    # __getattr__ so callers can do settings.selection_scan_interval
    def __getattr__(self, name: str) -> Any:
        try:
            data = object.__getattribute__(self, '_data')
            if name in data:
                return data[name]
        except AttributeError:
            pass
        raise AttributeError(f"Settings has no attribute '{name}'")

    # ------------------------------------------------------------------
    def update(self, key: str, value: Any):
        """Persist *value* for *key* and update the local cache."""
        set_config(key, value)
        with self._lock:
            self._data[key] = value


# Module-level singleton — import and use directly.
settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Return the module-level Settings singleton, creating it if needed."""
    global settings
    if settings is None:
        settings = Settings()
    return settings
