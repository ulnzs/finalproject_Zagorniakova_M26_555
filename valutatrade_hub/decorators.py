"""Reusable action auditing and explicit confirmation decorators."""

import inspect
import json
import logging
from functools import wraps
from time import perf_counter

logger = logging.getLogger("valutatrade.actions")


def log_action(action=None, *, verbose=False):
    """Audit allowlisted fields only; record failures and re-raise the original exception."""

    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs).arguments
            service = bound.get("self")
            user = getattr(service, "current_user", None)
            fields = {
                "action": action or function.__name__.upper(),
                "user_id": user.user_id if user else None,
            }
            # Passwords, salts, positional reprs, and arbitrary kwargs must never be logged.
            for key in ("currency_code", "amount"):
                if key in bound:
                    fields[key] = str(bound[key])
            started = perf_counter()
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                fields.update(result="ERROR", error_type=type(exc).__name__, error_message=str(exc))
                logger.error(json.dumps(fields, ensure_ascii=False))
                raise
            fields["result"] = "OK"
            if isinstance(result, dict):
                for key in ("rate", "base", "user_id"):
                    if key in result:
                        fields[key] = str(result[key])
                if verbose:
                    fields["balances"] = result.get("balances")
            if verbose:
                fields["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
            logger.info(json.dumps(fields, ensure_ascii=False, default=str))
            return result

        return wrapped

    return decorate


def confirm_action(prompt):
    """Require an injected confirmation function; keep interactive input out of the domain."""

    def decorate(function):
        @wraps(function)
        def wrapped(*args, confirm, **kwargs):
            if not confirm(prompt):
                return None
            return function(*args, **kwargs)

        return wrapped

    return decorate
