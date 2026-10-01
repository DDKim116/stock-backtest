"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { ExplainResult, Spec } from "@/lib/types";
import { useAIStatus } from "./AIAsk";

export default function AIExplain({
  spec,
  dataset,
  onLoad,
}: {
  spec: Spec;
  dataset: string;
  onLoad: (text: string) => void;
}) {
  const { status, reload } = useAIStatus();
  const [res, setRes] = useState<ExplainResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!status?.enabled) return null;

  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      setRes(await api<ExplainResult>("/api/ai/explain", { body: { dataset, spec } }));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
      reload();
    }
  };

  if (!res)
    return (
      <div>
        <button
          type="button"
          onClick={run}
          disabled={busy}
          className="w-full rounded-xl border border-accent bg-card py-2.5 text-sm font-medium text-accent disabled:opacity-60"
        >
          {busy ? "AI가 결과를 읽는 중… (10~40초)" : `AI 해설 받기 (약 ${status.estimate_krw.explain.toLocaleString("ko-KR")}원)`}
        </button>
        {err && <p className="mt-2 text-sm text-critical">⚠ {err}</p>}
      </div>
    );

  return (
    <section className="space-y-3 rounded-xl border border-accent/50 bg-card p-4 text-sm">
      <h3 className="font-semibold">AI 해설</h3>
      <p className="font-medium">{res.verdict}</p>
      <ul className="list-disc space-y-1 pl-5">
        {res.points.map((p, i) => (
          <li key={i}>{p}</li>
        ))}
      </ul>
      {res.risks.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-semibold text-warning">! 주의할 점</div>
          <ul className="list-disc space-y-1 pl-5 text-muted">
            {res.risks.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </div>
      )}
      {res.suggestions.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs font-semibold text-muted">다음에 검증해 볼 만한 조건</div>
          {res.suggestions.map((s, i) => (
            <div key={i} className="rounded-lg bg-background p-3">
              <div className="font-medium">{s.title}</div>
              <div className="text-muted">{s.why}</div>
              <button
                type="button"
                onClick={() => onLoad(s.text)}
                className="mt-2 rounded-lg border border-border bg-card px-3 py-1.5 text-xs"
              >
                이 조건 불러오기 (무료)
              </button>
            </div>
          ))}
        </div>
      )}
      <p className="text-right text-[11px] text-faint">
        AI 비용 약 {res.usage.cost_krw.toLocaleString("ko-KR")}원 · 투자 권유가 아닌 검증 결과 해석입니다
      </p>
    </section>
  );
}
