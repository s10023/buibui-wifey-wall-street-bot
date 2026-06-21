from pathlib import Path

from tools.expand_universe_sp500 import merge_constituents


def test_merge_preserves_existing_and_adds_new(tmp_path: Path) -> None:
    universe = {
        "universe_policy": {"scope": "x", "as_of": "fixed", "survivorship_note": "n"},
        "membership_as_of": "2026-06-16",
        "members": {
            "AAPL": {
                "sector": "Information Technology",
                "kind": "stock",
                "delisted": False,
            },
            "SPY": {"sector": "ETF", "kind": "etf", "delisted": False},
        },
    }
    # constituents = (symbol, sector) rows; AAPL already present, FOO is new.
    constituents = [("AAPL", "Information Technology"), ("FOO", "Industrials")]
    merged, snapshot = merge_constituents(universe, constituents)

    assert snapshot == ["AAPL"]  # pre-existing STOCK symbols only (SPY excluded)
    assert merged["members"]["FOO"] == {
        "sector": "Industrials",
        "kind": "stock",
        "delisted": False,
    }
    # existing entries untouched (idempotent on AAPL, ETF preserved)
    assert merged["members"]["AAPL"]["sector"] == "Information Technology"
    assert merged["members"]["SPY"]["kind"] == "etf"
