"""데이터 관리 명령.

  python -m sbt.data.cli demo                      # 가상 데이터 생성 + 빌드
  python -m sbt.data.cli kr-collect --start 2010-01-01   # KRX 원본 수집 (이어받기 지원)
  python -m sbt.data.cli kr-update                 # 최근 며칠 갱신 + 빌드 (매일 자동 실행용)
  python -m sbt.data.cli research --save 5          # 여러 전략 유형을 한꺼번에 돌려 순위 (약 10~30분)
  python -m sbt.data.cli research2                 # 2차: 급락 반등 정밀 점검 + 신고가 시장 필터
  python -m sbt.data.cli dart-collect               # DART 재무(매출·영업이익·순이익) 2015~ 받기 (키는 앱 설정에서)
  python -m sbt.data.cli growth-test                # 매출 꾸준히 느는 코스피 상위 100 회사: 주가와의 관계, 언제 사고 팔지
  python -m sbt.data.cli profit-test                # 영업이익 증익·가속·흑자전환 공시 뒤 주가 (상위 100 / 101~300위)
  python -m sbt.data.cli accel-plan                 # 실적 가속 중형주 계좌 다듬기 + v2b 와 같은 계좌 vs 따로
  python -m sbt.data.cli bull-plan                  # 대상승장 계좌: 코스피 200일선 · 주도주 + 종목 정리 규칙 · 섞기
  python -m sbt.data.cli leader-test                # v2b 대형주 빼기 · 대형주 신고가 더하기 · 주도주 계좌(코스피 상위 100 모멘텀)
  python -m sbt.data.cli box-test                   # 박스피 점검: 코스피 시총 상위 100 vs 나머지, 박스장에서 통하는 전략
  python -m sbt.data.cli auto-report                # 키움 모의투자 자동 매매 성적표 (기대치 vs 실제)
  python -m sbt.data.cli qld-test                   # 미국: QLD(나스닥100 2배) 매일 1만원 적립, 시작 해마다 vs QQQ
  python -m sbt.data.cli qld-rule                   # 미국: QLD 5천원 + 비중 25% 상한 규칙 (VOO 1.5만 + QQQ 1만과 함께)
  python -m sbt.data.cli qld-dip                    # 미국: QLD 가 평단보다 싸면 1만·2만으로 더 사기 vs 그 돈을 VOO 로
  python -m sbt.data.cli us-test                    # 미국: VOO 매일 적립 · SPY/QQQ 월적립 · 무한매수법(TQQQ) 검증
  python -m sbt.data.cli size-test                  # v2b 1회 금액: 고정 vs 계좌 비율 (연 수익·낙폭·연속 손실·노는 돈)
  python -m sbt.data.cli bunt-test                  # 중간값 번트: 목표 +2.5%, 손절 = 장대양봉 저가, 갭상승 3%↑ 제외
  python -m sbt.data.cli mid-test                   # 중간값 전략 살리기 (목표·손절·시장 조건·지정가 바꿔 비교)
  python -m sbt.data.cli trend-test                 # 신고가 추세 다듬기 + v2b·중간값과 5천만 계좌로 합쳐 굴리기
  python -m sbt.data.cli bear-test                  # 하락장 전략 후보 비교 (약세장 해 따로)
  python -m sbt.data.cli bull-test                  # 상승장 전략 후보 비교 (강세장 해 따로)
  python -m sbt.data.cli breadth-test               # v2b 시장 하락 조건: 지수 vs 상승비율·동일가중 비교
  python -m sbt.data.cli recommend --save          # 지수 조건을 붙인 추천 전략 검증 + 전략 탭에 저장
  python -m sbt.data.cli rank                      # 하루 신호가 많을 때 고르는 순서 비교 (급락 반등 v2b, 하루 5종목·1회 500만원)
  python -m sbt.data.cli kr-evening                # 오늘 국내 시세를 야후로 임시 갱신 (다음 날 아침 공식 값으로 교체)
  python -m sbt.data.cli kr-build                  # 원본 → prices.parquet
  python -m sbt.data.cli us-collect                # 미국 전 종목 첫 수집 (야후)
  python -m sbt.data.cli us-update                 # 미국 최근 한 달 갱신 + 빌드 (매일 자동 실행용)
  python -m sbt.data.cli index-update             # 지수(코스피·코스닥·S&P 500·나스닥) 갱신
  python -m sbt.data.cli check --code 005930       # 특정 종목 최근 가격 출력 (증권사 차트와 대조용)
  python -m sbt.data.cli check --dataset us --code AAPL
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

import pandas as pd

from .. import config, krx_status
from . import build, indices, krx, krx_web, synthetic, us, yahoo_kr


def main(argv: list[str] | None = None) -> None:
    # tee·로그 파일로 보낼 때도 진행 상황이 바로 보이게
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(prog="sbt.data.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo")
    c = sub.add_parser("kr-collect")
    c.add_argument("--start", default="2010-01-01")
    c.add_argument("--end", default=None)
    c.add_argument("--max-calls", type=int, default=9000)
    u = sub.add_parser("kr-update")
    u.add_argument("--days", type=int, default=10, help="최근 n일은 다시 받아 덮어씀 (정정 반영)")
    sub.add_parser("kr-build")
    uc = sub.add_parser("us-collect")
    uc.add_argument("--start", default="2010-01-01")
    uc.add_argument("--refetch", action="store_true", help="이미 받은 종목도 다시 받기")
    sub.add_parser("us-update")
    sub.add_parser("us-build")
    sub.add_parser("index-update")
    sub.add_parser("kr-evening")
    rk = sub.add_parser("rank")
    rk.add_argument("--dataset", default="kr")
    rk.add_argument("--per", type=float, default=5_000_000, help="1회 매수금(원)")
    rk.add_argument("--daily", type=int, default=5, help="하루 최대 종목 수")
    rk.add_argument("--capital", default="50000000,100000000", help="자본(원), 쉼표로 여러 개")
    ar = sub.add_parser("auto-report")
    ar.add_argument("--dataset", default="kr")
    sub.add_parser("us-test")
    sub.add_parser("qld-test")
    sub.add_parser("qld-rule")
    sub.add_parser("qld-dip")
    dc = sub.add_parser("dart-collect")
    dc.add_argument("--start", type=int, default=2015)
    gt = sub.add_parser("growth-test")
    gt.add_argument("--dataset", default="kr")
    pt = sub.add_parser("profit-test")
    pt.add_argument("--dataset", default="kr")
    acp = sub.add_parser("accel-plan")
    acp.add_argument("--dataset", default="kr")
    bp = sub.add_parser("bull-plan")
    bp.add_argument("--dataset", default="kr")
    ld = sub.add_parser("leader-test")
    ld.add_argument("--dataset", default="kr")
    bx = sub.add_parser("box-test")
    bx.add_argument("--dataset", default="kr")
    sz = sub.add_parser("size-test")
    sz.add_argument("--dataset", default="kr")
    bu = sub.add_parser("bunt-test")
    bu.add_argument("--dataset", default="kr")
    mt = sub.add_parser("mid-test")
    mt.add_argument("--dataset", default="kr")
    tc = sub.add_parser("trend-check")
    tc.add_argument("--dataset", default="kr")
    tt = sub.add_parser("trend-test")
    tt.add_argument("--dataset", default="kr")
    br = sub.add_parser("bear-test")
    br.add_argument("--dataset", default="kr")
    bl = sub.add_parser("bull-test")
    bl.add_argument("--dataset", default="kr")
    bt = sub.add_parser("breadth-test")
    bt.add_argument("--dataset", default="kr")
    rc = sub.add_parser("recommend")
    rc.add_argument("--dataset", default="kr")
    rc.add_argument("--save", action="store_true", help="앱 '전략' 탭에 저장")
    r2 = sub.add_parser("research2")
    r2.add_argument("--dataset", default="kr")
    rs = sub.add_parser("research")
    rs.add_argument("--dataset", default="kr")
    rs.add_argument("--markets", default="코스피,코스닥", help="예: 코스피 또는 코스피,코스닥")
    rs.add_argument("--save", type=int, default=0, help="상위 n개를 앱 '전략' 탭에 저장")
    yt = sub.add_parser("kr-yahoo-test")
    yt.add_argument("--date", default=None, help="YYYY-MM-DD (기본: 오늘)")
    yt.add_argument("--n", type=int, default=300, help="비교할 종목 수")
    wt = sub.add_parser("kr-web-test")
    wt.add_argument("--date", default=None, help="YYYY-MM-DD (기본: 오늘)")
    k = sub.add_parser("check")
    k.add_argument("--dataset", default="kr")
    k.add_argument("--code", required=True)
    k.add_argument("--n", type=int, default=15)
    a = ap.parse_args(argv)

    if a.cmd == "demo":
        root = config.dataset_dir("demo")
        synthetic.write(root)
        build.build(root)
    elif a.cmd == "kr-collect":
        root = config.dataset_dir("kr")
        end = dt.date.fromisoformat(a.end) if a.end else dt.date.today()
        krx.collect(root, dt.date.fromisoformat(a.start), end, config.KRX_API_KEY, a.max_calls)
    elif a.cmd == "kr-update":
        root = config.dataset_dir("kr")
        today = dt.date.today()
        m = krx.load_manifest(root)
        start = min(dt.date.fromisoformat(max(m)) if m else today, today - dt.timedelta(days=a.days))
        try:
            n = krx.collect(root, start, today, config.KRX_API_KEY, refetch_recent_days=a.days)
        except krx.KrxError as e:
            krx_status.record_error("stock", str(e), krx.is_auth_error(e))
            raise
        krx_status.record_ok("stock")
        print(f"새로 저장 {n}일")
        yahoo_kr.clear_if_official(root)
        build.build(root)
        try:
            indices.update_kr(config.KRX_API_KEY)
        except Exception as e:  # noqa: BLE001 — 지수는 실패해도 종목 갱신에는 영향 없음
            print(f"한국 지수: {e}")
        from .. import pulse
        pulse.update_fear()  # VIX·VKOSPI (실패해도 넘어감)
    elif a.cmd == "kr-build":
        build.build(config.dataset_dir("kr"))
    elif a.cmd == "us-collect":
        root = config.dataset_dir("us")
        us.collect(root, a.start, a.refetch)
        us.build(root)
    elif a.cmd == "us-update":
        root = config.dataset_dir("us")
        us.update(root)
        us.build(root)
        try:
            indices.update_us()
        except Exception as e:  # noqa: BLE001
            print(f"미국 지수 갱신 실패: {e}")
        from .. import pulse
        pulse.update_fear()
    elif a.cmd == "us-build":
        us.build(config.dataset_dir("us"))
    elif a.cmd == "rank":
        from .. import research4
        research4.run(a.dataset, a.per, a.daily, tuple(float(x) for x in a.capital.split(",") if x.strip()))
    elif a.cmd == "auto-report":
        from .. import autoreport
        autoreport.run(a.dataset)
    elif a.cmd == "us-test":
        from .. import research11
        research11.run()
    elif a.cmd == "qld-test":
        from .. import research12
        research12.run()
    elif a.cmd == "qld-rule":
        from .. import research13
        research13.run()
    elif a.cmd == "dart-collect":
        from . import dart
        dart.run_collect(a.start)
    elif a.cmd == "growth-test":
        from .. import research17
        research17.run(a.dataset)
    elif a.cmd == "profit-test":
        from .. import research18
        research18.run(a.dataset)
    elif a.cmd == "accel-plan":
        from .. import research19
        research19.run(a.dataset)
    elif a.cmd == "bull-plan":
        from .. import research16
        research16.run(a.dataset)
    elif a.cmd == "leader-test":
        from .. import research15
        research15.run(a.dataset)
    elif a.cmd == "box-test":
        from .. import research14
        research14.run(a.dataset)
    elif a.cmd == "qld-dip":
        from .. import research13
        research13.run_dip()
    elif a.cmd == "size-test":
        from .. import research10
        research10.run(a.dataset)
    elif a.cmd == "bunt-test":
        from .. import research9
        research9.run(a.dataset)
    elif a.cmd == "mid-test":
        from .. import research8
        research8.run(a.dataset)
    elif a.cmd == "trend-check":
        from .. import research7
        research7.run_check(a.dataset)
    elif a.cmd == "trend-test":
        from .. import research7
        research7.run(a.dataset)
    elif a.cmd in ("bear-test", "bull-test"):
        from .. import research6
        research6.run(a.dataset, "bear" if a.cmd == "bear-test" else "bull")
    elif a.cmd == "breadth-test":
        from .. import research5
        research5.run(a.dataset)
    elif a.cmd == "recommend":
        from .. import research3
        research3.run(a.dataset, a.save)
    elif a.cmd == "research2":
        from .. import research2
        research2.run(a.dataset)
    elif a.cmd == "research":
        from .. import research
        research.run(a.dataset, [m.strip() for m in a.markets.split(",") if m.strip()], a.save)
    elif a.cmd == "kr-evening":
        root = config.dataset_dir("kr")
        try:
            indices.update_kr_evening()
            indices.update_ohlc(keys=["KOSPI", "KOSDAQ"])
        except Exception as e:  # noqa: BLE001 — 지수 실패해도 종목 갱신은 진행
            print(f"지수 저녁 갱신 실패: {e}")
        if yahoo_kr.update_today(root):
            build.build(root)
    elif a.cmd == "kr-yahoo-test":
        yahoo_kr.test(dt.date.fromisoformat(a.date) if a.date else dt.date.today(), a.n)
    elif a.cmd == "kr-web-test":
        krx_web.test(dt.date.fromisoformat(a.date) if a.date else dt.date.today())
    elif a.cmd == "index-update":
        indices.update()
    elif a.cmd == "check":
        p = config.dataset_dir(a.dataset) / "prices.parquet"
        df = pd.read_parquet(p, filters=[("code", "==", a.code)]).sort_values("date").tail(a.n)
        with pd.option_context("display.width", 200, "display.max_columns", 20):
            print(df.to_string(index=False))
        meta = config.dataset_dir(a.dataset) / "meta.json"
        if meta.exists():
            print(json.dumps(json.loads(meta.read_text()), ensure_ascii=False))


if __name__ == "__main__":
    main()
