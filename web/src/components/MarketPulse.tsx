"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, cachedApi } from "@/lib/api";
import type { Spec, Trade } from "@/lib/types";
import { useBackClose } from "@/lib/useBackClose";
import TradeChart from "./TradeChart";

interface KrFear {
  available: boolean;
  as_of?: string;
  score?: number;
  label?: string;
  week_ago?: number | null;
  month_ago?: number | null;
  components?: { name: string; value: number | null; unit: string; score: number | null }[];
  vkospi?: { value: number; date: string; prev: number | null } | null;
  vkospi_note?: string | null;
}
interface UsFear {
  available: boolean;
  as_of?: string;
  value?: number;
  prev?: number;
  label?: string;
  year_low?: number;
  year_high?: number;
  pct_rank?: number;
  sp500_vs_200?: number | null;
}
interface Tile {
  code: string;
  name: string;
  cap: number;
  d1: number | null;
  w1: number | null;
  m1: number | null;
}
interface Pulse {
  kr: KrFear;
  us: UsFear;
  treemap: Record<Mkt, { as_of?: string; items: Tile[] } | undefined>;
}

type Mkt = "KOSPI" | "KOSDAQ" | "US";
const MKT_LABEL: Record<Mkt, string> = { KOSPI: "코스피", KOSDAQ: "코스닥", US: "미국" };
const md = (d?: string) => (d ? `${+d.slice(5, 7)}/${+d.slice(8, 10)}` : "");
// 한국 시각: 평일 09:00 이후이고 오늘이 지도 날짜보다 뒤면 키움 실시간 값으로 1일 등락을 바꾼다
function kst() {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", weekday: "short", hour12: false,
    }).formatToParts(new Date()).map((x) => [x.type, x.value]),
  );
  return { date: `${p.year}-${p.month}-${p.day}`, hm: `${p.hour === "24" ? "00" : p.hour}:${p.minute}`, wd: p.weekday as string };
}
interface LiveQ {
  price: number;
  change_pct: number | null;
  mktcap: number | null;
}
const sign = (v: number, nd = 1) => `${v > 0 ? "+" : ""}${v.toFixed(nd)}`;

// 0 공포(파랑) ~ 100 탐욕(빨강)
function fearColor(score: number): string {
  if (score < 25) return "var(--down)";
  if (score < 45) return "color-mix(in srgb, var(--down) 55%, var(--muted))";
  if (score <= 55) return "var(--muted)";
  if (score <= 75) return "color-mix(in srgb, var(--up) 55%, var(--muted))";
  return "var(--up)";
}

// 홈: 국장 공포·탐욕 · 미장 VIX · 시총 지도
export default function MarketPulse() {
  const [p, setP] = useState<Pulse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    cachedApi<Pulse>("/api/pulse")
      .then(setP)
      .catch((e) => setErr((e as Error).message));
  }, []);

  if (err) return <p className="text-xs text-faint">시장 온도: {err}</p>;
  if (!p) return <p className="text-xs text-faint">시장 온도 불러오는 중…</p>;
  const kr = p.kr;
  const us = p.us;

  return (
    <div className="space-y-3">
      <section className="grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="space-y-1.5 rounded-xl border border-border bg-card p-3 text-left"
        >
          <div className="flex items-baseline justify-between text-xs text-muted">
            <span>국장 공포·탐욕</span>
            <span className="tabular">{md(kr.as_of)}</span>
          </div>
          {kr.available && kr.score != null ? (
            <>
              <div className="flex items-baseline gap-1.5">
                <span className="tabular text-2xl font-semibold" style={{ color: fearColor(kr.score) }}>
                  {Math.round(kr.score)}
                </span>
                <span className="text-sm font-medium" style={{ color: fearColor(kr.score) }}>
                  {kr.label}
                </span>
              </div>
              <Meter value={kr.score} />
              <div className="tabular text-[11px] text-faint">
                1주 전 {kr.week_ago != null ? Math.round(kr.week_ago) : "–"} · 1달 전 {kr.month_ago != null ? Math.round(kr.month_ago) : "–"}
                {kr.vkospi && ` · VKOSPI ${kr.vkospi.value.toFixed(1)}`}
              </div>
            </>
          ) : (
            <div className="text-sm text-faint">자료 없음</div>
          )}
        </button>
        <div className="space-y-1.5 rounded-xl border border-border bg-card p-3">
          <div className="flex items-baseline justify-between text-xs text-muted">
            <span>미장 공포 (VIX)</span>
            <span className="tabular">{md(us.as_of)}</span>
          </div>
          {us.available && us.value != null ? (
            <>
              <div className="flex items-baseline gap-1.5">
                <span className="tabular text-2xl font-semibold" style={{ color: vixColor(us.value) }}>
                  {us.value.toFixed(1)}
                </span>
                <span className="text-sm font-medium" style={{ color: vixColor(us.value) }}>
                  {us.label}
                </span>
                {us.prev != null && <span className="tabular text-[11px] text-faint">{sign(us.value - us.prev)}</span>}
              </div>
              <Meter value={Math.min(100, Math.max(0, ((40 - us.value) / 30) * 100))} />
              <div className="tabular text-[11px] text-faint">
                1년 {us.year_low?.toFixed(0)}~{us.year_high?.toFixed(0)}
                {us.sp500_vs_200 != null && ` · S&P 200일선 ${sign(us.sp500_vs_200)}%`}
              </div>
            </>
          ) : (
            <div className="text-sm text-faint">자료 없음 (지수 갱신 뒤 나와요)</div>
          )}
        </div>
      </section>

      {open && kr.available && (
        <section className="space-y-1.5 rounded-xl border border-border bg-card p-3 text-xs">
          <div className="text-muted">국장 공포·탐욕 = 아래 다섯 가지의 평균 (각각 지난 2년 안에서 지금 위치, 0 공포 ~ 100 탐욕)</div>
          {kr.components?.map((c) => (
            <div key={c.name} className="grid grid-cols-[5.5rem_1fr_2rem] items-center gap-2">
              <span>{c.name}</span>
              <span className="text-faint">
                {c.value != null ? `${c.value}${c.unit}` : "–"}
              </span>
              <span className="tabular text-right font-semibold" style={{ color: c.score != null ? fearColor(c.score) : undefined }}>
                {c.score ?? "–"}
              </span>
            </div>
          ))}
          {kr.vkospi_note && <div className="text-warning">VKOSPI: {kr.vkospi_note}</div>}
          <div className="text-faint">참고용 · 공포일 때 v2b 신호가 많아지고, 탐욕일 때는 조용한 편이에요.</div>
        </section>
      )}

      <Treemap data={p.treemap} />
    </div>
  );
}

function vixColor(v: number): string {
  if (v < 15) return "var(--up)";
  if (v < 20) return "var(--muted)";
  if (v < 30) return "color-mix(in srgb, var(--down) 55%, var(--muted))";
  return "var(--down)";
}

// 0~100 막대 (왼쪽 공포 · 오른쪽 탐욕)
function Meter({ value }: { value: number }) {
  return (
    <div className="relative h-1.5 rounded-full" style={{ background: "linear-gradient(90deg, var(--down), var(--bar-track) 50%, var(--up))" }}>
      <span
        className="absolute top-1/2 h-3 w-1 -translate-y-1/2 rounded-sm bg-foreground"
        style={{ left: `calc(${Math.min(100, Math.max(0, value))}% - 2px)` }}
      />
    </div>
  );
}

// ---------------------------------------------------------------- 시총 지도
type Period = "d1" | "w1" | "m1";
const PERIODS: [Period, string][] = [
  ["d1", "1일"],
  ["w1", "1주"],
  ["m1", "1달"],
];
interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
  t: Tile;
}

// 정사각형에 가깝게 나누기 (squarified)
function squarify(items: Tile[], W: number, H: number): Rect[] {
  const total = items.reduce((s, t) => s + t.cap, 0);
  if (!total || W <= 0 || H <= 0) return [];
  const scale = (W * H) / total;
  const nodes = items.map((t) => ({ t, a: t.cap * scale }));
  const out: Rect[] = [];
  let x = 0,
    y = 0,
    w = W,
    h = H;
  let row: typeof nodes = [];
  const worst = (r: typeof nodes, side: number) => {
    const s = r.reduce((a, n) => a + n.a, 0);
    const mx = Math.max(...r.map((n) => n.a));
    const mn = Math.min(...r.map((n) => n.a));
    return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn));
  };
  const lay = (r: typeof nodes) => {
    const s = r.reduce((a, n) => a + n.a, 0);
    if (w >= h) {
      const cw = s / h;
      let cy = y;
      for (const n of r) {
        const nh = n.a / cw;
        out.push({ x, y: cy, w: cw, h: nh, t: n.t });
        cy += nh;
      }
      x += cw;
      w -= cw;
    } else {
      const ch = s / w;
      let cx = x;
      for (const n of r) {
        const nw = n.a / ch;
        out.push({ x: cx, y, w: nw, h: ch, t: n.t });
        cx += nw;
      }
      y += ch;
      h -= ch;
    }
  };
  for (const n of nodes) {
    const side = Math.min(w, h);
    if (!row.length || worst([...row, n], side) <= worst(row, side)) row.push(n);
    else {
      lay(row);
      row = [n];
    }
  }
  if (row.length) lay(row);
  return out;
}

// 등락률 → 색 (한국식: 오르면 빨강, 내리면 파랑). 3% 에서 가장 진함
function tileColor(v: number | null, period: Period): string {
  if (v == null) return "var(--bar-track)";
  const full = period === "d1" ? 3 : period === "w1" ? 7 : 15;
  const k = Math.min(1, Math.abs(v) / full);
  const pct = Math.round(25 + k * 75);
  return `color-mix(in srgb, ${v >= 0 ? "var(--up)" : "var(--down)"} ${pct}%, var(--card))`;
}

const COUNTS = [30, 50, 100];
const usd = (v: number) => (v >= 1e12 ? `$${(v / 1e12).toFixed(2)}T` : `$${Math.round(v / 1e9).toLocaleString("en-US")}B`);
const jo = (won: number) => (won >= 1e12 ? `${(won / 1e12).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}조` : `${Math.round(won / 1e8).toLocaleString("ko-KR")}억`);

function asTrade(t: Tile, m: Mkt): Trade {
  const today = kst().date;
  return {
    code: t.code, name: t.name, market: MKT_LABEL[m], signal_date: today, signal_close: null, limit: null, fill_date: null, fill_price: null,
    outcome: "", outcome_opt: "", uncertain: false, exit_date: null, exit_price: null, days: null, ret: null, ret_opt: null, mfe: null, mae: null,
  };
}

function Treemap({ data }: { data: Pulse["treemap"] }) {
  const [market, setMarket] = useState<Mkt>("KOSPI");
  const [period, setPeriod] = useState<Period>("d1");
  const [count, setCount] = useState(50);
  const [pick, setPick] = useState<Tile | null>(null);
  // 네모를 누르면 그 종목 차트 (국내: 키움 실시간 5분봉부터, 미국: 일봉)
  const [chart, setChart] = useState<{ t: Tile; market: Mkt } | null>(null);
  const [spec, setSpec] = useState<Spec | null>(null);
  const closeChart = useCallback(() => setChart(null), []);
  const back = useBackClose(!!chart, closeChart);
  const openChart = (t: Tile) => {
    setPick(t);
    if (!spec)
      api<{ spec: Spec }>("/api/parse", { body: { text: "[신호]\n종가 > 0\n" } })
        .then((r) => setSpec({ ...r.spec, exit: { ...r.spec.exit, target_pct: null, stop_pct: null } }))
        .catch(() => {});
    back.push();
    setChart({ t, market });
  };
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(0);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const H = Math.round(Math.min(560, Math.max(320, W * 0.75)));
  const d = data[market];
  // 장중(또는 저녁 갱신 전)엔 키움 실시간 값: 1일 등락·시총만 바꾼다 (1주·1달은 종가 기준 그대로)
  const [live, setLive] = useState<{ market: Mkt; at: string; q: Record<string, LiveQ> } | null>(null);
  useEffect(() => {
    if (market === "US" || !d?.as_of) return;
    const codes = d.items.slice(0, 100).map((t) => t.code);
    let stop = false;
    const load = () => {
      const n = kst();
      if (n.wd === "Sat" || n.wd === "Sun" || n.hm < "09:00" || n.date <= (d.as_of ?? "")) return;
      api<Record<string, LiveQ>>(`/api/live/quote?codes=${codes.join(",")}`)
        .then((q) => !stop && setLive({ market, at: n.hm, q }))
        .catch(() => {});
    };
    load();
    const t = setInterval(() => kst().hm < "15:35" && load(), 60_000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [market, d]);
  const lv = live && live.market === market ? live : null;
  const items = useMemo(() => {
    const base = (d?.items ?? []).slice(0, count);
    if (!lv) return base;
    return base.map((t) => {
      const q = lv.q[t.code];
      if (!q || q.change_pct == null) return t;
      return { ...t, d1: q.change_pct, cap: q.mktcap ?? t.cap * (1 + q.change_pct / 100) };
    });
  }, [d, count, lv]);
  const rects = useMemo(() => squarify(items, W, H), [items, W, H]);
  const sel = pick && items.find((t) => t.code === pick.code);

  return (
    <section className="space-y-2 rounded-xl border border-border bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="text-muted">
          시총 지도 · {MKT_LABEL[market]} 상위 {Math.min(count, d?.items.length ?? 0)}{" "}
          {lv ? (
            <span className="text-accent">· 실시간 {lv.at} (1일)</span>
          ) : (
            d?.as_of && `· ${md(d.as_of)} 종가`
          )}
        </span>
        <span className="flex flex-wrap gap-1">
          {(["KOSPI", "KOSDAQ", "US"] as const).map((m) => (data[m]?.items.length ?? 0) > 0 && (
            <button
              key={m}
              type="button"
              onClick={() => {
                setMarket(m);
                setPick(null);
              }}
              className={`whitespace-nowrap rounded-md px-2 py-0.5 ${market === m ? "bg-accent text-on-accent" : "border border-border text-muted"}`}
            >
              {MKT_LABEL[m]}
            </button>
          ))}
          <span className="w-1" />
          {COUNTS.map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => setCount(n)}
              className={`whitespace-nowrap rounded-md px-2 py-0.5 ${count === n ? "bg-accent text-on-accent" : "border border-border text-muted"}`}
            >
              {n}
            </button>
          ))}
          <span className="w-1" />
          {PERIODS.map(([k, label]) => (
            <button
              key={k}
              type="button"
              onClick={() => setPeriod(k)}
              className={`whitespace-nowrap rounded-md px-2 py-0.5 ${period === k ? "bg-accent text-on-accent" : "border border-border text-muted"}`}
            >
              {label}
            </button>
          ))}
        </span>
      </div>
      <div className="flex min-h-9 items-center justify-between gap-2 rounded-lg bg-background px-3 py-1.5 text-sm">
        {sel ? (
          <>
            <span className="min-w-0 truncate font-semibold">{market === "US" ? `${sel.code} · ${sel.name.split(" - ")[0]}` : sel.name}</span>
            <span className="tabular flex shrink-0 gap-2 text-xs">
              {PERIODS.map(([k, label]) => {
                const v = sel[k];
                return (
                  <span key={k} className={k === period ? "font-semibold" : "text-muted"}>
                    {label} <span className={v == null ? "" : v >= 0 ? "text-up" : "text-down"}>{v == null ? "–" : `${sign(v, 1)}%`}</span>
                  </span>
                );
              })}
              <span className="text-faint">{market === "US" ? usd(sel.cap) : jo(sel.cap)}</span>
            </span>
          </>
        ) : (
          <span className="text-xs text-faint">네모를 누르면 그 종목 차트가 열려요 (국내는 실시간 분봉)</span>
        )}
      </div>
      <div ref={box} className="relative w-full overflow-hidden rounded-lg" style={{ height: H }}>
        {rects.map((r) => {
          const v = r.t[period];
          const big = r.w > 46 && r.h > 30;
          const mid = !big && r.w > 30 && r.h > 16;
          const on = sel?.code === r.t.code;
          return (
            <button
              type="button"
              key={r.t.code}
              onClick={() => openChart(r.t)}
              title={`${r.t.name} ${v == null ? "–" : sign(v, 2) + "%"}`}
              className={`absolute flex flex-col items-center justify-center overflow-hidden border text-center leading-tight ${on ? "z-10 border-2 border-foreground" : "border-background"}`}
              style={{ left: r.x, top: r.y, width: r.w, height: r.h, background: tileColor(v, period) }}
            >
              {mid && <span className="tabular text-[9px]">{v == null ? "–" : `${sign(v, 1)}`}</span>}
              {big && (
                <>
                  <span className="max-w-full truncate px-0.5 font-medium" style={{ fontSize: Math.min(15, Math.max(9, Math.sqrt(r.w * r.h) / 7)) }}>
                    {market === "US" ? r.t.code : r.t.name}
                  </span>
                  <span className="tabular" style={{ fontSize: Math.min(13, Math.max(8, Math.sqrt(r.w * r.h) / 8)) }}>
                    {v == null ? "–" : `${sign(v, 1)}%`}
                  </span>
                </>
              )}
            </button>
          );
        })}
      </div>
      <div className="text-[11px] text-faint">네모 크기 = 시가총액 · 빨강 오름 / 파랑 내림 · 코스피·코스닥 1일은 장중 실시간(키움, 1분마다), 1주·1달과 미국은 마지막 종가 기준</div>
      {chart && spec && (
        <TradeChart
          trade={asTrade(chart.t, chart.market)}
          spec={spec}
          dataset={chart.market === "US" ? "us" : "kr"}
          currency={chart.market === "US" ? "USD" : "KRW"}
          onClose={back.close}
          strategy={false}
          startMode={chart.market === "US" ? "day" : 5}
          subtitle={`시총 지도 · ${MKT_LABEL[chart.market]}${chart.market === "US" ? " · 일봉 (미국은 실시간 없음)" : " · 분봉은 키움 실시간 (30초마다)"}`}
        />
      )}
    </section>
  );
}
