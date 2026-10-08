"""Measure the correlation deflator for a pooled panel.

``tools/distil_power.py`` **accepts** ``--n-series`` / ``--n-eff`` and deflates
a power calculation by them, but cannot **measure** the second — and
``distil_power.effective_n`` returns ``n_obs`` undeflated when both flags are
omitted. So the deflator fails open on the tool that priced H-004. (H-001/H-002 used a two-sample MDE instead — this tool cannot
price a calendar-cycle claim — but a pooled ``sd`` there carries the same
correlation problem.) This tool supplies that measurement.

Prints the two flags to paste into ``distil_power``, and **refuses to print
them** when the deflator could not actually be measured — an unmeasurable panel
and an uncorrelated one both yield a deflator of 1.0, and emitting flags for the
first would launder "we could not tell" into "no correction needed".

**Coverage is reported, never assumed.** ``4h`` reaches only ~21% of the 505
universe and that subset is size-tilted, so a deflator measured there describes
large caps, not the universe. The banner states requested-vs-returned every run;
read it before quoting the number.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import duckdb
import pandas as pd

from analytics.research_guards import SeriesDeflator, effective_independent_series
from analytics.store import DEFAULT_DB_PATH
from utils.config_validation import load_research_universe, load_stocks_config

SOURCES = ("universe", "stocks", "watchlist")

# Below this many overlapping return observations a pairwise correlation is
# noise dressed as a measurement, so the series is dropped and reported rather
# than silently thinning the panel.
DEFAULT_MIN_OBS = 60

# Coverage below this fraction of the requested set changes what the deflator
# describes, so it is warned about rather than left to the reader to divide.
_COVERAGE_WARN = 0.9

# Name the dropped symbols while the list is short enough to act on.
_NAME_DROPS_UPTO = 10


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="n_eff",
        description="Measure effective independent series for a pooled panel.",
    )
    parser.add_argument("--source", choices=SOURCES, default="universe")
    parser.add_argument(
        "--symbols",
        default=None,
        help="Comma-separated explicit symbol list; overrides --source.",
    )
    parser.add_argument("--timeframe", default="1d")
    parser.add_argument("--min-obs", type=int, default=DEFAULT_MIN_OBS)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    return parser.parse_args(argv)


def resolve_symbols(source: str, symbols: str | None) -> list[str]:
    """Symbol set for the run, from an explicit list or a config universe."""
    if symbols:
        return [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if source == "universe":
        return load_research_universe().active_symbols()
    if source == "stocks":
        return load_research_universe().stocks()
    # Via the real loader, never a hand-rolled json.load: stocks.json is keyed
    # by symbol BUT also carries N1's `universe_policy` block, which a bare
    # `sorted(json.load(f))` returns as if it were a ticker.
    return sorted(load_stocks_config())


def load_returns(
    conn: duckdb.DuckDBPyConnection,
    symbols: Sequence[str],
    timeframe: str,
    min_obs: int,
) -> tuple[dict[str, pd.Series], list[str]]:
    """Per-symbol simple close-to-close returns, plus the symbols dropped.

    A symbol is dropped when it carries fewer than ``min_obs`` usable return
    observations — absent from the DB at this timeframe, or too short to
    correlate. Both cases are returned rather than logged away, because a
    thinned panel changes what the deflator describes.
    """
    if not symbols:
        return {}, []
    placeholders = ",".join("?" for _ in symbols)
    frame = conn.execute(
        "SELECT symbol, open_time, close FROM ohlcv "
        f"WHERE timeframe = ? AND symbol IN ({placeholders}) "
        "ORDER BY symbol, open_time",
        [timeframe, *symbols],
    ).df()
    if frame.empty:
        return {}, list(symbols)
    # Returns are computed per symbol on its own index, never across a shared
    # pivot. A pivot looks equivalent and is not: symbols whose bars sit on
    # different stamp grids turn the union index sparse, and `pct_change` then
    # nulls almost every row because consecutive union rows belong to different
    # symbols. Measured on this DB at `1wk` it silently dropped 505 of 505
    # symbols that the fixed implementation keeps (503 of 505). Alignment is the
    # correlation step's job (pandas pairs columns pairwise), not the return
    # step's.
    kept: dict[str, pd.Series] = {}
    dropped: list[str] = []
    by_symbol = {str(sym): group for sym, group in frame.groupby("symbol", sort=False)}
    for symbol in symbols:
        group = by_symbol.get(symbol)
        if group is None:
            dropped.append(symbol)
            continue
        closes = pd.Series(
            group["close"].to_numpy(), index=group["open_time"].to_numpy()
        )
        series = closes.pct_change(fill_method=None).dropna()
        if len(series) < min_obs:
            dropped.append(symbol)
            continue
        kept[symbol] = series
    return kept, dropped


def render(
    result: SeriesDeflator,
    *,
    requested: int,
    dropped: Sequence[str],
    timeframe: str,
    min_obs: int,
) -> str:
    """Human-readable report; the paste-ready flags are withheld unless measured."""
    lines = [
        "Effective independent series",
        "----------------------------",
        f"  timeframe         {timeframe}",
        f"  symbols requested {requested}",
        f"  series measured   {result.k}"
        f"  ({len(dropped)} dropped at <{min_obs} return obs)",
    ]
    if dropped and len(dropped) <= _NAME_DROPS_UPTO:
        lines.append(f"  dropped           {', '.join(dropped)}")
    coverage = result.k / requested if requested else 1.0
    if coverage < _COVERAGE_WARN:
        lines.append(
            f"  ⚠ COVERAGE {coverage:.0%} — the deflator describes the symbols that"
        )
        lines.append("    had data, not the ones requested.")
        if timeframe == "4h":
            lines.append(
                "    ⚠ The 4h subset is SIZE-TILTED — this describes large caps."
            )
    lines.append("")
    if not result.measured:
        lines.append("  VERDICT: NOT MEASURABLE — no usable pairwise correlation.")
        lines.append(f"  mean pairwise rho   {result.rho}")
        lines.append("")
        lines.append("  No --n-series / --n-eff flags are emitted. A deflator of 1.0")
        lines.append("  here means 'could not tell', never 'no correction needed'.")
        return "\n".join(lines)
    lines.append(f"  mean pairwise rho   {result.rho:+.4f}")
    lines.append(f"  n_eff               {result.n_eff:.3f}  (of {result.k} series)")
    lines.append(f"  t-stat inflation    {result.t_deflator:.3f}x  <- the correction")
    lines.append("")
    lines.append("  Paste into distil_power:")
    lines.append(f"    --n-series {result.k} --n-eff {result.n_eff:.4f}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    symbols = resolve_symbols(args.source, args.symbols)
    conn = duckdb.connect(args.db, read_only=True)
    try:
        kept, dropped = load_returns(conn, symbols, args.timeframe, args.min_obs)
    finally:
        conn.close()
    result = effective_independent_series(kept)
    print(
        render(
            result,
            requested=len(symbols),
            dropped=dropped,
            timeframe=args.timeframe,
            min_obs=args.min_obs,
        )
    )
    return 0 if result.measured else 1


if __name__ == "__main__":
    sys.exit(main())
