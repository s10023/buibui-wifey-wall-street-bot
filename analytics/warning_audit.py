"""H9 warning-value audit lib (pure — no DB/IO).

The live alert path computes six candle-anatomy warnings at fire time
(``signals.alert_formatter._build_candle_warnings``) but never persists the
flags, so this module re-derives them historically from OHLCV using the SAME
helpers — import, never reimplement — then evaluates whether any warning
predicts avg_r via :mod:`analytics.audit_guard` (block-bootstrap CI clearing
±bar + Holm haircut, one family per audited source).

Scope guard: the volume-spike / low-volume conviction notes are excluded. The
parent excludes them as *already audited* by ``tools/gate_audit.py``; **here the
reason is different and stronger** — wifey never had ``backtest_trades``'
``low_volume`` / ``volume_spike`` columns, so those notes are not re-derivable
from the primary substrate at all. That is precisely why this module, and not
``gate_audit.py``, is the host that unblocks the guard: the six candle warnings
below need only OHLCV. CME-gap warnings do not exist in this fork.

Ported from parent PR #492. The module body is byte-identical to upstream — all
six ``alert_formatter`` helpers and the whole ``audit_guard`` public surface are
present here with matching signatures, verified before the copy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics import audit_guard
from signals.alert_formatter import (
    _has_consecutive_candles,
    _has_equal_levels,
    _is_doji,
    _is_inside_bar,
    _is_marubozu,
    _wick_rejection_against,
)

WARNING_KEYS: tuple[str, ...] = (
    "w1_marubozu",
    "w2_equal_levels",
    "w5_wick_rejection",
    "w6_consecutive",
    "w7_doji",
    "w8_inside_bar",
)

VERDICT_SUPPRESS = "SUPPRESS-CANDIDATE"
VERDICT_REVERSE = "REVERSE"
VERDICT_COSMETIC = "COSMETIC"
VERDICT_INSUFFICIENT = "INSUFFICIENT"


def compute_warning_flags(
    window: pd.DataFrame,
    direction: str,
    price: float | None = None,
) -> dict[str, bool] | None:
    """Re-derive the six warning flags for the window's LAST candle.

    ``window`` holds OHLCV rows ending AT the signal candle (last row = signal
    candle). Returns ``None`` when the window has < 2 rows — the live path
    emits no candle warnings there either. ``price`` (for W2's equal-levels
    scan) defaults to the signal-candle close, the exact value the live path
    passes (``SignalEvent.price``). Precedence mirrors
    ``_build_candle_warnings``: doji wins over marubozu; wick-rejection is
    skipped on a doji.
    """
    if len(window) < 2:
        return None
    last = window.iloc[-1]
    o = float(last["open"])
    h = float(last["high"])
    lo = float(last["low"])
    c = float(last["close"])
    prev = window.iloc[-2]
    prev_h = float(prev["high"])
    prev_l = float(prev["low"])
    p = c if price is None else price
    doji = _is_doji(o, h, lo, c)
    return {
        "w1_marubozu": (not doji) and _is_marubozu(o, h, lo, c),
        "w2_equal_levels": _has_equal_levels(window, p, direction),
        "w5_wick_rejection": (not doji)
        and _wick_rejection_against(o, h, lo, c, direction),
        "w6_consecutive": _has_consecutive_candles(window, direction),
        "w7_doji": doji,
        "w8_inside_bar": _is_inside_bar(h, lo, prev_h, prev_l),
    }


def two_sample_lift_ci(
    warned: Sequence[float] | npt.NDArray[np.float64],
    clean: Sequence[float] | npt.NDArray[np.float64],
    *,
    alpha: float = 0.05,
    n_boot: int = 10_000,
    seed: int | None = 12345,
) -> tuple[float, float]:
    """Seeded two-sample bootstrap CI for ``mean(warned) - mean(clean)``.

    Independent i.i.d. resamples of each cohort; percentile CI of the
    difference of means. ``(nan, nan)`` when either cohort has < 2 rows.
    Report-only corroboration — never gate-deciding (see plan).
    """
    a = np.asarray(warned, dtype=np.float64)
    b = np.asarray(clean, dtype=np.float64)
    if a.shape[0] < 2 or b.shape[0] < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    diffs = np.empty(n_boot, dtype=np.float64)
    for i in range(n_boot):
        ra = rng.integers(0, a.shape[0], a.shape[0])
        rb = rng.integers(0, b.shape[0], b.shape[0])
        diffs[i] = a[ra].mean() - b[rb].mean()
    lo = float(np.quantile(diffs, alpha / 2.0))
    hi = float(np.quantile(diffs, 1.0 - alpha / 2.0))
    return (lo, hi)


def tag_trades(
    entries: pd.DataFrame,
    ohlcv_by_key: Mapping[tuple[str, str], pd.DataFrame],
    *,
    window_bars: int = 12,
) -> tuple[pd.DataFrame, int]:
    """Tag every entry row with its six warning flags.

    ``entries`` columns: symbol, tf, strategy, direction, ts_ms (signal candle
    ``open_time``), r. Rows whose signal candle is absent from the OHLCV frame
    or whose window has < 2 bars are dropped and counted. ``window_bars=12``
    covers every helper's lookback (W2 scans 10 prior bars).
    """
    keep_idx: list[int] = []
    flag_rows: list[dict[str, bool]] = []
    dropped = 0
    pairs = entries[["symbol", "tf"]].drop_duplicates()
    for symbol, tf in zip(pairs["symbol"], pairs["tf"], strict=True):
        sub = entries[(entries["symbol"] == symbol) & (entries["tf"] == tf)]
        df = ohlcv_by_key.get((str(symbol), str(tf)))
        if df is None or df.empty:
            dropped += len(sub)
            continue
        times = df["open_time"].to_numpy(dtype=np.int64)
        for idx, ts, direction in zip(
            sub.index, sub["ts_ms"], sub["direction"], strict=True
        ):
            i = int(np.searchsorted(times, int(ts)))
            if i >= len(times) or int(times[i]) != int(ts):
                dropped += 1
                continue
            window = df.iloc[max(0, i - window_bars + 1) : i + 1]
            flags = compute_warning_flags(window, str(direction))
            if flags is None:
                dropped += 1
                continue
            keep_idx.append(int(idx))
            flag_rows.append(flags)
    if not keep_idx:
        empty = entries.iloc[0:0].copy()
        for key in WARNING_KEYS:
            empty[key] = pd.Series(dtype=bool)
        return empty, dropped
    tagged = entries.loc[keep_idx].reset_index(drop=True)
    flags_df = pd.DataFrame(flag_rows)[list(WARNING_KEYS)]
    return pd.concat([tagged, flags_df], axis=1), dropped


@dataclass(frozen=True)
class WarningVerdict:
    """Pre-committed H9 verdict for one (warning × direction) cell."""

    warning: str
    direction: str
    n_warned: int
    n_clean: int
    avg_warned: float | None
    avg_clean: float | None
    ci_lo: float | None
    ci_hi: float | None
    adj_pvalue: float | None
    n_tests: int
    lift_lo: float
    lift_hi: float
    raw_decision: str  # audit_guard ENABLE/DISABLE/CONCENTRATE/INSUFFICIENT
    verdict: str  # SUPPRESS-CANDIDATE / REVERSE / COSMETIC / INSUFFICIENT
    reasons: list[str]


def evaluate_warning_cells(
    tagged: pd.DataFrame,
    *,
    min_n: int = 30,
    bar: float = 0.05,
    alpha: float = 0.05,
    n_boot: int = 10_000,
    seed: int | None = 12345,
) -> list[WarningVerdict]:
    """Verdict per (warning × direction) — ONE Holm family per call.

    The warned slice is treated as the would-be-suppressed slice of a
    hypothetical warning gate, so :mod:`analytics.audit_guard` semantics map
    directly: ENABLE (warned reliably ≤ −bar) → SUPPRESS-CANDIDATE; DISABLE
    (warned reliably ≥ +bar) → REVERSE; CONCENTRATE and tested-but-not-clearing
    (both cohorts ≥ min_n) → COSMETIC; otherwise INSUFFICIENT. The two-sample
    lift CI is a reported corroboration stamp, never gate-deciding.
    """
    specs = [(w, d) for w in WARNING_KEYS for d in ("long", "short")]
    warned_arrays: list[npt.NDArray[np.float64]] = []
    clean_arrays: list[npt.NDArray[np.float64]] = []
    for w, d in specs:
        sub = tagged[tagged["direction"] == d]
        warned_arrays.append(sub.loc[sub[w], "r"].to_numpy(dtype=np.float64))
        clean_arrays.append(sub.loc[~sub[w], "r"].to_numpy(dtype=np.float64))
    cells = [
        audit_guard.AuditCell(
            label=f"{w}/{d}",
            supp_r=warned_arrays[i].tolist(),
            kept_r=clean_arrays[i].tolist(),
        )
        for i, (w, d) in enumerate(specs)
    ]
    cell_verdicts = audit_guard.evaluate_audit_cells(
        cells, bar=bar, alpha=alpha, min_n=min_n, n_boot=n_boot, seed=seed
    )
    out: list[WarningVerdict] = []
    for i, ((w, d), cv) in enumerate(zip(specs, cell_verdicts, strict=True)):
        lift_lo, lift_hi = two_sample_lift_ci(
            warned_arrays[i], clean_arrays[i], alpha=alpha, n_boot=n_boot, seed=seed
        )
        if cv.decision == audit_guard.DECISION_ENABLE:
            verdict = VERDICT_SUPPRESS
        elif cv.decision == audit_guard.DECISION_DISABLE:
            verdict = VERDICT_REVERSE
        elif (
            cv.decision == audit_guard.DECISION_CONCENTRATE
            or cv.n_supp >= min_n
            and cv.n_kept >= min_n
        ):
            verdict = VERDICT_COSMETIC
        else:
            verdict = VERDICT_INSUFFICIENT
        out.append(
            WarningVerdict(
                warning=w,
                direction=d,
                n_warned=cv.n_supp,
                n_clean=cv.n_kept,
                avg_warned=cv.supp_avg,
                avg_clean=cv.kept_avg,
                ci_lo=cv.ci_lo,
                ci_hi=cv.ci_hi,
                adj_pvalue=cv.adj_pvalue,
                n_tests=cv.n_tests,
                lift_lo=lift_lo,
                lift_hi=lift_hi,
                raw_decision=cv.decision,
                verdict=verdict,
                reasons=list(cv.reasons),
            )
        )
    return out
