"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { pct, retClass } from "@/lib/format";
import type { Spec, Strategy, Trade } from "@/lib/types";
import { useBackClose } from "@/lib/useBackClose";
import { SectionHead } from "./AutoTrade";
import TradeChart, { limitLabel } from "./TradeChart";

interface Item {
  sid: number;
  strategy: string;
  code: string;
  name: string;
  signal_date: string;
  kind: "pending" | "holding";
  limit: number | null;
  left: number | null;
  entry: number | null;
  held: number | null;
  fill_date?: string | null;
  hold: number;
  price: number | null;
  gap?: number;
  ret?: number | null;
  target?: number | null;
  stop?: number | null;
  touched_today?: boolean;
  events: string[];
}
interface WatchView {
  settings: { strategy_ids: number[] };
  items: Item[];
  updated: string | null;
  mode?: string | null;
  error?: string;
  specs?: Record<string, Spec>;
}
const EVENT: Record<string, string> = { buy: "매수가 도달", target: "목표 도달", stop: "손절가 도달", last: "마지막 날" };
const won = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v).toLocaleString("ko-KR")}`);
const md = (d?: string | null) => (d ? `${+d.slice(5, 7)}/${+d.slice(8, 10)}` : "–");

// 차트에 넘길 거래: 신호일·지정가(중간값)·산 것으로 계산한 날과 값
function asTrade(i: Item): Trade {
  const outcome = i.kind === "holding" ? "진행중" : "주문 대기";
  return {
    code: i.code, name: i.name, market: "", signal_date: i.signal_date, signal_close: null, limit: i.limit,
    fill_date: i.kind === "holding" ? (i.fill_date ?? null) : null, fill_price: i.kind === "holding" ? i.entry : null,
    outcome, outcome_opt: outcome, uncertain: false, exit_date: null, exit_price: null, days: null,
    ret: null, ret_opt: null, mfe: null, mae: null,
  };
}

// 모투 탭: 고른 전략의 지정가 대기·보유 종목을 장중에 키움 시세로 지켜보고 알림 (주문은 하지 않음)
export default function WatchCard({ onFlash }: { onFlash: (m: string) => void }) {
  const [v, setV] = useState<WatchView | null>(null);
  const [strategies, setStrategies] = useState<Strategy[] | null>(null);
  const [edit, setEdit] = useState(false);
  const [busy, setBusy] = useState(false);
  const [chart, setChart] = useState<{ trade: Trade; spec: Spec } | null>(null);
  const closeChart = useCallback(() => setChart(null), []);
  const back = useBackClose(!!chart, closeChart);
  const open = (i: Item) => {
    const spec = v?.specs?.[String(i.sid)];
    if (!spec) return;
    back.push();
    setChart({ trade: asTrade(i), spec });
  };

  const load = useCallback(async () => {
    try {
      setV(await api<WatchView>("/api/watch"));
    } catch (e) {
      setV({ settings: { strategy_ids: [] }, items: [], updated: null, error: (e as Error).message });
    }
  }, []);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 처음 한 번 + 30초마다
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  const toggle = async (id: number) => {
    if (!v) return;
    const ids = v.settings.strategy_ids.includes(id) ? v.settings.strategy_ids.filter((x) => x !== id) : [...v.settings.strategy_ids, id];
    setBusy(true);
    try {
      setV(await api<WatchView>("/api/watch/settings", { method: "PUT", body: { strategy_ids: ids } }));
    } catch (e) {
      onFlash((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!v) return null;
  const ids = v.settings.strategy_ids;
  const pending = v.items.filter((i) => i.kind === "pending");
  const holding = v.items.filter((i) => i.kind === "holding");

  return (
    <section className="space-y-3 rounded-xl border border-border bg-card px-3 py-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-semibold">장중 실시간 감시</h3>
        <span className="text-xs text-muted">{v.updated ? `${v.updated} 시세` : "장중(09:00~15:20)에 갱신"}</span>
      </div>
      <p className="text-xs text-muted">
        고른 전략의 지정가 대기·보유 종목을 키움 시세로 약 20초마다 확인해, 매수가·목표·손절에 닿으면 휴대폰으로 알립니다. 주문은 하지 않습니다.
      </p>
      {v.mode === null && <p className="text-xs text-warning">설정 → 키움 연결에 키를 넣어야 시세를 받을 수 있습니다.</p>}
      {v.error && <p className="text-xs text-critical">⚠ {v.error}</p>}

      {ids.length === 0 && !edit && <p className="text-sm text-faint">감시할 전략을 고르세요.</p>}

      {pending.length > 0 && (
        <div className="border-t border-border pt-2.5 text-sm">
          <SectionHead color="var(--accent)" title={`지정가 대기 ${pending.length}`} />
          <ul className="divide-y divide-border">
            {pending.map((i) => (
              <li key={`${i.sid}-${i.code}-${i.signal_date}`} onClick={() => open(i)} className="cursor-pointer py-2">
                <div className="flex justify-between gap-2">
                  <span className="truncate">
                    {i.name} <span className="text-xs text-faint">{i.code}</span> <span className="text-xs text-accent">차트</span>
                  </span>
                  <span className="tabular text-xs">{i.gap != null ? `지정가까지 ${i.gap > 0 ? "-" : ""}${Math.abs(i.gap).toFixed(1)}%` : ""}</span>
                </div>
                <div className="tabular text-xs text-muted">
                  {v.specs?.[String(i.sid)] && limitLabel(v.specs[String(i.sid)]) === "중간값" ? (
                    <span className="text-mid">중간값 {won(i.limit)}</span>
                  ) : (
                    <>지정가 {won(i.limit)}</>
                  )} · 현재 {won(i.price)} · 신호 {md(i.signal_date)} · 남은 {i.left}일
                  {ids.length > 1 && <span className="text-faint"> · {i.strategy}</span>}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {holding.length > 0 && (
        <div className="border-t border-border pt-2.5 text-sm">
          <SectionHead color="var(--accent)" title={`산 것으로 계산 ${holding.length}`} solid />
          <ul className="divide-y divide-border">
            {holding.map((i) => (
              <li key={`${i.sid}-${i.code}-${i.signal_date}`} onClick={() => open(i)} className="cursor-pointer py-2">
                <div className="flex justify-between gap-2">
                  <span className="truncate">
                    {i.name} <span className="text-xs text-faint">{i.code}</span> <span className="text-xs text-accent">차트</span>
                  </span>
                  <span className={`tabular ${retClass(i.ret ?? null)}`}>{pct(i.ret ?? null, 2, true)}</span>
                </div>
                <div className="tabular text-xs text-muted">
                  매수 {won(i.entry)} · 현재 {won(i.price)} · 목표 {won(i.target)} · 손절 {won(i.stop)} ·{" "}
                  {i.touched_today ? "오늘 매수" : `${(i.held ?? 0) + 1}/${i.hold}일째`}
                </div>
                {i.events.length > 0 && (
                  <div className="mt-0.5 flex flex-wrap gap-1">
                    {i.events.map((e) => (
                      <span key={e} className="rounded bg-background px-1.5 text-[11px] text-accent">
                        {EVENT[e] ?? e}
                      </span>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {ids.length > 0 && v.items.length === 0 && !v.error && <p className="text-sm text-faint">지금 지켜볼 종목이 없습니다 (신호가 나면 생깁니다).</p>}

      <button
        type="button"
        onClick={async () => {
          if (!strategies) setStrategies(await api<Strategy[]>("/api/strategies").catch(() => []));
          setEdit((x) => !x);
        }}
        className="rounded-lg border border-border px-3 py-1.5 text-xs"
      >
        감시할 전략 {edit ? "닫기" : "고르기"}
      </button>
      {edit && strategies && (
        <ul className="space-y-1 rounded-lg border border-border p-2.5 text-sm">
          {strategies.map((x) => (
            <li key={x.id}>
              <label className="flex items-start gap-2">
                <input type="checkbox" checked={ids.includes(x.id)} disabled={busy} onChange={() => toggle(x.id)} className="mt-1 !w-auto" />
                <span>{x.name}</span>
              </label>
            </li>
          ))}
        </ul>
      )}
      {chart && <TradeChart trade={chart.trade} spec={chart.spec} dataset="kr" currency="KRW" onClose={back.close} />}
    </section>
  );
}
