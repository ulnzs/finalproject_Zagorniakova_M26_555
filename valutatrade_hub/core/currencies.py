"""Currency hierarchy and the supported currency registry."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from valutatrade_hub.core.exceptions import CurrencyNotFoundError
from valutatrade_hub.core.utils import currency_code


@dataclass(frozen=True)
class Currency(ABC):
    """Immutable currency identity with a polymorphic display contract."""

    name: str
    code: str

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Название валюты не может быть пустым")
        object.__setattr__(self, "code", currency_code(self.code))

    @abstractmethod
    def get_display_info(self) -> str:
        """Return a readable description for the UI."""


@dataclass(frozen=True)
class FiatCurrency(Currency):
    """A currency issued by a country or monetary union."""

    issuing_country: str

    def get_display_info(self):
        """Include the issuing country or monetary union."""
        return f"[FIAT] {self.code} — {self.name} (Issuing: {self.issuing_country})"


@dataclass(frozen=True)
class CryptoCurrency(Currency):
    """A digital asset with algorithm and optional capitalization metadata."""

    algorithm: str
    market_cap: float = 0.0

    def get_display_info(self):
        """Include algorithm and capitalization (zero means unknown)."""
        return (
            f"[CRYPTO] {self.code} — {self.name} "
            f"(Algo: {self.algorithm}, MCAP: {self.market_cap:.3g})"
        )


REGISTRY = {
    c.code: c
    for c in (
        FiatCurrency("US Dollar", "USD", "United States"),
        FiatCurrency("Euro", "EUR", "Eurozone"),
        FiatCurrency("Pound Sterling", "GBP", "United Kingdom"),
        FiatCurrency("Russian Ruble", "RUB", "Russia"),
        CryptoCurrency("Bitcoin", "BTC", "SHA-256"),
        CryptoCurrency("Ethereum", "ETH", "Proof of Stake"),
        CryptoCurrency("Solana", "SOL", "Proof of History / Stake"),
    )
}


def get_currency(code) -> Currency:
    """Resolve a normalized ticker or raise a domain error."""
    normalized = currency_code(code)
    try:
        return REGISTRY[normalized]
    except KeyError:
        raise CurrencyNotFoundError(normalized) from None
