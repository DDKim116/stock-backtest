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
  check_msg?: string | null; // '확인 필요' 사유 (status === "check")
  carry?: string | null; // 다음 거래일 장 시작에 이어서 팔 사유
  held?: number | null; // 서버가 센 보유 거래일 (공휴일 반영, 기간만료 셈과 같음)
}
// 서버 잔고 확인이 찾은 불일치: 키움에만 있는 종목(untracked) · 앱 기록보다 많은 종목(extra, qty = 남는 수량)
export interface HoldingIssue {
  code: string;
  name: string;
  kind: "untracked" | "extra";
  qty: number;
  held?: number;
  avg_price?: number | null;
  ret?: number | null;
}
export interface AutoView {
  key_saved: boolean;
  settings: { enabled: boolean; mode: "approve" | "auto"; order_cap?: number; daily_loss_pct?: number; skip_risky?: boolean };
  strategies: AutoStrategy[];
  plans: Plan[];
  plan_date: string | null;
  positions: Position[];
  stats: { n: number; win: number; avg: number | null };
  stats_manual: { n: number; win: number; avg: number | null };
  last_plan: { trade_date: string; signal_date: string; count: number; per?: Record<string, number> } | null;
  log: { ts: string; text: string }[];
  untracked?: HoldingIssue[];
  untracked_at?: string | null;
  state?: { halted?: string | null; halted_why?: string | null; halt_skipped?: string; last_tick?: string; last_error?: string; buy_done?: string; expire_done?: string; overdue_done?: string; close_done?: string };
  version?: string;
  deploy?: string | null; // 마지막 배포 결과
  now?: string;
}
export interface Account {
  cash: { orderable: number | null; deposit: number | null };
  balance: { assets: number | null; total_eval: number | null; pnl: number | null; holdings: Holding[] };
}

export interface Holding {
  code: string;
  name: string;
  qty: number;
  avg_price: number;
  price: number;
  pnl: number | null;
  ret: number | null;
}

// 키움에는 있는데 자동매매가 산 기록이 없는 종목 (영웅문에서 직접 샀거나, 앱이 놓친 체결)
export const untracked = (acct: Account | null, positions: { code: string; status: string }[]) => {
  const ours = new Set(positions.filter((p) => p.status !== "closed").map((p) => p.code));
  return (acct?.balance.holdings ?? []).filter((h) => !ours.has(h.code));
};

// 화면 경고용: 키움에만 있는 종목 · 앱 기록보다 많은 종목.
// 지금 계좌 조회가 있으면 수량·종류는 화면에서 바로 계산한 값을 쓰고(최신), 없을 때만 서버 잔고 확인 기록을 쓴다.
export const holdingIssues = (
  acct: Account | null,
  positions: { code: string; status: string; qty?: number; sold_qty?: number }[],
  server?: HoldingIssue[],
): HoldingIssue[] => {
  if (!acct) return server ?? [];
  const ours = new Map<string, number>();
  for (const p of positions)
    if (p.status !== "closed") ours.set(p.code, (ours.get(p.code) ?? 0) + Math.max(0, (p.qty ?? 0) - (p.sold_qty ?? 0)));
  const out: HoldingIssue[] = [];
  for (const h of acct.balance.holdings) {
    const mine = ours.get(h.code);
    const base = { code: h.code, name: h.name, held: h.qty, avg_price: h.avg_price, ret: h.ret };
    if (mine === undefined) out.push({ ...base, kind: "untracked", qty: h.qty });
    else if (h.qty > mine) out.push({ ...base, kind: "extra", qty: h.qty - mine });
  }
  return out;
};

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
