"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { wonShort } from "@/lib/auto";
import { pct, retClass } from "@/lib/format";

interface Item {
  strategy_id: number;
  name: string;
  color: string;
  per_trade: number;
  actual: { n: number; avg: number; win: number; pnl: number; mdd: number; lose_streak: number; first: string; last: string } | null;
  expect: { n: number; avg: number; std: number; win: number; since: string } | null;
  band: [number, number] | null;
  win_band: [number, number] | null;
  verdict: "early" | "inside" | "above" | "below" | null;
}
const VERDICT: Record<string, [string, string]> = {
  early: ["아직 판단 이름", "bg-background text-muted"],
  inside: ["기대 범위 안", "bg-accent/10 text-accent"],
  above: ["기대보다 좋음", "bg-up/10 text-up"],
  below: ["기대보다 나쁨 · 점검", "bg-down/10 text-down"],
};
const MIN_N = 30;

// 기대 범위 막대: 범위(띠) · 백테스트 평균(세로선) · 실제(점)
function Band({ lo, hi, exp, act, color }: { lo: number; hi: number; exp: number; act: number; color: string }) {
  const min = Math.min(lo, act) - 0.5;
  const max = Math.max(hi, act) + 0.5;
  const x = (v: number) => `${((v - min) / (max - min)) * 100}%`;
  return (
    <div className="relative h-4" aria-hidden>
      <div className="absolute inset-x-0 top-1/2 h-px bg-border" />
      <div className="absolute top-1/2 h-2 -translate-y-1/2 rounded bg-foreground/15" style={{ left: x(lo), width: `calc(${x(hi)} - ${x(lo)})` }} />
      <div className="absolute top-0 h-4 w-0.5 bg-foreground/60" style={{ left: x(exp) }} />
      <div className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card" style={{ left: x(act), background: color }} />
    </div>
  );
}

// 매매 → 키움 자동: 전략마다 모의투자 실제 vs 백테스트 기대치
export default function AutoCompare() {
  const [items, setItems] = useState<Item[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api<{ items: Item[] }>("/api/auto/compare").then(
      (r) => setItems(r.items),
      (e) => setErr((e as Error).message),
    );
  }, []);
  if (err) return <p className="text-xs text-critical">⚠ 기대치 비교: {err}</p>;
  if (!items) return null;
  return (
    <section className="space-y-3 rounded-xl border border-border bg-card p-3">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="font-semibold">기대치 vs 실제</h3>
        <span className="text-[11px] text-muted">전략대로 청산한 거래만 · 비용 차감</span>
      </div>
      <div className="grid gap-3 fold:grid-cols-2">
        {items.map((it) => {
          const a = it.actual;
          const e = it.expect;
          const [vl, vc] = VERDICT[it.verdict ?? "early"];
          return (
            <div key={it.strategy_id} className="space-y-2 border-t border-border pt-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="rounded-md px-2 py-0.5 text-xs font-semibold" style={{ border: `1.5px solid ${it.color}` }}>
                  {it.name}
                </span>
                {a && e && <span className={`rounded-md px-2 py-0.5 text-[11px] font-semibold ${vc}`}>{vl}</span>}
              </div>
              {!a && <p className="text-xs text-faint">아직 전략대로 청산한 거래가 없습니다.</p>}
              {a && (
                <>
                  <dl className="tabular grid grid-cols-[auto_1fr_1fr] gap-x-3 gap-y-1 text-xs">
                    <dt className="text-muted" />
                    <dd className="text-right text-muted">실제 (모의투자)</dd>
                    <dd className="text-right text-muted">기대 (백테스트)</dd>
                    <dt className="text-muted">1건당 평균</dt>
                    <dd className={`text-right font-semibold ${retClass(a.avg)}`}>{pct(a.avg, 2, true)}</dd>
                    <dd className="text-right">
                      {e ? (
                        <>
                          {pct(e.avg, 2, true)}
                          {it.band && (
                            <span className="block text-[11px] text-faint">
                              정상 범위 {pct(it.band[0], 1, true)} ~ {pct(it.band[1], 1, true)}
                            </span>
                          )}
                        </>
                      ) : (
                        "–"
                      )}
                    </dd>
                    <dt className="text-muted">수익 비율</dt>
                    <dd className="text-right">{a.win.toFixed(0)}%</dd>
                    <dd className="text-right">
                      {e ? `${e.win.toFixed(0)}%` : "–"}
                      {it.win_band && (
                        <span className="block text-[11px] text-faint">
                          {it.win_band[0].toFixed(0)} ~ {it.win_band[1].toFixed(0)}%
                        </span>
                      )}
                    </dd>
                    <dt className="text-muted">청산 건수</dt>
                    <dd className="text-right">{a.n}건</dd>
                    <dd className="text-right text-faint">{e ? `${e.since.slice(0, 4)}~ ${e.n.toLocaleString("ko-KR")}건` : ""}</dd>
                  </dl>
                  {e && it.band && <Band lo={it.band[0]} hi={it.band[1]} exp={e.avg} act={a.avg} color={it.color} />}
                  <p className="tabular text-[11px] text-muted">
                    실현 손익 <span className={retClass(a.pnl)}>{wonShort(a.pnl)}</span> · 최대 낙폭 {wonShort(a.mdd)} · 최장 연속 손실{" "}
                    {a.lose_streak}번 · {a.first.slice(5).replace("-", "/")}~{a.last.slice(5).replace("-", "/")}
                  </p>
                  {a.n < MIN_N && (
                    <p className="text-[11px] text-faint">
                      {a.n}/{MIN_N}건 · 30건이 넘어야 실력인지 운인지 구분할 수 있어요. 그 전엔 범위를 벗어나도 흔한 일이에요.
                    </p>
                  )}
                </>
              )}
            </div>
          );
        })}
      </div>
      <p className="text-[11px] text-faint">
        기대 = 같은 전략을 2021년부터 백테스트한 거래 1건당 평균. 정상 범위 = 지금 건수에서 우연히 나올 수 있는 폭(약 95%). 실제는 수수료·세금(0.23%)을
        빼서 맞췄고, 모의투자 체결은 실제 시장보다 유리할 수 있어 실투에선 조금 낮게 보세요.
      </p>
    </section>
  );
}
