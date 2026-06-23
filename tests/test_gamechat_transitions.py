"""GAMECHAT transition behaviour: a universal overlay (appears over any screen) that
returns only to the screen it interrupted + that screen's direct joiners, with
HOME<->GAMECHAT see-through so neither overlay is ever a return target for the other.
"""
import os

import cv2

from mkw_tracker.detection.screen import Screen, ScreenDetector

SHOTS = os.path.join(os.path.dirname(__file__), "..", "screenshots", "en_uk")


def _shot(name):
    img = cv2.imread(os.path.join(SHOTS, name), cv2.IMREAD_COLOR)
    assert img is not None, name
    return img


def test_gamechat_is_a_universal_candidate():
    d = ScreenDetector(switch2_language="en_uk")
    d.force_screen(Screen.COURSE_SELECT)
    assert Screen.GAMECHAT in d._candidate_screens()
    d.force_screen(Screen.CHARACTER_SELECT)
    assert Screen.GAMECHAT in d._candidate_screens()


def test_gamechat_returns_to_interrupted_screen_and_its_joiners():
    d = ScreenDetector(switch2_language="en_uk")
    d.force_screen(Screen.RACING)
    d.force_screen(Screen.GAMECHAT)
    assert d._pre_overlay_screen == Screen.RACING
    cands = d._candidate_screens()
    assert Screen.RACING in cands                 # the interrupted screen
    assert Screen.RACE_MENU in cands              # a direct joiner of RACING
    assert Screen.HOME in cands                   # gamechat can pop to home


def test_home_on_top_of_gamechat_sees_through_to_real_screen():
    # RACING -> GAMECHAT -> HOME : HOME must point at RACING, never GAMECHAT.
    d = ScreenDetector(switch2_language="en_uk")
    d.force_screen(Screen.RACING)
    d.force_screen(Screen.GAMECHAT)
    d.force_screen(Screen.HOME)
    assert d._pre_overlay_screen == Screen.RACING
    cands = d._candidate_screens()
    assert Screen.RACING in cands and Screen.RACE_MENU in cands
    assert Screen.GAMECHAT not in (d._pre_overlay_screen,)   # never the return target


def test_gamechat_on_top_of_home_inherits_pre_home():
    # RACING -> HOME -> GAMECHAT : GAMECHAT inherits RACING through HOME.
    d = ScreenDetector(switch2_language="en_uk")
    d.force_screen(Screen.RACING)
    d.force_screen(Screen.HOME)
    d.force_screen(Screen.GAMECHAT)
    assert d._pre_overlay_screen == Screen.RACING


def test_stacked_overlays_stay_resolved_to_real_screen():
    # RACING -> GAMECHAT -> HOME -> GAMECHAT -> HOME : always RACING underneath.
    d = ScreenDetector(switch2_language="en_uk")
    d.force_screen(Screen.RACING)
    for s in (Screen.GAMECHAT, Screen.HOME, Screen.GAMECHAT, Screen.HOME):
        d.force_screen(s)
    assert d._pre_overlay_screen == Screen.RACING
    d.force_screen(Screen.GAMECHAT)
    assert d._pre_overlay_screen == Screen.RACING


def test_detection_drives_racing_gamechat_racing():
    d = ScreenDetector(switch2_language="en_uk")
    d.force_screen(Screen.RACING)
    assert d.update(_shot("gamechat.png"))[0] == Screen.GAMECHAT
    assert d.update(_shot("racing_coin.png"))[0] == Screen.RACING


def test_gamechat_wins_over_a_still_confirming_underlying_screen():
    # GameChat overlays without obscuring, so the underlying RACING tell still
    # confirms (its HUD ROIs are untouched). GAMECHAT must take priority anyway.
    d = ScreenDetector(switch2_language="en_uk")
    d.force_screen(Screen.RACING)
    composite = _shot("racing_coin.png").copy()
    gc = _shot("gamechat.png")
    x1, y1, x2, y2 = 120, 504, 168, 552
    composite[y1 - 12:y2 + 12, x1 - 12:x2 + 12] = gc[y1 - 12:y2 + 12, x1 - 12:x2 + 12]
    # sanity: the RACING tell still confirms on this composite
    from mkw_tracker.detection.screen import detect_tell
    import copy as _c
    racing_tell = _c.deepcopy(next(t for t in __import__(
        "mkw_tracker.detection.screen", fromlist=["TELLS"]).TELLS if t.screen == Screen.RACING))
    racing_tell.load("en_uk")
    assert detect_tell(composite, racing_tell)[0], "precondition: RACING still matches"
    # priority: gamechat wins
    assert d.update(composite)[0] == Screen.GAMECHAT


# ── GALLERY_VIEW: a universal overlay that OBSCURES the screen (so the normal confirm-miss
#    scan finds it, no per-frame priority) and joins the unified overlay see-through chain ──

def test_gallery_view_is_a_universal_candidate():
    d = ScreenDetector(switch2_language="en_uk")
    d.force_screen(Screen.RACING)
    assert Screen.GALLERY_VIEW in d._candidate_screens()
    d.force_screen(Screen.COURSE_SELECT)
    assert Screen.GALLERY_VIEW in d._candidate_screens()


def test_gallery_view_returns_to_interrupted_screen_and_its_joiners():
    d = ScreenDetector(switch2_language="en_uk")
    d.force_screen(Screen.RACING)
    d.force_screen(Screen.GALLERY_VIEW)
    assert d._pre_overlay_screen == Screen.RACING
    cands = d._candidate_screens()
    assert Screen.RACING in cands                 # the interrupted screen
    assert Screen.RACE_MENU in cands              # a direct joiner of RACING
    assert Screen.GALLERY in cands and Screen.HOME in cands   # its own targets


def test_gallery_view_chain_sees_through_to_real_screen():
    # The user's chain: RACING -> GALLERY_VIEW -> GALLERY -> HOME must leave the overlay
    # stack pointing at RACING, so HOME returns to the pre-GALLERY_VIEW screen.
    d = ScreenDetector(switch2_language="en_uk")
    d.force_screen(Screen.RACING)
    for s in (Screen.GALLERY_VIEW, Screen.GALLERY, Screen.HOME):
        d.force_screen(s)
    assert d._pre_overlay_screen == Screen.RACING
    cands = d._candidate_screens()
    assert Screen.RACING in cands and Screen.RACE_MENU in cands


def test_leaving_the_overlay_stack_clears_the_pre_screen():
    d = ScreenDetector(switch2_language="en_uk")
    d.force_screen(Screen.RACING)
    d.force_screen(Screen.GALLERY_VIEW)
    d.force_screen(Screen.RACING)                 # back to a real screen
    assert d._pre_overlay_screen is None


def test_detection_drives_racing_gallery_view_racing():
    # GALLERY_VIEW obscures RACING (the footer covers the HUD), so RACING stops confirming
    # and the confirm-miss scan lands on GALLERY_VIEW; back to the live HUD returns RACING.
    d = ScreenDetector(switch2_language="en_uk")
    d.force_screen(Screen.RACING)
    assert d.update(_shot("galleryview.png"))[0] == Screen.GALLERY_VIEW
    assert d.update(_shot("racing_coin.png"))[0] == Screen.RACING
