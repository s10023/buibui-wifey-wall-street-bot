from analytics.xasset.universe import (
    BROAD_BASKET,
    COMMODITY_BASKET,
    MARKET_PROXY,
    broad_symbols,
    commodity_symbols,
)


def test_broad_basket_has_13_unique_symbols() -> None:
    syms = broad_symbols()
    assert len(syms) == 13
    assert len(set(syms)) == 13  # no duplicate tickers


def test_commodity_basket_is_a_strict_subset() -> None:
    broad = set(broad_symbols())
    commodity = set(commodity_symbols())
    assert commodity == {"GLD", "SLV", "DBC", "USO", "DBA"}
    assert commodity < broad  # strict subset


def test_market_proxy_is_in_the_basket() -> None:
    assert MARKET_PROXY in broad_symbols()


def test_every_member_has_a_nonempty_asset_class() -> None:
    assert len(BROAD_BASKET) == 13
    assert len(COMMODITY_BASKET) == 5
    for m in BROAD_BASKET:
        assert m.asset_class
