"""이벤트 검증: 신호 → 매수 체결 → 청산 결과를 종목·일자별로 계산한다.

일봉만으로 판단하므로 장중 순서를 모르는 경우가 있다. 그래서 두 가지로 계산한다.
- 보수적(cons): 애매하면 불리하게 (같은 날 목표·손절 모두 닿으면 손절, 체결 전 고가일 수 있으면 미도달)
- 낙관적(opt) : 애매하면 유리하게
두 결과가 다른 거래를 '불확실'로 표시한다.

하루 번호: 0 = 체결일, 1..max_days = 이후 거래일.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..data.panel import Panel
from .spec import Spec

# 체결 방식
FILL_AT_CLOSE = 0  # 신호일 종가 매수 → 체결일 장중 판정 없음
FILL_AT_OPEN = 1  # 시가 체결 → 체결일 고가·저가 모두 체결 이후
FILL_INTRADAY = 2  # 장중 지정가 체결 → 체결 전 고가였을 수 있음

# 결과 코드
NO_FILL = 0
TARGET = 1
STOP = 2
TIMEOUT = 3  # 보유기간 만료 → 마지막 날 종가 청산
OPEN = 4  # 데이터가 끝나 아직 결과가 정해지지 않음

OUTCOME_LABEL = {NO_FILL: "미체결", TARGET: "목표도달", STOP: "손절", TIMEOUT: "기간만료", OPEN: "진행중"}


@dataclass
class Trades:
    sig: np.ndarray  # 신호 행 인덱스
    limit: np.ndarray  # 지정가 (limit 방식이 아니면 NaN)
    fill_row: np.ndarray  # 체결 행 인덱스 (-1 = 미체결)
    fill_kind: np.ndarray
    fill_price: np.ndarray
    # 보수적 / 낙관적
    outcome_c: np.ndarray
    outcome_o: np.ndarray
    exit_row_c: np.ndarray
    exit_row_o: np.ndarray
    exit_price_c: np.ndarray
    exit_price_o: np.ndarray
    days_c: np.ndarray  # 체결일로부터 청산까지 거래일 수
    days_o: np.ndarray
    mfe: np.ndarray  # 보유기간 중 최고 수익률(%) — 고가 기준
    mae: np.ndarray  # 보유기간 중 최저 수익률(%) — 저가 기준
    ret_c: np.ndarray  # 비용 차감 수익률(%)
    ret_o: np.ndarray

    def __len__(self) -> int:
        return len(self.sig)


def dedupe(sig: np.ndarray, panel: Panel, days: int) -> np.ndarray:
    """같은 종목에서 직전 채택 신호 후 days 거래일 이내 신호를 제외."""
    if days <= 0 or len(sig) == 0:
        return sig
    keep = []
    last_gid, last_pos = -1, -(10**9)
    for i in sig:  # sig 는 (종목, 일자) 순으로 정렬되어 있음
        g, p = panel.gid[i], panel.pos[i]
        if g != last_gid or p - last_pos > days:
            keep.append(i)
            last_gid, last_pos = g, p
    return np.asarray(keep, dtype=np.int64)


def _gather(arr: np.ndarray, idx: np.ndarray, valid: np.ndarray) -> np.ndarray:
    out = arr[np.clip(idx, 0, len(arr) - 1)]
    return np.where(valid, out, np.nan)


def simulate(panel: Panel, sig: np.ndarray, spec: Spec, limit_price: np.ndarray | None,
             fx: np.ndarray | None = None) -> Trades:
    """sig: 신호 행 인덱스 배열. limit_price: 행 전체에 대한 지정가 배열 (limit 방식일 때)."""
    o, h, lo, c = (panel.cols[k] for k in ("open", "high", "low", "close"))
    gend = panel.gend
    n = len(sig)
    e, x = spec.entry, spec.exit

    # ---------------- 체결
    fill_row = np.full(n, -1, dtype=np.int64)
    fill_kind = np.full(n, -1, dtype=np.int8)
    fill_price = np.full(n, np.nan)
    lim = np.full(n, np.nan)

    if e.type == "close":
        fill_row[:] = sig
        fill_kind[:] = FILL_AT_CLOSE
        fill_price[:] = c[sig]
    elif e.type == "next_open":
        j = sig + 1
        ok = j <= gend[sig]
        fill_row[ok] = j[ok]
        fill_kind[ok] = FILL_AT_OPEN
        fill_price[ok] = o[j[ok]]
    else:
        assert limit_price is not None
        lim = limit_price[sig]
        pending = np.isfinite(lim) & (lim > 0)
        for k in range(1, int(e.valid_days) + 1):
            j = sig + k
            ok = pending & (j <= gend[sig])
            jj = np.where(ok, j, 0)
            hit = ok & (lo[jj] <= lim)
            at_open = hit & (o[jj] <= lim)
            fill_row[hit] = j[hit]
            fill_kind[hit] = np.where(at_open[hit], FILL_AT_OPEN, FILL_INTRADAY)
            fill_price[hit] = np.where(at_open[hit], o[jj][hit], lim[hit])
            pending &= ~hit
            if not pending.any():
                break

    filled = fill_row >= 0
    D = int(x.max_days)

    # ---------------- 보유 구간 행렬 (n, D+1)
    fr = np.where(filled, fill_row, 0)
    idx = fr[:, None] + np.arange(D + 1)[None, :]
    valid = filled[:, None] & (idx <= gend[fr][:, None])
    H = _gather(h, idx, valid)
    L = _gather(lo, idx, valid)
    O = _gather(o, idx, valid)
    C = _gather(c, idx, valid)

    fp = fill_price[:, None]
    tgt = fp * (1 + x.target_pct / 100.0) if x.target_pct is not None else None
    stp = fp * (1 - x.stop_pct / 100.0) if x.stop_pct is not None else None

    with np.errstate(invalid="ignore"):
        if tgt is not None:
            if x.target_basis == "high":
                hitT = H >= tgt
            else:
                hitT = C >= tgt
        else:
            hitT = np.zeros_like(valid)
        hitS = (L <= stp) if stp is not None else np.zeros_like(valid)

    hitT = hitT & valid
    hitS = hitS & valid

    # 체결일(0번째 날) 보정
    kind = fill_kind
    close_fill = kind == FILL_AT_CLOSE
    intraday = kind == FILL_INTRADAY
    hitT_c, hitT_o = hitT.copy(), hitT.copy()
    hitS0 = hitS[:, 0].copy()
    hitT_c[close_fill, 0] = False
    hitT_o[close_fill, 0] = False
    hitS0[close_fill] = False
    if tgt is not None and x.target_basis == "high":
        # 장중 체결이면 고가가 체결 전이었을 수 있다 → 종가가 목표 이상일 때만 확실
        with np.errstate(invalid="ignore"):
            sure = C[:, 0] >= tgt[:, 0]
        hitT_c[intraday, 0] = hitT[intraday, 0] & sure[intraday]
    hitS_all = hitS.copy()
    hitS_all[:, 0] = hitS0

    days = np.arange(D + 1)[None, :]
    res = {}
    for mode, hT in (("c", hitT_c), ("o", hitT_o)):
        anyT = hT.any(axis=1)
        anyS = hitS_all.any(axis=1)
        dT = np.where(anyT, np.argmax(hT, axis=1), D + 1)
        dS = np.where(anyS, np.argmax(hitS_all, axis=1), D + 1)
        if mode == "c":
            is_stop = anyS & (dS <= dT)
            is_tgt = anyT & (dT < dS)
        else:
            is_tgt = anyT & (dT <= dS)
            is_stop = anyS & (dS < dT)
        # 마지막 유효일 (데이터 끝 고려)
        last_valid = valid.sum(axis=1) - 1
        complete = last_valid >= D
        outcome = np.full(n, NO_FILL, dtype=np.int8)
        outcome[filled] = np.where(complete[filled], TIMEOUT, OPEN)
        outcome[is_stop] = STOP
        outcome[is_tgt] = TARGET
        exit_day = np.where(is_tgt, dT, np.where(is_stop, dS, np.where(complete, D, -1)))
        exit_day = np.where(filled, exit_day, -1)

        ed = np.clip(exit_day, 0, D)
        rows = np.arange(n)
        o_at = O[rows, ed]
        c_at = C[rows, ed]
        price = np.full(n, np.nan)
        if tgt is not None:
            t1 = tgt[:, 0]
            if x.target_basis == "high":
                # 갭 상승으로 시가가 목표 이상이면 시가에 매도 (체결일 제외)
                p = np.where((ed > 0) & (o_at >= t1), o_at, t1)
            else:
                p = c_at
            price = np.where(is_tgt, p, price)
        if stp is not None:
            s1 = stp[:, 0]
            p = np.where((ed > 0) & (o_at <= s1), o_at, s1)
            price = np.where(is_stop, p, price)
        timeout = outcome == TIMEOUT
        price = np.where(timeout, c_at, price)
        exit_row = np.where(exit_day >= 0, fr + ed, -1)
        res[mode] = (outcome, exit_row, price, np.where(exit_day >= 0, exit_day, -1))

    # 보유기간 중 최고/최저 (체결일은 시가 체결일 때만 포함)
    Hm, Lm = H.copy(), L.copy()
    Hm[kind != FILL_AT_OPEN, 0] = np.nan
    Lm[kind != FILL_AT_OPEN, 0] = np.nan
    with np.errstate(invalid="ignore", divide="ignore"):
        all_nan = ~np.isfinite(Hm).any(axis=1)
        mfe = (np.nanmax(np.where(all_nan[:, None], 0, Hm), axis=1) / fill_price - 1) * 100
        mae = (np.nanmin(np.where(all_nan[:, None], 0, Lm), axis=1) / fill_price - 1) * 100
        mfe[all_nan | ~filled] = np.nan
        mae[all_nan | ~filled] = np.nan

    cs = spec.costs
    buy_cost = 1 + (cs.buy_fee_pct + cs.slippage_pct) / 100
    sell_keep = 1 - (cs.sell_fee_pct + cs.sell_tax_pct + cs.slippage_pct) / 100

    def net(price, exit_row):
        buy, sell = fill_price, price
        if fx is not None:
            # 원화 기준: 체결일·청산일 환율을 곱한다
            buy = fill_price * np.where(filled, fx[np.clip(fill_row, 0, None)], np.nan)
            sell = price * np.where(exit_row >= 0, fx[np.clip(exit_row, 0, None)], np.nan)
        with np.errstate(invalid="ignore"):
            return ((sell * sell_keep) / (buy * buy_cost) - 1) * 100

    return Trades(
        sig=sig, limit=lim, fill_row=fill_row, fill_kind=fill_kind, fill_price=fill_price,
        outcome_c=res["c"][0], outcome_o=res["o"][0],
        exit_row_c=res["c"][1], exit_row_o=res["o"][1],
        exit_price_c=res["c"][2], exit_price_o=res["o"][2],
        days_c=res["c"][3], days_o=res["o"][3],
        mfe=mfe, mae=mae, ret_c=net(res["c"][2], res["c"][1]), ret_o=net(res["o"][2], res["o"][1]),
    )
