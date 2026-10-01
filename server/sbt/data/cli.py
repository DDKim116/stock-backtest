"""데이터 관리 명령.

  python -m sbt.data.cli demo                      # 가상 데이터 생성 + 빌드
  python -m sbt.data.cli kr-collect --start 2010-01-01   # KRX 원본 수집 (이어받기 지원)
  python -m sbt.data.cli kr-update                 # 최근 며칠 갱신 + 빌드 (매일 자동 실행용)
  python -m sbt.data.cli kr-build                  # 원본 → prices.parquet
  python -m sbt.data.cli us-collect                # 미국 전 종목 첫 수집 (야후)
  python -m sbt.data.cli us-update                 # 미국 최근 한 달 갱신 + 빌드 (매일 자동 실행용)
  python -m sbt.data.cli check --code 005930       # 특정 종목 최근 가격 출력 (증권사 차트와 대조용)
  python -m sbt.data.cli check --dataset us --code AAPL
"""
from __future__ import annotations

import argparse
import datetime as dt
import json

import pandas as pd

from .. import config
from . import build, krx, synthetic, us


def main(argv: list[str] | None = None) -> None:
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
        n = krx.collect(root, start, today, config.KRX_API_KEY, refetch_recent_days=a.days)
        print(f"새로 저장 {n}일")
        build.build(root)
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
    elif a.cmd == "us-build":
        us.build(config.dataset_dir("us"))
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
