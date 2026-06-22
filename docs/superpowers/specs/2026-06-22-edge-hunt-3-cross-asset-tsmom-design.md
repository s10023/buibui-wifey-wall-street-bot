# Edge-Hunt #3 — Cross-Asset Time-Series Momentum (TSMOM)

## Motivation

The binding constraint is recorded in memory
[[binding-constraint-no-equity-edge]]: **wifey has no trustworthy, deployable
positive-Sharpe equity edge.** Four sleeves now fail their pre-registered gates —
forecast trend **G2 ≈ 0** (#91), cross-sectional momentum **G3 = −0.156** (#92),
residualized XS-momentum **experiment #1 = FAIL** (#98), and low-beta / BAB
**edge-hunt #2 = FAIL** (#100). The whole sizing / portfolio / risk stack is built
and portable, but it is *downstream* of a signal we do not have: you cannot
vol-target your way out of negative Sharpe.

This is **edge-hunt #3** on the roadmap fixed in the experiment-#1 spec
(`docs/superpowers/specs/2026-06-21-equity-edge-hunt-roadmap-residual-xsmom-design.md`):
**cross-asset time-series momentum** over free-data ETFs, reusing the existing
`analytics/forecast/` EWMAC engine. Each edge family gets its own
spec → plan → implementation cycle; this spec designs #3 in full.

## The lesson from #1 and #2 that shapes this design

The single fact common to all four failures is that they all lived **inside US
equities** during a 2018–2025 window in which US-equity beta was relentlessly
positive. That made survivorship bias and bull-market drift confound every
candidate "edge": experiment #1's only positive number was a long-only leg that
was simply long the survivors, and #2's only positive cells were the
survivorship/beta-confounded long-only books (realized β +17.5). Worse, #2's
*intended* market-neutral cell never achieved realized neutrality (β +3.9), so it
could not even cleanly test its premium.

Cross-asset TSMOM is the first family on the roadmap that **structurally leaves
the equity-beta factor**. Trend-following a basket spanning equities, rates,
credit, metals, energy, agriculture and FX produces a book whose net equity beta
is ≈ 0 over a cycle (long equities in a bull, short or flat in a bear), and whose
return is driven by trends that are largely uncorrelated *across* asset classes.
The diversification that all four prior sleeves lacked is the entire point.

Crucially, the engine already exists: the `forecast/` sleeve **is**
time-series momentum (continuous, multi-speed, vol-normalised, causal). The only
genuinely new thing in #3 is the **universe**. That makes this the cheapest
high-information test left on the roadmap — almost pure reuse.

## Hypothesis and null

- **Null under attack:** "Diversified cross-asset time-series momentum has no edge
  in free-data instruments."
- **Economic prior:** TSMOM is one of the most replicated anomalies in finance
  (Moskowitz, Ooi & Pedersen 2012 documented it across ~58 futures markets and
  five asset classes back to 1965; the AQR / Hurst–Ooi–Pedersen work extended it
  to a century). The premium is usually attributed to under- then over-reaction
  and to the demand for crisis-hedging "divergent" exposure. It is *not* an
  equity anomaly, so it is unconfounded by the bull-market / survivorship trap
  that faked the prior four results. The open empirical question is whether it
  **survives in a small, free, ETF-only proxy basket net of realistic ETF
  costs** over the post-2007 window.
- **Decisive either way.** A pre-registered pass → the first trustworthy,
  beta-diversified sleeve, and the binding constraint finally cracks (the
  deferred `portfolio/` sizing ports begin to earn their keep once a second
  sleeve clears). A clean fail → trend has no edge in our investable free-data
  set, and the roadmap goes to #4 (PEAD-lite) or the honest-exit criterion,
  having spent the least to learn it because the engine was already built.

## Construction (maximal reuse, additive, read-only)

The book is the existing `forecast/` EWMAC engine run over a cross-asset ETF
basket instead of single equities:

1. **Causal multi-speed forecast** per instrument — `combine_forecasts` over the
   four Carver speeds `(8,32) (16,64) (32,128) (64,256)`, vol-normalised and
   capped, `.shift(1)` look-ahead-guarded. Unchanged from `forecast/`.
2. **Per-instrument vol-target sizing** — `leverage = (forecast/10) ×
   (vol_target / realized_vol_ann)`, causal. Unchanged.
3. **Portfolio vol governor** — `run_forecast_backtest` aggregates equal-risk
   across active instruments and applies the causal trailing-vol governor
   (`gov_window=64`, `g_min=0.5`, `g_max=1.5`). With 13 instruments the governor
   has enough breadth to stay off its `g_min` floor — the saturation that broke
   #2 was a 500-name vol-parity book, a different mechanism that does not apply
   here.
4. **Honest costs** — turnover cost = `|Δleverage| × (fee + slippage)` in the
   book, reported at 0 / 2 / 8 bps slippage. TSMOM is low-turnover, so this is
   the family's friendliest cost profile and a death-by-8 bps here is especially
   damning.

### The one additive touch to existing code

The "long-biasable" deployable form (roadmap requirement) needs the combined
forecast clipped at zero before sizing. This is added as a **default-off**
keyword-only `long_only: bool = False` on `forecast.book.instrument_returns` and
`forecast.book.run_forecast_backtest`. When `False` (the default and the only
path anything existing takes) the code is byte-identical, so the `forecast/` G2
audit, its tests, and the regression goldens are all unmoved. When `True` the
combined forecast is `clip(lower=0.0)` — long or flat, never short. This mirrors
the additive `leverage=…` injection that edge-hunt #2 added to
`xsmom.book.run_xs_backtest`.

### The four book forms (the 2×2 grid)

| universe ↓ \ construct → | long-short (signed) | long-flat (clip ≥ 0) |
| --- | --- | --- |
| **broad cross-asset** | **◀ GATED CELL** | deployable (flagged) |
| commodity / metals only | breadth-vs-commodity contrast | deployable (flagged) |

- `broad_ls` — **the pre-committed gated cell**: signed multi-speed TSMOM over
  the full 13-ETF basket. The canonical diversified MOP construction.
- `broad_long` — broad basket, long-flat. The deployable no-short form.
- `commodity_ls` — the 5-name commodity/metals sub-basket, signed. The "is the
  edge cross-asset *breadth* or just commodity trend?" contrast.
- `commodity_long` — commodity sub-basket, long-flat.

The verdict is read on `broad_ls` **only** — not whichever of the four scores
highest (that would re-introduce selection bias). The other three are diagnostic
context, and the full four-book family feeds the DSR deflation and the CSCV / PBO
trial count.

### Equity-beta guardrail (the thesis check)

The whole premise is that this book diversifies away from equity beta. So the
realized β of the committed `broad_ls` book to **SPY** is reported as a
first-class diagnostic and is expected to come back **≈ 0**. SPY is loaded
*separately* as the equity benchmark (it is also a basket member, so its own
return series must not double-count in the attribution). This is the structural
analogue of edge-hunt #2's realized-β guardrail: there, β ≈ 0 was the
*requirement* of a market-neutral construct; here it is the *confirmation* that
the cross-asset book delivers the diversification the thesis claims. A committed
book that passed the Sharpe gate but came back with a large positive equity β
would be flagged as "trend-following the equity bull in disguise," not a
diversifier.

## Universe (frozen, pre-registered)

A fixed **13-ETF broad basket**, all liquid, free on yfinance, with full daily
history available from early 2007:

| Asset class | Tickers |
| --- | --- |
| Equity index | SPY, QQQ, EFA, EEM |
| Rates / credit | TLT, IEF, LQD |
| Metals | GLD, SLV |
| Energy / commodity | DBC, USO, DBA |
| FX | UUP |

- **Storage:** a frozen module constant in `analytics/xasset/universe.py`
  (pre-registered, git-tracked, asset-class tagged) — deliberately **not** added
  to `config/universe.json`. The breadth universe is "the current S&P 500"; bonds
  / commodities / FX do not belong in it, and keeping them out means every one of
  the four equity-sleeve audits and the equity backfill stay literally untouched.
- **Commodity / metals sub-basket:** `{GLD, SLV, DBC, USO, DBA}` — the narrow
  contrast arm.
- **History:** backfilled from **2007-03-01** — the first available daily bar of
  the latest-listing member, UUP (FX; DBA the next-latest at 2007-01-05) — so all
  13 instruments are present from bar one, with no staggered entry contaminating
  the cross-asset portfolio. That is **~18 years / ~4850 daily observations**,
  versus the equity panel's 7 years —
  a large robustness win for DSR / PBO / MinTRL, and there is no survivorship
  reason to truncate continuous ETFs at 2018.
- **Survivorship:** none. These are continuous, never-delisted ETFs, so the
  survivorship caveat that bounds every equity-sleeve claim does not apply here —
  a further reason this is a cleaner test than #1 / #2.

## Acceptance gate (pre-registered — fixed before any result exists)

The bar is fixed **now**, before any number is computed, and mirrors the
edge-hunt #2 gate verbatim so the two are directly comparable:

- **PASS = all of:** net-of-cost **DSR ≥ 0.95**, **PBO ≤ 0.5**, bootstrap-CI
  **lower bound > 0**, **n ≥ MinTRL**, and **OOS Sharpe ≥ 0.7**.
- **Pre-committed cell:** the verdict is read on the **`broad_ls`** book. The
  other three books are diagnostic context, and the full four-book grid feeds the
  PBO / DSR trial count.
- **Deploy-grade tier (annotation, not the pass/fail line):** Sharpe **≥ 1.0**
  *and* the long-flat translated form (`broad_long`) holds up. A `≥ 0.7` pass is
  "real edge, candidate sleeve"; a `≥ 1.0` pass is "deploy-grade, fast-track."
  Diversified TSMOM net of ETF costs is unlikely to clear 1.0 even when a real
  premium exists (MOP's diversified factor ran a gross Sharpe near 1 over six
  decades; an ETF-only proxy net of cost over 18 years will be lower), so 1.0 as
  the pass line would make the experiment low-information.
- **Costs in every P&L surface:** report Sharpe at **0 / 2 / 8 bps** slippage
  sensitivity (`--slippage-bps`, like the #1 / #2 audits). A pass that dies by
  8 bps is "thin / parked," not a deploy.
- **Equity-beta guardrail:** the realized β of `broad_ls` to SPY is reported and
  expected ≈ 0. A Sharpe pass with a large positive equity β is flagged
  suspect (equity-bull trend in disguise), not a clean diversifier.

The `≥ 0.7` Sharpe floor paired with a net-of-cost **DSR ≥ 0.95 deflated over the
full four-book trial count** is a high bar against thin / overfit edges; a
spurious 0.7 will not clear it.

## Components (units, each one purpose, additive)

A new self-contained package `analytics/xasset/` mirroring the `forecast/` /
`xsmom/` / `lowvol/` split, plus a read-only audit CLI. Nothing existing imports
it, so the regression goldens are trivially unmoved.

- **`analytics/xasset/universe.py`** — pure, no I/O. The frozen
  `BROAD_BASKET: tuple[AssetMember, ...]` (ticker + asset-class tag) and the
  derived `COMMODITY_BASKET`, plus `broad_symbols()` / `commodity_symbols()`
  helpers. The pre-registration artifact.
- **`analytics/xasset/replay.py`** — the **only** DB-touching module, read-only.
  Reuses `forecast.replay.load_daily_inputs` (1d closes + all-zero equity
  funding) to load the basket. `replay_xasset_grid(...)` runs the 2×2 grid via
  `run_forecast_backtest` (with `long_only=…` per cell and the symbol set per
  universe arm) and returns `dict[str, ForecastBookResult]` keyed by the four
  cell names; `xasset_market_return(...)` returns the SPY benchmark return as a
  separate Series (so SPY's own series does not double-count in the attribution).
- **`analytics/xasset/report.py`** — pure. `XAssetGridReport` (frozen dataclass:
  per-cell `G2Report`, per-cell realized-β attribution to SPY, `committed_key`,
  `passed`, `deploy_grade`) and `evaluate_xasset_grid(...)`. Reuses
  `forecast.report.evaluate` (DSR / PBO / boot-CI / MinTRL, annualization
  threaded from `cfg`) per cell with the four-cell family as the trial set, and
  `xsmom.diagnostics.beta_attribution` for the equity-β guardrail. The gate
  (`_GATE_SHARPE = 0.7`, `_DEPLOY_SHARPE = 1.0`) is read on `broad_ls`.
- **`analytics/xasset/__init__.py`** — eager re-exports, matching the sibling
  packages.
- **`tools/xasset_audit.py`** — read-only verdict CLI. Prints the 2×2 grid at
  0 / 2 / 8 bps with each cell's headline + DSR / PBO / boot-CI / MinTRL, the
  realized equity-β diagnostic, the long-flat leg Sharpe, and the PASS / FAIL
  (+ deploy-grade flag) on `broad_ls`. `build_grid` is the testable unit. Run via
  `make wifey-xasset-audit` or `PYTHONPATH=. poetry run python
  tools/xasset_audit.py [--slippage-bps N]`.
- **`Makefile`** — `wifey-xasset-audit` (run the audit) and
  `wifey-xasset-backfill` (backfill the frozen basket from 2007-03-01 via the
  existing `data_sync.backfill`, looping the 13 tickers for 1d) targets +
  `.PHONY` entries.
- **`tests/test_xasset_*.py`** — `test_xasset_universe.py` (basket shape /
  sub-set / no duplicates), `test_xasset_replay.py` (four books returned, SPY
  benchmark is a Series, in-memory DuckDB seed), `test_xasset_report.py` (grid
  structure, committed key, finite β, gate boolean), plus a one-line addition to
  the existing `forecast` book test asserting `long_only=True` clips shorts while
  `long_only=False` is byte-identical to the current path.
- **`docs/audits/2026-06-22-edge-hunt-3-cross-asset-tsmom.md`** — the verdict
  note, written after the audit runs.

## Data flow

```text
config (frozen BROAD_BASKET)           wifey-xasset-backfill (one-shot, 2007+)
        │                                       │
        ▼                                       ▼
analytics/xasset/universe.py            ohlcv table (DuckDB, read-only thereafter)
        │                                       │
        └───────────────┬───────────────────────┘
                        ▼
        analytics/xasset/replay.py  (load_daily_inputs → run_forecast_backtest ×4 cells
                        │            + SPY benchmark return)
                        ▼
        analytics/xasset/report.py  (forecast.evaluate per cell over the 4-cell trial
                        │            family + beta_attribution to SPY → gate on broad_ls)
                        ▼
        tools/xasset_audit.py  →  docs/audits/2026-06-22-edge-hunt-3-cross-asset-tsmom.md
```

Read-only after backfill: no DB writes, no schema change, no detector change, no
signal-path import ⇒ regression goldens byte-identical.

## Testing

- **Pure-unit tests** for `universe.py` (basket membership, sub-set relation, no
  duplicate tickers), `signals`-free `report.py` (grid keys, committed key, gate
  boolean, finite realized β) over a synthetic in-memory book.
- **Replay tests** seed an in-memory DuckDB with deterministic random-walk OHLCV
  for a handful of tickers and assert the four cells are returned and the SPY
  benchmark is a non-empty Series — the `lowvol` / `xsmom` replay-test pattern.
- **`long_only` parity test** in the existing forecast book test:
  `long_only=False` reproduces the current `instrument_returns` output
  byte-for-byte (guards the goldens), and `long_only=True` produces no negative
  leverage.
- **Regression goldens** (`make test-regression`) must stay byte-identical — if
  one moves, STOP, it means an unintended import onto the signal / backtest path.
- Full `make lint-py` (ruff), `make typecheck` (mypy strict), `make test`.

## Out of scope (YAGNI)

- No sizing / portfolio / paper-book work — gated on ≥ 2 clearing sleeves per the
  roadmap convergence rule.
- No new boolean TA detectors, no tp_r / gate / threshold sweeps (frozen
  category).
- No paid data, no futures data, no continuous-contract roll modelling — ETF
  proxies are the deliberately-cheap free-data test. USO's contango drag is a
  known proxy imperfection accepted as part of the free-data constraint, not
  corrected.
- No per-speed weight study (the `forecast/` G2 weight-study mode is not ported
  to the cross-asset audit — the four-cell grid is the trial family).
- No FX-carry, no commodity term-structure / roll-yield signal — those are
  separate edge families, not in-scope for a pure TSMOM test.
- No short-borrow / financing model for the long-short cells beyond the book's
  turnover cost — consistent with the prior sleeves; noted as mildly optimistic.
- Roadmap item #4 (PEAD-lite) is cataloged in the edge-hunt roadmap spec and
  designed in its own spec.

## Open questions

- **Backfill cadence:** the cross-asset basket is backfilled once from 2007 and
  is not part of the daily `make go-live` watchlist refresh. A future re-run of
  the audit just needs a `wifey analytics sync` over the basket; whether to wire
  that into a periodic job is deferred until (and unless) a pass makes it a live
  sleeve.
- **Annualization for the long-flat cells:** the forecast `evaluate` annualizes
  at 252 sessions; the long-flat books are still daily-rebalanced, so 252 holds —
  noted to confirm during implementation that no cell needs a different factor.
