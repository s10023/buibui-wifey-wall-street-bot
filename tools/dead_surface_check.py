"""Report (strategy × timeframe) cells where declaration and output disagree.

The recurring defect class in this repo is a **declared surface that nothing
executes and nothing asserts about**. Emptiness is indistinguishable from
coverage: `signal_watch.toml` scanned `1wk` under `tue_thu` for three months
while `backtest_runs` held 338 rows for it — the surface *looked* covered, and
every one of those rows had zero closed trades.

A config declares a cell for every (strategy × timeframe) pair it will scan.
That set and the set of cells the system carries state for should be the same
set, and this checks both directions of the mismatch:

**Dead cells** — declared but silent. Walks the declared set, joins it against
what `backtest_runs` recorded, and reports any cell whose detector has *never
fired* across the whole history and universe. Not a thin sample: a declaration
the system cannot honour, costing detector work every scan cycle and returning
nothing.

**Orphaned ratings** — rated but undeclared, the exact inverse. `recalibrate`
rebuilds `confidence_ratings` from historical `backtest_runs` with no notion
of what the config currently declares, while `upsert_confidence_ratings` only
ever inserts-or-replaces. A cell dropped from a config therefore keeps its stars
and collects a *fresh timestamp on a stale value* on every refresh. Found
2026-08-06: `fib_golden_zone × 4h` sat at 3★ +0.4688 — the second-highest-rated
cell in the `signal_watch` table — 2.5 months after the strategy was removed.
Ratings are a displayed surface (Backtest UI stars, the Telegram star line) and
a live one (the backtest conflict resolver's tiebreaker), so an orphan is not
merely cosmetic. Note the asymmetry with dead cells: a dead cell shows up as a
zero and reads as absence, whereas an orphan shows up as a *number* and reads
as evidence.

Orphans are reported in **two tiers**, because the two live configs partition the
calendar by `day_filter` and a cell can be undeclared by the config that rates it
while the other config still declares it. Both are orphans *for that config* —
that daemon will never scan the cell — but the fix differs: `declared by another
config` is a rating filed under the wrong day filter, whereas `undeclared
anywhere` means nothing scans it at all. The tier only labels a finding; it never
suppresses one. The sister repo's check has a direction-aware half, omitted
here: it exists upstream because all three of its configs carry
`strategy_timeframes_long` / `_short` narrowing, and this repo declares no such
key, so `declared_cells` is direction-agnostic here and a direction-aware check
would report exactly the same set.

Deliberately data-driven rather than static: `signal_config.dead_timeframes`
already refuses the *structurally* impossible pairings it knows about (a day
filter that excludes a fixed-open-weekday timeframe). This catches the ones
nobody predicted — a detector that simply never triggers on a timeframe, or a
rating outliving the declaration that produced it.

Usage:
    make check-dead-surfaces
    poetry run python tools/dead_surface_check.py [--config PATH ...] [--strict]

Exit codes: 0 = no unexpected dead cells and no orphaned ratings, 1 = at least
one (or a bad invocation).
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

from analytics.signal_config import (  # noqa: E402
    SignalWatchConfig,
    declared_cells,
    load_signal_config,
)
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
# Empty: every declared cell in both configs produces signals. The five
# original entries were resolved, and the diagnosis first recorded for three of
# them was wrong, which is worth keeping as a caution:
#
#   doji × {1d, 1wk}  — annotated "near-inert everywhere ... worth a detector
#       review". The detector was fine: it fires 1,247 times on 1d across the 13
#       live symbols. `volume_suppress` in conjunction with the ADR gate was
#       discarding ~100% of its output. Removing that flag revived all three
#       cells. See docs/audits/2026-08-06-adr-volume-gate-conjunction.md.
#   {eqh_eql, ema} × 1wk — this one held up: genuine bar scarcity under both day
#       filters, so both are not declared in signal_watch_weekdays.toml.
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


@dataclass(frozen=True)
class OrphanRating:
    """A `confidence_ratings` row for a cell its own config does not declare."""

    config: str
    strategy: str
    timeframe: str
    direction: str
    stars: int
    avg_r: float | None
    declared_elsewhere: bool = False

    @property
    def tier(self) -> str:
        """Which of the two orphan diagnoses this row is.

        Both mean the rating's own daemon will never scan the cell, so both are
        orphans. They differ in the fix: a cell another config still declares is
        a *config split* — the rating is attached to the wrong day filter — while
        `undeclared anywhere` means nothing scans it at all and the stars are
        pure residue.
        """
        return (
            "declared by another config"
            if self.declared_elsewhere
            else "undeclared anywhere"
        )

    def __str__(self) -> str:
        avg_r = "—" if self.avg_r is None else f"{self.avg_r:+.4f}"
        return (
            f"{self.strategy} × {self.timeframe} [{self.direction}] — "
            f"{self.stars}★ avg_r={avg_r}, {self.tier}"
        )


def find_orphan_ratings(
    conn: duckdb.DuckDBPyConnection,
    cfg: SignalWatchConfig,
    config_name: str,
    elsewhere: set[tuple[str, str]] | None = None,
) -> list[OrphanRating]:
    """Rated cells the config does not declare, worst-first by displayed stars.

    `config_name` is the TOML stem (`signal_watch`), which is what
    `confidence_ratings.config_name` stores — not the path the CLI takes.

    `elsewhere` is the union of the cells the *other* checked configs declare. It
    only splits the findings into tiers; it never suppresses one. Passing `None`
    reports every orphan as `undeclared anywhere`, which is right for a
    single-config run because there is no other config to have declared it.
    """
    declared = set(declared_cells(cfg))
    others = elsewhere or set()
    rows = conn.execute(
        "SELECT strategy, tf, direction, stars, avg_r FROM confidence_ratings "
        "WHERE config_name = ? ORDER BY stars DESC, avg_r DESC",
        [config_name],
    ).fetchall()
    return [
        OrphanRating(
            config=config_name,
            strategy=str(strategy),
            timeframe=str(tf),
            direction=str(direction),
            stars=int(stars),
            avg_r=None if avg_r is None else float(avg_r),
            declared_elsewhere=(str(strategy), str(tf)) in others,
        )
        for strategy, tf, direction, stars, avg_r in rows
        if (str(strategy), str(tf)) not in declared
    ]


def cells_declared_elsewhere(
    declared_by_config: dict[str, set[tuple[str, str]]],
    config: str,
) -> set[tuple[str, str]]:
    """Every cell declared by a config OTHER than `config`.

    Extracted from `main` rather than inlined so a test can reach it: the union
    is the whole of the tier decision, and a defect here would silently downgrade
    every orphan to one tier without changing the pass/fail outcome that the
    end-to-end run asserts.
    """
    return set[tuple[str, str]]().union(
        *(cells for name, cells in declared_by_config.items() if name != config)
    )


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

    # Load every config first: an orphan's tier is a statement about the OTHER
    # configs, so it cannot be decided while walking them one at a time.
    loaded = {config: load_signal_config(config) for config in configs}
    declared_by_config = {c: set(declared_cells(cfg)) for c, cfg in loaded.items()}

    all_dead: list[DeadCell] = []
    all_orphans: list[OrphanRating] = []
    with duckdb.connect(str(db_path), read_only=True) as conn:
        for config in configs:
            cfg = loaded[config]
            elsewhere = cells_declared_elsewhere(declared_by_config, config)
            found = find_dead_cells(conn, cfg, config)
            all_dead.extend(found)
            orphans = find_orphan_ratings(conn, cfg, Path(config).stem, elsewhere)
            all_orphans.extend(orphans)
            n_declared = len(declared_cells(cfg))
            print(
                f"\n{config}  (day_filter={cfg.day_filter}, {n_declared} cells declared)"
            )
            if found:
                for cell in found:
                    mark = "  " if cell.key in _KNOWN_DEAD_CELLS else "❌"
                    known = " [known]" if cell.key in _KNOWN_DEAD_CELLS else ""
                    print(f"  {mark} {cell}{known}")
            else:
                print("  ✅ every declared cell produces signals")
            if orphans:
                for orphan in orphans:
                    print(f"  ❌ {orphan}")
            else:
                print("  ✅ every rated cell is declared")

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
    if all_orphans:
        nowhere = [o for o in all_orphans if not o.declared_elsewhere]
        split = [o for o in all_orphans if o.declared_elsewhere]
        print(
            f"FAIL: {len(all_orphans)} orphaned confidence rating(s) "
            f"({len(nowhere)} undeclared anywhere, {len(split)} declared by another config):"
        )
        for orphan in all_orphans:
            print(f"  - {orphan.config}: {orphan}")
        print(
            "\nA rating for an undeclared cell is displayed in the UI and the Telegram\n"
            "star line, and feeds the backtest conflict resolver, for a cell the daemon\n"
            "will never scan. Run `make db-update-recalibrate` to prune them."
        )
        if split:
            print(
                "\nThe `declared by another config` rows are the milder tier: some daemon\n"
                "still scans that cell, just not the one the rating is filed under. Check\n"
                "the rating's day_filter before pruning."
            )
    if failing or all_orphans:
        return 1

    n_known = len(all_dead)
    print(
        "OK: no unexpected dead cells and no orphaned ratings"
        + (f" ({n_known} known-dead, allowlisted)" if n_known else "")
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
