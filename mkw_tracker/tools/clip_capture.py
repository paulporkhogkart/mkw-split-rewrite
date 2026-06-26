"""Command-driven ffmpeg preview↔record manager feeding the tracker frame_ref.

Reuses record_clips' ffmpeg machinery. One ffmpeg owns the card: a preview pipe
between clips, a tee record pipe during a clip (4K .mkv + 1080p frames). Frames
from whichever pipe is active are pumped into frame_ref[0] for detection/grounding.
"""
import json
import os
import time
import warnings
from typing import Optional

from .record_clips import (FramePipe, tee_cmd, preview_cmd, pick_encoder,
                           _bundled_bin, _resolve_device)


class ClipCaptureManager:
    def __init__(self, out_dir, device, size, fps, frame_ref, *,
                 _pipe_factory=FramePipe, clock=time.monotonic,
                 encoder=None, quality=14):
        self.out_dir = out_dir
        self.device = device
        self.size = size
        self.fps = fps
        self.frame_ref = frame_ref
        self._pf = _pipe_factory
        self._clock = clock
        try:
            self._ffmpeg = _bundled_bin("ffmpeg")
            enc_text = __import__("subprocess").run(
                [self._ffmpeg, "-hide_banner", "-encoders"],
                capture_output=True, text=True).stdout
            self._enc, self._enc_args = pick_encoder(enc_text, encoder, quality)
        except Exception:
            self._ffmpeg = "ffmpeg"
            self._enc, self._enc_args = "libx264", ["-preset", "superfast", "-crf", str(quality)]
        os.makedirs(out_dir, exist_ok=True)
        self._pipe: Optional[object] = None
        self._item: Optional[str] = None
        self._t0 = 0.0
        self._events: dict = {}

    # ── pipe lifecycle ────────────────────────────────────────────────────────
    def start_preview(self):
        self._stop_pipe()
        self._pipe = self._pf(preview_cmd(self._ffmpeg, self.device, self.size, self.fps))

    def _stop_pipe(self):
        if self._pipe is not None:
            try:
                self._pipe.stop()
            finally:
                self._pipe = None

    def pump(self):
        """Copy the active pipe's latest frame into frame_ref[0] (call each tick)."""
        if self._pipe is not None:
            f = self._pipe.latest()
            if f is not None:
                self.frame_ref[0] = f

    # ── recording ─────────────────────────────────────────────────────────────
    def _path(self, item, ext): return os.path.join(self.out_dir, f"{item}.{ext}")

    def exists(self, item) -> bool:
        p = self._path(item, "mkv")
        return os.path.exists(p) and os.path.getsize(p) > 0

    def begin(self, item):
        self._stop_pipe()
        time.sleep(0.3)                       # let the device free before re-opening
        self._item = item
        self._events = {"item": item, "fps": self.fps,
                        "swap_t": None, "flourish_t": None,
                        "flourish_end_t": None, "duration_t": None}
        cmd = tee_cmd(self._ffmpeg, self.device, self.size, self.fps,
                      duration=10_000, out_path=self._path(item, "mkv"),
                      enc=self._enc, enc_args=self._enc_args)
        self._pipe = self._pf(cmd, quiet=False)
        self._t0 = self._clock()

    def mark(self, event):
        key = {"swap": "swap_t", "flourish": "flourish_t"}[event]
        self._events[key] = self._clock() - self._t0

    def set_duration_end(self):
        t = self._clock() - self._t0
        self._events["flourish_end_t"] = t
        self._events["duration_t"] = t

    def end(self) -> dict:
        ev = dict(self._events)
        with open(self._path(self._item, "events.json"), "w", encoding="utf-8") as f:
            json.dump(ev, f, indent=2)
        self.start_preview()                  # stops the record pipe, reopens preview
        self._item = None
        return ev

    def abort(self):
        item = self._item
        self.start_preview()
        for ext in ("mkv", "events.json"):
            p = self._path(item, ext)
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError as e:
                    warnings.warn(f"abort: could not delete {p}: {e}")
        self._item = None
