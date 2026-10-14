"""KRX 정보데이터시스템 웹사이트(data.krx.co.kr)의 '전종목 시세' — 당일 저녁에 그날 자료를 받기 위한 보조 경로.

공식 OpenAPI 는 전날 자료를 다음 날 아침에 주므로, 저녁에는 이 경로로 먼저 채우고
다음 날 아침 OpenAPI 갱신(최근 며칠 다시 받기)으로 덮어쓴다.
응답 필드는 OpenAPI 와 거의 같아 같은 형태(krx.FIELD_MAP 컬럼)로 맞춘다.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import requests

from . import krx

URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101",
    "X-Requested-With": "XMLHttpRequest",
}
MKT_ID = {"KOSPI": "STK", "KOSDAQ": "KSQ"}
FIELD_MAP = {
    "ISU_SRT_CD": "code",
    "ISU_ABBRV": "name",
    "TDD_OPNPRC": "open",
    "TDD_HGPRC": "high",
    "TDD_LWPRC": "low",
    "TDD_CLSPRC": "close",
    "CMPPREVDD_PRC": "diff",
    "FLUC_RT": "chg_rt",
    "ACC_TRDVOL": "volume",
    "ACC_TRDVAL": "value",
    "MKTCAP": "mktcap",
    "LIST_SHRS": "shares",
}


def fetch_day(day: dt.date, market: str, session: requests.Session | None = None) -> pd.DataFrame:
    sess = session or requests.Session()
    form = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
        "locale": "ko_KR",
        "mktId": MKT_ID[market],
        "trdDd": day.strftime("%Y%m%d"),
        "share": "1",
        "money": "1",
        "csvxls_isNo": "false",
    }
    r = sess.post(URL, data=form, headers=HEADERS, timeout=30)
    if r.status_code != 200:
        raise krx.KrxError(f"KRX 웹 응답 오류 {r.status_code}: {r.text[:200]}")
    try:
        data = r.json()
    except ValueError as e:
        raise krx.KrxError(f"KRX 웹 응답이 JSON 이 아닙니다 (로그인 요구 등): {r.text[:200]!r}") from e
    rows = data.get("OutBlock_1") or data.get("output") or []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).rename(columns=FIELD_MAP)
    missing = [c for c in FIELD_MAP.values() if c not in df]
    if missing:
        raise krx.KrxError(f"KRX 웹 응답에 예상한 필드가 없습니다: {missing} / 받은 필드: {list(rows[0])[:20]}")
    df = df[list(FIELD_MAP.values())].copy()
    for c in krx.NUM_COLS:
        df[c] = krx._to_num(df[c])
    df["code"] = df["code"].astype(str).str.strip()
    # 거래 없는 날(휴장일)에는 가격이 모두 비어서 온다
    if df["close"].notna().sum() == 0 or (df["volume"].fillna(0) == 0).all():
        return pd.DataFrame()
    df["market"] = market
    df["date"] = pd.Timestamp(day)
    return df


def test(day: dt.date) -> None:
    """서버에서 이 경로가 되는지 확인 (저장하지 않음)."""
    sess = requests.Session()
    for mkt in MKT_ID:
        try:
            df = fetch_day(day, mkt, sess)
        except Exception as e:  # noqa: BLE001
            print(f"[{mkt}] 실패: {e}")
            continue
        if df.empty:
            print(f"[{mkt}] {day}: 자료 없음 (휴장일이거나 아직 공개 전)")
            continue
        print(f"[{mkt}] {day}: {len(df):,}종목 받음")
        show = df[df["code"].isin(["005930", "000660", "035720", "247540", "086520"])]
        if show.empty:
            show = df.head(3)
        with pd.option_context("display.width", 200, "display.max_columns", 20):
            print(show[["code", "name", "open", "high", "low", "close", "chg_rt", "volume"]].to_string(index=False))
