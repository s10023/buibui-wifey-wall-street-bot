# Per-TF directional `tp_r` keys — 32 declared, none applied

**Date:** 2026-10-01
**Audit:** this file. Owner: Issue #317.

## Verdict

FOUND — all 32 per-timeframe directional `tp_r` keys (`tp_r_long_<tf>` / `tp_r_short_<tf>`) in the
two live configs are parsed and applied by nothing. The live alert and the sweep both resolve each
of them to a different value from the one declared, and 31 of the 32 sit on a declared cell. The
alert, the live EV-gate book and the ratings sweep agree with one another on every one, so there
is no gate/alert split; the calibrations just never run. The operator chooses between two
remedies. Deleting the keys changes nothing live. Wiring them in moves live targets on up to 31
cells, which is a `tp_r` change under the TA freeze and needs `db-update`. Deletion is recommended,
because no sweep has ever executed these values, so wiring them in would ship parameters that
were never tested on this tape. This file fixes the doc half only.

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

`effective_tp_r` on both config classes is therefore the only code that honours the keys, and no
caller passes it a direction. `check-dead-surfaces` cannot see this: it works at the
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

Wiring in is also a provenance problem. The declared values were never executed by the sweep that
produces `confidence_ratings`, so nothing on this tape supports them; they are inherited or
hand-set picks. Shipping them would commit an untested parameter.

## The doc half

This branch corrects the prose that claimed the keys take effect: the `tp_r_long_per_tf` comment
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
