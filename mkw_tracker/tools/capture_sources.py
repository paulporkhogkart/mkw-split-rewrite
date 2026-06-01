"""Template-source full-screenshot capture tool.

Hover over every character / costume / kart / course on the live capture-card
feed; this tool reuses the existing ScreenDetector + SelectionTracker to label
what you're hovering and saves a FULL 1920x1080 screenshot the first time it
sees each one, named to match the existing template files.  The shots are
processed offline later into new match templates.

Run:
    python -m mkw_tracker.tools.capture_sources [--device NAME] [--lang LANG]
        [--out DIR] [--min-conf 0.8] [--hold 3] [--no-sound]

This module is split into a pure, unit-tested core (NameResolver, CaptureGate,
resume helpers) and a thin I/O shell (camera loop + OpenCV HUD + beep) that is
exercised manually with a capture card.
"""
import argparse
import os
import re
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

import cv2

from ..detection.selection import _norm_name
from ..detection.screen import Screen

# Capture categories, also the output subfolder names.
CATEGORIES: Tuple[str, ...] = ("characters", "costumes", "karts", "courses")

# SelectionState fields each selection screen exposes: (category, name, conf).
_SCREEN_FIELDS: Dict[Screen, List[Tuple[str, str, str]]] = {
    Screen.CHARACTER_SELECT: [("characters", "character", "character_conf"),
                              ("costumes",   "costume",   "costume_conf")],
    Screen.KART_SELECT:      [("karts",   "kart",   "kart_conf")],
    Screen.COURSE_SELECT:    [("courses", "course", "course_conf")],
}


def _slug(name: str) -> str:
    """Lowercase, collapse non-alphanumeric runs to single '_' (filename fallback)."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


# ---------------------------------------------------------------------------
# NameResolver
# ---------------------------------------------------------------------------

class NameResolver:
    """Maps a detected display name to its on-disk template base filename.

    Built once per language from ``images/<category>/<lang>/*.png``.  The tracker
    reports display names ("All-Terrain", "Baby Daisy"); we need the matching
    template base ("all_terrain", "baby_daisy") so captures are drop-in for the
    template pipeline.  Lookup is separator/case-insensitive (via _norm_name), so
    hyphens/apostrophes/periods round-trip correctly.  Unknown names fall back to
    a slug of the display name.
    """

    def __init__(self, lang: str, images_root: str):
        self.lang = lang
        self._maps: Dict[str, Dict[str, str]] = {}
        for cat in CATEGORIES:
            directory = os.path.join(images_root, cat, lang)
            mapping: Dict[str, str] = {}
            if os.path.isdir(directory):
                for filename in os.listdir(directory):
                    low = filename.lower()
                    if not low.endswith(".png") or low.endswith("_tight.png"):
                        continue
                    base = filename[:-4]
                    mapping[_norm_name(base)] = base
            self._maps[cat] = mapping

    def resolve(self, category: str, display_name: str) -> str:
        """Return the template base filename for *display_name* in *category*."""
        return (self._maps.get(category, {}).get(_norm_name(display_name))
                or _slug(display_name))

    def known(self, category: str) -> Set[str]:
        """All template base filenames for *category* (the capture checklist)."""
        return set(self._maps.get(category, {}).values())


# ---------------------------------------------------------------------------
# CaptureGate
# ---------------------------------------------------------------------------

class CaptureGate:
    """Decides when a detected item should be auto-captured.

    A field's detection must hold the same resolved filename for ``hold``
    consecutive ``observe`` calls at confidence >= ``min_conf`` before it fires.
    Each filename fires at most once per category (global dedup) and never when
    already captured or skipped.
    """

    def __init__(self, resolver: NameResolver, min_conf: float = 0.8, hold: int = 3):
        self.resolver = resolver
        self.min_conf = min_conf
        self.hold = hold
        self.captured: Dict[str, Set[str]] = {c: set() for c in CATEGORIES}
        self.skipped:  Dict[str, Set[str]] = {c: set() for c in CATEGORIES}
        self._last:   Dict[str, Optional[str]] = {c: None for c in CATEGORIES}
        self._streak: Dict[str, int] = {c: 0 for c in CATEGORIES}

    def _fields(self, screen) -> List[Tuple[str, str, str]]:
        return _SCREEN_FIELDS.get(screen, [])

    def observe(self, screen, state) -> List[Tuple[str, str]]:
        """Feed one detection frame; return the list of (category, base) that fired."""
        fired: List[Tuple[str, str]] = []
        for cat, name_attr, conf_attr in self._fields(screen):
            display = getattr(state, name_attr)
            conf = getattr(state, conf_attr)
            base = self.resolver.resolve(cat, display) if display else None
            fired.extend(self._step(cat, base, conf))
        return fired

    def _step(self, cat: str, base: Optional[str], conf: float) -> List[Tuple[str, str]]:
        if base is None or conf < self.min_conf:
            self._last[cat] = None
            self._streak[cat] = 0
            return []
        if base == self._last[cat]:
            self._streak[cat] += 1
        else:
            self._last[cat] = base
            self._streak[cat] = 1
        if (self._streak[cat] >= self.hold
                and base not in self.captured[cat]
                and base not in self.skipped[cat]):
            self.captured[cat].add(base)
            return [(cat, base)]
        return []

    def mark_captured(self, category: str, base: str) -> None:
        self.captured[category].add(base)

    def skip(self, category: str, base: str) -> None:
        self.skipped[category].add(base)

    def remaining(self, category: str) -> Set[str]:
        return (self.resolver.known(category)
                - self.captured[category] - self.skipped[category])

    def status(self, category: str, base: str) -> str:
        if base in self.skipped[category]:
            return "SKIPPED"
        if base in self.captured[category]:
            return "CAPTURED"
        return "NEW"

    def current_targets(self, screen, state) -> List[Tuple[str, str, float, str]]:
        """Detected (category, base, conf, status) for the current screen's fields.

        Skips fields with nothing detected.  Read-only: does not advance streaks.
        Used by the HUD and by force-capture / skip key handlers.
        """
        out: List[Tuple[str, str, float, str]] = []
        for cat, name_attr, conf_attr in self._fields(screen):
            display = getattr(state, name_attr)
            if not display:
                continue
            base = self.resolver.resolve(cat, display)
            out.append((cat, base, getattr(state, conf_attr), self.status(cat, base)))
        return out


# ---------------------------------------------------------------------------
# Resume from disk
# ---------------------------------------------------------------------------

def scan_existing_captures(out_root: str, lang: str) -> Dict[str, Set[str]]:
    """Return ``{category: {base, ...}}`` for PNGs already under out_root/lang/."""
    found: Dict[str, Set[str]] = {c: set() for c in CATEGORIES}
    for cat in CATEGORIES:
        directory = os.path.join(out_root, lang, cat)
        if not os.path.isdir(directory):
            continue
        for filename in os.listdir(directory):
            if filename.lower().endswith(".png"):
                found[cat].add(filename[:-4])
    return found


def prime_gate_from_disk(gate: CaptureGate, out_root: str, lang: str) -> None:
    """Mark every already-saved capture as captured so it is not re-grabbed."""
    for cat, bases in scan_existing_captures(out_root, lang).items():
        for base in bases:
            gate.mark_captured(cat, base)


# ===========================================================================
# I/O shell  -  camera loop + OpenCV HUD + beep.  Not unit-tested (hardware);
# all decision logic lives in the pure units above.
# ===========================================================================

_REF_W, _REF_H = 1920, 1080
_OBSERVE_INTERVAL = 0.1     # seconds between gate.observe calls (~tracker scan rate)
_FRAME_INTERVAL = 1.0 / 30  # render/loop cap

_STATUS_COLOR = {
    "NEW":      (60, 215, 235),   # amber  - available to grab
    "CAPTURED": (150, 150, 150),  # gray   - done
    "SKIPPED":  (90, 90, 200),    # dim red
}


def _beep(enabled: bool = True) -> None:
    """Fire a short beep off-thread (never stalls the loop). No-op if unavailable."""
    if not enabled:
        return
    def _ring():
        try:
            import winsound
            winsound.Beep(1000, 90)
        except Exception:
            pass
    threading.Thread(target=_ring, daemon=True).start()


def _resize_1080p(frame):
    """Resize to 1920x1080 if needed (matches main._norm; calibration LUT stays off)."""
    h, w = frame.shape[:2]
    if w == _REF_W and h == _REF_H:
        return frame
    return cv2.resize(frame, (_REF_W, _REF_H), interpolation=cv2.INTER_LINEAR)


def _save_capture(out_root: str, lang: str, category: str, base: str, frame) -> str:
    """Write the full frame to out_root/lang/category/base.png; return the path."""
    directory = os.path.join(out_root, lang, category)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{base}.png")
    cv2.imwrite(path, frame)
    return path


def _build_detector(lang: str):
    """Construct a ScreenDetector with persisted tell-tree overrides applied,
    exactly as main.py does, so screen detection behaves like the real app."""
    from ..detection.screen import ScreenDetector
    from ..database.config_repo import get_config
    from ..database.tell_repo import groups_from_blob
    detector = ScreenDetector(on_screen_change=None, switch2_language=lang)
    for screen_enum, tell in detector._tells_by_screen.items():
        blob = get_config(f"tell_tree_{screen_enum.name}")
        if blob:
            tell.groups = groups_from_blob(blob)
    for tell in detector._tells_by_screen.values():
        tell.load(lang)
    return detector


def _put(img, text, org, color=(235, 235, 235), scale=0.5, thick=1):
    """Draw text with a black outline for legibility over the video."""
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def _draw_wrapped(display, names, x, y, max_rows=8):
    """Render a wrapped, truncated list of remaining item names. Returns next y."""
    if not names:
        _put(display, "  (all captured)", (x, y), (120, 170, 120), 0.4)
        return y + 18
    rows, line = [], ""
    for n in names:
        cand = (line + "  " + n).strip()
        if len(cand) > 48 and line:
            rows.append(line)
            line = n
        else:
            line = cand
    if line:
        rows.append(line)
    for r in rows[:max_rows]:
        _put(display, "  " + r, (x, y), (180, 180, 180), 0.4)
        y += 16
    if len(rows) > max_rows:
        _put(display, f"  ... +{len(rows) - max_rows} more rows", (x, y), (140, 140, 140), 0.4)
        y += 16
    return y


def _draw_hud(display, screen, score, gate, state, flash_text):
    h, w = display.shape[:2]
    panel_w = 440
    overlay = display.copy()
    cv2.rectangle(overlay, (0, 0), (panel_w, h), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.45, display, 0.55, 0, display)

    y = 30
    _put(display, f"{screen.name}  ({score:.2f})", (14, y), (235, 235, 235), 0.62, 2)
    y += 26
    _put(display, f"lang {gate.resolver.lang}", (14, y), (170, 170, 170), 0.45)
    y += 28

    targets = gate.current_targets(screen, state)
    if targets:
        _put(display, "detecting:", (14, y), (205, 205, 205), 0.45)
        y += 22
        for cat, base, conf, stat in targets:
            col = _STATUS_COLOR.get(stat, (235, 235, 235))
            _put(display, f"  {cat[:4]:<4} {base[:18]:<18} {conf:0.2f}  {stat}",
                 (14, y), col, 0.45)
            y += 22
    y += 10

    _put(display, "progress:", (14, y), (205, 205, 205), 0.45)
    y += 22
    for cat in CATEGORIES:
        done, total = len(gate.captured[cat]), len(gate.resolver.known(cat))
        _put(display, f"  {cat:<11} {done:>3}/{total:<3}", (14, y), (200, 220, 200), 0.45)
        y += 22
    y += 10

    for cat in [c for c, _, _ in _SCREEN_FIELDS.get(screen, [])]:
        rem = sorted(gate.remaining(cat))
        _put(display, f"{cat} left ({len(rem)}):", (14, y), (205, 205, 205), 0.45)
        y += 20
        y = _draw_wrapped(display, rem, 14, y)
        y += 8

    _put(display, "SPACE force  .  s skip  .  Tab HUD  .  q quit",
         (14, h - 14), (170, 170, 170), 0.45)

    if flash_text:
        _put(display, flash_text, (panel_w + 30, 44), (60, 220, 60), 0.85, 2)


def run(args):
    from ..config.settings import get_settings
    from ..database.migrations import apply_migrations
    from ..detection.selection import SelectionTracker
    from ..utils.camera import build_camera_source
    from ..utils.paths import resource_path, data_dir

    apply_migrations()
    settings = get_settings()
    lang = args.lang or settings.get("switch2_language", "en_uk") or "en_uk"
    device = args.device if args.device is not None else (settings.get("camera_device", "") or None)
    out_root = args.out or str(data_dir() / "captures")
    images_root = resource_path("images")

    resolver = NameResolver(lang, images_root)
    gate = CaptureGate(resolver, min_conf=args.min_conf, hold=args.hold)
    prime_gate_from_disk(gate, out_root, lang)

    detector = _build_detector(lang)
    tracker = SelectionTracker(switch2_language=lang)

    print(f"[capture] lang={lang!r}  device={device!r}  out={out_root!r}  "
          f"min_conf={args.min_conf}  hold={args.hold}")
    for cat in CATEGORIES:
        print(f"[capture]   {cat:<11} {len(gate.captured[cat])}/{len(resolver.known(cat))} already on disk")
    print("[capture] Hover items in-game. q=quit  SPACE=force  s=skip  Tab=HUD")

    try:
        cap = build_camera_source(device_name=device)
    except Exception as e:
        print(f"[capture] camera open failed: {e}")
        return

    win = "MKW Capture"
    show_hud = True
    flash_text, flash_until = "", 0.0
    last_observe = 0.0

    try:
        while True:
            t = time.perf_counter()
            ret, frame = cap.read()
            if not ret or frame is None:
                if (cap.waitKey(1) & 0xFF) in (ord("q"), 27):
                    break
                continue
            frame = _resize_1080p(frame)

            screen, perf = detector.update(frame)
            tracker.update(frame, screen, perf.current_score)
            state = tracker.state

            if t - last_observe >= _OBSERVE_INTERVAL:
                last_observe = t
                for cat, base in gate.observe(screen, state):
                    path = _save_capture(out_root, lang, cat, base, frame)
                    _beep(not args.no_sound)
                    flash_text, flash_until = f"SAVED {cat}/{base}", t + 0.6
                    print(f"[capture] saved {path}")

            display = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_LINEAR)
            if show_hud:
                _draw_hud(display, screen, perf.current_score, gate, state,
                          flash_text if t < flash_until else "")
            cv2.imshow(win, display)

            key = (cap.waitKey(1) if cap is not None else cv2.waitKey(1)) & 0xFF
            if key in (ord("q"), 27):
                break
            elif key == ord(" "):
                for cat, base, conf, stat in gate.current_targets(screen, state):
                    path = _save_capture(out_root, lang, cat, base, frame)
                    gate.mark_captured(cat, base)
                    _beep(not args.no_sound)
                    flash_text, flash_until = f"FORCED {cat}/{base}", time.perf_counter() + 0.6
                    print(f"[capture] forced {path}")
            elif key == ord("s"):
                for cat, base, conf, stat in gate.current_targets(screen, state):
                    gate.skip(cat, base)
                    print(f"[capture] skipped {cat}/{base}")
            elif key == 9:   # Tab
                show_hud = not show_hud

            spare = _FRAME_INTERVAL - (time.perf_counter() - t)
            if spare > 0:
                time.sleep(spare)
    finally:
        try:
            cap.release()
        except Exception:
            pass
        cv2.destroyAllWindows()


def main():
    p = argparse.ArgumentParser(
        description="Capture full 1920x1080 screenshots of every character/costume/"
                    "kart/course by hovering in-game; auto-labeled via the existing "
                    "detection system, named to match the template files.")
    p.add_argument("--device", default=None,
                   help="Capture device name (default: saved camera_device, else auto-probe).")
    p.add_argument("--lang", default=None,
                   help="Template language + output subfolder (default: switch2_language setting).")
    p.add_argument("--out", default=None,
                   help="Output root (default: <data_dir>/captures).")
    p.add_argument("--min-conf", type=float, default=0.8, dest="min_conf",
                   help="Min confidence to auto-capture (default 0.8).")
    p.add_argument("--hold", type=int, default=3,
                   help="Consecutive stable scans before auto-capture (default 3).")
    p.add_argument("--no-sound", action="store_true", help="Disable the capture beep.")
    run(p.parse_args())


if __name__ == "__main__":
    main()
