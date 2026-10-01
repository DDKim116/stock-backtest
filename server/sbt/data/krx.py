"""KRX 정보데이터시스템 OpenAPI 수집기.

- 발급: https://openapi.krx.co.kr → 회원가입 → API 인증키 신청 → 서비스별 이용 신청
  (필요 서비스: '유가증권 일별매매정보', '코스닥 일별매매정보')
- 하루치 전 종목 데이터를 한 번에 받는다. 그날 거래된 종목은 이후 상장폐지되었더라도 포함된다.
- 원본은 data/kr/raw/YYYY/YYYYMMDD.parquet 로 하루 한 파일씩 저장한다.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import pandas as pd
import requests

BASE = "https://data-dbg.krx.co.kr/svc/apis/sto"
ENDPOINTS = {"KOSPI": "stk_bydd_trd", "KOSDAQ": "ksq_bydd_trd"}

# 응답 필드 → 내부 컬럼
FIELD_MAP = {
    "ISU_CD": "code",
    "ISU_NM": "name",
    "TDD_OPNPRC": "open",
    "TDD_HGPRC": "high",
    "TDD_LWPRC": "low",
    "TDD_CLSPRC": "close",
    "CMPPREVDD_PRC": "diff",  # 전일 대비 (기준가 기준, 부호 포함)
    "FLUC_RT": "chg_rt",
    "ACC_TRDVOL": "volume",
    "ACC_TRDVAL": "value",
    "MKTCAP": "mktcap",
    "LIST_SHRS": "shares",
}
NUM_COLS = ["open", "high", "low", "close", "diff", "chg_rt", "volume", "value", "mktcap", "shares"]


class KrxError(RuntimeError):
    pass


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(",", "", regex=False).replace({"-": None, "": None}),
                         errors="coerce")


def fetch_day(day: dt.date, market: str, key: str, session: requests.Session | None = None) -> pd.DataFrame:
    sess = session or requests.Session()
    url = f"{BASE}/{ENDPOINTS[market]}"
    r = sess.get(url, params={"basDd": day.strftime("%Y%m%d")}, headers={"AUTH_KEY": key}, timeout=30)
    if r.status_code == 401:
        raise KrxError("KRX 인증키가 올바르지 않거나 해당 서비스 이용 신청이 승인되지 않았습니다")
    if r.status_code != 200:
        raise KrxError(f"KRX 응답 오류 {r.status_code}: {r.text[:200]}")
    data = r.json()
    rows = data.get("OutBlock_1") or []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).rename(columns=FIELD_MAP)
    missing = [c for c in FIELD_MAP.values() if c not in df]
    if missing:
        raise KrxError(f"KRX 응답에 예상한 필드가 없습니다: {missing}")
    df = df[list(FIELD_MAP.values())].copy()
    for c in NUM_COLS:
        df[c] = _to_num(df[c])
    # 표준코드(12자리)로 오는 경우 단축코드(6자리)로 변환
    df["code"] = df["code"].astype(str).str.strip()
    long = df["code"].str.len() == 12
    df.loc[long, "code"] = df.loc[long, "code"].str[3:9]
    df["market"] = market
    df["date"] = pd.Timestamp(day)
    return df


def raw_dir(root: Path) -> Path:
    return root / "raw"


def _manifest_path(root: Path) -> Path:
    return raw_dir(root) / "_manifest.json"


def load_manifest(root: Path) -> dict:
    p = _manifest_path(root)
    return json.loads(p.read_text()) if p.exists() else {}


def save_manifest(root: Path, m: dict) -> None:
    p = _manifest_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=0, sort_keys=True))
    tmp.replace(p)


def collect(root: Path, start: dt.date, end: dt.date, key: str, max_calls: int = 9000,
            refetch_recent_days: int = 0, log=print) -> int:
    """start~end 사이 평일을 수집한다. 이미 받은 날은 건너뛴다. 반환: 새로 저장한 일수."""
    if not key:
        raise KrxError("KRX_API_KEY 환경 변수가 비어 있습니다")
    manifest = load_manifest(root)
    sess = requests.Session()
    calls, saved = 0, 0
    recent_cut = end - dt.timedelta(days=refetch_recent_days)
    day = start
    while day <= end:
        if day.weekday() >= 5:
            day += dt.timedelta(days=1)
            continue
        k = day.isoformat()
        if k in manifest and day < recent_cut:
            day += dt.timedelta(days=1)
            continue
        if calls + len(ENDPOINTS) > max_calls:
            log(f"호출 한도({max_calls}) 도달. 내일 이어서 실행하세요. 마지막 날짜: {day}")
            break
        frames = []
        for mkt in ENDPOINTS:
            for attempt in range(4):
                try:
                    frames.append(fetch_day(day, mkt, key, sess))
                    break
                except (requests.RequestException, ValueError) as e:
                    if attempt == 3:
                        raise
                    log(f"재시도 {day} {mkt}: {e}")
                    time.sleep(2 ** (attempt + 1))
            calls += 1
            time.sleep(0.1)
        df = pd.concat([f for f in frames if len(f)], ignore_index=True) if any(len(f) for f in frames) else None
        if df is None:
            manifest[k] = 0  # 휴장일
        else:
            out = raw_dir(root) / str(day.year) / f"{day.strftime('%Y%m%d')}.parquet"
            out.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(out, index=False)
            manifest[k] = len(df)
            saved += 1
            log(f"{day}: {len(df)} 종목")
        if calls % 50 == 0:
            save_manifest(root, manifest)
        day += dt.timedelta(days=1)
    save_manifest(root, manifest)
    return saved
