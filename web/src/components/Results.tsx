"use client";

import { useCallback, useMemo, useState } from "react";
import { useBackClose } from "@/lib/useBackClose";
import { num, pct, price, retClass } from "@/lib/format";
import type { BreakdownRow, Check, RunResult, SingleResult, Spec, SweepResult, Trade } from "@/lib/types";
import AIExplain from "./AIExplain";
import TradeChart from "./TradeChart";

const CHECK_STYLE: Record<Check["level"], { icon: string; cls: string; label: string }> = {
  good: { icon: "✓", cls: "text-good", label: "좋음" },
  info: { icon: "i", cls: "text-muted", label: "참고" },
  warn: { icon: "!", cls: "text-warning", label: "주의" },
  danger: { icon: "✕", cls: "text-critical", label: "경고" },
};

function Tile({
  label,
  value,
  sub,
  cls,
  help,
}: {
  label: string;
  value: string;
  sub?: string;
  cls?: string;
  help?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`rounded-xl border border-border bg-card px-3 py-2.5 ${open ? "col-span-2 sm:col-span-3" : ""}`}>
      <div className="flex items-start justify-between gap-1 text-xs text-muted">
        <span>{label}</span>
        {help && (
          <button
            type="button"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            aria-label={`${label} 설명`}
            className={`-mr-1 -mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[11px] ${open ? "border-accent text-accent" : "border-border text-faint"}`}
          >
            ?
          </button>
        )}
      </div>
      <div className={`tabular text-xl font-semibold ${cls ?? ""}`}>{value}</div>
      {sub && <div className="tabular text-xs text-faint">{sub}</div>}
      {open && help && <p className="mt-2 whitespace-pre-line border-t border-border pt-2 text-xs leading-relaxed text-muted">{help}</p>}
    </div>
  );
}

// 결과 칸마다 (?) 를 누르면 나오는 설명
function helpTexts(s: SingleResult["summary"], b: SingleResult["baseline"], spec: Spec) {
  const c = spec.costs;
  const cost = (c.buy_fee_pct + c.sell_fee_pct + c.sell_tax_pct + c.slippage_pct).toFixed(2);
  const tgt = typeof spec.exit.target_pct === "number" ? spec.exit.target_pct : null;
  const days = spec.exit.max_days;
  return {
    success: tgt != null
      ? `매수가 체결된 거래 중 보유기간(${days}거래일) 안에 목표가(+${tgt}%)에 닿은 비율입니다. ${spec.exit.target_basis === "high" ? "고가 기준이라 장중에 한 번이라도 닿으면 성공입니다." : "종가 기준이라 종가가 목표 이상이어야 성공입니다."}
'기준'은 같은 매수·청산 규칙으로 아무 날이나 샀을 때의 성공률(${pct(b.success_rate)})입니다. 주식은 원래 일정 확률로 목표에 닿기 때문에, 성공률 자체보다 기준과의 차이(%p)가 이 조건의 실력입니다.`
      : "목표가 없어서, 결과가 확정된 거래 중 비용을 빼고 수익이 난 비율입니다. '기준'은 아무 날이나 샀을 때의 같은 비율입니다.",
    complete: `신호가 ${num(s.n_signals)}번 났고, 그중 매수가 실제로 체결된 건 ${pct(s.fill_rate)}입니다 (지정가면 가격에 닿아야 체결).
체결된 거래 중 결과가 정해진 것만 셉니다. 최근 신호처럼 보유기간이 아직 안 끝난 거래(${num(s.n_open)}건)는 빠집니다. 많을수록 믿을 만하고 100건 이상을 권장합니다.`,
    ret: `거래 한 번당 평균 수익률입니다. 매수 수수료 ${c.buy_fee_pct}%, 매도 수수료 ${c.sell_fee_pct}%, 세금 ${c.sell_tax_pct}%${c.slippage_pct ? `, 슬리피지 ${c.slippage_pct}%` : ""} (합계 약 ${cost}%)를 뺀 실제로 손에 쥐는 수익입니다.
성공한 거래는 목표 수익에서 비용을 뺀 값, 실패한 거래는 손절가나 보유기간 마지막 날 종가에 판 값입니다.
중앙값은 거래를 수익률 순으로 줄 세운 가운데 값입니다. 중앙값은 높은데 평균이 낮으면 '대부분 조금 벌지만 가끔 크게 잃는' 구조라는 뜻입니다.`,
    uncertain: `일봉은 하루의 시가·고가·저가·종가만 있어서 장중 순서를 모릅니다. 예를 들어 매수가 체결된 날의 고가가 매수 전이었는지 후였는지, 같은 날 목표가와 손절가 중 어느 쪽에 먼저 닿았는지 알 수 없습니다.
이런 거래는 기본 통계에서 불리하게(실패로) 계산합니다. 유리하게 보면 성공률은 ${pct(s.success_rate_opt)}이고, 실제는 그 사이에 있다고 보면 됩니다.`,
    mfe: `매수한 뒤 청산할 때까지, 매수가 대비 가장 많이 올랐을 때(고가 기준)와 가장 많이 빠졌을 때(저가 기준)의 평균입니다.
최고가 목표보다 훨씬 크면 목표를 높여 볼 만하고, 최저가 깊으면 중간 손실을 견뎌야 한다는 뜻이라 손절 규칙을 검토할 이유가 됩니다.`,
    days: `성공한 거래가 매수 후 평균 며칠(거래일) 만에 목표가에 닿았는지입니다.
아래 숫자는 거래가 끝난 이유별 건수입니다. 목표: 목표가 도달, 손절: 손절가 도달, 추적: 최고가 대비 추적손절, 조건: 청산 조건식 만족 후 다음 날 시가 매도, 만료: 보유기간(${days}일)이 끝나 마지막 날 종가에 정리.`,
  };
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

const OUTCOME_FILTERS = ["전체", "목표도달", "손절", "추적손절", "조건청산", "기간만료", "미체결", "불확실"] as const;

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
  const closed = useCallback(() => setOpen(null), []);
  const back = useBackClose(!!open, closed);
  const openChart = (t: Trade) => {
    back.push();
    setOpen(t);
  };
  const closeChart = back.close;
  const [copied, setCopied] = useState(false);
  const s = r.summary;
  const b = r.baseline;
  const cur = r.dataset.currency ?? "KRW";

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
  const h = helpTexts(s, b, r.spec);

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
          help={h.success}
          label={hasTarget ? "성공률 (목표 도달)" : "수익 확률"}
          value={pct(s.success_rate)}
          sub={`기준 ${pct(b.success_rate)} · ${s.edge_pp !== undefined ? (s.edge_pp > 0 ? "+" : "") + s.edge_pp + "%p" : "–"}`}
        />
        <Tile help={h.complete} label="결과 확정 건수" value={num(s.n_complete)} sub={`신호 ${num(s.n_signals)} · 체결률 ${pct(s.fill_rate, 0)}`} />
        <Tile
          help={h.ret}
          label="평균 수익률 (비용 차감)"
          value={pct(s.avg_ret, 2, true)}
          cls={retClass(s.avg_ret)}
          sub={`중앙값 ${pct(s.median_ret, 2, true)}`}
        />
        <Tile help={h.uncertain} label="불확실 (일봉 한계)" value={num(s.n_uncertain)} sub={`낙관적 성공률 ${pct(s.success_rate_opt)}`} />
        <Tile help={h.mfe} label="보유 중 평균 최고/최저" value={`${pct(s.avg_mfe, 1, true)}`} sub={`최저 ${pct(s.avg_mae, 1, true)}`} />
        <Tile
          help={h.days}
          label="목표까지 평균"
          value={s.avg_days_to_target !== null ? `${s.avg_days_to_target}일` : "–"}
          sub={[
            `목표 ${s.n_target}`,
            `손절 ${s.n_stop}`,
            s.n_trail ? `추적 ${s.n_trail}` : "",
            s.n_signal_exit ? `조건 ${s.n_signal_exit}` : "",
            `만료 ${s.n_timeout}`,
          ]
            .filter(Boolean)
            .join(" · ")}
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
        <p className="mt-2 text-[11px] leading-relaxed text-faint">
          막대의 세로선은 아무 날이나 샀을 때의 성공률(기준)입니다. 해마다 막대가 세로선보다 오른쪽이고 평균이 플러스면 꾸준한 전략,
          특정 해만 좋거나 기준선 근처를 오가면 시장 분위기에 기댄 결과일 수 있습니다.
        </p>
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="font-semibold">사례 {num(trades.length)}건</h3>
          <button type="button" onClick={copy} className="rounded-lg border border-border bg-card px-3 py-1 text-xs">
            {copied ? "복사됨 ✓" : "결과 요약 복사"}
          </button>
        </div>
        <div className="mb-2 flex gap-1 overflow-x-auto text-xs">
          {OUTCOME_FILTERS.filter(
            (f) => (f !== "추적손절" && f !== "조건청산") || r.trades.some((t) => t.outcome === f),
          ).map((f) => (
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
              <button type="button" onClick={() => openChart(t)} className="flex w-full items-center gap-3 px-3 py-2.5 text-left">
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
                  <div className="text-xs text-faint">{t.fill_price ? price(t.fill_price, cur) : "미체결"}</div>
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
      {open && (
        <TradeChart trade={open} spec={r.spec} dataset={r.dataset.name} currency={cur} onClose={closeChart} />
      )}
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
  runSpec,
  onPick,
  onBack,
  onLoadText,
}: {
  result: RunResult;
  detail: SingleResult | null;
  runSpec: Spec;
  onPick: (s: Spec) => void;
  onBack: () => void;
  onLoadText: (text: string) => void;
}) {
  const spec = detail ? detail.spec : result.kind === "single" ? result.spec : runSpec;
  let body;
  if (detail) body = <Single r={detail} onBack={result.kind === "sweep" ? onBack : undefined} />;
  else if (result.kind === "sweep") body = <Sweep r={result} onPick={onPick} />;
  else body = <Single r={result} />;
  return (
    <div className="space-y-4">
      <AIExplain key={JSON.stringify(spec)} spec={spec} dataset={result.dataset.name} onLoad={onLoadText} />
      {body}
    </div>
  );
}
