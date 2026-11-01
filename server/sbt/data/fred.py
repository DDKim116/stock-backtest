"""FRED(미국 연준 세인트루이스) 경제 지표: 금리·금리차·신용 스프레드·실업률·물가·달러·환율.

  python -m sbt.data.cli fred-update      # 전부 다시 받기 (가볍다, 평소엔 us-update 때 자동)

- 인증키는 앱 설정에서 넣는다 (data/fred.json, 서버에만 저장, 화면에 다시 나오지 않음). 영문 소문자·숫자 32자.
- 저장: data/fred/<시리즈>.csv (date, value).
- 매달 나오는 지표(실업률·물가)는 다음 달에 발표되므로, 분석에서는 한 달 뒤부터 안 것으로 친다(release_lag).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
from pathlib import Path

import pandas as pd
import requests

from .. import config

BASE = "https://api.stlouisfed.org/fred/series/observations"
SERIES: dict[str, dict] = {
    "DFF": {"name": "미국 기준금리(실효)", "unit": "%", "lag": 1},
    "T10Y2Y": {"name": "장단기 금리차 (10년-2년)", "unit": "%p", "lag": 1},
    "T10Y3M": {"name": "장단기 금리차 (10년-3개월)", "unit": "%p", "lag": 1},
    "DGS10": {"name": "미국 10년물 금리", "unit": "%", "lag": 1},
    "BAA10Y": {"name": "신용 스프레드 (Baa 회사채-10년물)", "unit": "%p", "lag": 1},
    "BAMLH0A0HYM2": {"name": "하이일드 스프레드", "unit": "%p", "lag": 1},
    "UNRATE": {"name": "실업률", "unit": "%", "lag": 35},
    "CPIAUCSL": {"name": "소비자물가(CPI)", "unit": "지수", "lag": 45},
    "ICSA": {"name": "주간 실업수당 신청", "unit": "건", "lag": 6},
    "DTWEXBGS": {"name": "달러 지수(넓은)", "unit": "지수", "lag": 1},
    "DEXKOUS": {"name": "원/달러 환율", "unit": "원", "lag": 1},
}
_lock = threading.Lock()


class FredError(Exception):
    pass


def _key_path() -> Path:
    return config.DATA_DIR / "fred.json"


def set_key(key: str) -> None:
    key = key.strip()
    if len(key) != 32 or not key.isalnum():
        raise FredError("FRED 키는 영문·숫자 32자입니다. 공백 없이 전체를 붙여넣어 주세요")
    p = _key_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"key": key.lower(), "saved_at": time.strftime("%Y-%m-%d %H:%M")}, f)
        os.chmod(p, 0o600)


def clear_key() -> None:
    with _lock:
        _key_path().unlink(missing_ok=True)


def _key() -> str:
    p = _key_path()
    if not p.exists():
        raise FredError("FRED 키가 없습니다. 앱 설정 → FRED 경제 지표에서 넣어 주세요")
    return json.loads(p.read_text())["key"]


def _dir() -> Path:
    return config.DATA_DIR / "fred"


def collected() -> dict:
    files = sorted(_dir().glob("*.csv")) if _dir().exists() else []
    if not files:
        return {"files": 0}
    return {"files": len(files),
            "updated": dt.datetime.fromtimestamp(max(f.stat().st_mtime for f in files)).strftime("%Y-%m-%d %H:%M")}


def view() -> dict:
    p = _key_path()
    if not p.exists():
        return {"saved": False, "collected": collected()}
    d = json.loads(p.read_text())
    return {"saved": True, "key_tail": d["key"][-4:], "saved_at": d.get("saved_at"), "collected": collected()}


def fetch(series_id: str, start: str = "1976-01-01", session: requests.Session | None = None) -> pd.Series:
    s = session or requests
    r = s.get(BASE, params={"series_id": series_id, "api_key": _key(), "file_type": "json",
                            "observation_start": start}, timeout=60)
    if r.status_code == 400 and "api_key" in r.text:
        raise FredError("FRED 키가 맞지 않습니다")
    r.raise_for_status()
    obs = r.json().get("observations") or []
    vals = pd.to_numeric(pd.Series([o.get("value") for o in obs]), errors="coerce")
    out = pd.Series(vals.to_numpy(), index=pd.to_datetime([o["date"] for o in obs])).dropna()
    return out


def test() -> str:
    s = fetch("T10Y2Y", (dt.date.today() - dt.timedelta(days=20)).isoformat())
    if s.empty:
        raise FredError("응답은 왔지만 값이 없습니다")
    return f"연결 성공: 장단기 금리차 {s.iloc[-1]:+.2f}%p ({s.index[-1].date()})"


def update(log=print) -> int:
    """모든 시리즈를 다시 받는다. 받은 개수."""
    _dir().mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    n = 0
    for sid, info in SERIES.items():
        try:
            s = fetch(sid, session=sess)
        except FredError:
            raise
        except Exception as e:  # noqa: BLE001
            log(f"  {info['name']}: 실패 ({e})")
            continue
        if s.empty:
            log(f"  {info['name']}: 값 없음")
            continue
        pd.DataFrame({"date": s.index.strftime("%Y-%m-%d"), "value": s.values}).to_csv(_dir() / f"{sid}.csv", index=False)
        log(f"  {info['name']}: {s.index[0].date()} ~ {s.index[-1].date()} ({len(s):,}개)")
        n += 1
        time.sleep(0.3)
    return n


def load(series_id: str, known: bool = False) -> pd.Series | None:
    """저장된 시리즈. known=True 면 '발표되어 알 수 있었던 날' 기준으로 날짜를 lag 만큼 미룬다."""
    p = _dir() / f"{series_id}.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["date"])
    s = df.set_index("date")["value"].astype(float).sort_index()
    if known:
        s.index = s.index + pd.Timedelta(days=SERIES.get(series_id, {}).get("lag", 1))
    return s


def run_update(log=print) -> Path:
    """연구 탭에서 돌리는 받기: 요약을 결과 파일로 남긴다."""
    L = [f"FRED 경제 지표 받기 · {dt.date.today()}"]
    try:
        n = update(lambda x: (log(x), L.append(x)))
        L.append(f"받은 지표 {n}개")
    except FredError as e:
        L.append(f"멈춤: {e}")
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"fred-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text("\n".join(L))
    return path
