# North star, gates and frozen list

The goal every piece of work here must serve, the gates that order it, and what is deliberately
not done. Open work lives in GitHub Issues, never here. Built-and-measured verdicts live in
`CLAUDE.md` § Sleeve verdicts and `docs/audits/INDEX.md`.

## North star

> A vol-targeted, multi-sleeve paper portfolio on US equities, long-only on the wife sleeve,
> with Sharpe ≥ 1.0 net of honest costs over ≥ 3 consecutive months and max drawdown inside
> budget (~15% at a 10–15% vol target), with per-sleeve attribution. Then, and only then,
> Phase B (broker and automation) at the smallest viable size, one sleeve at a time.

- The number to manage is paper-book Sharpe net of costs, never win rate. Win rate and RR are
  sleeve personalities, not goals.
- The TA detector book is low-weight confirmation at most. The live alert loop keeps running as
  the out-of-sample ledger generator: data collection, not a tuning target.
- The wife channel is long-only by sleeve design; shorts go to the personal channel only.

## Acceptance gates

| Gate | Criterion | Decides |
| --- | --- | --- |
| G1 | First sized paper book, honest costs, paper Sharpe > 0 | Whether the sizing playbook transplants to equities |
| G2 | Core sleeve OOS Sharpe ≥ ~1 on the breadth universe, costs in, DSR/PBO-gated | That sleeve becomes the core. Any threshold revision is pre-registered before looking at results |
| G3 | Multi-sleeve paper book ≥ 3 consecutive months Sharpe ≥ 1.0 net, drawdown inside budget | Permission to spec Phase B: risk layer (kill-switch, drawdown governor) first, executor after |
| G4 | Live tracks paper within tolerance for ≥ 4 weeks at minimum size | Permission to scale size |

G1 is not live: it was scoped to the parent's paper book, which never re-evaluated it, and no
sleeve here has a positive per-trade edge net of cost to size. Sizing does not rescue a negative
per-trade edge, so do not port the parent's `portfolio/` until a sleeve shows one. Breadth relief
is not available either: the 505-member universe carries `n_eff` ≈ 2.96 at `1d`
(`make wifey-n-eff`), and `n_eff` tends to `1/rho` as names are added.

## Data-cost policy

Free first; pay only when a gate needs it.

| Data | Cost | When |
| --- | --- | --- |
| yfinance OHLCV `4h`/`1d`/`1wk` | free | now |
| Breadth universe (505 members, `config/universe.json`) | free | have; PIT membership deliberately not scraped, survivorship bounded in `universe_policy` |
| Delisted-ticker price history | paywalled | never: bound the claim instead |
| Earnings dates | free (yfinance / EDGAR) | have (`earnings_facts`); live wiring is an Issue |
| Ken French factor library | free | with the first paper book |
| Polygon.io (~$29/mo) | paid | on trigger only: Yahoo breaks, or > 2 years of `4h` needed |
| Order book / tape, options surface | paid | never: no phase needs them |

## Frozen

- New boolean TA detectors. Inherited category verdict (parent: 0/123 DSR); the equity null is
  stronger. Unfreeze post-G2 only, as confirmation features, never standalone.
- TA-book parameter sweeps (`tp_r`, gates, thresholds). Guard maintenance is excepted.
- UI work, except an eventual advisory trade card.
- Carry, funding and basis: not applicable to cash equities; never rebuild.
- Phase B and automation: gated G3 → G4, risk layer first, however good one month looks.
- LLM-as-signal experiments: never.
- A new free-data edge hunt: the arc is concluded
  (`docs/audits/2026-06-24-honest-exit-free-data-edge-arc.md`). Re-opening is per candidate and
  needs an explicit user go.
