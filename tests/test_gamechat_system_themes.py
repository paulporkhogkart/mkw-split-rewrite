"""GAMECHAT screen + alternate (white) theme OR-regions for the system overlays.

The Switch system overlays (HOME, GALLERY, GameChat) ship in two colour themes; a
grayscale template cut from the dark theme can't match the inverted white theme
(TM_CCOEFF_NORMED goes negative), so each tell carries an extra OR-region cut from
the white capture. GAMECHAT is a new universal overlay (the "C" GameChat logo).
"""
import copy
import os

import cv2

from mkw_tracker.detection.screen import Screen, detect_tell, TELLS

SHOTS = os.path.join(os.path.dirname(__file__), "..", "screenshots", "en_uk")


def _tell(screen):
    t = copy.deepcopy(next(t for t in TELLS if t.screen == screen))
    t.load("en_uk")
    return t


def _load(name):
    img = cv2.imread(os.path.join(SHOTS, name), cv2.IMREAD_COLOR)
    assert img is not None, f"missing screenshot: {name}"
    return img


def test_gamechat_screen_exists():
    assert Screen.GAMECHAT


def test_gamechat_dark_and_white_detect():
    detected, score = detect_tell(_load("gamechat.png"), _tell(Screen.GAMECHAT))
    assert detected, f"GAMECHAT dark theme not detected (score={score:.3f})"
    detected, score = detect_tell(_load("gamechat-white.png"), _tell(Screen.GAMECHAT))
    assert detected, f"GAMECHAT white theme not detected (score={score:.3f})"


def test_home_detects_both_themes():
    # dark theme (existing capture) must still match - no regression
    assert detect_tell(_load("home.png"), _tell(Screen.HOME))[0]
    assert detect_tell(_load("home-white.png"), _tell(Screen.HOME))[0]


def test_gallery_detects_both_themes():
    assert detect_tell(_load("gallery.png"), _tell(Screen.GALLERY))[0]
    assert detect_tell(_load("gallery-white.png"), _tell(Screen.GALLERY))[0]


def test_gamechat_tell_rejects_racing_and_black():
    import numpy as np
    assert not detect_tell(_load("racing_coin.png"), _tell(Screen.GAMECHAT))[0]
    assert not detect_tell(np.zeros((1080, 1920, 3), np.uint8), _tell(Screen.GAMECHAT))[0]
