"""Shared fixtures.

Tests must never touch the real candidate store: they previously appended to
data/candidates.json until it grew to ~3 MB and became corrupt, which broke
every request that read it. Each session now writes to a temporary file that
is discarded afterwards.
"""
import os
import sys
import tempfile

os.environ["SEMANTIC_ENGINE"] = "tfidf"
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_store():
    """Point the JSON fallback DB at a throwaway file for the test session."""
    from database import db_handler
    tmpdir = tempfile.mkdtemp(prefix="resume_tests_")
    db_handler.LOCAL_DB_PATH = os.path.join(tmpdir, "candidates.json")
    yield db_handler.LOCAL_DB_PATH


@pytest.fixture(autouse=True)
def _clear_store(_isolated_store):
    """Start each test from an empty candidate store."""
    from database import db_handler
    db_handler._save_local([])
    yield