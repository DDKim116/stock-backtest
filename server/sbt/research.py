"""전략 연구: 잘 알려진 매매 아이디어 여러 전략 유형을 숫자 조합까지 한꺼번에 돌려, 기간을 나눠 검증하고 순위를 매긴다.

  python -m sbt.data.cli research                 # 국내 전체 (코스피·코스닥)
  python -m sbt.data.cli research --markets 코스피 --save 5

- 유형마다 [a, b] 로 숫자 후보를 넣어 두고 탐색(explore)과 같은 방식으로 과거 구간 / 검증 구간을 따로 집계한다.
- 표본 부족, 어느 구간이든 평균 손실, 수익 난 해가 적음, 이웃 숫자에서 무너짐(과최적화)이면 탈락.
- 통과한 조합을 '두 구간 중 나쁜 쪽 평균 수익률'로 정렬, 유형마다 최대 2개까지만 상위에 올린다.
- 모든 신호에 유동성 조건(거래대금 10억 이상, 주가 1,000원 이상)을 붙여 실제로 사고팔 수 있는 종목만 본다.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

from . import config
from .data.panel import Panel
from .engine.run import Runner
from .engine.spec import parse_text, to_text

LIQ = "그리고 거래대금 >= 10억 그리고 종가 >= 1000"

# (이름, 아이디어 설명, 조건식 글) — [신호]에 LIQ 가 자동으로 붙는다
FAMILIES: list[tuple[str, str, str]] = [
    ("급등 다음날 시가", "거래량이 터지며 크게 오른 날, 다음 날 시가에 사서 짧게 먹기",
     """[신호]
거래량 >= 이평(거래량, 20) * [3, 5]
그리고 등락률 >= [8, 12, 20]
[매수]
다음날시가
[청산]
목표 = [5, 10]
손절 = 5
보유기간 = [5, 10]"""),
    ("급등 후 눌림 지정가", "급등한 날의 (시가+종가)÷2 에 지정가를 걸어 눌림에 사기",
     """[신호]
거래량 >= 거래량(1) * [3, 5]
그리고 등락률 >= [10, 15]
[매수]
지정가 = (시가 + 종가) / 2
유효기간 = 5
[청산]
목표 = [3, 5]
손절 = [5, 10]
보유기간 = 10"""),
    ("52주 신고가 추세", "1년 최고가를 거래량과 함께 돌파하면 사서, 고점 대비 일정 % 빠질 때까지 들고 가기",
     """[신호]
종가 > 이전(최고(고가, 250), 1)
그리고 거래량 >= 이평(거래량, 20) * [1.5, 3]
[매수]
다음날시가
[청산]
목표 = 없음
손절 = 10
추적손절 = [8, 12, 20]
보유기간 = 120"""),
    ("52주 신고가 + 50일선", "신고가 돌파 후 50일선을 깨면 팔기",
     """[신호]
종가 > 이전(최고(고가, 250), 1)
그리고 거래량 >= 이평(거래량, 20) * [1.5, 3]
[매수]
다음날시가
[청산]
목표 = 없음
손절 = [8, 12]
보유기간 = 120
청산조건 = 종가 < 이평(종가, 50)"""),
    ("터틀 20일 돌파", "20일 최고가 돌파에 사고 10일 최저가를 깨면 팔기 (장기 추세 위에서만)",
     """[신호]
종가 > 이전(최고(고가, 20), 1)
그리고 종가 > 이평(종가, [60, 120])
[매수]
다음날시가
[청산]
목표 = 없음
손절 = [7, 10]
보유기간 = 120
청산조건 = 종가 < 이전(최저(저가, 10), 1)"""),
    ("상승 추세 눌림목", "60일선 위 정배열 종목이 잠깐 과매도(RSI)일 때 사기",
     """[신호]
종가 > 이평(종가, 60)
그리고 이평(종가, 20) > 이평(종가, 60)
그리고 rsi(14) <= [35, 40, 45]
[매수]
다음날시가
[청산]
목표 = [5, 10]
손절 = [5, 8]
보유기간 = [10, 20]"""),
    ("RSI2 평균회귀", "장기 상승 종목이 2일 RSI 로 극단적 과매도일 때 사서 5일선 위로 오르면 팔기",
     """[신호]
rsi(2) <= [5, 10]
그리고 종가 > 이평(종가, [120, 200])
[매수]
다음날시가
[청산]
목표 = 없음
손절 = [7, 10]
보유기간 = 10
청산조건 = 종가 > 이평(종가, 5)"""),
    ("단기 급락 반등", "5일 동안 크게 빠지고 과매도인 종목의 기술적 반등",
     """[신호]
수익률(5) <= [-15, -20, -25]
그리고 rsi(14) <= 30
[매수]
다음날시가
[청산]
목표 = [5, 10]
손절 = [7, 10]
보유기간 = [5, 10]"""),
    ("골든크로스 + 거래량", "5일선이 20일선을 뚫고 거래량이 늘 때 (60일선 위)",
     """[신호]
상향돌파(이평(종가, 5), 이평(종가, 20))
그리고 종가 > 이평(종가, 60)
그리고 거래량 >= 이평(거래량, 20) * [1.5, 2]
[매수]
다음날시가
[청산]
목표 = [5, 10]
손절 = 5
보유기간 = [10, 20]"""),
    ("볼린저 하단 반등", "볼린저 밴드 하단 아래에서 양봉으로 돌아선 날",
     """[신호]
저가 < 이평(종가, 20) - 표준편차(종가, 20) * [2, 2.5]
그리고 종가 > 시가
그리고 종가 > 이평(종가, 120)
[매수]
다음날시가
[청산]
목표 = [3, 5]
손절 = [5, 8]
보유기간 = [5, 10]"""),
    ("갭상승 지속", "크게 갭상승하고 밀리지 않고 양봉으로 끝난 날",
     """[신호]
시가 >= 종가(1) * [1.03, 1.05]
그리고 종가 > 시가
그리고 거래량 >= 이평(거래량, 20) * 2
[매수]
다음날시가
[청산]
목표 = [5, 10]
손절 = 5
보유기간 = [5, 10]"""),
    ("장대양봉 지정가 눌림", "시가 대비 크게 오른 장대양봉 다음 며칠 안에 5% 아래 지정가로 사기",
     """[신호]
종가 >= 시가 * [1.1, 1.15]
그리고 거래대금 >= 100억
[매수]
지정가 = 종가 * [0.95, 0.97]
유효기간 = 3
[청산]
목표 = [5, 8]
손절 = 5
보유기간 = 10"""),
]


def _market_line(markets: list[str]) -> str:
    return "시장 = " + ", ".join(markets)


def _family_spec(text: str, markets: list[str]):
    lines = text.splitlines()
    out, in_sig = [], False
    for ln in lines:
        if ln.strip().startswith("["):
            if in_sig:
                out.append(LIQ)
            in_sig = ln.strip() == "[신호]"
        out.append(ln)
    if in_sig:
        out.append(LIQ)
    out += ["[대상]", _market_line(markets), "기간 = 2010-01-01 ~ 오늘", "제외 = 스팩, 우선주"]
    return parse_text("\n".join(out))


def _params(spec) -> str:
    e, x = spec.entry, spec.exit
    buy = {"limit": f"지정가 {e.price} ({e.valid_days}일)", "next_open": "다음날 시가", "close": "당일 종가"}[e.type]
    ex = []
    ex.append("목표 없음" if x.target_pct is None else f"목표 {x.target_pct:g}%")
    if x.stop_pct is not None:
        ex.append(f"손절 {x.stop_pct:g}%")
    if x.trail_pct is not None:
        ex.append(f"추적 {x.trail_pct:g}%")
    if x.exit_signal:
        ex.append(f"청산조건 {x.exit_signal}")
    ex.append(f"최대 {x.max_days}일")
    return f"{buy} · " + ", ".join(ex)


def run(dataset: str = "kr", markets: list[str] | None = None, save: int = 0, log=print) -> Path:
    markets = markets or ["코스피", "코스닥"]
    root = config.dataset_dir(dataset)
    t0 = time.time()
    log(f"데이터 읽는 중… ({root / 'prices.parquet'})")
    panel = Panel.load(root / "prices.parquet")
    runner = Runner(panel, {"name": dataset, "markets": ["KOSPI", "KOSDAQ"], "currency": "KRW"})
    log(f"{len(panel.dates):,}행 읽음 ({time.time() - t0:.0f}초). 전략 유형 {len(FAMILIES)}개를 돌립니다.")
    results = []
    for fi, (name, idea, text) in enumerate(FAMILIES, 1):
        t1 = time.time()
        spec = _family_spec(text, markets)
        try:
            r = runner.explore(spec)
        except Exception as e:  # noqa: BLE001 — 한 유형이 실패해도 나머지는 계속
            log(f"[{fi}/{len(FAMILIES)}] {name}: 실패 ({e})")
            continue
        log(f"[{fi}/{len(FAMILIES)}] {name}: 조합 {r['n_total']}개 중 통과 {r['n_passed']}개 ({time.time() - t1:.0f}초)")
        best_any = (r["passed"] or r["near_miss"] or [None])[0]
        results.append({"family": name, "idea": idea, "split": r["split"], "n_total": r["n_total"],
                        "n_passed": r["n_passed"], "passed": r["passed"][:5], "near_miss": r["near_miss"][:2],
                        "best": best_any})

    # 전체 순위: 유형마다 최대 2개
    pool = []
    for fr in results:
        for k, row in enumerate(fr["passed"][:2]):
            pool.append((row["score"], fr, row, k))
    pool.sort(key=lambda x: x[0], reverse=True)

    split = results[0]["split"] if results else "-"
    L = []
    L.append(f"전략 연구 결과 · {dt.date.today()} · {', '.join(markets)} · 2010~ (과거 구간 ~{split} 이전 / 검증 구간 {split}~)")
    L.append("유동성 조건: 거래대금 10억 이상, 주가 1,000원 이상 · 비용(수수료·세금) 차감 · 평균은 거래 1건당 수익률")
    L.append("성공 = 목표 도달 비율 (목표 없음인 전략은 수익으로 끝난 비율)")
    L.append("")
    L.append(f"■ 통과 순위 (두 구간 중 나쁜 쪽 평균 수익률 순, 유형당 최대 2개) — {len(pool)}개")
    for rank, (score, fr, row, _) in enumerate(pool[:15], 1):
        s = parse_text_safe(row["spec"])
        i, o = row["is"], row["oos"]
        L.append(f"{rank:>2}. [{fr['family']}]  과거 {i['avg_ret']:+.2f}% (성공 {i['success_rate']}%, {i['n_complete']:,}건)"
                 f"  검증 {o['avg_ret']:+.2f}% (성공 {o['success_rate']}%, {o['n_complete']:,}건)"
                 f"  수익 난 해 {round((row['pos_years'] or 0) * 100)}%")
        if s is not None:
            L.append(f"    신호: {s.signal.replace(LIQ, '').strip()}")
            L.append(f"    매매: {_params(s)}")
    L.append("")
    L.append("■ 전략 유형별 요약 (통과 수 / 조합 수, 가장 좋은 조합의 검증 구간 평균)")
    for fr in results:
        b = fr["best"]
        tag = "" if fr["n_passed"] else " (통과 없음 → 탈락 중 최선)"
        bo = f"{b['oos']['avg_ret']:+.2f}% ({b['oos']['n_complete']:,}건)" if b and b["oos"]["avg_ret"] is not None else "-"
        L.append(f" - {fr['family']}: {fr['n_passed']}/{fr['n_total']} · 검증 {bo}{tag}")
    L.append("")
    L.append(f"소요 {round((time.time() - t0) / 60, 1)}분")
    report = "\n".join(L)

    out_dir = config.DATA_DIR / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    txt = out_dir / f"research-{stamp}.txt"
    txt.write_text(report)
    (out_dir / f"research-{stamp}.json").write_text(json.dumps(results, ensure_ascii=False, default=str))
    log("")
    log(report)

    if save and pool:
        from .api.store import Store

        store = Store(config.DATA_DIR / "app.sqlite3")
        for rank, (score, fr, row, _) in enumerate(pool[:save], 1):
            s = parse_text_safe(row["spec"])
            if s is None:
                continue
            nm = f"추천 {rank} · {fr['family']} ({dt.date.today().strftime('%m/%d')})"
            store.save(nm, to_text(s), note=f"전략 연구 {dt.date.today()} · {fr['idea']}")
        log(f"\n상위 {min(save, len(pool))}개를 앱의 '전략' 탭에 저장했습니다.")
    log(f"\n보고서 파일: {txt}")
    return txt


def parse_text_safe(spec_json):
    from .engine.spec import Spec

    try:
        return Spec.model_validate(spec_json)
    except Exception:  # noqa: BLE001
        return None
