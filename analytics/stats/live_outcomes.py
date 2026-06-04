"""Live signal-alert outcome statistics (cross-symbol).

Graduates the read-only CLI stop-gap ``tools/live_outcomes_report.py`` into the
Stats page. Reads the live ``signal_alert_outcomes`` ledger (populated by the
signal daemon's outcome writer + backfill worker) and returns:

- a roll-up: total / resolved / open mix + win/loss/expired counts (all-time —
  ``open_no_tp`` is a data-integrity gauge that should read 0 after the
  outcome-ledger SL/TP fallback fix);
- per-(strategy, tf, direction) win-rate / avg-R cells (windowed by ``days``);
- a per-strategy roll-up (windowed by ``days``), ordered by avg-R desc.

Unlike the per-symbol StatsBundle cards this aggregates across all symbols, so
it lives in its own router and is never cached. Empty ledger is a valid state
(no alerts fired yet) — returns a zero roll-up rather than raising.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import duckdb


@dataclass
class LiveOutcomesRollup:
    """All-time integrity + outcome roll-up over the ledger."""

    total_rows: int
    resolved: int
    open: int
    open_no_tp: int  # open AND tp_price IS NULL — should be 0 post-fix
    wins: int
    losses: int
    expired: int


@dataclass
class LiveOutcomeCell:
    """Per-(strategy, tf, direction) resolved-trade breakdown."""

    strategy: str
    tf: str
    direction: str
    n: int
    wins: int
    losses: int
    expired: int
    win_rate: float | None  # wins / (wins + losses); expired excluded
    avg_r: float | None  # mean outcome_r over all resolved rows in the cell


@dataclass
class LiveOutcomeStrategyRow:
    """Per-strategy resolved-trade roll-up across TFs and directions."""

    strategy: str
    n: int
    win_rate: float | None
    avg_r: float | None


@dataclass
class LiveOutcomesResult:
    """Full live-outcomes payload for the Stats card."""

    days: int  # window applied to cells/by_strategy (0 = all time)
    min_n: int  # minimum resolved rows per cell/strategy
    rollup: LiveOutcomesRollup
    cells: list[LiveOutcomeCell]
    by_strategy: list[LiveOutcomeStrategyRow]


def compute_live_outcomes(
    conn: duckdb.DuckDBPyConnection,
    days: int = 30,
    min_n: int = 1,
) -> LiveOutcomesResult:
    """Compute the live-outcomes roll-up + breakdowns.

    ``days`` windows only the per-cell / per-strategy tables (0 = all time); the
    roll-up is always all-time so ``open_no_tp`` stays a true integrity gauge.
    Never raises on empty data — returns a zero roll-up.
    """
    totals = conn.execute(
        """
        SELECT
          COUNT(*)                                     AS total_rows,
          COUNT(*) FILTER (WHERE outcome IS NOT NULL)  AS resolved,
          COUNT(*) FILTER (WHERE outcome IS NULL)      AS open_rows,
          COUNT(*) FILTER (WHERE outcome IS NULL
                           AND tp_price IS NULL)       AS open_no_tp,
          COUNT(*) FILTER (WHERE outcome = 'win')      AS wins,
          COUNT(*) FILTER (WHERE outcome = 'loss')     AS losses,
          COUNT(*) FILTER (WHERE outcome = 'expired')  AS expired
        FROM signal_alert_outcomes
        """
    ).fetchone()

    rollup = LiveOutcomesRollup(
        total_rows=int(totals[0]) if totals else 0,
        resolved=int(totals[1]) if totals else 0,
        open=int(totals[2]) if totals else 0,
        open_no_tp=int(totals[3]) if totals else 0,
        wins=int(totals[4]) if totals else 0,
        losses=int(totals[5]) if totals else 0,
        expired=int(totals[6]) if totals else 0,
    )

    where = "WHERE outcome IS NOT NULL"
    params: list[object] = []
    if days > 0:
        cutoff_ms = int((time.time() - days * 86_400) * 1000)
        where += " AND fired_at_ms >= ?"
        params.append(cutoff_ms)

    cell_rows = conn.execute(
        f"""
        SELECT
          strategy, tf, direction,
          COUNT(*)                                  AS n,
          COUNT(*) FILTER (WHERE outcome='win')     AS wins,
          COUNT(*) FILTER (WHERE outcome='loss')    AS losses,
          COUNT(*) FILTER (WHERE outcome='expired') AS expired,
          AVG(CASE WHEN outcome='win'  THEN 1.0
                   WHEN outcome='loss' THEN 0.0 END) AS win_rate,
          AVG(outcome_r)                            AS avg_r
        FROM signal_alert_outcomes
        {where}
        GROUP BY strategy, tf, direction
        HAVING COUNT(*) >= ?
        ORDER BY strategy, tf, direction
        """,
        (*params, min_n),
    ).fetchall()

    cells = [
        LiveOutcomeCell(
            strategy=str(s),
            tf=str(tf),
            direction=str(direction),
            n=int(n),
            wins=int(wins),
            losses=int(losses),
            expired=int(expired),
            win_rate=None if win_rate is None else float(win_rate),
            avg_r=None if avg_r is None else float(avg_r),
        )
        for (s, tf, direction, n, wins, losses, expired, win_rate, avg_r) in cell_rows
    ]

    strat_rows = conn.execute(
        f"""
        SELECT
          strategy,
          COUNT(*) AS n,
          AVG(CASE WHEN outcome='win'  THEN 1.0
                   WHEN outcome='loss' THEN 0.0 END) AS win_rate,
          AVG(outcome_r)                            AS avg_r
        FROM signal_alert_outcomes
        {where}
        GROUP BY strategy
        HAVING COUNT(*) >= ?
        ORDER BY avg_r DESC NULLS LAST, strategy
        """,
        (*params, min_n),
    ).fetchall()

    by_strategy = [
        LiveOutcomeStrategyRow(
            strategy=str(s),
            n=int(n),
            win_rate=None if win_rate is None else float(win_rate),
            avg_r=None if avg_r is None else float(avg_r),
        )
        for (s, n, win_rate, avg_r) in strat_rows
    ]

    return LiveOutcomesResult(
        days=days,
        min_n=min_n,
        rollup=rollup,
        cells=cells,
        by_strategy=by_strategy,
    )
