"use client";

import { useEffect, useRef, useState } from "react";
import { CandlestickSeries, createChart, type IChartApi, LineSeries, type Time } from "lightweight-charts";
import { api } from "@/lib/api";
import { pct, retClass } from "@/lib/format";
import { sma } from "@/lib/indicators";

interface Bar {
  t: string;
  o: number;
  h: number;
  l: number;
  c: number;
}
const RANGES: [string, number][] = [
  ["3개월", 63],
  ["6개월", 126],
  ["1년", 250],
  ["3년", 750],
  ["전체", 0],
];
const MAS: [number, string][] = [
  [20, "#7ea7dd"],
  [60, "#7fc29b"],
  [120, "#a68fdc"],
];

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
const num = (x: number | null | undefined) => (x == null ? "–" : x.toLocaleString("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

// 시장 탭: 고른 지수 하나의 일봉 (요약 상자를 누르면 바뀐다)
export default function IndexChart({ ixKey, name, color }: { ixKey: string; name: string; color: string }) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [data, setData] = useState<{ key: string; bars: Bar[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [span, setSpan] = useState(250);
  const [hover, setHover] = useState<Bar | null>(null);

  useEffect(() => {
    let stop = false;
    api<{ bars: Bar[] }>(`/api/indices/bars?key=${ixKey}`)
      .then((r) => {
        if (stop) return;
        setData({ key: ixKey, bars: r.bars });
        setErr(null);
      })
      .catch((e) => !stop && setErr((e as Error).message));
    return () => {
      stop = true;
    };
  }, [ixKey]);

  const bars = data?.key === ixKey ? data.bars : null;

  useEffect(() => {
    if (!bars || !bars.length || !box.current) return;
    const up = cssVar("--up");
    const down = cssVar("--down");
    const grid = cssVar("--border");
    const chart = createChart(box.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: cssVar("--muted"), fontFamily: "'IBM Plex Sans KR', sans-serif" },
      grid: { vertLines: { visible: false }, horzLines: { color: grid } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      localization: { locale: "ko-KR" },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      borderUpColor: up,
      borderDownColor: down,
      wickUpColor: up,
      wickDownColor: down,
      priceLineVisible: false,
      priceFormat: { type: "custom", formatter: (p: number) => num(p), minMove: 0.01 },
    });
    candles.setData(bars.map((b) => ({ time: b.t as Time, open: b.o, high: b.h, low: b.l, close: b.c })));
    const closes = bars.map((b) => b.c);
    for (const [n, c] of MAS) {
      const v = sma(closes, n);
      const s = chart.addSeries(LineSeries, {
        color: c,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      s.setData(bars.map((b, i) => (v[i] == null ? { time: b.t as Time } : { time: b.t as Time, value: v[i] as number })));
    }
    const byT = new Map(bars.map((b) => [b.t, b]));
    chart.subscribeCrosshairMove((p) => setHover(p.time ? (byT.get(String(p.time)) ?? null) : null));
    chartRef.current = chart;
    return () => {
      chartRef.current = null;
      chart.remove();
    };
  }, [bars]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !bars) return;
    const right = bars.length - 1;
    if (span === 0) chart.timeScale().fitContent();
    else chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, right - span), to: right + 1 });
  }, [span, bars]);

  const last = bars?.[bars.length - 1];
  const shown = hover ?? last;
  const i = shown && bars ? bars.indexOf(shown) : -1;
  const prev = i > 0 && bars ? bars[i - 1].c : null;
  const chg = shown && prev ? (shown.c / prev - 1) * 100 : null;

  return (
    <section className="space-y-2 rounded-xl border border-border bg-card p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="flex items-center gap-1.5 font-semibold">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color }} />
          {name} 일봉
        </h3>
        {shown && (
          <span className="tabular text-xs text-muted">
            {shown.t} · 시 {num(shown.o)} · 고 {num(shown.h)} · 저 {num(shown.l)} · 종{" "}
            <span className="text-foreground">{num(shown.c)}</span> <span className={retClass(chg)}>{pct(chg, 2, true)}</span>
          </span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1 text-xs">
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
        <span className="ml-auto flex gap-2 text-muted">
          {MAS.map(([n, c]) => (
            <span key={n} className="flex items-center gap-1">
              <span className="inline-block h-0.5 w-3 rounded" style={{ background: c }} />
              {n}일
            </span>
          ))}
        </span>
      </div>
      {err && <p className="text-sm text-critical">⚠ {err}</p>}
      {!bars && !err && <p className="text-sm text-muted">불러오는 중…</p>}
      <div ref={box} className="h-72 w-full fold:h-80" />
      <p className="text-[11px] text-faint">종가는 연도별 분석과 같은 값, 시가·고가·저가는 야후 일봉. 오늘 봉은 장 마감 뒤 저녁에 더해집니다.</p>
    </section>
  );
}
