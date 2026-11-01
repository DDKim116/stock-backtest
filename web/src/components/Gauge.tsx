"use client";

import { type ReactNode, useEffect, useState } from "react";
import { cachedApi } from "@/lib/api";

interface GaugeData {
  available: boolean;
  as_of?: string;
  kospi?: number;
  box?: { ratio: number; is_box: boolean; high: number; low: number; pos: number | null };
  trend?: { ma200: number; pct: number };
  skew?: { top10: number | null; rest: number | null; gap: number | null };
  breadth?: number | null;
  v2b?: { signals: number | null; days: number | null };
  hints?: { who: string; text: string }[];
  macro?: MacroRow[];
}

interface Hist {
  now: number;
  min: number;
  max: number;
  avg: number;
  pct: number;
  start: string;
  spark: number[];
}
interface MacroRow {
  id?: string;
  label: string;
  value: string;
  note: string;
  alert: boolean;
  hist?: Hist;
  ref?: number | null;
}

const sign = (v: number, nd = 1) => `${v > 0 ? "+" : ""}${v.toFixed(nd)}`;
const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));

// 점 바로 밑의 ▲ (점과 같은 위치) + 옆에 글 (왼쪽 절반이면 오른쪽에, 오른쪽 절반이면 왼쪽에)
function Under({ p, children }: { p: number; children: ReactNode }) {
  const x = clamp(p, 0, 100);
  return (
    <div className="relative h-4 text-[11px] font-medium">
      <span className="absolute top-0 -translate-x-1/2 leading-4" style={{ left: `${x}%` }}>
        ▲
      </span>
      <span
        className="absolute top-0 whitespace-nowrap leading-4"
        style={x < 50 ? { left: `calc(${x}% + 9px)` } : { right: `calc(${100 - x}% + 9px)` }}
      >
        {children}
      </span>
    </div>
  );
}

// 0 을 가운데 둔 막대 (scale = 끝까지 갔을 때의 값)
function Diverge({ v, scale, label }: { v: number; scale: number; label?: string }) {
  const w = (clamp(Math.abs(v) / scale, 0, 1) * 50).toFixed(1);
  return (
    <div className="flex items-center gap-2">
      {label && <span className="w-16 shrink-0 text-[11px] text-faint">{label}</span>}
      <div className="relative h-2.5 flex-1 rounded-full bg-background">
        <div className="absolute inset-y-[-3px] left-1/2 w-px bg-faint" />
        <div
          className={`absolute inset-y-0 rounded-full ${v >= 0 ? "bg-up" : "bg-down"}`}
          style={v >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }}
        />
      </div>
      {label && <span className={`tabular w-12 shrink-0 text-right text-[11px] ${v >= 0 ? "text-up" : "text-down"}`}>{sign(v, 0)}%</span>}
    </div>
  );
}

function Pic({ g, id }: { g: GaugeData; id: string }) {
  if (id === "box" && g.box) {
    const pos = g.box.pos ?? 0;
    return (
      <div className="space-y-1">
        <div className="relative h-2.5 rounded-full bg-gradient-to-r from-[var(--down)] to-[var(--up)] opacity-80">
          <div className="absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-foreground" style={{ left: `${clamp(pos, 0, 100)}%` }} />
        </div>
        <Under p={pos}>지금 {pos}%</Under>
        <div className="tabular flex justify-between text-[11px] text-faint">
          <span>1년 저점 {Math.round(g.box.low).toLocaleString("ko-KR")} (0%)</span>
          <span>(100%) 고점 {Math.round(g.box.high).toLocaleString("ko-KR")}</span>
        </div>
      </div>
    );
  }
  if (id === "trend" && g.trend)
    return (
      <div className="space-y-1">
        <Diverge v={g.trend.pct} scale={30} />
        <div className="flex justify-between text-[11px] text-faint">
          <span>200일선 아래</span>
          <span>|</span>
          <span>200일선 위</span>
        </div>
      </div>
    );
  if (id === "skew" && g.skew && g.skew.top10 != null && g.skew.rest != null) {
    const sc = Math.max(10, Math.abs(g.skew.top10), Math.abs(g.skew.rest));
    return (
      <div className="space-y-1">
        <Diverge v={g.skew.top10} scale={sc} label="상위 10" />
        <Diverge v={g.skew.rest} scale={sc} label="보통 종목" />
      </div>
    );
  }
  if (id === "breadth" && g.breadth != null) {
    const b = clamp(g.breadth, 0, 100);
    return (
      <div className="space-y-1">
        <div className="relative h-2.5 overflow-hidden rounded-full bg-[var(--down)]">
          <div className="absolute inset-y-0 left-0 bg-[var(--up)]" style={{ width: `${b}%` }} />
          <div className="absolute inset-y-0 left-1/2 w-0.5 bg-card" />
        </div>
        <div className="tabular flex justify-between text-[11px] text-faint">
          <span className="text-up">오른 종목 {b.toFixed(0)}%</span>
          <span>50% 기준</span>
          <span className="text-down">내린 종목 {(100 - b).toFixed(0)}%</span>
        </div>
      </div>
    );
  }
  return null;
}

// 탭하면 펼쳐지는 쉬운 설명
const EASY: Record<string, string[]> = {
  box: [
    "최근 1년 코스피 가장 높았던 값 ÷ 가장 낮았던 값.",
    "1.25배 이내 = 박스장(좁은 범위에서 오르락내리락, 2012~2016 '박스피'). 넘으면 크게 오르거나 빠진 추세장.",
    "점 = 지금 위치. 0% 는 1년 중 바닥, 100% 는 꼭대기.",
  ],
  trend: [
    "코스피가 최근 200거래일(약 10개월) 평균보다 몇 % 위·아래인지.",
    "위면 긴 흐름이 오르는 중, 아래면 내리는 중. 가장 흔하게 쓰는 '큰 흐름' 기준.",
  ],
  skew: [
    "최근 3개월 동안 시총 상위 10개(삼성전자·SK하이닉스 등) 수익률 − 101위 아래 보통 종목 평균 수익률.",
    "+10%p 이상 = 대형주만 오르는 장 (보통 종목은 지수만큼 못 오름). 마이너스 = 중소형주가 더 잘 오름.",
    "실적 가속 중형주 전략은 쏠림이 강할 때 지수보다 약했고, 박스장 + 쏠림 약할 때 강했어요.",
  ],
  breadth: [
    "최근 20거래일 동안 매일 '오른 종목 비율'의 평균.",
    "50% 넘으면 오르는 종목이 더 많음(골고루 오르는 장), 아래면 내리는 종목이 더 많음.",
    "지수는 오르는데 이게 50% 아래면 → 몇몇 대형주가 지수를 끌어올리는 중.",
  ],
  v2b: ["최근 20거래일 동안 v2b 신호가 몇 건 나왔는지. 시장이 흔들릴 때 많이 나오고, 조용하면 쉬어요."],
};

// 미국 경제: 지난 20년 작은 그림 (가로 = 시간, 세로 = 값). ref = 기준선(예: 금리차 0), 점선 = 20년 평균
function Spark({ h, ref0 }: { h: Hist; ref0?: number | null }) {
  const W = 300;
  const H = 56;
  const lo = Math.min(h.min, ref0 ?? h.min);
  const hi = Math.max(h.max, ref0 ?? h.max);
  const y = (v: number) => (hi > lo ? H - 3 - ((v - lo) / (hi - lo)) * (H - 6) : H / 2);
  const n = h.spark.length;
  const x = (i: number) => (n > 1 ? (i / (n - 1)) * W : 0);
  const pts = h.spark.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-14 w-full" aria-hidden>
        {ref0 != null && <line x1={0} x2={W} y1={y(ref0)} y2={y(ref0)} stroke="var(--critical, var(--up))" strokeWidth={1} vectorEffect="non-scaling-stroke" />}
        <line x1={0} x2={W} y1={y(h.avg)} y2={y(h.avg)} stroke="var(--faint)" strokeDasharray="4 4" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth={1.6} vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="tabular flex justify-between text-[11px] text-faint">
        <span>{h.start.slice(0, 4)}</span>
        <span>
          낮음 {h.min.toLocaleString("ko-KR", { maximumFractionDigits: 2 })} · 평균(점선) {h.avg.toLocaleString("ko-KR", { maximumFractionDigits: 2 })} · 높음{" "}
          {h.max.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}
        </span>
        <span>지금</span>
      </div>
    </div>
  );
}

// 지난 20년 중 지금이 어디쯤인지 (왼쪽 = 가장 낮았을 때, 오른쪽 = 가장 높았을 때)
function Where({ h }: { h: Hist }) {
  const p = h.pct; // 지난 20년 중 지금보다 낮았던 날의 비율 = 점 위치
  const word = h.pct >= 80 ? "20년 중 높은 편" : h.pct <= 20 ? "20년 중 낮은 편" : "20년 중 보통";
  return (
    <div className="space-y-1">
      <div className="relative h-2 rounded-full bg-background">
        <div className="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-accent" style={{ left: `${clamp(p, 0, 100)}%` }} />
      </div>
      <Under p={p}>{word}</Under>
      <div className="flex justify-between text-[11px] text-faint">
        <span>20년 중 가장 낮음</span>
        <span>지금보다 낮았던 날 {h.pct}%</span>
        <span>가장 높음</span>
      </div>
    </div>
  );
}

const EASY_MACRO: Record<string, string[]> = {
  t10y2y: [
    "미국 10년 국채 금리 − 2년 국채 금리. 보통은 오래 빌려줄수록 이자가 높아서 플러스(+0~2.5%p).",
    "마이너스 = '역전'. 시장이 '앞으로 경기가 나빠져 금리를 내릴 것'이라고 보는 것. 2000·2006~07·2022~24 에 역전됐고 앞의 두 번은 1~2년 뒤 침체가 왔어요.",
    "실제 침체는 역전이 풀려 다시 플러스로 돌아설 무렵 온 경우가 많았어요. 다만 시점을 맞히기 어려워 우리 시험에선 매매 신호로 쓰면 손해였어요.",
    "그림의 빨간 선 = 0 (이 아래가 역전).",
  ],
  baa: [
    "신용도 중간쯤(Baa) 회사가 돈 빌릴 때 금리 − 미국 정부 10년 금리. '회사 빚을 얼마나 불안하게 보나'의 온도계.",
    "평소 1.5~2.5%p. 3%p 넘으면 긴장, 2008 금융위기 땐 6%p 넘었고 2020 코로나 땐 4%p 넘게 튀었어요.",
    "주식보다 먼저 튀는 경우가 있지만, 우리 시험에선 이걸로 주식을 쉬면 오히려 손해였어요 → 지켜보기만.",
  ],
  unrate: [
    "미국 일하려는 사람 중 일자리가 없는 사람 비율. 매달 첫 금요일 고용보고서로 발표.",
    "3~4% = 일자리 넘침, 5% 안팎 = 보통. 2009 금융위기 10%, 2020 코로나 14.7%까지.",
    "수준보다 '오르는 속도'가 중요: 3개월 평균이 1년 저점보다 0.5%p 이상 오르면 침체 경고(삼의 법칙). 우리가 알림을 받는 유일한 지표예요.",
  ],
  dff: [
    "미국 중앙은행(연준)이 정하는 기준 금리. 모든 대출·예금 이자의 바탕.",
    "2009~15·2020~21 은 거의 0%(돈 풀기), 2023~24 엔 5%대(2000년대 이후 가장 높음, 물가 잡기).",
    "올리면 빌리기 비싸져 주식(특히 성장주·QLD·비트코인)에 부담, 내리면 보통 도움. 단 위기가 와서 급하게 내릴 땐 주가도 빠져요.",
  ],
  krw: [
    "1달러를 사려면 원화가 얼마 드는지. 오르면 원화 약세(달러 비쌈).",
    "2010년대엔 1,100~1,250원이 보통, 1,400원 넘었던 때는 2008 금융위기·2022·2024~ 등.",
    "원화 약세가 빠르면 외국인이 한국 주식을 파는 경우가 많아요. 미국 적립(VOO 등)은 원화로 사니까 같은 돈으로 덜 사지만, 이미 가진 건 원화로 더 커 보여요.",
  ],
};

function Macro({ rows }: { rows: MacroRow[] }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div>
      <div className="mb-1 text-xs text-muted">미국 경제 (FRED) · 누르면 설명 · 그림 = 지난 20년</div>
      <div className="divide-y divide-border">
        {rows.map((m) => {
          const id = m.id ?? m.label;
          return (
            <button key={m.label} type="button" onClick={() => setOpen(open === id ? null : id)} className="block w-full space-y-1.5 py-2.5 text-left text-sm">
              <div className="grid grid-cols-[5.5rem_4.5rem_1fr] items-baseline gap-2">
                <span className="font-bold">
                  {m.label} <span className="text-[10px] text-faint">{open === id ? "▴" : "▾"}</span>
                </span>
                <span className={`tabular text-right font-semibold ${m.alert ? "text-warning" : ""}`}>{m.value}</span>
                <span className={`text-xs ${m.alert ? "text-warning" : "text-faint"}`}>{m.note}</span>
              </div>
              {m.hist && (
                <div className="space-y-1.5 px-5">
                  <Where h={m.hist} />
                  {open === id && <Spark h={m.hist} ref0={m.ref} />}
                </div>
              )}
              {open === id && EASY_MACRO[id] && (
                <ul className="space-y-0.5 rounded-lg bg-background p-2.5 text-xs text-muted">
                  {EASY_MACRO[id].map((t) => (
                    <li key={t}>· {t}</li>
                  ))}
                </ul>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// 쏠림·폭·200일선을 같이 본 한 줄
function together(g: GaugeData): string | null {
  const gap = g.skew?.gap;
  const b = g.breadth;
  const up = (g.trend?.pct ?? 0) >= 0;
  if (gap == null || b == null || !g.trend) return null;
  if (up && b < 50 && gap >= 10) return "지수는 오르지만 대부분 종목은 제자리·하락 → 몇몇 대형주가 끌어올리는 장";
  if (up && b >= 50 && gap < 10) return "많은 종목이 같이 오름 → 골고루 오르는 건강한 상승장";
  if (up && b >= 50) return "많은 종목이 오르지만 대형주가 특히 강한 상승장";
  if (up) return "지수는 큰 흐름 위지만 오르는 종목이 적음 → 힘이 빠지는 중일 수 있음";
  if (b >= 50) return "지수는 큰 흐름 아래지만 오르는 종목이 많아짐 → 바닥을 다지는 중일 수 있음";
  return "지수도 종목들도 약함 → 하락장 (v2b 신호가 많이 나오는 때)";
}

// 시장 탭: 지금 코스피 모습 (박스·추세·쏠림·폭·v2b) — 참고용, 규칙을 바꾸지 않는다
export default function Gauge({ bare = false }: { bare?: boolean }) {
  const [g, setG] = useState<GaugeData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    cachedApi<GaugeData>("/api/gauge")
      .then(setG)
      .catch((e) => setErr((e as Error).message));
  }, []);

  if (err) return <p className="text-xs text-faint">계기판: {err}</p>;
  if (!g) return <p className="text-xs text-faint">계기판 불러오는 중…</p>;
  if (!g.available) return null;

  const rows: { id: string; label: string; value: string; note: string; tone?: "up" | "down" }[] = [];
  if (g.box)
    rows.push({
      id: "box",
      label: "박스 정도",
      value: `${g.box.ratio.toFixed(2)}배`,
      note: `1년 고점÷저점 · ${g.box.is_box ? "1.25배 이내 → 박스장" : "범위 넓음 → 추세장"}${g.box.pos != null ? ` · 범위 안 위치 ${g.box.pos}%` : ""}`,
    });
  if (g.trend)
    rows.push({
      id: "trend",
      label: "200일선",
      value: `${sign(g.trend.pct)}%`,
      note: `코스피 ${g.kospi?.toLocaleString("ko-KR")} · 200일선 ${Math.round(g.trend.ma200).toLocaleString("ko-KR")}`,
      tone: g.trend.pct >= 0 ? "up" : "down",
    });
  if (g.skew && g.skew.gap != null)
    rows.push({
      id: "skew",
      label: "대형주 쏠림",
      value: `${sign(g.skew.gap, 0)}%p`,
      note: `최근 60일 · 상위 10 ${sign(g.skew.top10 ?? 0, 0)}% vs 101위 아래 보통 종목 ${sign(g.skew.rest ?? 0, 0)}%`,
      tone: g.skew.gap >= 0 ? "up" : "down",
    });
  if (g.breadth != null)
    rows.push({
      id: "breadth",
      label: "시장 폭",
      value: `${g.breadth.toFixed(0)}%`,
      note: "최근 20일 평균, 그날 오른 종목 비율 (50% 아래면 내린 종목이 더 많음)",
      tone: g.breadth >= 50 ? "up" : "down",
    });
  if (g.v2b?.signals != null)
    rows.push({ id: "v2b", label: "v2b 신호", value: `${g.v2b.signals}건`, note: `최근 20거래일 · ${g.v2b.days}일에 나옴` });

  return (
    <section className={bare ? "space-y-3" : "space-y-3 rounded-xl border border-border bg-card p-4"}>
      <div className="flex items-baseline justify-between">
        {!bare && <h2 className="text-lg font-bold">시장 계기판</h2>}
        <span className="tabular text-xs text-muted">{g.as_of} · 코스피</span>
      </div>
      <div className="divide-y divide-border">
        {rows.map((r) => (
          <button
            key={r.label}
            type="button"
            onClick={() => setOpen(open === r.id ? null : r.id)}
            className="block w-full space-y-1.5 py-2.5 text-left text-sm"
          >
            <div className="grid grid-cols-[5.5rem_4.5rem_1fr] items-baseline gap-2">
              <span className="font-bold">
                {r.label} <span className="text-[10px] text-faint">{open === r.id ? "▴" : "▾"}</span>
              </span>
              <span className={`tabular text-right font-semibold ${r.tone === "up" ? "text-up" : r.tone === "down" ? "text-down" : ""}`}>
                {r.value}
              </span>
              <span className="text-xs text-faint">{r.note}</span>
            </div>
            <div className="px-5">
              <Pic g={g} id={r.id} />
            </div>
            {open === r.id && (
              <ul className="space-y-0.5 rounded-lg bg-background p-2.5 text-xs text-muted">
                {EASY[r.id]?.map((t) => (
                  <li key={t}>· {t}</li>
                ))}
              </ul>
            )}
          </button>
        ))}
      </div>
      {together(g) && (
        <div className="rounded-lg border border-border p-2.5 text-xs">
          <span className="text-muted">200일선 · 쏠림 · 폭을 같이 보면 → </span>
          <span className="font-medium">{together(g)}</span>
        </div>
      )}
      {g.macro && g.macro.length > 0 && <Macro rows={g.macro} />}
      {g.hints && g.hints.length > 0 && (
        <div className="space-y-1.5 rounded-lg bg-background p-3 text-xs">
          <div className="text-muted">지금 장에서 우리 전략들은 (연구 결과 기준)</div>
          {g.hints.map((h) => (
            <div key={h.who} className="flex gap-2">
              <span className="w-24 shrink-0 font-medium">{h.who}</span>
              <span className="text-muted">{h.text}</span>
            </div>
          ))}
        </div>
      )}
      <p className="text-[11px] text-faint">항목을 누르면 쉬운 설명이 나와요. 지금 모습을 보는 참고용이에요. 이걸로 규칙을 바꾸지 않아요 — v2b·적립은 어떤 장이든 그대로.</p>
    </section>
  );
}
