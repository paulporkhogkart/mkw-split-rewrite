"""Dark-loading (RESET) tell must reject dark-but-not-loading screens.

The loading screen's invariant signature across capture cards: the WHOLE frame
is near-uniformly dark (the confetti pattern may be crushed to pure black) and
the bottom-right icon box shows a bright, COLOURFUL item (mushroom, fire
flower, ...). Dark Switch system screens are grayscale in that corner (gray
logos / button glyphs, saturation < 10), and they carry bright content
elsewhere (game thumbnails, the Nintendo logo) that a real loading screen
never has.

Real-capture fixtures (tests/fixtures/):
  reset_dev.png             true loading screen, dev card (confetti ~20, icon = boxed mushroom)
  reset_crushed_boxed.png   true loading screen, black-crushing card (ROI mean ~0.9, boxed fire flower)
  reset_crushed_open.png    same load, box-open animation state (flower only)
  falsereset_user_select.png  Switch "Who is using the software?" screen (was falsely detected)
  falsereset_switch_boot.png  Switch 2 boot logo screen (was falsely detected)
  racing_dark_section.png     dark race section, crushed card: flat bottom-left + white
                              minimap lines inside the icon ROI (cold-start false positive)
"""
import os

import cv2
import numpy as np
import pytest

from mkw_tracker.detection.screen import (
    Screen, TELLS, Region, detect_tell, score_region,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
RESET_TELL = next(t for t in TELLS if t.screen == Screen.RESET)


def _img(name):
    img = cv2.imread(os.path.join(FIXTURES, name), cv2.IMREAD_COLOR)
    assert img is not None, f"missing fixture {name}"
    assert img.shape[:2] == (1080, 1920)
    return img


@pytest.mark.parametrize("name", [
    "reset_dev.png",
    "reset_crushed_boxed.png",
    "reset_crushed_open.png",
])
def test_reset_tell_accepts_real_loading_screens(name):
    detected, score = detect_tell(_img(name), RESET_TELL)
    assert detected, f"{name} should detect as RESET (score={score})"


@pytest.mark.parametrize("name", [
    "falsereset_user_select.png",
    "falsereset_switch_boot.png",
    "racing_dark_section.png",
])
def test_reset_tell_rejects_dark_non_loading_screens(name):
    detected, score = detect_tell(_img(name), RESET_TELL)
    assert not detected, f"{name} must NOT detect as RESET (score={score})"


def test_dark_loading_rejects_bright_but_achromatic_icon():
    """A gray/white blob in the icon box (Switch UI glyphs, minimap lines) is
    not a loading icon: bright pixels must also be saturated."""
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[930:1020, 1720:1850] = 220                  # white blob: bright, S=0
    r = Region(kind="dark_loading", roi=(0, 589, 527, 1080),
               icon_roi=(1700, 920, 1870, 1030))
    assert score_region(frame, r, 0.9) == 0.0


def test_dark_loading_accepts_colourful_icon():
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[930:1020, 1720:1850] = (60, 200, 250)       # orange blob (BGR): bright + saturated
    r = Region(kind="dark_loading", roi=(0, 589, 527, 1080),
               icon_roi=(1700, 920, 1870, 1030))
    assert score_region(frame, r, 0.9) == 1.0


def test_reset_tell_rejects_bright_top_content():
    """A plausible icon is not enough: the rest of the frame must be dark too
    (the boot screen carries the Nintendo logo top-left, the user-select
    screen a row of game thumbnails)."""
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[930:1020, 1720:1850] = (60, 200, 250)       # plausible colourful icon
    frame[40:200, 60:380] = 230                       # bright logo, top-left
    detected, _ = detect_tell(frame, RESET_TELL)
    assert not detected


def test_reset_family_tells_stay_identical():
    fam = {t.screen: t for t in TELLS
           if t.screen in (Screen.RESET, Screen.GHOST_RESET, Screen.UNKNOWN_RESET)}
    shape = lambda t: [[(r.kind, r.roi, r.icon_roi) for r in g] for g in t.groups]
    base = shape(fam[Screen.RESET])
    for s in (Screen.GHOST_RESET, Screen.UNKNOWN_RESET):
        assert shape(fam[s]) == base
