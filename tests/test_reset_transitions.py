"""Reset-family transition + thumbnail invariants.

The three reset screens (RESET / GHOST_RESET / UNKNOWN_RESET) share one identical
`dark_loading` tell, so they can't be told apart by pixels — only by race-mode
context. Two invariants protect that:

  * From an *unknown* context only the ambiguous UNKNOWN_RESET is reachable, so a
    dark frame can't make all three "detect" at once (and a cold HOME, which folds
    in TRANSITIONS[UNKNOWN], can't jump to a confident reset). UNKNOWN_RESET
    self-resolves to the right subtype once the next screen is known.
  * The confident subtypes are only reachable from their real contexts.
"""
from mkw_tracker.detection.screen import (
    Screen, TRANSITIONS, GRAPH_NODE_SHOTS, ScreenDetector,
)


def test_unknown_only_reaches_ambiguous_reset():
    unk = TRANSITIONS[Screen.UNKNOWN]
    assert Screen.UNKNOWN_RESET in unk
    assert Screen.RESET not in unk
    assert Screen.GHOST_RESET not in unk


def test_confident_resets_only_from_their_real_contexts():
    assert Screen.RESET in TRANSITIONS[Screen.RACE_MENU]
    assert Screen.GHOST_RESET in TRANSITIONS[Screen.REPLAY_MENU]


def test_unknown_reset_resolves_to_subtype():
    res = ScreenDetector._RESET_TYPE_RESOLUTION
    assert res[Screen.RACING] == Screen.RESET
    assert res[Screen.GHOST] == Screen.GHOST_RESET


def test_graph_shots_cover_reset_family():
    for s in (Screen.RESET, Screen.GHOST_RESET, Screen.UNKNOWN_RESET):
        assert GRAPH_NODE_SHOTS[s] == "reset.png"
