import datetime as dt

from sbt import calendar, config


def test_events(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    def get(path, params):
        if path == "releases":
            return {"releases": [{"id": 10, "name": "Consumer Price Index"}, {"id": 101, "name": "FOMC Press Release"}]}
        return {"release_dates": [{"date": "2026-11-12" if params["release_id"] == 10 else "2026-12-09"}]}

    assert calendar.update_fred_dates(get, log=lambda *a: None) == 1  # FOMC 는 FRED 대신 공식 일정
    ev = calendar.events(dt.date(2026, 11, 1), dt.date(2026, 12, 31))
    names = {(e["date"], e["name"], e["time"]) for e in ev}
    assert ("2026-11-12", "미국 소비자물가(CPI)", "22:30") in names  # 동부 08:30 (겨울) = 한국 22:30
    assert ("2026-12-10", "FOMC 금리 결정", "04:00") in names  # 동부 14:00 = 한국 다음 날 04:00
    assert not any(n == "FOMC 금리 결정" and d < "2026-12-01" for d, n, _ in names)  # 11월엔 회의 없음
    assert ("2026-11-12", "국내 옵션 만기", "15:20") in names  # 11월 둘째 목요일
    assert ("2026-12-10", "국내 선물·옵션 동시 만기", "15:20") in names
    assert ("2026-12-19", "미국 쿼드러플 위칭", "06:00") in names  # 12/18 셋째 금요일 16:00 → 한국 토요일 06:00 (겨울)
    assert any(e["name"] == "QLD·비트코인 비중 점검" and e["date"] == "2026-12-15" for e in ev)
    assert any(e["name"] == "한국 수출입 동향" and e["date"] == "2026-12-01" for e in ev)
    assert ev == sorted(ev, key=lambda e: (e["date"], e["time"] or "99", -e["imp"]))
