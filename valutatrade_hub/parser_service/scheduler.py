"""Standalone parser entry point with optional periodic foreground execution."""

import argparse
import logging
import time

from valutatrade_hub.core.exceptions import ValutaTradeError
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.logging_config import configure_logging
from valutatrade_hub.parser_service.updater import create_updater


def main(argv=None):
    """Update once by default; --interval N repeats until Ctrl+C."""
    parser = argparse.ArgumentParser(description="ValutaTrade Parser Service")
    parser.add_argument("--source", choices=["coingecko", "exchangerate"])
    parser.add_argument("--interval", type=float)
    args = parser.parse_args(argv)
    if args.interval is not None and args.interval <= 0:
        parser.error("--interval должен быть положительным")
    configure_logging()
    DatabaseManager().initialize()
    updater = create_updater(args.source)
    try:
        while True:
            try:
                report = updater.run_update()
                print(f"Обновлено курсов: {report['updated']}; ошибок: {len(report['errors'])}")
                status = int(bool(report["errors"]))
            except ValutaTradeError as exc:
                logging.getLogger("valutatrade.parser").error("%s", exc)
                print(exc)
                status = 1
            if args.interval is None:
                return status
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
