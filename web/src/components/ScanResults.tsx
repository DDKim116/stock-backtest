"use client";

import { useCallback, useMemo, useState } from "react";
import { pct, price, retClass } from "@/lib/format";
import type { ScanResult, ScanRow, Trade } from "@/lib/types";
import { useBackClose } from "@/lib/useBackClose";
import TradeChart from "./TradeChart";

const FILTERS: { key: ScanRow["kind"] | "all"; label: string }[] = [
  { key: "all", label: "전체" },
  { key: "pending", label: "주문 대기" },
  { key: "holding", label: "보유 중" },
  { key: "closed", label: "청산됨" },
  { key: "expired", label: "기간 끝남" },
];

const KIND_STYLE: Record<ScanRow["kind"], string> = {
  pending: "bg-accent text-white",
  holding: "bg-accent-soft text-accent",
  closed: "bg-border text-muted",
  expired: "bg-border text-faint",
};

// 거래대금처럼 큰 금액을 짧게
function money(x: number | null, currency: string): string {
  if (x == null) return "–";
  if (currency === "USD") {
    if (x >= 1e9) return `$${(x / 1e9).toFixed(1)}B`;
    if (x >= 1e6) return `$${(x / 1e6).toFixed(0)}M`;
    return `$${Math.round(x / 1e3)}K`;
  }
  if (x >= 1e12) return `${(x / 1e12).toFixed(1)}조`;
  if (x >= 1e8) return `${Math.round(x / 1e8).toLocaleString("ko-KR")}억`;
  return `${Math.round(x / 1e4).toLocaleString("ko-KR")}만`;
}

function asTrade(r: ScanRow): Trade {
  return {
    code: r.code,
    name: r.name,
    market: r.market,
    signal_date: r.signal_date,
    signal_close: r.close,
    limit: r.limit,
    fill_date: r.fill_date,
    fill_price: r.fill_price,
    outcome: r.outcome,
    outcome_opt: r.outcome,
    uncertain: false,
    exit_date: r.exit_date,
    exit_price: r.exit_price,
    days: null,
    ret: r.ret ?? r.ret_now,
    ret_opt: r.ret ?? r.ret_now,
    mfe: r.mfe,
    mae: r.mae,
  };
}

export default function ScanResults({
  r,
  onBacktest,
  onRecord,
}: {
  r: ScanResult;
  onBacktest: () => void;
  onRecord: (row: ScanRow) => void;
}) {
  const cur = r.dataset.currency ?? "KRW";
  const [filter, setFilter] = useState<ScanRow["kind"] | "all">("all");
  const [open, setOpen] = useState<Trade | null>(null);
  const closed = useCallback(() => setOpen(null), []);
  const back = useBackClose(!!open, closed);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: r.rows.length };
    for (const x of r.rows) c[x.kind] = (c[x.kind] ?? 0) + 1;
    return c;
  }, [r.rows]);
  const rows = filter === "all" ? r.rows : r.rows.filter((x) => x.kind === filter);
  const entry = r.spec.entry;

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-border bg-card p-3 text-sm">
        <div className="font-semibold">
          {r.as_of} 기준 · {r.days === 1 ? "이날" : `최근 ${r.days}거래일`} 신호 {r.count.toLocaleString("ko-KR")}종목
        </div>
        <p className="mt-1 text-xs text-muted">
          {entry.type === "limit"
            ? `지정가 주문은 신호 다음 날부터 ${entry.valid_days}거래일 동안 유효해서, 그 기간 안의 신호를 모두 보여 줍니다. '주문 대기'가 지금 주문을 넣을 수 있는 종목이에요.`
            : entry.type === "next_open"
              ? "다음 거래일 시가에 사는 방식이라, 마지막 거래일의 신호를 보여 줍니다."
              : "신호일 종가에 사는 방식이라, 마지막 거래일에 이미 산 것으로 봅니다."}
        </p>
        <p className="mt-1 text-xs text-faint">
          장 마감 후 확정 데이터 기준입니다 (국내는 평일 저녁 6시 40분, 미국은 다음 날 아침 7시 40분 갱신). 장중 실시간 시세는 아닙니다.
        </p>
      </div>

      <div className="flex gap-1 overflow-x-auto text-xs">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            onClick={() => setFilter(f.key)}
            className={`shrink-0 rounded-full border px-2.5 py-1 ${filter === f.key ? "border-accent text-accent" : "border-border text-muted"}`}
          >
            {f.label} {counts[f.key] ?? 0}
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <p className="rounded-xl border border-border bg-card p-4 text-center text-sm text-muted">
          {r.count === 0 ? "조건에 맞는 종목이 없습니다." : "이 분류에 해당하는 종목이 없습니다."}
        </p>
      ) : (
        <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
          {rows.map((x) => (
            <li key={`${x.code}-${x.signal_date}`} className="flex items-stretch">
              <button
                type="button"
                onClick={() => {
                  back.push();
                  setOpen(asTrade(x));
                }}
                className="flex min-w-0 flex-1 items-center gap-3 px-3 py-2.5 text-left"
              >
                <div className="min-w-0 flex-1 space-y-0.5">
                  <div className="truncate text-sm font-medium">
                    {x.name} <span className="text-xs font-normal text-faint">{x.code}</span>
                  </div>
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted">
                    <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${KIND_STYLE[x.kind]}`}>{x.status}</span>
                    <span className="tabular">신호 {x.signal_date.slice(5)}</span>
                    <span className="tabular">대금 {money(x.value, cur)}</span>
                  </div>
                </div>
                <div className="tabular shrink-0 text-right">
                  {x.kind === "pending" && x.limit != null ? (
                    <>
                      <div className="text-sm font-semibold">{price(x.limit, cur)}</div>
                      <div className="text-xs text-faint">지정가</div>
                    </>
                  ) : x.kind === "holding" ? (
                    <>
                      <div className={`text-sm font-semibold ${retClass(x.ret_now)}`}>{pct(x.ret_now, 2, true)}</div>
                      <div className="text-xs text-faint">매수 {price(x.fill_price, cur)}</div>
                    </>
                  ) : x.kind === "closed" ? (
                    <>
                      <div className={`text-sm font-semibold ${retClass(x.ret)}`}>{pct(x.ret, 2, true)}</div>
                      <div className="text-xs text-faint">{x.exit_date?.slice(5)} 매도</div>
                    </>
                  ) : (
                    <>
                      <div className="text-sm">{price(x.close, cur)}</div>
                      <div className={`text-xs ${retClass(x.change)}`}>{pct(x.change, 1, true)}</div>
                    </>
                  )}
                </div>
              </button>
              <button
                type="button"
                onClick={() => onRecord(x)}
                className="shrink-0 border-l border-border px-3 text-xs font-medium text-accent"
                aria-label={`${x.name} 매매 기록`}
              >
                기록
              </button>
            </li>
          ))}
        </ul>
      )}
      {r.truncated && <p className="text-xs text-faint">종목이 많아 500개까지만 표시합니다. 조건을 더 좁혀 보세요.</p>}

      <button
        type="button"
        onClick={onBacktest}
        className="w-full rounded-xl border border-border bg-card py-2.5 text-sm"
      >
        이 조건의 과거 성공률 검증하기
      </button>

      {open && <TradeChart trade={open} spec={r.spec} dataset={r.dataset.name} currency={cur} onClose={back.close} />}
    </div>
  );
}
