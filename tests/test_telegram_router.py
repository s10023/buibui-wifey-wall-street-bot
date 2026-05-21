"""Tests for utils/telegram_router.py — dual-channel dispatcher."""

import logging
from typing import Any
from unittest.mock import patch

from utils.telegram_router import (
    dispatch_to_channel,
    get_channel_credentials,
    is_wife_dry_run,
)


class TestGetChannelCredentials:
    """Channel selects the right env var pair."""

    def test_primary_reads_legacy_envs(self) -> None:
        with patch.dict(
            "os.environ",
            {"TELEGRAM_BOT_TOKEN": "primary_tok", "TELEGRAM_CHAT_ID": "primary_chat"},
            clear=False,
        ):
            assert get_channel_credentials("primary") == ("primary_tok", "primary_chat")

    def test_wife_reads_suffixed_envs(self) -> None:
        with patch.dict(
            "os.environ",
            {"TELEGRAM_BOT_TOKEN_2": "wife_tok", "TELEGRAM_CHAT_ID_2": "wife_chat"},
            clear=False,
        ):
            assert get_channel_credentials("wife") == ("wife_tok", "wife_chat")

    def test_missing_returns_none_pair(self) -> None:
        with patch.dict(
            "os.environ",
            {"TELEGRAM_BOT_TOKEN_2": "", "TELEGRAM_CHAT_ID_2": ""},
            clear=False,
        ):
            # Empty strings are returned as-is; dispatcher treats them as missing.
            token, chat_id = get_channel_credentials("wife")
            assert not token
            assert not chat_id


class TestIsWifeDryRun:
    def test_unset_is_false(self) -> None:
        with patch.dict("os.environ", {"TELEGRAM_WIFE_DRY_RUN": ""}, clear=False):
            assert is_wife_dry_run() is False

    def test_truthy_values(self) -> None:
        for val in ("1", "true", "True", "yes", " 1 "):
            with patch.dict("os.environ", {"TELEGRAM_WIFE_DRY_RUN": val}, clear=False):
                assert is_wife_dry_run() is True, f"Expected truthy for {val!r}"

    def test_falsy_values(self) -> None:
        for val in ("0", "false", "no", ""):
            with patch.dict("os.environ", {"TELEGRAM_WIFE_DRY_RUN": val}, clear=False):
                assert is_wife_dry_run() is False, f"Expected falsy for {val!r}"


class TestDispatchToChannel:
    @patch("utils.telegram_router.send_telegram_message")
    def test_primary_uses_legacy_envs(self, mock_send: Any) -> None:
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "primary_tok",
                "TELEGRAM_CHAT_ID": "primary_chat",
            },
            clear=False,
        ):
            sent = dispatch_to_channel("hello", "primary")
        assert sent is True
        mock_send.assert_called_once_with(
            "hello", bot_token="primary_tok", chat_id="primary_chat"
        )

    @patch("utils.telegram_router.send_telegram_message")
    def test_wife_uses_suffixed_envs(self, mock_send: Any) -> None:
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN_2": "wife_tok",
                "TELEGRAM_CHAT_ID_2": "wife_chat",
                "TELEGRAM_WIFE_DRY_RUN": "",
            },
            clear=False,
        ):
            sent = dispatch_to_channel("hello", "wife")
        assert sent is True
        mock_send.assert_called_once_with(
            "hello", bot_token="wife_tok", chat_id="wife_chat"
        )

    @patch("utils.telegram_router.send_telegram_message")
    def test_wife_dry_run_skips_send(self, mock_send: Any, caplog: Any) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "TELEGRAM_BOT_TOKEN_2": "wife_tok",
                    "TELEGRAM_CHAT_ID_2": "wife_chat",
                    "TELEGRAM_WIFE_DRY_RUN": "1",
                },
                clear=False,
            ),
            caplog.at_level(logging.INFO, logger="utils.telegram_router"),
        ):
            sent = dispatch_to_channel("hello dry-run\nsecond line", "wife")
        assert sent is False
        mock_send.assert_not_called()
        assert any("dry-run" in r.message for r in caplog.records)

    @patch("utils.telegram_router.send_telegram_message")
    def test_missing_wife_creds_skip_silently(
        self, mock_send: Any, caplog: Any
    ) -> None:
        with (
            patch.dict(
                "os.environ",
                {"TELEGRAM_BOT_TOKEN_2": "", "TELEGRAM_CHAT_ID_2": ""},
                clear=False,
            ),
            caplog.at_level(logging.INFO, logger="utils.telegram_router"),
        ):
            sent = dispatch_to_channel("hello", "wife")
        assert sent is False
        mock_send.assert_not_called()
        assert any("not configured" in r.message for r in caplog.records)

    @patch("utils.telegram_router.send_telegram_message")
    def test_primary_dry_run_env_does_not_affect_primary(self, mock_send: Any) -> None:
        """TELEGRAM_WIFE_DRY_RUN must not gate the primary channel."""
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "primary_tok",
                "TELEGRAM_CHAT_ID": "primary_chat",
                "TELEGRAM_WIFE_DRY_RUN": "1",
            },
            clear=False,
        ):
            sent = dispatch_to_channel("hello", "primary")
        assert sent is True
        mock_send.assert_called_once()
