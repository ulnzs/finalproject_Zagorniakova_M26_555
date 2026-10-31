"""Collect rates independently from each provider and retain partial successes."""

import logging

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.utils import iso_time
from valutatrade_hub.parser_service.api_clients import CoinGeckoClient, ExchangeRateApiClient
from valutatrade_hub.parser_service.storage import RateStorage

logger = logging.getLogger("valutatrade.parser")


class RatesUpdater:
    """Coordinate clients and storage; no CLI or wallet responsibilities."""

    def __init__(self, clients, storage=None):
        self.clients = list(clients)
        self.storage = storage or RateStorage()

    def run_update(self):
        """Save successful provider responses and return counts plus safe error messages."""
        logger.info("Starting rates update")
        records, errors = [], []
        for client in self.clients:
            try:
                logger.info("Fetching from %s", client.source)
                rates = client.fetch_rates()
                timestamp = client.updated_at or iso_time()
                for pair, rate in rates.items():
                    from_code, to_code = pair.split("_")
                    records.append(
                        {
                            "id": f"{pair}_{timestamp}",
                            "from_currency": from_code,
                            "to_currency": to_code,
                            "rate": rate,
                            "timestamp": timestamp,
                            "source": client.source,
                            "meta": client.metadata.get(pair, {}),
                        }
                    )
                logger.info("%s OK (%s rates)", client.source, len(rates))
            except ApiRequestError as exc:
                errors.append(str(exc))
                logger.error("%s", exc)
        if not records:
            raise ApiRequestError("; ".join(errors) or "источники не вернули курсы")
        changed = self.storage.save(records)
        logger.info("Update complete: %s updated, %s failed providers", changed, len(errors))
        return {"updated": changed, "errors": errors, "last_refresh": iso_time()}


def create_updater(source=None):
    """Build a production updater with optional source filtering."""
    factories = {"coingecko": CoinGeckoClient, "exchangerate": ExchangeRateApiClient}
    if source is not None and source not in factories:
        raise ValueError("Источник: coingecko или exchangerate")
    return RatesUpdater(
        [factory() for name, factory in factories.items() if source in (None, name)]
    )
