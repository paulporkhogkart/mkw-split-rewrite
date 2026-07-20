"""Course-key normalization in the minimap detection-config lookups.

The live detector derives course names from template FILENAMES (`_` -> space +
.title(): sky_high_sundae.png -> "Sky High Sundae"), but seed/ROI rows written by
the _SEED_V2 migration carry canonical punctuation ("Sky-High Sundae"). The old
exact-match lookups missed, so live minimap tracking silently never seeded on
Sky-High Sundae: every SHS run - for every player, since trails shipped -
uploaded with laps but ZERO trail points (found 2026-07-20; the WR service only
worked because its Rust side deliberately sends the hyphenated name).

The getters now fall back to a separator/punctuation/case-insensitive match, so
BOTH spellings hit the same row and neither the live detector nor the WR
service's set_selection can miss it again.
"""
from mkw_tracker.database.replay_repo import (
    get_minimap_roi, get_minimap_seed, get_minimap_threshold,
    set_minimap_roi, set_minimap_seed, set_minimap_threshold,
)


def test_the_shs_incident_row_is_now_found_by_the_detectors_spelling(memdb):
    # memdb applies the REAL migrations, so this asserts against the actual
    # _SEED_V2 rows that caused the incident: 'Sky-High Sundae' seed + ROI.
    # The live detector asks with the filename-derived (space) name.
    assert get_minimap_seed("Sky High Sundae") == {
        "cx": 1753, "cy": 767, "radius": 20, "conf": None,
    }
    assert get_minimap_roi("Sky High Sundae") == {"x": 1610, "y": 467, "w": 220, "h": 520}
    # The WR service's hyphenated set_selection keeps working too.
    assert get_minimap_seed("Sky-High Sundae") is not None


def test_lookup_survives_dots_hyphens_apostrophes_and_case(memdb):
    # Fictional courses so the migration-seeded rows can't collide.
    set_minimap_seed("Testville-Alpha Circuit", 10, 11, 20)
    assert get_minimap_seed("Testville Alpha Circuit")["cx"] == 10
    set_minimap_roi("Dr. Test's Raceway", 1, 2, 3, 4)
    assert get_minimap_roi("Dr Tests Raceway") == {"x": 1, "y": 2, "w": 3, "h": 4}
    # Curly apostrophe (what mkwrs uses) vs straight.
    set_minimap_roi("Testy’s Galleon", 5, 6, 7, 8)
    assert get_minimap_roi("Testy's Galleon") == {"x": 5, "y": 6, "w": 7, "h": 8}


def test_threshold_lookup_normalizes_the_course_key(memdb):
    set_minimap_threshold("Sky-High Sundae", "Toadette", "Explorer", 0.7)
    assert get_minimap_threshold("Sky High Sundae", "Toadette", "Explorer") == 0.7
    # Character stays exact - only course keys have two writers with two spellings.
    assert get_minimap_threshold("Sky High Sundae", "Toad", "Explorer") is None


def test_exact_match_wins_over_a_normalized_neighbour(memdb):
    # If a live re-seed created a second spelling, the exact row is the fresher
    # intent and must win; the fallback only fires when exact finds nothing.
    set_minimap_seed("Sky-High Sundae", 1, 1, 20)
    set_minimap_seed("Sky High Sundae", 2, 2, 20)
    assert get_minimap_seed("Sky High Sundae")["cx"] == 2
    assert get_minimap_seed("Sky-High Sundae")["cx"] == 1


def test_a_genuinely_unknown_course_still_returns_none(memdb):
    assert get_minimap_seed("Nonexistent Speedway") is None
    assert get_minimap_roi("Nonexistent Speedway") is None
    assert get_minimap_threshold("Nonexistent Speedway", "Mario", None) is None
