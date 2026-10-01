"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { joinAnd, parseNumList, showNumList, splitTopLevelAnd } from "@/lib/format";
import type { DatasetInfo, Spec } from "@/lib/types";
import AIAsk from "./AIAsk";

export const DEFAULT_SPEC: Spec = {
  name: "",
  signal: "거래량 >= 거래량(1) * 5 AND 등락률 >= 12",
  entry: { type: "limit", price: "(시가 + 종가) / 2", valid_days: 5 },
  exit: { target_pct: 3, target_basis: "high", stop_pct: null, max_days: 10 },
  universe: {
    markets: ["KOSPI", "KOSDAQ"],
    start: "2010-01-01",
    end: null,
    exclude_spac: true,
    exclude_preferred: true,
  },
  costs: { buy_fee_pct: 0.015, sell_fee_pct: 0.015, sell_tax_pct: 0.2, slippage_pct: 0 },
  dedupe_days: 0,
  fx_krw: false,
};

const TEMPLATES = [
  { label: "거래량 급증", expr: "거래량 >= 거래량(1) * 5" },
  { label: "급등", expr: "등락률 >= 12" },
  { label: "20일선 위", expr: "종가 > 이평(종가, 20)" },
  { label: "거래대금", expr: "거래대금 >= 100억" },
  { label: "골든크로스", expr: "상향돌파(이평(종가, 5), 이평(종가, 20))" },
  { label: "신고가 돌파", expr: "종가 > 이전(최고(고가, 60), 1)" },
  { label: "RSI 과매도", expr: "rsi(14) < 30" },
  { label: "20일 수익률", expr: "수익률(20) >= 30" },
  { label: "가격대", expr: "종가 >= 1000" },
];

const MARKET_LABEL: Record<string, string> = {
  KOSPI: "코스피",
  KOSDAQ: "코스닥",
  NASDAQ: "나스닥",
  NYSE: "뉴욕",
  AMEX: "아멕스",
};

function ExprInput({ value, onChange, onRemove }: { value: string; onChange: (v: string) => void; onRemove?: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (!value.trim()) return;
    // [10, 12] 같은 비교값 표기는 검사 전에 첫 값으로 바꿔서 확인
    const probe = value.replace(/\[\s*(-?[\d.]+)[^\]]*\]/g, "$1");
    timer.current = setTimeout(() => {
      api<{ ok: boolean; error?: string }>("/api/check-expr", { body: { expr: probe } })
        .then((r) => setErr(r.ok ? null : (r.error ?? "오류")))
        .catch(() => setErr(null));
    }, 500);
  }, [value]);
  return (
    <div>
      <div className="flex gap-2">
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="예: 등락률 >= 12"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          className={err ? "!border-critical" : ""}
        />
        {onRemove && (
          <button type="button" onClick={onRemove} className="px-3 text-faint" aria-label="조건 삭제">
            ✕
          </button>
        )}
      </div>
      {err && value.trim() && <p className="mt-1 text-xs text-critical">⚠ {err}</p>}
    </div>
  );
}

function NumField({
  label,
  value,
  onChange,
  suffix,
  hint,
  allowEmpty,
}: {
  label: string;
  value: number | number[] | null;
  onChange: (v: number | number[] | null) => void;
  suffix?: string;
  hint?: string;
  allowEmpty?: boolean;
}) {
  const [text, setText] = useState(showNumList(value));
  const [bad, setBad] = useState(false);
  const [prev, setPrev] = useState(value);
  // 바깥에서 값이 바뀌면(불러오기 등) 입력칸도 맞춘다
  if (prev !== value) {
    setPrev(value);
    const shown = showNumList(value);
    if (JSON.stringify(parseNumList(text)) !== JSON.stringify(value)) setText(shown);
  }
  return (
    <label className="block">
      <span className="text-sm text-muted">{label}</span>
      <div className="mt-1 flex items-center gap-2">
        <input
          inputMode="decimal"
          value={text}
          placeholder={allowEmpty ? "없음" : ""}
          onChange={(e) => {
            setText(e.target.value);
            const v = parseNumList(e.target.value);
            const ok = !(typeof v === "number" && Number.isNaN(v)) && (allowEmpty || v !== null);
            setBad(!ok);
            if (ok) onChange(v);
          }}
          className={bad ? "!border-critical" : ""}
        />
        {suffix && <span className="shrink-0 text-sm text-muted">{suffix}</span>}
      </div>
      {hint && <span className="mt-1 block text-xs text-faint">{hint}</span>}
    </label>
  );
}

export default function Editor({
  spec,
  setSpec,
  onRun,
  dataset,
  running,
  onSave,
}: {
  spec: Spec;
  setSpec: (s: Spec) => void;
  onRun: (s: Spec) => void;
  dataset: DatasetInfo | undefined;
  running: boolean;
  onSave: (s: Spec) => void;
}) {
  const [mode, setMode] = useState<"build" | "text" | "ai">("build");
  const [rows, setRows] = useState<string[]>(() => splitTopLevelAnd(spec.signal));
  const [text, setText] = useState("");
  const [textErr, setTextErr] = useState<string | null>(null);
  const [showCosts, setShowCosts] = useState(false);
  const lastSignal = useRef(spec.signal);

  // 외부에서 spec 이 바뀌면(불러오기 등) 조건 줄을 다시 나눈다.
  useEffect(() => {
    if (spec.signal !== lastSignal.current) {
      setRows(splitTopLevelAnd(spec.signal));
      lastSignal.current = spec.signal;
    }
  }, [spec.signal]);

  const update = (patch: Partial<Spec>) => setSpec({ ...spec, ...patch });
  const setRowsAndSignal = (r: string[]) => {
    setRows(r);
    const sig = joinAnd(r);
    lastSignal.current = sig;
    update({ signal: sig });
  };

  const switchMode = async (m: "build" | "text" | "ai") => {
    if (m === "text" && mode !== "text") {
      try {
        const r = await api<{ text: string }>("/api/to-text", { body: spec });
        setText(r.text);
        setTextErr(null);
      } catch (e) {
        setTextErr((e as Error).message);
      }
    }
    if (m !== "text" && mode === "text") {
      const ok = await applyText();
      if (!ok) return;
    }
    setMode(m);
  };

  const applyText = async (): Promise<Spec | null> => {
    try {
      const r = await api<{ spec: Spec }>("/api/parse", { body: { text } });
      setSpec(r.spec);
      setTextErr(null);
      return r.spec;
    } catch (e) {
      setTextErr((e as Error).message);
      return null;
    }
  };

  const run = async () => {
    if (mode === "text") {
      const s = await applyText();
      if (s) onRun(s);
    } else {
      onRun(spec);
    }
  };

  const save = async () => {
    if (mode === "text") {
      const s = await applyText();
      if (s) onSave(s);
    } else onSave(spec);
  };

  const tab = (m: "build" | "text" | "ai", label: string) => (
    <button
      type="button"
      onClick={() => switchMode(m)}
      className={`flex-1 rounded-md py-2 text-sm font-medium ${mode === m ? "bg-card text-foreground shadow-sm" : "text-muted"}`}
    >
      {label}
    </button>
  );

  return (
    <section className="space-y-4">
      <div className="flex gap-1 rounded-lg bg-border/60 p-1">
        {tab("build", "조립")}
        {tab("text", "조건식")}
        {tab("ai", "AI 질문")}
      </div>

      {/* 대화 내용이 유지되도록 탭을 옮겨도 숨기기만 한다 */}
      <div className={mode === "ai" ? "" : "hidden"}>
        <AIAsk
          dataset={dataset?.name ?? "kr"}
          onApply={(s) => {
            setSpec(s);
            setMode("build");
          }}
          onRun={(s) => {
            setSpec(s);
            onRun(s);
          }}
        />
      </div>

      {mode === "text" && (
        <div className="space-y-2">
          <textarea
            rows={18}
            value={text}
            onChange={(e) => setText(e.target.value)}
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
          />
          {textErr && <p className="whitespace-pre-wrap text-sm text-critical">⚠ {textErr}</p>}
          <p className="text-xs text-faint">숫자 자리에 [10, 12, 15] 처럼 쓰면 각 값을 모두 돌려서 비교합니다.</p>
        </div>
      )}

      {mode === "build" && (
        <div className="space-y-4">
          <div className="rounded-xl border border-border bg-card p-4">
            <h3 className="font-semibold">신호 조건</h3>
            <p className="mb-3 text-xs text-faint">모든 줄을 동시에 만족하는 날(AND)이 신호일입니다.</p>
            <div className="space-y-2">
              {rows.map((r, i) => (
                <ExprInput
                  key={i}
                  value={r}
                  onChange={(v) => setRowsAndSignal(rows.map((x, j) => (j === i ? v : x)))}
                  onRemove={rows.length > 1 ? () => setRowsAndSignal(rows.filter((_, j) => j !== i)) : undefined}
                />
              ))}
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {TEMPLATES.map((t) => (
                <button
                  key={t.label}
                  type="button"
                  onClick={() => setRowsAndSignal([...rows.filter((r) => r.trim()), t.expr])}
                  className="rounded-full border border-border px-2.5 py-1 text-xs text-muted"
                >
                  + {t.label}
                </button>
              ))}
            </div>
          </div>

          <div className="rounded-xl border border-border bg-card p-4">
            <h3 className="mb-3 font-semibold">매수</h3>
            <select
              value={spec.entry.type}
              onChange={(e) => update({ entry: { ...spec.entry, type: e.target.value as Spec["entry"]["type"] } })}
            >
              <option value="limit">지정가 (다음 날부터 주문)</option>
              <option value="next_open">다음 날 시가</option>
              <option value="close">신호일 종가</option>
            </select>
            {spec.entry.type === "limit" && (
              <div className="mt-3 space-y-3">
                <label className="block">
                  <span className="text-sm text-muted">지정가 (신호일 기준 식)</span>
                  <div className="mt-1">
                    <ExprInput value={spec.entry.price} onChange={(v) => update({ entry: { ...spec.entry, price: v } })} />
                  </div>
                </label>
                <NumField
                  label="주문 유효기간"
                  suffix="거래일"
                  value={spec.entry.valid_days}
                  onChange={(v) => update({ entry: { ...spec.entry, valid_days: (v ?? 1) as number } })}
                  hint="이 기간 안에 저가가 지정가 이하로 내려오면 체결"
                />
              </div>
            )}
          </div>

          <div className="rounded-xl border border-border bg-card p-4">
            <h3 className="mb-3 font-semibold">청산 · 성공 기준</h3>
            <div className="grid grid-cols-2 gap-3">
              <NumField
                label="목표 수익률"
                suffix="%"
                allowEmpty
                value={spec.exit.target_pct}
                onChange={(v) => update({ exit: { ...spec.exit, target_pct: v } })}
              />
              <label className="block">
                <span className="text-sm text-muted">목표 판정 기준</span>
                <select
                  className="mt-1"
                  value={spec.exit.target_basis}
                  onChange={(e) =>
                    update({ exit: { ...spec.exit, target_basis: e.target.value as "high" | "close" } })
                  }
                >
                  <option value="high">고가 (한 번이라도 닿으면)</option>
                  <option value="close">종가</option>
                </select>
              </label>
              <NumField
                label="손절"
                suffix="%"
                allowEmpty
                value={spec.exit.stop_pct}
                onChange={(v) => update({ exit: { ...spec.exit, stop_pct: v } })}
                hint="비우면 손절 없음"
              />
              <NumField
                label="보유기간"
                suffix="거래일"
                value={spec.exit.max_days}
                onChange={(v) => update({ exit: { ...spec.exit, max_days: (v ?? 10) as number } })}
                hint="끝나면 종가 청산"
              />
            </div>
          </div>

          <div className="rounded-xl border border-border bg-card p-4">
            <h3 className="mb-3 font-semibold">대상</h3>
            <div className="flex flex-wrap gap-4">
              {(dataset?.markets ?? ["KOSPI", "KOSDAQ"]).map((code) => (
                <label key={code} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={spec.universe.markets.includes(code)}
                    onChange={(e) => {
                      const ms = e.target.checked
                        ? [...spec.universe.markets, code]
                        : spec.universe.markets.filter((x) => x !== code);
                      update({ universe: { ...spec.universe, markets: ms } });
                    }}
                  />
                  {MARKET_LABEL[code] ?? code}
                </label>
              ))}
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={spec.universe.exclude_spac}
                  onChange={(e) => update({ universe: { ...spec.universe, exclude_spac: e.target.checked } })}
                />
                스팩 제외
              </label>
              {dataset?.currency !== "USD" && (
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={spec.universe.exclude_preferred}
                    onChange={(e) => update({ universe: { ...spec.universe, exclude_preferred: e.target.checked } })}
                  />
                  우선주 제외
                </label>
              )}
              {dataset?.currency === "USD" && (
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={spec.fx_krw}
                    onChange={(e) => update({ fx_krw: e.target.checked })}
                  />
                  원화 기준 수익률 (환율 반영)
                </label>
              )}
            </div>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-sm text-muted">시작일</span>
                <input
                  type="date"
                  className="mt-1"
                  value={spec.universe.start}
                  onChange={(e) => update({ universe: { ...spec.universe, start: e.target.value } })}
                />
              </label>
              <label className="block">
                <span className="text-sm text-muted">종료일 (비우면 오늘)</span>
                <input
                  type="date"
                  className="mt-1"
                  value={spec.universe.end ?? ""}
                  onChange={(e) => update({ universe: { ...spec.universe, end: e.target.value || null } })}
                />
              </label>
            </div>
          </div>

          <div className="rounded-xl border border-border bg-card p-4">
            <button type="button" onClick={() => setShowCosts(!showCosts)} className="flex w-full justify-between">
              <h3 className="font-semibold">비용 · 옵션</h3>
              <span className="text-sm text-muted">
                {showCosts ? "접기" : `수수료 ${spec.costs.buy_fee_pct}% · 세금 ${spec.costs.sell_tax_pct}%`}
              </span>
            </button>
            {showCosts && (
              <div className="mt-3 grid grid-cols-2 gap-3">
                {(
                  [
                    ["buy_fee_pct", "매수 수수료"],
                    ["sell_fee_pct", "매도 수수료"],
                    ["sell_tax_pct", "매도 세금"],
                    ["slippage_pct", "슬리피지"],
                  ] as const
                ).map(([k, label]) => (
                  <NumField
                    key={k}
                    label={label}
                    suffix="%"
                    value={spec.costs[k]}
                    onChange={(v) => update({ costs: { ...spec.costs, [k]: typeof v === "number" ? v : 0 } })}
                  />
                ))}
                <NumField
                  label="중복 신호 제외"
                  suffix="거래일"
                  value={spec.dedupe_days}
                  onChange={(v) => update({ dedupe_days: typeof v === "number" ? v : 0 })}
                  hint="같은 종목에서 n일 안에 다시 나온 신호 제외 (0 = 모두 포함)"
                />
              </div>
            )}
          </div>
          <p className="text-xs text-faint">숫자 칸에 3, 5, 7 처럼 쉼표로 여러 값을 넣으면 한 번에 비교표가 나옵니다.</p>
        </div>
      )}

      {mode !== "ai" && (
        <div className="sticky bottom-[68px] z-10 flex gap-2 bg-background/90 py-2 backdrop-blur">
          <button
            type="button"
            onClick={run}
            disabled={running}
            className="flex-1 rounded-xl bg-accent py-3 font-semibold text-white disabled:opacity-60"
          >
            {running ? "계산 중…" : "검증 실행"}
          </button>
          <button type="button" onClick={save} className="rounded-xl border border-border bg-card px-4 text-sm">
            저장
          </button>
        </div>
      )}
    </section>
  );
}
