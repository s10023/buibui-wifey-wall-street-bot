# The gap through the stop, measured on both books

**Date:** 2026-08-19
**Verdict:** **BOUNDED** — the bias is real but **SHARED**, so correcting it moves both books
together and changes no verdict. Live −0.1374R per loss against a matched-backtest −0.0976R, a
difference whose 95% CI **[−0.1180, +0.0266] contains zero**. It becomes a genuine distortion only
against a stop-free external benchmark.
**Scope:** measurement only, no code changed — `analytics/signal/outcome_backfill.py:322`,
`analytics/backtest/engine.py:1116`, `signal_alert_outcomes`, `backtest_trades`, `ohlcv`

## The question

Both books resolve a stopped-out trade at exactly the stop price: `_scan_forward` returns a hard
`-1.0`, and `engine.py:1116` sets `exit_price = sl_price`. Neither asks whether price was *already*
past the stop when the bar opened. `2026-08-19-live-ledger-net-of-cost.md` filed the size of that
absence as **~−0.1026R per resolved row against the cost charge's −0.0141R**, i.e. ~7× — but filed
it explicitly as a **bound, not a measurement**, on the grounds that the fill assumption was
untested and that on `4h` an RTH "gap" might include the resample boundary rather than a real
overnight break.

> Is the −0.1026R soft, and is the absence symmetric between the two books?

Both halves matter. A soft number should not be quoted as ~7×; and a *shared* absence changes what
fixing it buys.

## Method and its one validation

For every resolved loss, the SL-hit bar was re-derived by replaying the same forward scan, then the
bar's **open** compared against `sl_price`. A stop-market resting at `sl_price` fills at the open
when price gapped past it, so realized `R = (open − entry) / |entry − sl| × sign`.

The re-derivation was checked against the resolver's own stored `outcome_filled_at_ms` and matched
on **218 of 218** rows. Without that check the rest of the walk would be an assertion.

## Finding 1 — the resample-boundary confound is empty

`4h` bars sit on a fixed **UTC** grid (13:30 and 17:30 UTC), two per RTH day, so the boundary
between the two same-day bars is continuous and only the day boundary is a real break.

| book | gapped losses | at an intraday boundary |
| --- | --- | --- |
| live | 46 | **0** |
| backtest | 819 | **2 (0.24%)** |

The confound is real in principle and absent in fact, on a backtest sample ~18× the live one. It is
also structurally near-impossible: a same-session second bar opens at the first bar's close, so a
stop breached there would already have been caught by the earlier bar's low. **−0.1026R was never
soft** — the caveat should be retired rather than carried, because carrying it says the number is
weaker than it is.

## Finding 2 — the absence is shared, and that is the decision

Matched on the same 13 symbols, the same `4h`+`1d`, and the same window
(2026-05-20 → 2026-08-13):

| | gap rate | mean realized R when gapped | slip per loss |
| --- | --- | --- | --- |
| live | 21.1% | −1.6512 | **−0.1374R** |
| backtest, matched | 17.2% | −1.5677 | **−0.0976R** |

Difference −0.0398R; bootstrap 95% CI **[−0.1180, +0.0266]**, so it is **not distinguishable from
zero**. Matching mattered: unmatched, the backtest reads −0.0767R per trade, and the gap to live
looked like an asymmetry when most of it was population — the unmatched pool spans 505 symbols,
years of history, and a `1wk` book whose gap rate is 1.0%.

⚠ **Per-loss, not per-row.** Per-*row* slip also moves with the loss **share** of the denominator
(live 74.7%, matched backtest 64.4%), which differs for reasons that have nothing to do with the
fill assumption. Quoting per-row across two books with different win rates compares two things at
once.

Applied to both books, live pooled `avg_r` would move **−0.2192 → −0.3218**.

## Finding 3 — an ET hour is not a stable key on a UTC bar grid

The same UTC grid renders **09:30 / 13:30 ET in summer and 08:30 / 12:30 ET in winter**. Bar count
and session alignment are unaffected — still two per day — but any logic keyed on an ET *hour*
silently mislabels every winter bar. The first classifier written for this audit did exactly that,
and the live ledger could never have exposed it: every resolved row to date is EDT-dated. This is
the sibling of the standing rule that a bar count is not a calendar span; the durable form is that
**neither a bar count nor a wall-clock hour survives a tape whose grid is fixed in a different
timezone**.

## What this does and does not buy

Fixing it is correctness, not a decision change. Because the bias is shared and the difference is
within noise, no backtest-vs-live comparison moves, and no sleeve verdict is reachable from it —
the eight sleeves are vol-targeted books, not stop-exited trades.

The forward-looking reason it should still be fixed: a shared bias is invisible between the two
books but **not** against an external benchmark. SPY buy-hold and random-entry nulls are on the G2
roadmap and carry no stops, so they carry no gap slip; the moment either is used as a reference,
~0.10R per loss runs in our favour.

⚠ **Cost of the fix, stated so it is scheduled rather than assumed cheap:** it changes
`engine.py`, requires a **second** full restatement of the live ledger on top of migration 004, and
**will move the regression goldens** — a real move, not the data drift the `db-update` banner
warns about.

## Reproducing this

Four scripts in `docs/plans/scripts/` (gitignored, like the rest of that tree), each
read-only against `analytics.db`:

```bash
PYTHONPATH=. poetry run python docs/plans/scripts/gap_through_stop_live.py         # finding 1, live
PYTHONPATH=. poetry run python docs/plans/scripts/gap_through_stop_backtest.py     # finding 1, backtest + finding 3
PYTHONPATH=. poetry run python docs/plans/scripts/gap_through_stop_matched.py      # finding 2, matched table
PYTHONPATH=. poetry run python docs/plans/scripts/gap_through_stop_significance.py # finding 2, the CI
```

⚠ **The bootstrap is seeded but the queries must also be ORDERED.** A seeded
`default_rng` still draws differently when DuckDB returns rows in a different order, so the
first run of the significance script produced a CI whose endpoints moved in the third
decimal between invocations. `ORDER BY` on both queries makes it byte-reproducible; the CI
quoted above is the deterministic one. **A seed alone does not make a resampling result
reproducible — the input order is half of it.**

## Still open

- Whether to apply it at all — **a user call**, unchanged, because it moves both books.
- Slippage *beyond* the opening print is not modelled here. It would make both books worse, in the
  same direction, so it cannot rescue the asymmetry finding.
