"""연구 탭: 지금까지 돌린 시뮬레이션 목록·결론·결과 파일, 앱에서 다시 돌리기.

결과 파일은 data/research/<prefix>-YYYYMMDD-HHMM.txt (각 research*.py 가 쓴다).
돌리기는 서버 명령(python -m sbt.data.cli <cmd>)을 따로 띄워서 한다. 한 번에 하나만.
"""
from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
import threading
from pathlib import Path

from . import config

SERVER_DIR = Path(__file__).resolve().parents[1]
NAME_RE = re.compile(r"^([a-z0-9-]+?)-(\d{8}-\d{4})\.txt$")

# 새 연구를 만들면 여기에 한 줄 추가. 결론은 결과를 같이 보고 정한 것.
CATALOG: list[dict] = [
    # ---- 코인
    {"cmd": "btc-dip", "prefix": "btc-dip", "area": "코인", "title": "비트코인 더 사기 · 주식과의 관계",
     "question": "비트코인이 쌀 때(평단 아래·고점 -50%) 더 사면 나을까? 미국 주식과 같이 움직일까?",
     "conclusion": "평단 아래 6천·−30% 아래 1.2만(B)이 거의 모든 시작 해에서 기본보다 연 +0.6~6%p, 같은 돈을 VOO 에 넣는 것보다도 대부분 나음. "
                   "필요한 여윳돈은 한 해 최대 약 110만. 고점 −50% 규칙(C)은 돈이 훨씬 많이 들고 최근엔 거의 안 울림. "
                   "2020년부터 미국 주식과 같이 움직임(상관 0.3~0.6, S&P −2% 날 비트코인 평균 −2.8%) → 피난처 아님, 10% 상한 유지.", "minutes": 1},
    {"cmd": "crypto-test", "prefix": "crypto", "area": "코인", "title": "메이저 코인 매일 적립",
     "question": "비트코인을 매일 1만원씩 샀다면? 이더리움·리플·솔라나와 VOO 와 비교하면? 섞으면?",
     "conclusion": "비트코인은 5년 단위로는 늘 VOO 를 이겼지만 앞선 이득이 해마다 줄어듦 (2015 시작 연 62% → 2021~23 시작 연 21~25%, VOO 17~18%) "
                   "· 평가액 낙폭 −45~−82%. 2021~ 이더리움 +3%로 VOO(+64%)보다 못함, 솔라나는 원금 대비 최악 −56~−73%. "
                   "VOO 2만에 비트코인 2천(약 9%)은 낙폭을 거의 안 늘리고 수익만 조금 더함 → 비트코인만, 소액(전체의 10% 이내).", "minutes": 1},
    # ---- 미장
    {"cmd": "us-v2b", "prefix": "us-v2b", "area": "미장", "title": "미국판 v2b",
     "question": "국장 v2b(5일 급락 + 과매도 + 지수도 빠질 때)를 미국 전 종목에 쓰면? 국장 v2b 가 쉬는 달을 채워 줄까?",
     "conclusion": "미국에선 안 통함: 과거(~2020) 1건당 −1.6%, 2021~ +1.3%로 들쭉날쭉, 2020 코로나 때 3,901건 −3.6%. 계좌로 연 +1.8%·낙폭 −45% "
                   "(큰 회사만 연 +3.6%). 상장폐지 종목이 빠져 이것도 부풀려진 숫자 → 채택 안 함. 국장 v2b 와 상관은 −0.31로 낮았음.", "minutes": 5},
    {"cmd": "macro-test", "prefix": "macro", "area": "미장", "title": "경제 지표로 위험한 때 피하기",
     "question": "금리차 역전·신용 스프레드·실업률 상승·금리 인상기에 주식(VOO·QQQ·QLD)과 비트코인을 쉬었다면?",
     "conclusion": "대부분 그냥 보유보다 못함 (신용 스프레드·금리 인상기는 손해). 쓸 만한 건 '실업률 상승'(삼의 법칙) 하나: S&P 연 10.4→9.7%로 "
                   "조금 덜 벌고 낙폭 −55→−34%, 36년에 12번만 사고팜. 금리차 역전은 1~2년 앞서 울려 시점 맞추기 어려움. QLD·비트코인은 "
                   "어떤 신호로도 낙폭 −64~−99% 그대로 → 적립은 계속, 경제 지표는 계기판으로 지켜보기만.", "minutes": 1},
    {"cmd": "fred-update", "prefix": "fred", "area": "미장", "title": "FRED 경제 지표 받기",
     "question": "미국 금리·금리차·신용 스프레드·실업률·물가·달러·원달러를 받는다 (설정에 FRED 키 필요)",
     "conclusion": "처음 한 번 받으면 이후엔 매일 미국 갱신 때 자동.", "minutes": 1},
    {"cmd": "qld-dip", "prefix": "qld-dip", "area": "미장", "title": "QLD 폭락 때 더 사기",
     "question": "QLD가 내 평단보다 떨어지면 1만·2만원으로 늘려 사면 회복 때 더 벌까? 그 돈을 VOO에 넣는 것보다 나을까?",
     "conclusion": "효과는 작지만 꾸준함: 대부분 시작 해에서 연 +0.1~1.0%p (같은 돈 VOO보다도 +0.3~1.0%p). 예외는 닷컴처럼 "
                   "3년 내리 빠진 1999·2000 시작(VOO가 나음). 필요한 여윳돈은 보통 한 해 100~150만, 최악 370만 → 채택: "
                   "QLD 수익률 0% 아래면 1만, −30% 아래면 2만.", "minutes": 1},
    {"cmd": "qld-rule", "prefix": "qld-rule", "area": "미장", "title": "QLD 5천원 + 비중 25% 상한",
     "question": "QLD를 조금 섞을 때 비중이 커지지 않게 막는 규칙은 무엇이 좋을까?",
     "conclusion": "새 돈만 VOO로 돌리기는 효과가 거의 없음. 1년에 한 번 25% 넘는 만큼 팔아 VOO로 옮기기가 거품 붕괴 때 "
                   "손실을 이익으로 바꿈 → QLD 매일 5천원 + 12월 중순 리밸런싱.", "minutes": 1},
    {"cmd": "qld-test", "prefix": "qld", "area": "미장", "title": "QLD(나스닥100 2배) 매일 1만원",
     "question": "QLD를 매일 사면 QQQ보다 나을까? 최악은 어땠나?",
     "conclusion": "10년 단위 27번 중 10번(1993~2002 시작)은 QQQ에 졌고 5번은 원금 손실(1999 시작 −64%). "
                   "상승장 10년에는 QQQ의 1.5~2.8배. 혼자 크게 하기엔 위험.", "minutes": 1},
    {"cmd": "us-test", "prefix": "us", "area": "미장", "title": "VOO 적립 · QQQ · 무한매수법",
     "question": "VOO 매일 1.5만원은 10년 뒤 어떻게 됐나? QQQ·TQQQ·무한매수법은?",
     "conclusion": "VOO 매일 적립은 2010~2016 어느 해에 시작해도 10년 뒤 +91~171%. 월적립에서 QQQ가 S&P500을 매번 이김. "
                   "TQQQ·무한매수법은 낙폭이 −54~−100%로 크고 대부분 QQQ 보유보다 못함.", "minutes": 1},
    # ---- 국장
    {"cmd": "third-touch", "prefix": "third-touch", "area": "국장", "title": "전고점 세 번째 돌파",
     "question": "1년 고점을 두 번 찍고 내려왔다가 세 번째에 뚫으면 얼마나 갈까? 목표·손절은? 시가총액대별로는?",
     "conclusion": "결과를 보고 정해요.", "minutes": 3},
    {"cmd": "ecos-update", "prefix": "ecos", "area": "국장", "title": "한국은행 ECOS 받기 (아파트·금리)",
     "question": "서울 아파트 매매가격지수(1986~)·한국 기준금리·코스피(월)를 받는다 (설정에 ECOS 키 필요)",
     "conclusion": "처음 한 번 받으면 이후엔 매일 국내 갱신 때 자동. 고른 아파트 표 이름이 결과에 나와요.", "minutes": 1},
    {"cmd": "apt-test", "prefix": "apt", "area": "국장", "title": "서울 아파트값 · 코스피 · 기준금리",
     "question": "금리를 올리거나 내리기 시작하면 서울 아파트값은 어떻게 됐나? 코스피와 누가 먼저 움직였나?",
     "conclusion": "2003~2026 (한국부동산원 서울 아파트): 연 +3.6% (코스피 +9.9%, 배당 제외). 금리 방향은 약한 신호 — 인하 시작 뒤 2년 "
                   "평균 +5%, 인상 시작 뒤도 +7% (오를 때 올리기 시작해서). 크게 빠진 건 2021.8 급한 인상 뒤 2년 −8% 한 번. "
                   "코스피가 약 9달 먼저 움직이는 편(상관 0.51). 대출 규제·공급 같은 정책 영향이 더 클 수 있음.", "minutes": 1},
    {"cmd": "v2b-target", "prefix": "v2b-target", "area": "국장", "title": "v2b 목표가 날마다 낮추기",
     "question": "5일 동안 목표 10%에 닿는 비율은? 3일째 5%·4일째 3%처럼 목표를 낮추면 손실이 줄고 수익은 늘까?",
     "conclusion": "지금 규칙: 10건 중 목표 5.1 · 손절 3.6 · 만료 1.3, 60%가 산 날·다음 날에 끝남. 3일째 이후까지 가는 거래가 적어 "
                   "목표를 낮춰도 거의 차이 없음 (3일째 5%·4일째 3%: 계좌 연 17.5% vs 지금 17.6%, 2021~ 11.1 vs 11.6%). "
                   "처음부터 5%는 연 10.2%로 크게 나빠짐 → 목표 10% 그대로.", "minutes": 3},
    {"cmd": "dart-events", "prefix": "dart-events", "area": "국장", "title": "DART 공시 사건 받기",
     "question": "자사주 매입 결정 · 임원·주요주주 지분 변동 · 잠정 실적 공시를 2015년부터 받는다 (설정에 DART 키 필요)",
     "conclusion": "처음엔 30~60분. 하루 한도에 걸리거나 멈추면 다시 누르면 이어서 받아요.", "minutes": 45},
    {"cmd": "disclosure-test", "prefix": "disclosure", "area": "국장", "title": "자사주 매입 · 임원 매수 따라가기",
     "question": "회사가 자사주를 사겠다고 하거나 임원·대주주가 자기 회사 주식을 산 공시 뒤에 사면 주가가 더 갈까?",
     "conclusion": "약함: 자사주 매입 뒤 초과 +1% 안팎, 오른 비율은 50% 아래(몇 종목이 평균을 끌어올림). 501위~ 계좌는 연 12~15%로 기준(9%)보다 "
                   "낫지만 낙폭 −38~−40%·한 번에 23~90종목 → v2b 대신 쓸 정도는 아님. 임원 매수는 DART 가 최근 약 2년치만 줘서 판단 보류.", "minutes": 2},
    {"cmd": "accel-fast", "prefix": "accel-fast", "area": "국장", "title": "실적 가속을 잠정 실적 날에",
     "question": "실적 가속 신호를 정기보고서 대신 잠정 실적 공시 날에 사면 더 나을까?",
     "conclusion": "오히려 손해: 잠정 날에 사면 계좌 연 11.3% → 9.2%, 낙폭 −32 → −37%. 잠정 공시가 있는 신호도 18%뿐 → 지금처럼 "
                   "정기보고서 다음 날에 사는 게 맞음 (발표 뒤에도 천천히 오르는 신호).", "minutes": 2},
    {"cmd": "accel-plan", "prefix": "accel", "area": "국장", "title": "실적 가속 중형주 계좌 다듬기",
     "question": "보유 기간·종목 수·정리 규칙 중 무엇이 낙폭을 줄일까? v2b 와 한 계좌로 굴리는 게 나을까, 따로가 나을까?",
     "conclusion": "최대 30종목 · 6개월이 무난: 연 11.3%, 낙폭 −32% (기본 20종목 −43%). 50일선 정리는 거의 바로 팔려 망가짐. 같은 기간 "
                   "코스피 지수(연 약 10%)와 비슷하지만 갇힌 장(2021.7~2024)에 +26%로 강함 → 지수 대신·옆에 둘 후보. v2b 와 한 계좌면 "
                   "v2b 거래가 1,424→612건으로 줄고 낙폭 −40% → 따로 운영 확정. v2b 혼자(연 14.5%)가 여전히 가장 좋음.", "minutes": 2},
    {"cmd": "profit-test", "prefix": "profit", "area": "국장", "title": "영업이익 증익·가속 공시 뒤",
     "question": "영업이익이 크게·빨라지게 늘었다는 공시 뒤에 사도 주가가 계속 더 갈까? 상위 100과 101~300위는 다를까?",
     "conclusion": "매출과 달리 영업이익은 통함, 특히 중형주(101~300위): '2분기 연속 +20% 이상 가속' 공시 뒤 1년 초과 +8.2%, "
                   "6개월 보유 계좌 연 11.8% (그룹 평균 3.4%, 코스피 지수와 비슷), 2021.7~2024 갇힌 장에서도 +31%. 감익은 반대로 "
                   "계속 약함. 상위 100은 효과 작음. 다만 낙폭 −53%, 2025~ 초대형주 랠리는 지수보다 덜 먹음 → 다듬기 시험 필요.", "minutes": 2},
    {"cmd": "growth-test", "prefix": "growth", "area": "국장", "title": "매출 성장 회사: 언제 사고 팔까",
     "question": "매출이 꾸준히 느는 코스피 상위 100 회사는 주가도 오르나? 언제 사서 언제 파는 게 좋을까?",
     "conclusion": "매출과 주가는 같은 해에 약하게만 같이 움직이고(상관 +0.16), 실적을 알고 나서 사면 효과 없음(+0.04). 꾸준 성장·고성장 "
                   "그룹은 상위 100 평균과 같거나 못함. '매출 대비 쌀 때' 사기는 오히려 손해(연 3%대). 꾸준 성장 + 200일선 위만 평균보다 "
                   "조금 나음(연 10.1% vs 8.5%). 같은 기간 코스피 지수(2.6배)가 다 이김 → 매출로 타이밍 잡기는 안 통함.", "minutes": 2},
    {"cmd": "dart-collect", "prefix": "dart", "area": "국장", "title": "DART 재무 자료 받기",
     "question": "2015년부터 코스피·코스닥 회사의 매출·영업이익·순이익을 받는다 (설정에 DART 키 필요)",
     "conclusion": "처음 한 번 받고, 분기 실적 발표 뒤마다 다시 누르면 새 공시가 들어와요.", "minutes": 5},
    {"cmd": "bull-plan", "prefix": "bull-plan", "area": "국장", "title": "대상승장 계좌: 낙폭 줄이기",
     "question": "코스피 200일선 필터, 주도주 종목별 정리(−15%·50일선), 지수와 섞기 중 무엇이 큰 상승장을 먹으면서 낙폭을 줄일까?",
     "conclusion": "코스피 200일선 필터는 오히려 손해(박스피에서 들락날락). 종목별 정리는 효과: 주도주 10 + −15% 정리 5.7배·낙폭 −47%, "
                   "주도주 5 + 50일선 정리 7.1배·연 13.3%·낙폭 −39% (지수 2.8배·−44%). 다만 2025~ 초대형주 랠리는 지수 보유(+139%)를 "
                   "못 따라감(+50~69%). 주도주 5 결과가 운인지 숫자 바꿔 한 번 더 확인 필요.", "minutes": 1},
    {"cmd": "leader-test", "prefix": "leader", "area": "국장", "title": "v2b 대형주 빼기 · 주도주 계좌",
     "question": "v2b에서 시총 상위 종목을 빼면 나을까? 대형주 신고가를 더하면? 상승장을 길게 먹는 주도주 계좌는?",
     "conclusion": "상위 100 빼기는 차이 거의 없음(연 17.6→17.8%) → v2b 그대로. 대형주 신고가를 더하면 연 +1.4%p지만 낙폭 2배 "
                   "(−8→−15%)·큰 수익 몇 건 덕분 → 안 넣음. 주도주 계좌는 지수보다 연 +3~5%p(2011~2016 박스피에도 플러스)지만 "
                   "최대 낙폭 −50~55%, 2025~ 대형 랠리는 지수보다 덜 먹음. v2b 계좌는 평균 86%가 현금으로 놂.", "minutes": 1},
    {"cmd": "box-test", "prefix": "box", "area": "국장", "title": "박스피 점검: 상위 100 vs 나머지",
     "question": "최근 상승은 대형주만 오른 건가? 다시 박스피에 갇히면 어떤 전략이 통할까?",
     "conclusion": "맞음: 2025~ 상위 10 +242% vs 101위 아래(동일 가중) +15%, 코스피 종목 73%가 아직 2021-06보다 쌈(301위 아래 중간값 −50%). "
                   "v2b는 박스장·추세장 모두 통하고 힘은 101위 아래(+2.6%)에서 나옴, 상위 100은 약함. 대형주 52주 신고가는 추세장에서만 "
                   "+3.1%. 볼린저·종목 박스 하단·박스권 돌파는 안 통함. 지수 박스 매매는 박스 땐 좋지만 추세장에서 크게 놓침.", "minutes": 1},
    {"cmd": "auto-report", "prefix": "auto-report", "area": "국장", "title": "키움 모의투자 성적표",
     "question": "모의투자 실제 성적이 백테스트 기대치 범위 안에 있나?",
     "conclusion": "2주쯤마다 돌려 보기. 정상 범위 안이면 규칙대로 계속.", "minutes": 1},
    {"cmd": "size-test", "prefix": "size", "area": "국장", "title": "v2b 1회 금액",
     "question": "1회 금액을 고정할까, 계좌 비율로 할까?",
     "conclusion": "계좌 10% × 하루 10종목, 1회 최대 2천만이 가장 좋음 → 지금 설정. 현금 여유도 남음.", "minutes": 3},
    {"cmd": "bunt-test", "prefix": "bunt", "area": "국장", "title": "중간값 번트 (+2.5%)",
     "question": "중간값 전략을 짧은 목표·저가 손절로 바꾸면 살아날까?",
     "conclusion": "목표 도달 72%지만 본전에 83%가 필요. % 손절은 더 나빠짐 → 중간값은 끔.", "minutes": 5},
    {"cmd": "mid-test", "prefix": "mid", "area": "국장", "title": "중간값 전략 살리기",
     "question": "목표·손절·시장 조건·지정가를 바꾸면 중간값 전략이 살아날까?",
     "conclusion": "50가지 넘게 바꿔도 손해 → 끔.", "minutes": 5},
    {"cmd": "trend-check", "prefix": "trend-check", "area": "국장", "title": "신고가 추세 점검",
     "question": "신고가 추세 전략의 수익은 어디서 나오나?",
     "conclusion": "몇몇 큰 종목 덕분이고 조건부로만 통함 → 추가 안 함.", "minutes": 5},
    {"cmd": "trend-test", "prefix": "trend", "area": "국장", "title": "신고가 추세 + v2b",
     "question": "신고가 추세 전략을 v2b와 같은 계좌로 굴리면?",
     "conclusion": "수익은 조금 늘지만 최대 낙폭이 두 배 → 추가 안 함.", "minutes": 8},
    {"cmd": "bull-test", "prefix": "bull", "area": "국장", "title": "상승장 전략 후보",
     "question": "강세장 해에 통하는 전략이 있나?",
     "conclusion": "대부분 과거·검증 둘 다 버티지 못함 → 추가 안 함.", "minutes": 8},
    {"cmd": "bear-test", "prefix": "bear", "area": "국장", "title": "하락장 전략 후보",
     "question": "약세장 해에 통하는 전략이 있나?",
     "conclusion": "대부분 과거·검증 둘 다 버티지 못함 → 추가 안 함.", "minutes": 8},
    {"cmd": "breadth-test", "prefix": "breadth", "area": "국장", "title": "v2b 시장 조건: 지수 vs 상승비율",
     "question": "v2b의 '지수 5일 −3%' 대신 상승비율·동일가중을 쓰면 나을까?",
     "conclusion": "뚜렷하게 낫지 않음 → 지금 지수 조건 유지.", "minutes": 5},
    {"cmd": "rank", "prefix": "rank", "area": "국장", "title": "신호 많은 날 고르는 순서",
     "question": "하루 신호가 많을 때 어떤 종목부터 살까?",
     "conclusion": "거래대금 작은 순.", "minutes": 5},
    {"cmd": "research2", "prefix": "research2", "area": "국장", "title": "2차: 급락 반등 정밀 점검",
     "question": "급락 반등 전략을 쪼개 보면 언제 통하나?",
     "conclusion": "시장 상황에 따라 크게 갈림 → 지수 조건을 붙인 v2b로.", "minutes": 15},
    {"cmd": "research", "prefix": "research", "area": "국장", "title": "1차: 잘 알려진 전략 12가지",
     "question": "잘 알려진 전략 중 국내에서 실제로 통하는 건?",
     "conclusion": "과거·검증 구간 모두 버틴 건 단기 급락 반등뿐.", "minutes": 20},
]
_BY_CMD = {c["cmd"]: c for c in CATALOG}
_BY_PREFIX = {c["prefix"]: c["cmd"] for c in CATALOG}

_lock = threading.Lock()
_run: dict | None = None  # {"cmd", "started", "proc", "log", "code"}


def out_dir() -> Path:
    return config.DATA_DIR / "research"


def reports() -> list[dict]:
    d = out_dir()
    if not d.exists():
        return []
    out = []
    for p in d.glob("*.txt"):
        m = NAME_RE.match(p.name)
        if not m:
            continue
        st = dt.datetime.strptime(m.group(2), "%Y%m%d-%H%M")
        out.append({"name": p.name, "cmd": _BY_PREFIX.get(m.group(1)), "at": st.isoformat(timespec="minutes"),
                    "size": p.stat().st_size})
    out.sort(key=lambda r: r["at"], reverse=True)
    return out


def read(name: str) -> str | None:
    if not NAME_RE.match(name):
        return None
    p = out_dir() / name
    return p.read_text(errors="replace") if p.is_file() else None


def status() -> dict | None:
    if _run is None:
        return None
    proc = _run["proc"]
    code = proc.poll()
    try:
        tail = Path(_run["log"]).read_text(errors="replace").splitlines()[-6:]
    except OSError:
        tail = []
    return {"cmd": _run["cmd"], "started": _run["started"], "running": code is None, "code": code, "tail": tail}


def start(cmd: str) -> dict:
    """서버 명령을 따로 띄운다. 이미 돌고 있으면 ValueError."""
    global _run
    if cmd not in _BY_CMD:
        raise ValueError("모르는 연구입니다")
    with _lock:
        if _run is not None and _run["proc"].poll() is None:
            raise ValueError(f"'{_BY_CMD[_run['cmd']]['title']}'이(가) 아직 돌고 있습니다")
        out_dir().mkdir(parents=True, exist_ok=True)
        log = out_dir() / ".running.log"
        fh = open(log, "w")
        proc = subprocess.Popen([sys.executable, "-m", "sbt.data.cli", cmd], cwd=SERVER_DIR, stdout=fh,
                                stderr=subprocess.STDOUT, start_new_session=True)
        fh.close()
        _run = {"cmd": cmd, "started": dt.datetime.now().isoformat(timespec="seconds"), "proc": proc, "log": str(log)}
    return status() or {}


def view() -> dict:
    rs = reports()
    latest: dict[str, dict] = {}
    for r in rs:
        if r["cmd"] and r["cmd"] not in latest:
            latest[r["cmd"]] = r
    items = [{k: v for k, v in c.items() if k != "prefix"} | {"latest": latest.get(c["cmd"]),
             "count": sum(1 for r in rs if r["cmd"] == c["cmd"])} for c in CATALOG]
    return {"items": items, "reports": rs, "running": status()}
