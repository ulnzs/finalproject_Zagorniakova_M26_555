"""Model invariants and recovery of persisted state."""

import json
from decimal import Decimal

import pytest

from valutatrade_hub.core.currencies import Currency, get_currency
from valutatrade_hub.core.exceptions import (
    CurrencyNotFoundError,
    InsufficientFundsError,
    StorageError,
)
from valutatrade_hub.core.models import Portfolio, User, Wallet
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader


def test_password_serialization_and_change():
    user = User.create(1, "alice", "secret")
    other = User.create(2, "bob", "secret")
    assert user.hashed_password != other.hashed_password
    assert user.verify_password("secret") and not user.verify_password("wrong")
    assert "hashed_password" not in user.get_user_info()
    assert User(**user.to_dict()).verify_password("secret")
    user.change_password("new-password")
    assert not user.verify_password("secret") and user.verify_password("new-password")
    with pytest.raises(ValueError):
        user.change_password("a")
    with pytest.raises(ValueError):
        user.username = " "


@pytest.mark.parametrize("amount", [-1, 0, True, None, "NaN", "Infinity", float("nan"), []])
def test_wallet_rejects_invalid_deposits(amount):
    wallet = Wallet("USD", 10)
    with pytest.raises(ValueError):
        wallet.deposit(amount)
    assert wallet.balance == 10


def test_wallet_exact_arithmetic_and_overdraft():
    wallet = Wallet("USD", "0.1")
    wallet.deposit("0.2")
    assert wallet.balance == Decimal("0.3")
    with pytest.raises(InsufficientFundsError):
        wallet.withdraw(1)
    assert wallet.balance == Decimal("0.3")
    for bad in [-1, True, None, "NaN"]:
        with pytest.raises(ValueError):
            wallet.balance = bad


def test_portfolio_registry_and_copy():
    with pytest.raises(TypeError):
        Currency("X", "XXX")
    with pytest.raises(CurrencyNotFoundError):
        get_currency("XYZ")
    assert "[FIAT]" in get_currency("usd").get_display_info()
    assert "[CRYPTO]" in get_currency("BTC").get_display_info()
    user = User.create(1, "alice", "1234")
    portfolio = Portfolio(1, user=user, rate_provider=lambda a, b: (Decimal(2), "now"))
    wallet = portfolio.add_currency("EUR")
    assert portfolio.add_currency("EUR") is wallet
    portfolio.wallets.clear()
    wallet.deposit(10)
    assert portfolio.get_total_value() == 20
    assert portfolio.user is user
    assert Portfolio.from_dict(portfolio.to_dict()).get_wallet("EUR").balance == 10


def test_singletons_and_transaction_recovery():
    assert SettingsLoader() is SettingsLoader()
    db = DatabaseManager()
    assert db is DatabaseManager()
    db.initialize()
    pending = {"users.json": [{"user_id": 99}], "portfolios.json": []}
    (db.root / ".pending.json").write_text(json.dumps(pending), encoding="utf-8")
    with db.transaction():
        assert db.read("users.json") == [{"user_id": 99}]
    assert not (db.root / ".pending.json").exists()
    (db.root / "users.json").write_text("broken", encoding="utf-8")
    with pytest.raises(StorageError), db.transaction():
        db.read("users.json")
    assert (db.root / "users.json").read_text() == "broken"
