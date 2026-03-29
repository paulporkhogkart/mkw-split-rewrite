"""HUD overlay for race telemetry: laps, coins, timestamp, finish, mushrooms."""
import numpy as np
from typing import Optional

from ..detection.screen import Screen
from ..race.laps import LapState, LAP_CURRENT_ROI, LAP_TOTAL_ROI
from ..race.coins import CoinState, COIN_LEFT_ROI, COIN_RIGHT_ROI
from ..race.timestamp import TimestampState, TIMESTAMP_ROIS
from ..race.finish import FinishState, FINISH_ROI
from ..race.mushrooms import MushroomState, MUSHROOM_ROI
from ..utils.image import draw_roi_box, draw_roi_label

_LAP_COLOUR    = (255, 200, 60)
_COIN_COLOUR   = (60, 220, 255)
_TS_COLOUR     = (200, 100, 255)
_FINISH_COLOUR = (80, 80, 255)
_MUSH_COLOUR   = (80, 255, 160)


def draw_lap_rois(
    frame:   Optional[np.ndarray],
    display: Optional[np.ndarray],
    screen:  Screen,
    laps:    LapState,
):
    if screen != Screen.RACING:
        return
    items = [
        (LAP_CURRENT_ROI, f"lap {laps.current_lap or '?'} ({laps.current_lap_conf:.2f})"),
        (LAP_TOTAL_ROI,   f"/{laps.total_laps or '?'} ({laps.total_laps_conf:.2f})"),
    ]
    for roi, label in items:
        if frame is not None:
            draw_roi_box(frame, roi, _LAP_COLOUR, thickness=2)
        if display is not None:
            draw_roi_label(display, roi, _LAP_COLOUR, label)


def draw_coin_rois(
    frame:   Optional[np.ndarray],
    display: Optional[np.ndarray],
    screen:  Screen,
    coins:   CoinState,
):
    if screen != Screen.RACING:
        return
    label = f"coins {coins.coins if coins.coins is not None else '?'}"
    for roi in (COIN_LEFT_ROI, COIN_RIGHT_ROI):
        if frame is not None:
            draw_roi_box(frame, roi, _COIN_COLOUR, thickness=2)
        if display is not None:
            draw_roi_label(display, roi, _COIN_COLOUR, label)


def draw_timestamp_rois(
    frame:   Optional[np.ndarray],
    display: Optional[np.ndarray],
    screen:  Screen,
    ts:      TimestampState,
):
    if screen != Screen.RACING:
        return
    label = f"time {ts.formatted() or '?'}"
    for roi in TIMESTAMP_ROIS.values():
        if frame is not None:
            draw_roi_box(frame, roi, _TS_COLOUR, thickness=2)
        if display is not None:
            draw_roi_label(display, roi, _TS_COLOUR, label)


def draw_finish_roi(
    frame:   Optional[np.ndarray],
    display: Optional[np.ndarray],
    screen:  Screen,
    finish:  FinishState,
):
    if screen != Screen.RACING:
        return
    label = (f"{finish.result} ({finish.conf:.2f})"
             if finish.detected else f"finish? ({finish.conf:.2f})")
    if frame is not None:
        draw_roi_box(frame, FINISH_ROI, _FINISH_COLOUR, thickness=2)
    if display is not None:
        draw_roi_label(display, FINISH_ROI, _FINISH_COLOUR, label)


def draw_mushroom_roi(
    frame:   Optional[np.ndarray],
    display: Optional[np.ndarray],
    screen:  Screen,
    mush:    MushroomState,
):
    if screen != Screen.RACING:
        return
    label = f"mush {mush.count}" + (f" ({mush.conf:.2f})" if mush.count > 0 else "")
    if frame is not None:
        draw_roi_box(frame, MUSHROOM_ROI, _MUSH_COLOUR, thickness=2)
    if display is not None:
        draw_roi_label(display, MUSHROOM_ROI, _MUSH_COLOUR, label)
