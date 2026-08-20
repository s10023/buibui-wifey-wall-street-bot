# H-1: the 200d MA regime filter is a risk reducer, is not distinguishable from slow TSMOM, and is not a sleeve

**Date:** 2026-08-20
**Verdict:** **EXCLUDED as a sleeve** — no arm clears `GATE_SHARPE = 0.7` net of cost
(`ma200d` **0.593**, `ma40w` **0.604**, `tsmom12m` **0.492**, buy-and-hold **0.422** @2bps), and
the MA arms fail a second, independent leg at **PBO 0.877**. ⚠ **The premise H-1 actually asks —
"does the filter add anything over plain slow TSMOM?" — splits by arm, and the two halves of what
is one window do NOT get the same verdict.** For the weekly-rebalance arm it is a **powered null**
(+0.67%/yr, CI **[−1.23%, +2.53%]** contained inside its ±2.62% bar): no advantage, and the design
could have seen one. For the daily arm it is **BLOCKED** — +0.93%/yr, CI **[−0.78%, +2.64%]**,
which contains zero *and* breaches the ±2.37%/yr detection bar, so **INSUFFICIENT**, not refuted.
The hypothesis's own parenthetical is the part that **replicates**: the filter sits in the same
long/flat state as slow TSMOM **84.4%** of sessions (position corr **+0.641**), differing on ~39
sessions a year. What edge exists is **pre-1977 and reverses afterwards**.
**Scope:** measurement only, no production code changed. `^GSPC` `1d`, **24,774 sessions
1928-01-03 → 2026-08-19** (98.3y), price-only index, `k=1` so **no `n_eff` deflator**.
**Reproduce:** `docs/plans/scripts/h1_ma_regime_power_precheck.py` then
`h1_ma_regime_study.py` (both gitignored one-shots; the study imports its design from the
precheck so the two cannot drift).

## Provenance — H-1 is a translation, not a pundit quote, and "200d/40w" is one window

Checked rather than assumed, because the framing changes what a result would mean.
`memory/project_parent_fresh_eyes_port.md:60` records H-1 as a **hypothesis translation**: the
parent repo's crypto "50W-EMA 4-year-cycle" rendered into an equity form. The parent tracks the
Cowen 50-week idea **separately, as its own row**, and its Cowen-derived bear-score work uses the
`{50W SMA, 50W EMA, 200D EMA, 200D SMA, 20W SMA, 21W EMA}` band. So there is **no external author
to misquote and no published claim to replicate** — a prior session chose the canonical equity
trend window when porting the idea across.

⚠ **That makes "200d/40w" ONE lookback, not two: 40 weeks × 5 sessions = 200 sessions.** The two
halves differ only in **decision frequency** — decide daily and hold a day, versus decide weekly
and hold the week. `ma40w` is therefore reported throughout as a **rebalance-frequency arm**.
Reading it as an independent second window would have double-counted one idea as two, and would
have made a flat sensitivity profile look like two-for-two corroboration.

## Method

Four long-or-flat books on `^GSPC`, no shorts and no leverage. `signal_t` is computed from closes
up to and including `t` and earns the return of `t+1`; the lag lives inside one `positions()`
helper so there is no second place to get it wrong.

| arm | rule |
| --- | --- |
| `bh` | always long — benchmark |
| `ma200d` | long while close > SMA(200 sessions) |
| `ma40w` | long while Friday close > SMA(40 weeks), held through the week |
| `tsmom12m` | long while trailing 252-session return > 0 |

Every arm shares one evaluation window, starting where the **longest** warm-up (TSMOM's 252
sessions) is defined — comparing a 1927-start benchmark against a 1929-start filter would price a
different tape, not a different rule.

**Causality** was checked rather than argued: 6 truncation cuts × 4 arms produced **0**
disagreements against the full-series computation. ⚠ **A "did not change" result is satisfied both
by the invariant holding and by the perturbation never arriving**, so the check ships a positive
control — a deliberately non-causal rule reading tomorrow's close, which the same check flags on
**2 of 6** cuts. (Not 6 of 6 by construction: on a truncated series the peeked value is absent and
reads as `0.0`, which differs from the full-series answer only when the next session was up.) Two
of six is enough to establish the check is not vacuous, which is all a control owes.

**Costs** are charged where they are incurred — `|Δpos| × bps` per switch, at 0/1/2 bps. This book
has no `Trade` objects, so `analytics/backtest/cost_model.py` does not apply; feeding it this book
would mean inventing the trades first. At **3.1–6.0 switches per year** cost is immaterial: 2bps
moves Sharpe by ~0.01.

### Powered first, measured second

The design was priced before any return column was read, using only nuisance parameters
(volatility, time-in-market, switch counts). H-021 died on exactly this axis.

- **Primary MDE: 0.283** annualized Sharpe-of-difference, i.e. **~2.4pp/yr**. The study is
  **blind below that** and the verdict says so.
- **DSR-implied Sharpe bar: 0.5924 annualized**, which sits *below* `GATE_SHARPE = 0.7`. At 98
  years of daily depth **the economic bar binds, not the statistical one** — so a failure here is
  a failure of the idea, not of the sample.

⚠ **A units trap surfaced on the first precheck run and is worth carrying forward.**
`research_guards.psr` forms `z = (sr − sr_benchmark)·√(n_obs−1)`, so `sr` is **per-observation**
and `expected_max_sharpe`'s `sr_variance` is per-observation too. Passing an annualized `0.25`
(sd 0.5) means sd 0.5 *daily* ≈ 7.9 annualized, and the bar came back at **6.94 annualized** — a
gate nothing could clear. **It was caught only because it was absurd**; a subtler mis-scaling
would have printed a plausible number and been believed.

## Results

All figures net of 2bps per side.

| arm | ann.ret | ann.vol | Sharpe | maxDD | in-market | switches/yr |
| --- | --- | --- | --- | --- | --- | --- |
| `bh` | 7.98% | 0.189 | 0.422 | −86.2% | 100.0% | 0.00 |
| `tsmom12m` | 6.26% | 0.127 | 0.492 | −44.6% | 69.1% | 4.12 |
| `ma200d` | 7.19% | 0.121 | **0.593** | −51.8% | 67.4% | 5.99 |
| `ma40w` | 6.93% | 0.115 | **0.604** | −46.9% | 66.4% | 3.10 |

**The filter is a risk reducer, not a return generator.** `ma200d` earns *less* per year than
buy-and-hold (7.19% vs 7.98%) while cutting volatility by a third (0.121 vs 0.189) and halving the
worst drawdown (−51.8% vs −86.2%). Both statements are true simultaneously and quoting either
alone misleads.

### The disguise test — return-free, and it replicates

| arm | same state as `tsmom12m` | position corr | sessions differing |
| --- | --- | --- | --- |
| `ma200d` | **84.4%** | **+0.641** | 3,855 (~39/yr) |
| `ma40w` | 82.9% | +0.609 | 4,247 (~43/yr) |

These cannot be moved by how the P&L lands. H-1's parenthetical was substantially right: it is
**mostly** slow TSMOM, though not identically so — ~39 sessions a year of genuine disagreement is
enough room for a real difference, which is why the paired test below was worth running.

### Primary — the paired difference, and why the two arms get different verdicts

| pair | ann.diff | t (iid) | SR diff | block-bootstrap 95% | ±MDE bar | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| `ma200d − tsmom12m` | +0.93% | 1.10 | 0.111 | [−0.78%, +2.64%] | ±2.37% | **INSUFFICIENT** |
| `ma40w − tsmom12m` | +0.67% | 0.71 | 0.072 | [−1.23%, +2.53%] | ±2.62% | **POWERED NULL** |

⚠ **A CI containing zero is not the claim "ruled out".** `audit_guard.powered_null` requires the
interval to sit **strictly inside** ±bar. `ma40w` satisfies that, so its null is real: no
advantage over slow TSMOM, and the design could have seen one. `ma200d` does **not** — its upper
edge (+2.64%) breaches its bar (2.37%), so the correct reading there is **no advantage was
demonstrated, and an advantage as large as ~2.6pp/yr cannot be excluded.** Collapsing the two
states would print the confident one for both.

⚠ **Worth noticing why they diverge, because it is not the effect.** These are the same 200-session
lookback; the point estimates are close (+0.93% vs +0.67%) and both CIs are similar widths. What
separates them is the **bar**, not the interval: the weekly arm's difference series is noisier
(±2.62% vs ±2.37%), so its own detection threshold moved out further than its CI did. The
containment margin is **0.09pp** — `ma40w` clears by ~3% of its bar. It is a powered null on the
declared rule, and it is one narrowly enough that no weight should be put on the *contrast*
between the two rows.

### Sensitivity — 200d is not special

Sharpe across MA windows 50 → 300 spans just **0.556 – 0.606**, a flat profile with no peak worth
naming (nominal best is 225 at 0.606, inside the noise). **Max |t| over the grid is 1.31 against a
Bonferroni bar of 2.807** — the grid maximum does not clear even before asking whether picking a
maximum is legitimate. **PBO over that 10-window family is 0.877**: a window chosen as best
in-sample ranks below median out-of-sample ~88% of the time, so *window selection here does not
generalize at all*.

⚠ **PBO is a property of a trial family, not of an arm.** Only `ma200d` is a genuine member of the
MA-window grid; `ma40w` (weekly rebalance) and `tsmom12m` (a different rule) are not, so their PBO
is **borrowed and marked as such** in the script output. An unmarked family statistic printed on a
non-member row reads as that row's own. No verdict turns on it — both fail on Sharpe first.

### Stability — the advantage is pre-1977 and then reverses

| split | n | `bh` SR | `ma200d` SR | `tsmom` SR | `ma200d − tsmom` | t |
| --- | --- | --- | --- | --- | --- | --- |
| first half | 12,387 | 0.277 | 0.520 | 0.350 | **+2.34%** | 1.85 |
| second half | 12,387 | 0.591 | 0.676 | 0.623 | **−0.49%** | −0.43 |
| post-1950 | 19,279 | 0.588 | 0.746 | 0.632 | +0.34% | 0.43 |
| post-2000 | 6,697 | 0.420 | 0.463 | 0.534 | **−1.66%** | −1.09 |

The whole (already-insignificant) advantage over slow TSMOM lives in the **first half** and turns
**negative** in both modern cuts. Post-2000, plain TSMOM is the *better* arm. Any live-deployment
reading of the pooled +0.93% is reading a pre-1977 tape.

### Where the timing behaviour comes from

Summed daily excess of `ma200d` over buy-and-hold, by decade — an attribution diagnostic, **not a
tradeable claim** (it inherits the frame limits below):

```text
1920s  −4.8%   1930s +34.7%   1940s  +6.5%   1950s −10.5%   1960s +11.2%   1970s  +8.2%
1980s −10.4%   1990s −34.8%   2000s +13.2%   2010s −56.7%   2020s −33.7%
```

Cumulatively ≈ **−77pp**, of which the **2010s alone are −56.7pp (74%)**. The pattern is the
familiar one: the filter pays for itself in 1929–1932 and in the 2000s, and gives the money back
across long uninterrupted bull runs where being flat a third of the time is expensive. That it
still wins on *Sharpe* is entirely the volatility denominator, not the numerator.

## Frame limits — fixed before the run, not negotiable after it

1. **`^GSPC` is price-only.** Buy-and-hold is always invested and so forgoes the most dividend;
   the omission **favours** every timing arm against `bh`.
2. **Flat earns 0%, not T-bills.** Over 98 years — including the 1980s — that **disfavours**
   timing arms against `bh`.
3. Single index, so no survivorship, delisting or PIT-membership exposure. `k=1`, no deflator.
   ⚠ **Any future leg that pools names inherits the 13× `n_eff` correction** (`n_eff` 2.96 at `1d`).

(1) and (2) partially offset and **neither is small**; their net sign is not knowable from this
data, so **no `bh` comparison in this audit is a tradeable claim.** This is precisely why the
`bh` column is *not* the primary statistic. The paired MA-vs-TSMOM test is immune to first order,
because both arms are long-or-flat on the same index with time-in-market within 1.7pp of each
other (67.4% vs 69.1%) — the distortions land on both sides and cancel.

## What this closes and what it does not

- **Closes:** H-1 as a *sleeve* candidate. Two independent gate legs fail and the sensitivity
  profile shows the window is arbitrary. ⚠ **It does not become a ninth shelved sleeve** — H-1 is
  a hypothesis-inbox row that was measured and closed, never a built sleeve module, and the count
  of eight in CLAUDE.md's verdict table is unchanged.
- **Closes:** the "is it just slow TSMOM?" question, in the affirmative-but-not-identical
  direction — 84.4% same-state, and no demonstrable difference in what the disagreement buys.
- **Closes:** the weekly-rebalance arm's advantage over slow TSMOM, as a **powered null** — though
  by a 0.09pp margin, so it is the *verdict* that is established, not a wide safety margin.
- **Does NOT close:** whether a ~1–2.6pp/yr advantage exists for the **daily** arm. The design is
  blind there, and 98 years of daily `^GSPC` is the deepest single-index panel available, so
  **more of this data cannot settle it** — only a different unit (more markets, hence the 13×
  deflator) could, and that trade is unattractive given the sleeve verdict above.
- **Does NOT license** a regime-overlay claim. H5 asks a different question (overlay on a paper
  book, post-G1) and nothing here measures it.
