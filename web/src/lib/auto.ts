// 키움 자동 매매 화면(매매 → 키움 자동, 오늘)이 같이 쓰는 타입과 도우미
import type { Spec, Trade } from "./types";

export interface AutoRules {
  target: number | null;
  stop: number | null;
  hold: number;
  entry: "next_open" | "limit" | "close";
  valid_days: number;
  limit_expr: string | null;
}
export interface AutoStrategy {
  id: number;
  name: string;
  color: string;
  enabled: boolean;
  per_trade: number;
  daily_max: number;
  size_mode?: "fixed" | "pct";
  pct?: number;
  max_per_trade?: number;
  rules: AutoRules;
  midpoint: boolean;
  spec: Spec;
  stats: { n: number; win: number; avg: number | null };
}
export interface Plan {
  id: number;
  trade_date: string;
  code: string;
  name: string;
  signal_date: string;
  rank: number;
  value: number | null;
  close: number;
  limit_price: number;
  qty: number;
  status: string;
  msg: string;
  strategy_id: number;
  valid_until: string | null;
  entry_kind: string | null;
}
export interface Position {
  id: number;
  code: string;
  name: string;
  qty: number;
  sold_qty: number;
  sold_amount: number;
  entry_date: string;
  entry_price: number;
  target_price: number;
  stop_price: number;
  status: string;
  exit_reason: string | null;
  exit_date: string | null;
  exit_price: number | null;
  ret: number | null;
  signal_date: string | null;
  limit_price: number | null;
  strategy_id: number;
}
export interface AutoView {
  key_saved: boolean;
  settings: { enabled: boolean; mode: "approve" | "auto" };
  strategies: AutoStrategy[];
  plans: Plan[];
  plan_date: string | null;
  positions: Position[];
  stats: { n: number; win: number; avg: number | null };
  stats_manual: { n: number; win: number; avg: number | null };
  last_plan: { trade_date: string; signal_date: string; count: number; per?: Record<string, number> } | null;
  log: { ts: string; text: string }[];
}
export interface Account {
  cash: { orderable: number | null; deposit: number | null };
  balance: { assets: number | null; total_eval: number | null; pnl: number | null; holdings: unknown[] };
}

export const won = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v).toLocaleString("ko-KR")}원`);
export const man = (v: number) => `${Math.round(v / 10000).toLocaleString("ko-KR")}만`;
// 청산 손익(원, 수수료·세금 전) = 판 금액 - 판 수량 × 매수가
export const pnlOf = (p: { sold_amount: number; sold_qty: number; entry_price: number }) =>
  p.sold_qty && p.sold_amount != null && Number.isFinite(p.sold_amount) ? p.sold_amount - p.sold_qty * p.entry_price : null;
// +12.3만 / -4,560원 처럼 짧게
export const wonShort = (v: number | null) => {
  if (v == null) return "–";
  const sign = v > 0 ? "+" : v < 0 ? "-" : "";
  const a = Math.abs(v);
  return a >= 10_000 ? `${sign}${(a / 10_000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}만` : `${sign}${Math.round(a).toLocaleString("ko-KR")}원`;
};
export const md = (d?: string | null) => (d ? `${+d.slice(5, 7)}/${+d.slice(8, 10)}` : "–");

export function stratOf(v: AutoView, id: number | null | undefined): AutoStrategy | undefined {
  return v.strategies.find((s) => s.id === id) ?? v.strategies[0];
}

// 1회 금액 설명: '500만' 또는 '계좌 10% (최대 2,000만)'
export function sizeLabel(s: AutoStrategy): string {
  if (s.size_mode === "pct") return `계좌 ${s.pct ?? 10}% (최대 ${man(s.max_per_trade ?? 20_000_000)})`;
  return man(s.per_trade);
}

// '다음날 시가' / '중간값 지정가 5일' / '지정가 3일'
export function entryLabel(s: AutoStrategy): string {
  if (s.rules.entry === "limit") return `${s.midpoint ? "중간값 " : ""}지정가 ${s.rules.valid_days}일`;
  return "다음날 시가";
}

// 차트에 넘길 거래 (실제 키움 체결가 기준)
export function asTrade(x: { code: string; name: string; signal_date: string | null; limit: number | null }, pos?: Position): Trade {
  const outcome = pos ? (pos.status === "closed" ? (pos.exit_reason === "목표" ? "목표도달" : (pos.exit_reason ?? "청산")) : "진행중") : "주문 전";
  return {
    code: x.code, name: x.name, market: "", signal_date: x.signal_date ?? pos?.entry_date ?? "", signal_close: null,
    limit: x.limit, fill_date: pos?.entry_date ?? null, fill_price: pos?.entry_price ?? null, outcome, outcome_opt: outcome,
    uncertain: false, exit_date: pos?.exit_date ?? null, exit_price: pos?.exit_price ?? null, days: null,
    ret: pos?.ret ?? null, ret_opt: pos?.ret ?? null, mfe: null, mae: null,
  };
}
