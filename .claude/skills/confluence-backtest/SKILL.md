---
name: confluence-backtest
description: >
  Run cross-TF and same-TF co-firing confluence backtests via `--cross-tf` and
  `--combo` modes, then spot-check the resulting `backtest_combos` /
  `backtest_cross_tf_combos` tables via `tools/combo_health.py`. Measures
  uplift when 2+ strategies agree within a window — feeds the live
  signal-watch combo gate and the D10 confluence research. Invoke when the
  user says "/confluence-backtest", asks to run "cross-TF" or "combo" or
  "co-firing" backtests, mentions HTF/LTF pairs, `window_hours`, `D10`, wants
  to measure confluence uplift, or asks to "spot-check combos", "are combos
  healthy", or "did the combo refresh work".
allowed-tools: Bash, Read
---

# Confluence Backtest — Cross-TF + Same-TF Co-Firing

Two related backtest modes that measure uplift when multiple strategies agree:

- `--combo` — **same-TF** co-firing: two strategies fire on the same symbol +
  TF within a candle window. Powers the live `[combo]` gate in
  `signal_watch.toml`.
- `--cross-tf` — **cross-TF** co-firing: an HTF context strategy + an LTF
  entry strategy. Powers Card 12 (`query_cross_tf_combos`) and the D10
  confluence layer roadmap.

Both write to dedicated combo tables in `analytics.db` and are surfaced in the
web UI Backtest tab and the digest queries.

## Same-TF combo (`--combo`)

```bash
# Full co-firing sweep across all (strategy_a, strategy_b, symbol, tf) pairs
make wifey-combo-backtest CONFIG=config/signal_watch.toml SAVE=1

# With anchored window for comparable runs:
make wifey-combo-backtest CONFIG=config/signal_watch.toml SINCE=2025-09-12 SAVE=1

# Tighter / wider co-firing window (in candles)
make wifey-combo-backtest CONFIG=config/signal_watch.toml WINDOW=2 SAVE=1
```

Direct CLI:

```bash
wifey backtest --combo --config config/signal_watch.toml --save
wifey backtest --combo --symbols AAPL --timeframes 4h --window 2
```

Key flags: `--window N` (candles between co-firing signals; default tuned per
config), `--workers N` (parallel pairs), `--day-filter`, `--min-trades N`,
`--fee-pct`.

## Cross-TF co-firing (`--cross-tf`)

```bash
# All 5 canonical HTF:LTF pairs
make wifey-cross-tf-backtest CONFIG=config/signal_watch.toml SAVE=1

# Specific pairs only. All 5 canonical defaults are fetchable here (the dead
# `15m` pairs the fork inherited are gone — tests/test_cross_tf_cofire.py pins
# it), but live scans run 4h/1d only, so a pair with a `1h` leg can never
# co-fire on the live path — prefer the live-reachable ones:
make wifey-cross-tf-backtest \
  CONFIG=config/signal_watch.toml \
  HTF_LTF="1wk:1d 1wk:4h 1d:4h" \
  SAVE=1

# Sweep window_hours (the LTF lookback for HTF context)
make wifey-cross-tf-backtest CONFIG=config/signal_watch.toml WINDOW_HOURS=2.0 SAVE=1
make wifey-cross-tf-backtest CONFIG=config/signal_watch.toml WINDOW_HOURS=8.0 SAVE=1
```

Direct CLI:

```bash
wifey backtest --cross-tf --config config/signal_watch.toml --save
wifey backtest --cross-tf --htf-ltf 1wk:1d 1d:4h --window-hours 4.0
```

Key flags: `--htf-ltf "HTF:LTF ..."` (default: 5 canonical pairs),
`--window-hours FLOAT` (default `4.0` — how far back from each LTF candle to
search for an HTF signal), `--workers N`, `--day-filter`, `--min-trades`,
`--fee-pct`.

## Post-run health check

After any `SAVE=1` run — and especially after refreshing both signal_watch configs —
spot-check the resulting tables before drawing conclusions:

```bash
PYTHONPATH=. poetry run python tools/combo_health.py
# Custom freshness window (default 2h) or different day_filter bucket:
PYTHONPATH=. poetry run python tools/combo_health.py --fresh-hours 6 --day-filter off
```

Reports, per table (`backtest_combos`, `backtest_cross_tf_combos`):

- Total row count and `MAX(run_at_ms)` (last save).
- Rows from runs within the freshness window (defaults to 2h).
- `day_filter` distribution (`off` / `weekdays` / `tue_thu`).
- Count and top 10 rows that pass the live alert gates:
  - same-TF: `tue_thu` + `avg_r ≥ 1.0` + `closed_trades ≥ 5`
  - cross-TF: `tue_thu` + `avg_r ≥ 0.0` + `closed_trades ≥ 5`

Flags: `--db`, `--fresh-hours`, `--same-tf-min-avg-r`, `--cross-tf-min-avg-r`,
`--min-trades`, `--day-filter`. Defaults match `[combo]` in
`config/strategy_params.toml`.

Common interpretations:

- **n_fresh > 0** for both tables → refresh wrote rows; the `last_run_utc`
  timestamp should match the wall-clock time of the run.
- **same-TF viable count drops sharply between refreshes** → real regime
  shift in confluence edge (e.g. the 2026-04-22 → 2026-05-11 drop from 28
  to 13). Confluence blockquote in Telegram alerts will thin.
- **Viable count = 0** → no surviving combos pass `min_avg_r`. Either the
  gate is too tight or confluence has no edge in the current regime;
  consider relaxing `[combo]` thresholds before assuming a data bug.

## Reading the output

Both modes print per-pair tables with: `trades`, `win%`, `avg_r`, `lift_R`
(uplift vs the LTF / strategy_b baseline). What to look for:

- **`lift_R > 0`** — confluence helps; promote the pair to live config.
- **`lift_R ≈ 0`** — neutral; sample is probably too small or the pair is
  redundant.
- **`lift_R < 0`** — confluence hurts; the second signal is selecting worse
  setups (or co-firing is rare and noisy). Suppress.

Rule of thumb: only act on pairs with `trades ≥ 30` (combo) or `trades ≥ 20`
(cross-TF). Smaller samples are noise.

## Wiring confluence back into the live config

Same-TF combos: edit `[combo]` in `config/signal_watch.toml` (or the variant
config) — list pair allowlists / suppress rules. The signal daemon refreshes
the combo lookup every 10 cycles.

Cross-TF: the live wiring HAS landed (D10 step 4) — `signal_runner` loads
`get_cross_tf_combo_lookup` at startup and refreshes it on the same 10-cycle
cadence, and `run_scan_cycle` tags co-fires via `_find_cross_tf_cofire`.
⚠ Both combo tables currently hold **0 rows**, so the whole co-fire layer is
inert until a `SAVE=1` combo / cross-TF refresh writes them — `combo_health`
reporting empty means *never refreshed*, not refresh-failed.

After updating the config, `/db-update` (or at minimum
`make wifey-recalibrate`) so star ratings reflect the new gate.

## When to run

- After adding a new strategy — both modes, to find which existing strategies
  it pairs well with.
- After changing entry / SL logic on any strategy — combo lift is sensitive
  to per-trade R distribution.
- Before promoting an experimental config — cross-TF sweep across multiple
  `--window-hours` values to pick the optimum.
- Quarterly, alongside `/wfo-sweep`, to catch drift in pair edges.

## Implementation files

| File | Role |
| ------ | ------ |
| `analytics/backtest_lib.py` | `run_combo_backtest()`, cross-TF combo runner, D10 result types |
| `analytics/backtest_runner.py` | `run_combo_backtest_cmd()`, `run_cross_tf_combo_backtest_cmd()` |
| `analytics/signal_config.py` | `ComboConfig` (`[combo]` section parser) |
| `analytics/signal_lib.py` | Live combo detection in `run_scan_cycle()` Phase 3 |
| `analytics/digest_lib.py` | Card 12 `query_cross_tf_combos` |
| `analytics/data_store.py` | Combo result tables (`backtest_combos`, `backtest_cross_tf_combos`; real home `analytics/store/`) |
| `wifey.py` | `--combo`, `--cross-tf`, `--htf-ltf`, `--window`, `--window-hours` flags |
| `Makefile` | `wifey-combo-backtest`, `wifey-cross-tf-backtest` targets |
| `tools/combo_health.py` | Post-run spot-check: totals, freshness, live-gate viable counts, top combos |

## Related

- `/backtest-run` — general backtest invocations.
- `/wfo-sweep` — single-strategy tp_r refresh; run before confluence sweeps so
  baselines are fair.
- D10 in MEMORY To-Do — full confluence-layer roadmap; cross-TF results feed
  steps 3–5.
