import json

from mkw_tracker.detection.selection import SelectionTracker
from mkw_tracker.ipc.protocol import emit_option_lists


def test_option_lists_returns_sorted_unique_keys():
    # __new__ skips __init__ so the test never depends on which template PNGs exist.
    t = SelectionTracker.__new__(SelectionTracker)
    t._char_templates    = {"Mario": [], "Luigi": []}
    t._kart_templates    = {"Pipe Frame": []}
    t._course_templates  = {"Rainbow Road": [], "Mario Circuit": []}
    t._costume_templates = {"Aero": []}
    assert t.option_lists() == {
        "characters": ["Luigi", "Mario"],
        "karts":      ["Pipe Frame"],
        "courses":    ["Mario Circuit", "Rainbow Road"],
        "costumes":   ["Aero"],
    }


def test_emit_option_lists_shape():
    msg = json.loads(emit_option_lists(
        characters=["Mario"], karts=["K"], courses=["RR"], costumes=["Base"]))
    assert msg["type"] == "option_lists"
    assert msg["characters"] == ["Mario"]
    assert msg["karts"] == ["K"]
    assert msg["courses"] == ["RR"]
    assert msg["costumes"] == ["Base"]
