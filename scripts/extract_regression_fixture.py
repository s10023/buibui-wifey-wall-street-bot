#!/usr/bin/env python3
"""Extract frozen OHLCV fixture files for regression testing.

Run once to seed tests/fixtures/ — re-run only when intentionally refreshing
the fixture window (triggers a mass golden-file regeneration).

Usage:
    poetry run python scripts/extract_regression_fixture.py

Output:
    tests/fixtures/aapl_4h.parquet
    tests/fixtures/aapl_1d.parquet
    tests/fixtures/aapl_1wk.parquet
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from analytics.data_store import DEFAULT_DB_PATH, get_ohlcv  # noqa: E402

SYMBOL = "AAPL"
# Monday after 4h data start (2024-05-16) — gives clean weekly alignment and
# ~2 years of 4h coverage (the limiting timeframe under yfinance).
SINCE = "2024-06-03"
TIMEFRAMES = ["4h", "1d", "1wk"]
OUTPUT_DIR = REPO_ROOT / "tests" / "fixtures"


def main() -> None:
    db_path = REPO_ROOT / DEFAULT_DB_PATH
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    since_ms = int(
        datetime.strptime(SINCE, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000
    )
    now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    symbol_slug = SYMBOL.lower()
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        for tf in TIMEFRAMES:
            df = get_ohlcv(conn, SYMBOL, tf, since_ms, now_ms)
            if df.empty:
                print(f"WARNING: no data for {SYMBOL} {tf} — skipping", file=sys.stderr)
                continue
            # Save string columns as object so pyarrow doesn't re-encode them
            # as large_string on reload (which creates extra ExtensionBlocks
            # that make iloc row-access very slow inside detector loops).
            for col in ("symbol", "timeframe"):
                if col in df.columns:
                    df[col] = df[col].astype(object)
            out = OUTPUT_DIR / f"{symbol_slug}_{tf}.parquet"
            df.to_parquet(out, index=False)
            print(f"  wrote {out.name}  ({len(df)} rows)")
    finally:
        conn.close()

    print(f"\nFixtures written to {OUTPUT_DIR}/")
    print("Next: poetry run pytest tests/test_regression.py --update-golden -v")


if __name__ == "__main__":
    main()
