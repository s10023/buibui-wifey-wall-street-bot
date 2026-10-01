"""Pure backtest sweep configuration loader.

Loads TOML config from a file and provides a BacktestSweepConfig dataclass.
CLI flags take precedence over config values — the caller is responsible
for merging (see wifey.py run_backtest).
No module-level side effects.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from analytics.backtest.cost_model import CostModel, cost_model_from_toml
from analytics.backtest.live_parity_config import LiveParityConfig

if TYPE_CHECKING:
    from analytics.signal_config import BiasConfig
    from analytics.signal_config import StrategyOverride as LiveStrategyOverride


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
    tp_r_per_tf: dict[str, float] = field(default_factory=dict)
    tp_r_long_per_tf: dict[str, float] = field(default_factory=dict)
    tp_r_short_per_tf: dict[str, float] = field(default_factory=dict)
    sl_pct_per_tf: dict[str, float] = field(default_factory=dict)
    atr_sl_multiplier_per_tf: dict[str, float] = field(default_factory=dict)


@dataclass
class StrategyOverride:
    """Per-strategy parameter overrides.

    Lookup order for each param: symbol+TF → symbol → TF-specific → strategy-wide → global.

    TOML example (sub-table form):
        [strategy_params.bos]
        tp_r_4h = 2.5     # 4h-specific override; other TFs use global tp_r

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
    per_symbol: dict[str, SymbolOverride] = field(default_factory=dict)
    adr_exempt: bool = False
    # None = inherit global volume_suppress; True/False = per-strategy override.
    volume_suppress: bool | None = None
    # None = inherit global volume_spike_boost; True/False = per-strategy override.
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


@dataclass
class BacktestSweepConfig:
    """All configurable options for a backtest sweep."""

    symbols: list[str] | None = None  # None = all from stocks.json
    timeframes: list[str] = field(default_factory=lambda: ["4h"])
    strategies: list[str] | None = None  # None = all non-seasonality strategies
    days: int = 90
    # When set, anchors the backtest window to a fixed start date (YYYY-MM-DD) instead of
    # floating `now - days`. Use `--since 2025-09-12` (data backfill date) for stable,
    # comparable runs that don't drift day-to-day. Overrides `days` when both are set.
    since: str | None = None
    sl_pct: float = 0.02
    tp_r: float = 2.0
    fee_pct: float = 0.0
    min_sl_pct: float = 0.0
    # Global fallback — use effective_min_trades(tf) to get per-TF value.
    # Sweep mode applies this to total closed trades (not directional) for table filtering.
    min_trades: int = 20
    # Per-TF override: check this first, fall back to min_trades.
    # Calibrated from backtest_runs DB (total closed trades, 200d window): 15m→30, 1h→20, 4h→10, 1d→5
    # Note: signal_config.py uses directional trade counts (lower values) for Telegram alerts.
    # Signal rate is NOT uniform — higher TFs fire less frequently per candle.
    # To scale for a different lookback: new_value = base × (your_days / 200)
    min_trades_per_tf: dict[str, int] = field(default_factory=dict)
    # Suppress signals by day: "off" | "weekdays" (Mon–Fri) | "tue_thu" (Tue–Thu only)
    day_filter: str = "off"
    # Persist aggregate results to backtest_runs table in DB
    save_results: bool = False
    # When non-empty, run the full sweep once per value and print a TP ratio comparison
    # table showing avg R per strategy at each tp_r. e.g. [1.0, 1.5, 2.0, 2.5, 3.0]
    # Overrides the single tp_r value for the purpose of comparison only.
    # Note: per-strategy tp_r overrides in strategy_params are ignored in TP sweep mode
    # (you are exploring tp_r space globally). Per-strategy sl_pct overrides still apply.
    tp_r_values: list[float] = field(default_factory=list)
    # Per-strategy parameter overrides (tp_r, sl_pct, TF-specific variants).
    # Lookup order: TF-specific → strategy-wide → global tp_r / sl_pct.
    strategy_params: dict[str, StrategyOverride] = field(default_factory=dict)
    # Global ATR-based SL multiplier (None = use sl_pct instead).
    # When set, SL distance = atr_sl_multiplier × ATR14 at signal candle.
    # Per-strategy overrides in strategy_params take precedence.
    atr_sl_multiplier: float | None = None
    # When non-empty, sweep ATR multiplier values and print a comparison table.
    # e.g. [0.5, 1.0, 1.5, 2.0, 2.5] — shows avg R per strategy × TF at each multiplier.
    # Overrides atr_sl_multiplier for comparison only; tp_r and per-strategy overrides apply.
    atr_sl_multiplier_values: list[float] = field(default_factory=list)
    # F9: use atr_sl_multiplier × ATR14 as a minimum SL distance on top of
    # structural sl_price (max of structural / ATR-derived distance). Required
    # to make the ATR multiplier actually bite on strategies that emit
    # structural sl_price (all current production strategies).
    atr_sl_floor: bool = False
    # ADR bias gate: when set, suppress signals where today's range >= this fraction of ADR-14
    # in the chasing direction (same logic as live BiasConfig.adr_suppress_threshold).
    # Mirrors the [bias] TOML section used by the signal watcher.
    adr_suppress_threshold: float | None = None
    # Global volume suppress fallback. Per-strategy override takes precedence.
    volume_suppress: bool = False
    # Exempt spike candles (volume > 3× rolling mean) from volume_suppress.
    # Default off — enable per-strategy after confirming spike edge via volume split table.
    volume_spike_boost: bool = False
    # T6 backtest-live parity toggles. Defaults to a no-op config so existing
    # callers see no behavioural change. Loaded from `[backtest.live_parity]`.
    live_parity: LiveParityConfig = field(default_factory=LiveParityConfig)
    # Phase 0.4 equity cost model — None = legacy flat fee_pct path. Loaded
    # from `[backtest.cost_model]`; off by default (goldens byte-identical).
    cost_model: CostModel | None = None
    # Live `[bias]` block (regime gate, F8 HTF EMA, direction filter, ADR
    # threshold). Populated by `load_backtest_config()` from the same TOML the
    # signal daemon reads, via `load_signal_config(path).bias`. None when the
    # config is built programmatically without a TOML. Consumed by the T6
    # live-parity gates; default-built sweeps ignore it.
    bias: "BiasConfig | None" = None
    # Live `[strategy_params.*]` block as parsed by signal_config (has
    # suppress_long / suppress_short for the T6 direction_filter gate, plus
    # the live-side adr_exempt_long / adr_exempt_short for later PRs). Kept
    # alongside the backtest-config StrategyOverride above because the two
    # dataclasses have different fields by design — the backtest one carries
    # SL/TP overrides used by the engine; this one carries the live gate flags.
    live_strategy_params: "dict[str, LiveStrategyOverride] | None" = None
    # TOML stem the config was loaded from (e.g. 'signal_watch' for
    # `signal_watch.toml`). Used by the T6 PR-4b conflict_resolver gate to key
    # into the `confidence_ratings` table for the per-config avg_r tiebreaker.
    # None when the config is built programmatically without a TOML.
    config_name: str | None = None

    def effective_min_trades(self, tf: str) -> int:
        return self.min_trades_per_tf.get(tf, self.min_trades)

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

        Steps 1, 4 and 6 need ``direction``, and no production caller passes one: the sweep
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

    def is_adr_exempt(self, strategy: str) -> bool:
        """Return True if this strategy should bypass the ADR bias gate."""
        override = self.strategy_params.get(strategy)
        return override.adr_exempt if override is not None else False

    def effective_volume_suppress(self, strategy: str) -> bool:
        override = self.strategy_params.get(strategy)
        if override is not None and override.volume_suppress is not None:
            return override.volume_suppress
        return self.volume_suppress

    def effective_volume_spike_boost(self, strategy: str) -> bool:
        override = self.strategy_params.get(strategy)
        if override is not None and override.volume_spike_boost is not None:
            return override.volume_spike_boost
        return self.volume_spike_boost

    def effective_volume_suppress_long(self, strategy: str) -> bool | None:
        """Return per-strategy volume_suppress_long override, or None (fall back to symmetric)."""
        override = self.strategy_params.get(strategy)
        if override is not None:
            return override.volume_suppress_long
        return None

    def effective_volume_suppress_short(self, strategy: str) -> bool | None:
        """Return per-strategy volume_suppress_short override, or None (fall back to symmetric)."""
        override = self.strategy_params.get(strategy)
        if override is not None:
            return override.volume_suppress_short
        return None

    def effective_volume_spike_boost_long(self, strategy: str) -> bool | None:
        """Return per-strategy volume_spike_boost_long override, or None (fall back to symmetric)."""
        override = self.strategy_params.get(strategy)
        if override is not None:
            return override.volume_spike_boost_long
        return None

    def effective_volume_spike_boost_short(self, strategy: str) -> bool | None:
        """Return per-strategy volume_spike_boost_short override, or None (fall back to symmetric)."""
        override = self.strategy_params.get(strategy)
        if override is not None:
            return override.volume_spike_boost_short
        return None


def load_backtest_config(path: str | Path) -> BacktestSweepConfig:
    """Load BacktestSweepConfig from a TOML file.

    Raises FileNotFoundError if path does not exist.
    Raises tomllib.TOMLDecodeError if the file is not valid TOML.
    """
    data = _load_toml_with_extends(path)

    per_tf = {
        k[len("min_trades_") :]: int(v)
        for k, v in data.items()
        if k.startswith("min_trades_") and k != "min_trades"
    }
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
        tp_r_val = vals.get("tp_r")
        sl_pct_val = vals.get("sl_pct")
        atr_sl_val = vals.get("atr_sl_multiplier")
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
                sym_tp_r_val = sym_vals.get("tp_r")
                sym_sl_pct_val = sym_vals.get("sl_pct")
                sym_atr_sl_val = sym_vals.get("atr_sl_multiplier")
                per_symbol[sym_key] = SymbolOverride(
                    tp_r=float(sym_tp_r_val) if sym_tp_r_val is not None else None,
                    sl_pct=float(sym_sl_pct_val)
                    if sym_sl_pct_val is not None
                    else None,
                    atr_sl_multiplier=(
                        float(sym_atr_sl_val) if sym_atr_sl_val is not None else None
                    ),
                    tp_r_per_tf=sym_tp_r_per_tf,
                    tp_r_long_per_tf=sym_tp_r_long_per_tf,
                    tp_r_short_per_tf=sym_tp_r_short_per_tf,
                    sl_pct_per_tf=sym_sl_pct_per_tf,
                    atr_sl_multiplier_per_tf=sym_atr_sl_per_tf,
                )
        raw_vs = vals.get("volume_suppress")
        raw_vsb = vals.get("volume_spike_boost")
        raw_vsl = vals.get("volume_suppress_long")
        raw_vss = vals.get("volume_suppress_short")
        raw_vsbl = vals.get("volume_spike_boost_long")
        raw_vsbs = vals.get("volume_spike_boost_short")
        raw_tp_r_long = vals.get("tp_r_long")
        raw_tp_r_short = vals.get("tp_r_short")
        strategy_params[str(strat_name)] = StrategyOverride(
            tp_r=float(tp_r_val) if tp_r_val is not None else None,
            sl_pct=float(sl_pct_val) if sl_pct_val is not None else None,
            atr_sl_multiplier=float(atr_sl_val) if atr_sl_val is not None else None,
            tp_r_per_tf=tp_r_per_tf,
            tp_r_long_per_tf=tp_r_long_per_tf,
            tp_r_short_per_tf=tp_r_short_per_tf,
            sl_pct_per_tf=sl_pct_per_tf,
            atr_sl_multiplier_per_tf=atr_sl_per_tf,
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
        )

    # Some signal_watch configs place flags inside a [backtest] sub-table;
    # fall back to that if not present at the top level.
    _bt_section: dict[str, object] = data.get("backtest", {})

    # volume_suppress: [backtest] sub-table takes precedence over top-level
    _raw_vs = _bt_section.get("volume_suppress", data.get("volume_suppress", False))

    _lp_raw = _bt_section.get("live_parity", {})
    if not isinstance(_lp_raw, dict):
        raise ValueError(
            "backtest.live_parity must be a TOML table (e.g. [backtest.live_parity])"
        )
    _lp_cooldown_raw = _lp_raw.get("cooldown_bars", None)
    _lp_cooldown: dict[str, int] | None
    if _lp_cooldown_raw is None:
        _lp_cooldown = None
    elif isinstance(_lp_cooldown_raw, dict):
        _lp_cooldown = {str(k): int(v) for k, v in _lp_cooldown_raw.items()}
    else:
        raise ValueError(
            "backtest.live_parity.cooldown_bars must be a TOML table of TF=int entries"
        )
    live_parity_cfg = LiveParityConfig(
        enabled=bool(_lp_raw.get("enabled", False)),
        regime=bool(_lp_raw.get("regime", False)),
        direction_filter=bool(_lp_raw.get("direction_filter", False)),
        f8_htf_ema=bool(_lp_raw.get("f8_htf_ema", False)),
        adr_bias=bool(_lp_raw.get("adr_bias", False)),
        conflict_resolver=bool(_lp_raw.get("conflict_resolver", False)),
        cooldown=bool(_lp_raw.get("cooldown", False)),
        cooldown_bars_per_tf=_lp_cooldown,
    )
    cost_model_cfg = cost_model_from_toml(_bt_section.get("cost_model"))

    # Reuse the live-config parser for `[bias]` and `[strategy_params]`
    # (single source of truth for the live gate inputs). Lazy import to keep
    # backtest_config free of analytics.signal_config at module load time.
    from analytics.signal_config import load_signal_config

    _live_cfg = load_signal_config(path)
    bias_cfg = _live_cfg.bias
    live_strategy_params = _live_cfg.strategy_params or None

    return BacktestSweepConfig(
        symbols=data.get("symbols"),
        timeframes=data.get("timeframes", ["4h"]),
        strategies=data.get("strategies"),
        days=int(data.get("days", 90)),
        since=str(data["since"]) if data.get("since") else None,
        sl_pct=float(data.get("sl_pct", 0.02)),
        tp_r=float(data.get("tp_r", 2.0)),
        fee_pct=float(data.get("fee_pct", 0.0)),
        min_sl_pct=float(data.get("min_sl_pct", 0.0)),
        min_trades=int(data.get("min_trades", 20)),
        min_trades_per_tf=per_tf,
        day_filter=str(data.get("day_filter", "off")),
        save_results=bool(data.get("save_results", False)),
        tp_r_values=[float(v) for v in data.get("tp_r_values", [])],
        strategy_params=strategy_params,
        atr_sl_multiplier_values=[
            float(v) for v in data.get("atr_sl_multiplier_values", [])
        ],
        atr_sl_multiplier=(
            float(data["atr_sl_multiplier"])
            if data.get("atr_sl_multiplier") is not None
            else None
        ),
        atr_sl_floor=bool(
            data.get("atr_sl_floor", _bt_section.get("atr_sl_floor", False))
        ),
        adr_suppress_threshold=(
            float(data["bias"]["adr_suppress_threshold"])
            if isinstance(data.get("bias"), dict)
            and data["bias"].get("adr_suppress_threshold") is not None
            else None
        ),
        volume_suppress=bool(_raw_vs),
        volume_spike_boost=bool(
            _bt_section.get("volume_spike_boost", data.get("volume_spike_boost", False))
        ),
        live_parity=live_parity_cfg,
        cost_model=cost_model_cfg,
        bias=bias_cfg,
        config_name=Path(path).stem,
        live_strategy_params=live_strategy_params,
    )
