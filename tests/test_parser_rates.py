"""Contract tests for HTTP adapters, partial updates, history, and cache freshness."""

import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
import requests

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.rates import RateService
from valutatrade_hub.core.utils import iso_time, ttl_cache, utc_now
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.parser_service.api_clients import CoinGeckoClient, ExchangeRateApiClient
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import RateStorage
from valutatrade_hub.parser_service.updater import RatesUpdater


def session_for(payload, status=200):
    response = Mock(status_code=status, headers={})
    response.json.return_value = payload
    return Mock(get=Mock(return_value=response))


def test_http_normalization_timeout_and_fiat_inverse():
    session = session_for(
        {
            "result": "success",
            "base_code": "USD",
            "conversion_rates": {
                "EUR": 0.8,
                "GBP": 0.5,
                "RUB": 100,
            },
        }
    )
    config = ParserConfig(EXCHANGERATE_API_KEY="not-a-real-key")
    client = ExchangeRateApiClient(config, session)
    assert client.fetch_rates() == {"EUR_USD": 1.25, "GBP_USD": 2.0, "RUB_USD": 0.01}
    assert session.get.call_args.kwargs["timeout"] == 10
    session = session_for(
        {"bitcoin": {"usd": 60000}, "ethereum": {"usd": 3000}, "solana": {"usd": 150}}
    )
    assert CoinGeckoClient(session=session).fetch_rates()["BTC_USD"] == 60000
    assert session.get.call_args.kwargs["params"]["ids"] == "bitcoin,ethereum,solana"


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_http_status_errors_never_leak_key(status):
    client = ExchangeRateApiClient(
        ParserConfig(EXCHANGERATE_API_KEY="SECRET"), session_for({}, status)
    )
    with pytest.raises(ApiRequestError) as error:
        client.fetch_rates()
    assert str(status) in str(error.value) and "SECRET" not in str(error.value)


@pytest.mark.parametrize(
    "payload", [[], {}, {"bitcoin": {"usd": -1}}, {"bitcoin": {"usd": float("nan")}}]
)
def test_bad_payloads(payload):
    with pytest.raises(ApiRequestError):
        CoinGeckoClient(session=session_for(payload)).fetch_rates()


def test_network_and_missing_key_errors():
    session = Mock()
    session.get.side_effect = requests.Timeout("https://example/SECRET")
    with pytest.raises(ApiRequestError) as error:
        CoinGeckoClient(session=session).fetch_rates()
    assert "SECRET" not in str(error.value)
    with pytest.raises(ApiRequestError, match="EXCHANGERATE_API_KEY"):
        ExchangeRateApiClient().fetch_rates()


def record(pair, rate, timestamp):
    a, b = pair.split("_")
    return {
        "id": f"{pair}_{timestamp}",
        "from_currency": a,
        "to_currency": b,
        "rate": rate,
        "timestamp": timestamp,
        "source": "test",
        "meta": {},
    }


def test_history_dedup_and_newer_wins():
    now = iso_time()
    old = iso_time(utc_now() - timedelta(days=1))
    storage = RateStorage()
    assert storage.save([record("EUR_USD", 1.25, now)]) == 1
    assert storage.save([record("EUR_USD", 1.25, now)]) == 0
    assert storage.save([record("EUR_USD", 1.1, old)]) == 0
    with storage.database.transaction():
        assert len(storage.database.read("exchange_rates.json")) == 2
        assert storage.database.read("rates.json")["pairs"]["EUR_USD"]["rate"] == 1.25


def test_partial_failure_preserves_other_source_and_total_failure_writes_nothing():
    good = Mock(source="CoinGecko", updated_at=iso_time(), metadata={})
    good.fetch_rates.return_value = {"BTC_USD": 60000}
    bad = Mock(source="ExchangeRate-API")
    bad.fetch_rates.side_effect = ApiRequestError("offline")
    result = RatesUpdater([bad, good]).run_update()
    assert result["updated"] == 1 and len(result["errors"]) == 1
    db = DatabaseManager()
    before = (db.root / "rates.json").read_bytes()
    with pytest.raises(ApiRequestError):
        RatesUpdater([bad]).run_update()
    assert (db.root / "rates.json").read_bytes() == before


def test_rate_direct_inverse_cross_and_refresh():
    storage = RateStorage()
    now = utc_now()
    storage.save([record("EUR_USD", 1.25, iso_time(now)), record("BTC_USD", 60000, iso_time(now))])
    refresh = Mock()
    rates = RateService(refresh=refresh, clock=lambda: now)
    assert rates.get_rate("USD", "EUR")[0] == Decimal("0.8")
    assert rates.get_rate("BTC", "EUR")[0] == Decimal("48000")
    assert rates.get_rate("USD", "USD")[0] == 1
    refresh.assert_not_called()
    expired = RateService(refresh=refresh, clock=lambda: now + timedelta(seconds=300))
    with pytest.raises(ApiRequestError, match="устарел"):
        expired.get_rate("EUR", "USD")
    refresh.assert_called_once()
    # A fresh on-disk snapshot invalidates memoized JSON immediately.
    storage.save([record("EUR_USD", 1.5, iso_time(now + timedelta(seconds=1)))])
    assert rates.get_rate("EUR", "USD")[0] == Decimal("1.5")


def test_corrupt_rate_is_not_traded_and_closure_expires():
    db = DatabaseManager()
    db.initialize()
    with db.transaction():
        db.save_many(
            {
                "rates.json": {
                    "pairs": {
                        "EUR_USD": {
                            "rate": -1,
                            "updated_at": iso_time(),
                        }
                    }
                }
            }
        )
    with pytest.raises(ApiRequestError):
        RateService().get_rate("EUR", "USD", auto_refresh=False)
    ticks = [0]
    function = Mock(return_value=42)
    cached = ttl_cache(5, clock=lambda: ticks[0])(function)
    assert cached("a") == cached("a") == 42
    assert function.call_count == 1
    ticks[0] = 5
    cached("a")
    assert function.call_count == 2
    json.dumps(db.read("rates.json"), allow_nan=False)
