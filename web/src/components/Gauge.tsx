"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

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
}

const sign = (v: number, nd = 1) => `${v > 0 ? "+" : ""}${v.toFixed(nd)}`;

// 시장 탭: 지금 코스피 모습 (박스·추세·쏠림·폭·v2b) — 참고용, 규칙을 바꾸지 않는다
export default function Gauge() {
  const [g, setG] = useState<GaugeData | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api<GaugeData>("/api/gauge")
      .then(setG)
      .catch((e) => setErr((e as Error).message));
  }, []);

  if (err) return <p className="text-xs text-faint">계기판: {err}</p>;
  if (!g) return <p className="text-xs text-faint">계기판 불러오는 중…</p>;
  if (!g.available) return null;

  const rows: { label: string; value: string; note: string; tone?: "up" | "down" }[] = [];
  if (g.box)
    rows.push({
      label: "박스 정도",
      value: `${g.box.ratio.toFixed(2)}배`,
      note: `1년 고점÷저점 · ${g.box.is_box ? "1.25배 이내 → 박스장" : "범위 넓음 → 추세장"}${g.box.pos != null ? ` · 범위 안 위치 ${g.box.pos}%` : ""}`,
    });
  if (g.trend)
    rows.push({
      label: "200일선",
      value: `${sign(g.trend.pct)}%`,
      note: `코스피 ${g.kospi?.toLocaleString("ko-KR")} · 200일선 ${Math.round(g.trend.ma200).toLocaleString("ko-KR")}`,
      tone: g.trend.pct >= 0 ? "up" : "down",
    });
  if (g.skew && g.skew.gap != null)
    rows.push({
      label: "대형주 쏠림",
      value: `${sign(g.skew.gap, 0)}%p`,
      note: `최근 60일 · 상위 10 ${sign(g.skew.top10 ?? 0, 0)}% vs 101위 아래 보통 종목 ${sign(g.skew.rest ?? 0, 0)}%`,
      tone: g.skew.gap >= 0 ? "up" : "down",
    });
  if (g.breadth != null)
    rows.push({
      label: "시장 폭",
      value: `${g.breadth.toFixed(0)}%`,
      note: "최근 20일 평균, 그날 오른 종목 비율 (50% 아래면 내린 종목이 더 많음)",
      tone: g.breadth >= 50 ? "up" : "down",
    });
  if (g.v2b?.signals != null)
    rows.push({ label: "v2b 신호", value: `${g.v2b.signals}건`, note: `최근 20거래일 · ${g.v2b.days}일에 나옴` });

  return (
    <section className="space-y-3 rounded-xl border border-border bg-card p-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-lg font-bold">시장 계기판</h2>
        <span className="tabular text-xs text-muted">{g.as_of} · 코스피</span>
      </div>
      <div className="divide-y divide-border">
        {rows.map((r) => (
          <div key={r.label} className="grid grid-cols-[5.5rem_4.5rem_1fr] items-baseline gap-2 py-2 text-sm">
            <span className="text-muted">{r.label}</span>
            <span className={`tabular text-right font-semibold ${r.tone === "up" ? "text-up" : r.tone === "down" ? "text-down" : ""}`}>
              {r.value}
            </span>
            <span className="text-xs text-faint">{r.note}</span>
          </div>
        ))}
      </div>
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
      <p className="text-[11px] text-faint">지금 모습을 보는 참고용이에요. 이걸로 규칙을 바꾸지 않아요 — v2b·적립은 어떤 장이든 그대로.</p>
    </section>
  );
}
