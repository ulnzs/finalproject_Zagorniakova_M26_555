"""End-to-end session, accounting, failure atomicity, and safe auditing."""

import logging
import shlex
from unittest.mock import Mock

import pytest

from valutatrade_hub.cli.interface import Console
from valutatrade_hub.core.exceptions import (
    AuthenticationError,
    InsufficientFundsError,
    ValutaTradeError,
)
from valutatrade_hub.core.usecases import TradingService
from valutatrade_hub.core.utils import iso_time
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.logging_config import configure_logging
from valutatrade_hub.parser_service.storage import RateStorage


@pytest.fixture
def service():
    db = DatabaseManager()
    db.initialize()
    stamp = iso_time()
    RateStorage(db).save(
        [
            {
                "id": f"{code}_USD_{stamp}",
                "from_currency": code,
                "to_currency": "USD",
                "rate": rate,
                "timestamp": stamp,
                "source": "fixture",
                "meta": {},
            }
            for code, rate in [("BTC", 60000), ("ETH", 3000), ("EUR", 1.25)]
        ]
    )
    return TradingService(db)


def test_full_trade_cycle_reload_and_no_partial_failure(service):
    with pytest.raises(AuthenticationError):
        service.buy("BTC", "0.01")
    service.register("alice", "safe-password")
    with pytest.raises(ValutaTradeError):
        service.register(" alice ", "safe-password")
    service.login("alice", "safe-password")
    assert service.get_portfolio().wallets == {}
    service.deposit(1000)
    service.buy("BTC", "0.01")
    assert service.get_portfolio().get_wallet("USD").balance == 400
    before = (service.database.root / "portfolios.json").read_bytes()
    with pytest.raises(InsufficientFundsError):
        service.buy("BTC", 1)
    with pytest.raises(InsufficientFundsError):
        service.sell("BTC", 1)
    assert (service.database.root / "portfolios.json").read_bytes() == before
    service.sell("BTC", "0.004")
    assert service.show_portfolio()["total"] == 1000
    assert service.show_portfolio("EUR")["total"] == 800
    other_session = TradingService()
    other_session.login("alice", "safe-password")
    assert other_session.get_portfolio().get_wallet("USD").balance == 640
    with service.database.transaction():
        assert len(service.database.read("transactions.json")) == 3
    with pytest.raises(AuthenticationError):
        service.login("alice", "wrong-password")
    assert service.current_user is None


def test_cli_happy_path_errors_and_source_filter(service):
    output = []
    updater = Mock()
    updater.return_value.run_update.return_value = {
        "updated": 3,
        "errors": [],
        "last_refresh": iso_time(),
    }
    console = Console(service, output=output.append, updater_factory=updater)
    commands = [
        "register --username alice --password 1234",
        "login --username alice --password 1234",
        "deposit --amount 1000",
        "buy --currency BTC --amount 0.01",
        "sell --currency BTC --amount 0.004",
        "show-portfolio",
        "get-rate --from USD --to BTC",
        "update-rates --source coingecko",
        "show-rates --top 1 --base EUR",
    ]
    for command in commands:
        assert console.execute(shlex.split(command)) == 0
    updater.assert_called_once_with("coingecko")
    assert console.execute(["buy", "--currency", "BTC"]) == 1
    assert console.execute(shlex.split("sell --currency BTC --amount 1")) == 1
    assert console.execute(shlex.split("get-rate --from ABC --to USD")) == 1
    assert "Недостаточно средств" in "\n".join(output)
    assert "Поддерживаются" in "\n".join(output)


def test_logging_success_failure_rotation_and_no_passwords(service):
    configure_logging()
    configure_logging()
    logger = logging.getLogger("valutatrade.actions")
    assert len(logger.handlers) == 1
    assert logger.handlers[0].maxBytes == 1048576
    service.register("alice", "SUPER_SECRET_PASSWORD")
    service.login("alice", "SUPER_SECRET_PASSWORD")
    service.deposit(1000)
    service.buy("BTC", "0.01")
    with pytest.raises(InsufficientFundsError):
        service.sell("BTC", 999)
    logger.handlers[0].flush()
    with open(logger.handlers[0].baseFilename, encoding="utf-8") as stream:
        content = stream.read()
    assert '"result": "OK"' in content and '"result": "ERROR"' in content
    assert '"rate": "60000"' in content and '"base": "USD"' in content
    assert "SUPER_SECRET_PASSWORD" not in content
    assert "InsufficientFundsError" in content


def test_repl_recovers_from_bad_quotes_and_exit_confirmation(service):
    lines = iter(['buy "', "exit", "n", "exit", "y"])
    output = []
    console = Console(service, reader=lambda _: next(lines), output=output.append)
    assert console.run() == 0
    assert any("Ошибка команды" in line for line in output)


def test_help_does_not_terminate_session(service, capsys):
    console = Console(service)
    assert console.execute(["help", "get-rate"]) == 0
    assert "--from" in capsys.readouterr().out
    assert console.execute(["help"]) == 0
    assert "register" in capsys.readouterr().out


def test_multiple_users_do_not_share_balances(service):
    service.register("alice", "1234")
    service.register("bob", "1234")
    service.login("alice", "1234")
    service.deposit(200)
    service.login("bob", "1234")
    assert service.show_portfolio()["total"] == 0
    service.logout()
    with pytest.raises(AuthenticationError):
        service.get_portfolio()
