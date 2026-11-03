"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

interface Item {
  code: string;
  name: string;
  strategy_id: number | null;
  reason: string | null;
  ret: number | null;
  pnl: number;
  entry_date: string;
}
interface Day {
  date: string;
  pnl: number; // 실제 계좌 수수료·세금을 뺀 손익
  gross?: number; // 빼기 전
  cost?: number; // 실제 계좌 수수료·세금
  items: Item[];
}
interface Daily {
  days: Day[];
  equity: { date: string; assets: number }[];
  start?: string | null; // 처음 산 날
  buy_amount?: number; // 산 금액 합계
  sell_amount?: number; // 판 금액 합계
}
const START = 50_000_000; // 모의투자 계좌 시작 금액

type Range = "1m" | "3m" | "all";
const RANGES: [Range, string, number][] = [
  ["1m", "1달", 31],
  ["3m", "3달", 92],
  ["all", "전체", 100_000],
];
const md = (d: string) => `${+d.slice(5, 7)}/${+d.slice(8, 10)}`;
const man = (v: number) => {
  const a = Math.abs(v);
  const s = a >= 1e8 ? `${(a / 1e8).toLocaleString("ko-KR", { maximumFractionDigits: 2 })}억` : `${Math.round(a / 1e4).toLocaleString("ko-KR")}만`;
  return `${v < 0 ? "-" : v > 0 ? "+" : ""}${s}`;
};
const won = (v: number) => (v >= 1e8 ? `${(v / 1e8).toLocaleString("ko-KR", { maximumFractionDigits: 2 })}억` : `${Math.round(v / 1e4).toLocaleString("ko-KR")}만`);
const tone = (v: number) => (v > 0 ? "text-up" : v < 0 ? "text-down" : "text-muted");

// 날마다 판 종목들의 손익(막대, 누르면 종목) + 계좌 금액(꺾은선)
export default function PnlChart({ assets, unrealized, colorOf }: { assets?: number | null; unrealized?: number | null; colorOf?: (sid: number | null) => string }) {
  const [data, setData] = useState<Daily | null>(null);
  const [range, setRange] = useState<Range>("1m");
  const [pick, setPick] = useState<string | null>(null);

  useEffect(() => {
    api<Daily>("/api/auto/daily").then(setData, () => setData({ days: [], equity: [] }));
  }, []);

  const [nowMs] = useState(() => Date.now() + 9 * 3600e3); // 한국 시각
  const today = new Date(nowMs).toISOString().slice(0, 10);
  const from = new Date(nowMs - RANGES.find(([k]) => k === range)![2] * 86400e3).toISOString().slice(0, 10);

  // 계좌 금액 (실제 계좌 기준): 시작 5천만 + 그날까지 판 손익(실제 수수료·세금 뺀 값), 오늘은 + 들고 있는 종목 평가손익
  // (키움 모의투자 계좌 금액은 모의투자 수수료가 더 높아 이보다 낮게 나온다 → 참고로만 따로 표시)
  const line = useMemo(() => {
    if (!data) return [];
    const first = [data.start, data.days[0]?.date].filter(Boolean).sort()[0] as string | undefined;
    const pts: { date: string; v: number }[] = [];
    if (first) pts.push({ date: first, v: START });
    let cum = 0;
    for (const d of data.days) {
      cum += d.pnl;
      pts.push({ date: d.date, v: START + cum });
    }
    if (unrealized != null) {
      const i = pts.findIndex((p) => p.date === today);
      const v = START + cum + unrealized;
      if (i >= 0) pts[i] = { date: today, v };
      else pts.push({ date: today, v });
    }
    pts.sort((a, b) => a.date.localeCompare(b.date));
    return pts.filter((p, i) => i === pts.length - 1 || pts[i + 1].date !== p.date);
  }, [data, unrealized, today]);

  if (!data) return null;
  const days = data.days.filter((d) => d.date >= from);
  const lp = line.filter((p) => p.date >= from);
  if (data.days.length === 0 && line.length < 2) return null;

  const maxAbs = Math.max(1, ...days.map((d) => Math.abs(d.pnl)));
  const sum = days.reduce((s, d) => s + d.pnl, 0);
  const sel = pick ? days.find((d) => d.date === pick) : null;
  const wins = days.flatMap((d) => d.items).filter((x) => x.pnl > 0).length;
  const cnt = days.flatMap((d) => d.items).length;

  // 꺾은선
  const W = 300;
  const H = 90;
  const lo = Math.min(START, ...lp.map((p) => p.v));
  const hi = Math.max(START, ...lp.map((p) => p.v));
  const y = (v: number) => (hi > lo ? H - 6 - ((v - lo) / (hi - lo)) * (H - 12) : H / 2);
  const x = (i: number) => (lp.length > 1 ? (i / (lp.length - 1)) * W : W / 2);
  const pts = lp.map((p, i) => `${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
  const now = lp.length ? lp[lp.length - 1].v : START;
  // 계좌 변화 = 판 종목 손익(수수료 전) − 실제 계좌 수수료·세금 + 들고 있는 종목 평가손익
  const gross = data.days.reduce((s, d) => s + (d.gross ?? d.pnl), 0);
  const cost = data.days.reduce((s, d) => s + (d.cost ?? 0), 0);
  const parts = unrealized != null ? { realized: gross, held: unrealized, cost: -cost } : null;
  const chg = now - START; // 늘 시작 5천만 대비

  return (
    <section className="space-y-3 rounded-xl border border-border bg-card p-4">
      <div className="flex items-center justify-between">
        <h3 className="font-bold">손익 그래프</h3>
        <span className="flex gap-1 text-xs">
          {RANGES.map(([k, label]) => (
            <button
              key={k}
              type="button"
              onClick={() => {
                setRange(k);
                setPick(null);
              }}
              className={`whitespace-nowrap rounded-md px-2 py-0.5 ${range === k ? "bg-accent text-on-accent" : "border border-border text-muted"}`}
            >
              {label}
            </button>
          ))}
        </span>
      </div>

      {/* 계좌 금액 꺾은선 */}
      {lp.length > 1 && (
        <div className="space-y-1">
          <div className="flex items-baseline justify-between text-xs">
            <span className="font-semibold">계좌 금액</span>
            <span className="tabular">
              시작 {won(START)} → <b>{won(now)}</b>{" "}
              <span className={tone(chg)}>
                ({man(chg)}, {`${chg >= 0 ? "+" : ""}${((chg / START) * 100).toFixed(1)}%`})
              </span>
            </span>
          </div>
          <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-24 w-full" aria-label="계좌 금액">
            <polygon points={`0,${H} ${pts} ${W},${H}`} fill="var(--accent)" opacity={0.12} />
            <line x1={0} x2={W} y1={y(START)} y2={y(START)} stroke="var(--faint)" strokeDasharray="4 4" strokeWidth={1} vectorEffect="non-scaling-stroke" />
            <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth={2} vectorEffect="non-scaling-stroke" />
          </svg>
          <div className="tabular flex justify-between text-[11px] text-faint">
            <span>{md(lp[0].date)}</span>
            <span>
              낮음 {won(lo)} · 높음 {won(hi)}
            </span>
            <span>{md(lp[lp.length - 1].date)}</span>
          </div>
          {parts && (
            <div className="grid grid-cols-3 gap-2 rounded-lg bg-background p-2 text-center text-[11px]">
              <div>
                <div className="text-faint">판 종목 손익</div>
                <div className={`tabular font-semibold ${tone(parts.realized)}`}>{man(parts.realized)}</div>
              </div>
              <div>
                <div className="text-faint">들고 있는 종목 평가</div>
                <div className={`tabular font-semibold ${tone(parts.held)}`}>{man(parts.held)}</div>
              </div>
              <div>
                <div className="text-faint">수수료·세금 (실거래)</div>
                <div className={`tabular font-semibold ${tone(parts.cost)}`}>{man(parts.cost)}</div>
              </div>
              <div className="col-span-3 text-faint">
                셋을 더하면 실거래 기준 계좌 변화 <b className={tone(chg)}>{man(chg)}</b> (시작 5,000만 대비, 처음부터)
                {assets != null && (
                  <span className="block">
                    키움 모의투자 계좌 실제 금액 {won(assets)} (모의투자는 수수료가 실제보다 높아 더 낮게 나와요)
                  </span>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* 날마다 실현 손익 막대 */}
      <div className="space-y-1">
        <div className="flex items-baseline justify-between text-xs">
          <span className="font-semibold">날마다 판 종목 손익</span>
          <span className="tabular">
            합계 <b className={tone(sum)}>{man(sum)}</b> · {cnt}건 중 이익 {wins}
          </span>
        </div>
        {days.length === 0 ? (
          <p className="py-3 text-center text-xs text-faint">이 기간에 판 종목이 없어요</p>
        ) : (
          <div className="flex h-32 items-stretch gap-[3px] overflow-x-auto">
            {days.map((d) => {
              const h = (Math.abs(d.pnl) / maxAbs) * 50;
              const on = pick === d.date;
              return (
                <button
                  key={d.date}
                  type="button"
                  onClick={() => setPick(on ? null : d.date)}
                  aria-label={`${md(d.date)} ${man(d.pnl)}`}
                  className={`relative min-w-[10px] flex-1 rounded-sm ${on ? "bg-accent-soft" : ""}`}
                >
                  <span className="absolute inset-x-0 top-1/2 h-px bg-border" />
                  <span
                    className={`absolute inset-x-[15%] rounded-sm ${d.pnl >= 0 ? "bg-up" : "bg-down"} ${pick && !on ? "opacity-40" : ""}`}
                    style={d.pnl >= 0 ? { bottom: "50%", height: `${Math.max(h, 1)}%` } : { top: "50%", height: `${Math.max(h, 1)}%` }}
                  />
                </button>
              );
            })}
          </div>
        )}
        {days.length > 0 && (
          <div className="tabular flex justify-between text-[11px] text-faint">
            <span>{md(days[0].date)}</span>
            <span>막대를 누르면 그날 판 종목</span>
            <span>{md(days[days.length - 1].date)}</span>
          </div>
        )}
      </div>

      {sel && (
        <div className="space-y-1 rounded-lg bg-background p-3">
          <div className="flex items-baseline justify-between text-sm">
            <span className="font-semibold">{md(sel.date)} 판 종목 {sel.items.length}</span>
            <span className={`tabular font-semibold ${tone(sel.pnl)}`}>{man(sel.pnl)}</span>
          </div>
          <div className="divide-y divide-border">
            {sel.items.map((it, i) => (
              <div key={it.code + i} className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-baseline gap-3 py-1.5 text-sm">
                <span className="flex min-w-0 items-center gap-1.5 truncate">
                  <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: colorOf?.(it.strategy_id) ?? "var(--faint)" }} />
                  {it.name}
                  <span className="text-[11px] text-faint">
                    {it.reason} · {md(it.entry_date)} 매수
                  </span>
                </span>
                <span className={`tabular text-xs ${tone(it.ret ?? 0)}`}>{it.ret == null ? "–" : `${it.ret > 0 ? "+" : ""}${it.ret.toFixed(1)}%`}</span>
                <span className={`tabular text-right font-semibold ${tone(it.pnl)}`}>{man(it.pnl)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      <p className="text-[11px] text-faint">모두 실제 계좌 기준 (수수료 0.015% · 거래세 0.20%를 뺀 손익) · 점선 = 시작 5,000만 · 오늘 금액은 들고 있는 종목 평가 포함</p>
    </section>
  );
}
