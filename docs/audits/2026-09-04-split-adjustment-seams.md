# Split-adjustment seams — the refresh that creates the defect

**Date:** 2026-09-04
**Verdict:** FOUND and FIXED — four contaminated names, a guard at the ingest seam, and one
wrong-instrument tape that no refetch can repair.
**Audit:** this file.

## How it surfaced

`make freshness-check` reported one finding: `AVB 1d`, 9 sessions behind, 1 of 1109 scheduled
series. Chasing a single stale symbol was expected to be a benign provider hole. It was not.

`AVB`'s stored tape ran at **$68** while `fast_info` reported the live price at **$184.06**, with
`marketCap / shares` consistent with the latter. The staleness was a symptom; the series was
carrying a different instrument.

## Finding 1 — `sync` appends the tail, so a split leaves a permanent fake return

`data_sync.sync` fetches from each series' own newest bar forward. The provider restates **every
historical bar** onto the post-split basis, but the bars already stored keep the old one. Nothing
in the pipeline ever re-reads history, so the seam is created *by* the refresh and survives every
later one.

Scanning the whole DB for single-bar moves above 35% since 2025-01-01 returned 23 rows, which
separate cleanly at ~45%:

| symbol | corporate action | stored artifact |
| --- | --- | --- |
| CRWD | 4:1 split 2026-07-02 | **−74.9%** on 2026-06-18 |
| DD | 1:3 reverse 2026-06-24 | **+198.5%** on 2026-06-18 |
| MNST | 2:1 split 2026-08-11 | **±50% / +95%, seven times** Jul 20 – Aug 11 |
| AVB | (see finding 3) | **−61.1%** on 2026-07-17 |

MNST is the worst because its split landed *inside* the active sync window, so overlapping
fetches wrote bars on both bases and the series alternates rather than stepping once.

Everything below ~45% is a real event — FISV −44%, CNC −40%, TTD −38.6%, SNPS −35.8%, WST −38.2%,
ALGN −36.6% — and none was touched.

⚠ **The count grows on its own.** A series carries a seam exactly when a split lands *after* its
last full-history fetch — an earlier split comes back inside one internally-consistent call and
leaves nothing behind. So the population is not fixed: left alone, the weekly
`wifey-universe-sync` adds a new seam for every future split in the 505-member universe.

⚠ **The date of any series' last full fetch is NOT recoverable** — `ohlcv` stores no ingest
timestamp, only `open_time`. That every seam found here falls in 2026-06 → 2026-08 is consistent
with a mid-2026 universe backfill but does not establish one, and nothing in this audit rests on
it.

## Finding 2 — the fix, and why 1% is safe

The overlap bar is the whole mechanism. `sync` already re-fetches `latest` (not `latest + 1`) so a
candle stored mid-formation gets its final values — which means a restatement is observable at
**exactly one point**, for free, on the fetch the sync was going to make anyway.
`ADJUSTMENT_BASIS_TOL = 0.01`: when that bar's close moves more than 1%, the whole series is
re-synced from its earliest stored bar.

⚠ **The threshold is only safe because `utils/yfinance_client` fetches with `auto_adjust=False`.**
Yahoo applies splits to the raw OHLC series retroactively but leaves dividends out of it, so a
stored close is stable across syncs except when a split lands. Under `auto_adjust=True` every
ex-dividend date would shift history slightly and trip the guard on names that did nothing. The
nearest canonical split factor to parity is 0.5 — 50× the tolerance away — so the two populations
are nowhere near meeting.

Verified end-to-end on real provider data: a 4× stale basis planted across CRWD's 1,818 real bars
was detected at the overlap bar (859.88 → 214.97, ×0.2500), re-synced, and left at a **maximum
deviation from truth of 0.000000**.

⚠ **The guard prevents a NEW seam and cannot repair a stored one** — by the time a seam is
noticed the overlap bar has long since settled onto the new basis, so there is nothing left to
observe. Repair is a full re-backfill, which is what closed CRWD, DD and MNST here.

## Finding 3 — `AVB` is a wrong instrument, and refetch is not a repair

Between 2026-07-17 and 2026-08-24 the provider's history endpoint served a $63–71 tape under
`AVB`, then stopped updating it. `yf.Ticker("AVB").history(start="2018-01-01")` returns **27
rows** — the bogus window only — so a re-backfill would overwrite the 27 bad bars with the same 27
bad bars and reach none of the 2,127 good ones. `migrations/007` deletes them instead: 27 `1d` +
7 `1wk` = **34 rows**, `AVB` 2603 → 2569, a second run reporting 0.

The two selection predicates agree exactly and are both required: the good bars' minimum **low**
is 118.17 against the bogus band's maximum **high** of 70.61 (a 1.67× gap with nothing inside it),
and no `AVB` row of any timeframe sits between the last good bar and the first bogus one.

⚠ **Not a delisting, so `config/universe.json` is untouched.** `EA`/`EQR`/`SATS` were flagged
in #281 because they had stopped trading; AVB is alive and quoted at $184. What it becomes after the
purge is a **stale** series, which `freshness-check` reports and which repairs itself the moment
the provider fixes the ticker. The finding count went 1 → 2 and that is the improvement: a stale
series is visible and correct by omission, a corrupt one is invisible and wrong.

## Finding 4 — a big move is not evidence of this defect

`MRNA` **+177.0%** on 2026-08-19 survived a full re-backfill, so it is what the provider serves
rather than a stale basis. The bar opens at 116.02 having closed at 62.96, trades 114.46–176.66 on
**199,252,300 shares against a normal 4–8M**, and the level sustains for days (133, 145, 139, 159)
into a live 148.87. No split is recorded. It is a real re-rating, correctly left alone, and its
`return outlier` warning is warn-only by design.

**Check whether the provider still serves the jump before treating one as a seam.** A seam
disappears on re-backfill; an event does not. That test costs one call and is the only thing
separating the two classes from the outside.

## What did not move

No sleeve verdict and no gate. `confidence_ratings` carries no symbol axis, the goldens read the
committed fixture parquets rather than the DB, and none of the five names appears in the 13-symbol
live watchlist — so `make test-regression` could not move and did not. The exposure was to any
pooled cross-section reading the research universe, which is exactly where these outliers would
have dominated.
