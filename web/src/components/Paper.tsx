"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { money, pct, price, retClass, today } from "@/lib/format";
import type { PaperGroup, PaperNews, PaperTrade, PaperView, Spec, Trade } from "@/lib/types";
import { useBackClose } from "@/lib/useBackClose";
import { useLiveQuotes } from "@/lib/useLiveQuotes";
import PaperPick from "./PaperPick";
import TradeChart from "./TradeChart";
import NumInput from "./NumInput";

const STATUS_STYLE: Record<string, string> = {
  pending: "bg-accent text-on-accent",
  holding: "bg-accent-soft text-accent",
  closed: "bg-border text-muted",
  nofill: "bg-border text-faint",
  nodata: "bg-border text-faint",
};

const FILTERS: { key: string; label: string }[] = [
  { key: "all", label: "전체" },
  { key: "holding", label: "보유 중" },
  { key: "pending", label: "주문 대기" },
  { key: "closed", label: "청산" },
  { key: "nofill", label: "미체결" },
  { key: "skip", label: "건너뜀" },
  { key: "retro", label: "소급" },
];

function asTrade(t: PaperTrade): Trade {
  return {
    code: t.code,
    name: t.name,
    market: t.market,
    signal_date: t.signal_date,
    signal_close: t.signal_close ?? null,
    limit: t.limit ?? null,
    fill_date: t.fill_date ?? null,
    fill_price: t.fill_price ?? null,
    outcome: t.outcome ?? t.label,
    outcome_opt: t.outcome ?? t.label,
    uncertain: false,
    exit_date: t.exit_date ?? null,
    exit_price: t.exit_price ?? null,
    days: t.days ?? null,
    ret: t.ret ?? t.ret_now ?? null,
    ret_opt: t.ret ?? t.ret_now ?? null,
    mfe: t.mfe ?? null,
    mae: t.mae ?? null,
  };
}

function Tile({ label, value, sub, cls }: { label: string; value: string; sub?: string; cls?: string }) {
  return (
    <div className="rounded-xl border border-border bg-card p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className={`tabular mt-0.5 text-lg font-semibold ${cls ?? ""}`}>{value}</div>
      {sub && <div className="tabular text-xs text-faint">{sub}</div>}
    </div>
  );
}

// ---------------------------------------------------------------- 목록
const NEWS_STYLE: Record<string, string> = {
  fill: "text-accent",
  exit: "",
  wait: "text-warning",
  expire: "text-faint",
};
const NEWS_ICON: Record<string, string> = { fill: "🟢", exit: "🏁", wait: "⏳", expire: "⚪" };

// 모투 소식: 내 선택 종목의 매수 체결·미체결·주문 끝남·매도
function NewsCard({ news, onOpen }: { news: PaperNews; onOpen: (bookId: number) => void }) {
  const [more, setMore] = useState(false);
  const fresh = news.items.filter((x) => x.new);
  const shown = more ? news.items.slice(0, 40) : fresh.length ? fresh.slice(0, 20) : news.items.slice(0, 5);
  if (news.items.length === 0) return null;
  return (
    <section className="rounded-xl border border-border bg-card p-3">
      <div className="mb-1.5 flex items-center justify-between">
        <h3 className="font-semibold">
          모투 소식 {fresh.length > 0 && <span className="ml-1 rounded-full bg-critical px-1.5 text-xs text-white">새 {fresh.length}</span>}
        </h3>
        <span className="text-[11px] text-faint">
          {Object.entries(news.as_of)
            .map(([k, d]) => `${k === "us" ? "미국" : k === "kr" ? "국내" : k} ${d.slice(5)}`)
            .join(" · ")}{" "}
          기준
        </span>
      </div>
      {fresh.length === 0 && !more && <p className="mb-1 text-xs text-muted">새 소식이 없습니다. 최근 소식:</p>}
      <ul className="divide-y divide-border">
        {shown.map((x, i) => (
          <li key={`${x.trade_id}-${x.kind}-${i}`}>
            <button type="button" onClick={() => onOpen(x.book_id)} className="flex w-full items-start gap-2 py-1.5 text-left text-sm">
              <span aria-hidden>{NEWS_ICON[x.kind]}</span>
              <span className="min-w-0 flex-1">
                <span className="font-medium">{x.name}</span>{" "}
                <span className={`${NEWS_STYLE[x.kind]} ${x.kind === "exit" ? retClass(x.ret) : ""}`}>{x.text}</span>
                <span className="block text-[11px] text-faint">
                  {x.date} · {x.book}
                  {x.retro ? " · 소급" : ""}
                </span>
              </span>
              {x.new && <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-critical" aria-label="새 소식" />}
            </button>
          </li>
        ))}
      </ul>
      {news.items.length > shown.length && !more && (
        <button type="button" onClick={() => setMore(true)} className="mt-1 text-xs text-muted underline">
          지난 소식 더 보기
        </button>
      )}
    </section>
  );
}

export default function Paper({
  onLoadSpec,
  onFlash,
  onNewsSeen,
  spec,
  dataset,
  currency,
}: {
  onLoadSpec: (s: Spec) => void;
  onFlash: (m: string) => void;
  onNewsSeen: () => void;
  spec: Spec; // 검증 화면의 지금 조건 (새 카테고리용)
  dataset: string;
  currency: string;
}) {
  const [creating, setCreating] = useState(false);
  const [news, setNews] = useState<PaperNews | null>(null);
  // 소식은 한 번 보여 준 뒤 '읽음'으로 (빨간 점은 이번 화면에서는 그대로 보임)
  useEffect(() => {
    api<PaperNews>("/api/paper/news")
      .then((n) => {
        setNews(n);
        if (n.unseen > 0) api("/api/paper/news/seen", { method: "POST" }).then(onNewsSeen, () => {});
      })
      .catch(() => {});
  }, [onNewsSeen]);
  const [list, setList] = useState<PaperView[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ds, setDs] = useState<string>("all");
  const [showArchived, setShowArchived] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const closeDetail = useCallback(() => setOpenId(null), []);
  const back = useBackClose(openId != null, closeDetail);

  const load = useCallback(() => {
    api<PaperView[]>("/api/paper/books")
      .then((r) => {
        setList(r);
        setErr(null);
      })
      .catch((e) => setErr((e as Error).message));
  }, []);
  useEffect(load, [load]);

  if (openId != null)
    return (
      <Detail
        id={openId}
        onBack={() => {
          back.close();
          load();
        }}
        onLoadSpec={onLoadSpec}
        onFlash={onFlash}
      />
    );

  const items = (list ?? []).filter((v) => (ds === "all" || v.book.dataset === ds) && (showArchived || !v.book.archived));
  const datasets = [...new Set((list ?? []).map((v) => v.book.dataset))];

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold">모의투자</h2>
        {datasets.length > 1 && (
          <div className="flex rounded-lg border border-border p-0.5 text-xs">
            {[["all", "전체"], ...datasets.map((d) => [d, d === "kr" ? "국내" : d === "us" ? "미국" : d])].map(([k, l]) => (
              <button
                key={k}
                type="button"
                onClick={() => setDs(k)}
                className={`rounded-md px-2.5 py-1 ${ds === k ? "bg-accent text-on-accent" : "text-muted"}`}
              >
                {l}
              </button>
            ))}
          </div>
        )}
      </div>
      {news && (
        <NewsCard
          news={news}
          onOpen={(id) => {
            back.push();
            setOpenId(id);
          }}
        />
      )}
      {err && <p className="text-sm text-critical">⚠ {err}</p>}
      {!list && !err && <p className="text-sm text-muted">불러오는 중…</p>}
      {list && list.length === 0 && (
        <div className="space-y-2 rounded-xl border border-border bg-card p-4 text-sm text-muted">
          <p className="font-medium text-foreground">아직 모의 카테고리가 없습니다.</p>
          <p>
            검증 화면에서 조건을 정하고 <b>오늘 신호</b>를 누른 뒤, 종목 옆 <b>모의</b> 버튼으로 담으면 카테고리가 만들어집니다. 담은 종목은
            매일 실제 시세로 전략 규칙대로 사고팔린 것으로 계산됩니다.
          </p>
        </div>
      )}
      <ul className="space-y-2">
        {items.map((v) => {
          const a = v.account;
          const g = v.groups?.picked;
          return (
            <li key={v.book.id}>
              <button
                type="button"
                onClick={() => {
                  back.push();
                  setOpenId(v.book.id);
                }}
                className="w-full rounded-xl border border-border bg-card p-3 text-left"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate font-semibold">{v.book.name}</div>
                    <div className="text-xs text-muted">
                      {v.book.dataset === "us" ? "미국" : "국내"} · {v.book.created_at.slice(0, 10)}부터
                      {v.book.auto ? " · 자동 담기" : ""}
                      {v.book.archived ? " · 보관됨" : ""}
                    </div>
                  </div>
                  {a && (
                    <div className="tabular shrink-0 text-right">
                      <div className={`font-semibold ${retClass(a.ret)}`}>{pct(a.ret, 2, true)}</div>
                      <div className="text-xs text-faint">{money(a.equity, v.book.currency)}</div>
                    </div>
                  )}
                </div>
                {g && (
                  <div className="tabular mt-1.5 flex flex-wrap gap-x-3 text-xs text-muted">
                    <span>내 선택 {g.n}건</span>
                    <span>보유 {g.holding}</span>
                    <span>청산 {g.closed}</span>
                    {g.win_rate != null && <span>승률 {g.win_rate}%</span>}
                    {g.avg_ret != null && <span className={retClass(g.avg_ret)}>평균 {pct(g.avg_ret, 2, true)}</span>}
                  </div>
                )}
                {v.error && <div className="mt-1 text-xs text-critical">⚠ {v.error}</div>}
              </button>
            </li>
          );
        })}
      </ul>
      <button
        type="button"
        onClick={() => setCreating(true)}
        className="w-full rounded-xl border border-dashed border-border p-3 text-sm text-accent"
      >
        + 새 카테고리 (검증 화면의 조건으로)
      </button>
      {creating && (
        <PaperPick
          row={null}
          spec={spec}
          dataset={dataset}
          currency={currency}
          onClose={() => setCreating(false)}
          onDone={(m) => {
            setCreating(false);
            onFlash(m);
            load();
          }}
        />
      )}
      {(list ?? []).some((v) => v.book.archived) && (
        <button type="button" onClick={() => setShowArchived((x) => !x)} className="text-xs text-muted underline">
          {showArchived ? "보관한 카테고리 숨기기" : "보관한 카테고리 보기"}
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 상세
function GroupTable({ g, cur }: { g: NonNullable<PaperView["groups"]>; cur: string }) {
  const rows: [string, PaperGroup | undefined][] = [
    ["내 선택", g.picked],
    ["전체 신호", g.all],
    ["고르지 않은 것", g.unpicked],
  ];
  const few = g.picked.closed < 30;
  return (
    <section className="rounded-xl border border-border bg-card p-3">
      <h3 className="mb-2 font-semibold">내 선택 vs 전체 신호</h3>
      <table className="tabular w-full text-right text-xs">
        <thead className="text-muted">
          <tr>
            <th className="py-1 text-left font-normal" />
            <th className="font-normal">청산</th>
            <th className="font-normal">승률</th>
            <th className="font-normal">평균 수익</th>
            <th className="font-normal">보유 중 평균</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, x]) =>
            x ? (
              <tr key={label} className="border-t border-border">
                <td className="py-1.5 text-left">{label}</td>
                <td>{x.closed}</td>
                <td>{x.win_rate == null ? "–" : `${x.win_rate}%`}</td>
                <td className={retClass(x.avg_ret)}>{pct(x.avg_ret, 2, true)}</td>
                <td className={retClass(x.avg_now)}>{pct(x.avg_now, 1, true)}</td>
              </tr>
            ) : null,
          )}
          {g.edge && (
            <tr className="border-t border-border font-semibold">
              <td className="py-1.5 text-left">선택 효과</td>
              <td />
              <td className={retClass(g.edge.win_rate)}>{g.edge.win_rate == null ? "–" : `${g.edge.win_rate > 0 ? "+" : ""}${g.edge.win_rate}%p`}</td>
              <td className={retClass(g.edge.avg_ret)}>{g.edge.avg_ret == null ? "–" : `${g.edge.avg_ret > 0 ? "+" : ""}${g.edge.avg_ret}%p`}</td>
              <td />
            </tr>
          )}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] text-faint">
        거래당 수익률(비용 차감) 기준입니다. 전체 신호는 자본금 한도 없이 모두 샀다고 보고 계산합니다.
        {few && " 내 선택 청산이 30건이 안 돼 아직은 우연일 수 있습니다."}
        {cur && ""}
      </p>
    </section>
  );
}

function Detail({
  id,
  onBack,
  onLoadSpec,
  onFlash,
}: {
  id: number;
  onBack: () => void;
  onLoadSpec: (s: Spec) => void;
  onFlash: (m: string) => void;
}) {
  const [v, setV] = useState<PaperView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<"picked" | "all">("picked");
  const [filter, setFilter] = useState("all");
  const [limit, setLimit] = useState(100);
  const [chart, setChart] = useState<PaperTrade | null>(null);
  const closeChart = useCallback(() => setChart(null), []);
  const chartBack = useBackClose(!!chart, closeChart);
  const [act, setAct] = useState<PaperTrade | null>(null);
  const [manage, setManage] = useState(false);
  const [cashKind, setCashKind] = useState<"deposit" | "withdraw" | "per_trade">("deposit");
  const [cashAmt, setCashAmt] = useState("");
  const [cashDate, setCashDate] = useState(today());

  const load = useCallback(() => {
    api<PaperView>(`/api/paper/books/${id}`)
      .then((r) => {
        setV(r);
        setErr(null);
      })
      .catch((e) => setErr((e as Error).message));
  }, [id]);
  useEffect(load, [load]);
  // 국내 모투의 보유 종목은 키움 현재가로 지금 수익률 (키가 없으면 전날 종가 기준 그대로)
  const live = useLiveQuotes(
    (v?.trades ?? []).filter((t) => t.status === "holding").map((t) => t.code),
    v?.book.dataset === "kr",
  );

  const call = async (path: string, init: { method?: string; body?: unknown }) => {
    try {
      const r = await api<PaperView>(path, init);
      if (r && r.book) setV(r);
      else load();
      setErr(null);
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  if (!v) return <p className="text-sm text-muted">{err ? `⚠ ${err}` : "불러오는 중…"}</p>;
  const b = v.book;
  const cur = b.currency;
  const a = v.account;
  const all = v.trades ?? [];
  const base = view === "picked" ? all.filter((t) => t.picked) : all.filter((t) => t.auto);
  const counts: Record<string, number> = { all: base.length };
  for (const t of base) {
    counts[t.status] = (counts[t.status] ?? 0) + 1;
    if (t.skip && t.picked) counts.skip = (counts.skip ?? 0) + 1;
    if (t.retro) counts.retro = (counts.retro ?? 0) + 1;
  }
  const shown = base
    .filter((t) =>
      filter === "all" ? true : filter === "skip" ? !!t.skip && !!t.picked : filter === "retro" ? !!t.retro : t.status === filter,
    )
    .sort((x, y) => (x.signal_date < y.signal_date ? 1 : -1));
  const bt = b.backtest;
  const pg = v.groups?.picked;

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <button type="button" onClick={onBack} className="rounded-lg border border-border px-3 py-1.5 text-sm">
          ← 목록
        </button>
        <div className="min-w-0 flex-1">
          <div className="truncate font-bold">{b.name}</div>
          <div className="text-xs text-muted">
            {b.dataset === "us" ? "미국" : "국내"} · {b.created_at.slice(0, 10)}부터 · {v.as_of} 기준
          </div>
        </div>
      </div>
      {(err || v.error) && <p className="text-sm text-critical">⚠ {err || v.error}</p>}

      {a && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Tile label="평가금액" value={money(a.equity, cur)} sub={`투입 ${money(a.contributed, cur)}`} />
          <Tile label="누적 손익" value={pct(a.ret, 2, true)} sub={money(a.pnl, cur, true)} cls={retClass(a.ret)} />
          <Tile label="현금" value={money(a.cash, cur)} sub={`1회 매수금 ${money(a.per_trade, cur)}`} />
          <Tile label="보유 종목" value={`${a.holding}개`} sub={`평가 ${money(a.positions_value, cur)}`} />
          {pg && (
            <Tile
              label="내 선택 승률"
              value={pg.win_rate == null ? "–" : `${pg.win_rate}%`}
              sub={`청산 ${pg.closed}건 · 평균 ${pct(pg.avg_ret, 2, true)}`}
            />
          )}
          <Tile label="최대 낙폭" value={pct(a.mdd, 1, true)} sub="입출금 효과 제외" cls={retClass(a.mdd)} />
        </div>
      )}

      {bt && pg && (
        <p className="rounded-xl border border-border bg-card p-3 text-xs text-muted">
          만들 때 검증 결과: 성공률 <b className="text-foreground">{bt.success_rate ?? "–"}%</b> · 평균{" "}
          <b className={retClass(bt.avg_ret)}>{pct(bt.avg_ret, 2, true)}</b> ({bt.n_complete?.toLocaleString("ko-KR")}건)
          {pg.closed > 0 && (
            <>
              {" "}
              → 모의 내 선택: 승률 <b className="text-foreground">{pg.win_rate ?? "–"}%</b> · 평균{" "}
              <b className={retClass(pg.avg_ret)}>{pct(pg.avg_ret, 2, true)}</b> ({pg.closed}건)
            </>
          )}
          <span className="block text-faint">검증의 성공률은 목표 도달 기준, 모의 승률은 수익으로 끝난 비율입니다.</span>
        </p>
      )}

      {b.auto && v.groups?.all ? <GroupTable g={v.groups} cur={cur} /> : null}

      {/* 거래 목록 */}
      <section className="space-y-2">
        {b.auto ? (
          <div className="flex rounded-lg border border-border p-0.5 text-sm">
            {(
              [
                ["picked", `내 선택 ${all.filter((t) => t.picked).length}`],
                ["all", `전체 신호 ${all.filter((t) => t.auto).length}`],
              ] as const
            ).map(([k, l]) => (
              <button
                key={k}
                type="button"
                onClick={() => {
                  setView(k);
                  setFilter("all");
                }}
                className={`flex-1 rounded-md py-1 ${view === k ? "bg-accent text-on-accent" : "text-muted"}`}
              >
                {l}
              </button>
            ))}
          </div>
        ) : null}
        <div className="flex gap-1 overflow-x-auto text-xs">
          {FILTERS.filter((f) => f.key === "all" || counts[f.key]).map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              className={`shrink-0 rounded-full border px-2.5 py-1 ${filter === f.key ? "border-accent text-accent" : "border-border text-muted"}`}
            >
              {f.label} {counts[f.key] ?? 0}
            </button>
          ))}
        </div>
        {shown.length === 0 ? (
          <p className="rounded-xl border border-border bg-card p-4 text-center text-sm text-muted">
            {base.length === 0
              ? view === "picked"
                ? "아직 담은 종목이 없습니다. 검증 → 오늘 신호에서 '모의'를 눌러 담으세요."
                : "아직 자동으로 담긴 신호가 없습니다. 데이터가 갱신되면 매일 담깁니다."
              : "이 분류에 해당하는 거래가 없습니다."}
          </p>
        ) : (
          <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card">
            {shown.slice(0, limit).map((t) => (
              <li key={t.id} className="flex items-stretch">
                <button
                  type="button"
                  onClick={() => {
                    chartBack.push();
                    setChart(t);
                  }}
                  className="flex min-w-0 flex-1 items-center gap-3 px-3 py-2.5 text-left"
                >
                  <div className="min-w-0 flex-1 space-y-0.5">
                    <div className="truncate text-sm font-medium">
                      {t.name} <span className="text-xs font-normal text-faint">{t.code}</span>
                    </div>
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted">
                      <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${STATUS_STYLE[t.status] ?? ""}`}>{t.label}</span>
                      <span className="tabular">신호 {t.signal_date.slice(5)}</span>
                      {t.fill_date && <span className="tabular">매수 {t.fill_date.slice(5)}</span>}
                      {t.exit_date && <span className="tabular">매도 {t.exit_date.slice(5)}</span>}
                      {view === "all" && t.picked ? <span className="text-accent">내 선택</span> : null}
                      {t.retro ? <span className="text-warning">소급</span> : null}
                      {t.picked && t.skip ? <span className="text-warning">{t.skip}</span> : null}
                    </div>
                  </div>
                  <div className="tabular shrink-0 text-right">
                    {t.status === "closed" ? (
                      <>
                        <div className={`text-sm font-semibold ${retClass(t.ret)}`}>{pct(t.ret, 2, true)}</div>
                        {t.pnl != null && t.qty ? <div className={`text-xs ${retClass(t.pnl)}`}>{money(t.pnl, cur, true)}</div> : null}
                      </>
                    ) : t.status === "holding" && live[t.code] && t.fill_price ? (
                      <>
                        <div className={`text-sm font-semibold ${retClass(live[t.code].price / t.fill_price - 1)}`}>
                          {pct((live[t.code].price / t.fill_price - 1) * 100, 2, true)}
                        </div>
                        <div className="text-xs text-faint">
                          {price(t.fill_price, cur)} → <span className="text-foreground">{price(live[t.code].price, cur)}</span>
                        </div>
                      </>
                    ) : t.status === "holding" ? (
                      <>
                        <div className={`text-sm font-semibold ${retClass(t.ret_now)}`}>{pct(t.ret_now, 2, true)}</div>
                        <div className="text-xs text-faint">매수 {price(t.fill_price, cur)} · 전날 종가 기준</div>
                      </>
                    ) : t.status === "pending" && t.limit != null ? (
                      <>
                        <div className="text-sm">{price(t.limit, cur)}</div>
                        <div className="text-xs text-faint">지정가</div>
                      </>
                    ) : null}
                  </div>
                </button>
                <button
                  type="button"
                  onClick={() => setAct(t)}
                  className="shrink-0 border-l border-border px-3 text-muted"
                  aria-label={`${t.name} 관리`}
                >
                  ⋯
                </button>
              </li>
            ))}
          </ul>
        )}
        {shown.length > limit && (
          <button type="button" onClick={() => setLimit((n) => n + 100)} className="w-full rounded-xl border border-border py-2 text-sm text-muted">
            더 보기 ({shown.length - limit}건 남음)
          </button>
        )}
      </section>

      {/* 관리 */}
      <section className="rounded-xl border border-border bg-card p-3 text-sm">
        <button type="button" onClick={() => setManage((x) => !x)} className="flex w-full items-center justify-between font-semibold">
          카테고리 관리 <span className="text-muted">{manage ? "▲" : "▼"}</span>
        </button>
        {manage && (
          <div className="mt-3 space-y-4">
            <div className="space-y-2">
              <div className="font-medium">자본금 · 1회 매수금</div>
              <div className="flex rounded-lg border border-border p-0.5 text-xs">
                {(
                  [
                    ["deposit", "입금"],
                    ["withdraw", "출금"],
                    ["per_trade", "1회 매수금 변경"],
                  ] as const
                ).map(([k, l]) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => setCashKind(k)}
                    className={`flex-1 rounded-md py-1 ${cashKind === k ? "bg-accent text-on-accent" : "text-muted"}`}
                  >
                    {l}
                  </button>
                ))}
              </div>
              <div className="flex gap-2">
                <NumInput
                  decimal={cur === "USD"}
                  placeholder={cur === "USD" ? "금액 (달러)" : "금액 (원)"}
                  value={cashAmt}
                  onChange={setCashAmt}
                  className="min-w-0 flex-1"
                />
                <input type="date" value={cashDate} onChange={(e) => setCashDate(e.target.value)} className="!w-36" />
              </div>
              <button
                type="button"
                disabled={!Number(cashAmt)}
                onClick={async () => {
                  await call(`/api/paper/books/${id}/cash`, { body: { kind: cashKind, amount: Number(cashAmt), date: cashDate } });
                  setCashAmt("");
                  onFlash(cashKind === "per_trade" ? "1회 매수금을 바꿨습니다" : cashKind === "deposit" ? "입금했습니다" : "출금했습니다");
                }}
                className="w-full rounded-xl bg-accent py-2 font-semibold text-on-accent disabled:opacity-50"
              >
                적용
              </button>
              <p className="text-xs text-faint">입금·출금과 1회 매수금 변경은 고른 날짜부터 적용됩니다. 이미 산 종목에는 영향이 없습니다.</p>
              <ul className="tabular divide-y divide-border text-xs">
                {v.cash.map((c) => (
                  <li key={c.id} className="flex items-center justify-between py-1.5">
                    <span>
                      {c.date} ·{" "}
                      {{ init: "시작 자본금", deposit: "입금", withdraw: "출금", per_trade: "1회 매수금" }[c.kind]}{" "}
                      {money(c.amount, cur)}
                    </span>
                    {c.kind !== "init" && c.id !== Math.min(...v.cash.filter((x) => x.kind === "per_trade").map((x) => x.id)) && (
                      <button
                        type="button"
                        onClick={async () => {
                          if (!confirm("이 기록을 지울까요?")) return;
                          await api(`/api/paper/cash/${c.id}`, { method: "DELETE" });
                          load();
                        }}
                        className="text-faint"
                      >
                        삭제
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>

            <label className="flex items-start gap-2">
              <input
                type="checkbox"
                checked={!!b.auto}
                onChange={(e) => call(`/api/paper/books/${id}`, { method: "PUT", body: { auto: e.target.checked } })}
                className="mt-1 !w-auto"
              />
              <span>
                자동 담기 <span className="text-xs text-muted">— 이 조건의 신호를 매일 전부 담아 내 선택과 비교 ({b.auto_since ?? "–"}부터)</span>
              </span>
            </label>

            <div className="space-y-1">
              <div className="font-medium">고정된 조건</div>
              <pre className="overflow-x-auto rounded-lg bg-background p-2 text-xs leading-relaxed">{b.spec_text}</pre>
              {b.spec && (
                <button
                  type="button"
                  onClick={() => b.spec && onLoadSpec({ ...b.spec, name: b.name })}
                  className="rounded-lg border border-border px-3 py-1.5 text-xs"
                >
                  이 조건을 검증 화면으로 불러오기
                </button>
              )}
            </div>

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => {
                  const n = prompt("새 이름", b.name);
                  if (n && n.trim()) call(`/api/paper/books/${id}`, { method: "PUT", body: { name: n.trim() } });
                }}
                className="rounded-lg border border-border px-3 py-1.5 text-xs"
              >
                이름 바꾸기
              </button>
              <button
                type="button"
                onClick={() => call(`/api/paper/books/${id}`, { method: "PUT", body: { archived: !b.archived } })}
                className="rounded-lg border border-border px-3 py-1.5 text-xs"
              >
                {b.archived ? "보관 해제" : "보관 (목록에서 숨김)"}
              </button>
              <button
                type="button"
                onClick={async () => {
                  if (!confirm(`'${b.name}' 카테고리와 담은 거래를 모두 지울까요? 되돌릴 수 없습니다.`)) return;
                  await api(`/api/paper/books/${id}`, { method: "DELETE" });
                  onBack();
                }}
                className="rounded-lg border border-critical/40 px-3 py-1.5 text-xs text-critical"
              >
                삭제
              </button>
            </div>
          </div>
        )}
      </section>

      {/* 거래 관리 */}
      {act && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 sm:items-center" onClick={() => setAct(null)}>
          <div
            className="w-full max-w-lg space-y-2 rounded-t-2xl bg-card p-4 pb-[calc(1rem+env(safe-area-inset-bottom))] text-sm sm:rounded-2xl"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <div className="font-semibold">
              {act.name} <span className="text-xs font-normal text-muted">신호 {act.signal_date}</span>
            </div>
            {!!act.picked && act.status === "holding" && (
              <button
                type="button"
                onClick={async () => {
                  await call(`/api/paper/trades/${act.id}`, { method: "PUT", body: { manual_exit_date: act.last_date ?? today() } });
                  setAct(null);
                }}
                className="w-full rounded-xl border border-border py-2.5"
              >
                {act.last_date} 종가에 팔았다고 치기 (수동 정리)
              </button>
            )}
            {!!act.picked && !!act.manual_exit_date && (
              <button
                type="button"
                onClick={async () => {
                  await call(`/api/paper/trades/${act.id}`, { method: "PUT", body: { reopen: true } });
                  setAct(null);
                }}
                className="w-full rounded-xl border border-border py-2.5"
              >
                수동 정리 취소 (전략 규칙대로)
              </button>
            )}
            {!act.picked && (
              <button
                type="button"
                onClick={async () => {
                  await call(`/api/paper/books/${id}/trades`, { body: { code: act.code, signal_date: act.signal_date } });
                  setAct(null);
                }}
                className="w-full rounded-xl border border-border py-2.5"
              >
                내 선택으로 담기 (매수 시점이 지났으면 소급)
              </button>
            )}
            {act.picked && act.retro ? (
              <button
                type="button"
                onClick={async () => {
                  await call(`/api/paper/trades/${act.id}`, { method: "PUT", body: { include: true } });
                  setAct(null);
                }}
                className="w-full rounded-xl border border-border py-2.5"
              >
                소급 표시 지우기 (통계·계좌에 넣기)
              </button>
            ) : null}
            {act.picked ? (
              <button
                type="button"
                onClick={async () => {
                  if (!confirm(act.auto ? "내 선택에서 뺄까요? (전체 신호에는 남습니다)" : "이 거래를 지울까요?")) return;
                  await call(`/api/paper/trades/${act.id}`, { method: "DELETE" });
                  setAct(null);
                }}
                className="w-full rounded-xl border border-critical/40 py-2.5 text-critical"
              >
                {act.auto ? "내 선택에서 빼기" : "삭제"}
              </button>
            ) : null}
            <button type="button" onClick={() => setAct(null)} className="w-full py-2 text-muted">
              닫기
            </button>
          </div>
        </div>
      )}

      {chart && b.spec && (
        <TradeChart trade={asTrade(chart)} spec={b.spec} dataset={b.dataset} currency={cur} onClose={chartBack.close} />
      )}
    </div>
  );
}
