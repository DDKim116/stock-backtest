"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { pct, retClass } from "@/lib/format";

interface MonthRet {
  m: number;
  ret: number | null;
  close: number | null;
  partial: boolean;
}
interface YearRet {
  year: number;
  ret: number | null;
  base: number | null;
  close: number | null;
  high: number | null;
  low: number | null;
  mdd: number | null;
  days: number;
  partial: boolean;
  months: (MonthRet | null)[];
}
interface IndexData {
  key: string;
  name: string;
  available: boolean;
  first?: string | null;
  last?: string;
  last_close?: number;
  years?: YearRet[];
  stats?: {
    n: number;
    from: number;
    to: number;
    avg: number;
    cagr: number;
    up: number;
    best: number;
    worst: number;
  } | null;
}
interface Summary {
  group: string;
  groups: { key: string; name: string }[];
  indices: IndexData[];
}

const GROUPS = [
  { key: "kr", name: "한국" },
  { key: "us", name: "미국" },
];
// 지수 색 (보라·주황: 상승 빨강/하락 파랑과 겹치지 않게). 첫째 지수 = 1번, 둘째 = 2번
const SERIES = ["var(--series-1)", "var(--series-2)"];
const MONTHS = ["1월", "2월", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"];

function num(x: number | null | undefined, d = 2): string {
  if (x == null) return "–";
  return x.toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d });
}

function Swatch({ i }: { i: number }) {
  return <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: SERIES[i] }} />;
}

// 히트맵 칸 색: 오르면 빨강, 내리면 파랑. 10% 이상이면 가장 진하게
function heat(v: number | null | undefined): string | undefined {
  if (v == null) return undefined;
  const a = Math.min(1, Math.abs(v) / 10) * 55 + 5;
  return `color-mix(in oklab, var(${v >= 0 ? "--up" : "--down"}) ${a.toFixed(0)}%, var(--card))`;
}

export default function Market() {
  const [group, setGroup] = useState("kr");
  const [data, setData] = useState<Record<string, Summary>>({});
  const [err, setErr] = useState<string | null>(null);
  const [only, setOnly] = useState<string | null>(null); // null = 나란히
  const [year, setYear] = useState<number | null>(null);
  const [heatKey, setHeatKey] = useState<string | null>(null);

  useEffect(() => {
    if (data[group]) return;
    api<Summary>(`/api/indices?group=${group}`)
      .then((r) => {
        setData((d) => ({ ...d, [group]: r }));
        setErr(null);
      })
      .catch((e) => setErr((e as Error).message));
  }, [group, data]);

  const sum = data[group];
  const all = sum?.indices ?? [];
  const avail = all.filter((x) => x.available);
  // 색은 지수마다 고정 (보이는 지수 수가 바뀌어도 색이 바뀌지 않게)
  const colorOf = (key: string) => Math.max(0, all.findIndex((x) => x.key === key));
  const shown = avail.filter((x) => !only || x.key === only);

  // 연도 → 지수별 결과
  const yearSet = new Set<number>();
  for (const x of shown) for (const y of x.years ?? []) yearSet.add(y.year);
  const years = [...yearSet].sort((a, b) => b - a);
  const byYear = (x: IndexData, y: number) => x.years?.find((v) => v.year === y) ?? null;
  let maxAbs = 5;
  for (const x of shown) for (const y of x.years ?? []) if (y.ret != null) maxAbs = Math.max(maxAbs, Math.abs(y.ret));

  const heatIdx = avail.find((x) => x.key === heatKey) ?? avail[0];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-bold">지수 흐름</h2>
        <div className="flex rounded-lg border border-border p-0.5 text-sm">
          {GROUPS.map((g) => (
            <button
              key={g.key}
              type="button"
              onClick={() => {
                setGroup(g.key);
                setOnly(null);
                setYear(null);
                setHeatKey(null);
              }}
              className={`rounded-md px-3 py-1 ${group === g.key ? "bg-accent text-white" : "text-muted"}`}
            >
              {g.name}
            </button>
          ))}
        </div>
      </div>

      {err && <p className="text-sm text-critical">⚠ {err}</p>}
      {!sum && !err && <p className="text-sm text-muted">불러오는 중…</p>}

      {sum && avail.length === 0 && (
        <div className="rounded-xl border border-border bg-card p-4 text-sm text-muted">
          {group === "kr" ? (
            <>
              코스피·코스닥 지수 데이터가 아직 없습니다. KRX OpenAPI에서 <b>KOSPI 시리즈 일별시세정보</b>와{" "}
              <b>KOSDAQ 시리즈 일별시세정보</b> 이용 신청이 승인되면 서버에서{" "}
              <code>python -m sbt.data.cli index-update</code> 를 한 번 실행하세요. 이후에는 매일 자동으로 갱신됩니다.
            </>
          ) : (
            <>
              미국 지수 데이터가 아직 없습니다. 서버에서 <code>python -m sbt.data.cli index-update</code> 를 한 번 실행하세요.
            </>
          )}
        </div>
      )}

      {avail.length > 0 && (
        <>
          {/* 나란히 / 하나만 */}
          {avail.length > 1 && (
            <div className="flex gap-1 text-xs">
              {[{ key: null as string | null, name: "나란히" }, ...avail.map((x) => ({ key: x.key, name: x.name }))].map((o) => (
                <button
                  key={o.key ?? "all"}
                  type="button"
                  onClick={() => setOnly(o.key)}
                  className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 ${only === o.key ? "border-accent text-accent" : "border-border text-muted"}`}
                >
                  {o.key && <Swatch i={colorOf(o.key)} />}
                  {o.name}
                </button>
              ))}
            </div>
          )}

          {/* 요약 */}
          <div className={`grid gap-2 ${shown.length > 1 ? "grid-cols-2" : "grid-cols-1"}`}>
            {shown.map((x) => (
              <div key={x.key} className="tabular space-y-1 rounded-xl border border-border bg-card p-3 text-xs">
                <div className="flex items-center gap-1.5 text-sm font-semibold">
                  <Swatch i={colorOf(x.key)} />
                  {x.name}
                </div>
                <div className="text-muted">
                  {x.last} · <span className="text-foreground">{num(x.last_close)}</span>
                </div>
                {x.stats && (
                  <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
                    <dt className="text-muted">연평균</dt>
                    <dd className={`text-right ${retClass(x.stats.avg)}`}>{pct(x.stats.avg, 1, true)}</dd>
                    <dt className="text-muted">연복리</dt>
                    <dd className={`text-right ${retClass(x.stats.cagr)}`}>{pct(x.stats.cagr, 1, true)}</dd>
                    <dt className="text-muted">상승한 해</dt>
                    <dd className="text-right">
                      {x.stats.up}/{x.stats.n}
                    </dd>
                    <dt className="text-muted">최고</dt>
                    <dd className="text-right">
                      {x.stats.best}{" "}
                      <span className={retClass(1)}>{pct(byYear(x, x.stats.best)?.ret, 1, true)}</span>
                    </dd>
                    <dt className="text-muted">최저</dt>
                    <dd className="text-right">
                      {x.stats.worst}{" "}
                      <span className={retClass(-1)}>{pct(byYear(x, x.stats.worst)?.ret, 1, true)}</span>
                    </dd>
                  </dl>
                )}
                {x.stats && (
                  <div className="text-[11px] text-faint">
                    {x.stats.from}~{x.stats.to}년 기준 (올해 제외)
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* 연도별 */}
          <section className="rounded-xl border border-border bg-card p-3">
            <div className="mb-2 flex items-baseline justify-between">
              <h3 className="font-semibold">연도별 등락률</h3>
              <span className="text-xs text-muted">연도를 누르면 월별</span>
            </div>
            <ul className="space-y-0.5">
              {years.map((y) => {
                const open = year === y;
                return (
                  <li key={y} id={`year-${y}`} className="scroll-mt-4">
                    <button
                      type="button"
                      onClick={() => setYear(open ? null : y)}
                      aria-expanded={open}
                      className={`tabular flex w-full items-center gap-2 rounded-lg px-1 py-1 text-left text-xs ${open ? "bg-accent-soft" : ""}`}
                    >
                      <span className="w-10 shrink-0 font-medium">
                        {y}
                        {shown.some((x) => byYear(x, y)?.partial) && <span className="block text-[10px] text-faint">진행 중</span>}
                      </span>
                      <span className="relative flex-1 space-y-0.5 py-0.5">
                        <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
                        {shown.map((x) => {
                          const r = byYear(x, y)?.ret;
                          const w = r == null ? 0 : (Math.abs(r) / maxAbs) * 50;
                          return (
                            <span key={x.key} className="relative block h-2">
                              {r != null && (
                                <span
                                  className={`absolute top-0 h-2 ${r >= 0 ? "rounded-r" : "rounded-l"}`}
                                  style={{
                                    background: SERIES[colorOf(x.key)],
                                    width: `${Math.max(w, 0.5)}%`,
                                    left: r >= 0 ? "50%" : `${50 - w}%`,
                                  }}
                                />
                              )}
                            </span>
                          );
                        })}
                      </span>
                      <span className="w-14 shrink-0 text-right">
                        {shown.map((x) => {
                          const r = byYear(x, y)?.ret;
                          return (
                            <span key={x.key} className={`block leading-tight ${retClass(r)}`}>
                              {r == null ? "–" : pct(r, 1, true)}
                            </span>
                          );
                        })}
                      </span>
                    </button>
                    {open && <YearDetail y={y} items={shown} colorOf={colorOf} />}
                  </li>
                );
              })}
            </ul>
            <p className="mt-2 text-[11px] text-faint">전년 마지막 거래일 종가 대비 그해 마지막 거래일 종가.</p>
          </section>

          {/* 월별 계절성 */}
          {heatIdx && (
            <section className="rounded-xl border border-border bg-card p-3">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <h3 className="font-semibold">월별 등락 한눈에</h3>
                {avail.length > 1 && (
                  <div className="flex gap-1 text-xs">
                    {avail.map((x) => (
                      <button
                        key={x.key}
                        type="button"
                        onClick={() => setHeatKey(x.key)}
                        className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 ${heatIdx.key === x.key ? "border-accent text-accent" : "border-border text-muted"}`}
                      >
                        <Swatch i={colorOf(x.key)} />
                        {x.name}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <Heatmap
                idx={heatIdx}
                onYear={(y) => {
                  setYear(y);
                  setTimeout(() => document.getElementById(`year-${y}`)?.scrollIntoView({ behavior: "smooth" }), 50);
                }}
              />
            </section>
          )}
        </>
      )}
    </div>
  );
}

function YearDetail({ y, items, colorOf }: { y: number; items: IndexData[]; colorOf: (k: string) => number }) {
  const rows = items.map((x) => ({ x, yr: x.years?.find((v) => v.year === y) ?? null }));
  const [tip, setTip] = useState<number | null>(null);
  let maxAbs = 3;
  for (const { yr } of rows) for (const m of yr?.months ?? []) if (m?.ret != null) maxAbs = Math.max(maxAbs, Math.abs(m.ret));
  const W = 360;
  const H = 150;
  const top = 8;
  const bottom = 18;
  const mid = top + (H - top - bottom) / 2;
  const half = (H - top - bottom) / 2;
  const slot = W / 12;
  const n = rows.length;
  const bw = Math.min(12, (slot - 6 - (n - 1) * 2) / n);

  return (
    <div className="mb-2 mt-1 space-y-2 rounded-lg border border-border bg-background p-2">
      <div className="text-xs font-semibold">{y}년 월별 등락률</div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={`${y}년 월별 등락률 막대 차트`}>
        <line x1={0} x2={W} y1={mid} y2={mid} stroke="var(--border)" />
        {MONTHS.map((label, mi) => {
          const x0 = mi * slot + (slot - (n * bw + (n - 1) * 2)) / 2;
          return (
            <g key={mi} onClick={() => setTip(tip === mi ? null : mi)} className="cursor-pointer">
              <rect x={mi * slot} y={0} width={slot} height={H} fill={tip === mi ? "var(--accent-soft)" : "transparent"} />
              {rows.map(({ x, yr }, si) => {
                const r = yr?.months[mi]?.ret;
                if (r == null) return null;
                const h = Math.max(1, (Math.abs(r) / maxAbs) * half);
                return (
                  <rect
                    key={x.key}
                    x={x0 + si * (bw + 2)}
                    y={r >= 0 ? mid - h : mid}
                    width={bw}
                    height={h}
                    rx={2}
                    fill={SERIES[colorOf(x.key)]}
                  />
                );
              })}
              <text x={mi * slot + slot / 2} y={H - 4} textAnchor="middle" fontSize={9} fill="var(--muted)">
                {mi + 1}
              </text>
            </g>
          );
        })}
      </svg>
      {tip != null && (
        <div className="tabular flex flex-wrap gap-x-3 text-xs">
          <span className="font-semibold">{MONTHS[tip]}</span>
          {rows.map(({ x, yr }) => (
            <span key={x.key} className="inline-flex items-center gap-1">
              <Swatch i={colorOf(x.key)} />
              {x.name} <span className={retClass(yr?.months[tip]?.ret)}>{pct(yr?.months[tip]?.ret, 2, true)}</span>
            </span>
          ))}
        </div>
      )}
      {/* 숫자 표 (휴대폰 폭에 맞게 지수 이름 대신 색 표시) */}
      <table className="tabular w-full table-fixed text-right text-[10px]">
        <thead>
          <tr className="text-muted">
            <th className="w-3" />
            {MONTHS.map((m, i) => (
              <th key={m} className="font-normal">
                {i + 1}월
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ x, yr }) => (
            <tr key={x.key}>
              <td className="py-0.5 text-left">
                <Swatch i={colorOf(x.key)} />
              </td>
              {MONTHS.map((_, i) => {
                const m = yr?.months[i];
                return (
                  <td key={i} className={`py-0.5 ${retClass(m?.ret)}`}>
                    {m?.ret == null ? "" : m.ret.toFixed(1)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <dl className="tabular grid gap-1 text-[11px] text-muted">
        {rows.map(({ x, yr }) =>
          yr ? (
            <div key={x.key} className="flex flex-wrap items-center gap-x-3">
              <span className="inline-flex items-center gap-1 text-foreground">
                <Swatch i={colorOf(x.key)} />
                {x.name}
              </span>
              <span>
                {num(yr.base)} → {num(yr.close)}
              </span>
              <span>
                최고 {num(yr.high)} · 최저 {num(yr.low)}
              </span>
              <span>최대 낙폭 {pct(yr.mdd, 1, true)}</span>
            </div>
          ) : null,
        )}
      </dl>
    </div>
  );
}

function Heatmap({ idx, onYear }: { idx: IndexData; onYear: (y: number) => void }) {
  const years = [...(idx.years ?? [])].sort((a, b) => b.year - a.year);
  // 달마다 평균·상승 확률 (진행 중인 달 제외)
  const stat = MONTHS.map((_, i) => {
    const v = years.map((y) => y.months[i]).filter((m): m is MonthRet => !!m && m.ret != null && !m.partial);
    const rs = v.map((m) => m.ret as number);
    return {
      avg: rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : null,
      up: rs.filter((r) => r > 0).length,
      n: rs.length,
    };
  });
  return (
    <>
      <div className="max-h-[28rem] overflow-auto">
        <table className="tabular w-full border-separate border-spacing-0.5 text-center text-[11px]">
          <thead className="sticky top-0 bg-card">
            <tr className="text-muted">
              <th className="sticky left-0 bg-card px-1 text-left font-normal">월</th>
              {MONTHS.map((m, i) => (
                <th key={m} className="px-0.5 font-normal">
                  {i + 1}
                </th>
              ))}
              <th className="whitespace-nowrap px-1 font-normal">연간</th>
            </tr>
          </thead>
          <tbody>
            <tr className="font-semibold">
              <td className="sticky left-0 bg-card px-1 text-left">평균</td>
              {stat.map((s, i) => (
                <td key={i} className="rounded px-0.5 py-1" style={{ background: heat(s.avg) }}>
                  {s.avg == null ? "" : s.avg.toFixed(1)}
                </td>
              ))}
              <td />
            </tr>
            <tr className="text-muted">
              <td className="sticky left-0 bg-card px-1 text-left">상승</td>
              {stat.map((s, i) => (
                <td key={i} className="px-0.5 py-1">
                  {s.n ? `${Math.round((s.up / s.n) * 100)}%` : ""}
                </td>
              ))}
              <td />
            </tr>
            {years.map((y) => (
              <tr key={y.year}>
                <td className="sticky left-0 bg-card px-1 text-left">
                  <button type="button" onClick={() => onYear(y.year)} className="underline decoration-dotted">
                    {y.year}
                  </button>
                </td>
                {y.months.map((m, i) => (
                  <td key={i} className="rounded px-0.5 py-1" style={{ background: heat(m?.ret) }}>
                    {m?.ret == null ? "" : m.ret.toFixed(1)}
                  </td>
                ))}
                <td className="rounded px-1 py-1 font-semibold" style={{ background: heat(y.ret) }}>
                  {y.ret == null ? "" : y.ret.toFixed(1)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-faint">
        숫자는 등락률(%). 빨강은 상승, 파랑은 하락이며 진할수록 크게 움직인 달입니다. &apos;상승&apos;은 그 달이 오른 해의 비율이고, 평균·상승에는 진행 중인 달을 넣지 않았습니다. 연도를 누르면 위 연도별 목록에서 그해가 열립니다.
      </p>
    </>
  );
}
