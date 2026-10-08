# Inline classification rubric

Supports `SKILL.md` section `## Inline classification rubric` (pass-1 and pass-2 prompts).

> A distilled snapshot of the SoT's Frozen / Closed / Parked state so each subagent
> classifies from the prompt alone. Refresh from `docs/north-star.md` (Frozen) and
> `project_todo_master.md` (Closed) periodically —
> treat as a de-biasing prior, not gospel; NOVEL still passes the human gate. This block is
> shared verbatim with `/ingest-x`'s rubric — keep the two in sync when either is refreshed.
> `content_type`: **setup** = a specific symbol+direction+levels trade call → Stream C;
> **mechanic** = an exit/risk/data/microstructure execution rule → Stream B; **claim** = a
> generalizable market-behaviour assertion → verdict below.

**Frozen — never propose a new TA detector.** The TA detector book (wicks, marubozu, ORB,
FVG, BOS/market-structure, EQH/EQL, order block, trend day, engulfing, pin bar, inside bar,
hammer, doji, morning/evening star, fib retracement / golden zone, OTE, EMA — plus the
stripped crypto-era detectors: liquidity sweep, funding extreme, SMT, CVD divergence) is
frozen under an inherited category verdict (TA = negative-EV; no new boolean detectors, no
tp_r/gate/threshold sweeps). A claim that just restates one of these candlestick/structure
patterns → `FROZEN-CATEGORY`.

**Scope — the freeze binds this repo's equity signal engine, nothing wider.** It covers the
boolean bar-pattern detectors in `analytics/strategies/_registry.py::DETECTOR_REGISTRY`,
evaluated at `4h`/`1d`/`1wk` on the US-equity universe. It is a policy — an inherited
category verdict — never a measurement about moving averages or price structure in general.
Mark `FROZEN-CATEGORY` only when the claim would land as a new detector or a new sweep in
that book. A monthly-timeframe MA band, a regime or drawdown classifier, and anything on
another asset class or horizon are all out of scope: judge them on their merits. When
unsure, return `NOVEL` and let the human review gate decide — a wrong `FROZEN-CATEGORY`
drops the item silently, a wrong `NOVEL` costs one line of review.

**Already-tested (verdict known → `ALREADY-TESTED`, drop unless materially new evidence).**
All eight research sleeves measured on US equities came back non-positive and the free-data
edge arc is concluded (`CLAUDE.md` § Sleeve verdicts):

- Absolute **trend** (multi-speed EWMAC): G2 FAIL — portfolio Sharpe −0.05, negative even
  pre-cost (a signal failure, not a cost failure).
- Cross-sectional **XS momentum** (relative strength): G3 FAIL — combined Sharpe −0.156
  @2bps, corr_to_trend +0.62 (not even a diversification win).
- **Residualized XS-mom** (beta-stripped + skip-month): FAIL — committed L/S cell +0.15,
  DSR 0.44; the long-only +0.88 leg is survivorship/beta-confounded.
- **Low-vol / BAB** (beta-neutral L/S): FAIL — the realized-β guardrail fired (β +3.9):
  ex-ante β-neutralization did not deliver market-neutrality.
- **Cross-asset TSMOM** (13-ETF basket): FAIL, clean — β-guardrail held; +0.41 cost-free,
  never ≥0.7 gate. Futures-grade breadth is a paid-data question.
- **PEAD-lite** (seasonal SUE on free EDGAR data): FAIL — β-guardrail fired on the broad
  arm; the controlled mega arm showed negative drift. No PEAD in liquid large-caps net of
  cost on free data.
- **Gap-fill magnet** (unfilled gaps as magnets / "gaps always fill"): EXCLUDED, direction
  REFUTED — cost-free the magnet returns −0.460, so gaps continue rather than revert; the
  post-hoc inverse never reaches the bar, and ~211× daily gross turnover kills both
  directions. "90.3% of gaps fill within 60 sessions" is true and almost entirely diffusion
  (matched placebo 88.9%).
- **Velocity alternation** (the pace of a decline predicts the pace of the next move):
  EXCLUDED as a null — β-guardrail fired, beta-hedged −0.169 at alpha t −0.49, and the
  velocity ratio performs indistinguishably from depth alone.
- DOW / day-of-week seasonality (e.g. "Monday is the weekly high → short"): parent-inherited
  verdict — base rate real but the tradeable edge decays OOS; the version that looks clean
  is look-ahead.
- Exit-policy fixes ("your stops are wrong, not your entries"): BOUNDED — the replay A/B
  measured the lever's ceiling at +0.368R of paired uplift, entirely the time-stop, and no
  arm's own mean R clears zero. Re-run trigger is ledger growth, not a restated claim.

**Parked / captured / blocked:**

- IPO post-hype dip-buy (long a faded recent IPO reclaiming its listing price): already
  captured as a thesis memo — a reassertion is "already in inbox", don't duplicate the
  H-row.
- Anything requiring paid data (Polygon intraday, options flow, L2/auction feeds, futures
  breadth): `NOVEL` in principle but data-blocked — say so in `gap_note` (paid data
  currently declined).

**`NOT-FALSIFIABLE`:** vibes / no testable prediction / unfalsifiable hindsight.
**`NOVEL`:** a genuinely new, testable, uncovered market-behaviour claim.
