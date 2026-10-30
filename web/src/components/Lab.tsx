"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

interface Report {
  name: string;
  cmd: string | null;
  at: string;
  size: number;
}
interface Item {
  cmd: string;
  area: "국장" | "미장";
  title: string;
  question: string;
  conclusion: string;
  minutes: number;
  latest: Report | null;
  count: number;
}
interface Running {
  cmd: string;
  started: string;
  running: boolean;
  code: number | null;
  tail: string[];
}
interface LabView {
  items: Item[];
  reports: Report[];
  running: Running | null;
}

const AREAS = ["전체", "국장", "미장"] as const;
// 2026-10-30T12:05 → 10/30 12:05
const when = (iso: string) => `${+iso.slice(5, 7)}/${+iso.slice(8, 10)} ${iso.slice(11, 16)}`;

// 연구 탭: 지금까지 돌린 시뮬레이션 (질문 → 결론 → 결과 파일), 앱에서 다시 돌리기
export default function Lab() {
  const [v, setV] = useState<LabView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [area, setArea] = useState<(typeof AREAS)[number]>("전체");
  const [open, setOpen] = useState<Item | null>(null);

  const load = useCallback(() => {
    api<LabView>("/api/lab")
      .then((r) => {
        setV(r);
        setErr(null);
      })
      .catch((e) => setErr((e as Error).message));
  }, []);
  useEffect(load, [load]);

  // 돌고 있으면 5초마다 진행 상황
  const running = v?.running?.running ?? false;
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [running, load]);

  const run = async (it: Item) => {
    if (!confirm(`'${it.title}'을(를) 서버에서 다시 돌릴까요? (약 ${it.minutes}분)`)) return;
    try {
      await api("/api/lab/run", { body: { cmd: it.cmd } });
      load();
    } catch (e) {
      alert((e as Error).message);
    }
  };

  const items = (v?.items ?? []).filter((x) => area === "전체" || x.area === area);
  const titleOf = (cmd: string) => v?.items.find((x) => x.cmd === cmd)?.title ?? cmd;

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        궁금한 걸 질문 → 시뮬레이션 → 결론. 새 질문은 Claude에게 물어보면 여기에 하나씩 쌓여요.
      </p>

      {v?.running && (
        <section
          className={`rounded-xl border p-3 text-sm ${v.running.running ? "border-accent bg-accent-soft" : v.running.code === 0 ? "border-border bg-card" : "border-critical bg-card"}`}
        >
          <div className="font-medium">
            {v.running.running ? "돌리는 중 · " : v.running.code === 0 ? "끝남 · " : "실패 · "}
            {titleOf(v.running.cmd)}
            <span className="ml-1 text-xs font-normal text-muted">{when(v.running.started)} 시작</span>
          </div>
          {v.running.tail.length > 0 && (
            <pre className="mt-2 overflow-x-auto whitespace-pre text-[11px] leading-relaxed text-muted [font-family:var(--font-num)]">
              {v.running.tail.join("\n")}
            </pre>
          )}
        </section>
      )}

      <div className="flex gap-1 rounded-xl border border-border bg-card p-1" role="tablist">
        {AREAS.map((a) => (
          <button
            key={a}
            type="button"
            role="tab"
            aria-selected={area === a}
            onClick={() => setArea(a)}
            className={`flex-1 rounded-lg py-1.5 text-sm ${area === a ? "bg-accent-soft font-semibold text-accent" : "text-muted"}`}
          >
            {a}
          </button>
        ))}
      </div>

      {err && <p className="text-sm text-critical">⚠ {err}</p>}
      {!v && !err && <p className="text-sm text-muted">불러오는 중…</p>}

      <div className="grid gap-3 fold:grid-cols-2">
        {items.map((it) => (
          <article key={it.cmd} className="flex flex-col rounded-xl border border-border bg-card p-4">
            <div className="flex items-start justify-between gap-2">
              <h3 className="font-semibold leading-snug">{it.title}</h3>
              <span
                className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] ${it.area === "미장" ? "border-down text-down" : "border-up text-up"}`}
              >
                {it.area}
              </span>
            </div>
            <p className="mt-1 text-sm text-muted">Q. {it.question}</p>
            <p className="mt-2 border-l-2 border-accent pl-2.5 text-sm leading-relaxed">{it.conclusion}</p>
            <div className="mt-auto flex items-center justify-between gap-2 pt-3">
              <span className="text-xs text-faint">
                {it.latest ? `최근 ${when(it.latest.at)} · ${it.count}번` : "아직 결과 없음"} · 약 {it.minutes}분
              </span>
              <span className="flex gap-1.5">
                {it.latest && (
                  <button
                    type="button"
                    onClick={() => setOpen(it)}
                    className="rounded-lg border border-border px-2.5 py-1 text-xs"
                  >
                    결과 보기
                  </button>
                )}
                <button
                  type="button"
                  disabled={running}
                  onClick={() => run(it)}
                  className="rounded-lg border border-border px-2.5 py-1 text-xs disabled:opacity-40"
                >
                  다시 돌리기
                </button>
              </span>
            </div>
          </article>
        ))}
      </div>

      {open && v && (
        <ReportView item={open} reports={v.reports.filter((r) => r.cmd === open.cmd)} onClose={() => setOpen(null)} />
      )}
    </div>
  );
}

// 결과 파일 보기 (화면 가득). 지난 결과도 골라 볼 수 있다
function ReportView({ item, reports, onClose }: { item: Item; reports: Report[]; onClose: () => void }) {
  const [name, setName] = useState(reports[0]?.name ?? "");
  const [text, setText] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [wrap, setWrap] = useState(false);
  const [copied, setCopied] = useState(false);

  // 결과 글 전체를 클립보드로 (Claude 에게 붙여 넣기 좋게 제목·결론도 위에)
  const copy = async () => {
    const body = text[name];
    if (body == null) return;
    const all = `[${item.title}] ${name}\n${body}`;
    try {
      await navigator.clipboard.writeText(all);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = all;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  useEffect(() => {
    if (!name || text[name] != null) return;
    api<{ text: string }>(`/api/lab/report/${encodeURIComponent(name)}`)
      .then((r) => setText((t) => ({ ...t, [name]: r.text })))
      .catch((e) => setErr((e as Error).message));
  }, [name, text]);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-background" role="dialog" aria-modal="true">
      <header className="flex items-center gap-2 border-b border-border px-4 py-3 pt-[calc(0.75rem+env(safe-area-inset-top))]">
        <div className="min-w-0 flex-1 truncate font-semibold">{item.title}</div>
        {reports.length > 1 && (
          <select value={name} onChange={(e) => setName(e.target.value)} className="!w-auto py-1 text-xs">
            {reports.map((r) => (
              <option key={r.name} value={r.name}>
                {when(r.at)}
              </option>
            ))}
          </select>
        )}
        <button
          type="button"
          onClick={copy}
          disabled={text[name] == null}
          className={`rounded-lg border px-2.5 py-1.5 text-xs disabled:opacity-40 ${copied ? "border-good text-good" : "border-accent text-accent"}`}
        >
          {copied ? "복사됨" : "복사"}
        </button>
        <button
          type="button"
          onClick={() => setWrap((w) => !w)}
          className={`rounded-lg border px-2.5 py-1.5 text-xs ${wrap ? "border-accent text-accent" : "border-border"}`}
        >
          줄바꿈
        </button>
        <button type="button" onClick={onClose} className="rounded-lg border border-border px-3 py-1.5 text-sm">
          닫기
        </button>
      </header>
      <div className="border-b border-border px-4 py-2 text-xs text-muted">
        <span className="font-medium text-foreground">결론</span> {item.conclusion}
      </div>
      {err && <p className="p-4 text-sm text-critical">⚠ {err}</p>}
      {text[name] == null && !err && <p className="p-4 text-sm text-muted">불러오는 중…</p>}
      {text[name] != null && (
        <pre
          className={`min-h-0 flex-1 overflow-auto p-4 text-[11px] leading-relaxed [font-family:var(--font-num)] fold:text-xs ${wrap ? "whitespace-pre-wrap break-all" : "whitespace-pre"}`}
        >
          {text[name]}
        </pre>
      )}
    </div>
  );
}
