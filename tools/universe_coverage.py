"""Universe coverage report (N3) — read-only OHLCV coverage over the research
breadth universe.

For every (member symbol × timeframe) in config/universe.json, reports bars
present in the analytics DB, the date range, and which members are missing. The
report is headed by the universe policy's lifecycle-bias caveat so every reader
sees the bounded-claim flag. Read-only — no writes, no schema changes.

Usage::

    PYTHONPATH=. poetry run python tools/universe_coverage.py
    PYTHONPATH=. poetry run python tools/universe_coverage.py --timeframes 4h 1d 1wk
    PYTHONPATH=. poetry run python tools/universe_coverage.py --db analytics.db
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from analytics.store import DEFAULT_DB_PATH
from utils.config_validation import ResearchUniverse, load_research_universe

_DEFAULT_TFS = ["4h", "1d", "1wk"]


@dataclass(frozen=True)
class CoverageRow:
    symbol: str
    timeframe: str
    kind: str
    sector: str
    bars: int
    first_ms: int | None
    last_ms: int | None

    @property
    def present(self) -> bool:
        return self.bars > 0


@dataclass(frozen=True)
class CoverageSummary:
    n_symbols: int
    n_timeframes: int
    missing_symbols: list[str]


def _fmt_date(ms: int | None) -> str:
    if ms is None:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def build_coverage_rows(
    conn: duckdb.DuckDBPyConnection,
    universe: ResearchUniverse,
    timeframes: list[str],
) -> list[CoverageRow]:
    """One CoverageRow per (member, timeframe) — bars present + date range."""
    rows: list[CoverageRow] = []
    for member in universe.members:
        for tf in timeframes:
            res = conn.execute(
                """
                SELECT COUNT(*) AS n, MIN(open_time) AS first_ms,
                       MAX(open_time) AS last_ms
                FROM ohlcv
                WHERE symbol = ? AND timeframe = ?
                """,
                [member.symbol, tf],
            ).fetchone()
            n = int(res[0]) if res is not None else 0
            first_ms = int(res[1]) if res is not None and res[1] is not None else None
            last_ms = int(res[2]) if res is not None and res[2] is not None else None
            rows.append(
                CoverageRow(
                    symbol=member.symbol,
                    timeframe=tf,
                    kind=member.kind,
                    sector=member.sector,
                    bars=n,
                    first_ms=first_ms,
                    last_ms=last_ms,
                )
            )
    return rows


def summarize(rows: list[CoverageRow], timeframes: list[str]) -> CoverageSummary:
    """Roll up coverage rows: symbol count + symbols missing ALL timeframes."""
    symbols = sorted({r.symbol for r in rows})
    missing = sorted(
        sym for sym in symbols if all(not r.present for r in rows if r.symbol == sym)
    )
    return CoverageSummary(
        n_symbols=len(symbols),
        n_timeframes=len(timeframes),
        missing_symbols=missing,
    )


def _print_report(
    universe: ResearchUniverse,
    rows: list[CoverageRow],
    summary: CoverageSummary,
) -> None:
    print(universe.describe())
    print()
    cols = ["symbol", "kind", "tf", "bars", "first", "last"]
    table = [
        (
            r.symbol,
            r.kind,
            r.timeframe,
            str(r.bars),
            _fmt_date(r.first_ms),
            _fmt_date(r.last_ms),
        )
        for r in rows
    ]
    widths = [
        max(len(c), max((len(row[i]) for row in table), default=0))
        for i, c in enumerate(cols)
    ]
    print(" | ".join(c.ljust(w) for c, w in zip(cols, widths, strict=True)))
    print("-+-".join("-" * w for w in widths))
    for row in table:
        print(" | ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)))
    print()
    covered = summary.n_symbols - len(summary.missing_symbols)
    print(
        f"Coverage: {covered}/{summary.n_symbols} members have data in "
        f"≥1 of {summary.n_timeframes} timeframe(s)."
    )
    if summary.missing_symbols:
        print(f"  Missing (no data any TF): {', '.join(summary.missing_symbols)}")
        print(
            "  → backfill with: poetry run python wifey.py analytics backfill "
            "--universe --timeframes 4h 1d 1wk --since 2018-01-01"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="DuckDB path")
    parser.add_argument(
        "--universe-path",
        type=Path,
        default=Path("config/universe.json"),
        help="Path to the research universe config",
    )
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=_DEFAULT_TFS,
        help="Timeframes to report (default: 4h 1d 1wk)",
    )
    args = parser.parse_args()

    universe = load_research_universe(args.universe_path)
    conn = duckdb.connect(str(args.db), read_only=True)
    try:
        rows = build_coverage_rows(conn, universe, args.timeframes)
    finally:
        conn.close()
    summary = summarize(rows, args.timeframes)
    _print_report(universe, rows, summary)


if __name__ == "__main__":
    main()
