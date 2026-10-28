"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { pct, retClass, today } from "@/lib/format";
import type { PaperView, ScanRow, Spec } from "@/lib/types";
import NumInput from "./NumInput";

// 오늘 신호 목록의 '모의' → 모의 카테고리를 골라 담는다 (없으면 새로 만든다)
export default function PaperPick({
  row,
  spec,
  dataset,
  currency,
  onClose,
  onDone,
}: {
  row: ScanRow | null; // null 이면 담지 않고 카테고리만 만든다
  spec: Spec;
  dataset: string;
  currency: string;
  onClose: () => void;
  onDone: (msg: string) => void;
}) {
  const usd = currency === "USD";
  const [books, setBooks] = useState<PaperView[] | null>(row ? null : []);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(!row);
  const [name, setName] = useState(spec.name || `전략 ${today().slice(5).replace("-", "/")}`);
  const [capital, setCapital] = useState(usd ? "10000" : "10000000");
  const [perTrade, setPerTrade] = useState(usd ? "1000" : "1000000");
  const [auto, setAuto] = useState(true);

  useEffect(() => {
    if (!row) return;
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
  }, [spec, dataset, row]);

  const pick = async (bookId: number, bookName: string) => {
    if (!row) {
      onDone(`'${bookName}' 카테고리를 만들었습니다. 신호가 나오면 자동으로 담기거나, 오늘 신호에서 '모의'로 담을 수 있습니다`);
      return;
    }
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
            <div className="font-semibold">{row ? "모의로 담기" : "새 모의 카테고리"}</div>
            <div className="text-xs text-muted">
              {row ? `${row.name} ${row.code} · 신호 ${row.signal_date}` : "지금 검증 화면의 조건으로 만듭니다"}
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
              {row ? "지금 검색한" : "검증 화면의"} 조건(신호·매수·청산)을 그대로 고정해 새 카테고리를 만듭니다. 조건을 바꿔 시험하려면 카테고리를 새로 만드세요.
            </p>
            <p className="rounded-lg bg-background p-2 text-xs">
              <b>매수 방식:</b>{" "}
              {spec.entry.type === "limit"
                ? `지정가 ${spec.entry.price} · 신호 다음 날부터 ${spec.entry.valid_days}거래일 동안 주문 유지 (그 안에 가격이 오면 체결)`
                : spec.entry.type === "next_open"
                  ? "다음 거래일 시가 (바로 체결)"
                  : "신호일 종가"}
              {spec.entry.type === "limit" && (
                <span className="block text-faint">주문 기간을 바꾸려면 검증 화면 매수의 &apos;주문 유효기간&apos;을 고친 뒤 새 카테고리로 만드세요.</span>
              )}
            </p>
            <label className="block">
              <span className="text-muted">이름</span>
              <input value={name} onChange={(e) => setName(e.target.value)} className="mt-1" />
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className="text-muted">총 자본금 ({usd ? "달러" : "원"})</span>
                <NumInput decimal={usd} value={capital} onChange={setCapital} className="mt-1" />
              </label>
              <label className="block">
                <span className="text-muted">1회 매수금 ({usd ? "달러" : "원"})</span>
                <NumInput decimal={usd} value={perTrade} onChange={setPerTrade} className="mt-1" />
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
                className="flex-1 rounded-xl bg-accent py-2.5 font-semibold text-on-accent disabled:opacity-50"
              >
                {row ? "만들고 담기" : "만들기"}
              </button>
            </div>
          </div>
        )}
        {err && <p className="mt-3 whitespace-pre-wrap text-sm text-critical">⚠ {err}</p>}
      </div>
    </div>
  );
}
