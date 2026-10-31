"""Validated history and latest-rate snapshot persistence."""

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.utils import iso_time, parse_time, positive_rate
from valutatrade_hub.infra.database import DatabaseManager


class RateStorage:
    """Persist history and current quotes in one recoverable transaction."""

    def __init__(self, database=None):
        self.database = database or DatabaseManager()

    def save(self, records):
        """Deduplicate history IDs; only newer observations replace snapshot entries."""
        for record in records:
            get_currency(record["from_currency"])
            get_currency(record["to_currency"])
            positive_rate(record["rate"])
            parse_time(record["timestamp"])
            expected = f"{record['from_currency']}_{record['to_currency']}_{record['timestamp']}"
            if record["id"] != expected:
                raise ValueError("Неверный идентификатор замера")
        db = self.database
        with db.transaction():
            history = db.read("exchange_rates.json")
            snapshot = db.read("rates.json")
            pairs = snapshot.setdefault("pairs", {})
            known_ids = {r["id"] for r in history}
            changed = 0
            for record in records:
                if record["id"] not in known_ids:
                    history.append(record)
                    known_ids.add(record["id"])
                pair = f"{record['from_currency']}_{record['to_currency']}"
                current = pairs.get(pair)
                if current is None or parse_time(record["timestamp"]) > parse_time(
                    current["updated_at"]
                ):
                    pairs[pair] = {
                        "rate": record["rate"],
                        "updated_at": record["timestamp"],
                        "source": record["source"],
                    }
                    changed += 1
            if changed:
                snapshot["last_refresh"] = iso_time()
            db.save_many({"rates.json": snapshot, "exchange_rates.json": history})
            return changed
