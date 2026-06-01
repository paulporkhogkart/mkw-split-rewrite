"""Tests for the capture verifier's pure classification logic.

The matching itself reuses tested primitives; this covers the decision that turns
a ranked [(name, score)] list + the file's label into OK / WEAK / MISS / NOLABEL /
NOTEMPL, including per-category weak thresholds and separator-insensitive labels.
"""


def test_classify_ok():
    from mkw_tracker.tools.verify_captures import _classify
    r = _classify("karts", "hot_rod", [("Hot Rod", 0.98), ("Pipe Frame", 0.5)])
    assert r["status"] == "OK"
    assert r["labeled"] == 0.98
    assert r["best"] == "Hot Rod"


def test_classify_weak_below_category_threshold():
    from mkw_tracker.tools.verify_captures import _classify
    r = _classify("characters", "mario", [("Mario", 0.66), ("Luigi", 0.40)])
    assert r["status"] == "WEAK"      # 0.66 < 0.70 char threshold, but still best


def test_classify_miss_when_other_template_wins():
    from mkw_tracker.tools.verify_captures import _classify
    r = _classify("characters", "toad", [("Toadette", 0.80), ("Toad", 0.50)])
    assert r["status"] == "MISS"
    assert r["best"] == "Toadette"


def test_classify_nolabel_when_file_matches_no_template():
    from mkw_tracker.tools.verify_captures import _classify
    r = _classify("karts", "mystery_kart", [("Hot Rod", 0.9)])
    assert r["status"] == "NOLABEL"


def test_classify_notempl_on_empty_ranked():
    from mkw_tracker.tools.verify_captures import _classify
    r = _classify("karts", "hot_rod", [])
    assert r["status"] == "NOTEMPL"


def test_classify_costume_threshold_is_lower():
    """Costumes match on edges and score lower, so 0.55 is OK for a costume."""
    from mkw_tracker.tools.verify_captures import _classify
    r = _classify("costumes", "pro_racer", [("Pro Racer", 0.55), ("Touring", 0.30)])
    assert r["status"] == "OK"


def test_classify_label_is_separator_insensitive():
    from mkw_tracker.tools.verify_captures import _classify
    r = _classify("costumes", "all_terrain", [("All-Terrain", 0.80), ("Aero", 0.40)])
    assert r["status"] == "OK"
    assert r["labeled"] == 0.80
