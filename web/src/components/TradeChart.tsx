"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { api } from "@/lib/api";
import { bigMoney, pct, price, retClass } from "@/lib/format";
import { bollinger, rsi, sma } from "@/lib/indicators";
import type { Bar, Spec, Trade } from "@/lib/types";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

const RANGES = [
  { label: "3개월", bars: 63 },
  { label: "6개월", bars: 126 },
  { label: "1년", bars: 250 },
  { label: "2년", bars: 500 },
];

// 이평선 색: 5·20·60·120 은 증권사 차트에서 흔히 쓰는 구분
const MA_COLORS: Record<number, string> = { 5: "#e5a50a", 20: "#2e9e6b", 60: "#8a5cd0", 120: "#8a8f98" };
const EXTRA_COLORS = ["#d9620f", "#1f8fb5", "#c2418a"];
const DEFAULT_MA = [5, 20, 60, 120];

interface Ind {
  ma: boolean;
  bb: boolean;
  rsi: boolean;
}
const IND_KEY = "sbt-chart-ind";
function loadInd(): Ind {
  try {
    const v = JSON.parse(localStorage.getItem(IND_KEY) ?? "");
    if (v && typeof v === "object") return { ma: !!v.ma, bb: !!v.bb, rsi: !!v.rsi };
  } catch {}
  return { ma: true, bb: false, rsi: false };
}

// 조건식에 쓴 이평 기간도 차트에 함께 그린다 (예: 이평(종가, 33))
function maPeriods(signal: string): number[] {
  const extra = [...signal.matchAll(/(?:이평|ma)\(\s*종가\s*,\s*(\d+)\s*\)/g)].map((m) => +m[1]);
  return [...new Set([...DEFAULT_MA, ...extra])].filter((n) => n >= 2 && n <= 300).sort((a, b) => a - b);
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
  currency,
  onClose,
  mine,
  strategy = true,
}: {
  trade: Trade;
  spec: Spec;
  dataset: string;
  currency: string;
  onClose: () => void;
  mine?: { date: string; side: "buy" | "sell"; price: number }[]; // 매매일지의 내 체결
  strategy?: boolean; // false 면 전략 표시(신호·매수·청산·가격선)를 빼고 내 체결만
}) {
  const box = useRef<HTMLDivElement>(null);
  const [bars, setBars] = useState<Bar[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [hover, setHover] = useState<Bar | null>(null);
  const [pinned, setPinned] = useState<Bar | null>(null); // 누른 봉 (손을 떼도 유지)
  const [span, setSpan] = useState(RANGES[1].bars);
  const [ind, setInd] = useState<Ind>(loadInd);
  const toggle = (k: keyof Ind) =>
    setInd((v) => {
      const n = { ...v, [k]: !v[k] };
      try {
        localStorage.setItem(IND_KEY, JSON.stringify(n));
      } catch {}
      return n;
    });
  const periods = maPeriods(spec.signal);
  const maColor = (n: number) => MA_COLORS[n] ?? EXTRA_COLORS[periods.filter((p) => !MA_COLORS[p]).indexOf(n) % EXTRA_COLORS.length];
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const end = trade.exit_date ?? trade.fill_date ?? trade.signal_date;
    const q = new URLSearchParams({
      dataset,
      code: trade.code,
      start: shiftDate(trade.signal_date, -760),
      end: shiftDate(end, 370),
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
      layout: {
        background: { color: "transparent" },
        textColor: text,
        fontFamily: "Pretendard, sans-serif",
        panes: { separatorColor: grid },
      },
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
      priceFormat: { type: "custom", formatter: (p: number) => price(p, currency), minMove: 0.01 },
    });
    candles.setData(bars.map((b) => ({ time: b.t as Time, open: b.o, high: b.h, low: b.l, close: b.c })));
    // 거래량은 아래 별도 칸에 (같은 축을 쓰면 가격 축에 0·음수 눈금이 생김)
    const vol = chart.addSeries(
      HistogramSeries,
      {
        priceFormat: { type: "volume" },
        lastValueVisible: false,
        priceLineVisible: false,
      },
      1,
    );
    // 보조지표
    const closes = bars.map((b) => b.c);
    const line = (vals: (number | null)[], color: string, pane = 0, style = LineStyle.Solid) => {
      const ls = chart.addSeries(
        LineSeries,
        {
          color,
          lineWidth: 1,
          lineStyle: style,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          priceFormat: pane === 0 ? { type: "custom", formatter: (p: number) => price(p, currency), minMove: 0.01 } : undefined,
        },
        pane,
      );
      ls.setData(bars.map((b, i) => (vals[i] == null ? { time: b.t as Time } : { time: b.t as Time, value: vals[i] as number })));
      return ls;
    };
    if (ind.ma) for (const n of periods) line(sma(closes, n), maColor(n));
    if (ind.bb) {
      const bb = bollinger(closes, 20, 2);
      const c = cssVar("--muted");
      line(bb.up, c, 0, LineStyle.Dashed);
      line(bb.mid, c, 0, LineStyle.Dotted);
      line(bb.lo, c, 0, LineStyle.Dashed);
    }
    if (ind.rsi) {
      const r = line(rsi(closes, 14), cssVar("--accent"), 2);
      r.createPriceLine({ price: 70, color: up, lineStyle: LineStyle.Dotted, lineWidth: 1, title: "70" });
      r.createPriceLine({ price: 30, color: down, lineStyle: LineStyle.Dotted, lineWidth: 1, title: "30" });
    }
    const panes = chart.panes();
    panes[0].setStretchFactor(4);
    panes[1].setStretchFactor(1);
    if (panes[2]) panes[2].setStretchFactor(1.2);
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.05 } });
    vol.setData(
      bars.map((b) => ({ time: b.t as Time, value: b.v, color: (b.c >= b.o ? up : down) + "66" })),
    );

    const fg = cssVar("--foreground");
    const tag = mine ? "전략 " : "";
    const markers: SeriesMarker<Time>[] = strategy
      ? [{ time: trade.signal_date as Time, position: "aboveBar", shape: "circle", color: fg, text: "신호" }]
      : [];
    if (strategy && trade.fill_date)
      markers.push({ time: trade.fill_date as Time, position: "belowBar", shape: "arrowUp", color: up, text: tag + "매수" });
    if (strategy && trade.exit_date) {
      const label =
        trade.outcome === "목표도달"
          ? "목표"
          : trade.outcome === "손절" || trade.outcome === "추적손절" || trade.outcome === "조건청산"
            ? trade.outcome
            : "청산";
      markers.push({ time: trade.exit_date as Time, position: "aboveBar", shape: "arrowDown", color: down, text: tag + label });
    }
    const acc = cssVar("--accent");
    for (const f of mine ?? [])
      markers.push(
        f.side === "buy"
          ? { time: f.date as Time, position: "belowBar", shape: "square", color: acc, text: "내 매수" }
          : { time: f.date as Time, position: "aboveBar", shape: "square", color: acc, text: "내 매도" },
      );
    markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
    createSeriesMarkers(candles, markers);

    if (!strategy) {
      // 내 매매만 볼 때는 전략 가격선을 그리지 않는다
    } else if (trade.fill_price) {
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

    chartRef.current = chart;

    const byTime = new Map(bars.map((b) => [b.t, b]));
    chart.subscribeCrosshairMove((p) => {
      setHover(p.time ? (byTime.get(String(p.time)) ?? null) : null);
    });
    chart.subscribeClick((p) => {
      if (p.time) setPinned(byTime.get(String(p.time)) ?? null);
    });
    return () => {
      chartRef.current = null;
      chart.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- periods·maColor 는 spec 에서 나옴
  }, [bars, trade, spec, currency, ind, mine, strategy]);

  // 보이는 기간: 매수·매도 지점이 오른쪽에 오도록 앞쪽을 span 만큼 보여 준다
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !bars) return;
    const idx = Math.max(0, bars.findIndex((b) => b.t >= trade.signal_date));
    const exitIdx = bars.findIndex((b) => b.t >= (trade.exit_date ?? trade.signal_date));
    const right = Math.min(bars.length - 1, Math.max(idx, exitIdx) + Math.max(10, Math.round(span * 0.12)));
    chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, right - span), to: right + 1 });
  }, [bars, trade, span, ind]);

  const shown = hover ?? pinned ?? bars?.find((b) => b.t === trade.signal_date) ?? null;

  // 고른 봉의 등락률·이평·RSI·매수가 대비
  const info = useMemo(() => {
    if (!bars || !shown) return null;
    const i = bars.findIndex((b) => b.t === shown.t);
    if (i < 0) return null;
    const closes = bars.map((b) => b.c);
    const prev = i > 0 ? bars[i - 1].c : null;
    const mas = ind.ma ? periods.map((n) => ({ n, v: sma(closes, n)[i] })) : [];
    const r = ind.rsi ? rsi(closes, 14)[i] : null;
    const bb = ind.bb ? bollinger(closes, 20, 2) : null;
    const base = trade.fill_price ?? null;
    return {
      chg: prev ? (shown.c / prev - 1) * 100 : null,
      mas,
      rsi: r,
      bb: bb ? { up: bb.up[i], lo: bb.lo[i] } : null,
      vsBuy: base && shown.t >= (trade.fill_date ?? "") ? (shown.c / base - 1) * 100 : null,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- periods 는 spec 에서 나옴
  }, [bars, shown, ind, trade, spec]);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-background" role="dialog" aria-modal="true">
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <div>
          <div className="font-semibold">
            {trade.name} <span className="text-sm font-normal text-muted">{trade.code} · {trade.market}</span>
          </div>
          <div className="text-xs text-muted">
            {strategy ? `신호 ${trade.signal_date} · ${trade.outcome}` : "내 매수·매도 표시"}
            {trade.uncertain && ` (낙관적: ${trade.outcome_opt})`}
          </div>
        </div>
        <button type="button" onClick={onClose} className="rounded-lg border border-border px-3 py-1.5 text-sm">
          닫기
        </button>
      </header>
      <div className="tabular space-y-0.5 px-4 py-2 text-xs text-muted">
        {shown ? (
          <>
            <div className="flex flex-wrap gap-x-3">
              <span className="font-semibold text-foreground">
                {shown.t}
                {pinned && pinned.t === shown.t && !hover && " 📌"}
              </span>
              {info?.chg != null && <span className={retClass(info.chg)}>{pct(info.chg, 2, true)}</span>}
              <span>시 {price(shown.o, currency)}</span>
              <span>고 {price(shown.h, currency)}</span>
              <span>저 {price(shown.l, currency)}</span>
              <span>종 {price(shown.c, currency)}</span>
            </div>
            <div className="flex flex-wrap gap-x-3">
              <span>량 {Math.round(shown.v).toLocaleString("ko-KR")}</span>
              {shown.a != null && <span>대금 {bigMoney(shown.a, currency)}</span>}
              {info?.vsBuy != null && (
                <span>
                  매수가 대비 <span className={retClass(info.vsBuy)}>{pct(info.vsBuy, 2, true)}</span>
                </span>
              )}
              {info?.mas.map((m) =>
                m.v != null ? (
                  <span key={m.n}>
                    {m.n}일 {price(m.v, currency)}
                  </span>
                ) : null,
              )}
              {info?.bb && info.bb.up != null && (
                <span>
                  밴드 {price(info.bb.lo, currency)}~{price(info.bb.up, currency)}
                </span>
              )}
              {info?.rsi != null && <span>RSI {info.rsi.toFixed(1)}</span>}
            </div>
          </>
        ) : (
          <span>차트를 눌러 가격 확인</span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-1 px-4 pb-1">
        {RANGES.map((r) => (
          <button
            key={r.label}
            type="button"
            onClick={() => setSpan(r.bars)}
            className={`rounded-md px-2.5 py-1 text-xs ${span === r.bars ? "bg-accent text-white" : "border border-border text-muted"}`}
          >
            {r.label}
          </button>
        ))}
        <span className="mx-1 h-4 w-px bg-border" />
        {(
          [
            ["ma", "이평선"],
            ["bb", "볼린저"],
            ["rsi", "RSI"],
          ] as const
        ).map(([k, label]) => (
          <button
            key={k}
            type="button"
            onClick={() => toggle(k)}
            aria-pressed={ind[k]}
            className={`rounded-md px-2.5 py-1 text-xs ${ind[k] ? "border border-accent text-accent" : "border border-border text-muted"}`}
          >
            {label}
          </button>
        ))}
      </div>
      {ind.ma && (
        <div className="flex flex-wrap gap-x-3 px-4 pb-1 text-[11px] text-muted">
          {periods.map((n) => (
            <span key={n} className="inline-flex items-center gap-1">
              <span className="inline-block h-0.5 w-3 rounded" style={{ background: maColor(n) }} />
              {n}일
            </span>
          ))}
          {ind.bb && <span>볼린저 20일 ±2σ (점선)</span>}
        </div>
      )}
      {!ind.ma && ind.bb && <div className="px-4 pb-1 text-[11px] text-muted">볼린저 20일 ±2σ (점선)</div>}
      <div className="relative min-h-0 flex-1">
        {err && <p className="p-4 text-sm text-critical">⚠ {err}</p>}
        {!bars && !err && <p className="p-4 text-sm text-muted">불러오는 중…</p>}
        <div ref={box} className="absolute inset-0" />
      </div>
      {strategy && !mine && (
      <dl className="tabular grid grid-cols-3 gap-px border-t border-border bg-border text-center text-sm">
        {[
          ["지정가", price(trade.limit, currency)],
          ["매수", trade.fill_date ? `${price(trade.fill_price, currency)}` : "미체결"],
          ["매도", trade.exit_date ? price(trade.exit_price, currency) : "–"],
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
      )}
      <p className="bg-card px-4 pb-3 text-[11px] text-faint">수정주가 기준 · 일봉 · 최고/최저는 보유기간 중 고가/저가 기준</p>
    </div>
  );
}
