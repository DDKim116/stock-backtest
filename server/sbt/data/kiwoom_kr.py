"""키움 시세로 당일 종가 채우기 — 장 마감(15:30) 직후 15:45 에 받아서 그날 후보를 바로 만든다.

야후(16:40~, 지연·누락·시총 없음)보다 빠르고 정확하다: 키움은 거래소 시세 그대로이고 시가총액·거래대금도 준다.
지정종목 정보요청(ka10095)으로 50종목씩 (약 2,700종목 → 50여 번 호출, 1분 안팎).
16:00 부터는 시간외 단일가가 시작돼 '현재가'가 정규장 종가와 달라질 수 있어서 그 전에 받는다.
공식 KRX 수집 기록(manifest)에는 넣지 않으므로(src='kiwoom') 다음 날 아침 kr-update 가 공식 값으로 덮어쓴다.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from .yahoo_kr import SUFFIX, fill_index, provisional_path

CLOSE_DONE = dt.time(15, 31)
AFTER_HOURS = dt.time(16, 0)  # 시간외 단일가 시작


def today_src(root: Path, day: dt.date) -> str | None:
    """그날 원본 파일이 있으면 출처: 'official'(KRX) · 'kiwoom' · 'yahoo', 없으면 None."""
    f = root / "raw" / str(day.year) / f"{day.strftime('%Y%m%d')}.parquet"
    if not f.exists():
        return None
    cur = pd.read_parquet(f)
    if "src" not in cur.columns:
        return "official"
    return str(cur["src"].iloc[0]) if len(cur) else None


def update_today(root: Path, day: dt.date | None = None, mode: str = "mock", min_cover: float = 0.9, log=print,
                 now: dt.datetime | None = None, quotes: Callable | None = None, index_quotes: Callable | None = None) -> bool:
    """오늘 국내 시세를 키움으로 받아 raw/YYYY/YYYYMMDD.parquet 에 임시로 쓴다. 반환: 저장했으면 True."""
    from ..autotrade import is_trading_day
    from ..broker import kiwoom
    from . import indices

    now = now or dt.datetime.now()
    day = day or now.date()
    if not is_trading_day(day):
        log(f"{day} 휴장일이라 건너뜁니다")
        return False
    if day == now.date() and not (CLOSE_DONE <= now.time() < AFTER_HOURS):
        log("키움 종가는 15:31~16:00 사이에만 받습니다 (그 밖엔 장중 값이거나 시간외 단일가가 섞일 수 있음)")
        return False
    src = today_src(root, day)
    if src in ("official", "kiwoom"):
        log(f"{day} 이미 {'공식' if src == 'official' else '키움'} 자료가 있습니다")
        return False
    if quotes is None:
        if not kiwoom.view().get(mode, {}).get("saved"):
            log("키움 키가 없어 건너뜁니다")
            return False
        quotes = lambda codes: kiwoom.quotes_full(mode, codes)  # noqa: E731
        index_quotes = index_quotes or (lambda: kiwoom.index_quotes(mode))
    raw = root / "raw"
    official = [f for f in sorted(raw.glob("*/*.parquet")) if f.stem < day.strftime("%Y%m%d")]
    if not official:
        log("이전 거래일 원본이 없습니다")
        return False
    prev = pd.read_parquet(official[-1])
    prev = prev[prev["market"].isin(SUFFIX) & (prev["volume"].fillna(0) > 0)].drop_duplicates("code")
    codes = prev["code"].astype(str).tolist()
    t0 = time.time()
    q = quotes(codes)
    ymd = day.strftime("%Y%m%d")
    rows = []
    for c in codes:
        r = q.get(c)
        if not r or not r.get("price") or not r.get("volume"):
            continue  # 거래정지 등
        if r.get("date") and str(r["date"])[:8] != ymd:
            continue  # 오늘 값이 아님
        o, h, lo = r.get("open") or r["price"], r.get("high") or r["price"], r.get("low") or r["price"]
        rows.append({"code": c, "open": o, "high": h, "low": lo, "close": r["price"], "volume": r["volume"],
                     "value": r.get("value"), "mktcap": r.get("mktcap")})
    cover = len(rows) / max(1, len(codes))
    log(f"키움 {day}: {len(rows):,}/{len(codes):,}종목 ({cover:.0%}, {time.time() - t0:.0f}초)")
    if cover < min_cover:
        log(f"받은 종목이 {min_cover:.0%} 미만이라 저장하지 않습니다")
        return False
    y = pd.DataFrame(rows)
    p = prev.set_index("code")
    y["name"] = y["code"].map(p["name"])
    y["market"] = y["code"].map(p["market"])
    pc = y["code"].map(p["close"])
    y["diff"] = y["close"] - pc
    y["chg_rt"] = (y["diff"] / pc * 100).round(2)
    est = ((y["high"] + y["low"] + y["close"]) / 3 * y["volume"]).round()
    y["value"] = pd.to_numeric(y["value"], errors="coerce").where(lambda v: v > 0, est)
    y["shares"] = y["code"].map(p["shares"]) if "shares" in p else np.nan
    y["mktcap"] = pd.to_numeric(y["mktcap"], errors="coerce")
    fb = y["shares"] * y["close"]
    if "mktcap" in p:
        fb = fb.where(fb > 0, y["code"].map(p["mktcap"]) * y["close"] / pc)
    y["mktcap"] = y["mktcap"].where(y["mktcap"] > 0, fb)
    y["date"] = pd.Timestamp(day)
    y["src"] = "kiwoom"
    out = raw / str(day.year) / f"{ymd}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    y.to_parquet(out, index=False)
    provisional_path(root).write_text(json.dumps(
        {"date": day.isoformat(), "at": now.isoformat(timespec="seconds"), "codes": len(y), "src": "kiwoom"}))
    # 지수: 키움 업종 현재가 → 안 되면 종목 시총 합으로 추정
    try:
        iq = index_quotes() if index_quotes else {}
    except Exception as e:  # noqa: BLE001
        log(f"키움 지수 실패: {e}")
        iq = {}
    for key in ("KOSPI", "KOSDAQ"):
        s = indices.load(key)
        v = (iq.get(key) or {}).get("price")
        if s is not None and len(s) and s.index[-1] < pd.Timestamp(day) and v:
            indices._save(key, pd.concat([s, pd.Series([float(v)], index=[pd.Timestamp(day)])]))
            log(f"{key} 지수: 키움 {float(v):,.2f} (임시, 내일 아침 공식 값으로 바뀜)")
    try:
        fill_index(y, p, day, pd.Timestamp(official[-1].stem), log)
    except Exception as e:  # noqa: BLE001
        log(f"지수 추정 실패: {e}")
    return True
