"""Price monitor — thin wrapper that creates dependencies and delegates to price_lib."""

import logging
import sys
from typing import Any

from colorama import init
from tabulate import tabulate
from utils.binance_client import create_client, load_coins_config

from monitor import live_price
from monitor.price_lib import (
    PRICE_HEADERS,
    VALID_SORT_COLS,
    clear_screen,
    get_price_changes,
    sort_table,
)
from utils.telegram import send_telegram_message

init(autoreset=True)


def main(live: bool = False, telegram: bool = False, sort: str = "") -> None:
    try:
        client: Any = create_client()
    except Exception as e:
        logging.error(f"Failed to create Binance client: {e}")
        sys.exit(1)

    try:
        coins = list(load_coins_config().keys())
    except Exception as e:
        logging.error(f"Error loading config/coins.json: {e}")
        sys.exit(1)

    sort_col, _, sort_dir = sort.partition(":")
    sort_order = sort_dir.lower() != "asc"

    if live:
        live_price.run(client, coins, sort_col=sort_col, sort_order=sort_order)
        return

    clear_screen()
    print("\U0001f4c8 Crypto Price Snapshot \u2014 Buibui Moon Bot\n")
    price_table, invalid_symbols = get_price_changes(client, coins, telegram=telegram)
    if sort_col in VALID_SORT_COLS:
        price_table = sort_table(price_table, PRICE_HEADERS, sort_col, sort_order)

    print(tabulate(price_table, headers=PRICE_HEADERS, tablefmt="fancy_grid"))

    if sort_col in VALID_SORT_COLS:
        arrow = "\U0001f53d" if sort_order else "\U0001f53c"
        direction = "descending" if sort_order else "ascending"
        print(f"\n{arrow} Sorted by: {sort_col} ({direction})")

    if invalid_symbols:
        print("\n\u26a0\ufe0f  The following symbols had errors:")
        for symbol, reason in sorted(invalid_symbols):
            print(f"  - {symbol}: {reason}")

    if telegram:
        plain_table = tabulate(price_table, headers=PRICE_HEADERS, tablefmt="plain")
        try:
            send_telegram_message(
                f"\U0001f4c8 Snapshot Price Monitor\n```\n{plain_table}\n```"
            )
        except Exception as e:
            print("\u274c Telegram message failed:", e)
