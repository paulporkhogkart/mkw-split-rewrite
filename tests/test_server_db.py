# tests/test_server_db.py
"""Tests for the canonical server schema."""
import importlib


def test_server_package_imports():
    mod = importlib.import_module("server")
    assert mod is not None
