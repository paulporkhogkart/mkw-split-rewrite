"""Repeatable, idempotent importer for the legacy kart-off hogkart.db."""
from __future__ import annotations  # PEP 604 (str | None) annotations on the Pi's Python 3.9

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from server.db import init_schema
from server.courses import seed_courses, legacy_track_slug
from server.queries import recompute_is_pb


@dataclass
class ImportReport:
    players: int
    courses: int
    s0_runs: int
    world_records: int
    carryover_seeds: int


def wipe_imported(conn: sqlite3.Connection) -> None:
    """Delete all imported + carry-over rows; live data is left untouched."""
    conn.execute("DELETE FROM runs WHERE provenance IN ('legacy_import','carryover')")
    conn.execute("DELETE FROM world_records WHERE provenance='legacy_import'")


def ensure_seasons(conn, legacy, cutover_iso) -> tuple[int, int]:
    """Create/update Season 0 (historical) and Season 1 (active). Returns (s0_id, s1_id)."""
    row = legacy.execute(
        "SELECT MIN(achieved_at_utc) AS earliest FROM personal_bests").fetchone()
    s0_start = row["earliest"] if row and row["earliest"] else cutover_iso

    def _upsert(name, started, ended, active):
        existing = conn.execute("SELECT id FROM seasons WHERE name=?", (name,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE seasons SET started_at=?, ended_at=?, is_active=? WHERE id=?",
                (started, ended, active, existing["id"]))
            return existing["id"]
        return conn.execute(
            "INSERT INTO seasons(name, started_at, ended_at, is_active) VALUES(?,?,?,?)",
            (name, started, ended, active)).lastrowid

    s0_id = _upsert("Season 0", s0_start, cutover_iso, 0)
    s1_id = _upsert("Season 1", cutover_iso, None, 1)
    return s0_id, s1_id


# Per-player edge colours (lower-cased name -> hex), seeded with the players. Used by the
# Discord bot for /leaderboard and /nemesis embed edges; tuned for Discord dark mode.
PLAYER_COLORS = {
    "paul": "#a78bfa",     # violet
    "gub": "#38bdf8",      # blue
    "aliias": "#4ade80",   # green
    "luke": "#f87171",     # red
}


# Legacy hogkart names now displayed under a different name (privacy rename), keyed
# lower-case -> canonical display name. Applied at import so a re-import maps the old
# name onto the existing renamed player instead of recreating it / splitting history.
LEGACY_NAME_ALIASES = {"adymer": "Gub"}


def map_players(conn, legacy, s0_id, s1_id) -> dict[int, int]:
    """Map legacy player ids -> server player ids (case-insensitive), seeding rosters + colours.

    NOTE: this recreates a server player for every name in the legacy source. A player removed
    from the kart-off (see pi/src/db/purgeRemovedPlayers.ts) must also be scrubbed from the legacy
    hogkart DB before a fresh manual import, or this re-adds them until the next boot purge.
    """
    mapping: dict[int, int] = {}
    for row in legacy.execute("SELECT id, name FROM players"):
        name = LEGACY_NAME_ALIASES.get(row["name"].lower(), row["name"])
        color = PLAYER_COLORS.get(name.lower())
        existing = conn.execute(
            "SELECT id FROM players WHERE display_name = ? COLLATE NOCASE",
            (name,)).fetchone()
        if existing:
            pid = existing["id"]
            if color is not None:
                conn.execute("UPDATE players SET color=? WHERE id=?", (color, pid))
        else:
            pid = conn.execute(
                "INSERT INTO players(display_name, color) VALUES (?, ?)",
                (name, color)).lastrowid
        for sid in (s0_id, s1_id):
            conn.execute(
                "INSERT OR IGNORE INTO season_rosters(season_id, player_id) VALUES (?,?)",
                (sid, pid))
        mapping[row["id"]] = pid
    return mapping


def map_courses(conn, legacy) -> dict[int, int]:
    """Map legacy track ids -> server course ids. Raises ValueError on any unmapped track."""
    slug_to_id = {r["slug"]: r["id"] for r in conn.execute("SELECT id, slug FROM courses")}
    mapping: dict[int, int] = {}
    unmapped: list[str] = []
    for row in legacy.execute("SELECT id, name FROM tracks"):
        slug = legacy_track_slug(row["name"])
        if slug in slug_to_id:
            mapping[row["id"]] = slug_to_id[slug]
        else:
            unmapped.append(row["name"])
    if unmapped:
        raise ValueError(f"Unmapped legacy tracks (no canonical course): {unmapped}")
    return mapping


def import_pbs(conn, legacy, s0_id, player_map, course_map) -> int:
    """Insert each legacy PB as a Season 0 finished run (total-time only)."""
    n = 0
    for row in legacy.execute(
            "SELECT player_id, track_id, record, record_ms, achieved_at_utc "
            "FROM personal_bests"):
        ts = row["achieved_at_utc"]
        conn.execute(
            "INSERT INTO runs(season_id, player_id, course_id, cc, status, provenance, "
            "started_at, ended_at, total_time_ms, total_time_str, is_pb, created_at) "
            "VALUES (?,?,?,150,'finished','legacy_import',NULL,?,?,?,0,?)",
            (s0_id, player_map[row["player_id"]], course_map[row["track_id"]],
             ts, row["record_ms"], row["record"], ts),
        )
        n += 1
    return n


def import_world_records(conn, legacy, course_map) -> int:
    """Insert each legacy WR as a global world_records row (cc=150)."""
    n = 0
    for row in legacy.execute(
            "SELECT track_id, holder, record, record_ms, achieved_at_utc, "
            "video_url, character, vehicle FROM world_records"):
        conn.execute(
            "INSERT INTO world_records(course_id, cc, holder_name, record_ms, record_str, "
            "achieved_at, video_url, character, vehicle, provenance) "
            "VALUES (?,150,?,?,?,?,?,?,?,'legacy_import')",
            (course_map[row["track_id"]], row["holder"], row["record_ms"], row["record"],
             row["achieved_at_utc"], row["video_url"], row["character"], row["vehicle"]),
        )
        n += 1
    return n


def build_carryover(conn, s0_id, s1_id, cutover_iso) -> int:
    """Seed Season 1 from each player's final Season 0 PB.

    The seed mirrors the source PB's started_at/ended_at so it reflects when the run
    actually happened (the original achievement time), not the cutover. Only
    created_at marks the row's birth at cutover.
    """
    rows = conn.execute(
        "SELECT player_id, course_id, cc, total_time_ms, total_time_str, "
        "started_at, ended_at "
        "FROM runs WHERE season_id=? AND is_pb=1", (s0_id,)).fetchall()
    for r in rows:
        conn.execute(
            "INSERT INTO runs(season_id, player_id, course_id, cc, status, provenance, "
            "started_at, ended_at, total_time_ms, total_time_str, is_pb, created_at) "
            "VALUES (?,?,?,?,'finished','carryover',?,?,?,?,1,?)",
            (s1_id, r["player_id"], r["course_id"], r["cc"],
             r["started_at"], r["ended_at"],
             r["total_time_ms"], r["total_time_str"], cutover_iso),
        )
    return len(rows)


def import_legacy(legacy_db_path: str, conn, cutover_iso: str | None = None) -> ImportReport:
    """Idempotently import a legacy hogkart.db into the canonical store.

    Re-runnable: wipes prior imported + carry-over rows, leaves provenance='live'
    untouched, then reloads. The data phase commits once at the end (or rolls back).
    """
    if cutover_iso is None:
        cutover_iso = datetime.now(timezone.utc).isoformat()
    init_schema(conn)
    legacy = sqlite3.connect(f"file:{legacy_db_path}?mode=ro", uri=True)
    legacy.row_factory = sqlite3.Row
    try:
        wipe_imported(conn)
        seed_courses(conn)
        s0_id, s1_id = ensure_seasons(conn, legacy, cutover_iso)
        player_map = map_players(conn, legacy, s0_id, s1_id)
        course_map = map_courses(conn, legacy)
        n_pb = import_pbs(conn, legacy, s0_id, player_map, course_map)
        n_wr = import_world_records(conn, legacy, course_map)
        recompute_is_pb(conn, s0_id)
        n_carry = build_carryover(conn, s0_id, s1_id, cutover_iso)
        # The active season may already hold live runs (e.g. restoring the carryover
        # after a wipe): reconcile so each scope keeps exactly one is_pb - the
        # fastest finished run, whether that's a live run or the carryover seed.
        recompute_is_pb(conn, s1_id)
        n_courses = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        legacy.close()
    return ImportReport(players=len(player_map), courses=n_courses,
                        s0_runs=n_pb, world_records=n_wr, carryover_seeds=n_carry)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        description="Import the legacy kart-off hogkart.db into the canonical server DB.")
    ap.add_argument("--legacy-db", required=True, help="Path to a copy of hogkart.db")
    ap.add_argument("--out", required=True, help="Path to the server SQLite DB (created/updated)")
    ap.add_argument("--cutover", default=None,
                    help="ISO-8601 cutover timestamp (default: now, UTC)")
    args = ap.parse_args()

    from server.db import connect
    conn = connect(args.out)
    rep = import_legacy(args.legacy_db, conn, args.cutover)
    conn.close()
    print("Legacy import complete:")
    print(f"  players:         {rep.players}")
    print(f"  courses:         {rep.courses}")
    print(f"  S0 runs:         {rep.s0_runs}")
    print(f"  world_records:   {rep.world_records}")
    print(f"  carryover seeds: {rep.carryover_seeds}")


if __name__ == "__main__":
    main()
