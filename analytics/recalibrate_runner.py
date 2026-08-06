"""Recalibration runner — thin wrapper: opens DB, calls lib, prints report.

No business logic here. All logic lives in recalibrate_lib.py.
"""

import argparse
from pathlib import Path

import duckdb

from analytics.data_store import DEFAULT_DB_PATH, init_schema
from analytics.recalibrate_lib import (
    compute_directional_ratings,
    compute_recalibrated_ratings,
    format_recalibration_report,
    get_backtest_win_rates,
    write_confidence_to_db,
    write_confidence_to_source,
)
from analytics.strategies import STRATEGY_REGISTRY

_REGISTRY_PATH = Path(__file__).parent / "strategies" / "_registry.py"


def run(
    args: argparse.Namespace,
    db_path: Path = DEFAULT_DB_PATH,
    source_path: Path = _REGISTRY_PATH,
) -> None:
    """Open DB, compute recalibrated ratings, print report.

    --dry-run (default): show what would change without modifying anything.
    --apply with --config: write ratings to confidence_ratings DB table keyed by config name.
    --apply without --config: legacy — patch confidence=N values directly in analytics/strategies/_registry.py.
    """
    apply: bool = getattr(args, "apply", False)
    min_trades: int = getattr(args, "min_trades", 10)
    config_path: str | None = getattr(args, "config", None)
    day_filter: str | None = getattr(args, "day_filter", None)
    config_name: str | None = None

    adr_suppress_threshold: float | None = None
    # Cells the config actually scans. None in legacy source-patching mode
    # (no --config), where there is no config to be undeclared by.
    declared: list[tuple[str, str]] | None = None

    if config_path:
        from analytics.signal_config import declared_cells, load_signal_config

        watch_cfg = load_signal_config(config_path)
        day_filter = watch_cfg.day_filter
        config_name = Path(config_path).stem
        adr_suppress_threshold = watch_cfg.bias.adr_suppress_threshold
        declared = declared_cells(watch_cfg)

    conn: duckdb.DuckDBPyConnection = duckdb.connect(str(db_path))
    try:
        init_schema(conn)
        win_rates = get_backtest_win_rates(
            conn,
            day_filter=day_filter,
            adr_suppress_threshold=adr_suppress_threshold,
            declared=declared,
        )
        new_ratings = compute_recalibrated_ratings(
            conn,
            min_trades=min_trades,
            day_filter=day_filter,
            adr_suppress_threshold=adr_suppress_threshold,
            declared=declared,
        )
        dir_ratings = compute_directional_ratings(
            conn,
            min_trades=max(min_trades // 2, 2),
            day_filter=day_filter,
            adr_suppress_threshold=adr_suppress_threshold,
            declared=declared,
        )

        old_ratings = {
            name: spec.confidence for name, spec in STRATEGY_REGISTRY.items()
        }
        report = format_recalibration_report(
            old_ratings, new_ratings, win_rates, directional_ratings=dir_ratings
        )
        print(report)

        if apply:
            if config_name:
                # Runs even when new_ratings is empty: an orphaned rating is
                # exactly a row no current run can overwrite, so gating the
                # prune on there being something to write would leave the
                # worst case — nothing rated, everything stale — untouched.
                pruned = write_confidence_to_db(
                    conn,
                    config_name,
                    new_ratings,
                    win_rates,
                    day_filter=day_filter,
                    directional_ratings=dir_ratings,
                    declared=declared,
                )
                if pruned:
                    print(
                        f"\n  Pruned {len(pruned)} rating(s) for cells "
                        f"'{config_name}' no longer declares:"
                    )
                    for strategy, tf, direction in sorted(pruned):
                        print(f"    {strategy} × {tf} [{direction}]")
                if not new_ratings:
                    print(
                        "\n  Nothing rated — no declared cell had sufficient backtest data."
                    )
                    return
                print(
                    f"\n  Written to confidence_ratings table for config '{config_name}'."
                )
                for name in sorted(new_ratings):
                    print(f"    {name}: {new_ratings[name]}")
                print("\n  Restart signal watch to pick up the new ratings.")
            else:
                if not new_ratings:
                    print(
                        "\n  Nothing to apply — no strategies had sufficient backtest data."
                    )
                    return
                # Legacy: patch indicators_lib.py source directly.
                patched = write_confidence_to_source(new_ratings, source_path)
                print(f"\n  Patched {len(patched)} strategy/ies in {source_path.name}.")
                for name in sorted(patched):
                    print(f"    {name}: {new_ratings[name]}")
                if patched:
                    print("\n  Restart signal watch to pick up the new ratings.")
        else:
            if config_name:
                print(
                    f"\n  Dry-run mode — use --apply to write to DB for config '{config_name}'."
                )
            else:
                print(
                    "\n  Dry-run mode — no changes applied."
                    " Use --apply to write ratings to analytics/strategies/_registry.py."
                    " Pass --config to write per-config ratings to DB instead."
                )
    finally:
        conn.close()
