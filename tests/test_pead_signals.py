"""Edge-hunt #4 (PEAD-lite): SUE math + causal overlapping-cohort leverage."""

from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.pead.signals import seasonal_sue, sue_leverage


def _panel() -> pd.DataFrame:
    """Two names, eight fiscal years of quarters — enough for warmed-up SUEs."""
    rows: list[dict[str, object]] = []
    for sym in ("AAA", "BBB"):
        eps = 1.0
        for fy in range(2015, 2023):
            for i, fp in enumerate(("Q1", "Q2", "Q3", "Q4")):
                eps += 0.05 + 0.03 * np.sin(fy + i)
                month = (2, 5, 8, 11)[i]
                rows.append(
                    {
                        "symbol": sym,
                        "fy": fy,
                        "fp": fp,
                        "announce_date": f"{fy}-{month:02d}-01",
                        "eps_diluted": round(eps, 3),
                    }
                )
    return pd.DataFrame(rows)


def _closes(symbols: tuple[str, ...] = ("AAA", "BBB", "CCC")) -> dict[str, pd.Series]:
    idx = pd.bdate_range("2021-01-01", periods=400)
    rng = np.random.default_rng(0)
    return {
        s: pd.Series(
            100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx)))), index=idx
        )
        for s in symbols
    }


def _sue_panel() -> pd.DataFrame:
    dates = ("2021-03-01", "2021-06-01", "2021-09-01")
    sues = {
        "AAA": (2.0, -1.5, 1.0),
        "BBB": (-1.0, 2.5, -0.5),
        "CCC": (0.5, -2.0, 1.5),
    }
    rows = [
        {"symbol": s, "announce_date": d, "sue": v}
        for s, vals in sues.items()
        for d, v in zip(dates, vals, strict=True)
    ]
    return pd.DataFrame(rows)


def test_seasonal_sue_is_eps_minus_year_ago() -> None:
    eps = pd.DataFrame(
        {
            "symbol": ["AAA"] * 8,
            "fy": [2021, 2021, 2021, 2021, 2022, 2022, 2022, 2022],
            "fp": ["Q1", "Q2", "Q3", "Q4"] * 2,
            "announce_date": pd.to_datetime(
                [
                    "2021-02-01",
                    "2021-05-01",
                    "2021-08-01",
                    "2021-11-01",
                    "2022-02-01",
                    "2022-05-01",
                    "2022-08-01",
                    "2022-11-01",
                ]
            ),
            "eps_diluted": [1.0, 1.1, 1.2, 1.3, 1.2, 1.3, 1.5, 1.6],
        }
    )
    sue = seasonal_sue(eps)
    ue = sue.loc[sue["announce_date"] == pd.Timestamp("2022-02-01"), "ue"].iloc[0]
    assert abs(ue - 0.2) < 1e-9  # 1.2 − 1.0
    # the first fiscal year has no prior-year base → UE is NaN
    assert pd.isna(
        sue.loc[sue["announce_date"] == pd.Timestamp("2021-02-01"), "ue"].iloc[0]
    )


def test_sue_std_uses_only_prior_quarters_no_lookahead() -> None:
    base = _panel()
    sue_base = seasonal_sue(base)
    perturbed = base.copy()
    mx = pd.to_datetime(perturbed["announce_date"]).max()
    perturbed.loc[pd.to_datetime(perturbed["announce_date"]) == mx, "eps_diluted"] = (
        999.0
    )
    sue_pert = seasonal_sue(perturbed)
    earlier = sue_base["announce_date"] < sue_base["announce_date"].max()
    pd.testing.assert_series_equal(
        sue_base.loc[earlier, "sue"], sue_pert.loc[earlier, "sue"]
    )


def test_no_position_on_or_before_announcement() -> None:
    closes = {"AAA": _closes()["AAA"]}
    a = "2021-03-01"
    panel = pd.DataFrame([{"symbol": "AAA", "announce_date": a, "sue": 2.0}])
    lev = sue_leverage(panel, closes, long_only=True, window=60)
    ts = pd.Timestamp(a)
    before = lev.loc[lev.index <= ts, "AAA"]
    assert float(before.fillna(0.0).abs().sum()) == 0.0  # causal: nothing on/before
    after = lev.loc[lev.index > ts, "AAA"].dropna()
    assert len(after) > 0 and after.iloc[0] > 0.0  # enters the next session


def test_long_only_clip_has_no_negative_weight() -> None:
    lev = sue_leverage(_sue_panel(), _closes(), long_only=True, window=60)
    assert bool((lev.fillna(0.0) >= -1e-12).all().all())


def test_long_short_is_dollar_neutral_each_day() -> None:
    lev = sue_leverage(_sue_panel(), _closes(), long_only=False, window=60)
    day_sums = lev.sum(axis=1).dropna().to_numpy()
    assert np.allclose(day_sums, 0.0, atol=1e-9)
