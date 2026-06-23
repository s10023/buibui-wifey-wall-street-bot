"""Frozen, pre-registered cross-asset ETF basket for edge-hunt #3 (TSMOM).

Pure, no I/O. The basket and its metals/commodity sub-set are committed here as
the pre-registration artifact: changing them after a result exists would
re-introduce selection bias. All 13 ETFs are liquid, free on yfinance, and have
full daily history from 2007-03-01 (UUP, the latest listing).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssetMember:
    """One basket member: ticker + its broad asset-class tag."""

    symbol: str
    asset_class: str


BROAD_BASKET: tuple[AssetMember, ...] = (
    AssetMember("SPY", "equity"),
    AssetMember("QQQ", "equity"),
    AssetMember("EFA", "equity"),
    AssetMember("EEM", "equity"),
    AssetMember("TLT", "rates"),
    AssetMember("IEF", "rates"),
    AssetMember("LQD", "credit"),
    AssetMember("GLD", "metal"),
    AssetMember("SLV", "metal"),
    AssetMember("DBC", "commodity"),
    AssetMember("USO", "commodity"),
    AssetMember("DBA", "commodity"),
    AssetMember("UUP", "fx"),
)

# The narrow contrast arm: metals + commodities only.
_COMMODITY_CLASSES = frozenset({"metal", "commodity"})
COMMODITY_BASKET: tuple[AssetMember, ...] = tuple(
    m for m in BROAD_BASKET if m.asset_class in _COMMODITY_CLASSES
)

# The equity-beta benchmark used by the realized-beta guardrail.
MARKET_PROXY = "SPY"


def broad_symbols() -> list[str]:
    """All 13 basket tickers, in pre-registered order."""
    return [m.symbol for m in BROAD_BASKET]


def commodity_symbols() -> list[str]:
    """The 5 metals/commodity tickers (the narrow contrast arm)."""
    return [m.symbol for m in COMMODITY_BASKET]
