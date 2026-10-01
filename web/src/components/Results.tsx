"use client";

import { useMemo, useState } from "react";
import { num, pct, price, retClass } from "@/lib/format";
import type { BreakdownRow, Check, RunResult, SingleResult, Spec, SweepResult, Trade } from "@/lib/types";
import TradeChart from "./TradeChart";

const CHECK_STYLE: Record<Check["level"], { icon: string; cls: string; label: string }> = {
  good: { icon: "✓", cls: "text-good", label: "좋음" },
  info: { icon: "i", cls: "text-muted", label: "참고" },
  warn: { icon: "!", cls: "text-warning", label: "주의" },
  danger: { icon: "✕", cls: "text-critical", label: "경고" },
};

function Tile({ label, value, sub, cls }: { label: string; value: string; sub?: string; cls?: string }) {
  return (
    <div className="rounded-xl border border-border bg-card px-3 py-2.5">
      <div className="text-xs text-muted">{label}</div>
      <div className={`tabular text-xl font-semibold ${cls ?? ""}`}>{value}</div>
      {sub && <div className="tabular text-xs text-faint">{sub}</div>}
    </div>
  );
}

function RateBar({ value, base }: { value: number | null; base?: number | null }) {
  // 0~100% 막대. 기준선(아무 날이나 샀을 때)을 세로선으로 표시
  return (
    <div className="relative h-2 w-full rounded-full bg-[var(--bar-track)]">
      {value !== null && (
        <div className="absolute inset-y-0 left-0 rounded-full bg-accent" style={{ width: `${Math.min(100, value)}%` }} />
      )}
      {base !== null && base !== undefined && (
        <div className="absolute -inset-y-0.5 w-0.5 bg-foreground" style={{ left: `${Math.min(100, base)}%` }} />
      )}
    </div>
  );
}

function BreakdownTable({ rows, base }: { rows: BreakdownRow[]; base: number | null }) {
  return (
    <table className="tabular w-full text-sm">
      <thead>
        <tr className="text-left text-xs text-muted">
          <th className="py-1.5 font-normal">구분</th>
          <th className="py-1.5 text-right font-normal">건수</th>
          <th className="w-[38%] py-1.5 pl-3 font-normal">성공률</th>
          <th className="py-1.5 text-right font-normal">평균</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key} className="border-t border-border">
            <td className="py-1.5">{r.key}</td>
            <td className="py-1.5 text-right">{num(r.n_complete)}</td>
            <td className="py-1.5 pl-3">
              <div className="flex items-center gap-2">
                <span className="w-11 shrink-0 text-right">{pct(r.success_rate, 0)}</span>
                <RateBar value={r.success_rate} base={base} />
              </div>
            </td>
            <td className={`py-1.5 text-right ${retClass(r.avg_ret)}`}>{pct(r.avg_ret, 1, true)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const OUTCOME_FILTERS = ["전체", "목표도달", "손절", "기간만료", "미체결", "불확실"] as const;

function summaryText(r: SingleResult): string {
  const s = r.summary;
  const lines = [
    `■ 검증 조건 (${r.dataset.label})`,
    r.text.trim(),
    "",
    "■ 결과",
    `신호 ${s.n_signals}건 / 체결 ${s.n_filled}건 (체결률 ${pct(s.fill_rate)}) / 결과 확정 ${s.n_complete}건`,
    `성공률 ${pct(s.success_rate)} (낙관적 ${pct(s.success_rate_opt)}, 불확실 ${s.n_uncertain}건)`,
    `아무 날이나 샀을 때 성공률 ${pct(r.baseline.success_rate)} → 차이 ${s.edge_pp ?? "–"}%p`,
    `비용 차감 평균 수익률 ${pct(s.avg_ret, 2, true)} / 중앙값 ${pct(s.median_ret, 2, true)}`,
    `보유 중 평균 최고 ${pct(s.avg_mfe, 1, true)} / 평균 최저 ${pct(s.avg_mae, 1, true)}`,
    "",
    "■ 연도별 (건수 / 성공률 / 평균수익률)",
    ...r.breakdowns.year.map((y) => `${y.key}: ${y.n_complete}건 / ${pct(y.success_rate)} / ${pct(y.avg_ret, 1, true)}`),
    "",
    "■ 자동 점검",
    ...r.checks.map((c) => `- ${c.text}`),
  ];
  return lines.join("\n");
}

function Single({ r, onBack }: { r: SingleResult; onBack?: () => void }) {
  const [tab, setTab] = useState<"year" | "market" | "price">("year");
  const [filter, setFilter] = useState<(typeof OUTCOME_FILTERS)[number]>("전체");
  const [limit, setLimit] = useState(100);
  const [open, setOpen] = useState<Trade | null>(null);
  const [copied, setCopied] = useState(false);
  const s = r.summary;
  const b = r.baseline;

  const trades = useMemo(() => {
    if (filter === "전체") return r.trades;
    if (filter === "불확실") return r.trades.filter((t) => t.uncertain);
    return r.trades.filter((t) => t.outcome === filter);
  }, [r.trades, filter]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(summaryText(r));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* 클립보드 권한 없음 */
    }
  };

  const hasTarget = typeof r.spec.exit.target_pct === "number";

  return (
    <div className="space-y-4">
      {onBack && (
        <button type="button" onClick={onBack} className="text-sm text-accent">
          ← 비교표로 돌아가기
        </button>
      )}

      <ul className="space-y-1.5">
        {r.checks.map((c, i) => {
          const st = CHECK_STYLE[c.level];
          return (
            <li key={i} className="flex gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm">
              <span className={`shrink-0 font-bold ${st.cls}`} aria-label={st.label}>
                {st.icon}
              </span>
              <span>{c.text}</span>
            </li>
          );
        })}
      </ul>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Tile
          label={hasTarget ? "성공률 (목표 도달)" : "수익 확률"}
          value={pct(s.success_rate)}
          sub={`기준 ${pct(b.success_rate)} · ${s.edge_pp !== undefined ? (s.edge_pp > 0 ? "+" : "") + s.edge_pp + "%p" : "–"}`}
        />
        <Tile label="결과 확정 건수" value={num(s.n_complete)} sub={`신호 ${num(s.n_signals)} · 체결률 ${pct(s.fill_rate, 0)}`} />
        <Tile
          label="평균 수익률 (비용 차감)"
          value={pct(s.avg_ret, 2, true)}
          cls={retClass(s.avg_ret)}
          sub={`중앙값 ${pct(s.median_ret, 2, true)}`}
        />
        <Tile label="불확실 (일봉 한계)" value={num(s.n_uncertain)} sub={`낙관적 성공률 ${pct(s.success_rate_opt)}`} />
        <Tile label="보유 중 평균 최고/최저" value={`${pct(s.avg_mfe, 1, true)}`} sub={`최저 ${pct(s.avg_mae, 1, true)}`} />
        <Tile
          label="목표까지 평균"
          value={s.avg_days_to_target !== null ? `${s.avg_days_to_target}일` : "–"}
          sub={`목표 ${s.n_target} · 손절 ${s.n_stop} · 만료 ${s.n_timeout}`}
        />
      </div>

      <div className="rounded-xl border border-border bg-card p-3">
        <div className="mb-2 flex items-center justify-between">
          <div className="flex gap-1 text-sm">
            {(
              [
                ["year", "연도별"],
                ["market", "시장별"],
                ["price", "가격대별"],
              ] as const
            ).map(([k, label]) => (
              <button
                key={k}
                type="button"
                onClick={() => setTab(k)}
                className={`rounded-md px-2.5 py-1 ${tab === k ? "bg-accent-soft font-medium text-accent" : "text-muted"}`}
              >
                {label}
              </button>
            ))}
          </div>
          <span className="flex items-center gap-1 text-[11px] text-faint">
            <span className="inline-block h-3 w-0.5 bg-foreground" /> 기준 성공률
          </span>
        </div>
        <BreakdownTable rows={r.breakdowns[tab]} base={b.success_rate} />
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="font-semibold">사례 {num(trades.length)}건</h3>
          <button type="button" onClick={copy} className="rounded-lg border border-border bg-card px-3 py-1 text-xs">
            {copied ? "복사됨 ✓" : "결과 요약 복사"}
          </button>
        </div>
        <div className="mb-2 flex gap-1 overflow-x-auto text-xs">
          {OUTCOME_FILTERS.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => {
                setFilter(f);
                setLimit(100);
              }}
              className={`shrink-0 rounded-full border px-2.5 py-1 ${filter === f ? "border-accent text-accent" : "border-border text-muted"}`}
            >
              {f}
            </button>
          ))}
        </div>
        <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
          {trades.slice(0, limit).map((t, i) => (
            <li key={`${t.code}-${t.signal_date}-${i}`}>
              <button type="button" onClick={() => setOpen(t)} className="flex w-full items-center gap-3 px-3 py-2.5 text-left">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">
                    {t.name} <span className="text-xs font-normal text-faint">{t.code}</span>
                  </div>
                  <div className="tabular text-xs text-muted">
                    {t.signal_date} · {t.outcome}
                    {t.uncertain ? " · 불확실" : ""}
                    {t.days !== null ? ` · ${t.days}일` : ""}
                  </div>
                </div>
                <div className="tabular text-right">
                  <div className={`text-sm font-semibold ${retClass(t.ret)}`}>{pct(t.ret, 2, true)}</div>
                  <div className="text-xs text-faint">{t.fill_price ? price(t.fill_price) : "미체결"}</div>
                </div>
              </button>
            </li>
          ))}
        </ul>
        {trades.length > limit && (
          <button
            type="button"
            onClick={() => setLimit(limit + 200)}
            className="mt-2 w-full rounded-xl border border-border bg-card py-2 text-sm text-muted"
          >
            더 보기 ({num(trades.length - limit)}건 남음)
          </button>
        )}
        {r.trades_truncated && <p className="mt-1 text-xs text-faint">사례가 많아 최근 20,000건만 표시합니다.</p>}
      </div>
      <p className="text-xs text-faint">
        계산 {r.elapsed}초 · {r.dataset.label} · 수정주가·일봉 기준
      </p>
      {open && <TradeChart trade={open} spec={r.spec} dataset={r.dataset.name} onClose={() => setOpen(null)} />}
    </div>
  );
}

function Sweep({ r, onPick }: { r: SweepResult; onPick: (s: Spec) => void }) {
  const keys = Object.keys(r.variants[0]?.params ?? {});
  const label = (k: string) => k.replace(/#(\d+)$/, " ($1)");
  const best = Math.max(...r.variants.map((v) => (v.summary.success_rate ?? 0) - (v.baseline.success_rate ?? 0)));
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">행을 누르면 그 조건의 상세 결과와 사례 차트를 볼 수 있습니다.</p>
      <div className="overflow-x-auto rounded-xl border border-border bg-card">
        <table className="tabular w-full text-[13px]">
          <thead>
            <tr className="text-left text-xs text-muted">
              {keys.map((k) => (
                <th key={k} className="px-2 py-2 font-normal">
                  {label(k)}
                </th>
              ))}
              <th className="px-2 py-2 text-right font-normal">건수</th>
              <th className="px-2 py-2 text-right font-normal">성공률</th>
              <th className="px-2 py-2 text-right font-normal">기준 차이</th>
              <th className="px-2 py-2 text-right font-normal">평균</th>
            </tr>
          </thead>
          <tbody>
            {r.variants.map((v, i) => {
              const edge =
                v.summary.success_rate !== null && v.baseline.success_rate !== null
                  ? v.summary.success_rate - v.baseline.success_rate
                  : null;
              return (
                <tr key={i} onClick={() => onPick(v.spec)} className="cursor-pointer border-t border-border">
                  {keys.map((k) => (
                    <td key={k} className="px-2 py-2 font-medium">
                      {v.params[k]}
                    </td>
                  ))}
                  <td className="px-2 py-2 text-right">{num(v.summary.n_complete)}</td>
                  <td className="px-2 py-2 text-right">{pct(v.summary.success_rate)}</td>
                  <td className="px-2 py-2 text-right">
                    {edge === null ? "–" : `${edge > 0 ? "+" : ""}${edge.toFixed(1)}%p`}
                    {edge !== null && edge === best && r.variants.length > 1 ? " ★" : ""}
                  </td>
                  <td className={`px-3 py-2 text-right ${retClass(v.summary.avg_ret)}`}>{pct(v.summary.avg_ret, 2, true)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-faint">
        ★ 기준 대비 우위가 가장 큰 조합. 표본이 적은 조합은 우연일 수 있으니 건수를 함께 보세요. · 계산 {r.elapsed}초
      </p>
    </div>
  );
}

export default function Results({
  result,
  detail,
  onPick,
  onBack,
}: {
  result: RunResult;
  detail: SingleResult | null;
  onPick: (s: Spec) => void;
  onBack: () => void;
}) {
  if (detail) return <Single r={detail} onBack={result.kind === "sweep" ? onBack : undefined} />;
  if (result.kind === "sweep") return <Sweep r={result} onPick={onPick} />;
  return <Single r={result} />;
}
