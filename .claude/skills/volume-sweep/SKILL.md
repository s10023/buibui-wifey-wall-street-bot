---
name: volume-sweep
description: >
  Test the `volume_suppress` flag per strategy by reading the "Volume Impact"
  split in backtest output (High Vol vs Low Vol avg R) and committing the
  winner to TOML.
  Invoke when the user says "/volume-sweep", adds a new strategy, changes entry
  logic, or asks about "volume_suppress", "volume spike boost", or
  "low-volume signals".
allowed-tools: Bash, Read, Edit, Write
effort: high
---

# Volume Suppression Testing

Dormant while the TA book is frozen (see CLAUDE.md → Fork lineage); run only for maintenance the
user asks for.

Test whether filtering low-volume candles improves strategy performance. Uses the per-strategy `volume_suppress` flag in `[strategy_params.X]` TOML blocks.

## What volume_suppress does

Suppresses signals where the signal candle's volume is below 1.5× the 20-candle rolling mean. Low-volume candles are considered noise — signals during illiquid conditions have less reliable follow-through.

The "Volume Impact" split table is **always printed** in backtest output regardless of whether `volume_suppress` is enabled. This lets you assess impact before committing to the filter.

## How to test

Run any backtest and look for the "Volume Impact" section in the output:

```bash
make wifey-backtest CONFIG=config/signal_watch.toml
```

The split table shows aggregated results across all symbols × TFs:

```text
  Strategy               Low-vol  Avg R    Normal  Avg R    Delta
  bos                        736 -0.32R      1104 -0.20R   +0.11R   ← suppress
  pin_bar                   1950 +0.22R       512 -0.00R   -0.22R   ← do NOT suppress
  engulfing                 1336 +0.33R       292 +0.30R   -0.03R   ← neutral
```

Decision threshold:

- Delta > +0.05R → `volume_suppress = true` (normal-vol signals win)
- Delta < -0.05R → `volume_suppress = false` (explicitly keep low-vol signals)
- |Delta| ≤ 0.05R → neutral (omit the flag entirely — inherits global default)

## Check the ADR gate before setting `volume_suppress = true`

The ADR gate is intraday-only (`adr_gate_applies()` — `1d` / `1wk` no-op, since "range consumed up
to this candle" needs more than one bar per calendar day). The conjunction below can only bite on
`4h`. On `1d` / `1wk` there is no conjunction to avoid, but check `adr_exempt` anyway — it is what
the `voided_volume_gates` load guard keys on, and the guard is timeframe-blind.

A favourable Delta is not sufficient. The split table above is computed on the population *before*
the volume filter, and it knows nothing about the `[bias]` ADR gate. The two gates select for
opposite bars: the ADR gate keeps candles that have consumed little of their typical daily range,
while `volume_suppress` keeps candles with ≥1.5× mean volume, and range and volume correlate at
+0.613 (1d) / +0.673 (4h) on the live watchlist. Their conjunction is nearly the empty set —
`P(pass both) = 0.0046` against `0.036` under independence, an 8× shortfall. Setting the flag on a
strategy that is not `adr_exempt` therefore discards roughly 99% of its signals, silently: the
detector still fires normally and the events drop one at a time. A strategy caught by this can fall
to near-zero signal count, or even flip the sign of the Delta this skill reads.

Before committing `volume_suppress = true` (or the `_long` / `_short` variants):

1. Check `[strategy_params.<name>].adr_exempt`. If it is not `true`, do not set the flag —
   `load_signal_config` refuses to load the config (`voided_volume_gates`), so the run fails loudly
   rather than silently.
2. If the strategy genuinely fires at range extremes by construction (breakouts, structure sweeps —
   `bos`, `eqh_eql`), set `adr_exempt = true` in the shared base `config/strategy_params.toml`, never
   in one day-filter config — an exemption declared only in one TOML does not carry over to the
   other, silently voiding it there.
3. Re-read the Delta after the ADR gate, not before. A split measured on the pre-gate population
   does not describe the population the flag will actually filter.
4. The TA-sweep freeze applies: tuning a flag to chase avg R is frozen work (CLAUDE.md → sleeve
   verdicts). Removing a provably-void flag is correctness and remains allowed.

Background: `docs/audits/2026-08-06-adr-volume-gate-conjunction.md`.

Related trap — **`volume_spike_boost` is inert without suppression.** The engine reads
`_boost` only inside `if _suppress and is_low_vol` (`analytics/backtest/engine.py`), so
a spike-boost flag on a strategy with no volume suppression changes no filtering
decision. `Trade.volume_spike` is still tagged, so the split stays measurable — but do
not read an unchanged result as "the boost did nothing useful"; it did nothing at all.

## Where to set volume_suppress

### Per-strategy

```toml
[strategy_params.bos]
tp_r = 3.0
volume_suppress = true        # set only after adr_exempt = true and a fresh Delta check

[strategy_params.pin_bar]
tp_r = 3.0
volume_suppress = false       # explicit false pins the low-vol edge; omit to inherit the default
```

Resolution order: per-strategy → global `[backtest].volume_suppress` (default false).

### Global fallback

```toml
[backtest]
volume_suppress = true   # applies to all strategies with no per-strategy override
```

No strategy in either production config currently sets `volume_suppress*` — the flags that once
reached the equity configs were removed because they conjoined with the ADR gate and voided their
strategies, or because the claim did not replicate on equities. Do not reuse an old sweep table:
re-measure Delta fresh each time, per config, since the day filter changes the trade population, and
read the ADR-gate check above before setting the flag.

## Workflow

```bash
# 1. Run sweep to see volume split table
make wifey-backtest CONFIG=config/signal_watch.toml

# 2. For each strategy compute Delta = normal_avg_r - low_vol_avg_r
#    > +0.05R → volume_suppress = true
#    < -0.05R → volume_suppress = false
#    |Δ| ≤ 0.05R → omit (neutral)
#
#    A raw Delta is a point estimate on a noisy sample, not a result. Test it: split
#    the unsuppressed trades on `Trade.low_volume` and run a Welch t-test (or bootstrap
#    the difference). Never compare flag-on against flag-off — on is a strict subset of
#    off, so the arms are dependent and no test applies.

# 2b. Confirm `adr_exempt = true` before any `volume_suppress = true`.
#     Without it `load_signal_config` raises (voided_volume_gates). See above.

# 3. Add volume_suppress to [strategy_params.X] in TOML
# 4. Repeat for signal_watch_weekdays.toml separately (day filter changes trade population)

# 5. Run quality gates
make lint-py && make typecheck && make test
```

## Implementation files

| File | Role |
| ------ | ------ |
| `analytics/backtest_lib.py` | `format_volume_split()` — volume split table; `run_backtest(volume_suppress=bool)` — skips low-vol signals when True |
| `analytics/backtest_config.py` | `StrategyOverride.volume_suppress: bool \| None`; `BacktestSweepConfig.volume_suppress: bool`; `effective_volume_suppress(strategy)` |
| `analytics/signal_config.py` | `StrategyOverride.volume_suppress: bool \| None`; `BacktestFilterConfig.volume_suppress: bool`; `SignalWatchConfig.effective_volume_suppress(strategy)` |
| `analytics/signal_lib.py` | `_resolve_volume_suppress()` — per-event lookup in live daemon loop |
| `analytics/backtest_runner.py` | Passes `cfg.effective_volume_suppress(strategy)` to every `run_backtest()` call |
| `config/signal_watch.toml` | Per-strategy `volume_suppress` in each `[strategy_params.X]` block |
| `config/signal_watch_weekdays.toml` | Same — weekdays-specific decisions |
