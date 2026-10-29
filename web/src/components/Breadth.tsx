"use client";

import { useEffect, useRef, useState } from "react";
import { createChart, HistogramSeries, LineSeries, LineStyle, type Time } from "lightweight-charts";
import { api } from "@/lib/api";
import { pct, retClass } from "@/lib/format";

interface Data {
  market: string;
  index_name: string | null;
  dates: string[];
  adv: (number | null)[];
  up: number[];
  n: number[];
  ew: number[];
  index: (number | null)[] | null;
}
const MARKETS: [string, string][] = [
  ["KOSPI", "코스피"],
  ["KOSDAQ", "코스닥"],
];
const RANGES: [string, number][] = [
  ["3개월", 63],
  ["1년", 250],
  ["3년", 750],
  ["전체", 0],
];
const EW = "#3CC4B4";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function avg(a: (number | null)[], i: number, n: number): number | null {
  let s = 0;
  let k = 0;
  for (let j = Math.max(0, i - n + 1); j <= i; j++) {
    if (a[j] == null) continue;
    s += a[j] as number;
    k++;
  }
  return k === n ? s / k : null;
}

// 시장 탭: 지수(시총 가중) vs 동일가중 지수, 상승비율 — 대형주만 오르는 장인지 본다
export default function Breadth() {
  const [market, setMarket] = useState("KOSPI");
  const [span, setSpan] = useState(250);
  const [data, setData] = useState<Record<string, Data>>({});
  const [err, setErr] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (data[market]) return;
    api<Data>(`/api/breadth?market=${market}`)
      .then((r) => {
        setData((d) => ({ ...d, [market]: r }));
        setErr(null);
      })
      .catch((e) => setErr((e as Error).message));
  }, [market, data]);

  const d = data[market];
  const N = d?.dates.length ?? 0;
  const from = d ? (span ? Math.max(1, N - span) : 1) : 0;
  // 기간 첫날을 100으로 맞춘 두 지수
  const ix0 = d?.index ? d.index.slice(from).find((v) => v != null) : null;
  const ew0 = d ? d.ew[from] : null;
  const lastIx = d?.index ? [...d.index].reverse().find((v) => v != null) : null;
  const ixRet = ix0 && lastIx ? (lastIx / ix0 - 1) * 100 : null;
  const ewRet = d && ew0 ? (d.ew[N - 1] / ew0 - 1) * 100 : null;

  useEffect(() => {
    if (!d || !box.current || N < 2) return;
    const chart = createChart(box.current, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: cssVar("--muted"),
        fontFamily: "'IBM Plex Sans KR', sans-serif",
        panes: { separatorColor: cssVar("--border") },
      },
      grid: { vertLines: { visible: false }, horzLines: { color: cssVar("--border") } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      localization: { locale: "ko-KR" },
      handleScroll: false,
      handleScale: false,
    });
    const t = (i: number) => d.dates[i] as Time;
    const idx = [...Array(N - from).keys()].map((k) => k + from);
    const fmt = { type: "custom" as const, formatter: (p: number) => p.toFixed(0), minMove: 0.1 };
    if (d.index && ix0) {
      const s = chart.addSeries(LineSeries, { color: cssVar("--series-1"), lineWidth: 2, priceLineVisible: false, priceFormat: fmt });
      s.setData(idx.map((i) => (d.index![i] == null ? { time: t(i) } : { time: t(i), value: ((d.index![i] as number) / ix0) * 100 })));
    }
    const e = chart.addSeries(LineSeries, { color: EW, lineWidth: 2, priceLineVisible: false, priceFormat: fmt });
    e.setData(idx.map((i) => ({ time: t(i), value: (d.ew[i] / (ew0 as number)) * 100 })));
    // 아래 칸: 상승비율 (막대: 그날, 선: 20일 평균)
    const h = chart.addSeries(
      HistogramSeries,
      { priceLineVisible: false, lastValueVisible: false, priceFormat: { type: "custom", formatter: (p: number) => `${p.toFixed(0)}%`, minMove: 1 } },
      1,
    );
    const up = cssVar("--up");
    const down = cssVar("--down");
    h.setData(idx.map((i) => (d.adv[i] == null ? { time: t(i) } : { time: t(i), value: d.adv[i] as number, color: ((d.adv[i] as number) >= 50 ? up : down) + "55" })));
    const a20 = chart.addSeries(LineSeries, { color: cssVar("--foreground"), lineWidth: 1, priceLineVisible: false, lastValueVisible: false }, 1);
    a20.setData(idx.map((i) => {
      const v = avg(d.adv, i, 20);
      return v == null ? { time: t(i) } : { time: t(i), value: v };
    }));
    a20.createPriceLine({ price: 50, color: cssVar("--muted"), lineStyle: LineStyle.Dotted, lineWidth: 1, axisLabelVisible: false });
    chart.panes()[1]?.setHeight(90);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [d, N, from, ix0, ew0]);

  const last = N - 1;
  const today = d?.adv[last];
  const a20 = d ? avg(d.adv, last, 20) : null;

  return (
    <section className="space-y-3 rounded-xl border border-border bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">시장 폭 · 지수 vs 종목 전체</h3>
        <div className="flex rounded-lg border border-border p-0.5 text-xs">
          {MARKETS.map(([k, l]) => (
            <button
              key={k}
              type="button"
              onClick={() => setMarket(k)}
              className={`rounded-md px-2.5 py-1 ${market === k ? "bg-accent text-on-accent" : "text-muted"}`}
            >
              {l}
            </button>
          ))}
        </div>
      </div>
      {err && <p className="text-sm text-critical">⚠ {err}</p>}
      {!d && !err && <p className="text-sm text-muted">불러오는 중…</p>}
      {d && (
        <>
          <div className="grid grid-cols-2 gap-2 fold:grid-cols-4">
            <div className="rounded-lg bg-background p-2">
              <div className="text-[11px] text-muted">오른 종목 ({d.dates[last].slice(5).replace("-", "/")})</div>
              <div className={`tabular text-lg font-semibold ${retClass(today == null ? null : today - 50)}`}>{today == null ? "–" : `${today.toFixed(0)}%`}</div>
              <div className="tabular text-[11px] text-faint">
                {d.up[last].toLocaleString("ko-KR")} / {d.n[last].toLocaleString("ko-KR")}종목
              </div>
            </div>
            <div className="rounded-lg bg-background p-2">
              <div className="text-[11px] text-muted">상승비율 20일 평균</div>
              <div className={`tabular text-lg font-semibold ${retClass(a20 == null ? null : a20 - 50)}`}>{a20 == null ? "–" : `${a20.toFixed(0)}%`}</div>
              <div className="text-[11px] text-faint">50% 위면 오르는 종목이 더 많음</div>
            </div>
            <div className="rounded-lg bg-background p-2">
              <div className="flex items-center gap-1 text-[11px] text-muted">
                <span className="inline-block h-0.5 w-3 rounded" style={{ background: "var(--series-1)" }} />
                {d.index_name ?? "지수"} ({RANGES.find(([, n]) => n === span)?.[0]})
              </div>
              <div className={`tabular text-lg font-semibold ${retClass(ixRet)}`}>{pct(ixRet, 1, true)}</div>
            </div>
            <div className="rounded-lg bg-background p-2">
              <div className="flex items-center gap-1 text-[11px] text-muted">
                <span className="inline-block h-0.5 w-3 rounded" style={{ background: EW }} />
                동일가중 ({RANGES.find(([, n]) => n === span)?.[0]})
              </div>
              <div className={`tabular text-lg font-semibold ${retClass(ewRet)}`}>{pct(ewRet, 1, true)}</div>
            </div>
          </div>
          <div className="flex gap-1 text-xs">
            {RANGES.map(([l, n]) => (
              <button
                key={l}
                type="button"
                onClick={() => setSpan(n)}
                className={`rounded-md border px-2.5 py-1 ${span === n ? "border-accent bg-accent text-on-accent" : "border-border text-muted"}`}
              >
                {l}
              </button>
            ))}
          </div>
          <div ref={box} className="h-80 w-full fold:h-96" />
          <p className="text-[11px] text-faint">
            위: 기간 첫날을 100으로 맞춘 {d.index_name ?? "지수"}(시총 가중)와 동일가중 지수. 두 선이 벌어지면 대형주와 나머지 종목이 따로 움직이는
            장입니다. 아래: 그날 오른 종목 비율(막대)과 20일 평균(선). 조건식에서 <b>상승비율</b>, <b>동일가중</b>으로 쓸 수 있어요 (예: 상승비율 &lt;= 20).
          </p>
        </>
      )}
    </section>
  );
}
