"""
Minimap calibration tool: draw a course's minimap ROI and click its start seed
on the live capture feed, straight into the tracker's SQLite tables.

Port of the original ``seed-clicker.py`` (mkw-split-poc) that produced the 30
launch-course seeds/ROIs, rewired from the legacy JSON files to
``minimap_seeds`` / ``minimap_rois`` and with the seed snapped onto the ring by
``minimap.badge.refine_seed_centre`` (the same snap the live tracker applies).

Usage:
    python -m mkw_tracker.tools.minimap_calib                       # saved camera + language
    python -m mkw_tracker.tools.minimap_calib --course "SNES Mario Circuit 1"   # force the course key
    python -m mkw_tracker.tools.minimap_calib --scale 0.75          # smaller window (coords map back)

Workflow per course: pick the course in-game (the tool reads the course name off
the course-select screen like the tracker does), start the race, and on the
countdown / first frames:
    right-drag   the minimap box for this course        -> minimap_rois
    left-click   the centre of your ring on the minimap -> minimap_seeds (snapped)
    p            print the migration rows for every course touched this session
                 (also written to temp/minimap_seed_rows.sql) - paste into
                 database/migrations.py so other installs get them
    q            quit

A magnified loupe follows the cursor over the minimap so the click lands on the
badge centre. The stored key is the tracker's course display name (what the live
engine looks up), so a course with a template is required first - run
``tools.capture_sources --manual snes`` + ``scripts/gen_selection_templates.py``
before calibrating a brand-new course.
"""

import argparse
import os
import time
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from ..minimap.tracker import MINIMAP_ROI

DEFAULT_RADIUS = 20          # matches the shipped _SEED_V2 rows
LOUPE_SRC_R = 24             # source radius to magnify (full-res px)
LOUPE_DST_R = 80             # loupe radius on screen (display px)
MIN_ROI_SIDE = 10

Rect = Dict[str, int]


# ---------------------------------------------------------------------------
# SeedSession - pure logic (persistence + refinement injected, so it is testable)
# ---------------------------------------------------------------------------

class SeedSession:
    """Records seeds/ROIs for courses and formats them as migration rows.

    get_seed(course) -> {cx, cy, radius, ...} | None
    set_seed(course, cx, cy, radius)
    get_roi(course)  -> {x, y, w, h} | None
    set_roi(course, x, y, w, h)
    refine(frame, x, y) -> (x, y): snap a click onto the ring (None = no snap)
    """

    def __init__(self, get_seed: Callable, set_seed: Callable,
                 get_roi: Callable, set_roi: Callable,
                 refine: Optional[Callable] = None, radius: int = DEFAULT_RADIUS):
        self._get_seed, self._set_seed = get_seed, set_seed
        self._get_roi, self._set_roi = get_roi, set_roi
        self._refine = refine
        self.radius = radius
        self.touched: List[str] = []       # courses saved this session, in order
        self.last_saved = ""

    def _touch(self, course: str) -> None:
        if course not in self.touched:
            self.touched.append(course)
        self.last_saved = course

    def click_seed(self, course: str, x: int, y: int, frame=None) -> Tuple[int, int]:
        """Persist a seed at the click, snapped onto the ring when a frame is given."""
        cx, cy = int(x), int(y)
        if frame is not None and self._refine is not None:
            cx, cy = (int(v) for v in self._refine(frame, cx, cy))
        self._set_seed(course, cx, cy, self.radius)
        self._touch(course)
        return cx, cy

    def drag_roi(self, course: str, x1: int, y1: int, x2: int, y2: int) -> Optional[Rect]:
        """Persist a ROI from any two drag corners; None when the box is too small."""
        x, y = min(x1, x2), min(y1, y2)
        w, h = abs(x2 - x1), abs(y2 - y1)
        if w < MIN_ROI_SIDE or h < MIN_ROI_SIDE:
            return None
        self._set_roi(course, int(x), int(y), int(w), int(h))
        self._touch(course)
        return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}

    def seed_for(self, course: str) -> Optional[dict]:
        return self._get_seed(course) if course else None

    def roi_for(self, course: str) -> Optional[dict]:
        return self._get_roi(course) if course else None

    def migration_rows(self, courses: Optional[List[str]] = None) -> str:
        """SQL in the _SEED_V2 style for the given courses (default: touched this session)."""
        names = list(courses) if courses is not None else list(self.touched)
        seeds, rois = [], []
        for c in names:
            s = self._get_seed(c)
            if s:
                seeds.append(f"    ({_q(c)+',':<24}{s['cx']:>5},{s['cy']:>5},{int(s.get('radius') or self.radius):>4})")
            r = self._get_roi(c)
            if r:
                rois.append(f"    ({_q(c)+',':<24}{r['x']:>5},{r['y']:>5},{r['w']:>5},{r['h']:>5})")
        out = []
        if seeds:
            out.append("INSERT OR IGNORE INTO minimap_seeds (course, cx, cy, radius) VALUES\n"
                       + ",\n".join(seeds) + ";")
        if rois:
            out.append("INSERT OR IGNORE INTO minimap_rois (course, x, y, w, h) VALUES\n"
                       + ",\n".join(rois) + ";")
        return "\n\n".join(out)


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# Live tool
# ---------------------------------------------------------------------------

def _draw_loupe(display, frame, mx, my, scale: float) -> None:
    """Magnified loupe anchored upper-left of the cursor (never covers the minimap)."""
    fx, fy = int(mx / scale), int(my / scale)
    sx1, sy1 = max(0, fx - LOUPE_SRC_R), max(0, fy - LOUPE_SRC_R)
    sx2, sy2 = min(frame.shape[1], fx + LOUPE_SRC_R), min(frame.shape[0], fy + LOUPE_SRC_R)
    src = frame[sy1:sy2, sx1:sx2]
    if src.size == 0:
        return
    diam = LOUPE_DST_R * 2
    zoomed = cv2.resize(src, (diam, diam), interpolation=cv2.INTER_NEAREST)
    gap = 12
    lx1 = mx - LOUPE_DST_R - gap - LOUPE_DST_R
    ly1 = my - LOUPE_DST_R - gap - LOUPE_DST_R
    dh, dw = display.shape[:2]
    lx1 = max(0, min(dw - diam, lx1))
    ly1 = max(0, min(dh - diam, ly1))
    lx2, ly2 = lx1 + diam, ly1 + diam
    if lx2 > dw or ly2 > dh:
        return
    mask = np.zeros((diam, diam), dtype=np.uint8)
    cv2.circle(mask, (LOUPE_DST_R, LOUPE_DST_R), LOUPE_DST_R - 1, 255, -1)
    roi = display[ly1:ly2, lx1:lx2]
    roi[mask == 255] = zoomed[mask == 255]
    lx, ly = lx1 + LOUPE_DST_R, ly1 + LOUPE_DST_R
    cv2.circle(display, (lx, ly), LOUPE_DST_R, (180, 180, 180), 1, cv2.LINE_AA)
    arm = LOUPE_DST_R - 6
    cv2.line(display, (lx - arm, ly), (lx + arm, ly), (0, 255, 255), 1, cv2.LINE_AA)
    cv2.line(display, (lx, ly - arm), (lx, ly + arm), (0, 255, 255), 1, cv2.LINE_AA)


def _put(img, text, org, color=(220, 220, 220), scale=0.55, thick=1):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def run(args) -> None:
    from ..config.settings import get_settings
    from ..database import replay_repo
    from ..database.migrations import apply_migrations
    from ..detection.selection import SelectionTracker
    from ..minimap.badge import refine_seed_centre
    from ..utils.camera import build_camera_source
    from .capture_sources import _build_detector, _resize_1080p

    apply_migrations()
    settings = get_settings()
    lang = args.lang or settings.get("switch2_language", "en_uk") or "en_uk"
    device = args.device if args.device is not None else (settings.get("camera_device", "") or None)
    scale = float(args.scale)

    refine = None if args.no_refine else (lambda frame, x, y: refine_seed_centre(frame, x, y))
    session = SeedSession(replay_repo.get_minimap_seed, replay_repo.set_minimap_seed,
                          replay_repo.get_minimap_roi, replay_repo.set_minimap_roi,
                          refine=refine)

    detector = _build_detector(lang)
    tracker = SelectionTracker(switch2_language=lang)
    try:
        cap = build_camera_source(device_name=device)
    except Exception as e:
        print(f"[minimap_calib] camera open failed: {e}")
        return

    win = "MKW Minimap Calibration"
    cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
    mouse = {"x": 0, "y": 0, "click": None, "drag0": None, "drag1": None, "drop": False}

    def on_mouse(event, x, y, flags, param):
        mouse["x"], mouse["y"] = x, y
        if event == cv2.EVENT_LBUTTONDOWN:
            mouse["click"] = (x, y)
        elif event == cv2.EVENT_RBUTTONDOWN:
            mouse["drag0"], mouse["drag1"], mouse["drop"] = (x, y), (x, y), False
        elif event == cv2.EVENT_MOUSEMOVE and mouse["drag0"] is not None and not mouse["drop"]:
            mouse["drag1"] = (x, y)
        elif event == cv2.EVENT_RBUTTONUP and mouse["drag0"] is not None:
            mouse["drag1"], mouse["drop"] = (x, y), True

    cv2.setMouseCallback(win, on_mouse)
    to_full = lambda x, y: (int(round(x / scale)), int(round(y / scale)))
    to_disp = lambda x, y: (int(round(x * scale)), int(round(y * scale)))
    rows_path = os.path.join("temp", "minimap_seed_rows.sql")

    def dump_rows() -> None:
        sql = session.migration_rows()
        if not sql:
            print("[minimap_calib] nothing saved this session yet")
            return
        os.makedirs(os.path.dirname(rows_path), exist_ok=True)
        with open(rows_path, "w", encoding="utf-8") as fh:
            fh.write(sql + "\n")
        print("\n" + sql + f"\n[minimap_calib] rows written to {rows_path}\n")

    print(f"[minimap_calib] lang={lang!r} device={device!r} scale={scale} "
          f"refine={'off' if args.no_refine else 'on'}"
          + (f" course override={args.course!r}" if args.course else ""))
    print("[minimap_calib] left-click = seed (snapped onto the ring), right-drag = minimap ROI, "
          "p = print migration rows, q = quit")

    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                if (cap.waitKey(1) & 0xFF) in (ord("q"), 27):
                    break
                continue
            frame = _resize_1080p(frame)
            screen, perf = detector.update(frame)
            tracker.update(frame, screen, perf.current_score)
            course = args.course or tracker.state.course

            display = frame if scale == 1.0 else cv2.resize(
                frame, (int(frame.shape[1] * scale), int(frame.shape[0] * scale)),
                interpolation=cv2.INTER_AREA)
            display = display.copy()

            # Default ROI (orange) and this course's stored ROI (blue) + seed (green).
            dx1, dy1 = to_disp(MINIMAP_ROI[0], MINIMAP_ROI[1])
            dx2, dy2 = to_disp(MINIMAP_ROI[0] + MINIMAP_ROI[2], MINIMAP_ROI[1] + MINIMAP_ROI[3])
            cv2.rectangle(display, (dx1, dy1), (dx2, dy2), (0, 200, 255), 1)
            roi = session.roi_for(course)
            if roi:
                rx1, ry1 = to_disp(roi["x"], roi["y"])
                rx2, ry2 = to_disp(roi["x"] + roi["w"], roi["y"] + roi["h"])
                cv2.rectangle(display, (rx1, ry1), (rx2, ry2), (255, 120, 0), 2)
                _put(display, "course ROI", (rx1 + 2, ry1 - 4), (255, 120, 0))
            seed = session.seed_for(course)
            if seed:
                sx, sy = to_disp(seed["cx"], seed["cy"])
                cv2.circle(display, (sx, sy), 5, (0, 255, 0), -1, cv2.LINE_AA)
                cv2.circle(display, (sx, sy), int(seed.get("radius") or DEFAULT_RADIUS) , (0, 255, 0), 1, cv2.LINE_AA)

            mx, my = mouse["x"], mouse["y"]
            cv2.line(display, (mx - 14, my), (mx + 14, my), (0, 255, 255), 1, cv2.LINE_AA)
            cv2.line(display, (mx, my - 14), (mx, my + 14), (0, 255, 255), 1, cv2.LINE_AA)
            _draw_loupe(display, frame, mx, my, scale)

            if mouse["drag0"] and mouse["drag1"] and not mouse["drop"]:
                (ax, ay), (bx, by) = mouse["drag0"], mouse["drag1"]
                cv2.rectangle(display, (min(ax, bx), min(ay, by)), (max(ax, bx), max(ay, by)), (255, 120, 0), 1)
                fw, fh = abs(bx - ax) / scale, abs(by - ay) / scale
                _put(display, f"{fw:.0f}x{fh:.0f}", (min(ax, bx) + 2, min(ay, by) - 4), (255, 120, 0))

            msg = ""
            if mouse["drop"]:
                (ax, ay), (bx, by) = mouse["drag0"], mouse["drag1"]
                mouse["drag0"] = mouse["drag1"] = None
                mouse["drop"] = False
                if not course:
                    msg = "no course detected - use --course"
                else:
                    r = session.drag_roi(course, *to_full(ax, ay), *to_full(bx, by))
                    msg = (f"saved ROI for {course}: {r}" if r else "ROI too small (min 10x10)")
                print(f"[minimap_calib] {msg}")
            if mouse["click"] is not None:
                cx, cy = mouse["click"]
                mouse["click"] = None
                if not course:
                    msg = "no course detected - use --course"
                else:
                    fx, fy = to_full(cx, cy)
                    sx, sy = session.click_seed(course, fx, fy, frame)
                    snap = "" if (sx, sy) == (fx, fy) else f" (snapped from {fx},{fy})"
                    msg = f"saved seed for {course}: ({sx}, {sy}) r={session.radius}{snap}"
                print(f"[minimap_calib] {msg}")

            lines = [
                f"Screen: {screen.name}",
                f"Course: {course or '--'}   seed:{'YES' if seed else 'NO'}  roi:{'YES' if roi else 'NO (default)'}",
                "Left-click: seed (snapped to ring)   Right-drag: minimap ROI",
                "p: print migration rows   q: quit",
                f"Saved this session: {len(session.touched)}"
                + (f"   last: {session.last_saved}" if session.last_saved else ""),
            ]
            ov = display.copy()
            cv2.rectangle(ov, (8, 8), (620, 8 + len(lines) * 24 + 12), (0, 0, 0), -1)
            cv2.addWeighted(ov, 0.5, display, 0.5, 0, display)
            for i, line in enumerate(lines):
                _put(display, line, (14, 30 + i * 24), (0, 255, 255) if i == 0 else (220, 220, 220))
            if session.touched:
                _put(display, "This session:", (int(display.shape[1] * 0.73), 24), (0, 255, 100))
                for i, c in enumerate(session.touched):
                    s_m = "S" if session.seed_for(c) else "-"
                    r_m = "R" if session.roi_for(c) else "-"
                    _put(display, f" [{s_m}{r_m}] {c}{'  <--' if c == course else ''}",
                         (int(display.shape[1] * 0.73), 24 + (i + 1) * 24), (180, 255, 180))

            cv2.imshow(win, display)
            key = cap.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            elif key == ord("p"):
                dump_rows()
    finally:
        if session.touched:
            dump_rows()
        try:
            cap.release()
        except Exception:
            pass
        cv2.destroyAllWindows()


def main():
    p = argparse.ArgumentParser(description="Draw a course's minimap ROI and click its start "
                                            "seed on the live feed; saves to the tracker DB.")
    p.add_argument("--device", default=None, help="Capture device name (default: saved camera_device).")
    p.add_argument("--lang", default=None, help="Template language (default: switch2_language setting).")
    p.add_argument("--course", default=None,
                   help="Force the course key instead of reading it off the course-select screen.")
    p.add_argument("--scale", type=float, default=1.0,
                   help="Window scale (1.0 = native 1920x1080; clicks map back to full-res).")
    p.add_argument("--no-refine", action="store_true", dest="no_refine",
                   help="Store the raw click instead of snapping it onto the ring.")
    run(p.parse_args())


if __name__ == "__main__":
    main()
