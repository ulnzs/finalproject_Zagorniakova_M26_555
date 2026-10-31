"""HTTP adapters that expose one normalized {FROM_TO: numeric_rate} contract."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from time import perf_counter

import requests

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.utils import iso_time, positive_rate
from valutatrade_hub.parser_service.config import ParserConfig


class BaseApiClient(ABC):
    """Common timeout, safe error mapping, and response metadata."""

    source = "abstract"

    def __init__(self, config=None, session=None):
        self.config = config or ParserConfig()
        self.session = session or requests.Session()
        self.metadata = {}
        self.updated_at = None

    def _request(self, url, **kwargs):
        started = perf_counter()
        try:
            response = self.session.get(url, timeout=self.config.REQUEST_TIMEOUT, **kwargs)
            if response.status_code != 200:
                reasons = {
                    401: "неверный API-ключ",
                    403: "доступ запрещён",
                    429: "лимит запросов исчерпан, повторите позже",
                }
                raise ApiRequestError(
                    f"{self.source}: HTTP {response.status_code}; "
                    f"{reasons.get(response.status_code, 'сервис недоступен')}"
                )
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("JSON object expected")
        except requests.RequestException as exc:
            # requests exception strings may contain the API key embedded in the URL.
            raise ApiRequestError(f"{self.source}: сетевая ошибка ({type(exc).__name__})") from None
        except (ValueError, TypeError):
            raise ApiRequestError(f"{self.source}: неверный JSON-ответ") from None
        self.updated_at = iso_time()
        return payload, {
            "request_ms": round((perf_counter() - started) * 1000),
            "status_code": response.status_code,
            "etag": response.headers.get("ETag"),
        }

    @abstractmethod
    def fetch_rates(self) -> dict:
        """Return positive finite numeric rates expressed as FROM_TO pairs."""


class CoinGeckoClient(BaseApiClient):
    """Fetch crypto prices in USD using CoinGecko IDs rather than tickers."""

    source = "CoinGecko"

    def fetch_rates(self):
        """Normalize all requested crypto assets or reject the malformed response."""
        config = self.config
        headers = {}
        if config.COINGECKO_API_KEY:
            headers["x-cg-demo-api-key"] = config.COINGECKO_API_KEY
        data, meta = self._request(
            config.COINGECKO_URL,
            params={
                "ids": ",".join(config.CRYPTO_ID_MAP[c] for c in config.CRYPTO_CURRENCIES),
                "vs_currencies": config.BASE_CURRENCY.lower(),
                "include_last_updated_at": "true",
            },
            headers=headers,
        )
        rates, metadata = {}, {}
        try:
            for code in config.CRYPTO_CURRENCIES:
                raw_id = config.CRYPTO_ID_MAP[code]
                entry = data[raw_id]
                pair = f"{code}_{config.BASE_CURRENCY}"
                rates[pair] = positive_rate(entry[config.BASE_CURRENCY.lower()])
                metadata[pair] = {**meta, "raw_id": raw_id}
                if "last_updated_at" in entry:
                    metadata[pair]["source_updated_at"] = iso_time(
                        datetime.fromtimestamp(entry["last_updated_at"], UTC)
                    )
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            raise ApiRequestError("CoinGecko: отсутствуют или некорректны курсы") from None
        self.metadata = metadata
        return rates


class ExchangeRateApiClient(BaseApiClient):
    """Fetch fiat quotes; invert USD→EUR to obtain the normalized EUR→USD rate."""

    source = "ExchangeRate-API"

    def fetch_rates(self):
        """Accept the official conversion_rates field and the legacy rates example."""
        config = self.config
        if not config.EXCHANGERATE_API_KEY:
            raise ApiRequestError("ExchangeRate-API: задайте EXCHANGERATE_API_KEY")
        url = (
            f"{config.EXCHANGERATE_API_URL}/{config.EXCHANGERATE_API_KEY}"
            f"/latest/{config.BASE_CURRENCY}"
        )
        data, meta = self._request(url)
        if data.get("result") != "success" or data.get("base_code") != config.BASE_CURRENCY:
            raise ApiRequestError("ExchangeRate-API: ошибка сервиса или неверная базовая валюта")
        try:
            raw = data.get("conversion_rates", data.get("rates"))
            rates = {
                f"{c}_{config.BASE_CURRENCY}": positive_rate(1 / positive_rate(raw[c]))
                for c in config.FIAT_CURRENCIES
            }
            if "time_last_update_unix" in data:
                meta["source_updated_at"] = iso_time(
                    datetime.fromtimestamp(data["time_last_update_unix"], UTC)
                )
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            raise ApiRequestError("ExchangeRate-API: отсутствуют или некорректны курсы") from None
        self.metadata = {pair: meta.copy() for pair in rates}
        return rates
