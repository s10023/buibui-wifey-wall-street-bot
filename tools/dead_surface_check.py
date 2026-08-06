"""Report declared (strategy × timeframe) cells that produce no signals at all.

The recurring defect class in this repo is a **declared surface that nothing
executes and nothing asserts about**. Emptiness is indistinguishable from
coverage: `signal_watch.toml` scanned `1wk` under `tue_thu` for three months
while `backtest_runs` held 338 rows for it — the surface *looked* covered, and
every one of those rows had zero closed trades (#139).

A config declares a cell for every (strategy × timeframe) pair it will scan.
This walks that declared set, joins it against what `backtest_runs` actually
recorded, and reports any cell whose detector has **never fired** across the
whole history and universe. A cell like that is not a thin sample — it is a
declaration the system cannot honour, costing detector work every scan cycle
and returning nothing.

Deliberately data-driven rather than static: `signal_config.dead_timeframes`
already refuses the *structurally* impossible pairings it knows about (a day
filter that excludes a fixed-open-weekday timeframe). This catches the ones
nobody predicted — a detector that simply never triggers on a timeframe.

Usage:
    make check-dead-surfaces
    poetry run python tools/dead_surface_check.py [--config PATH ...] [--strict]

Exit codes: 0 = no unexpected dead cells, 1 = at least one (or a bad invocation).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT))

from analytics.signal_config import SignalWatchConfig, load_signal_config  # noqa: E402
from analytics.store import DEFAULT_DB_PATH  # noqa: E402

DEFAULT_CONFIGS = (
    "config/signal_watch.toml",
    "config/signal_watch_weekdays.toml",
)

# Cells accepted as dead, kept visible rather than silently tolerated. Mirrors
# `tests/test_lookahead.py::_KNOWN_LOOKAHEAD_DETECTORS`, which shrank to empty
# once its findings were fixed. **This list should only ever shrink** — a new
# entry means a new dead surface was accepted, which is the thing this module
# exists to prevent.
#
# Keyed (day_filter, strategy, timeframe) because a cell can be dead under one
# day filter and healthy under another.
#
# EMPTY as of 2026-08-06 — every declared cell in both configs now produces
# signals. The five original entries were resolved, and the diagnosis recorded
# here for three of them was WRONG, which is worth keeping as a caution:
#
#   doji × {1d, 1wk}  — annotated "near-inert everywhere ... worth a detector
#       review". The detector was fine: it fires 1,247 times on 1d across the 13
#       live symbols. `volume_suppress` in conjunction with the ADR gate was
#       discarding ~100% of its output. Removing that flag revived all three
#       cells. See docs/audits/2026-08-06-adr-volume-gate-conjunction.md.
#   {eqh_eql, ema} × 1wk — this one held up: genuine bar scarcity under BOTH day
#       filters, so both were dropped from signal_watch_weekdays.toml.
#
# The caution: a zero-signal cell looks identical whatever zeroed it, so the
# reason recorded beside an entry is a hypothesis until it is traced. This check
# reliably finds dead cells; it cannot diagnose them.
_KNOWN_DEAD_CELLS: frozenset[tuple[str, str, str]] = frozenset()


@dataclass(frozen=True)
class DeadCell:
    """A declared cell whose detector never fired."""

    config: str
    day_filter: str
    strategy: str
    timeframe: str
    reason: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.day_filter, self.strategy, self.timeframe)

    def __str__(self) -> str:
        return f"{self.strategy} × {self.timeframe} [{self.day_filter}] — {self.reason}"


def declared_cells(cfg: SignalWatchConfig) -> list[tuple[str, str]]:
    """(strategy, timeframe) pairs the config will scan, in declaration order.

    A strategy listed in `strategy_timeframes` is restricted to those timeframes;
    every other strategy runs on the config's full `timeframes` list.
    """
    cells: list[tuple[str, str]] = []
    for strategy in cfg.strategies or []:
        for tf in cfg.strategy_timeframes.get(strategy, cfg.timeframes):
            if (strategy, tf) not in cells:
                cells.append((strategy, tf))
    return cells


def find_dead_cells(
    conn: duckdb.DuckDBPyConnection,
    cfg: SignalWatchConfig,
    config_name: str,
) -> list[DeadCell]:
    """Declared cells with no backtest runs, or runs that produced zero signals."""
    dead: list[DeadCell] = []
    for strategy, tf in declared_cells(cfg):
        row = conn.execute(
            "SELECT count(*), coalesce(sum(total_signals), 0) FROM backtest_runs "
            "WHERE strategy = ? AND timeframe = ? AND day_filter = ?",
            [strategy, tf, cfg.day_filter],
        ).fetchone()
        n_runs, n_signals = (0, 0) if row is None else (int(row[0]), int(row[1]))
        if n_runs == 0:
            reason = "never backtested (no runs)"
        elif n_signals == 0:
            reason = f"detector never fired ({n_runs} runs, 0 signals)"
        else:
            continue
        dead.append(DeadCell(config_name, cfg.day_filter, strategy, tf, reason))
    return dead


def unexpected(cells: list[DeadCell]) -> list[DeadCell]:
    """Dead cells that are not on the known-dead allowlist."""
    return [c for c in cells if c.key not in _KNOWN_DEAD_CELLS]


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--config",
        dest="configs",
        action="append",
        default=None,
        help=f"config TOML to check (repeatable; default: {', '.join(DEFAULT_CONFIGS)})",
    )
    p.add_argument("--db", default=str(DEFAULT_DB_PATH), help="analytics DB path")
    p.add_argument(
        "--strict",
        action="store_true",
        help="also fail on allowlisted cells (use to verify the allowlist can shrink)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    configs = args.configs or list(DEFAULT_CONFIGS)

    db_path = Path(args.db)
    if not db_path.exists():
        print(
            f"ERROR: analytics DB not found at {db_path}. Run `make db-update` first."
        )
        return 1

    all_dead: list[DeadCell] = []
    with duckdb.connect(str(db_path), read_only=True) as conn:
        for config in configs:
            cfg = load_signal_config(config)
            found = find_dead_cells(conn, cfg, config)
            all_dead.extend(found)
            n_declared = len(declared_cells(cfg))
            print(
                f"\n{config}  (day_filter={cfg.day_filter}, {n_declared} cells declared)"
            )
            if not found:
                print("  ✅ every declared cell produces signals")
                continue
            for cell in found:
                mark = "  " if cell.key in _KNOWN_DEAD_CELLS else "❌"
                known = " [known]" if cell.key in _KNOWN_DEAD_CELLS else ""
                print(f"  {mark} {cell}{known}")

    failing = all_dead if args.strict else unexpected(all_dead)
    print()
    if failing:
        print(f"FAIL: {len(failing)} dead cell(s) not on the known-dead allowlist:")
        for cell in failing:
            print(f"  - {cell.config}: {cell}")
        print(
            "\nA declared cell that never fires costs detector work every scan cycle "
            "and returns nothing.\nEither drop it from the config, or add it to "
            "_KNOWN_DEAD_CELLS with a reason."
        )
        return 1

    n_known = len(all_dead)
    print(
        "OK: no unexpected dead cells"
        + (f" ({n_known} known-dead, allowlisted)" if n_known else "")
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
