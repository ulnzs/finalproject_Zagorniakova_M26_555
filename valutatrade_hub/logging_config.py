"""UTF-8 rotating action and parser logs with UTC ISO timestamps."""

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from valutatrade_hub.infra.settings import SettingsLoader


def configure_logging():
    """Configure only application loggers; repeated calls never duplicate handlers."""
    settings = SettingsLoader()
    folder = Path(settings.get("LOG_DIR"))
    folder.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(levelname)s %(asctime)sZ %(message)s", "%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    for name, filename in (("actions", "actions.log"), ("parser", "parser.log")):
        logger = logging.getLogger(f"valutatrade.{name}")
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)
        handler = RotatingFileHandler(
            folder / filename,
            maxBytes=settings.get("LOG_MAX_BYTES"),
            backupCount=settings.get("LOG_BACKUP_COUNT"),
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(settings.get("LOG_LEVEL"))
        logger.propagate = False
