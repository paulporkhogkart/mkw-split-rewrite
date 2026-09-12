"""v6 migration: minimap seeds + ROIs for the 10 SNES courses (game v1.8.0)."""
import sqlite3

from mkw_tracker.database.migrations import apply_migrations

SNES = ["Snes Vanilla Lake 1", "Snes Choco Island 1", "Snes Choco Island 2", "Snes Koopa Beach 1",
        "Snes Ghost Valley 1", "Snes Ghost Valley 2", "Snes Ghost Valley 3",
        "Snes Mario Circuit 1", "Snes Mario Circuit 2", "Snes Mario Circuit 3"]


def test_fresh_db_has_40_seeds_and_rois_at_v6(memdb):
    conn = sqlite3.connect(memdb)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM minimap_seeds").fetchone()[0] == 40
    assert conn.execute("SELECT COUNT(*) FROM minimap_rois").fetchone()[0] == 40
    for c in SNES:
        seed = conn.execute("SELECT cx, cy, radius FROM minimap_seeds WHERE course=?", (c,)).fetchone()
        roi = conn.execute("SELECT x, y, w, h FROM minimap_rois WHERE course=?", (c,)).fetchone()
        assert seed and roi, c
        # the seed must sit inside its own ROI
        x, y, w, h = roi
        assert x <= seed[0] <= x + w and y <= seed[1] <= y + h, (c, seed, roi)
        assert seed[2] == 20


def test_v5_db_gets_the_snes_rows_but_keeps_local_calibration(memdb):
    conn = sqlite3.connect(memdb)
    # Roll back to v5 with a locally calibrated SNES course still present.
    conn.execute("DELETE FROM minimap_seeds WHERE course LIKE 'Snes%'")
    conn.execute("DELETE FROM minimap_rois WHERE course LIKE 'Snes%'")
    conn.execute("INSERT INTO minimap_seeds(course, cx, cy, radius) VALUES ('Snes Koopa Beach 1', 1, 2, 20)")
    conn.execute("UPDATE schema_version SET version=5")
    conn.commit(); conn.close()

    apply_migrations(memdb)

    conn = sqlite3.connect(memdb)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM minimap_seeds WHERE course LIKE 'Snes%'").fetchone()[0] == 10
    assert conn.execute("SELECT COUNT(*) FROM minimap_rois WHERE course LIKE 'Snes%'").fetchone()[0] == 10
    # INSERT OR IGNORE: the locally calibrated seed wins over the shipped one
    assert conn.execute("SELECT cx, cy FROM minimap_seeds WHERE course='Snes Koopa Beach 1'").fetchone() == (1, 2)


def test_live_lookup_resolves_the_snes_rows_by_any_spelling(memdb):
    from mkw_tracker.database.replay_repo import get_minimap_seed, get_minimap_roi
    assert get_minimap_seed("Snes Mario Circuit 1")["cx"] == 1837      # detector spelling
    assert get_minimap_seed("SNES Mario Circuit 1")["cx"] == 1837      # canonical / WR-service spelling
    assert get_minimap_roi("SNES Ghost Valley 3")["w"] == 381
