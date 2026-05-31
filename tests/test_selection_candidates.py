"""Tests for top_candidates() helper and SelectionTracker score-map retention."""
import pytest


def test_top_candidates_sorted():
    from mkw_tracker.detection.selection import top_candidates
    scores = {"Mario": 0.95, "Luigi": 0.40, "Peach": 0.72}
    out = top_candidates(scores, n=3)
    assert [c["name"] for c in out] == ["Mario", "Peach", "Luigi"]
    assert out[0]["score"] == 0.95


def test_top_candidates_limit_and_empty():
    from mkw_tracker.detection.selection import top_candidates
    assert top_candidates({}, n=5) == []
    assert len(top_candidates({"a": .1, "b": .2, "c": .3}, n=2)) == 2


def test_top_candidates_score_rounded():
    """Scores should be rounded to 4 decimal places."""
    from mkw_tracker.detection.selection import top_candidates
    scores = {"A": 0.123456789}
    out = top_candidates(scores, n=1)
    assert out[0]["score"] == round(0.123456789, 4)


def test_top_candidates_n_larger_than_dict():
    """Asking for more than available entries returns all entries."""
    from mkw_tracker.detection.selection import top_candidates
    scores = {"X": 0.5, "Y": 0.8}
    out = top_candidates(scores, n=10)
    assert len(out) == 2
    assert out[0]["name"] == "Y"


def test_tracker_exposes_score_maps(tmp_path):
    """SelectionTracker.score_maps returns a dict with char/kart/course/costume keys.

    When no templates are loaded (empty dirs) all fields are empty lists.
    """
    from mkw_tracker.detection.selection import SelectionTracker
    tracker = SelectionTracker(
        char_dir=str(tmp_path / "chars"),
        costume_dir=str(tmp_path / "costumes"),
        kart_dir=str(tmp_path / "karts"),
        course_dir=str(tmp_path / "courses"),
    )
    maps = tracker.score_maps
    assert set(maps.keys()) == {"char", "kart", "course", "costume"}
    # With no templates loaded, all fields are empty lists
    for field_key in ("char", "kart", "course", "costume"):
        assert isinstance(maps[field_key], list)
        assert maps[field_key] == []
