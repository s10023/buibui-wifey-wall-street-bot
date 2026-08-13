# Warning-value audit — do the W1–W8 alert warnings predict avg_r?

**Date:** 2026-08-13
**Verdict:** COSMETIC on 11 of 12 cells; **one SUPPRESS-CANDIDATE — `w5_wick_rejection` /
long** (backtest substrate). Not shipped as a gate: the finding is backtest-only and the live
substrate cannot corroborate it at n=1.
**Audit:** `tools/warning_value_audit.py` (`make wifey-warning-value-audit`), lib
`analytics/warning_audit.py`
**Ported from:** parent PR #492

## Why this exists

The live alert path appends six candle-anatomy warnings at fire time
(`signals/alert_formatter.py::_build_candle_warnings`) — W1 marubozu, W2 equal levels, W5 wick
rejection, W6 consecutive candles, W7 doji, W8 inside bar. **They are rendered text: never
persisted, and they gate nothing.** Nobody had ever checked whether they carry information.

This also closes **SoT N2**. `analytics/audit_guard.py` was ported in Phase N2 and then sat
**unwired** — no tool hosted `evaluate_audit_cells`. The obvious host, the parent's
`tools/gate_audit.py`, is blocked here: it selects `low_volume` / `volume_spike` from
`backtest_trades`, **columns wifey never had**, and skipping that migration fails *silently*
(`fillna(False)` ⇒ an all-false mask). This audit needs neither — it re-derives its flags from
OHLCV — which is why it, and not `gate_audit.py`, is the host.

## Method

Flags are re-derived historically by **importing the live helpers, never reimplementing them**,
so the audited quantity is the one the operator actually sees. Precedence is inherited with
them: doji beats marubozu, W5 is skipped on a doji, and a `<2`-bar window emits nothing.

Each (warning × direction) cell treats the **warned slice as the would-be-suppressed slice of a
hypothetical warning gate**, which maps `audit_guard` semantics directly onto it: `ENABLE` →
SUPPRESS-CANDIDATE, `DISABLE` → REVERSE, `CONCENTRATE` or tested-but-not-clearing → COSMETIC,
else INSUFFICIENT. One Holm family per source across all 12 cells. A cell must clear a ±0.05R
bootstrap CI **and** its Holm-adjusted p < 0.05. The two-sample lift CI (warned − clean) is
reported as corroboration and is **never gate-deciding**.

Substrate roles were pre-committed: **`backtest_trades` primary, `signal_alert_outcomes`
corroboration only.**

## The substrate, and what it is not

| quantity | value |
| --- | --- |
| `backtest_trades` rows with `pnl_r` | 23,696 |
| distinct signals after dedup | **7,046** (3.4× duplication across saved runs) |
| dropped in tagging | **0** |
| per timeframe | 4h 3,804 · 1d 2,745 · 1wk 497 |
| `signal_time` span | 2025-05-20 → 2026-08-05, 13 symbols |
| live substrate | 267 resolved rows, 0 dropped |

Dedup is on `(symbol, tf, strategy, direction, signal_time)` keeping the lexicographically
latest `run_id` — necessary because `backtest_runs` has four writers and one signal legitimately
appears under several saved runs.

Three caveats that bound every number below:

1. **The population is heterogeneous across gate eras.** These rows accumulated over ~15 months
   spanning the ungated era, the #151 EV-gate change, and the live-parity work. This is not one
   experiment; it is the union of many.
2. **3.9% of signals come from RETIRED detectors** — 209 `fib_golden_zone` and 66
   `liquidity_sweep`, both removed as net-negative. For the one finding below they cut *against*
   it (both show positive lift), so excluding them would strengthen it slightly, not weaken it.
3. **Trades are non-iid.** Pooling across symbols and strategies means the effective sample is
   smaller than n suggests, so treat the CI as optimistic.

## Result — 11 of 12 cells COSMETIC

Every cell except one is well-powered and shows no gate-grade effect. That is a real answer: it
says the warnings are **decoration that does not mislead**, which is the outcome that justifies
leaving them in the alert.

## The exception — `w5_wick_rejection` / long

| | warned | clean |
| --- | --- | --- |
| n | 316 | 3,168 |
| avg R | **−0.315** | −0.037 |

Bootstrap CI **[−0.475, −0.144]** (clears the −0.05 bar), Holm-adjusted **p = 0.002**,
two-sample lift CI **[−0.450, −0.096]** (excludes zero). `audit_guard` decision `ENABLE`.

It survives the two checks that usually kill a pooled finding:

**It is not one timeframe.** The sign holds on all three, and 1wk is included only because this
port replaced upstream's `1w` literal (see below).

| tf | n warned | avg warned | avg clean | lift |
| --- | --- | --- | --- | --- |
| 4h | 172 | −0.379 | −0.016 | −0.363 |
| 1d | 126 | −0.220 | −0.055 | −0.164 |
| 1wk | 18 | −0.371 | −0.097 | −0.275 |

**It is not one strategy.** Of the six strategies with ≥10 warned longs, **five** show warned
underperforming clean: `bos` −0.462, `morning_evening_star` −0.287, `inside_bar` −0.218,
`order_block` −0.173, `engulfing` −0.097; only `fib_golden_zone` (+0.188, retired) runs the other
way.

**Why it is NOT shipped as a gate.** The live substrate has **n=1** warned long for W5 — it can
neither confirm nor refute. A backtest-only finding on a heterogeneous multi-era population is
not sufficient grounds to suppress live alerts, and acting on it would also collide with the TA
freeze's spirit even though the freeze does not formally cover rendered text. **The instrument is
the deliverable; the verdict is a lead.** Re-run when the live W5/long cell reaches n≥30.

## Two fork divergences from the port

**Upstream's timeframe map would have crashed this audit outright.** Parent #492 carries a literal
`_TF_MS` map spelling the weekly timeframe `1w`; every equity surface here uses yfinance's `1wk`,
which carries **497 signals** in the substrate. A verbatim copy raises `KeyError: '1wk'` on the
first real run. Fixed by deferring to `analytics.signal._common.parse_timeframe_secs`, which
already normalises the suffix — removing the trap by construction rather than by correcting a
literal. `TestTimeframeLength` pins it, and was mutation-checked: restoring upstream's map turns
exactly the two `1wk` assertions red.

**The volume-notes exclusion has a different and stronger reason here.** Upstream excludes the
volume-spike / low-volume notes as *already audited by `gate_audit.py`*. In wifey they are
excluded because `backtest_trades` has no `low_volume` / `volume_spike` columns at all, so they
are not re-derivable from the primary substrate.

## Reproducing

```bash
make wifey-warning-value-audit ARGS="--out docs/plans/warning-value-audit-2026-08-13.md"
```

Read-only; no engine or live change. Every number in this document comes from that run except the
substrate table and the concentration checks, which are direct DuckDB queries over
`backtest_trades` and a `tag_trades` replay that asserts it reproduces the shipped run
(`len(tagged) == 7046 and dropped == 0`) before slicing.
