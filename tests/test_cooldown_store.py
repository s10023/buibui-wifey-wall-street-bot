"""Tests for signals/cooldown_store.py — candle watermark dedup store."""

import json
from typing import Any

from signals.cooldown_store import CooldownStore


class TestCandleWatermark:
    def test_unknown_symbol_is_new_candle(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        assert store.is_new_candle("BTCUSDT", "1h", "fvg", 1_000) is True

    def test_mark_then_same_open_time_is_not_new(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "1h", "fvg", 1_000)
        assert store.is_new_candle("BTCUSDT", "1h", "fvg", 1_000) is False

    def test_older_open_time_is_not_new(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "1h", "fvg", 2_000)
        assert store.is_new_candle("BTCUSDT", "1h", "fvg", 1_000) is False

    def test_newer_open_time_is_new(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "1h", "fvg", 1_000)
        assert store.is_new_candle("BTCUSDT", "1h", "fvg", 2_000) is True

    def test_different_strategy_is_independent(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "1h", "fvg", 1_000)
        assert store.is_new_candle("BTCUSDT", "1h", "bos", 1_000) is True

    def test_different_symbol_is_independent(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "1h", "fvg", 1_000)
        assert store.is_new_candle("ETHUSDT", "1h", "fvg", 1_000) is True

    def test_different_timeframe_is_independent(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("BTCUSDT", "1h", "fvg", 1_000)
        assert store.is_new_candle("BTCUSDT", "4h", "fvg", 1_000) is True


class TestJsonPersistence:
    def test_watermark_persists_across_instances(self, tmp_path: Any) -> None:
        path = str(tmp_path / "state.json")
        store1 = CooldownStore(path)
        store1.mark_candle("BTCUSDT", "1h", "fvg", 5_000)

        store2 = CooldownStore(path)
        assert store2.is_new_candle("BTCUSDT", "1h", "fvg", 5_000) is False

    def test_state_file_is_valid_json(self, tmp_path: Any) -> None:
        path = tmp_path / "state.json"
        store = CooldownStore(str(path))
        store.mark_candle("BTCUSDT", "1h", "fvg", 1_000)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "watermarks" in data
        assert data["watermarks"]["BTCUSDT:1h:fvg"] == 1_000

    def test_missing_file_starts_empty(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "nonexistent.json"))
        assert store.is_new_candle("BTCUSDT", "1h", "fvg", 1_000) is True

    def test_corrupted_file_starts_empty(self, tmp_path: Any) -> None:
        path = tmp_path / "state.json"
        path.write_text("not valid json {{{{", encoding="utf-8")
        store = CooldownStore(str(path))
        assert store.is_new_candle("BTCUSDT", "1h", "fvg", 1_000) is True

    def test_legacy_state_file_with_cooldowns_key_loads_cleanly(
        self, tmp_path: Any
    ) -> None:
        """Existing signal_state.json files may have a 'cooldowns' key — must not crash."""
        path = tmp_path / "state.json"
        path.write_text(
            json.dumps(
                {
                    "watermarks": {"BTCUSDT:1h:fvg": 5_000},
                    "cooldowns": {"BTCUSDT:fvg:long": 9_999_999_999.0},
                }
            ),
            encoding="utf-8",
        )
        store = CooldownStore(str(path))
        assert store.is_new_candle("BTCUSDT", "1h", "fvg", 5_000) is False
        assert store.is_new_candle("BTCUSDT", "1h", "fvg", 6_000) is True


class TestPerChannelWatermarks:
    """Wife channel watermark moves independently from primary."""

    def test_wife_and_primary_are_independent(self, tmp_path: Any) -> None:
        store = CooldownStore(str(tmp_path / "state.json"))
        store.mark_candle("AAPL", "1d", "engulfing", 1_000, channel="primary")
        # Wife channel has not seen this candle yet.
        assert (
            store.is_new_candle("AAPL", "1d", "engulfing", 1_000, channel="wife")
            is True
        )

    def test_primary_default_keeps_legacy_key_shape(self, tmp_path: Any) -> None:
        """Primary keys MUST stay `{sym}:{tf}:{strategy}` so live state files load."""
        path = tmp_path / "state.json"
        store = CooldownStore(str(path))
        store.mark_candle("AAPL", "1d", "engulfing", 1_000)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "AAPL:1d:engulfing" in data["watermarks"]

    def test_wife_key_uses_channel_suffix(self, tmp_path: Any) -> None:
        path = tmp_path / "state.json"
        store = CooldownStore(str(path))
        store.mark_candle("AAPL", "1d", "engulfing", 2_000, channel="wife")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "AAPL:1d:engulfing:wife" in data["watermarks"]
        assert data["watermarks"]["AAPL:1d:engulfing:wife"] == 2_000
