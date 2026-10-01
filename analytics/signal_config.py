"""Pure signal watch configuration loader.

Loads TOML config from a file and provides a SignalWatchConfig dataclass.
CLI flags take precedence over config file values — the caller is responsible
for merging (see wifey.py run_signal_watch).
No module-level side effects.
"""

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Any

from analytics.backtest.cost_model import CostModel, cost_model_from_toml


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base.

    Scalars and arrays: override wins.
    Dicts/tables: merged key-by-key (override wins per key, base keys not in override are kept).
    """
    result: dict[str, Any] = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _load_toml_with_extends(path: str | Path) -> dict[str, Any]:
    """Load a TOML file, merging a base file first if an 'extends' key is present.

    The 'extends' value must be a filename relative to the config file's directory.
    The base file is loaded first; the child file's keys are deep-merged on top
    (child wins on conflicts). The 'extends' key is consumed and not passed to callers.
    """
    resolved = Path(path)
    with open(resolved, "rb") as f:
        data: dict[str, Any] = tomllib.load(f)
    base_name = data.pop("extends", None)
    if base_name is not None:
        base_path = resolved.parent / str(base_name)
        with open(base_path, "rb") as f:
            base: dict[str, Any] = tomllib.load(f)
        data = _deep_merge(base, data)
    return data


@dataclass
class SymbolOverride:
    """Per-symbol parameter overrides within a strategy block.

    TOML example (sub-table under strategy_params):
        [strategy_params.doji.MSFT]
        tp_r_1d = 4.5      # MSFT 1d only
        tp_r_4h = 4.0      # MSFT 4h only

    Lookup order within a symbol block: TF-specific → symbol-wide.
    """

    tp_r: float | None = None
    sl_pct: float | None = None
    atr_sl_multiplier: float | None = None
    atr_sl_floor: bool | None = None
    tp_r_per_tf: dict[str, float] = field(default_factory=dict)
    tp_r_long_per_tf: dict[str, float] = field(default_factory=dict)
    tp_r_short_per_tf: dict[str, float] = field(default_factory=dict)
    sl_pct_per_tf: dict[str, float] = field(default_factory=dict)
    atr_sl_multiplier_per_tf: dict[str, float] = field(default_factory=dict)
    atr_sl_floor_per_tf: dict[str, bool] = field(default_factory=dict)


@dataclass
class StrategyOverride:
    """Per-strategy parameter overrides.

    Lookup order for each param: symbol+TF → symbol → TF-specific → strategy-wide → global.

    TOML example (signal_watch.toml):
        [strategy_params.engulfing]
        tp_r = 3.0        # all TFs unless overridden below
        tp_r_4h = 3.0     # 4h-specific override
        tp_r_1h = 2.5

        [strategy_params.doji]
        tp_r = 4.0           # all symbols fallback

        [strategy_params.doji.AAPL]
        tp_r_1d = 3.5

        [strategy_params.doji.MSFT]
        tp_r_1d = 4.5
    """

    tp_r: float | None = None
    sl_pct: float | None = None
    atr_sl_multiplier: float | None = None
    atr_sl_floor: bool | None = None
    tp_r_per_tf: dict[str, float] = field(default_factory=dict)
    # Per-TF directional overrides (TOML key: `tp_r_long_4h = 3.5`). Parsed, and
    # honoured by `effective_tp_r` when it is given a direction, but no consumer
    # applies them: `_resolve_tp_r` has no per-TF directional step and the sweep
    # calls `effective_tp_r` without a direction. Audit:
    # docs/audits/2026-10-01-dead-directional-tp-r-keys.md (Issue #317).
    tp_r_long_per_tf: dict[str, float] = field(default_factory=dict)
    tp_r_short_per_tf: dict[str, float] = field(default_factory=dict)
    sl_pct_per_tf: dict[str, float] = field(default_factory=dict)
    atr_sl_multiplier_per_tf: dict[str, float] = field(default_factory=dict)
    atr_sl_floor_per_tf: dict[str, bool] = field(default_factory=dict)
    per_symbol: dict[str, SymbolOverride] = field(default_factory=dict)
    adr_exempt: bool = False
    # None = inherit global [backtest].volume_suppress; True/False = per-strategy override.
    volume_suppress: bool | None = None
    # None = inherit global [backtest].volume_spike_boost; True/False = per-strategy override.
    volume_spike_boost: bool | None = None
    # Directional volume suppress — overrides volume_suppress for that direction when set.
    volume_suppress_long: bool | None = None
    volume_suppress_short: bool | None = None
    # Directional spike boost — overrides volume_spike_boost for that direction when set.
    volume_spike_boost_long: bool | None = None
    volume_spike_boost_short: bool | None = None
    # Optional direction-split TP multiples. Falls back to tp_r when None.
    tp_r_long: float | None = None
    tp_r_short: float | None = None
    # T2c per-strategy directional suppress. When True, drop signals of that
    # direction at the live bias chain (Step −0.5). Applied by
    # _apply_direction_filter_gate when [bias.direction_filter].enabled = true.
    suppress_long: bool = False
    suppress_short: bool = False


def _day_filter_to_weekdays(day_filter: str) -> list[int] | None:
    """Convert day_filter mode string to allowed weekday list (Mon=0…Sun=6).

    Modes:
      "off"      — no filter (all days)
      "weekdays" — Mon–Fri (removes Sat, Sun)
      "no_monfi" — remove Mon + Fri only; weekends still pass (legacy behaviour)
      "tue_thu"  — Tue–Thu only (removes Mon, Fri, Sat, Sun)
    """
    if day_filter == "weekdays":
        return [0, 1, 2, 3, 4]  # Mon–Fri
    if day_filter == "no_monfi":
        return [1, 2, 3, 5, 6]  # Tue, Wed, Thu, Sat, Sun
    if day_filter == "tue_thu":
        return [1, 2, 3]  # Tue–Thu only
    return None  # "off" — no filter


# Timeframes whose bars always carry the same open weekday, making a day filter
# that excludes that weekday a total blackout rather than a filter. Weekly bars
# are stamped at the week start (Monday) whether or not that Monday traded — all
# 5,837 weekly bars on the live watchlist open Monday.
_FIXED_OPEN_WEEKDAY: dict[str, int] = {"1wk": 0}


def dead_timeframes(day_filter: str, timeframes: Iterable[str]) -> list[str]:
    """Timeframes whose every bar ``day_filter`` discards, in declaration order.

    Pairing a day filter with a fixed-open-weekday timeframe suppresses 100% of
    that timeframe's signals, and it is silent at every downstream layer: the
    scanner drops the events one by one, the backtest still records runs (with
    zero closed trades), and recalibrate consequently writes no ratings — so
    ``get_confidence`` falls back to a hardcoded 3 that no run can ever correct.
    ``config/signal_watch.toml`` scanned ``1wk`` under ``tue_thu`` from PR #22
    until 2026-08-06 and dispatched zero alerts in that entire window.
    """
    allowed = _day_filter_to_weekdays(day_filter)
    if allowed is None:
        return []  # "off" — nothing can be filtered out
    return [
        tf
        for tf in dict.fromkeys(timeframes)
        if tf in _FIXED_OPEN_WEEKDAY and _FIXED_OPEN_WEEKDAY[tf] not in allowed
    ]


# Per-strategy flags that keep only HIGH-volume signal candles. Each is the
# opposite selection to the ADR gate, which keeps only bars that have consumed
# little of their typical daily range.
_VOLUME_SUPPRESS_FLAGS = (
    "volume_suppress",
    "volume_suppress_long",
    "volume_suppress_short",
)


def voided_volume_gates(
    strategy_params: dict[str, StrategyOverride],
    adr_suppress_threshold: float | None,
) -> list[str]:
    """Strategies whose volume gate and the ADR gate cancel each other out.

    ``volume_suppress*`` keeps only candles with >=1.5x the trailing mean volume;
    the ADR gate keeps only candles that have consumed < ``adr_suppress_threshold``
    of the 14-day average daily range. Range and volume are strongly positively
    correlated (+0.613 on 1d, +0.673 on 4h, measured over the 13 live symbols on
    2026-08-06), so the two select for opposite bars and their conjunction is very
    nearly the empty set: P(pass both) = 0.0046 against 0.036 under independence,
    an 8x shortfall, and P(pass volume | passed ADR) = 0.012 vs P(pass volume) =
    0.098.

    This is silent at every layer — the detector fires normally and the events are
    dropped one at a time, so the surface reads as "covered" while producing
    nothing. Measured damage before the 2026-08-06 fix, cost-inclusive:
    ``doji`` x 1d ran at exactly 0 signals against 1,247 raw detector fires;
    ``orb`` x 4h at n=1; and on ``engulfing`` x 1d / ``bos`` x 1d the pairing did
    not merely thin the sample but INVERTED its sign (-0.273R -> +0.042R and
    -0.033R -> +0.071R once the conjunction was removed).

    ``adr_exempt = true`` clears a strategy: entry geometries that fire at range
    extremes by construction (breakouts, structure sweeps) legitimately opt out of
    the ADR gate, leaving the volume flag to act alone.
    """
    if adr_suppress_threshold is None:
        return []  # ADR gate disabled — the volume flag acts alone, no conjunction
    return [
        name
        for name, override in strategy_params.items()
        if not override.adr_exempt
        and any(getattr(override, f) is True for f in _VOLUME_SUPPRESS_FLAGS)
    ]


@dataclass
class BacktestFilterConfig:
    """Configuration for the per-signal backtest filter."""

    # "soft": always fire alert, append win rate line
    # "hard": suppress alert if directional avg_r < min_avg_r (and enough trades)
    # "off":  disable entirely
    mode: str = "soft"
    days: int = 90
    # Anchor start date for stable backtest windows (e.g. "2025-09-12"). Overrides days
    # when set — use this so alert backtest stats match saved sweep runs exactly.
    since: str | None = None
    # Below this trade count the filter is bypassed (win rate is noise).
    # Global fallback — use effective_min_trades(tf) to get per-TF value.
    min_trades: int = 12
    # Per-TF override: check this first, fall back to min_trades.
    # Calibrated from backtest_runs DB (directional trade counts, not total):
    #   15m→20, 1h→12, 4h→5, 1d→2
    # Thresholds apply to the directional bucket (long or short), not total closed trades.
    # DB p25 directional: 15m=58, 1h=17, 4h=4-5, 1d=0-1 — values set to cover ~75%+ of runs.
    # Signal rate is NOT uniform — higher TFs fire less frequently per candle.
    # To scale for a different lookback: new_value = base × (your_days / 200)
    min_trades_per_tf: dict[str, int] = field(default_factory=dict)
    # hard mode only: suppress if directional avg_r < this (replaces win-rate gate)
    # 0.0 = must have positive EV; set lower to allow marginally negative strategies
    min_avg_r: float = 0.0
    # Optional direction-split thresholds. When set, these override min_avg_r for that
    # direction only. Falls back to min_avg_r when None.
    min_avg_r_long: float | None = None
    min_avg_r_short: float | None = None
    # Significance requirement on the BLOCK decision (2026-08-07). The gate
    # suppresses only when the directional avg_r falls below its threshold by
    # more than this many standard errors — i.e. when the shortfall is
    # distinguishable from zero, not merely negative. Default 1.64 = one-sided
    # 95%. Measured on the post-#150 population, a bare `avg_r < threshold` test
    # made 43% (signal_watch) / 55% (weekdays) of blocks on evidence
    # indistinguishable from zero, and a block destroys the ledger row too.
    # NOT multiplicity-corrected, deliberately: BH guards against SELECTING a
    # winner from many candidates (the sweep's problem); this gate makes an
    # independent per-leg call, and correcting over ~200-400 cells drives the
    # critical value to z~3.5, at which a fail-open gate blocks ~nothing.
    # 0.0 restores the legacy point-estimate behaviour.
    min_avg_r_z: float = 1.64
    # Persist computed backtest results to backtest_runs table (default on)
    save_results: bool = True
    # Taker fee per leg (e.g. 0.0005 = 0.05%); applied to each backtest trade
    fee_pct: float = 0.0
    # Phase 0.4 equity cost model — None = legacy flat fee_pct path.
    # Parsed from the [backtest.cost_model] TOML block; when set it replaces
    # fee_pct inside pnl_r. Off by default so goldens stay byte-identical.
    cost_model: CostModel | None = None
    # Minimum SL distance from entry as a fraction (e.g. 0.005 = 0.5%).
    # Widens structural SLs that land too close to entry (prevents fee-drag explosion).
    min_sl_pct: float = 0.0
    # Suppress alerts when the signal candle has volume < 1.5× its 20-candle rolling mean.
    # Enable after confirming via `make wifey-backtest` volume split that low-vol trades
    # underperform. Default off — investigate first.
    volume_suppress: bool = False
    # Exempt spike candles (volume > 3× rolling mean) from volume_suppress.
    # When True, a spike candle passes even if volume_suppress is on for that strategy.
    # Default off — enable per-strategy after confirming spike edge via volume split table.
    volume_spike_boost: bool = False
    # Enable two-layer backtest cache (L1 module dict + L2 DuckDB table).
    # Set false in TOML for instant rollback without a code deploy.
    cache_enabled: bool = True

    def effective_min_trades(self, tf: str) -> int:
        return self.min_trades_per_tf.get(tf, self.min_trades)


@dataclass
class HtfEmaAnchor:
    """HTF EMA anchor for the F8 directional gate.

    Resolved per-strategy: defaults from BiasConfig.htf_ema_default_* unless
    overridden in BiasConfig.htf_ema_per_strategy[<strategy>].
    """

    tf: str = "4h"
    period: int = 50
    slope_lookback: int = 10


@dataclass
class BiasConfig:
    """Configuration for the statistics-driven bias layer (F8) + regime gate (v2 Phase 2).

    Controls four gates applied after the backtest/volume filters:

    1. Regime gate (v2 Phase 2): drop signals whose strategy type is not enabled
       in the current 4h-classified regime (`trend` / `range` / `high_vol` /
       `unknown`). `unknown` and cache misses fall open. Per-strategy overrides
       supersede the type-level mapping (e.g. `bos` is continuation despite
       being typed `structural`).

    2. ADR hard suppress: drop signals when today's range has already consumed
       >= adr_suppress_threshold of the 14-day ADR (e.g. 0.80 = 80%).
       None = disabled (default).

    3. DOW soft suppress: reduce confidence by 1 star when the signal direction
       opposes today's historical DOW avg return.  Only fires when abs(avg_return)
       >= dow_suppress_min_abs_return (dead-band, default 0.5%).

    4. HTF EMA directional gate (F8): suppress signals whose direction opposes
       the slope of an HTF (4h or 1d) EMA.  Per-strategy anchors are derived
       from a 24-cell tue_thu sweep — most strategies use 4h EMA-50; a small
       set of counter-trend / session strategies use 1d EMA-50 instead.

       deadband_pct: when |slope| < deadband, the gate allows both directions
       (the HTF has no opinion).  mode="soft" logs only; mode="hard" suppresses.
    """

    adr_suppress_threshold: float | None = None
    dow_soft_suppress: bool = False
    dow_suppress_min_abs_return: float = 0.005

    # F8 HTF EMA directional gate.
    htf_ema_enabled: bool = False
    htf_ema_mode: str = "soft"  # "soft" = log only, "hard" = drop suppressed signals
    htf_ema_default_tf: str = "4h"
    htf_ema_default_period: int = 50
    htf_ema_default_slope_lookback: int = 10
    htf_ema_deadband_pct: float = 0.003
    htf_ema_per_strategy: dict[str, HtfEmaAnchor] = field(default_factory=dict)

    # T2c per-strategy directional suppress gate (Step −0.5 of bias chain).
    # Applied to StrategyOverride.suppress_long / .suppress_short flags.
    # mode="soft" logs only; mode="hard" drops the matched-direction events.
    direction_filter_enabled: bool = False
    direction_filter_mode: str = "soft"

    # v2 Phase 2 regime gate.
    regime_enabled: bool = False
    regime_mode: str = "soft"  # "soft" = log only, "hard" = drop suppressed signals
    regime_htf_tf: str = "4h"
    # Strategy-type → list of regimes where the type is allowed.
    # A strategy whose type is not in this map falls open (defensive: unknown
    # type means a freshly-added detector hasn't been classified yet).
    regime_enabled_regimes: dict[str, list[str]] = field(default_factory=dict)
    # Per-strategy override: pins a specific strategy to a regime list,
    # bypassing its strategy_type mapping. Used for strategies that don't
    # match their type's behaviour (e.g. `bos` is continuation despite type=structural).
    regime_per_strategy: dict[str, list[str]] = field(default_factory=dict)

    def htf_ema_anchor(self, strategy: str) -> HtfEmaAnchor:
        """Resolve the HTF anchor for a strategy (override → default)."""
        override = self.htf_ema_per_strategy.get(strategy)
        if override is not None:
            return override
        return HtfEmaAnchor(
            tf=self.htf_ema_default_tf,
            period=self.htf_ema_default_period,
            slope_lookback=self.htf_ema_default_slope_lookback,
        )

    def regime_allowed(self, strategy: str, strategy_type: str, regime: str) -> bool:
        """Is `strategy` allowed to fire in the current `regime`?

        Resolution order:
          1. Per-strategy override (if present) — wins over type-level mapping.
          2. Strategy-type → regime list.
          3. No mapping found → fall open (defensive).
        Always allow `unknown` regime (warmup / missing data).
        """
        if regime == "unknown":
            return True
        override = self.regime_per_strategy.get(strategy)
        if override is not None:
            return regime in override
        type_regimes = self.regime_enabled_regimes.get(strategy_type)
        if type_regimes is None:
            return True  # unknown type → fall open
        return regime in type_regimes


@dataclass
class ComboConfig:
    """Configuration for live co-firing confluence detection (D10 steps 3+4).

    Same-TF (step 3):
      window: look back this many candles in the signals DB for a co-firing partner.
      min_avg_r: only tag pairs whose backtest combo avg_r meets this threshold.

    Cross-TF (step 4):
      cross_tf_pairs: HTF:LTF pairs to check, e.g. ["4h:15m", "4h:1h"].
                      Empty list disables cross-TF detection entirely.
      cross_tf_window_hours: how far back (hours) to look for an HTF signal.
      cross_tf_min_avg_r: min backtest avg_r for a cross-TF pair to fire.
    """

    window: int = 5
    min_avg_r: float = 1.0
    cross_tf_pairs: list[str] = field(
        default_factory=lambda: ["4h:15m", "4h:1h", "1h:15m", "1d:4h", "1d:1h"]
    )
    cross_tf_window_hours: float = 4.0
    cross_tf_min_avg_r: float = 1.0


@dataclass
class SignalWatchConfig:
    """All configurable options for the signal watch daemon."""

    symbols: list[str] | None = None
    timeframes: list[str] = field(default_factory=lambda: ["4h"])
    strategies: list[str] | None = None
    telegram: bool = False
    min_sl_pct: float = 0.0
    tp_r: float = 2.0
    sl_pct: float = 0.02
    state_file: str = "signal_state.json"
    backtest: BacktestFilterConfig = field(default_factory=BacktestFilterConfig)
    # Suppress signals by day: "off" | "weekdays" (Mon–Fri) | "tue_thu" (Tue–Thu only)
    day_filter: str = "off"
    # Catch-up dispatch window, in hours measured from a candle's CLOSE. The
    # newest closed candle always alerts; an older one alerts only while it is
    # this fresh. 0.0 = newest-only, the pre-2026-08-25 rule. The live configs
    # inherit a non-zero value from strategy_params.toml because under one
    # pre-open run a day the session's FIRST 4h bar can never BE the newest
    # closed candle — see scanner.may_dispatch_candle.
    max_alert_age_hours: float = 0.0
    # Per-strategy timeframe allow-list: {"trend_day": ["4h", "1d"], ...}
    # Strategies not listed here run on all configured timeframes.
    strategy_timeframes: dict[str, list[str]] = field(default_factory=dict)
    # Per-strategy parameter overrides (tp_r, sl_pct, TF-specific variants).
    # Lookup order: TF-specific → strategy-wide → global tp_r / sl_pct.
    strategy_params: dict[str, StrategyOverride] = field(default_factory=dict)
    # Global ATR-based SL multiplier (None = use sl_pct instead).
    # When set, SL distance = atr_sl_multiplier × ATR14 at signal candle.
    # Per-strategy overrides in strategy_params take precedence.
    atr_sl_multiplier: float | None = None
    # F9 ATR-as-min-SL floor — when True, structural SLs are widened to
    # max(structural_dist, atr_sl_multiplier × ATR14) and tp_price is
    # recomputed from tp_r × new_sl_dist to keep R:R intact. Default off
    # mirrors backtest engine; opt-in per-strategy via strategy_params.
    atr_sl_floor: bool = False
    # Statistics-driven bias layer (F8): ADR progress gate + DOW soft suppress.
    bias: BiasConfig = field(default_factory=BiasConfig)
    # Live co-firing confluence detection (D10 step 3).
    combo: ComboConfig = field(default_factory=ComboConfig)

    def effective_tp_r(
        self, strategy: str, symbol: str, tf: str, direction: str = ""
    ) -> float:
        """Resolve tp_r.

        Precedence (highest first):
          1. symbol + TF + direction (sym.tp_r_long_per_tf[tf] / sym.tp_r_short_per_tf[tf])
          2. symbol + TF (sym.tp_r_per_tf[tf])
          3. symbol (sym.tp_r)
          4. strategy + TF + direction (override.tp_r_long_per_tf[tf] / override.tp_r_short_per_tf[tf])
          5. strategy + TF (override.tp_r_per_tf[tf])
          6. strategy + direction (override.tp_r_long / override.tp_r_short)
          7. strategy-wide (override.tp_r)
          8. global (self.tp_r)

        Steps 1, 4 and 6 need ``direction``, and no caller passes one: the sweep
        calls this direction-less and the live path resolves through
        ``analytics/signal/resolvers.py::_resolve_tp_r`` instead, which has steps
        2, 3, 5, 6, 7 and 8 only. Audit:
        docs/audits/2026-10-01-dead-directional-tp-r-keys.md.
        """
        override = self.strategy_params.get(strategy)
        if override is not None:
            sym = override.per_symbol.get(symbol)
            if sym is not None:
                if direction == "long" and tf in sym.tp_r_long_per_tf:
                    return sym.tp_r_long_per_tf[tf]
                if direction == "short" and tf in sym.tp_r_short_per_tf:
                    return sym.tp_r_short_per_tf[tf]
                if tf in sym.tp_r_per_tf:
                    return sym.tp_r_per_tf[tf]
                if sym.tp_r is not None:
                    return sym.tp_r
            if direction == "long" and tf in override.tp_r_long_per_tf:
                return override.tp_r_long_per_tf[tf]
            if direction == "short" and tf in override.tp_r_short_per_tf:
                return override.tp_r_short_per_tf[tf]
            if tf in override.tp_r_per_tf:
                return override.tp_r_per_tf[tf]
            if direction == "long" and override.tp_r_long is not None:
                return override.tp_r_long
            if direction == "short" and override.tp_r_short is not None:
                return override.tp_r_short
            if override.tp_r is not None:
                return override.tp_r
        return self.tp_r

    def effective_sl_pct(self, strategy: str, symbol: str, tf: str) -> float:
        """Resolve sl_pct: symbol+TF → symbol → TF-specific → strategy-wide → global."""
        override = self.strategy_params.get(strategy)
        if override is not None:
            sym = override.per_symbol.get(symbol)
            if sym is not None:
                if tf in sym.sl_pct_per_tf:
                    return sym.sl_pct_per_tf[tf]
                if sym.sl_pct is not None:
                    return sym.sl_pct
            if tf in override.sl_pct_per_tf:
                return override.sl_pct_per_tf[tf]
            if override.sl_pct is not None:
                return override.sl_pct
        return self.sl_pct

    def effective_volume_suppress(self, strategy: str) -> bool:
        override = self.strategy_params.get(strategy)
        if override is not None and override.volume_suppress is not None:
            return override.volume_suppress
        return self.backtest.volume_suppress

    def effective_volume_spike_boost(self, strategy: str) -> bool:
        override = self.strategy_params.get(strategy)
        if override is not None and override.volume_spike_boost is not None:
            return override.volume_spike_boost
        return self.backtest.volume_spike_boost

    def effective_volume_suppress_long(self, strategy: str) -> bool | None:
        override = self.strategy_params.get(strategy)
        if override is not None:
            return override.volume_suppress_long
        return None

    def effective_volume_suppress_short(self, strategy: str) -> bool | None:
        override = self.strategy_params.get(strategy)
        if override is not None:
            return override.volume_suppress_short
        return None

    def effective_volume_spike_boost_long(self, strategy: str) -> bool | None:
        override = self.strategy_params.get(strategy)
        if override is not None:
            return override.volume_spike_boost_long
        return None

    def effective_volume_spike_boost_short(self, strategy: str) -> bool | None:
        override = self.strategy_params.get(strategy)
        if override is not None:
            return override.volume_spike_boost_short
        return None

    def effective_atr_sl_multiplier(
        self, strategy: str, symbol: str, tf: str
    ) -> float | None:
        """Resolve atr_sl_multiplier: symbol+TF → symbol → TF-specific → strategy-wide → global."""
        override = self.strategy_params.get(strategy)
        if override is not None:
            sym = override.per_symbol.get(symbol)
            if sym is not None:
                if tf in sym.atr_sl_multiplier_per_tf:
                    return sym.atr_sl_multiplier_per_tf[tf]
                if sym.atr_sl_multiplier is not None:
                    return sym.atr_sl_multiplier
            if tf in override.atr_sl_multiplier_per_tf:
                return override.atr_sl_multiplier_per_tf[tf]
            if override.atr_sl_multiplier is not None:
                return override.atr_sl_multiplier
        return self.atr_sl_multiplier

    def effective_atr_sl_floor(self, strategy: str, symbol: str, tf: str) -> bool:
        """Resolve atr_sl_floor: symbol+TF → symbol → TF-specific → strategy-wide → global."""
        override = self.strategy_params.get(strategy)
        if override is not None:
            sym = override.per_symbol.get(symbol)
            if sym is not None:
                if tf in sym.atr_sl_floor_per_tf:
                    return sym.atr_sl_floor_per_tf[tf]
                if sym.atr_sl_floor is not None:
                    return sym.atr_sl_floor
            if tf in override.atr_sl_floor_per_tf:
                return override.atr_sl_floor_per_tf[tf]
            if override.atr_sl_floor is not None:
                return override.atr_sl_floor
        return self.atr_sl_floor


def declared_cells(cfg: SignalWatchConfig) -> list[tuple[str, str]]:
    """(strategy, timeframe) pairs the config will scan, in declaration order.

    A strategy listed in ``strategy_timeframes`` is restricted to those
    timeframes; every other strategy runs on the config's full ``timeframes``
    list. This mirrors the allow-list that ``signal.scanner.scan_symbol``
    applies per candle, so it is the authoritative answer to "what does this
    config actually scan?".

    Lives here rather than beside either of its two consumers because they ask
    the question from opposite ends and must not drift apart:
    ``tools/dead_surface_check.py`` asks which declared cells produce nothing,
    and ``recalibrate_lib`` asks which rated cells are no longer declared. A
    forked definition would let a cell be dead under one and healthy under the
    other.
    """
    cells: list[tuple[str, str]] = []
    for strategy in cfg.strategies or []:
        for tf in cfg.strategy_timeframes.get(strategy, cfg.timeframes):
            if (strategy, tf) not in cells:
                cells.append((strategy, tf))
    return cells


def load_signal_config(path: str | Path) -> SignalWatchConfig:
    """Load SignalWatchConfig from a TOML file.

    Raises FileNotFoundError if path does not exist.
    Raises tomllib.TOMLDecodeError if the file is not valid TOML.
    """
    data = _load_toml_with_extends(path)

    raw_stf = data.get("strategy_timeframes", {})
    if not isinstance(raw_stf, dict):
        raise ValueError(
            "strategy_timeframes must be a TOML table of strategy = [timeframes] entries"
        )
    strategy_timeframes: dict[str, list[str]] = {
        str(k): [str(tf) for tf in v] for k, v in raw_stf.items()
    }

    raw_bt = data.get("backtest", {})
    bt_per_tf = {
        k[len("min_trades_") :]: int(v)
        for k, v in raw_bt.items()
        if k.startswith("min_trades_") and k != "min_trades"
    }
    # Same defect class as max_alert_age_hours below: `filter_threshold` was the
    # win-rate gate that `min_avg_r` replaced. It was kept as a parsed field "for
    # TOML back-compat", but nothing ever read it and unknown keys in [backtest]
    # are ignored anyway — so the field bought no compatibility and instead made a
    # dead key look honoured. A config carrying it declares a suppression that
    # cannot fire. Refuse it rather than accepting it inertly.
    if "filter_threshold" in raw_bt:
        raise ValueError(
            f"[backtest] filter_threshold={raw_bt['filter_threshold']} is a dead "
            "key: it was the win-rate gate that min_avg_r replaced, and nothing "
            "has read it since. Left in place it reads as an active suppression "
            "while gating nothing. Remove it, and express the intent as min_avg_r "
            "(a directional avg_r floor) if a gate is wanted."
        )

    backtest = BacktestFilterConfig(
        mode=str(raw_bt.get("mode", "soft")),
        days=int(raw_bt.get("days", 90)),
        since=str(raw_bt["since"]) if raw_bt.get("since") else None,
        min_trades=int(raw_bt.get("min_trades", 20)),
        min_trades_per_tf=bt_per_tf,
        min_avg_r=float(raw_bt.get("min_avg_r", 0.0)),
        min_avg_r_long=(
            float(raw_bt["min_avg_r_long"])
            if raw_bt.get("min_avg_r_long") is not None
            else None
        ),
        min_avg_r_short=(
            float(raw_bt["min_avg_r_short"])
            if raw_bt.get("min_avg_r_short") is not None
            else None
        ),
        # Default matches the dataclass (1.64). Deliberately NOT defaulted off:
        # T6 shipped LiveParityConfig default-off "to be flipped later" and it
        # stayed off for ~2.5 months (#149). A behaviour change ships on.
        min_avg_r_z=float(raw_bt.get("min_avg_r_z", 1.64)),
        save_results=bool(raw_bt.get("save_results", True)),
        # [backtest].fee_pct takes precedence; falls back to top-level fee_pct
        fee_pct=float(raw_bt.get("fee_pct", data.get("fee_pct", 0.0))),
        # [backtest].min_sl_pct takes precedence; falls back to top-level min_sl_pct
        min_sl_pct=float(raw_bt.get("min_sl_pct", data.get("min_sl_pct", 0.0))),
        volume_suppress=bool(raw_bt.get("volume_suppress", False)),
        volume_spike_boost=bool(raw_bt.get("volume_spike_boost", False)),
        cost_model=cost_model_from_toml(raw_bt.get("cost_model")),
    )

    raw_strategy_params = data.get("strategy_params", {})
    if not isinstance(raw_strategy_params, dict):
        raise ValueError("strategy_params must be a TOML table of strategy sub-tables")
    strategy_params: dict[str, StrategyOverride] = {}
    for strat_name, vals in raw_strategy_params.items():
        if not isinstance(vals, dict):
            raise ValueError(
                f"strategy_params.{strat_name} must be a TOML table "
                "(e.g. [strategy_params.engulfing] or inline {{tp_r = 3.0}})"
            )
        tp_r_per_tf = {
            k[len("tp_r_") :]: float(v)
            for k, v in vals.items()
            if k.startswith("tp_r_")
            and k not in ("tp_r", "tp_r_long", "tp_r_short")
            and not k.startswith("tp_r_long_")
            and not k.startswith("tp_r_short_")
            and not isinstance(v, dict)
        }
        tp_r_long_per_tf = {
            k[len("tp_r_long_") :]: float(v)
            for k, v in vals.items()
            if k.startswith("tp_r_long_") and not isinstance(v, dict)
        }
        tp_r_short_per_tf = {
            k[len("tp_r_short_") :]: float(v)
            for k, v in vals.items()
            if k.startswith("tp_r_short_") and not isinstance(v, dict)
        }
        sl_pct_per_tf = {
            k[len("sl_pct_") :]: float(v)
            for k, v in vals.items()
            if k.startswith("sl_pct_") and k != "sl_pct" and not isinstance(v, dict)
        }
        atr_sl_per_tf = {
            k[len("atr_sl_multiplier_") :]: float(v)
            for k, v in vals.items()
            if k.startswith("atr_sl_multiplier_")
            and k != "atr_sl_multiplier"
            and not isinstance(v, dict)
        }
        atr_sl_floor_per_tf = {
            k[len("atr_sl_floor_") :]: bool(v)
            for k, v in vals.items()
            if k.startswith("atr_sl_floor_")
            and k != "atr_sl_floor"
            and not isinstance(v, dict)
        }
        tp_r_val = vals.get("tp_r")
        sl_pct_val = vals.get("sl_pct")
        atr_sl_val = vals.get("atr_sl_multiplier")
        atr_sl_floor_val = vals.get("atr_sl_floor")
        per_symbol: dict[str, SymbolOverride] = {}
        for sym_key, sym_vals in vals.items():
            if isinstance(sym_vals, dict):
                sym_tp_r_per_tf = {
                    k[len("tp_r_") :]: float(v)
                    for k, v in sym_vals.items()
                    if k.startswith("tp_r_")
                    and k not in ("tp_r", "tp_r_long", "tp_r_short")
                    and not k.startswith("tp_r_long_")
                    and not k.startswith("tp_r_short_")
                }
                sym_tp_r_long_per_tf = {
                    k[len("tp_r_long_") :]: float(v)
                    for k, v in sym_vals.items()
                    if k.startswith("tp_r_long_")
                }
                sym_tp_r_short_per_tf = {
                    k[len("tp_r_short_") :]: float(v)
                    for k, v in sym_vals.items()
                    if k.startswith("tp_r_short_")
                }
                sym_sl_pct_per_tf = {
                    k[len("sl_pct_") :]: float(v)
                    for k, v in sym_vals.items()
                    if k.startswith("sl_pct_") and k != "sl_pct"
                }
                sym_atr_sl_per_tf = {
                    k[len("atr_sl_multiplier_") :]: float(v)
                    for k, v in sym_vals.items()
                    if k.startswith("atr_sl_multiplier_") and k != "atr_sl_multiplier"
                }
                sym_atr_sl_floor_per_tf = {
                    k[len("atr_sl_floor_") :]: bool(v)
                    for k, v in sym_vals.items()
                    if k.startswith("atr_sl_floor_") and k != "atr_sl_floor"
                }
                sym_tp_r_val = sym_vals.get("tp_r")
                sym_sl_pct_val = sym_vals.get("sl_pct")
                sym_atr_sl_val = sym_vals.get("atr_sl_multiplier")
                sym_atr_sl_floor_val = sym_vals.get("atr_sl_floor")
                per_symbol[sym_key] = SymbolOverride(
                    tp_r=float(sym_tp_r_val) if sym_tp_r_val is not None else None,
                    sl_pct=float(sym_sl_pct_val)
                    if sym_sl_pct_val is not None
                    else None,
                    atr_sl_multiplier=(
                        float(sym_atr_sl_val) if sym_atr_sl_val is not None else None
                    ),
                    atr_sl_floor=(
                        bool(sym_atr_sl_floor_val)
                        if sym_atr_sl_floor_val is not None
                        else None
                    ),
                    tp_r_per_tf=sym_tp_r_per_tf,
                    tp_r_long_per_tf=sym_tp_r_long_per_tf,
                    tp_r_short_per_tf=sym_tp_r_short_per_tf,
                    sl_pct_per_tf=sym_sl_pct_per_tf,
                    atr_sl_multiplier_per_tf=sym_atr_sl_per_tf,
                    atr_sl_floor_per_tf=sym_atr_sl_floor_per_tf,
                )
        raw_vs = vals.get("volume_suppress")
        raw_vsb = vals.get("volume_spike_boost")
        raw_vsl = vals.get("volume_suppress_long")
        raw_vss = vals.get("volume_suppress_short")
        raw_vsbl = vals.get("volume_spike_boost_long")
        raw_vsbs = vals.get("volume_spike_boost_short")
        raw_tp_r_long = vals.get("tp_r_long")
        raw_tp_r_short = vals.get("tp_r_short")
        raw_suppress_long = vals.get("suppress_long")
        raw_suppress_short = vals.get("suppress_short")
        strategy_params[str(strat_name)] = StrategyOverride(
            tp_r=float(tp_r_val) if tp_r_val is not None else None,
            sl_pct=float(sl_pct_val) if sl_pct_val is not None else None,
            atr_sl_multiplier=float(atr_sl_val) if atr_sl_val is not None else None,
            atr_sl_floor=(
                bool(atr_sl_floor_val) if atr_sl_floor_val is not None else None
            ),
            tp_r_per_tf=tp_r_per_tf,
            tp_r_long_per_tf=tp_r_long_per_tf,
            tp_r_short_per_tf=tp_r_short_per_tf,
            sl_pct_per_tf=sl_pct_per_tf,
            atr_sl_multiplier_per_tf=atr_sl_per_tf,
            atr_sl_floor_per_tf=atr_sl_floor_per_tf,
            per_symbol=per_symbol,
            adr_exempt=bool(vals.get("adr_exempt", False)),
            volume_suppress=bool(raw_vs) if raw_vs is not None else None,
            volume_spike_boost=bool(raw_vsb) if raw_vsb is not None else None,
            volume_suppress_long=bool(raw_vsl) if raw_vsl is not None else None,
            volume_suppress_short=bool(raw_vss) if raw_vss is not None else None,
            volume_spike_boost_long=bool(raw_vsbl) if raw_vsbl is not None else None,
            volume_spike_boost_short=bool(raw_vsbs) if raw_vsbs is not None else None,
            tp_r_long=float(raw_tp_r_long) if raw_tp_r_long is not None else None,
            tp_r_short=float(raw_tp_r_short) if raw_tp_r_short is not None else None,
            suppress_long=bool(raw_suppress_long)
            if raw_suppress_long is not None
            else False,
            suppress_short=bool(raw_suppress_short)
            if raw_suppress_short is not None
            else False,
        )

    raw_bias = data.get("bias", {})
    raw_adr = raw_bias.get("adr_suppress_threshold")

    raw_htf = raw_bias.get("htf_ema", {})
    if not isinstance(raw_htf, dict):
        raise ValueError("[bias.htf_ema] must be a TOML table")
    raw_htf_overrides = raw_htf.get("per_strategy", {})
    if not isinstance(raw_htf_overrides, dict):
        raise ValueError("[bias.htf_ema.per_strategy] must be a TOML table")
    htf_default_tf = str(raw_htf.get("default_tf", "4h"))
    htf_default_period = int(raw_htf.get("default_period", 50))
    htf_default_slope_lb = int(raw_htf.get("default_slope_lookback", 10))
    htf_per_strategy: dict[str, HtfEmaAnchor] = {}
    for strat, ov in raw_htf_overrides.items():
        if not isinstance(ov, dict):
            raise ValueError(
                f"[bias.htf_ema.per_strategy.{strat}] must be a TOML table"
            )
        htf_per_strategy[str(strat)] = HtfEmaAnchor(
            tf=str(ov.get("tf", htf_default_tf)),
            period=int(ov.get("period", htf_default_period)),
            slope_lookback=int(ov.get("slope_lookback", htf_default_slope_lb)),
        )

    raw_dir_filter = raw_bias.get("direction_filter", {})
    if not isinstance(raw_dir_filter, dict):
        raise ValueError("[bias.direction_filter] must be a TOML table")

    raw_regime = raw_bias.get("regime", {})
    if not isinstance(raw_regime, dict):
        raise ValueError("[bias.regime] must be a TOML table")
    raw_regime_enabled_regimes = raw_regime.get("enabled_regimes", {})
    if not isinstance(raw_regime_enabled_regimes, dict):
        raise ValueError("[bias.regime.enabled_regimes] must be a TOML table")
    raw_regime_per_strategy = raw_regime.get("per_strategy", {})
    if not isinstance(raw_regime_per_strategy, dict):
        raise ValueError("[bias.regime.per_strategy] must be a TOML table")
    regime_enabled_regimes: dict[str, list[str]] = {
        str(k): [str(r) for r in v] for k, v in raw_regime_enabled_regimes.items()
    }
    regime_per_strategy: dict[str, list[str]] = {
        str(k): [str(r) for r in v] for k, v in raw_regime_per_strategy.items()
    }

    bias = BiasConfig(
        adr_suppress_threshold=float(raw_adr) if raw_adr is not None else None,
        dow_soft_suppress=bool(raw_bias.get("dow_soft_suppress", False)),
        dow_suppress_min_abs_return=float(
            raw_bias.get("dow_suppress_min_abs_return", 0.005)
        ),
        htf_ema_enabled=bool(raw_htf.get("enabled", False)),
        htf_ema_mode=str(raw_htf.get("mode", "soft")),
        htf_ema_default_tf=htf_default_tf,
        htf_ema_default_period=htf_default_period,
        htf_ema_default_slope_lookback=htf_default_slope_lb,
        htf_ema_deadband_pct=float(raw_htf.get("deadband_pct", 0.003)),
        htf_ema_per_strategy=htf_per_strategy,
        direction_filter_enabled=bool(raw_dir_filter.get("enabled", False)),
        direction_filter_mode=str(raw_dir_filter.get("mode", "soft")),
        regime_enabled=bool(raw_regime.get("enabled", False)),
        regime_mode=str(raw_regime.get("mode", "soft")),
        regime_htf_tf=str(raw_regime.get("htf_tf", "4h")),
        regime_enabled_regimes=regime_enabled_regimes,
        regime_per_strategy=regime_per_strategy,
    )

    raw_combo = data.get("combo", {})
    _default_cross_tf_pairs = ["4h:15m", "4h:1h", "1h:15m", "1d:4h", "1d:1h"]
    combo = ComboConfig(
        window=int(raw_combo.get("window", 5)),
        min_avg_r=float(raw_combo.get("min_avg_r", 1.0)),
        cross_tf_pairs=list(raw_combo.get("cross_tf_pairs", _default_cross_tf_pairs)),
        cross_tf_window_hours=float(raw_combo.get("cross_tf_window_hours", 4.0)),
        cross_tf_min_avg_r=float(raw_combo.get("cross_tf_min_avg_r", 1.0)),
    )

    # Guard the day_filter × timeframe pairing before anything can consume it: a
    # blackout pairing is invisible downstream (see dead_timeframes).
    day_filter = str(data.get("day_filter", "off"))
    timeframes: list[str] = data.get("timeframes", ["4h"])
    dead = dead_timeframes(day_filter, chain(timeframes, *strategy_timeframes.values()))
    if dead:
        raise ValueError(
            f"day_filter={day_filter!r} discards every bar of {dead} — those "
            "timeframes stamp every bar on one weekday, so they would scan and "
            "silently dispatch nothing. Drop them from the config or widen "
            "day_filter."
        )

    # A negative window is not a stricter setting, it is a nonsense one: the
    # comparison it feeds is `age <= window`, so any negative value behaves
    # exactly like 0.0 while READING like a deliberate tightening. Refuse it
    # rather than silently collapsing it to the default.
    max_alert_age_hours = float(data.get("max_alert_age_hours", 0.0))
    if max_alert_age_hours < 0:
        raise ValueError(
            f"max_alert_age_hours={max_alert_age_hours} is negative. The window "
            "is an age bound measured from a candle's close, so a negative value "
            "silently means the same as 0.0 (newest closed candle only) while "
            "looking like a stricter setting. Use 0.0 to mean newest-only."
        )

    # Same defect class, different axis: a volume gate and the ADR gate that select
    # for opposite bars leave a strategy declared-but-voided (see voided_volume_gates).
    voided = voided_volume_gates(strategy_params, bias.adr_suppress_threshold)
    if voided:
        raise ValueError(
            f"{voided} declare volume_suppress* without adr_exempt, while the ADR "
            f"gate is active (adr_suppress_threshold="
            f"{bias.adr_suppress_threshold}). The two gates select for opposite "
            "bars — range and volume correlate at ~+0.65 — so their conjunction "
            "discards ~99% of signals silently and can invert measured avg_r. "
            "Drop the volume flag, or set adr_exempt = true if the strategy's "
            "entry genuinely fires at range extremes."
        )

    return SignalWatchConfig(
        symbols=data.get("symbols"),
        timeframes=timeframes,
        strategies=data.get("strategies"),
        telegram=bool(data.get("telegram", False)),
        min_sl_pct=float(data.get("min_sl_pct", 0.0)),
        tp_r=float(data.get("tp_r", 2.0)),
        sl_pct=float(data.get("sl_pct", 0.02)),
        state_file=str(data.get("state_file", "signal_state.json")),
        backtest=backtest,
        day_filter=day_filter,
        max_alert_age_hours=max_alert_age_hours,
        strategy_timeframes=strategy_timeframes,
        strategy_params=strategy_params,
        atr_sl_multiplier=(
            float(data["atr_sl_multiplier"])
            if data.get("atr_sl_multiplier") is not None
            else None
        ),
        atr_sl_floor=bool(data.get("atr_sl_floor", False)),
        bias=bias,
        combo=combo,
    )
