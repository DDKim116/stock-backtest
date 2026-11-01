"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

export interface CalEvent {
  date: string;
  time: string;
  name: string;
  imp: number;
  note: string;
  kind: "us" | "kr" | "me";
}
interface CalData {
  today: string;
  events: CalEvent[];
  has_us: boolean;
}

const WD = ["일", "월", "화", "수", "목", "금", "토"];
const KIND: Record<CalEvent["kind"], [string, string]> = {
  us: ["미국", "var(--series-1)"],
  kr: ["한국", "var(--series-2)"],
  me: ["내 일정", "var(--accent)"],
};
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const md = (s: string) => {
  const d = new Date(s + "T00:00:00");
  return `${d.getMonth() + 1}/${d.getDate()} (${WD[d.getDay()]})`;
};

function Row({ e, showDate = true }: { e: CalEvent; showDate?: boolean }) {
  return (
    <div className="flex gap-3 py-2 text-sm">
      <span className="tabular w-[4.5rem] shrink-0 text-xs text-muted">
        {showDate && <span className="block">{md(e.date)}</span>}
        {e.time && <span className="block">{e.time}</span>}
      </span>
      <span className="min-w-0">
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: KIND[e.kind][1] }} />
          <span className={e.imp >= 3 ? "font-semibold" : ""}>{e.name}</span>
          {e.imp >= 3 && <span className="text-xs text-warning">★</span>}
        </span>
        <span className="block text-xs text-faint">{e.note}</span>
      </span>
    </div>
  );
}

// 홈: 앞으로 7일 중요한 일정 몇 개
export function Upcoming({ onMore }: { onMore: () => void }) {
  const [ev, setEv] = useState<CalEvent[] | null>(null);
  useEffect(() => {
    const t = new Date();
    const e = new Date(t.getTime() + 7 * 86400000);
    api<CalData>(`/api/calendar?start=${iso(t)}&end=${iso(e)}`)
      .then((r) => setEv(r.events.filter((x) => x.imp >= 2 || x.kind === "me")))
      .catch(() => setEv([]));
  }, []);
  if (!ev || ev.length === 0) return null;
  return (
    <section className="rounded-xl border border-border bg-card px-4 py-2">
      <div className="flex items-baseline justify-between pt-1 text-xs text-muted">
        <span>다가오는 일정 · 7일</span>
        <button type="button" onClick={onMore} className="text-accent">
          달력 →
        </button>
      </div>
      <div className="divide-y divide-border">
        {ev.slice(0, 5).map((e) => (
          <Row key={e.date + e.name} e={e} />
        ))}
      </div>
    </section>
  );
}

// 시장 → 일정: 달력과 날짜별 목록
export default function Calendar() {
  const now = new Date();
  const [ym, setYm] = useState<[number, number]>([now.getFullYear(), now.getMonth()]);
  const [data, setData] = useState<CalData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pick, setPick] = useState<string | null>(null);
  const [kind, setKind] = useState<"all" | CalEvent["kind"]>("all");

  useEffect(() => {
    const a = new Date(ym[0], ym[1], 1);
    const b = new Date(ym[0], ym[1] + 1, 0);
    api<CalData>(`/api/calendar?start=${iso(a)}&end=${iso(b)}`)
      .then((r) => {
        setData(r);
        setErr(null);
      })
      .catch((e) => setErr((e as Error).message));
  }, [ym]);

  const events = useMemo(() => (data?.events ?? []).filter((e) => kind === "all" || e.kind === kind), [data, kind]);
  const byDay = useMemo(() => {
    const m: Record<string, CalEvent[]> = {};
    for (const e of events) (m[e.date] ??= []).push(e);
    return m;
  }, [events]);

  const first = new Date(ym[0], ym[1], 1);
  const days = new Date(ym[0], ym[1] + 1, 0).getDate();
  const cells: (string | null)[] = [...Array(first.getDay()).fill(null)];
  for (let d = 1; d <= days; d++) cells.push(iso(new Date(ym[0], ym[1], d)));
  const today = data?.today ?? iso(now);
  const list = pick ? (byDay[pick] ?? []) : events.filter((e) => e.date >= today);
  const move = (k: number) => {
    setPick(null);
    setYm(([y, m]) => {
      const d = new Date(y, m + k, 1);
      return [d.getFullYear(), d.getMonth()];
    });
  };

  return (
    <div className="space-y-3 fold:grid fold:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] fold:items-start fold:gap-4 fold:space-y-0">
      <section className="space-y-3 rounded-xl border border-border bg-card p-3">
        <div className="flex items-center justify-between">
          <button type="button" onClick={() => move(-1)} className="rounded-lg border border-border px-3 py-1 text-sm" aria-label="이전 달">
            ‹
          </button>
          <span className="font-semibold">
            {ym[0]}년 {ym[1] + 1}월
          </span>
          <button type="button" onClick={() => move(1)} className="rounded-lg border border-border px-3 py-1 text-sm" aria-label="다음 달">
            ›
          </button>
        </div>
        <div className="flex flex-wrap gap-1 text-xs">
          {(["all", "us", "kr", "me"] as const).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => setKind(k)}
              className={`flex items-center gap-1 rounded-full border px-2.5 py-1 ${kind === k ? "border-accent text-accent" : "border-border text-muted"}`}
            >
              {k !== "all" && <span className="h-2 w-2 rounded-full" style={{ background: KIND[k][1] }} />}
              {k === "all" ? "전체" : KIND[k][0]}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-7 gap-1 text-center text-xs">
          {WD.map((w, i) => (
            <span key={w} className={`py-1 ${i === 0 ? "text-up" : i === 6 ? "text-down" : "text-muted"}`}>
              {w}
            </span>
          ))}
          {cells.map((c, i) => {
            if (!c) return <span key={`b${i}`} />;
            const evs = byDay[c] ?? [];
            const on = pick === c;
            const big = evs.some((e) => e.imp >= 3);
            return (
              <button
                key={c}
                type="button"
                onClick={() => setPick(on ? null : c)}
                className={`flex aspect-square flex-col items-center justify-start gap-1 rounded-lg pt-1 ${on ? "bg-accent-soft ring-1 ring-accent" : c === today ? "bg-background" : ""}`}
              >
                <span className={`tabular ${c === today ? "font-bold text-accent" : ""} ${big ? "underline decoration-warning decoration-2 underline-offset-2" : ""}`}>
                  {+c.slice(8)}
                </span>
                <span className="flex flex-wrap justify-center gap-0.5">
                  {evs.slice(0, 4).map((e, k) => (
                    <span key={k} className="h-1.5 w-1.5 rounded-full" style={{ background: KIND[e.kind][1] }} />
                  ))}
                </span>
              </button>
            );
          })}
        </div>
        {data && !data.has_us && (
          <p className="text-[11px] text-warning">미국 발표 일정은 FRED 자료를 받으면 나와요 (설정에 FRED 키 → 연구 탭 &lsquo;FRED 경제 지표 받기&rsquo;).</p>
        )}
        <p className="text-[11px] text-faint">시간은 한국 시간 · ★ 크게 움직일 수 있는 날 · 날짜 밑줄 = ★ 일정이 있는 날</p>
      </section>

      <section className="rounded-xl border border-border bg-card px-4 py-2">
        <div className="pt-1 text-xs text-muted">{pick ? `${md(pick)} 일정` : "이번 달 남은 일정"}</div>
        {err && <p className="py-2 text-sm text-critical">⚠ {err}</p>}
        {!data && !err && <p className="py-2 text-sm text-muted">불러오는 중…</p>}
        {data && list.length === 0 && <p className="py-2 text-sm text-faint">일정이 없어요</p>}
        <div className="divide-y divide-border">
          {list.map((e) => (
            <Row key={e.date + e.name} e={e} showDate={!pick} />
          ))}
        </div>
      </section>
    </div>
  );
}
