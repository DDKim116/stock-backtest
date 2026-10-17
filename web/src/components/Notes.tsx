"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

// 설정 탭의 '제작 노트': 서버의 docs/notes.html 을 로그인 후 받아 화면 가득 보여 준다
export default function Notes({ onClose }: { onClose: () => void }) {
  const [html, setHtml] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api<{ html: string }>("/api/notes")
      .then((r) => setHtml(r.html))
      .catch((e) => setErr((e as Error).message));
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-background" role="dialog" aria-modal="true">
      <header className="flex items-center justify-between border-b border-border px-4 py-3 pt-[calc(0.75rem+env(safe-area-inset-top))]">
        <div className="font-semibold">제작 노트</div>
        <button type="button" onClick={onClose} className="rounded-lg border border-border px-3 py-1.5 text-sm">
          닫기
        </button>
      </header>
      {err && <p className="p-4 text-sm text-critical">⚠ {err}</p>}
      {!html && !err && <p className="p-4 text-sm text-muted">불러오는 중…</p>}
      {html && (
        <iframe
          title="제작 노트"
          srcDoc={html}
          sandbox="allow-scripts"
          allow="clipboard-write"
          className="min-h-0 w-full flex-1 border-0"
        />
      )}
    </div>
  );
}
