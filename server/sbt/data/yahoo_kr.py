"""야후 파이낸스의 국내 시세 — 당일 저녁에 임시로 채우기 위한 보조 경로 (D안).

공식 KRX OpenAPI 는 전날 자료를 다음 날 아침에 주므로, 저녁에는 야후 값으로 먼저 채우고
다음 날 아침 OpenAPI 갱신(최근 며칠 다시 받기)으로 덮어쓰는 용도.
티커: 코스피 = 005930.KS, 코스닥 = 247540.KQ
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config
from . import us

SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}
BIG = ["005930", "000660", "373220", "207940", "005380", "035420", "035720", "247540", "086520", "196170"]


def _raw_file(day: dt.date | None) -> Path | None:
    raw = config.dataset_dir("kr") / "raw"
    if day:
        p = raw / str(day.year) / f"{day.strftime('%Y%m%d')}.parquet"
        return p if p.exists() else None
    files = sorted(raw.glob("*/*.parquet"))
    return files[-1] if files else None


def test(day: dt.date, n: int = 300) -> None:
    """야후 값이 KRX 공식 값과 얼마나 맞는지 확인 (저장하지 않음)."""
    official = _raw_file(day)
    src = official or _raw_file(None)
    if src is None:
        print("국내 원본 데이터가 없습니다")
        return
    base = pd.read_parquet(src)
    base = base[base["market"].isin(SUFFIX) & (base["volume"].fillna(0) > 0)]
    print(f"종목 목록: {src.name} 기준 {len(base):,}종목" + ("" if official else f" (공식 {day} 자료는 아직 없음 → 비교 없이 값만 표시)"))
    rng = np.random.default_rng(0)
    pick = list(dict.fromkeys(BIG + list(rng.choice(base["code"].to_numpy(), size=min(n, len(base)), replace=False))))
    base = base.set_index("code")
    pick = [c for c in pick if c in base.index]
    tickers = [c + SUFFIX[base.at[c, "market"]] for c in pick]

    t0 = time.time()
    got: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), us.BATCH):
        got.update(us._download(tickers[i:i + us.BATCH], start=(day - dt.timedelta(days=7)).isoformat()))
    took = time.time() - t0
    rows = []
    for c, t in zip(pick, tickers):
        df = got.get(t)
        if df is None:
            continue
        r = df[df["date"] == pd.Timestamp(day)]
        if len(r):
            rows.append({"code": c, **{k: float(r.iloc[-1][k]) for k in ["open", "high", "low", "close", "volume"]}})
    y = pd.DataFrame(rows).set_index("code") if rows else pd.DataFrame()
    print(f"야후 요청 {len(tickers):,}종목 → 받은 종목 {len(got):,}, {day} 값이 있는 종목 {len(y):,} ({took:.0f}초)")
    print(f"전 종목(약 {len(base):,}개)을 받으면 약 {took / max(1, len(tickers)) * len(base) / 60:.1f}분 예상")
    if y.empty:
        print(f"{day} 야후 값이 아직 없습니다 (장 마감 후 시간이 더 지나야 할 수 있음)")
        return
    if not official:
        show = y.head(10).join(base[["name"]])
        print(show.to_string())
        return
    o = base.loc[y.index]
    def pct_ok(col, tol):
        d = (y[col] / o[col] - 1).abs()
        return (d <= tol).mean() * 100
    print("KRX 공식 값과 비교 (같은 종목·같은 날):")
    for col in ["open", "high", "low", "close"]:
        print(f"  {col:6s} 정확히 일치 {pct_ok(col, 1e-9):5.1f}% · 0.5% 이내 {pct_ok(col, 0.005):5.1f}%")
    print(f"  volume 5% 이내 {pct_ok('volume', 0.05):5.1f}% · 20% 이내 {pct_ok('volume', 0.2):5.1f}%")
    bad = (y["close"] / o["close"] - 1).abs().sort_values(ascending=False)
    bad = bad[bad > 0.005].head(8)
    if len(bad):
        print("종가가 0.5% 넘게 다른 종목:")
        for c in bad.index:
            print(f"  {c} {o.at[c, 'name']}: 공식 {o.at[c, 'close']:,.0f} / 야후 {y.at[c, 'close']:,.0f}")
    show = y.loc[[c for c in BIG if c in y.index]].join(o[["name", "close", "volume"]], rsuffix="_krx")
    print(show[["name", "close", "close_krx", "volume", "volume_krx"]].to_string())


# ---------------------------------------------------------------- 저녁 임시 갱신
def provisional_path(root: Path) -> Path:
    return root / "provisional.json"


def update_today(root: Path, day: dt.date | None = None, min_cover: float = 0.8, log=print) -> bool:
    """오늘 국내 시세를 야후로 받아 raw/YYYY/YYYYMMDD.parquet 에 임시로 쓴다 (src='yahoo').

    KRX 공식 수집 기록(manifest)에는 넣지 않으므로 다음 날 아침 kr-update 가 같은 파일을 공식 값으로 덮어쓴다.
    반환: 저장했으면 True.
    """
    import json

    day = day or dt.date.today()
    if day.weekday() >= 5:
        log("주말이라 건너뜁니다")
        return False
    raw = root / "raw"
    files = sorted(raw.glob("*/*.parquet"))
    official = [f for f in files if f.stem < day.strftime("%Y%m%d")]
    if not official:
        log("이전 거래일 원본이 없습니다")
        return False
    if any(f.stem == day.strftime("%Y%m%d") for f in files):
        cur = pd.read_parquet(raw / str(day.year) / f"{day.strftime('%Y%m%d')}.parquet", columns=None)
        if "src" not in cur.columns:
            log(f"{day} 공식 자료가 이미 있습니다")
            return False
    prev = pd.read_parquet(official[-1])
    prev = prev[prev["market"].isin(SUFFIX) & (prev["volume"].fillna(0) > 0)].drop_duplicates("code")
    tickers = {c + SUFFIX[m]: c for c, m in zip(prev["code"], prev["market"])}
    t0 = time.time()
    got: dict[str, pd.DataFrame] = {}
    tl = list(tickers)
    for i in range(0, len(tl), us.BATCH):
        got.update(us._download(tl[i:i + us.BATCH], start=(day - dt.timedelta(days=7)).isoformat()))
    rows = []
    for t, df in got.items():
        r = df[df["date"] == pd.Timestamp(day)]
        if len(r) and r.iloc[-1]["volume"] > 0:
            x = r.iloc[-1]
            rows.append({"code": tickers[t], "open": x["open"], "high": x["high"], "low": x["low"],
                         "close": x["close"], "volume": x["volume"]})
    cover = len(rows) / max(1, len(tickers))
    log(f"야후 {day}: {len(rows):,}/{len(tickers):,}종목 ({cover:.0%}, {time.time() - t0:.0f}초)")
    if cover < min_cover:
        log(f"받은 종목이 {min_cover:.0%} 미만이라 저장하지 않습니다 (야후에 아직 안 올라왔거나 휴장일)")
        return False
    y = pd.DataFrame(rows)
    p = prev.set_index("code")
    y["name"] = y["code"].map(p["name"])
    y["market"] = y["code"].map(p["market"])
    pc = y["code"].map(p["close"])
    y["diff"] = y["close"] - pc  # 권리락 등은 다음 날 아침 공식 값에서 바로잡힌다
    y["chg_rt"] = (y["diff"] / pc * 100).round(2)
    y["value"] = ((y["high"] + y["low"] + y["close"]) / 3 * y["volume"]).round()  # 거래대금 추정
    y["shares"] = y["code"].map(p["shares"]) if "shares" in p else None
    y["mktcap"] = y["shares"] * y["close"] if "shares" in p else None
    y["date"] = pd.Timestamp(day)
    y["src"] = "yahoo"
    out = raw / str(day.year) / f"{day.strftime('%Y%m%d')}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    y.to_parquet(out, index=False)
    provisional_path(root).write_text(json.dumps(
        {"date": day.isoformat(), "at": dt.datetime.now().isoformat(timespec="seconds"), "codes": len(y)}))
    return True


def clear_if_official(root: Path) -> None:
    """임시로 채운 날이 공식 값으로 바뀌었으면 표시를 지운다."""
    import json

    pp = provisional_path(root)
    if not pp.exists():
        return
    d = dt.date.fromisoformat(json.loads(pp.read_text())["date"])
    f = root / "raw" / str(d.year) / f"{d.strftime('%Y%m%d')}.parquet"
    if f.exists() and "src" not in pd.read_parquet(f).columns:
        pp.unlink()
