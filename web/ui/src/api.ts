// Typed API client — interfaces match real FastAPI response models.

// ── Config ────────────────────────────────────────────────────────────────────

export interface SymbolConfig {
  sl_pct: number;
}

export type ConfigResponse = Record<string, SymbolConfig>;

// ── Strategies ────────────────────────────────────────────────────────────────

export interface ParamSpec {
  name: string;
  param_type: "int" | "float";
  default: number;
  min_val: number;
  max_val: number;
  description: string;
}

export interface StrategySpec {
  name: string;
  description: string;
  confidence: number | Record<string, number>;
  params: ParamSpec[];
  requires_funding: boolean;
  requires_secondary: boolean;
}

export type StrategiesResponse = Record<string, StrategySpec>;

// ── OHLCV ─────────────────────────────────────────────────────────────────────

export interface CandleRow {
  open_time: number; // Unix ms
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface OhlcvResponse {
  candles: CandleRow[];
}

// ── Fibonacci ─────────────────────────────────────────────────────────────────

export interface FibLevel {
  label: string;
  price: number;
  golden: boolean;
}

export interface FibResponse {
  swing_low: number;
  swing_high: number;
  swing_start_ms: number;
  levels: FibLevel[];
}

// ── Signals ───────────────────────────────────────────────────────────────────

export interface SignalRow {
  open_time: number;
  direction: string;
  strategy: string;
  reason: string;
  sl_price: number;
  entry_price: number | null;
  confidence: number;
  context: string;
}

export interface SignalsResponse {
  signals: SignalRow[];
}

// ── Backtest ──────────────────────────────────────────────────────────────────

export interface BacktestRunSummary {
  run_id: string;
  symbol: string;
  timeframe: string;
  strategy: string;
  days: number;
  sl_pct: number;
  tp_r: number;
  fee_pct: number;
  day_filter: string;
  adr_suppress_threshold: number | null;
  closed_trades: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
  avg_r: number;
  total_r: number;
  max_drawdown_r: number;
  recovery_factor: number | null;
  sweep_id: string | null;
  run_at_ms: number;
  long_closed_trades: number | null;
  long_win_count: number | null;
  long_win_rate: number | null;
  long_avg_r: number | null;
  long_total_r: number | null;
  short_closed_trades: number | null;
  short_win_count: number | null;
  short_win_rate: number | null;
  short_avg_r: number | null;
  short_total_r: number | null;
  stars: number | null;
  long_stars: number | null;
  short_stars: number | null;
}

export interface TradeModel {
  signal_time: number;
  entry_time: number;
  entry_price: number;
  direction: string;
  sl_price: number;
  tp_price: number;
  exit_time: number | null;
  exit_price: number | null;
  outcome: string;
  pnl_r: number | null;
}

export interface BacktestResponse {
  symbol: string;
  timeframe: string;
  strategy: string;
  total_trades: number;
  closed_trades: number;
  win_count: number;
  loss_count: number;
  win_rate: number;
  avg_r: number;
  total_r: number;
  max_drawdown_r: number;
  recovery_factor: number;
  long_closed_trades: number;
  long_win_count: number;
  long_win_rate: number | null;
  long_avg_r: number | null;
  long_total_r: number | null;
  short_closed_trades: number;
  short_win_count: number;
  short_win_rate: number | null;
  short_avg_r: number | null;
  short_total_r: number | null;
  trades: TradeModel[];
}

// ── Core fetch helper ─────────────────────────────────────────────────────────

const TOKEN = (import.meta.env.VITE_API_TOKEN as string | undefined) ?? "";

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}),
    ...(options.headers as Record<string, string> | undefined),
  };
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    const text = await res.text();
    let detail = text;
    try {
      const json = JSON.parse(text) as { detail?: string };
      if (json.detail) detail = json.detail;
    } catch {
      /* not JSON — use raw text */
    }
    throw new Error(`API ${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

// ── Named helpers ─────────────────────────────────────────────────────────────

// ── Active Config ─────────────────────────────────────────────────────────────

export interface StrategyParamsModel {
  tp_r: number | null;
  sl_pct: number | null;
  tp_r_per_tf: Record<string, number>;
}

export interface ActiveConfigResponse {
  config_name: string | null;
  symbols: string[] | null;
  timeframes: string[];
  strategies: string[] | null;
  day_filter: string;
  tp_r: number;
  sl_pct: number;
  fee_pct: number;
  min_sl_pct: number;
  adr_suppress_threshold: number | null;
  strategy_params: Record<string, StrategyParamsModel>;
  min_trades: number;
  min_trades_per_tf: Record<string, number>;
}

export const getConfig = () => apiFetch<ConfigResponse>("/api/config");
export const getStrategies = (configName?: string | null) => {
  const path = configName ? `/api/strategies?config=${encodeURIComponent(configName)}` : "/api/strategies";
  return apiFetch<StrategiesResponse>(path);
};
export const getActiveConfig = () =>
  apiFetch<ActiveConfigResponse>("/api/active-config");

export interface UniversePolicyResponse {
  scope: string;
  as_of: string;
  survivorship_note: string;
  n_symbols: number | null;
}

export const getUniversePolicy = () =>
  apiFetch<UniversePolicyResponse>("/api/universe-policy");

export const getOhlcv = (params: {
  symbol: string;
  timeframe: string;
  start_ms: number;
  end_ms: number;
}) => {
  const q = new URLSearchParams({
    symbol: params.symbol,
    timeframe: params.timeframe,
    start_ms: String(params.start_ms),
    end_ms: String(params.end_ms),
  });
  return apiFetch<OhlcvResponse>(`/api/ohlcv?${q}`);
};

export const getSignals = (params: {
  symbol: string;
  timeframe: string;
  start_ms: number;
  end_ms: number;
  strategies: string[];
}) => apiFetch<SignalsResponse>("/api/signals", { method: "POST", body: JSON.stringify(params) });

export const getSignalsHistory = (params: {
  symbol: string;
  timeframe: string;
  start_ms: number;
  end_ms: number;
}) => {
  const q = new URLSearchParams({
    symbol: params.symbol,
    timeframe: params.timeframe,
    start_ms: String(params.start_ms),
    end_ms: String(params.end_ms),
  });
  return apiFetch<SignalsResponse>(`/api/signals/history?${q}`);
};

export const getBacktestRuns = () =>
  apiFetch<BacktestRunSummary[]>("/api/backtest/runs");

export interface DigestResult {
  columns: string[];
  rows: (string | number | null)[][];
}

export const getBacktestAnalysis = (
  query: string,
  minTrades: number = 5,
  topN: number = 20,
  useConfig: boolean = false,
) => {
  const q = new URLSearchParams({
    query,
    min_trades: String(minTrades),
    top_n: String(topN),
    use_config: String(useConfig),
  });
  return apiFetch<DigestResult>(`/api/backtest/analysis?${q}`);
};

export const runBacktest = (params: {
  symbol: string;
  timeframe: string;
  strategy: string;
  days: number;
  sl_pct: number;
  tp_r: number;
  fee_pct?: number;
  [key: string]: unknown;
}) =>
  apiFetch<BacktestResponse>("/api/backtest", {
    method: "POST",
    body: JSON.stringify(params),
  });

export const getFib = (params: {
  symbol: string;
  timeframe: string;
  start_ms: number;
  end_ms: number;
}) => {
  const q = new URLSearchParams({
    symbol: params.symbol,
    timeframe: params.timeframe,
    start_ms: String(params.start_ms),
    end_ms: String(params.end_ms),
  });
  return apiFetch<FibResponse>(`/api/fib?${q}`);
};

// ── Structural Zones ──────────────────────────────────────────────────────────

export interface ZoneBox {
  zone_type: "fvg" | "ob" | "fib_zone" | "ote";
  direction: "bull" | "bear";
  zone_low: number;
  zone_high: number;
  start_ms: number;
  close_ms: number | null;
  active: boolean;
}

export interface ZoneLine {
  zone_type: "eqh" | "eql" | "bos";
  direction: "bull" | "bear";
  price: number;
  start_ms: number;
  close_ms: number | null;
  label: string;
  active: boolean;
}

export interface SwingPoint {
  swing_type: "high" | "low";
  price: number;
  time_ms: number;
}

export interface ZonesResponse {
  boxes: ZoneBox[];
  lines: ZoneLine[];
  swings: SwingPoint[];
}

export const getZones = (params: {
  symbol: string;
  timeframe: string;
  start_ms: number;
  end_ms: number;
}) => {
  const q = new URLSearchParams({
    symbol: params.symbol,
    timeframe: params.timeframe,
    start_ms: String(params.start_ms),
    end_ms: String(params.end_ms),
  });
  return apiFetch<ZonesResponse>(`/api/zones?${q}`);
};

// ── Stats ─────────────────────────────────────────────────────────────────────

export interface P1P2DOWRow {
  dow: string;
  p1_low_pct: number;
  sample_days: number;
}

export interface P1P2Response {
  overall_p1_low_pct: number;
  by_dow: P1P2DOWRow[];
  sample_days: number;
  p1_strong_pct: number;
}

export interface HourlyExtremeRow {
  hour_myt: number;
  high_pct: number;
  low_pct: number;
}

export interface ADRResponse {
  adr_14: number;
  adr_30: number;
  adr_14_median: number;
  adr_30_median: number;
  today_range_pct: number | null;
  today_consumed_pct: number | null;
}

export interface DOWPatternRow {
  dow: string;
  avg_range_pct: number;
  /** Median (high-low)/open. Display-only companion to avg_range_pct — range is
   *  unsigned, so unlike the return columns it is never dimmed. */
  median_range_pct: number;
  bull_pct: number;
  sample_days: number;
  avg_return_pct: number;
  median_return_pct: number;
  /** stddev/sqrt(n). null at n < 2. Both dim when |mean| < 2.576 SE
   *  (Bonferroni, k=5 equity weekdays — NOT the parent's 2.69 for k=7). */
  return_stderr_pct: number | null;
  strong_high_pct: number;
  strong_low_pct: number;
}

export interface SessionRow {
  session: string;
  high_pct: number;
  low_pct: number;
}

export interface WeeklyP1P2Response {
  overall_p1_low_pct: number;
  low_day: string;
  high_day: string;
  sample_weeks: number;
  low_by_dow: Record<string, number>;
  high_by_dow: Record<string, number>;
}

export interface WeeklyP2TimingResponse {
  low_still_ahead_by_dow: Record<string, number>;
  high_still_ahead_by_dow: Record<string, number>;
  low_flip_risk_by_dow: Record<string, number>;
  high_flip_risk_by_dow: Record<string, number>;
}

export interface WeeklyCurrentStateResponse {
  current_isodow: number;
  current_dow: string;
  weekly_open: number;
  current_price: number;
  move_pct: number;
  move_bucket: "small" | "medium" | "large";
  low_still_ahead_conditioned: number | null;
  high_still_ahead_conditioned: number | null;
}

export interface FlipRiskConditionedRow {
  p1_direction: string;
  isodow: number;
  dow_label: string;
  flip_pct: number;
  sample_count: number;
}

export interface WeeklyFlipRiskConditionedResponse {
  rows: FlipRiskConditionedRow[];
}

export interface ConeComboResponse {
  direction: string;
  weekday: string;
  n: number;
  bands: number[][];
  low_in_by: number[];
  high_in_by: number[];
  mae_p: number[];
  mfe_p: number[];
  high_piv: number[];
  low_piv: number[];
}

export interface PathConeResponse {
  combos: Record<string, ConeComboResponse>;
  total_days: number;
}

export interface TodayPathResponse {
  points: number[];
  elapsed_h: number;
  adr14_today: number;
  today_open: number;
}

export interface WeeklyConeComboResponse {
  direction: string;
  n: number;
  bands: number[][];
  low_in_by: number[];
  high_in_by: number[];
  mae_p: number[];
  mfe_p: number[];
  high_piv: number[];
  low_piv: number[];
}

export interface WeeklyConeResponse {
  combos: Record<string, WeeklyConeComboResponse>;
  total_weeks: number;
}

export interface CurrentWeekPathResponse {
  points: number[];
  elapsed_h: number;
  awr14_current: number;
  week_open: number;
}

export interface WeeklyWickPercentileResponse {
  current_wick_of_adr: number | null;
  exceedance_pct: number | null;
  p1_direction: string | null;
  sample_count: number;
}

export interface StatsResponse {
  symbol: string;
  days: number;
  computed_at_ms: number;
  p1p2: P1P2Response;
  hourly_extremes: HourlyExtremeRow[];
  adr: ADRResponse;
  dow_patterns: DOWPatternRow[];
  sessions: SessionRow[];
  weekly_p1p2: WeeklyP1P2Response;
  weekly_p2_timing: WeeklyP2TimingResponse;
  weekly_current_state: WeeklyCurrentStateResponse | null;
  weekly_flip_risk_conditioned: WeeklyFlipRiskConditionedResponse | null;
  path_cone: PathConeResponse;
  today_path: TodayPathResponse | null;
  weekly_wick_percentile: WeeklyWickPercentileResponse | null;
  weekly_cone: WeeklyConeResponse | null;
  current_week_path: CurrentWeekPathResponse | null;
}

export const getStats = (symbol: string, days: number = 180) =>
  apiFetch<StatsResponse>(`/api/stats/${symbol}?days=${days}`);

// ── Live outcomes (cross-symbol signal_alert_outcomes ledger) ────────────────

export interface LiveOutcomesRollup {
  total_rows: number;
  resolved: number;
  open: number;
  open_no_tp: number;
  wins: number;
  losses: number;
  expired: number;
}

export interface LiveOutcomeCell {
  strategy: string;
  tf: string;
  direction: string;
  n: number;
  wins: number;
  losses: number;
  expired: number;
  win_rate: number | null;
  avg_r: number | null;
}

export interface LiveOutcomeStrategyRow {
  strategy: string;
  n: number;
  wins: number;
  losses: number;
  expired: number;
  win_rate: number | null;
  avg_r: number | null;
}

export interface LiveOutcomeSymbolRow {
  symbol: string;
  n: number;
}

export interface LiveOpenPosition {
  signal_id: string;
  symbol: string;
  strategy: string;
  tf: string;
  direction: string;
  fired_at_ms: number;
  entry_price: number | null;
  sl_price: number | null;
  tp_price: number | null;
  mark: number | null;
  unrealized_r: number | null;
  dist_sl_pct: number | null;
  dist_tp_pct: number | null;
}

export interface LiveOpenPositionsResponse {
  symbol: string | null;
  marks_ok: boolean;
  marked_at_ms: number;
  positions: LiveOpenPosition[];
}

export interface LiveOutcomesResponse {
  days: number;
  min_n: number;
  rollup: LiveOutcomesRollup;
  cells: LiveOutcomeCell[];
  by_strategy: LiveOutcomeStrategyRow[];
  symbols: LiveOutcomeSymbolRow[];
}

export const getLiveOutcomes = (
  days: number = 30,
  minN: number = 1,
  symbol: string | null = null,
) =>
  apiFetch<LiveOutcomesResponse>(
    `/api/live-outcomes?days=${days}&min_n=${minN}` +
      (symbol ? `&symbol=${encodeURIComponent(symbol)}` : ""),
  );

export const getLiveOutcomesOpen = (symbol: string | null = null) =>
  apiFetch<LiveOpenPositionsResponse>(
    `/api/live-outcomes/open` +
      (symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""),
  );

// ── Survival core (OV-1 × VM) ─────────────────────────────────────────────────

export interface CoreStateResponse {
  as_of: string; // ISO date of the latest completed ^GSPC close
  close: number;
  sma: number;
  ma_in: boolean;
  sessions_in_state: number;
  flip_level: number;
  sigma_ann: number; // fraction, annualised
  vm_weight: number;
  exposure: number; // fraction of the market to hold next session
  sma_distance: number; // close / SMA − 1
  flip_distance: number; // flip_level / close − 1
  missing_sessions: number; // closed NYSE sessions with no bar; > 0 = stale
}

export const getCoreState = () => apiFetch<CoreStateResponse>("/api/core-state");
