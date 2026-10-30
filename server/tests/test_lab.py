import time

from sbt import config, lab


def test_reports_and_view(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    d = tmp_path / "research"
    d.mkdir()
    (d / "qld-rule-20261030-1200.txt").write_text("규칙")
    (d / "qld-20261030-1100.txt").write_text("qld")
    (d / "trend-check-20261020-0900.txt").write_text("t")
    (d / "research-20261017-0800.json").write_text("{}")
    rs = lab.reports()
    assert [r["cmd"] for r in rs] == ["qld-rule", "qld-test", "trend-check"]
    v = lab.view()
    it = {x["cmd"]: x for x in v["items"]}
    assert it["qld-rule"]["latest"]["name"] == "qld-rule-20261030-1200.txt"
    assert it["qld-test"]["count"] == 1 and it["trend-test"]["latest"] is None
    assert lab.read("qld-20261030-1100.txt") == "qld"
    assert lab.read("../app.sqlite3") is None and lab.read("x.txt") is None


def test_start_rejects_unknown_and_runs_one(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    try:
        lab.start("rm -rf")
        assert False
    except ValueError:
        pass
    monkeypatch.setattr(lab.sys, "executable", "sleep")
    monkeypatch.setattr(lab, "_BY_CMD", {"1": {"title": "잠깐"}})
    s = lab.start("1")  # 'sleep -m ...' 는 바로 실패하지만 Popen 자체는 뜬다
    assert s["cmd"] == "1"
    for _ in range(50):
        if not lab.status()["running"]:
            break
        time.sleep(0.05)
    assert lab.status()["running"] is False
