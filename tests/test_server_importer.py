# tests/test_server_importer.py
"""Tests for the legacy importer against a synthetic mini hogkart.db.

The importer is referenced as `importer.<fn>` (module import) on purpose: its
functions are implemented across Tasks 5-8, and module-attribute access lets a
not-yet-written function fail as a clean AttributeError in its own test rather
than breaking collection for the whole file.
"""
import sqlite3
import pytest
from server.db import connect, init_schema
from server.courses import seed_courses
from server.queries import recompute_is_pb
from server import importer

CUTOVER = "2026-06-04T00:00:00+00:00"


def make_legacy(path):
    """Build a tiny hogkart.db-shaped DB: 2 players, 2 tracks, 4 PBs, 2 WRs."""
    lg = sqlite3.connect(path)
    lg.executescript(
        """
        CREATE TABLE players(id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE tracks(id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE personal_bests(
            id INTEGER PRIMARY KEY, player_id INT, track_id INT,
            record TEXT, record_ms INT, achieved_at_utc TEXT,
            video_url TEXT, character TEXT, vehicle TEXT);
        CREATE TABLE world_records(
            id INTEGER PRIMARY KEY, holder TEXT, track_id INT,
            record TEXT, record_ms INT, achieved_at_utc TEXT,
            video_url TEXT, character TEXT, vehicle TEXT);
        """
    )
    lg.executemany("INSERT INTO players(id,name) VALUES(?,?)",
                   [(1, "Paul"), (2, "Luke")])
    # An apostrophe name + the Wario alias, to exercise both mapping paths.
    lg.executemany("INSERT INTO tracks(id,name) VALUES(?,?)",
                   [(1, "Bowser's Castle"), (2, "Wario Shipyard")])
    lg.executemany(
        "INSERT INTO personal_bests(player_id,track_id,record,record_ms,achieved_at_utc)"
        " VALUES(?,?,?,?,?)",
        [
            (1, 1, "1:50.000", 110000, "2025-01-01 00:00:00+00:00"),
            (1, 1, "1:48.000", 108000, "2025-02-01 00:00:00+00:00"),  # Paul's PB
            (2, 1, "1:52.000", 112000, "2025-01-15 00:00:00+00:00"),
            (1, 2, "2:00.000", 120000, "2025-01-01 00:00:00+00:00"),
        ],
    )
    lg.executemany(
        "INSERT INTO world_records(holder,track_id,record,record_ms,achieved_at_utc,"
        "video_url,character,vehicle) VALUES(?,?,?,?,?,?,?,?)",
        [
            ("SuperFX", 1, "1:40.000", 100000, "2025-03-01 00:00:00+00:00",
             "http://x", "Spike", "R.O.B. H.O.G."),
            ("玉", 2, "1:55.000", 115000, "2025-03-02 00:00:00+00:00",
             None, "Mario", "Std"),  # non-ASCII holder name
        ],
    )
    lg.commit()
    lg.close()


@pytest.fixture
def legacy_db(tmp_path):
    p = tmp_path / "hogkart.db"
    make_legacy(str(p))
    return str(p)


@pytest.fixture
def server_db():
    conn = connect(":memory:")
    init_schema(conn)
    return conn


def _open_legacy_ro(path):
    lg = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    lg.row_factory = sqlite3.Row
    return lg


def test_ensure_seasons_creates_s0_and_s1(legacy_db, server_db):
    lg = _open_legacy_ro(legacy_db)
    s0, s1 = importer.ensure_seasons(server_db, lg, CUTOVER)
    rows = {r["name"]: r for r in server_db.execute("SELECT * FROM seasons")}
    assert rows["Season 0"]["ended_at"] == CUTOVER
    assert rows["Season 0"]["is_active"] == 0
    assert rows["Season 1"]["ended_at"] is None
    assert rows["Season 1"]["is_active"] == 1
    assert rows["Season 0"]["started_at"] == "2025-01-01 00:00:00+00:00"


def test_map_players_creates_and_rosters(legacy_db, server_db):
    lg = _open_legacy_ro(legacy_db)
    s0, s1 = importer.ensure_seasons(server_db, lg, CUTOVER)
    pmap = importer.map_players(server_db, lg, s0, s1)
    assert set(pmap.keys()) == {1, 2}
    names = {r["display_name"] for r in server_db.execute("SELECT display_name FROM players")}
    assert names == {"Paul", "Luke"}
    assert server_db.execute("SELECT COUNT(*) FROM season_rosters").fetchone()[0] == 4
    # Colours are seeded alongside the players (from importer.PLAYER_COLORS).
    colors = {r["display_name"]: r["color"] for r in server_db.execute("SELECT display_name, color FROM players")}
    assert colors["Paul"] == "#a78bfa"
    assert colors["Luke"] == "#f87171"


def test_map_courses_resolves_apostrophe_and_alias(legacy_db, server_db):
    lg = _open_legacy_ro(legacy_db)
    seed_courses(server_db)
    cmap = importer.map_courses(server_db, lg)
    bowser = server_db.execute(
        "SELECT id FROM courses WHERE slug='bowsers_castle'").fetchone()["id"]
    wario = server_db.execute(
        "SELECT id FROM courses WHERE slug='warios_galleon'").fetchone()["id"]
    assert cmap == {1: bowser, 2: wario}


def test_map_courses_fails_loudly_on_unmapped(server_db):
    seed_courses(server_db)
    bad = sqlite3.connect(":memory:")
    bad.row_factory = sqlite3.Row
    bad.executescript("CREATE TABLE tracks(id INTEGER PRIMARY KEY, name TEXT);")
    bad.execute("INSERT INTO tracks(id,name) VALUES(1,'Totally Fake Track')")
    bad.commit()
    with pytest.raises(ValueError, match="Unmapped"):
        importer.map_courses(server_db, bad)


def test_import_pbs_inserts_s0_runs(legacy_db, server_db):
    lg = _open_legacy_ro(legacy_db)
    seed_courses(server_db)
    s0, s1 = importer.ensure_seasons(server_db, lg, CUTOVER)
    pmap = importer.map_players(server_db, lg, s0, s1)
    cmap = importer.map_courses(server_db, lg)
    n = importer.import_pbs(server_db, lg, s0, pmap, cmap)
    assert n == 4
    rows = server_db.execute(
        "SELECT season_id, cc, status, provenance, total_time_ms, total_time_str, "
        "ended_at, created_at FROM runs ORDER BY total_time_ms").fetchall()
    assert all(r["season_id"] == s0 and r["cc"] == 150 for r in rows)
    assert all(r["status"] == "finished" and r["provenance"] == "legacy_import" for r in rows)
    fastest = rows[0]
    assert fastest["total_time_ms"] == 108000
    assert fastest["total_time_str"] == "1:48.000"
    assert fastest["ended_at"] == "2025-02-01 00:00:00+00:00"
    assert fastest["created_at"] == "2025-02-01 00:00:00+00:00"


def test_import_world_records_preserves_fields_and_utf8(legacy_db, server_db):
    lg = _open_legacy_ro(legacy_db)
    seed_courses(server_db)
    s0, s1 = importer.ensure_seasons(server_db, lg, CUTOVER)
    cmap = importer.map_courses(server_db, lg)
    n = importer.import_world_records(server_db, lg, cmap)
    assert n == 2
    sfx = server_db.execute(
        "SELECT * FROM world_records WHERE holder_name='SuperFX'").fetchone()
    assert sfx["record_ms"] == 100000
    assert sfx["record_str"] == "1:40.000"
    assert sfx["character"] == "Spike"
    assert sfx["vehicle"] == "R.O.B. H.O.G."
    assert sfx["video_url"] == "http://x"
    assert sfx["cc"] == 150
    assert sfx["provenance"] == "legacy_import"
    jp = server_db.execute(
        "SELECT holder_name FROM world_records WHERE holder_name=?", ("玉",)).fetchone()
    assert jp is not None


def test_build_carryover_one_seed_per_player_course(legacy_db, server_db):
    lg = _open_legacy_ro(legacy_db)
    seed_courses(server_db)
    s0, s1 = importer.ensure_seasons(server_db, lg, CUTOVER)
    pmap = importer.map_players(server_db, lg, s0, s1)
    cmap = importer.map_courses(server_db, lg)
    importer.import_pbs(server_db, lg, s0, pmap, cmap)
    recompute_is_pb(server_db, s0)
    n = importer.build_carryover(server_db, s0, s1, CUTOVER)
    # Paul@Bowser, Luke@Bowser, Paul@Wario = 3 carry-over seeds.
    assert n == 3
    # Each seed mirrors its source S0 PB's started_at/ended_at (the original
    # achievement time), NOT the cutover stamp; only created_at marks row birth.
    src = {(r["player_id"], r["course_id"]): r for r in server_db.execute(
        "SELECT player_id, course_id, started_at, ended_at FROM runs "
        "WHERE season_id=? AND is_pb=1", (s0,)).fetchall()}
    seeds = server_db.execute(
        "SELECT player_id, course_id, provenance, status, is_pb, cc, total_time_ms, "
        "started_at, ended_at, created_at "
        "FROM runs WHERE season_id=? ORDER BY total_time_ms", (s1,)).fetchall()
    assert [s["total_time_ms"] for s in seeds] == [108000, 112000, 120000]
    for s in seeds:
        o = src[(s["player_id"], s["course_id"])]
        assert s["provenance"] == "carryover"
        assert s["status"] == "finished"
        assert s["is_pb"] == 1
        assert s["cc"] == 150
        assert s["started_at"] == o["started_at"]   # NULL for legacy imports
        assert s["ended_at"] == o["ended_at"]        # original achievement time, not cutover
        assert s["created_at"] == CUTOVER            # row birth = cutover


def test_import_legacy_full_run_report(legacy_db):
    conn = connect(":memory:")
    rep = importer.import_legacy(legacy_db, conn, CUTOVER)
    assert rep == importer.ImportReport(players=2, courses=30, s0_runs=4,
                                        world_records=2, carryover_seeds=3)


def test_import_legacy_is_idempotent(legacy_db):
    conn = connect(":memory:")
    importer.import_legacy(legacy_db, conn, CUTOVER)
    rep2 = importer.import_legacy(legacy_db, conn, CUTOVER)  # second run
    assert rep2 == importer.ImportReport(players=2, courses=30, s0_runs=4,
                                         world_records=2, carryover_seeds=3)
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE provenance='legacy_import'"
                        ).fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM world_records").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE provenance='carryover'"
                        ).fetchone()[0] == 3


def test_import_legacy_preserves_live_rows(legacy_db):
    conn = connect(":memory:")
    importer.import_legacy(legacy_db, conn, CUTOVER)
    s1 = conn.execute("SELECT id FROM seasons WHERE name='Season 1'").fetchone()["id"]
    pid = conn.execute("SELECT id FROM players LIMIT 1").fetchone()["id"]
    cid = conn.execute("SELECT id FROM courses LIMIT 1").fetchone()["id"]
    conn.execute(
        "INSERT INTO runs(season_id, player_id, course_id, cc, status, provenance, "
        "total_time_ms) VALUES (?,?,?,150,'finished','live',99999)", (s1, pid, cid))
    conn.commit()
    importer.import_legacy(legacy_db, conn, CUTOVER)  # re-run must not touch live rows
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE provenance='live'"
                        ).fetchone()[0] == 1
