---
name: journal-trade
description: >
  Capture a manual trade the user took into the gitignored trade journal at
  `docs/plans/journal/` — structured YAML frontmatter (machine-parseable) plus a
  narrative body (Thesis / Plan / Execution / Outcome / Retrospective). These
  entries are ground-truth training data for a future AI trade-card, validation
  for the live outcome loop (`signal_alert_outcomes`), and a source of
  trade-management heuristics.
  Invoke when the user says "/journal-trade", "journal my trade", "log this
  trade", "add a trade to the journal", or pastes raw trade-execution details
  (entry/sl/tp + reasoning). Also use to fill in the outcome after a logged
  trade closes.
allowed-tools: Bash, Write, Read, Edit
---

# Journal Trade

Turn the user's manual trade into a structured journal entry. The journal is the ground-truth corpus
for the eventual trade-planning system — every entry is a closed-loop prediction (plan) + result
(outcome) + lesson (retrospective). The procedure memory lives in the PARENT's tree
(`~/.claude-personal/projects/-home-kng-repo-buibui-moon-trader-bot/memory/project_trade_journal_procedure.md`);
wifey has no local copy.

## When to use

- User says `/journal-trade`, "journal my trade", "log this trade", or pastes trade-execution details.
- A previously-logged trade closed and the user reports the exit → fill in the post-close fields.
- Several entries have accumulated and the user wants them mined for patterns.

## Where it lives

- Directory: `docs/plans/journal/` — **gitignored** (under `docs/plans/`; personal financial data, never
  committed). ⚠ **It does not exist yet on this box** (0 entries ever, measured 2026-08-19) — run
  `mkdir -p docs/plans/journal` on first use.
- One markdown file per trade. Filename: `YYYY-MM-DD-<symbol>-<direction>.md` (date = entry date, **UTC**).
- `docs/plans/journal/TEMPLATE.md` holds the canonical template. On first use, seed it from the parent's copy
  (`~/repo/buibui-moon-trader-bot/docs/plans/journal/TEMPLATE.md` — gitignored on both sides, so no clone
  carries it).

## Procedure (new entry)

1. **Parse** the user's raw details: symbol, direction, intent (scalp/swing/position), entry price + reason,
   SL (type + level + basis), TP (planned + eventual + reason), planned RR, hold expectation, outcome if known.
2. **Convert timestamps**: the user gives **MYT (UTC+8)**. Record *both* MYT (as given) and **UTC = MYT − 8h**
   in the frontmatter. UTC is mandatory — the bot reasons in UTC (`open_time`, `day_filter`), so UTC makes
   the entry cross-referenceable with `backtest_trades` / signal history.
3. **Tag**: fill `thesis_tags` (e.g. overnight_gap, amd, fvg, wick_fill, inverse_hns, absorption) — these drive
   later tag-win-rate mining. Fill `strategies_seen` with any bot strategies that fired near the setup
   (cross-check against `analytics/strategies/` names) — detected-vs-missed is a key signal.
4. **Write** the file using the template: structured YAML frontmatter + narrative sections
   **## Thesis / ## Plan / ## Execution / ## Outcome / ## Retrospective**.
5. **Post-close fields stay blank** until the trade closes (`exit_*`, `r_realized`, `outcome`, `self_rating`).
   If the trade is already closed, fill them.
6. **Retrospective must always ask**: "what signal / gate / confluence / setup-template should the *bot*
   learn from this?" — that line is the bridge from journal to system improvement.

## R / SL scoring convention

Score `r_realized` against the **stop that was actually working in the market** — i.e. the **soft** stop if
one was set (the real invalidation), not the wide hard catastrophe stop. When the basis is ambiguous, note
both in the Outcome section (e.g. "+3R on soft stop / +0.65R on hard 2%") and confirm with the user. Keep
the convention consistent across all entries. (Default chosen 2026-05-25 — soft-stop basis; revise here if
the user picks otherwise.)

## Filling in an outcome (trade closed)

Read the existing entry, then Edit: set `exit_ts_myt` / `exit_ts_utc` / `exit_price` / `r_realized` /
`outcome` (win|loss|breakeven|scratch) / `self_rating` (1-5, execution quality independent of P/L), and
complete the **## Outcome** and **## Retrospective** sections.

## Mining (when entries accumulate)

Offer to read all `docs/plans/journal/*.md` and report: win-rate by `thesis_tag` and setup template,
which setups the bot already detects (`strategies_seen`) vs misses, recurring trade-management heuristics
worth promoting to live trade-management, and any detector gaps worth a `/investigate-strategy` probe.
Feeds a future AI trade-card, the live outcome loop (`signal_alert_outcomes`), and trade-management heuristics.
