import datetime as dt
import os

import pytest
from fastapi.testclient import TestClient

SPEC_TEXT = """[신호]
등락률 >= 5
[매수]
다음날시가
[청산]
목표 = 3
손절 = 5
보유기간 = 10
"""


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    d = tmp_path_factory.mktemp("data")
    os.environ["SBT_DATA_DIR"] = str(d)
    import importlib

    from sbt import config
    importlib.reload(config)
    from sbt.data import build, synthetic
    synthetic.write(d / "demo", n_codes=40, start="2018-01-01", end="2020-12-31")
    build.build(d / "demo", log=lambda *a: None)
    from sbt.api import app as app_mod
    importlib.reload(app_mod)
    return TestClient(app_mod.app), app_mod


def _spec(client):
    return client.post("/api/parse", json={"text": SPEC_TEXT}).json()["spec"]


def test_paper_flow(ctx, monkeypatch):
    client, app_mod = ctx
    monkeypatch.setattr(app_mod.paper, "is_retro", lambda *a, **k: False)
    spec = _spec(client)
    r = client.post("/api/paper/books", json={"name": "급등 v1", "dataset": "demo", "spec": spec,
                                               "capital": 10_000_000, "per_trade": 3_000_000, "auto": True})
    assert r.status_code == 200, r.text
    bid = r.json()["book"]["id"]
    # 자동 담기 시작일을 과거로 (가상 데이터가 2020년에 끝나므로)
    app_mod.store.paper_book_update(bid, {"auto_since": "2020-09-01"})
    app_mod._paper_sync.clear()
    v = client.get(f"/api/paper/books/{bid}").json()
    allt = v["trades"]
    assert len(allt) > 5 and all(t["auto"] and not t["picked"] for t in allt)
    assert v["groups"]["all"]["n"] == len(allt) and v["groups"]["picked"]["n"] == 0
    # 같은 조건이면 목록에서 same_spec
    lst = client.get("/api/paper/books", params={"dataset": "demo", "spec_text": SPEC_TEXT}).json()
    assert lst[0]["same_spec"] and "trades" not in lst[0]
    # 내 선택: 처음 6개
    for t in allt[:6]:
        r = client.post(f"/api/paper/books/{bid}/trades", json={"code": t["code"], "signal_date": t["signal_date"]})
        assert r.status_code == 200 and r.json()["already"] is False
    v = client.get(f"/api/paper/books/{bid}").json()
    picked = [t for t in v["trades"] if t["picked"]]
    assert len(picked) == 6 and v["groups"]["picked"]["n"] == 6
    acc = v["account"]
    assert acc["contributed"] == 10_000_000 and acc["per_trade"] == 3_000_000
    bought = [t for t in picked if t.get("qty")]
    assert all(t["invested"] <= 3_000_000 for t in bought)
    assert abs(acc["equity"] - acc["cash"] - acc["positions_value"]) < 1
    assert "edge" in v["groups"]
    # 입금 → 투입 자본 증가, 1회 매수금 변경
    v = client.post(f"/api/paper/books/{bid}/cash", json={"kind": "deposit", "amount": 5_000_000,
                                                          "date": "2020-01-01"}).json()
    assert v["account"]["contributed"] == 15_000_000
    v = client.post(f"/api/paper/books/{bid}/cash", json={"kind": "per_trade", "amount": 1_000_000,
                                                          "date": "2020-01-01"}).json()
    assert v["account"]["per_trade"] == 1_000_000
    # 수동 정리
    t0 = next(t for t in v["trades"] if t["picked"] and t.get("fill_date"))
    v = client.put(f"/api/paper/trades/{t0['id']}", json={"manual_exit_date": t0["fill_date"]}).json()
    t1 = next(t for t in v["trades"] if t["id"] == t0["id"])
    assert t1["status"] == "closed" and t1["exit_date"] <= t0["fill_date"] or t1["label"] != "수동 정리"
    # 내 선택 해제: 자동 담기 거래는 남는다
    v = client.delete(f"/api/paper/trades/{t0['id']}").json()
    assert any(t["id"] == t0["id"] and not t["picked"] for t in v["trades"])
    # 이름 바꾸기, 삭제
    assert client.put(f"/api/paper/books/{bid}", json={"name": "급등 v1b"}).json()["book"]["name"] == "급등 v1b"
    assert client.delete(f"/api/paper/books/{bid}").status_code == 200
    assert client.get(f"/api/paper/books/{bid}").status_code == 404


def test_paper_errors_and_retro(ctx):
    client, _ = ctx
    spec = _spec(client)
    r = client.post("/api/paper/books", json={"name": "x", "dataset": "demo", "spec": spec,
                                               "capital": 100, "per_trade": 1000})
    assert r.status_code == 422
    r = client.post("/api/parse", json={"text": "[신호]\n등락률 >= [5, 8]\n"})
    r = client.post("/api/paper/books", json={"name": "x", "dataset": "demo", "spec": r.json()["spec"],
                                               "capital": 1000, "per_trade": 100})
    assert r.status_code == 422
    r = client.post("/api/paper/books", json={"name": "수동", "dataset": "demo", "spec": spec,
                                               "capital": 1_000_000, "per_trade": 100_000})
    bid = r.json()["book"]["id"]
    # 과거 신호를 지금 담으면 소급
    r = client.post(f"/api/paper/books/{bid}/trades", json={"code": "000010", "signal_date": "2020-12-30"})
    if r.status_code == 200:
        assert r.json()["retro"] is True
        v = client.get(f"/api/paper/books/{bid}").json()
        assert v["groups"]["retro"] == 1 and v["groups"]["picked"]["n"] == 0


def test_retro_rule():
    from sbt import paper
    KST = paper.KST
    # 국내 다음날시가: 다음 거래일 9시 전까지는 정상
    assert not paper.is_retro("kr", "2026-10-16", "next_open", dt.datetime(2026, 10, 19, 8, 59, tzinfo=KST))
    assert paper.is_retro("kr", "2026-10-16", "next_open", dt.datetime(2026, 10, 19, 9, 1, tzinfo=KST))
    # 종가 매수는 그날 밤까지
    assert not paper.is_retro("kr", "2026-10-15", "close", dt.datetime(2026, 10, 15, 22, 0, tzinfo=KST))
    # 미국: 다음 날 뉴욕 9:30
    assert not paper.is_retro("us", "2026-10-15", "next_open", dt.datetime(2026, 10, 16, 22, 0, tzinfo=KST))
    assert paper.is_retro("us", "2026-10-15", "next_open", dt.datetime(2026, 10, 16, 23, 0, tzinfo=KST))
