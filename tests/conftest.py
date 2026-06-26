"""Shared pytest fixtures."""
import os
import sys
import pytest
from mkw_tracker.database.connection import get_connection, close_connection
from mkw_tracker.database.migrations import apply_migrations

_root = os.path.dirname(__file__)
for _d in ("tools/autotemplate", "tools/asset_matte"):
    _p = os.path.abspath(os.path.join(_root, "..", _d))
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def memdb(tmp_path):
    """Bind the shared SQLite connection to a fresh temp DB with schema applied."""
    close_connection()                  # drop any cached (real-DB) connection
    db_file = tmp_path / "test.db"
    get_connection(str(db_file))        # bind the singleton to the temp file
    apply_migrations(str(db_file))
    yield str(db_file)
    close_connection()
