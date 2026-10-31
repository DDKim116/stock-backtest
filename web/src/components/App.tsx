"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, apiUrl, setApiUrl, setToken } from "@/lib/api";
import { pct } from "@/lib/format";
import { idbGet, idbSet } from "@/lib/persist";
import type { DatasetInfo, JournalPrefill, RunResult, ScanResult, SingleResult, Spec, Strategy } from "@/lib/types";
import { useAIStatus } from "./AIAsk";
import Editor, { DEFAULT_SPEC } from "./Editor";
import Results from "./Results";
import ScanResults from "./ScanResults";
import Explore from "./Explore";
import Journal from "./Journal";
import KrxBanner from "./KrxBanner";
import Market from "./Market";
import Paper from "./Paper";
import NumInput from "./NumInput";
import Notes from "./Notes";
import PushSettings from "./PushSettings";
import BrokerSettings from "./BrokerSettings";
import DartSettings from "./DartSettings";
import Today from "./Today";
import AutoTrade from "./AutoTrade";
import WatchCard from "./WatchCard";
import Lab from "./Lab";

type Tab = "today" | "run" | "explore" | "journal" | "auto" | "paper" | "market" | "lab" | "saved" | "more";
const TAB_KEYS: Tab[] = ["today", "run", "explore", "journal", "auto", "paper", "market", "lab", "saved", "more"];
// 아래 메뉴 6개 (시안 A). 둘 이상 묶인 곳은 위에 작은 탭으로 고른다
// 탭 이름 뒤 셋째 값은 작은 설명 (매매: 실제 주문인지 앱 계산인지 한눈에)
const GROUPS: { label: string; tabs: [Tab, string, string?][]; icon: string }[] = [
  { label: "오늘", tabs: [["today", "오늘"]], icon: "M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" },
  { label: "검증", tabs: [["run", "검증"], ["explore", "탐색"]], icon: "M4 19h16M7 16V9M12 16V5M17 16v-4" },
  { label: "매매", tabs: [["auto", "키움 자동", "실제 주문"], ["paper", "앱 모의", "계산만"], ["journal", "실투", "내 매매 기록"]], icon: "M4 7h13l-3-3M20 17H7l3 3" },
  { label: "시장", tabs: [["market", "지수"]], icon: "M3 17l5-6 4 4 8-9" },
  { label: "연구", tabs: [["lab", "연구"]], icon: "M9 3h6M10 3v6l-5.5 9.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3M7 15h10" },
  { label: "더보기", tabs: [["saved", "전략"], ["more", "설정"]], icon: "M5 12h.01M12 12h.01M19 12h.01" },
];
const groupOf = (t: Tab) => GROUPS.find((g) => g.tabs.some(([k]) => k === t)) ?? GROUPS[0];
// 알림을 눌러 열면 주소 끝의 #paper 같은 탭으로
function hashTab(): Tab | null {
  const h = window.location.hash.slice(1) as Tab;
  return TAB_KEYS.includes(h) ? h : null;
}

// 다시 열었을 때 이어 볼 화면
interface Session {
  v: 1;
  savedAt: string;
  dataset: string;
  tab: Tab;
  spec: Spec;
  runSpec: Spec;
  result: RunResult | null;
  detail: SingleResult | null;
  scan: ScanResult | null;
}
const SESSION_KEY = "session";

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

/** 데이터셋을 바꾸면 시장·비용 기본값을 그 나라에 맞춘다 (같은 나라면 그대로). */
function fitToDataset(s: Spec, d: DatasetInfo): Spec {
  if (s.universe.markets.some((m) => d.markets.includes(m))) return s;
  return {
    ...s,
    universe: { ...s.universe, markets: d.markets },
    costs: { ...d.costs },
    fx_krw: false,
  };
}

// 저장한 전략을 불러와 돌린 결과면 제목 옆에 전략 이름
function StrategyTag({ name }: { name?: string }) {
  if (!name) return null;
  return <span className="rounded-md bg-accent-soft px-2 py-0.5 text-sm font-semibold text-accent">{name}</span>;
}

export default function App() {
  const [tab, setTab] = useState<Tab>("today");
  const [notes, setNotes] = useState(false);
  const [newsCount, setNewsCount] = useState(0); // 모투 새 소식 (체결·미체결·청산)
  const clearNews = useCallback(() => setNewsCount(0), []);
  const [spec, setSpec] = useState<Spec>(DEFAULT_SPEC);
  const [result, setResult] = useState<RunResult | null>(null);
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [journalPrefill, setJournalPrefill] = useState<JournalPrefill | null>(null);
  const [restored, setRestored] = useState(false); // 저장된 화면을 읽기 전에는 덮어쓰지 않도록
  const [restoredAt, setRestoredAt] = useState<string | null>(null);
  const clearPrefill = useCallback(() => setJournalPrefill(null), []);
  const [detail, setDetail] = useState<SingleResult | null>(null);
  const [running, setRunning] = useState(false);
  const [runSpec, setRunSpec] = useState<Spec>(DEFAULT_SPEC);
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
      if (pick) {
        setDataset(pick.name);
        setSpec((s) => fitToDataset(s, pick));
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setNeedLogin(true);
      else setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 첫 화면에서 서버 상태를 한 번 불러온다
    loadDatasets();
  }, [loadDatasets]);

  // 마지막 화면 불러오기
  useEffect(() => {
    idbGet<Session>(SESSION_KEY).then((ss) => {
      if (ss && ss.v === 1 && ss.dataset === (readLS(KEY_DATASET) || ss.dataset)) {
        setSpec(ss.spec);
        setRunSpec(ss.runSpec);
        setResult(ss.result);
        setDetail(ss.detail);
        setScan(ss.scan);
        setTab(ss.tab);
        if (ss.result || ss.scan) setRestoredAt(ss.savedAt);
      }
      const h = hashTab();
      if (h) {
        setTab(h);
        history.replaceState(null, "", window.location.pathname);
      }
      setRestored(true);
    });
  }, []);

  // 앱이 열려 있을 때 알림을 누르면 서비스 워커가 탭 이름을 보내 준다
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    const on = (e: MessageEvent) => {
      const t = e.data?.type === "open-tab" ? (e.data.tab as Tab) : null;
      if (t && TAB_KEYS.includes(t)) setTab(t);
    };
    navigator.serviceWorker.addEventListener("message", on);
    return () => navigator.serviceWorker.removeEventListener("message", on);
  }, []);

  // 바뀔 때마다 저장 (0.5초 모아서)
  useEffect(() => {
    if (!restored) return;
    const t = setTimeout(() => {
      const ss: Session = {
        v: 1,
        savedAt: new Date().toISOString(),
        dataset,
        tab,
        spec,
        runSpec,
        result,
        detail,
        scan,
      };
      idbSet(SESSION_KEY, ss);
    }, 500);
    return () => clearTimeout(t);
  }, [restored, dataset, tab, spec, runSpec, result, detail, scan]);

  const flash = (m: string) => {
    setToast(m);
    setTimeout(() => setToast(null), Math.min(5000, 1800 + m.length * 40));
  };

  const run = async (s: Spec, keepSweep = false) => {
    setRunning(true);
    setError(null);
    try {
      const r = await api<RunResult>("/api/run", { body: { dataset, spec: s } });
      if (keepSweep && r.kind === "single") setDetail(r);
      else {
        setRunSpec(s);
        setResult(r);
        setDetail(null);
        setScan(null);
        setRestoredAt(null);
      }
      setTimeout(() => document.getElementById("results")?.scrollIntoView({ behavior: "smooth" }), 50);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setNeedLogin(true);
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  // 오늘 신호: 마지막 거래일 기준으로 조건에 맞는 종목과 주문 상태
  const doScan = async (s: Spec) => {
    setRunning(true);
    setError(null);
    try {
      const r = await api<ScanResult>("/api/scan", { body: { dataset, spec: s } });
      setScan(r);
      setResult(null);
      setDetail(null);
      setRestoredAt(null);
      setTimeout(() => document.getElementById("scan")?.scrollIntoView({ behavior: "smooth" }), 50);
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
      // 같은 이름이 있으면 덮어쓸지 묻는다
      const same = (await api<Strategy[]>("/api/strategies")).find((x) => x.name === name.trim());
      if (same) {
        if (!confirm(`'${same.name}' 이(가) 이미 저장돼 있습니다.\n덮어쓸까요? (취소하면 저장하지 않습니다. 다른 이름으로 저장하려면 취소 후 다시 저장을 누르세요.)`))
          return;
        await api(`/api/strategies/${same.id}`, {
          method: "PUT",
          body: { name: same.name, text, note: same.note, last_summary: summary },
        });
        setSpec({ ...s, name: name.trim() });
        flash("덮어썼습니다");
        return;
      }
      await api("/api/strategies", { body: { name: name.trim(), text, last_summary: summary } });
      setSpec({ ...s, name: name.trim() });
      flash("저장했습니다");
    } catch (e) {
      setError((e as Error).message);
    }
  };

  // 모투 새 소식 개수: 처음 열 때와 10분마다
  useEffect(() => {
    if (needLogin) return;
    const check = () =>
      api<{ unseen: number }>("/api/paper/news?limit=1")
        .then((r) => setNewsCount(r.unseen))
        .catch(() => {});
    check();
    const t = setInterval(check, 10 * 60 * 1000);
    return () => clearInterval(t);
  }, [needLogin]);

  const current = datasets.find((d) => d.name === dataset);

  return (
    <div className="mx-auto min-h-dvh max-w-3xl px-4 pb-28 pt-4 fold:max-w-6xl fold:pb-10 fold:pl-[5.25rem] fold:pr-4 lg:pl-24 lg:pr-6">
      <header className="mb-4 flex items-center justify-between">
        <h1 className="flex items-center gap-2 text-lg font-bold">
          {/* eslint-disable-next-line @next/next/no-img-element -- 정적 내보내기라 next/image 최적화를 못 씀 */}
          <img src="/icon-192.png" alt="" width={24} height={24} className="rounded-md" />
          Autobull
        </h1>
        {datasets.length > 0 && ["run", "explore", "saved", "journal"].includes(tab) && (
          <select
            value={dataset}
            onChange={(e) => {
              setDataset(e.target.value);
              writeLS(KEY_DATASET, e.target.value);
              const d = datasets.find((x) => x.name === e.target.value);
              if (d) setSpec(fitToDataset(spec, d));
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
      {!needLogin && <KrxBanner />}

      {!needLogin && groupOf(tab).tabs.length > 1 && (
        <div className="mb-4 flex gap-1 rounded-xl border border-border bg-card p-1" role="tablist">
          {groupOf(tab).tabs.map(([k, label, sub]) => (
            <button
              key={k}
              type="button"
              role="tab"
              aria-selected={tab === k}
              onClick={() => setTab(k)}
              className={`flex flex-1 flex-col items-center justify-center gap-x-1.5 rounded-lg px-1 py-2 text-sm fold:flex-row ${tab === k ? "bg-accent-soft font-semibold text-accent" : "text-muted"}`}
            >
              <span>
                {label}
                {k === "paper" && newsCount > 0 && <span className="ml-1 text-xs text-critical">●</span>}
              </span>
              {sub && <span className="text-[10px] font-normal fold:text-xs">{sub}</span>}
            </button>
          ))}
        </div>
      )}

      {!needLogin && tab === "today" && (
        <Today
          newsCount={newsCount}
          onGo={(t) => {
            setTab(t);
            window.scrollTo({ top: 0 });
          }}
        />
      )}

      {!needLogin && tab === "run" && (
        <>
          {current?.meta && (
            <p className="mb-3 text-xs text-faint">
              데이터 {current.meta.start} ~ {current.meta.end} · {current.meta.codes.toLocaleString("ko-KR")}종목
              {current.synthetic && " · ⚠ 가상 데이터"}
              {current.meta.provisional && (
                <span className="block text-warning">
                  {current.meta.provisional.date.slice(5)} 자료는 저녁 임시값(야후)입니다. 다음 날 아침 KRX 공식 값으로 바뀝니다.
                </span>
              )}
            </p>
          )}
          <Editor
            spec={spec}
            setSpec={setSpec}
            onRun={(s) => run(s)}
            onScan={doScan}
            dataset={current}
            running={running}
            onSave={save}
          />
          {error && (
            <p className="mt-4 whitespace-pre-wrap rounded-xl border border-critical/40 bg-card p-3 text-sm text-critical">
              ⚠ {error}
            </p>
          )}
          {restoredAt && (result || scan) && (
            <div className="mt-6 flex items-center justify-between gap-2 rounded-xl border border-border bg-card px-3 py-2 text-xs text-muted">
              <span>
                지난번 {scan ? "오늘 신호" : "검증"} 결과입니다 ({new Date(restoredAt).toLocaleString("ko-KR", { dateStyle: "short", timeStyle: "short" })}
                ). 그 뒤 데이터가 갱신됐을 수 있어요.
              </span>
              <button
                type="button"
                disabled={running}
                onClick={() => (scan ? doScan(scan.spec) : run(runSpec))}
                className="shrink-0 rounded-lg border border-accent px-2.5 py-1 font-semibold text-accent disabled:opacity-50"
              >
                다시 실행
              </button>
            </div>
          )}
          {scan && (
            <section id="scan" className="mt-6 scroll-mt-4">
              <h2 className="mb-3 flex flex-wrap items-baseline gap-x-2 text-lg font-bold">
                오늘 신호
                <StrategyTag name={scan.spec.name} />
              </h2>
              <ScanResults
                r={scan}
                onBacktest={() => run(scan.spec)}
                onFlash={flash}
                onRecord={async (row) => {
                  let text = "";
                  try {
                    text = (await api<{ text: string }>("/api/to-text", { body: scan.spec })).text;
                  } catch {}
                  setJournalPrefill({
                    code: row.code,
                    name: row.name,
                    market: row.market,
                    signal_date: row.signal_date,
                    strategy_id: null,
                    strategy_name: scan.spec.name || "오늘 신호 조건",
                    strategy_text: text,
                    price: row.fill_price ?? row.limit ?? row.close,
                    date: row.fill_date,
                  });
                  setTab("journal");
                }}
              />
            </section>
          )}
          {result && (
            <section id="results" className="mt-6 scroll-mt-4">
              <h2 className="mb-3 flex flex-wrap items-baseline gap-x-2 text-lg font-bold">
                {result.kind === "sweep" ? "비교 결과" : "검증 결과"}
                <StrategyTag name={result.kind === "sweep" ? result.variants[0]?.spec.name : result.spec.name} />
              </h2>
              <Results
                result={result}
                detail={detail}
                runSpec={runSpec}
                onPick={(s) => run(s, true)}
                onBack={() => setDetail(null)}
                onLoadText={async (text) => {
                  try {
                    const r = await api<{ spec: Spec }>("/api/parse", { body: { text } });
                    setSpec(r.spec);
                    window.scrollTo({ top: 0, behavior: "smooth" });
                    flash("조건을 불러왔습니다. '검증 실행'을 누르세요");
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              />
            </section>
          )}
        </>
      )}

      {!needLogin && tab === "saved" && (
        <Saved
          onScan={async (st) => {
            try {
              const r = await api<{ spec: Spec }>("/api/parse", { body: { text: st.text } });
              const named = { ...r.spec, name: st.name };
              const fitted = current ? fitToDataset(named, current) : named;
              setSpec(fitted);
              setTab("run");
              await doScan(fitted);
            } catch (e) {
              setError((e as Error).message);
              setTab("run");
            }
          }}
          onLoad={async (st) => {
            try {
              const r = await api<{ spec: Spec }>("/api/parse", { body: { text: st.text } });
              // 저장된 전략 이름을 결과 제목에 표시, 시장은 지금 고른 데이터에 맞춘다
              const named = { ...r.spec, name: st.name };
              setSpec(current ? fitToDataset(named, current) : named);
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
      {!needLogin && tab === "explore" && (
        <Explore
          spec={spec}
          dataset={current}
          onSetSpec={(s) => setSpec(current ? fitToDataset(s, current) : s)}
          onUse={(s) => {
            const f = current ? fitToDataset(s, current) : s;
            setSpec(f);
            setTab("run");
            run(f);
          }}
        />
      )}
      {!needLogin && tab === "journal" && (
        <Journal dataset={current} prefill={journalPrefill} onPrefillUsed={clearPrefill} />
      )}
      {!needLogin && tab === "auto" && (
        <div className="space-y-3">
          <div className="flex items-center gap-2.5 rounded-xl bg-accent-soft px-3 py-2.5 text-xs text-accent">
            <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" className="shrink-0">
              <path d="M12 3l9 16H3z" />
              <path d="M12 10v4M12 17h.01" />
            </svg>
            <span>
              키움 <b>모의투자</b> 계좌로 <b>실제 주문</b>이 나갑니다. 앱 안에서 계산만 하는 모투는 &apos;앱 모의&apos;에 있어요.
            </span>
          </div>
          <AutoTrade onFlash={flash} />
          <WatchCard onFlash={flash} />
        </div>
      )}
      {!needLogin && tab === "paper" && (
        <Paper
          spec={spec}
          dataset={dataset}
          currency={current?.currency ?? "KRW"}
          onFlash={flash}
          onNewsSeen={clearNews}
          onLoadSpec={(sp) => {
            setSpec(current ? fitToDataset(sp, current) : sp);
            setResult(null);
            setDetail(null);
            setTab("run");
            window.scrollTo({ top: 0 });
          }}
        />
      )}
      {!needLogin && tab === "market" && <Market />}
      {!needLogin && tab === "lab" && <Lab />}
      {tab === "more" && (
        <div className="space-y-8">
          <Settings onSaved={loadDatasets} />
          {!needLogin && <PushSettings />}
          {!needLogin && <BrokerSettings />}
          {!needLogin && <DartSettings />}
          {!needLogin && (
            <section>
              <h2 className="mb-3 text-lg font-bold">제작 노트</h2>
              <button
                type="button"
                onClick={() => setNotes(true)}
                className="w-full rounded-xl border border-border bg-card p-4 text-left"
              >
                <div className="font-medium">지금까지 만든 것 정리</div>
                <div className="mt-0.5 text-xs text-muted">구조 · 서버의 하루 · 계산 방식 · 탭별 기능 · 전략 연구 결과 · 만든 순서 · 관리 방법</div>
              </button>
            </section>
          )}
          {!needLogin && (
            <section>
              <h2 className="mb-3 text-lg font-bold">도움말</h2>
              <Help />
            </section>
          )}
        </div>
      )}

      {notes && <Notes onClose={() => setNotes(false)} />}

      {toast && (
        <div className="fixed inset-x-0 bottom-24 z-40 fold:bottom-8 mx-auto w-fit rounded-full bg-foreground px-4 py-2 text-sm text-background">
          {toast}
        </div>
      )}

      {/* 폰: 아래 메뉴 / 폴드 펼침·태블릿(넓은 화면): 왼쪽 세로 메뉴 */}
      <nav
        aria-label="주 메뉴"
        className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur fold:inset-y-0 fold:right-auto fold:w-16 fold:border-r fold:border-t-0 fold:pb-0"
      >
        <div className="mx-auto grid max-w-3xl grid-cols-6 fold:mt-16 fold:grid-cols-1 fold:gap-2">
          {GROUPS.map((g) => {
            const on = groupOf(tab) === g;
            return (
              <button
                key={g.label}
                type="button"
                onClick={() => {
                  if (!on) setTab(g.tabs[0][0]);
                  window.scrollTo({ top: 0 });
                }}
                aria-current={on ? "page" : undefined}
                className={`relative flex flex-col items-center gap-1 py-2.5 text-[11px] transition-colors ${on ? "font-semibold text-accent" : "text-muted"}`}
              >
                <svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={on ? 2 : 1.7} strokeLinecap="round" strokeLinejoin="round">
                  <path d={g.icon} />
                </svg>
                {g.label}
                {g.label === "매매" && newsCount > 0 && (
                  <span className="absolute right-[calc(50%-20px)] top-1.5 min-w-4 rounded-full bg-critical px-1 text-[10px] font-semibold leading-4 text-background">
                    {newsCount > 9 ? "9+" : newsCount}
                  </span>
                )}
              </button>
            );
          })}
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
      <button className="w-full rounded-xl bg-accent py-3 font-semibold text-on-accent">들어가기</button>
    </form>
  );
}

function Saved({ onLoad, onScan }: { onLoad: (s: Strategy) => void; onScan: (s: Strategy) => void }) {
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
              className="shrink-0 rounded-lg bg-accent px-2.5 py-1.5 text-xs font-semibold text-on-accent"
              onClick={() => onScan(s)}
            >
              오늘 신호
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

const TREND_EXIT = `[청산]
목표 = 없음
손절 = 8
보유기간 = 120
추적손절 = 10
청산조건 = 종가 < 이평(종가, 20)`;

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
          <li>
            추적손절을 넣으면 매수 후 최고가(전날까지의 고가)에서 그 비율만큼 빠질 때 팝니다. 청산 조건식을 넣으면 장 마감 때 그 식이 참인
            다음 날 시가에 팝니다. 여러 청산 규칙 중 먼저 걸리는 쪽으로 청산하며, 목표를 &apos;없음&apos;으로 두면 성공률은 &apos;이익으로 끝난 비율&apos;이 됩니다.
          </li>
          <li>같은 날 목표와 손절에 모두 닿은 경우처럼 일봉으로 순서를 알 수 없으면 &apos;불확실&apos;로 표시하고, 기본 성공률은 불리한 쪽(보수적)으로 계산합니다.</li>
          <li>같은 매수·청산 규칙으로 &apos;아무 날이나 샀을 때&apos;의 성공률을 함께 계산해 조건의 효과를 비교합니다.</li>
        </ol>
      </section>
      <section className="rounded-xl border border-border bg-card p-4">
        <h2 className="mb-2 font-semibold">조건식 예시</h2>
        <pre className="overflow-x-auto rounded-lg bg-background p-3 text-xs">{EXAMPLE_TEXT}</pre>
        <p className="mt-2 text-muted">추세를 길게 따라가는 청산 예시 (목표 없이 추적손절·조건식으로 팔기):</p>
        <pre className="mt-1 overflow-x-auto rounded-lg bg-background p-3 text-xs">{TREND_EXIT}</pre>
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

function readTheme(): string {
  try {
    return (typeof window !== "undefined" && localStorage.getItem("sbt-theme")) || "dark";
  } catch {
    return "dark";
  }
}

function Settings({ onSaved }: { onSaved: () => void }) {
  const [url, setUrl] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [theme, setTheme] = useState(readTheme);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- 저장된 주소를 입력칸에 채운다
  useEffect(() => setUrl(apiUrl()), []);
  const pickTheme = (t: string) => {
    setTheme(t);
    try {
      localStorage.setItem("sbt-theme", t);
    } catch {}
    const root = document.documentElement;
    if (t === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", t);
  };
  return (
    <div className="space-y-4">
      <section className="space-y-3 rounded-xl border border-border bg-card p-4">
        <h2 className="font-semibold">화면</h2>
        <div className="flex gap-1 rounded-lg border border-border p-1" role="radiogroup" aria-label="화면 밝기">
          {[
            ["dark", "어둡게"],
            ["light", "밝게"],
            ["system", "기기 설정 따름"],
          ].map(([k, label]) => (
            <button
              key={k}
              type="button"
              role="radio"
              aria-checked={theme === k}
              onClick={() => pickTheme(k)}
              className={`flex-1 rounded-md py-2 text-sm ${theme === k ? "bg-accent text-on-accent font-semibold" : "text-muted"}`}
            >
              {label}
            </button>
          ))}
        </div>
      </section>
      <section className="space-y-3 rounded-xl border border-border bg-card p-4">
        <h2 className="font-semibold">분석 서버 주소</h2>
        <p className="text-sm text-muted">보통은 비워 두면 됩니다(이 화면을 연 주소의 서버를 사용). 화면을 다른 곳(예: Vercel)에 올린 경우에만 서버 주소를 넣으세요.</p>
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://..." autoCapitalize="off" />
        <div className="flex gap-2">
          <button
            type="button"
            className="flex-1 rounded-xl bg-accent py-2.5 font-semibold text-on-accent"
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
      <AISettings />
    </div>
  );
}

function AISettings() {
  const { status, reload } = useAIStatus();
  const [limitKrw, setLimitKrw] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [prevLimit, setPrevLimit] = useState<number | null>(null);
  if (status && status.limit_krw !== prevLimit) {
    setPrevLimit(status.limit_krw);
    setLimitKrw(String(status.limit_krw));
  }
  if (!status) return null;

  const save = async (body: Record<string, unknown>) => {
    try {
      await api("/api/ai/settings", { method: "PUT", body });
      setMsg("저장했습니다");
      reload();
    } catch (e) {
      setMsg((e as Error).message);
    }
  };

  return (
    <section className="space-y-3 rounded-xl border border-border bg-card p-4 text-sm">
      <h2 className="font-semibold">AI 질문·해설</h2>
      {!status.enabled ? (
        <p className="text-muted">
          꺼져 있음. 서버 <code>.env</code> 에 <code>ANTHROPIC_API_KEY</code> 를 넣고 <code>sudo systemctl restart sbt-api</code> 를
          실행하면 켜집니다. 키는 console.anthropic.com 에서 발급하고 크레딧을 선불 충전해 사용합니다.
        </p>
      ) : (
        <>
          <div className="tabular rounded-lg bg-background p-3">
            이번 달 사용: <b>{status.month_krw.toLocaleString("ko-KR")}원</b> / 한도 {status.limit_krw.toLocaleString("ko-KR")}원 ·{" "}
            {status.month_count}회
            <div className="mt-2 h-2 rounded-full bg-[var(--bar-track)]">
              <div
                className="h-2 rounded-full bg-accent"
                style={{ width: `${Math.min(100, (status.month_krw / Math.max(1, status.limit_krw)) * 100)}%` }}
              />
            </div>
            <div className="mt-1 text-xs text-faint">
              1회 예상: 질문 약 {status.estimate_krw.translate.toLocaleString("ko-KR")}원 · 해설 약{" "}
              {status.estimate_krw.explain.toLocaleString("ko-KR")}원 (환율 {status.krw_rate.toLocaleString("ko-KR")}원/$ 기준 추정)
            </div>
          </div>
          <label className="block">
            <span className="text-muted">모델</span>
            <select className="mt-1" value={status.model} onChange={(e) => save({ model: e.target.value })}>
              {status.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-muted">월 한도 (원)</span>
            <div className="mt-1 flex gap-2">
              <NumInput value={limitKrw} onChange={setLimitKrw} />
              <button
                type="button"
                className="shrink-0 rounded-lg border border-border px-3"
                onClick={() => save({ monthly_limit_usd: Number(limitKrw || 0) / status.krw_rate })}
              >
                저장
              </button>
            </div>
            <span className="mt-1 block text-xs text-faint">한도에 도달하면 그달에는 AI 기능이 멈춥니다. 조건식 검증은 계속 무료입니다.</span>
          </label>
          {msg && <p className="text-muted">{msg}</p>}
        </>
      )}
    </section>
  );
}
