"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { pct, retClass } from "@/lib/format";
import ExploreReview from "./ExploreReview";
import StartEditor from "./StartEditor";
import type { DatasetInfo, ExploreAxis, ExploreJob, ExplorePlan, ExploreRow, Spec } from "@/lib/types";

const JOB_KEY = "sbt-explore-job";

function readJob(): string | null {
  try {
    return localStorage.getItem(JOB_KEY);
  } catch {
    return null;
  }
}
function writeJob(id: string | null) {
  try {
    if (id) localStorage.setItem(JOB_KEY, id);
    else localStorage.removeItem(JOB_KEY);
  } catch {}
}

function fmtVal(a: ExploreAxis, v: number | null): string {
  if (v === null) return "없음";
  if (a.kind === "target" || a.kind === "stop") return `${v}%`;
  if (a.kind === "days") return `${v}일`;
  if (a.key === "목표" || a.key === "손절") return `${v}%`;
  if (a.key === "보유기간" || a.key === "유효기간") return `${v}일`;
  return String(v);
}

function shortKey(a: ExploreAxis): string {
  if (a.kind === "signal") return a.key.replace(/^신호 기준값 \d+ \((.+)\)$/, "신호 $1");
  return a.key.replace("(%)", "").replace("(일)", "");
}

const ENTRY_LABEL = { limit: "지정가", next_open: "다음날 시가", close: "당일 종가" } as const;

export default function Explore({
  spec,
  dataset,
  onUse,
  onSetSpec,
}: {
  spec: Spec;
  dataset: DatasetInfo | undefined;
  onUse: (s: Spec) => void;
  onSetSpec: (s: Spec) => void;
}) {
  const [plan, setPlan] = useState<ExplorePlan | null>(null);
  const [planErr, setPlanErr] = useState<string | null>(null);
  const [split, setSplit] = useState<string>("");
  const [job, setJob] = useState<ExploreJob | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showMiss, setShowMiss] = useState(false);
  const [shown, setShown] = useState(10);
  const [bestRank, setBestRank] = useState(0);
  const [jobId, setJobId] = useState<string | null>(readJob);
  const ds = dataset?.name ?? "kr";

  // 탐색 범위 미리 보기
  useEffect(() => {
    let alive = true;
    api<ExplorePlan>("/api/explore/plan", { body: { dataset: ds, spec, split: split || null } })
      .then((p) => {
        if (!alive) return;
        setPlan(p);
        setPlanErr(null);
      })
      .catch((e) => {
        if (!alive) return;
        setPlan(null);
        setPlanErr((e as Error).message);
      });
    return () => {
      alive = false;
    };
  }, [spec, ds, split]);

  // 진행 중인 탐색 상태를 2초마다 확인 (다른 화면에 갔다 와도 저장해 둔 번호로 이어서 본다)
  useEffect(() => {
    if (!jobId) return;
    let alive = true;
    let t: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const j = await api<ExploreJob>(`/api/explore/${jobId}`);
        if (!alive) return;
        setJob(j);
        if (j.status === "running") t = setTimeout(tick, 2000);
      } catch (e) {
        if (!alive) return;
        setErr((e as Error).message);
        writeJob(null);
      }
    };
    tick();
    return () => {
      alive = false;
      if (t) clearTimeout(t);
    };
  }, [jobId]);

  const start = async () => {
    setErr(null);
    try {
      const r = await api<{ id: string; total: number }>("/api/explore", {
        body: { dataset: ds, spec, split: split || null },
      });
      writeJob(r.id);
      setJob({ id: r.id, status: "running", done: 0, total: r.total, result: null, error: null, elapsed: 0 });
      setJobId(r.id);
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  const cancel = async () => {
    if (!job) return;
    await api(`/api/explore/${job.id}`, { method: "DELETE" }).catch(() => {});
  };

  const running = job?.status === "running";
  const res = job?.result;
  const years = dataset?.meta ? range(+dataset.meta.start.slice(0, 4) + 3, +dataset.meta.end.slice(0, 4) - 1) : [];

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border bg-card p-3 text-sm">
        <h2 className="font-semibold">전략 탐색</h2>
        <p className="mt-1 text-muted">
          출발점 조건(여기서 바로 입력하거나 검증 화면의 조건)을 바탕으로 숫자 조합을 자동으로 바꿔 가며 돌립니다. 기간을 <b>과거 구간</b>과 <b>검증 구간</b>으로 나눠, 과거에서
          고른 조합이 보지 않은 기간에서도 통하는지 확인하고 운에 맞춘 조합은 걸러 냅니다. AI를 쓰지 않아 무료입니다.
        </p>
      </div>

      <section className="space-y-2 rounded-xl border border-border bg-card p-3 text-sm">
        <h3 className="font-semibold">1. 출발점</h3>
        <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg bg-background p-2 text-xs leading-relaxed">
          {spec.signal.replace(/\s+AND\s+/g, " 그리고 ").replace(/\s+OR\s+/g, " 또는 ")}
        </pre>
        <p className="text-xs text-muted">
          매수: {ENTRY_LABEL[spec.entry.type]}
          {spec.entry.type === "limit" ? ` (${spec.entry.price})` : ""} · {dataset?.label ?? ""}
        </p>
        <StartEditor spec={spec} onApply={onSetSpec} />
        <p className="text-[11px] text-faint">검증 탭의 조건과 같이 바뀝니다.</p>
      </section>

      <section className="space-y-2 rounded-xl border border-border bg-card p-3 text-sm">
        <h3 className="font-semibold">2. 바꿔 볼 값</h3>
        {planErr && <p className="whitespace-pre-wrap text-critical">⚠ {planErr}</p>}
        {!plan && !planErr && <p className="text-xs text-muted">범위를 계산하는 중…</p>}
        {plan && (
          <>
            <ul className="space-y-1">
              {plan.axes.map((a) => (
                <li key={a.key} className="flex flex-wrap items-center gap-1.5">
                  <span className="text-xs text-muted">{a.key}</span>
                  {a.values.map((v, i) => (
                    <span key={i} className="tabular rounded bg-background px-1.5 py-0.5 text-xs">
                      {fmtVal(a, v)}
                    </span>
                  ))}
                </li>
              ))}
            </ul>
            <p className="text-xs text-muted">
              모두 <b>{plan.n_combos.toLocaleString("ko-KR")}개 조합</b>
              {plan.user_defined ? " (직접 적은 [a, b] 값 사용)" : " (신호 기준값은 ±30%, 청산은 정해진 후보)"}
            </p>
            {plan.notes.map((n) => (
              <p key={n} className="text-xs text-warning">
                {n}
              </p>
            ))}
            <p className="text-xs text-faint">
              원하는 값만 돌리려면 검증 화면 숫자 칸에 <code>8, 10, 12</code>처럼 여러 값을 적으세요. 그 값들만 탐색합니다.
            </p>
          </>
        )}
      </section>

      <section className="space-y-2 rounded-xl border border-border bg-card p-3 text-sm">
        <h3 className="font-semibold">3. 기간 나누기</h3>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted">검증 구간 시작</span>
          <select value={split} onChange={(e) => setSplit(e.target.value)} className="!w-auto py-1 text-sm">
            <option value="">자동 ({plan?.split?.slice(0, 4) ?? "…"}년)</option>
            {years.map((y) => (
              <option key={y} value={`${y}-01-01`}>
                {y}년
              </option>
            ))}
          </select>
        </div>
        <p className="text-xs text-faint">
          과거 구간에서 좋은 조합을 고르고, 이 연도부터의 검증 구간에서도 수익이 나는지 봅니다. 자동은 전체 기간의 뒤쪽 약 30%입니다.
        </p>
      </section>

      {!running && (
        <button
          type="button"
          onClick={start}
          disabled={!plan}
          className="w-full rounded-xl bg-accent py-3 font-semibold text-white disabled:opacity-50"
        >
          탐색 시작 ({plan?.n_combos ?? 0}개 조합)
        </button>
      )}
      {err && <p className="whitespace-pre-wrap text-sm text-critical">⚠ {err}</p>}

      {running && job && (
        <div className="space-y-2 rounded-xl border border-border bg-card p-3 text-sm">
          <div className="flex justify-between">
            <span>
              조합 {job.done.toLocaleString("ko-KR")} / {job.total.toLocaleString("ko-KR")} 계산 중…
            </span>
            <span className="tabular text-muted">{Math.round(job.elapsed)}초</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-background">
            <div className="h-full bg-accent transition-all" style={{ width: `${(job.done / Math.max(1, job.total)) * 100}%` }} />
          </div>
          <p className="text-xs text-faint">
            서버에서 계산하므로 다른 화면에 가거나 앱을 닫아도 계속됩니다. 돌아오면 이어서 보여 줍니다.
            {job.done > 0 &&
              ` 남은 시간 약 ${Math.max(1, Math.round(((job.elapsed / job.done) * (job.total - job.done)) / 60))}분.`}
          </p>
          <button type="button" onClick={cancel} className="rounded-lg border border-border px-3 py-1 text-xs">
            중지
          </button>
        </div>
      )}

      {job?.status === "error" && <p className="text-sm text-critical">⚠ 탐색 중 오류: {job.error}</p>}
      {job?.status === "cancelled" && <p className="text-sm text-muted">탐색을 중지했습니다. 계산된 조합까지만 보여 줍니다.</p>}

      {res && (
        <section className="space-y-3">
          <div className="rounded-xl border border-border bg-card p-3 text-sm">
            <div className="font-semibold">
              {res.n_total.toLocaleString("ko-KR")}개 중 <span className="text-accent">{res.n_passed}개 통과</span>
            </div>
            <p className="mt-1 text-xs text-muted">
              과거 구간 {res.period?.[0]} ~ {res.split} 전 · 검증 구간 {res.split} ~ {res.period?.[1]} · {res.elapsed}초
            </p>
            {res.fail_reasons.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {res.fail_reasons.map(([k, n]) => (
                  <span key={k} className="rounded-full bg-background px-2 py-0.5 text-[11px] text-muted">
                    {k} {n}
                  </span>
                ))}
              </div>
            )}
            <p className="mt-2 text-[11px] text-faint">
              통과 기준: 과거 구간 100건·검증 구간 30건 이상, 두 구간 모두 평균 수익(비용 차감) 플러스, 수익 난 해 60% 이상, 바로 옆 숫자
              조합들도 플러스. 순위는 두 구간 중 나쁜 쪽 평균 수익률 기준입니다.
            </p>
          </div>

          {res.axis_effects && res.axis_effects.length > 0 && (
            <details className="rounded-xl border border-border bg-card p-3 text-sm">
              <summary className="cursor-pointer font-semibold">값별 평균 수익률</summary>
              <p className="mt-1 text-[11px] text-faint">
                그 값을 쓴 모든 조합의 평균 수익률(중앙값). 과거 → 검증 구간 순. 어느 쪽 값이 통하는지 방향을 볼 때 씁니다.
              </p>
              <div className="mt-2 space-y-2">
                {res.axis_effects.map((a, k) => (
                  <div key={a.key}>
                    <div className="text-xs text-muted">{a.key}</div>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {a.values.map((v, i) => (
                        <span key={i} className="tabular rounded bg-background px-1.5 py-0.5 text-xs">
                          {fmtVal(res.axes[k], v.value)}:{" "}
                          <span className={retClass(v.is_avg)}>{pct(v.is_avg, 2, true)}</span> →{" "}
                          <span className={retClass(v.oos_avg)}>{pct(v.oos_avg, 2, true)}</span>
                          <span className="text-faint"> · 통과 {v.passed}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </details>
          )}

          {job && job.status !== "running" && (
            <ExploreReview
              key={job.id}
              jobId={job.id}
              initial={job.review}
              onBest={(rank) => {
                setBestRank(rank);
                setShown((n) => Math.max(n, rank));
              }}
              onExplore={(sp) => {
                onSetSpec(sp);
                window.scrollTo({ top: 0, behavior: "smooth" });
              }}
              onRun={onUse}
            />
          )}

          {res.passed.length === 0 && (
            <p className="rounded-xl border border-border bg-card p-4 text-center text-sm text-muted">
              통과한 조합이 없습니다. 이 아이디어는 비용을 빼고 나면 꾸준한 우위가 없다는 뜻일 수 있어요. 아래 &apos;아깝게 탈락&apos;을 참고해
              조건을 바꿔 보세요.
            </p>
          )}
          {res.passed.slice(0, shown).map((r, i) => (
            <Card key={i} rank={i + 1} best={bestRank === i + 1 || job?.review?.best.rank === i + 1} r={r} axes={res.axes} onUse={onUse} />
          ))}

          {res.passed.length > shown && (
            <button
              type="button"
              onClick={() => setShown(shown + 10)}
              className="w-full rounded-xl border border-border bg-card py-2 text-sm text-muted"
            >
              더 보기 ({res.passed.length - shown}개 남음)
            </button>
          )}

          {res.near_miss.length > 0 && (
            <div>
              <button type="button" onClick={() => setShowMiss(!showMiss)} className="text-sm text-muted underline">
                아깝게 탈락한 조합 {res.near_miss.length}개 {showMiss ? "접기" : "보기"}
              </button>
              {showMiss && (
                <div className="mt-2 space-y-2">
                  {res.near_miss.map((r, i) => (
                    <Card key={i} r={r} axes={res.axes} onUse={onUse} />
                  ))}
                </div>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function range(a: number, b: number): number[] {
  const out = [];
  for (let y = a; y <= b; y++) out.push(y);
  return out;
}

function Card({
  r,
  axes,
  rank,
  best,
  onUse,
}: {
  r: ExploreRow;
  axes: ExploreAxis[];
  rank?: number;
  best?: boolean;
  onUse: (s: Spec) => void;
}) {
  const cell = (label: string, b: ExploreRow["is"]) => (
    <div className="rounded-lg bg-background p-2">
      <div className="text-[11px] text-muted">{label}</div>
      <div className={`tabular text-base font-semibold ${retClass(b.avg_ret)}`}>
        {pct(b.avg_ret, 2, true)}
      </div>
      <div className="tabular text-[11px] text-muted">
        성공 {pct(b.success_rate)} · {b.n_complete.toLocaleString("ko-KR")}건
      </div>
    </div>
  );
  return (
    <div className={`space-y-2 rounded-xl border bg-card p-3 text-sm ${best ? "border-accent" : "border-border"}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        {rank && <span className="mr-1 font-bold text-accent">{rank}위</span>}
        {best && <span className="rounded bg-accent px-1.5 py-0.5 text-[11px] font-semibold text-white">AI 추천</span>}
        {axes.map((a, k) => (
          <span key={a.key} className="tabular rounded bg-accent-soft px-1.5 py-0.5 text-xs text-accent">
            {shortKey(a)} {fmtVal(a, r.combo[k])}
          </span>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-2">
        {cell("과거 구간 평균 수익", r.is)}
        {cell("검증 구간 평균 수익", r.oos)}
      </div>
      <div className="tabular text-[11px] text-muted">
        수익 난 해 {r.pos_years != null ? `${Math.round(r.pos_years * 100)}%` : "–"} ({r.n_years}년) · 옆 조합 평균{" "}
        {pct(r.neighbor_avg, 2, true)} · 아무 날이나 샀을 때 {pct(r.baseline.avg_ret, 2, true)}
      </div>
      {r.reasons.length > 0 && <div className="text-xs text-warning">탈락: {r.reasons.join(", ")}</div>}
      <button
        type="button"
        onClick={() => onUse(r.spec)}
        className="w-full rounded-lg border border-accent py-2 text-sm font-semibold text-accent"
      >
        이 조합으로 자세히 검증
      </button>
    </div>
  );
}
