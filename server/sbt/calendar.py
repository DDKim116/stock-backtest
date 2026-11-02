"""달력: 미국 경제 발표(FRED 발표 일정) · 국내외 옵션 만기 · 수출입 동향 · 우리 규칙 일정.

- 미국 발표: FRED releases/dates (fred-update 때 data/fred/calendar.json 에 저장). 시간은 미국 동부 기준을 한국 시간으로.
- 만기: 국내 옵션 매월 둘째 목요일(3·6·9·12월은 선물·옵션 동시 만기), 미국 옵션 셋째 금요일(분기 달은 쿼드러플 위칭).
- 수출입 동향: 매월 1일 오전 9시 (산업통상자원부).
- 우리 일정: 12월 중순 QLD·비트코인 비중 점검, 분기 보고서 마감 뒤 DART 받기, 월말 v2b 성적표.
"""
from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo

from . import config

ET, KST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Seoul")

# FRED 발표 이름 → (한글 이름, 미국 동부 시각, 중요도 1~3, 한 줄 설명)
RELEASES: list[tuple[str, str, str, int, str]] = [
    ("Consumer Price Index", "미국 소비자물가(CPI)", "08:30", 3, "물가가 예상보다 높으면 금리 인하가 늦어져 성장주에 부담"),
    ("Employment Situation", "미국 고용보고서", "08:30", 3, "실업률·일자리 수. 실업률 경고(삼의 법칙)도 이 숫자로 판단"),
    ("Personal Income and Outlays", "미국 PCE 물가", "08:30", 2, "연준이 가장 중요하게 보는 물가"),
    ("Producer Price Index", "미국 생산자물가(PPI)", "08:30", 2, "기업이 받는 가격. CPI 의 앞선 신호"),
    ("Gross Domestic Product", "미국 GDP", "08:30", 2, "경제 성장률 (분기)"),
    ("Advance Monthly Sales for Retail and Food Services", "미국 소매판매", "08:30", 2, "소비가 살아 있는지"),
    ("Job Openings and Labor Turnover Survey", "미국 구인(JOLTS)", "10:00", 1, "일자리 수요"),
]


# FOMC 는 FRED 발표 목록이 매일 갱신돼 쓸 수 없어 연준 공식 일정(회의 이틀째 = 발표일, 동부 14:00)을 직접 적는다.
# 새해 일정이 나오면 여기에 더한다 (federalreserve.gov → FOMC → Meeting calendars).
FOMC_DAYS = [
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]
FOMC_NOTE = "미국 기준금리 발표(이틀 회의 끝, 한국은 새벽). 주식·코인·환율이 가장 크게 움직이는 날"


def _path():
    return config.DATA_DIR / "fred" / "calendar.json"


def update_fred_dates(get, log=print) -> int:
    """get(path, params) -> json. FRED 발표 목록에서 이름으로 찾아 앞으로의 발표일을 저장. 저장한 일정 수."""
    rel = get("releases", {"limit": 1000}).get("releases", [])
    by_name = {r["name"]: r["id"] for r in rel}
    start = (dt.date.today() - dt.timedelta(days=45)).isoformat()
    out = []
    for name, ko, t, imp, note in RELEASES:
        rid = by_name.get(name) or next((i for n, i in by_name.items() if n.startswith(name)), None)
        if rid is None:
            log(f"  달력: '{name}' 발표를 찾지 못함")
            continue
        j = get("release/dates", {"release_id": rid, "realtime_start": start, "realtime_end": "9999-12-31",
                                  "include_release_dates_with_no_data": "true", "sort_order": "asc", "limit": 200})
        got = j.get("release_dates", [])
        if len(got) > 60:  # 매일 갱신되는 발표면 일정으로 못 씀 (예전 FOMC 처럼)
            log(f"  달력: '{name}' 은 발표일이 너무 잦아 뺌 ({len(got)}개)")
            continue
        for d in got:
            out.append({"date": d["date"], "name": ko, "et": t, "imp": imp, "note": note, "kind": "us"})
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(out, ensure_ascii=False))
    log(f"  달력: 미국 발표 일정 {len(out)}개")
    return len(out)


def _kst(day: dt.date, et: str) -> tuple[dt.date, str]:
    h, m = map(int, et.split(":"))
    k = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=ET).astimezone(KST)
    return k.date(), k.strftime("%H:%M")


def _nth_weekday(y: int, m: int, weekday: int, n: int) -> dt.date:
    d = dt.date(y, m, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(y: int, m: int) -> dt.date:
    d = (dt.date(y + (m == 12), m % 12 + 1, 1)) - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def events(start: dt.date, end: dt.date) -> list[dict]:
    out: list[dict] = []
    # 미국 발표 (FRED)
    for d in FOMC_DAYS:
        kd, kt = _kst(dt.date.fromisoformat(d), "14:00")
        if start <= kd <= end:
            out.append({"date": kd.isoformat(), "time": kt, "name": "FOMC 금리 결정", "imp": 3, "note": FOMC_NOTE, "kind": "us"})
    if _path().exists():
        for e in json.loads(_path().read_text()):
            if e["name"] == "FOMC 금리 결정":  # 예전에 FRED 에서 받은 것 (매일로 잘못 들어감)
                continue
            day = dt.date.fromisoformat(e["date"])
            kd, kt = _kst(day, e["et"])
            if start <= kd <= end:
                out.append({"date": kd.isoformat(), "time": kt, "name": e["name"], "imp": e["imp"], "note": e["note"],
                            "kind": "us"})
    y, m = start.year, start.month
    while dt.date(y, m, 1) <= end:
        q = m in (3, 6, 9, 12)
        kr_exp = _nth_weekday(y, m, 3, 2)  # 둘째 목요일
        out.append({"date": kr_exp.isoformat(), "time": "15:20", "imp": 2 if q else 1, "kind": "kr",
                    "name": "국내 선물·옵션 동시 만기" if q else "국내 옵션 만기",
                    "note": "장 막판 대형주 변동이 커질 수 있음" + (" (분기 동시 만기)" if q else "")})
        us_exp = _nth_weekday(y, m, 4, 3)  # 셋째 금요일 (한국은 다음 날 새벽)
        ud, ut = _kst(us_exp, "16:00")  # 미국 장 마감 = 한국 다음 날 새벽 (서머타임 05:00, 겨울 06:00)
        out.append({"date": ud.isoformat(), "time": ut, "imp": 2 if q else 1, "kind": "us",
                    "name": "미국 쿼드러플 위칭" if q else "미국 옵션 만기",
                    "note": "미국 장 마감 무렵 거래량·변동 커짐" + (" (분기 만기)" if q else "")})
        out.append({"date": dt.date(y, m, 1).isoformat(), "time": "09:00", "imp": 2, "kind": "kr", "name": "한국 수출입 동향",
                    "note": "반도체 수출이 코스피 이익의 큰 몫 — 지난달 숫자"})
        sat = _nth_weekday(y, m, 5, 1)
        out.append({"date": sat.isoformat(), "time": "", "imp": 1, "kind": "me", "name": "서버 업데이트",
                    "note": "Termius: sudo apt update && sudo apt upgrade -y (재시작 필요하면 sudo reboot)"})
        lw = _last_weekday(y, m)
        out.append({"date": lw.isoformat(), "time": "", "imp": 1, "kind": "me", "name": "v2b 성적표 보기",
                    "note": "연구 탭 → 키움 모의투자 성적표 → 다시 돌리기"})
        if m == 12:
            out.append({"date": dt.date(y, 12, 15).isoformat(), "time": "", "imp": 3, "kind": "me", "name": "QLD·비트코인 비중 점검",
                        "note": "QLD 25%·비트코인 10% 넘으면 넘는 만큼 팔아 VOO 로 (연말 결제 마감 전에)"})
        dart_day = {4: 2, 5: 16, 8: 16, 11: 16}.get(m)
        if dart_day:
            out.append({"date": dt.date(y, m, dart_day).isoformat(), "time": "", "imp": 1, "kind": "me", "name": "DART 자료 받기",
                        "note": "분기·사업보고서 마감 뒤 — 연구 탭 'DART 재무 자료 받기' (실적 가속 신호 갱신)"})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    out = [e for e in out if start.isoformat() <= e["date"] <= end.isoformat()]
    out.sort(key=lambda e: (e["date"], e["time"] or "99", -e["imp"]))
    return out
