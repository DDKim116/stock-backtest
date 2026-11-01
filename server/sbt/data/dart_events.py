"""DART 공시 사건: 자사주 매입 · 임원·주요주주 지분 변동 · 잠정 실적.

  python -m sbt.data.cli dart-events      # 처음엔 30~60분 (하루 한도 안), 다음부터는 새로 생긴 것만

- 자사주: 공시 목록(list.json, 주요사항보고 B)을 3개월씩 받아 '자기주식취득결정'·'자기주식취득신탁계약체결결정'만.
- 임원·주요주주: 회사마다 elestock.json (보고 날짜·보고자·직위·증감 수량·증감 비율). 30일 지난 회사만 다시.
- 잠정 실적: 코스피 회사마다 list.json(거래소공시 I) 중 이름에 '잠정' 과 '실적' 이 있는 것. 30일 지난 회사만 다시.
저장: data/dart/events_buyback.parquet · events_insider.parquet · events_prelim.parquet
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import pandas as pd
import requests

from .. import config
from . import dart


def _dir() -> Path:
    return config.DATA_DIR / "dart"


def _list(sess, params: dict) -> list[dict]:
    out, page = [], 1
    while True:
        j = dart._get("list.json", {**params, "page_no": page, "page_count": 100}, sess).json()
        rows = dart._check(j)
        out += rows
        if page >= int(j.get("total_page") or 1) or not rows:
            return out
        page += 1
        time.sleep(0.1)


def buybacks(sess, start: int = 2015, log=print) -> pd.DataFrame:
    """3개월 묶음 파일로 이어받기 (지난 묶음은 다시 안 받음, 최근 두 묶음만 다시)."""
    d = _dir() / "buyback"
    d.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()
    a = dt.date(start, 1, 1)
    while a <= today:
        b = min((pd.Timestamp(a) + pd.DateOffset(months=3) - pd.Timedelta(days=1)).date(), today)
        f = d / f"{a:%Y%m}.parquet"
        if not f.exists() or b >= today - dt.timedelta(days=100):
            rows = _list(sess, {"bgn_de": f"{a:%Y%m%d}", "end_de": f"{b:%Y%m%d}", "pblntf_ty": "B"})
            keep = [{"code": r.get("stock_code", "").strip(), "date": r["rcept_dt"], "name": r.get("corp_name"),
                     "kind": "신탁" if "신탁" in r["report_nm"] else "직접", "title": r["report_nm"]}
                    for r in rows if "자기주식취득" in r.get("report_nm", "").replace(" ", "")
                    and "정정" not in r["report_nm"] and len(r.get("stock_code", "").strip()) == 6]
            pd.DataFrame(keep, columns=["code", "date", "name", "kind", "title"]).to_parquet(f)
            log(f"  자사주 {a:%Y-%m}~{b:%Y-%m}: {len(keep)}건")
        a = (pd.Timestamp(b) + pd.Timedelta(days=1)).date()
    parts = [pd.read_parquet(f) for f in sorted(d.glob("*.parquet"))]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _num(x) -> float | None:
    return dart._num(x)


def _per_corp(kind: str, corps: dict[str, str], fetch, log, max_calls: int) -> pd.DataFrame:
    """회사마다 받아 하나의 파일로. 30일 안에 받은 회사는 건너뛴다. corps: 종목코드 → 고유번호."""
    path = _dir() / f"events_{kind}.parquet"
    meta_p = _dir() / f"events_{kind}.json"
    old = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    cutoff = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    todo = [c for c in corps if meta.get(c, "") < cutoff]
    log(f"  {kind}: 회사 {len(corps):,}곳 중 새로 받을 곳 {len(todo):,}")
    new, calls = [], 0

    def save() -> pd.DataFrame:
        nonlocal old, new
        if not new:
            return old
        done = {c for c, _ in new}
        keep = old[~old["code"].isin(done)] if len(old) else old
        add = pd.DataFrame([r for _, rows in new for r in rows])
        allp = pd.concat([keep, add], ignore_index=True) if len(add) else keep
        allp.to_parquet(path)
        meta_p.write_text(json.dumps(meta))
        old, new = allp, []
        return allp

    for i, code in enumerate(todo):
        if calls >= max_calls:
            log(f"  {kind}: 이번엔 여기까지 ({calls}번) — 다음에 이어서")
            break
        try:
            rows, n = fetch(code, corps[code])
        except dart.DartError as e:
            if "한도" in str(e):
                log(f"  {kind}: {e} — 내일 이어서")
                break
            save()
            raise
        except requests.RequestException:
            continue
        calls += n
        new.append((code, rows))
        meta[code] = dt.date.today().isoformat()
        if (i + 1) % 300 == 0:
            save()  # 중간에 멈춰도 받은 것은 남게
            log(f"    {i + 1:,}/{len(todo):,}")
        time.sleep(0.12)
    return save()


def insiders(sess, corps: dict[str, str], log=print, max_calls: int = 6000) -> pd.DataFrame:
    def fetch(code: str, cc: str):
        j = dart._get("elestock.json", {"corp_code": cc}, sess).json()
        rows = []
        for r in dart._check(j):
            chg = _num(r.get("sp_stock_lmp_irds_cnt"))
            if not chg:
                continue
            rows.append({"code": code, "date": r.get("rcept_dt", "").replace("-", ""), "who": r.get("repror"),
                         "reg": r.get("isu_exctv_rgist_at"), "pos": r.get("isu_exctv_ofcps"),
                         "major": r.get("isu_main_shrholdr"), "chg": chg, "chg_rate": _num(r.get("sp_stock_lmp_irds_rate"))})
        return rows, 1
    return _per_corp("insider", corps, fetch, log, max_calls)


def prelims(sess, corps: dict[str, str], start: int = 2015, log=print, max_calls: int = 6000) -> pd.DataFrame:
    def fetch(code: str, cc: str):
        rows, calls = [], 0
        y = start
        this = dt.date.today().year
        while y <= this:  # 회사 하나라도 기간은 나눠서 (응답이 너무 길어지지 않게)
            b = min(y + 4, this)
            got = _list(sess, {"corp_code": cc, "bgn_de": f"{y}0101", "end_de": f"{b}1231", "pblntf_ty": "I"})
            calls += 1
            for r in got:
                nm = r.get("report_nm", "").replace(" ", "")
                if "잠정" in nm and "실적" in nm and "정정" not in nm:
                    rows.append({"code": code, "date": r["rcept_dt"], "title": r["report_nm"]})
            y = b + 1
        return rows, calls
    return _per_corp("prelim", corps, fetch, log, max_calls)


def load(kind: str) -> pd.DataFrame:
    if kind == "buyback":
        d = _dir() / "buyback"
        parts = [pd.read_parquet(f) for f in sorted(d.glob("*.parquet"))] if d.exists() else []
        df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["code", "date"])
    else:
        p = _dir() / f"events_{kind}.parquet"
        df = pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=["code", "date"])
    if len(df):
        df["date"] = pd.to_datetime(df["date"].astype(str).str.replace("-", ""), format="%Y%m%d", errors="coerce")
        df = df.dropna(subset=["date"])
    return df


def run_collect(dataset: str = "kr", log=print) -> Path:
    from .panel import Panel

    t0 = time.time()
    panel = Panel.load(config.dataset_dir(dataset) / "prices.parquet")
    mk = pd.Series(panel.markets)
    codes_all = {str(c) for c in panel.codes[(mk.isin(["KOSPI", "KOSDAQ"])).to_numpy() & ~panel.is_preferred]}
    codes_kospi = {str(c) for c in panel.codes[(mk == "KOSPI").to_numpy() & ~panel.is_preferred]}
    L = [f"DART 공시 사건 받기 · {dt.date.today()}"]

    def lg(x):
        log(x)
        L.append(x)

    sess = requests.Session()
    try:
        cc = dart.corp_codes()
        b = buybacks(sess, log=lg)
        lg(f"자사주 매입 공시 {len(b):,}건")
        ins = insiders(sess, {c: cc[c] for c in sorted(codes_all) if c in cc}, log=lg)
        lg(f"임원·주요주주 지분 변동 {len(ins):,}건")
        pre = prelims(sess, {c: cc[c] for c in sorted(codes_kospi) if c in cc}, log=lg)
        lg(f"잠정 실적 공시 {len(pre):,}건 (코스피)")
    except dart.DartError as e:
        lg(f"멈춤: {e}")
    L.append(f"소요 {round((time.time() - t0) / 60, 1)}분 · 다 못 받았으면 다시 누르면 이어서 받아요")
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"dart-events-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text("\n".join(L))
    return path
