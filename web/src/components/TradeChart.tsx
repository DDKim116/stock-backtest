"use client";

import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineStyle,
  type IChartApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { api } from "@/lib/api";
import { pct, price } from "@/lib/format";
import type { Bar, Spec, Trade } from "@/lib/types";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function shiftDate(d: string, days: number): string {
  const t = new Date(d + "T00:00:00Z");
  t.setUTCDate(t.getUTCDate() + days);
  return t.toISOString().slice(0, 10);
}

export default function TradeChart({
  trade,
  spec,
  dataset,
  onClose,
}: {
  trade: Trade;
  spec: Spec;
  dataset: string;
  onClose: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [bars, setBars] = useState<Bar[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [hover, setHover] = useState<Bar | null>(null);

  useEffect(() => {
    const end = trade.exit_date ?? trade.fill_date ?? trade.signal_date;
    const q = new URLSearchParams({
      dataset,
      code: trade.code,
      start: shiftDate(trade.signal_date, -150),
      end: shiftDate(end, 45),
    });
    api<{ bars: Bar[] }>(`/api/chart?${q}`)
      .then((r) => setBars(r.bars))
      .catch((e) => setErr((e as Error).message));
  }, [trade, dataset]);

  useEffect(() => {
    if (!bars || !box.current) return;
    const up = cssVar("--up");
    const down = cssVar("--down");
    const text = cssVar("--muted");
    const grid = cssVar("--border");
    const chart: IChartApi = createChart(box.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: text, fontFamily: "Pretendard, sans-serif" },
      grid: { vertLines: { visible: false }, horzLines: { color: grid } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      localization: { locale: "ko-KR", priceFormatter: (p: number) => price(p) },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      borderUpColor: up,
      borderDownColor: down,
      wickUpColor: up,
      wickDownColor: down,
      priceLineVisible: false,
    });
    candles.setData(bars.map((b) => ({ time: b.t as Time, open: b.o, high: b.h, low: b.l, close: b.c })));
    const vol = chart.addSeries(HistogramSeries, {
      priceScaleId: "vol",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.25 } });
    vol.setData(
      bars.map((b) => ({ time: b.t as Time, value: b.v, color: (b.c >= b.o ? up : down) + "66" })),
    );

    const fg = cssVar("--foreground");
    const markers: SeriesMarker<Time>[] = [
      { time: trade.signal_date as Time, position: "aboveBar", shape: "circle", color: fg, text: "신호" },
    ];
    if (trade.fill_date)
      markers.push({ time: trade.fill_date as Time, position: "belowBar", shape: "arrowUp", color: up, text: "매수" });
    if (trade.exit_date) {
      const label = trade.outcome === "목표도달" ? "목표" : trade.outcome === "손절" ? "손절" : "청산";
      markers.push({ time: trade.exit_date as Time, position: "aboveBar", shape: "arrowDown", color: down, text: label });
    }
    markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
    createSeriesMarkers(candles, markers);

    if (trade.fill_price) {
      candles.createPriceLine({ price: trade.fill_price, color: fg, lineStyle: LineStyle.Dashed, lineWidth: 1, title: "매수가" });
      const t = spec.exit.target_pct;
      if (typeof t === "number")
        candles.createPriceLine({
          price: trade.fill_price * (1 + t / 100),
          color: up,
          lineStyle: LineStyle.Dotted,
          lineWidth: 1,
          title: `목표 +${t}%`,
        });
      const s = spec.exit.stop_pct;
      if (typeof s === "number")
        candles.createPriceLine({
          price: trade.fill_price * (1 - s / 100),
          color: down,
          lineStyle: LineStyle.Dotted,
          lineWidth: 1,
          title: `손절 -${s}%`,
        });
    } else if (trade.limit) {
      candles.createPriceLine({ price: trade.limit, color: fg, lineStyle: LineStyle.Dashed, lineWidth: 1, title: "지정가" });
    }

    // 신호일 앞뒤가 보이도록 범위 설정
    const idx = bars.findIndex((b) => b.t >= trade.signal_date);
    const endIdx = Math.max(idx, bars.findIndex((b) => b.t >= (trade.exit_date ?? trade.signal_date)));
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, idx - 40), to: Math.min(bars.length, endIdx + 15) });

    const byTime = new Map(bars.map((b) => [b.t, b]));
    chart.subscribeCrosshairMove((p) => {
      setHover(p.time ? (byTime.get(String(p.time)) ?? null) : null);
    });
    return () => chart.remove();
  }, [bars, trade, spec]);

  const shown = hover ?? bars?.find((b) => b.t === trade.signal_date) ?? null;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-background" role="dialog" aria-modal="true">
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <div>
          <div className="font-semibold">
            {trade.name} <span className="text-sm font-normal text-muted">{trade.code} · {trade.market}</span>
          </div>
          <div className="text-xs text-muted">
            신호 {trade.signal_date} · {trade.outcome}
            {trade.uncertain && ` (낙관적: ${trade.outcome_opt})`}
          </div>
        </div>
        <button type="button" onClick={onClose} className="rounded-lg border border-border px-3 py-1.5 text-sm">
          닫기
        </button>
      </header>
      <div className="tabular flex flex-wrap gap-x-3 px-4 py-2 text-xs text-muted">
        {shown ? (
          <>
            <span>{shown.t}</span>
            <span>시 {price(shown.o)}</span>
            <span>고 {price(shown.h)}</span>
            <span>저 {price(shown.l)}</span>
            <span>종 {price(shown.c)}</span>
            <span>량 {Math.round(shown.v).toLocaleString("ko-KR")}</span>
          </>
        ) : (
          <span>차트를 눌러 가격 확인</span>
        )}
      </div>
      <div className="relative min-h-0 flex-1">
        {err && <p className="p-4 text-sm text-critical">⚠ {err}</p>}
        {!bars && !err && <p className="p-4 text-sm text-muted">불러오는 중…</p>}
        <div ref={box} className="absolute inset-0" />
      </div>
      <dl className="tabular grid grid-cols-3 gap-px border-t border-border bg-border text-center text-sm">
        {[
          ["지정가", price(trade.limit)],
          ["매수", trade.fill_date ? `${price(trade.fill_price)}` : "미체결"],
          ["매도", trade.exit_date ? price(trade.exit_price) : "–"],
          ["수익률", pct(trade.ret, 2, true)],
          ["최고", pct(trade.mfe, 1, true)],
          ["최저", pct(trade.mae, 1, true)],
        ].map(([k, v]) => (
          <div key={k} className="bg-card px-2 py-2">
            <dt className="text-xs text-muted">{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <p className="bg-card px-4 pb-3 text-[11px] text-faint">수정주가 기준 · 일봉 · 최고/최저는 보유기간 중 고가/저가 기준</p>
    </div>
  );
}
