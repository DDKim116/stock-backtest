"use client";

import { useCallback, useEffect, useState } from "react";
import { api, apiDownload } from "@/lib/api";
import { money, pct, price, retClass, today } from "@/lib/format";
import type {
  DatasetInfo,
  JournalPrefill,
  JournalStats,
  JournalTrade,
  Spec,
  Strategy,
  Trade,
} from "@/lib/types";
import { useBackClose } from "@/lib/useBackClose";
import JournalReview from "./JournalReview";
import TradeChart from "./TradeChart";
import NumInput from "./NumInput";

type View = "open" | "closed" | "stats";

export default function Journal({
  dataset,
  prefill,
  onPrefillUsed,
}: {
  dataset: DatasetInfo | undefined;
  prefill: JournalPrefill | null;
  onPrefillUsed: () => void;
}) {
  const ds = dataset?.name ?? "kr";
  const cur = dataset?.currency ?? "KRW";
  const [view, setView] = useState<View>("open");
  const [items, setItems] = useState<JournalTrade[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [adding, setAdding] = useState<JournalPrefill | "blank" | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);

  const load = useCallback(() => {
    api<JournalTrade[]>(`/api/journal?dataset=${ds}`)
      .then((r) => {
        setItems(r);
        setErr(null);
      })
      .catch((e) => setErr((e as Error).message));
  }, [ds]);

  useEffect(() => {
    load();
  }, [load]);

  // 오늘 신호에서 '기록'을 눌러 들어온 경우 바로 입력 화면을 연다
  useEffect(() => {
    if (prefill) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- 다른 화면에서 넘겨받은 값을 한 번 반영
      setAdding(prefill);
      onPrefillUsed();
    }
  }, [prefill, onPrefillUsed]);

  const closedDetail = useCallback(() => setOpenId(null), []);
  const back = useBackClose(openId !== null, closedDetail);
  const openTrade = (id: number) => {
    back.push();
    setOpenId(id);
  };

  const list = (items ?? []).filter((t) =>
    view === "open" ? t.pos.status !== "closed" : view === "closed" ? t.pos.status === "closed" : false,
  );
  const openN = (items ?? []).filter((t) => t.pos.status !== "closed").length;
  const closedN = (items ?? []).filter((t) => t.pos.status === "closed").length;
  const unreal = (items ?? []).reduce((a, t) => a + (t.pos.status === "open" ? (t.pos.unrealized ?? 0) : 0), 0);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold">실전투자 일지</h2>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => apiDownload(`/api/journal/export?dataset=${ds}`, "trading-journal.xlsx").catch((e) => setErr((e as Error).message))}
            className="rounded-lg border border-border bg-card px-3 py-1.5 text-sm"
          >
            엑셀
          </button>
          <button
            type="button"
            onClick={() => setAdding("blank")}
            className="rounded-lg bg-accent px-3 py-1.5 text-sm font-semibold text-white"
          >
            + 기록
          </button>
        </div>
      </div>
      <p className="text-xs text-muted">
        {dataset?.label} · 보유 {openN}종목 (평가손익 <span className={retClass(unreal)}>{money(unreal, cur, true)}</span>) · 청산 {closedN}건
      </p>

      {adding && (
        <NewTrade
          ds={ds}
          cur={cur}
          prefill={adding === "blank" ? null : adding}
          onCancel={() => setAdding(null)}
          onCreated={(t) => {
            setAdding(null);
            load();
            openTrade(t.id);
          }}
        />
      )}

      <div className="flex gap-1 rounded-lg bg-border/60 p-1">
        {(
          [
            ["open", `보유 중 ${openN}`],
            ["closed", `청산 ${closedN}`],
            ["stats", "분석"],
          ] as const
        ).map(([k, label]) => (
          <button
            key={k}
            type="button"
            onClick={() => setView(k)}
            className={`flex-1 rounded-md py-2 text-sm font-medium ${view === k ? "bg-card text-foreground shadow-sm" : "text-muted"}`}
          >
            {label}
          </button>
        ))}
      </div>

      {err && <p className="text-sm text-critical">⚠ {err}</p>}
      {!items && !err && <p className="text-sm text-muted">불러오는 중…</p>}

      {view !== "stats" && items && (
        <>
          {list.length === 0 ? (
            <p className="rounded-xl border border-border bg-card p-4 text-center text-sm text-muted">
              {view === "open"
                ? "보유 중인 기록이 없습니다. '+ 기록'을 누르거나 오늘 신호 목록에서 '기록'을 누르세요."
                : "청산한 기록이 없습니다."}
            </p>
          ) : (
            <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
              {list.map((t) => (
                <li key={t.id}>
                  <button type="button" onClick={() => openTrade(t.id)} className="flex w-full items-center gap-3 px-3 py-2.5 text-left">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">
                        {t.name} <span className="text-xs font-normal text-faint">{t.code}</span>
                      </div>
                      <div className="flex flex-wrap gap-x-2 text-xs text-muted">
                        {t.strategy_name && <span className="text-accent">{t.strategy_name}</span>}
                        <span className="tabular">
                          {t.pos.status === "closed"
                            ? `${t.pos.first_buy?.slice(2)} ~ ${t.pos.last_sell?.slice(2)}`
                            : `${t.pos.qty?.toLocaleString("ko-KR")}주 · 평단 ${price(t.pos.avg_buy, t.currency)}`}
                        </span>
                        {t.compare && (
                          <span className={t.compare.followed ? "text-accent" : "text-warning"}>
                            {t.compare.followed ? "전략대로" : "전략과 다름"}
                          </span>
                        )}
                        {t.pos.status === "error" && <span className="text-critical">기록 확인 필요</span>}
                      </div>
                    </div>
                    <div className="tabular shrink-0 text-right">
                      <div className={`text-sm font-semibold ${retClass(t.pos.ret)}`}>{pct(t.pos.ret, 2, true)}</div>
                      <div className={`text-xs ${retClass(t.pos.pnl)}`}>{money(t.pos.pnl, t.currency, true)}</div>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {view === "stats" && (
        <div className="space-y-4">
          <JournalReview ds={ds} />
          <Stats ds={ds} />
        </div>
      )}

      {openId !== null && (
        <Detail
          id={openId}
          onClose={back.close}
          onChanged={load}
          onDeleted={() => {
            back.close();
            load();
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 새 기록

function NewTrade({
  ds,
  cur,
  prefill,
  onCancel,
  onCreated,
}: {
  ds: string;
  cur: string;
  prefill: JournalPrefill | null;
  onCancel: () => void;
  onCreated: (t: JournalTrade) => void;
}) {
  const [stock, setStock] = useState<{ code: string; name: string; market: string } | null>(
    prefill ? { code: prefill.code, name: prefill.name, market: prefill.market } : null,
  );
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ code: string; name: string; market: string }[]>([]);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [stratKey, setStratKey] = useState<string>(prefill?.strategy_text ? "prefill" : "");
  const [signalDate, setSignalDate] = useState(prefill?.signal_date ?? "");
  const [date, setDate] = useState(prefill?.date ?? today());
  const [px, setPx] = useState(prefill?.price != null ? String(prefill.price) : "");
  const [qty, setQty] = useState("");
  const [memo, setMemo] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Strategy[]>("/api/strategies").then(setStrategies).catch(() => setStrategies([]));
  }, []);

  useEffect(() => {
    if (stock || !q.trim()) return;
    const t = setTimeout(() => {
      api<{ code: string; name: string; market: string }[]>(`/api/stocks?dataset=${ds}&q=${encodeURIComponent(q)}`)
        .then(setHits)
        .catch(() => setHits([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q, ds, stock]);

  const submit = async () => {
    if (!stock) return setErr("종목을 고르세요");
    const p = parseFloat(px.replace(/,/g, ""));
    const n = parseFloat(qty.replace(/,/g, ""));
    if (!(p > 0) || !(n > 0)) return setErr("매수가와 수량을 넣어 주세요");
    let strategy: { strategy_id: number | null; strategy_name: string; strategy_text: string } = {
      strategy_id: null,
      strategy_name: "",
      strategy_text: "",
    };
    if (stratKey === "prefill" && prefill)
      strategy = { strategy_id: prefill.strategy_id, strategy_name: prefill.strategy_name, strategy_text: prefill.strategy_text };
    else if (stratKey) {
      const st = strategies.find((s) => String(s.id) === stratKey);
      if (st) strategy = { strategy_id: st.id, strategy_name: st.name, strategy_text: st.text };
    }
    setBusy(true);
    try {
      const t = await api<JournalTrade>("/api/journal", {
        body: {
          dataset: ds,
          code: stock.code,
          signal_date: signalDate || null,
          memo,
          ...strategy,
          fills: [{ side: "buy", date, price: p, qty: n }],
        },
      });
      onCreated(t);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="space-y-3 rounded-xl border border-accent/50 bg-card p-3 text-sm">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">새 매매 기록</h3>
        <button type="button" onClick={onCancel} className="text-xs text-muted">
          취소
        </button>
      </div>

      <div className="space-y-1">
        <span className="text-xs text-muted">종목</span>
        {stock ? (
          <div className="flex items-center justify-between rounded-lg bg-background px-3 py-2">
            <span>
              {stock.name} <span className="text-xs text-faint">{stock.code} · {stock.market}</span>
            </span>
            {!prefill && (
              <button type="button" onClick={() => setStock(null)} className="text-xs text-accent">
                바꾸기
              </button>
            )}
          </div>
        ) : (
          <>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="종목명 또는 코드" aria-label="종목 검색" />
            {hits.length > 0 && (
              <ul className="max-h-48 divide-y divide-border overflow-y-auto rounded-lg border border-border">
                {hits.map((h) => (
                  <li key={h.code}>
                    <button
                      type="button"
                      onClick={() => {
                        setStock(h);
                        setHits([]);
                      }}
                      className="w-full px-3 py-2 text-left"
                    >
                      {h.name} <span className="text-xs text-faint">{h.code} · {h.market}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2">
        <label className="space-y-1">
          <span className="text-xs text-muted">전략 (선택)</span>
          <select value={stratKey} onChange={(e) => setStratKey(e.target.value)}>
            <option value="">없음</option>
            {prefill?.strategy_text && <option value="prefill">{prefill.strategy_name || "오늘 신호 조건"}</option>}
            {strategies.map((s) => (
              <option key={s.id} value={String(s.id)}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-xs text-muted">신호일 (선택)</span>
          <input type="date" value={signalDate} onChange={(e) => setSignalDate(e.target.value)} />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-muted">매수일</span>
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-muted">매수가 ({cur === "USD" ? "$" : "원"})</span>
          <NumInput decimal value={px} onChange={setPx} />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-muted">수량 (주)</span>
          <NumInput decimal value={qty} onChange={setQty} />
        </label>
        <label className="space-y-1">
          <span className="text-xs text-muted">메모 (선택)</span>
          <input value={memo} onChange={(e) => setMemo(e.target.value)} placeholder="산 이유" />
        </label>
      </div>
      <p className="text-[11px] text-faint">
        전략과 신호일을 넣으면 &apos;전략대로 했을 때&apos;와 내 매매를 비교해 줍니다. 추가 매수와 매도는 저장 후 기록 화면에서 넣습니다.
      </p>
      {err && <p className="text-critical">⚠ {err}</p>}
      <button
        type="button"
        onClick={submit}
        disabled={busy}
        className="w-full rounded-xl bg-accent py-2.5 font-semibold text-white disabled:opacity-50"
      >
        저장
      </button>
    </section>
  );
}

// ---------------------------------------------------------------- 상세

function Detail({
  id,
  onClose,
  onChanged,
  onDeleted,
}: {
  id: number;
  onClose: () => void;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const [t, setT] = useState<JournalTrade | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [side, setSide] = useState<"buy" | "sell">("sell");
  const [date, setDate] = useState(today());
  const [px, setPx] = useState("");
  const [qty, setQty] = useState("");
  const [memo, setMemo] = useState<string | null>(null);
  const [chart, setChart] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);

  const apply = (x: JournalTrade) => {
    setT(x);
    setErr(null);
    onChanged();
  };

  useEffect(() => {
    api<JournalTrade>(`/api/journal/${id}`)
      .then(setT)
      .catch((e) => setErr((e as Error).message));
  }, [id]);

  if (!t)
    return (
      <div className="fixed inset-0 z-50 bg-background p-4" role="dialog" aria-modal="true">
        <button type="button" onClick={onClose} className="rounded-lg border border-border px-3 py-1.5 text-sm">
          닫기
        </button>
        <p className="mt-4 text-sm text-muted">{err ? `⚠ ${err}` : "불러오는 중…"}</p>
      </div>
    );

  const c = t.currency;
  const p = t.pos;
  const cmp = t.compare;
  const s = cmp?.strategy;

  const addFill = async () => {
    const pr = parseFloat(px.replace(/,/g, ""));
    const sd = p.qty > 0 ? side : "buy";
    const n = parseFloat((qty || (sd === "sell" ? String(p.qty) : "")).replace(/,/g, ""));
    if (!(pr > 0) || !(n > 0)) return setErr("가격과 수량을 넣어 주세요");
    try {
      apply(await api<JournalTrade>(`/api/journal/${t.id}/fills`, { body: { side: sd, date, price: pr, qty: n } }));
      setPx("");
      setQty("");
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  const chartTrade: Trade = {
    code: t.code,
    name: t.name,
    market: t.market,
    signal_date: t.signal_date ?? p.first_buy ?? today(),
    signal_close: null,
    limit: s?.limit ?? null,
    fill_date: s?.fill_date ?? null,
    fill_price: s?.fill_price ?? null,
    outcome: s?.outcome ?? "",
    outcome_opt: s?.outcome ?? "",
    uncertain: false,
    exit_date: s ? (s.exit_date ?? null) : (p.last_sell ?? null),
    exit_price: s?.exit_price ?? null,
    days: null,
    ret: p.ret,
    ret_opt: p.ret,
    mfe: s?.mfe ?? null,
    mae: s?.mae ?? null,
  };

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-background" role="dialog" aria-modal="true">
      <header className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-background px-4 py-3">
        <div className="min-w-0">
          <div className="truncate font-semibold">
            {t.name} <span className="text-sm font-normal text-muted">{t.code}</span>
          </div>
          <div className="text-xs text-muted">
            {t.strategy_name || "전략 없음"}
            {t.signal_date && ` · 신호 ${t.signal_date}`}
          </div>
        </div>
        <button type="button" onClick={onClose} className="rounded-lg border border-border px-3 py-1.5 text-sm">
          닫기
        </button>
      </header>

      <div className="mx-auto max-w-3xl space-y-4 p-4 pb-16 text-sm">
        {p.error && <p className="rounded-lg border border-critical/40 p-2 text-critical">⚠ {p.error}</p>}

        <div className="grid grid-cols-2 gap-2">
          <Tile label={p.closed ? "실현 수익률" : "수익률 (평가 포함)"} value={pct(p.ret, 2, true)} cls={retClass(p.ret)} />
          <Tile label="손익" value={money(p.pnl, c, true)} cls={retClass(p.pnl)} />
          <Tile label="보유 수량" value={`${(p.qty ?? 0).toLocaleString("ko-KR")}주`} sub={p.avg_cost ? `주당 원가 ${price(p.avg_cost, c)}` : undefined} />
          <Tile
            label="평균 매수 / 매도가"
            value={`${price(p.avg_buy, c)} / ${price(p.avg_sell, c)}`}
            sub={`수수료·세금 ${money(p.fees, c)}`}
          />
        </div>
        {!p.closed && t.last_close != null && (
          <p className="text-xs text-muted">
            평가는 {t.last_date} 종가 {price(t.last_close, c)} 기준 (매도 수수료·세금 차감). 실현 {money(p.realized, c, true)} · 평가{" "}
            {money(p.unrealized, c, true)}
          </p>
        )}

        {cmp && s && (
          <section className="space-y-2 rounded-xl border border-border bg-card p-3">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold">전략대로 했다면</h3>
              <span className={`rounded px-1.5 py-0.5 text-xs ${cmp.followed ? "bg-accent-soft text-accent" : "bg-warning/10 text-warning"}`}>
                {cmp.verdict}
              </span>
            </div>
            <table className="tabular w-full text-xs">
              <thead className="text-muted">
                <tr>
                  <th className="py-1 text-left font-normal" />
                  <th className="py-1 text-right font-normal">전략</th>
                  <th className="py-1 text-right font-normal">나</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-t border-border">
                  <td className="py-1.5 text-muted">매수</td>
                  <td className="text-right">{s.fill_date ? `${s.fill_date.slice(5)} · ${price(s.fill_price, c)}` : "미체결"}</td>
                  <td className="text-right">
                    {p.first_buy?.slice(5)} · {price(p.avg_buy, c)}
                  </td>
                </tr>
                <tr className="border-t border-border">
                  <td className="py-1.5 text-muted">매도</td>
                  <td className="text-right">
                    {s.exit_date && s.outcome !== "진행중" ? `${s.exit_date.slice(5)} · ${price(s.exit_price, c)} (${s.outcome})` : s.outcome}
                  </td>
                  <td className="text-right">{p.last_sell ? `${p.last_sell.slice(5)} · ${price(p.avg_sell, c)}` : "보유 중"}</td>
                </tr>
                <tr className="border-t border-border">
                  <td className="py-1.5 text-muted">수익률</td>
                  <td className={`text-right font-semibold ${retClass(s.ret)}`}>{pct(s.ret, 2, true)}</td>
                  <td className={`text-right font-semibold ${retClass(p.ret)}`}>{pct(p.ret, 2, true)}</td>
                </tr>
              </tbody>
            </table>
            {cmp.ret_diff != null && (
              <p className="text-xs text-muted">
                전략 대비 <span className={retClass(cmp.ret_diff)}>{pct(cmp.ret_diff, 2, true)}p</span>
                {cmp.sell_timing && ` · ${cmp.sell_timing}`}
                {cmp.buy_diff_pct != null && ` · 매수가 차이 ${pct(cmp.buy_diff_pct, 1, true)}`}
              </p>
            )}
          </section>
        )}

        <button type="button" onClick={() => setChart(true)} className="w-full rounded-xl border border-border bg-card py-2 text-sm">
          차트 보기 (내 매수·매도 표시)
        </button>

        <section className="space-y-2">
          <h3 className="font-semibold">체결 기록</h3>
          <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
            {t.fills.map((f) => (
              <li key={f.id} className="flex items-center gap-2 px-3 py-2">
                <span className={`w-9 text-xs font-semibold ${f.side === "buy" ? "text-up" : "text-down"}`}>
                  {f.side === "buy" ? "매수" : "매도"}
                </span>
                <span className="tabular flex-1 text-xs">
                  {f.date} · {price(f.price, c)} × {f.qty.toLocaleString("ko-KR")}주
                </span>
                <button
                  type="button"
                  onClick={async () => {
                    try {
                      apply(await api<JournalTrade>(`/api/journal/fills/${f.id}`, { method: "DELETE" }));
                    } catch (e) {
                      setErr((e as Error).message);
                    }
                  }}
                  className="text-xs text-faint"
                >
                  삭제
                </button>
              </li>
            ))}
          </ul>

          <div className="space-y-2 rounded-xl border border-border bg-card p-3">
            <div className="flex gap-1 rounded-lg bg-border/60 p-1">
              {(p.qty > 0 ? (["buy", "sell"] as const) : (["buy"] as const)).map((k) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => setSide(k)}
                  className={`flex-1 rounded-md py-1.5 text-sm ${side === k || p.qty === 0 ? "bg-card font-semibold shadow-sm" : "text-muted"}`}
                >
                  {k === "buy" ? (p.qty > 0 ? "추가 매수" : "다시 매수") : "매도"}
                </button>
              ))}
            </div>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            <div className="grid grid-cols-2 gap-2">
              <NumInput decimal value={px} onChange={setPx} placeholder="가격" />
              <NumInput
                decimal
                value={qty}
                onChange={setQty}
                placeholder={side === "sell" ? `수량 (${p.qty?.toLocaleString("ko-KR")})` : "수량"}
              />
            </div>
            {side === "sell" && p.qty > 0 && <p className="text-[11px] text-faint">수량을 비우면 남은 {p.qty}주 전부 매도로 기록합니다.</p>}
            <button type="button" onClick={addFill} className="w-full rounded-lg bg-accent py-2 text-sm font-semibold text-white">
              {side === "buy" || p.qty === 0 ? "매수 기록" : "매도 기록"}
            </button>
          </div>
        </section>

        <section className="space-y-2">
          <h3 className="font-semibold">메모</h3>
          <textarea
            rows={3}
            value={memo ?? t.memo}
            onChange={(e) => setMemo(e.target.value)}
            placeholder="산 이유, 판 이유, 배운 점"
            className="!font-sans"
          />
          {memo !== null && memo !== t.memo && (
            <button
              type="button"
              onClick={async () => {
                apply(await api<JournalTrade>(`/api/journal/${t.id}`, { method: "PUT", body: { memo } }));
                setMemo(null);
              }}
              className="rounded-lg border border-accent px-3 py-1.5 text-xs font-semibold text-accent"
            >
              메모 저장
            </button>
          )}
        </section>

        {err && <p className="whitespace-pre-wrap text-critical">⚠ {err}</p>}

        <div className="pt-4">
          {!confirmDel ? (
            <button type="button" onClick={() => setConfirmDel(true)} className="text-xs text-critical">
              이 기록 삭제
            </button>
          ) : (
            <div className="flex items-center gap-2 text-xs">
              <span>정말 삭제할까요?</span>
              <button
                type="button"
                onClick={async () => {
                  await api(`/api/journal/${t.id}`, { method: "DELETE" });
                  onDeleted();
                }}
                className="rounded border border-critical px-2 py-1 text-critical"
              >
                삭제
              </button>
              <button type="button" onClick={() => setConfirmDel(false)} className="text-muted">
                취소
              </button>
            </div>
          )}
        </div>
      </div>

      {chart && (
        <ChartOverlay trade={chartTrade} t={t} onClose={() => setChart(false)} />
      )}
    </div>
  );
}

function ChartOverlay({ trade, t, onClose }: { trade: Trade; t: JournalTrade; onClose: () => void }) {
  const [spec, setSpec] = useState<Spec | null>(null);
  useEffect(() => {
    const text = t.strategy_text || "[신호]\n종가 > 0\n";
    api<{ spec: Spec }>("/api/parse", { body: { text } })
      .then((r) => setSpec(r.spec))
      .catch(() => api<{ spec: Spec }>("/api/parse", { body: { text: "[신호]\n종가 > 0\n" } }).then((r) => setSpec(r.spec)));
  }, [t.strategy_text]);
  if (!spec) return null;
  return (
    <TradeChart
      trade={trade}
      spec={t.strategy_text ? spec : { ...spec, exit: { ...spec.exit, target_pct: null, stop_pct: null } }}
      dataset={t.dataset}
      currency={t.currency}
      onClose={onClose}
      mine={t.fills.map((f) => ({ date: f.date, side: f.side, price: f.price }))}
      strategy={!!t.compare}
    />
  );
}

function Tile({ label, value, sub, cls = "" }: { label: string; value: string; sub?: string; cls?: string }) {
  return (
    <div className="rounded-xl border border-border bg-card p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className={`tabular mt-0.5 text-lg font-bold ${cls}`}>{value}</div>
      {sub && <div className="tabular text-[11px] text-faint">{sub}</div>}
    </div>
  );
}

// ---------------------------------------------------------------- 분석

function Stats({ ds }: { ds: string }) {
  const [s, setS] = useState<JournalStats | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api<JournalStats>(`/api/journal/stats?dataset=${ds}`)
      .then(setS)
      .catch((e) => setErr((e as Error).message));
  }, [ds]);
  if (err) return <p className="text-sm text-critical">⚠ {err}</p>;
  if (!s) return <p className="text-sm text-muted">계산 중…</p>;
  const c = s.currency;
  const a = s.closed;
  if (a.n === 0 && s.open.n === 0)
    return <p className="rounded-xl border border-border bg-card p-4 text-center text-sm text-muted">기록이 쌓이면 여기에 분석이 나옵니다.</p>;
  const maxAbs = Math.max(1, ...s.monthly.map((m) => Math.abs(m.total_pnl)));
  return (
    <div className="space-y-4 text-sm">
      <div className="grid grid-cols-2 gap-2">
        <Tile label="청산 손익 합계" value={money(a.total_pnl, c, true)} cls={retClass(a.total_pnl)} sub={`${a.n}건`} />
        <Tile label="승률" value={pct(a.win_rate)} sub={`평균 ${pct(a.avg_ret, 2, true)} · ${a.avg_days ?? "–"}일 보유`} />
        <Tile label="최고 / 최저" value={`${pct(a.best, 1, true)} / ${pct(a.worst, 1, true)}`} />
        <Tile label="보유 중 평가손익" value={money(s.open.unrealized, c, true)} cls={retClass(s.open.unrealized)} sub={`${s.open.n}종목 · 투입 ${money(s.open.invested, c)}`} />
      </div>

      {s.monthly.length > 0 && (
        <section className="space-y-2">
          <h3 className="font-semibold">월별 손익 (청산 기준)</h3>
          <ul className="space-y-1.5 rounded-xl border border-border bg-card p-3">
            {s.monthly.map((m) => (
              <li key={m.month} className="tabular flex items-center gap-2 text-xs">
                <span className="w-14 text-muted">{m.month}</span>
                <div className="relative h-3 flex-1 rounded bg-background">
                  <div
                    className={`absolute top-0 h-3 rounded ${m.total_pnl >= 0 ? "left-1/2 bg-up/70" : "right-1/2 bg-down/70"}`}
                    style={{ width: `${(Math.abs(m.total_pnl) / maxAbs) * 50}%` }}
                  />
                </div>
                <span className={`w-24 text-right ${retClass(m.total_pnl)}`}>{money(m.total_pnl, c, true)}</span>
                <span className="w-8 text-right text-faint">{m.n}건</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {s.by_strategy.length > 0 && (
        <section className="space-y-2">
          <h3 className="font-semibold">전략별 실전 성과</h3>
          <div className="overflow-x-auto rounded-xl border border-border bg-card">
            <table className="tabular w-full text-xs">
              <thead className="text-muted">
                <tr>
                  <th className="px-3 py-2 text-left font-normal">전략</th>
                  <th className="px-2 py-2 text-right font-normal">건수</th>
                  <th className="px-2 py-2 text-right font-normal">승률</th>
                  <th className="px-2 py-2 text-right font-normal">내 평균</th>
                  <th className="px-2 py-2 text-right font-normal">전략대로</th>
                  <th className="px-3 py-2 text-right font-normal">백테스트</th>
                </tr>
              </thead>
              <tbody>
                {s.by_strategy.map((r) => (
                  <tr key={r.name} className="border-t border-border">
                    <td className="max-w-[8rem] truncate px-3 py-2">{r.name}</td>
                    <td className="px-2 text-right">{r.n}</td>
                    <td className="px-2 text-right">{pct(r.win_rate)}</td>
                    <td className={`px-2 text-right ${retClass(r.avg_ret)}`}>{pct(r.avg_ret, 2, true)}</td>
                    <td className={`px-2 text-right ${retClass(r.strategy_avg_ret)}`}>{pct(r.strategy_avg_ret, 2, true)}</td>
                    <td className="px-3 text-right text-muted">
                      {r.backtest_avg_ret != null ? `${pct(r.backtest_success)} · ${pct(r.backtest_avg_ret, 2, true)}` : "–"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-faint">
            &apos;전략대로&apos;는 같은 신호를 전략 규칙대로 매매했다면 낸 평균 수익률, &apos;백테스트&apos;는 저장할 때의 과거 검증 성공률·평균입니다.
            내 평균이 전략대로보다 낮으면 매수·매도 타이밍에서 손해를 보고 있다는 뜻입니다.
          </p>
        </section>
      )}

      {(s.followed.n > 0 || s.deviated.n > 0) && (
        <section className="space-y-2">
          <h3 className="font-semibold">전략을 지켰을 때 vs 다르게 했을 때</h3>
          <div className="grid grid-cols-2 gap-2">
            <Tile label={`전략대로 (${s.followed.n}건)`} value={pct(s.followed.avg_ret, 2, true)} cls={retClass(s.followed.avg_ret)} sub={`승률 ${pct(s.followed.win_rate)}`} />
            <Tile label={`다르게 (${s.deviated.n}건)`} value={pct(s.deviated.avg_ret, 2, true)} cls={retClass(s.deviated.avg_ret)} sub={`승률 ${pct(s.deviated.win_rate)}`} />
          </div>
        </section>
      )}
    </div>
  );
}
