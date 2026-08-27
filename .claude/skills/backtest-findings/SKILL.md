---
name: backtest-findings
description: >
  Interpret any backtest sweep table (ATR / TP / volume / duration) and commit
  the winning params to TOML.
  Invoke when the user says "/backtest-findings", pastes a sweep table, or asks
  "what do these results mean", "which tp_r should I pick", or otherwise needs
  a sweep table translated into a TOML decision — even when they only want it
  read aloud.
allowed-tools: Bash, Read, Edit
---

# Backtest Findings — Interpreting Sweep Output

Workflow for reading a sweep table and translating results into committed TOML config.

## Min-trades thresholds (before trusting any result)

These are the calibrated minimums per TF — the referent is `config/strategy_params.toml`
(top-level `min_trades_*` for the sweep table, `[backtest].min_trades_*` for the daemon):

| TF | Sweep table (`min_trades_*`) | Signal watch daemon (`[backtest].min_trades_*`) |
| ---- | ------------------------------ | ------------------------------------------------ |
| global fallback | 20 | 12 |
| 4h | 10 | 5 |
| 1d | 5 | 2 |
| 1wk | 2 | 1 |

Rows below threshold are hidden or should be ignored. The daemon's thresholds are lower because its gate counts only the tested direction's closed trades (a subset of the total) — and remember it **fails open** below the floor, so raising a `min_trades_*` makes it suppress less.

## Reading a TP sweep table

```text
  Strategy              TF      1.0R    1.5R    2.0R    2.5R    3.0R
  engulfing             1h    +0.08R  +0.12R  +0.16R  +0.18R  +0.19R
  engulfing             4h    +0.10R  +0.18R  +0.22R  +0.24R  +0.25R
  pin_bar               1h    +0.09R  +0.14R  +0.19R  +0.23R  +0.26R
  eqh_eql               1h    -0.10R  -0.08R  -0.05R  -0.03R  -0.04R
```

1. **Peak column** per row = optimal tp_r for that strategy × TF
2. **Monotonically increasing** across all columns → try extending the range (add 3.5R, 4.0R)
3. **Flat or all negative** → TP tuning won't fix this strategy; investigate SL, volume filter, or TF restrictions
4. **Peaks differ by TF** → use TF-specific override keys (`tp_r_4h`, `tp_r_1h`)

## Reading an ATR sweep table

```text
  Strategy              TF      0.5×    1.0×    1.5×    2.0×    2.5×
  bos                   1h    +0.08R  +0.22R  +0.31R  +0.28R  +0.19R
```

- Peak column = optimal `atr_sl_multiplier` for that strategy × TF
- **All rows flat across every column?** The sweep was run without `--atr-sl-floor` (or `atr_sl_floor = true`). Every active strategy emits a structural `sl_price`, which short-circuits the ATR branch. Re-run with the floor on and the rows will move.
- With the floor on, expect best multipliers to cluster at 2.0–2.5× — structural SLs are systematically too tight on most strategies.
- TP scales with SL distance: a wider ATR-floored SL also widens the `tp_r × dist` target. Pair any `atr_sl_multiplier` commit with a `tp_r` re-sweep at the chosen multiplier per cell.

## Reading the volume split table

Always printed alongside main results (regardless of `volume_suppress` setting):

```text
  Strategy              TF    High Vol   Low Vol   Δ
  bos                   1h    +0.31R     +0.10R    +0.21R
  pin_bar               1h    +0.18R     +0.37R    -0.19R
```

- **Positive Δ** (High Vol >> Low Vol): consider `volume_suppress = true` for this strategy
- **Negative Δ** (Low Vol >> High Vol): do NOT suppress — low-vol signals have edge here
- Decision threshold: |Δ| > 0.10R is meaningful; < 0.05R is noise

**Before acting on a positive Δ (both added 2026-08-06):**

1. **`volume_suppress = true` requires `adr_exempt = true`** on the same strategy —
   `load_signal_config` raises otherwise. The ADR gate keeps quiet, small-range bars
   while this flag keeps high-volume ones, and range/volume correlate at ~+0.65, so
   the conjunction discards ~99% of signals silently.
2. **The Δ above is a point estimate, not a result.** Significance-test it before
   committing: a Δ of +0.11R justified throwing away 94% of `bos`'s signals for
   months, and on retest every cell gave p ≥ 0.113 with the CI straddling zero. All
   four shipped volume flags were removed once tested.

See `/volume-sweep` and `docs/audits/2026-08-06-adr-volume-gate-conjunction.md`.

**A14b findings — SUPERSEDED; do not re-apply.** Every A14b volume flag was removed on
2026-08-06: three because the ADR-gate conjunction voided their strategies, and `bos`
because its claim failed retest on equities (p ≥ 0.113 on every cell). **As of
2026-08-06 no strategy in either config sets `volume_suppress*`.** The old
suppress/do-not lists survive only in `/volume-sweep`'s superseded table, kept as a
record of how the decisions were made. Always re-run the volume split after any tp_r
change before trusting any old Δ.

## Reading the duration table

```text
  Strategy    TF    Trades   Avg Hold   Median Hold   Max Hold
  engulfing   1h     328     1.2d       16.0h         10.1d
  bos         1h    4356     1.4d       13.0h         39.8d
```

Speed tiers (⚠ crypto-era illustration — wifey's TFs are 4h/1d/1wk and its bars are RTH;
re-derive tiers from a current duration table before quoting any of these):

- **Fast < 1d median**: marubozu, eqh_eql, trend_day — hits SL/TP quickly
- **Overnight 13–16h**: all candlestick patterns regardless of TF — NOT scalping strategies
- **Multi-day**: bos 4h (2.2d), order_block 1d (6.3d) — need patient management

## Committing TOML config

### Strategy-wide override

```toml
[strategy_params.engulfing]
tp_r = 3.0              # applies to all TFs
```

### TF-specific override

```toml
[strategy_params.engulfing]
tp_r_4h = 3.0           # 4h only
tp_r_1d = 2.0           # 1d only
# other TFs fall back to global tp_r
```

### ATR SL override (per-strategy)

```toml
# Required once at the top level — the floor is what makes per-strategy
# atr_sl_multiplier actually take effect for structural strategies.
atr_sl_floor = true

[strategy_params.bos]
atr_sl_multiplier = 1.5
atr_sl_multiplier_4h = 2.0    # TF-specific
```

### Suppressing a TF via strategy_timeframes

```toml
[strategy_timeframes]
engulfing = ["4h", "1d", "1wk"]   # removes 1h — high noise, low edge
```

### When to use strategy-wide vs TF-specific

- If the optimal value is the same across all TFs → strategy-wide `tp_r`
- If one TF has a clearly different optimum → TF-specific key
- If one TF is consistently negative → add it to `[strategy_timeframes]` to suppress entirely

## After committing findings

```bash
# Persist winning config to DB
make wifey-backtest CONFIG=config/signal_watch.toml SAVE=1

# Optionally recalibrate star ratings from DB
wifey recalibrate          # dry-run — shows diff
wifey recalibrate --apply --config config/signal_watch.toml  # writes confidence_ratings
```

## Where findings are stored

- `config/signal_watch.toml` — committed `[strategy_params.*]` overrides, each with an
  inline WFO-evidence comment (the durable record of *why* a value was picked)
- `analytics.db` — all saved `backtest_runs` rows (queryable via DuckDB)
- (The old `project_f6_tp_sweep_findings.md` / `project_a13_volume_findings.md` memory
  files no longer exist in either repo's tree — do not cite them)

## Task: interpret and commit sweep results

When the user pastes a sweep table or asks to translate findings:

1. Read the table — identify peak column per strategy × TF row (respecting min_trades thresholds)
2. Check volume split: note any strategies where |Δ| > 0.10R
3. Check duration table: categorise strategies into speed tiers
4. Open `config/signal_watch.toml` (or target config) and update `[strategy_params.*]` entries
5. Commit the changes
6. Run `make wifey-backtest CONFIG=<file> SAVE=1`
7. Record the evidence as inline TOML comments beside each changed value
8. Update MEMORY.md with session summary
