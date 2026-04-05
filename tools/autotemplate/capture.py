"""
Capture card reader + template saver for autotemplate.

Reads ROI configuration from the tracker's SQLite database so it respects
whatever the user calibrated in the setup wizard.  Falls back to the same
hardcoded defaults the tracker uses if no DB value exists.
"""
import json
import os
import sqlite3
import time
from typing import Optional

import cv2
import numpy as np

# ── ROI defaults (mirrors mkw_tracker/detection/selection.py + mushrooms.py) ─

_DEFAULT_ROIS = {
    "char_name_roi":   (1210, 830, 1770, 894),
    "costume_roi":     (1210, 916, 1770, 958),
    "kart_name_roi":   (1240, 830, 1740, 894),
    "course_name_roi": (163,  387, 647,  462),
    "mushroom_roi":    (50,   50,  240,  240),
}

# ── Category → (roi_key, images_subdir, processing) ──────────────────────────

_CATEGORY_CONFIG = {
    "characters": ("char_name_roi",   "images/characters", "binary"),
    "karts":      ("kart_name_roi",   "images/karts",      "binary"),
    "courses":    ("course_name_roi", "images/courses",    "binary"),
    "costumes":   ("costume_roi",     "images/costumes",   "raw"),
    "mushrooms":  ("mushroom_roi",    "images/mushrooms",  "binary"),
}

_REF_W, _REF_H = 1920, 1080
_BINARY_THRESH = 170


def _load_roi_from_db(db_path: str, key: str) -> Optional[tuple]:
    """Read a single ROI list from the config table and return as (x1,y1,x2,y2)."""
    try:
        conn = sqlite3.connect(db_path)
        cur  = conn.execute("SELECT value FROM config WHERE key=? LIMIT 1", (key,))
        row  = cur.fetchone()
        conn.close()
        if row:
            val = json.loads(row[0])
            if isinstance(val, list) and len(val) == 4:
                return tuple(int(v) for v in val)
    except Exception:
        pass
    return None


class CaptureSession:
    """
    Opens a capture card and saves template images for the autotemplate runner.

    db_path   : path to mkw_tracker.db (can be a Windows path via /mnt/c/…)
    device    : OpenCV device index or path (e.g. 0 or '/dev/video0')
    repo_root : path to the repo root (images/ lives here)
    """

    def __init__(self, db_path: str, repo_root: str, device: int | str = 0):
        self._db_path   = db_path
        self._repo_root = repo_root
        self._device    = device
        self._cap       = None
        self._roi_cache: dict[str, tuple] = {}

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def open(self) -> None:
        self._cap = cv2.VideoCapture(self._device)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open capture device {self._device!r}")
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1920)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        # Drain a few frames so the first read isn't stale
        for _ in range(5):
            self._cap.read()
        print(f"Capture card opened: {self._device}")

    def close(self) -> None:
        if self._cap:
            self._cap.release()
            self._cap = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()

    # ── ROI helpers ─────────────────────────────────────────────────────────

    def _roi(self, key: str) -> tuple:
        if key not in self._roi_cache:
            val = _load_roi_from_db(self._db_path, key)
            self._roi_cache[key] = val if val else _DEFAULT_ROIS.get(key, (0, 0, 100, 100))
        return self._roi_cache[key]

    # ── Frame helpers ────────────────────────────────────────────────────────

    def _read_frame(self, settle_frames: int = 3) -> Optional[np.ndarray]:
        """Read a fresh frame, discarding `settle_frames` to flush buffer lag."""
        if self._cap is None:
            raise RuntimeError("Session not open")
        for _ in range(settle_frames):
            self._cap.read()
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        h, w = frame.shape[:2]
        if w != _REF_W or h != _REF_H:
            frame = cv2.resize(frame, (_REF_W, _REF_H), interpolation=cv2.INTER_LINEAR)
        return frame

    # ── Template saving ──────────────────────────────────────────────────────

    def capture_template(self, category: str, item_file: str) -> bool:
        """
        Read the current frame, crop the configured ROI for this category,
        process it (binarize or raw), and save to images/{category}/{item_file}.png.

        Returns True on success.
        """
        cfg = _CATEGORY_CONFIG.get(category)
        if cfg is None:
            print(f"[WARN] Unknown category: {category!r}")
            return False

        roi_key, img_subdir, processing = cfg
        roi = self._roi(roi_key)

        frame = self._read_frame()
        if frame is None:
            print("[WARN] Failed to read frame from capture card")
            return False

        x1, y1, x2, y2 = roi
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            print(f"[WARN] Empty crop for ROI {roi}")
            return False

        if processing == "binary":
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            _, processed = cv2.threshold(gray, _BINARY_THRESH, 255, cv2.THRESH_BINARY)
        else:
            # raw — load_template_dir applies edges at load time for costumes
            processed = crop

        out_dir  = os.path.join(self._repo_root, img_subdir)
        out_path = os.path.join(out_dir, f"{item_file}.png")
        os.makedirs(out_dir, exist_ok=True)
        cv2.imwrite(out_path, processed)
        print(f"  Saved: {out_path}")
        return True

    def preview_roi(self, category: str) -> None:
        """Show a live OpenCV window of the ROI for the given category (debug aid)."""
        cfg = _CATEGORY_CONFIG.get(category)
        if cfg is None:
            return
        roi_key, _, _ = cfg
        roi = self._roi(roi_key)
        print(f"Previewing ROI {roi} — press Q to close")
        while True:
            frame = self._read_frame(settle_frames=0)
            if frame is None:
                break
            x1, y1, x2, y2 = roi
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            small = cv2.resize(frame, (960, 540))
            cv2.imshow("ROI Preview", small)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        cv2.destroyAllWindows()
