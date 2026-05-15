"""Position monitor — thin wrapper that creates dependencies and delegates to position_lib."""

import logging
import os
import sys
from typing import Any

from utils.binance_client import create_client, get_wallet_target, load_coins_config

from monitor import live_position
from monitor.position_lib import display_table


def main(
    sort: str = "default",
    telegram: bool = False,
    hide_empty: bool = False,
    compact: bool = False,
    live: bool = False,
) -> None:
    try:
        client: Any = create_client()
    except Exception as e:
        logging.error(f"Failed to create Binance client: {e}")
        sys.exit(1)

    try:
        coins_config = load_coins_config()
        coin_order = list(coins_config.keys())
    except Exception as e:
        logging.error(f"Error loading config/coins.json: {e}")
        sys.exit(1)

    wallet_target, wallet_target_invalid = get_wallet_target()

    sort_key, _, sort_dir = sort.partition(":")
    sort_order = sort_dir.lower() != "asc"

    if live:
        if telegram:
            logging.warning(
                "--telegram is ignored in --live mode (would spam every poll cycle)"
            )
        live_position.run(
            client,
            coins_config,
            coin_order,
            wallet_target,
            wallet_target_invalid,
            sort_by=sort_key,
            descending=sort_order,
            hide_empty=hide_empty,
            compact=compact,
        )
        return

    os.system("cls" if os.name == "nt" else "clear")
    print("\U0001f4c8 Trades Position Snapshot \u2014 Buibui Moon Bot\n")
    print(
        display_table(
            client,
            coins_config,
            coin_order,
            wallet_target,
            wallet_target_invalid,
            sort_by=sort_key,
            descending=sort_order,
            telegram=telegram,
            hide_empty=hide_empty,
            compact=compact,
        )
    )
