"""한국은행 ECOS: 서울 아파트 매매가격지수 · 한국 기준금리(그 전은 콜금리) · 코스피(월).

  python -m sbt.data.cli ecos-update      # 전부 다시 받기 (가볍다, 몇 번만 부른다)

- 인증키는 앱 설정에서 넣는다 (data/ecos.json, 서버에만, 화면에 다시 나오지 않음). 영문·숫자.
- 서울 아파트 표는 이름으로 찾는다: 통계표 목록에서 '아파트'·'매매가격지수' 가 들어간 표 → 항목 중 '서울'.
  (KB국민은행 지수가 1986년부터라 가장 길어서 먼저 고른다.) 무엇을 골랐는지 data/ecos/meta.json 과 받기 결과에 남긴다.
- 저장: data/ecos/<이름>.csv (date=그달 1일, value).
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

BASE = "https://ecos.bok.or.kr/api"
RATE = ("722Y001", "0101000")  # 한국은행 기준금리 (월)
NAMES = {"seoul_apt": "서울 아파트 매매가격지수", "base_rate": "한국 기준금리", "call_rate": "콜금리(1일)",
         "kospi": "코스피(월말)"}
_lock = threading.Lock()


class EcosError(Exception):
    pass


def _key_path() -> Path:
    return config.DATA_DIR / "ecos.json"


def _dir() -> Path:
    return config.DATA_DIR / "ecos"


def set_key(key: str) -> None:
    key = key.strip()
    if not (16 <= len(key) <= 40) or not key.isalnum():
        raise EcosError("ECOS 인증키는 영문·숫자 20자 정도입니다. 공백 없이 전체를 붙여넣어 주세요")
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
        raise EcosError("ECOS 키가 없습니다. 앱 설정 → 한국은행 ECOS 에서 넣어 주세요")
    return json.loads(p.read_text())["key"]


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


def _call(service: str, *parts: str, n: int = 10000, sess=None) -> list[dict]:
    url = "/".join([BASE, service, _key(), "json", "kr", "1", str(n), *[p for p in parts if p]])
    r = (sess or requests).get(url, timeout=60)
    r.raise_for_status()
    j = r.json()
    if "RESULT" in j:  # 오류 또는 자료 없음
        code, msg = j["RESULT"].get("CODE", ""), j["RESULT"].get("MESSAGE", "")
        if code == "INFO-200":
            return []
        if code in ("INFO-100", "ERROR-100"):
            raise EcosError(f"ECOS 키가 맞지 않습니다 ({msg})")
        raise EcosError(f"ECOS {code}: {msg}")
    body = j.get(service) or {}
    return body.get("row") or []


def _series(rows: list[dict]) -> pd.Series:
    def date(t: str) -> pd.Timestamp:
        t = str(t)
        if len(t) == 8:
            return pd.Timestamp(f"{t[:4]}-{t[4:6]}-{t[6:]}")
        if "Q" in t:
            return pd.Timestamp(f"{t[:4]}-{(int(t[-1]) - 1) * 3 + 1:02d}-01")
        return pd.Timestamp(f"{t[:4]}-{t[4:6] or '01'}-01")

    vals = pd.to_numeric(pd.Series([r.get("DATA_VALUE") for r in rows]), errors="coerce")
    s = pd.Series(vals.to_numpy(), index=pd.DatetimeIndex([date(r["TIME"]) for r in rows])).dropna()
    return s[~s.index.duplicated(keep="last")].sort_index()


def search(stat: str, cycle: str, start: str, end: str, items: list[str], sess=None) -> pd.Series:
    return _series(_call("StatisticSearch", stat, cycle, start, end, *items, sess=sess))


def _tables(sess=None) -> list[dict]:
    return _call("StatisticTableList", n=100000, sess=sess)


def _items(stat: str, sess=None) -> list[dict]:
    return _call("StatisticItemList", stat, sess=sess)


def _pick_items(items: list[dict], want: str) -> list[str] | None:
    """항목 묶음(GRP)마다 하나: want(예: '서울')가 있으면 그것, '아파트'가 있으면 그것, 하나뿐이면 그것."""
    groups: dict[str, list[dict]] = {}
    for it in items:
        if it.get("CYCLE") not in (None, "", "M"):
            continue
        groups.setdefault(it.get("GRP_CODE") or "G", []).append(it)
    out, found = [], False
    for _, its in sorted(groups.items()):
        names = [(str(i.get("ITEM_NAME", "")).strip(), i["ITEM_CODE"]) for i in its]
        hit = next((c for n, c in names if n == want), None) or next((c for n, c in names if n.startswith(want)), None)
        if hit:
            found = True
            out.append(hit)
            continue
        hit = next((c for n, c in names if n in ("아파트", "아파트매매")), None)
        out.append(hit or names[0][1])
    return out if found else None


def find_seoul_apt(sess=None, log=print) -> dict | None:
    """통계표 목록에서 서울 아파트 매매가격지수를 찾는다 (가장 긴 것)."""
    cands = []
    for t in _tables(sess):
        nm = str(t.get("STAT_NAME", "")).replace(" ", "")
        if t.get("SRCH_YN") != "Y" or "매매가격지수" not in nm or "전세" in nm:
            continue
        score = (2 if "아파트" in nm else 0) + (1 if "KB" in nm.upper() else 0) + (1 if "주택" in nm else 0)
        cands.append((score, t["STAT_CODE"], t.get("STAT_NAME", ""), t.get("CYCLE", "")))
    cands.sort(key=lambda x: -x[0])
    best = None
    for score, code, name, cyc in cands[:8]:
        try:
            items = _items(code, sess)
            pick = _pick_items(items, "서울")
            if not pick:
                continue
            s = search(code, "M", "198001", dt.date.today().strftime("%Y%m"), pick, sess)
        except EcosError as e:
            log(f"  {name}: {e}")
            continue
        log(f"  후보 {code} {name}: {len(s)}달 ({s.index[0].date() if len(s) else '-'} ~)")
        if len(s) >= 60 and (best is None or len(s) > len(best["s"])):
            names = {i["ITEM_CODE"]: i.get("ITEM_NAME") for i in items}
            best = {"stat": code, "name": name, "items": pick, "item_names": [names.get(c, c) for c in pick], "s": s}
        time.sleep(0.2)
    return best


def _find_item(stat: str, want: tuple[str, ...], sess=None) -> tuple[str, str] | None:
    for it in _items(stat, sess):
        nm = str(it.get("ITEM_NAME", ""))
        if any(w in nm for w in want):
            return it["ITEM_CODE"], it.get("CYCLE") or "M"
    return None


def _save(name: str, s: pd.Series) -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": s.index.strftime("%Y-%m-%d"), "value": s.values}).to_csv(_dir() / f"{name}.csv", index=False)


def test() -> str:
    s = search(*RATE[:1], "M", (dt.date.today() - dt.timedelta(days=200)).strftime("%Y%m"),
               dt.date.today().strftime("%Y%m"), [RATE[1]])
    if s.empty:
        raise EcosError("응답은 왔지만 값이 없습니다")
    return f"연결 성공: 한국 기준금리 {s.iloc[-1]:.2f}% ({s.index[-1]:%Y-%m})"


def update(log=print) -> int:
    sess = requests.Session()
    end = dt.date.today().strftime("%Y%m")
    meta: dict = {"updated": dt.datetime.now().isoformat(timespec="minutes")}
    n = 0
    # 기준금리
    s = search(RATE[0], "M", "199901", end, [RATE[1]], sess)
    if len(s):
        _save("base_rate", s)
        log(f"  한국 기준금리: {s.index[0]:%Y-%m} ~ {s.index[-1]:%Y-%m} ({len(s)}달) · 지금 {s.iloc[-1]:.2f}%")
        n += 1
    # 콜금리 (1999 전 금리 흐름용)
    try:
        hit = _find_item("721Y001", ("콜금리(1일", "콜금리(전체", "콜금리"), sess)
        if hit:
            c = search("721Y001", "M", "198001", end, [hit[0]], sess)
            if len(c):
                _save("call_rate", c)
                meta["call_rate"] = {"stat": "721Y001", "item": hit[0]}
                log(f"  콜금리: {c.index[0]:%Y-%m} ~ ({len(c)}달)")
                n += 1
    except EcosError as e:
        log(f"  콜금리: {e}")
    # 서울 아파트
    log("  서울 아파트 매매가격지수 표 찾는 중…")
    best = find_seoul_apt(sess, log)
    if best:
        _save("seoul_apt", best["s"])
        meta["seoul_apt"] = {k: v for k, v in best.items() if k != "s"}
        log(f"  → 고른 표: {best['stat']} {best['name']} · 항목 {', '.join(map(str, best['item_names']))} · "
            f"{best['s'].index[0]:%Y-%m} ~ {best['s'].index[-1]:%Y-%m}")
        n += 1
    else:
        log("  ⚠ 서울 아파트 매매가격지수 표를 찾지 못했습니다 (결과를 알려 주세요)")
    # 코스피 월말 (ECOS 주식시장 표에서, 없으면 앱의 지수 파일을 쓴다)
    try:
        tried = 0
        for t in _tables(sess):
            nm = str(t.get("STAT_NAME", ""))
            if t.get("SRCH_YN") == "Y" and ("주식" in nm or "주가" in nm) and t.get("CYCLE") in ("M", "MM") and tried < 8:
                tried += 1
                hit = _find_item(t["STAT_CODE"], ("KOSPI", "코스피", "종합주가지수"), sess)
                if hit:
                    k = search(t["STAT_CODE"], "M", "198001", end, [hit[0]], sess)
                    if len(k) > 120:
                        _save("kospi", k)
                        meta["kospi"] = {"stat": t["STAT_CODE"], "name": nm, "item": hit[0]}
                        log(f"  코스피(월): {t['STAT_CODE']} {nm} · {k.index[0]:%Y-%m} ~ ({len(k)}달)")
                        n += 1
                        break
    except EcosError as e:
        log(f"  코스피(월): {e}")
    _dir().mkdir(parents=True, exist_ok=True)
    (_dir() / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return n


def load(name: str) -> pd.Series | None:
    p = _dir() / f"{name}.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["date"])
    return df.set_index("date")["value"].astype(float).sort_index()


def meta() -> dict:
    p = _dir() / "meta.json"
    return json.loads(p.read_text()) if p.exists() else {}


def run_update(log=print) -> Path:
    L = [f"한국은행 ECOS 받기 · {dt.date.today()}"]
    try:
        n = update(lambda x: (log(x), L.append(x)))
        L.append(f"받은 자료 {n}개")
    except EcosError as e:
        L.append(f"멈춤: {e}")
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"ecos-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text("\n".join(L))
    return path
