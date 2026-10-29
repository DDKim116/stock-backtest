"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { createChart, LineSeries, type Time } from "lightweight-charts";
import { api } from "@/lib/api";

interface Row {
  date: string;
  total: number;
  n: number;
  top1: number;
  top5: number;
  top10: number;
  top30: number;
}
interface Year extends Row {
  year: number;
  partial: boolean;
  top: { code: string; name: string; cap: number; w: number }[];
}
interface Data {
  market: string;
  available: boolean;
  as_of?: string;
  months?: Row[];
  years?: Year[];
}
// 상위 1·5·10·30: 진할수록 적은 종목
const LINES: [keyof Row, string, string][] = [
  ["top1", "1위", "#F08A5D"],
  ["top5", "상위 5", "#E3B341"],
  ["top10", "상위 10", "#3CC4B4"],
  ["top30", "상위 30", "#7EC8F2"],
];
const MARKETS: [string, string][] = [
  ["KOSPI", "코스피"],
  ["KOSDAQ", "코스닥"],
];
const jo = (won: number) => `${(won / 1e12).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}조`;

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// 시장 탭: 시총 상위 종목이 시장 전체에서 차지하는 비율의 흐름
export default function Concentration() {
  const [market, setMarket] = useState("KOSPI");
  const [data, setData] = useState<Record<string, Data>>({});
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null); // 상위 10 펼친 해
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (data[market]) return;
    api<Data>(`/api/concentration?market=${market}`)
      .then((r) => {
        setData((d) => ({ ...d, [market]: r }));
        setErr(null);
      })
      .catch((e) => setErr((e as Error).message));
  }, [market, data]);

  const d = data[market];
  const months = d?.months;

  useEffect(() => {
    if (!months || !months.length || !box.current) return;
    const chart = createChart(box.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: cssVar("--muted"), fontFamily: "'IBM Plex Sans KR', sans-serif" },
      grid: { vertLines: { visible: false }, horzLines: { color: cssVar("--border") } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      localization: { locale: "ko-KR" },
      handleScroll: false,
      handleScale: false,
    });
    for (const [k, , color] of LINES) {
      const s = chart.addSeries(LineSeries, {
        color,
        lineWidth: 2,
        priceLineVisible: false,
        priceFormat: { type: "custom", formatter: (p: number) => `${p.toFixed(0)}%`, minMove: 0.1 },
      });
      s.setData(months.map((m) => ({ time: m.date as Time, value: m[k] as number })));
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [months]);

  const latest = months?.[months.length - 1];
  const first = months?.[0];

  return (
    <section className="space-y-3 rounded-xl border border-border bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">시총 상위 종목 비중</h3>
        <div className="flex rounded-lg border border-border p-0.5 text-xs">
          {MARKETS.map(([k, l]) => (
            <button
              key={k}
              type="button"
              onClick={() => {
                setMarket(k);
                setOpen(null);
              }}
              className={`rounded-md px-2.5 py-1 ${market === k ? "bg-accent text-on-accent" : "text-muted"}`}
            >
              {l}
            </button>
          ))}
        </div>
      </div>
      {err && <p className="text-sm text-critical">⚠ {err}</p>}
      {!d && !err && <p className="text-sm text-muted">계산하는 중… (처음엔 몇 초 걸립니다)</p>}
      {d && !d.available && <p className="text-sm text-muted">시가총액 데이터가 없습니다.</p>}

      {latest && first && (
        <>
          <div className="grid grid-cols-4 gap-2">
            {LINES.map(([k, l, c]) => (
              <div key={k} className="rounded-lg bg-background p-2">
                <div className="flex items-center gap-1 text-[11px] text-muted">
                  <span className="inline-block h-0.5 w-3 rounded" style={{ background: c }} />
                  {l}
                </div>
                <div className="tabular text-lg font-semibold">{(latest[k] as number).toFixed(1)}%</div>
                <div className="tabular text-[11px] text-faint">
                  {first.date.slice(0, 4)}년 {(first[k] as number).toFixed(1)}%
                </div>
              </div>
            ))}
          </div>
          <div ref={box} className="h-56 w-full fold:h-64" />
          <p className="text-[11px] text-faint">
            {d.as_of} 기준 · 전체 {jo(latest.total)} · {latest.n.toLocaleString("ko-KR")}개사 · 매월 마지막 거래일 값
          </p>

          <div className="overflow-x-auto">
            <table className="tabular w-full text-right text-xs">
              <thead className="text-muted">
                <tr>
                  <th className="py-1 text-left font-normal">연말</th>
                  <th className="font-normal">1위</th>
                  <th className="font-normal">상위 5</th>
                  <th className="font-normal">상위 10</th>
                  <th className="font-normal">상위 30</th>
                  <th className="hidden font-normal fold:table-cell">전체 시총</th>
                  <th className="py-1 pl-2 text-left font-normal">1위 종목</th>
                </tr>
              </thead>
              <tbody>
                {d.years?.map((y) => (
                  <Fragment key={y.year}>
                    <tr onClick={() => setOpen(open === y.year ? null : y.year)} className="cursor-pointer border-t border-border">
                      <td className="py-1.5 text-left">
                        {y.year}
                        {y.partial && <span className="text-faint"> 진행 중</span>}
                      </td>
                      <td>{y.top1.toFixed(1)}</td>
                      <td>{y.top5.toFixed(1)}</td>
                      <td>{y.top10.toFixed(1)}</td>
                      <td>{y.top30.toFixed(1)}</td>
                      <td className="hidden text-muted fold:table-cell">{jo(y.total)}</td>
                      <td className="max-w-28 truncate py-1.5 pl-2 text-left">
                        {y.top[0]?.name} <span className="text-accent">{open === y.year ? "▲" : "▼"}</span>
                      </td>
                    </tr>
                    {open === y.year && (
                      <tr>
                        <td colSpan={7} className="pb-2">
                          <ol className="space-y-1 rounded-lg bg-background p-2 text-left">
                            {y.top.map((t, i) => (
                              <li key={t.code} className="grid grid-cols-[1.5rem_minmax(0,1fr)_3.5rem_4.5rem] items-center gap-2">
                                <span className="text-faint">{i + 1}</span>
                                <span className="relative truncate">
                                  <span
                                    className="absolute inset-y-0 left-0 rounded-sm bg-accent/15"
                                    style={{ width: `${Math.min(100, (t.w / y.top[0].w) * 100)}%` }}
                                  />
                                  <span className="relative">{t.name}</span>
                                </span>
                                <span className="text-right">{t.w.toFixed(1)}%</span>
                                <span className="text-right text-muted">{jo(t.cap)}</span>
                              </li>
                            ))}
                          </ol>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-faint">
            단위 %. 연도를 누르면 그해 상위 10 종목. 우선주는 보통주와 한 회사로 합쳤고(예: 삼성전자 + 삼성전자우), 상장폐지된 종목도 그 시점 시총에
            들어 있습니다.
          </p>
        </>
      )}
    </section>
  );
}
