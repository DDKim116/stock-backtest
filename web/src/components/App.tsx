"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, apiUrl, setApiUrl, setToken } from "@/lib/api";
import { pct } from "@/lib/format";
import type { DatasetInfo, RunResult, SingleResult, Spec, Strategy } from "@/lib/types";
import Editor, { DEFAULT_SPEC } from "./Editor";
import Results from "./Results";

type Tab = "run" | "saved" | "help" | "settings";

const KEY_DATASET = "sbt.dataset";

function readLS(k: string) {
  try {
    return localStorage.getItem(k) ?? "";
  } catch {
    return "";
  }
}

function writeLS(k: string, v: string) {
  try {
    localStorage.setItem(k, v);
  } catch {
    /* 무시 */
  }
}

export default function App() {
  const [tab, setTab] = useState<Tab>("run");
  const [spec, setSpec] = useState<Spec>(DEFAULT_SPEC);
  const [result, setResult] = useState<RunResult | null>(null);
  const [detail, setDetail] = useState<SingleResult | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [dataset, setDataset] = useState("kr");
  const [needLogin, setNeedLogin] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const loadDatasets = useCallback(async () => {
    try {
      const ds = await api<DatasetInfo[]>("/api/datasets");
      setDatasets(ds);
      setNeedLogin(false);
      const saved = readLS(KEY_DATASET);
      const ready = ds.filter((d) => d.ready);
      const pick = ready.find((d) => d.name === saved) ?? ready.find((d) => !d.synthetic) ?? ready[0];
      if (pick) setDataset(pick.name);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setNeedLogin(true);
      else setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 첫 화면에서 서버 상태를 한 번 불러온다
    loadDatasets();
  }, [loadDatasets]);

  const flash = (m: string) => {
    setToast(m);
    setTimeout(() => setToast(null), 2000);
  };

  const run = async (s: Spec, keepSweep = false) => {
    setRunning(true);
    setError(null);
    try {
      const r = await api<RunResult>("/api/run", { body: { dataset, spec: s } });
      if (keepSweep && r.kind === "single") setDetail(r);
      else {
        setResult(r);
        setDetail(null);
      }
      setTimeout(() => document.getElementById("results")?.scrollIntoView({ behavior: "smooth" }), 50);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setNeedLogin(true);
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const save = async (s: Spec) => {
    const name = prompt("저장할 이름", s.name || "") ?? "";
    if (!name.trim()) return;
    try {
      const { text } = await api<{ text: string }>("/api/to-text", { body: { ...s, name: name.trim() } });
      const summary = result?.kind === "single" && !detail ? result.summary : (detail?.summary ?? null);
      await api("/api/strategies", { body: { name: name.trim(), text, last_summary: summary } });
      setSpec({ ...s, name: name.trim() });
      flash("저장했습니다");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const current = datasets.find((d) => d.name === dataset);

  return (
    <div className="mx-auto min-h-dvh max-w-3xl px-4 pb-28 pt-4">
      <header className="mb-4 flex items-center justify-between">
        <h1 className="text-lg font-bold">주식 검증기</h1>
        {datasets.length > 0 && (
          <select
            value={dataset}
            onChange={(e) => {
              setDataset(e.target.value);
              writeLS(KEY_DATASET, e.target.value);
            }}
            className="!w-auto py-1 text-sm"
          >
            {datasets.map((d) => (
              <option key={d.name} value={d.name} disabled={!d.ready}>
                {d.label}
                {d.ready ? "" : " (준비 안 됨)"}
              </option>
            ))}
          </select>
        )}
      </header>

      {needLogin && <Login onDone={loadDatasets} />}

      {!needLogin && tab === "run" && (
        <>
          {current?.meta && (
            <p className="mb-3 text-xs text-faint">
              데이터 {current.meta.start} ~ {current.meta.end} · {current.meta.codes.toLocaleString("ko-KR")}종목
              {current.synthetic && " · ⚠ 가상 데이터"}
            </p>
          )}
          <Editor spec={spec} setSpec={setSpec} onRun={(s) => run(s)} running={running} onSave={save} />
          {error && (
            <p className="mt-4 whitespace-pre-wrap rounded-xl border border-critical/40 bg-card p-3 text-sm text-critical">
              ⚠ {error}
            </p>
          )}
          {result && (
            <section id="results" className="mt-6 scroll-mt-4">
              <h2 className="mb-3 text-lg font-bold">{result.kind === "sweep" ? "비교 결과" : "검증 결과"}</h2>
              <Results
                result={result}
                detail={detail}
                onPick={(s) => run(s, true)}
                onBack={() => setDetail(null)}
              />
            </section>
          )}
        </>
      )}

      {!needLogin && tab === "saved" && (
        <Saved
          onLoad={async (st) => {
            try {
              const r = await api<{ spec: Spec }>("/api/parse", { body: { text: st.text } });
              setSpec(r.spec);
              setResult(null);
              setDetail(null);
              setTab("run");
            } catch (e) {
              setError((e as Error).message);
              setTab("run");
            }
          }}
        />
      )}
      {!needLogin && tab === "help" && <Help />}
      {tab === "settings" && <Settings onSaved={loadDatasets} />}

      {toast && (
        <div className="fixed inset-x-0 bottom-24 z-40 mx-auto w-fit rounded-full bg-foreground px-4 py-2 text-sm text-background">
          {toast}
        </div>
      )}

      <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-card/95 pb-[env(safe-area-inset-bottom)] backdrop-blur">
        <div className="mx-auto flex max-w-3xl">
          {(
            [
              ["run", "검증"],
              ["saved", "저장 목록"],
              ["help", "도움말"],
              ["settings", "설정"],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              type="button"
              onClick={() => setTab(k)}
              className={`flex-1 py-4 text-sm ${tab === k ? "font-semibold text-accent" : "text-muted"}`}
            >
              {label}
            </button>
          ))}
        </div>
      </nav>
    </div>
  );
}

function Login({ onDone }: { onDone: () => void }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <form
      className="space-y-3 rounded-xl border border-border bg-card p-4"
      onSubmit={async (e) => {
        e.preventDefault();
        try {
          const r = await api<{ token: string }>("/api/login", { body: { password: pw } });
          setToken(r.token);
          onDone();
        } catch (e2) {
          setErr((e2 as Error).message);
        }
      }}
    >
      <h2 className="font-semibold">로그인</h2>
      <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="비밀번호" autoFocus />
      {err && <p className="text-sm text-critical">⚠ {err}</p>}
      <button className="w-full rounded-xl bg-accent py-3 font-semibold text-white">들어가기</button>
    </form>
  );
}

function Saved({ onLoad }: { onLoad: (s: Strategy) => void }) {
  const [items, setItems] = useState<Strategy[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => {
    api<Strategy[]>("/api/strategies")
      .then(setItems)
      .catch((e) => setErr((e as Error).message));
  }, []);
  useEffect(load, [load]);
  if (err) return <p className="text-sm text-critical">⚠ {err}</p>;
  if (!items) return <p className="text-sm text-muted">불러오는 중…</p>;
  if (!items.length) return <p className="text-sm text-muted">저장한 전략이 없습니다. 검증 화면에서 &apos;저장&apos;을 누르세요.</p>;
  return (
    <ul className="space-y-2">
      {items.map((s) => (
        <li key={s.id} className="rounded-xl border border-border bg-card p-3">
          <div className="flex items-start justify-between gap-2">
            <button type="button" className="min-w-0 flex-1 text-left" onClick={() => onLoad(s)}>
              <div className="font-medium">{s.name}</div>
              <div className="truncate text-xs text-muted">{s.text.split("\n").slice(1, 3).join(" ")}</div>
              {s.last_summary && (
                <div className="tabular mt-1 text-xs text-faint">
                  저장 당시: {s.last_summary.n_complete}건 · 성공률 {pct(s.last_summary.success_rate)} · 평균{" "}
                  {pct(s.last_summary.avg_ret, 2, true)}
                </div>
              )}
              <div className="text-[11px] text-faint">{s.updated_at.replace("T", " ")}</div>
            </button>
            <button
              type="button"
              className="text-xs text-critical"
              onClick={async () => {
                if (!confirm(`'${s.name}' 을(를) 삭제할까요?`)) return;
                await api(`/api/strategies/${s.id}`, { method: "DELETE" });
                load();
              }}
            >
              삭제
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}

interface Reference {
  fields: { name: string; desc: string }[];
  functions: { name: string; desc: string; example: string }[];
  logic: string[];
}

const EXAMPLE_TEXT = `[신호]
거래량 >= 거래량(1) * 5
AND 등락률 >= 12

[매수]
지정가 = (시가 + 종가) / 2
유효기간 = 5

[청산]
목표 = 3
기준 = 고가
손절 = 없음
보유기간 = 10

[대상]
시장 = 코스피, 코스닥
기간 = 2010-01-01 ~ 오늘
제외 = 스팩, 우선주`;

function Help() {
  const [ref, setRef] = useState<Reference | null>(null);
  useEffect(() => {
    api<Reference>("/api/reference").then(setRef).catch(() => setRef(null));
  }, []);
  return (
    <div className="space-y-4 text-sm leading-relaxed">
      <section className="rounded-xl border border-border bg-card p-4">
        <h2 className="mb-2 font-semibold">어떻게 계산하나요?</h2>
        <ol className="list-decimal space-y-1 pl-5 text-muted">
          <li>대상 기간의 모든 종목·모든 날에서 신호 조건을 만족하는 날을 찾습니다.</li>
          <li>매수 방식에 따라 체결 여부를 봅니다. 지정가는 다음 날부터 유효기간 동안 저가가 지정가 이하로 내려오면 체결입니다 (시가가 더 낮으면 시가 체결).</li>
          <li>체결 후 보유기간 안에 목표가(고가 기준)에 닿으면 성공, 손절가(저가 기준)에 닿으면 손절, 둘 다 아니면 마지막 날 종가로 청산합니다.</li>
          <li>같은 날 목표와 손절에 모두 닿은 경우처럼 일봉으로 순서를 알 수 없으면 &apos;불확실&apos;로 표시하고, 기본 성공률은 불리한 쪽(보수적)으로 계산합니다.</li>
          <li>같은 매수·청산 규칙으로 &apos;아무 날이나 샀을 때&apos;의 성공률을 함께 계산해 조건의 효과를 비교합니다.</li>
        </ol>
      </section>
      <section className="rounded-xl border border-border bg-card p-4">
        <h2 className="mb-2 font-semibold">조건식 예시</h2>
        <pre className="overflow-x-auto rounded-lg bg-background p-3 text-xs">{EXAMPLE_TEXT}</pre>
        <p className="mt-2 text-muted">
          숫자 자리에 <code>[10, 12, 15]</code> 처럼 쓰면 각 값으로 모두 돌려 비교표를 만듭니다. 조립 화면의 숫자 칸은 <code>3, 5, 7</code> 처럼 쉼표로 씁니다.
        </p>
      </section>
      {ref && (
        <>
          <section className="rounded-xl border border-border bg-card p-4">
            <h2 className="mb-2 font-semibold">값</h2>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
              {ref.fields.map((f) => (
                <div key={f.name} className="contents">
                  <dt className="font-medium">{f.name}</dt>
                  <dd className="text-muted">{f.desc}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-2 text-muted">숫자 뒤에 만·억·조를 붙일 수 있습니다 (예: 거래대금 &gt;= 100억). % 기호는 쓰지 않습니다.</p>
          </section>
          <section className="rounded-xl border border-border bg-card p-4">
            <h2 className="mb-2 font-semibold">함수</h2>
            <ul className="space-y-2">
              {ref.functions.map((f) => (
                <li key={f.name}>
                  <div className="font-medium">{f.name}</div>
                  <div className="text-muted">{f.desc}</div>
                  <code className="text-xs text-accent">{f.example}</code>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-muted">논리: {ref.logic.join(" · ")}</p>
          </section>
        </>
      )}
    </div>
  );
}

function Settings({ onSaved }: { onSaved: () => void }) {
  const [url, setUrl] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- 저장된 주소를 입력칸에 채운다
  useEffect(() => setUrl(apiUrl()), []);
  return (
    <div className="space-y-4">
      <section className="space-y-3 rounded-xl border border-border bg-card p-4">
        <h2 className="font-semibold">분석 서버 주소</h2>
        <p className="text-sm text-muted">오라클 서버의 Cloudflare 주소 (예: https://api.내도메인.com). 비우면 이 화면과 같은 주소를 씁니다.</p>
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://..." autoCapitalize="off" />
        <div className="flex gap-2">
          <button
            type="button"
            className="flex-1 rounded-xl bg-accent py-2.5 font-semibold text-white"
            onClick={async () => {
              setApiUrl(url);
              try {
                const h = await api<{ ok: boolean; auth_required: boolean }>("/api/health");
                setMsg(h.ok ? "연결 성공" + (h.auth_required ? " · 로그인 필요" : "") : "응답 이상");
                onSaved();
              } catch (e) {
                setMsg((e as Error).message);
              }
            }}
          >
            저장 후 연결 확인
          </button>
          <button
            type="button"
            className="rounded-xl border border-border px-4 text-sm"
            onClick={() => {
              setToken("");
              setMsg("로그아웃했습니다");
              onSaved();
            }}
          >
            로그아웃
          </button>
        </div>
        {msg && <p className="text-sm text-muted">{msg}</p>}
      </section>
      <section className="rounded-xl border border-border bg-card p-4 text-sm text-muted">
        <h2 className="mb-1 font-semibold text-foreground">AI 질문 (다음 단계)</h2>
        말로 질문하는 기능은 조건식 단계가 안정되면 추가합니다. AI 사용 시에만 Anthropic API 요금이 발생하며, 이 화면에서 사용 한도를 정할 수 있게 됩니다.
      </section>
    </div>
  );
}
