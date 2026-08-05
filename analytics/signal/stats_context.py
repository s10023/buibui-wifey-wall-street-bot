"""Per-symbol StatsContext computation for alert decoration."""

import datetime
import logging

import duckdb

from analytics.signal.types import StatsContext

logger = logging.getLogger(__name__)


def _compute_stats_context(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    now_myt: datetime.datetime,
) -> StatsContext | None:
    """Return a StatsContext for the current symbol/DOW, or None on any error.

    Never raises — stats failure must never block signal dispatch.
    """
    try:
        from analytics.stats_lib import compute_all, compute_weekly_current_state

        bundle = compute_all(conn, symbol, days=90)
        wcs = compute_weekly_current_state(conn, symbol, bundle.adr.adr_14, days=90)
        # DOW must match the UTC-date grouping compute_dow_patterns uses
        # (it buckets 1h bars by epoch_ms(open_time)::DATE, i.e. the UTC day).
        dow_full = datetime.datetime.now(tz=datetime.UTC).strftime("%A")
        dow_short = dow_full[:3]  # e.g. "Thu"

        # P1=Low % for today's DOW
        p1_low_today = bundle.p1p2.by_dow.get(dow_short, bundle.p1p2.overall_p1_low_pct)

        # Today's ADR consumed
        adr_consumed = bundle.adr.today_consumed_pct

        # DOW row for bull% and avg return
        dow_row = next((r for r in bundle.dow.rows if r.dow == dow_short), None)
        bull_pct_today = dow_row.bull_pct if dow_row else 0.5
        avg_return_today = dow_row.avg_return_pct if dow_row else 0.0

        # Per-DOW peak hours
        peak_high_hour_dow = bundle.hourly.peak_high_hour_by_dow.get(dow_short)
        peak_low_hour_dow = bundle.hourly.peak_low_hour_by_dow.get(dow_short)

        # Weekly timing
        wk_low_still_ahead = bundle.weekly_p2_timing.low_still_ahead_by_dow.get(
            dow_short
        )
        wk_high_still_ahead = bundle.weekly_p2_timing.high_still_ahead_by_dow.get(
            dow_short
        )

        return StatsContext(
            today_dow=dow_full,
            p1_low_pct_today=p1_low_today,
            adr_14=bundle.adr.adr_14,
            adr_consumed_pct=adr_consumed,
            peak_high_hour_myt=bundle.hourly.peak_high_hour,
            peak_low_hour_myt=bundle.hourly.peak_low_hour,
            bull_pct_today=bull_pct_today,
            avg_return_today=avg_return_today,
            peak_high_hour_dow=peak_high_hour_dow,
            peak_low_hour_dow=peak_low_hour_dow,
            wk_low_still_ahead_pct=wk_low_still_ahead,
            wk_high_still_ahead_pct=wk_high_still_ahead,
            adr_move_up=bundle.adr.today_move_up,
            wk_low_still_ahead_conditioned_pct=wcs.low_still_ahead_conditioned
            if wcs
            else None,
            wk_high_still_ahead_conditioned_pct=wcs.high_still_ahead_conditioned
            if wcs
            else None,
            wk_move_bucket=wcs.move_bucket if wcs else None,
        )
    except Exception:
        logger.debug("_compute_stats_context failed for %s — skipping", symbol)
        return None
