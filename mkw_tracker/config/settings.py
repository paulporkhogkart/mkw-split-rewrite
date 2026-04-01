"""Settings dataclass: loads from DB, falls back to Defaults, supports hot-reload."""
import threading
from typing import Any, Optional
from .defaults import Defaults
from ..database.config_repo import get_config, set_config, ensure_defaults

# All frames are normalised to this resolution before any detection, so
# fractional ROI values are always scaled against these fixed constants —
# never against the raw camera dimensions.
_REF_W, _REF_H = 1920, 1080

# Keys whose values are [x1,y1,x2,y2] or [x,y,w,h] and may be stored as
# fractions (0-1).  get() auto-scales them to pixels using the reference dims.
_ROI_KEYS = frozenset({
    'lap_current_roi', 'lap_total_roi', 'coin_left_roi', 'coin_right_roi',
    'finish_roi', 'mushroom_roi', 'minimap_roi',
    'char_name_roi', 'costume_roi', 'kart_name_roi', 'course_name_roi',
})


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
            val = self._data.get(key, default)
        # Auto-scale fractional ROIs to camera pixel space.
        # Fractional ROIs have all values in [0, 1]; absolute ones have values > 1.
        if key in _ROI_KEYS and isinstance(val, list) and len(val) >= 4:
            try:
                if max(float(v) for v in val[:4]) <= 1.0:
                    return [
                        int(round(float(val[0]) * _REF_W)),
                        int(round(float(val[1]) * _REF_H)),
                        int(round(float(val[2]) * _REF_W)),
                        int(round(float(val[3]) * _REF_H)),
                    ]
            except (TypeError, ValueError):
                pass
        return val

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
