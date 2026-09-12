"""Canonical course list, slug normalization, and legacy track mapping."""
import re
import sqlite3

# Canonical 30 courses as (slug, display_name). The slug matches the
# images/courses/<lang>/<slug>.png stems and is the stable cross-system key.
CANONICAL_COURSES: list[tuple[str, str]] = [
    ("mario_bros_circuit",   "Mario Bros. Circuit"),
    ("crown_city",           "Crown City"),
    ("whistlestop_summit",   "Whistlestop Summit"),
    ("dk_spaceport",         "DK Spaceport"),
    ("desert_hills",         "Desert Hills"),
    ("shy_guy_bazaar",       "Shy Guy Bazaar"),
    ("wario_stadium",        "Wario Stadium"),
    ("airship_fortress",     "Airship Fortress"),
    ("dk_pass",              "DK Pass"),
    ("starview_peak",        "Starview Peak"),
    ("sky_high_sundae",      "Sky-High Sundae"),
    ("warios_galleon",       "Wario’s Galleon"),
    ("koopa_troopa_beach",   "Koopa Troopa Beach"),
    ("faraway_oasis",        "Faraway Oasis"),
    ("peach_stadium",        "Peach Stadium"),
    ("peach_beach",          "Peach Beach"),
    ("salty_salty_speedway", "Salty Salty Speedway"),
    ("dino_dino_jungle",     "Dino Dino Jungle"),
    ("great_block_ruins",    "Great ? Block Ruins"),
    ("cheep_cheep_falls",    "Cheep Cheep Falls"),
    ("dandelion_depths",     "Dandelion Depths"),
    ("boo_cinema",           "Boo Cinema"),
    ("dry_bones_burnout",    "Dry Bones Burnout"),
    ("moo_moo_meadows",      "Moo Moo Meadows"),
    ("choco_mountain",       "Choco Mountain"),
    ("toads_factory",        "Toad’s Factory"),
    ("bowsers_castle",       "Bowser’s Castle"),
    ("acorn_heights",        "Acorn Heights"),
    ("mario_circuit",        "Mario Circuit"),
    ("rainbow_road",         "Rainbow Road"),
    # Game v1.8.0 (Sept 2026): 10 SNES retro courses added to Time Trials (5 laps each).
    # Appended so existing courses.id values stay stable.
    ("snes_mario_circuit_1", "SNES Mario Circuit 1"),
    ("snes_mario_circuit_2", "SNES Mario Circuit 2"),
    ("snes_mario_circuit_3", "SNES Mario Circuit 3"),
    ("snes_ghost_valley_1",  "SNES Ghost Valley 1"),
    ("snes_ghost_valley_2",  "SNES Ghost Valley 2"),
    ("snes_ghost_valley_3",  "SNES Ghost Valley 3"),
    ("snes_choco_island_1",  "SNES Choco Island 1"),
    ("snes_choco_island_2",  "SNES Choco Island 2"),
    ("snes_koopa_beach_1",   "SNES Koopa Beach 1"),
    ("snes_vanilla_lake_1",  "SNES Vanilla Lake 1"),
]

# Retro courses are not on the Mario Kart World island, so they have no place on the
# site's world/turf map (web/public/map). Everything else (leaderboards, tracks pages,
# WRs, trails) treats all CANONICAL_COURSES alike.
RETRO_COURSES: frozenset[str] = frozenset(s for s, _ in CANONICAL_COURSES if s.startswith("snes_"))
WORLD_MAP_COURSES: list[tuple[str, str]] = [c for c in CANONICAL_COURSES if c[0] not in RETRO_COURSES]

# Legacy track names whose slug does NOT match the canonical slug.
LEGACY_ALIASES: dict[str, str] = {
    "Wario Shipyard": "warios_galleon",
}


def slugify(name: str) -> str:
    """Lowercase, drop apostrophes, collapse non-alphanumeric runs to single '_'.

    Apostrophes are deleted (not turned into '_') so "Bowser's Castle" ->
    "bowsers_castle", matching the image-asset slugs.
    """
    s = name.lower().replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def legacy_track_slug(name: str) -> str:
    """Map a legacy track name to a canonical slug (alias-aware)."""
    if name in LEGACY_ALIASES:
        return LEGACY_ALIASES[name]
    return slugify(name)


def seed_courses(conn: sqlite3.Connection) -> None:
    """Insert the 30 canonical courses (idempotent)."""
    conn.executemany(
        "INSERT OR IGNORE INTO courses(slug, display_name) VALUES (?, ?)",
        CANONICAL_COURSES,
    )
    conn.commit()
