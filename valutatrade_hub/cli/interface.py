"""Argument parsing and presentation; business rules live in core/usecases.py."""

import argparse
import getpass
import shlex
import sys

from prettytable import PrettyTable

from valutatrade_hub.core.currencies import REGISTRY
from valutatrade_hub.core.exceptions import ApiRequestError, CurrencyNotFoundError, ValutaTradeError
from valutatrade_hub.core.usecases import TradingService
from valutatrade_hub.decorators import confirm_action
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.logging_config import configure_logging
from valutatrade_hub.parser_service.updater import create_updater


class CommandParser(argparse.ArgumentParser):
    """Report command usage errors without terminating the interactive session."""

    def error(self, message):
        """Convert argparse errors into user-facing validation failures."""
        raise ValueError(message)


def build_parser():
    """Define the complete CLI grammar in one place."""
    parser = CommandParser(prog="valutatrade", description="ValutaTrade Hub — виртуальный кошелёк")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("register", "login"):
        p = sub.add_parser(command)
        p.add_argument("--username", required=True)
        p.add_argument("--password", help="Если не указан — скрытый ввод")
    p = sub.add_parser("show-portfolio")
    p.add_argument("--base", default="USD")
    for command in ("buy", "sell"):
        p = sub.add_parser(command)
        p.add_argument("--currency", required=True)
        p.add_argument("--amount", required=True)
    p = sub.add_parser("deposit", help="Пополнить виртуальный USD-кошелёк")
    p.add_argument("--amount", required=True)
    p = sub.add_parser("get-rate")
    p.add_argument("--from", dest="from_code", required=True)
    p.add_argument("--to", dest="to_code", required=True)
    p = sub.add_parser("update-rates")
    p.add_argument("--source", choices=["coingecko", "exchangerate"])
    p = sub.add_parser("show-rates")
    p.add_argument("--currency")
    p.add_argument("--top", type=int)
    p.add_argument("--base", default="USD")
    for command in ("currencies", "logout", "exit"):
        sub.add_parser(command)
    p = sub.add_parser("help")
    p.add_argument("topic", nargs="?")
    return parser


@confirm_action("Завершить работу? [y/N]: ")
def confirmed_exit():
    """Signal exit only after the confirmation decorator accepts it."""
    return "exit"


class Console:
    """One interactive session with injectable input/output and updater for tests/demo."""

    def __init__(self, service=None, *, output=print, reader=input, updater_factory=create_updater):
        self.service = service or TradingService()
        self.output = output
        self.reader = reader
        self.updater_factory = updater_factory
        self.parser = build_parser()

    def execute(self, arguments):
        """Execute one argument vector; return a status code or the exit sentinel."""
        try:
            args = self.parser.parse_args(arguments)
            return self._dispatch(args)
        except SystemExit as exc:
            return exc.code  # --help must not kill the REPL.
        except CurrencyNotFoundError as exc:
            self.output(f"{exc}. Поддерживаются: {', '.join(REGISTRY)}. См. help get-rate")
        except ApiRequestError as exc:
            self.output(f"{exc}. Проверьте сеть и ключ API; повторите позже")
        except (ValutaTradeError, ValueError) as exc:
            self.output(str(exc))
        except (OSError, EOFError):
            self.output("Не удалось выполнить ввод/вывод. Проверьте доступ к файлам и терминалу")
        return 1

    def _table(self, headers, rows):
        table = PrettyTable(headers)
        for row in rows:
            table.add_row(row)
        self.output(str(table))

    def _dispatch(self, args):
        command = args.command
        service = self.service
        if command in ("register", "login"):
            password = args.password if args.password is not None else getpass.getpass("Пароль: ")
            info = getattr(service, command)(args.username, password)
            if command == "register":
                self.output(
                    f"Пользователь '{info['username']}' зарегистрирован "
                    f"(id={info['user_id']}). Выполните login"
                )
            else:
                self.output(f"Вы вошли как '{info['username']}'")
        elif command == "logout":
            service.logout()
            self.output("Вы вышли из учётной записи")
        elif command == "deposit":
            result = service.deposit(args.amount)
            self.output(f"Виртуальный кошелёк пополнен на {result['amount']} USD")
        elif command in ("buy", "sell"):
            result = getattr(service, command)(args.currency, args.amount)
            label = "Покупка" if command == "buy" else "Продажа"
            self.output(
                f"{label} выполнена: {result['amount']} {result['currency_code']} "
                f"по курсу {result['rate']} USD; стоимость {result['value']:.2f} USD"
            )
            for code, balance in result["balances"]["after"].items():
                self.output(f"{code}: {result['balances']['before'][code]} → {balance}")
        elif command == "show-portfolio":
            result = service.show_portfolio(args.base)
            self.output(f"Портфель пользователя '{result['username']}' (база: {result['base']})")
            if not result["rows"]:
                self.output("Портфель пуст. Пополните виртуальный баланс командой deposit")
            self._table(
                ["Валюта", "Баланс", f"Стоимость ({result['base']})"],
                [(c, str(b), f"{v:.2f}") for c, b, v in result["rows"]],
            )
            self.output(f"ИТОГО: {result['total']:.2f} {result['base']}")
        elif command == "get-rate":
            rate, stamp = service.get_rate(args.from_code, args.to_code)
            self.output(
                f"Курс {args.from_code.upper()}→{args.to_code.upper()}: {rate:.10g} "
                f"(обновлено: {stamp})"
            )
            self.output(f"Обратный курс: {1 / rate:.10g}")
        elif command == "update-rates":
            result = self.updater_factory(args.source).run_update()
            self.output(f"Обновлено курсов: {result['updated']}; время: {result['last_refresh']}")
            for error in result["errors"]:
                self.output(f"Источник не обновлён: {error}")
            return int(bool(result["errors"]))
        elif command == "show-rates":
            result = service.show_rates(args.currency, args.top, args.base)
            self._table(
                ["Пара", "Курс", "Обновлено (UTC)"],
                [(f"{c}_{result['base']}", f"{r:.10g}", t) for c, r, t in result["rows"]],
            )
        elif command == "currencies":
            for currency in REGISTRY.values():
                self.output(currency.get_display_info())
        elif command == "help":
            self.parser.parse_args([args.topic, "--help"] if args.topic else ["--help"])
        elif command == "exit":
            return confirmed_exit(
                confirm=lambda prompt: self.reader(prompt).lower() in ("y", "yes")
            )
        return 0

    def run(self):
        """Read portable command lines with shlex; never execute OS shell commands."""
        self.output("ValutaTrade Hub. help — команды; exit — выход. Все средства виртуальные.")
        while True:
            try:
                line = self.reader("valutatrade> ")
                if not line.strip():
                    continue
                arguments = shlex.split(line, posix=True)
                if self.execute(arguments) == "exit":
                    return 0
            except ValueError as exc:
                self.output(f"Ошибка команды: {exc}")
            except (EOFError, KeyboardInterrupt):
                self.output("\nРабота завершена")
                return 0


def main(argv=None):
    """Launch an interactive session or run a single stateless command."""
    try:
        configure_logging()
        DatabaseManager().initialize()
        arguments = sys.argv[1:] if argv is None else argv
        console = Console()
        return console.execute(arguments) if arguments else console.run()
    except (ValutaTradeError, OSError, ValueError) as exc:
        print(f"Не удалось запустить приложение: {exc}")
        return 1
