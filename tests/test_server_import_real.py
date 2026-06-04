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
