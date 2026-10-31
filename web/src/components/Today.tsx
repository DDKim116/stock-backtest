"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { pct, retClass } from "@/lib/format";
import { type AutoView, pnlOf, type Position as Pos, stratOf, wonShort } from "@/lib/auto";
import type { Spec, Trade } from "@/lib/types";
import { useBackClose } from "@/lib/useBackClose";
import MarketPulse from "./MarketPulse";
import TradeChart from "./TradeChart";

interface Quote {
  price: number;
  change_pct: number | null;
}
interface IndexQ {
  price: number;
  change: number | null;
  change_pct: number | null;
}
interface Account {
  cash: { orderable: number | null };
  balance: { assets: number | null; pnl: number | null; ret: number | null };
}
interface WatchView {
  settings: { strategy_ids: number[] };
  items: { kind: string }[];
}

const won = (v: number | null | undefined) => (v == null ? "–" : Math.round(v).toLocaleString("ko-KR"));
const man = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v / 10000).toLocaleString("ko-KR")}만`);
const md = (d?: string | null) => (d ? `${+d.slice(5, 7)}/${+d.slice(8, 10)}` : "–");

function kstNow() {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", weekday: "short", hour12: false,
    }).formatToParts(new Date()).map((x) => [x.type, x.value]),
  );
  return { date: `${p.year}-${p.month}-${p.day}`, hm: `${p.hour === "24" ? "00" : p.hour}:${p.minute}`, wd: p.weekday as string };
}
const WD: Record<string, string> = { Mon: "월", Tue: "화", Wed: "수", Thu: "목", Fri: "금", Sat: "토", Sun: "일" };

function session(hm: string, wd: string): string {
  if (wd === "Sat" || wd === "Sun") return "휴장";
  if (hm < "09:00") return "장 전";
  if (hm < "15:30") return "장중";
  return "장 마감";
}

// 다음 자동 일정 (휴장일 목록까지는 보지 않음)
function nextEvent(hm: string, wd: string, approve: boolean): [string, string] {
  if (wd === "Sat" || wd === "Sun") return ["월 08:50", "매수 주문 · 목표가 다시 걸기"];
  const ev: [string, string][] = [
    ...(approve ? ([["08:45", "매수 후보 승인 마감"]] as [string, string][]) : []),
    ["08:50", "매수 주문 · 목표가 다시 걸기"],
    ["09:00", "장 시작 · 손절 감시"],
    ["15:20", "보유기간 만료 매도"],
    ["16:40", "다음 거래일 매수 후보 계산"],
  ];
  const n = ev.find(([t]) => t > hm);
  return n ?? [wd === "Fri" ? "월 08:50" : "내일 08:50", "매수 주문 · 목표가 다시 걸기"];
}

function asTrade(p: Pos): Trade {
  const outcome = p.status === "closed" ? (p.exit_reason === "목표" ? "목표도달" : (p.exit_reason ?? "청산")) : "진행중";
  return {
    code: p.code, name: p.name, market: "", signal_date: p.signal_date ?? p.entry_date, signal_close: null, limit: p.limit_price,
    fill_date: p.entry_date, fill_price: p.entry_price, outcome, outcome_opt: outcome, uncertain: false,
    exit_date: p.exit_date, exit_price: p.exit_price, days: null, ret: p.ret, ret_opt: p.ret, mfe: null, mae: null,
  };
}

// 홈 '오늘': 자동 매매 현황·보유 종목 실시간·다음 일정·지수·실시간 감시를 한 화면에
export default function Today({ onGo, newsCount }: { onGo: (tab: "auto" | "paper" | "run" | "market") => void; newsCount: number }) {
  const [auto, setAuto] = useState<AutoView | null>(null);
  const [acct, setAcct] = useState<Account | null>(null);
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [index, setIndex] = useState<Record<string, IndexQ> | null>(null);
  const [watch, setWatch] = useState<WatchView | null>(null);
  const [at, setAt] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [now, setNow] = useState(kstNow);
  const [chart, setChart] = useState<{ trade: Trade; spec: Spec } | null>(null);
  const closeChart = useCallback(() => setChart(null), []);
  const back = useBackClose(!!chart, closeChart);

  const load = useCallback(async () => {
    setNow(kstNow());
    try {
      const a = await api<AutoView>("/api/auto");
      setAuto(a);
      setErr(null);
      const codes = a.positions.filter((p) => p.status !== "closed").map((p) => p.code);
      const live = a.key_saved;
      const jobs: Promise<unknown>[] = [
        api<WatchView>("/api/watch").then(setWatch, () => {}),
      ];
      if (live) {
        jobs.push(api<Record<string, IndexQ>>("/api/live/index").then(setIndex, () => {}));
        jobs.push(api<Account>("/api/auto/account").then(setAcct, () => {}));
        if (codes.length) jobs.push(api<Record<string, Quote>>(`/api/live/quote?codes=${codes.join(",")}`).then(setQuotes, () => {}));
      }
      await Promise.all(jobs);
      setAt(kstNow().hm);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 처음 한 번 + 장중 20초 / 그 밖 2분마다
    load();
    const t = setInterval(() => {
      const n = kstNow();
      const open = session(n.hm, n.wd) === "장중";
      if (open || Number(n.hm.slice(3)) % 2 === 0) load();
    }, 20_000);
    return () => clearInterval(t);
  }, [load]);

  const sess = session(now.hm, now.wd);
  const active = (auto?.positions ?? []).filter((p) => p.status !== "closed");
  const closed = (auto?.positions ?? []).filter((p) => p.status === "closed").slice(0, 3);
  const waiting = (auto?.plans ?? []).filter((p) => p.status === "proposed");
  const [nextT, nextL] = nextEvent(now.hm, now.wd, auto?.settings.mode === "approve");
  const watchN = watch?.items.length ?? 0;
  const color = (p: Pos) => (auto && stratOf(auto, p.strategy_id)?.color) || "var(--faint)";
  const open = (p: Pos) => {
    const st = auto && stratOf(auto, p.strategy_id);
    if (!st) return;
    back.push();
    setChart({ trade: asTrade(p), spec: st.spec });
  };
  const per = auto?.strategies.map((x) => [x, waiting.filter((p) => p.strategy_id === x.id).length] as const).filter(([, n]) => n > 0);
  const banner = waiting.length > 0 && auto?.plan_date && (
    <button type="button" onClick={() => onGo("auto")} className="flex w-full items-center justify-between gap-3 rounded-xl bg-accent px-4 py-3 text-left text-on-accent fold:flex-col fold:items-start lg:flex-row lg:items-center">
      <span>
        <span className="block font-semibold">{md(auto.plan_date)} 매수 후보 {waiting.length}종목 승인 대기</span>
        <span className="text-xs">
          {per && per.length > 1 && per.map(([x, n]) => `${x.name} ${n}`).join(" · ") + " · "}
          {md(auto.plan_date)} 08:45 까지
        </span>
      </span>
      <span className="shrink-0 text-sm font-semibold">승인하기 →</span>
    </button>
  );

  return (
    <div className="space-y-3">
      <header className="flex items-end justify-between">
        <div>
          <div className="text-xs tracking-wide text-muted">
            {+now.date.slice(5, 7)}월 {+now.date.slice(8)}일 {WD[now.wd]} · {sess}
          </div>
          <h2 className="text-xl font-bold">오늘</h2>
        </div>
        {auto && (
          <button
            type="button"
            onClick={() => onGo("auto")}
            className={`flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs ${auto.settings.enabled ? "border-border text-accent" : "border-border text-faint"}`}
          >
            <span className={`h-2 w-2 rounded-full ${auto.settings.enabled ? "bg-accent" : "bg-faint"}`} />
            자동 매매 {auto.settings.enabled ? "켜짐" : "꺼짐"}
          </button>
        )}
      </header>

      {err && <p className="text-sm text-critical">⚠ {err}</p>}

      <div className="fold:hidden">{banner}</div>

      <div className="space-y-3 fold:grid fold:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)] fold:items-start fold:gap-4 fold:space-y-0">
      <div className="space-y-3">
      <section className="space-y-2.5 rounded-xl border border-border bg-card p-4">
        <div className="flex items-baseline justify-between text-xs text-muted">
          <span className="flex min-w-0 flex-wrap items-center gap-x-2">
            키움 모의투자 ·
            {auto?.strategies.length ? (
              auto.strategies.map((x) => (
                <span key={x.id} className="flex items-center gap-1">
                  <span className="h-2 w-2 rounded-sm" style={{ background: x.color }} />
                  {x.name}
                </span>
              ))
            ) : (
              <span>전략 없음</span>
            )}
          </span>
          {at && <span className="tabular shrink-0 whitespace-nowrap">{at} 기준</span>}
        </div>
        <div className="grid grid-cols-3 gap-2">
          <div>
            <div className="text-[11px] text-muted">보유</div>
            <div className="tabular text-2xl font-semibold">{active.length}</div>
          </div>
          <div>
            <div className="text-[11px] text-muted">추정자산</div>
            <div className="tabular text-2xl font-semibold">{man(acct?.balance.assets)}</div>
          </div>
          <div>
            <div className="text-[11px] text-muted">평가손익</div>
            <div className={`tabular text-2xl font-semibold ${retClass(acct?.balance.pnl ?? null)}`}>
              {acct?.balance.pnl == null ? "–" : `${acct.balance.pnl > 0 ? "+" : ""}${man(acct.balance.pnl)}`}
            </div>
          </div>
        </div>
        {acct && <div className="tabular text-xs text-muted">주문가능 {won(acct.cash.orderable)}원</div>}
        {auto && !auto.key_saved && <p className="text-xs text-warning">설정 → 키움 연결에 키를 넣으면 실시간 값이 보입니다.</p>}
      </section>

      <section className="space-y-1.5">
        <div className="flex justify-between px-1 text-xs text-muted">
          <span>보유 종목</span>
          <span>매수가 → 현재가 · 수익률</span>
        </div>
        <div className="divide-y divide-border rounded-xl border border-border bg-card">
          {active.length === 0 && <p className="px-4 py-3 text-sm text-faint">보유 종목이 없습니다.</p>}
          {active.slice(0, 6).map((p) => {
            const q = quotes[p.code];
            const r = q ? (q.price / p.entry_price - 1) * 100 : null;
            return (
              <button
                key={p.id}
                type="button"
                onClick={() => open(p)}
                className="grid w-full grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-3 px-4 py-2.5 text-left md:grid-cols-[minmax(0,1fr)_auto_auto_6rem]"
              >
                <span className="min-w-0">
                  <span className="flex items-center gap-1.5 truncate text-sm">
                    <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: color(p) }} />
                    {p.name}
                  </span>
                  <span className="text-[11px] text-faint">
                    {p.qty.toLocaleString("ko-KR")}주 · {md(p.entry_date)} 매수{p.status === "closing" ? ` · ${p.exit_reason} 매도 중` : ""}
                  </span>
                </span>
                <span className="tabular text-right text-xs text-muted">
                  {won(p.entry_price)}
                  <span className="block text-sm text-foreground">{q ? won(q.price) : "–"}</span>
                </span>
                <span className={`tabular w-16 text-right text-sm font-semibold ${retClass(r)}`}>{pct(r, 2, true)}</span>
                <StopTarget p={p} price={q?.price ?? null} />
              </button>
            );
          })}
          {active.length > 6 && (
            <button type="button" onClick={() => onGo("auto")} className="w-full px-4 py-2.5 text-left text-xs text-accent">
              외 {active.length - 6}종목 · 모두 보기
            </button>
          )}
        </div>
      </section>

      </div>
      <div className="space-y-3">
      <div className="hidden fold:block">{banner}</div>
      <section className="grid grid-cols-2 gap-2 fold:grid-cols-1">
        <div className="rounded-xl border border-border bg-card p-3">
          <div className="text-[11px] text-muted">다음 일정</div>
          <div className="tabular text-lg font-semibold text-accent">{nextT}</div>
          <div className="text-xs">{nextL}</div>
        </div>
        <button type="button" onClick={() => onGo("market")} className="rounded-xl border border-border bg-card p-3 text-left">
          {(["KOSPI", "KOSDAQ"] as const).map((k) => {
            const q = index?.[k];
            return (
              <div key={k} className="flex items-baseline justify-between gap-1">
                <span className="text-[11px] text-muted">{k === "KOSPI" ? "코스피" : "코스닥"}</span>
                <span className="tabular text-sm">
                  {q ? q.price.toLocaleString("ko-KR", { maximumFractionDigits: 2 }) : "–"}{" "}
                  <span className={retClass(q?.change_pct ?? null)}>{q ? pct(q.change_pct, 2, true) : ""}</span>
                </span>
              </div>
            );
          })}
        </button>
      </section>

      <button type="button" onClick={() => onGo("auto")} className="flex w-full items-center justify-between rounded-xl border border-dashed border-border px-4 py-3 text-left">
        <span>
          <span className="block text-sm">장중 실시간 감시</span>
          <span className="text-[11px] text-muted">
            {watch && watch.settings.strategy_ids.length > 0 ? (watchN ? `지켜보는 종목 ${watchN}` : "지켜볼 종목 없음") : "감시할 전략을 고르세요"}
          </span>
        </span>
        <span className="tabular text-[11px] text-muted">09:00–15:20</span>
      </button>

      {(closed.length > 0 || newsCount > 0) && (
        <section className="space-y-1.5">
          <div className="flex justify-between px-1 text-xs text-muted">
            <span>최근 청산</span>
            {newsCount > 0 && (
              <button type="button" onClick={() => onGo("paper")} className="text-accent">
                모투 소식 {newsCount}
              </button>
            )}
          </div>
          {closed.length > 0 && (
            <div className="divide-y divide-border rounded-xl border border-border bg-card">
              {closed.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => open(p)}
                  className="flex w-full justify-between gap-2 px-4 py-2.5 text-left text-sm"
                >
                  <span className="flex min-w-0 items-center gap-1.5 truncate">
                    <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: color(p) }} />
                    {p.name} <span className="text-xs text-faint">{p.exit_reason} · {p.exit_date && md(p.exit_date)}</span>
                  </span>
                  <span className="tabular shrink-0 text-right">
                    <span className={retClass(p.ret)}>{pct(p.ret, 2, true)}</span>{" "}
                    <span className={`text-xs ${retClass(pnlOf(p))}`}>{wonShort(pnlOf(p))}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </section>
      )}

      {auto?.last_plan && auto.last_plan.count === 0 && waiting.length === 0 && (
        <p className="px-1 text-xs text-faint">
          {md(auto.last_plan.trade_date)} 매수 후보 없음 · {md(auto.last_plan.signal_date)}에는 신호가 없었습니다.
        </p>
      )}
      </div>
      </div>

      <MarketPulse />

      {chart && <TradeChart trade={chart.trade} spec={chart.spec} dataset="kr" currency="KRW" onClose={back.close} />}
    </div>
  );
}

// 넓은 화면: 손절선(왼쪽)~목표가(오른쪽) 사이 현재가 위치
function StopTarget({ p, price }: { p: Pos; price: number | null }) {
  if (!p.stop_price || !p.target_price || price == null) return <span className="hidden md:block" />;
  const f = Math.min(1, Math.max(0, (price - p.stop_price) / (p.target_price - p.stop_price)));
  return (
    <span className="relative hidden h-1.5 rounded-full bg-background md:block" title="손절 ← → 목표">
      <span className="absolute top-1/2 h-2.5 w-1 -translate-y-1/2 rounded-sm bg-foreground" style={{ left: `calc(${f * 100}% - 2px)` }} />
    </span>
  );
}
