"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { cachedApi } from "@/lib/api";

interface AptData {
  available: boolean;
  message?: string;
  dates?: string[]; // YYYY-MM
  apt?: (number | null)[];
  kospi?: (number | null)[];
  rate?: (number | null)[];
  cycles?: [string, "인상" | "인하"][];
  source?: string | null;
}

type Range = "10" | "20" | "all";
const RANGES: [Range, string][] = [
  ["10", "10년"],
  ["20", "20년"],
  ["all", "전체"],
];
const pctS = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(0)}%`;

// 시장 탭 '서울 아파트': 서울 아파트값·코스피(시작 = 100)와 기준금리, 금리 인상기(빨강)·인하기(파랑) 배경
export default function AptChart() {
  const [d, setD] = useState<AptData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [range, setRange] = useState<Range>("20");
  const [pick, setPick] = useState<number | null>(null);
  const box = useRef<SVGSVGElement>(null);

  useEffect(() => {
    cachedApi<AptData>("/api/apt").then(setD, (e) => setErr((e as Error).message));
  }, []);

  const v = useMemo(() => {
    if (!d?.available || !d.dates) return null;
    const n = d.dates.length;
    const from = range === "all" ? 0 : Math.max(0, n - Number(range) * 12);
    const dates = d.dates.slice(from);
    const apt = d.apt!.slice(from);
    const kospi = d.kospi!.slice(from);
    const rate = d.rate!.slice(from);
    const base = (a: (number | null)[]) => a.find((x) => x != null && x > 0) ?? null;
    const a0 = base(apt);
    const k0 = base(kospi);
    const A = apt.map((x) => (x != null && a0 ? (x / a0) * 100 : null));
    const K = kospi.map((x) => (x != null && k0 ? (x / k0) * 100 : null));
    // 금리 방향 구간
    const cyc = (d.cycles ?? []).filter(([m]) => m <= dates[dates.length - 1]);
    const bands: { s: number; e: number; kind: string }[] = [];
    cyc.forEach(([m, kind], i) => {
      const next = cyc[i + 1]?.[0];
      let s = dates.findIndex((x) => x >= m);
      if (s < 0) return;
      if (m < dates[0]) s = 0;
      let e = next ? dates.findIndex((x) => x >= next) : dates.length - 1;
      if (e < 0) e = dates.length - 1;
      if (next && next < dates[0]) return;
      bands.push({ s, e, kind });
    });
    return { dates, A, K, rate, bands };
  }, [d, range]);

  if (err) return <p className="text-xs text-faint">서울 아파트: {err}</p>;
  if (!d) return <p className="text-xs text-faint">불러오는 중…</p>;
  if (!d.available || !v)
    return (
      <p className="text-xs text-muted">
        {d.message ?? "자료가 없어요."} (설정 → 한국은행 ECOS 에 키 → 연구 탭 &lsquo;한국은행 ECOS 받기&rsquo;)
      </p>
    );

  const W = 360;
  const H1 = 170;
  const H2 = 60;
  const n = v.dates.length;
  const x = (i: number) => (n > 1 ? (i / (n - 1)) * W : 0);
  // 로그 눈금: 같은 % 변화는 같은 높이 (코스피가 크게 흔들려도 아파트 선이 납작해지지 않게)
  const vals = [...v.A, ...v.K].filter((t): t is number => t != null && t > 0).map(Math.log);
  const lo = Math.min(Math.log(100), ...vals);
  const hi = Math.max(Math.log(100), ...vals);
  const y1 = (t: number) => (hi > lo ? H1 - 6 - ((Math.log(Math.max(t, 1e-9)) - lo) / (hi - lo)) * (H1 - 12) : H1 / 2);
  const rv = v.rate.filter((t): t is number => t != null);
  const rlo = Math.min(0, ...rv);
  const rhi = Math.max(1, ...rv);
  const y2 = (t: number) => H2 - 6 - ((t - rlo) / (rhi - rlo)) * (H2 - 12);
  const path = (arr: (number | null)[], y: (t: number) => number) =>
    arr.map((t, i) => (t == null ? "" : `${x(i).toFixed(1)},${y(t).toFixed(1)}`)).filter(Boolean).join(" ");
  const step = v.rate
    .map((t, i) => (t == null ? "" : `${x(i).toFixed(1)},${y2(t).toFixed(1)} ${x(i + 1 < n ? i + 1 : i).toFixed(1)},${y2(t).toFixed(1)}`))
    .filter(Boolean)
    .join(" ");
  const last = (arr: (number | null)[]) => [...arr].reverse().find((t) => t != null) ?? null;
  const p = pick ?? n - 1;
  const onMove = (cx: number) => {
    const r = box.current?.getBoundingClientRect();
    if (!r) return;
    setPick(Math.max(0, Math.min(n - 1, Math.round(((cx - r.left) / r.width) * (n - 1)))));
  };
  const aEnd = last(v.A);
  const kEnd = last(v.K);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="text-muted">
          {v.dates[0]} ~ {v.dates[n - 1]} · 시작 = 100
        </span>
        <span className="flex gap-1">
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

      <div className="grid grid-cols-3 gap-2 text-center text-xs">
        <div className="rounded-lg bg-background p-2">
          <div className="text-faint">서울 아파트</div>
          <div className="tabular font-semibold" style={{ color: "var(--accent)" }}>
            {aEnd != null ? pctS(aEnd - 100) : "–"}
          </div>
        </div>
        <div className="rounded-lg bg-background p-2">
          <div className="text-faint">코스피</div>
          <div className="tabular font-semibold" style={{ color: "var(--series-1)" }}>
            {kEnd != null ? pctS(kEnd - 100) : "–"}
          </div>
        </div>
        <div className="rounded-lg bg-background p-2">
          <div className="text-faint">기준금리</div>
          <div className="tabular font-semibold">
            {v.rate.find((t) => t != null)?.toFixed(2)} → {last(v.rate)?.toFixed(2)}%
          </div>
        </div>
      </div>

      <svg
        ref={box}
        viewBox={`0 0 ${W} ${H1 + H2 + 8}`}
        className="w-full touch-none select-none"
        onPointerDown={(e) => onMove(e.clientX)}
        onPointerMove={(e) => e.buttons && onMove(e.clientX)}
        aria-label="서울 아파트·코스피·기준금리"
      >
        {v.bands.map((b, i) => (
          <rect
            key={i}
            x={x(b.s)}
            y={0}
            width={Math.max(1, x(b.e) - x(b.s))}
            height={H1 + H2 + 8}
            fill={b.kind === "인상" ? "var(--up)" : "var(--down)"}
            opacity={0.08}
          />
        ))}
        <line x1={0} x2={W} y1={y1(100)} y2={y1(100)} stroke="var(--faint)" strokeDasharray="3 4" strokeWidth={0.8} />
        <polyline points={path(v.K, y1)} fill="none" stroke="var(--series-1)" strokeWidth={1.4} />
        <polyline points={path(v.A, y1)} fill="none" stroke="var(--accent)" strokeWidth={2} />
        <g transform={`translate(0 ${H1 + 8})`}>
          <line x1={0} x2={W} y1={H2} y2={H2} stroke="var(--border)" />
          <polyline points={step} fill="none" stroke="var(--foreground)" strokeWidth={1.4} />
          <text x={W - 2} y={H2 - 3} fontSize={9} fill="var(--faint)" textAnchor="end">
            기준금리 (아래 {rlo.toFixed(0)}% · 위 {rhi.toFixed(1)}%)
          </text>
        </g>
        <line x1={x(p)} x2={x(p)} y1={0} y2={H1 + H2 + 8} stroke="var(--faint)" strokeWidth={0.8} />
      </svg>

      <div className="tabular flex flex-wrap justify-between gap-x-3 rounded-lg bg-background px-3 py-2 text-xs">
        <b>{v.dates[p]}</b>
        <span style={{ color: "var(--accent)" }}>아파트 {v.A[p] != null ? pctS(v.A[p]! - 100) : "–"}</span>
        <span style={{ color: "var(--series-1)" }}>코스피 {v.K[p] != null ? pctS(v.K[p]! - 100) : "–"}</span>
        <span>금리 {v.rate[p]?.toFixed(2) ?? "–"}%</span>
      </div>

      <ul className="space-y-0.5 text-[11px] text-faint">
        <li>· 굵은 주황 = 서울 아파트 매매가격지수, 보라 = 코스피 (둘 다 보는 기간 첫 달 = 100) · 아래 계단선 = 기준금리 · 위 그림은 로그 눈금(같은 % 변화 = 같은 높이)</li>
        <li>· 배경 빨강 = 금리 올리는 시기, 파랑 = 내리는 시기 · 그림을 누르거나 밀면 그달 값</li>
        <li>· 물가를 빼지 않은 숫자 · 출처 한국은행 ECOS{d.source ? ` (${d.source})` : ""} · 자세한 분석은 연구 탭 &lsquo;서울 아파트값 · 코스피 · 기준금리&rsquo;</li>
      </ul>
    </div>
  );
}
