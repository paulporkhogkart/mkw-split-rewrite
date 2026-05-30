"""Shared pytest fixtures."""
import pytest
from mkw_tracker.database.connection import get_connection, close_connection
from mkw_tracker.database.migrations import apply_migrations


@pytest.fixture
def memdb(tmp_path):
    """Bind the shared SQLite connection to a fresh temp DB with schema applied."""
    close_connection()                  # drop any cached (real-DB) connection
    db_file = tmp_path / "test.db"
    get_connection(str(db_file))        # bind the singleton to the temp file
    apply_migrations(str(db_file))
    yield str(db_file)
    close_connection()
