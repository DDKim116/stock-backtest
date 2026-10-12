"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { convert, type SentenceResult } from "@/lib/sentence";
import type { Spec } from "@/lib/types";
import { mergeSpecText } from "./SentenceConvert";

// 탐색 화면에서 바로 출발점 조건을 넣는 칸 (문장 → 규칙 변환, 또는 조건식 직접 입력)
export default function StartEditor({ spec, onApply }: { spec: Spec; onApply: (s: Spec) => void }) {
  const [mode, setMode] = useState<"none" | "sentence" | "text">("none");
  const [q, setQ] = useState("");
  const [conv, setConv] = useState<SentenceResult | null>(null);
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const open = async (m: "sentence" | "text") => {
    setErr(null);
    if (mode === m) return setMode("none");
    setMode(m);
    if (m === "text") {
      try {
        setText((await api<{ text: string }>("/api/to-text", { body: spec })).text);
      } catch (e) {
        setErr((e as Error).message);
      }
    }
  };

  const apply = async (fullText: string) => {
    setBusy(true);
    try {
      const r = await api<{ spec: Spec }>("/api/parse", { body: { text: fullText } });
      onApply(r.spec);
      setErr(null);
      setMode("none");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const applySentence = async () => {
    if (!conv?.spec) return;
    try {
      const cur = await api<{ text: string }>("/api/to-text", { body: spec });
      await apply(mergeSpecText(cur.text, conv.spec));
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  const tabCls = (m: string) =>
    `flex-1 rounded-lg border py-2 text-xs font-medium ${mode === m ? "border-accent text-accent" : "border-border text-muted"}`;

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <button type="button" onClick={() => open("sentence")} className={tabCls("sentence")}>
          문장으로 바꾸기 (무료)
        </button>
        <button type="button" onClick={() => open("text")} className={tabCls("text")}>
          조건식 직접 고치기
        </button>
      </div>

      {mode === "sentence" && (
        <div className="space-y-2">
          <textarea
            rows={3}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                setConv(convert(q));
              }
            }}
            placeholder="예: 거래량이 20일 평균의 3배 이상이고 52주 신고가, 다음날 시가 매수"
            className="!font-sans"
          />
          <button
            type="button"
            onClick={() => setConv(convert(q))}
            disabled={!q.trim()}
            className="w-full rounded-lg border border-accent py-2 text-sm font-semibold text-accent disabled:opacity-50"
          >
            조건식으로 바꾸기
          </button>
          {conv && (
            <div className="space-y-1 rounded-lg bg-background p-2 text-xs">
              {conv.items.map((it, i) => (
                <div key={i}>
                  <span className="text-muted">{it.src} → </span>
                  {it.exprs ? (
                    <span className="font-mono">{it.exprs.join(" 그리고 ")}</span>
                  ) : (
                    <span className={it.error ? "text-critical" : "text-warning"}>{it.error ?? "알아듣지 못함"}</span>
                  )}
                  {it.note && <div className="text-warning">{it.note}</div>}
                </div>
              ))}
              {conv.spec ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={applySentence}
                  className="mt-1 w-full rounded-lg bg-accent py-2 text-sm font-semibold text-white disabled:opacity-50"
                >
                  이 조건을 출발점으로
                </button>
              ) : (
                <p className="text-muted">신호 조건을 찾지 못했어요. 표현을 바꾸거나 &apos;조건식 직접 고치기&apos;를 쓰세요.</p>
              )}
            </div>
          )}
        </div>
      )}

      {mode === "text" && (
        <div className="space-y-2">
          <textarea
            rows={14}
            value={text}
            onChange={(e) => setText(e.target.value)}
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            className="text-xs"
          />
          <p className="text-[11px] text-faint">
            숫자 자리에 <code>[8, 10, 12]</code>처럼 여러 값을 쓰면 자동 범위 대신 그 값들만 탐색합니다. 예: <code>등락률 &gt;= [8, 10, 12]</code>,{" "}
            <code>목표 = [3, 5, 8]</code>, <code>손절 = [5, 7]</code>
          </p>
          <button
            type="button"
            disabled={busy}
            onClick={() => apply(text)}
            className="w-full rounded-lg bg-accent py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            적용
          </button>
        </div>
      )}
      {err && <p className="whitespace-pre-wrap text-xs text-critical">⚠ {err}</p>}
    </div>
  );
}
