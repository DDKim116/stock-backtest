import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
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
    return TestClient(app_mod.app)


TEXT = """[신호]
거래량 >= 거래량(1) * 5 AND 등락률 >= 10
[매수]
지정가 = (시가 + 종가) / 2
유효기간 = 5
[청산]
목표 = 3
보유기간 = 10
[대상]
시장 = 코스피, 코스닥
기간 = 2018-01-01 ~ 오늘
"""


def test_run_and_chart(client):
    r = client.post("/api/run", json={"dataset": "demo", "text": TEXT})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["kind"] == "single" and j["summary"]["n_signals"] > 0
    assert any("가상" in c["text"] for c in j["checks"])
    code = j["trades"][0]["code"]
    c = client.get("/api/chart", params={"dataset": "demo", "code": code})
    assert c.status_code == 200 and len(c.json()["bars"]) > 100


def test_sweep(client):
    r = client.post("/api/run", json={"dataset": "demo", "text": TEXT.replace("목표 = 3", "목표 = [3, 5]")})
    assert r.status_code == 200 and len(r.json()["variants"]) == 2


def test_errors(client):
    r = client.post("/api/run", json={"dataset": "demo", "text": "[신호]\n등락률 >= 12%"})
    assert r.status_code == 422 and "%" in r.json()["detail"]
    r = client.post("/api/check-expr", json={"expr": "종가 > 이평(종가)"})
    assert r.json()["ok"] is False
    r = client.post("/api/run", json={"dataset": "kr", "text": TEXT})
    assert r.status_code == 409


def test_strategies(client):
    r = client.post("/api/strategies", json={"name": "급등 눌림", "text": TEXT})
    sid = r.json()["id"]
    assert any(s["id"] == sid for s in client.get("/api/strategies").json())
    client.delete(f"/api/strategies/{sid}")
    assert not any(s["id"] == sid for s in client.get("/api/strategies").json())
