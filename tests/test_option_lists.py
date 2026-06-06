import json

from mkw_tracker.detection.selection import SelectionTracker, KNOWN_COSTUMES
from mkw_tracker.ipc.protocol import emit_option_lists


def test_option_lists_returns_sorted_unique_keys():
    # __new__ skips __init__ so the test never depends on which template PNGs exist.
    t = SelectionTracker.__new__(SelectionTracker)
    t._char_templates    = {"Mario": [], "Luigi": []}
    t._kart_templates    = {"Pipe Frame": []}
    t._course_templates  = {"Rainbow Road": [], "Mario Circuit": []}
    t._costume_templates = {"Aero": []}
    ol = t.option_lists()
    assert ol["characters"] == ["Luigi", "Mario"]
    assert ol["karts"]      == ["Pipe Frame"]
    assert ol["courses"]    == ["Mario Circuit", "Rainbow Road"]
    assert ol["costumes"]   == ["Aero"]


def test_option_lists_includes_costumes_by_character():
    t = SelectionTracker.__new__(SelectionTracker)
    # costumes_by_character is the engine's KNOWN_COSTUMES, independent of templates.
    t._char_templates = {}
    t._kart_templates = {}
    t._course_templates = {}
    t._costume_templates = {}
    cbc = t.option_lists()["costumes_by_character"]
    assert cbc["Mario"] == KNOWN_COSTUMES["Mario"]   # mirrors the detection map
    assert cbc["Wiggler"] == []                      # a character with no costumes
    assert "Base" not in cbc["Mario"]                # Base is the popup's implicit default


def test_emit_option_lists_shape():
    msg = json.loads(emit_option_lists(
        characters=["Mario"], karts=["K"], courses=["RR"], costumes=["Base"],
        costumes_by_character={"Mario": ["Touring", "Pro Racer"]}))
    assert msg["type"] == "option_lists"
    assert msg["characters"] == ["Mario"]
    assert msg["karts"] == ["K"]
    assert msg["courses"] == ["RR"]
    assert msg["costumes"] == ["Base"]
    assert msg["costumes_by_character"] == {"Mario": ["Touring", "Pro Racer"]}
