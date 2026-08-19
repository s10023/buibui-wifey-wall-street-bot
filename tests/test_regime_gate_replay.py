"""Unit tests for the regime gate backtest replay (`tools/regime_gate_replay.py`).

Covers the pure logic — time alignment, suppression labelling, aggregation —
so the verdict on the live DB run can be trusted.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from analytics.signal_config import BiasConfig
from tools.regime_gate_replay import (
    FLIP_BLOCKED,
    FLIP_HOLD,
    FLIP_JUSTIFIED,
    MAPPING_CLEAN,
    MAPPING_FIX,
    MAPPING_HOLD,
    _regimes_at_entries,
    aggregate,
    annotate_regime_4h,
    annotate_suppression,
    attach_verdicts,
    build_audit_cells,
    build_kept_audit_cells,
    evaluate_cells,
    evaluate_kept_cells,
    flip_verdict,
    mapping_verdict,
    render_verdict,
)

# An RTH equity 4h tape: two bars a day, stamped 13:30 and 17:30 UTC. NONE of
# these is a multiple of 4h from UTC midnight — 0 of 105,708 such bars in the
# live DB are — which is precisely what the old modulo floor assumed.
_MIDNIGHT_2026_01_01 = 1_767_225_600_000
_DAY_MS = 86_400_000
_RTH_OFFSETS_MS = (48_600_000, 63_000_000)  # 13:30, 17:30
_FOUR_HOURS_MS = 4 * 60 * 60 * 1000  # crypto tape, for the non-regression case


def _rth_open_times(n_bars: int) -> list[int]:
    out: list[int] = []
    day = 0
    while len(out) < n_bars:
        for off in _RTH_OFFSETS_MS:
            out.append(_MIDNIGHT_2026_01_01 + day * _DAY_MS + off)
        day += 1
    return out[:n_bars]


def _bias() -> BiasConfig:
    return BiasConfig(
        regime_enabled=True,
        regime_mode="hard",
        regime_htf_tf="4h",
        regime_enabled_regimes={
            "trend": ["trend"],
            "fib": ["trend"],
            "structural": ["trend", "range", "high_vol"],
        },
        regime_per_strategy={"bos": ["trend"]},
    )


class TestRegimesAtEntries:
    """The bar-alignment contract. Every case here is an RTH tape unless named
    otherwise, because the tape this repo actually reads is never UTC-aligned."""

    def _labels(self, n: int) -> pd.Series:
        return pd.Series([f"r{i}" for i in range(n)], dtype=object)

    def test_rth_entry_resolves_to_previous_closed_bar(self) -> None:
        opens = _rth_open_times(6)
        entry = opens[3] + 42 * 60_000  # 42 min into bar 3
        got = _regimes_at_entries(pd.Series([entry]), pd.Series(opens), self._labels(6))
        assert got.iloc[0] == "r2"  # bar 3 is in progress → last CLOSED is bar 2

    def test_rth_entry_does_NOT_fall_open(self) -> None:
        """Positive control for the defect this replaces.

        The modulo floor returned a key no RTH bar has, so every lookup missed
        and `fillna` produced "unknown" — a fall-open indistinguishable from a
        genuine cache miss. Asserting "not unknown" is the only assertion that
        fails on the old implementation, so it is the one that must exist.
        """
        opens = _rth_open_times(8)
        entries = pd.Series([o + 60_000 for o in opens[2:]])
        got = _regimes_at_entries(entries, pd.Series(opens), self._labels(8))
        assert (got != "unknown").all()

    def test_entry_at_exact_bar_open_uses_the_prior_bar(self) -> None:
        opens = _rth_open_times(5)
        got = _regimes_at_entries(
            pd.Series([opens[4]]), pd.Series(opens), self._labels(5)
        )
        assert got.iloc[0] == "r3"

    def test_entry_before_any_closed_bar_is_unknown(self) -> None:
        opens = _rth_open_times(4)
        got = _regimes_at_entries(
            pd.Series([opens[0] - 1]), pd.Series(opens), self._labels(4)
        )
        assert got.iloc[0] == "unknown"

    def test_empty_bar_series_is_unknown(self) -> None:
        got = _regimes_at_entries(
            pd.Series([_MIDNIGHT_2026_01_01]),
            pd.Series([], dtype="int64"),
            pd.Series([], dtype=object),
        )
        assert got.iloc[0] == "unknown"

    def test_utc_aligned_crypto_tape_still_resolves(self) -> None:
        """Non-regression for the 24/7 shape the old floor was written for."""
        opens = [i * _FOUR_HOURS_MS for i in range(6)]
        entry = opens[3] + 30 * 60_000
        got = _regimes_at_entries(pd.Series([entry]), pd.Series(opens), self._labels(6))
        assert got.iloc[0] == "r2"


class TestRegimeAnnotation:
    def _conn_with_rth_bars(self, n_bars: int) -> duckdb.DuckDBPyConnection:
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE ohlcv (symbol TEXT, timeframe TEXT, open_time BIGINT, "
            "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE)"
        )
        for i, ot in enumerate(_rth_open_times(n_bars)):
            close = 100.0 + i  # steady uptrend so the classifier commits a label
            conn.execute(
                "INSERT INTO ohlcv VALUES ('AAPL', '4h', ?, ?, ?, ?, ?, 1000)",
                [ot, close, close + 1.0, close - 1.0, close],
            )
        return conn

    def test_rth_trade_resolves_to_a_real_regime(self) -> None:
        """End-to-end positive control: the replay must SEE a regime.

        The predecessor asserted "unknown" on 5 bars — below the classifier's
        minimum — so it passed both before and after the bug, and the tool
        shipped reporting 0 suppressed of 2,849 live trades.
        """
        n = 400
        conn = self._conn_with_rth_bars(n)
        entry = _rth_open_times(n)[-1] + 60_000
        trades = pd.DataFrame(
            {
                "strategy": ["ema"],
                "symbol": ["AAPL"],
                "timeframe": ["4h"],
                "direction": ["long"],
                "entry_time": [entry],
                "pnl_r": [0.5],
            }
        )
        out = annotate_regime_4h(trades, conn)
        assert out["regime"].iloc[0] != "unknown"

    def test_missing_ohlcv_falls_open(self) -> None:
        conn = duckdb.connect(":memory:")
        conn.execute(
            "CREATE TABLE ohlcv (symbol TEXT, timeframe TEXT, open_time BIGINT, "
            "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume DOUBLE)"
        )
        trades = pd.DataFrame(
            {
                "strategy": ["ema"],
                "symbol": ["NOSUCH"],
                "timeframe": ["4h"],
                "direction": ["long"],
                "entry_time": [_MIDNIGHT_2026_01_01],
                "pnl_r": [0.5],
            }
        )
        out = annotate_regime_4h(trades, conn)
        assert out["regime"].iloc[0] == "unknown"


class TestSuppressionLabelling:
    def _row(self, strategy: str, regime: str) -> pd.DataFrame:
        return pd.DataFrame(
            {"strategy": [strategy], "regime": [regime], "pnl_r": [0.0]}
        )

    def test_continuation_in_range_is_suppressed(self) -> None:
        # ema (type=trend) → only allowed in trend → suppressed in range.
        out = annotate_suppression(self._row("ema", "range"), _bias().regime_allowed)
        assert out["suppressed"].iloc[0] is True or out["suppressed"].iloc[0] == True  # noqa: E712

    def test_reversion_in_range_is_kept(self) -> None:
        # fvg (type=structural) → enabled in range.
        out = annotate_suppression(self._row("fvg", "range"), _bias().regime_allowed)
        assert not out["suppressed"].iloc[0]

    def test_unknown_regime_falls_open(self) -> None:
        out = annotate_suppression(self._row("ema", "unknown"), _bias().regime_allowed)
        assert not out["suppressed"].iloc[0]

    def test_per_strategy_override_bos(self) -> None:
        # bos overridden to ["trend"] — suppressed in range despite type=structural.
        out = annotate_suppression(self._row("bos", "range"), _bias().regime_allowed)
        assert out["suppressed"].iloc[0]


class TestAggregate:
    def test_aggregate_groups_by_strategy_regime_suppressed(self) -> None:
        trades = pd.DataFrame(
            {
                "strategy": ["ema", "ema", "ema", "ema"],
                "regime": ["range", "range", "trend", "trend"],
                "suppressed": [True, True, False, False],
                "pnl_r": [-0.5, -0.3, 1.0, 0.5],
            }
        )
        agg = aggregate(trades)
        assert len(agg) == 2  # (ema, range, suppressed) + (ema, trend, kept)
        suppressed_row = agg[agg["suppressed"]].iloc[0]
        kept_row = agg[~agg["suppressed"]].iloc[0]
        assert suppressed_row["n"] == 2
        assert suppressed_row["avg_r"] == -0.4
        assert kept_row["n"] == 2
        assert kept_row["avg_r"] == 0.75


def _cell_trades(
    spec: list[tuple[str, str, bool, list[float]]],
) -> pd.DataFrame:
    """Build a trades frame from (strategy, regime, suppressed, pnl_r list)."""
    rows: list[dict[str, object]] = []
    for strategy, regime, suppressed, values in spec:
        for value in values:
            rows.append(
                {
                    "strategy": strategy,
                    "regime": regime,
                    "suppressed": suppressed,
                    "pnl_r": value,
                }
            )
    return pd.DataFrame(rows)


def _tight(mean: float, n: int) -> list[float]:
    """``n`` values averaging ``mean`` with small, non-zero dispersion.

    Non-zero on purpose: a zero-variance slice makes ``_slice_sharpe`` infinite,
    which audit_guard treats as maximally significant — that would let a cell
    pass on a degenerate input rather than on the effect under test.
    """
    return [mean - 0.05 if i % 2 else mean + 0.05 for i in range(n)]


def _pooled_avg_r(agg: pd.DataFrame, *, suppressed: bool) -> float:
    rows = agg[agg["suppressed"]] if suppressed else agg[~agg["suppressed"]]
    return float((rows["avg_r"] * rows["n"]).sum() / rows["n"].sum())


class TestBuildAuditCells:
    def test_only_suppressed_cells_become_audit_cells(self) -> None:
        trades = _cell_trades(
            [
                ("bos", "trend", True, _tight(-0.5, 40)),
                ("bos", "range", False, _tight(0.2, 40)),
            ]
        )
        built = build_audit_cells(trades)
        assert [(s, r) for s, r, _ in built] == [("bos", "trend")]

    def test_kept_slice_is_the_same_strategys_surviving_book(self) -> None:
        """``kept_r`` must be that strategy's kept trades — never the cell's own,
        which is empty by construction, and never another strategy's."""
        trades = _cell_trades(
            [
                ("bos", "trend", True, _tight(-0.5, 40)),
                ("bos", "range", False, _tight(0.2, 30)),
                ("ema", "range", False, _tight(9.0, 25)),
            ]
        )
        [(_, _, cell)] = build_audit_cells(trades)
        assert len(cell.kept_r) == 30
        assert abs(float(sum(cell.kept_r) / len(cell.kept_r)) - 0.2) < 1e-9

    def test_empty_frame_yields_no_cells(self) -> None:
        assert build_audit_cells(pd.DataFrame()) == []


class TestFlipVerdictCombinesCellsNotPools:
    """The single-global-switch rule. ``[bias.regime].mode`` flips every cell at
    once, so these tests pin that ONE reliably-winning cell vetoes the flip."""

    def test_one_blocking_cell_vetoes_a_dominant_losing_aggregate(self) -> None:
        """POSITIVE CONTROL — this fixture satisfies the OLD pooled rule's FLIP
        condition exactly, and must still come back blocked.

        Both halves are asserted. Without the pooled assertions the test would
        pass against a pooling implementation too, and so could not detect a
        revert — which is the failure mode this test exists for.
        """
        trades = _cell_trades(
            [
                # Large losing cell — carries any n-weighted mean.
                ("bos", "trend", True, _tight(-0.5, 400)),
                # Small reliable winner the global switch would also suppress.
                ("ema", "high_vol", True, _tight(0.6, 60)),
                ("bos", "range", False, _tight(0.1, 50)),
                ("ema", "range", False, _tight(0.1, 50)),
            ]
        )

        # The old rule's precondition: pooled suppressed <= 0 AND kept > suppressed.
        agg = aggregate(trades)
        pooled_supp = _pooled_avg_r(agg, suppressed=True)
        pooled_kept = _pooled_avg_r(agg, suppressed=False)
        assert pooled_supp <= 0, "fixture must satisfy the pooled FLIP condition"
        assert pooled_kept > pooled_supp, (
            "fixture must satisfy the pooled FLIP condition"
        )

        cells = evaluate_cells(trades)
        decision, reasons = flip_verdict(cells)
        assert decision == FLIP_BLOCKED
        blocking = [c for c in cells if c.regime == "high_vol"]
        assert [c.verdict.decision for c in blocking] == ["DISABLE"]
        assert any("ONE GLOBAL SWITCH" in r for r in reasons)

    def test_all_losing_cells_justify_the_flip(self) -> None:
        trades = _cell_trades(
            [
                ("bos", "trend", True, _tight(-0.5, 60)),
                ("ema", "high_vol", True, _tight(-0.6, 60)),
                ("bos", "range", False, _tight(0.1, 50)),
                ("ema", "range", False, _tight(0.1, 50)),
            ]
        )
        cells = evaluate_cells(trades)
        decision, _ = flip_verdict(cells)
        assert decision == FLIP_JUSTIFIED
        assert {c.verdict.decision for c in cells} == {"ENABLE"}

    def test_undersized_cells_hold_rather_than_flip(self) -> None:
        trades = _cell_trades(
            [
                ("bos", "trend", True, _tight(-0.5, 5)),
                ("bos", "range", False, _tight(0.1, 50)),
            ]
        )
        cells = evaluate_cells(trades)
        decision, reasons = flip_verdict(cells)
        assert decision == FLIP_HOLD
        assert [c.verdict.decision for c in cells] == ["INSUFFICIENT"]
        assert any("cannot tell" in r for r in reasons)

    def test_hold_never_claims_the_gate_is_harmless(self) -> None:
        """HOLD is an absence of evidence, and must say so — collapsing it into a
        null is the ``powered_null`` failure audit_guard exists to prevent."""
        trades = _cell_trades(
            [
                ("bos", "trend", True, _tight(-0.5, 5)),
                ("bos", "range", False, _tight(0.1, 50)),
            ]
        )
        _, reasons = flip_verdict(evaluate_cells(trades))
        assert any("NOT evidence" in r for r in reasons)

    def test_no_cells_holds(self) -> None:
        decision, _ = flip_verdict([])
        assert decision == FLIP_HOLD


class TestRenderVerdict:
    def test_pooled_aggregates_are_labelled_non_decision_bearing(self) -> None:
        trades = _cell_trades(
            [
                ("bos", "trend", True, _tight(-0.5, 400)),
                ("ema", "high_vol", True, _tight(0.6, 60)),
                ("bos", "range", False, _tight(0.1, 50)),
                ("ema", "range", False, _tight(0.1, 50)),
            ]
        )
        agg = aggregate(trades)
        out = render_verdict(agg, evaluate_cells(trades))
        assert "DESCRIPTIVE aggregates — NOT decision-bearing" in out
        # The decision line reflects the cells, not the dominant losing aggregate.
        assert FLIP_BLOCKED in out
        assert "ema" in out and "high_vol" in out


class TestAttachVerdicts:
    """The exported CSV must carry the decision-bearing columns, not just the
    descriptive aggregate — a reader with only the latter cannot re-derive the
    verdict the banner printed."""

    def _frame(self) -> pd.DataFrame:
        return _cell_trades(
            [
                ("bos", "trend", True, _tight(-0.5, 60)),
                ("ema", "high_vol", True, _tight(0.6, 60)),
                ("bos", "range", False, _tight(0.1, 50)),
            ]
        )

    def test_tested_cells_carry_verdict_and_ci(self) -> None:
        trades = self._frame()
        out = attach_verdicts(aggregate(trades), evaluate_cells(trades))
        tested = out[out["suppressed"]]
        assert set(tested["verdict"]) == {"ENABLE", "DISABLE"}
        assert tested["ci_lo"].notna().all()
        assert tested["adj_pvalue"].notna().all()

    def test_kept_rows_are_null_by_construction(self) -> None:
        """Kept cells are never tested, so their verdict is absent rather than
        blank — collapsing the two would read as an untested cell having passed."""
        trades = self._frame()
        out = attach_verdicts(aggregate(trades), evaluate_cells(trades))
        kept = out[~out["suppressed"]]
        assert len(kept) == 1
        assert kept["verdict"].isna().all()
        assert kept["ci_lo"].isna().all()

    def test_verdict_binds_to_its_own_cell(self) -> None:
        """Positive control against a row-order mismatch: the join is by
        (strategy, regime), so a reordered aggregate must still line up."""
        trades = self._frame()
        out = attach_verdicts(aggregate(trades), evaluate_cells(trades))
        row = out[(out["strategy"] == "ema") & (out["regime"] == "high_vol")].iloc[0]
        assert row["verdict"] == "DISABLE"
        assert float(row["ci_lo"]) > 0


class TestBuildKeptAuditCells:
    """The mirror of :class:`TestBuildAuditCells`. A KEPT cell asks whether the
    mapping should START suppressing it — the question the flip verdict is
    structurally unable to reach."""

    def test_only_kept_cells_become_kept_audit_cells(self) -> None:
        trades = _cell_trades(
            [
                ("bos", "trend", True, _tight(-0.5, 40)),
                ("bos", "range", False, _tight(0.2, 40)),
            ]
        )
        built = build_kept_audit_cells(trades)
        assert [(s, r) for s, r, _ in built] == [("bos", "range")]

    def test_kept_slice_is_the_strategys_OTHER_kept_cells(self) -> None:
        """``kept_r`` is the surviving book under the change being considered:
        the same strategy's other KEPT cells — never the cell's own trades,
        never another strategy's, and never an already-suppressed cell, which no
        reachable config would trade."""
        trades = _cell_trades(
            [
                ("bos", "high_vol", False, _tight(-0.4, 40)),
                ("bos", "range", False, _tight(0.2, 30)),
                ("bos", "trend", True, _tight(-0.9, 50)),  # suppressed: excluded
                ("ema", "range", False, _tight(9.0, 25)),  # other strategy: excluded
            ]
        )
        cell = next(c for s, r, c in build_kept_audit_cells(trades) if r == "high_vol")
        assert len(cell.supp_r) == 40
        assert len(cell.kept_r) == 30
        assert abs(float(sum(cell.kept_r) / len(cell.kept_r)) - 0.2) < 1e-9

    def test_sole_kept_cell_has_an_empty_counterfactual(self) -> None:
        """Dropping the only kept cell leaves nothing — an empty ``kept_r``,
        not a silent fallback to the suppressed book."""
        trades = _cell_trades(
            [
                ("bos", "high_vol", False, _tight(-0.4, 40)),
                ("bos", "trend", True, _tight(-0.9, 50)),
            ]
        )
        [(_, _, cell)] = build_kept_audit_cells(trades)
        assert list(cell.kept_r) == []

    def test_empty_frame_yields_no_cells(self) -> None:
        assert build_kept_audit_cells(pd.DataFrame()) == []


class TestKeptFamilyIsSeparate:
    """Regression control for the shipped flip verdict (#231). The two questions
    are different families; merging them would move the flip's Holm denominator
    and silently restate a decision this branch never intended to touch."""

    def _frame(self) -> pd.DataFrame:
        return _cell_trades(
            [
                ("bos", "trend", True, _tight(-0.5, 60)),
                ("ema", "high_vol", True, _tight(0.6, 60)),
                ("bos", "high_vol", False, _tight(-0.5, 60)),
                ("bos", "range", False, _tight(0.2, 60)),
            ]
        )

    def test_suppressed_family_size_excludes_kept_cells(self) -> None:
        trades = self._frame()
        supp = evaluate_cells(trades)
        kept = evaluate_kept_cells(trades)
        assert len(supp) == 2 and len(kept) == 2
        # n_tests is the Holm denominator: each family counts only its own.
        assert {c.verdict.n_tests for c in supp} == {2}
        assert {c.verdict.n_tests for c in kept} == {2}

    def test_adding_kept_cells_does_not_move_the_flip_verdict(self) -> None:
        """Positive control: the kept cells here are numerous and extreme, so a
        merged family would visibly shift the suppressed cells' adjusted p."""
        trades = self._frame()
        before = {
            (c.strategy, c.regime): c.verdict.adj_pvalue for c in evaluate_cells(trades)
        }
        evaluate_kept_cells(trades)  # must not mutate or share state
        after = {
            (c.strategy, c.regime): c.verdict.adj_pvalue for c in evaluate_cells(trades)
        }
        assert before == after
        assert flip_verdict(evaluate_cells(trades))[0] == FLIP_BLOCKED


class TestMappingVerdictDoesNotVeto:
    """The asymmetry with :func:`flip_verdict`. ``mode`` is one global switch so
    one winner vetoes the flip; each mapping entry is edited independently, so a
    winner must NOT cancel a loser. Reusing the veto here would hide a real
    finding whenever any other cell won."""

    def test_a_losing_cell_is_reported_even_beside_a_winning_one(self) -> None:
        trades = _cell_trades(
            [
                ("bos", "high_vol", False, _tight(-0.6, 60)),
                ("ema", "trend", False, _tight(0.9, 60)),
            ]
        )
        decision, reasons = mapping_verdict(evaluate_kept_cells(trades))
        assert decision == MAPPING_FIX
        joined = " ".join(reasons)
        assert "bos/high_vol" in joined and "drop 'high_vol'" in joined
        # the winner is reported too, not silently dropped
        assert "ema/trend" in joined

    def test_all_winners_is_clean(self) -> None:
        trades = _cell_trades(
            [
                ("bos", "range", False, _tight(0.9, 60)),
                ("ema", "trend", False, _tight(0.8, 60)),
            ]
        )
        decision, _ = mapping_verdict(evaluate_kept_cells(trades))
        assert decision == MAPPING_CLEAN

    def test_undersized_cells_hold_and_claim_nothing(self) -> None:
        trades = _cell_trades([("bos", "range", False, _tight(-0.6, 5))])
        decision, reasons = mapping_verdict(evaluate_kept_cells(trades))
        assert decision == MAPPING_HOLD
        assert "NOT evidence the mapping is right" in " ".join(reasons)

    def test_no_cells_holds(self) -> None:
        assert mapping_verdict([])[0] == MAPPING_HOLD


class TestAttachVerdictsKeptFamily:
    def _frame(self) -> pd.DataFrame:
        return _cell_trades(
            [
                ("bos", "trend", True, _tight(-0.5, 60)),
                ("bos", "high_vol", False, _tight(-0.5, 60)),
            ]
        )

    def test_kept_rows_carry_a_verdict_when_the_kept_family_is_passed(self) -> None:
        trades = self._frame()
        out = attach_verdicts(
            aggregate(trades), evaluate_cells(trades), evaluate_kept_cells(trades)
        )
        kept = out[~out["suppressed"]]
        assert kept["verdict"].notna().all()
        assert set(kept["tested_as"]) == {"kept"}
        assert set(out[out["suppressed"]]["tested_as"]) == {"suppressed"}

    def test_omitting_the_kept_family_still_nulls_kept_rows(self) -> None:
        """Backward compatibility, and the honest reading of a null: 'not in the
        family passed to this call', never 'tested and unremarkable'."""
        trades = self._frame()
        out = attach_verdicts(aggregate(trades), evaluate_cells(trades))
        kept = out[~out["suppressed"]]
        assert kept["verdict"].isna().all()
        assert kept["tested_as"].isna().all()
