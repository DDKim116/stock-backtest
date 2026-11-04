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
import re
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


def _family(name: str) -> str:
    """'4.4.3.1.4. 유형별 주택매매가격지수(기준월: 2021.06)' → '유형별주택매매가격지수' (기준월만 다른 같은 표 묶기)."""
    n = re.sub(r"^[\d.]+\s*", "", str(name))
    n = re.sub(r"\(?\s*기준\s*(월|시점|년)?\s*[:：]?\s*[\d.\s]+\)?", "", n)
    return n.replace(" ", "")


def chain(series: list[pd.Series]) -> pd.Series:
    """기준월만 다른 지수들을 이어 붙인다: 가장 최근 것 기준, 그 전은 겹치는 달의 비율로 맞춰 앞에 붙임."""
    ss = sorted([x for x in series if len(x)], key=lambda x: (x.index[-1], x.index[0]), reverse=True)
    if not ss:
        return pd.Series(dtype=float)
    out = ss[0].copy()
    for o in ss[1:]:
        ov = out.index.intersection(o.index)
        if len(ov) < 3 or o.index[0] >= out.index[0]:
            continue
        ov = ov[-12:]
        f = float((out.loc[ov] / o.loc[ov]).median())
        out = pd.concat([o[o.index < out.index[0]] * f, out]).sort_index()
    return out


def find_seoul_apt(sess=None, log=print) -> dict | None:
    """통계표 목록에서 서울 아파트 매매가격지수를 찾는다. 기준월만 다른 표는 이어 붙이고, 가장 긴 것을 고른다."""
    cands = []
    for t in _tables(sess):
        nm = str(t.get("STAT_NAME", "")).replace(" ", "")
        if t.get("SRCH_YN") != "Y" or "매매가격지수" not in nm or "전세" in nm or "월세" in nm:
            continue
        cands.append((t["STAT_CODE"], t.get("STAT_NAME", "")))
    log(f"  매매가격지수 표 {len(cands)}개: " + " · ".join(f"{c} {n}" for c, n in cands[:20]))
    fams: dict[str, list] = {}
    end = dt.date.today().strftime("%Y%m")
    for code, name in cands[:20]:
        try:
            items = _items(code, sess)
            pick = _pick_items(items, "서울")
            if not pick:
                sample = sorted({f"{i.get('ITEM_NAME')}({i.get('CYCLE')})" for i in items})[:25]
                log(f"  {code} {name}: '서울' 항목 없음 · 항목 {len(items)}개 예: {', '.join(sample)}")
                continue
            sr = search(code, "M", "198001", end, pick, sess)
        except EcosError as e:
            log(f"  {code} {name}: {e}")
            continue
        names = {i["ITEM_CODE"]: i.get("ITEM_NAME") for i in items}
        log(f"  후보 {code} {name} [{', '.join(str(names.get(c, c)) for c in pick)}]: {len(sr)}달"
            + (f" ({sr.index[0]:%Y-%m} ~ {sr.index[-1]:%Y-%m})" if len(sr) else ""))
        if len(sr) >= 12:
            fams.setdefault(_family(name), []).append({"stat": code, "name": name, "items": pick,
                                                        "item_names": [names.get(c, c) for c in pick], "s": sr})
        time.sleep(0.2)
    best = None
    for fam, parts in fams.items():
        sr = chain([x["s"] for x in parts])
        # 아파트 표가 아니면 항목에 '아파트'가 있어야 함
        apt_ok = "아파트" in fam or any("아파트" in str(n) for x in parts for n in x["item_names"])
        if not apt_ok or len(sr) < 60:
            continue
        key = (len(sr), sr.index[-1])
        if best is None or key > best["key"]:
            best = {"key": key, "stat": ",".join(x["stat"] for x in parts), "name": fam, "s": sr,
                    "items": parts[0]["items"], "item_names": parts[0]["item_names"], "tables": len(parts)}
    if best:
        best.pop("key")
    return best


def find_kospi(ref: pd.Series | None, sess=None, log=print) -> dict | None:
    """주식시장(월) 표에서 코스피 '지수' 항목 (거래대금·시가총액 같은 것 말고). 앱 지수와 겹치는 달 값이 맞는 것."""
    bad = ("거래", "대금", "시가총액", "상장", "회사", "수량", "주식수", "외국인", "PER", "배당")
    tried = 0
    for t in _tables(sess):
        nm = str(t.get("STAT_NAME", ""))
        if t.get("SRCH_YN") != "Y" or not ("주식" in nm or "주가" in nm) or t.get("CYCLE") not in ("M", "MM") or tried >= 6:
            continue
        tried += 1
        for it in _items(t["STAT_CODE"], sess):
            iname = str(it.get("ITEM_NAME", ""))
            if not any(w in iname.upper() for w in ("KOSPI", "코스피", "종합주가")) or any(b in iname for b in bad):
                continue
            k = search(t["STAT_CODE"], "M", "198001", dt.date.today().strftime("%Y%m"), [it["ITEM_CODE"]], sess)
            if len(k) < 120:
                continue
            ok = True
            if ref is not None and len(ref):
                ov = k.index.intersection(ref.index)
                err = float((k.loc[ov] / ref.loc[ov] - 1).abs().median()) if len(ov) >= 12 else 1.0
                ok = err < 0.03
                log(f"  코스피 후보 {t['STAT_CODE']} {nm} · {iname}: 앱 지수와 차이 {err * 100:.1f}%")
            if ok:
                return {"stat": t["STAT_CODE"], "name": nm, "item": it["ITEM_CODE"], "item_name": iname, "s": k}
    return None


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
        log(f"  → 고른 표: {best['stat']} {best['name']} (기준월 다른 표 {best['tables']}개 이어 붙임) · "
            f"항목 {', '.join(map(str, best['item_names']))} · {best['s'].index[0]:%Y-%m} ~ {best['s'].index[-1]:%Y-%m}")
        n += 1
    else:
        log("  ⚠ 서울 아파트 매매가격지수 표를 찾지 못했습니다 (결과를 알려 주세요)")
    # 코스피 월말 (ECOS 주식시장 표에서 '지수' 항목 — 앱 지수(2010~)와 값이 맞는지 확인해서, 그 전 달을 채우는 데 쓴다)
    try:
        from .indices import load as index_load

        ik = index_load("KOSPI")
        ref = None
        if ik is not None and len(ik):
            ref = ik.groupby(ik.index.to_period("M")).last()
            ref.index = ref.index.to_timestamp()
        hit = find_kospi(ref, sess, log)
        if hit:
            _save("kospi", hit["s"])
            meta["kospi"] = {k: v for k, v in hit.items() if k != "s"}
            log(f"  코스피(월): {hit['stat']} {hit['name']} · {hit['item_name']} · {hit['s'].index[0]:%Y-%m} ~")
            n += 1
        else:
            (_dir() / "kospi.csv").unlink(missing_ok=True)
            log("  코스피(월): ECOS 에서 맞는 항목을 못 찾음 → 앱 지수(2010~)만 씀")
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
