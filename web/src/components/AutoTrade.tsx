"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { type Account, asTrade, type AutoStrategy, type AutoView, entryLabel, man, md, type Plan, type Position, won } from "@/lib/auto";
import { pct, retClass, today } from "@/lib/format";
import type { Spec, Strategy, Trade } from "@/lib/types";
import { useBackClose } from "@/lib/useBackClose";
import NumInput from "./NumInput";
import TradeChart from "./TradeChart";

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
const INK = "#10131A"; // 전략 색 띠 위 글자

type Run = (f: () => Promise<unknown>, ok?: string) => Promise<void>;

// 매매 → 키움 자동: 전략별 카드(색 띠), 계좌 돈은 모든 전략이 같이 쓴다
export default function AutoTrade({ onFlash }: { onFlash: (m: string) => void }) {
  const [v, setV] = useState<AutoView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState(false);
  const [adding, setAdding] = useState(false);
  const [showLog, setShowLog] = useState(false);
  const [acct, setAcct] = useState<Account | null>(null);
  const [chart, setChart] = useState<{ trade: Trade; spec: Spec } | null>(null);
  const closeChart = useCallback(() => setChart(null), []);
  const chartBack = useBackClose(!!chart, closeChart);
  const openChart = (trade: Trade, spec: Spec) => {
    chartBack.push();
    setChart({ trade, spec });
  };

  const load = useCallback(async () => {
    try {
      const r = await api<AutoView>("/api/auto");
      setV(r);
      setErr(null);
      if (r.key_saved) api<Account>("/api/auto/account").then(setAcct, () => {});
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

  const run: Run = async (f, ok) => {
    setBusy(true);
    try {
      const r = await f();
      if (r && typeof r === "object" && "strategies" in r) setV(r as AutoView);
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
  const first = v.strategies[0]?.id;
  const sid = (x: { strategy_id: number | null }) => x.strategy_id ?? first;
  const active = v.positions.filter((p) => p.status !== "closed");
  const closed = v.positions.filter((p) => p.status === "closed").slice(0, 12);
  const waiting = v.plans.filter((p) => p.status === "proposed");
  const used = (id: number) =>
    active.filter((p) => sid(p) === id).reduce((a, p) => a + p.entry_price * (p.qty - (p.sold_qty ?? 0)), 0);
  const usedAll = v.strategies.reduce((a, x) => a + used(x.id), 0);
  const total = acct?.balance.assets ?? null;
  const base = Math.max(total ?? 0, usedAll + (acct?.cash.orderable ?? 0), 1);
  const color = (id: number | undefined) => v.strategies.find((x) => x.id === id)?.color ?? "var(--faint)";

  return (
    <div className="space-y-3">
      {/* 계좌 사용 (전략 색으로 나눠 보기) */}
      <section className="space-y-2 rounded-xl border border-border bg-card p-4">
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
        <div className="flex justify-between gap-2 text-sm">
          <span className="text-muted">계좌 사용</span>
          <span className="tabular">
            {man(usedAll)}
            {total != null && <> / {man(total)}</>}
            {acct?.cash.orderable != null && <span className="text-muted"> · 주문가능 {man(acct.cash.orderable)}</span>}
          </span>
        </div>
        <div className="flex h-2.5 overflow-hidden rounded-full bg-background" aria-hidden>
          {v.strategies.map((x) => (
            <div key={x.id} style={{ width: `${(used(x.id) / base) * 100}%`, background: x.color }} />
          ))}
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
          {v.strategies.map((x) => (
            <span key={x.id} className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-sm" style={{ background: x.color }} />
              {x.name} <span className="tabular">{man(used(x.id))}</span>
            </span>
          ))}
          <span className="ml-auto">
            자금 <b className="text-foreground">같이 쓰기</b>
          </span>
        </div>
        {acct && (
          <p className="tabular text-xs text-faint">
            추정자산 {won(acct.balance.assets)} · 평가손익 <span className={retClass(acct.balance.pnl)}>{won(acct.balance.pnl)}</span> · 키움
            보유 {acct.balance.holdings.length}종목
          </p>
        )}
      </section>

      {v.last_plan && v.last_plan.count === 0 && v.last_plan.trade_date !== v.plan_date && (
        <p className="rounded-lg bg-card p-3 text-xs text-muted">
          {md(v.last_plan.trade_date)} 매수 후보 없음 · {md(v.last_plan.signal_date)}에는 켜진 전략의 신호가 없었습니다.
        </p>
      )}

      {waiting.length > 0 && v.plan_date && (
        <div className="flex items-center justify-between gap-2 rounded-xl bg-accent-soft p-3 text-sm text-accent">
          <span>
            <b>{md(v.plan_date)} 후보 {waiting.length}종목 승인 대기</b>
            <span className="block text-xs">08:45 까지 승인한 종목만 08:50 에 주문합니다</span>
          </span>
          <button
            type="button"
            disabled={busy}
            onClick={() => run(() => api("/api/auto/approve-all", { body: { trade_date: v.plan_date } }), "모두 승인했습니다")}
            className="shrink-0 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-on-accent disabled:opacity-50"
          >
            모두 승인
          </button>
        </div>
      )}

      {/* 전략별 카드: 폴드 펼침(넓은 화면)에서는 나란히 */}
      <div className="grid items-start gap-3 fold:grid-cols-2">
        {v.strategies.map((x) => (
          <StrategyCard
            key={x.id}
            x={x}
            mode={s.mode}
            busy={busy}
            plans={v.plans.filter((p) => sid(p) === x.id)}
            positions={active.filter((p) => sid(p) === x.id)}
            run={run}
            openChart={openChart}
          />
        ))}
        {v.strategies.length === 0 && <p className="text-sm text-muted">자동 매매할 전략을 추가하세요.</p>}
      </div>

      {adding ? (
        <AddStrategy
          have={v.strategies.map((x) => x.id)}
          busy={busy}
          onCancel={() => setAdding(false)}
          onAdd={(id) =>
            run(async () => {
              const r = await api(`/api/auto/strategies/${id}`, { method: "PUT", body: {} });
              setAdding(false);
              return r;
            }, "전략을 추가했습니다 · 1회 300만 × 하루 5종목 (카드의 '설정'에서 바꿀 수 있어요)")
          }
        />
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="w-full rounded-xl border border-dashed border-border py-3 text-sm text-muted fold:w-auto fold:px-5"
        >
          + 전략 추가
        </button>
      )}

      {/* 청산 (모든 전략) */}
      {closed.length > 0 && (
        <section className="space-y-1 rounded-xl border border-border bg-card p-4 text-sm">
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
          <ul className="divide-y divide-border">
            {closed.map((p) => {
              const st = v.strategies.find((x) => x.id === sid(p));
              return (
                <li key={p.id} className="flex justify-between gap-2 py-2">
                  <button
                    type="button"
                    onClick={() =>
                      st && openChart(asTrade({ code: p.code, name: p.name, signal_date: p.signal_date, limit: p.limit_price }, p), st.spec)
                    }
                    className="flex min-w-0 items-center gap-2 truncate text-left"
                  >
                    <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: color(sid(p)) }} />
                    {p.name} <span className="text-xs text-faint">{p.exit_reason} · {p.exit_date && md(p.exit_date)}</span>
                  </button>
                  <span className={`tabular ${retClass(p.ret)}`}>{pct(p.ret, 2, true)}</span>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy || !v.key_saved}
          onClick={() => run(async () => setAcct(await api<Account>("/api/auto/account")), "계좌를 새로 불러왔습니다")}
          className="rounded-lg border border-border px-3 py-1.5 text-xs disabled:opacity-50"
        >
          계좌 새로고침
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
          켜기·끄기 / 주문 방식
        </button>
        <button type="button" onClick={() => setShowLog((x) => !x)} className="rounded-lg border border-border px-3 py-1.5 text-xs">
          기록 {showLog ? "닫기" : "보기"}
        </button>
      </div>
      {edit && <GlobalSettings v={v} busy={busy} onSave={(b) => run(() => api("/api/auto/settings", { method: "PUT", body: b }), "저장했습니다")} />}
      {showLog && (
        <ul className="max-h-60 space-y-0.5 overflow-y-auto rounded-lg bg-card p-2 text-xs text-muted">
          {v.log.length === 0 && <li>기록이 없습니다.</li>}
          {v.log.map((l, i) => (
            <li key={i}>
              <span className="tabular text-faint">{l.ts.slice(5, 16)}</span> {l.text}
            </li>
          ))}
        </ul>
      )}
      {chart && <TradeChart trade={chart.trade} spec={chart.spec} dataset="kr" currency="KRW" onClose={chartBack.close} />}
      <p className="text-[11px] text-faint">
        계좌 돈은 모든 전략이 같이 씁니다. 아침 주문은 위 전략 순서대로 나가고, 주문가능금액이 모자라면 그 종목은 건너뜁니다 (지정가 전략은 다음 날 다시
        시도). &apos;전체 중지&apos;는 새 매수만 멈추고, 이미 산 종목은 목표가·손절·기간 만료 매도를 계속합니다. 지금은 모의투자에서만 주문합니다.
      </p>
    </div>
  );
}

function StrategyCard({
  x,
  mode,
  busy,
  plans,
  positions,
  run,
  openChart,
}: {
  x: AutoStrategy;
  mode: "approve" | "auto";
  busy: boolean;
  plans: Plan[];
  positions: Position[];
  run: Run;
  openChart: (t: Trade, spec: Spec) => void;
}) {
  const [edit, setEdit] = useState(false);
  const [sellId, setSellId] = useState<number | null>(null); // '지금 팔기' 두 번 눌러 확인
  const r = x.rules;
  const now = today();
  const limitName = x.midpoint ? "중간값" : "지정가";
  const dates = [...new Set(plans.map((p) => p.trade_date))];
  return (
    <section className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between gap-2 px-4 py-2.5" style={{ background: x.color, color: INK }}>
        <span className="truncate font-bold">{x.name}</span>
        <span className="flex shrink-0 items-center gap-2 text-xs font-semibold">
          {x.enabled ? `켜짐 · ${mode === "auto" ? "자동" : "승인"}` : "꺼짐"}
          <button type="button" onClick={() => setEdit((e) => !e)} className="rounded-md px-2 py-0.5" style={{ background: "#10131A22" }}>
            {edit ? "닫기" : "설정"}
          </button>
        </span>
      </div>
      <div className="space-y-3 p-4 text-sm">
        <p className="tabular text-xs text-muted">
          {entryLabel(x)} · {man(x.per_trade)} × 하루 {x.daily_max} · +{r.target ?? "–"} / −{r.stop ?? "–"} · {r.hold}일
        </p>
        {edit && <StrategySettings x={x} busy={busy} run={run} />}

        {plans.length > 0 && (
          <div className="space-y-1">
            <div className="text-xs text-muted">
              {dates.map(md).join(" · ")} 후보
              {r.entry === "limit" && ` · 매일 아침 ${limitName}에 다시 주문 (최대 ${r.valid_days}일)`}
            </div>
            <ul className="divide-y divide-border">
              {plans.map((p) => {
                const [label, cls] = STATUS[p.status] ?? [p.status, "text-muted"];
                const later = p.trade_date < now; // 지정가 후보의 둘째 날부터
                const canApprove = mode === "approve" && !later && ["proposed", "approved", "rejected"].includes(p.status);
                const canDrop = p.status === "approved" && (later || mode === "auto");
                return (
                  <li key={p.id} className="flex items-center justify-between gap-2 py-2">
                    <button
                      type="button"
                      onClick={() => openChart(asTrade({ code: p.code, name: p.name, signal_date: p.signal_date, limit: p.limit_price }), x.spec)}
                      className="min-w-0 text-left"
                    >
                      <div className="truncate">
                        {p.name} <span className="text-xs text-faint">{p.code}</span>
                      </div>
                      <div className="tabular text-xs text-muted">
                        {p.qty.toLocaleString("ko-KR")}주 ·{" "}
                        {p.entry_kind === "limit" ? (
                          <span className="text-mid">
                            {limitName} {won(p.limit_price)}
                          </span>
                        ) : (
                          <>지정가 {won(p.limit_price)}</>
                        )}
                        {p.entry_kind === "limit" && p.valid_until && <> · ~{md(p.valid_until)}</>}
                        {p.msg && <span className="text-faint"> · {p.msg}</span>}
                      </div>
                    </button>
                    {canApprove ? (
                      <div className="flex shrink-0 gap-1">
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => run(() => api(`/api/auto/plans/${p.id}`, { body: { approve: true } }))}
                          className={`rounded-lg px-2.5 py-1 text-xs ${p.status === "approved" ? "bg-accent text-on-accent" : "border border-border"}`}
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
                    ) : canDrop ? (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => run(() => api(`/api/auto/plans/${p.id}`, { body: { approve: false } }), `${p.name} 다음 주문을 멈췄습니다`)}
                        className="shrink-0 rounded-lg border border-border px-2.5 py-1 text-xs"
                      >
                        빼기
                      </button>
                    ) : (
                      <span className={`shrink-0 text-xs ${cls}`}>{label}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        <div className="space-y-1 border-t border-border pt-2">
          <div className="text-xs text-muted">
            보유 {positions.length} · 청산 {x.stats.n}건
            {x.stats.n > 0 && (
              <>
                {" "}
                · 평균 <span className={retClass(x.stats.avg)}>{pct(x.stats.avg, 2, true)}</span>
              </>
            )}
          </div>
          {positions.length > 0 && (
            <ul className="divide-y divide-border">
              {positions.map((p) => (
                <li key={p.id} className="py-2">
                  <div className="flex justify-between gap-2">
                    <button
                      type="button"
                      onClick={() => openChart(asTrade({ code: p.code, name: p.name, signal_date: p.signal_date, limit: p.limit_price }, p), x.spec)}
                      className="min-w-0 truncate text-left"
                    >
                      {p.name} <span className="text-xs text-faint">{p.code}</span>
                    </button>
                    <span className="shrink-0 text-xs text-muted">
                      {p.status === "closing" ? `${p.exit_reason} 매도 중` : `${md(p.entry_date)} 매수`}
                    </span>
                  </div>
                  <div className="flex items-end justify-between gap-2">
                    <div className="tabular text-xs text-muted">
                      {(p.qty - (p.sold_qty ?? 0)).toLocaleString("ko-KR")}주 · {won(p.entry_price)} · 목표 {won(p.target_price || null)} · 손절{" "}
                      {won(p.stop_price || null)}
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
      </div>
    </section>
  );
}

function StrategySettings({ x, busy, run }: { x: AutoStrategy; busy: boolean; run: Run }) {
  const [per, setPer] = useState(String(x.per_trade));
  const [daily, setDaily] = useState(String(x.daily_max));
  const [drop, setDrop] = useState(false);
  const put = (b: object, ok: string) => run(() => api(`/api/auto/strategies/${x.id}`, { method: "PUT", body: b }), ok);
  return (
    <div className="space-y-2 rounded-lg border border-border p-3">
      <div className="grid grid-cols-2 gap-2">
        <label className="block">
          <span className="text-xs text-muted">1회 매수금 (원)</span>
          <NumInput value={per} onChange={setPer} className="mt-1" />
        </label>
        <label className="block">
          <span className="text-xs text-muted">하루 최대 종목</span>
          <NumInput value={daily} onChange={setDaily} className="mt-1" />
        </label>
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => put({ per_trade: Number(per), daily_max: Number(daily) }, "저장했습니다")}
          className="rounded-lg border border-accent px-3 py-1.5 text-xs font-semibold text-accent disabled:opacity-50"
        >
          저장
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => put({ enabled: !x.enabled }, x.enabled ? "이 전략의 새 매수를 멈췄습니다" : "이 전략을 켰습니다")}
          className="rounded-lg border border-border px-3 py-1.5 text-xs disabled:opacity-50"
        >
          {x.enabled ? "이 전략 끄기" : "이 전략 켜기"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            if (!drop) return setDrop(true);
            run(() => api(`/api/auto/strategies/${x.id}`, { method: "DELETE" }), "자동 매매에서 뺐습니다");
          }}
          className={`ml-auto rounded-lg px-3 py-1.5 text-xs ${drop ? "bg-critical text-white" : "border border-border text-muted"}`}
        >
          {drop ? "정말 빼기" : "자동 매매에서 빼기"}
        </button>
      </div>
      <p className="text-[11px] text-faint">끄거나 빼도 이미 산 종목은 규칙대로 계속 팝니다.</p>
    </div>
  );
}

function AddStrategy({ have, busy, onAdd, onCancel }: { have: number[]; busy: boolean; onAdd: (id: number) => void; onCancel: () => void }) {
  const [list, setList] = useState<Strategy[]>([]);
  const [sid, setSid] = useState<number | null>(null);
  useEffect(() => {
    api<Strategy[]>("/api/strategies").then(setList, () => {});
  }, []);
  const left = list.filter((x) => !have.includes(x.id));
  return (
    <div className="space-y-2 rounded-xl border border-dashed border-border p-3 text-sm">
      <label className="block">
        <span className="text-muted">추가할 전략 (저장한 전략에서)</span>
        <select value={sid ?? ""} onChange={(e) => setSid(e.target.value ? +e.target.value : null)} className="mt-1">
          <option value="">고르세요</option>
          {left.map((x) => (
            <option key={x.id} value={x.id}>
              {x.name}
            </option>
          ))}
        </select>
      </label>
      <p className="text-xs text-faint">
        처음엔 1회 300만원 × 하루 5종목으로 시작합니다. 카드의 &apos;설정&apos;에서 바꿀 수 있어요. &apos;당일종가&apos; 매수 전략은 쓸 수 없습니다.
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy || !sid}
          onClick={() => sid && onAdd(sid)}
          className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-on-accent disabled:opacity-50"
        >
          추가
        </button>
        <button type="button" onClick={onCancel} className="rounded-lg border border-border px-3 py-1.5 text-xs">
          취소
        </button>
      </div>
    </div>
  );
}

function GlobalSettings({ v, busy, onSave }: { v: AutoView; busy: boolean; onSave: (b: object) => void }) {
  const s = v.settings;
  const [mode, setMode] = useState(s.mode);
  return (
    <div className="space-y-3 rounded-xl border border-border bg-card p-3 text-sm">
      <button
        type="button"
        disabled={busy}
        onClick={() => onSave({ enabled: !s.enabled })}
        className={`w-full rounded-xl py-2.5 font-semibold disabled:opacity-50 ${s.enabled ? "border border-critical text-critical" : "bg-accent text-on-accent"}`}
      >
        {s.enabled ? "전체 중지 (모든 전략 새 매수 멈춤)" : "자동 매매 켜기"}
      </button>
      <label className="block">
        <span className="text-muted">주문 방식 (모든 전략)</span>
        <select value={mode} onChange={(e) => setMode(e.target.value as "approve" | "auto")} className="mt-1">
          <option value="approve">승인 모드 — 아침에 승인한 종목만 주문</option>
          <option value="auto">완전 자동 — 승인 없이 주문</option>
        </select>
      </label>
      <button
        type="button"
        disabled={busy || mode === s.mode}
        onClick={() => onSave({ mode })}
        className="w-full rounded-xl border border-accent py-2 font-semibold text-accent disabled:opacity-50"
      >
        저장
      </button>
    </div>
  );
}
