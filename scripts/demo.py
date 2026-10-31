"""Record actual CLI execution with explicitly synthetic API responses, without a network."""

import json
import logging
import os
import shlex
import tempfile
from pathlib import Path
from unittest.mock import Mock

from valutatrade_hub.cli.interface import Console
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.logging_config import configure_logging
from valutatrade_hub.parser_service.api_clients import CoinGeckoClient, ExchangeRateApiClient
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.updater import RatesUpdater


def fixture_session(payload):
    """Provide deterministic HTTP responses while keeping real API adapter parsing."""
    response = Mock(status_code=200, headers={})
    response.json.return_value = payload
    return Mock(get=Mock(return_value=response))


def demo_updater(source=None):
    """Construct normal clients with synthetic responses and visibly marked sources."""
    crypto = CoinGeckoClient(
        session=fixture_session(
            {
                "bitcoin": {"usd": 60000},
                "ethereum": {"usd": 3000},
                "solana": {"usd": 150},
            }
        )
    )
    fiat = ExchangeRateApiClient(
        config=ParserConfig(EXCHANGERATE_API_KEY="DEMO-NOT-A-REAL-KEY"),
        session=fixture_session(
            {
                "result": "success",
                "base_code": "USD",
                "conversion_rates": {"EUR": 0.8, "GBP": 0.5, "RUB": 100},
            }
        ),
    )
    crypto.source, fiat.source = "CoinGecko (DEMO)", "ExchangeRate-API (DEMO)"
    return RatesUpdater(
        [
            client
            for name, client in (("coingecko", crypto), ("exchangerate", fiat))
            if source in (None, name)
        ]
    )


def main():
    """Generate an asciicast v2, plain transcript, and standalone HTML playback."""
    root = Path(__file__).resolve().parents[1]
    target = root / "docs"
    target.mkdir(exist_ok=True)
    frames, transcript = [], []
    elapsed = 0

    def output(value):
        nonlocal elapsed
        value = str(value)
        transcript.append(value)
        frames.append([round(elapsed, 2), "o", value.replace("\n", "\r\n") + "\r\n"])
        elapsed += min(0.3 + len(value) / 1800, 1.8)

    commands = [
        ("register --username demo --password demo1234", 0),
        ("login --username demo --password demo1234", 0),
        ("show-portfolio", 0),
        ("deposit --amount 1000", 0),
        ("update-rates", 0),
        ("show-rates --top 2", 0),
        ("buy --currency BTC --amount 0.01", 0),
        ("sell --currency BTC --amount 0.004", 0),
        ("show-portfolio", 0),
        ("get-rate --from USD --to BTC", 0),
        ("show-rates --currency RUB", 0),
        ("sell --currency BTC --amount 1", 1),
        ("get-rate --from ABC --to USD", 1),
        ("buy --currency ETH --amount -1", 1),
    ]
    with tempfile.TemporaryDirectory(prefix="valutatrade-demo-") as directory:
        # CLI data is isolated from all real users and removed after recording.
        os.environ["VALUTATRADE_HOME"] = directory
        os.environ["VALUTATRADE_DATA_DIR"] = str(Path(directory) / "data")
        os.environ["VALUTATRADE_LOG_DIR"] = str(Path(directory) / "logs")
        SettingsLoader._instance = DatabaseManager._instance = None
        configure_logging()
        DatabaseManager().initialize()
        console = Console(output=output, updater_factory=demo_updater)
        output("ValutaTrade Hub | OFFLINE DEMO | учебные курсы, не рыночные данные")
        for command, expected in commands:
            elapsed += 0.8
            output(f"valutatrade> {command}")
            status = console.execute(shlex.split(command))
            if status != expected:
                raise RuntimeError(f"Demo failed: {command}; status={status}")
        assert console.service.show_portfolio()["total"] == 1000
        output("Проверено: 640 USD + 0.006 BTC × 60000 = 1000 USD")
        # Windows will not remove a temporary directory while log files are open.
        for name in ("valutatrade.actions", "valutatrade.parser"):
            logger = logging.getLogger(name)
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)
    header = {
        "version": 2,
        "width": 120,
        "height": 36,
        "title": "ValutaTrade Hub — OFFLINE DEMO",
        "env": {"TERM": "xterm-256color", "SHELL": "valutatrade"},
    }
    (target / "demo.cast").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in [header, *frames]) + "\n",
        encoding="utf-8",
    )
    (target / "demo.txt").write_text("\n".join(transcript) + "\n", encoding="utf-8")
    player = """<!doctype html><html lang="ru"><meta charset="utf-8">
<title>ValutaTrade Hub — демонстрация</title>
<style>body{margin:24px;background:#121820;color:#e8f0f8;font:16px system-ui}
button{padding:10px 20px;cursor:pointer}pre{white-space:pre-wrap;background:#080c12;
padding:24px;border-radius:12px;line-height:1.5;min-height:70vh;font:15px Consolas,monospace}
</style><h1>ValutaTrade Hub</h1><p>Учебные курсы. Выполнение настоящего CLI
с подготовленными ответами API; сеть и ключи не используются.</p>
<button id="play">Повторить</button> <button id="all">Показать всё</button>
<pre id="screen"></pre><script>
const frames=FRAMES; const screen=document.getElementById('screen');let timer;
function play(){clearTimeout(timer);screen.textContent='';let i=0;
function tick(){screen.textContent+=frames[i][2];i++;
screen.scrollIntoView({block:'end',behavior:'smooth'});
if(i<frames.length)timer=setTimeout(tick,(frames[i][0]-frames[i-1][0])*1000);}tick();}
document.getElementById('play').onclick=play;
document.getElementById('all').onclick=()=>{clearTimeout(timer);
screen.textContent=frames.map(f=>f[2]).join('');};play();</script></html>"""
    (target / "demo.html").write_text(
        player.replace("FRAMES", json.dumps(frames, ensure_ascii=False)), encoding="utf-8"
    )
    print(f"Demo verified and saved: {target / 'demo.cast'}")


if __name__ == "__main__":
    main()
