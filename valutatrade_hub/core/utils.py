"""Shared validation, UTC timestamps, and closure-based TTL memoization."""

import math
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from time import monotonic


def currency_code(value):
    """Normalize a 2–5 character ASCII currency ticker."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{2,5}", value):
        raise ValueError("Код валюты: 2–5 латинских букв без пробелов")
    return value.upper()


def decimal_amount(value, *, allow_zero=False):
    """Convert a finite number to Decimal and enforce its sign."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("'amount' должен быть положительным числом")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("'amount' должен быть положительным числом") from None
    if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
        raise ValueError("'amount' должен быть положительным конечным числом")
    return result


def positive_rate(value):
    """Validate a numeric API rate (strings and booleans are rejected)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Курс должен быть числом")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Курс должен быть положительным конечным числом")
    return value


def utc_now():
    """Return an aware UTC datetime."""
    return datetime.now(UTC)


def iso_time(value=None):
    """Serialize a UTC instant without losing subsecond precision."""
    return (value or utc_now()).astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_time(value):
    """Parse an ISO timestamp; legacy naive timestamps are treated as UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def ttl_cache(seconds, clock=monotonic):
    """Memoize successful calls using a bounded closure and a monotonic clock."""

    def decorate(function):
        cache = {}

        @wraps(function)
        def wrapped(*args):
            now = clock()
            if args in cache and now - cache[args][0] < seconds:
                return cache[args][1]
            result = function(*args)
            if len(cache) >= 128:
                cache.clear()
            cache[args] = (now, result)
            return result

        wrapped.cache_clear = cache.clear
        return wrapped

    return decorate
