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

.PHONY: lint lint-md lint-md-fix docs-index docs-index-check lint-py-check lint-py typecheck test test-cov test-regression regression-update poetry-install poetry-update docker-build docker-analytics-backfill docker-analytics-sync docker-backtest docker-signal-watch wifey-open-trades wifey-analytics-backfill wifey-analytics-sync wifey-universe-backfill universe-coverage wifey-forecast-audit wifey-xsmom-audit wifey-xsmom-residual-audit wifey-lowvol-audit wifey-xasset-audit wifey-xasset-backfill wifey-pead-audit wifey-pead-backfill wifey-exit-audit wifey-pundit-score wifey-check-levels wifey-route-dedup-seed wifey-backtest wifey-combo-backtest wifey-cross-tf-backtest wifey-signal-watch go-live go-live-prep backup backup-dry-run wifey-param-audit wifey-param-sweep wifey-recalibrate wifey-sync-parent check-dead-surfaces check-orphan-tests wifey-digest wifey-web web-install web-dev web-build web-preview web-full clean-db clean

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

wifey-universe-backfill:
	@echo "📥 Backfilling the research breadth universe (config/universe.json)..."
	@poetry run python wifey.py analytics backfill --universe \
		--timeframes 4h 1d 1wk --since $(or $(SINCE),2018-01-01)

universe-coverage:
	@PYTHONPATH=. poetry run python tools/universe_coverage.py \
		$(if $(DB),--db $(DB),)

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

wifey-pead-backfill:
	@echo "📥 Edge-hunt #4 — ingesting EDGAR earnings facts (one-shot)..."
	@PYTHONPATH=. poetry run python tools/pead_backfill.py $(ARGS)

wifey-exit-audit:
	@echo "🚪 Exit MFE/MAE diagnostic over the live alert ledger (spec §2)..."
	@PYTHONPATH=. poetry run python tools/exit_audit.py $(ARGS)

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

## Reports Test* classes that NAME a unit but never CALL it (#150: five TestEvGate
## tests never invoked the EV gate — it was a closure, so they re-implemented the
## comparison and passed against any implementation). Heuristic, so it is advisory
## and NOT in `make test`; --strict gives a non-zero exit. Run from /sanity-check.
check-orphan-tests:
	@echo "🔍 Checking for test classes that name a unit but never call it..."
	@poetry run python tools/orphan_test_audit.py

## The completion banner is CONDITIONAL on the surface check. It used to print
## an unqualified ✅ beside nine orphaned confidence_ratings rows, one of them
## displaying 3★ — the refresh had "succeeded" and the output said so.
db-update: db-update-backtest db-update-recalibrate regression-update
	@$(MAKE) --no-print-directory check-dead-surfaces \
	  && echo "✅ Routine DB update complete. Review: git diff tests/fixtures/golden_*.json" \
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
# To run as a continuous daemon instead (self-syncs + sleeps to candle boundaries),
# drop the once flag: make wifey-signal-watch CONFIG=... TELEGRAM=1
# Pass CATCH_UP=1 to replay candles missed since the last run (skipped run-day recovery).
# Recovered candles land in the DB/outcome ledger only — never Telegram — so it is
# safe to pass CATCH_UP=1 on every run.
# Also refreshes watchlist 1h OHLCV first (non-fatal) — the Stats path cone's
# substrate; the signal daemon itself only syncs its own signal TFs (4h/1d/1wk).
go-live:
	@echo "🚀 Go-live (single cycle): $(GO_LIVE_CONFIG) — Telegram ON"
	-$(MAKE) wifey-analytics-sync TIMEFRAMES=1h
	$(MAKE) wifey-signal-watch CONFIG=$(GO_LIVE_CONFIG) TELEGRAM=1 ONCE=1 $(if $(CATCH_UP),CATCH_UP=1,)

## --- Backup (verified local snapshot) — see deploy/README.md ---
# Snapshots analytics.db + the whole gitignored docs/plans research tree to
# $WIFEY_BACKUP_ROOT (default ~/backups/wifey). Verified and atomically
# published, so a snapshot at the final path is always restorable.
# This is the LIKELY-failure leg only — it does not survive disk loss.
backup:
	@echo "💾 Verified local backup → $${WIFEY_BACKUP_ROOT:-$$HOME/backups/wifey}"
	./deploy/backup-analytics.sh --weekly-if-due

# Report what would be captured; writes nothing. Use this after adding anything
# to docs/plans to confirm the file count moved.
backup-dry-run:
	./deploy/backup-analytics.sh --dry-run

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
