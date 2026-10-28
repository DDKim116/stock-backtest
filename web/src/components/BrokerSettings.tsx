"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

type Mode = "mock" | "real";
interface KeyState {
  saved: boolean;
  key_tail?: string;
  saved_at?: string;
}
interface BrokerInfo {
  ip: string | null;
  kiwoom: Record<Mode, KeyState>;
}
const LABEL: Record<Mode, string> = { mock: "모의투자", real: "실전" };

// 설정 탭의 '키움 연결': 허용 IP 로 넣을 서버 주소, 키 저장(다시 보이지 않음), 연결 시험(삼성전자 현재가)
export default function BrokerSettings() {
  const [info, setInfo] = useState<BrokerInfo | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setInfo(await api<BrokerInfo>("/api/broker"));
    } catch (e) {
      setMsg((e as Error).message);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 처음 한 번 저장 상태를 불러온다
    load();
  }, [load]);

  const copyIp = async () => {
    if (!info?.ip) return;
    try {
      await navigator.clipboard.writeText(info.ip);
      setMsg("서버 주소를 복사했습니다");
    } catch {
      setMsg(`복사가 안 되면 직접 적어 주세요: ${info.ip}`);
    }
  };

  return (
    <section className="space-y-3 rounded-xl border border-border bg-card p-4">
      <h2 className="font-semibold">키움 연결</h2>
      <p className="text-sm text-muted">
        실시간 시세를 받기 위한 키움 REST API 키입니다. 지금은 시세 조회만 하고 주문은 하지 않습니다. 저장한 키는 서버에만 있고 화면에 다시 나오지 않습니다.
      </p>
      <div className="rounded-lg bg-background p-3 text-sm">
        <div className="text-xs text-muted">키움 사이트의 &apos;허용 IP&apos;에 넣을 서버 주소</div>
        <div className="mt-1 flex items-center justify-between gap-2">
          <span className="tabular font-semibold">{info ? (info.ip ?? "확인 못 함") : "…"}</span>
          {info?.ip && (
            <button type="button" onClick={copyIp} className="rounded-lg border border-border px-3 py-1 text-xs">
              복사
            </button>
          )}
        </div>
      </div>
      {info && (["mock", "real"] as Mode[]).map((m) => <KeyCard key={m} mode={m} state={info.kiwoom[m]} onChange={setInfo} />)}
      {msg && <p className="text-sm text-muted">{msg}</p>}
    </section>
  );
}

function KeyCard({ mode, state, onChange }: { mode: Mode; state: KeyState; onChange: (i: BrokerInfo) => void }) {
  const [edit, setEdit] = useState(false);
  const [appkey, setAppkey] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const run = async (f: () => Promise<string>) => {
    setBusy(true);
    setMsg(null);
    try {
      setMsg({ ok: true, text: await f() });
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const save = () =>
    run(async () => {
      onChange(await api<BrokerInfo>(`/api/broker/kiwoom/${mode}`, { method: "PUT", body: { appkey, secretkey: secret } }));
      setAppkey("");
      setSecret("");
      setEdit(false);
      return "저장했습니다. '연결 시험'을 눌러 확인하세요.";
    });

  const test = () =>
    run(async () => {
      const r = await api<{ name: string; price: number | null; change_pct: number | null }>(`/api/broker/kiwoom/${mode}/test`, { body: {} });
      const chg = r.change_pct == null ? "" : ` (${r.change_pct > 0 ? "+" : ""}${r.change_pct}%)`;
      return `연결 성공 · ${r.name} 현재가 ${r.price?.toLocaleString("ko-KR") ?? "–"}원${chg}`;
    });

  const remove = () =>
    run(async () => {
      onChange(await api<BrokerInfo>(`/api/broker/kiwoom/${mode}`, { method: "DELETE" }));
      return "키를 지웠습니다.";
    });

  return (
    <div className="space-y-2 rounded-lg border border-border p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">
          {LABEL[mode]} 키{" "}
          {state.saved ? (
            <span className="text-xs text-accent">저장됨 · …{state.key_tail} · {state.saved_at}</span>
          ) : (
            <span className="text-xs text-faint">없음</span>
          )}
        </span>
        {state.saved && !edit && (
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
      {(!state.saved || edit) && (
        <div className="space-y-2">
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="App Key 붙여넣기"
            value={appkey}
            onChange={(e) => setAppkey(e.target.value)}
          />
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="Secret Key 붙여넣기"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
          />
          <div className="flex gap-2">
            {edit && (
              <button type="button" onClick={() => setEdit(false)} className="rounded-xl border border-border px-4 py-2">
                취소
              </button>
            )}
            <button
              type="button"
              disabled={busy || !appkey.trim() || !secret.trim()}
              onClick={save}
              className="flex-1 rounded-xl bg-accent py-2 font-semibold text-on-accent disabled:opacity-50"
            >
              {LABEL[mode]} 키 저장
            </button>
          </div>
        </div>
      )}
      {msg && <p className={`whitespace-pre-wrap text-xs ${msg.ok ? "text-accent" : "text-critical"}`}>{msg.text}</p>}
    </div>
  );
}
