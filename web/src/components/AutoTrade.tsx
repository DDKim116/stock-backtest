"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { type Account, asTrade, pnlOf, sizeLabel, wonShort, type AutoStrategy, type AutoView, entryLabel, holdingIssues, man, md, type Plan, type Position, won } from "@/lib/auto";
import { pct, retClass, today } from "@/lib/format";
import type { Bar, Strategy } from "@/lib/types";
import { useBackClose } from "@/lib/useBackClose";
import { type LiveQuote, useLiveQuotes } from "@/lib/useLiveQuotes";
import { useWide } from "@/lib/useWide";
import AutoCompare from "./AutoCompare";
import NumInput from "./NumInput";
import TradeChart from "./TradeChart";
import WatchCard from "./WatchCard";
import { Explain, InfoButton, InfoGroup, Swap } from "./ui/Info";

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
  unknown: ["확인 중", "text-warning"],
  check: ["확인 필요", "text-critical"],
};
const INK = "#10131A";
// 전략 색 고르기 (서버 autotrade.COLORS 와 같음. 라임 = 중간값 선 색)
const COLORS = ["#A99CF5", "#B5E655", "#3CC4B4", "#F08A5D", "#7EC8F2", "#E3B341", "#FF7AB8"]; // 전략 색 띠 위 글자

type Run = (f: () => Promise<unknown>, ok?: string) => Promise<void>;

// 목록에서 고른 것: 매수 후보 · 보유 · 청산 (id 로 찾는다 — 1분마다 새로 불러와도 선택이 유지되게)
type Kind = "plan" | "pos" | "closed";
type Key = { kind: Kind; id: number };
type Item = { kind: "plan"; p: Plan } | { kind: "pos"; p: Position } | { kind: "closed"; p: Position };
const sameKey = (a: Key | null, b: Key | null) => !!a && !!b && a.kind === b.kind && a.id === b.id;

// 화면 곳곳에서 같이 쓰는 값
interface Ctx {
  v: AutoView;
  busy: boolean;
  run: Run;
  quotes: Record<string, LiveQuote>;
  now: string; // 오늘 (기기 날짜)
  strat: (id: number | null | undefined) => AutoStrategy | undefined;
}

// 계좌 칩: 지금 실제로 있는 건 키움 모의투자 자동뿐 (나머지는 준비 중 안내만)
const ACCOUNTS: [string, string, boolean][] = [
  ["mock", "모의 자동", true],
  ["real", "실전 자동", false],
  ["manual", "수동", false],
  ["coin", "코인", false],
];

// 체결일 다음 평일부터 오늘까지 (오늘이 평일이면 포함) — 서버의 보유 거래일 셈과 같은 방식, 공휴일은 빼지 못함
function heldDays(entry: string, todayStr: string): number {
  const d = new Date(entry + "T00:00:00Z");
  const end = new Date(todayStr + "T00:00:00Z");
  let n = 0;
  for (d.setUTCDate(d.getUTCDate() + 1); d <= end; d.setUTCDate(d.getUTCDate() + 1)) {
    const w = d.getUTCDay();
    if (w !== 0 && w !== 6) n++;
  }
  return n;
}
const shiftDate = (d: string, days: number) => {
  const t = new Date(d + "T00:00:00Z");
  t.setUTCDate(t.getUTCDate() + days);
  return t.toISOString().slice(0, 10);
};

// 매매 → 키움 자동. 넓은 화면: 왼쪽 목록(오늘 | 설정) + 오른쪽 고른 종목. 좁은 화면: 한 단, 종목을 누르면 바로 전체 화면 차트
export default function AutoTrade({ onFlash }: { onFlash: (m: string) => void }) {
  const [v, setV] = useState<AutoView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [showLog, setShowLog] = useState(false);
  const [acct, setAcct] = useState<Account | null>(null);
  const wide = useWide();
  const [view, setView] = useState<"today" | "settings">("today");
  const [sel, setSel] = useState<Key | null>(null); // 오른쪽 칸에 보이는 종목 (접었다 펴도 그대로)
  const [chart, setChart] = useState<Key | null>(null); // 전체 화면 차트로 연 종목
  // 차트로 연 종목이 목록에서 사라져도(팔림 등) 차트는 닫힐 때까지 그대로 보이게 열 때의 모습을 같이 둔다
  const [chartSnap, setChartSnap] = useState<Item | null>(null);
  const closeChart = useCallback(() => setChart(null), []);
  const chartBack = useBackClose(!!chart, closeChart);
  const openChart = (k: Key, snap?: Item) => {
    if (!chart) chartBack.push(); // 휴대폰 '뒤로'가 차트만 닫게 (이전/다음으로 바꿀 때는 기록을 더 쌓지 않음)
    setChart(k);
    setChartSnap(snap ?? null);
    setSel(k);
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

  // 보유 종목 현재가 (키움 시세) → 종목별 지금 수익률. 고른 후보 종목도 같이
  const selPlanCode = sel?.kind === "plan" ? v?.plans.find((p) => p.id === sel.id)?.code : undefined;
  const quotes = useLiveQuotes(
    [...(v?.positions ?? []).filter((p) => p.status !== "closed").map((p) => p.code), ...(selPlanCode ? [selPlanCode] : [])],
    !!v?.key_saved,
  );
  if (err) return <p className="text-sm text-critical">⚠ 자동 매매: {err}</p>;
  if (!v) return null;
  const s = v.settings;
  const first = v.strategies[0]?.id;
  const sid = (x: { strategy_id: number | null }) => x.strategy_id ?? first;
  const strat = (id: number | null | undefined) => v.strategies.find((x) => x.id === (id ?? first));
  const active = v.positions.filter((p) => p.status !== "closed");
  const closedAll = v.positions.filter((p) => p.status === "closed");
  const closed = closedAll.slice(0, 12);
  const closedPnl = closedAll.reduce((a, p) => a + (pnlOf(p) ?? 0), 0);
  const waiting = v.plans.filter((p) => p.status === "proposed");
  const now = today();
  const ctx: Ctx = { v, busy, run, quotes, now, strat };

  const find = (k: Key | null): Item | undefined => {
    if (!k) return undefined;
    if (k.kind === "plan") {
      const p = v.plans.find((x) => x.id === k.id);
      return p && { kind: "plan", p };
    }
    const p = (k.kind === "pos" ? active : closed).find((x) => x.id === k.id);
    return p && (k.kind === "pos" ? { kind: "pos", p } : { kind: "closed", p });
  };
  // 이전/다음 종목: 오늘 목록(후보 → 보유) 안에서, 청산에서 열었으면 청산 목록 안에서
  const todayKeys: Key[] = [...v.plans.map((p) => ({ kind: "plan" as const, id: p.id })), ...active.map((p) => ({ kind: "pos" as const, id: p.id }))];
  const closedKeys: Key[] = closed.map((p) => ({ kind: "closed" as const, id: p.id }));
  // 목록에서 누름: 넓은 화면은 오른쪽 칸에 요약, 좁은 화면은 바로 전체 화면 차트
  const pick = (k: Key) => (wide ? setSel(k) : openChart(k, find(k)));
  const selItem = find(sel);
  const chartItem = chart ? (find(chart) ?? chartSnap) : null;
  const navKeys = chart?.kind === "closed" ? closedKeys : todayKeys;
  const navAt = chart ? navKeys.findIndex((k) => sameKey(k, chart)) : -1;

  return (
    <div className="space-y-3">
      <TopAlerts ctx={ctx} acct={acct} waiting={waiting.length} onSettings={() => setView("settings")} onCheck={(k) => {
        setView("today");
        pick(k);
      }} />

      <div className="fold:grid fold:grid-cols-[340px_minmax(0,1fr)] fold:items-start fold:gap-4">
        {/* 왼쪽(좁은 화면에서는 전부): 계좌 칩 → 오늘 | 설정 → 목록 */}
        <div className="min-w-0 space-y-3">
          <AccountChips onFlash={onFlash} />
          <div className="flex gap-0.5 rounded-xl border border-border bg-card p-1" role="group" aria-label="보기">
            {(
              [
                ["today", "오늘"],
                ["settings", "설정"],
              ] as const
            ).map(([k, l]) => (
              <button
                key={k}
                type="button"
                aria-pressed={view === k}
                onClick={() => setView(k)}
                className={`min-h-10 flex-1 rounded-lg text-sm ${view === k ? "bg-accent-soft font-semibold text-accent" : "text-muted"}`}
              >
                {l}
              </button>
            ))}
          </div>

          {view === "today" ? (
            <>
              <StatusLine v={v} acct={acct} />
              <Candidates ctx={ctx} sel={wide ? sel : null} onPick={pick} />
              <Holdings ctx={ctx} positions={active} sel={wide ? sel : null} onPick={pick} />
              {closed.length > 0 && (
                <section className="space-y-1 rounded-xl border border-border bg-card p-4 text-sm">
                  <div className="font-medium">
                    전략대로 청산 {v.stats.n}건{" "}
                    <span className="text-xs text-muted">
                      · 수익 {v.stats.win}건 · 평균 <span className={retClass(v.stats.avg)}>{pct(v.stats.avg, 2, true)}</span> (수수료·세금 전)
                    </span>
                  </div>
                  <div className="text-xs text-muted">
                    청산 손익 합계{" "}
                    <span className={`tabular font-semibold ${retClass(closedPnl)}`}>{wonShort(closedPnl)}</span> (직접 매도 포함 {closedAll.length}건, 수수료·세금 전)
                  </div>
                  {v.stats_manual.n > 0 && (
                    <div className="text-xs text-muted">
                      직접 매도 {v.stats_manual.n}건 · 평균 <span className={retClass(v.stats_manual.avg)}>{pct(v.stats_manual.avg, 2, true)}</span> (백테스트 비교에서는 뺌)
                    </div>
                  )}
                  <ul className="divide-y divide-border">
                    {closed.map((p) => {
                      const k: Key = { kind: "closed", id: p.id };
                      const on = wide && sameKey(sel, k);
                      return (
                        <li key={p.id} className={`-mx-2 flex justify-between gap-2 rounded-lg px-2 py-2 ${on ? "bg-accent-soft" : ""}`}>
                          <button type="button" onClick={() => pick(k)} aria-current={on ? "true" : undefined} className="flex min-w-0 items-center gap-2 truncate text-left">
                            <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: strat(sid(p))?.color ?? "var(--faint)" }} />
                            {p.name} <span className="text-xs text-faint">{p.exit_reason} · {p.exit_date && md(p.exit_date)}</span>
                          </button>
                          <span className="tabular shrink-0 text-right">
                            <span className={retClass(p.ret)}>{pct(p.ret, 2, true)}</span>
                            <span className={`block text-xs ${retClass(pnlOf(p))}`}>{wonShort(pnlOf(p))}</span>
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              )}
            </>
          ) : (
            <>
              <GlobalSettings v={v} busy={busy} onSave={(b) => run(() => api("/api/auto/settings", { method: "PUT", body: b }), "저장했습니다")} />
              {v.strategies.map((x) => (
                <StrategyCard
                  key={x.id}
                  x={x}
                  mode={s.mode}
                  busy={busy}
                  run={run}
                  plans={v.plans.filter((p) => sid(p) === x.id).length}
                  positions={active.filter((p) => sid(p) === x.id)}
                  quotes={quotes}
                />
              ))}
              {v.strategies.length === 0 && <p className="text-sm text-muted">자동 매매할 전략을 추가하세요.</p>}
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
                <button type="button" onClick={() => setAdding(true)} className="min-h-11 w-full rounded-xl border border-dashed border-border py-3 text-sm text-muted">
                  + 전략 추가
                </button>
              )}
              <div className="rounded-xl border border-border bg-card p-1.5">
                <Safety v={v} run={run} busy={busy} />
              </div>
              <AccountUse ctx={ctx} acct={acct} active={active} onRefresh={() => run(async () => setAcct(await api<Account>("/api/auto/account")), "계좌를 새로 불러왔습니다")} />
              {/* 넓은 화면에서는 기대치 vs 실제 · 장중 실시간 감시를 오른쪽 칸에 (왼쪽 340px 칸은 표가 좁아서) */}
              {!wide && (
                <>
                  <AutoCompare />
                  <WatchCard onFlash={onFlash} />
                </>
              )}
              <div>
                <button type="button" onClick={() => setShowLog((x) => !x)} aria-expanded={showLog} className="min-h-9 rounded-lg border border-border px-3 py-1.5 text-xs">
                  기록 {showLog ? "닫기" : "보기"}
                </button>
              </div>
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
              <Swap label="운영 규칙" short={<p className="text-[11px] text-faint">계좌 돈은 전략들이 같이 씀 · 지금은 모의투자에서만 주문</p>}>
                <p className="text-[11px] text-faint">
                  계좌 돈은 모든 전략이 같이 씁니다. 아침 주문은 위 전략 순서대로 나가고, 주문가능금액이 모자라면 그 종목은 건너뜁니다 (지정가 전략은 다음 날 다시
                  시도). &apos;전체 중지&apos;는 새 매수만 멈추고, 이미 산 종목은 목표가·손절·기간 만료 매도를 계속합니다. 지금은 모의투자에서만 주문합니다.
                </p>
              </Swap>
            </>
          )}
        </div>

        {/* 오른쪽(넓은 화면만): 고른 종목 요약 + 작은 차트 + '전체 화면으로 크게' · 안 골랐으면 계좌 요약 */}
        {wide && (
          <aside
            className={`min-w-0 space-y-3 ${view === "today" ? "sticky top-4 max-h-[calc(100dvh-2rem)] overflow-y-auto" : ""}`}
            aria-label="고른 종목"
          >
            {selItem ? (
              <ItemPanel key={`${sel!.kind}-${sel!.id}`} ctx={ctx} item={selItem} onFull={() => openChart(sel!, selItem)} onClear={() => setSel(null)} />
            ) : (
              <>
                <AccountSummary ctx={ctx} acct={acct} active={active} />
                {view === "today" && <AutoCompare />}
              </>
            )}
            {view === "settings" && (
              <>
                <AutoCompare />
                <WatchCard onFlash={onFlash} />
              </>
            )}
          </aside>
        )}
      </div>

      {chart && chartItem && (() => {
        const st = strat(sid(chartItem.p));
        if (!st) return null;
        const p = chartItem.p;
        const trade = chartItem.kind === "plan" ? asTrade({ code: p.code, name: p.name, signal_date: p.signal_date, limit: (p as Plan).limit_price }) : asTrade({ code: p.code, name: p.name, signal_date: (p as Position).signal_date, limit: (p as Position).limit_price }, p as Position);
        const go = (d: -1 | 1) => {
          const k = navKeys[navAt + d];
          if (k) {
            setChart(k);
            setChartSnap(find(k) ?? null);
            setSel(k);
          }
        };
        return (
          <TradeChart
            key={`${chart.kind}-${chart.id}`}
            trade={trade}
            spec={st.spec}
            dataset="kr"
            currency="KRW"
            onClose={chartBack.close}
            head={<PriceHead q={quotes[p.code]} />}
            footer={
              <div className="space-y-2 border-t border-border bg-card px-3 pb-2 pt-2.5 fold:px-4">
                <ItemStats ctx={ctx} item={chartItem} />
                {chartItem.kind === "pos" && chartItem.p.status === "check" && <PosCheck ctx={ctx} p={chartItem.p} />}
                <div className="flex flex-wrap items-center gap-2">
                  <button type="button" disabled={navAt <= 0} onClick={() => go(-1)} className="min-h-11 rounded-lg border border-border px-3 text-sm disabled:opacity-40">
                    ‹ 이전 종목
                  </button>
                  <span className="tabular text-xs text-muted">{navAt >= 0 ? `${navAt + 1}/${navKeys.length}` : ""}</span>
                  <button
                    type="button"
                    disabled={navAt < 0 || navAt >= navKeys.length - 1}
                    onClick={() => go(1)}
                    className="min-h-11 rounded-lg border border-border px-3 text-sm disabled:opacity-40"
                  >
                    다음 종목 ›
                  </button>
                  <div className="ml-auto">
                    <ItemActions ctx={ctx} item={chartItem} big />
                  </div>
                </div>
              </div>
            }
          />
        );
      })()}
    </div>
  );
}

// 늘 화면 위쪽에 보이는 돈·안전 경고 (오늘 | 설정 어느 쪽을 보든)
function TopAlerts({ ctx, acct, waiting, onSettings, onCheck }: { ctx: Ctx; acct: Account | null; waiting: number; onSettings: () => void; onCheck: (k: Key) => void }) {
  const { v, busy, run } = ctx;
  const st = v.state ?? {};
  const todayS = (v.now ?? "").slice(0, 10);
  const paused = !!st.halted && (st.halted === todayS || st.halt_skipped !== st.halted);
  const checkPlans = v.plans.filter((p) => p.status === "check");
  const checkPos = v.positions.filter((p) => p.status === "check");
  const firstCheck: Key | null = checkPos[0] ? { kind: "pos", id: checkPos[0].id } : checkPlans[0] ? { kind: "plan", id: checkPlans[0].id } : null;
  const issues = holdingIssues(acct, v.positions, v.untracked);
  return (
    <>
      {!v.key_saved && <p className="rounded-xl bg-card p-3 text-sm text-warning">설정 → 키움 연결에서 모의투자 키를 먼저 넣어 주세요.</p>}
      {paused && (
        <div className="flex items-center justify-between gap-2 rounded-xl bg-critical/10 p-3 text-sm text-critical">
          <span>
            ⚠ {st.halted_why === "비상 정지 버튼" ? "비상 정지" : st.halted === todayS ? "다음 거래일 매수 쉼" : "오늘 매수 쉼"}: {st.halted_why}
          </span>
          <button type="button" onClick={onSettings} className="min-h-9 shrink-0 rounded-lg border border-critical px-3 text-xs">
            안전장치 보기
          </button>
        </div>
      )}
      {v.deploy && v.deploy.includes("실패") && <p className="rounded-xl bg-critical/10 p-3 text-sm text-critical">⚠ 서버 업데이트 {v.deploy}</p>}
      {firstCheck && (
        <div className="flex items-center justify-between gap-2 rounded-xl bg-critical/10 p-3 text-sm text-critical">
          <span>
            ⚠ 확인 필요 {checkPlans.length + checkPos.length}종목
            <span className="block text-xs">키움 앱에서 주문·잔고를 확인한 뒤 목록에서 정리해 주세요 (그전까지 자동 매매가 손대지 않아요)</span>
          </span>
          <button type="button" onClick={() => onCheck(firstCheck)} className="min-h-9 shrink-0 rounded-lg border border-critical px-3 text-xs">
            보기
          </button>
        </div>
      )}
      {issues.length > 0 && (
        <p className="rounded-xl bg-warning/10 p-3 text-xs text-warning">
          ⚠ 앱 규칙 밖의 키움 보유:{" "}
          {issues
            .map(
              (h) =>
                `${h.name} ${h.qty.toLocaleString("ko-KR")}주${h.kind === "extra" ? " (앱 기록보다 많음)" : ""}${h.ret != null ? ` (${h.ret > 0 ? "+" : ""}${h.ret.toFixed(1)}%)` : ""}`,
            )
            .join(", ")}
          <span className="block text-faint">
            자동매매 규칙(목표·손절·기간만료)이 적용되지 않아요 · 영웅문에서 직접 샀거나, 앱이 체결을 놓친 경우 · 정리하려면 영웅문에서 직접 매도
          </span>
        </p>
      )}
      {waiting > 0 && v.plan_date && (
        <div className="flex items-center justify-between gap-2 rounded-xl bg-accent-soft p-3 text-sm text-accent">
          <span>
            <b>
              {md(v.plan_date)} 후보 {waiting}종목 승인 대기
            </b>
            <span className="block text-xs">08:45 까지 승인한 종목만 08:50 에 주문합니다</span>
          </span>
          <button
            type="button"
            disabled={busy}
            onClick={() => run(() => api("/api/auto/approve-all", { body: { trade_date: v.plan_date } }), "모두 승인했습니다")}
            className="min-h-9 shrink-0 rounded-lg bg-accent-fill px-3 py-1.5 text-xs font-semibold text-on-accent disabled:opacity-50"
          >
            모두 승인
          </button>
        </div>
      )}
    </>
  );
}

function AccountChips({ onFlash }: { onFlash: (m: string) => void }) {
  return (
    <div className="-mx-1 flex items-center gap-1.5 overflow-x-auto px-1 pb-0.5" role="group" aria-label="계좌">
      {ACCOUNTS.map(([k, label, ready]) => (
        <button
          key={k}
          type="button"
          aria-pressed={k === "mock"}
          aria-disabled={!ready || undefined}
          title={ready ? undefined : "준비 중"}
          onClick={() => !ready && onFlash(`${label}은 준비 중이에요 · 지금은 키움 모의투자 자동만 있어요`)}
          className={`min-h-9 shrink-0 whitespace-nowrap rounded-full px-3 text-sm ${
            k === "mock" ? "bg-accent-chip font-semibold text-on-accent-chip" : "border border-dashed border-border text-faint"
          }`}
        >
          {label}
          {!ready && <span className="sr-only"> (준비 중)</span>}
        </button>
      ))}
      <span className="shrink-0 text-[11px] text-faint">점선 = 준비 중</span>
    </div>
  );
}

// 작동 상태 한 줄 (ⓘ 를 누르거나 '자세히'면 오늘 단계·버전·진단 복사·키움 응답 점검)
function StatusLine({ v, acct }: { v: AutoView; acct: Account | null }) {
  const st = v.state ?? {};
  const now = v.now ? new Date(v.now) : null;
  const last = st.last_tick ? new Date(st.last_tick) : null;
  const ago = now && last ? Math.round((now.getTime() - last.getTime()) / 1000) : null;
  const todayS = (v.now ?? "").slice(0, 10);
  const wd = now ? now.getDay() : 0;
  const hm = (v.now ?? "").slice(11, 16);
  const trading = wd >= 1 && wd <= 5;
  const mark = (done: string | undefined, due: string) => (done === todayS ? "✓" : trading && hm >= due ? "✗" : "·");
  const alive = ago != null && ago < 120;
  const errToday = st.last_error && st.last_error.startsWith(todayS);
  const [copied, setCopied] = useState(false);
  const [probed, setProbed] = useState<string | null>(null);
  // 실전 전 확인: 키움 미체결·체결 응답의 '모양'(칸 이름·줄 수·연속조회 헤더·해석한 줄 수)을 복사 (값·계좌번호 없음)
  const probe = async () => {
    setProbed("확인 중…");
    try {
      const r = await api(`/api/auto/kiwoom-probe`);
      await copyText(`[키움 응답 점검]\n${JSON.stringify(r, null, 2)}`);
      setProbed("복사됨 ✓");
    } catch (e) {
      setProbed(`실패: ${e instanceof Error ? e.message : String(e)}`.slice(0, 60));
    }
    setTimeout(() => setProbed(null), 4000);
  };
  // 문제가 생겼을 때 그대로 붙여 보낼 수 있게: 상태 · 보유 종목 · 최근 기록 (키·계좌번호 같은 비밀은 없음)
  const copy = async () => {
    const pos = v.positions
      .filter((p) => p.status !== "closed")
      .map((p) => {
        const x = p as unknown as Record<string, unknown>;
        return `  ${p.name}(${p.code}) ${p.status} · ${p.entry_date} 매수 ${p.qty}주 @${p.entry_price} · 판 ${p.sold_qty ?? 0}주 · 목표주문 ${x.target_order ?? "-"}(${x.target_date ?? "-"}) · 매도주문 ${x.exit_order ?? "-"} ${p.exit_reason ?? ""} · 보유기간 ${x.hold_days ?? "-"}일`;
      });
    const text = [
      `[자동 매매 진단] ${v.now} · 버전 ${v.version} · ${v.settings.enabled ? "켜짐" : "꺼짐"} · ${v.settings.mode}`,
      `배포: ${v.deploy ?? "-"}`,
      `상태: ${JSON.stringify(st)}`,
      `보유 ${pos.length}:`,
      ...pos,
      ...(acct
        ? [`키움 잔고 ${acct.balance.holdings.length}종목 · 앱 규칙 밖: ${holdingIssues(acct, v.positions, v.untracked).map((h) => `${h.name}(${h.code}) ${h.qty}주${h.kind === "extra" ? "(기록보다 많음)" : ""} @${h.avg_price ?? "-"} ${h.ret ?? ""}%`).join(", ") || "없음"}`]
        : []),
      `기록 (최근 ${v.log.length}):`,
      ...v.log.map((l) => `  ${l.ts} ${l.text}`),
    ].join("\n");
    await copyText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <InfoGroup>
      <div className="space-y-1 rounded-xl border border-border bg-card px-3 py-2 text-xs">
        <div className="flex items-center gap-2">
          <span className={`shrink-0 font-semibold ${alive ? "text-accent" : "text-critical"}`}>● {alive ? "작동 중" : "멈춤?"}</span>
          <span className="min-w-0 flex-1 truncate text-muted">
            {v.settings.enabled ? "켜짐" : "꺼짐"} · {v.settings.mode === "auto" ? "완전 자동" : "승인 모드"} ·{" "}
            {last ? `확인 ${st.last_tick!.slice(11, 19)}${ago != null ? ` (${ago}초 전)` : ""}` : "기록 없음"}
          </span>
          <InfoButton label="작동 상태" />
        </div>
        {errToday && (() => {
          // HTML 오류 페이지(키움 502 등)는 태그를 빼고 짧게 · 30분 넘게 지난 오류는 '지나간 오류'로 흐리게
          const raw = st.last_error!.slice(11);
          const msg = raw.includes("<") ? raw.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 80) : raw;
          const at = new Date(st.last_error!.slice(0, 19));
          const old = now ? (now.getTime() - at.getTime()) / 60000 > 30 : false;
          return old ? <div className="text-faint">지나간 오류 {msg} · 지금은 정상</div> : <div className="text-warning">마지막 오류 {msg}</div>;
        })()}
        <Explain label="작동 상태">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border pt-1.5">
            <span className="tabular text-muted">
              오늘 매수 {mark(st.buy_done, "08:50")} · 기간만료 {mark(st.expire_done, "14:32")} · 마무리 {mark(st.close_done, "15:32")}
            </span>
            {v.version && <span className="tabular text-faint">버전 {v.version}</span>}
            <button type="button" onClick={copy} className="min-h-8 rounded-md border border-border px-2 py-0.5 text-muted">
              {copied ? "복사됨 ✓" : "진단 복사"}
            </button>
            <button type="button" onClick={probe} className="min-h-8 rounded-md border border-border px-2 py-0.5 text-muted">
              {probed ?? "키움 응답 점검"}
            </button>
          </div>
        </Explain>
      </div>
    </InfoGroup>
  );
}

// 매수 후보 (모든 전략, 전략 색 점)
function Candidates({ ctx, sel, onPick }: { ctx: Ctx; sel: Key | null; onPick: (k: Key) => void }) {
  const { v, strat } = ctx;
  const plans = v.plans;
  const waitingN = plans.filter((p) => p.status === "proposed").length;
  const dates = [...new Set(plans.map((p) => p.trade_date))];
  // 지정가 전략 안내: 후보가 있는 지정가 전략마다
  const limitNotes = v.strategies.filter((x) => x.rules.entry === "limit" && plans.some((p) => (p.strategy_id ?? v.strategies[0]?.id) === x.id));
  return (
    <section className="rounded-xl border border-border bg-card px-3 pb-1 pt-2.5 text-sm" aria-label="매수 후보">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 className="font-semibold">
          매수 후보 <span className="tabular text-muted">{plans.length}</span>
        </h3>
        <span className="text-xs text-muted">
          {waitingN > 0 ? <span className="text-warning">승인 대기 {waitingN} · 08:45 마감</span> : plans.length > 0 ? dates.map(md).join(" · ") : null}
        </span>
      </div>
      {v.last_plan && v.last_plan.count === 0 && v.last_plan.trade_date >= (v.plan_date ?? "") && (
        <p className="pb-1 text-xs text-muted">
          {md(v.last_plan.trade_date)} 매수 후보 없음 · {md(v.last_plan.signal_date)}에는 켜진 전략의 신호가 없었습니다.
        </p>
      )}
      {plans.length === 0 ? (
        <div className="pb-2">
          <Swap label="후보 만드는 때" short={<p className="text-xs text-faint">지금 후보 없음 · 15:50~16:40쯤 새로 만듦</p>}>
            <p className="text-xs text-faint">지금 후보 없음 · 장 마감 뒤(15:50쯤, 늦으면 16:40) 새 데이터가 들어오면 다음 거래일 후보를 만듭니다</p>
          </Swap>
        </div>
      ) : (
        <>
          {limitNotes.map((x) => (
            <p key={x.id} className="text-[11px] text-muted">
              {v.strategies.length > 1 && `${x.name}: `}매일 아침 {x.midpoint ? "중간값" : "지정가"}에 다시 주문 (최대 {x.rules.valid_days}일)
            </p>
          ))}
          <ul className="divide-y divide-border">
            {plans.map((p) => {
              const k: Key = { kind: "plan", id: p.id };
              const on = sameKey(sel, k);
              const x = strat(p.strategy_id);
              const limitName = x?.midpoint ? "중간값" : "지정가";
              return (
                <li key={p.id} className={`-mx-2 flex items-center gap-2 rounded-lg px-2 py-2 ${on ? "bg-accent-soft" : ""}`}>
                  <button type="button" onClick={() => onPick(k)} aria-current={on ? "true" : undefined} className="flex min-w-0 flex-1 items-start gap-2 text-left">
                    <span className="mt-1.5 h-2 w-2 shrink-0 rounded-sm border-[1.5px]" style={{ borderColor: x?.color ?? "var(--faint)" }} aria-hidden />
                    <span className="min-w-0">
                      <span className="block truncate">
                        {p.name} <span className="text-xs text-faint">{p.code}</span>
                      </span>
                      <span className="tabular block text-xs text-muted">
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
                      </span>
                    </span>
                  </button>
                  <PlanActions ctx={ctx} p={p} />
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}

// 후보 하나의 행동: 확인 필요 → 샀음/안 샀음 · 승인 모드 → 승인/빼기 · 이미 승인된 지정가 → 빼기 · 그 밖엔 상태
function PlanActions({ ctx, p, big }: { ctx: Ctx; p: Plan; big?: boolean }) {
  const { v, busy, run, now } = ctx;
  const mode = v.settings.mode;
  const [label, cls] = STATUS[p.status] ?? [p.status, "text-muted"];
  const later = p.trade_date < now; // 지정가 후보의 둘째 날부터
  const canApprove = mode === "approve" && !later && ["proposed", "approved", "rejected"].includes(p.status);
  const canDrop = p.status === "approved" && (later || mode === "auto");
  const sz = big ? "min-h-11 px-4 text-sm" : "min-h-9 px-2.5 text-xs";
  if (p.status === "check")
    // 결과 모르는 매수: 키움 앱에서 확인한 대로 정리 (앱은 스스로 받지 않음)
    return (
      <div className="flex shrink-0 gap-1">
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            const q = window.prompt(`${p.name}: 키움 앱에서 확인한 체결 수량 (1~${p.qty}주)`, String(p.qty));
            if (!q) return;
            const pr = window.prompt(`${p.name}: 체결 평균가 (원)`, String(p.limit_price));
            if (!pr) return;
            run(() => api(`/api/auto/plans/${p.id}/resolve`, { body: { action: "bought", qty: Number(q), price: Number(pr) } }), `${p.name} 매수를 기록했습니다`);
          }}
          className={`rounded-lg border border-border ${sz}`}
        >
          샀음
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => run(() => api(`/api/auto/plans/${p.id}/resolve`, { body: { action: "not_bought" } }), `${p.name} 안 산 것으로 정리했습니다`)}
          className={`rounded-lg border border-border ${sz}`}
        >
          안 샀음
        </button>
      </div>
    );
  if (canApprove)
    return (
      <div className="flex shrink-0 gap-1">
        <button
          type="button"
          disabled={busy}
          aria-label={`${p.name} 승인`}
          aria-pressed={p.status === "approved"}
          onClick={() => run(() => api(`/api/auto/plans/${p.id}`, { body: { approve: true } }))}
          className={`rounded-lg ${sz} ${p.status === "approved" ? "bg-accent-chip text-on-accent-chip" : "border border-border"}`}
        >
          승인
        </button>
        <button
          type="button"
          disabled={busy}
          aria-label={`${p.name} 빼기`}
          aria-pressed={p.status === "rejected"}
          onClick={() => run(() => api(`/api/auto/plans/${p.id}`, { body: { approve: false } }))}
          className={`rounded-lg ${sz} ${p.status === "rejected" ? "bg-foreground text-background" : "border border-border"}`}
        >
          빼기
        </button>
      </div>
    );
  if (canDrop)
    return (
      <button
        type="button"
        disabled={busy}
        aria-label={`${p.name} 빼기`}
        onClick={() => run(() => api(`/api/auto/plans/${p.id}`, { body: { approve: false } }), `${p.name} 다음 주문을 멈췄습니다`)}
        className={`shrink-0 rounded-lg border border-border ${sz}`}
      >
        빼기
      </button>
    );
  return <span className={`shrink-0 text-xs ${cls}`}>{label}</span>;
}

// 보유 종목 (모든 전략): 수익률 · 손절~목표 사이 어디인지 · n일째/보유기간
function Holdings({ ctx, positions, sel, onPick }: { ctx: Ctx; positions: Position[]; sel: Key | null; onPick: (k: Key) => void }) {
  const { quotes, strat, now } = ctx;
  const evalSum = positions.reduce(
    (a, p) => {
      const q = quotes[p.code];
      if (!q) return a;
      const n = p.qty - (p.sold_qty ?? 0);
      return { cost: a.cost + n * p.entry_price, pnl: a.pnl + n * (q.price - p.entry_price) };
    },
    { cost: 0, pnl: 0 },
  );
  return (
    <section className="rounded-xl border border-border bg-card px-3 pb-1 pt-2.5 text-sm" aria-label="보유 종목">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 className="font-semibold">
          보유 <span className="tabular text-muted">{positions.length}</span>
        </h3>
        {evalSum.cost > 0 && (
          <span className="tabular text-xs text-muted">
            평가 <span className={retClass(evalSum.pnl)}>{wonShort(evalSum.pnl)}</span> (
            <span className={retClass(evalSum.pnl)}>{pct((evalSum.pnl / evalSum.cost) * 100, 2, true)}</span>)
          </span>
        )}
      </div>
      {positions.length === 0 ? (
        <p className="pb-2 text-xs text-faint">보유 종목이 없습니다</p>
      ) : (
        <ul className="divide-y divide-border">
          {positions.map((p) => {
            const k: Key = { kind: "pos", id: p.id };
            const on = sameKey(sel, k);
            const x = strat(p.strategy_id);
            const q = quotes[p.code];
            const ret = q ? (q.price / p.entry_price - 1) * 100 : null;
            const hold = (p as unknown as { hold_days?: number | null }).hold_days ?? x?.rules.hold ?? null;
            return (
              <li key={p.id} className={`-mx-2 rounded-lg px-2 py-2 ${on ? "bg-accent-soft" : ""}`}>
                <button type="button" onClick={() => onPick(k)} aria-current={on ? "true" : undefined} className="block w-full text-left">
                  <span className="flex items-center gap-2">
                    <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: x?.color ?? "var(--faint)" }} aria-hidden />
                    <span className="min-w-0 flex-1 truncate">
                      {p.name} <span className="text-xs text-faint">{p.code}</span>
                    </span>
                    <span className={`tabular shrink-0 font-semibold ${retClass(ret)}`}>{q ? pct(ret, 2, true) : "–"}</span>
                  </span>
                  <RangeBar p={p} price={q?.price ?? null} x={x} />
                  <span className="tabular flex justify-between text-[11px] text-muted">
                    <span>손절 −{x?.rules.stop ?? "–"}%</span>
                    <span>
                      {p.held ?? heldDays(p.entry_date, now)}일째{hold ? ` / ${hold}` : ""}
                    </span>
                    <span>목표 +{x?.rules.target ?? "–"}%</span>
                  </span>
                  <span className="tabular mt-0.5 block text-xs text-muted">
                    {(p.qty - (p.sold_qty ?? 0)).toLocaleString("ko-KR")}주 · {won(p.entry_price)} → <span className="text-foreground">{won(q?.price)}</span> · {posNote(p)}
                  </span>
                </button>
                {p.status === "check" && <PosCheck ctx={ctx} p={p} />}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

const posNote = (p: Position) =>
  p.status === "closing" ? `${p.exit_reason} 매도 중` : p.status === "check" ? "확인 필요" : p.carry ? `다음 장 시작에 ${p.carry} 이어서` : `${md(p.entry_date)} 매수`;

// 손절가 ~ 목표가 사이에서 지금 가격이 어디인지 (세로선 = 매수가)
function RangeBar({ p, price, x }: { p: Position; price: number | null; x?: AutoStrategy }) {
  const lo = p.stop_price || null;
  const hi = p.target_price || null;
  if (!lo || !hi || hi <= lo) return <span className="my-1.5 block h-1.5 rounded-full bg-[var(--bar-track)]" aria-hidden />;
  const at = (v: number) => `${Math.min(100, Math.max(0, ((v - lo) / (hi - lo)) * 100))}%`;
  const up = price != null && price >= p.entry_price;
  return (
    <span
      className="relative my-1.5 block h-1.5 rounded-full bg-[var(--bar-track)]"
      role="img"
      aria-label={`손절 ${won(lo)}(−${x?.rules.stop ?? "?"}%) ~ 목표 ${won(hi)}(+${x?.rules.target ?? "?"}%) 사이, 매수가 ${won(p.entry_price)}, 지금 ${won(price)}`}
    >
      <span className="absolute -top-0.5 h-2.5 w-0.5 -translate-x-1/2 rounded bg-foreground/60" style={{ left: at(p.entry_price) }} />
      {price != null && (
        <span
          className={`absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card ${up ? "bg-up" : "bg-down"}`}
          style={{ left: at(price) }}
        />
      )}
    </span>
  );
}

// '확인 필요' 보유: 키움 앱에서 확인한 대로 정리 (늘 보이게 목록 안에)
function PosCheck({ ctx, p }: { ctx: Ctx; p: Position }) {
  const { busy, run } = ctx;
  return (
    <div className="mt-1 rounded-md bg-critical/10 p-2 text-xs text-critical">
      ⚠ {p.check_msg ?? "확인 필요"}
      <span className="block text-faint">
        키움 앱에서 이 종목 주문·잔고를 확인한 뒤 골라 주세요 (그전까지 자동 매매가 손대지 않아요) · &apos;다시 관리&apos;는 키움에 걸린 매도 주문이 없을 때만
      </span>
      <div className="mt-1 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => run(() => api(`/api/auto/positions/${p.id}/resolve`, { body: { action: "resume" } }), `${p.name} 다시 관리합니다`)}
          className="min-h-9 rounded-lg border border-border px-2 py-0.5 text-foreground"
        >
          걸린 주문 없음 · 다시 관리
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => run(() => api(`/api/auto/positions/${p.id}/resolve`, { body: { action: "close" } }), `${p.name} 정리됨으로 기록했습니다`)}
          className="min-h-9 rounded-lg border border-border px-2 py-0.5 text-foreground"
        >
          이미 정리됨
        </button>
      </div>
    </div>
  );
}

// 보유 종목 '지금 팔기' (두 번 눌러 확인)
function SellNow({ ctx, p, big }: { ctx: Ctx; p: Position; big?: boolean }) {
  const { busy, run } = ctx;
  // 확인 상태는 '이 종목'에만 묶는다 (다른 종목을 고르면 풀림) · 5초 지나면 저절로 풀림
  const [armedId, setArmedId] = useState<number | null>(null);
  const armed = armedId === p.id;
  useEffect(() => {
    if (armedId == null) return;
    const t = setTimeout(() => setArmedId(null), 5000);
    return () => clearTimeout(t);
  }, [armedId]);
  if (p.status !== "open") return null;
  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => {
        if (!armed) return setArmedId(p.id);
        setArmedId(null);
        run(() => api(`/api/auto/positions/${p.id}/sell`, { method: "POST" }), `${p.name} 시장가 매도 주문을 냈습니다`);
      }}
      className={`shrink-0 rounded-lg ${big ? "min-h-11 px-4 text-sm" : "min-h-9 px-3 text-xs"} ${armed ? "bg-critical text-white" : "border border-border"}`}
    >
      {armed ? "정말 팔기" : "지금 팔기"}
    </button>
  );
}

function ItemActions({ ctx, item, big }: { ctx: Ctx; item: Item; big?: boolean }) {
  if (item.kind === "plan") return <PlanActions ctx={ctx} p={item.p} big={big} />;
  if (item.kind === "pos") return <SellNow ctx={ctx} p={item.p} big={big} />;
  return null;
}

// 종목 요약 숫자 4칸 (오른쪽 칸 · 전체 화면 차트 아래)
function ItemStats({ ctx, item }: { ctx: Ctx; item: Item }) {
  const { quotes, strat, now } = ctx;
  let rows: [string, string, string?][];
  if (item.kind === "pos") {
    const p = item.p;
    const q = quotes[p.code];
    const n = p.qty - (p.sold_qty ?? 0);
    const ret = q ? (q.price / p.entry_price - 1) * 100 : null;
    const pnl = q ? n * (q.price - p.entry_price) : null;
    const hold = (p as unknown as { hold_days?: number | null }).hold_days ?? strat(p.strategy_id)?.rules.hold ?? null;
    rows = [
      ["수익", q ? pct(ret, 2, true) : "–", retClass(ret)],
      ["평가 손익", wonShort(pnl), retClass(pnl)],
      ["보유", `${p.held ?? heldDays(p.entry_date, now)}일째${hold ? ` / ${hold}` : ""}`],
      ["수량", `${n.toLocaleString("ko-KR")}주`],
    ];
  } else if (item.kind === "plan") {
    const p = item.p;
    const x = strat(p.strategy_id);
    rows = [
      ["수량", `${p.qty.toLocaleString("ko-KR")}주`],
      [p.entry_kind === "limit" && x?.midpoint ? "중간값" : "지정가", won(p.limit_price)],
      ["상태", (STATUS[p.status] ?? [p.status])[0], (STATUS[p.status] ?? [p.status, ""])[1]],
      ["주문일", `${md(p.trade_date)}${p.valid_until ? `~${md(p.valid_until)}` : ""}`],
    ];
  } else {
    const p = item.p;
    rows = [
      ["수익", pct(p.ret, 2, true), retClass(p.ret)],
      ["손익", wonShort(pnlOf(p)), retClass(pnlOf(p))],
      ["매도", `${p.exit_reason ?? "–"} ${md(p.exit_date)}`],
      ["수량", `${p.qty.toLocaleString("ko-KR")}주`],
    ];
  }
  return (
    <dl className="tabular grid grid-cols-4 gap-1 text-center">
      {rows.map(([k, val, cls]) => (
        <div key={k} className="min-w-0 rounded-lg bg-background px-1 py-1.5">
          <dt className="truncate text-[11px] text-muted">{k}</dt>
          <dd className={`truncate text-sm font-semibold ${cls ?? ""}`}>{val}</dd>
        </div>
      ))}
    </dl>
  );
}

// 현재가 · 전일 대비 (키움 시세가 있을 때)
function PriceHead({ q, fallback }: { q?: LiveQuote; fallback?: { price: number; chg: number | null } | null }) {
  const price = q?.price ?? fallback?.price ?? null;
  const chg = q ? q.change_pct : (fallback?.chg ?? null);
  if (price == null) return null;
  return (
    <div className="tabular leading-tight">
      <div className="text-lg font-bold">{Math.round(price).toLocaleString("ko-KR")}</div>
      <div className={`text-xs ${retClass(chg)}`}>
        {pct(chg, 2, true)} <span className="text-muted">{q ? "전일 대비" : "마지막 종가"}</span>
      </div>
    </div>
  );
}

// 오른쪽 칸: 고른 종목
function ItemPanel({ ctx, item, onFull, onClear }: { ctx: Ctx; item: Item; onFull: () => void; onClear: () => void }) {
  const { v, quotes, strat } = ctx;
  const p = item.p;
  const x = strat(p.strategy_id);
  const q = quotes[p.code];
  const [last, setLast] = useState<{ price: number; chg: number | null } | null>(null);
  const kindLabel = item.kind === "plan" ? "매수 후보" : item.kind === "pos" ? "보유" : "청산";
  const pos = item.kind !== "plan" ? (item.p as Position) : null;
  const tgtLive = pos && (pos as unknown as { target_order?: string | null; target_date?: string | null });
  return (
    <section className="space-y-3 rounded-xl border border-border bg-card p-4" aria-label={`${p.name} 요약`}>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-lg font-bold">
            {p.name} <span className="text-sm font-normal text-muted">{p.code}</span>
          </h3>
          <p className="flex items-center gap-1.5 text-xs text-muted">
            <span className="h-2 w-2 rounded-sm" style={{ background: x?.color ?? "var(--faint)" }} aria-hidden />
            {x?.name ?? "전략"} · {kindLabel}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <PriceHead q={q} fallback={last} />
        </div>
        <button type="button" onClick={onClear} aria-label="선택 해제 (계좌 요약 보기)" className="-mr-2 -mt-2 flex h-10 w-10 shrink-0 items-center justify-center text-muted">
          ✕
        </button>
      </div>
      <MiniChart code={p.code} pos={pos} limit={item.kind === "plan" ? item.p.limit_price : null} onLast={setLast} onOpen={onFull} />
      <button type="button" onClick={onFull} className="min-h-11 w-full rounded-xl border border-accent text-sm font-semibold text-accent">
        전체 화면으로 크게 ⤢
      </button>
      <ItemStats ctx={ctx} item={item} />
      {pos && item.kind === "pos" && (
        <p className="tabular text-xs text-muted">
          매수 {won(pos.entry_price)} ({md(pos.entry_date)}) · 목표 {won(pos.target_price || null)} · 손절 {won(pos.stop_price || null)}
          <span className="block">
            {pos.status === "open" && tgtLive?.target_order && tgtLive.target_date === (v.now ?? "").slice(0, 10)
              ? `목표가 주문 ${won(pos.target_price)} 걸려 있음`
              : posNote(pos)}
          </span>
        </p>
      )}
      {item.kind === "plan" && item.p.msg && <p className="text-xs text-faint">{item.p.msg}</p>}
      {item.kind === "pos" && item.p.status === "check" && <PosCheck ctx={ctx} p={item.p} />}
      <div className="flex justify-end">
        <ItemActions ctx={ctx} item={item} big />
      </div>
    </section>
  );
}

// 작은 차트 미리보기 (최근 약 60거래일 · 목표·매수·손절 또는 지정가 선). 누르면 전체 화면
function MiniChart({
  code,
  pos,
  limit,
  onLast,
  onOpen,
}: {
  code: string;
  pos: Position | null;
  limit: number | null;
  onLast: (v: { price: number; chg: number | null } | null) => void;
  onOpen: () => void;
}) {
  const [bars, setBars] = useState<Bar[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let stop = false;
    const end = today();
    const q = new URLSearchParams({ dataset: "kr", code, start: shiftDate(end, -130), end });
    api<{ bars: Bar[] }>(`/api/chart?${q}`)
      .then((r) => {
        if (stop) return;
        const b = r.bars.slice(-60);
        setBars(b);
        setFailed(false);
        const n = b.length;
        onLast(n ? { price: b[n - 1].c, chg: n > 1 ? (b[n - 1].c / b[n - 2].c - 1) * 100 : null } : null);
      })
      .catch(() => {
        if (stop) return;
        setBars([]);
        setFailed(true);
        onLast(null);
      });
    return () => {
      stop = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 종목이 바뀔 때만 다시 받는다
  }, [code]);
  const W = 400;
  const H = 160;
  const lines: [number, string, string][] = [];
  if (pos) {
    if (pos.target_price) lines.push([pos.target_price, "var(--up)", "목표"]);
    lines.push([pos.entry_price, "var(--foreground)", "매수"]);
    if (pos.stop_price) lines.push([pos.stop_price, "var(--down)", "손절"]);
  } else if (limit) lines.push([limit, "var(--mid)", "지정가"]);
  if (!bars) return <div className="h-40 rounded-lg bg-background" aria-hidden />;
  if (!bars.length)
    return <p className="flex h-40 items-center justify-center rounded-lg bg-background text-xs text-muted">{failed ? "차트 자료를 못 불러왔어요" : "최근 차트 자료가 없어요"}</p>;
  const vals = [...bars.flatMap((b) => [b.h, b.l]), ...lines.map((l) => l[0])];
  const max = Math.max(...vals);
  const min = Math.min(...vals);
  const y = (v: number) => 6 + ((max - v) / (max - min || 1)) * (H - 12);
  const step = W / bars.length;
  return (
    <button type="button" onClick={onOpen} className="block w-full rounded-lg bg-background p-1" aria-label="차트 전체 화면으로 크게 보기">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-40 w-full" preserveAspectRatio="none" role="img" aria-label={`최근 ${bars.length}거래일 일봉`}>
        {lines.map(([v, c]) => (
          <line key={c} x1={0} x2={W} y1={y(v)} y2={y(v)} stroke={c} strokeDasharray="4 4" strokeWidth={1.3} vectorEffect="non-scaling-stroke" />
        ))}
        {bars.map((b, i) => {
          const up = b.c >= b.o;
          const c = up ? "var(--up)" : "var(--down)";
          const cx = i * step + step / 2;
          return (
            <g key={b.t}>
              <line x1={cx} x2={cx} y1={y(b.h)} y2={y(b.l)} stroke={c} strokeWidth={1} vectorEffect="non-scaling-stroke" />
              <rect x={cx - step * 0.32} width={step * 0.64} y={y(Math.max(b.o, b.c))} height={Math.max(1, Math.abs(y(b.o) - y(b.c)))} fill={c} />
            </g>
          );
        })}
      </svg>
      {lines.length > 0 && (
        <span className="tabular flex flex-wrap justify-center gap-x-3 pt-0.5 text-[11px] text-muted">
          {lines.map(([v, c, l]) => (
            <span key={l} className="inline-flex items-center gap-1">
              <span className="inline-block w-3 border-t border-dashed" style={{ borderColor: c }} />
              {l} {Math.round(v).toLocaleString("ko-KR")}
            </span>
          ))}
        </span>
      )}
    </button>
  );
}

// 오른쪽 칸 (아무것도 안 골랐을 때): 계좌 요약
function AccountSummary({ ctx, acct, active }: { ctx: Ctx; acct: Account | null; active: Position[] }) {
  const { v } = ctx;
  const cells: [string, string, string?][] = [
    ["추정자산", acct ? won(acct.balance.assets) : "–"],
    ["평가손익", acct ? won(acct.balance.pnl) : "–", retClass(acct?.balance.pnl)],
    ["주문가능", acct ? won(acct.cash.orderable) : "–"],
    ["보유", `${active.length}종목${acct ? ` · 키움 ${acct.balance.holdings.length}` : ""}`],
  ];
  return (
    <section className="space-y-2 rounded-xl border border-border bg-card p-4" aria-label="계좌 요약">
      <h3 className="font-semibold">키움 모의투자 계좌</h3>
      <dl className="tabular grid grid-cols-2 gap-2">
        {cells.map(([k, val, cls]) => (
          <div key={k} className="rounded-lg bg-background px-3 py-2">
            <dt className="text-xs text-muted">{k}</dt>
            <dd className={`text-lg font-semibold ${cls ?? ""}`}>{val}</dd>
          </div>
        ))}
      </dl>
      {!acct && <p className="text-xs text-faint">{v.key_saved ? "키움 계좌를 아직 못 읽었어요 (설정의 '계좌 새로고침')" : "키움 키를 넣으면 계좌 값이 보입니다"}</p>}
      <p className="text-xs text-faint">왼쪽 목록에서 종목을 누르면 여기에 요약과 차트가 나와요.</p>
    </section>
  );
}

// 설정: 계좌 사용 (전략 색으로 나눠 보기) · 계좌 새로고침 · 지금 후보 만들기
function AccountUse({ ctx, acct, active, onRefresh }: { ctx: Ctx; acct: Account | null; active: Position[]; onRefresh: () => void }) {
  const { v, busy, run } = ctx;
  const first = v.strategies[0]?.id;
  const used = (id: number) =>
    active.filter((p) => (p.strategy_id ?? first) === id).reduce((a, p) => a + p.entry_price * (p.qty - (p.sold_qty ?? 0)), 0);
  const usedAll = v.strategies.reduce((a, x) => a + used(x.id), 0);
  const total = acct?.balance.assets ?? null;
  const base = Math.max(total ?? 0, usedAll + (acct?.cash.orderable ?? 0), 1);
  return (
    <section className="space-y-2 rounded-xl border border-border bg-card p-4 text-sm" aria-label="계좌 사용">
      <div className="flex justify-between gap-2">
        <span className="shrink-0 text-muted">계좌 사용</span>
        <span className="tabular text-right">
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
          추정자산 {won(acct.balance.assets)} · 평가손익 <span className={retClass(acct.balance.pnl)}>{won(acct.balance.pnl)}</span> · 키움 보유{" "}
          {acct.balance.holdings.length}종목
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={busy || !v.key_saved} onClick={onRefresh} className="min-h-9 rounded-lg border border-border px-3 py-1.5 text-xs disabled:opacity-50">
          계좌 새로고침
        </button>
        <button
          type="button"
          disabled={busy || !v.key_saved}
          onClick={() => run(() => api("/api/auto/plan-now", { method: "POST" }), "지금 데이터로 다음 거래일 후보를 만들었습니다")}
          className="min-h-9 rounded-lg border border-border px-3 py-1.5 text-xs disabled:opacity-50"
        >
          지금 후보 만들기
        </button>
      </div>
    </section>
  );
}

// 설정: 전략 카드 (색 띠 · 규칙 · 설정 열기). 후보·보유 목록은 '오늘'에
function StrategyCard({
  x,
  mode,
  busy,
  run,
  plans,
  positions,
  quotes,
}: {
  x: AutoStrategy;
  mode: "approve" | "auto";
  busy: boolean;
  run: Run;
  plans: number;
  positions: Position[];
  quotes: Record<string, LiveQuote>;
}) {
  const [edit, setEdit] = useState(false);
  const r = x.rules;
  const evalSum = positions.reduce(
    (a, p) => {
      const q = quotes[p.code];
      if (!q) return a;
      const n = p.qty - (p.sold_qty ?? 0);
      return { cost: a.cost + n * p.entry_price, pnl: a.pnl + n * (q.price - p.entry_price) };
    },
    { cost: 0, pnl: 0 },
  );
  return (
    <section className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between gap-2 px-4 py-2.5" style={{ background: x.color, color: INK }}>
        <span className="truncate font-bold">{x.name}</span>
        <span className="flex shrink-0 items-center gap-2 text-xs font-semibold">
          {x.enabled ? `켜짐 · ${mode === "auto" ? "자동" : "승인"}` : "꺼짐"}
          <button type="button" onClick={() => setEdit((e) => !e)} aria-expanded={edit} className="min-h-8 rounded-md px-2 py-0.5" style={{ background: "#10131A22" }}>
            {edit ? "닫기" : "설정"}
          </button>
        </span>
      </div>
      <div className="space-y-2 px-3 pb-3 pt-3 text-sm">
        <p className="tabular text-xs text-muted">
          {entryLabel(x)} · {sizeLabel(x)} × 하루 {x.daily_max} · +{r.target ?? "–"} / −{r.stop ?? "–"} · {r.hold}일
        </p>
        <p className="tabular text-xs text-muted">
          후보 {plans} · 보유 {positions.length}
          {evalSum.cost > 0 && (
            <>
              {" "}· 평가 <span className={retClass(evalSum.pnl)}>{wonShort(evalSum.pnl)}</span> (
              <span className={retClass(evalSum.pnl)}>{pct((evalSum.pnl / evalSum.cost) * 100, 2, true)}</span>)
            </>
          )}{" "}
          · 청산 {x.stats.n}건
          {x.stats.n > 0 && (
            <>
              {" "}· 평균 <span className={retClass(x.stats.avg)}>{pct(x.stats.avg, 2, true)}</span>
            </>
          )}
        </p>
        {edit && <StrategySettings x={x} busy={busy} run={run} />}
      </div>
    </section>
  );
}

function StrategySettings({ x, busy, run }: { x: AutoStrategy; busy: boolean; run: Run }) {
  const [per, setPer] = useState(String(x.per_trade));
  const [daily, setDaily] = useState(String(x.daily_max));
  const [sizeMode, setSizeMode] = useState<"fixed" | "pct">(x.size_mode ?? "fixed");
  const [pctV, setPctV] = useState(String(x.pct ?? 10));
  const [cap, setCap] = useState(String(x.max_per_trade ?? 20_000_000));
  const [drop, setDrop] = useState(false);
  const put = (b: object, ok: string) => run(() => api(`/api/auto/strategies/${x.id}`, { method: "PUT", body: b }), ok);
  return (
    <div className="space-y-2 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">색</span>
        {COLORS.map((c) => (
          <button
            key={c}
            type="button"
            aria-label={`색 ${c}`}
            aria-pressed={x.color === c}
            disabled={busy}
            onClick={() => put({ color: c }, "색을 바꿨습니다")}
            className="h-6 w-6 rounded-md"
            style={{ background: c, boxShadow: x.color === c ? `0 0 0 2px var(--card), 0 0 0 4px ${c}` : undefined }}
          />
        ))}
      </div>
      <div className="space-y-1">
        <span className="text-xs text-muted">1회 매수 금액</span>
        <div className="flex rounded-lg border border-border p-0.5 text-xs">
          {(
            [
              ["fixed", "고정 금액"],
              ["pct", "계좌 비율 (복리)"],
            ] as const
          ).map(([k, l]) => (
            <button
              key={k}
              type="button"
              onClick={() => setSizeMode(k)}
              className={`flex-1 rounded-md py-1 ${sizeMode === k ? "bg-accent-chip text-on-accent-chip" : "text-muted"}`}
            >
              {l}
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {sizeMode === "fixed" ? (
          <label className="block">
            <span className="text-xs text-muted">1회 매수금 (원)</span>
            <NumInput value={per} onChange={setPer} className="mt-1" />
          </label>
        ) : (
          <>
            <label className="block">
              <span className="text-xs text-muted">계좌의 % 씩</span>
              <NumInput value={pctV} onChange={setPctV} className="mt-1" />
            </label>
            <label className="block">
              <span className="text-xs text-muted">1회 최대 (원)</span>
              <NumInput value={cap} onChange={setCap} className="mt-1" />
            </label>
          </>
        )}
        <label className="block">
          <span className="text-xs text-muted">하루 최대 종목</span>
          <NumInput value={daily} onChange={setDaily} className="mt-1" />
        </label>
      </div>
      {sizeMode === "pct" && (
        <Explain label="계좌 % 계산">
          <p className="text-[11px] text-faint">
            매수 후보를 만들 때(저녁) 키움 계좌의 추정자산 × {pctV || "?"}% 로 사고, {man(Number(cap) || 0)}을 넘지 않습니다. 계좌가 커지면 같이
            커져요. 계좌를 못 읽으면 고정 금액({man(Number(per) || 0)})으로 삽니다.
          </p>
        </Explain>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            put(
              { per_trade: Number(per), daily_max: Number(daily), size_mode: sizeMode, pct: Number(pctV), max_per_trade: Number(cap) },
              "저장했습니다",
            )
          }
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
      <Swap label="전략 추가 기본값" short={<p className="text-xs text-faint">처음엔 1회 300만원 × 하루 5종목</p>}>
        <p className="text-xs text-faint">
          처음엔 1회 300만원 × 하루 5종목으로 시작합니다. 카드의 &apos;설정&apos;에서 바꿀 수 있어요. &apos;당일종가&apos; 매수 전략은 쓸 수 없습니다.
        </p>
      </Swap>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy || !sid}
          onClick={() => sid && onAdd(sid)}
          className="rounded-lg bg-accent-fill px-3 py-1.5 text-xs font-semibold text-on-accent disabled:opacity-50"
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
        className={`w-full rounded-xl py-2.5 font-semibold disabled:opacity-50 ${s.enabled ? "border border-critical text-critical" : "bg-accent-fill text-on-accent"}`}
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

// 클립보드 복사 (안 되는 브라우저는 숨긴 입력칸으로)
async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}

// 안전장치: 1회 주문 상한 · 하루 손실 한도 · 위험 종목 제외 · 비상 정지
function Safety({ v, run, busy }: { v: AutoView; run: Run; busy: boolean }) {
  const s = v.settings;
  const [cap, setCap] = useState(String(s.order_cap ?? 20000000));
  const [loss, setLoss] = useState(String(s.daily_loss_pct ?? 8));
  const [risky, setRisky] = useState(s.skip_risky ?? true);
  const st = v.state ?? {};
  const today = (v.now ?? "").slice(0, 10);
  const save = () =>
    run(
      () => api("/api/auto/settings", { method: "PUT", body: { order_cap: Number(cap), daily_loss_pct: Number(loss), skip_risky: risky } }),
      "안전장치를 저장했습니다",
    );
  const stop = (sellAll: boolean) => {
    const msg = sellAll
      ? "비상 정지: 새 매수를 끄고, 매수 주문·후보를 취소하고, 보유 종목을 전부 시장가로 팝니다. 계속할까요?"
      : "비상 정지: 새 매수를 끄고, 오늘 매수 주문·대기 후보를 취소합니다 (보유 종목은 목표·손절·기간만료 그대로). 계속할까요?";
    if (!confirm(msg)) return;
    run(() => api("/api/auto/emergency", { body: { sell_all: sellAll } }), sellAll ? "비상 정지 · 전부 팔기 주문" : "비상 정지 · 새 매수 멈춤");
  };
  const paused = !!st.halted && (st.halted === today || st.halt_skipped !== st.halted);
  return (
    <details className="rounded-lg bg-background p-2.5 text-xs" open={paused ? true : undefined}>
      <summary className="cursor-pointer font-semibold">
        안전장치 · 비상 정지
        {paused && (
          <span className="ml-2 text-critical">
            {st.halted_why === "비상 정지 버튼" ? "비상 정지" : st.halted === today ? "다음 거래일 매수 쉼" : "오늘 매수 쉼"}: {st.halted_why}
          </span>
        )}
      </summary>
      <div className="mt-2 space-y-2">
        <div className="grid grid-cols-2 gap-2">
          <label className="block">
            <span className="text-muted">1회 주문 상한 (원)</span>
            <NumInput value={cap} onChange={setCap} className="mt-1" />
          </label>
          <label className="block">
            <span className="text-muted">하루 손실 한도 (%, 0 = 끔)</span>
            <NumInput value={loss} onChange={setLoss} decimal className="mt-1" />
          </label>
        </div>
        <label className="flex items-start gap-2">
          <input type="checkbox" checked={risky} onChange={(e) => setRisky(e.target.checked)} className="mt-0.5 !w-auto" />
          <span>관리종목·거래정지·투자경고/위험·단기과열·정리매매 종목은 매수 후보에서 빼기</span>
        </label>
        <Swap label="손실 한도" short={<p className="text-faint">손실 한도: 전날 마감보다 이만큼 줄면 다음 거래일 매수 하루 쉼</p>}>
          <p className="text-faint">
            하루 손실 한도: 장중 계좌가 전날 마감보다 이만큼 줄면 알림 + 다음 거래일 매수를 하루 쉬고, 그다음 날부터 저절로 다시 삽니다 (보유 종목의 목표·손절·기간만료는 계속 · 자동매매는 꺼지지 않음). v2b 는 하루 3~5% 출렁임이 흔해서 8% 정도를 권장 · 바로 다시 사려면 위의 켜기.
          </p>
        </Swap>
        <button type="button" disabled={busy} onClick={save} className="rounded-lg border border-accent px-3 py-1.5 font-semibold text-accent disabled:opacity-50">
          저장
        </button>
        <div className="flex flex-wrap gap-2 border-t border-border pt-2">
          <button type="button" disabled={busy} onClick={() => stop(false)} className="rounded-lg bg-critical/15 px-3 py-1.5 font-semibold text-critical disabled:opacity-50">
            비상 정지 (새 매수 멈춤)
          </button>
          <button type="button" disabled={busy} onClick={() => stop(true)} className="rounded-lg border border-critical px-3 py-1.5 text-critical disabled:opacity-50">
            비상 정지 + 전부 팔기
          </button>
        </div>
      </div>
    </details>
  );
}
