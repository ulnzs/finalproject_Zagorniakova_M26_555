"""Errors that can be safely presented to console users."""


class ValutaTradeError(Exception):
    """Base application error."""


class InsufficientFundsError(ValutaTradeError):
    """A wallet cannot cover the requested withdrawal."""

    def __init__(self, available, required, code):
        super().__init__(
            f"Недостаточно средств: доступно {available} {code}, требуется {required} {code}"
        )


class CurrencyNotFoundError(ValutaTradeError):
    """The currency registry does not contain a code."""

    def __init__(self, code):
        super().__init__(f"Неизвестная валюта '{code}'")


class ApiRequestError(ValutaTradeError):
    """Market data cannot be retrieved or used."""

    def __init__(self, reason):
        super().__init__(f"Ошибка при обращении к внешнему API: {reason}")


class StorageError(ValutaTradeError):
    """Local data could not be read or persisted safely."""


class AuthenticationError(ValutaTradeError):
    """An operation needs a valid authenticated session."""
