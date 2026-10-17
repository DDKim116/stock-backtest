"use client";

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
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
// 이평선 색 (파스텔 톤). 240일선은 짙은 회색으로 조금 굵게
const MA_COLORS: Record<number, string> = { 5: "#e8898c", 20: "#7ea7dd", 60: "#7fc29b", 120: "#a68fdc", 240: "#6b7079" };
// 어두운 화면에서는 짙은 회색이 묻혀서 한 단계 밝게
function isDark(): boolean {
  const t = document.documentElement.dataset.theme;
  if (t) return t === "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}
const MA_WIDTH: Record<number, 1 | 2> = { 240: 2 };
const DEFAULT_MA = [5, 20, 60, 120, 240];
// 전략 이평선 (조건식에 쓴 기간 중 기본선에 없는 것). 점선으로 구분
const STRAT_COLORS = ["#e9a96f", "#6fbcc8", "#d993bf", "#b5b86a"];
function strategyPeriods(spec: Spec): number[] {
  const text = `${spec.signal}\n${spec.exit?.exit_signal ?? ""}`;
  const ns = [...text.matchAll(/(?:지수이평|이평|ema|ma)\(\s*종가\s*,\s*(\d+)\s*\)/gi)].map((m) => +m[1]);
  return [...new Set(ns)].filter((n) => n >= 2 && n <= 300 && !DEFAULT_MA.includes(n)).sort((a, b) => a - b);
}

// 조건식에 쓴 지수 기간: 이평(지수, n) → 지수 칸에 점선, 이전(지수, n) → n일 등락률
function indexPeriods(spec: Spec): { ma: number[]; back: number[] } {
  const text = `${spec.signal}\n${spec.exit?.exit_signal ?? ""}`;
  const pick = (re: RegExp) =>
    [...new Set([...text.matchAll(re)].map((m) => +m[1]))].filter((n) => n >= 1 && n <= 300).sort((a, b) => a - b);
  return {
    ma: pick(/(?:지수이평|이평|ema|ma)\(\s*(?:지수|시장지수|index|idx)\s*,\s*(\d+)\s*\)/gi),
    back: pick(/이전\(\s*(?:지수|시장지수|index|idx)\s*,\s*(\d+)\s*\)/gi),
  };
}
const IDX_MA_COLORS = ["#e9a96f", "#6fbcc8", "#d993bf"];

interface Ind {
  ma: boolean;
  st: boolean; // 전략 이평선
  bb: boolean;
  rsi: boolean;
  ix: boolean; // 지수
}
const IND_KEY = "sbt-chart-ind";
function loadInd(): Ind {
  try {
    const v = JSON.parse(localStorage.getItem(IND_KEY) ?? "");
    if (v && typeof v === "object") return { ma: !!v.ma, st: !!v.st, bb: !!v.bb, rsi: !!v.rsi, ix: v.ix ?? true };
  } catch {}
  return { ma: true, st: false, bb: false, rsi: false, ix: true };
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
  const [ixName, setIxName] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [hover, setHover] = useState<Bar | null>(null);
  const [pinned, setPinned] = useState<Bar | null>(null); // 누른 봉 (손을 떼도 유지)
  const [pinLeft, setPinLeft] = useState(false); // 누른 곳 반대편에 정보 카드를 띄운다
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
  const stratMA = strategyPeriods(spec);
  // 지금 그릴 이평선: 기본선(이평선 버튼) + 전략 이평선(전략 이평선 버튼)
  const periods = [...(ind.ma ? DEFAULT_MA : []), ...(ind.st ? stratMA : [])];
  const isStrat = (n: number) => !DEFAULT_MA.includes(n);
  const maColor = (n: number) =>
    isStrat(n) ? STRAT_COLORS[stratMA.indexOf(n) % STRAT_COLORS.length] : n === 240 && isDark() ? "#9a9ea6" : MA_COLORS[n];
  const ixP = indexPeriods(spec);
  const hasIx = !!bars?.some((b) => b.x != null);
  const showIx = ind.ix && hasIx;
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const end = trade.exit_date ?? trade.fill_date ?? trade.signal_date;
    const q = new URLSearchParams({
      dataset,
      code: trade.code,
      start: shiftDate(trade.signal_date, -760),
      end: shiftDate(end, 370),
    });
    api<{ bars: Bar[]; index_name?: string | null }>(`/api/chart?${q}`)
      .then((r) => {
        setBars(r.bars);
        setIxName(r.index_name ?? null);
      })
      .catch((e) => setErr((e as Error).message));
  }, [trade, dataset]);

  useEffect(() => {
    if (!bars || !box.current) return;
    const up = cssVar("--up");
    const fg0 = cssVar("--foreground");
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
    const line = (vals: (number | null)[], color: string, pane = 0, style = LineStyle.Solid, width: 1 | 2 = 1) => {
      const ls = chart.addSeries(
        LineSeries,
        {
          color,
          lineWidth: width,
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
    for (const n of periods) line(sma(closes, n), maColor(n), 0, isStrat(n) ? LineStyle.Dashed : LineStyle.Solid, MA_WIDTH[n] ?? 1);
    if (ind.bb) {
      const bb = bollinger(closes, 20, 2);
      const c = cssVar("--muted");
      line(bb.up, c, 0, LineStyle.Dashed);
      line(bb.mid, c, 0, LineStyle.Dotted);
      line(bb.lo, c, 0, LineStyle.Dashed);
    }
    let nextPane = 2;
    if (showIx) {
      // 지수: 종가 선 + 조건식에 쓴 지수 이평선(점선). 칸이 따로라 축도 따로
      const ixVals = bars.map((b) => b.x ?? null);
      const ixFmt = { type: "custom" as const, formatter: (p: number) => p.toLocaleString("ko-KR", { maximumFractionDigits: 2 }), minMove: 0.01 };
      const xl = line(ixVals, fg0, nextPane);
      xl.applyOptions({ priceFormat: ixFmt, lastValueVisible: true, title: ixName ?? "지수" });
      ixP.ma.forEach((n, k) => {
        const s = line(smaGap(ixVals, n), IDX_MA_COLORS[k % IDX_MA_COLORS.length], nextPane, LineStyle.Dashed);
        s.applyOptions({ priceFormat: ixFmt });
      });
      nextPane++;
    }
    if (ind.rsi) {
      const r = line(rsi(closes, 14), cssVar("--accent"), nextPane);
      r.createPriceLine({ price: 70, color: up, lineStyle: LineStyle.Dotted, lineWidth: 1, title: "70" });
      r.createPriceLine({ price: 30, color: down, lineStyle: LineStyle.Dotted, lineWidth: 1, title: "30" });
    }
    const panes = chart.panes();
    panes[0].setStretchFactor(4);
    panes[1].setStretchFactor(1);
    for (const pn of panes.slice(2)) pn.setStretchFactor(1.3);
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
      if (!p.time) return;
      setPinned(byTime.get(String(p.time)) ?? null);
      if (p.point && box.current) setPinLeft(p.point.x > box.current.clientWidth / 2);
    });
    return () => {
      chartRef.current = null;
      chart.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- periods·maColor 는 spec 에서 나옴
  }, [bars, trade, spec, currency, ind, mine, strategy, ixName]);

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
    const mas = periods.map((n) => ({ n, v: sma(closes, n)[i] }));
    const r = ind.rsi ? rsi(closes, 14)[i] : null;
    const bb = ind.bb ? bollinger(closes, 20, 2) : null;
    const base = trade.fill_price ?? null;
    return {
      chg: prev ? (shown.c / prev - 1) * 100 : null,
      mas,
      rsi: r,
      bb: bb ? { up: bb.up[i], lo: bb.lo[i] } : null,
      vsBuy: base && shown.t >= (trade.fill_date ?? "") ? (shown.c / base - 1) * 100 : null,
      ix: showIx ? ixInfo(bars, i, ixP) : null,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- periods 는 spec 에서 나옴
  }, [bars, shown, ind, trade, spec]);

  // 누른 봉 정보 카드 (항목, 값, 색)
  const pinCard = useMemo(() => {
    if (!bars || !pinned) return null;
    const i = bars.findIndex((b) => b.t === pinned.t);
    if (i < 0) return null;
    const closes = bars.map((b) => b.c);
    const prev = i > 0 ? bars[i - 1].c : null;
    const chg = prev ? (pinned.c / prev - 1) * 100 : null;
    const rows: [string, string, string?][] = [
      ["등락률", pct(chg, 2, true), retClass(chg)],
      ["시가", price(pinned.o, currency)],
      ["고가", price(pinned.h, currency)],
      ["저가", price(pinned.l, currency)],
      ["종가", price(pinned.c, currency)],
      ["거래량", Math.round(pinned.v).toLocaleString("ko-KR")],
    ];
    if (pinned.a != null) rows.push(["거래대금", bigMoney(pinned.a, currency)]);
    if (prev && i > 0 && bars[i - 1].v > 0) rows.push(["전일 대비 량", `${(pinned.v / bars[i - 1].v).toFixed(1)}배`]);
    for (const n of periods) {
      const v = sma(closes, n)[i];
      if (v != null) rows.push([`${n}일선${isStrat(n) ? " (전략)" : ""}`, price(v, currency)]);
    }
    if (ind.bb) {
      const bb = bollinger(closes, 20, 2);
      if (bb.up[i] != null) rows.push(["볼린저", `${price(bb.lo[i], currency)}~${price(bb.up[i], currency)}`]);
    }
    if (ind.rsi) {
      const r = rsi(closes, 14)[i];
      if (r != null) rows.push(["RSI", r.toFixed(1)]);
    }
    const xi = showIx ? ixInfo(bars, i, ixP) : null;
    if (xi) {
      rows.push([ixName ?? "지수", xi.v.toLocaleString("ko-KR", { maximumFractionDigits: 2 })]);
      rows.push(["지수 등락", pct(xi.chg, 2, true), retClass(xi.chg)]);
      for (const b of xi.back) rows.push([`지수 ${b.n}일 등락`, pct(b.r, 2, true), retClass(b.r)]);
      for (const m of xi.ma) if (m.v != null) rows.push([`지수 ${m.n}일선`, m.v.toLocaleString("ko-KR", { maximumFractionDigits: 2 })]);
    }
    const base = trade.fill_price ?? null;
    if (base && pinned.t >= (trade.fill_date ?? "")) {
      const d = (pinned.c / base - 1) * 100;
      rows.push(["매수가 대비", pct(d, 2, true), retClass(d)]);
    }
    return rows;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- periods 는 spec 에서 나옴
  }, [bars, pinned, ind, trade, spec, currency, ixName]);

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
              {info?.ix && (
                <span>
                  {ixName ?? "지수"} {info.ix.v.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}{" "}
                  <span className={retClass(info.ix.chg)}>{pct(info.ix.chg, 2, true)}</span>
                  {info.ix.back.map((b) => (
                    <span key={b.n}>
                      {" "}· {b.n}일 <span className={retClass(b.r)}>{pct(b.r, 1, true)}</span>
                    </span>
                  ))}
                </span>
              )}
            </div>
          </>
        ) : (
          <span>차트의 봉을 누르면 그날 정보가 나옵니다</span>
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
            ["st", "전략 이평선"],
            ["bb", "볼린저"],
            ["rsi", "RSI"],
            ["ix", "지수"],
          ] as const
        )
          .filter(([k]) => (k !== "st" || stratMA.length > 0) && (k !== "ix" || hasIx))
          .map(([k, label]) => (
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
      {periods.length > 0 && (
        <div className="flex flex-wrap gap-x-3 px-4 pb-1 text-[11px] text-muted">
          {periods.map((n) => (
            <span key={n} className="inline-flex items-center gap-1">
              {isStrat(n) ? (
                <span className="inline-block w-3 border-t-2 border-dashed" style={{ borderColor: maColor(n) }} />
              ) : (
                <span className={`inline-block w-3 rounded ${MA_WIDTH[n] === 2 ? "h-1" : "h-0.5"}`} style={{ background: maColor(n) }} />
              )}
              {n}일{isStrat(n) && " (전략)"}
            </span>
          ))}
          {ind.bb && <span>볼린저 20일 ±2σ (점선)</span>}
        </div>
      )}
      {showIx && ixP.ma.length > 0 && (
        <div className="flex flex-wrap gap-x-3 px-4 pb-1 text-[11px] text-muted">
          {ixP.ma.map((n, k) => (
            <span key={n} className="inline-flex items-center gap-1">
              <span className="inline-block w-3 border-t-2 border-dashed" style={{ borderColor: IDX_MA_COLORS[k % IDX_MA_COLORS.length] }} />
              지수 {n}일
            </span>
          ))}
        </div>
      )}
      {periods.length === 0 && ind.bb && <div className="px-4 pb-1 text-[11px] text-muted">볼린저 20일 ±2σ (점선)</div>}
      <div className="relative min-h-0 flex-1">
        {err && <p className="p-4 text-sm text-critical">⚠ {err}</p>}
        {!bars && !err && <p className="p-4 text-sm text-muted">불러오는 중…</p>}
        <div ref={box} className="absolute inset-0" />
        {pinned && pinCard && (
          <div
            className={`tabular absolute top-2 z-10 w-44 rounded-lg border border-border bg-card/95 p-2 text-xs shadow-lg ${pinLeft ? "left-2" : "right-16"}`}
          >
            <div className="mb-1 flex items-center justify-between">
              <span className="font-semibold">{pinned.t}</span>
              <button type="button" onClick={() => setPinned(null)} className="px-1 text-muted" aria-label="봉 정보 닫기">
                ✕
              </button>
            </div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5">
              {pinCard.map(([k, v, cls]) => (
                <Fragment key={k}>
                  <dt className="text-muted">{k}</dt>
                  <dd className={`text-right ${cls ?? ""}`}>{v}</dd>
                </Fragment>
              ))}
            </dl>
          </div>
        )}
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

// 지수에는 빈 날(null)이 있을 수 있어서 null 을 건너뛰지 않고 그 자리만 비운다
function smaGap(vals: (number | null)[], n: number): (number | null)[] {
  const out: (number | null)[] = new Array(vals.length).fill(null);
  let sum = 0;
  let cnt = 0;
  for (let i = 0; i < vals.length; i++) {
    const v = vals[i];
    if (v == null) {
      sum = 0;
      cnt = 0;
      continue;
    }
    sum += v;
    cnt++;
    if (cnt > n) sum -= vals[i - n] as number;
    if (cnt >= n) out[i] = sum / n;
  }
  return out;
}

function ixInfo(bars: Bar[], i: number, p: { ma: number[]; back: number[] }) {
  const v = bars[i].x;
  if (v == null) return null;
  const prev = i > 0 ? bars[i - 1].x : null;
  const vals = bars.map((b) => b.x ?? null);
  return {
    v,
    chg: prev ? (v / prev - 1) * 100 : null,
    back: (p.back.length ? p.back : [5])
      .map((n) => ({ n, b: i - n >= 0 ? bars[i - n].x : null }))
      .filter((o): o is { n: number; b: number } => o.b != null)
      .map((o) => ({ n: o.n, r: (v / o.b - 1) * 100 })),
    ma: p.ma.map((n) => ({ n, v: smaGap(vals, n)[i] })),
  };
}
