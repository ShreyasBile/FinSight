"""Test-wide safety settings, applied BEFORE any app module is imported.

* FINSIGHT_FORBID_HOSTED — no test can ever send a document to a hosted LLM
  (storage/ holds real applicants' documents).
* a throwaway data dir — tests never read or write the real Settings choice,
  dedupe index or traces.
* database + SQL sink off — tests that need LocalDB are marked `mssql`.
"""
import os
import sys
import tempfile
from pathlib import Path

API = Path(__file__).resolve().parents[1]
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

_DATA = tempfile.mkdtemp(prefix="finsight-tests-")
os.environ.update({
    "OCR_PROVIDER": "mock",
    "TAMPERING_AUTHENTICITY_ENABLED": "false",
    "TAMPERING_EXPLAIN_ENABLED": "false",
    "DB_ENABLED": "false",
    "AGENTS_SQL_SINK": "false",
    "FINSIGHT_FORBID_HOSTED": "true",
    "FINSIGHT_DATA_DIR": _DATA,
    "PYTHONIOENCODING": "utf-8",
})

import pytest  # noqa: E402


@pytest.fixture
def data_dir():
    return Path(_DATA)


@pytest.fixture
def fresh_settings(monkeypatch, tmp_path):
    """Each test gets its own empty LLM settings file."""
    from app.config import settings
    from app.agents.llm import settings_store
    monkeypatch.setattr(settings, "finsight_data_dir", str(tmp_path))
    monkeypatch.setattr(settings_store, "_cache", None)
    monkeypatch.setattr(settings_store, "_cache_mtime", None)
    return tmp_path


@pytest.fixture
def mock_llm(monkeypatch):
    """Register canned responses for agents while the provider is 'mock'."""
    from app.agents.llm import gateway
    registry = {}
    monkeypatch.setattr(gateway, "MOCK_RESPONDERS", registry)
    return registry
