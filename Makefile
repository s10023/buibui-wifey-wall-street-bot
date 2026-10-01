# Exported to EVERY recipe, not just `test`. Windows defaults a redirected
# stdout and every implicit text read to the ANSI codepage (cp1252 here), and
# this tree's source, docs, configs and fixtures are full of em-dashes and ⚠.
#
# ⚠ **Measured, not reasoned**: the suite is `0 failed` with this set and
# `41 failed` without it — 40 UnicodeDecodeError, 38 UnicodeEncodeError, all
# `'charmap' codec`, concentrated in `test_video_fetch` (17) and
# `test_systemd_units` (10). Nothing in the repo set it before, so a Windows
# session's `make test` was red for reasons that had nothing to do with its
# diff, and a session that exported the variable by hand got a green that its
# own shell was producing.
#
# ⚠ **This covers `make`, and `make` only.** A bare `poetry run pytest`, or any
# tool run directly, still starts in cp1252 — there are 56 `read_text()` sites
# with no explicit `encoding=`, which is the deeper fix and is NOT done here.
# Linux CI is unaffected either way: UTF-8 is already its default.
export PYTHONUTF8 := 1

SORT ?= default
SYMBOL ?= SPY
STRATEGY ?= fvg
INTERVAL ?= 4h
DAYS ?= 90
SAVE ?=
PORT ?= 8000
DEV_PORT ?= 5173
# Makefile — Lint Markdown and Python

PYTHON_FILES = $(shell find . -name "*.py" -not -path "./venv/*" -not -path "./.venv/*")
DOCKER_IMAGE = wifey-bot
# MEMORY.md's path is NOT a variable here. It was `$(HOME)/.claude-personal/
# projects/-home-kng-repo-…`, i.e. the old Linux box's home spelled out in a
# tracked file, which resolved to nothing after the Windows migration and made
# `make status` print `?` for a file that was present all along. It now comes
# from `tools/claude_home.py` via `post_branch_checks`, the one derivation.

.PHONY: status wait-ci wait-ci-main lint lint-md lint-md-fix docs-index docs-index-check lint-py-check lint-py typecheck test test-cov test-regression regression-update poetry-install poetry-update docker-build docker-analytics-backfill docker-analytics-sync docker-backtest docker-signal-watch wifey-open-trades wifey-analytics-backfill wifey-analytics-sync wifey-pundit-sync wifey-pundit-backfill wifey-universe-backfill wifey-universe-sync universe-coverage universe-stamp-listed wifey-forecast-audit wifey-xsmom-audit wifey-xsmom-residual-audit wifey-lowvol-audit wifey-xasset-audit wifey-xasset-backfill wifey-pead-audit wifey-pead-backfill wifey-insider-backfill wifey-insider-cohort wifey-insider-audit wifey-velocity-audit wifey-exit-audit wifey-exit-replay wifey-warning-value-audit wifey-n-eff wifey-pundit-score wifey-check-levels wifey-route-dedup-seed wifey-backtest wifey-combo-backtest wifey-cross-tf-backtest wifey-signal-watch go-live go-live-prep backup backup-check freshness-check session-digest backup-dry-run backup-offsite backup-offsite-dry-run wifey-param-audit wifey-param-sweep wifey-recalibrate wifey-sync-parent check-dead-surfaces check-orphan-tests post-branch-checks post-branch-text sanity-checks preflight cadence-check cadence-stamp wifey-digest wifey-web web-install web-dev web-build web-preview web-full clean-db clean

lint: lint-md lint-py

lint-md:
	@echo "🔍 Running markdownlint on all Markdown files..."
	npx markdownlint-cli2

docs-index:
	@echo "📚 Regenerating the audit + spec indexes..."
	poetry run python tools/docs_index.py

docs-index-check:
	@echo "📚 Checking the audit + spec indexes are current..."
	poetry run python tools/docs_index.py --check

lint-md-fix:
	@echo "🔍 Running markdownlint on all Markdown files..."
	npx markdownlint-cli2 --fix

lint-py-check:
	@echo "🧹 Checking Python formatting and linting with ruff..."
	poetry run ruff check .
	poetry run ruff format --check .

lint-py:
	@echo "🎨 Formatting and linting Python code with ruff..."
	poetry run ruff check --fix .
	poetry run ruff format .

typecheck:
	@echo "🔎 Type checking with mypy..."
	poetry run mypy .

test:
	@echo "🧪 Running tests..."
	poetry run pytest tests/ -q --durations=10 --ignore=tests/test_regression.py

# Coverage on demand. It is not in `make test` because nothing gates on it —
# the tracer cost was being paid on every local run and every CI run to produce
# a report no one read. Run this when you actually want to read it.
test-cov:
	@echo "🧪 Running tests with coverage..."
	poetry run pytest tests/ --cov --cov-report=term-missing --ignore=tests/test_regression.py

test-regression:
	@echo "🔍 Running regression tests..."
	poetry run pytest tests/test_regression.py -v --timeout=300

regression-update:
	@echo "🔄 Regenerating golden files..."
	poetry run python scripts/extract_regression_fixture.py
	poetry run pytest tests/test_regression.py --update-golden -v --timeout=300
	@echo ""
	@echo "Review: git diff tests/fixtures/golden_*.json"
	@echo "Commit golden updates alongside your TOML/code changes."

poetry-install:
	@echo "📦 Installing dependencies with Poetry..."
	poetry install --no-root

poetry-update:
	@echo "🔄 Updating dependencies with Poetry..."
	poetry update

docker-build:
	@echo "🐳 Building Docker image..."
	docker build -t $(DOCKER_IMAGE) .

docker-analytics-backfill:
	@echo "📥 Running analytics backfill in Docker..."
	@touch analytics.db
	docker run --rm --env-file .env \
		-v $(PWD)/analytics.db:/app/analytics.db \
		-v $(PWD)/config/stocks.json:/app/config/stocks.json:ro \
		$(DOCKER_IMAGE) poetry run python wifey.py analytics backfill --since $(or $(SINCE),2023-01-01) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),)

docker-analytics-sync:
	@echo "🔄 Syncing analytics data in Docker..."
	@touch analytics.db
	docker run --rm --env-file .env \
		-v $(PWD)/analytics.db:/app/analytics.db \
		-v $(PWD)/config/stocks.json:/app/config/stocks.json:ro \
		$(DOCKER_IMAGE) poetry run python wifey.py analytics sync \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),)

docker-backtest:
	@echo "📊 Running backtest in Docker..."
	@touch analytics.db
	docker run --rm --env-file .env \
		-v $(PWD)/analytics.db:/app/analytics.db \
		-v $(PWD)/config/stocks.json:/app/config/stocks.json:ro \
		$(DOCKER_IMAGE) poetry run python wifey.py backtest \
		--symbol $(SYMBOL) \
		--strategy $(STRATEGY) \
		--interval $(INTERVAL) \
		--days $(DAYS) \
		$(if $(SL_PCT),--sl-pct $(SL_PCT),) \
		$(if $(TP_R),--tp-r $(TP_R),) \
		$(if $(SAVE),--save,)

wifey-analytics-backfill:
	@echo "📥 Running analytics backfill..."
	@poetry run python wifey.py analytics backfill --since $(or $(SINCE),2023-01-01) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),)

wifey-analytics-sync:
	@echo "🔄 Syncing analytics data..."
	@poetry run python wifey.py analytics sync \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),)

# The pundit ledger is a THIRD universe and nothing else refreshes it. `go-live` syncs
# config/stocks.json (13 ETF/equity proxies); the ledger records the index and futures
# UNDERLYINGS a pundit actually quoted (^GSPC, GC=F) — two correct-but-separate symbol
# conventions. Without this target those bars go stale silently, `pundit_score` degrades
# to STALE rather than erroring, and a permanently-unresolving ledger reads as "nothing
# has triggered yet". 1d only: these are horizon-scored calls, not intraday signals.
wifey-pundit-sync:
	@echo "🔄 Syncing pundit-ledger symbols (docs/plans/pundit-calls.jsonl)..."
	@poetry run python wifey.py analytics sync --pundit \
		--timeframes $(or $(TIMEFRAMES),1d)

# First-time history for a ledger symbol that has no bars at all. `sync` refuses an
# empty symbol ("run backfill first"), so a newly-quoted underlying needs this once.
wifey-pundit-backfill:
	@echo "📥 Backfilling pundit-ledger symbols (docs/plans/pundit-calls.jsonl)..."
	@poetry run python wifey.py analytics backfill --pundit \
		--since $(or $(SINCE),2023-01-01) \
		--timeframes $(or $(TIMEFRAMES),1d)

wifey-universe-backfill:
	@echo "📥 Backfilling the research breadth universe (config/universe.json)..."
	@poetry run python wifey.py analytics backfill --universe \
		--timeframes 4h 1d 1wk --since $(or $(SINCE),2018-01-01)

# INCREMENTAL refresh of the same universe, and the target
# `wifey-universe-sync.timer` drives -- so the scheduled run is literally the
# documented hand-run, the property `go-live` already relies on.
#
# ⚠ NOT a thinner `wifey-universe-backfill`. The backfill re-fetches from
# --since (2018) for 505 symbols x 3 timeframes; this asks each series for the
# tail after its own newest bar. Measured 2026-08-26 on a 10-week-stale tree:
# 2m15s wall clock for the whole universe. A weekly BACKFILL would be minutes of
# redundant network for bars already stored.
#
# ⚠ `sync` REFUSES a symbol with no bars at all ("run backfill first") and skips
# it rather than failing the run. That is why this cannot be the only universe
# target: a newly-added constituent needs `wifey-universe-backfill` once. It is
# also why 400 of 505 `4h` series stay absent here -- yfinance's intraday history
# window, not a staleness problem, and the reason this target cannot close the
# 21% `4h` coverage gap. See memory `reference_ohlcv_timeframe_coverage.md`.
wifey-universe-sync:
	@echo "🔄 Syncing the research breadth universe (config/universe.json)..."
	@poetry run python wifey.py analytics sync --universe \
		--timeframes $(or $(TIMEFRAMES),4h 1d 1wk)

universe-coverage:
	@PYTHONPATH=. poetry run python tools/universe_coverage.py \
		$(if $(DB),--db $(DB),)

# Reconcile config/universe.json's `listed` seam against DB first-1d-bar truth.
# Report-only unless WRITE=1. Re-run after wifey-universe-backfill or after
# tools/expand_universe_sp500.py adds constituents (they arrive unstamped).
universe-stamp-listed:
	@PYTHONPATH=. poetry run python tools/stamp_universe_listed.py \
		$(if $(DB),--db $(DB),) $(if $(WRITE),--write,)

wifey-forecast-audit:
	@echo "📈 G2 audit — EWMAC trend sleeve over the breadth universe (1d)..."
	@PYTHONPATH=. poetry run python tools/forecast_audit.py $(ARGS)

wifey-xsmom-audit:
	@echo "📊 G3 audit — XS-momentum sleeve over the breadth universe (1d)..."
	@PYTHONPATH=. poetry run python tools/xsmom_audit.py $(ARGS)

wifey-xsmom-residual-audit:
	@echo "🔬 Experiment #1 — residualized XS-momentum 2x2 audit..."
	@PYTHONPATH=. poetry run python tools/xsmom_residual_audit.py $(ARGS)

wifey-lowvol-audit:
	@echo "🔬 Edge-hunt #2 — low-beta/BAB 2x2 audit over the breadth universe (1d)..."
	@PYTHONPATH=. poetry run python tools/lowvol_audit.py $(ARGS)

wifey-xasset-audit:
	@echo "🔬 Edge-hunt #3 — cross-asset TSMOM 2x2 audit over the frozen ETF basket (1d)..."
	@PYTHONPATH=. poetry run python tools/xasset_audit.py $(ARGS)

wifey-xasset-backfill:
	@echo "📥 Backfilling the cross-asset TSMOM ETF basket (1d) from $(or $(SINCE),2007-03-01)..."
	@poetry run python wifey.py analytics backfill \
		--symbols SPY QQQ EFA EEM TLT IEF LQD GLD SLV DBC USO DBA UUP \
		--timeframes 1d --since $(or $(SINCE),2007-03-01)

wifey-pead-audit:
	@echo "🔬 Edge-hunt #4 — PEAD-lite 2x2 audit over the breadth universe (1d)..."
	@PYTHONPATH=. poetry run python tools/pead_audit.py $(ARGS)

wifey-gapfill-audit:
	@echo "🔬 Edge-hunt #5 — gap-fill magnet audit over the breadth universe (1d)..."
	@PYTHONPATH=. poetry run python tools/gapfill_audit.py $(ARGS)

wifey-velocity-audit:
	@echo "🔬 Edge-hunt #6 — velocity-alternation audit over the breadth universe (1d)..."
	@PYTHONPATH=. poetry run python tools/velocity_audit.py $(ARGS)

wifey-pead-backfill:
	@echo "📥 Edge-hunt #4 — ingesting EDGAR earnings facts (one-shot)..."
	@PYTHONPATH=. poetry run python tools/pead_backfill.py $(ARGS)

wifey-insider-backfill:
	@echo "📥 H-024 — ingesting EDGAR Form 4 insider transactions (one-shot)..."
	@PYTHONPATH=. poetry run python tools/insider_backfill.py $(ARGS)

wifey-insider-cohort:
	@echo "🔎 H-024 phase 2 — routine vs opportunistic cohort shape (no returns)..."
	@PYTHONPATH=. poetry run python tools/insider_cohort.py $(ARGS)

wifey-insider-audit:
	@echo "🧪 H-024 phase 3 — insider trial family, gross vs net (read-only)..."
	@PYTHONPATH=. poetry run python tools/insider_audit.py $(ARGS)

wifey-exit-audit:
	@echo "🚪 Exit MFE/MAE diagnostic over the live alert ledger (spec §2)..."
	@PYTHONPATH=. poetry run python tools/exit_audit.py $(ARGS)

wifey-exit-replay:  ## A/B candidate exit policies over the live ledger (read-only)
	@echo "🚪 Exit-policy replay A/B (spec §3-§5)..."
	@PYTHONPATH=. poetry run python tools/exit_audit.py --replay $(ARGS)

wifey-warning-value-audit:  ## do the W1-W8 alert warnings predict avg_r? (read-only)
	@echo "🔎 Warning-value audit — re-deriving W1-W8 flags from OHLCV..."
	@PYTHONPATH=. poetry run python tools/warning_value_audit.py $(ARGS)

wifey-n-eff:  ## measure effective independent series (n_eff) for a pooled panel -> distil_power flags
	@echo "🔗 Measuring the correlation deflator (n_eff) over the panel..."
	@PYTHONPATH=. poetry run python tools/n_eff.py $(ARGS)

wifey-pundit-score:  ## score the pundit-call ledger vs OHLCV -> priors (sync the ledger's symbols first)
	@echo "🎯 Scoring the pundit-call ledger against stored OHLCV..."
	@PYTHONPATH=. poetry run python tools/pundit_score.py $(ARGS)

wifey-check-levels:  ## sign-check the pundit ledger's level ordering (advisory; never edits)
	@echo "📏 Sign-checking Stream C level ordering..."
	@PYTHONPATH=. poetry run python tools/x_route.py --check-levels $(if $(LEDGER),$(LEDGER),docs/plans/pundit-calls.jsonl)

wifey-route-dedup-seed:  ## backfill the routing ledger from Stream C (read-only; APPLY=1 to write)
	@echo "🗂  Seeding the routing-dedup ledger from pundit-calls.jsonl..."
	@PYTHONPATH=. poetry run python tools/route_dedup.py seed $(if $(APPLY),--apply,) $(ARGS)

wifey-backtest:
	@echo "📊 Running backtest..."
	@poetry run python wifey.py backtest \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(SYMBOL),--symbol $(SYMBOL),) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(STRATEGY),--strategy $(STRATEGY),) \
		$(if $(STRATEGIES),--strategies $(STRATEGIES),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),) \
		$(if $(INTERVAL),--interval $(INTERVAL),) \
		$(if $(DAYS),--days $(DAYS),) \
		$(if $(SINCE),--since $(SINCE),) \
		$(if $(SL_PCT),--sl-pct $(SL_PCT),) \
		$(if $(TP_R),--tp-r $(TP_R),) \
		$(if $(MIN_TRADES),--min-trades $(MIN_TRADES),) \
		$(if $(COMBO),--combo,) \
		$(if $(WINDOW),--window $(WINDOW),) \
		$(if $(SAVE),--save,)

wifey-combo-backtest:
	@echo "📊 Running co-firing confluence backtest..."
	@poetry run python wifey.py backtest \
		--combo \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),) \
		$(if $(WINDOW),--window $(WINDOW),) \
		$(if $(DAYS),--days $(DAYS),) \
		$(if $(SINCE),--since $(SINCE),) \
		$(if $(MIN_TRADES),--min-trades $(MIN_TRADES),) \
		$(if $(FEE_PCT),--fee-pct $(FEE_PCT),) \
		$(if $(DAY_FILTER),--day-filter,) \
		$(if $(SAVE),--save,) \
		$(if $(WORKERS),--workers $(WORKERS),)

wifey-cross-tf-backtest:
	@echo "📊 Running cross-TF co-firing backtest..."
	@poetry run python wifey.py backtest \
		--cross-tf \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(HTF_LTF),--htf-ltf $(HTF_LTF),) \
		$(if $(WINDOW_HOURS),--window-hours $(WINDOW_HOURS),) \
		$(if $(DAYS),--days $(DAYS),) \
		$(if $(SINCE),--since $(SINCE),) \
		$(if $(MIN_TRADES),--min-trades $(MIN_TRADES),) \
		$(if $(FEE_PCT),--fee-pct $(FEE_PCT),) \
		$(if $(DAY_FILTER),--day-filter,) \
		$(if $(SAVE),--save,) \
		$(if $(WORKERS),--workers $(WORKERS),)

wifey-param-audit:
	@echo "🔬 Running strategy audit..."
	@poetry run python wifey.py param-audit \
		$(if $(SYMBOL),--symbol $(SYMBOL),$(error SYMBOL is required)) \
		$(if $(TIMEFRAME),--timeframe $(TIMEFRAME),$(error TIMEFRAME is required)) \
		$(if $(STRATEGIES),--strategies $(STRATEGIES),) \
		$(if $(DAYS),--days $(DAYS),) \
		$(if $(SINCE),--since $(SINCE),) \
		$(if $(WFO_SPLIT),--wfo-split $(WFO_SPLIT),) \
		$(if $(FEE_PCT),--fee-pct $(FEE_PCT),) \
		$(if $(DAY_FILTER),--day-filter $(DAY_FILTER),)

wifey-param-sweep:
	@echo "🔬 Running WFO parameter sweep..."
	@poetry run python wifey.py param-sweep \
		$(if $(STRATEGY),--strategy $(STRATEGY),$(error STRATEGY is required)) \
		$(if $(SYMBOL),--symbol $(SYMBOL),$(error SYMBOL is required)) \
		$(if $(TIMEFRAME),--timeframe $(TIMEFRAME),$(error TIMEFRAME is required)) \
		$(if $(PARAM),--param $(PARAM),) \
		$(if $(WFO_SPLIT),--wfo-split $(WFO_SPLIT),) \
		$(if $(MIN_TRADES),--min-trades $(MIN_TRADES),) \
		$(if $(TOP_N),--top-n $(TOP_N),) \
		$(if $(DAYS),--days $(DAYS),) \
		$(if $(SINCE),--since $(SINCE),) \
		$(if $(FEE_PCT),--fee-pct $(FEE_PCT),) \
		$(if $(DAY_FILTER),--day-filter $(DAY_FILTER),)

wifey-recalibrate:
	@echo "⭐ Recalibrating confidence star ratings from backtest DB..."
	@poetry run python wifey.py recalibrate \
		$(if $(MIN_TRADES),--min-trades $(MIN_TRADES),) \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(DAY_FILTER),--day-filter $(DAY_FILTER),) \
		$(if $(APPLY),--apply,)

wifey-sync-parent:
	@echo "🔀 Scanning parent repo for portable changes..."
	@PYTHONPATH=. poetry run python tools/sync_parent.py \
		$(if $(FROM),--from $(FROM),) \
		$(if $(FULL),--full,) \
		$(if $(BUMP_TO),--bump-to $(BUMP_TO),) \
		$(if $(NO_FETCH),--no-fetch,)

## Routine DB update: run both signal_watch backtests + recalibrate + regression update
db-update-backtest:
	@echo "📊 Running backtest for both signal_watch configs..."
	$(MAKE) wifey-backtest CONFIG=config/signal_watch.toml SAVE=1
	$(MAKE) wifey-backtest CONFIG=config/signal_watch_weekdays.toml SAVE=1

db-update-recalibrate:
	@echo "⭐ Recalibrating both signal_watch configs..."
	$(MAKE) wifey-recalibrate CONFIG=config/signal_watch.toml APPLY=1
	$(MAKE) wifey-recalibrate CONFIG=config/signal_watch_weekdays.toml APPLY=1

## Report (strategy × timeframe) cells where declaration and output disagree:
## declared-but-dead (detector never fires) and rated-but-undeclared (a
## confidence_ratings row outliving the config that produced it).
## Advisory inside db-update so neither ever blocks a DB refresh; run the
## target directly for a non-zero exit.
check-dead-surfaces:
	@echo "🔍 Checking for declared-but-dead and rated-but-undeclared cells..."
	@poetry run python tools/dead_surface_check.py

## Prints the repo-shape numbers that used to be hand-copied into the handoff and
## MEMORY.md. Every one of them has a history of being quoted stale — the fix is to
## PRINT them, not to write them down. Fast: no full suite, no full mypy.
status:
	@echo "📊 Repo shape ($$(date -u +%Y-%m-%d))"
	@printf '  tests collected   %s  (incl. 2 regression that `make test` ignores)\n' "$$(poetry run pytest tests/ --collect-only -q 2>/dev/null | tail -1 | grep -oE '^[0-9]+' || echo '?')"
	@printf '  python files      %s\n' "$$(git ls-files '*.py' | wc -l)"
	@printf '  markdown files    %s\n' "$$(npx markdownlint-cli2 2>&1 | grep -oE 'Linting: [0-9]+' | grep -oE '[0-9]+' || echo '?')"
	@printf '  CLAUDE.md         %s KB\n' "$$(wc -c < CLAUDE.md | awk '{printf "%.1f", $$1/1024}')"
	@printf '  handoff           %s\n' "$$(H=docs/plans/next-conversation-prompt.md; if [ -f $$H ]; then wc -l < $$H | awk '{print $$1" lines"}'; else printf 'ABSENT'; fi)"
## The bullet count CALLS the gate's own implementation rather than re-deriving it.
## The awk range form (`/^## Current State/,0`) ran to EOF, so it counted every
## bullet in every following section -- correct here only by accident, because
## Current State happens to be the last heading. Both measurement defects this
## line has had (the other was `du -k`, i.e. disk blocks) sat in the REPORTER and
## never in the gate, and both over-measured, i.e. toward needless rolling that
## the gate never demanded. When a gate and a report compute one quantity, the
## report must call the gate.
	@PYTHONPATH=. poetry run python -c 'from tools.post_branch_checks import MEMORY, current_state_bullets; kb = "%.1f KB" % (MEMORY.stat().st_size / 1024) if MEMORY.is_file() else "ABSENT"; n = current_state_bullets(MEMORY.read_text(encoding="utf-8")) if MEMORY.is_file() else "?"; print("  MEMORY.md         %s, %s Current State bullets" % (kb, n))' 2>/dev/null || printf '  MEMORY.md         ?\n'
	@printf '  audits            %s\n' "$$(ls docs/audits/*.md | grep -vc INDEX)"
	@printf '  skills            %s\n' "$$(ls -d .claude/skills/*/ | wc -l)"
	@printf '  context docs      %s\n' "$$(ls .claude/context/*.md | wc -l)"
	@printf '  tools             %s\n' "$$(ls tools/*.py | wc -l)"

## Reports Test* classes that NAME a unit but never CALL it (#150: five TestEvGate
## tests never invoked the EV gate — it was a closure, so they re-implemented the
## comparison and passed against any implementation). Heuristic, so it is advisory
## and NOT in `make test`; --strict gives a non-zero exit. Run from /sanity-check.
check-orphan-tests:
	@echo "🔍 Checking for test classes that name a unit but never call it..."
	@poetry run python tools/orphan_test_audit.py

## Every mechanical /post-branch check in one run. Advisory by design: a finding
## is a candidate to dismiss in seconds, not an automatic edit. Exits non-zero on
## any finding so it can gate if a caller wants it to; pass --exit-zero not to.
post-branch-checks:
	@echo "🔍 Running the mechanical /post-branch sweep..."
	@PYTHONPATH=. poetry run python tools/post_branch_checks.py --exit-zero

## Screen a composed PR title/body for sensitive terms BEFORE `gh pr create`.
## FILE=- reads stdin, so a title pipes straight in. Unlike the sweep above this
## one GATES (exit 1 on a hit): a posted body is public the moment it lands and
## editing it later does not unpublish it. The sweep's three git legs ask the
## tracked tree and this branch's commits — a PR body is neither, so they report
## clean on one naming every term. Run it in /post-branch phase 5.
## ⚠ Through make the exit code is make's own 2, never the tool's 1 — read the
## banner, as with wait-ci and preflight. An unreadable FILE also exits 2, which
## is the point: it must not render as a clean single-check run.
post-branch-text:
	@PYTHONPATH=. poetry run python tools/post_branch_checks.py --text $(FILE)

## Every mechanical /sanity-check check in one run. Unlike post-branch-checks
## this one GATES: it exits non-zero on any finding, and the same sweep runs in
## `make test` via tests/test_sanity_checks.py, so CI is what actually enforces
## it. CLAUDE.md's rule is the reason — a self-check outside CI is not a check.
## Every leg is CI-portable: the one that needs the gitignored watchlist
## degrades to a printed note rather than a finding.
sanity-checks:
	@echo "🔍 Running the mechanical /sanity-check sweep..."
	@PYTHONPATH=. poetry run python tools/sanity_checks.py

## The clean-clone pre-flight. Run it in /post-branch phase 5, AFTER the doc
## commits and BEFORE `gh pr create` — a clone sees COMMITTED state only, so
## running it earlier tests stale HEAD and reports green. It REPLACES that
## branch's `make test`, which it mirrors argument-for-argument; only this one
## is hermetic. Bare python3 on purpose: the gate must still run when the dev
## venv is the thing that is broken.
## ⚠ make collapses the recipe's exit code, so read the printed banner: REFUSED
## (dirty tree) and INFRA (clone/install died) are NOT suite failures.
preflight:
	@echo "🧪 Running the clean-clone pre-flight..."
	@python3 tools/clone_preflight.py

## Which recurring tasks are overdue, read from docs/plans/task-marks/.
## ADVISORY and deliberately NOT in `make test`: the marks are gitignored, so a
## fresh CI clone sees every one absent and would report every task permanently
## overdue. A check that can only be red in CI is worse than no check.
## A MISSING mark reads as OVERDUE on purpose — a lost mark must shout.
cadence-check:
	@PYTHONPATH=. poetry run python tools/cadence_check.py

## Record that a recurring task just ran: `make cadence-stamp TASK=sanity-check`.
## A flag rather than a `date -u ... > path` redirect, which silently writes the
## wrong file on a typo; this refuses a slug that is not a declared task.
cadence-stamp:
	@PYTHONPATH=. poetry run python tools/cadence_check.py --stamp $(TASK)

## The completion banner is CONDITIONAL on the surface check. It used to print
## an unqualified ✅ beside nine orphaned confidence_ratings rows, one of them
## displaying 3★ — the refresh had "succeeded" and the output said so.
##
## The banner also carries /db-update step 3's falsifier, because the banner is
## what a DIRECT runner reads. Running `make db-update` instead of invoking the
## skill is how that step got skipped on 2026-08-14: regression-update re-derives
## the fixture parquets from a DB that has moved on, so a golden diff is usually
## DATA DRIFT and reads as a behaviour change (13 star moves, 12 of them out of
## the change's reach). A procedure that lives only in the skill does not reach
## the person who typed the target.
db-update: db-update-backtest db-update-recalibrate regression-update
	@$(MAKE) --no-print-directory check-dead-surfaces \
	  && printf '%s\n' \
	    "✅ Routine DB update complete." \
	    "   Review:  git diff tests/fixtures/golden_*.json" \
	    "   FALSIFY: git checkout -- tests/fixtures/ && make test-regression" \
	    "   If that PASSES, your code is golden-neutral: the diff is fixture data" \
	    "   drift, NOT your change — revert the goldens rather than shipping them." \
	  || echo "⚠️  DB refresh finished, but the surface check above FAILED — fix that before trusting the ratings."

wifey-digest:
	@echo "📊 Running backtest analysis digest..."
	@poetry run python wifey.py digest \
		$(if $(QUERY),--query $(QUERY),) \
		$(if $(MIN_TRADES),--min-trades $(MIN_TRADES),) \
		$(if $(TOP_N),--top-n $(TOP_N),)

wifey-signal-watch:
	@echo "🔍 Running signal detection daemon..."
	@poetry run python wifey.py signal watch \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),) \
		$(if $(STRATEGIES),--strategies $(STRATEGIES),) \
		$(if $(TELEGRAM),--telegram,) \
		$(if $(MIN_SL_PCT),--min-sl-pct $(MIN_SL_PCT),) \
		$(if $(ONCE),--once,) \
		$(if $(CATCH_UP),--catch-up,)

## --- Go-live (Phase A signal-alert bot) — see README "Go Live (Phase A)" ---
GO_LIVE_CONFIG ?= config/signal_watch.toml

# One-time prep: backfill the full watchlist OHLCV. Run once on a fresh box, or
# after a wiped DB / data gap. Override the start date with SINCE=YYYY-MM-DD.
go-live-prep:
	@echo "📥 Go-live prep: backfilling watchlist OHLCV (one-time)..."
	$(MAKE) wifey-analytics-backfill SINCE=$(or $(SINCE),2023-01-01)

# Run ONE signal-alert scan cycle (sync → scan → alert → outcome backfill), then exit.
# Telegram ON. Built for once-a-day manual / cron use — no long-running daemon.
# Run it PRE-OPEN (before 13:30 UTC) on trading days — NOT after the US close.
# yfinance stamps a 1d bar at 04:00 UTC (05:00 under EST) and it forms for a full
# tf_ms, so Tuesday's daily bar does not close until Wed 04:00/05:00 UTC: at the
# 20:00/21:00 UTC close it is still forming and that session's 1d signals cannot
# alert. The last 4h RTH bar (17:30 UTC) closes at 21:30 UTC, also after the bell.
# A pre-open run is safely past both. Override config:
#   make go-live GO_LIVE_CONFIG=config/signal_watch_weekdays.toml
# Do NOT run both configs live on the same day. They share signal_state.json on
# purpose (weekdays' 32 declared cells are a strict superset of signal_watch's 22),
# so whichever runs FIRST alerts the shared Tue–Thu candles with ITS tp_r and the
# other then stays silent. Nothing is lost, but the parameters are the first
# runner's — see the header of config/signal_watch_weekdays.toml.
# To run as a continuous daemon instead (self-syncs + sleeps to candle boundaries),
# drop the once flag: make wifey-signal-watch CONFIG=... TELEGRAM=1
# Pass CATCH_UP=1 to replay candles missed since the last run. ⚠ The sentence that
# stood here until 2026-08-25 — "recovered candles land in the DB/outcome ledger
# only, never Telegram" — was the PRE-#260 rule and is now false. A replayed candle
# whose CLOSE is within max_alert_age_hours (24.0 in the shared base) DOES dispatch;
# see scanner.may_dispatch_candle. That is the point: under one pre-open run a day
# the session's FIRST 4h bar is never the newest closed candle, so the old rule made
# it structurally undeliverable (120 of 351 candles, 34%).
# Still safe to pass on every run, for a narrower reason — the window is a CALENDAR
# bound, so an old candle ages out rather than arriving as a stale alert. Two things
# it deliberately will NOT do: re-send history (a consumed watermark stays consumed)
# and recover a skipped run DAY's alerts (~87h stale, outside the window) — that day's
# candles land as ledger evidence and nothing is sent.
# ⚠ max_alert_age_hours is INERT without CATCH_UP=1: only catch-up emits an event for
# a non-latest candle in the first place. The scheduled unit therefore passes it —
# deploy/systemd/user/wifey-signal-watch.service.
# Also refreshes watchlist 1h OHLCV first (non-fatal) — the Stats path cone's
# substrate; the signal daemon itself only syncs its own signal TFs (4h/1d/1wk).
go-live:
	@echo "🚀 Go-live (single cycle): $(GO_LIVE_CONFIG) — Telegram ON"
	-$(MAKE) wifey-analytics-sync TIMEFRAMES=1h
	$(MAKE) wifey-signal-watch CONFIG=$(GO_LIVE_CONFIG) TELEGRAM=1 ONCE=1 $(if $(CATCH_UP),CATCH_UP=1,)

## --- Backup (verified local snapshot) — see deploy/README.md ---
# Snapshots analytics.db, the whole gitignored docs/plans research tree, and the
# memory tree (which lives OUTSIDE the repo and holds the SoT) to
# $WIFEY_BACKUP_ROOT (default ~/backups/wifey). Verified and atomically
# published, so a snapshot at the final path is always restorable.
# This is the LIKELY-failure leg only — it does not survive disk loss.
# Sources .env for the same reason the off-site targets below do: both units
# carry EnvironmentFile=, so a hand-run that skipped it would apply the built-in
# 14/8 retention while the TIMER applied whatever .env says — a hand-run and a
# scheduled run pruning to different depths, with nothing reporting a
# difference. No WIFEY_KEEP_* is set today, so this is latent, not live.
backup:
	@set -a; [ -f .env ] && . ./.env; set +a; \
		echo "💾 Verified local backup → $${WIFEY_BACKUP_ROOT:-$$HOME/backups/wifey}"; \
		./deploy/backup-analytics.sh --weekly-if-due

# Report what would be captured; writes nothing. Use this after adding anything
# to docs/plans to confirm the file count moved.
backup-dry-run:
	@set -a; [ -f .env ] && . ./.env; set +a; ./deploy/backup-analytics.sh --dry-run

## Report the AGE of the newest verified snapshot — the observed-state probe the
## failure-only alert channel cannot be. `OnFailure=` means a timer with nothing
## to report and a timer that STOPPED FIRING look identical from Telegram, so
## verify a snapshot's age rather than a unit's `enabled` state.
## Reads captured_at_utc from MANIFEST.json, never the directory's mtime: mtime
## moves for a restore or an rclone round-trip, and it fails in the direction
## that reports fresher than reality.
## ADVISORY and deliberately NOT in `make test` or `make sanity-checks` — the
## backup root is machine-local single-copy state that no clone has, so CI would
## report a missing backup forever. A check that can only be red in CI is worse
## than no check. `--exit-nonzero` opts in for a human who wants a shell condition.
## Measures the LOCAL tree only; it cannot see whether the off-site mirror got it.
backup-check:
	@set -a; [ -f .env ] && . ./.env; set +a; \
		PYTHONPATH=. poetry run python tools/backup_check.py

## Date the observed state of the two scheduled surfaces `backup-check` cannot see:
## has a scan RUN (the watchlist sync a run performs), and when did one last
## DISPATCH (the watermarks). Same reason as `backup-check`: `OnFailure=` alerting
## cannot tell a timer with nothing to report from one that STOPPED FIRING.
## Age is in NYSE SESSIONS, never wall-clock — on an RTH tape `4h` is 2 bars/day,
## so the parent tool's `(now - newest) / bar_ms` would red every series forever.
## ⚠ The 505-member research universe is graded ONLY where `wifey-universe-sync.timer`
## reads enabled, never unconditionally: the units are opt-in, so on a box that
## installed nothing this would print ~1,100 findings and a leg that is never green
## stops being read. Refresh it with `make wifey-universe-sync`; the 2018
## `wifey-universe-backfill` stays hand-run for a NEW constituent, which `sync` skips.
## ADVISORY and deliberately NOT in `make test`, `make sanity-checks` or CI — both
## legs read machine-local single-copy state no clone has. The PURE grading half
## is unit-tested in `tests/test_freshness_check.py`, which does run in `make test`.
## `--exit-nonzero` opts in for a human who wants a shell condition.
freshness-check:
	@PYTHONPATH=. poetry run python tools/freshness_check.py

## One-screen digest: signal-watch scheduled?, watchlist OHLCV, backup, cadence, open
## Issues, the handoff's first move. The SessionStart hook runs it at every session
## start; `wifey-daily-check` runs it with TELEGRAM=1, which sends to the PERSONAL
## channel every day, green included, so a missing message means the scheduler died.
## Always exits 0 (a digest must never block); `EXIT_NONZERO=1` opts into exit 1 on RED.
session-digest:
	@PYTHONPATH=. poetry run python tools/session_digest.py \
		$(if $(TELEGRAM),--telegram) $(if $(EXIT_NONZERO),--exit-nonzero)

# Off-machine leg: rclone sync of $WIFEY_BACKUP_ROOT to $WIFEY_BACKUP_REMOTE.
# This is the one that survives disk death or a lost laptop. It syncs whatever
# `backup` already verified and snapshots nothing itself.
# ⚠ `sync` MIRRORS DELETIONS. Read deploy/README.md's off-site section before
# setting WIFEY_BACKUP_REMOTE. wifey has its OWN rclone remote pinned to its own
# folder (gdrive-wifey:snapshots) — that separation is what keeps this off the
# crypto parent's snapshot tree on the same Drive account, NOT the script's own
# guards, which cannot tell a same-shaped sibling from our data.
# Both targets source .env, because unlike `backup` this script REQUIRES config
# and would otherwise fail "WIFEY_BACKUP_REMOTE is unset" on every hand-run.
# The systemd unit reads the same file via EnvironmentFile= instead.
backup-offsite:
	@set -a; [ -f .env ] && . ./.env; set +a; \
		echo "☁️  Off-site backup → $${WIFEY_BACKUP_REMOTE:-<unset: see deploy/README.md>}"; \
		./deploy/backup-offsite.sh

# Report what would upload and what would be DELETED remotely; writes nothing.
# Always run this first after changing WIFEY_BACKUP_REMOTE, and read the DELETE
# lines rather than only the copies — deletions are the direction that loses data.
backup-offsite-dry-run:
	@set -a; [ -f .env ] && . ./.env; set +a; ./deploy/backup-offsite.sh --dry-run

docker-signal-watch:
	@echo "🔍 Running signal detection daemon in Docker..."
	@touch analytics.db signal_state.json
	docker run -it --env-file .env \
		-v $(PWD)/analytics.db:/app/analytics.db \
		-v $(PWD)/config/stocks.json:/app/config/stocks.json:ro \
		-v $(PWD)/signal_state.json:/app/signal_state.json \
		$(DOCKER_IMAGE) poetry run python wifey.py signal watch \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(SYMBOLS),--symbols $(SYMBOLS),) \
		$(if $(TIMEFRAMES),--timeframes $(TIMEFRAMES),) \
		$(if $(STRATEGIES),--strategies $(STRATEGIES),) \
		$(if $(TELEGRAM),--telegram,) \
		$(if $(MIN_SL_PCT),--min-sl-pct $(MIN_SL_PCT),)

wifey-signal-test:
	@echo "🧪 Firing test alert from historical data..."
	@poetry run python wifey.py signal test \
		$(if $(CONFIG),--config $(CONFIG),) \
		$(if $(SYMBOL),--symbol $(SYMBOL),) \
		$(if $(TIMEFRAME),--timeframe $(TIMEFRAME),) \
		$(if $(STRATEGY),--strategy $(STRATEGY),) \
		$(if $(AT),--at $(AT),) \
		$(if $(SINCE),--since $(SINCE),) \
		$(if $(LOOKBACK),--lookback $(LOOKBACK),) \
		$(if $(DIRECTION),--direction $(DIRECTION),) \
		$(if $(TELEGRAM),--telegram,)

wifey-web:
	@echo "Starting web backend..."
	poetry run python wifey.py web --host 0.0.0.0 --port $(PORT) \
		$(if $(CONFIG),--config $(CONFIG),)

web-install:
	cd web/ui && npm install

web-dev:
	cd web/ui && npm run dev -- --port $(DEV_PORT)

web-build:
	cd web/ui && npm run build

web-check:
	cd web/ui && npx svelte-check

web-preview:
	cd web/ui && npm run preview -- --port $(DEV_PORT)

web-full: web-build wifey-web

wifey-open-trades:  ## Phase B placeholder — fails loudly; there is no order layer yet
	@echo "❌ No order layer exists. Phase A is signals-only."
	@echo "   trade/open_trades.py is a 0-byte placeholder: the fork dropped the parent's"
	@echo "   Binance Futures opener and Phase B (equities broker, TBD) has not landed."
	@echo "   This target used to run the empty file, print a success banner and exit 0 —"
	@echo "   indistinguishable from having placed orders. Failing instead (2026-08-06)."
	@exit 1

db-prune-backtests:
	@echo "🧹 Pruning old backtest runs (hard cutoff 30d; soft cutoff 7d+top-10 per strategy×symbol×tf×day_filter×adr_threshold)..."
	@poetry run python scripts/db_prune_backtests.py

clean-db:
	@echo "🗑️  Removing analytics DB and WAL files..."
	rm -f analytics.db analytics.db.wal

clean:
	@echo "🧹 Cleaning cache and build artifacts..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .mypy_cache .ruff_cache .pytest_cache .coverage htmlcov/ dist/

## Waits for a PR's checks to settle, then reports whether they actually RAN.
## Encodes four scars as code rather than handoff prose: a check-count FLOOR (an
## empty rollup satisfies "nothing pending"), ""-is-pending (not null), steps=0 =
## billing not code, and a fast PASS needs the same scrutiny as a fast FAIL.
wait-ci:
	@PYTHONPATH=. poetry run python tools/wait_ci.py --pr $(PR)

## The FLIP-BACK gate: wait for main's push run to reach >=5 COMPLETED jobs before
## flipping the repo private. Merging starts a fresh run on main and flipping kills
## whatever is created after the flip; `Regression tests` needs lint-typecheck-test
## and is not created until ~4 min in, so an early flip leaves it at steps=0 and main
## looks red for billing reasons. Gates on a job-count FLOOR, never "nothing pending".
wait-ci-main:
	@PYTHONPATH=. poetry run python tools/wait_ci.py --branch main --min-jobs 5
