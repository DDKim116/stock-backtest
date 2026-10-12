"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { JournalReview as Review } from "@/lib/types";
import { useAIStatus } from "./AIAsk";

export default function JournalReview({ ds }: { ds: string }) {
  const { status, reload } = useAIStatus();
  const [res, setRes] = useState<Review | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!status?.enabled) return null;

  const ask = async () => {
    setBusy(true);
    setErr(null);
    try {
      setRes(await api<Review>("/api/ai/journal-review", { body: { dataset: ds } }));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
      reload();
    }
  };

  const est = status.estimate_krw.journal ?? status.estimate_krw.explain;
  if (!res)
    return (
      <div>
        <button
          type="button"
          onClick={ask}
          disabled={busy}
          className="w-full rounded-xl border border-accent bg-card py-2.5 text-sm font-medium text-accent disabled:opacity-60"
        >
          {busy ? "AI가 매매일지를 읽는 중… (20~60초)" : `AI 매매 복기 (약 ${est.toLocaleString("ko-KR")}원)`}
        </button>
        <p className="mt-1 text-[11px] text-faint">
          청산한 거래(최근 60건)와 보유 종목, 메모를 읽고 반복되는 실수와 다음에 지킬 규칙을 정리합니다.
        </p>
        {err && <p className="mt-2 text-sm text-critical">⚠ {err}</p>}
      </div>
    );

  return (
    <section className="space-y-3 rounded-xl border border-accent/50 bg-card p-4 text-sm">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">AI 매매 복기</h3>
        <button type="button" onClick={() => setRes(null)} className="text-xs text-muted">
          닫기
        </button>
      </div>
      <p className="font-medium">{res.summary}</p>
      {res.good.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-semibold text-accent">잘하고 있는 점</div>
          <ul className="list-disc space-y-1 pl-5">
            {res.good.map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        </div>
      )}
      {res.mistakes.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs font-semibold text-warning">반복되는 실수</div>
          {res.mistakes.map((m, i) => (
            <div key={i} className="space-y-1 rounded-lg bg-background p-2">
              <div className="font-medium">{m.pattern}</div>
              <div className="text-xs text-muted">근거: {m.evidence}</div>
              <div className="text-xs">→ {m.fix}</div>
            </div>
          ))}
        </div>
      )}
      {res.rules.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-semibold text-muted">다음 매매에서 지킬 규칙</div>
          <ol className="list-decimal space-y-1 pl-5">
            {res.rules.map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ol>
        </div>
      )}
      {res.watch.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-semibold text-muted">보유 종목 점검</div>
          <ul className="list-disc space-y-1 pl-5">
            {res.watch.map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="text-right text-[11px] text-faint">
        청산 {res.n_closed}건 기준 · AI 비용 약 {res.usage.cost_krw.toLocaleString("ko-KR")}원
      </div>
    </section>
  );
}
