"""Dual-Telegram channel router.

Two publishers share the existing `send_telegram_message` low-level send:

- **primary** — `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`. Receives the full
  trader-facing alert (LONG/SHORT, strategy reasons, warnings, edge, stats).
- **wife**    — `TELEGRAM_BOT_TOKEN_2` / `TELEGRAM_CHAT_ID_2`. Receives the
  minimal BUY/HOLD-relabelled alert produced by
  `signals.alert_formatter.format_wife_confluence_alert`.

Set `TELEGRAM_WIFE_DRY_RUN=1` to log the wife-variant message instead of
sending it (lets us roll out without spamming the wife channel).
"""

import logging
import os
from typing import Literal

from utils.telegram import send_telegram_message

Channel = Literal["primary", "wife"]

_PRIMARY_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
_PRIMARY_CHAT_ID_ENV = "TELEGRAM_CHAT_ID"
_WIFE_TOKEN_ENV = "TELEGRAM_BOT_TOKEN_2"
_WIFE_CHAT_ID_ENV = "TELEGRAM_CHAT_ID_2"
_WIFE_DRY_RUN_ENV = "TELEGRAM_WIFE_DRY_RUN"

logger = logging.getLogger(__name__)


def get_channel_credentials(channel: Channel) -> tuple[str | None, str | None]:
    """Read (bot_token, chat_id) from env for the given channel."""
    if channel == "primary":
        return os.getenv(_PRIMARY_TOKEN_ENV), os.getenv(_PRIMARY_CHAT_ID_ENV)
    return os.getenv(_WIFE_TOKEN_ENV), os.getenv(_WIFE_CHAT_ID_ENV)


def is_wife_dry_run() -> bool:
    """True when the wife channel is in dry-run mode (log instead of send)."""
    return os.getenv(_WIFE_DRY_RUN_ENV, "").strip().lower() in {"1", "true", "yes"}


def dispatch_to_channel(text: str, channel: Channel) -> bool:
    """Dispatch `text` to the given channel. Returns True iff the send was attempted live.

    - Missing credentials: skip silently (logs at INFO so unconfigured wife channel
      doesn't spam ERROR every cycle).
    - Wife in dry-run: log the message preview and return False.
    - Otherwise: call `send_telegram_message`; exceptions bubble to the caller.
    """
    token, chat_id = get_channel_credentials(channel)
    if not token or not chat_id:
        logger.info("Telegram %s channel not configured — skipping send.", channel)
        return False

    if channel == "wife" and is_wife_dry_run():
        preview = text.splitlines()[0] if text else ""
        logger.info(
            "Telegram wife dry-run (%d chars). First line: %s",
            len(text),
            preview,
        )
        return False

    send_telegram_message(text, bot_token=token, chat_id=chat_id)
    return True
