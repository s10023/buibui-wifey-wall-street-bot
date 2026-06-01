"""Pure backtest simulation engine.

Accepts OHLCV DataFrame and signals DataFrame, simulates trades, and returns
a BacktestResult with statistics.

No database access, no network calls. No module-level side effects.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from analytics.backtest.gates import _is_low_volume, _is_volume_spike
from analytics.backtest.live_parity_config import LiveParityConfig

if TYPE_CHECKING:
    from analytics.signal.types import SignalEvent
    from analytics.signal_config import BiasConfig, StrategyOverride

logger = logging.getLogger(__name__)

_LIVE_PARITY_GATE_ORDER = (
    "regime",
    "direction_filter",
    "f8_htf_ema",
    "adr_bias",
    "conflict_resolver",
    "cooldown",
)


def _compute_atr14(
    highs: np.ndarray[Any, np.dtype[np.float64]],
    lows: np.ndarray[Any, np.dtype[np.float64]],
    closes: np.ndarray[Any, np.dtype[np.float64]],
    idx: int,
) -> float | None:
    """Return ATR14 at candle idx (mean true range over up to 14 candles ending at idx).

    True Range at candle i: max(high-low, |high-prev_close|, |low-prev_close|).
    Returns None when idx < 1 (no prior close for TR) or when ATR is zero.
    """
    if idx < 1:
        return None
    start = max(1, idx - 13)  # need prev_close, so minimum start is 1
    tr_vals: list[float] = []
    for i in range(start, idx + 1):
        hl = float(highs[i]) - float(lows[i])
        hc = abs(float(highs[i]) - float(closes[i - 1]))
        lc = abs(float(lows[i]) - float(closes[i - 1]))
        tr_vals.append(max(hl, hc, lc))
    if not tr_vals:
        return None
    atr = sum(tr_vals) / len(tr_vals)
    return atr if atr > 0.0 else None


@dataclass
class Trade:
    """A single simulated trade."""

    signal_time: int
    entry_time: int
    entry_price: float
    direction: str  # "long" | "short"
    sl_price: float
    tp_price: float
    exit_time: int | None = None
    exit_price: float | None = None
    outcome: str = "open"  # "win" | "loss" | "open"
    fee_pct: float = 0.0
    low_volume: bool = False  # True when signal candle volume < 1.5× rolling mean
    volume_spike: bool = False  # True when signal candle volume > 3× rolling mean

    @property
    def pnl_r(self) -> float | None:
        """P&L expressed in R multiples (1R = amount risked), after fees.

        Fee drag: each leg (entry + exit) costs fee_pct of notional.
        Position is sized to risk 1R at the actual SL distance, so:
          fee_drag_r = 2 * fee_pct * entry_price / risk

        This correctly penalises tight-SL strategies (e.g. wick_fill on 15m)
        where fees consume a large fraction of the actual risk taken.
        """
        if self.exit_price is None:
            return None
        risk = abs(self.entry_price - self.sl_price)
        if risk == 0.0:
            return None
        if self.direction == "long":
            raw_r = (self.exit_price - self.entry_price) / risk
        else:
            raw_r = (self.entry_price - self.exit_price) / risk
        fee_drag_r = 2.0 * self.fee_pct * self.entry_price / risk
        return raw_r - fee_drag_r


@dataclass
class BacktestResult:
    """Aggregated results for a strategy backtest."""

    symbol: str
    timeframe: str
    strategy: str
    fee_pct: float = 0.0
    trades: list[Trade] = field(default_factory=list)

    @functools.cached_property
    def closed_trades(self) -> list[Trade]:
        return [t for t in self.trades if t.outcome != "open"]

    @functools.cached_property
    def long_closed_trades(self) -> list[Trade]:
        return [t for t in self.closed_trades if t.direction == "long"]

    @functools.cached_property
    def short_closed_trades(self) -> list[Trade]:
        return [t for t in self.closed_trades if t.direction == "short"]

    @property
    def win_count(self) -> int:
        return sum(1 for t in self.closed_trades if t.outcome == "win")

    @property
    def loss_count(self) -> int:
        return sum(1 for t in self.closed_trades if t.outcome == "loss")

    @property
    def win_rate(self) -> float:
        closed = len(self.closed_trades)
        return self.win_count / closed if closed > 0 else 0.0

    @property
    def long_win_count(self) -> int:
        return sum(1 for t in self.long_closed_trades if t.outcome == "win")

    @property
    def long_win_rate(self) -> float | None:
        n = len(self.long_closed_trades)
        return self.long_win_count / n if n > 0 else None

    @property
    def long_avg_r(self) -> float | None:
        r_values = [t.pnl_r for t in self.long_closed_trades if t.pnl_r is not None]
        return sum(r_values) / len(r_values) if r_values else None

    @property
    def short_win_count(self) -> int:
        return sum(1 for t in self.short_closed_trades if t.outcome == "win")

    @property
    def short_win_rate(self) -> float | None:
        n = len(self.short_closed_trades)
        return self.short_win_count / n if n > 0 else None

    @property
    def short_avg_r(self) -> float | None:
        r_values = [t.pnl_r for t in self.short_closed_trades if t.pnl_r is not None]
        return sum(r_values) / len(r_values) if r_values else None

    @property
    def avg_r(self) -> float:
        r_values = [t.pnl_r for t in self.closed_trades if t.pnl_r is not None]
        return sum(r_values) / len(r_values) if r_values else 0.0

    @property
    def total_r(self) -> float:
        r_values = [t.pnl_r for t in self.closed_trades if t.pnl_r is not None]
        return sum(r_values)

    @property
    def long_total_r(self) -> float:
        r_values = [t.pnl_r for t in self.long_closed_trades if t.pnl_r is not None]
        return sum(r_values)

    @property
    def short_total_r(self) -> float:
        r_values = [t.pnl_r for t in self.short_closed_trades if t.pnl_r is not None]
        return sum(r_values)

    @functools.cached_property
    def low_vol_closed_trades(self) -> list[Trade]:
        return [t for t in self.closed_trades if t.low_volume]

    @functools.cached_property
    def normal_vol_closed_trades(self) -> list[Trade]:
        return [
            t for t in self.closed_trades if not t.low_volume and not t.volume_spike
        ]

    @functools.cached_property
    def spike_vol_closed_trades(self) -> list[Trade]:
        return [t for t in self.closed_trades if t.volume_spike]

    @property
    def low_vol_avg_r(self) -> float | None:
        r_vals = [t.pnl_r for t in self.low_vol_closed_trades if t.pnl_r is not None]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def normal_vol_avg_r(self) -> float | None:
        r_vals = [t.pnl_r for t in self.normal_vol_closed_trades if t.pnl_r is not None]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def spike_vol_avg_r(self) -> float | None:
        r_vals = [t.pnl_r for t in self.spike_vol_closed_trades if t.pnl_r is not None]
        return sum(r_vals) / len(r_vals) if r_vals else None

    # --- Directional × volume cross-tab ---

    @functools.cached_property
    def long_low_vol_closed_trades(self) -> list[Trade]:
        return [t for t in self.long_closed_trades if t.low_volume]

    @functools.cached_property
    def long_normal_vol_closed_trades(self) -> list[Trade]:
        return [
            t
            for t in self.long_closed_trades
            if not t.low_volume and not t.volume_spike
        ]

    @functools.cached_property
    def long_spike_vol_closed_trades(self) -> list[Trade]:
        return [t for t in self.long_closed_trades if t.volume_spike]

    @functools.cached_property
    def short_low_vol_closed_trades(self) -> list[Trade]:
        return [t for t in self.short_closed_trades if t.low_volume]

    @functools.cached_property
    def short_normal_vol_closed_trades(self) -> list[Trade]:
        return [
            t
            for t in self.short_closed_trades
            if not t.low_volume and not t.volume_spike
        ]

    @functools.cached_property
    def short_spike_vol_closed_trades(self) -> list[Trade]:
        return [t for t in self.short_closed_trades if t.volume_spike]

    @property
    def long_low_vol_avg_r(self) -> float | None:
        r_vals = [
            t.pnl_r for t in self.long_low_vol_closed_trades if t.pnl_r is not None
        ]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def long_normal_vol_avg_r(self) -> float | None:
        r_vals = [
            t.pnl_r for t in self.long_normal_vol_closed_trades if t.pnl_r is not None
        ]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def long_spike_vol_avg_r(self) -> float | None:
        r_vals = [
            t.pnl_r for t in self.long_spike_vol_closed_trades if t.pnl_r is not None
        ]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def short_low_vol_avg_r(self) -> float | None:
        r_vals = [
            t.pnl_r for t in self.short_low_vol_closed_trades if t.pnl_r is not None
        ]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def short_normal_vol_avg_r(self) -> float | None:
        r_vals = [
            t.pnl_r for t in self.short_normal_vol_closed_trades if t.pnl_r is not None
        ]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def short_spike_vol_avg_r(self) -> float | None:
        r_vals = [
            t.pnl_r for t in self.short_spike_vol_closed_trades if t.pnl_r is not None
        ]
        return sum(r_vals) / len(r_vals) if r_vals else None

    @property
    def durations_h(self) -> list[float]:
        """Hold time in hours for each closed trade."""
        return [
            (t.exit_time - t.entry_time) / 3_600_000
            for t in self.closed_trades
            if t.exit_time is not None
        ]

    @property
    def avg_duration_h(self) -> float | None:
        d = self.durations_h
        return sum(d) / len(d) if d else None

    @property
    def median_duration_h(self) -> float | None:
        d = sorted(self.durations_h)
        n = len(d)
        if not n:
            return None
        mid = n // 2
        return (d[mid - 1] + d[mid]) / 2 if n % 2 == 0 else d[mid]

    @property
    def long_median_duration_h(self) -> float | None:
        d = sorted(
            (t.exit_time - t.entry_time) / 3_600_000
            for t in self.long_closed_trades
            if t.exit_time is not None
        )
        n = len(d)
        if not n:
            return None
        mid = n // 2
        return (d[mid - 1] + d[mid]) / 2 if n % 2 == 0 else d[mid]

    @property
    def short_median_duration_h(self) -> float | None:
        d = sorted(
            (t.exit_time - t.entry_time) / 3_600_000
            for t in self.short_closed_trades
            if t.exit_time is not None
        )
        n = len(d)
        if not n:
            return None
        mid = n // 2
        return (d[mid - 1] + d[mid]) / 2 if n % 2 == 0 else d[mid]

    @property
    def max_drawdown_r(self) -> float:
        """Largest peak-to-trough drawdown in cumulative R."""
        r_values = [t.pnl_r for t in self.closed_trades if t.pnl_r is not None]
        if not r_values:
            return 0.0
        peak = 0.0
        cumulative = 0.0
        max_dd = 0.0
        for r in r_values:
            cumulative += r
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @property
    def recovery_factor(self) -> float:
        """Total R divided by max drawdown. 0.0 when max drawdown is zero."""
        dd = self.max_drawdown_r
        return self.total_r / dd if dd > 0 else 0.0


def _df_to_events(
    signals: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategy: str,
) -> list[SignalEvent]:
    """Adapt a backtest signals frame into the live `SignalEvent` shape.

    Lets PR-2+ feed backtest signals straight through `analytics/signal/gates.py`
    (which operates on `list[SignalEvent]`). Columns absent in the backtest
    frame fall back to SignalEvent defaults.
    """
    if signals.empty:
        return []

    from analytics.signal.types import SignalEvent

    events: list[SignalEvent] = []
    for record in signals.to_dict("records"):
        events.append(
            SignalEvent(
                symbol=symbol,
                timeframe=timeframe,
                strategy=strategy,
                direction=str(record["direction"]),
                reason=str(record.get("reason", "")),
                open_time=int(record["open_time"]),
                price=float(record.get("price", 0.0)),
                sl_price=float(record.get("sl_price", 0.0) or 0.0),
                context=str(record.get("context", "") or ""),
                low_volume=bool(record.get("low_volume", False)),
                volume_spike=bool(record.get("volume_spike", False)),
                tp_price=float(record.get("tp_price", 0.0) or 0.0),
            )
        )
    return events


def _events_to_df(
    events: list[SignalEvent],
    original_df: pd.DataFrame,
) -> pd.DataFrame:
    """Project a (possibly filtered) event list back onto `original_df`.

    Preserves the original frame's columns + dtypes — gates only ever *drop*
    events, so we filter `original_df` to the surviving `open_time` set and
    keep insertion order. Empty events => empty frame with the same columns.
    """
    if not events:
        return original_df.iloc[0:0].copy()

    kept = [int(ev.open_time) for ev in events]
    kept_set = set(kept)
    mask = original_df["open_time"].astype("int64").isin(kept_set)
    return original_df.loc[mask].reset_index(drop=True)


def _resolve_regime_at(
    signal_time_ms: int,
    regime_series: pd.Series,
) -> str | None:
    """Return the regime of the HTF candle BEFORE the in-progress one at `signal_time_ms`.

    Mirrors live's `_series.iloc[-2]` semantics (scanner.py): at scan time, the
    last row of the HTF series is the still-open candle, and the live gate uses
    the row before it (the last fully-closed candle). For backtest replay at
    historical `signal_time_ms`, the equivalent is:

      1. Find the largest HTF open_time <= signal_time_ms  ("current" candle,
         which may itself still be in-progress at that moment).
      2. Return the regime of the row immediately before it.

    Returns None when there is no prior closed HTF candle (warmup window) — the
    caller falls open, matching live's cache-miss behaviour.
    """
    if regime_series.empty:
        return None
    idx = regime_series.index.to_numpy()
    pos = int(np.searchsorted(idx, signal_time_ms, side="right")) - 1
    target = pos - 1
    if target < 0:
        return None
    val = regime_series.iloc[target]
    return None if pd.isna(val) else str(val)


def _apply_regime_gate_to_signals(
    signals: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategy: str,
    bias_cfg: BiasConfig,
    regime_series: pd.Series | None,
) -> pd.DataFrame:
    """Run live's `_apply_regime_gate` against a backtest signals frame.

    Per-signal regime resolution: each event's regime is looked up at its own
    `open_time` (via `_resolve_regime_at`) so historical signals are evaluated
    against the regime that was active at that moment — not today. Events are
    then grouped by resolved regime and the live gate is invoked once per group
    with a synthetic `regime_cache={symbol: regime}`. Reuses live verbatim —
    no re-implementation, zero drift risk.
    """
    if signals.empty or not bias_cfg.regime_enabled:
        return signals
    if regime_series is None or regime_series.empty:
        return signals  # No HTF series → fall open (matches live cache-miss).

    from analytics.regime import Regime
    from analytics.signal.gates import _apply_regime_gate

    events = _df_to_events(signals, symbol, timeframe, strategy)
    if not events:
        return signals

    by_regime: dict[str | None, list[SignalEvent]] = {}
    for ev in events:
        regime = _resolve_regime_at(int(ev.open_time), regime_series)
        by_regime.setdefault(regime, []).append(ev)

    kept: list[SignalEvent] = []
    for regime, group in by_regime.items():
        if regime is None:
            kept.extend(group)
            continue
        regime_cache: dict[str, Regime] = {symbol: regime}  # type: ignore[dict-item]
        kept.extend(
            _apply_regime_gate(group, bias_cfg, regime_cache, symbol, timeframe)
        )

    return _events_to_df(kept, signals)


def _resolve_series_at(
    signal_time_ms: int,
    series: pd.Series,
) -> float | None:
    """Numeric variant of `_resolve_regime_at` for HTF slope (and similar) lookups.

    Same `iloc[-2]` semantics: find the largest HTF open_time <= signal_time_ms
    (the "current" candle, possibly still in-progress at that moment), then
    return the value at the row BEFORE it (the last fully-closed HTF candle).
    NaN values are normalised to None so the caller treats warmup and missing
    data identically — matching live's `compute_htf_ema_slope` returning None.
    """
    if series.empty:
        return None
    idx = series.index.to_numpy()
    pos = int(np.searchsorted(idx, signal_time_ms, side="right")) - 1
    target = pos - 1
    if target < 0:
        return None
    val = series.iloc[target]
    return None if pd.isna(val) else float(val)


def _apply_direction_filter_gate_to_signals(
    signals: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategy: str,
    bias_cfg: BiasConfig,
    strategy_params: dict[str, StrategyOverride] | None,
) -> pd.DataFrame:
    """Run live's `_apply_direction_filter_gate` against a backtest signals frame.

    Cheapest gate in the chain — pure per-event flag check on
    `StrategyOverride.suppress_long` / `.suppress_short`. No HTF data, no cache
    lookups, no time-series resolution. Adapter wraps `_df_to_events` →
    `_apply_direction_filter_gate` (verbatim from `analytics/signal/gates.py`)
    → `_events_to_df`.
    """
    if signals.empty or not bias_cfg.direction_filter_enabled:
        return signals
    if not strategy_params:
        return signals

    from analytics.signal.gates import _apply_direction_filter_gate

    events = _df_to_events(signals, symbol, timeframe, strategy)
    if not events:
        return signals
    kept = _apply_direction_filter_gate(
        events, bias_cfg, strategy_params, symbol, timeframe
    )
    return _events_to_df(kept, signals)


def _apply_htf_ema_gate_to_signals(
    signals: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategy: str,
    bias_cfg: BiasConfig,
    htf_slope_series_by_anchor: Mapping[tuple[str, int, int], pd.Series] | None,
) -> pd.DataFrame:
    """Run live's `_apply_htf_ema_gate` against a backtest signals frame.

    Per-signal HTF slope resolution: each event's slope is looked up at its
    own `open_time` (via `_resolve_series_at`) using the pre-computed slope
    series for that strategy's anchor — so historical signals are evaluated
    against the slope that was observable at that moment, not today. Events
    are grouped by (anchor, resolved slope) and the live gate is invoked once
    per group with a synthetic single-entry cache, reusing live verbatim.
    """
    if signals.empty or not bias_cfg.htf_ema_enabled:
        return signals
    if not htf_slope_series_by_anchor:
        return signals  # No HTF data → fall open (matches live cache-miss).

    from analytics.signal.gates import _apply_htf_ema_gate

    events = _df_to_events(signals, symbol, timeframe, strategy)
    if not events:
        return signals

    # Group events by (anchor key, resolved slope value) so the live gate is
    # called once per distinct value. Each group sees a synthetic cache with
    # the one anchor key it asks about — keeping the cache lookup verbatim.
    by_key: dict[tuple[tuple[str, int, int], float | None], list[SignalEvent]] = {}
    for ev in events:
        anchor = bias_cfg.htf_ema_anchor(ev.strategy)
        anchor_key = (anchor.tf, anchor.period, anchor.slope_lookback)
        series = htf_slope_series_by_anchor.get(anchor_key)
        slope = (
            _resolve_series_at(int(ev.open_time), series)
            if series is not None
            else None
        )
        by_key.setdefault((anchor_key, slope), []).append(ev)

    kept: list[SignalEvent] = []
    for (anchor_key, slope), group in by_key.items():
        cache_key = (symbol, *anchor_key)
        synthetic_cache: dict[tuple[str, str, int, int], float | None] = {
            cache_key: slope
        }
        kept.extend(
            _apply_htf_ema_gate(group, bias_cfg, synthetic_cache, symbol, timeframe)
        )

    return _events_to_df(kept, signals)


def run_backtest(
    ohlcv: pd.DataFrame,
    signals: pd.DataFrame,
    symbol: str,
    timeframe: str,
    strategy: str,
    sl_pct: float = 0.02,
    tp_r: float = 2.0,
    fee_pct: float = 0.0,
    min_sl_pct: float = 0.0,
    atr_sl_multiplier: float | None = None,
    atr_sl_floor: bool = False,
    volume_suppress: bool = False,
    volume_spike_boost: bool = False,
    volume_suppress_long: bool | None = None,
    volume_suppress_short: bool | None = None,
    volume_spike_boost_long: bool | None = None,
    volume_spike_boost_short: bool | None = None,
    tp_r_long: float | None = None,
    tp_r_short: float | None = None,
    *,
    live_parity: LiveParityConfig | None = None,
    bias_cfg: BiasConfig | None = None,
    regime_series: pd.Series | None = None,
    strategy_params: dict[str, StrategyOverride] | None = None,
    htf_slope_series_by_anchor: Mapping[tuple[str, int, int], pd.Series] | None = None,
) -> BacktestResult:
    """Simulate trades from signals on historical OHLCV.

    Entry:  next candle's open after the signal candle.
    SL:     per-signal sl_price from the signals DataFrame when present (structural);
            otherwise atr_sl_multiplier × ATR14 when set (volatility-adaptive);
            otherwise sl_pct fraction of entry price (fixed fallback).
            min_sl_pct enforces a minimum SL distance from entry (e.g. 0.005 = 0.5%),
            widening SLs that land too close to entry.
            atr_sl_floor=True (F9): on the structural-SL branch, widen sl_price
            when atr_sl_multiplier × ATR14 exceeds the structural distance. Lets
            ATR act as a volatility-adaptive minimum without overriding wider
            structural levels. No-op when atr_sl_multiplier is None.
    TP:     tp_r × risk distance from entry price. When tp_r_long / tp_r_short are set,
            the directional value is used instead of tp_r for that trade direction.
    fee_pct: taker fee fraction applied on both entry and exit legs
             (e.g. 0.0005 for 0.05%). Fee drag is deducted from pnl_r.

    A trade closes when a candle's high or low touches the SL or TP level.
    Trades still open at end of data are marked as outcome="open".

    live_parity toggles parity-with-live gates. The gates apply (in the same
    order as live `run_scan_cycle`):
      1. regime (PR-2) — drops signals not enabled in the HTF regime at signal
         time. Needs `bias_cfg.regime_enabled` and a `regime_series`.
      2. direction_filter (PR-3) — drops signals whose direction is suppressed
         per StrategyOverride.suppress_long / .suppress_short. Needs
         `bias_cfg.direction_filter_enabled` and `strategy_params`. No
         time-series state required.
      3. f8_htf_ema (PR-3) — drops signals opposing the HTF EMA slope active
         at signal time. Needs `bias_cfg.htf_ema_enabled` and
         `htf_slope_series_by_anchor` keyed by (anchor_tf, period, slope_lookback).
    All gates default off and no-op when their inputs are absent — existing
    callers see no behavioural change.
    """
    if live_parity is not None and any(
        live_parity.is_on(gate) for gate in _LIVE_PARITY_GATE_ORDER
    ):
        logger.info(
            "live_parity: %s",
            " ".join(
                f"{gate}={'on' if live_parity.is_on(gate) else 'off'}"
                for gate in _LIVE_PARITY_GATE_ORDER
            ),
        )

    if (
        live_parity is not None
        and live_parity.is_on("regime")
        and bias_cfg is not None
        and bias_cfg.regime_enabled
    ):
        signals = _apply_regime_gate_to_signals(
            signals, symbol, timeframe, strategy, bias_cfg, regime_series
        )

    if (
        live_parity is not None
        and live_parity.is_on("direction_filter")
        and bias_cfg is not None
        and bias_cfg.direction_filter_enabled
    ):
        signals = _apply_direction_filter_gate_to_signals(
            signals, symbol, timeframe, strategy, bias_cfg, strategy_params
        )

    if (
        live_parity is not None
        and live_parity.is_on("f8_htf_ema")
        and bias_cfg is not None
        and bias_cfg.htf_ema_enabled
    ):
        signals = _apply_htf_ema_gate_to_signals(
            signals,
            symbol,
            timeframe,
            strategy,
            bias_cfg,
            htf_slope_series_by_anchor,
        )

    result = BacktestResult(
        symbol=symbol, timeframe=timeframe, strategy=strategy, fee_pct=fee_pct
    )

    if signals.empty or ohlcv.empty:
        return result

    has_per_signal_sl = "sl_price" in signals.columns

    # Pre-extract OHLCV arrays once — avoids per-row pandas overhead in inner loops.
    ohlcv_times_np = ohlcv["open_time"].to_numpy(dtype=np.int64)
    opens_np = ohlcv["open"].to_numpy(dtype=float)
    highs_np = ohlcv["high"].to_numpy(dtype=float)
    lows_np = ohlcv["low"].to_numpy(dtype=float)
    closes_np = ohlcv["close"].to_numpy(dtype=float)
    time_to_idx: dict[int, int] = {int(t): i for i, t in enumerate(ohlcv_times_np)}
    n_candles = len(ohlcv_times_np)

    # Pre-extract signal arrays once — avoids creating a pandas Series per row.
    sig_times_np = signals["open_time"].to_numpy(dtype=np.int64)
    sig_dirs_np = signals["direction"].to_numpy(dtype=object)
    sig_sl_np = signals["sl_price"].to_numpy(dtype=float) if has_per_signal_sl else None

    for si in range(len(sig_times_np)):
        signal_time = int(sig_times_np[si])
        direction = str(sig_dirs_np[si])

        if signal_time not in time_to_idx:
            continue
        sig_idx = time_to_idx[signal_time]
        entry_idx = sig_idx + 1

        if entry_idx >= n_candles:
            continue

        # Volume classification — computed once, used for both the suppress gate
        # and the Trade tag so they are always consistent.
        is_spike = _is_volume_spike(ohlcv, sig_idx)
        is_low_vol = _is_low_volume(ohlcv, sig_idx)

        # Volume suppression: skip low-volume signal candles when enabled.
        # Directional params (volume_suppress_long / volume_suppress_short) take
        # precedence over the symmetric volume_suppress for their respective direction.
        # Spike boost: exempt high-conviction candles (> 3× mean) from suppression.
        # Directional spike boost params override the symmetric volume_spike_boost.
        _suppress = (
            volume_suppress_long
            if direction == "long" and volume_suppress_long is not None
            else volume_suppress_short
            if direction == "short" and volume_suppress_short is not None
            else volume_suppress
        )
        _boost = (
            volume_spike_boost_long
            if direction == "long" and volume_spike_boost_long is not None
            else volume_spike_boost_short
            if direction == "short" and volume_spike_boost_short is not None
            else volume_spike_boost
        )
        if _suppress and is_low_vol and not (_boost and is_spike):
            continue

        entry_time = int(ohlcv_times_np[entry_idx])
        entry_price = opens_np[entry_idx]

        # Resolve direction-split TP multiple for this trade.
        if direction == "long" and tp_r_long is not None:
            eff_tp_r = tp_r_long
        elif direction == "short" and tp_r_short is not None:
            eff_tp_r = tp_r_short
        else:
            eff_tp_r = tp_r

        # SL priority: structural (per-signal) → ATR-based → fixed sl_pct fraction.
        if sig_sl_np is not None:
            sl_price = sig_sl_np[si]
            # Enforce minimum SL distance — widening structural SLs that land
            # too close to entry (prevents fee-drag explosion on tight SLs).
            if min_sl_pct > 0.0:
                min_dist = entry_price * min_sl_pct
                if direction == "long":
                    sl_price = min(sl_price, entry_price - min_dist)
                else:
                    sl_price = max(sl_price, entry_price + min_dist)
            # F9: ATR as volatility-adaptive minimum on structural SLs. Widens
            # tight structural stops on volatile candles; wider structural SLs
            # still win. Opt-in via atr_sl_floor — default off preserves prior
            # behaviour (atr_sl_multiplier is otherwise dead in this branch).
            if atr_sl_floor and atr_sl_multiplier is not None:
                atr = _compute_atr14(highs_np, lows_np, closes_np, sig_idx)
                if atr is not None:
                    atr_dist = atr_sl_multiplier * atr
                    structural_dist = abs(entry_price - sl_price)
                    if atr_dist > structural_dist:
                        if direction == "long":
                            sl_price = entry_price - atr_dist
                        else:
                            sl_price = entry_price + atr_dist
            if direction == "long":
                tp_price = entry_price + eff_tp_r * abs(entry_price - sl_price)
            else:
                tp_price = entry_price - eff_tp_r * abs(entry_price - sl_price)
        elif atr_sl_multiplier is not None:
            atr = _compute_atr14(highs_np, lows_np, closes_np, sig_idx)
            if atr is not None:
                sl_dist = atr_sl_multiplier * atr
                if min_sl_pct > 0.0:
                    sl_dist = max(sl_dist, entry_price * min_sl_pct)
                if direction == "long":
                    sl_price = entry_price - sl_dist
                    tp_price = entry_price + eff_tp_r * sl_dist
                else:
                    sl_price = entry_price + sl_dist
                    tp_price = entry_price - eff_tp_r * sl_dist
            else:
                # Fallback to sl_pct when ATR unavailable (e.g. signal at candle 0)
                if direction == "long":
                    sl_price = entry_price * (1.0 - sl_pct)
                    tp_price = entry_price + eff_tp_r * (entry_price - sl_price)
                else:
                    sl_price = entry_price * (1.0 + sl_pct)
                    tp_price = entry_price - eff_tp_r * (sl_price - entry_price)
        elif direction == "long":
            sl_price = entry_price * (1.0 - sl_pct)
            tp_price = entry_price + eff_tp_r * (entry_price - sl_price)
        else:
            sl_price = entry_price * (1.0 + sl_pct)
            tp_price = entry_price - eff_tp_r * (sl_price - entry_price)

        trade = Trade(
            signal_time=signal_time,
            entry_time=entry_time,
            entry_price=entry_price,
            direction=direction,
            sl_price=sl_price,
            tp_price=tp_price,
            fee_pct=fee_pct,
            low_volume=is_low_vol,
            volume_spike=is_spike,
        )

        # Vectorized candle scan: find first SL-hit and first TP-hit index using
        # numpy nonzero (C loop) instead of a Python for-loop over ohlcv.iloc[i].
        h = highs_np[entry_idx:]
        lo = lows_np[entry_idx:]
        t = ohlcv_times_np[entry_idx:]

        if direction == "long":
            sl_idxs = np.nonzero(lo <= sl_price)[0]
            tp_idxs = np.nonzero(h >= tp_price)[0]
        else:
            sl_idxs = np.nonzero(h >= sl_price)[0]
            tp_idxs = np.nonzero(lo <= tp_price)[0]

        sl_first = int(sl_idxs[0]) if len(sl_idxs) else len(t)
        tp_first = int(tp_idxs[0]) if len(tp_idxs) else len(t)

        # SL takes priority on a same-candle tie (mirrors the original sequential check).
        if sl_first <= tp_first and sl_first < len(t):
            trade.exit_time = int(t[sl_first])
            trade.exit_price = sl_price
            trade.outcome = "loss"
        elif tp_first < len(t):
            trade.exit_time = int(t[tp_first])
            trade.exit_price = tp_price
            trade.outcome = "win"
        # else: neither hit → trade remains open

        result.trades.append(trade)

    return result
