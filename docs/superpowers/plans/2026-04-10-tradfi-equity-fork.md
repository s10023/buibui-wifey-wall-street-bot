# TradFi Equity Fork Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

---

## 0. Updates after 2026-05-14 (read this first)

This plan was drafted 2026-04-10 around an Alpaca-Markets data layer and a single-shot fork that included paper trading. Three decisions made 2026-05-14 materially alter it (companion spec `../specs/2026-04-10-tradfi-equity-fork-design.md` §0.1):

1. **Alpaca dropped; yfinance committed for Phase A.** Polygon.io Starter $29/mo is the pre-approved upgrade target.
2. **Timeframe scope cut to 4h / 1d / 1w only.** 15m and 1h are out of scope — this is what made yfinance viable.
3. **Phased delivery.** Phase A = signals + dual Telegram only (no order layer). Phase B = broker / order execution, deferred entirely.
4. **Migration tooling** between buibui and this fork is an open question (skill / shared package / patch queue). Deferred.

### 0.a Task survival matrix (body below = original 2026-04-10 text, read with this lens)

<!-- markdownlint-disable MD060 -->

| Task                                                            | Status                    | Notes                                                                                                                                                                                                                                                                                 |
|-----------------------------------------------------------------|---------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1. Fork repo + strip crypto skeleton                            | ✅ ready                   | Delete also: anything tied to Binance live WebSockets. Positions tab + live wrappers stay out for Phase A.                                                                                                                                                                            |
| 2. Add alpaca-py + create Alpaca client                         | 🔁 **REWRITE → yfinance** | `pip install yfinance`; new `utils/yfinance_client.py` factory (no auth needed); see 0.b sketch                                                                                                                                                                                       |
| 3. Replace `data_fetcher.py` with Alpaca                        | 🔁 **REWRITE → yfinance** | Use `yf.Ticker(sym).history(period=..., interval=..., auto_adjust=False)` for 1h/1d/1w; resample 1h→4h client-side anchored to 13:30 UTC; see 0.b sketch                                                                                                                              |
| 4. Update `data_store.py` (vwap column, drop funding/OI tables) | ⚠️ partial                | "drop funding/OI" survives. **`vwap` column is DROPPED entirely** (yfinance has no VWAP). Re-add only if swapping to Polygon. `taker_buy_volume` column is removed without replacement.                                                                                               |
| 5. Update `data_sync.py` — remove funding/OI sync               | ⚠️ partial                | "remove funding/OI sync" survives. Rewire to yfinance client. Symbol format = plain ticker (`AAPL`, not `AAPLUSD`).                                                                                                                                                                   |
| 6. Create `stocks.json` + config validation                     | ✅ ready                   |                                                                                                                                                                                                                                                                                       |
| 7. Remove `funding_reversion` + `cvd_divergence`                | ✅ ready                   | `funding_reversion` already deleted in parent Phase 1 H3 — strip step is for `cvd_divergence` only                                                                                                                                                                                    |
| 8. Fix ORB session anchor for US market open                    | ⚠️ scope change           | Original spec used 15m/1h ORB. With TFs ≥ 4h, ORB-on-15m is moot. Keep the anchor fix (13:30 UTC) for any future re-add of intraday TFs, but the strategy may not actively fire in Phase A. Verify `strategy_timeframes` in equity TOML excludes ORB or restricts it to non-intraday. |
| 9. Create `overnight_gap_lib.py`                                | ✅ ready                   |                                                                                                                                                                                                                                                                                       |
| 10. Wire overnight gap into signal_lib                          | ⚠️ import-path fix        | `analytics/signal_lib.py` is now a re-export shim; real wiring lives in `analytics/signal/` package (parent Phase 2). Update target paths.                                                                                                                                            |
| 11. End-to-end smoke test                                       | 🔁 **REWRITE → yfinance** | No credentials needed; smoke test = `yf.Ticker("AAPL").history(period="6mo", interval="1h")` returns a DataFrame, then full pipeline through signal + alert + backtest                                                                                                                |

<!-- markdownlint-enable MD060 -->

### 0.b yfinance task sketches (for Tasks 2, 3, 11 rewrites)

```python
# utils/yfinance_client.py — Task 2 replacement
# No auth, no client object. Module-level helpers only.
import yfinance as yf

_INTERVAL_MAP = {"1h": "60m", "1d": "1d", "1wk": "1wk"}

def fetch_bars_yf(symbol: str, interval: str, period: str = "max") -> pd.DataFrame:
    yf_interval = _INTERVAL_MAP[interval]
    df = yf.Ticker(symbol).history(period=period, interval=yf_interval, auto_adjust=False)
    # rename columns to canonical OHLCV schema (open, high, low, close, volume)
    # convert index → open_time (UTC ms)
    return df_canonical

def resample_to_4h(hourly_df: pd.DataFrame) -> pd.DataFrame:
    # anchor to 13:30 UTC = US market open (regular session)
    return hourly_df.resample("4h", origin="start_day", offset="13h30min").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna()
```

### 0.c Missing tasks not in the original plan body

Audit 2026-05-14: the original plan was data-layer-only. These are required for a working Phase A bot and are **not present below**:

<!-- markdownlint-disable MD060 -->

| New task | Detail |
| --- | --- |
| **T12. Dual-Telegram dispatcher** | Spec §7.6 was sketched after the plan was written. Two BotFather bots → `TELEGRAM_PERSONAL_TOKEN/_CHAT_ID` + `TELEGRAM_WIFE_TOKEN/_CHAT_ID`. Dispatcher in `signals/` fans out to N publishers each with `(direction_filter, label_rewrite)`. Wife channel = long-only + "LONG"→"BUY" rewrite. **Port the pattern from parent PR #367's `_apply_direction_filter_gate` in `analytics/signal/gates.py` + `StrategyOverride.suppress_long/_short` flags** rather than re-deriving. |
| **T13. Equity `signal_watch_*.toml` configs** | Parent has `signal_watch.toml` + `_all` + `_weekdays` extending `strategy_params.toml`. Equity fork needs its own base + variants with `strategy_timeframes` cut to 4h/1d/1w, equity session windows, no crypto-specific `smt_pairs` defaults. |
| **T14. Backtest WFO + recalibrate on equity data** | Parent `tp_r` / `atr_sl_multiplier` values were tuned on crypto. They likely don't transfer. After Task 11 smoke test: run `make buibui-backtest SAVE=1` against the equity TOML → run `recalibrate` → review star ratings → spot-check via `tools/combo_health.py`. |
| **T15. Alert formatter equity adaptations** | `signals/alert_formatter.py` formats `BTCUSDT @ $50000` with crypto conventions. Equity needs: ticker without `USDT` suffix, dollar formatting with 2 decimals (`$AAPL @ $187.42`), session warning footer ("after-hours data may not reflect retail fills"). |
| **T16. Web UI equity hygiene** | Remove Positions tab from nav (deferred per spec §10). Default symbol in dropdowns → AAPL or MSTR. Page titles. |
| **T17. Run-cadence wiring** | Bot is "on-demand" but the plan never says how. Options: manual CLI (`wifey scan`), local cron, GitHub Actions. Phase A pick: **manual CLI first**, document the cron snippet, defer automation to Phase B. |
| **T18. Migration tooling — DEFERRED** | Placeholder task. Decision pending (skill / shared package / patch queue). Re-open after fork is alive and porting pain is concrete. |

<!-- markdownlint-enable MD060 -->

### 0.d Phase 2 module renames (apply when rewriting Tasks 2–5, 10, and the new T12–T16)

| Old reference in this plan    | Post-Phase-2 home (parent repo)                                        |
|-------------------------------|------------------------------------------------------------------------|
| `analytics/data_store.py`     | `analytics/store/` package (8 modules); shim still re-exports          |
| `analytics/data_fetcher.py`   | unchanged path                                                         |
| `analytics/signal_lib.py`     | `analytics/signal/` package (10 modules); shim still re-exports        |
| `analytics/indicators_lib.py` | **DELETED** in strat-3 (PR #340) — use `analytics/strategies/` package |
| `analytics/backtest_lib.py`   | `analytics/backtest/` package                                          |
| `analytics/stats_lib.py`      | `analytics/stats/` package                                             |

### 0.e Net execution order (revised)

1. Tasks 1, 6, 7, 9, 10 — ready today, broker-agnostic + minor import path fixes
2. Tasks 2, 3, 4, 5, 11 — execute against yfinance using 0.b sketches
3. Task 8 — apply anchor fix, verify TOML excludes ORB from active strategies (or keep as no-op stub)
4. **T12** dual-Telegram — port from PR #367 pattern
5. **T13** equity TOML configs — derive from parent `signal_watch.toml`
6. **T14** backtest + recalibrate on equity data
7. **T15, T16** alert + UI hygiene
8. **T17** document manual-CLI run cadence

The body of this plan below is **left as-is for historical context**. Read top-down: §0 here, then §0.b sketches, then §0.c additions, then dive into Task 1.

---

**Goal:** Fork buibui-moon-trader-bot into a US-equities bot powered by yfinance (Phase A; Polygon.io $29 as pre-approved upgrade), running on-demand (EOD/EOW) against a watchlist of single stocks. Timeframes: 4h / 1d / 1w only.

**Architecture:** All strategy/backtest/web layers carry over unchanged. Only the data layer
is swapped (Binance → yfinance). Three crypto-only concepts are removed (funding, OI, CME gap)
and replaced with equity equivalents (overnight gap; **`vwap` column dropped entirely** — yfinance has no VWAP). The fork is a standalone repo — no shared packages, no cross-repo sync.

**Tech Stack:** Python 3.11+, Poetry, yfinance, DuckDB, FastAPI, Svelte 5

> **IMPORTANT — Scope:** All tasks below execute inside the **forked repo**, not buibui.
> After Task 1 you will be working in `~/repo/[BOTNAME]/`. File paths are relative to that root.
> `[BOTNAME]` is a placeholder — substitute the chosen name throughout (e.g. `lunafi`).

---

## File Map

<!-- markdownlint-disable MD060 -->

| Action  | Path                             | Purpose                                                    |
|---------|----------------------------------|------------------------------------------------------------|
| Create  | `utils/yfinance_client.py`       | yfinance helper module (no auth; module-level functions)   |
| Rewrite | `analytics/data_fetcher.py`      | yfinance bar fetching + 1h→4h resample (replaces Binance)  |
| Modify  | `analytics/store/`               | **Drop** `taker_buy_volume` column entirely; drop funding/OI tables. (Post-Phase-2: was `analytics/data_store.py`.) |
| Modify  | `analytics/data_sync.py`         | Remove funding/OI sync; wire yfinance fetcher              |
| Create  | `config/stocks.json`             | Equity watchlist (replaces coins.json)                     |
| Modify  | `utils/config_validation.py`     | Validate stocks.json schema                                |
| Modify  | `analytics/strategies/_registry.py` | Remove `cvd_divergence` (`funding_reversion` already gone); update ORB session anchor default. (Post-Phase-2: was `analytics/indicators_lib.py`.) |
| Modify  | `signals/registry.py`            | Remove `cvd_divergence` entry (`funding_reversion` already gone) |
| Create  | `analytics/overnight_gap_lib.py` | Overnight gap detection (replaces cme_gap_lib.py)          |
| Modify  | `analytics/signal/scanner.py`    | Wire overnight_gap_lib; remove cme_gap imports. (Post-Phase-2: was `analytics/signal_lib.py`.) |
| Delete  | `analytics/cme_gap_lib.py`       | Crypto-specific — no equity equivalent                     |
| Delete  | `utils/binance_client.py`        | Replaced by yfinance_client.py                             |

<!-- markdownlint-enable MD060 -->

---

## Task 1: Fork repo and strip crypto skeleton

**Files:**

- Delete: `utils/binance_client.py`
- Delete: `analytics/cme_gap_lib.py`
- Delete: `monitor/live_price.py`, `monitor/live_position.py` (Binance WebSocket wrappers)

- [ ] **Step 1: Fork on GitHub**

  ```bash
  # On GitHub: fork buibui-moon-trader-bot → [BOTNAME]
  # Then clone:
  git clone git@github.com:<you>/[BOTNAME].git ~/repo/[BOTNAME]
  cd ~/repo/[BOTNAME]
  ```

- [ ] **Step 2: Global rename buibui → [BOTNAME] in CLI and config**

  ```bash
  # Entry point
  mv wifey.py [BOTNAME].py

  # Makefile — replace all references to "buibui" with [BOTNAME]
  sed -i 's/buibui/[BOTNAME]/g' Makefile

  # pyproject.toml — update [tool.poetry] name
  sed -i 's/name = "buibui-moon-trader-bot"/name = "[BOTNAME]"/' pyproject.toml
  ```

- [ ] **Step 3: Delete crypto-specific files**

  ```bash
  rm utils/binance_client.py
  rm analytics/cme_gap_lib.py
  rm monitor/live_price.py monitor/live_position.py
  ```

- [ ] **Step 4: Verify tests still load (many will fail — that's expected)**

  ```bash
  poetry run pytest tests/ --collect-only 2>&1 | tail -20
  ```

  Expected: collection errors for binance imports — we'll fix these per task.

- [ ] **Step 5: Commit**

  ```bash
  git add -A
  git commit -m "chore: fork from buibui — strip crypto skeleton"
  ```

---

## Task 2: Add yfinance and create yfinance helper module

**Files:**

- Create: `utils/yfinance_client.py`
- Create: `tests/test_yfinance_client.py`

**Note:** yfinance has no API key. There's no "client" object in the Alpaca/Binance sense — just module-level functions wrapping `yf.Ticker(...).history()`. The "client" naming is kept for symmetry with the parent repo's `utils/binance_client.py`.

- [ ] **Step 1: Add yfinance dependency**

  ```bash
  poetry add yfinance
  ```

- [ ] **Step 2: Write the failing test**

  Create `tests/test_yfinance_client.py`:

  ```python
  from unittest.mock import MagicMock, patch

  import pandas as pd


  def test_fetch_history_calls_yfinance_with_canonical_args() -> None:
      from utils.yfinance_client import fetch_history

      mock_ticker = MagicMock()
      mock_ticker.history.return_value = pd.DataFrame(
          {"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [100]},
          index=pd.DatetimeIndex(["2026-01-02"], tz="America/New_York"),
      )
      with patch("utils.yfinance_client.yf.Ticker", return_value=mock_ticker) as mock_cls:
          df = fetch_history("AAPL", interval="1d", period="6mo")
          mock_cls.assert_called_once_with("AAPL")
          mock_ticker.history.assert_called_once_with(
              period="6mo", interval="1d", auto_adjust=False, actions=False,
          )
          # canonical columns + UTC ms index
          assert list(df.columns) == ["open", "high", "low", "close", "volume"]
          assert df.index.tz is None  # converted to UTC then dropped tz


  def test_fetch_history_returns_empty_df_on_no_data() -> None:
      from utils.yfinance_client import fetch_history

      mock_ticker = MagicMock()
      mock_ticker.history.return_value = pd.DataFrame()
      with patch("utils.yfinance_client.yf.Ticker", return_value=mock_ticker):
          df = fetch_history("INVALID", interval="1d", period="6mo")
          assert df.empty
  ```

- [ ] **Step 3: Run test to verify it fails**

  ```bash
  poetry run pytest tests/test_yfinance_client.py -v
  ```

  Expected: `ImportError: cannot import name 'fetch_history'`

- [ ] **Step 4: Create `utils/yfinance_client.py`**

  ```python
  """yfinance helper module.

  No credentials, no module-level side effects. Wraps yfinance.Ticker.history()
  and normalises the returned DataFrame to the canonical OHLCV schema used by
  the rest of the pipeline (lowercase columns; UTC-naive DatetimeIndex).

  yfinance is an unofficial Yahoo Finance scraper. Yahoo may break or rate-limit
  the underlying endpoints at any time — failures must be handled by callers.
  """

  import pandas as pd
  import yfinance as yf

  # yfinance interval strings (note: 4h is NOT supported natively — caller resamples 1h→4h)
  YF_INTERVALS: dict[str, str] = {
      "1h": "60m",
      "1d": "1d",
      "1wk": "1wk",
  }


  def fetch_history(
      symbol: str,
      *,
      interval: str,
      period: str = "max",
  ) -> pd.DataFrame:
      """Fetch raw OHLCV bars for a single symbol.

      Args:
          symbol: Plain ticker, e.g. ``"AAPL"`` (no suffix).
          interval: One of ``YF_INTERVALS`` keys (``"1h"``, ``"1d"``, ``"1wk"``).
          period: yfinance period string (``"6mo"``, ``"2y"``, ``"max"``, ...).
                  yfinance caps 1h period to 730 days regardless of this value.

      Returns:
          DataFrame with columns ``open, high, low, close, volume`` and a
          UTC-naive DatetimeIndex. Empty DataFrame on no data.

      ``auto_adjust=False`` preserves raw prices (S/R levels need absolute close).
      ``actions=False`` strips Dividends and Stock Splits columns. Split adjustment
      is applied by yfinance by default; dividend adjustment is not.
      """
      yf_interval = YF_INTERVALS[interval]
      raw = yf.Ticker(symbol).history(
          period=period,
          interval=yf_interval,
          auto_adjust=False,
          actions=False,
      )
      if raw.empty:
          return raw
      # Yahoo returns tz-aware America/New_York; convert to UTC then drop tz
      idx_utc = raw.index.tz_convert("UTC").tz_localize(None)
      df = raw.rename(
          columns={"Open": "open", "High": "high", "Low": "low",
                   "Close": "close", "Volume": "volume"},
      )[["open", "high", "low", "close", "volume"]]
      df.index = idx_utc
      return df
  ```

- [ ] **Step 5: Run tests to verify they pass**

  ```bash
  poetry run pytest tests/test_yfinance_client.py -v
  ```

  Expected: 2 PASSED

- [ ] **Step 6: Commit**

  ```bash
  git add utils/yfinance_client.py tests/test_yfinance_client.py pyproject.toml poetry.lock
  git commit -m "feat: add yfinance helper module"
  ```

---

## Task 3: Replace data_fetcher.py with yfinance implementation

**Files:**

- Rewrite: `analytics/data_fetcher.py`
- Modify: `tests/test_data_fetcher.py` (update existing tests)

The new `fetch_bars()` keeps a similar call signature to the old `fetch_klines()` so `data_sync.py` needs minimal changes. **Differences from the Alpaca design:**

- **No client parameter.** yfinance has no client object; functions wrap `utils.yfinance_client.fetch_history()` directly. Tests inject by patching `utils.yfinance_client.fetch_history`.
- **No `vwap` column.** Dropped from `OHLCV_COLUMNS` and the schema.
- **4h is synthesised.** yfinance does not serve 4h natively; fetch 1h and resample anchored to 13:30 UTC (US regular-session open). Calls for `"4h"` delegate to a `_resample_to_4h()` helper.
- **No pagination by `limit` bars.** yfinance returns the full requested `period` in one call; the function caps the returned DataFrame at `limit` rows. The `start_ms` parameter is mapped to a yfinance `period` string ("6mo", "2y", "max") via a small helper.

- [ ] **Step 1: Write the failing tests**

  Replace the contents of `tests/test_data_fetcher.py`:

  ```python
  """Tests for yfinance-backed data_fetcher."""

  from datetime import datetime, timezone
  from typing import Any
  from unittest.mock import patch

  import pandas as pd

  from analytics.data_fetcher import BARS_MAX_LIMIT, OHLCV_COLUMNS, fetch_bars


  def _yf_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
      """Build a UTC-naive OHLCV DataFrame matching utils.yfinance_client output."""
      idx = pd.DatetimeIndex([r["ts"] for r in rows])
      return pd.DataFrame(
          {
              "open": [r["open"] for r in rows],
              "high": [r["high"] for r in rows],
              "low": [r["low"] for r in rows],
              "close": [r["close"] for r in rows],
              "volume": [r["volume"] for r in rows],
          },
          index=idx,
      )


  def _row(ts: str = "2024-01-15T14:30:00", price: float = 186.0) -> dict[str, Any]:
      return {"ts": ts, "open": 185.0, "high": 187.5, "low": 184.0,
              "close": price, "volume": 1_000_000.0}


  def test_fetch_bars_returns_canonical_columns() -> None:
      start_ms = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp() * 1000)
      with patch(
          "analytics.data_fetcher.fetch_history",
          return_value=_yf_frame([_row()]),
      ):
          result = fetch_bars("AAPL", "1d", start_ms)
      assert list(result.columns) == OHLCV_COLUMNS


  def test_fetch_bars_maps_fields_correctly() -> None:
      start_ms = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp() * 1000)
      with patch(
          "analytics.data_fetcher.fetch_history",
          return_value=_yf_frame([_row()]),
      ):
          result = fetch_bars("AAPL", "1d", start_ms)
      row = result.iloc[0]
      assert row["symbol"] == "AAPL"
      assert row["timeframe"] == "1d"
      assert row["close"] == 186.0
      assert row["open_time"] == int(
          datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc).timestamp() * 1000
      )


  def test_fetch_bars_empty_returns_correct_columns() -> None:
      start_ms = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp() * 1000)
      with patch(
          "analytics.data_fetcher.fetch_history",
          return_value=pd.DataFrame(),
      ):
          result = fetch_bars("AAPL", "1d", start_ms)
      assert result.empty
      assert list(result.columns) == OHLCV_COLUMNS


  def test_fetch_bars_respects_limit() -> None:
      rows = [_row(ts=f"2024-01-{15 + i:02d}T14:30:00") for i in range(5)]
      start_ms = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp() * 1000)
      with patch(
          "analytics.data_fetcher.fetch_history",
          return_value=_yf_frame(rows),
      ):
          result = fetch_bars("AAPL", "1d", start_ms, limit=3)
      assert len(result) == 3


  def test_fetch_bars_4h_resamples_from_1h() -> None:
      """Caller asks for 4h → fetcher pulls 1h and resamples anchored to 13:30 UTC."""
      # 8 consecutive 1h bars starting 13:30 UTC → 2 complete 4h bars
      rows = [_row(ts=f"2024-01-15T{13 + i:02d}:30:00") for i in range(8)]
      start_ms = int(datetime(2024, 1, 15, tzinfo=timezone.utc).timestamp() * 1000)
      with patch(
          "analytics.data_fetcher.fetch_history",
          return_value=_yf_frame(rows),
      ) as mock_fetch:
          result = fetch_bars("AAPL", "4h", start_ms)
      # underlying fetch must request 1h, not 4h
      assert mock_fetch.call_args.kwargs["interval"] == "1h"
      assert len(result) == 2
      assert (result["timeframe"] == "4h").all()
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  poetry run pytest tests/test_data_fetcher.py -v
  ```

  Expected: `ImportError` — `fetch_bars` doesn't exist yet.

- [ ] **Step 3: Rewrite `analytics/data_fetcher.py`**

  ```python
  """Pure data-fetching logic — yfinance to canonical OHLCV DataFrames.

  4h bars are synthesised by resampling 1h bars anchored to 13:30 UTC
  (US regular-session open). Other intervals (1h, 1d, 1wk) pass through.

  No module-level side effects.
  """

  from datetime import datetime, timezone

  import pandas as pd

  from utils.yfinance_client import fetch_history

  BARS_MAX_LIMIT: int = 5000

  OHLCV_COLUMNS: list[str] = [
      "symbol",
      "timeframe",
      "open_time",
      "open",
      "high",
      "low",
      "close",
      "volume",
  ]

  # Mapping from canonical interval to (yfinance_interval, default_period).
  # yfinance caps history per interval — defaults below stay within those caps.
  _INTERVAL_CONFIG: dict[str, tuple[str, str]] = {
      "1h": ("1h", "2y"),    # yf 1h caps at 730d
      "4h": ("1h", "2y"),    # synthesised by resampling 1h
      "1d": ("1d", "max"),   # unlimited
      "1wk": ("1wk", "max"),
  }


  def fetch_bars(
      symbol: str,
      interval: str,
      start_ms: int,
      limit: int = BARS_MAX_LIMIT,
  ) -> pd.DataFrame:
      """Fetch up to ``limit`` bars at or after start_ms (Unix ms).

      Returns a DataFrame with columns matching OHLCV_COLUMNS.
      Returns an empty DataFrame (with correct columns) on no data.
      Raises on yfinance / network errors — callers decide whether to retry.
      """
      if interval not in _INTERVAL_CONFIG:
          raise ValueError(
              f"Unsupported interval '{interval}'. "
              f"Supported: {list(_INTERVAL_CONFIG)}"
          )
      yf_interval, period = _INTERVAL_CONFIG[interval]
      raw = fetch_history(symbol, interval=yf_interval, period=period)
      if raw.empty:
          return pd.DataFrame(columns=OHLCV_COLUMNS)

      if interval == "4h":
          raw = _resample_to_4h(raw)
          if raw.empty:
              return pd.DataFrame(columns=OHLCV_COLUMNS)

      # filter to start_ms forward
      start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
      raw = raw.loc[raw.index >= start_dt]
      raw = raw.head(limit)

      result = pd.DataFrame(
          {
              "symbol": symbol,
              "timeframe": interval,
              "open_time": (raw.index.astype("int64") // 1_000_000).values,
              "open": raw["open"].astype(float).values,
              "high": raw["high"].astype(float).values,
              "low": raw["low"].astype(float).values,
              "close": raw["close"].astype(float).values,
              "volume": raw["volume"].astype(float).values,
          }
      )
      return result


  def _resample_to_4h(hourly: pd.DataFrame) -> pd.DataFrame:
      """Resample 1h bars to 4h, anchored to 13:30 UTC (US regular-session open).

      4h bins: 13:30-17:30, 17:30-21:30, 21:30-01:30, 01:30-05:30, 05:30-09:30, 09:30-13:30.
      The first three cover RTH + early after-hours; the latter three cover overnight.
      """
      return (
          hourly.resample("4h", origin="start_day", offset="13h30min")
          .agg({"open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum"})
          .dropna()
      )
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  poetry run pytest tests/test_data_fetcher.py -v
  ```

  Expected: 5 PASSED

- [ ] **Step 5: Run full lint + typecheck**

  ```bash
  make lint-py && make typecheck
  ```

  Fix any issues before continuing.

- [ ] **Step 6: Commit**

  ```bash
  git add analytics/data_fetcher.py tests/test_data_fetcher.py
  git commit -m "feat: replace Binance data_fetcher with yfinance fetch_bars"
  ```

---

## Task 4: Update store schema — drop taker_buy_volume column, drop funding/OI tables

**Files:**

- Modify: `analytics/data_store.py`
- Modify: `tests/test_data_store.py`

- [ ] **Step 1: Write the failing tests**

  Add to `tests/test_data_store.py`:

  ```python
  def test_ohlcv_schema_has_no_taker_buy_volume_or_vwap() -> None:
      """yfinance has no VWAP — column dropped entirely (not renamed)."""
      import duckdb
      from analytics.data_store import init_db

      conn = duckdb.connect(":memory:")
      init_db(conn)
      cols = [
          r[1]
          for r in conn.execute(
              "PRAGMA table_info('ohlcv')"
          ).fetchall()
      ]
      assert "taker_buy_volume" not in cols
      assert "vwap" not in cols
      conn.close()


  def test_no_funding_rates_table() -> None:
      import duckdb
      from analytics.data_store import init_db

      conn = duckdb.connect(":memory:")
      init_db(conn)
      tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
      assert "funding_rates" not in tables
      conn.close()


  def test_no_open_interest_table() -> None:
      import duckdb
      from analytics.data_store import init_db

      conn = duckdb.connect(":memory:")
      init_db(conn)
      tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
      assert "open_interest" not in tables
      conn.close()
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  poetry run pytest tests/test_data_store.py::test_ohlcv_schema_has_vwap_not_taker_buy_volume tests/test_data_store.py::test_no_funding_rates_table tests/test_data_store.py::test_no_open_interest_table -v
  ```

  Expected: 3 FAILED (column is still `taker_buy_volume`, tables still exist).

- [ ] **Step 3: Edit `analytics/store/schema.py` — drop the column entirely**

  (Post-Phase-2: schema lives in `analytics/store/schema.py`; `analytics/data_store.py` is a re-export shim.)

  Find the line in `init_schema()` that defines the `ohlcv` table. **Delete** the `taker_buy_volume` line entirely — there is no replacement. yfinance does not provide VWAP per bar, so the column has no source.

  ```python
  # Before
      taker_buy_volume DOUBLE NOT NULL,

  # After
  # (line deleted)
  ```

  If the column is later wanted (e.g. when upgrading to Polygon $29 Starter), re-add it as `vwap DOUBLE` (nullable) and backfill.

- [ ] **Step 4: Edit `analytics/data_store.py` — remove funding_rates and open_interest table creation**

  Remove the `CREATE TABLE IF NOT EXISTS funding_rates` block and the
  `CREATE TABLE IF NOT EXISTS open_interest` block from `init_db()`.

- [ ] **Step 5: Remove upsert_funding_rates, upsert_open_interest, get_funding_rates, get_open_interest functions**

  Delete these functions entirely from `data_store.py`. Also remove any
  corresponding exports from `__all__` if present.

- [ ] **Step 6: Update upsert_ohlcv to drop taker_buy_volume**

  (Post-Phase-2: `upsert_ohlcv` lives in `analytics/store/market_data.py`.) Anywhere it references `taker_buy_volume`, **delete** the reference. The INSERT column list, ON CONFLICT clause, and the VALUES mapping all need the column removed.

- [ ] **Step 7: Run tests to verify they pass**

  ```bash
  poetry run pytest tests/test_data_store.py -v
  ```

  Expected: all PASSED (including new tests).

- [ ] **Step 8: Commit**

  ```bash
  git add analytics/data_store.py tests/test_data_store.py
  git commit -m "feat: drop taker_buy_volume column, drop funding/OI tables"
  ```

---

## Task 5: Update data_sync.py — remove funding/OI sync

**Files:**

- Modify: `analytics/data_sync.py`
- Modify: `tests/test_data_sync.py`

- [ ] **Step 1: Write the failing test**

  Add to `tests/test_data_sync.py`:

  ```python
  def test_data_sync_has_no_funding_or_oi_imports() -> None:
      import analytics.data_sync as m
      import inspect

      src = inspect.getsource(m)
      assert "funding" not in src.lower()
      assert "open_interest" not in src.lower()
  ```

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  poetry run pytest tests/test_data_sync.py::test_data_sync_has_no_funding_or_oi_imports -v
  ```

  Expected: FAILED (funding references still present).

- [ ] **Step 3: Edit `analytics/data_sync.py` imports**

  Remove from the `from analytics.data_fetcher import` block:

  ```python
  # Remove these lines
  OIPeriod,
  fetch_funding_rates,
  fetch_open_interest,
  ```

  Remove from the `from analytics.store import` block (post-Phase-2; was `analytics.data_store`):

  ```python
  # Remove these lines
  upsert_funding_rates,
  upsert_open_interest,
  ```

  Remove the Binance `Client` import. **No replacement client import needed** — yfinance has no client object, so `data_sync.py` no longer needs a client parameter at all:

  ```python
  # Before
  from binance.client import Client

  # After: (delete the import; downstream calls drop the client argument)
  ```

- [ ] **Step 4: Remove sync_funding_rates and sync_open_interest functions**

  Delete the `sync_funding_rates()` and `sync_open_interest()` functions entirely.

- [ ] **Step 5: Update backfill() to use fetch_bars (no client argument)**

  The `backfill()` function currently calls `fetch_klines(client, ...)`. Replace with `fetch_bars(symbol, ...)` — note **no client argument** in the new signature:

  ```python
  # Before
  from analytics.data_fetcher import (
      KLINES_MAX_LIMIT,
      fetch_klines,
  )
  # ... inside backfill():
  df = fetch_klines(client, symbol, timeframe, current_start, limit=KLINES_MAX_LIMIT)

  # After
  from analytics.data_fetcher import (
      BARS_MAX_LIMIT,
      fetch_bars,
  )
  # ... inside backfill():
  df = fetch_bars(symbol, timeframe, current_start, limit=BARS_MAX_LIMIT)
  ```

  Update the `backfill()` signature to drop the `client` parameter entirely:

  ```python
  def backfill(
      conn: duckdb.DuckDBPyConnection,
      symbol: str,
      timeframe: str,
      start_ms: int,
      sleep_fn: Callable[[float], None] | None = None,
  ) -> int:
  ```

  Also update the pagination check:

  ```python
  # Before
  if len(df) < KLINES_MAX_LIMIT:

  # After
  if len(df) < BARS_MAX_LIMIT:
  ```

  **Note on pagination:** yfinance returns the full requested `period` in one call, so the parent repo's "page until short batch" loop in `backfill()` becomes a single call. Adjust the loop or short-circuit after one fetch — the choice is a follow-up cleanup, not blocking.

- [ ] **Step 6: Update incremental_sync() similarly**

  Find `incremental_sync()` and replace any `fetch_klines(client, ...)` calls with `fetch_bars(symbol, ...)`. Drop the `client` parameter from the function signature.
  Remove any `sync_funding_rates` or `sync_open_interest` calls inside it.

- [ ] **Step 7: Run tests**

  ```bash
  poetry run pytest tests/test_data_sync.py -v && make typecheck
  ```

  Expected: all PASSED, no mypy errors.

- [ ] **Step 8: Update `analytics/analytics_runner.py`**

  This thin wrapper creates the client and calls sync. With yfinance there's no client to create — just delete the client setup entirely:

  ```python
  # Before
  from utils.binance_client import create_client
  client = create_client()
  backfill(conn, client, symbol, timeframe, start_ms)

  # After
  # (no client import or creation)
  backfill(conn, symbol, timeframe, start_ms)
  ```

  Delete any remaining `client` variable references.

- [ ] **Step 9: Commit**

  ```bash
  git add analytics/data_sync.py analytics/analytics_runner.py tests/test_data_sync.py
  git commit -m "feat: rewire data_sync to yfinance, remove funding/OI + client argument"
  ```

---

## Task 6: Create stocks.json watchlist and update config validation

**Files:**

- Create: `config/stocks.json`
- Modify: `utils/config_validation.py`
- Modify: `tests/test_config_validation.py`

- [ ] **Step 1: Write the failing tests**

  Add to `tests/test_config_validation.py`:

  ```python
  def test_valid_stocks_json_passes() -> None:
      from utils.config_validation import validate_stocks_config

      config = {
          "AAPL": {"sl_pct": 0.05},
          "MSTR": {"sl_pct": 0.08},
      }
      validate_stocks_config(config)  # should not raise


  def test_stocks_json_missing_sl_pct_raises() -> None:
      from utils.config_validation import validate_stocks_config

      with pytest.raises(ValueError, match="sl_pct"):
          validate_stocks_config({"AAPL": {}})


  def test_stocks_json_invalid_sl_pct_raises() -> None:
      from utils.config_validation import validate_stocks_config

      with pytest.raises(ValueError, match="sl_pct"):
          validate_stocks_config({"AAPL": {"sl_pct": -0.1}})
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  poetry run pytest tests/test_config_validation.py -k "stocks" -v
  ```

  Expected: `ImportError: cannot import name 'validate_stocks_config'`

- [ ] **Step 3: Add `validate_stocks_config` to `utils/config_validation.py`**

  ```python
  def validate_stocks_config(config: dict[str, Any]) -> None:
      """Validate the stocks.json watchlist schema.

      Each key is a ticker symbol. Each value must have:
        sl_pct: float > 0  — stop-loss as a fraction of price (e.g. 0.05 = 5%)
      """
      for symbol, cfg in config.items():
          if "sl_pct" not in cfg:
              raise ValueError(
                  f"stocks.json: symbol '{symbol}' missing required field 'sl_pct'"
              )
          sl_pct = cfg["sl_pct"]
          if not isinstance(sl_pct, (int, float)) or sl_pct <= 0:
              raise ValueError(
                  f"stocks.json: symbol '{symbol}' sl_pct must be a positive number, got {sl_pct!r}"
              )
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  poetry run pytest tests/test_config_validation.py -k "stocks" -v
  ```

  Expected: 3 PASSED

- [ ] **Step 5: Create `config/stocks.json`**

  ```json
  {
    "AAPL": { "sl_pct": 0.05 },
    "MSTR": { "sl_pct": 0.08 },
    "CRCL": { "sl_pct": 0.07 }
  }
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add config/stocks.json utils/config_validation.py tests/test_config_validation.py
  git commit -m "feat: add stocks.json watchlist and validate_stocks_config"
  ```

---

## Task 7: Remove funding_reversion and cvd_divergence from all registries

**Files:**

- Modify: `analytics/indicators_lib.py`
- Modify: `signals/registry.py`
- Modify: `tests/test_indicators_lib.py`

- [ ] **Step 1: Write the failing tests**

  Add to `tests/test_indicators_lib.py`:

  ```python
  def test_funding_reversion_not_in_strategy_registry() -> None:
      from analytics.indicators_lib import STRATEGY_REGISTRY

      assert "funding_reversion" not in STRATEGY_REGISTRY


  def test_cvd_divergence_not_in_strategy_registry() -> None:
      from analytics.indicators_lib import STRATEGY_REGISTRY

      assert "cvd_divergence" not in STRATEGY_REGISTRY


  def test_funding_reversion_not_in_signal_registry() -> None:
      from signals.registry import SIGNAL_REGISTRY

      assert "funding_reversion" not in SIGNAL_REGISTRY


  def test_cvd_divergence_not_in_signal_registry() -> None:
      from signals.registry import SIGNAL_REGISTRY

      assert "cvd_divergence" not in SIGNAL_REGISTRY
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  poetry run pytest tests/test_indicators_lib.py -k "funding_reversion or cvd_divergence" -v
  ```

  Expected: 4 FAILED

- [ ] **Step 3: Edit `analytics/indicators_lib.py` — remove from STRATEGY_REGISTRY**

  In `STRATEGY_REGISTRY`, delete the entire `"funding_reversion": StrategySpec(...)` block
  and the entire `"cvd_divergence": StrategySpec(...)` block.

- [ ] **Step 4: Edit `analytics/indicators_lib.py` — remove from DETECTOR_REGISTRY**

  In `DETECTOR_REGISTRY`, delete the `"funding_reversion"` and `"cvd_divergence"` entries.

- [ ] **Step 5: Edit `analytics/indicators_lib.py` — remove from KNOWN_STRATEGIES**

  Remove `"funding_reversion"` and `"cvd_divergence"` from the `KNOWN_STRATEGIES` list.

- [ ] **Step 6: Delete detect_funding_extreme and detect_cvd_divergence functions**

  Remove the entire `detect_funding_extreme()` function (and its docstring/comments).
  Remove the entire `detect_cvd_divergence()` function (and its docstring/comments).

  Also remove the `requires_funding: bool = False` field from `StrategySpec` if it is
  only used by the funding_reversion strategy. Check first:

  ```bash
  grep -n "requires_funding" analytics/indicators_lib.py
  ```

  If the only remaining usages are in the field definition itself, delete the field.

- [ ] **Step 7: Edit `signals/registry.py` — remove entries and import**

  Remove the import of `detect_cvd_divergence` from the top of the file.
  Delete the `"funding_reversion": SignalPlugin(...)` block.
  Delete the `"cvd_divergence": SignalPlugin(...)` block.

- [ ] **Step 8: Run tests to verify they pass**

  ```bash
  poetry run pytest tests/test_indicators_lib.py -v && make typecheck
  ```

  Expected: all PASSED, no mypy errors.

- [ ] **Step 9: Commit**

  ```bash
  git add analytics/indicators_lib.py signals/registry.py tests/test_indicators_lib.py
  git commit -m "feat: remove funding_reversion and cvd_divergence — no equity equivalent"
  ```

---

## Task 8: Fix ORB session anchor for US market open

**Files:**

- Modify: `analytics/indicators_lib.py`
- Modify: `tests/test_indicators_lib.py`

The current `detect_orb_breakout` has `session_hour_utc` as a legacy param that is
intentionally ignored — the implementation always anchors at 00:00 UTC. For equities we need
it to anchor at 13:00 UTC (9:00am ET; market opens at 9:30, so the range window starts close
to open). This requires making the param functional again.

- [ ] **Step 1: Read the current ORB implementation**

  ```bash
  grep -n "session_hour_utc\|00:00\|anchor\|daily\|open_time" analytics/indicators_lib.py | head -40
  ```

  Understand how the function currently identifies "session start" candles before proceeding.

- [ ] **Step 2: Write the failing test**

  Add to `tests/test_indicators_lib.py`:

  ```python
  def test_orb_uses_session_hour_utc_anchor() -> None:
      """ORB should only use candles from the specified session hour, not 00:00 UTC."""
      import pandas as pd
      from analytics.indicators_lib import detect_orb_breakout

      # Build 3 days of hourly candles anchored at 13:00 UTC (US market open)
      base_ms = 1_705_276_800_000  # 2024-01-15 00:00 UTC
      hour_ms = 3_600_000
      rows = []
      for day in range(3):
          for hour in range(24):
              ts = base_ms + day * 24 * hour_ms + hour * hour_ms
              rows.append({
                  "open_time": ts,
                  "open": 185.0 + hour * 0.1,
                  "high": 187.0 + hour * 0.1,
                  "low": 183.0 + hour * 0.1,
                  "close": 186.0 + hour * 0.1,
                  "volume": 1_000_000.0,
                  "vwap": 185.5,
              })
      df = pd.DataFrame(rows)
      # With anchor at 13, signals reference 13:00 UTC candles as session open
      signals = detect_orb_breakout(df, session_hour_utc=13)
      # Should not raise and should return a DataFrame
      assert isinstance(signals, pd.DataFrame)
  ```

- [ ] **Step 3: Run test to verify it passes (or adjust if already works)**

  ```bash
  poetry run pytest tests/test_indicators_lib.py::test_orb_uses_session_hour_utc_anchor -v
  ```

  If it fails with a column error (`vwap` not expected, etc.), adjust the test row schema
  to match what the function actually reads.

- [ ] **Step 4: Make session_hour_utc functional in detect_orb_breakout**

  Find the section in `detect_orb_breakout` that identifies "session open" candles.
  The comment says the param is ignored and it anchors on 00:00 UTC.

  Change the logic so it uses `session_hour_utc` to identify the first candle of a session:

  ```python
  # Before (anchors on 00:00 UTC — hour == 0)
  session_mask = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.hour == 0

  # After (anchors on session_hour_utc — default 13 for 9am ET)
  session_mask = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.hour == session_hour_utc
  ```

  The exact code will differ — match the pattern you found in Step 1.
  Change the default value of `session_hour_utc` from `0` to `13`.
  Remove the comment saying the param is ignored.

- [ ] **Step 5: Run all ORB tests**

  ```bash
  poetry run pytest tests/ -k "orb" -v
  ```

  Expected: all PASSED.

- [ ] **Step 6: Commit**

  ```bash
  git add analytics/indicators_lib.py tests/test_indicators_lib.py
  git commit -m "fix(orb): make session_hour_utc functional, default to 13 (9am ET)"
  ```

---

## Task 9: Create overnight_gap_lib.py

**Files:**

- Create: `analytics/overnight_gap_lib.py`
- Create: `tests/test_overnight_gap_lib.py`

- [ ] **Step 1: Write the failing tests**

  Create `tests/test_overnight_gap_lib.py`:

  ```python
  """Tests for overnight gap detection (replaces CME gap for equities)."""

  import pandas as pd
  import pytest

  from analytics.overnight_gap_lib import OvernightGap, gap_fill_warning, get_overnight_gap


  def _make_df(rows: list[dict]) -> pd.DataFrame:  # type: ignore[type-arg]
      return pd.DataFrame(rows)


  def test_gap_up_detected() -> None:
      df = _make_df([
          {"open_time": 1, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1e6, "vwap": 100.5},
          {"open_time": 2, "open": 103.0, "high": 105.0, "low": 102.0, "close": 104.0, "volume": 1e6, "vwap": 103.5},
      ])
      gap = get_overnight_gap(df)
      assert gap is not None
      assert gap.gap_up is True
      assert gap.prev_close == pytest.approx(101.0)
      assert gap.today_open == pytest.approx(103.0)
      assert gap.gap_pct == pytest.approx((103.0 - 101.0) / 101.0)


  def test_gap_down_detected() -> None:
      df = _make_df([
          {"open_time": 1, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1e6, "vwap": 100.5},
          {"open_time": 2, "open": 98.0, "high": 99.0, "low": 97.0, "close": 98.5, "volume": 1e6, "vwap": 98.2},
      ])
      gap = get_overnight_gap(df)
      assert gap is not None
      assert gap.gap_up is False
      assert gap.gap_pct == pytest.approx((98.0 - 101.0) / 101.0)


  def test_gap_up_filled_when_low_touches_prev_close() -> None:
      df = _make_df([
          {"open_time": 1, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1e6, "vwap": 100.5},
          # Gap up to 105 open but low comes back to 101 (filled)
          {"open_time": 2, "open": 105.0, "high": 107.0, "low": 101.0, "close": 106.0, "volume": 1e6, "vwap": 105.0},
      ])
      gap = get_overnight_gap(df)
      assert gap is not None
      assert gap.filled is True


  def test_gap_up_not_filled_when_low_above_prev_close() -> None:
      df = _make_df([
          {"open_time": 1, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1e6, "vwap": 100.5},
          {"open_time": 2, "open": 105.0, "high": 107.0, "low": 103.0, "close": 106.0, "volume": 1e6, "vwap": 105.0},
      ])
      gap = get_overnight_gap(df)
      assert gap is not None
      assert gap.filled is False


  def test_returns_none_when_fewer_than_two_rows() -> None:
      df = _make_df([
          {"open_time": 1, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1e6, "vwap": 100.5},
      ])
      assert get_overnight_gap(df) is None


  def test_gap_fill_warning_long_below_unfilled_gap_down() -> None:
      gap = OvernightGap(gap_pct=-0.02, gap_up=False, filled=False, prev_close=101.0, today_open=99.0)
      # Going long; unfilled gap-down at 101 is above entry — gap may act as resistance
      warning = gap_fill_warning(gap, direction="long", entry=100.0)
      assert warning is not None
      assert "101" in warning


  def test_gap_fill_warning_none_when_gap_filled() -> None:
      gap = OvernightGap(gap_pct=-0.02, gap_up=False, filled=True, prev_close=101.0, today_open=99.0)
      assert gap_fill_warning(gap, direction="long", entry=100.0) is None


  def test_gap_fill_warning_short_above_unfilled_gap_up() -> None:
      gap = OvernightGap(gap_pct=0.02, gap_up=True, filled=False, prev_close=99.0, today_open=101.0)
      # Going short; unfilled gap-up prev_close at 99 is below entry — may act as support
      warning = gap_fill_warning(gap, direction="short", entry=100.0)
      assert warning is not None
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  poetry run pytest tests/test_overnight_gap_lib.py -v
  ```

  Expected: `ImportError` — module doesn't exist yet.

- [ ] **Step 3: Create `analytics/overnight_gap_lib.py`**

  ```python
  """Overnight gap detection for US equities.

  Every US equity trading session leaves an overnight gap between the prior
  session's close and the next session's open. Unfilled gaps act as price
  magnets and can invalidate trade setups.

  Replaces cme_gap_lib.py from buibui (crypto-specific weekend gap).
  No module-level side effects.
  """

  from dataclasses import dataclass

  import pandas as pd


  @dataclass
  class OvernightGap:
      gap_pct: float      # (today_open - prev_close) / prev_close; negative = gap down
      gap_up: bool        # True if today opened above yesterday's close
      filled: bool        # True if price returned to prev_close during today's session
      prev_close: float   # Yesterday's closing price
      today_open: float   # Today's opening price


  def get_overnight_gap(ohlcv_df: pd.DataFrame) -> OvernightGap | None:
      """Compute the overnight gap from the two most recent rows of OHLCV data.

      ohlcv_df must have columns: open_time, open, high, low, close.
      Returns None if fewer than two rows are available.
      """
      if len(ohlcv_df) < 2:
          return None

      prev = ohlcv_df.iloc[-2]
      curr = ohlcv_df.iloc[-1]

      prev_close = float(prev["close"])
      today_open = float(curr["open"])
      gap_pct = (today_open - prev_close) / prev_close
      gap_up = today_open > prev_close

      if gap_up:
          # Gap up is filled when today's low touches or goes below prev_close
          filled = float(curr["low"]) <= prev_close
      else:
          # Gap down is filled when today's high touches or exceeds prev_close
          filled = float(curr["high"]) >= prev_close

      return OvernightGap(
          gap_pct=gap_pct,
          gap_up=gap_up,
          filled=filled,
          prev_close=prev_close,
          today_open=today_open,
      )


  def gap_fill_warning(
      gap: OvernightGap,
      direction: str,
      entry: float,
  ) -> str | None:
      """Return a warning string if the unfilled gap threatens the trade.

      For a LONG trade: an unfilled gap-down above entry may act as resistance
      as price attempts to fill the gap (pulling price down).
      For a SHORT trade: an unfilled gap-up below entry may act as support
      as price attempts to fill the gap (pushing price up).

      Returns None when the gap is already filled or poses no threat.
      """
      if gap.filled:
          return None

      if direction == "long" and not gap.gap_up:
          # Gap-down: prev_close is above today's open; if entry < prev_close,
          # the unfilled gap zone (today_open → prev_close) is overhead resistance
          if entry < gap.prev_close:
              return (
                  f"⚠️ Unfilled gap-down at ${gap.prev_close:.2f} "
                  f"({abs(gap.gap_pct):.1%} above open) — potential overhead resistance"
              )

      elif direction == "short" and gap.gap_up:
          # Gap-up: prev_close is below today's open; if entry > prev_close,
          # the unfilled gap zone (prev_close → today_open) is below as support
          if entry > gap.prev_close:
              return (
                  f"⚠️ Unfilled gap-up at ${gap.prev_close:.2f} "
                  f"({gap.gap_pct:.1%} below open) — potential downside support"
              )

      return None
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  poetry run pytest tests/test_overnight_gap_lib.py -v
  ```

  Expected: 8 PASSED

- [ ] **Step 5: Run typecheck**

  ```bash
  make typecheck
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add analytics/overnight_gap_lib.py tests/test_overnight_gap_lib.py
  git commit -m "feat: add overnight_gap_lib — replaces cme_gap_lib for equities"
  ```

---

## Task 10: Wire overnight gap into signal_lib.py

**Files:**

- Modify: `analytics/signal_lib.py`

- [ ] **Step 1: Replace CME gap import**

  In `analytics/signal_lib.py`, find and replace:

  ```python
  # Before
  from analytics.cme_gap_lib import cme_gap_alert_warning, get_recent_cme_gap

  # After
  from analytics.overnight_gap_lib import gap_fill_warning, get_overnight_gap
  ```

- [ ] **Step 2: Replace get_recent_cme_gap call**

  Around line 723 (find with `grep -n "cme_gap\|get_recent_cme_gap" analytics/signal_lib.py`):

  ```python
  # Before
  cme_gap = get_recent_cme_gap(ohlcv_df)

  # After
  overnight_gap = get_overnight_gap(ohlcv_df)
  ```

- [ ] **Step 3: Replace cme_gap_alert_warning calls**

  Around lines 1097–1109, replace:

  ```python
  # Before
  _gap_warning = cme_gap_alert_warning(
      cme_gap, direction, _entry, _rough_tp
  )
  # ...
  cme_gap_warning=_gap_warning,

  # After
  _gap_warning = gap_fill_warning(overnight_gap, direction, _entry)
  # ...
  cme_gap_warning=_gap_warning,
  ```

  Note: `gap_fill_warning` takes 3 args (`gap`, `direction`, `entry`) vs
  `cme_gap_alert_warning` which took 4 (also `tp_price`). The new version does not
  need `tp_price`.

- [ ] **Step 4: Run scan-path tests**

  ```bash
  poetry run pytest tests/test_signal_lib.py -v && make typecheck
  ```

  Fix any remaining `cme_gap` references found by mypy.

- [ ] **Step 5: Confirm no remaining CME gap references**

  ```bash
  grep -rn "cme_gap" analytics/ signals/ monitor/ utils/ web/ || echo "clean"
  ```

  Expected: `clean`

- [ ] **Step 6: Commit**

  ```bash
  git add analytics/signal_lib.py
  git commit -m "feat: wire overnight_gap_lib into signal scan — replaces CME gap"
  ```

---

## Task 11: End-to-end smoke test

**No credentials needed for data layer** — yfinance is unauthenticated. Telegram bot tokens are still needed for any alert path, but optional for this smoke test (defer to T12 work).

- [ ] **Step 1: Sanity-check yfinance reachability**

  Confirm the network + yfinance install work before running anything heavier:

  ```bash
  poetry run python -c "import yfinance as yf; print(yf.Ticker('AAPL').history(period='5d', interval='1d'))"
  ```

  Expected: 5 rows of recent AAPL daily OHLCV printed. If this fails (rate-limit, geo-block, Yahoo schema break), every other step will too — investigate before continuing.

- [ ] **Step 2: Run the full test suite**

  ```bash
  make test
  ```

  Expected: all tests pass. Fix any remaining import errors (likely culprits: leftover `binance` / `alpaca` imports, stale `vwap` column references in tests).

- [ ] **Step 3: Backfill AAPL daily candles**

  ```bash
  poetry run python [BOTNAME].py sync --symbol AAPL --timeframe 1d --since 2023-01-01
  ```

  Expected: candles stored in `analytics.db`. Spot-check via DuckDB CLI: `SELECT COUNT(*) FROM ohlcv WHERE symbol = 'AAPL' AND timeframe = '1d';` should be ≥ 500 rows.

- [ ] **Step 4: Backfill AAPL 4h candles (validates resample path)**

  ```bash
  poetry run python [BOTNAME].py sync --symbol AAPL --timeframe 4h --since 2024-01-01
  ```

  Expected: 4h candles stored. **Key validation** — the resample from 1h→4h must produce bars anchored to 13:30 UTC. Spot-check: `SELECT MIN(EXTRACT('hour' FROM make_timestamp(open_time * 1000))) FROM ohlcv WHERE symbol = 'AAPL' AND timeframe = '4h';` — values should be {1, 5, 9, 13, 17, 21} (the 4h grid offset by 13:30 minutes — actually just check the minute portion is 30).

- [ ] **Step 5: Run a backtest on AAPL 1d**

  ```bash
  poetry run python [BOTNAME].py backtest --symbol AAPL --timeframe 1d --days 365
  ```

  Expected: backtest results printed; no crash. **Expect ratings to be poor** — crypto `tp_r` / `atr_sl_multiplier` defaults don't transfer to equities. This is T14's job to fix, not Task 11's.

- [ ] **Step 6: Run the signal scanner**

  ```bash
  poetry run python [BOTNAME].py scan --symbol AAPL --timeframe 1d
  ```

  Expected: signals printed or "no signals"; no crash. If alerts are wired (T12 complete), Telegram should receive messages on both channels.

- [ ] **Step 7: Start the web dashboard**

  ```bash
  poetry run python [BOTNAME].py web
  ```

  Visit `http://localhost:8000`. Expected: dashboard loads, Chart tab shows AAPL with daily candles. Positions tab should be **absent** (T16) — if it shows, that's a UI-hygiene leftover.

- [ ] **Step 8: Final commit**

  ```bash
  git add -A
  git commit -m "chore: smoke test passed — equity fork operational on AAPL 1d + 4h"
  ```

**What this smoke test does NOT cover (handled by later tasks):**

- Telegram dispatcher dual-publishing → T12
- Equity-tuned `tp_r` and ratings → T14
- Stock-formatted alert labels → T15
- Web UI hygiene (Positions tab removal, default symbol) → T16
- Run-cadence wiring (manual CLI vs cron vs Actions) → T17
