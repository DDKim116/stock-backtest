"""QLD(나스닥100 2배) 매일 1만원 적립 검증.

  python -m sbt.data.cli qld-test

데이터(야후): QLD 수정주가. 상장(2006-06) 전은 나스닥100 하루 등락 × 2 에서 연 비용을 뺀 '가짜 QLD' 로 이어 붙인다.
QQQ 는 us-test 와 같이 QQQ ← 나스닥100 지수. 환율(KRW=X)은 2003-12 부터라 그 전 시작은 달러 기준으로 본다.

1부. 각 해 1월 첫 거래일부터 미국 거래일마다 1만원씩: QLD vs 같은 돈 QQQ → 10년 뒤(있으면)와 지금.
2부. 지금 적립(VOO 1.5만 + QQQ 1만)에 QLD 1만을 더했을 때 vs 그 1만을 QQQ 로 넣었을 때 (2010~ 시작).
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import pandas as pd

from . import config
from .research11 import _yf, dca, splice, synth_lev

QLD_COST = 0.025  # 가짜 QLD 연 비용: 운용보수 0.95% + 레버리지 자금 비용(대략)
AMT = 10_000


def load(cache: Path | None = None) -> dict[str, pd.Series]:
    """시세 받기 (하루 한 번 캐시)."""
    cache = cache or config.DATA_DIR / "us_qld.parquet"
    if cache.exists() and dt.date.fromtimestamp(cache.stat().st_mtime) == dt.date.today():
        df = pd.read_parquet(cache)
        return {c: df[c].dropna() for c in df.columns}
    spx, ndx = _yf("^GSPC"), _yf("^NDX")
    spy, voo, qqq, qld = _yf("SPY"), _yf("VOO"), _yf("QQQ"), _yf("QLD")
    fx = _yf("KRW=X", "2003-01-01")
    fake = synth_lev(ndx, 2.0, QLD_COST)
    s = {
        "SP500": splice(voo, splice(spy, spx)),
        "NDX100": splice(qqq, ndx),
        "QLD": splice(qld, fake),
        "KRW": fx[(fx > 500) & (fx < 3000)],
        "_qld_real": qld,
        "_qld_fake": fake,
    }
    pd.DataFrame(s).to_parquet(cache)
    return s


def _won(v: float) -> str:
    return f"{v / 1e8:,.2f}억" if v >= 1e8 else f"{v / 1e4:,.0f}만"


def _mdd(v: pd.Series) -> float:
    return float((v / v.cummax() - 1).min() * 100)


def run(log=print) -> Path:
    t0 = time.time()
    log("시세 받는 중 (야후)…")
    s = load()
    sp, nq, ql, fx = s["SP500"], s["NDX100"], s["QLD"], s["KRW"]
    last = min(nq.index[-1], ql.index[-1])
    L = [f"QLD(나스닥100 2배) 매일 1만원 적립 · {dt.date.today()} · 데이터 ~{last.date()} · 배당 재투자(수정주가)"]
    real, fake = s["_qld_real"], s["_qld_fake"]
    both = real.index.intersection(fake.index)
    if len(both) > 250:
        rr, ff = real[both] / real[both].iloc[0], fake[both] / fake[both].iloc[0]
        L.append(f"가짜 QLD 확인: {both[0].date()}~ 실제 QLD {rr.iloc[-1]:.1f}배 vs 가짜 {ff.iloc[-1]:.1f}배 "
                 f"(하루 등락 상관 {real[both].pct_change().corr(fake[both].pct_change()):.3f})")
    q_all = ql[ql.index <= last]
    L.append(f"QLD 역대 최대 낙폭(고점→저점, 가격): {_mdd(q_all):.0f}% · QQQ {_mdd(nq[nq.index <= last]):.0f}%")

    # 1부
    L += ["", "1부. 매일 1만원 (각 해 1월 첫 거래일 시작) · QLD vs 같은 돈으로 QQQ",
          "  시작 | 10년 뒤 QLD / QQQ (원금)                 | 지금 QLD / QQQ (원금)                      | "
          "QLD 원금 대비 최악 · 평가액 최대 낙폭 | QQQ 최악",
          "  ($ = 환율 데이터 전이라 달러 기준)"]
    for y in range(1990, last.year + 1):
        start = pd.Timestamp(f"{y}-01-01")
        f = fx if start >= fx.index[0] else None
        mark = " " if f is not None else "$"
        end10 = pd.Timestamp(f"{y + 10}-01-01") - pd.Timedelta(days=1)
        a10 = dca(ql, f, start, end10, AMT, "day") if end10 <= last else {}
        b10 = dca(nq, f, start, end10, AMT, "day") if end10 <= last else {}
        a, b = dca(ql, f, start, last, AMT, "day"), dca(nq, f, start, last, AMT, "day")
        if not a or not b:
            continue
        t10 = (f"{_won(a10['value'])} ({a10['ret']:+.0f}%) / {_won(b10['value'])} ({b10['ret']:+.0f}%) "
               f"({_won(a10['paid'])})" if a10 and b10 else "아직 10년 안 됨")
        L.append(f" {mark}{y} | {t10:<40} | {_won(a['value'])} (연 {a['irr']:+.1f}%) / {_won(b['value'])} "
                 f"(연 {b['irr']:+.1f}%) ({_won(a['paid'])}) | {a['worst']:+.0f}% · {a['mdd']:.0f}% | {b['worst']:+.0f}%")
    L += ["  원금 대비 최악 = 적립하는 동안 평가액이 넣은 돈보다 가장 많이 밑돌았을 때",
          "  평가액 최대 낙폭 = 쌓인 평가액이 가장 좋았을 때에서 가장 많이 빠졌을 때 (매일 넣는 돈 포함)"]

    # 2부
    L += ["", "2부. 지금 적립(VOO 1.5만 + QQQ 1만)에 1만원 더하기: QLD 로 vs QQQ 로 (원화 기준, 지금까지)",
          "  시작 | 원금 | +QLD 1만: 평가액 (수익률, 낙폭) | +QQQ 1만: 평가액 (수익률, 낙폭) | QLD 가 차지하는 비중(지금)"]
    for y in (2007, 2010, 2013, 2015, 2018, 2020, 2021, 2022, 2024):
        start = pd.Timestamp(f"{y}-01-01")
        v = dca(sp, fx, start, last, 15_000, "day")
        q = dca(nq, fx, start, last, 10_000, "day")
        x_l, x_q = dca(ql, fx, start, last, AMT, "day"), dca(nq, fx, start, last, AMT, "day")
        if not (v and q and x_l and x_q):
            continue
        base = v["v"].add(q["v"], fill_value=0)
        with_l, with_q = base.add(x_l["v"], fill_value=0), base.add(x_q["v"], fill_value=0)
        paid = v["paid"] + q["paid"] + x_l["paid"]
        ret_l = (with_l.iloc[-1] / paid - 1) * 100
        ret_q = (with_q.iloc[-1] / paid - 1) * 100
        L.append(f"  {y} | {_won(paid)} | {_won(with_l.iloc[-1])} ({ret_l:+.0f}%, 낙폭 {_mdd(with_l):.0f}%) | "
                 f"{_won(with_q.iloc[-1])} ({ret_q:+.0f}%, 낙폭 {_mdd(with_q):.0f}%) | "
                 f"{x_l['value'] / with_l.iloc[-1] * 100:.0f}%")
    L += ["  (SCHD 는 빼고 계산)",
          f"  {both[0].date() if len(both) else '2006-06'} 전 QLD 는 나스닥100 × 2 에서 연 {QLD_COST * 100:.1f}% 를 뺀 가짜.",
          f"소요 {round((time.time() - t0) / 60, 1)}분"]
    report = "\n".join(L)
    out = config.DATA_DIR / "research"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"qld-{dt.datetime.now().strftime('%Y%m%d-%H%M')}.txt"
    path.write_text(report)
    log("")
    log(report)
    return path
