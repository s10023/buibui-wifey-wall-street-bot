"""Tests for the pure parts of tools/warning_value_audit.py."""

from __future__ import annotations

import pandas as pd
import pytest

from analytics.warning_audit import WARNING_KEYS, WarningVerdict
from tools.warning_value_audit import (
    _tf_ms,
    exploratory_by_tf,
    format_report,
    normalize_backtest,
    normalize_live,
)


def _verdict(warning: str, direction: str, verdict: str) -> WarningVerdict:
    return WarningVerdict(
        warning=warning,
        direction=direction,
        n_warned=100,
        n_clean=400,
        avg_warned=-0.2,
        avg_clean=0.1,
        ci_lo=-0.3,
        ci_hi=-0.1,
        adj_pvalue=0.01,
        n_tests=8,
        lift_lo=-0.4,
        lift_hi=-0.2,
        raw_decision="ENABLE",
        verdict=verdict,
        reasons=[],
    )


class TestTimeframeLength:
    """The fork-specific guard: upstream's literal map spells weekly ``1w``.

    Every equity surface here uses yfinance's ``1wk``, and ``1wk`` carries
    backtest trades (it is one of the three timeframes in ``backtest_trades``)
    while never being scanned live. A copied ``_TF_MS`` map therefore raises
    ``KeyError`` on real data and on no fixture — which is exactly the shape
    that ships green. Delegating to ``parse_timeframe_secs`` is what removes it.
    """

    @pytest.mark.parametrize(
        ("tf", "expected_ms"),
        [
            ("4h", 4 * 60 * 60_000),
            ("1d", 24 * 60 * 60_000),
            ("1wk", 7 * 24 * 60 * 60_000),
            ("1h", 60 * 60_000),
        ],
    )
    def test_known_timeframes(self, tf: str, expected_ms: int) -> None:
        assert _tf_ms(tf) == expected_ms

    def test_every_timeframe_in_the_backtest_substrate_resolves(self) -> None:
        # The three that actually appear in backtest_trades. A KeyError here is
        # the defect this class exists for, not a missing fixture.
        for tf in ("4h", "1d", "1wk"):
            assert _tf_ms(tf) > 0

    def test_unknown_timeframe_raises_valueerror_not_keyerror(self) -> None:
        with pytest.raises(ValueError, match="unknown timeframe"):
            _tf_ms("banana")


class TestNormalizers:
    def test_normalize_live_shape(self) -> None:
        raw = pd.DataFrame(
            {
                "symbol": ["SPY", "QQQ"],
                "tf": ["4h", "1d"],
                "strategy": ["fvg", "bos"],
                "direction": ["long", "short"],
                "candle_ts_ms": [1000, None],
                "outcome_r": [0.5, 1.0],
            }
        )
        out = normalize_live(raw)
        assert list(out.columns) == [
            "symbol",
            "tf",
            "strategy",
            "direction",
            "ts_ms",
            "r",
        ]
        assert len(out) == 1  # null candle_ts_ms dropped

    def test_normalize_backtest_dedups_across_runs(self) -> None:
        raw = pd.DataFrame(
            {
                "run_id": ["run_a", "run_b", "run_a"],
                "symbol": ["SPY"] * 3,
                "timeframe": ["4h"] * 3,
                "strategy": ["fvg"] * 3,
                "direction": ["long"] * 3,
                "signal_time": [1000, 1000, 2000],
                "pnl_r": [0.5, 0.9, -0.2],
            }
        )
        out = normalize_backtest(raw)
        assert len(out) == 2  # duplicate signal collapsed
        kept = out[out["ts_ms"] == 1000].iloc[0]
        assert kept["r"] == 0.9  # latest run_id wins
        assert "run_id" not in out.columns


class TestExploratory:
    def test_by_tf_rows(self) -> None:
        tagged = pd.DataFrame(
            {
                "symbol": ["SPY"] * 4,
                "tf": ["4h", "4h", "1d", "1d"],
                "strategy": ["fvg"] * 4,
                "direction": ["long"] * 4,
                "ts_ms": [1, 2, 3, 4],
                "r": [1.0, -1.0, 0.5, 0.5],
            }
        )
        for key in WARNING_KEYS:
            tagged[key] = False
        tagged.loc[0, "w7_doji"] = True
        out = exploratory_by_tf(tagged)
        row = out[(out["warning"] == "w7_doji") & (out["tf"] == "4h")].iloc[0]
        assert row["n_warned"] == 1
        assert row["avg_warned"] == 1.0
        assert row["avg_clean"] == -1.0


class TestFormatReport:
    def test_headline_and_tables(self) -> None:
        verdicts = [
            _verdict("w7_doji", "long", "SUPPRESS-CANDIDATE"),
            _verdict("w1_marubozu", "short", "COSMETIC"),
        ]
        expl = pd.DataFrame(
            [
                {
                    "warning": "w7_doji",
                    "direction": "long",
                    "tf": "4h",
                    "n_warned": 10,
                    "avg_warned": -0.2,
                    "n_clean": 40,
                    "avg_clean": 0.1,
                    "lift": -0.3,
                }
            ]
        )
        report = format_report(
            {"backtest": (verdicts, expl, 500, 12)},
            min_n=30,
            bar=0.05,
            alpha=0.05,
            n_boot=100,
            seed=1,
        )
        assert "SUPPRESS-CANDIDATE: w7_doji/long" in report
        assert "| --- |" in report  # markdownlint-conformant delimiters
        assert "500 tagged" in report
