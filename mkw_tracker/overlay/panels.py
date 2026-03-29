"""Legend panel, state/perf panel, screen badge."""
import cv2
import numpy as np
from collections import deque
from typing import Optional

from ..detection.screen import Screen, PerfStats
from ..detection.selection import SelectionState
from ..race.laps import LapState
from ..race.coins import CoinState
from ..race.timestamp import TimestampState
from ..race.finish import FinishState
from ..race.mushrooms import MushroomState
from ..minimap.tracker import MinimapState, MINIMAP_ROI
from ..utils.image import DISPLAY_SCALE

FONT       = cv2.FONT_HERSHEY_SIMPLEX
FONT_LARGE = 0.8
FONT_SMALL = 0.48
LINE_H     = 18


def draw_screen_badge(display: np.ndarray, screen: Screen):
    """Draw the current screen name badge in the top-left corner."""
    state_colour = (0, 255, 0) if screen != Screen.UNKNOWN else (0, 100, 255)
    if screen == Screen.UNKNOWN_RACE_ACTIVE:
        state_colour = (0, 165, 255)
    badge_text = screen.name.replace('_', ' ')
    (bw, bh), _ = cv2.getTextSize(badge_text, FONT, FONT_LARGE, 2)
    bx, by = 16, 16
    badge_overlay = display.copy()
    cv2.rectangle(badge_overlay, (bx - 4, by - 4),
                  (bx + bw + 4, by + bh + 8), (0, 0, 0), -1)
    cv2.addWeighted(badge_overlay, 0.45, display, 0.55, 0, display)
    cv2.putText(display, badge_text, (bx, by + bh),
                FONT, FONT_LARGE, (0, 0, 0), 6, cv2.LINE_AA)
    cv2.putText(display, badge_text, (bx, by + bh),
                FONT, FONT_LARGE, state_colour, 2, cv2.LINE_AA)


def draw_legend(display: np.ndarray):
    """Draw the colour-coded legend in the bottom-left corner."""
    legend_items = [
        ((0, 255, 0),   "Active tell: matched"),
        ((0, 140, 255), "Active tell: not matched"),
        ((255, 255, 0), "Candidate next-screen"),
        ((255, 180, 60),  "Character ROI"),
        ((255, 120, 200), "Costume ROI"),
        ((60,  200, 255), "Kart ROI"),
        ((180, 255, 80),  "Course ROI"),
        ((255, 200, 60),  "Lap ROI"),
        ((60,  220, 255), "Coin ROI"),
        ((200, 100, 255), "Timestamp ROI"),
        ((80,  80,  255), "Finish ROI"),
        ((80,  255, 160), "Mushroom ROI"),
        ((0,   255, 255), "Minimap: ring + face confirmed"),
        ((0,   140, 255), "Minimap: ring only (hazard / ghost face)"),
        ((0,   220, 255), "Minimap: reacquiring"),
    ]
    h, w = display.shape[:2]
    x = 10
    y = h - len(legend_items) * (LINE_H + 2) - 10
    for colour, text in legend_items:
        cv2.rectangle(display, (x, y), (x + 12, y + 12), colour, -1)
        cv2.putText(display, text, (x + 16, y + 11),
                    FONT, FONT_SMALL, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(display, text, (x + 16, y + 11),
                    FONT, FONT_SMALL, (255, 255, 255), 1, cv2.LINE_AA)
        y += LINE_H + 2


def _template_to_bgr(tmpl: np.ndarray) -> np.ndarray:
    """Convert the stored HSV-CLAHE float32 template back to a uint8 BGR image."""
    h_ch = (tmpl[:, :, 0] * 179).astype(np.uint8)
    s_ch = (tmpl[:, :, 1] * 255).astype(np.uint8)
    v_ch = (tmpl[:, :, 2] * 255).astype(np.uint8)
    return cv2.cvtColor(np.stack([h_ch, s_ch, v_ch], axis=2), cv2.COLOR_HSV2BGR)


def draw_state_panel(
    display:    np.ndarray,
    screen:     Screen,
    perf:       PerfStats,
    selection:  SelectionState,
    laps:       LapState,
    coins:      CoinState,
    ts:         TimestampState,
    finish:     FinishState,
    mush:       MushroomState,
    mm:         MinimapState,
    avg_fps:    float,
    avg_ms:     float,
    avg_tells:  float,
    peak_ms:    float,
    transition_count: int,
    lap_splits:     Optional[dict]       = None,
    char_template:  Optional[np.ndarray] = None,
):
    """Draw a compact state panel on the right side of the display."""
    h, w = display.shape[:2]
    panel_x = w - 260
    y = 10
    items = [
        f"FPS: {avg_fps:.1f}  ms: {avg_ms:.1f} (pk {peak_ms:.1f})",
        f"Tells: {avg_tells:.1f}  transitions: {transition_count}",
        f"Screen: {screen.name}",
        f"Char: {selection.character or '-'} ({selection.character_conf:.2f})",
        f"Costume: {selection.costume or '-'} ({selection.costume_conf:.2f})",
        f"Kart: {selection.kart or '-'} ({selection.kart_conf:.2f})",
        f"Course: {selection.course or '-'} ({selection.course_conf:.2f})",
        f"Lap: {laps.current_lap or '-'}/{laps.total_laps or '-'}",
    ]
    if lap_splits:
        for lap_num in sorted(lap_splits.keys()):
            items.append(f"  L{lap_num}: {lap_splits[lap_num]}")
    items += [
        f"Coins: {coins.coins if coins.coins is not None else '-'}",
        f"Time: {ts.formatted() or '-'}",
        f"Finish: {finish.result or '-'}",
        f"Mush: {mush.count}",
        f"MM: {mm.track_state} ({mm.cx_smooth:.0f},{mm.cy_smooth:.0f}) s={mm.last_score:.3f}",
    ]
    for text in items:
        cv2.putText(display, text, (panel_x, y + 12),
                    FONT, FONT_SMALL, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(display, text, (panel_x, y + 12),
                    FONT, FONT_SMALL, (200, 200, 200), 1, cv2.LINE_AA)
        y += LINE_H

    # ── Face template thumbnail ───────────────────────────────────────────────
    if char_template is not None:
        bgr   = _template_to_bgr(char_template)
        th, tw = bgr.shape[:2]
        thumb = cv2.resize(bgr, (tw * 3, th * 3), interpolation=cv2.INTER_NEAREST)
        # Position: just left of the minimap ROI, bottom-aligned to it
        mm_left   = int(MINIMAP_ROI[0] * DISPLAY_SCALE)
        mm_bottom = int((MINIMAP_ROI[1] + MINIMAP_ROI[3]) * DISPLAY_SCALE)
        tx = mm_left - thumb.shape[1] - 4
        ty = mm_bottom - thumb.shape[0]
        # Clip to display bounds
        disp_h, disp_w = display.shape[:2]
        paste_h = min(thumb.shape[0], disp_h - ty)
        paste_w = min(thumb.shape[1], disp_w - tx)
        if paste_h > 0 and paste_w > 0:
            # Black border
            cv2.rectangle(display, (tx - 1, ty - 1),
                          (tx + paste_w, ty + paste_h), (0, 0, 0), 1)
            display[ty:ty + paste_h, tx:tx + paste_w] = thumb[:paste_h, :paste_w]
            cv2.putText(display, "seed", (tx, ty + paste_h + 10),
                        FONT, 0.35, (0, 0, 0), 2, cv2.LINE_AA)
            cv2.putText(display, "seed", (tx, ty + paste_h + 10),
                        FONT, 0.35, (160, 160, 160), 1, cv2.LINE_AA)
