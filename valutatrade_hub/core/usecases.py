"""Application operations: authentication, portfolios, simulated funding, and trades."""

from decimal import Decimal
from uuid import uuid4

from valutatrade_hub.core.currencies import REGISTRY, CryptoCurrency, get_currency
from valutatrade_hub.core.exceptions import AuthenticationError, ValutaTradeError
from valutatrade_hub.core.models import Portfolio, User
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.core.utils import decimal_amount, iso_time
from valutatrade_hub.decorators import log_action
from valutatrade_hub.infra.database import DatabaseManager


class TradingService:
    """Keep session state in memory and persist each balance mutation with its audit record."""

    def __init__(self, database=None, rates=None):
        self.database = database or DatabaseManager()
        self.rates = rates or RateService(self.database)
        self.current_user = None

    def require_login(self):
        """Return the current user or reject unauthenticated operations."""
        if self.current_user is None:
            raise AuthenticationError("Сначала выполните login")
        return self.current_user

    @log_action("REGISTER")
    def register(self, username, password):
        """Register a unique name and an empty portfolio in one transaction."""
        # Validate before checking duplicates so normalized usernames have one identity.
        candidate = User.create(1, username, password)
        with self.database.transaction():
            users = self.database.read("users.json")
            if any(u["username"] == candidate.username for u in users):
                raise ValutaTradeError(f"Имя пользователя '{candidate.username}' уже занято")
            user = User.create(
                max((u["user_id"] for u in users), default=0) + 1, candidate.username, password
            )
            portfolios = self.database.read("portfolios.json")
            users.append(user.to_dict())
            portfolios.append(Portfolio(user.user_id).to_dict())
            self.database.save_many({"users.json": users, "portfolios.json": portfolios})
        return user.get_user_info()

    @log_action("LOGIN")
    def login(self, username, password):
        """Authenticate in this process; a failed login clears any previous session."""
        self.current_user = None
        with self.database.transaction():
            raw = next(
                (u for u in self.database.read("users.json") if u["username"] == username), None
            )
        if raw is None:
            raise AuthenticationError(f"Пользователь '{username}' не найден")
        user = User(**raw)
        if not user.verify_password(password):
            raise AuthenticationError("Неверный пароль")
        self.current_user = user
        return user.get_user_info()

    def logout(self):
        """Forget the in-memory authenticated session."""
        self.current_user = None

    def _load_portfolio(self, documents):
        user = self.require_login()
        for index, raw in enumerate(documents):
            if raw["user_id"] == user.user_id:
                return index, Portfolio.from_dict(raw, user=user, rate_provider=self.get_rate)
        raise ValutaTradeError("Портфель пользователя не найден")

    def get_portfolio(self):
        """Load an isolated model representing the current persisted portfolio."""
        self.require_login()
        with self.database.transaction():
            return self._load_portfolio(self.database.read("portfolios.json"))[1]

    def get_rate(self, from_code, to_code):
        """Expose the shared rate provider contract."""
        return self.rates.get_rate(from_code, to_code)

    def _persist_change(self, documents, index, portfolio, action, code, amount, rate):
        transactions = self.database.read("transactions.json")
        transactions.append(
            {
                "id": uuid4().hex,
                "timestamp": iso_time(),
                "user_id": portfolio.user_id,
                "action": action,
                "currency_code": code,
                "amount": str(amount),
                "rate": str(rate),
                "base": "USD",
                "value": str(amount * rate),
            }
        )
        documents[index] = portfolio.to_dict()
        self.database.save_many({"portfolios.json": documents, "transactions.json": transactions})

    @log_action("DEPOSIT")
    def deposit(self, amount):
        """Credit virtual USD explicitly; this is a simulator, no real payments occur."""
        self.require_login()
        amount = decimal_amount(amount)
        with self.database.transaction():
            documents = self.database.read("portfolios.json")
            index, portfolio = self._load_portfolio(documents)
            portfolio.add_currency("USD").deposit(amount)
            self._persist_change(documents, index, portfolio, "DEPOSIT", "USD", amount, Decimal(1))
        return {"currency_code": "USD", "amount": amount, "base": "USD", "rate": Decimal(1)}

    def _trade(self, action, currency_code, amount):
        self.require_login()
        code = get_currency(currency_code).code
        amount = decimal_amount(amount)
        if code == "USD":
            raise ValueError("USD — расчётная валюта. Для пополнения используйте deposit")
        # Never hold a storage lock while making a slow HTTP request.
        rate, updated_at = self.get_rate(code, "USD")
        cost = amount * rate
        with self.database.transaction():
            documents = self.database.read("portfolios.json")
            index, portfolio = self._load_portfolio(documents)
            usd = portfolio.add_currency("USD")
            target = portfolio.add_currency(code)
            before = {"USD": str(usd.balance), code: str(target.balance)}
            if action == "BUY":
                usd.withdraw(cost)
                target.deposit(amount)
            else:
                target.withdraw(amount)
                usd.deposit(cost)
            self._persist_change(documents, index, portfolio, action, code, amount, rate)
            after = {"USD": str(usd.balance), code: str(target.balance)}
        return {
            "currency_code": code,
            "amount": amount,
            "rate": rate,
            "base": "USD",
            "value": cost,
            "updated_at": updated_at,
            "balances": {"before": before, "after": after},
        }

    @log_action("BUY", verbose=True)
    def buy(self, currency_code, amount):
        """Debit USD and credit the purchased asset atomically."""
        return self._trade("BUY", currency_code, amount)

    @log_action("SELL", verbose=True)
    def sell(self, currency_code, amount):
        """Debit an asset and credit the USD proceeds atomically."""
        return self._trade("SELL", currency_code, amount)

    def show_portfolio(self, base="USD"):
        """Prepare a complete valuation for presentation without printing."""
        base = get_currency(base).code
        portfolio = self.get_portfolio()
        rows = []
        for code, wallet in sorted(portfolio.wallets.items()):
            value = wallet.balance * self.get_rate(code, base)[0] if wallet.balance else Decimal(0)
            rows.append((code, wallet.balance, value))
        return {
            "username": self.current_user.username,
            "base": base,
            "rows": rows,
            "total": sum((row[2] for row in rows), Decimal(0)),
        }

    def show_rates(self, currency=None, top=None, base="USD"):
        """List only fresh cached quotes; this read-only command never initiates HTTP."""
        base = get_currency(base).code
        selected = get_currency(currency).code if currency else None
        if top is not None and top <= 0:
            raise ValueError("--top должен быть положительным целым числом")
        snapshot = self.rates.snapshot()
        pairs = snapshot.get("pairs", {})
        codes = {c for pair in pairs for c in pair.split("_")}
        if selected:
            codes = {selected}
        rows = []
        for code in sorted(codes - {base}):
            if top is not None and not isinstance(REGISTRY.get(code), CryptoCurrency):
                continue
            rate, updated_at = self.rates.get_rate(code, base, auto_refresh=False)
            rows.append((code, rate, updated_at))
        if not rows:
            raise ValutaTradeError("Локальный кеш курсов пуст. Выполните update-rates")
        if top is not None:
            rows = sorted(rows, key=lambda row: row[1], reverse=True)[:top]
        return {"base": base, "rows": rows, "last_refresh": snapshot.get("last_refresh")}
