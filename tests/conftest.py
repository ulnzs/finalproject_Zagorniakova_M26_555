"""Isolated data roots for every test; never use a real user's wallet."""

import pytest

from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("VALUTATRADE_HOME", str(tmp_path))
    monkeypatch.delenv("VALUTATRADE_DATA_DIR", raising=False)
    monkeypatch.delenv("VALUTATRADE_LOG_DIR", raising=False)
    monkeypatch.delenv("EXCHANGERATE_API_KEY", raising=False)
    SettingsLoader._instance = None
    DatabaseManager._instance = None
    yield
    SettingsLoader._instance = None
    DatabaseManager._instance = None
