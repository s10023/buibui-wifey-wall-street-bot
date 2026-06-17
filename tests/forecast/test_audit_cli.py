import duckdb

from analytics.store.schema import init_schema
from tests.forecast.test_replay import _seed  # reuse the seeder
from tools.forecast_audit import build_g2_report_row


def test_build_g2_report_row_smoke() -> None:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    syms = ["AAA", "BBB", "CCC", "DDD"]
    for k, s in enumerate(syms):
        _seed(conn, s, 600, k)
    row = build_g2_report_row(conn, "universe @2bps", syms, 2.0)
    assert row["label"] == "universe @2bps"
    days = row["days"]
    assert isinstance(days, int) and days > 0
    for key in ("sharpe", "dsr", "pbo", "boot_lo", "boot_hi", "min_trl"):
        assert key in row
