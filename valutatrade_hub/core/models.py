"""User, Wallet, and Portfolio models with serialization boundaries."""

import hashlib
import hmac
import secrets
from decimal import Decimal

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.exceptions import InsufficientFundsError
from valutatrade_hub.core.utils import decimal_amount, iso_time, parse_time, utc_now


class User:
    """Registered user; passwords are salted SHA-256 hashes as required by the assignment."""

    def __init__(self, user_id, username, hashed_password, salt, registration_date):
        self._user_id = user_id
        self.username = username
        self._hashed_password = hashed_password
        self._salt = salt
        self._registration_date = (
            parse_time(registration_date)
            if isinstance(registration_date, str)
            else registration_date
        )

    @classmethod
    def create(cls, user_id, username, password):
        """Construct a new user with a random per-user salt."""
        user = cls(user_id, username, "", "", utc_now())
        user.change_password(password)
        return user

    @property
    def user_id(self):
        """Immutable storage identifier."""
        return self._user_id

    @property
    def username(self):
        """Validated display/login name."""
        return self._username

    @username.setter
    def username(self, value):
        if not isinstance(value, str) or not value.strip() or not value.isprintable():
            raise ValueError(
                "Имя пользователя не может быть пустым или содержать управляющие символы"
            )
        self._username = value.strip()

    @property
    def hashed_password(self):
        """One-way password digest; used only by persistence."""
        return self._hashed_password

    @property
    def salt(self):
        """Random salt used by the password digest."""
        return self._salt

    @property
    def registration_date(self):
        """Registration instant in UTC."""
        return self._registration_date

    def change_password(self, new_password):
        """Replace the password hash and salt after validating length."""
        if not isinstance(new_password, str) or len(new_password) < 4:
            raise ValueError("Пароль должен быть не короче 4 символов")
        self._salt = secrets.token_hex(16)
        self._hashed_password = self._hash(new_password)

    def _hash(self, password):
        return hashlib.sha256((password + self._salt).encode("utf-8")).hexdigest()

    def verify_password(self, password):
        """Compare digests in constant time."""
        return isinstance(password, str) and hmac.compare_digest(
            self._hashed_password, self._hash(password)
        )

    def get_user_info(self):
        """Return public identity information without hash or salt."""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "registration_date": iso_time(self.registration_date),
        }

    def to_dict(self):
        """Serialize for the private users.json file."""
        return {**self.get_user_info(), "hashed_password": self.hashed_password, "salt": self.salt}


class Wallet:
    """A single-currency wallet using Decimal for exact balance accounting."""

    def __init__(self, currency_code, balance=0):
        self._currency_code = get_currency(currency_code).code
        self.balance = balance

    @property
    def currency_code(self):
        """Immutable wallet currency."""
        return self._currency_code

    @property
    def balance(self):
        """Nonnegative finite balance."""
        return self._balance

    @balance.setter
    def balance(self, value):
        self._balance = decimal_amount(value, allow_zero=True)

    def deposit(self, amount):
        """Add a strictly positive amount."""
        self.balance += decimal_amount(amount)

    def withdraw(self, amount):
        """Withdraw only if the wallet can cover the whole amount."""
        amount = decimal_amount(amount)
        if amount > self.balance:
            raise InsufficientFundsError(self.balance, amount, self.currency_code)
        self.balance -= amount

    def get_balance_info(self):
        """Describe the current balance."""
        return f"{self.currency_code}: {self.balance}"


class Portfolio:
    """All wallets belonging to one user with an injected rate provider."""

    def __init__(self, user_id, wallets=None, *, user=None, rate_provider=None):
        self._user_id = user_id
        self._user = user
        self._wallets = dict(wallets or {})
        self._rate_provider = rate_provider

    @property
    def user_id(self):
        """Immutable owner identifier."""
        return self._user_id

    @property
    def user(self):
        """Owner object, without a setter."""
        return self._user

    @property
    def wallets(self):
        """A shallow copy; callers cannot add/remove internal dictionary entries."""
        return self._wallets.copy()

    def add_currency(self, currency_code):
        """Idempotently create a wallet and return it."""
        code = get_currency(currency_code).code
        if code not in self._wallets:
            self._wallets[code] = Wallet(code)
        return self._wallets[code]

    def get_wallet(self, currency_code):
        """Return a wallet or None if this currency was never added."""
        return self._wallets.get(get_currency(currency_code).code)

    def get_total_value(self, base_currency="USD"):
        """Value nonzero balances with the same rate contract used by trades."""
        base = get_currency(base_currency).code
        if self._rate_provider is None:
            from valutatrade_hub.core.rates import RateService

            provider = RateService().get_rate
        else:
            provider = self._rate_provider
        return sum(
            (w.balance * provider(code, base)[0] for code, w in self._wallets.items() if w.balance),
            Decimal(0),
        )

    def to_dict(self):
        """Persist exact decimal balances as JSON strings."""
        return {
            "user_id": self.user_id,
            "wallets": {c: {"balance": str(w.balance)} for c, w in self._wallets.items()},
        }

    @classmethod
    def from_dict(cls, data, **kwargs):
        """Load both legacy numeric and exact string balances."""
        return cls(
            data["user_id"],
            {c: Wallet(c, w["balance"]) for c, w in data["wallets"].items()},
            **kwargs,
        )
