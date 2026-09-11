# tests/test_server_courses.py
"""Tests for canonical courses, slugify, and legacy track mapping."""
from server.db import connect, init_schema
from server.courses import (
    slugify, CANONICAL_COURSES, LEGACY_ALIASES, legacy_track_slug, seed_courses,
)

# The exact 30 legacy track names (from hogkart.db `tracks`).
LEGACY_TRACK_NAMES = [
    "Mario Bros. Circuit", "Crown City", "Whistlestop Summit", "DK Spaceport",
    "Desert Hills", "Shy Guy Bazaar", "Wario Stadium", "Airship Fortress",
    "DK Pass", "Starview Peak", "Sky-High Sundae", "Wario Shipyard",
    "Koopa Troopa Beach", "Faraway Oasis", "Peach Stadium", "Peach Beach",
    "Salty Salty Speedway", "Dino Dino Jungle", "Great ? Block Ruins",
    "Cheep Cheep Falls", "Dandelion Depths", "Boo Cinema", "Dry Bones Burnout",
    "Moo Moo Meadows", "Choco Mountain", "Toad's Factory", "Bowser's Castle",
    "Acorn Heights", "Mario Circuit", "Rainbow Road",
]


def test_slugify_strips_apostrophes_and_punctuation():
    assert slugify("Bowser's Castle") == "bowsers_castle"
    assert slugify("Toad's Factory") == "toads_factory"
    assert slugify("Wario's Galleon") == "warios_galleon"
    assert slugify("Mario Bros. Circuit") == "mario_bros_circuit"
    assert slugify("Great ? Block Ruins") == "great_block_ruins"
    assert slugify("Sky-High Sundae") == "sky_high_sundae"
    assert slugify("DK Pass") == "dk_pass"


def test_forty_canonical_courses():
    slugs = [s for s, _ in CANONICAL_COURSES]
    assert len(CANONICAL_COURSES) == 40
    assert len(set(slugs)) == 40


def test_world_map_courses_are_the_30_island_courses():
    from server.courses import RETRO_COURSES, WORLD_MAP_COURSES
    assert len(WORLD_MAP_COURSES) == 30
    assert len(RETRO_COURSES) == 10
    assert all(s.startswith("snes_") for s in RETRO_COURSES)
    assert not any(s in RETRO_COURSES for s, _ in WORLD_MAP_COURSES)
    # Appended, so pre-existing ids are stable: the 30 island courses come first.
    assert [s for s, _ in CANONICAL_COURSES[:30]] == [s for s, _ in WORLD_MAP_COURSES]


def test_canonical_slug_equals_slugified_display():
    for slug, display in CANONICAL_COURSES:
        assert slugify(display) == slug, f"{display!r} -> {slugify(display)!r} != {slug!r}"


def test_every_legacy_track_resolves_to_a_canonical_slug():
    canonical = {s for s, _ in CANONICAL_COURSES}
    for name in LEGACY_TRACK_NAMES:
        slug = legacy_track_slug(name)
        assert slug in canonical, f"{name!r} -> {slug!r} not in canonical set"


def test_wario_shipyard_aliases_to_warios_galleon():
    assert LEGACY_ALIASES["Wario Shipyard"] == "warios_galleon"
    assert legacy_track_slug("Wario Shipyard") == "warios_galleon"


def test_seed_courses_inserts_forty_rows_idempotently():
    conn = connect(":memory:")
    init_schema(conn)
    seed_courses(conn)
    seed_courses(conn)  # idempotent
    assert conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0] == 40
