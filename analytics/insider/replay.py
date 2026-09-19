"""Read-only DuckDB front door for the H-024 insider sleeve — phase 3.

The only module in ``analytics/insider/`` that touches the DB, and it never
writes. It loads the price and liquidity panels, labels the stored Form 4
population through the phase-2 classifier, and runs the frozen four-trial family
plus its routine-arm placebos through the pure book in
:mod:`analytics.insider.book`.

⚠ **The cost model is read from the shared base, not constructed here.**
``config/strategy_params.toml``'s ``[backtest.cost_model]`` is the same block the
TA backtests price against, so a change there reaches this sleeve; a local
``CostModel()`` would silently fork the moment that block moved. An absent or
disabled block is an error rather than a fallback — the pre-registration prices
costs, and booking a sleeve gross while reporting it as net is the failure this
refuses to make quietly.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import duckdb
import pandas as pd

from analytics.backtest.cost_model import CostModel, cost_model_from_toml
from analytics.forecast.replay import load_daily_inputs
from analytics.insider.book import (
    PLACEBOS,
    STUDY_START,
    TRIALS,
    InsiderInputs,
    book_weights,
    leg_weights,
    trailing_adv,
    trailing_sigma,
)
from analytics.insider.classify import label_transactions
from analytics.store.insider import get_insider_transactions
from analytics.store.market_data import get_ohlcv
from analytics.xsmom.book import XSBookResult
from utils.config_validation import load_research_universe

_FAR_PAST: int = 0
_FAR_FUTURE: int = 9_999_999_999_999

#: Equity-beta benchmark for the realized-β guardrail.
MARKET_PROXY = "SPY"

#: The shared base the cost model is read from.
SHARED_BASE = Path("config/strategy_params.toml")


def shared_base_cost_model(path: Path = SHARED_BASE) -> CostModel:
    """The shared base's ``[backtest.cost_model]``, or raise saying why not."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    cost = cost_model_from_toml(raw.get("backtest", {}).get("cost_model"))
    if cost is None:
        raise ValueError(
            f"{path} has no enabled [backtest.cost_model] block; H-024's "
            "pre-registration prices costs and will not book a sleeve gross"
        )
    return cost


def load_insider_inputs(
    conn: duckdb.DuckDBPyConnection, symbols: list[str]
) -> InsiderInputs:
    """Daily closes and dollar volume for ``symbols``, aligned on a union index.

    Symbols with no ``1d`` bars are skipped — 400 of the 505 research universe
    carry no ``4h`` series and a handful carry no daily one either, and an
    all-NaN column would weight as a real name at formation.
    """
    closes: dict[str, pd.Series] = {}
    dollars: dict[str, pd.Series] = {}
    for sym in symbols:
        bars = get_ohlcv(conn, sym, "1d", _FAR_PAST, _FAR_FUTURE)
        if bars.empty:
            continue
        idx = pd.to_datetime(bars["open_time"], unit="ms", utc=True).dt.normalize()
        close = pd.Series(bars["close"].to_numpy(dtype=float), index=idx)
        volume = pd.Series(bars["volume"].to_numpy(dtype=float), index=idx)
        keep = ~close.index.duplicated(keep="last")
        close, volume = close[keep].sort_index(), volume[keep].sort_index()
        close.index = pd.DatetimeIndex(close.index).tz_localize(None)
        volume.index = pd.DatetimeIndex(volume.index).tz_localize(None)
        closes[sym] = close
        dollars[sym] = close * volume
    if not closes:
        return InsiderInputs(closes=pd.DataFrame(), dollar_volume=pd.DataFrame())
    return InsiderInputs(
        closes=pd.concat(closes, axis=1).sort_index(),
        dollar_volume=pd.concat(dollars, axis=1).sort_index(),
    )


def insider_market_return(conn: duckdb.DuckDBPyConnection) -> pd.Series:
    """SPY daily return — the β-guardrail benchmark (read-only)."""
    closes, _ = load_daily_inputs(conn, [MARKET_PROXY])
    spy = closes.get(MARKET_PROXY)
    if spy is None:
        return pd.Series(dtype=float)
    spy = spy.copy()
    spy.index = pd.DatetimeIndex(spy.index).tz_localize(None)
    return spy.pct_change()


def labelled_transactions(
    conn: duckdb.DuckDBPyConnection, symbols: list[str] | None = None
) -> pd.DataFrame:
    """The stored open-market population with each trade's cohort label.

    Labels are recomputed from the transactions every run — the spec's "derived
    state, never stored" — so the book can never read a label that has drifted
    from the rows it summarises.
    """
    txns = get_insider_transactions(conn, symbols=symbols)
    return label_transactions(txns)


def replay_insider_trials(
    conn: duckdb.DuckDBPyConnection,
    *,
    symbols: list[str] | None = None,
    study_start: str = STUDY_START,
    cost: CostModel | None = None,
    min_history_days: int | None = None,
    inputs: InsiderInputs | None = None,
    labelled: pd.DataFrame | None = None,
) -> dict[str, XSBookResult]:
    """Run the four frozen trials and their four routine placebos.

    Returns ``{"T1".."T4", "P1".."P4"}``; the trials are the DSR family and the
    placebos are controls. Every cell is booked on the same daily index, so a
    trial and its placebo are paired day-for-day, which is what makes the
    reversal observable a paired test rather than a comparison of two samples.

    ``inputs`` and ``labelled`` are accepted pre-loaded so a caller running the
    family more than once — the gross-vs-net read, say — pays the 505-symbol
    panel load once. Passing a DIFFERENT panel than the one the labels were
    drawn from is the caller's error to avoid; nothing here can detect it.
    """
    universe = (
        symbols or load_research_universe(min_history_days=min_history_days).stocks()
    )
    if inputs is None:
        inputs = load_insider_inputs(conn, universe)
    if inputs.closes.empty:
        return {}

    if labelled is None:
        labelled = labelled_transactions(conn, universe)
    model = cost or shared_base_cost_model()
    window = int(round(model.adv_window_days))

    adv = trailing_adv(inputs.dollar_volume, window)
    sigma = trailing_sigma(inputs.closes, window)

    index = pd.DatetimeIndex(inputs.closes.index)
    index = index[index >= pd.Timestamp(study_start)]
    columns = inputs.symbols

    books: dict[str, XSBookResult] = {}
    for key, (label, long_only, hold) in {**TRIALS, **PLACEBOS}.items():
        weights = leg_weights(
            labelled,
            adv,
            index,
            columns,
            label=label,
            long_only=long_only,
            hold_months=hold,
        )
        books[key] = book_weights(weights, inputs.closes, adv, sigma, model)
    return books
