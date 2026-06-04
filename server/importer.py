"""Repeatable, idempotent importer for the legacy kart-off hogkart.db."""
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


def map_players(conn, legacy, s0_id, s1_id) -> dict[int, int]:
    """Map legacy player ids -> server player ids (case-insensitive), seeding rosters."""
    mapping: dict[int, int] = {}
    for row in legacy.execute("SELECT id, name FROM players"):
        existing = conn.execute(
            "SELECT id FROM players WHERE display_name = ? COLLATE NOCASE",
            (row["name"],)).fetchone()
        pid = existing["id"] if existing else conn.execute(
            "INSERT INTO players(display_name) VALUES (?)", (row["name"],)).lastrowid
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
    """Seed Season 1 from each player's final Season 0 PB, timestamped at cutover."""
    rows = conn.execute(
        "SELECT player_id, course_id, cc, total_time_ms, total_time_str "
        "FROM runs WHERE season_id=? AND is_pb=1", (s0_id,)).fetchall()
    for r in rows:
        conn.execute(
            "INSERT INTO runs(season_id, player_id, course_id, cc, status, provenance, "
            "started_at, ended_at, total_time_ms, total_time_str, is_pb, created_at) "
            "VALUES (?,?,?,?,'finished','carryover',?,?,?,?,1,?)",
            (s1_id, r["player_id"], r["course_id"], r["cc"], cutover_iso, cutover_iso,
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
        n_courses = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        legacy.close()
    return ImportReport(players=len(player_map), courses=n_courses,
                        s0_runs=n_pb, world_records=n_wr, carryover_seeds=n_carry)
