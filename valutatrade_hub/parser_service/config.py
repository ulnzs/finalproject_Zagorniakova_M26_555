"""Centralized API endpoints, watched assets, and environment credentials."""

import os
from dataclasses import dataclass, field

CRYPTO_ID_MAP = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}


@dataclass
class ParserConfig:
    """Load secrets when constructing the config, never at module import time."""

    EXCHANGERATE_API_KEY: str = field(
        default_factory=lambda: os.getenv("EXCHANGERATE_API_KEY", ""), repr=False
    )
    COINGECKO_API_KEY: str = field(
        default_factory=lambda: os.getenv("COINGECKO_API_KEY", ""), repr=False
    )
    COINGECKO_URL: str = "https://api.coingecko.com/api/v3/simple/price"
    EXCHANGERATE_API_URL: str = "https://v6.exchangerate-api.com/v6"
    BASE_CURRENCY: str = "USD"
    FIAT_CURRENCIES: tuple = ("EUR", "GBP", "RUB")
    CRYPTO_CURRENCIES: tuple = ("BTC", "ETH", "SOL")
    CRYPTO_ID_MAP: dict = field(default_factory=lambda: CRYPTO_ID_MAP.copy())
    REQUEST_TIMEOUT: float = 10
