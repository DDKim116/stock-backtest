export type NumOrList = number | number[] | null;

export interface Spec {
  name: string;
  signal: string;
  entry: { type: "limit" | "next_open" | "close"; price: string; valid_days: number | number[] };
  exit: {
    target_pct: NumOrList;
    target_basis: "high" | "close";
    stop_pct: NumOrList;
    max_days: number | number[];
    trail_pct?: NumOrList;
    exit_signal?: string;
  };
  universe: {
    markets: string[];
    start: string;
    end: string | null;
    exclude_spac: boolean;
    exclude_preferred: boolean;
  };
  costs: { buy_fee_pct: number; sell_fee_pct: number; sell_tax_pct: number; slippage_pct: number };
  dedupe_days: number;
  fx_krw: boolean;
}

export interface Summary {
  n_signals: number;
  n_filled: number;
  fill_rate: number | null;
  n_complete: number;
  n_open: number;
  n_target: number;
  n_stop: number;
  n_timeout: number;
  n_trail?: number;
  n_signal_exit?: number;
  success_rate: number | null;
  success_rate_opt: number | null;
  n_uncertain: number;
  avg_ret: number | null;
  median_ret: number | null;
  avg_ret_opt: number | null;
  profit_rate: number | null;
  avg_mfe: number | null;
  avg_mae: number | null;
  avg_days_to_target: number | null;
  edge_pp?: number;
}

export interface Baseline {
  n: number;
  success_rate: number | null;
  avg_ret: number | null;
  fill_rate: number | null;
}

export interface Check {
  level: "good" | "info" | "warn" | "danger";
  text: string;
}

export interface Trade {
  code: string;
  name: string;
  market: string;
  signal_date: string;
  signal_close: number | null;
  limit: number | null;
  fill_date: string | null;
  fill_price: number | null;
  outcome: string;
  outcome_opt: string;
  uncertain: boolean;
  exit_date: string | null;
  exit_price: number | null;
  days: number | null;
  ret: number | null;
  ret_opt: number | null;
  mfe: number | null;
  mae: number | null;
}

export type BreakdownRow = Summary & { key: string };

export interface DatasetInfo {
  name: string;
  label: string;
  synthetic: boolean;
  currency: "KRW" | "USD";
  markets: string[];
  costs: Spec["costs"];
  ready: boolean;
  meta: { rows: number; codes: number; start: string; end: string; built_at: string } | null;
}

export interface SingleResult {
  kind: "single";
  spec: Spec;
  text: string;
  summary: Summary;
  baseline: Baseline;
  breakdowns: { year: BreakdownRow[]; market: BreakdownRow[]; price: BreakdownRow[] };
  checks: Check[];
  trades: Trade[];
  trades_truncated: boolean;
  elapsed: number;
  dataset: { name: string; label: string; currency?: string };
}

export interface SweepResult {
  kind: "sweep";
  text: string;
  variants: { params: Record<string, number>; spec: Spec; summary: Summary; baseline: Baseline }[];
  elapsed: number;
  dataset: { name: string; label: string };
}

export type RunResult = SingleResult | SweepResult;

export interface Strategy {
  id: number;
  name: string;
  text: string;
  note: string;
  last_summary: Summary | null;
  created_at: string;
  updated_at: string;
}

export interface Bar {
  t: string;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
  a?: number | null; // 거래대금
}

export interface AIStatus {
  enabled: boolean;
  model: string;
  monthly_limit_usd: number;
  krw_rate: number;
  models: { id: string; label: string }[];
  month_count: number;
  month_usd: number;
  month_krw: number;
  limit_krw: number;
  estimate_krw: { translate: number; explain: number; review?: number; journal?: number };
}

export interface AIUsage {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  cost_krw: number;
}

export interface TranslateResult {
  ok: boolean;
  reply: string;
  spec?: Spec;
  text?: string;
  interpretation?: string[];
  assumptions?: { item: string; chosen: string; alternatives: string[] }[];
  unsupported?: string[];
  usage?: AIUsage;
}

export interface ExplainResult {
  verdict: string;
  points: string[];
  risks: string[];
  suggestions: { title: string; why: string; text: string }[];
  usage: AIUsage;
}

export interface ScanRow {
  code: string;
  name: string;
  market: string;
  signal_date: string;
  close: number | null;
  change: number | null;
  volume: number | null;
  value: number | null;
  mktcap: number | null;
  limit: number | null;
  fill_date: string | null;
  fill_price: number | null;
  last_close: number | null;
  ret_now: number | null;
  status: string;
  kind: "pending" | "holding" | "closed" | "expired";
  outcome: string;
  exit_date: string | null;
  exit_price: number | null;
  ret: number | null;
  mfe: number | null;
  mae: number | null;
}

export interface ScanResult {
  as_of: string;
  since: string;
  days: number;
  count: number;
  rows: ScanRow[];
  truncated: boolean;
  spec: Spec;
  dataset: { name: string; label: string; currency?: string };
}

export interface ExploreAxis {
  key: string;
  kind: "signal" | "target" | "stop" | "days" | "user";
  values: (number | null)[];
}

export interface ExplorePlan {
  axes: ExploreAxis[];
  n_combos: number;
  user_defined: boolean;
  notes: string[];
  split: string;
}

export interface ExploreBrief {
  n_complete: number;
  success_rate: number | null;
  avg_ret: number | null;
  median_ret: number | null;
  fill_rate: number | null;
  n_stop: number;
}

export interface ExploreRow {
  combo: (number | null)[];
  spec: Spec;
  baseline: { n: number; success_rate: number | null; avg_ret: number | null };
  all: ExploreBrief;
  is: ExploreBrief;
  oos: ExploreBrief;
  pos_years: number | null;
  n_years: number;
  neighbor_avg: number | null;
  reasons: string[];
  passed: boolean;
  score: number | null;
}

export interface ExploreResult {
  axes: ExploreAxis[];
  user_defined: boolean;
  notes: string[];
  split: string;
  period: [string, string] | null;
  elapsed: number;
  n_total: number;
  n_passed: number;
  fail_reasons: [string, number][];
  passed: ExploreRow[];
  near_miss: ExploreRow[];
  axis_effects?: {
    key: string;
    kind: ExploreAxis["kind"];
    values: { value: number | null; is_avg: number | null; oos_avg: number | null; passed: number; n: number }[];
  }[];
  dataset: { name: string; label: string; currency?: string };
}

export interface ExploreJob {
  id: string;
  status: "running" | "done" | "error" | "cancelled";
  done: number;
  total: number;
  result: ExploreResult | null;
  error: string | null;
  elapsed: number;
  review?: ExploreReview;
}

export interface ExploreReview {
  verdict: string;
  best: { rank: number; why: string };
  patterns: string[];
  risks: string[];
  next_ideas: { title: string; why: string; text: string }[];
  usage: AIUsage;
}

export interface JournalFill {
  id: number;
  trade_id: number;
  side: "buy" | "sell";
  date: string;
  price: number;
  qty: number;
  fee: number | null;
  memo: string;
}

export interface JournalPos {
  qty: number;
  avg_cost: number | null;
  avg_buy: number | null;
  avg_sell: number | null;
  bought_qty: number;
  sold_qty: number;
  invested: number;
  fees: number;
  realized: number;
  unrealized: number | null;
  pnl: number | null;
  ret: number | null;
  closed: boolean;
  status: "open" | "closed" | "empty" | "error";
  first_buy: string | null;
  last_sell: string | null;
  holding_days: number | null;
  error?: string;
}

export interface JournalCompare {
  strategy: {
    signal_date: string;
    limit: number | null;
    fill_date: string | null;
    fill_price: number | null;
    outcome: string;
    exit_date: string | null;
    exit_price: number | null;
    ret: number | null;
    mfe: number | null;
    mae: number | null;
  };
  verdict: string;
  followed: boolean;
  buy_diff_pct?: number | null;
  sell_timing?: string;
  ret_diff?: number | null;
}

export interface JournalTrade {
  id: number;
  dataset: string;
  code: string;
  name: string;
  market: string;
  strategy_id: number | null;
  strategy_name: string;
  strategy_text: string;
  signal_date: string | null;
  memo: string;
  created_at: string;
  updated_at: string;
  fills: JournalFill[];
  currency: string;
  last_date: string | null;
  last_close: number | null;
  pos: JournalPos;
  compare: JournalCompare | null;
}

export interface JournalAgg {
  n: number;
  win_rate: number | null;
  avg_ret: number | null;
  total_pnl: number;
  best: number | null;
  worst: number | null;
  avg_days: number | null;
}

export interface JournalStats {
  dataset: string;
  currency: string;
  closed: JournalAgg;
  open: { n: number; invested: number; unrealized: number };
  monthly: (JournalAgg & { month: string })[];
  by_strategy: (JournalAgg & {
    name: string;
    strategy_avg_ret: number | null;
    backtest_success: number | null;
    backtest_avg_ret: number | null;
  })[];
  followed: JournalAgg;
  deviated: JournalAgg;
}

export interface JournalPrefill {
  code: string;
  name: string;
  market: string;
  signal_date: string | null;
  strategy_id: number | null;
  strategy_name: string;
  strategy_text: string;
  price: number | null;
  date: string | null;
}

export interface JournalReview {
  summary: string;
  good: string[];
  mistakes: { pattern: string; evidence: string; fix: string }[];
  rules: string[];
  watch: string[];
  usage: AIUsage;
  n_closed: number;
}
