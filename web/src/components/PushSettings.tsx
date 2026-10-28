"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Strategy } from "@/lib/types";

interface PushPrefs {
  paper: boolean;
  signals: number[];
  signals_empty: boolean;
}
interface PushInfo {
  key: string;
  prefs: PushPrefs;
  devices: { endpoint: string; device: string; created: string }[];
}

function keyBytes(b64: string): Uint8Array<ArrayBuffer> {
  const s = atob((b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(s.length));
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

function deviceName(): string {
  const ua = navigator.userAgent;
  if (/Android/i.test(ua)) return /Mobile/i.test(ua) ? "안드로이드 휴대폰" : "안드로이드 태블릿";
  if (/iPhone/i.test(ua)) return "아이폰";
  if (/iPad|Macintosh/i.test(ua) && navigator.maxTouchPoints > 1) return "아이패드";
  return "컴퓨터";
}

const supported = () =>
  typeof window !== "undefined" && window.isSecureContext && "serviceWorker" in navigator && "PushManager" in window;

// 설정 탭의 '휴대폰 알림': 이 기기 켜기·끄기, 받을 알림 고르기, 시험 알림
export default function PushSettings() {
  const [info, setInfo] = useState<PushInfo | null>(null);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [mine, setMine] = useState<string | null>(null); // 이 기기의 구독 주소
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const ok = supported();

  const load = useCallback(async () => {
    try {
      const [i, s] = await Promise.all([api<PushInfo>("/api/push"), api<Strategy[]>("/api/strategies")]);
      setInfo(i);
      setStrategies(s);
      if (supported()) {
        const reg = await navigator.serviceWorker.getRegistration("/");
        const sub = await reg?.pushManager.getSubscription();
        setMine(sub && i.devices.some((d) => d.endpoint === sub.endpoint) ? sub.endpoint : null);
      }
    } catch (e) {
      setMsg((e as Error).message);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 처음 한 번 서버의 알림 설정을 불러온다
    load();
  }, [load]);

  const run = async (f: () => Promise<string | void>) => {
    setBusy(true);
    setMsg(null);
    try {
      const m = await f();
      if (m) setMsg(m);
    } catch (e) {
      const m = (e as Error).message;
      setMsg(
        /permission denied|NotAllowed/i.test(m)
          ? "알림 권한이 막혀 있습니다. 크롬 주소창 왼쪽 아이콘 → 권한(사이트 설정) → 알림을 '허용'으로 바꾼 뒤 다시 누르세요."
          : /push service/i.test(m)
            ? "푸시 서비스에 연결하지 못했습니다. 인터넷 연결을 확인하고 잠시 뒤 다시 누르세요."
            : m,
      );
    } finally {
      setBusy(false);
    }
  };

  const enable = () =>
    run(async () => {
      if (!info) return;
      const perm = await Notification.requestPermission();
      if (perm !== "granted")
        return "알림이 막혀 있습니다. 크롬 주소창 왼쪽 아이콘 → 권한(사이트 설정) → 알림을 '허용'으로 바꾼 뒤 다시 누르세요.";
      const reg = await navigator.serviceWorker.register("/sw.js");
      await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(info.key) });
      const i = await api<PushInfo>("/api/push/subscribe", { body: { subscription: sub.toJSON(), device: deviceName() } });
      setInfo(i);
      setMine(sub.endpoint);
      return "이 기기에서 알림을 켰습니다. '시험 알림'으로 확인해 보세요.";
    });

  const disable = () =>
    run(async () => {
      const reg = await navigator.serviceWorker.getRegistration("/");
      const sub = await reg?.pushManager.getSubscription();
      const endpoint = sub?.endpoint ?? mine;
      await sub?.unsubscribe();
      if (endpoint) setInfo(await api<PushInfo>("/api/push/unsubscribe", { body: { endpoint } }));
      setMine(null);
      return "이 기기의 알림을 껐습니다.";
    });

  const save = (p: PushPrefs) =>
    run(async () => {
      setInfo(await api<PushInfo>("/api/push/prefs", { method: "PUT", body: p }));
    });

  const test = () =>
    run(async () => {
      const r = await api<{ sent: number }>("/api/push/test", { body: {} });
      return `시험 알림을 ${r.sent}대에 보냈습니다. 몇 초 안에 안 오면 휴대폰 설정에서 크롬 알림이 켜져 있는지 확인하세요.`;
    });

  const p = info?.prefs;
  const toggleSignal = (id: number) =>
    p && save({ ...p, signals: p.signals.includes(id) ? p.signals.filter((x) => x !== id) : [...p.signals, id] });

  return (
    <section className="space-y-3 rounded-xl border border-border bg-card p-4">
      <h2 className="font-semibold">휴대폰 알림</h2>
      <p className="text-sm text-muted">
        새 거래일 데이터가 들어오면(평일 저녁 임시값, 늦으면 다음 날 아침) 한 번 알려 줍니다. 밤 11시~아침 7시에는 보내지 않습니다.
      </p>

      {!ok && (
        <p className="rounded-lg bg-background p-3 text-sm text-warning">
          이 브라우저에서는 알림을 받을 수 없습니다. 안드로이드 크롬에서 https 주소(Tailscale 주소)로 열어 주세요.
        </p>
      )}

      {ok && info && (
        <div className="flex flex-wrap items-center gap-2">
          {mine ? (
            <>
              <span className="rounded-full bg-accent/10 px-3 py-1 text-sm text-accent">이 기기 알림 켜짐</span>
              <button type="button" disabled={busy} onClick={test} className="rounded-xl bg-accent px-4 py-2 text-sm font-semibold text-on-accent disabled:opacity-50">
                시험 알림
              </button>
              <button type="button" disabled={busy} onClick={disable} className="rounded-xl border border-border px-4 py-2 text-sm disabled:opacity-50">
                이 기기 끄기
              </button>
            </>
          ) : (
            <button type="button" disabled={busy} onClick={enable} className="w-full rounded-xl bg-accent py-2.5 font-semibold text-on-accent disabled:opacity-50">
              이 기기에서 알림 켜기
            </button>
          )}
        </div>
      )}

      {p && (
        <div className="space-y-3 border-t border-border pt-3 text-sm">
          <label className="flex items-start gap-2">
            <input type="checkbox" checked={p.paper} disabled={busy} onChange={(e) => save({ ...p, paper: e.target.checked })} className="mt-1 !w-auto" />
            <span>
              모투 소식 <span className="text-xs text-muted">— 내가 고른 종목의 매수 체결·미체결·매도</span>
            </span>
          </label>
          <div>
            <div className="mb-1">
              저녁 신호 <span className="text-xs text-muted">— 고른 전략에 그날 신호가 난 종목</span>
            </div>
            {strategies.length === 0 ? (
              <p className="text-xs text-faint">저장한 전략이 없습니다. 전략 탭에 저장하면 여기서 고를 수 있습니다.</p>
            ) : (
              <ul className="space-y-1">
                {strategies.map((s) => (
                  <li key={s.id}>
                    <label className="flex items-start gap-2">
                      <input type="checkbox" checked={p.signals.includes(s.id)} disabled={busy} onChange={() => toggleSignal(s.id)} className="mt-1 !w-auto" />
                      <span>{s.name}</span>
                    </label>
                  </li>
                ))}
              </ul>
            )}
            {p.signals.length > 0 && (
              <label className="mt-2 flex items-start gap-2 text-xs text-muted">
                <input type="checkbox" checked={p.signals_empty} disabled={busy} onChange={(e) => save({ ...p, signals_empty: e.target.checked })} className="mt-0.5 !w-auto" />
                <span>신호가 없는 날에도 알림 (데이터가 들어왔는지 확인용)</span>
              </label>
            )}
          </div>
          {info.devices.length > 0 && (
            <p className="text-xs text-faint">
              알림 받는 기기: {info.devices.map((d) => d.device || "기기").join(", ")}
            </p>
          )}
        </div>
      )}
      {msg && <p className="whitespace-pre-wrap text-sm text-muted">{msg}</p>}
    </section>
  );
}
