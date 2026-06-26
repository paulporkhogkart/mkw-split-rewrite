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
        self._pipe = self._pf(
            preview_cmd(self._ffmpeg, self.device, self.size, self.fps,
                        scale_w=1920, scale_h=1080),
            w=1920, h=1080)

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
        print(f"[clip] begin({item!r}): swapping preview -> 4K record (encoder={self._enc})", flush=True)
        self._stop_pipe()
        self._item = item
        self._events = {"item": item, "fps": self.fps,
                        "swap_t": None, "flourish_t": None,
                        "flourish_end_t": None, "duration_t": None}
        out_path = self._path(item, "mkv")
        cmd = tee_cmd(self._ffmpeg, self.device, self.size, self.fps,
                      duration=10_000, out_path=out_path,
                      enc=self._enc, enc_args=self._enc_args,
                      scale_w=1920, scale_h=1080)
        # The capture card can take a moment to release after the preview ffmpeg is
        # killed, so the tee may fail to open it on the first try. Retry, and CONFIRM the
        # first frame before returning — so a return means recording is genuinely live,
        # and a failure prints the real ffmpeg error instead of silently freezing the feed.
        for attempt, gap in enumerate((0.6, 1.2), start=1):
            time.sleep(gap)
            self._pipe = self._pf(cmd, quiet=True, w=1920, h=1080)
            self._t0 = self._clock()
            has = getattr(self._pipe, "has_frame", None)
            if has is None:
                return                         # pipe can't report readiness (test fake) — assume live
            alive = getattr(self._pipe, "alive", None)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 4.0:
                if has():
                    print(f"[clip]   recording (frames after {time.monotonic() - t0:.1f}s, "
                          f"attempt {attempt}) -> {out_path}", flush=True)
                    return
                if alive is not None and not alive():
                    break
                time.sleep(0.05)
            err = (getattr(self._pipe, "error_tail", lambda n=8: "")() or "(no ffmpeg stderr)")
            why = "ffmpeg exited" if (alive is not None and not alive()) else "alive but no frames in 4s"
            print(f"[clip]   attempt {attempt} FAILED — {why}:\n        "
                  + err.replace("\n", "\n        "), flush=True)
            if attempt == 1:
                self._stop_pipe()              # clean retry; keep the LAST dead pipe for the watchdog
        # Both attempts failed: leave _item + the dead pipe so the main-loop watchdog
        # surfaces it, aborts (restoring preview), and emits clip_done{error}.
        print(f"[clip] begin({item!r}): RECORD PIPE WOULD NOT START — see ffmpeg error(s) above.", flush=True)

    # ── record health (main-loop watchdog) ──────────────────────────────────────
    def recording(self) -> bool:
        return self._item is not None

    def record_age(self) -> float:
        """Seconds since begin() (0 if not recording)."""
        return (self._clock() - self._t0) if self._item is not None else 0.0

    def pipe_seq(self) -> int:
        """Frame counter of whatever pipe is active (preview OR record); -1 if unknown.
        A frozen value means the active ffmpeg has stopped delivering frames."""
        fn = getattr(self._pipe, "frames_seen", None)
        return fn() if fn else -1

    def record_alive(self) -> bool:
        """False once the record ffmpeg has exited (encoder/device failure)."""
        alive = getattr(self._pipe, "alive", None)
        return bool(alive()) if alive else (self._pipe is not None)

    def record_has_frame(self) -> bool:
        has = getattr(self._pipe, "has_frame", None)
        return bool(has()) if has else (self._pipe is not None)

    def record_error(self, n: int = 10) -> str:
        et = getattr(self._pipe, "error_tail", None)
        return et(n) if et else ""

    def mark(self, event):
        key = {"swap": "swap_t", "flourish": "flourish_t"}.get(event)
        if key is None:
            warnings.warn(f"clip mark: unknown event {event!r}")
            return
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
