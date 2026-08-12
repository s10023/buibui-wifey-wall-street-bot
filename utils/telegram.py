import logging
import os
import time

import requests

_MAX_RETRIES = 3
_RETRY_DELAYS = [1, 2]  # seconds between attempt 1→2 and 2→3


def _get_env_credentials() -> tuple[str | None, str | None]:
    """Read Telegram credentials from environment."""
    return os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")


def _redact(value: object, bot_token: str | None) -> str:
    """Strip the bot token out of anything headed for a log.

    Both `requests.HTTPError` and the connection/timeout errors embed the full
    request URL in their `__str__`, and that URL carries the bot token -- so
    logging the bare exception writes a live credential into the terminal or
    journal. Verified here rather than assumed: a `ConnectionError` raised
    against `api.telegram.org.invalid` renders as
    ``Max retries exceeded with url: /bot<TOKEN>/sendMessage``.
    """
    text = str(value)
    if bot_token:
        text = text.replace(bot_token, "<REDACTED>")
    return text


def _status_of(err: requests.HTTPError) -> int | None:
    return err.response.status_code if err.response is not None else None


def _log_http_failure(
    err: requests.HTTPError, attempt: int, text: str, bot_token: str | None = None
) -> None:
    status = _status_of(err)
    if status == 400:
        preview = text[:200].replace("\n", " ")
        logging.warning(
            "Telegram 400 Bad Request (attempt %d/%d). Message preview: %r",
            attempt,
            _MAX_RETRIES,
            preview,
        )
    else:
        logging.warning(
            "Telegram HTTP error %s (attempt %d/%d): %s",
            status,
            attempt,
            _MAX_RETRIES,
            _redact(err, bot_token),
        )


def send_telegram_message(
    text: str,
    bot_token: str | None = None,
    chat_id: str | None = None,
) -> None:
    if bot_token is None or chat_id is None:
        env_token, env_chat_id = _get_env_credentials()
        bot_token = bot_token or env_token
        chat_id = chat_id or env_chat_id

    if not bot_token or not chat_id:
        logging.error("Telegram not configured properly.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    # The payload is rebuilt per attempt rather than mutated in place. Sharing one
    # dict across retries means `requests` (and any test double) holds a reference
    # to it, so dropping parse_mode below would retroactively rewrite what the
    # earlier attempts appear to have sent -- state that has already left the
    # function changing under it.
    use_parse_mode = True

    def _payload() -> dict[str, object]:
        body: dict[str, object] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if use_parse_mode:
            body["parse_mode"] = "HTML"
        return body

    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = requests.post(url, data=_payload(), timeout=10)
            response.raise_for_status()
            return
        except requests.HTTPError as e:
            last_exc = e
            _log_http_failure(e, attempt, text, bot_token)
            # A 400 from sendMessage is a REJECTED PAYLOAD, not a transient
            # error -- resending the identical body can never succeed. In
            # practice the trigger is almost always parse_mode: a Python
            # traceback carries `line 33, in <module>`, which Telegram's HTML
            # parser reads as an unclosed tag. That makes the failure alert fail
            # on precisely the crashes it exists to report.
            #
            # Dropping the formatting costs bold/italic in the retry and gains
            # delivery. Scoped to 400 on purpose: a 500 IS transient, so the
            # retry there must stay faithful to the original payload.
            if _status_of(e) == 400 and use_parse_mode:
                use_parse_mode = False
                logging.warning(
                    "Telegram 400 -- retrying as plain text (parse_mode dropped)"
                )
        except Exception as e:
            last_exc = e
            # Redacted for the same reason as the HTTPError branch, and this is
            # the leak site the upstream fix left open: a ConnectionError or
            # Timeout renders the request URL -- token included -- just as an
            # HTTPError does.
            logging.warning(
                "Telegram request failed (attempt %d/%d): %s",
                attempt,
                _MAX_RETRIES,
                _redact(e, bot_token),
            )

        if attempt < _MAX_RETRIES:
            delay = _RETRY_DELAYS[attempt - 1]
            logging.info("Retrying Telegram send in %ds...", delay)
            time.sleep(delay)

    logging.error(
        "\u274c Failed to send Telegram message after %d attempts: %s",
        _MAX_RETRIES,
        _redact(last_exc, bot_token),
    )
