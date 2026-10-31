"""Concurrent persistence and independently executable parser entry point."""

import os
import subprocess
import sys
from unittest.mock import Mock

import pytest

from valutatrade_hub.core.usecases import TradingService
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.parser_service import scheduler


def test_independent_processes_do_not_lose_deposits():
    db = DatabaseManager()
    db.initialize()
    service = TradingService()
    service.register("alice", "1234")
    code = (
        "from valutatrade_hub.core.usecases import TradingService; "
        "s = TradingService(); s.login('alice', '1234'); s.deposit('0.1')"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code],
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(4)
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, (stdout, stderr)
    service.login("alice", "1234")
    assert str(service.get_portfolio().get_wallet("USD").balance) == "0.4"
    with db.transaction():
        assert len(db.read("transactions.json")) == 4


def test_scheduler_once_partial_failure_and_invalid_interval(monkeypatch, capsys):
    updater = Mock()
    updater.run_update.return_value = {"updated": 2, "errors": ["offline"]}
    factory = Mock(return_value=updater)
    monkeypatch.setattr(scheduler, "create_updater", factory)
    assert scheduler.main(["--source", "coingecko"]) == 1
    factory.assert_called_once_with("coingecko")
    assert "2" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        scheduler.main(["--interval", "-1"])


def test_failed_commit_is_recovered_on_next_transaction(monkeypatch):
    db = DatabaseManager()
    db.initialize()
    service = TradingService()
    service.register("alice", "1234")
    service.login("alice", "1234")
    original = db._atomic_write
    calls = []

    def crash_during_commit(path, data):
        calls.append(path.name)
        if path.name == "transactions.json":
            raise OSError("simulated disk failure")
        original(path, data)

    from valutatrade_hub.core.exceptions import StorageError

    with monkeypatch.context() as temporary:
        temporary.setattr(db, "_atomic_write", crash_during_commit)
        with pytest.raises(StorageError):
            service.deposit(50)
    # The pending commit is rolled forward before subsequent reads are allowed.
    assert service.get_portfolio().get_wallet("USD").balance == 50
    with db.transaction():
        assert len(db.read("transactions.json")) == 1
    assert not (db.root / ".pending.json").exists()
