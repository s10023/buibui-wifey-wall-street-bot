"""Tests for utils/telegram.py."""

import logging
from typing import Any
from unittest.mock import MagicMock, call, patch

import requests as req

from utils.telegram import send_telegram_message

TOKEN = "test_token"
CHAT = "12345"

# Token-shaped on purpose: the leak these tests guard is that `requests` renders
# the request URL -- `/bot<TOKEN>/sendMessage` -- inside the exception string.
LEAK_TOKEN = "7654321:AAH-not-a-real-bot-token"


def _url_bearing_error(exc_type: type[Exception]) -> Exception:
    """An exception whose message embeds the bot token, the way requests' do."""
    return exc_type(
        "HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries "
        f"exceeded with url: /bot{LEAK_TOKEN}/sendMessage"
    )


def _http_error(status: int) -> req.HTTPError:
    response = MagicMock()
    response.status_code = status
    err = req.HTTPError(f"{status} error for url: /bot{LEAK_TOKEN}/sendMessage")
    err.response = response
    return err


class TestSendTelegramMessage:
    """Tests for send_telegram_message()."""

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_successful_send(self, mock_post: Any, mock_sleep: Any) -> None:
        """Message is sent with correct payload on first attempt."""
        send_telegram_message("Hello test", bot_token=TOKEN, chat_id=CHAT)

        mock_post.assert_called_once()
        mock_sleep.assert_not_called()
        call_kwargs = mock_post.call_args
        assert call_kwargs[0][0] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        payload = call_kwargs[1]["data"]
        assert payload["chat_id"] == CHAT
        assert payload["text"] == "Hello test"
        assert payload["parse_mode"] == "HTML"
        assert payload["disable_web_page_preview"] is True
        assert call_kwargs[1]["timeout"] == 10

    @patch("utils.telegram.requests.post")
    @patch("utils.telegram._get_env_credentials", return_value=(None, None))
    def test_missing_bot_token(self, _mock_creds: Any, mock_post: Any) -> None:
        """No HTTP call when bot_token is missing."""
        send_telegram_message("Hello", bot_token=None, chat_id=CHAT)
        mock_post.assert_not_called()

    @patch("utils.telegram.requests.post")
    @patch("utils.telegram._get_env_credentials", return_value=(None, None))
    def test_missing_chat_id(self, _mock_creds: Any, mock_post: Any) -> None:
        """No HTTP call when chat_id is missing."""
        send_telegram_message("Hello", bot_token=TOKEN, chat_id=None)
        mock_post.assert_not_called()

    @patch("utils.telegram.requests.post")
    def test_empty_credentials(self, mock_post: Any) -> None:
        """No HTTP call when credentials are empty strings."""
        send_telegram_message("Hello", bot_token="", chat_id="")
        mock_post.assert_not_called()

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_raise_for_status_called(self, mock_post: Any, mock_sleep: Any) -> None:
        """raise_for_status() is called on every attempt."""
        send_telegram_message("Hello", bot_token=TOKEN, chat_id=CHAT)
        assert mock_post.return_value.raise_for_status.call_count == 1

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_http_error_is_caught_not_raised(
        self, mock_post: Any, mock_sleep: Any
    ) -> None:
        """HTTPError from raise_for_status is caught — callers are not crashed."""
        mock_post.return_value.raise_for_status.side_effect = req.HTTPError(
            "403 Forbidden"
        )
        # Must not raise — error is logged and swallowed after all retries
        send_telegram_message("Hello", bot_token=TOKEN, chat_id=CHAT)

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post", side_effect=Exception("Network error"))
    def test_network_error_handled(self, mock_post: Any, mock_sleep: Any) -> None:
        """Network errors are caught and logged, not raised."""
        send_telegram_message("Hello", bot_token=TOKEN, chat_id=CHAT)

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post", side_effect=TimeoutError("Timeout"))
    def test_timeout_handled(self, mock_post: Any, mock_sleep: Any) -> None:
        """Timeout errors are caught and logged, not raised."""
        send_telegram_message("Hello", bot_token=TOKEN, chat_id=CHAT)

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    @patch(
        "utils.telegram._get_env_credentials", return_value=("env_token", "env_chat")
    )
    def test_falls_back_to_env(
        self, mock_creds: Any, mock_post: Any, mock_sleep: Any
    ) -> None:
        """Falls back to env vars when no explicit credentials passed."""
        send_telegram_message("Hello")

        mock_creds.assert_called_once()
        mock_post.assert_called_once()
        assert "env_token" in mock_post.call_args[0][0]
        assert mock_post.call_args[1]["data"]["chat_id"] == "env_chat"

    # --- Retry behaviour ---

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_success_on_first_attempt_no_retry(
        self, mock_post: Any, mock_sleep: Any
    ) -> None:
        """No sleep when message sends successfully on first try."""
        send_telegram_message("Hi", bot_token=TOKEN, chat_id=CHAT)
        mock_post.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_success_after_one_retry(self, mock_post: Any, mock_sleep: Any) -> None:
        """Succeeds on 2nd attempt; sleeps once with 1s delay."""
        # First call raises, second succeeds
        first_response = MagicMock()
        first_response.raise_for_status.side_effect = req.HTTPError("500 Server Error")
        first_response.response = MagicMock()
        first_response.response.status_code = 500
        second_response = MagicMock()
        second_response.raise_for_status.return_value = None

        mock_post.side_effect = [first_response, second_response]

        send_telegram_message("Hi", bot_token=TOKEN, chat_id=CHAT)

        assert mock_post.call_count == 2
        mock_sleep.assert_called_once_with(1)

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_raises_after_max_retries_exhausted(
        self, mock_post: Any, mock_sleep: Any
    ) -> None:
        """After 3 failed attempts, error is logged and function returns without raising."""
        mock_post.return_value.raise_for_status.side_effect = req.HTTPError("500")
        # Must not raise to callers
        send_telegram_message("Hi", bot_token=TOKEN, chat_id=CHAT)
        assert mock_post.call_count == 3
        assert mock_sleep.call_count == 2
        mock_sleep.assert_has_calls([call(1), call(2)])

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_400_logs_message_preview(
        self, mock_post: Any, mock_sleep: Any, caplog: Any
    ) -> None:
        """400 Bad Request logs a preview of the message text."""
        import logging

        bad_response = MagicMock()
        bad_response.status_code = 400
        http_err = req.HTTPError("400 Bad Request")
        http_err.response = bad_response
        mock_post.return_value.raise_for_status.side_effect = http_err

        with caplog.at_level(logging.WARNING, logger="root"):
            send_telegram_message("Special chars: *_[]", bot_token=TOKEN, chat_id=CHAT)

        assert any(
            "400" in r.message and "Special chars" in r.message for r in caplog.records
        )


class TestTokenRedaction:
    """The bot token must never reach a log record.

    `requests` puts the request URL into every exception it raises, and that URL
    is `https://api.telegram.org/bot<TOKEN>/sendMessage` -- so logging the bare
    exception writes a live credential to the terminal or the journal. There are
    THREE such sites, and each test below covers exactly one.
    """

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_http_error_log_is_redacted(
        self, mock_post: Any, mock_sleep: Any, caplog: Any
    ) -> None:
        """Site 1: the per-attempt HTTPError warning (non-400 branch)."""
        mock_post.return_value.raise_for_status.side_effect = _http_error(500)

        with caplog.at_level(logging.WARNING, logger="root"):
            send_telegram_message("Hi", bot_token=LEAK_TOKEN, chat_id=CHAT)

        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert LEAK_TOKEN not in blob
        assert "<REDACTED>" in blob

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_connection_error_log_is_redacted(
        self, mock_post: Any, mock_sleep: Any, caplog: Any
    ) -> None:
        """Site 2: the generic-exception warning.

        This is the site the upstream fix (parent #579) left open -- it redacted
        the HTTPError paths only. A ConnectionError or Timeout renders the same
        URL, so it leaks identically.
        """
        mock_post.side_effect = _url_bearing_error(req.ConnectionError)

        with caplog.at_level(logging.WARNING, logger="root"):
            send_telegram_message("Hi", bot_token=LEAK_TOKEN, chat_id=CHAT)

        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert LEAK_TOKEN not in blob
        assert "<REDACTED>" in blob

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_final_failure_log_is_redacted(
        self, mock_post: Any, mock_sleep: Any, caplog: Any
    ) -> None:
        """Site 3: the ERROR logged once every attempt is exhausted."""
        mock_post.side_effect = _url_bearing_error(req.Timeout)

        with caplog.at_level(logging.ERROR, logger="root"):
            send_telegram_message("Hi", bot_token=LEAK_TOKEN, chat_id=CHAT)

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, "expected a final-failure ERROR record"
        blob = "\n".join(r.getMessage() for r in errors)
        assert LEAK_TOKEN not in blob
        assert "<REDACTED>" in blob

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_redaction_is_a_no_op_without_a_token(
        self, mock_post: Any, mock_sleep: Any, caplog: Any
    ) -> None:
        """Positive control: a message with no token still reaches the log intact.

        Guards the other direction -- a `_redact` that blanked the whole string
        would pass all three tests above while destroying every diagnostic.
        """
        mock_post.side_effect = req.ConnectionError("name resolution failed")

        with caplog.at_level(logging.WARNING, logger="root"):
            send_telegram_message("Hi", bot_token=LEAK_TOKEN, chat_id=CHAT)

        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert "name resolution failed" in blob


class TestParseModeFallback:
    """A 400 is a rejected payload, so retrying the identical body cannot work.

    The usual trigger is `parse_mode: HTML`: any text carrying a bare `<` -- a
    Python traceback's `line 33, in <module>` is the canonical one -- is read by
    Telegram's HTML parser as an unclosed tag. That makes an alert about a crash
    fail on precisely the crashes it exists to report.
    """

    @staticmethod
    def _payloads(mock_post: Any) -> list[dict[str, object]]:
        return [c.kwargs["data"] for c in mock_post.call_args_list]

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_400_drops_parse_mode_on_retry(
        self, mock_post: Any, mock_sleep: Any
    ) -> None:
        mock_post.return_value.raise_for_status.side_effect = _http_error(400)

        send_telegram_message("crash at <module>", bot_token=TOKEN, chat_id=CHAT)

        payloads = self._payloads(mock_post)
        assert len(payloads) == 3
        assert payloads[0].get("parse_mode") == "HTML"
        assert "parse_mode" not in payloads[1]
        assert "parse_mode" not in payloads[2]

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_500_keeps_parse_mode_on_retry(
        self, mock_post: Any, mock_sleep: Any
    ) -> None:
        """Scoping control: a 500 IS transient, so the retry stays faithful.

        Without this, "drop parse_mode on any failure" would pass the 400 test
        above and silently strip formatting from every recoverable send.
        """
        mock_post.return_value.raise_for_status.side_effect = _http_error(500)

        send_telegram_message("Hi", bot_token=TOKEN, chat_id=CHAT)

        assert all(p.get("parse_mode") == "HTML" for p in self._payloads(mock_post))

    @patch("utils.telegram.time.sleep")
    @patch("utils.telegram.requests.post")
    def test_earlier_attempts_are_not_rewritten(
        self, mock_post: Any, mock_sleep: Any
    ) -> None:
        """The payload is rebuilt per attempt, never mutated in place.

        A single shared dict would leave `requests` -- and this mock -- holding a
        reference to it, so dropping parse_mode on attempt 2 would retroactively
        rewrite what attempt 1 appears to have sent. The assertion that attempt 1
        still carries parse_mode is what falsifies the mutate-in-place variant.
        """
        mock_post.return_value.raise_for_status.side_effect = _http_error(400)

        send_telegram_message("crash at <module>", bot_token=TOKEN, chat_id=CHAT)

        assert self._payloads(mock_post)[0].get("parse_mode") == "HTML"
