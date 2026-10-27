"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { pct, retClass } from "@/lib/format";
import type { Spec, Strategy, Trade } from "@/lib/types";
import { useBackClose } from "@/lib/useBackClose";
import NumInput from "./NumInput";
import TradeChart from "./TradeChart";

interface Plan {
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
}
interface Position {
  id: number;
  code: string;
  name: string;
  qty: number;
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
}
interface AutoView {
  key_saved: boolean;
  rules: { target: number | null; stop: number | null; hold: number };
  settings: { enabled: boolean; mode: "approve" | "auto"; strategy_id: number | null; per_trade: number; daily_max: number };
  rank_label: string;
  strategy: { id: number; name: string } | null;
  spec: Spec | null;
  plans: Plan[];
  plan_date: string | null;
  positions: Position[];
  stats: { n: number; win: number; avg: number | null };
  stats_manual: { n: number; win: number; avg: number | null };
  last_plan: { trade_date: string; signal_date: string; count: number } | null;
  log: { ts: string; text: string }[];
}
interface Account {
  cash: { orderable: number | null; deposit: number | null };
  balance: { assets: number | null; total_eval: number | null; pnl: number | null; holdings: unknown[] };
}

const STATUS: Record<string, [string, string]> = {
  proposed: ["승인 대기", "text-warning"],
  approved: ["승인됨", "text-accent"],
  rejected: ["뺌", "text-faint"],
  expired: ["승인 안 함", "text-faint"],
  ordered: ["주문함", "text-accent"],
  partial: ["일부 체결", "text-accent"],
  filled: ["체결", "text-accent"],
  nofill: ["미체결", "text-faint"],
  skipped: ["건너뜀", "text-faint"],
  failed: ["주문 실패", "text-critical"],
};
const won = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v).toLocaleString("ko-KR")}원`);
const man = (v: number) => `${(v / 10000).toLocaleString("ko-KR")}만원`;
const md = (d: string) => `${+d.slice(5, 7)}/${+d.slice(8, 10)}`;

// 차트에 넘길 거래 (실제 키움 체결가 기준)
function asTrade(x: { code: string; name: string; signal_date: string | null; limit: number | null }, pos?: Position): Trade {
  const outcome = pos ? (pos.status === "closed" ? (pos.exit_reason === "목표" ? "목표도달" : (pos.exit_reason ?? "청산")) : "진행중") : "주문 전";
  return {
    code: x.code, name: x.name, market: "", signal_date: x.signal_date ?? pos?.entry_date ?? "", signal_close: null,
    limit: x.limit, fill_date: pos?.entry_date ?? null, fill_price: pos?.entry_price ?? null, outcome, outcome_opt: outcome,
    uncertain: false, exit_date: pos?.exit_date ?? null, exit_price: pos?.exit_price ?? null, days: null,
    ret: pos?.ret ?? null, ret_opt: pos?.ret ?? null, mfe: null, mae: null,
  };
}

// 모투 탭 맨 위: 키움 모의투자 자동 매매 (설정, 매수 후보 승인, 보유·청산, 기록)
export default function AutoTrade({ onFlash }: { onFlash: (m: string) => void }) {
  const [v, setV] = useState<AutoView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState(false);
  const [showLog, setShowLog] = useState(false);
  const [acct, setAcct] = useState<Account | null>(null);
  const [sellId, setSellId] = useState<number | null>(null); // '지금 팔기' 두 번 눌러 확인
  const [chart, setChart] = useState<Trade | null>(null);
  const closeChart = useCallback(() => setChart(null), []);
  const chartBack = useBackClose(!!chart, closeChart);
  const openChart = (t: Trade) => {
    chartBack.push();
    setChart(t);
  };

  const load = useCallback(async () => {
    try {
      setV(await api<AutoView>("/api/auto"));
      setErr(null);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 처음 한 번 + 1분마다 새로 불러온다
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  const run = async (f: () => Promise<unknown>, ok?: string) => {
    setBusy(true);
    try {
      const r = await f();
      if (r && typeof r === "object" && "settings" in r) setV(r as AutoView);
      if (ok) onFlash(ok);
    } catch (e) {
      onFlash((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (err) return <p className="text-sm text-critical">⚠ 자동 매매: {err}</p>;
  if (!v) return null;
  const s = v.settings;
  const active = v.positions.filter((p) => p.status !== "closed");
  const closed = v.positions.filter((p) => p.status === "closed").slice(0, 10);
  const waiting = v.plans.filter((p) => p.status === "proposed");

  return (
    <section className="space-y-3 rounded-xl border border-border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">키움 모의투자 자동 매매</h3>
        <div className="flex gap-1 text-xs">
          <span className={`rounded-full px-2 py-0.5 ${s.enabled ? "bg-accent/10 text-accent" : "bg-background text-faint"}`}>
            {s.enabled ? "켜짐" : "꺼짐"}
          </span>
          <span className="rounded-full bg-background px-2 py-0.5 text-muted">{s.mode === "auto" ? "완전 자동" : "승인 모드"}</span>
        </div>
      </div>

      {!v.key_saved && (
        <p className="rounded-lg bg-background p-3 text-sm text-warning">설정 → 키움 연결에서 모의투자 키를 먼저 넣어 주세요.</p>
      )}

      <p className="text-xs text-muted">
        {v.strategy?.name ?? "전략 없음"} · 1회 {man(s.per_trade)} · 하루 최대 {s.daily_max}종목 · {v.rank_label} · 목표{" "}
        {v.rules.target ?? "없음"}% · 손절 {v.rules.stop ?? "없음"}% · {v.rules.hold}거래일
      </p>

      {v.last_plan && v.last_plan.count === 0 && v.last_plan.trade_date !== v.plan_date && (
        <p className="rounded-lg bg-background p-2 text-xs text-muted">
          {md(v.last_plan.trade_date)} 매수 후보 없음 · {md(v.last_plan.signal_date)}에는 이 전략의 신호가 없었습니다.
        </p>
      )}

      {/* 매수 후보 */}
      {v.plan_date && v.plans.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span className="font-medium">
              {md(v.plan_date)} 매수 후보 <span className="text-xs text-muted">(신호일 {md(v.plans[0].signal_date)})</span>
            </span>
            {waiting.length > 0 && (
              <button
                type="button"
                disabled={busy}
                onClick={() => run(() => api("/api/auto/approve-all", { body: { trade_date: v.plan_date } }), "모두 승인했습니다")}
                className="rounded-lg bg-accent px-3 py-1 text-xs font-semibold text-white disabled:opacity-50"
              >
                모두 승인 ({waiting.length})
              </button>
            )}
          </div>
          {waiting.length > 0 && <p className="text-xs text-warning">{md(v.plan_date)} 08:45 까지 승인한 종목만 08:50 에 주문합니다.</p>}
          <ul className="divide-y divide-border rounded-lg border border-border text-sm">
            {v.plans.map((p) => {
              const [label, cls] = STATUS[p.status] ?? [p.status, "text-muted"];
              return (
                <li key={p.id} className="flex items-center justify-between gap-2 px-3 py-2">
                  <button
                    type="button"
                    onClick={() => openChart(asTrade({ code: p.code, name: p.name, signal_date: p.signal_date, limit: p.limit_price }))}
                    className="min-w-0 text-left"
                  >
                    <div className="truncate">
                      {p.rank}. {p.name} <span className="text-xs text-faint">{p.code}</span> <span className="text-xs text-accent">차트</span>
                    </div>
                    <div className="tabular text-xs text-muted">
                      {p.qty.toLocaleString("ko-KR")}주 · 지정가 {won(p.limit_price)} · 종가 {won(p.close)}
                      {p.msg && <span className="text-faint"> · {p.msg}</span>}
                    </div>
                  </button>
                  {["proposed", "approved", "rejected"].includes(p.status) && s.mode === "approve" ? (
                    <div className="flex shrink-0 gap-1">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => run(() => api(`/api/auto/plans/${p.id}`, { body: { approve: true } }))}
                        className={`rounded-lg px-2.5 py-1 text-xs ${p.status === "approved" ? "bg-accent text-white" : "border border-border"}`}
                      >
                        승인
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => run(() => api(`/api/auto/plans/${p.id}`, { body: { approve: false } }))}
                        className={`rounded-lg px-2.5 py-1 text-xs ${p.status === "rejected" ? "bg-foreground text-background" : "border border-border"}`}
                      >
                        빼기
                      </button>
                    </div>
                  ) : (
                    <span className={`shrink-0 text-xs ${cls}`}>{label}</span>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* 보유 */}
      <div className="space-y-1 text-sm">
        <div className="font-medium">보유 {active.length}종목</div>
        {active.length === 0 ? (
          <p className="text-xs text-faint">보유 종목이 없습니다.</p>
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border">
            {active.map((p) => (
              <li key={p.id} className="px-3 py-2">
                <div className="flex justify-between gap-2">
                  <button
                    type="button"
                    onClick={() => openChart(asTrade({ code: p.code, name: p.name, signal_date: p.signal_date, limit: p.limit_price }, p))}
                    className="min-w-0 truncate text-left"
                  >
                    {p.name} <span className="text-xs text-faint">{p.code}</span> <span className="text-xs text-accent">차트</span>
                  </button>
                  <span className="text-xs text-muted">{p.status === "closing" ? `${p.exit_reason} 매도 중` : `${md(p.entry_date)} 매수`}</span>
                </div>
                <div className="flex items-end justify-between gap-2">
                  <div className="tabular text-xs text-muted">
                    {p.qty.toLocaleString("ko-KR")}주 · 매수 {won(p.entry_price)} · 목표 {won(p.target_price || null)} · 손절 {won(p.stop_price || null)}
                  </div>
                  {p.status === "open" && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        if (sellId !== p.id) return setSellId(p.id);
                        setSellId(null);
                        run(() => api(`/api/auto/positions/${p.id}/sell`, { method: "POST" }), `${p.name} 시장가 매도 주문을 냈습니다`);
                      }}
                      className={`shrink-0 rounded-lg px-2.5 py-1 text-xs ${sellId === p.id ? "bg-critical text-white" : "border border-border"}`}
                    >
                      {sellId === p.id ? "정말 팔기" : "지금 팔기"}
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* 청산 */}
      {closed.length > 0 && (
        <div className="space-y-1 text-sm">
          <div className="font-medium">
            전략대로 청산 {v.stats.n}건{" "}
            <span className="text-xs text-muted">
              · 수익 {v.stats.win}건 · 평균 <span className={retClass(v.stats.avg)}>{pct(v.stats.avg, 2, true)}</span> (수수료·세금 전)
            </span>
          </div>
          {v.stats_manual.n > 0 && (
            <div className="text-xs text-muted">
              직접 매도 {v.stats_manual.n}건 · 평균 <span className={retClass(v.stats_manual.avg)}>{pct(v.stats_manual.avg, 2, true)}</span> (백테스트 비교에서는 뺌)
            </div>
          )}
          <ul className="divide-y divide-border rounded-lg border border-border">
            {closed.map((p) => (
              <li key={p.id} className="flex justify-between gap-2 px-3 py-2">
                <button
                  type="button"
                  onClick={() => openChart(asTrade({ code: p.code, name: p.name, signal_date: p.signal_date, limit: p.limit_price }, p))}
                  className="min-w-0 truncate text-left"
                >
                  {p.name} <span className="text-xs text-faint">{p.exit_reason} · {p.exit_date && md(p.exit_date)}</span>
                </button>
                <span className={`tabular ${retClass(p.ret)}`}>{pct(p.ret, 2, true)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 계좌 */}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy || !v.key_saved}
          onClick={() => run(async () => setAcct(await api<Account>("/api/auto/account")))}
          className="rounded-lg border border-border px-3 py-1.5 text-xs disabled:opacity-50"
        >
          모의 계좌 보기
        </button>
        <button
          type="button"
          disabled={busy || !v.key_saved}
          onClick={() => run(() => api("/api/auto/plan-now", { method: "POST" }), "지금 데이터로 다음 거래일 후보를 만들었습니다")}
          className="rounded-lg border border-border px-3 py-1.5 text-xs disabled:opacity-50"
        >
          지금 후보 만들기
        </button>
        <button type="button" onClick={() => setEdit((x) => !x)} className="rounded-lg border border-border px-3 py-1.5 text-xs">
          설정 바꾸기
        </button>
        <button type="button" onClick={() => setShowLog((x) => !x)} className="rounded-lg border border-border px-3 py-1.5 text-xs">
          기록 {showLog ? "닫기" : "보기"}
        </button>
      </div>
      {acct && (
        <p className="tabular rounded-lg bg-background p-2 text-xs text-muted">
          주문가능 {won(acct.cash.orderable)} · 추정자산 {won(acct.balance.assets)} · 평가손익{" "}
          <span className={retClass(acct.balance.pnl)}>{won(acct.balance.pnl)}</span> · 보유 {acct.balance.holdings.length}종목
        </p>
      )}
      {edit && <Settings v={v} busy={busy} onSave={(b) => run(() => api("/api/auto/settings", { method: "PUT", body: b }), "저장했습니다")} />}
      {showLog && (
        <ul className="max-h-60 space-y-0.5 overflow-y-auto rounded-lg bg-background p-2 text-xs text-muted">
          {v.log.length === 0 && <li>기록이 없습니다.</li>}
          {v.log.map((l, i) => (
            <li key={i}>
              <span className="tabular text-faint">{l.ts.slice(5, 16)}</span> {l.text}
            </li>
          ))}
        </ul>
      )}
      {chart && v.spec && <TradeChart trade={chart} spec={v.spec} dataset="kr" currency="KRW" onClose={chartBack.close} />}
      <p className="text-[11px] text-faint">
        &apos;전체 중지&apos;는 새 매수만 멈춥니다. 이미 산 종목은 목표가·손절·기간 만료 매도를 계속합니다. 직접 팔 때는 키움 앱 대신 &apos;지금 팔기&apos;를 쓰세요 (키움에서 판 것도 2~4분 안에 알아채 정리합니다). 지금은 모의투자에서만 주문합니다.
      </p>
    </section>
  );
}

function Settings({ v, busy, onSave }: { v: AutoView; busy: boolean; onSave: (b: object) => void }) {
  const s = v.settings;
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [mode, setMode] = useState(s.mode);
  const [sid, setSid] = useState<number | null>(s.strategy_id);
  const [per, setPer] = useState(String(s.per_trade));
  const [daily, setDaily] = useState(String(s.daily_max));
  useEffect(() => {
    api<Strategy[]>("/api/strategies").then(setStrategies, () => {});
  }, []);
  return (
    <div className="space-y-3 rounded-lg border border-border p-3 text-sm">
      <button
        type="button"
        disabled={busy}
        onClick={() => onSave({ enabled: !s.enabled })}
        className={`w-full rounded-xl py-2.5 font-semibold disabled:opacity-50 ${s.enabled ? "border border-critical text-critical" : "bg-accent text-white"}`}
      >
        {s.enabled ? "전체 중지 (새 매수 멈춤)" : "자동 매매 켜기"}
      </button>
      <label className="block">
        <span className="text-muted">주문 방식</span>
        <select value={mode} onChange={(e) => setMode(e.target.value as "approve" | "auto")} className="mt-1">
          <option value="approve">승인 모드 — 아침에 승인한 종목만 주문</option>
          <option value="auto">완전 자동 — 승인 없이 주문</option>
        </select>
      </label>
      <label className="block">
        <span className="text-muted">전략</span>
        <select value={sid ?? ""} onChange={(e) => setSid(e.target.value ? +e.target.value : null)} className="mt-1">
          <option value="">고르세요</option>
          {strategies.map((x) => (
            <option key={x.id} value={x.id}>
              {x.name}
            </option>
          ))}
        </select>
      </label>
      <div className="grid grid-cols-2 gap-2">
        <label className="block">
          <span className="text-muted">1회 매수금 (원)</span>
          <NumInput value={per} onChange={setPer} className="mt-1" />
        </label>
        <label className="block">
          <span className="text-muted">하루 최대 종목</span>
          <NumInput value={daily} onChange={setDaily} className="mt-1" />
        </label>
      </div>
      <button
        type="button"
        disabled={busy || !sid}
        onClick={() => onSave({ mode, strategy_id: sid, per_trade: Number(per), daily_max: Number(daily) })}
        className="w-full rounded-xl border border-accent py-2 font-semibold text-accent disabled:opacity-50"
      >
        저장
      </button>
    </div>
  );
}
