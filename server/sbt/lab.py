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
    # ---- 미장
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
