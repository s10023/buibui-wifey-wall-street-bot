# Per-TF directional `tp_r` keys — 32 declared, none applied, all removed

**Date:** 2026-10-01
**Audit:** this file. Owner: Issue #317.

## Verdict

FOUND and FIXED — all 32 per-timeframe directional `tp_r` keys (`tp_r_long_<tf>` / `tp_r_short_<tf>`) in the
two live configs are parsed and applied by nothing in production. The live alert and the sweep both resolve each
of them to a different value from the one declared, and 31 of the 32 sit on a declared cell. The
alert, the live EV-gate book and the ratings sweep agree with one another on every one, so there
is no gate/alert split; the calibrations just never run. The operator chose deletion on
2026-10-01, which changes nothing live: every key is demoted to a `# … dropped (#317)` comment
that keeps its value and provenance, all 531 live resolutions are identical before and after, and
a test now refuses a re-declared key while nothing applies it. Wiring them in instead would have
moved live targets on up to 31 cells, a `tp_r` change under the TA freeze. The one consumer
that did apply them was the regression harness, so the goldens pinned metrics for targets
production never ran; they are regenerated here against the unchanged fixtures.

## The defect

`StrategyOverride` parses `tp_r_long_<tf>` and `tp_r_short_<tf>` into `tp_r_long_per_tf` /
`tp_r_short_per_tf` in both loaders (`analytics/signal_config.py`, `analytics/backtest_config.py`).
Two consumers could apply them, and neither does:

- **The live path** resolves `tp_r` through `analytics/signal/resolvers.py::_resolve_tp_r`, which is
  used by the scanner (alert target and EV-gate book), `atr_floor.py` and `signal_test_runner.py`.
  Its order is symbol+TF → symbol → strategy+TF → strategy+direction → strategy → global. It has no
  per-TF directional step at either the strategy or the symbol level.
- **The sweep** (`analytics/backtest_runner.py`, lines 529 and 756) calls
  `BacktestSweepConfig.effective_tp_r(strategy, symbol, tf)` with no `direction`. That method has
  the per-TF directional steps, but only behind `direction == "long"` / `"short"`, so from this
  call site they never fire.

No production caller passes `effective_tp_r` a direction. One test does:
`tests/test_regression.py::test_golden_metrics` resolves `tp_r_long` and `tp_r_short` through
`effective_tp_r(..., "long" | "short")` and hands them to the engine. The regression gate was
therefore the only code that applied these keys, and the goldens recorded metrics at targets
neither alerts nor ratings ever used. The gate guarded a configuration production does not run. `check-dead-surfaces` cannot see this: it works at the
`(strategy × timeframe)` cell level, and every affected cell is live and rated.

## Measurement

Both configs, strategy level (no symbol-level directional keys exist). "Alert" is
`_resolve_tp_r(..., direction)`, "sweep" is the direction-less `effective_tp_r`, and "live cell"
means the timeframe is in that strategy's `strategy_timeframes`.

`config/signal_watch.toml` (14 keys):

| Strategy | TF | Dir | Declared | Alert | Sweep | Live cell |
| --- | --- | --- | ---: | ---: | ---: | --- |
| bos | 1d | long | 4.00 | 2.50 | 2.50 | yes |
| bos | 4h | short | 1.50 | 3.00 | 3.00 | no |
| engulfing | 4h | short | 3.00 | 4.00 | 4.00 | yes |
| eqh_eql | 1d | long | 5.00 | 3.00 | 3.00 | yes |
| hammer_hanging_man | 1d | long | 2.50 | 2.00 | 2.00 | yes |
| hammer_hanging_man | 1d | short | 1.50 | 2.00 | 2.00 | yes |
| morning_evening_star | 1d | long | 5.00 | 2.00 | 2.00 | yes |
| morning_evening_star | 4h | long | 4.50 | 2.50 | 2.50 | yes |
| morning_evening_star | 1d | short | 1.50 | 2.00 | 2.00 | yes |
| order_block | 4h | short | 4.50 | 2.50 | 2.50 | yes |
| pin_bar | 1d | long | 5.00 | 2.50 | 2.50 | yes |
| trend_day | 4h | long | 5.00 | 4.50 | 4.50 | yes |
| trend_day | 1d | short | 1.00 | 5.00 | 5.00 | yes |
| trend_day | 4h | short | 3.00 | 4.50 | 4.50 | yes |

`config/signal_watch_weekdays.toml` (18 keys):

| Strategy | TF | Dir | Declared | Alert | Sweep | Live cell |
| --- | --- | --- | ---: | ---: | ---: | --- |
| bos | 1d | long | 4.00 | 2.50 | 2.50 | yes |
| ema | 4h | long | 3.50 | 3.00 | 3.00 | yes |
| ema | 4h | short | 1.00 | 3.00 | 3.00 | yes |
| eqh_eql | 4h | short | 5.00 | 2.00 | 2.00 | yes |
| hammer_hanging_man | 1d | long | 2.50 | 2.00 | 2.00 | yes |
| morning_evening_star | 1d | long | 3.50 | 2.00 | 2.00 | yes |
| morning_evening_star | 1wk | long | 3.50 | 1.00 | 1.00 | yes |
| morning_evening_star | 1d | short | 1.50 | 2.00 | 2.00 | yes |
| morning_evening_star | 4h | short | 1.00 | 1.50 | 1.50 | yes |
| orb | 4h | short | 2.00 | 3.00 | 3.00 | yes |
| order_block | 1wk | long | 2.50 | 2.00 | 2.00 | yes |
| order_block | 1d | short | 2.50 | 3.00 | 3.00 | yes |
| order_block | 4h | short | 1.50 | 2.00 | 2.00 | yes |
| pin_bar | 1wk | long | 3.50 | 4.50 | 4.50 | yes |
| pin_bar | 4h | long | 3.00 | 2.00 | 2.00 | yes |
| trend_day | 1d | long | 5.00 | 3.50 | 3.50 | yes |
| trend_day | 1wk | long | 4.50 | 3.00 | 3.00 | yes |
| trend_day | 1wk | short | 1.50 | 3.00 | 3.00 | yes |

32 of 32 resolve differently from declared, 31 are on a live cell, and alert equals sweep on all
32. The largest gap is `trend_day` 1d short in `signal_watch.toml`: declared 1.0R, alerting and
rated at 5.0R.

## A latent split the same probe found

The sweep also never passes strategy-wide `tp_r_long` / `tp_r_short` to the engine:
`backtest_runner` neither resolves them nor forwards them to `run_backtest`'s `tp_r_long` /
`tp_r_short` parameters, while the scanner's EV-gate book does both. Today this is inert: neither
live config declares a strategy-wide directional `tp_r` (probe B below returns 0 cells). The first
one declared would split the alert and the EV-gate book from the ratings, which is exactly the
split this audit found absent for the per-TF keys. Wiring in the per-TF keys has to close this too.

## The two remedies

| Remedy | Live change | Needs |
| --- | --- | --- |
| Delete the 32 keys (and, optionally, the `*_per_tf` directional fields and their parse) | none: every cell already resolves without them | a config-only PR; the parse can stay as dead code or go with its tests |
| Wire them in: add the per-TF directional step to `_resolve_tp_r`, pass `direction` from the sweep, and forward `tp_r_long` / `tp_r_short` there | targets move on up to 31 live cells | an explicit lift of the TA freeze for these cells, then `db-update`, which re-rates them |

Wiring in is also a provenance problem. Each value's comment says it was picked from the
directional split of an earlier resweep ("Phase 2 resweep", "candle-resweep", "live-parity
re-derive"), so each is an in-sample argmax over a `tp_r` grid, several on thin samples (n=10,
n=15). The sweep that produces `confidence_ratings` never executed any of them, so no rating
reflects them. Shipping them would commit selected-but-unrated parameters.

**Decision (operator, 2026-10-01): delete.** Each key line became a comment of the form
`# tp_r_long_1d = 5.0 dropped 2026-10-01 (#317): no consumer ever applied it — <original note>`,
following the files' existing `# tp_r_long_1d dropped — …` convention, so the value and its
provenance stay where a reader of the cell looks. The parser and the `*_per_tf` directional fields
stay: removing them is a code change with its own tests, and the guard below makes them inert.

**Verification.** A dump of every live resolution (both configs × every strategy × each declared
timeframe × symbols `-`, `NVDA`, `AAPL` × directions `-`, `long`, `short`, through both
`_resolve_tp_r` and the sweep's `effective_tp_r`) gives 531 lines, identical before and after. The
positive control: setting one `tp_r_4h` to 9.9 moves 9 of those lines, so the dump sees a real
`tp_r` change. `tests/test_signal_config.py::TestNoUnappliedDirectionalPerTfTpR` asserts that
neither shipped config declares a per-TF directional key, and it fails on the pre-deletion configs.
Its own positive control asserts that the key still parses into the field, so the guard cannot
pass vacuously. The two `*_strategy_params_parsed` tests pinned all 32 declared directional values;
their 32 asserts now pin the combined value each cell resolves to, which is the "alert" column
above.

**The goldens moved, by design.** With the keys gone the regression harness resolves the same
targets production does, so `make test-regression` failed on 23 `(strategy × timeframe)` cells:
9 in `golden_signal_watch.json` and 14 in `golden_weekdays.json`. Every moved cell is one that
lost a key; no other cell moved. Three cells that lost a key did not move, each for a stated
reason. `bos` 4h in `signal_watch.toml` is not a declared cell and has no golden. `eqh_eql` 1d has
no long trades on the fixture symbol. `morning_evening_star` 1wk long averages −1.02R, so every
long stopped out before any target. The goldens were regenerated with
`pytest tests/test_regression.py --update-golden` against the committed fixture parquets, not with
`make regression-update`, which re-extracts the parquets from `analytics.db`. This rules out data
drift by construction: only the two JSON files changed, and within them only those 23 cells and
`generated_at`.

## The doc half

The same branch corrects the prose that claimed the keys take effect: the `tp_r_long_per_tf` comment
on both `StrategyOverride` classes, the `effective_tp_r` docstrings (which describe the method
correctly but not its callers), and README's `[strategy_params]` resolution order, which omitted
the directional steps entirely.

## Reproduce

```python
from analytics.backtest_config import load_backtest_config
from analytics.signal.resolvers import _resolve_tp_r
from analytics.signal_config import load_signal_config

for path in ("config/signal_watch.toml", "config/signal_watch_weekdays.toml"):
    cfg, bt = load_signal_config(path), load_backtest_config(path)
    for strat, ov in sorted(cfg.strategy_params.items()):
        for d, table in (("long", ov.tp_r_long_per_tf), ("short", ov.tp_r_short_per_tf)):
            for tf, declared in sorted(table.items()):
                alert = _resolve_tp_r(cfg.strategy_params, strat, "", tf, cfg.tp_r, d)
                sweep = bt.effective_tp_r(strat, "", tf)
                live = tf in cfg.strategy_timeframes.get(strat, cfg.timeframes)
                print(path, strat, tf, d, declared, alert, sweep, live)
        # probe B: strategy-wide directional keys, per live cell
        for d, v in (("long", ov.tp_r_long), ("short", ov.tp_r_short)):
            if v is not None:
                print("strategy-wide", path, strat, d, v)
```

Run with `PYTHONUTF8=1 PYTHONPATH=. poetry run python <file>`. It needs only the two tracked
configs, no database.
