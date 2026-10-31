"""Fresh-cache rate resolution, inverse rates, and cross conversion through USD."""

from decimal import Decimal

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.exceptions import ApiRequestError, StorageError
from valutatrade_hub.core.utils import iso_time, parse_time, positive_rate, ttl_cache, utc_now
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader


class RateService:
    """Resolve quotes locally, refreshing at most once when data is missing or stale."""

    def __init__(self, database=None, refresh=None, clock=utc_now):
        self.database = database or DatabaseManager()
        self.settings = SettingsLoader()
        self.refresh = refresh or self._refresh
        self.clock = clock

        @ttl_cache(1)
        def read_snapshot(signature):
            with self.database.transaction():
                return self.database.read("rates.json")

        self._read_snapshot = read_snapshot

    @staticmethod
    def _refresh():
        from valutatrade_hub.parser_service.updater import create_updater

        return create_updater().run_update()

    def snapshot(self):
        """Read a snapshot; the file signature invalidates the closure cache on updates."""
        path = self.database.root / "rates.json"
        try:
            stat = path.stat() if path.exists() else None
        except OSError:
            raise StorageError("Не удалось прочитать файл курсов") from None
        signature = (stat.st_mtime_ns, stat.st_size) if stat else None
        return self._read_snapshot(signature)

    def _resolve(self, pairs, from_code, to_code):
        def direct(a, b):
            if a == b:
                return Decimal(1), self.clock()
            entry = pairs.get(f"{a}_{b}")
            inverse = False
            if entry is None:
                entry = pairs.get(f"{b}_{a}")
                inverse = True
            if not isinstance(entry, dict):
                raise ValueError("нет данных")
            rate = Decimal(str(positive_rate(entry["rate"])))
            stamp = parse_time(entry["updated_at"])
            age = (self.clock() - stamp).total_seconds()
            if age < -5 or age >= self.settings.get("RATES_TTL_SECONDS"):
                raise ValueError("курсы устарели")
            return (1 / rate if inverse else rate), stamp

        if f"{from_code}_{to_code}" in pairs or f"{to_code}_{from_code}" in pairs:
            return direct(from_code, to_code)
        a, at = direct(from_code, "USD")
        b, bt = direct(to_code, "USD")
        return a / b, min(at, bt)

    def get_rate(self, from_code, to_code, *, auto_refresh=True):
        """Return (Decimal rate, ISO observation timestamp), never a stale trade quote."""
        from_code, to_code = get_currency(from_code).code, get_currency(to_code).code
        if from_code == to_code:
            return Decimal(1), iso_time(self.clock())
        for attempt in range(2):
            try:
                rate, stamp = self._resolve(self.snapshot().get("pairs", {}), from_code, to_code)
                return rate, iso_time(stamp)
            except (KeyError, TypeError, ValueError, AttributeError):
                if attempt == 0 and auto_refresh:
                    self.refresh()
                    self._read_snapshot.cache_clear()
                    continue
                raise ApiRequestError(
                    f"Курс {from_code}→{to_code} недоступен или устарел. "
                    "Выполните update-rates; проверьте сеть и повторите позже"
                ) from None
