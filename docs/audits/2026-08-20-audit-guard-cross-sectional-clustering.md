# `audit_guard`'s bootstrap absorbs serial dependence, not cross-sectional clustering

**Date:** 2026-08-20
**Verdict:** **FOUND** — the CI is too narrow by **~1.9×** (median) on single-strategy cells, and
the `[bias.regime]` flip verdict's **sole blocking cell does not survive the correction**. The
filed `EXCLUDED` is not supported by its own evidence once a trading day rather than a trade is the
resampling unit.
**Subject:** `analytics/audit_guard.py::evaluate_audit_cells`, its two consumers
(`analytics/warning_audit.py`, `tools/regime_gate_replay.py`)
**Status:** measurement only — **no code changed, no config changed.** The fix and the moved
verdict are both decisions, listed at the end.

## The question

`evaluate_audit_cells` puts a circular block bootstrap on a **pooled** slice of per-trade R. The
module docstring claims one thing for it — *"Serial-correlation aware — an iid CI understates the
width on autocorrelated trade streams"* — and that claim is true. What was never tested is the
other axis: a cell pools **13 correlated symbols**, so a day on which one strategy fires on eight
of them is closer to one bet than to eight. If the bootstrap does not absorb that, its CIs are too
narrow, and a too-narrow CI is most dangerous on `powered_null`, where it **over-licenses "ruled
out"**.

The repo already does this correction by hand elsewhere: the exits A/B took *"31 ET session days
rather than 267 alerts"* as the unit. That is the temporal analogue of the same question.

## Answer: it does not, and it cannot

A block bootstrap absorbs dependence between observations **adjacent in the array it is handed**.
Neither consumer hands it an array whose adjacency means anything:

- `tools/regime_gate_replay.py::_load_trades` has **no `ORDER BY`** — row order is DuckDB's
  storage order. (The `ORDER BY open_time` at line 119 is the *OHLCV* query, not this one.)
- `analytics/warning_audit.py::tag_trades` builds `keep_idx` by looping over `(symbol, tf)` pairs,
  so the array is **symbol-blocked**: every same-day cross-symbol pair is maximally far apart, and
  a block of `round(n**(1/3))` spans one symbol's consecutive trades only.

So same-day cross-symbol clustering is not merely under-absorbed; it is structurally unreachable.

## Measurement

### Route A and Route B agree

64 cells cut as `strategy × timeframe × direction` (`n ≥ 30`, 27,026 trades pooled), against the
shipped defaults (circular, `n_boot=2000`, `alpha=0.05`, `seed=12345`):

| quantity | median |
| --- | --- |
| trades per calendar day | 4.74 |
| ICC of `pnl_r` by day | **0.598** |
| design effect `1 + (m̄−1)·ICC` | **3.349** |
| **Route A** — analytic `√DEFF` | **1.830** |
| **Route B** — measured day-clustered CI width ÷ shipped CI width | **1.919** |

Route B spread: p25 1.585, p75 2.359, max 3.150. The two routes are independent — one is an
analytic design effect, the other resamples whole days — and they agree: correlation **0.800**
across cells, median per-cell gap **0.196**.

### The second channel is larger than the first

The CI is only one of the two legs a verdict needs. The other is `_two_sided_p`, which forms
`t = sr·√n` on the **trade count, undeflated**. Deflating to `n/DEFF`:

| | significant at 0.05 (raw, pre-Holm) |
| --- | --- |
| shipped | **30 of 64** cells |
| deflated | **12 of 64** cells |

18 cells lose raw significance, several by an order of magnitude — `pin_bar/4h/short` 0.00017 →
0.112, `trend_day/4h/long` 0.00082 → 0.153, `ema/4h/short` 0.0055 → 0.288.

⚠ **This is the same defect class closed last session in `tools/distil_power.py`** — a deflator
that is accepted but not applied. Here it is not even accepted: `AuditCell` carries `supp_r` and
`kept_r` as bare lists, so there is no channel through which a caller *could* declare the unit.

### The size of the error depends on how the cell is cut

| consumer | cell shape | median width ratio | verdict moves? |
| --- | --- | --- | --- |
| `warning_audit` | warning × direction, **pooled over all strategies and timeframes** | **1.208** | **no** |
| `regime_gate_replay` | strategy × regime, **one strategy** | **1.840** (suppressed) / 1.792 (kept) | **yes** |

Pooling across strategies decorrelates the same-day trades, so the warning audit — the consumer
whose `COSMETIC` verdict literally means "ruled out" — is the one the defect barely touches. Its
12 cells hold **0 powered nulls before and 0 after**; nothing it filed moves. Its single `ENABLE`
(`w5_wick_rejection/long`) survives: CI `[−0.482, −0.139]` → `[−0.494, −0.131]`, still clear of the
−0.05 bar.

⚠ **The original worry was aimed at the right class but the wrong instance.** `powered_null` holds
on **0 of 64** cells anywhere in the repo today, so nothing "ruled out" is currently over-licensed.
The live damage is on the `ENABLE`/`DISABLE` branch instead.

## The filed verdict that moves

`docs/audits/2026-08-19-regime-gate-pooled-banner.md` filed **EXCLUDED** on one sentence:

> One suppressed cell is a reliable winner at Holm-adjusted p=0.001, and `mode` is a single global
> switch, so no per-cell gain can be taken without also taking that loss.

That cell is **`ema/high_vol`**, and it is the only thing standing between `DO NOT FLIP` and
`FLIP justified` — `DECISION_DISABLE` is in `_BLOCKING_DECISIONS`, and one blocking cell vetoes.

| cell | n | days | shipped CI | day-clustered CI | CR1 cluster-robust CI | shipped → corrected |
| --- | --- | --- | --- | --- | --- | --- |
| `ema/high_vol` | 172 | **25** | `[+0.085, +1.023]` | `[−0.287, +1.440]` | `[−0.348, +1.447]` | **DISABLE → INSUFFICIENT** |
| `bos/high_vol` | 655 | 114 | `[−0.514, −0.245]` | `[−0.632, −0.096]` | `[−0.658, −0.112]` | ENABLE → ENABLE |
| `bos/trend` | 897 | 161 | `[−0.462, −0.246]` | `[−0.533, −0.164]` | `[−0.536, −0.172]` | ENABLE → ENABLE |

The blocking cell collapses; the two enabling cells hold. Running the repo's **own**
`flip_verdict` on the re-priced CIs: **`DO NOT FLIP` → `FLIP justified`**, stable across 5 seeds,
and under a **Bonferroni** stand-in for Holm — i.e. a *more* conservative significance leg than the
one shipped, so the surviving `ENABLE`s are not an artifact of a looser test. `mapping_verdict` is
unchanged at `MAPPING UNTESTED`.

`ema/high_vol`'s shipped lower bound was `+0.0850` against a `+0.05` bar — a hair above — on 172
trades spread over 25 days, **one of which contributed 20 trades** and the top three 29% of the
cell. Its cluster-robust SE is **3.01× the iid SE**.

## Why the third route was needed

25 clusters is few, and a cluster bootstrap is known to misbehave at small `k` — so the pivotal
cell was checked a third way, analytically. **CR1** (`√(k/(k−1) · Σ_g (Σ_i e_i)²) / n`) uses no
resampling at all and reproduces the collapse. It is quoted with a **normal** critical value; a
`t` on `k−1` df would be wider still, so that check runs **against** the finding and the finding
survives anyway.

## Bounds on the claim

- **The correction is a lower bound.** Clusters are keyed on **entry** day, so a `1d` or `1wk`
  trade held for weeks overlaps days it is not clustered with. The true design effect is larger.
- **UTC date is safe as the key here.** RTH `4h` bars stamp 13:30/17:30 UTC and `1d` bars
  04:00/05:00 UTC, so a UTC date and an ET session date name the same session and the winter/summer
  ET shift (CLAUDE.md § Footguns) cannot move a trade across a cluster boundary.
- ⚠ **`FLIP justified` is not a recommendation to flip.** Widening every CI **removes** evidence;
  it adds none. The honest reading is that the veto was manufactured by a too-narrow interval, so
  the *reason filed for EXCLUDED* is unsupported — not that flipping is now positively indicated.
- Nothing here touches a sleeve verdict. Those gates run on a portfolio-level series (k=1), where
  there is no cross-section to cluster.

## Decisions this leaves open

1. **The fix.** `AuditCell` has no channel for a cluster key, so `audit_guard` cannot resample days
   even if told to. The shape matching this repo's precedent (`adr_gate_applies(timeframe)`,
   `effective_independent_series`) is a **required** per-observation cluster key with the verdict
   **failing closed** to `INSUFFICIENT` when a caller cannot supply one — an unmeasurable panel and
   an uncorrelated one must not both read as a deflator of 1.0.
2. **The moved verdict.** Whether the corrected evidence changes `[bias.regime].mode` is a config
   decision, not a correctness one, and is left to the operator.
3. **`tools/warning_value_audit.py` needs no change on the evidence here** — but that is a fact
   about its cell shape, not a property of the tool, and it would not survive re-cutting its cells
   per strategy.

## Reproduction

Six one-shots in `docs/plans/scripts/` (gitignored, backed up by `make backup`), each run as
`PYTHONPATH=. poetry run python docs/plans/scripts/<name>`. Every figure above traces to one:

| script | produces |
| --- | --- |
| `audit_guard_clustering_deff.py` | the 64-cell table: ICC, DEFF, shipped vs day-clustered CI width |
| `audit_guard_clustering_holm_channel.py` | Route A vs Route B agreement, and the 30→12 Holm channel |
| `audit_guard_clustering_warning_cells.py` | the real `warning_audit` cells, re-priced |
| `audit_guard_clustering_regime_cells.py` | the real `regime_gate_replay` cells, both families |
| `audit_guard_clustering_flip_verdict.py` | `flip_verdict` / `mapping_verdict` on re-priced CIs, 5 seeds |
| `audit_guard_clustering_cr1_check.py` | the CR1 cluster-robust check on the three pivotal cells |

They import `analytics.audit_guard`, `analytics.research_guards.block_bootstrap_ci` and the two
consumers' own cell builders, so the cells are the tools' cells rather than a reimplementation.
**No repo code was modified to produce any figure above** — the day-clustered bootstrap and the CR1
SE are computed alongside the shipped CI, never in place of it.
