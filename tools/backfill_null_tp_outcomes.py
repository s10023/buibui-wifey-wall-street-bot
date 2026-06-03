"""Retroactively reconstruct SL/TP for NULL-tp `signal_alert_outcomes` rows.

Before the forward fix (specs/2026-06-01-outcome-ledger-sl-tp-fallback-design.md),
the live outcome-ledger writer persisted NULL `sl_price`/`tp_price` whenever an
event carried no valid structural SL — leaving ~89% of fired alerts unscoreable
forever. The structural SL itself was never stored, so it is unrecoverable; this
tool reconstructs the *pct fallback* SL/TP (the same one the forward fix and the
alert formatter use) from the stored `entry_price` + the per-(strategy, symbol,
tf, direction) `eff_sl_pct`/`eff_tp_r` resolved from a live config TOML, then
lets the existing forward-walk resolver (`backfill_outcomes`) score them.

Read-only by default — prints counts + a sample. Pass ``--apply`` to write the
reconstructed SL/TP and resolve outcomes. Idempotent: once a row carries a
tp_price it is no longer a NULL candidate.

Reconstructed rows always use the pct fallback, so a row whose original event
*did* have a valid structural SL will get a slightly different R than the alert
showed. Forward rows are exact; retro rows are best-effort.

Usage::

    PYTHONPATH=. poetry run python tools/backfill_null_tp_outcomes.py            # dry-run
    PYTHONPATH=. poetry run python tools/backfill_null_tp_outcomes.py --apply
    PYTHONPATH=. poetry run python tools/backfill_null_tp_outcomes.py --config config/signal_watch.toml
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb

from analytics.signal.outcome_backfill import backfill_outcomes
from analytics.signal.resolvers import _resolve_sl_pct, _resolve_tp_r
from analytics.signal.scanner import _resolve_outcome_sl_tp
from analytics.signal_config import StrategyOverride, load_signal_config
from analytics.store import DEFAULT_DB_PATH

_DEFAULT_CONFIG = "config/signal_watch.toml"


def reconstruct_null_outcomes(
    conn: duckdb.DuckDBPyConnection,
    *,
    sl_pct: float,
    tp_r: float,
    min_sl_pct: float,
    strategy_params: dict[str, StrategyOverride] | None,
    apply: bool = False,
    now_ms: int | None = None,
) -> dict[str, int]:
    """Reconstruct pct-fallback SL/TP for NULL-tp, unresolved outcome rows.

    When ``apply`` is True, writes the reconstructed sl_price/tp_price/rr_ratio
    and then runs ``backfill_outcomes`` to score them. Returns a counts dict.
    """
    rows = conn.execute(
        "SELECT signal_id, symbol, tf, strategy, direction, entry_price "
        "FROM signal_alert_outcomes "
        "WHERE tp_price IS NULL AND outcome IS NULL"
    ).fetchall()

    counts = {
        "null_rows": len(rows),
        "reconstructed": 0,
        "skipped_no_entry": 0,
    }
    updates: list[tuple[float, float, float, str]] = []
    for signal_id, symbol, tf, strategy, direction, entry_price in rows:
        if entry_price is None:
            counts["skipped_no_entry"] += 1
            continue
        eff_sl_pct = _resolve_sl_pct(strategy_params, strategy, symbol, tf, sl_pct)
        eff_tp_r = _resolve_tp_r(strategy_params, strategy, symbol, tf, tp_r, direction)
        ev_sl, ev_tp = _resolve_outcome_sl_tp(
            direction=str(direction),
            entry=float(entry_price),
            struct_sl=0.0,  # original structural SL not stored → pct fallback
            struct_tp=0.0,
            eff_sl_pct=eff_sl_pct,
            min_sl_pct=min_sl_pct,
            tp_r=eff_tp_r,
        )
        counts["reconstructed"] += 1
        updates.append((ev_sl, ev_tp, eff_tp_r, str(signal_id)))

    if apply and updates:
        conn.executemany(
            "UPDATE signal_alert_outcomes "
            "SET sl_price = ?, tp_price = ?, rr_ratio = ? WHERE signal_id = ?",
            updates,
        )
        resolved = backfill_outcomes(
            conn, now_ms=now_ms if now_ms is not None else int(time.time() * 1000)
        )
        for k, v in resolved.items():
            counts[f"resolved_{k}"] = v

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help=f"Path to analytics DB (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--config",
        default=_DEFAULT_CONFIG,
        help=f"Live config TOML for sl_pct/tp_r/strategy_params (default: {_DEFAULT_CONFIG}).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write reconstructed SL/TP + resolve. Without this flag, dry-run only.",
    )
    args = parser.parse_args()

    cfg = load_signal_config(Path(args.config))
    conn = duckdb.connect(args.db_path)
    counts = reconstruct_null_outcomes(
        conn,
        sl_pct=cfg.sl_pct,
        tp_r=cfg.tp_r,
        min_sl_pct=cfg.min_sl_pct,
        strategy_params=cfg.strategy_params or None,
        apply=args.apply,
    )

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== NULL-tp outcome backfill ({mode}) — config {args.config} ===")
    print(f"NULL-tp unresolved rows : {counts['null_rows']}")
    print(f"reconstructable         : {counts['reconstructed']}")
    print(f"skipped (no entry_price): {counts['skipped_no_entry']}")
    if args.apply:
        for k in ("win", "loss", "expired", "open", "no_ohlcv"):
            print(f"resolved {k:<8}       : {counts.get('resolved_' + k, 0)}")
    else:
        print("\n(dry-run — re-run with --apply to write; review first)")


if __name__ == "__main__":
    main()
