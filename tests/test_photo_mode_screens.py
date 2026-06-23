"""PHOTO_MODE + EXIT_PHOTO_MODE screen detection (photo-mode PB exploit fix, L3).

Photo mode pauses the in-game timer but was misclassified as RACING, so the final-lap
finish detector fired on the frozen screen and a partial time became a false PB. These
screens make photo mode a pause-like (non-RACING) state so all finish/timer logic bails.

  * PHOTO_MODE matches 4 ANDed controller-button glyphs (X / ZL / ZR / A) in the
    photo-mode HUD - language-independent, so the same template serves every language.
  * EXIT_PHOTO_MODE matches the "Stop taking photos?" dialog text (en_uk for now).

It must NOT fire on a plain dark / off-map fade (which carries no bright glyphs).
"""
import copy
import os

import cv2
import numpy as np

from mkw_tracker.detection.screen import Screen, ScreenDetector, detect_tell, TELLS

SHOTS = os.path.join(os.path.dirname(__file__), "..", "screenshots", "en_uk")


def _tell(screen):
    t = copy.deepcopy(next(t for t in TELLS if t.screen == screen))
    t.load("en_uk")
    return t


def _load(name):
    img = cv2.imread(os.path.join(SHOTS, name), cv2.IMREAD_COLOR)
    assert img is not None, f"missing screenshot: {name}"
    return img


def test_photo_mode_screens_exist():
    assert Screen.PHOTO_MODE and Screen.EXIT_PHOTO_MODE


def test_photomode_screenshot_matches_photo_mode_tell():
    detected, score = detect_tell(_load("photomode.png"), _tell(Screen.PHOTO_MODE))
    assert detected, f"PHOTO_MODE tell did not match its own screenshot (score={score:.3f})"


def test_exitphotomode_screenshot_matches_exit_tell():
    detected, score = detect_tell(_load("exitphotomode.png"), _tell(Screen.EXIT_PHOTO_MODE))
    assert detected, f"EXIT_PHOTO_MODE tell did not match its own screenshot (score={score:.3f})"


def test_photo_mode_tell_rejects_black_fade():
    """The off-map black fade (aiden ~4:00) carries no bright glyphs - must not match."""
    black = np.zeros((1080, 1920, 3), np.uint8)
    detected, _ = detect_tell(black, _tell(Screen.PHOTO_MODE))
    assert not detected


def test_photo_mode_tell_rejects_racing():
    detected, _ = detect_tell(_load("racing_coin.png"), _tell(Screen.PHOTO_MODE))
    assert not detected


def test_racing_transitions_to_photo_mode():
    d = ScreenDetector(switch2_language="en_uk")
    d.force_screen(Screen.RACING)
    scr, _ = d.update(_load("photomode.png"))
    assert scr == Screen.PHOTO_MODE


def test_photo_mode_transitions_to_exit_then_racing():
    d = ScreenDetector(switch2_language="en_uk")
    d.force_screen(Screen.PHOTO_MODE)
    scr, _ = d.update(_load("exitphotomode.png"))
    assert scr == Screen.EXIT_PHOTO_MODE
    scr, _ = d.update(_load("racing_coin.png"))
    assert scr == Screen.RACING
