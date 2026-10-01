"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AIStatus, Spec, TranslateResult } from "@/lib/types";

interface Turn {
  question: string;
  result: TranslateResult | null;
  error: string | null;
}

const EXAMPLES = [
  "거래량이 전일 대비 500% 이상 상승하고 주가가 12% 이상 상승한 날 이후, 그날 시가와 종가의 중간값에 매수했을 경우 3% 이상 상승한 비율은?",
  "20일 이동평균선을 골든크로스한 날 종가에 사서 10일 보유하면 수익률은?",
  "60일 신고가를 돌파하면서 거래대금이 100억 이상인 날 다음날 시가에 사면 5% 오를 확률은?",
];

export function useAIStatus() {
  const [status, setStatus] = useState<AIStatus | null>(null);
  const reload = () =>
    api<AIStatus>("/api/ai/status")
      .then(setStatus)
      .catch(() => setStatus(null));
  useEffect(() => {
    reload();
  }, []);
  return { status, reload };
}

export default function AIAsk({
  dataset,
  onApply,
  onRun,
}: {
  dataset: string;
  onApply: (s: Spec) => void;
  onRun: (s: Spec) => void;
}) {
  const { status, reload } = useAIStatus();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  const lastOk = [...turns].reverse().find((t) => t.result?.ok && t.result.text);

  const ask = async (question: string) => {
    if (!question.trim() || busy) return;
    setBusy(true);
    setQ("");
    const history = turns.map((t) => t.question);
    setTurns((ts) => [...ts, { question, result: null, error: null }]);
    try {
      const r = await api<TranslateResult>("/api/ai/translate", {
        body: { question, current_text: lastOk?.result?.text ?? null, history, dataset },
      });
      setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? { ...t, result: r } : t)));
    } catch (e) {
      setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? { ...t, error: (e as Error).message } : t)));
    } finally {
      setBusy(false);
      reload();
    }
  };

  if (status && !status.enabled) {
    return (
      <div className="rounded-xl border border-border bg-card p-4 text-sm text-muted">
        AI 기능이 꺼져 있습니다. 사용하려면 서버의 <code>.env</code> 에 <code>ANTHROPIC_API_KEY</code> 를 넣고 서버를 재시작하세요.
        조립·조건식 화면은 AI 없이 그대로 쓸 수 있습니다.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {turns.length === 0 && (
        <div className="space-y-2">
          <p className="text-sm text-muted">검증하고 싶은 매매 아이디어를 말하듯이 적어 주세요. AI가 조건식으로 바꿔 드리고, 실행·숫자 수정은 무료입니다.</p>
          {EXAMPLES.map((e) => (
            <button
              key={e}
              type="button"
              onClick={() => setQ(e)}
              className="block w-full rounded-lg border border-border bg-card px-3 py-2 text-left text-sm text-muted"
            >
              {e}
            </button>
          ))}
        </div>
      )}

      {turns.map((t, i) => (
        <div key={i} className="space-y-2">
          <div className="ml-8 rounded-xl bg-accent-soft px-3 py-2 text-sm">{t.question}</div>
          {!t.result && !t.error && <div className="text-sm text-muted">AI가 해석하는 중… (10~40초)</div>}
          {t.error && <div className="rounded-xl border border-border bg-card p-3 text-sm text-critical">⚠ {t.error}</div>}
          {t.result && (
            <div className="space-y-3 rounded-xl border border-border bg-card p-3 text-sm">
              <p>{t.result.reply}</p>
              {t.result.ok && (
                <>
                  {!!t.result.interpretation?.length && (
                    <ul className="list-disc space-y-0.5 pl-5 text-muted">
                      {t.result.interpretation.map((x, j) => (
                        <li key={j}>{x}</li>
                      ))}
                    </ul>
                  )}
                  {!!t.result.assumptions?.length && (
                    <div className="space-y-2">
                      <div className="text-xs font-semibold text-warning">! 애매해서 이렇게 정했어요</div>
                      {t.result.assumptions.map((a, j) => (
                        <div key={j} className="rounded-lg bg-background p-2">
                          <div>
                            <span className="text-muted">{a.item}:</span> {a.chosen}
                          </div>
                          {i === turns.length - 1 && a.alternatives.length > 0 && (
                            <div className="mt-1.5 flex flex-wrap gap-1.5">
                              {a.alternatives.map((alt) => (
                                <button
                                  key={alt}
                                  type="button"
                                  disabled={busy}
                                  onClick={() => ask(alt)}
                                  className="rounded-full border border-border px-2.5 py-1 text-xs"
                                >
                                  {alt}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                      {i === turns.length - 1 && (
                        <p className="text-[11px] text-faint">다른 해석을 누르면 AI가 다시 고칩니다 (유료).</p>
                      )}
                    </div>
                  )}
                  {!!t.result.unsupported?.length && (
                    <div className="space-y-1">
                      <div className="text-xs font-semibold text-critical">✕ 반영하지 못한 부분</div>
                      <ul className="list-disc pl-5 text-muted">
                        {t.result.unsupported.map((x, j) => (
                          <li key={j}>{x}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  <pre className="overflow-x-auto rounded-lg bg-background p-2 text-xs leading-relaxed">{t.result.text}</pre>
                  {i === turns.length - 1 && t.result.spec && (
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => onRun(t.result!.spec!)}
                        className="flex-1 rounded-xl bg-accent py-2.5 font-semibold text-white"
                      >
                        이대로 검증 (무료)
                      </button>
                      <button
                        type="button"
                        onClick={() => onApply(t.result!.spec!)}
                        className="rounded-xl border border-border px-3 text-sm"
                      >
                        조립 화면에서 수정
                      </button>
                    </div>
                  )}
                </>
              )}
              {t.result.usage && (
                <div className="text-right text-[11px] text-faint">
                  AI 비용 약 {t.result.usage.cost_krw.toLocaleString("ko-KR")}원
                </div>
              )}
            </div>
          )}
        </div>
      ))}

      <div className="space-y-2">
        <textarea
          rows={3}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={lastOk ? "고칠 부분을 말해 주세요 (예: 목표를 5%로, 손절 3% 추가)" : "예: 거래량이 5배 늘고 12% 오른 다음 날 시가에 사면 3% 오를 확률은?"}
          className="!font-sans"
        />
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => ask(q)}
            disabled={busy || !q.trim()}
            className="flex-1 rounded-xl bg-accent py-3 font-semibold text-white disabled:opacity-50"
          >
            {busy ? "해석 중…" : lastOk ? "AI에게 수정 요청" : "AI에게 묻기"}
          </button>
          {turns.length > 0 && (
            <button type="button" onClick={() => setTurns([])} className="rounded-xl border border-border px-3 py-3 text-sm">
              새 질문
            </button>
          )}
        </div>
        {status && (
          <p className="text-xs text-faint">
            1회 약 {status.estimate_krw.translate.toLocaleString("ko-KR")}원 · 이번 달 {status.month_krw.toLocaleString("ko-KR")}원 /
            한도 {status.limit_krw.toLocaleString("ko-KR")}원
          </p>
        )}
      </div>
    </div>
  );
}
