"""Course-select preview art as a tie-breaker for the course name plate.

The SNES siblings' name plates differ by one digit (cross 0.82 on edges); their
preview art differs completely (cross <= 0.54). The preview is consulted only when
the name read is confident AND ambiguous, and only the ambiguous candidates are scored.
"""
import os

import cv2
import numpy as np
import pytest

from mkw_tracker.detection.selection import resolve_with_preview

_LANG = "en_uk"
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CAPTURES = os.path.join(_ROOT, "captures", _LANG, "courses")
_PREVIEWS = os.path.join(_ROOT, "images", "course_previews", _LANG)
_HAVE = os.path.isdir(_CAPTURES) and os.path.isdir(_PREVIEWS) and len(os.listdir(_PREVIEWS)) >= 40


# ---------------------------------------------------------------------------
# Pure rule
# ---------------------------------------------------------------------------

def test_no_tie_break_when_the_name_read_is_unambiguous():
    assert resolve_with_preview({"A": 0.99, "B": 0.55}, {"A": 0.2, "B": 0.9}) is None
    assert resolve_with_preview({"A": 0.99}, {"A": 0.9}) is None
    assert resolve_with_preview({}, {"A": 0.9}) is None
    assert resolve_with_preview({"A": 0.99, "B": 0.9}, {}) is None


def test_near_tie_is_broken_by_the_best_preview():
    names = {"Snes Choco Island 1": 0.82, "Snes Choco Island 2": 1.00, "Rainbow Road": 0.3}
    previews = {"Snes Choco Island 1": 0.98, "Snes Choco Island 2": 0.41}
    # the preview says Island 1 even though the plate ranked Island 2 first (a hold/misread)
    assert resolve_with_preview(names, previews) == "Snes Choco Island 1"
    # and the other way round
    assert resolve_with_preview(names, {"Snes Choco Island 1": 0.40, "Snes Choco Island 2": 0.97}) == "Snes Choco Island 2"


def test_weak_previews_never_override():
    names = {"A": 0.9, "B": 0.85}
    assert resolve_with_preview(names, {"A": 0.3, "B": 0.45}) is None      # both below the min
    assert resolve_with_preview(names, {"A": 0.3}) is None                 # B missing counts as 0


def test_only_candidates_within_the_margin_take_part():
    names = {"A": 0.9, "B": 0.7, "C": 0.5}         # C is 0.4 below the top -> not a candidate
    assert resolve_with_preview(names, {"A": 0.6, "B": 0.7, "C": 0.99}) == "B"


# ---------------------------------------------------------------------------
# Capture-backed (skips without the captures + generated previews)
# ---------------------------------------------------------------------------

def _frame(base):
    return cv2.imread(os.path.join(_CAPTURES, f"{base}.png"), cv2.IMREAD_COLOR)


@pytest.mark.skipif(not _HAVE, reason="captures/en_uk/courses + images/course_previews/en_uk needed")
def test_every_capture_wins_on_preview_art_with_a_wide_margin():
    from mkw_tracker.detection.selection import SelectionTracker
    t = SelectionTracker(switch2_language=_LANG)
    names = list(t._course_previews)
    assert len(names) == 40
    worst = 1.0
    for base in sorted(os.listdir(_CAPTURES)):
        if not base.endswith(".png"):
            continue
        scores = t._preview_scores(_frame(base[:-4]), names)
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        assert ranked[0][0].lower() == base[:-4].replace("_", " "), (base, ranked[:3])
        worst = min(worst, ranked[0][1] - ranked[1][1])
    assert worst >= 0.30, f"preview margin collapsed to {worst:.2f}"


@pytest.mark.skipif(not _HAVE, reason="captures/en_uk/courses + images/course_previews/en_uk needed")
@pytest.mark.parametrize("shown,held", [
    ("snes_choco_island_2", "Snes Choco Island 1"),
    ("snes_ghost_valley_3", "Snes Ghost Valley 1"),
    ("snes_mario_circuit_1", "Snes Mario Circuit 2"),
])
def test_preview_breaks_a_sibling_tie_toward_the_course_on_screen(shown, held):
    """Simulate the plate ranking the OLD sibling first (a sticky hold / misread): the
    preview of what is actually on screen must win."""
    from mkw_tracker.detection.selection import SelectionTracker
    t = SelectionTracker(switch2_language=_LANG)
    on_screen = shown.replace("_", " ").title()
    picked = t._preview_pick(_frame(shown), {held: 0.90, on_screen: 0.82, "Rainbow Road": 0.2})
    assert picked == on_screen
    assert set(t._course_preview_scores) == {held, on_screen}       # only the candidates were scored


@pytest.mark.skipif(not _HAVE, reason="captures/en_uk/courses + images/course_previews/en_uk needed")
def test_preview_is_not_consulted_on_an_unambiguous_or_weak_read():
    from mkw_tracker.detection.selection import SelectionTracker
    t = SelectionTracker(switch2_language=_LANG)
    f = _frame("snes_koopa_beach_1")
    assert t._preview_pick(f, {"Snes Koopa Beach 1": 0.99, "Rainbow Road": 0.3}) is None
    assert t._course_preview_scores == {}
    assert t._preview_pick(f, {"Snes Koopa Beach 1": 0.60, "Snes Choco Island 1": 0.58}) is None  # below the floor
    assert t._course_preview_scores == {}


@pytest.mark.skipif(not _HAVE, reason="captures/en_uk/courses + images/course_previews/en_uk needed")
def test_full_course_update_still_reads_every_capture_correctly():
    from mkw_tracker.detection.selection import SelectionTracker
    for base in sorted(os.listdir(_CAPTURES)):
        if not base.endswith(".png"):
            continue
        t = SelectionTracker(switch2_language=_LANG)
        t._update_course(_frame(base[:-4]))
        assert (t.state.course or "").lower() == base[:-4].replace("_", " "), base
