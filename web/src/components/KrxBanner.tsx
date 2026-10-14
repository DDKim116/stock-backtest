"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface KrxStatus {
  remind: boolean;
  remind_from: string;
  errors: { kind: string; label: string; at: string; message: string; auth: boolean; last_ok: string | null }[];
}

const SERVICES = ["유가증권 일별매매정보", "코스닥 일별매매정보", "KOSPI 시리즈 일별시세정보", "KOSDAQ 시리즈 일별시세정보"];
const KEY_CMD =
  'read -rsp "새 KRX 키 붙여넣기(화면에 안 보임): " K; echo; sed -i "s|^KRX_API_KEY=.*|KRX_API_KEY=$K|" ~/stock-backtest/.env; unset K; echo 바꿈';
const RUN_CMD = "sudo systemctl start sbt-update.service; journalctl -u sbt-update.service -n 15 --no-pager";

function Cmd({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="flex items-start gap-2">
      <code className="block min-w-0 flex-1 select-all break-all rounded bg-background px-2 py-1 text-[11px]">{text}</code>
      <button
        type="button"
        onClick={() => {
          navigator.clipboard?.writeText(text).then(
            () => {
              setDone(true);
              setTimeout(() => setDone(false), 1500);
            },
            () => {},
          );
        }}
        className="shrink-0 rounded border border-border px-2 py-1 text-[11px]"
      >
        {done ? "복사됨" : "복사"}
      </button>
    </div>
  );
}

function Guide() {
  return (
    <ol className="mt-2 list-decimal space-y-2 pl-5 text-xs leading-relaxed text-foreground">
      <li>
        <a href="https://openapi.krx.co.kr" target="_blank" rel="noreferrer" className="text-accent underline">
          openapi.krx.co.kr
        </a>{" "}
        에 로그인 → <b>마이페이지 → API 이용현황(인증키 관리)</b>로 갑니다.
      </li>
      <li>
        아래 서비스의 이용 기간을 확인하고, 끝났거나 곧 끝나면 <b>연장</b>(연장 버튼이 없으면 다시 <b>이용신청</b>, 기간 12M)합니다.
        <ul className="mt-1 list-disc pl-4 text-muted">
          {SERVICES.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      </li>
      <li>
        <b>인증키</b>도 만료되어 새로 받았다면 Termius로 서버에 접속해 아래 명령을 붙여넣고, 물어보면 새 키를 붙여넣습니다 (화면에 안
        보이는 게 정상). 키가 그대로면 건너뛰세요.
        <Cmd text={KEY_CMD} />
      </li>
      <li>
        승인이 나면 바로 받아 보려면 아래 명령을 실행합니다 (안 해도 매일 아침 8시 20분에 자동으로 받고, 멈춘 사이 빠진 날도 채웁니다).
        <Cmd text={RUN_CMD} />
      </li>
      <li>
        끝나면 이 띠의 <b>연장 완료</b>를 누르세요. 다음 알림은 1년 뒤(만료 2주 전)에 다시 뜹니다. 갱신이 멈췄다는 빨간 띠는 다음 갱신이
        성공하면 저절로 사라집니다.
      </li>
    </ol>
  );
}

export default function KrxBanner() {
  const [st, setSt] = useState<KrxStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<KrxStatus>("/api/krx-status")
      .then(setSt)
      .catch(() => setSt(null));
  }, []);

  if (!st || (!st.remind && st.errors.length === 0)) return null;
  const err = st.errors.length > 0;

  return (
    <div
      role="status"
      className={`mb-4 rounded-xl border p-3 text-sm ${err ? "border-critical/40 bg-critical/10" : "border-warning/40 bg-warning/10"}`}
    >
      {err ? (
        <div className="space-y-1">
          <div className="font-semibold text-critical">⚠ KRX 데이터 갱신이 멈췄습니다</div>
          {st.errors.map((e) => (
            <p key={e.kind} className="text-xs text-foreground">
              <b>{e.label}</b>: {e.auth ? "이용 기간이 끝났거나 인증키가 만료된 것 같습니다." : e.message}
              {e.last_ok && <span className="text-muted"> (마지막 성공 {e.last_ok.slice(0, 10)})</span>}
            </p>
          ))}
          <p className="text-xs text-muted">이미 받은 데이터로 검증은 계속 됩니다. 최근 날짜만 더해지지 않습니다.</p>
        </div>
      ) : (
        <div className="space-y-1">
          <div className="font-semibold">🔔 KRX OpenAPI 이용 기간(12개월)이 곧 끝납니다</div>
          <p className="text-xs text-muted">
            끝나면 국내 종목·지수 데이터 갱신이 멈춥니다. 미리 연장해 두세요. ({st.remind_from}부터 알림)
          </p>
        </div>
      )}
      <div className="mt-2 flex flex-wrap gap-2">
        <button type="button" onClick={() => setOpen((v) => !v)} className="rounded-lg border border-border bg-card px-3 py-1.5 text-xs">
          {open ? "방법 닫기" : "연장·재승인 방법"}
        </button>
        {st.remind && (
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              if (!confirm("KRX 사이트에서 연장(재승인)을 마쳤나요? 다음 알림은 1년 뒤에 뜹니다.")) return;
              setBusy(true);
              try {
                await api("/api/krx-status/renewed", { method: "POST" });
                setSt(await api<KrxStatus>("/api/krx-status"));
              } finally {
                setBusy(false);
              }
            }}
            className="rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
          >
            연장 완료
          </button>
        )}
      </div>
      {open && <Guide />}
    </div>
  );
}
