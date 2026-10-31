import pytest

from sbt.data import cli


@pytest.mark.parametrize("cmd", ["accel-plan", "profit-test", "growth-test", "dart-collect", "bull-plan", "leader-test",
                                 "box-test", "qld-dip", "kr-update", "kr-evening", "us-update", "auto-report"])
def test_every_command_parses(cmd, monkeypatch):
    """명령 이름만으로 인자 해석이 되는지 (실제 실행은 하지 않음)."""
    seen = {}

    def fake_parse(self, argv=None, namespace=None):
        seen["ns"] = orig(self, argv, namespace)
        raise SystemExit(0)

    import argparse

    orig = argparse.ArgumentParser.parse_args
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", fake_parse)
    with pytest.raises(SystemExit):
        cli.main([cmd])
    assert seen["ns"].cmd == cmd
