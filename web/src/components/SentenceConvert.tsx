"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { convert, type SentenceResult } from "@/lib/sentence";
import type { Spec } from "@/lib/types";

const EXAMPLES = [
  "거래량이 전일 대비 500% 이상 상승하고 주가가 12% 이상 상승한 날 시가와 종가의 중간값에 매수해서 3% 이상 상승",
  "20일선 위에 있고 5일선이 20일선을 골든크로스 하면서 거래대금 50억 이상인 날 다음날 시가에 사서 5% 오르면 팔고 손절 3%, 10일 보유",
  "52주 신고가를 경신하고 거래량이 20일 평균의 3배 이상",
  "RSI 30 이하이고 5일 동안 20% 이상 하락한 시가총액 1000억 이상",
];

// "[신호] … [매수] …" 글을 구역별로 나눈다
function sections(text: string): Map<string, string[]> {
  const m = new Map<string, string[]>();
  let cur = "";
  for (const line of text.split("\n")) {
    const h = line.trim().match(/^\[(.+)\]$/);
    if (h) {
      cur = h[1];
      m.set(cur, []);
    } else if (cur) m.get(cur)!.push(line);
  }
  return m;
}

// 문장에서 나온 구역만 현재 설정에 덮어쓴다 (나머지는 지금 설정 유지)
export function mergeSpecText(current: string, patch: string): string {
  const cur = sections(current);
  const add = sections(patch);
  const names = ["신호", "매수", "청산", "대상", "비용", "옵션"];
  for (const k of cur.keys()) if (!names.includes(k)) names.push(k);
  const out: string[] = [];
  for (const n of names) {
    const body = add.get(n) ?? cur.get(n);
    if (!body) continue;
    const lines = [...body];
    while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
    if (n === "청산" && add.has(n) && cur.has(n)) {
      // 청산은 문장에 나온 항목만 바꾸고 나머지 값은 유지
      const keyOf = (l: string) => l.split("=")[0].trim();
      const given = new Map(lines.map((l) => [keyOf(l), l]));
      const kept = cur.get(n)!.filter((l) => l.trim() && !given.has(keyOf(l)));
      out.push(`[${n}]`, ...kept, ...lines, "");
    } else out.push(`[${n}]`, ...lines, "");
  }
  return out.join("\n");
}

export default function SentenceConvert({
  spec,
  onApply,
  onRun,
  onScan,
}: {
  spec: Spec;
  onApply: (s: Spec) => void;
  onRun: (s: Spec) => void;
  onScan: (s: Spec) => void;
}) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<SentenceResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const doConvert = (text = q) => {
    setErr(null);
    setRes(convert(text));
  };

  const build = async (): Promise<Spec | null> => {
    if (!res?.spec) return null;
    setBusy(true);
    try {
      const cur = await api<{ text: string }>("/api/to-text", { body: spec });
      const r = await api<{ spec: Spec }>("/api/parse", { body: { text: mergeSpecText(cur.text, res.spec) } });
      setErr(null);
      return r.spec;
    } catch (e) {
      setErr((e as Error).message);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const rest = res?.rest;
  const extra: string[] = [];
  if (rest) {
    if (rest.buy) extra.push(`매수: ${rest.buy.desc}${rest.buy.valid ? ` (${rest.buy.valid}일 동안 주문)` : ""}`);
    const ex = rest.exit;
    const ep: string[] = [];
    if (ex.target != null) ep.push(`목표 +${ex.target}%${ex.basis ? " (종가 기준)" : ""}`);
    if (ex.stop != null) ep.push(`손절 -${ex.stop}%`);
    if (ex.days != null) ep.push(`최대 ${ex.days}거래일 보유`);
    if (ep.length) extra.push("청산: " + ep.join(", "));
    if (rest.universe.markets) extra.push(`대상: ${rest.universe.markets}만`);
    if (rest.universe.start) extra.push(`기간: ${rest.universe.start}부터`);
    extra.push(...rest.notes);
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">
        매매 아이디어를 문장으로 쓰면 정해진 규칙으로 조건식을 만들어 줍니다. AI를 쓰지 않아 <b>무료</b>입니다. 적지 않은 항목은 지금
        설정을 그대로 씁니다.
      </p>
      <textarea
        rows={3}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            doConvert();
          }
        }}
        placeholder="예: 거래량이 5배 이상이고 12% 이상 오른 날 다음날 시가에 사서 5% 오르면 팔기"
        className="!font-sans"
      />
      <button
        type="button"
        onClick={() => doConvert()}
        disabled={!q.trim()}
        className="w-full rounded-xl bg-accent py-3 font-semibold text-white disabled:opacity-50"
      >
        조건식으로 바꾸기
      </button>
      {!res && (
        <div className="flex flex-wrap gap-1.5">
          {EXAMPLES.map((x) => (
            <button
              key={x}
              type="button"
              onClick={() => {
                setQ(x);
                doConvert(x);
              }}
              className="rounded-full border border-border px-2.5 py-1 text-left text-xs"
            >
              {x.length > 40 ? x.slice(0, 39) + "…" : x}
            </button>
          ))}
        </div>
      )}

      {res && (
        <div className="space-y-2 rounded-xl border border-border bg-card p-3 text-sm">
          {res.items.length === 0 && <p className="text-muted">신호 조건을 찾지 못했어요.</p>}
          {res.items.map((it, i) => (
            <div key={i} className="flex gap-2 rounded-lg bg-background p-2">
              <span className={`w-10 shrink-0 text-xs font-semibold ${it.op === "OR" ? "text-accent" : "text-muted"}`}>
                {it.op === "OR" ? "또는" : it.op ? "그리고" : "조건"}
              </span>
              <div className="min-w-0 space-y-0.5">
                <div className="text-xs text-muted">{it.src}</div>
                {it.exprs && <div className="break-words font-mono text-[13px]">{it.exprs.join("  그리고  ")}</div>}
                {it.desc && <div className="text-xs">{it.desc}</div>}
                {it.note && <div className="rounded bg-warning/10 px-1.5 py-0.5 text-xs text-warning">{it.note}</div>}
                {it.error && <div className="text-xs text-critical">{it.error}</div>}
                {it.unknown && (
                  <div className="rounded bg-warning/10 px-1.5 py-0.5 text-xs text-warning">
                    이 부분은 알아듣지 못했어요. 도움말 탭의 조건식 설명을 보고 조립 화면에서 직접 넣어 주세요.
                  </div>
                )}
              </div>
            </div>
          ))}
          {extra.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-5 text-muted">
              {extra.map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ul>
          )}
          {res.spec && <pre className="overflow-x-auto rounded-lg bg-background p-2 text-xs leading-relaxed">{res.spec}</pre>}
          {err && <p className="whitespace-pre-wrap text-critical">⚠ {err}</p>}
          {res.spec && (
            <div className="flex gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={async () => {
                  const s = await build();
                  if (s) onRun(s);
                }}
                className="flex-1 rounded-xl bg-accent py-2.5 font-semibold text-white disabled:opacity-50"
              >
                이대로 검증
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={async () => {
                  const s = await build();
                  if (s) onScan(s);
                }}
                className="rounded-xl border border-accent px-3 text-sm font-semibold text-accent"
              >
                오늘 신호
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={async () => {
                  const s = await build();
                  if (s) onApply(s);
                }}
                className="rounded-xl border border-border px-3 text-sm"
              >
                수정
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
