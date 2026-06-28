"""Tests for the clip-sweep grid model (tools/autotemplate/grid.py)."""
import os
import pytest
import grid


YAML = os.path.join(os.path.dirname(__file__), "..",
                    "tools", "autotemplate", "scripts", "clip_sweep.yaml")


@pytest.fixture
def g():
    return grid.load_grid(YAML)


def test_to_filename():
    assert grid.to_filename("Mario") == "mario"
    assert grid.to_filename("Baby Mario") == "baby_mario"
    assert grid.to_filename("R.O.B. H.O.G.") == "rob_hog"


def test_counts(g):
    assert len(g.cells("characters")) == 153
    assert len(g.cells("karts")) == 40


def test_char_slug_includes_costume(g):
    slugs = {c.slug for c in g.cells("characters")}
    assert "mario__base" in slugs
    assert "mario__touring" in slugs


def test_first_cell_is_mario_base(g):
    first = g.cells("characters")[0]
    assert first.slug == "mario__base" and first.coord == (0, 0)


def test_sweep_steps_row_transition(g):
    steps = g.sweep_steps("karts")
    assert steps[0][0] == "standard_kart" and steps[0][1] == []
    # the 11th kart cell starts row 1 → preceded by RIGHT (onto blank) then DOWN
    row1_first = next(s for s in steps if s[0] == "rally_kart")
    assert row1_first[1] == ["DPAD_RIGHT", "DPAD_DOWN"]


def test_horizontal_recovery_delta(g):
    # overshot by 2 within a row → step LEFT twice back to target
    assert g.horizontal_delta("zoom_buggy", "standard_kart") == ["DPAD_LEFT", "DPAD_LEFT"]
    # undershoot the other way → RIGHT
    assert g.horizontal_delta("standard_kart", "plushbuggy") == ["DPAD_RIGHT"]
    # same cell → no presses
    assert g.horizontal_delta("standard_kart", "standard_kart") == []


def test_span_of(g):
    # characters: 3 rows × 51 cols; karts: 4 rows × 10. span_of keys off the slug's category and
    # bounds the nav step budget (must exceed (rows-1)+(width-1)).
    assert g.span_of("mario__base") == (3, 51)
    assert g.span_of("dolphin__base") == (3, 51)
    assert g.span_of("standard_kart") == (4, 10)


def test_duplicate_slug_raises():
    with pytest.raises(ValueError):
        grid.Grid({"karts": [["Standard Kart", "Standard Kart"]]})
