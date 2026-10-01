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
  estimate_krw: { translate: number; explain: number };
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
