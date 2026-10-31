"""DART(금융감독원 전자공시) 재무 자료: 매출액·영업이익·당기순이익.

  python -m sbt.data.cli dart-collect            # 2015년부터 코스피·코스닥 회사 주요 계정 (이미 받은 건 건너뜀)

- 인증키는 앱 설정에서 넣는다 (data/dart.json, 서버에만 저장, 화면에 다시 나오지 않음).
- 회사 고유번호(corpCode.xml) → 종목코드 연결, 다중회사 주요계정(fnlttMultiAcnt, 한 번에 100개 회사)으로 받는다.
- 보고서: 사업보고서(11011) · 반기(11012) · 1분기(11013) · 3분기(11014). 숫자 자료는 2015년부터.
- 저장: data/dart/raw/<연도>_<보고서>.parquet (행 = 회사 × 계정 × 연결/별도). 최근 2년은 다시 받는다(새 공시 반영).
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import threading
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pandas as pd
import requests

from .. import config

BASE = "https://opendart.fss.or.kr/api"
REPORTS = {"11013": "1분기", "11012": "반기", "11014": "3분기", "11011": "사업"}
ACCOUNTS = ("매출액", "영업수익", "수익(매출액)", "매출", "영업이익", "영업이익(손실)", "당기순이익", "당기순이익(손실)")
_lock = threading.Lock()


class DartError(Exception):
    pass


# ---------------------------------------------------------------- 키 보관
def _key_path() -> Path:
    return config.DATA_DIR / "dart.json"


def set_key(key: str) -> None:
    key = key.strip()
    if len(key) != 40 or not key.isalnum():
        raise DartError("DART 인증키는 영문·숫자 40자입니다. 공백 없이 전체를 붙여넣어 주세요")
    p = _key_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"key": key, "saved_at": time.strftime("%Y-%m-%d %H:%M")}, f)
        os.chmod(p, 0o600)


def clear_key() -> None:
    with _lock:
        _key_path().unlink(missing_ok=True)


def _key() -> str:
    p = _key_path()
    if not p.exists():
        raise DartError("DART 인증키가 없습니다. 앱 설정 → DART 재무 자료에서 넣어 주세요")
    return json.loads(p.read_text())["key"]


def view() -> dict:
    p = _key_path()
    if not p.exists():
        return {"saved": False, "collected": collected()}
    d = json.loads(p.read_text())
    return {"saved": True, "key_tail": d["key"][-4:], "saved_at": d.get("saved_at"), "collected": collected()}


def _get(path: str, params: dict, session: requests.Session | None = None) -> requests.Response:
    s = session or requests
    r = s.get(f"{BASE}/{path}", params={"crtfc_key": _key(), **params}, timeout=60)
    r.raise_for_status()
    return r


def _check(j: dict) -> list:
    st = j.get("status")
    if st == "000":
        return j.get("list") or []
    if st == "013":  # 조회된 데이터 없음
        return []
    msg = {"010": "등록되지 않은 키", "011": "사용할 수 없는 키", "012": "접근할 수 없는 IP", "020": "하루 요청 한도 초과",
           "100": "필드 값 오류", "800": "시스템 점검 중", "900": "정의되지 않은 오류"}.get(st, j.get("message", "오류"))
    raise DartError(f"DART {st}: {msg}")


def test() -> str:
    """삼성전자 기업 개황으로 키 확인."""
    j = _get("company.json", {"corp_code": "00126380"}).json()
    _check(j)
    return f"연결 성공: {j.get('corp_name', '삼성전자')}"


# ---------------------------------------------------------------- 받기
def _dir() -> Path:
    return config.DATA_DIR / "dart"


def corp_codes(refresh: bool = False) -> dict[str, str]:
    """종목코드(6자리) → DART 고유번호(8자리). 하루 한 번만 새로 받는다."""
    p = _dir() / "corpcode.json"
    if p.exists() and not refresh and dt.date.fromtimestamp(p.stat().st_mtime) == dt.date.today():
        return json.loads(p.read_text())
    r = _get("corpCode.xml", {})
    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
    except zipfile.BadZipFile:
        _check(r.json())
        raise DartError("회사 목록을 받지 못했습니다")
    root = ElementTree.fromstring(zf.read(zf.namelist()[0]))
    out = {}
    for el in root.iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        if len(sc) == 6:
            out[sc] = el.findtext("corp_code").strip()
    _dir().mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out))
    return out


def _num(x) -> float | None:
    if x is None:
        return None
    s = str(x).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse(rows: list[dict]) -> pd.DataFrame:
    """API 행 → 표. 관심 계정만."""
    out = []
    for r in rows:
        name = (r.get("account_nm") or "").replace(" ", "")
        if name not in ACCOUNTS:
            continue
        out.append({
            "stock_code": (r.get("stock_code") or "").strip(),
            "corp_code": r.get("corp_code"),
            "year": int(r["bsns_year"]),
            "reprt": r["reprt_code"],
            "fs": r.get("fs_div"),
            "account": name,
            "amount": _num(r.get("thstrm_amount")),
            "add_amount": _num(r.get("thstrm_add_amount")),
            "period": r.get("thstrm_dt") or "",
            "rcept": (r.get("rcept_no") or "")[:8],
        })
    return pd.DataFrame(out)


def collect(codes: list[str], start: int = 2015, log=print, sleep: float = 0.15) -> int:
    """codes(종목코드) 의 주요 계정을 연도·보고서별로 받는다. 받은 파일 수를 돌려준다."""
    cc = corp_codes()
    corps = sorted({cc[c] for c in codes if c in cc})
    log(f"DART 회사 {len(corps):,}곳 (종목 {len(codes):,}개 중 고유번호 있는 곳)")
    raw = _dir() / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    this_year = dt.date.today().year
    sess = requests.Session()
    n_files = 0
    for year in range(start, this_year + 1):
        for reprt, label in REPORTS.items():
            path = raw / f"{year}_{reprt}.parquet"
            if path.exists() and year < this_year - 1:
                continue
            parts = []
            for k in range(0, len(corps), 100):
                chunk = corps[k:k + 100]
                r = _get("fnlttMultiAcnt.json", {"corp_code": ",".join(chunk), "bsns_year": str(year), "reprt_code": reprt},
                         sess)
                parts.append(parse(_check(r.json())))
                time.sleep(sleep)
            df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
            if df.empty:
                log(f"  {year} {label}: 아직 없음")
                continue
            df.to_parquet(path)
            n_files += 1
            log(f"  {year} {label}: 회사 {df['corp_code'].nunique():,}곳")
    return n_files


def collected() -> dict:
    raw = _dir() / "raw"
    files = sorted(raw.glob("*.parquet")) if raw.exists() else []
    if not files:
        return {"files": 0}
    last = files[-1].stem
    return {"files": len(files), "first": files[0].stem.split("_")[0], "last": last.split("_")[0],
            "last_report": REPORTS.get(last.split("_")[1], last.split("_")[1]),
            "updated": dt.datetime.fromtimestamp(max(f.stat().st_mtime for f in files)).strftime("%Y-%m-%d %H:%M")}


def load_raw() -> pd.DataFrame:
    raw = _dir() / "raw"
    files = sorted(raw.glob("*.parquet")) if raw.exists() else []
    if not files:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


# ---------------------------------------------------------------- 분기 표
REVENUE = ("매출액", "수익(매출액)", "영업수익", "매출")
OP = ("영업이익", "영업이익(손실)")


def quarterly(raw: pd.DataFrame, which: tuple[str, ...] = REVENUE) -> pd.DataFrame:
    """종목 × 연도 × 분기(1~4) 3개월 금액과 공시일. 연결(CFS) 우선, 없으면 별도(OFS).
    누적 금액으로 맞춘 뒤 차이로 분기를 낸다 (4분기 = 사업보고서 - 3분기 누적)."""
    if raw.empty:
        return pd.DataFrame(columns=["stock_code", "year", "q", "amount", "rcept"])
    d = raw[raw["account"].isin(which)].copy()
    d["pri"] = d["account"].map({a: i for i, a in enumerate(which)})
    d["fs_pri"] = (d["fs"] != "CFS").astype(int)
    d = d.sort_values(["stock_code", "year", "reprt", "fs_pri", "pri"]).drop_duplicates(["stock_code", "year", "reprt"])
    qmap = {"11013": 1, "11012": 2, "11014": 3, "11011": 4}
    d["q"] = d["reprt"].map(qmap)
    # 누적 금액: 사업보고서는 1년 전체, 분·반기는 누적이 있으면 그것, 기간이 1월부터면 그 금액
    cum = []
    for _, r in d.iterrows():
        if r["q"] == 4:
            cum.append(r["amount"])
        elif pd.notna(r["add_amount"]):
            cum.append(r["add_amount"])
        elif str(r["period"]).split("~")[0].strip().endswith(".01.01") or r["q"] == 1:
            cum.append(r["amount"])
        else:
            cum.append(None)  # 3개월 금액뿐 → 아래에서 앞 분기 누적에 더한다
    d["cum"] = cum
    d = d.sort_values(["stock_code", "year", "q"])
    out = []
    for (code, year), g in d.groupby(["stock_code", "year"], sort=False):
        prev_cum, prev_q = 0.0, 0  # 바로 앞 분기 누적 (없으면 None)
        for _, r in g.iterrows():
            q = int(r["q"])
            ok_prev = prev_q == q - 1 and prev_cum is not None
            if pd.notna(r["cum"]):
                c = r["cum"]
            elif ok_prev and pd.notna(r["amount"]):
                c = prev_cum + r["amount"]
            else:
                c = None
            # 앞 분기가 빠졌으면 이 분기 3개월 금액은 모른다 (누적은 1년 매출 등으로 쓸 수 있게 남긴다)
            amt = c - prev_cum if c is not None and ok_prev else None
            if c is not None:
                out.append({"stock_code": code, "year": int(year), "q": q, "amount": amt, "cum": c,
                            "rcept": pd.to_datetime(r["rcept"], format="%Y%m%d", errors="coerce")})
            prev_cum, prev_q = c, q
    return pd.DataFrame(out)


def run_collect(start: int = 2015, dataset: str = "kr", log=print) -> Path:
    """국내 종목 전체(코스피·코스닥 보통주) 주요 계정을 받고 요약을 연구 결과로 남긴다."""
    from .panel import Panel

    t0 = time.time()
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    m = pd.Series(panel.markets).isin(["KOSPI", "KOSDAQ"]).to_numpy() & ~panel.is_preferred
    codes = sorted({str(c) for c in panel.codes[m]})
    L = [f"DART 재무 자료 받기 · {dt.date.today()} · {start}~"]
    try:
        n = collect(codes, start, log=lambda x: (log(x), L.append(x)))
        L.append(f"새로 받거나 갱신한 파일 {n}개")
    except DartError as e:
        L.append(f"멈춤: {e}")
        log(str(e))
    raw = load_raw()
    if not raw.empty:
        q = quarterly(raw)
        L.append(f"매출 분기 자료: 회사 {q['stock_code'].nunique():,}곳 · {len(q):,}개 분기 · "
                 f"{int(q['year'].min())}~{int(q['year'].max())}")
        sam = q[q["stock_code"] == "005930"].tail(4)
        if len(sam):
            L.append("확인용 삼성전자 최근 분기 매출(조원): " + " · ".join(
                f"{int(r.year)}Q{int(r.q)} {r.amount / 1e12:,.1f}" for r in sam.itertuples()))
    L.append(f"소요 {round((time.time() - t0) / 60, 1)}분")
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"dart-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    return path
