# Canonical Data Model + Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new server-side `server/` Python package holding the canonical SQLite schema for the MKW time-trial competition, plus a repeatable, idempotent importer that loads the legacy `hogkart.db` (5 players, 30 tracks, 205 PBs, 473 WRs) into it.

**Architecture:** A standalone top-level `server/` package (separate from the `mkw_tracker` client engine, not shipped in its wheel, but importable from the repo root for tests and runnable via `python -m server.importer`). Plain `sqlite3`, no ORM, no global-singleton connection — every function takes an explicit `sqlite3.Connection` so tests stay isolated. Schema in a `.sql` file applied via `executescript` (matching the client's migration style). The importer reads a read-only copy of the legacy DB and rebuilds all imported + carry-over rows atomically, leaving any `provenance='live'` data untouched.

**Tech Stack:** Python ≥3.10 stdlib (`sqlite3`, `dataclasses`, `argparse`, `re`, `pathlib`), pytest. Spec: `docs/superpowers/specs/2026-06-04-canonical-data-model-migration-design.md`.

**Conventions for every commit in this plan:** end the commit message with a trailer line:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
Work happens on the existing `canonical-data-model` branch.

**Test-import convention:** `tests/test_server_importer.py` imports the importer as a *module* (`from server import importer`) and calls `importer.func(...)`. This is deliberate: the importer's functions are implemented across several tasks, and module-attribute access resolves at run time, so a not-yet-written function fails as a clean `AttributeError` inside the one new test (instead of an `ImportError` that breaks collection for the whole file).

---

## File Structure

| Path | Responsibility |
|---|---|
| `server/__init__.py` | Marks `server` a package (empty). |
| `server/schema.sql` | The canonical DDL: `seasons`, `players`, `season_rosters`, `courses`, `runs`, `run_laps`, `run_points`, `world_records` + indexes. All `CREATE ... IF NOT EXISTS` (re-runnable). |
| `server/db.py` | `connect(path) -> Connection` (Row factory, WAL, FK on); `init_schema(conn)` (executescript of `schema.sql`). |
| `server/courses.py` | `slugify(name)`; `CANONICAL_COURSES` (30 `(slug, display_name)` tuples); `LEGACY_ALIASES`; `legacy_track_slug(name)`; `seed_courses(conn)`. |
| `server/queries.py` | `recompute_is_pb(conn, season_id)`; `current_pb(...)`; `course_leaderboard(...)`. The minimal derived-data layer that proves the model and maintains the `is_pb` flag. |
| `server/importer.py` | `ImportReport` dataclass; step functions (`wipe_imported`, `ensure_seasons`, `map_players`, `map_courses`, `import_pbs`, `import_world_records`, `build_carryover`); `import_legacy(...)` orchestration; `main()` CLI. |
| `tests/test_server_db.py` | Schema applies; all tables/indexes exist; FK pragma on; CHECK constraints. |
| `tests/test_server_courses.py` | `slugify` cases; all 30 legacy names resolve (incl. apostrophes + Wario alias); `slugify(display)==slug` for all 30. |
| `tests/test_server_queries.py` | `current_pb` / `course_leaderboard` against a built DB. |
| `tests/test_server_importer.py` | Unit tests against a synthetic mini-legacy DB: each step + full run + idempotency + live-data preservation. |
| `tests/test_server_import_real.py` | Integration test, `skipif` the real `legacy/.../hogkart.db` is absent: asserts 5 / 30 / 205 / 473 / 150. |

**Interfaces locked here (used consistently across tasks):**

```python
# server/db.py
def connect(path: str) -> sqlite3.Connection: ...
def init_schema(conn: sqlite3.Connection) -> None: ...

# server/courses.py
def slugify(name: str) -> str: ...
CANONICAL_COURSES: list[tuple[str, str]]          # (slug, display_name)
LEGACY_ALIASES: dict[str, str]                     # legacy track name -> slug
def legacy_track_slug(name: str) -> str: ...       # alias-aware; never raises
def seed_courses(conn: sqlite3.Connection) -> None: ...

# server/queries.py
def recompute_is_pb(conn, season_id: int) -> None: ...
def current_pb(conn, season_id: int, player_id: int, course_id: int, cc: int): ...
def course_leaderboard(conn, season_id: int, course_id: int, cc: int) -> list: ...

# server/importer.py
@dataclass
class ImportReport:
    players: int; courses: int; s0_runs: int; world_records: int; carryover_seeds: int
def import_legacy(legacy_db_path: str, conn, cutover_iso: str | None = None) -> ImportReport: ...
```

---

### Task 1: Scaffold the `server/` package

**Files:**
- Create: `server/__init__.py`
- Test: `tests/test_server_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_server_db.py
"""Tests for the canonical server schema."""
import importlib


def test_server_package_imports():
    mod = importlib.import_module("server")
    assert mod is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_server_db.py::test_server_package_imports -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server'`

- [ ] **Step 3: Create the package**

Create `server/__init__.py`:

```python
"""Canonical server-side data model + legacy importer (sub-project A)."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_server_db.py::test_server_package_imports -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/__init__.py tests/test_server_db.py
git commit -m "feat(server): scaffold server package" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Schema + connection (`schema.sql`, `db.py`)

**Files:**
- Create: `server/schema.sql`, `server/db.py`
- Test: `tests/test_server_db.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_server_db.py`)

```python
import sqlite3
from server.db import connect, init_schema

EXPECTED_TABLES = {
    "seasons", "players", "season_rosters", "courses",
    "runs", "run_laps", "run_points", "world_records",
}


def _fresh():
    conn = connect(":memory:")
    init_schema(conn)
    return conn


def test_all_tables_created():
    conn = _fresh()
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= names


def test_runs_status_check_rejects_bad_value():
    conn = _fresh()
    conn.execute("INSERT INTO seasons(name) VALUES('Season 0')")
    conn.execute("INSERT INTO players(display_name) VALUES('P')")
    conn.execute("INSERT INTO courses(slug, display_name) VALUES('x','X')")
    try:
        conn.execute(
            "INSERT INTO runs(season_id, player_id, course_id, cc, status, provenance) "
            "VALUES(1,1,1,150,'bogus','live')")
        assert False, "CHECK on status should have rejected 'bogus'"
    except sqlite3.IntegrityError:
        pass


def test_foreign_keys_enabled():
    conn = _fresh()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_init_schema_is_idempotent():
    conn = _fresh()
    init_schema(conn)  # second call must not raise
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.db'`

- [ ] **Step 3: Create `server/schema.sql`**

```sql
-- Canonical server schema (sub-project A). SQLite, WAL. Re-runnable (IF NOT EXISTS).
CREATE TABLE IF NOT EXISTS seasons (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    started_at  TEXT,
    ended_at    TEXT,
    is_active   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS players (
    id            INTEGER PRIMARY KEY,
    display_name  TEXT NOT NULL UNIQUE,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS season_rosters (
    season_id  INTEGER NOT NULL REFERENCES seasons(id),
    player_id  INTEGER NOT NULL REFERENCES players(id),
    PRIMARY KEY (season_id, player_id)
);

CREATE TABLE IF NOT EXISTS courses (
    id            INTEGER PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    default_laps  INTEGER
);

CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY,
    season_id      INTEGER NOT NULL REFERENCES seasons(id),
    player_id      INTEGER NOT NULL REFERENCES players(id),
    course_id      INTEGER NOT NULL REFERENCES courses(id),
    cc             INTEGER NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('reset','dnf','finished')),
    provenance     TEXT NOT NULL CHECK (provenance IN ('live','legacy_import','carryover')),
    started_at     TEXT,
    ended_at       TEXT,
    total_time_ms  INTEGER,
    total_time_str TEXT,
    character      TEXT,
    kart           TEXT,
    costume        TEXT,
    is_pb          INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS run_laps (
    run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    lap_index     INTEGER NOT NULL,
    lap_time_ms   INTEGER NOT NULL,
    lap_time_str  TEXT,
    coins         INTEGER,
    shrooms       INTEGER,
    PRIMARY KEY (run_id, lap_index)
);

CREATE TABLE IF NOT EXISTS run_points (
    run_id   INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    t_ms     INTEGER NOT NULL,
    cx       REAL NOT NULL,
    cy       REAL NOT NULL,
    score    REAL NOT NULL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS world_records (
    id           INTEGER PRIMARY KEY,
    course_id    INTEGER NOT NULL REFERENCES courses(id),
    cc           INTEGER NOT NULL,
    holder_name  TEXT,
    record_ms    INTEGER NOT NULL,
    record_str   TEXT NOT NULL,
    achieved_at  TEXT,
    video_url    TEXT,
    character    TEXT,
    vehicle      TEXT,
    provenance   TEXT NOT NULL DEFAULT 'legacy_import',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_runs_leaderboard ON runs(season_id, course_id, cc, is_pb);
CREATE INDEX IF NOT EXISTS idx_runs_player      ON runs(season_id, player_id, course_id, cc);
CREATE INDEX IF NOT EXISTS idx_run_laps_run     ON run_laps(run_id);
CREATE INDEX IF NOT EXISTS idx_run_points_run   ON run_points(run_id);
CREATE INDEX IF NOT EXISTS idx_wr_course        ON world_records(course_id, cc, achieved_at);
```

- [ ] **Step 4: Create `server/db.py`**

```python
"""SQLite connection + schema bootstrap for the canonical server store."""
import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(path: str) -> sqlite3.Connection:
    """Open a connection with Row factory, WAL, and foreign keys enforced."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply the canonical schema. Safe to call repeatedly (IF NOT EXISTS)."""
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_server_db.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add server/schema.sql server/db.py tests/test_server_db.py
git commit -m "feat(server): canonical schema + connection helper" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Canonical courses + slugify + legacy mapping (`courses.py`)

**Files:**
- Create: `server/courses.py`
- Test: `tests/test_server_courses.py`

- [ ] **Step 1: Write the failing tests**

```python
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


def test_thirty_canonical_courses():
    slugs = [s for s, _ in CANONICAL_COURSES]
    assert len(CANONICAL_COURSES) == 30
    assert len(set(slugs)) == 30


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


def test_seed_courses_inserts_thirty_rows_idempotently():
    conn = connect(":memory:")
    init_schema(conn)
    seed_courses(conn)
    seed_courses(conn)  # idempotent
    assert conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0] == 30
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server_courses.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.courses'`

- [ ] **Step 3: Create `server/courses.py`**

```python
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
    ("warios_galleon",       "Wario's Galleon"),
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
    ("toads_factory",        "Toad's Factory"),
    ("bowsers_castle",       "Bowser's Castle"),
    ("acorn_heights",        "Acorn Heights"),
    ("mario_circuit",        "Mario Circuit"),
    ("rainbow_road",         "Rainbow Road"),
]

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server_courses.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add server/courses.py tests/test_server_courses.py
git commit -m "feat(server): canonical courses + apostrophe-stripping slugify" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: PB derivation queries (`queries.py`)

**Files:**
- Create: `server/queries.py`
- Test: `tests/test_server_queries.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_server_queries.py
"""Tests for is_pb maintenance and leaderboard derivation."""
from server.db import connect, init_schema
from server.courses import seed_courses
from server.queries import recompute_is_pb, current_pb, course_leaderboard


def _build():
    conn = connect(":memory:")
    init_schema(conn)
    seed_courses(conn)
    conn.execute("INSERT INTO seasons(id, name, is_active) VALUES(1,'Season 0',0)")
    conn.execute("INSERT INTO players(id, display_name) VALUES(1,'Paul'),(2,'Luke')")
    cid = conn.execute("SELECT id FROM courses WHERE slug='mario_circuit'").fetchone()["id"]
    # Paul: two finished runs (slower then faster); Luke: one. Plus a reset (ignored).
    rows = [
        (1, 1, cid, 150, "finished", "live", 110000, "2025-01-01"),
        (1, 1, cid, 150, "finished", "live", 108000, "2025-02-01"),
        (2, 1, cid, 150, "finished", "live", 112000, "2025-01-15"),
        (1, 1, cid, 150, "reset",    "live", None,   "2025-02-02"),
    ]
    conn.executemany(
        "INSERT INTO runs(season_id, player_id, course_id, cc, status, provenance, "
        "total_time_ms, ended_at) VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return conn, cid


def test_recompute_is_pb_flags_only_the_fastest_per_player():
    conn, cid = _build()
    recompute_is_pb(conn, 1)
    flagged = conn.execute(
        "SELECT player_id, total_time_ms FROM runs WHERE is_pb=1 ORDER BY player_id"
    ).fetchall()
    assert [(r["player_id"], r["total_time_ms"]) for r in flagged] == [(1, 108000), (2, 112000)]


def test_current_pb_returns_the_flagged_row():
    conn, cid = _build()
    recompute_is_pb(conn, 1)
    pb = current_pb(conn, 1, 1, cid, 150)
    assert pb["total_time_ms"] == 108000


def test_course_leaderboard_is_ordered_and_named():
    conn, cid = _build()
    recompute_is_pb(conn, 1)
    lb = course_leaderboard(conn, 1, cid, 150)
    assert [(r["display_name"], r["total_time_ms"]) for r in lb] == [
        ("Paul", 108000), ("Luke", 112000)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server_queries.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.queries'`

- [ ] **Step 3: Create `server/queries.py`**

```python
"""Derived-data layer: is_pb maintenance + leaderboard reads."""
import sqlite3


def recompute_is_pb(conn: sqlite3.Connection, season_id: int) -> None:
    """Set is_pb=1 on each (player, course, cc)'s fastest finished run, 0 elsewhere."""
    conn.execute("UPDATE runs SET is_pb=0 WHERE season_id=?", (season_id,))
    conn.execute(
        """
        UPDATE runs SET is_pb=1 WHERE id IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY player_id, course_id, cc
                    ORDER BY total_time_ms ASC, ended_at ASC
                ) AS rn
                FROM runs
                WHERE season_id=? AND status='finished'
            ) WHERE rn=1
        )
        """,
        (season_id,),
    )
    conn.commit()


def current_pb(conn, season_id, player_id, course_id, cc):
    """The current PB row for a (season, player, course, cc), or None."""
    return conn.execute(
        "SELECT * FROM runs WHERE season_id=? AND player_id=? AND course_id=? "
        "AND cc=? AND is_pb=1",
        (season_id, player_id, course_id, cc),
    ).fetchone()


def course_leaderboard(conn, season_id, course_id, cc):
    """Each player's PB on a course, fastest first, with display_name joined."""
    return conn.execute(
        "SELECT r.*, p.display_name FROM runs r "
        "JOIN players p ON p.id = r.player_id "
        "WHERE r.season_id=? AND r.course_id=? AND r.cc=? AND r.is_pb=1 "
        "ORDER BY r.total_time_ms ASC",
        (season_id, course_id, cc),
    ).fetchall()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server_queries.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add server/queries.py tests/test_server_queries.py
git commit -m "feat(server): is_pb recompute + leaderboard queries" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Importer — structural seed (seasons, players, courses, tracks)

**Files:**
- Create: `server/importer.py`
- Test: `tests/test_server_importer.py`

- [ ] **Step 1: Write the synthetic-legacy fixture + failing tests**

```python
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
    # S0 starts at the earliest legacy PB timestamp.
    assert rows["Season 0"]["started_at"] == "2025-01-01 00:00:00+00:00"


def test_map_players_creates_and_rosters(legacy_db, server_db):
    lg = _open_legacy_ro(legacy_db)
    s0, s1 = importer.ensure_seasons(server_db, lg, CUTOVER)
    pmap = importer.map_players(server_db, lg, s0, s1)
    assert set(pmap.keys()) == {1, 2}
    names = {r["display_name"] for r in server_db.execute("SELECT display_name FROM players")}
    assert names == {"Paul", "Luke"}
    assert server_db.execute("SELECT COUNT(*) FROM season_rosters").fetchone()[0] == 4


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server_importer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.importer'`

- [ ] **Step 3: Create `server/importer.py` with the structural-seed functions**

```python
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
```

Note: the `init_schema`, `seed_courses`, and `recompute_is_pb` imports are used by functions added in Tasks 6-8; importing them now is harmless.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server_importer.py -v`
Expected: PASS (4 tests). The PB/WR/carryover/import_legacy tests are added in later tasks.

- [ ] **Step 5: Commit**

```bash
git add server/importer.py tests/test_server_importer.py
git commit -m "feat(server): importer structural seed (seasons, players, tracks)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Importer — PB + WR row import

**Files:**
- Modify: `server/importer.py`
- Test: `tests/test_server_importer.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_server_importer.py`)

```python
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
    # Original legacy timestamp preserved on both ended_at and created_at.
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
    # Non-ASCII holder survives intact.
    jp = server_db.execute(
        "SELECT holder_name FROM world_records WHERE holder_name=?", ("玉",)).fetchone()
    assert jp is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server_importer.py::test_import_pbs_inserts_s0_runs tests/test_server_importer.py::test_import_world_records_preserves_fields_and_utf8 -v`
Expected: FAIL with `AttributeError: module 'server.importer' has no attribute 'import_pbs'`

- [ ] **Step 3: Add `import_pbs` and `import_world_records` to `server/importer.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_server_importer.py -v`
Expected: PASS (6 tests now)

- [ ] **Step 5: Commit**

```bash
git add server/importer.py tests/test_server_importer.py
git commit -m "feat(server): import legacy PBs (S0 runs) + world records" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Importer — carry-over seeds

**Files:**
- Modify: `server/importer.py`
- Test: `tests/test_server_importer.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_server_importer.py`)

```python
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
    seeds = server_db.execute(
        "SELECT provenance, status, is_pb, cc, total_time_ms, started_at, ended_at, created_at "
        "FROM runs WHERE season_id=? ORDER BY total_time_ms", (s1,)).fetchall()
    assert [s["total_time_ms"] for s in seeds] == [108000, 112000, 120000]
    for s in seeds:
        assert s["provenance"] == "carryover"
        assert s["status"] == "finished"
        assert s["is_pb"] == 1
        assert s["cc"] == 150
        assert s["started_at"] == CUTOVER
        assert s["ended_at"] == CUTOVER
        assert s["created_at"] == CUTOVER
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_server_importer.py::test_build_carryover_one_seed_per_player_course -v`
Expected: FAIL with `AttributeError: module 'server.importer' has no attribute 'build_carryover'`

- [ ] **Step 3: Add `build_carryover` to `server/importer.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_server_importer.py -v`
Expected: PASS (7 tests now)

- [ ] **Step 5: Commit**

```bash
git add server/importer.py tests/test_server_importer.py
git commit -m "feat(server): build Season 1 carry-over seeds from final S0 PBs" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Importer — `import_legacy` orchestration + idempotency

**Files:**
- Modify: `server/importer.py`
- Test: `tests/test_server_importer.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_server_importer.py`)

```python
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
    # Simulate a live Season 1 run arriving after import.
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_server_importer.py::test_import_legacy_full_run_report -v`
Expected: FAIL with `AttributeError: module 'server.importer' has no attribute 'import_legacy'`

- [ ] **Step 3: Add `import_legacy` to `server/importer.py`**

```python
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
```

Note: `init_schema`, `seed_courses`, and `recompute_is_pb` each `commit()` internally; that is harmless (they are idempotent). The destructive `wipe_imported` + reloads still roll back together on error because the failing statement aborts before the final `conn.commit()`.

- [ ] **Step 4: Run the full server test suite to verify it passes**

Run: `python -m pytest tests/test_server_importer.py tests/test_server_db.py tests/test_server_courses.py tests/test_server_queries.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add server/importer.py tests/test_server_importer.py
git commit -m "feat(server): import_legacy orchestration + idempotent reload" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: CLI + real-data integration test + README

**Files:**
- Modify: `server/importer.py` (add `main()` + `__main__` guard)
- Create: `tests/test_server_import_real.py`, `server/README.md`

- [ ] **Step 1: Write the real-data integration test**

```python
# tests/test_server_import_real.py
"""Integration test against the real copied legacy DB (skipped if absent)."""
from pathlib import Path
import pytest
from server.db import connect
from server.importer import import_legacy, ImportReport

REAL_LEGACY = (Path(__file__).resolve().parents[1]
               / "legacy" / "mkwpb2" / "kart-off" / "data" / "hogkart.db")

pytestmark = pytest.mark.skipif(
    not REAL_LEGACY.exists(),
    reason="real legacy hogkart.db not present (gitignored); run locally to validate migration",
)

CUTOVER = "2026-06-04T00:00:00+00:00"


def test_real_migration_acceptance_numbers(tmp_path):
    conn = connect(str(tmp_path / "server.db"))
    rep = import_legacy(str(REAL_LEGACY), conn, CUTOVER)
    assert rep == ImportReport(players=5, courses=30, s0_runs=205,
                               world_records=473, carryover_seeds=150)


def test_real_migration_is_idempotent(tmp_path):
    conn = connect(str(tmp_path / "server.db"))
    import_legacy(str(REAL_LEGACY), conn, CUTOVER)
    rep2 = import_legacy(str(REAL_LEGACY), conn, CUTOVER)
    assert rep2.s0_runs == 205 and rep2.world_records == 473 and rep2.carryover_seeds == 150
```

- [ ] **Step 2: Run the integration test (locally, where the legacy DB exists)**

Run: `python -m pytest tests/test_server_import_real.py -v`
Expected: PASS (real DB present) asserting 5 / 30 / 205 / 473 / 150; SKIPPED where `legacy/` is absent.

- [ ] **Step 3: Add the CLI to `server/importer.py`**

```python
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
```

- [ ] **Step 4: Run the CLI against the real snapshot (manual smoke test)**

Run:
```bash
python -m server.importer --legacy-db legacy/mkwpb2/kart-off/data/hogkart.db --out temp/server_practice.db --cutover 2026-06-04T00:00:00+00:00
```
Expected output:
```
Legacy import complete:
  players:         5
  courses:         30
  S0 runs:         205
  world_records:   473
  carryover seeds: 150
```
(`temp/` is gitignored, so the practice DB is not committed.)

- [ ] **Step 5: Create `server/README.md`**

```markdown
# server/ — Canonical data store + legacy importer (sub-project A)

Server-side canonical SQLite schema for the MKW time-trial competition, plus a
repeatable importer for the legacy `kart-off` data. See the design spec:
`docs/superpowers/specs/2026-06-04-canonical-data-model-migration-design.md`.

## Layout
- `schema.sql` — the canonical DDL (seasons, players, runs, world_records, ...).
- `db.py` — `connect(path)` / `init_schema(conn)`.
- `courses.py` — the 30 canonical courses, `slugify`, legacy track mapping.
- `queries.py` — `recompute_is_pb`, `current_pb`, `course_leaderboard`.
- `importer.py` — `import_legacy(...)` + `python -m server.importer` CLI.

## Run the importer
    python -m server.importer --legacy-db <copy-of-hogkart.db> --out server.db

Idempotent: re-running wipes prior `legacy_import` + `carryover` rows and reloads,
leaving any `provenance='live'` rows untouched. Practice now; run once more on the
final dump at cutover.

## Out of scope here (later sub-projects)
HTTP/transport, client auth + upload, the WR scraper, live push, website/OBS,
reign computation.
```

- [ ] **Step 6: Run the entire test suite (server + existing) to confirm nothing regressed**

Run: `python -m pytest -q`
Expected: all pre-existing tests pass + the new server tests pass (the real-data test PASSES locally, SKIPS in CI).

- [ ] **Step 7: Commit**

```bash
git add server/importer.py server/README.md tests/test_server_import_real.py
git commit -m "feat(server): import CLI + real-data acceptance test + README" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the executor

- **Run from the repo root** so `import server.*` and `python -m server.importer` resolve (the repo root is on `sys.path` for pytest because `tests/` is a package, and for `python -m` because CWD is added).
- **No packaging change** — `server/` deliberately stays out of the `mkw-tracker` wheel (`pyproject.toml` only includes `mkw_tracker*`). It is a separate deployable.
- **Window functions** (`ROW_NUMBER() OVER (...)`) require SQLite ≥3.25; Python ≥3.10 bundles a newer SQLite, so this is fine.
- **The real-data tests** depend on `legacy/mkwpb2/kart-off/data/hogkart.db`, which is gitignored. They run locally (validating the practice migration) and skip elsewhere — that is intended, not a failure.
- **Reigns are intentionally out of scope for sub-project A.** The spec (§6) treats them as derived and materializable later, and they're consumed by the broadcast/website (sub-project C). This plan implements `is_pb` + `course_leaderboard` only.
```
