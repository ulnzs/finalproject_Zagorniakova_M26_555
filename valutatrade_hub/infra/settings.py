"""One configuration instance shared by all application layers."""

import os
import tomllib
from pathlib import Path
from threading import RLock


class SettingsLoader:
    """Singleton through __new__: explicit, readable, and no import-time instance."""

    _instance = None
    _lock = RLock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance.reload()
                cls._instance = instance
            return cls._instance

    def reload(self):
        """Read project configuration and apply environment path overrides."""
        root = Path(os.getenv("VALUTATRADE_HOME", Path.cwd())).resolve()
        config = root / "pyproject.toml"
        values = {
            "DATA_DIR": "data",
            "LOG_DIR": "logs",
            "RATES_TTL_SECONDS": 300,
            "BASE_CURRENCY": "USD",
            "LOG_LEVEL": "INFO",
            "LOG_MAX_BYTES": 1048576,
            "LOG_BACKUP_COUNT": 3,
        }
        if config.exists():
            with config.open("rb") as stream:
                values.update(tomllib.load(stream).get("tool", {}).get("valutatrade", {}))
        for key in ("DATA_DIR", "LOG_DIR"):
            path = Path(os.getenv(f"VALUTATRADE_{key}", values[key]))
            values[key] = path if path.is_absolute() else root / path
        if not isinstance(values["RATES_TTL_SECONDS"], (int, float)) or (
            values["RATES_TTL_SECONDS"] <= 0
        ):
            raise ValueError("RATES_TTL_SECONDS должен быть положительным")
        self._values = values

    def get(self, key, default=None):
        """Read a setting without exposing the mutable configuration dictionary."""
        return self._values.get(key, default)
