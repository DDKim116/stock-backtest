"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { ExploreReview as Review, Spec } from "@/lib/types";
import { useAIStatus } from "./AIAsk";

export default function ExploreReview({
  jobId,
  initial,
  onBest,
  onExplore,
  onRun,
}: {
  jobId: string;
  initial?: Review;
  onBest: (rank: number) => void;
  onExplore: (s: Spec) => void;
  onRun: (s: Spec) => void;
}) {
  const { status, reload } = useAIStatus();
  const [res, setRes] = useState<Review | null>(initial ?? null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!status?.enabled && !res) return null;

  const ask = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api<Review>("/api/ai/explore-review", { body: { job_id: jobId } });
      setRes(r);
      if (r.best.rank > 0) onBest(r.best.rank);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
      reload();
    }
  };

  const toSpec = async (text: string): Promise<Spec | null> => {
    try {
      return (await api<{ spec: Spec }>("/api/parse", { body: { text } })).spec;
    } catch (e) {
      setErr((e as Error).message);
      return null;
    }
  };

  if (!res) {
    const est = status?.estimate_krw.review ?? status?.estimate_krw.explain ?? 0;
    return (
      <div>
        <button
          type="button"
          onClick={ask}
          disabled={busy}
          className="w-full rounded-xl border border-accent bg-card py-2.5 text-sm font-medium text-accent disabled:opacity-60"
        >
          {busy ? "AI가 탐색 결과를 검토하는 중… (20~60초)" : `AI 검토 받기 (약 ${est.toLocaleString("ko-KR")}원)`}
        </button>
        <p className="mt-1 text-[11px] text-faint">
          통과한 조합을 비교해 실전 후보를 고르고, 어떤 값이 통했는지와 다음 탐색 아이디어를 알려 줍니다.
        </p>
        {err && <p className="mt-2 text-sm text-critical">⚠ {err}</p>}
      </div>
    );
  }

  return (
    <section className="space-y-3 rounded-xl border border-accent/50 bg-card p-4 text-sm">
      <h3 className="font-semibold">AI 검토</h3>
      <p className="font-medium">{res.verdict}</p>
      {res.best.rank > 0 && (
        <div className="rounded-lg bg-accent-soft p-2">
          <div className="text-xs font-semibold text-accent">추천 후보: {res.best.rank}위</div>
          <p className="mt-0.5">{res.best.why}</p>
        </div>
      )}
      {res.best.rank === 0 && res.best.why && <p className="text-muted">{res.best.why}</p>}
      {res.patterns.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-semibold text-muted">보이는 경향</div>
          <ul className="list-disc space-y-1 pl-5">
            {res.patterns.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </div>
      )}
      {res.risks.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-semibold text-warning">주의할 점</div>
          <ul className="list-disc space-y-1 pl-5">
            {res.risks.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </div>
      )}
      {res.next_ideas.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs font-semibold text-muted">다음 탐색 아이디어</div>
          {res.next_ideas.map((s, i) => (
            <div key={i} className="space-y-2 rounded-lg bg-background p-2">
              <div className="font-medium">{s.title}</div>
              <p className="text-xs text-muted">{s.why}</p>
              <pre className="overflow-x-auto whitespace-pre-wrap text-[11px] leading-relaxed">
                {s.text.split("\n\n").slice(0, 3).join("\n\n")}
              </pre>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={async () => {
                    const sp = await toSpec(s.text);
                    if (sp) onExplore(sp);
                  }}
                  className="flex-1 rounded-lg bg-accent py-2 text-xs font-semibold text-on-accent"
                >
                  이 범위로 탐색 준비
                </button>
                <button
                  type="button"
                  onClick={async () => {
                    const sp = await toSpec(s.text);
                    if (sp) onRun(sp);
                  }}
                  className="rounded-lg border border-border px-3 text-xs"
                >
                  바로 검증
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      {err && <p className="text-critical">⚠ {err}</p>}
      <div className="text-right text-[11px] text-faint">AI 비용 약 {res.usage.cost_krw.toLocaleString("ko-KR")}원</div>
    </section>
  );
}
