# Conflict-Resolver Gate Ordering — Measured and Declined (2026-08-13)

**Verdict: the briefed defect (EV gate ordered before the volume/bias gates) does
not exist — reordering the EV gate is provably verdict-neutral. A DIFFERENT
ordering defect is real: `_apply_conflict_resolver` discards a direction before
any dropping gate runs, on 10% of signal moments. But it changes dispatch on only
0.55% of moments and its +7.92R/yr benefit sits at t=+1.09, failing the 1.64 bar
that #151 settled for the EV gate. DECLINED on evidence, not fixed.**

## The briefed mechanism does not hold

The carried task claimed the EV gate at `analytics/signal/scanner.py:749` runs
"before the volume and bias gates, so a signal is judged on evidence it would
never have produced post-bias". Two things are wrong with that.

**The evidence population already carries those filters.** `_compute_backtest` is
called (scanner.py:689–702) with `day_filter`, `volume_suppress{,_long,_short}`,
`volume_spike_boost*`, `adr_suppress_threshold` and `adr_exempt`. So volume, ADR
and the day filter are all *in* the population the EV gate judges.

**Reordering the EV gate cannot change a verdict.** `passes_ev_gate` reads only
`(bt_result, direction, timeframe, backtest_cfg)`. There are zero assignments to
`.direction` or `.strategy` anywhere in `analytics/signal/`, so no downstream gate
mutates any input it reads. Moving the call changes how many backtests get
computed — not one dispatch decision.

Of the gates absent from the backtest population, only **one actually drops**:

| Live gate | Drops? | In EV gate's evidence? |
| --- | --- | --- |
| day_filter `tue_thu` | yes | yes |
| EV gate (step 3) | yes | — is the gate |
| volume gate | yes | yes |
| regime gate | **no** — `mode = "soft"` | no |
| direction_filter | **no** — `mode = "soft"` | no |
| **HTF-EMA (F8)** | **yes** — `mode = "hard"` | **no** |
| ADR gate (4h only) | yes | yes |
| DOW suppress | no (adjusts stars) | n/a |

The single genuine population mismatch is the F8 HTF-EMA hard gate, and closing
it means threading a parameter into `_compute_backtest` — not reordering.

## The real ordering defect

`_apply_conflict_resolver` runs at **scanner.py:571**, before every dropping gate.
It collapses opposing directions to one winner by star rating and **drops the
loser outright** (`gates.py:154–173`). If the winner is then killed by the EV,
volume or HTF-EMA gate, the losing direction — which may have passed all of them
— is already gone.

The two compositions are not nested: `ev_filter(resolve(E))` (current) versus
`resolve(ev_filter(E))` (proposed). Filtering first can change which side wins the
max-confidence comparison, so the reorder can both gain and lose signals.

## Measurement

Script: `docs/plans/scripts/conflict_resolver_ordering_impact.py` (gitignored,
durable). Every decision calls a production function — `_compute_backtest`,
`passes_ev_gate`, `_apply_conflict_resolver`, and the config's `effective_*`
resolvers — so there is no replication to validate. Live population: 13 symbols ×
`4h`/`1d`, 286 cells, `days = 365`, `config/signal_watch.toml`.

```text
signal moments          : 2,179
  with both directions  :   218  (10.00%)
  dispatch set unchanged: 2,167
  dispatch set CHANGED  :    12  ( 0.55%)

signals GAINED by reorder  n=12  resolved=10  totR=+6.87  avgR=+0.687
   R: [-1.02,-1.02,-1.02,-1.02,-1.01,-1.0, 2.49, 2.5, 2.99, 4.99]
signals LOST by reorder    n= 1  resolved= 1  totR=-1.05  (MSFT 1d bos short)

net R delta               : +7.92R over 365d
per-trade contribution    : n=11  mean=+0.720R  sd=2.188  se=0.660  t=+1.09
=> NOT distinguishable from zero at |t| >= 1.64
```

The 10.00% conflict rate independently cross-checks a 10.83% rate derived from
`backtest_trades` over the same live cells by a different query.

## Why declined

Only 12 of 218 conflict moments change dispatch, because in almost every conflict
the winner passes the EV gate anyway and the order is moot. The +7.92R rests on
**11 resolved trades**, and 4 of the 10 gains (`+4.99, +2.99, +2.50, +2.49`)
carry the entire sum. At `t = +1.09` it fails the one-sided 95% bar that #151
settled for the EV gate — shipping it would be the "threshold applied to a point
estimate" move that `min_avg_r_z` exists to prevent.

## Bounds on the claim

- Population is **trade-producing** signals under one coherent parameterization.
- **HTF-EMA is not modelled** (no `_compute_backtest` parameter). It would kill
  more winners, so the true gain is probably *larger* than +7.92R — measured on an
  even smaller n, so this does not change the disposition.
- Live conflict frequency is **unobservable from the ledger**: `ev.conflict` is
  rendered into the Telegram message only (`signals/alert_formatter.py:368`) and
  never persisted to `signals`. A count of conflict-tagged ledger rows returns 0
  of 298 — that is the field being absent, not conflicts being rare. Any future
  attempt to measure this live needs the column added first.

## Transferable rules

- **A gate's evidence population is set by how its input was PARAMETERIZED, not by
  where the gate sits in the chain.** Reordering a filter whose verdict depends on
  none of its neighbours' outputs is a no-op; check for mutation of the inputs
  before assuming order matters.
- **`enabled = true` is not "drops".** Three of five bias gates were enabled; two
  were `mode = "soft"` (log and keep) and dropped nothing. Read the mode, not the
  flag.
- **A zero count from a render-only field is an artifact.** Before quoting an
  absence, confirm the field is persisted where you are counting it.
- **Sub-threshold is a verdict, not a null result.** Record the number and the
  bar it failed, so the next session does not re-measure it.
