"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

interface FredInfo {
  saved: boolean;
  key_tail?: string;
  saved_at?: string;
  collected: { files: number; updated?: string };
}

// 설정 탭의 'FRED 경제 지표': 미국 연준 FRED API 키 저장(다시 보이지 않음)·연결 시험·받은 자료 현황
export default function FredSettings() {
  const [info, setInfo] = useState<FredInfo | null>(null);
  const [edit, setEdit] = useState(false);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      setInfo(await api<FredInfo>("/api/fred"));
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 처음 한 번 저장 상태를 불러온다
    load();
  }, [load]);

  const run = async (f: () => Promise<{ ok: boolean; text: string }>) => {
    setBusy(true);
    setMsg(null);
    try {
      setMsg(await f());
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const save = () =>
    run(async () => {
      setInfo(await api<FredInfo>("/api/fred", { method: "PUT", body: { key } }));
      setKey("");
      setEdit(false);
      const t = await api<{ ok: boolean; message: string }>("/api/fred/test", { body: {} });
      return { ok: t.ok, text: t.ok ? `저장했습니다 · ${t.message}` : `저장했지만 시험이 실패했습니다: ${t.message}` };
    });

  const test = () =>
    run(async () => {
      const t = await api<{ ok: boolean; message: string }>("/api/fred/test", { body: {} });
      return { ok: t.ok, text: t.message };
    });

  const remove = () =>
    run(async () => {
      setInfo(await api<FredInfo>("/api/fred", { method: "DELETE" }));
      return { ok: true, text: "키를 지웠습니다." };
    });

  const c = info?.collected;
  return (
    <section className="space-y-3 rounded-xl border border-border bg-card p-4">
      <h2 className="font-semibold">FRED 경제 지표</h2>
      <p className="text-sm text-muted">
        미국 연준 세인트루이스(fred.stlouisfed.org) API 키입니다. 금리·금리차·신용 스프레드·실업률·물가·환율을 받아 계기판과 연구에 씁니다. 저장한 키는 서버에만 있고 화면에 다시 나오지 않습니다.
      </p>
      <div className="flex items-center justify-between gap-2 text-sm">
        <span className="font-medium">
          인증키{" "}
          {info?.saved ? (
            <span className="text-xs text-accent">저장됨 · …{info.key_tail} · {info.saved_at}</span>
          ) : (
            <span className="text-xs text-faint">{info ? "없음" : "…"}</span>
          )}
        </span>
        {info?.saved && !edit && (
          <div className="flex gap-1">
            <button type="button" disabled={busy} onClick={test} className="rounded-lg bg-accent px-3 py-1 text-xs font-semibold text-on-accent disabled:opacity-50">
              연결 시험
            </button>
            <button type="button" disabled={busy} onClick={() => setEdit(true)} className="rounded-lg border border-border px-3 py-1 text-xs">
              바꾸기
            </button>
            <button type="button" disabled={busy} onClick={remove} className="rounded-lg border border-border px-3 py-1 text-xs text-critical">
              지우기
            </button>
          </div>
        )}
      </div>
      {info && (!info.saved || edit) && (
        <div className="space-y-2">
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="API 키 32자 붙여넣기"
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
          <div className="flex gap-2">
            {edit && (
              <button type="button" onClick={() => setEdit(false)} className="rounded-xl border border-border px-4 py-2">
                취소
              </button>
            )}
            <button
              type="button"
              disabled={busy || !key.trim()}
              onClick={save}
              className="flex-1 rounded-xl bg-accent py-2 font-semibold text-on-accent disabled:opacity-50"
            >
              FRED 키 저장
            </button>
          </div>
        </div>
      )}
      {c && (
        <p className="text-xs text-muted">
          {c.files
            ? `받은 지표 ${c.files}개 · 마지막 ${c.updated}`
            : "아직 받은 자료가 없습니다. 키를 저장한 뒤 연구 탭 → 'FRED 경제 지표 받기' → 다시 돌리기"}
        </p>
      )}
      {msg && <p className={`whitespace-pre-wrap text-xs ${msg.ok ? "text-accent" : "text-critical"}`}>{msg.text}</p>}
    </section>
  );
}
