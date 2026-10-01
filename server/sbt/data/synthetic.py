"""데모·테스트용 가상 데이터 생성기.

실제 데이터를 받기 전에 화면과 엔진을 확인하는 용도. 이름에 '데모'가 붙고,
결과 화면에도 가상 데이터라는 경고가 표시된다.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def generate(n_codes: int = 300, start: str = "2012-01-02", end: str = "2025-12-31", seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, end)
    frames = []
    for i in range(n_codes):
        pref = i % 50 == 49
        code = f"{900000 + (i // 50) * 1000 + (i % 50) * 10 + (5 if pref else 0):06d}"
        name = f"데모종목{i + 1:03d}" + ("우" if pref else "")
        if i % 60 == 30:
            name = f"데모스팩{i // 60 + 1}호"
        market = "KOSPI" if i % 3 == 0 else "KOSDAQ"
        # 상장 시점과 상장폐지(일부) 시점
        a = int(rng.integers(0, len(days) // 3)) if i % 4 == 0 else 0
        b = len(days) - (int(rng.integers(len(days) // 4, len(days) // 2)) if i % 10 == 0 else 0)
        d = days[a:b]
        n = len(d)
        if n < 30:
            continue
        vol = rng.uniform(0.015, 0.035)
        r = rng.standard_t(4, n) * vol / np.sqrt(2)
        jump = rng.random(n) < 0.004
        r[jump] += rng.uniform(0.10, 0.25, jump.sum())
        # 급등 후 며칠간 약한 추세(데모용)
        for k in np.flatnonzero(jump):
            r[k + 1:k + 4] += rng.normal(0.004, 0.01, len(r[k + 1:k + 4]))
        r -= r.mean()  # 장기적으로 가격이 한쪽으로 치우치지 않게
        r = np.clip(r, -0.29, 0.29)
        p0 = float(rng.choice([800, 3000, 9000, 25000, 70000]))
        close = p0 * np.exp(np.cumsum(r))
        prev = np.concatenate([[p0], close[:-1]])
        gap = np.clip(rng.normal(0, vol / 2, n), -0.1, 0.1)
        open_ = prev * np.exp(gap)
        open_ = np.where(jump, prev * (1 + (close / prev - 1) * rng.uniform(0.2, 0.8, n)), open_)
        hi = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, vol / 2, n)))
        lo = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, vol / 2, n)))
        hi = np.minimum(hi, prev * 1.3)
        lo = np.maximum(lo, prev * 0.7)
        base_vol = rng.uniform(5e4, 2e6)
        volume = base_vol * np.exp(rng.normal(0, 0.5, n)) * (1 + 40 * np.abs(r))
        volume = np.where(jump, volume * rng.uniform(3, 15, n), volume)
        df = pd.DataFrame({
            "date": d, "code": code, "name": name, "market": market,
            "open": open_.round(), "high": hi.round(), "low": lo.round(), "close": close.round(),
            "volume": volume.round(),
        })
        df["high"] = df[["open", "high", "close"]].max(axis=1)
        df["low"] = df[["open", "low", "close"]].min(axis=1)
        df["diff"] = df["close"] - df["close"].shift(1).fillna(p0)
        # 일부 종목은 1:5 액면분할 (분할 전 원본 가격은 5배)
        if i % 25 == 5:
            k = n // 2
            for c in ("open", "high", "low", "close"):
                df.loc[: k - 1, c] = df.loc[: k - 1, c] * 5
            df.loc[: k - 1, "volume"] = (df.loc[: k - 1, "volume"] / 5).round()
            df.loc[: k - 1, "diff"] = df.loc[: k - 1, "diff"] * 5  # 분할 이전 전일대비도 분할 전 단위
            # 분할일의 전일대비는 기준가(분할 반영) 기준이므로 그대로
        df["value"] = (df["close"] * df["volume"]).round()
        df["mktcap"] = df["close"] * rng.uniform(5e6, 5e7)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def write(root: Path, **kw) -> Path:
    raw = root / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    out = raw / "demo.parquet"
    generate(**kw).to_parquet(out, index=False)
    return out
