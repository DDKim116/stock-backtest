"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { money, pct, retClass, today } from "@/lib/format";
import type { PaperView, ScanRow, Spec } from "@/lib/types";

// 오늘 신호 목록의 '모의' → 모의 카테고리를 골라 담는다 (없으면 새로 만든다)
export default function PaperPick({
  row,
  spec,
  dataset,
  currency,
  onClose,
  onDone,
}: {
  row: ScanRow;
  spec: Spec;
  dataset: string;
  currency: string;
  onClose: () => void;
  onDone: (msg: string) => void;
}) {
  const usd = currency === "USD";
  const [books, setBooks] = useState<PaperView[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState(spec.name || `전략 ${today().slice(5).replace("-", "/")}`);
  const [capital, setCapital] = useState(usd ? "10000" : "10000000");
  const [perTrade, setPerTrade] = useState(usd ? "1000" : "1000000");
  const [auto, setAuto] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const t = await api<{ text: string }>("/api/to-text", { body: spec });
        const q = new URLSearchParams({ dataset, spec_text: t.text });
        const list = (await api<PaperView[]>(`/api/paper/books?${q}`)).filter((b) => !b.book.archived);
        list.sort((a, b) => Number(!!b.same_spec) - Number(!!a.same_spec));
        setBooks(list);
        if (list.length === 0) setCreating(true);
      } catch (e) {
        setErr((e as Error).message);
      }
    })();
  }, [spec, dataset]);

  const pick = async (bookId: number, bookName: string) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api<{ already: boolean; retro: boolean }>(`/api/paper/books/${bookId}/trades`, {
        body: { code: row.code, signal_date: row.signal_date },
      });
      onDone(
        r.already
          ? `이미 '${bookName}'에 담겨 있습니다`
          : r.retro
            ? `'${bookName}'에 담았습니다 · 매수 시점이 지나 '소급'으로 표시되고 통계에서는 빠집니다`
            : `'${bookName}'에 모의로 담았습니다`,
      );
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    setBusy(true);
    setErr(null);
    try {
      const v = await api<PaperView>("/api/paper/books", {
        body: { name: name.trim(), dataset, spec, capital: Number(capital), per_trade: Number(perTrade), auto },
      });
      await pick(v.book.id, v.book.name);
    } catch (e) {
      setErr((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center" onClick={onClose}>
      <div
        className="max-h-[85dvh] w-full max-w-lg overflow-y-auto rounded-t-2xl bg-card p-4 pb-[calc(1rem+env(safe-area-inset-bottom))] sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="mb-3 flex items-start justify-between gap-2">
          <div>
            <div className="font-semibold">모의로 담기</div>
            <div className="text-xs text-muted">
              {row.name} {row.code} · 신호 {row.signal_date}
            </div>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg border border-border px-3 py-1 text-sm">
            닫기
          </button>
        </div>

        {!books && !err && <p className="text-sm text-muted">불러오는 중…</p>}

        {books && books.length > 0 && !creating && (
          <ul className="space-y-2">
            {books.map((b) => (
              <li key={b.book.id}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => pick(b.book.id, b.book.name)}
                  className="w-full rounded-xl border border-border bg-background p-3 text-left disabled:opacity-50"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">{b.book.name}</span>
                    {b.account?.ret != null && (
                      <span className={`tabular text-sm ${retClass(b.account.ret)}`}>{pct(b.account.ret, 2, true)}</span>
                    )}
                  </div>
                  <div className="mt-0.5 text-xs">
                    {b.same_spec ? (
                      <span className="text-accent">지금 조건과 같음</span>
                    ) : (
                      <span className="text-warning">조건 다름 · 이 카테고리의 매수·청산 규칙으로 계산</span>
                    )}
                    <span className="text-muted">
                      {" "}
                      · 내 선택 {b.groups?.picked.n ?? 0}건{b.book.auto ? " · 자동 담기" : ""}
                    </span>
                  </div>
                </button>
              </li>
            ))}
            <li>
              <button
                type="button"
                onClick={() => setCreating(true)}
                className="w-full rounded-xl border border-dashed border-border p-3 text-sm text-accent"
              >
                + 새 카테고리 만들기
              </button>
            </li>
          </ul>
        )}

        {creating && (
          <div className="space-y-3 text-sm">
            <p className="text-xs text-muted">
              지금 검색한 조건(신호·매수·청산)을 그대로 고정해 새 카테고리를 만듭니다. 조건을 바꿔 시험하려면 카테고리를 새로 만드세요.
            </p>
            <label className="block">
              <span className="text-muted">이름</span>
              <input value={name} onChange={(e) => setName(e.target.value)} className="mt-1" />
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="text-muted">총 자본금 ({usd ? "달러" : "원"})</span>
                <input inputMode="numeric" value={capital} onChange={(e) => setCapital(e.target.value.replace(/[^\d.]/g, ""))} className="mt-1" />
                <span className="text-xs text-faint">{money(Number(capital) || 0, currency)}</span>
              </label>
              <label className="block">
                <span className="text-muted">1회 매수금</span>
                <input inputMode="numeric" value={perTrade} onChange={(e) => setPerTrade(e.target.value.replace(/[^\d.]/g, ""))} className="mt-1" />
                <span className="text-xs text-faint">{money(Number(perTrade) || 0, currency)}</span>
              </label>
            </div>
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} className="mt-1 !w-auto" />
              <span>
                자동 담기 <span className="text-xs text-muted">— 이 조건의 신호를 매일 전부 &apos;전체 신호&apos;로 담아, 내가 고른 종목과 비교합니다</span>
              </span>
            </label>
            <p className="text-xs text-faint">자본금은 나중에 입금·출금으로, 1회 매수금도 언제든 바꿀 수 있습니다.</p>
            <div className="flex gap-2">
              {books && books.length > 0 && (
                <button type="button" onClick={() => setCreating(false)} className="rounded-xl border border-border px-4 py-2.5">
                  뒤로
                </button>
              )}
              <button
                type="button"
                disabled={busy || !name.trim() || !Number(capital) || !Number(perTrade)}
                onClick={create}
                className="flex-1 rounded-xl bg-accent py-2.5 font-semibold text-white disabled:opacity-50"
              >
                만들고 담기
              </button>
            </div>
          </div>
        )}
        {err && <p className="mt-3 whitespace-pre-wrap text-sm text-critical">⚠ {err}</p>}
      </div>
    </div>
  );
}
