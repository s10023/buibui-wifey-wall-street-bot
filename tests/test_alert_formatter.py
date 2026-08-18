"""Tests for _get_session_label in alert_formatter."""

from datetime import UTC, datetime

import pandas as pd

from signals.alert_formatter import (
    SignalEvent,
    StatsContext,
    _format_stats_line,
    _get_session_label,
    _reason_detail,
    format_signal_alert,
    format_wife_alert,
)

_UTC = UTC


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2024, 1, 15, hour, minute, tzinfo=_UTC)


def _utc_summer(hour: int, minute: int = 0) -> datetime:
    """2024-07-15 is in EDT (UTC-4) — tests DST handling."""
    return datetime(2024, 7, 15, hour, minute, tzinfo=_UTC)


class TestGetSessionLabel:
    # US equity sessions in ET (DST-aware via America/New_York):
    #   Pre-Market   04:00–09:30 ET
    #   RTH          09:30–15:00 ET
    #   Power Hour   15:00–16:00 ET
    #   After Hours  16:00–20:00 ET
    # Winter (EST, UTC-5) — these tests use Jan 15 UTC, which lands the ET hour at UTC-5.

    # --- Pre-Market: 04:00–09:30 ET ---
    # 04:00 ET (EST) = 09:00 UTC  |  09:30 ET (EST) = 14:30 UTC (exclusive end)

    def test_pre_market_start(self) -> None:
        # 09:00 UTC = 04:00 ET (EST) — Pre-Market start
        assert _get_session_label(_utc(9, 0)) == "Pre-Market"

    def test_pre_market_mid(self) -> None:
        # 12:00 UTC = 07:00 ET (EST)
        assert _get_session_label(_utc(12, 0)) == "Pre-Market"

    def test_pre_market_end_minute(self) -> None:
        # 14:29 UTC = 09:29 ET (EST) — last minute of Pre-Market
        assert _get_session_label(_utc(14, 29)) == "Pre-Market"

    # --- RTH: 09:30–15:00 ET ---
    # 09:30 ET (EST) = 14:30 UTC  |  15:00 ET (EST) = 20:00 UTC (exclusive end)

    def test_rth_start_at_open(self) -> None:
        # 14:30 UTC = 09:30 ET (EST) — RTH cash open
        assert _get_session_label(_utc(14, 30)) == "RTH"

    def test_rth_mid(self) -> None:
        # 17:00 UTC = 12:00 ET (EST) — midday
        assert _get_session_label(_utc(17, 0)) == "RTH"

    def test_rth_end_minute(self) -> None:
        # 19:59 UTC = 14:59 ET (EST) — last minute of RTH before Power Hour
        assert _get_session_label(_utc(19, 59)) == "RTH"

    # --- Power Hour: 15:00–16:00 ET ---
    # 15:00 ET (EST) = 20:00 UTC  |  16:00 ET (EST) = 21:00 UTC (exclusive end)

    def test_power_hour_start(self) -> None:
        # 20:00 UTC = 15:00 ET (EST) — Power Hour start
        assert _get_session_label(_utc(20, 0)) == "Power Hour"

    def test_power_hour_mid(self) -> None:
        # 20:30 UTC = 15:30 ET (EST)
        assert _get_session_label(_utc(20, 30)) == "Power Hour"

    def test_power_hour_end_minute(self) -> None:
        # 20:59 UTC = 15:59 ET (EST) — last minute of Power Hour
        assert _get_session_label(_utc(20, 59)) == "Power Hour"

    # --- After Hours: 16:00–20:00 ET ---
    # 16:00 ET (EST) = 21:00 UTC  |  20:00 ET (EST) = 01:00 UTC next day (exclusive end)

    def test_after_hours_start(self) -> None:
        # 21:00 UTC = 16:00 ET (EST) — After Hours start
        assert _get_session_label(_utc(21, 0)) == "After Hours"

    def test_after_hours_mid(self) -> None:
        # 23:00 UTC = 18:00 ET (EST)
        assert _get_session_label(_utc(23, 0)) == "After Hours"

    # --- Outside (overnight 20:00–04:00 ET) ---

    def test_outside_late_night(self) -> None:
        # 03:00 UTC = 22:00 ET (EST) on prior day in ET — overnight, no label
        assert _get_session_label(_utc(3, 0)) == ""

    def test_outside_early_morning(self) -> None:
        # 08:59 UTC = 03:59 ET (EST) — last minute before Pre-Market opens
        assert _get_session_label(_utc(8, 59)) == ""

    # --- DST: Summer 2024 (EDT, UTC-4) ---
    # Same ET clock times, but ET = UTC-4, so the equivalent UTC hours shift by 1.
    #   Pre-Market   08:00–13:30 UTC
    #   RTH          13:30–19:00 UTC
    #   Power Hour   19:00–20:00 UTC
    #   After Hours  20:00–00:00 UTC

    def test_pre_market_summer_dst(self) -> None:
        # 12:00 UTC on 2024-07-15 = 08:00 EDT — inside Pre-Market
        assert _get_session_label(_utc_summer(12, 0)) == "Pre-Market"

    def test_rth_open_summer_dst(self) -> None:
        # 13:30 UTC on 2024-07-15 = 09:30 EDT — RTH cash open
        assert _get_session_label(_utc_summer(13, 30)) == "RTH"

    def test_power_hour_summer_dst(self) -> None:
        # 19:30 UTC on 2024-07-15 = 15:30 EDT — inside Power Hour
        assert _get_session_label(_utc_summer(19, 30)) == "Power Hour"

    def test_after_hours_summer_dst(self) -> None:
        # 22:00 UTC on 2024-07-15 = 18:00 EDT — inside After Hours
        assert _get_session_label(_utc_summer(22, 0)) == "After Hours"


class TestFormatSignalAlertSessionTag:
    def _make_event(self, open_time_ms: int) -> SignalEvent:
        return SignalEvent(
            symbol="AAPL",
            timeframe="4h",
            strategy="fvg",
            direction="long",
            reason="fvg_long@200.00-202.00",
            open_time=open_time_ms,
            price=201.50,
            sl_price=198.00,
        )

    def test_session_tag_pre_market(self) -> None:
        # 2024-01-15 13:00 UTC = 08:00 ET (EST) — Pre-Market
        dt = datetime(2024, 1, 15, 13, 0, tzinfo=_UTC)
        ts_ms = int(dt.timestamp() * 1000)
        msg = format_signal_alert(self._make_event(ts_ms))
        assert "🌅 Pre-Market" in msg
        assert "Kill Zone" not in msg

    def test_session_tag_rth(self) -> None:
        # 2024-01-15 14:30 UTC = 09:30 ET (EST) — RTH cash open
        dt = datetime(2024, 1, 15, 14, 30, tzinfo=_UTC)
        ts_ms = int(dt.timestamp() * 1000)
        msg = format_signal_alert(self._make_event(ts_ms))
        assert "🏛️ RTH" in msg
        assert "Kill Zone" not in msg

    def test_session_tag_power_hour(self) -> None:
        # 2024-01-15 20:30 UTC = 15:30 ET (EST) — Power Hour
        dt = datetime(2024, 1, 15, 20, 30, tzinfo=_UTC)
        ts_ms = int(dt.timestamp() * 1000)
        msg = format_signal_alert(self._make_event(ts_ms))
        assert "⚡ Power Hour" in msg

    def test_session_tag_after_hours(self) -> None:
        # 2024-01-15 23:00 UTC = 18:00 ET (EST) — After Hours
        dt = datetime(2024, 1, 15, 23, 0, tzinfo=_UTC)
        ts_ms = int(dt.timestamp() * 1000)
        msg = format_signal_alert(self._make_event(ts_ms))
        assert "🌙 After Hours" in msg

    def test_no_session_tag_outside_windows(self) -> None:
        # 2024-01-15 03:00 UTC = 22:00 ET (EST prior day) — overnight, closed
        dt = datetime(2024, 1, 15, 3, 0, tzinfo=_UTC)
        ts_ms = int(dt.timestamp() * 1000)
        msg = format_signal_alert(self._make_event(ts_ms))
        for label in ("Pre-Market", "RTH", "Power Hour", "After Hours"):
            assert label not in msg


class TestCashtagHeader:
    """The header renders the ticker as $TICKER (fintwit cashtag convention)."""

    _RTH_TS_MS = int(datetime(2024, 1, 15, 17, 0, tzinfo=_UTC).timestamp() * 1000)

    def _event(self, symbol: str) -> SignalEvent:
        return SignalEvent(
            symbol=symbol,
            timeframe="4h",
            strategy="fvg",
            direction="long",
            reason="fvg_long@200.00-202.00",
            open_time=self._RTH_TS_MS,
            price=201.50,
            sl_price=198.00,
        )

    def test_cashtag_prefix_in_header(self) -> None:
        msg = format_signal_alert(self._event("AAPL"))
        assert "SIGNAL — $AAPL 4h" in msg

    def test_cashtag_for_multi_letter_ticker(self) -> None:
        msg = format_signal_alert(self._event("GOOGL"))
        assert "SIGNAL — $GOOGL 4h" in msg


class TestStatsContextFormat:
    """Tests for _format_stats_line with new plain-English 2-line format."""

    def _ctx(
        self,
        dow: str = "Monday",
        p1_low: float = 0.69,
        bull: float = 0.67,
        adr: float = 0.043,
        consumed: float | None = 0.82,
        peak_hi_dow: int | None = 23,
        peak_lo_dow: int | None = 8,
        wk_low: float | None = 0.78,
        wk_high: float | None = 0.65,
        wk_low_cond: float | None = None,
        wk_high_cond: float | None = None,
        wk_bucket: str | None = None,
    ) -> StatsContext:
        return StatsContext(
            today_dow=dow,
            p1_low_pct_today=p1_low,
            adr_14=adr,
            adr_consumed_pct=consumed,
            peak_high_hour_myt=14,
            peak_low_hour_myt=8,
            bull_pct_today=bull,
            avg_return_today=0.01,
            peak_high_hour_dow=peak_hi_dow,
            peak_low_hour_dow=peak_lo_dow,
            wk_low_still_ahead_pct=wk_low,
            wk_high_still_ahead_pct=wk_high,
            wk_low_still_ahead_conditioned_pct=wk_low_cond,
            wk_high_still_ahead_conditioned_pct=wk_high_cond,
            wk_move_bucket=wk_bucket,
        )

    def test_long_line1_contains_bull_p1_adr(self) -> None:
        line = _format_stats_line(self._ctx(), "long")
        assert "Mon closes bullish 67%" in line
        # p1_low=0.69 → still_ahead for long = 1-0.69 = 31%
        assert "Low still ahead 31% of Mondays" in line
        assert (
            "4.3%" in line
        )  # ADR value present (bar separates "ADR" prefix from value)
        assert "82%" in line

    def test_long_line2_peak_and_weekly(self) -> None:
        line = _format_stats_line(self._ctx(), "long")
        assert "TP window: high ~23:00 MYT on Mondays" in line
        assert "Weekly low: 78% still ahead" in line

    def test_short_line1_high_first(self) -> None:
        line = _format_stats_line(self._ctx(), "short")
        # p1_low=0.69 → still_ahead for short = p1_low = 69%
        assert "High still ahead 69% of Mondays" in line

    def test_short_line2_low_peak_and_weekly_high(self) -> None:
        line = _format_stats_line(self._ctx(), "short")
        assert "TP window: low ~08:00 MYT on Mondays" in line
        assert "Weekly high: 65% still ahead" in line

    def test_no_line2_when_optional_fields_none(self) -> None:
        ctx = self._ctx(peak_hi_dow=None, peak_lo_dow=None, wk_low=None, wk_high=None)
        line = _format_stats_line(ctx, "long")
        assert "🎯" not in line

    def test_adr_consumed_none(self) -> None:
        ctx = self._ctx(consumed=None)
        line = _format_stats_line(ctx, "long")
        assert "used" not in line

    def test_two_lines_separated_by_newline(self) -> None:
        line = _format_stats_line(self._ctx(), "long")
        lines = line.split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("📐")
        assert lines[1].startswith("🎯")

    def test_long_uses_conditioned_pct_over_unconditional(self) -> None:
        ctx = self._ctx(wk_low=0.78, wk_low_cond=0.62, wk_bucket="medium")
        line = _format_stats_line(ctx, "long")
        assert "Weekly low: 62% still ahead (medium move)" in line
        assert "78%" not in line

    def test_short_uses_conditioned_pct_over_unconditional(self) -> None:
        ctx = self._ctx(wk_high=0.65, wk_high_cond=0.44, wk_bucket="large")
        line = _format_stats_line(ctx, "short")
        assert "Weekly high: 44% still ahead (large move)" in line
        assert "65%" not in line

    def test_falls_back_to_unconditional_when_conditioned_none(self) -> None:
        # conditioned fields absent — should fall back to plain unconditional
        ctx = self._ctx(wk_low=0.78, wk_low_cond=None, wk_bucket=None)
        line = _format_stats_line(ctx, "long")
        assert "Weekly low: 78% still ahead" in line
        assert "move)" not in line

    def test_conditioned_pct_requires_bucket_to_activate(self) -> None:
        # conditioned pct present but bucket missing → fall back to unconditional
        ctx = self._ctx(wk_low=0.78, wk_low_cond=0.62, wk_bucket=None)
        line = _format_stats_line(ctx, "long")
        assert "Weekly low: 78% still ahead" in line
        assert "62%" not in line


class TestLowVolumeWarning:
    _TS_MS = int(datetime(2024, 1, 15, 11, 0, tzinfo=_UTC).timestamp() * 1000)

    def _make_event(self, low_volume: bool) -> SignalEvent:
        return SignalEvent(
            symbol="BTCUSDT",
            timeframe="1h",
            strategy="engulfing",
            direction="long",
            reason="bullish_engulfing@43000.00",
            open_time=self._TS_MS,
            price=43000.0,
            sl_price=42000.0,
            low_volume=low_volume,
        )

    def test_low_volume_true_shows_warning(self) -> None:
        msg = format_signal_alert(self._make_event(low_volume=True))
        assert "⚠️ Low volume — weaker conviction" in msg

    def test_low_volume_false_no_warning(self) -> None:
        msg = format_signal_alert(self._make_event(low_volume=False))
        assert "⚠️ Low volume — weaker conviction" not in msg


class TestWifeAlert:
    """BUY / WAIT wife-channel formatter — the primary layout, condensed.

    Design: `docs/superpowers/specs/2026-08-18-wife-alert-layout-design.md`.
    """

    def _event(
        self,
        direction: str,
        low_volume: bool = False,
        volume_spike: bool = False,
    ) -> SignalEvent:
        # 2024-01-15 14:30 UTC = 09:30 ET = 22:30 MYT (no DST in MYT)
        dt = datetime(2024, 1, 15, 14, 30, tzinfo=_UTC)
        return SignalEvent(
            symbol="AAPL",
            timeframe="1d",
            strategy="engulfing",
            direction=direction,
            reason="bullish_engulfing",
            open_time=int(dt.timestamp() * 1000),
            price=200.00,
            sl_price=196.00 if direction == "long" else 204.00,
            confidence=4,
            tp_price=0.0,
            low_volume=low_volume,
            volume_spike=volume_spike,
        )

    def _inside_bar_df(self) -> pd.DataFrame:
        """Prior bar engulfs the signal bar — fires the inside-range warning."""
        return pd.DataFrame(
            [
                {"open": 190.0, "high": 210.0, "low": 185.0, "close": 205.0},
                {"open": 199.0, "high": 201.0, "low": 198.0, "close": 200.0},
            ]
        )

    def test_long_renders_buy_with_stop_and_target(self) -> None:
        msg = format_wife_alert(self._event("long"))
        assert "BUY — $AAPL 1d" in msg
        assert "Entry 200.00" in msg
        assert "Stop 196.00" in msg
        assert "Target " in msg

    def test_buy_header_carries_stars(self) -> None:
        msg = format_wife_alert(self._event("long"))
        assert "★★★★☆" in msg

    def test_buy_uses_signed_percentages_not_r_multiples(self) -> None:
        """R is trader jargon; a signed percentage is directly readable."""
        msg = format_wife_alert(self._event("long"))
        assert "(−2.0%)" in msg
        assert "(+4.0%)" in msg
        assert "R)" not in msg
        assert "SL:" not in msg and "TP:" not in msg

    def test_short_renders_wait_without_levels(self) -> None:
        """WAIT, not HOLD: 'hold' presumes an open position she may not have."""
        msg = format_wife_alert(self._event("short"))
        assert "WAIT — $AAPL 1d" in msg
        assert "Sit tight — conditions look weak" in msg
        assert "HOLD" not in msg
        for token in ("Stop", "Target", "★", "SHORT"):
            assert token not in msg

    def test_no_primary_channel_content_leaks(self) -> None:
        """Everything dropped stays recoverable from `signal_alert_outcomes`."""
        msg = format_wife_alert(self._event("long"))
        for token in ("engulfing", "bullish_engulfing", "SIGNAL", "LONG", "Backtest"):
            assert token not in msg

    def test_buy_uses_html_bold(self) -> None:
        """Telegram parse_mode=HTML — header tag must survive."""
        msg = format_wife_alert(self._event("long"))
        assert msg.startswith("<b>BUY")
        assert "</b>" in msg

    def test_carries_at_most_one_warning_the_most_severe(self) -> None:
        """Low volume outranks the structural note, and only one is rendered."""
        msg = format_wife_alert(
            self._event("long", low_volume=True), ohlcv_df=self._inside_bar_df()
        )
        primary = format_signal_alert(
            self._event("long", low_volume=True), ohlcv_df=self._inside_bar_df()
        )
        assert primary.count("⚠️") > 1, "fixture must fire more than one warning"
        assert msg.count("⚠️") == 1
        assert "Low volume — weaker conviction" in msg
        assert "Signal inside prior range" not in msg

    def test_volume_spike_never_renders(self) -> None:
        """It is an encouragement, and it is the builder's first entry — so
        taking the head of the list would render it under a warning heading."""
        msg = format_wife_alert(self._event("long", volume_spike=True))
        assert "Volume spike" not in msg
        assert "⚡" not in msg

    def test_without_a_frame_only_the_volume_note_can_appear(self) -> None:
        """The frame is not all-or-nothing: volume comes off the event flags."""
        assert "Low volume" in format_wife_alert(self._event("long", low_volume=True))
        assert "⚠️" not in format_wife_alert(self._event("long"))


class TestReasonDetail:
    """`_reason_detail` strips what the alert already states elsewhere.

    The header carries the strategy and the direction; the entry line carries
    the price. What is left is the variant that actually fired.
    """

    def _event(self, strategy: str, reason: str, price: float) -> SignalEvent:
        return SignalEvent(
            symbol="AAPL",
            timeframe="4h",
            strategy=strategy,
            direction="long",
            reason=reason,
            open_time=0,
            price=price,
        )

    def test_drops_entry_price_and_direction(self) -> None:
        ev = self._event("ema", "ema_pullback_long@333.85", 333.85)
        assert _reason_detail(ev) == "pullback"

    def test_empty_when_reason_adds_nothing(self) -> None:
        """`doji_bull@326.99` is strategy + direction + entry, all repeats."""
        ev = self._event("doji", "doji_bull@326.99", 326.99)
        assert _reason_detail(ev) == ""

    def test_strips_strategy_named_as_a_suffix(self) -> None:
        ev = self._event("engulfing", "bullish_engulfing@200.00", 200.00)
        assert _reason_detail(ev) == ""

    def test_keeps_a_variant_that_merely_contains_the_strategy(self) -> None:
        """`evening_star` names which half of the pattern fired."""
        ev = self._event("morning_evening_star", "evening_star@306.47", 306.47)
        assert _reason_detail(ev) == "evening_star"

    def test_keeps_a_zone_whose_price_is_not_the_entry(self) -> None:
        """A blunt `@`-strip would delete the upper bound and leave `-307.23`."""
        ev = self._event("order_block", "ob_long@303.27-307.23", 305.00)
        assert _reason_detail(ev) == "ob@303.27-307.23"

    def test_keeps_a_trailing_parenthetical(self) -> None:
        ev = self._event(
            "fibonacci_retracement",
            "fib_golden_zone@333.85 (0.618=326.10)",
            333.85,
        )
        assert _reason_detail(ev) == "fib_golden_zone (0.618=326.10)"

    def test_direction_stripped_ahead_of_a_retained_level(self) -> None:
        ev = self._event("bos", "bos_choch_short@300.00", 312.00)
        assert _reason_detail(ev) == "choch@300.00"

    def test_direction_kept_when_stripping_would_dangle_the_level(self) -> None:
        """`short@305.67` must not become a bare `@305.67`."""
        ev = self._event("orb", "orb_short@305.67", 312.00)
        assert _reason_detail(ev) == "short@305.67"
