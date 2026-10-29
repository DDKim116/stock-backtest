"use client";

import { useEffect, useState } from "react";
import { api } from "./api";

export interface LiveQuote {
  price: number;
  change_pct: number | null;
}

// 국내 종목 현재가 (키움 시세). 장중엔 20초, 그 밖엔 2분마다. 키가 없거나 실패하면 빈 값
export function useLiveQuotes(codes: string[], enabled = true): Record<string, LiveQuote> {
  const [q, setQ] = useState<Record<string, LiveQuote>>({});
  const key = [...new Set(codes)].sort().join(",");
  useEffect(() => {
    if (!enabled || !key) return;
    let stop = false;
    const load = () =>
      api<Record<string, LiveQuote>>(`/api/live/quote?codes=${key}`).then(
        (r) => !stop && setQ(r),
        () => {},
      );
    load();
    const t = setInterval(() => {
      const hm = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit" }).format(new Date());
      if ((hm >= "08:59" && hm <= "15:35") || new Date().getMinutes() % 2 === 0) load();
    }, 20_000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [key, enabled]);
  return q;
}
