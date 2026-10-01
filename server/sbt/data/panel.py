"""종목×일자 가격 패널.

모든 행은 (종목코드, 일자) 순으로 정렬된 평평한 배열로 보관한다.
지표 계산은 배열 전체에 대해 한 번에 하고, 종목 경계를 넘는 값만 NaN 으로 지운다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

PRICE_COLS = ["open", "high", "low", "close", "volume", "value", "mktcap"]
# 미국 스팩(기업인수목적회사) 이름 패턴
SPAC_US = r"\bAcquisition (?:Corp|Corporation|Co|Company|Ltd|Limited|Inc|Holdings)\b"


@dataclass
class Panel:
    df: pd.DataFrame  # 정렬된 원본 프레임 (date, code, name, market, open..., close_raw)
    gid: np.ndarray = field(init=False)  # 종목 그룹 번호
    pos: np.ndarray = field(init=False)  # 그룹 내 순번 (0부터)
    gend: np.ndarray = field(init=False)  # 해당 행이 속한 그룹의 마지막 행 인덱스
    cols: dict[str, np.ndarray] = field(init=False)

    def __post_init__(self) -> None:
        df = self.df
        codes = df["code"].to_numpy()
        n = len(df)
        if n == 0:
            self.gid = self.pos = self.gend = np.zeros(0, dtype=np.int64)
            self.cols = {}
            return
        new_group = np.empty(n, dtype=bool)
        new_group[0] = True
        new_group[1:] = codes[1:] != codes[:-1]
        self.gid = np.cumsum(new_group) - 1
        starts = np.flatnonzero(new_group)
        ends = np.append(starts[1:] - 1, n - 1)
        self.pos = np.arange(n) - starts[self.gid]
        self.gend = ends[self.gid]
        self.cols = {c: df[c].to_numpy(dtype=np.float64) for c in PRICE_COLS if c in df}
        self.dates = df["date"].to_numpy(dtype="datetime64[D]")
        self.codes = codes
        self.markets = df["market"].to_numpy()
        self.names = df["name"].to_numpy()
        kr = np.isin(self.markets, ["KOSPI", "KOSDAQ", "KONEX"])
        names = df["name"].astype(str)
        us_spac = names.str.contains(SPAC_US, case=False, regex=True).to_numpy()
        self.is_spac = np.where(kr, names.str.contains("스팩", regex=False).to_numpy(), us_spac)
        # 원/달러 환율 (미국 데이터에만 있음)
        self.fx = df["fx"].to_numpy(dtype=np.float64) if "fx" in df else None
        self.is_preferred = kr & (df["code"].astype(str).str[-1] != "0").to_numpy()

    def __len__(self) -> int:
        return len(self.df)

    @classmethod
    def load(cls, path: Path) -> "Panel":
        df = pd.read_parquet(path)
        df = df.sort_values(["code", "date"], kind="stable").reset_index(drop=True)
        return cls(df)

    def rows_for(self, code: str) -> np.ndarray:
        return np.flatnonzero(self.codes == code)
