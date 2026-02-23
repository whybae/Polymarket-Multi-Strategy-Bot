"""
main.py
-------
Launches one or multiple Polymarket bots in parallel threads.

On startup, interactively asks:
  1. Which strategy  (DCA Snipe | YES+NO Arbitrage)
  2. Which markets   (btc, eth, sol, xrp — one or many)
  3. Which interval  (5m | 15m — YES+NO only)

Each bot runs in its own thread. Threads auto-restart on crash.
Ctrl+C shuts down all bots gracefully.

Non-interactive usage:
    python main.py --strategy dca   --operate btc eth
    python main.py --strategy dca   --operate all
    python main.py --strategy yesno --operate btc sol --interval 15m
"""

import argparse
import importlib
import importlib.util
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# ── Project root ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level   = logging.INFO,
    format  = "[%(asctime)s][%(levelname)s][%(name)s] - %(message)s",
    datefmt = "%H:%M:%S",
)
log = logging.getLogger("main")

# ── Constants ──────────────────────────────────────────────────────────────────
AVAILABLE_MARKETS = ["btc", "eth", "sol", "xrp"]

# For DCA: standard dot-path import (directory name is valid Python)
# For YES+NO: file-based import via spec_from_file_location ('+' in dir name)
STRATEGIES = {
    "dca": {
        "label"      : "DCA Snipe  — entry arming + bracket orders + optional DCA",
        "import_mode": "module",
        "modules": {
            "btc": "strategies.DCA_Snipe.markets.btc.bot",
            "eth": "strategies.DCA_Snipe.markets.eth.bot",
            "sol": "strategies.DCA_Snipe.markets.sol.bot",
            "xrp": "strategies.DCA_Snipe.markets.xrp.bot",
        },
    },
    "yesno": {
        "label"      : "YES+NO Arbitrage  — buy UP+DOWN inside PRICE_RANGE for < $1.00",
        "import_mode": "path",   # uses spec_from_file_location ('+' in folder name)
        "paths": {
            "btc": ROOT / "strategies" / "YES+NO_1usd" / "markets" / "btc" / "bot.py",
            "eth": ROOT / "strategies" / "YES+NO_1usd" / "markets" / "eth" / "bot.py",
            "sol": ROOT / "strategies" / "YES+NO_1usd" / "markets" / "sol" / "bot.py",
            "xrp": ROOT / "strategies" / "YES+NO_1usd" / "markets" / "xrp" / "bot.py",
        },
    },
}

# Shared shutdown flag
_shutdown = threading.Event()


# ══════════════════════════════════════════════════════════════════════════════
#  MODULE LOADER
# ══════════════════════════════════════════════════════════════════════════════

def load_bot_module(strategy_key: str, market: str):
    """
    Load a bot module by dot-path (DCA) or file path (YES+NO).
    Returns the loaded module object.
    """
    strategy = STRATEGIES[strategy_key]

    if strategy["import_mode"] == "module":
        dot_path = strategy["modules"][market]
        try:
            return importlib.import_module(dot_path)
        except ImportError as exc:
            raise ImportError(
                f"Cannot import {dot_path}.\n"
                f"  Make sure strategies/DCA_Snipe/markets/{market}/ exists.\n"
                f"  Original error: {exc}"
            )

    else:  # path-based (YES+NO)
        file_path = strategy["paths"][market]
        if not file_path.exists():
            raise FileNotFoundError(
                f"Bot file not found: {file_path}\n"
                f"  Run setup.py to verify your project structure."
            )
        spec   = importlib.util.spec_from_file_location(f"yesno_{market}", file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


# ══════════════════════════════════════════════════════════════════════════════
#  INTERACTIVE PROMPTS
# ══════════════════════════════════════════════════════════════════════════════

def ask_strategy() -> str:
    """Returns 'dca' or 'yesno'."""
    choices = [
        ("DCA Snipe  — entry arming + bracket orders + optional DCA",   "dca"),
        ("YES+NO Arbitrage  — buy UP+DOWN inside PRICE_RANGE for < $1.00", "yesno"),
    ]
    try:
        import questionary
        answer = questionary.select(
            "Select strategy:",
            choices=[label for label, _ in choices],
        ).ask()
        if answer is None:
            sys.exit(0)
        return dict(choices)[answer]
    except (ImportError, Exception):
        pass

    print("\nAvailable strategies:")
    for i, (label, _) in enumerate(choices, 1):
        print(f"  {i}) {label}")
    while True:
        raw = input("Select (1 or 2): ").strip()
        if raw == "1": return "dca"
        if raw == "2": return "yesno"
        print("  Please enter 1 or 2.")


def ask_markets() -> list[str]:
    """Returns list of selected market keys."""
    try:
        import questionary
        answers = questionary.checkbox(
            "Select markets:  (Space = toggle, Enter = confirm)",
            choices=[
                questionary.Choice("BTC — Bitcoin",  value="btc", checked=True),
                questionary.Choice("ETH — Ethereum", value="eth", checked=False),
                questionary.Choice("SOL — Solana",   value="sol", checked=False),
                questionary.Choice("XRP — Ripple",   value="xrp", checked=False),
            ],
        ).ask()
        if not answers:
            print("No markets selected — exiting.")
            sys.exit(0)
        return answers
    except (ImportError, Exception):
        pass

    print(f"\nAvailable markets: {', '.join(AVAILABLE_MARKETS)}")
    print("Enter markets separated by spaces, or 'all':")
    while True:
        raw = input("  → ").strip().lower()
        if not raw:
            print("  At least one market required.")
            continue
        if raw == "all":
            return list(AVAILABLE_MARKETS)
        selected = raw.split()
        invalid  = [m for m in selected if m not in AVAILABLE_MARKETS]
        if invalid:
            print(f"  Unknown: {', '.join(invalid)} — try again.")
            continue
        return selected


def ask_interval() -> str:
    """Returns '5m' or '15m'. Only used by YES+NO strategy."""
    try:
        import questionary
        choice = questionary.select(
            "Select market interval:",
            choices=["5 minutes", "15 minutes"],
        ).ask()
        if choice is None:
            sys.exit(0)
        return "15m" if "15" in choice else "5m"
    except (ImportError, Exception):
        pass

    while True:
        raw = input("Market interval — enter 5 or 15: ").strip()
        if raw in ("5", "15"):
            return f"{raw}m"
        print("  Please enter 5 or 15.")


# ══════════════════════════════════════════════════════════════════════════════
#  BOT THREAD
# ══════════════════════════════════════════════════════════════════════════════

class BotThread(threading.Thread):
    """
    Runs a single market bot in a daemon thread.
    Auto-restarts on unhandled exceptions until shutdown is signalled.
    """

    RESTART_DELAY = 10  # seconds between crash and restart

    def __init__(self, market: str, strategy_key: str, run_kwargs: dict):
        super().__init__(name=f"bot-{market}", daemon=True)
        self.market       = market
        self.strategy_key = strategy_key
        self.run_kwargs   = run_kwargs

    def run(self):
        try:
            module = load_bot_module(self.strategy_key, self.market)
            log.info(f"[{self.market.upper()}] Module loaded OK.")
        except Exception as exc:
            log.error(f"[{self.market.upper()}] Failed to load module: {exc}")
            return

        while not _shutdown.is_set():
            try:
                log.info(f"[{self.market.upper()}] Starting bot ...")
                module.run(**self.run_kwargs)
                log.info(f"[{self.market.upper()}] Bot exited cleanly.")
                break
            except Exception as exc:
                if _shutdown.is_set():
                    break
                log.error(
                    f"[{self.market.upper()}] Crashed: {exc} — "
                    f"restarting in {self.RESTART_DELAY}s ..."
                )
                _shutdown.wait(timeout=self.RESTART_DELAY)

        log.info(f"[{self.market.upper()}] Thread stopped.")


# ══════════════════════════════════════════════════════════════════════════════
#  SIGNAL HANDLER
# ══════════════════════════════════════════════════════════════════════════════

def _handle_shutdown(signum, frame):
    log.info("Shutdown signal received — stopping all bots ...")
    _shutdown.set()


# ══════════════════════════════════════════════════════════════════════════════
#  CLI ARGS
# ══════════════════════════════════════════════════════════════════════════════

def parse_args():
    parser = argparse.ArgumentParser(
        description="Polymarket Trading Asset Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples (non-interactive):
  python main.py --strategy dca   --operate btc eth
  python main.py --strategy dca   --operate all
  python main.py --strategy yesno --operate btc sol --interval 15m
  python main.py --strategy yesno --operate all     --interval 5m
        """,
    )
    parser.add_argument("--strategy", choices=["dca", "yesno"], default=None)
    parser.add_argument("--operate",  nargs="+", metavar="MARKET",  default=None)
    parser.add_argument("--interval", choices=["5m", "15m"],        default=None)
    return parser.parse_args()


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    args = parse_args()

    print("\n" + "=" * 60)
    print("  Polymarket Trading Asset Bot")
    print("=" * 60)

    # ── Strategy ───────────────────────────────────────────────────────────
    strategy_key = args.strategy or ask_strategy()

    # ── Markets ────────────────────────────────────────────────────────────
    if args.operate:
        markets = (
            list(AVAILABLE_MARKETS)
            if "all" in [m.lower() for m in args.operate]
            else [m.lower() for m in args.operate]
        )
        invalid = [m for m in markets if m not in AVAILABLE_MARKETS]
        if invalid:
            log.error(f"Unknown market(s): {', '.join(invalid)}")
            sys.exit(1)
    else:
        markets = ask_markets()

    # ── Interval (YES+NO only) ─────────────────────────────────────────────
    run_kwargs = {}
    if strategy_key == "yesno":
        interval           = args.interval or ask_interval()
        run_kwargs["interval"] = interval

    # ── Startup banner ─────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info(f"Strategy : {STRATEGIES[strategy_key]['label']}")
    log.info(f"Markets  : {', '.join(m.upper() for m in markets)}")
    if strategy_key == "yesno":
        log.info(f"Interval : {run_kwargs['interval']}")
    log.info("=" * 60)

    # ── Signals ────────────────────────────────────────────────────────────
    signal.signal(signal.SIGINT,  _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    # ── Launch threads ─────────────────────────────────────────────────────
    threads: list[BotThread] = []
    for market in markets:
        t = BotThread(
            market       = market,
            strategy_key = strategy_key,
            run_kwargs   = run_kwargs,
        )
        t.start()
        threads.append(t)
        time.sleep(0.5)  # stagger to avoid simultaneous API calls at startup

    log.info(f"{len(threads)} bot thread(s) running. Press Ctrl+C to stop.")

    # ── Monitor ────────────────────────────────────────────────────────────
    try:
        while not _shutdown.is_set():
            dead = [t for t in threads if not t.is_alive()]
            for t in dead:
                log.warning(f"[{t.market.upper()}] Thread is no longer alive.")
            if not any(t.is_alive() for t in threads):
                log.info("All bot threads have stopped.")
                break
            _shutdown.wait(timeout=5)
    except KeyboardInterrupt:
        _shutdown.set()

    # ── Graceful shutdown ──────────────────────────────────────────────────
    log.info("Waiting for all threads to stop ...")
    for t in threads:
        t.join(timeout=15)
        if t.is_alive():
            log.warning(f"[{t.market.upper()}] Thread did not stop in time.")

    log.info("All bots stopped. Goodbye.")


if __name__ == "__main__":
    main()