"""MinimapPlayer - loads replays from DB and draws dots/bubbles."""
import math
import os
import time
import cv2
import numpy as np
from typing import Optional

from ..detection.screen import Screen
from ..database.replay_repo import get_pb, get_history, get_friends_pbs
from ..utils.image import DISPLAY_SCALE
from ..utils.paths import resource_path

_HEAD_CACHE: dict = {}


def _load_head_image(player: str, size: int = 40) -> tuple:
    """Load and cache a player head BGRA image."""
    if player in _HEAD_CACHE:
        return _HEAD_CACHE[player]
    path = resource_path(os.path.join("images", "heads", f"{player}.png"))
    img  = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        blank = np.zeros((size, size, 4), dtype=np.uint8)
        _HEAD_CACHE[player] = (blank, blank)
        return _HEAD_CACHE[player]
    if img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    h, w   = img.shape[:2]
    scale  = size / max(h, w)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    full   = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    dnw    = max(1, int(nw * DISPLAY_SCALE))
    dnh    = max(1, int(nh * DISPLAY_SCALE))
    disp   = cv2.resize(img, (dnw, dnh), interpolation=cv2.INTER_AREA)
    _HEAD_CACHE[player] = (full, disp)
    return _HEAD_CACHE[player]


def _draw_x(canvas: np.ndarray, cx: int, cy: int, r: int, colour: tuple):
    """Draw a bold X at (cx, cy)."""
    arm   = max(3, r)
    thick = max(2, r // 2)
    shadow = tuple(max(0, c - 60) for c in colour)
    for (x0, y0), (x1, y1) in [
        ((cx - arm, cy - arm), (cx + arm, cy + arm)),
        ((cx + arm, cy - arm), (cx - arm, cy + arm)),
    ]:
        cv2.line(canvas, (x0, y0), (x1, y1), shadow, thick + 2, cv2.LINE_AA)
        cv2.line(canvas, (x0, y0), (x1, y1), colour, thick,     cv2.LINE_AA)


class MinimapPlayer:
    """
    Loads and plays back minimap replays from the DB.

    Two modes:
      "others"  - load friend PBs from DB (player != 'me')
      "history" - load my own history + PB from DB
    """

    HEAD_SIZE     = 40
    HEAD_ALPHA    = 1.0
    DOT_RADIUS    = 5
    BUBBLE_PAD    = 3
    BUBBLE_MARGIN = 4
    BUBBLE_RADIUS = 16
    BUBBLE_DIST   = 80

    COLOURS = [
        (120, 120, 255), (100, 220, 100), (255, 160, 120), (80,  230, 230),
        (220, 100, 220), (80,  180, 255), (220, 210, 100), (200, 120, 255),
    ]

    HISTORY_COLOUR = (120, 180, 255)
    PB_COLOUR      = (80, 220, 80)

    def __init__(self):
        self._replays:    list  = []
        self._race_start: float = 0.0
        self._active:     bool  = False
        self._mode:       str   = "others"

    def load(self, course: str, mode: str = "others") -> bool:
        """Load replays for *course* from DB. Returns True if any found."""
        self._replays = []
        self._mode    = mode

        if mode == "history":
            self._load_history(course)
        else:
            self._load_others(course)

        return bool(self._replays)

    def _load_history(self, course: str):
        hist_rows = get_history(course, limit=100)
        for i, row in enumerate(hist_rows):
            pts = [(p[0], p[1], p[2], p[3]) for p in row.get("points", [])]
            if not pts:
                continue
            alpha = max(0.0, 1.0 - i * 0.01)
            self._replays.append({
                "label":      "me",
                "points":     pts,
                "colour":     self.HISTORY_COLOUR,
                "alpha":      alpha,
                "is_pb":      False,
                "total_time": row.get("total_time"),
            })
        if self._replays:
            print(f"  [Replay] Loaded {len(self._replays)} history run(s) for '{course}'")

        pb_row = get_pb(course)
        if pb_row and pb_row.get("points"):
            pts = [(p[0], p[1], p[2], p[3]) for p in pb_row["points"]]
            pb_time = pb_row.get("total_time", "?")
            self._replays.append({
                "label":  f"PB {pb_time}",
                "points": pts,
                "colour": self.PB_COLOUR,
                "alpha":  1.0,
                "is_pb":  True,
            })
            print(f"  [Replay] Loaded PB ({pb_time}, {len(pts)} pts)")

    def _load_others(self, course: str):
        friends = get_friends_pbs(course)
        for i, row in enumerate(friends):
            pts = [(p[0], p[1], p[2], p[3]) for p in row.get("points", [])]
            if not pts:
                continue
            player = row.get("player", "unknown")
            self._replays.append({
                "label":    player,
                "points":   pts,
                "colour":   self.COLOURS[len(self._replays) % len(self.COLOURS)],
                "alpha":    1.0,
                "is_pb":    True,
            })
            _load_head_image(player, self.HEAD_SIZE)
            print(f"  [Replay] Loaded '{player}' ({len(pts)} pts)")
        for i, r in enumerate(self._replays):
            r["colour"] = self.COLOURS[i % len(self.COLOURS)]

    def start(self, offset_ms: int = 0):
        self._race_start = time.perf_counter() - offset_ms / 1000.0
        self._active     = bool(self._replays)

    def stop(self):
        self._active = False

    @staticmethod
    def _interpolate(points: list, t_ms: int) -> Optional[tuple]:
        if not points:
            return None
        if t_ms <= points[0][0]:
            return (float(points[0][1]), float(points[0][2]))
        if t_ms >= points[-1][0]:
            return (float(points[-1][1]), float(points[-1][2]))
        lo, hi = 0, len(points) - 1
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if points[mid][0] <= t_ms:
                lo = mid
            else:
                hi = mid
        t0, x0, y0 = points[lo][0], points[lo][1], points[lo][2]
        t1, x1, y1 = points[hi][0], points[hi][1], points[hi][2]
        if t1 == t0:
            return (x0, y0)
        frac = (t_ms - t0) / (t1 - t0)
        return (x0 + frac * (x1 - x0), y0 + frac * (y1 - y0))

    @staticmethod
    def _overlay_image(canvas: np.ndarray, img_bgra: np.ndarray,
                       cx: int, cy: int, alpha: float):
        ih, iw = img_bgra.shape[:2]
        x1 = cx - iw // 2;  y1 = cy - ih // 2
        x2 = x1 + iw;       y2 = y1 + ih
        ch, cw = canvas.shape[:2]
        ix1 = max(0, -x1);  iy1 = max(0, -y1)
        ix2 = iw - max(0, x2 - cw);  iy2 = ih - max(0, y2 - ch)
        cx1 = max(0, x1);  cy1 = max(0, y1)
        cx2 = min(cw, x2); cy2 = min(ch, y2)
        if cx2 <= cx1 or cy2 <= cy1:
            return
        src = img_bgra[iy1:iy2, ix1:ix2]
        dst = canvas[cy1:cy2, cx1:cx2]
        a   = src[:, :, 3:4].astype(np.float32) / 255.0 * alpha
        canvas[cy1:cy2, cx1:cx2] = (
            src[:, :, :3] * a + dst * (1.0 - a)
        ).astype(np.uint8)

    def draw(self, frame: Optional[np.ndarray], display: Optional[np.ndarray],
             screen: Screen):
        if not self._active or screen != Screen.RACING:
            return
        t_ms = int((time.perf_counter() - self._race_start) * 1000)
        dot_pts = [self._interpolate(r["points"], t_ms) for r in self._replays]
        if display is None:
            return
        canvas = display
        scale  = DISPLAY_SCALE
        ch, cw = canvas.shape[:2]
        dot_r  = max(2, int(self.DOT_RADIUS * scale))

        if self._mode == "history":
            self._draw_history(canvas, dot_pts, dot_r, scale, t_ms)
        else:
            from .tracker import MINIMAP_ROI
            self._draw_others(canvas, dot_pts, dot_r, scale, cw, ch, t_ms, MINIMAP_ROI)

    def _draw_history(self, canvas, dot_pts, dot_r, scale, t_ms):
        ch, cw = canvas.shape[:2]
        for i, replay in enumerate(self._replays):
            alpha  = replay["alpha"]
            colour = replay["colour"]
            r      = dot_r + (2 if replay.get("is_pb") else 0)
            pts    = replay["points"]
            at_end   = bool(pts) and t_ms >= pts[-1][0]
            abandoned = replay.get("total_time") is None and not replay.get("is_pb")
            show_x   = at_end and abandoned
            pos = dot_pts[i]
            if pos is None:
                continue
            ddx = int(round(pos[0] * scale))
            ddy = int(round(pos[1] * scale))
            if not (0 <= ddx < cw and 0 <= ddy < ch):
                continue
            pad = r + 4
            x1 = max(0, ddx - pad);  y1 = max(0, ddy - pad)
            x2 = min(cw, ddx + pad); y2 = min(ch, ddy + pad)
            if x2 <= x1 or y2 <= y1:
                continue
            if alpha >= 1.0:
                if show_x:
                    _draw_x(canvas, ddx, ddy, r, colour)
                else:
                    shadow = tuple(max(0, c - 60) for c in colour)
                    cv2.circle(canvas, (ddx, ddy), r + 1, shadow, -1, cv2.LINE_AA)
                    cv2.circle(canvas, (ddx, ddy), r,     colour,  -1, cv2.LINE_AA)
            else:
                patch   = canvas[y1:y2, x1:x2]
                overlay = patch.copy()
                lx = ddx - x1;  ly = ddy - y1
                if show_x:
                    _draw_x(overlay, lx, ly, r, colour)
                else:
                    shadow = tuple(max(0, c - 60) for c in colour)
                    cv2.circle(overlay, (lx, ly), r + 1, shadow, -1, cv2.LINE_AA)
                    cv2.circle(overlay, (lx, ly), r,     colour,  -1, cv2.LINE_AA)
                cv2.addWeighted(overlay, alpha, patch, 1.0 - alpha, 0, patch)
                canvas[y1:y2, x1:x2] = patch

    def _draw_others(self, canvas, dot_pts, dot_r, scale, cw, ch, t_ms, minimap_roi):
        n       = len(self._replays)
        head_s  = max(1, int(self.HEAD_SIZE * scale))
        bub_r   = head_s // 2 + max(1, int(self.BUBBLE_PAD * scale))
        mm_x2   = int(round((minimap_roi[0] + minimap_roi[2]) * scale))
        mm_y2   = int(round((minimap_roi[1] + minimap_roi[3]) * scale))
        slot_w  = bub_r * 2 + 4
        total_w = n * slot_w
        start_x = max(bub_r, mm_x2 - total_w)
        anchor_y = mm_y2 - bub_r - 2

        for i, replay in enumerate(self._replays):
            colour = replay["colour"]
            bx = start_x + i * slot_w + bub_r
            by = anchor_y
            bx = max(bub_r, min(cw - bub_r, bx))
            by = max(bub_r, min(ch - bub_r, by))
            if dot_pts[i] is not None:
                ddx = int(round(dot_pts[i][0] * scale))
                ddy = int(round(dot_pts[i][1] * scale))
                cv2.circle(canvas, (ddx, ddy), dot_r + 1,
                           tuple(max(0, c - 60) for c in colour), -1, cv2.LINE_AA)
                cv2.circle(canvas, (ddx, ddy), dot_r, colour, -1, cv2.LINE_AA)
            ring_col = tuple(max(0, c - 50) for c in colour)
            cv2.circle(canvas, (bx, by), bub_r + 1, ring_col, -1, cv2.LINE_AA)
            cv2.circle(canvas, (bx, by), bub_r,     colour,   -1, cv2.LINE_AA)
            _, disp_img = _load_head_image(replay["label"], head_s)
            ih, iw = disp_img.shape[:2]
            px1 = max(0, bx - iw // 2);  py1 = max(0, by - ih // 2)
            px2 = min(cw, px1 + iw);     py2 = min(ch, py1 + ih)
            if px2 > px1 and py2 > py1:
                patch = canvas[py1:py2, px1:px2].copy()
                pcx   = bx - px1;  pcy = by - py1
                tmp   = patch.copy()
                self._overlay_image(tmp, disp_img, pcx, pcy, self.HEAD_ALPHA)
                pmask = np.zeros((py2 - py1, px2 - px1), dtype=np.uint8)
                cv2.circle(pmask, (pcx, pcy), bub_r - 1, 255, -1)
                patch[pmask == 255] = tmp[pmask == 255]
                canvas[py1:py2, px1:px2] = patch
