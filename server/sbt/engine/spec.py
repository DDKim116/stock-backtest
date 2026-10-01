"""검증 조건(전략) 명세.

JSON(웹 화면/AI) 과 텍스트 블록(직접 입력) 두 가지 형태를 오간다.

텍스트 예)
    [신호]
    거래량 >= 거래량(1) * 5
    AND 등락률 >= 12

    [매수]
    지정가 = (시가 + 종가) / 2
    유효기간 = 5

    [청산]
    목표 = 3
    기준 = 고가
    손절 = 없음
    보유기간 = 10

    [대상]
    시장 = 코스피, 코스닥
    시작 = 2010-01-01
    제외 = 스팩, 우선주

숫자 자리에 [10, 12, 15] 처럼 쓰면 각 값으로 모두 돌려서 비교한다.
"""
from __future__ import annotations

import datetime as dt
import itertools
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from . import dsl

MARKET_ALIASES = {
    "코스피": "KOSPI", "kospi": "KOSPI", "유가증권": "KOSPI",
    "코스닥": "KOSDAQ", "kosdaq": "KOSDAQ",
    "코넥스": "KONEX", "konex": "KONEX",
    "나스닥": "NASDAQ", "nasdaq": "NASDAQ",
    "뉴욕": "NYSE", "nyse": "NYSE",
    "아멕스": "AMEX", "amex": "AMEX",
    "데모": "DEMO", "demo": "DEMO",
}
MARKET_LABEL = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "KONEX": "코넥스", "NASDAQ": "나스닥",
                "NYSE": "뉴욕", "AMEX": "아멕스", "DEMO": "데모"}

Num = float | list[float]
Int = int | list[int]


class Universe(BaseModel):
    markets: list[str] = ["KOSPI", "KOSDAQ"]
    start: dt.date = dt.date(2010, 1, 1)
    end: dt.date | None = None
    exclude_spac: bool = True
    exclude_preferred: bool = True

    @field_validator("markets")
    @classmethod
    def _markets(cls, v: list[str]) -> list[str]:
        out = []
        for m in v:
            key = m.strip()
            out.append(MARKET_ALIASES.get(key.lower(), MARKET_ALIASES.get(key, key.upper())))
        if not out:
            raise ValueError("시장을 하나 이상 선택하세요")
        return out


class Entry(BaseModel):
    # limit: 신호일 기준 가격식으로 다음 날부터 지정가 주문 / next_open: 다음 날 시가 / close: 신호일 종가
    type: Literal["limit", "next_open", "close"] = "limit"
    price: str = "(시가 + 종가) / 2"
    valid_days: Int = 5


class Exit(BaseModel):
    target_pct: Num | None = 3.0
    target_basis: Literal["high", "close"] = "high"
    stop_pct: Num | None = None
    max_days: Int = 10


class Costs(BaseModel):
    buy_fee_pct: float = 0.015
    sell_fee_pct: float = 0.015
    sell_tax_pct: float = 0.20
    slippage_pct: float = 0.0


class Spec(BaseModel):
    name: str = ""
    signal: str
    entry: Entry = Field(default_factory=Entry)
    exit: Exit = Field(default_factory=Exit)
    universe: Universe = Field(default_factory=Universe)
    costs: Costs = Field(default_factory=Costs)
    # 같은 종목에서 앞 신호 후 n 거래일 이내에 다시 나온 신호는 제외 (0 이면 모두 포함)
    dedupe_days: int = 0
    # 미국 주식: 매수·매도 시점 환율을 반영한 원화 기준 수익률
    fx_krw: bool = False


# ---------------------------------------------------------------- 스윕(여러 값 비교)

_SWEEP_RE = re.compile(r"\[\s*(-?\d+(?:\.\d+)?(?:\s*,\s*-?\d+(?:\.\d+)?)+)\s*\]")
MAX_COMBOS = 60


def expand(spec: Spec) -> list[tuple[dict[str, float], Spec]]:
    """[a, b, c] 로 표시된 값들의 모든 조합을 개별 Spec 으로 펼친다."""
    axes: list[tuple[str, list[float]]] = []

    def scan_text(label: str, text: str):
        for i, m in enumerate(_SWEEP_RE.finditer(text)):
            vals = [float(x) for x in m.group(1).split(",")]
            axes.append((f"{label}#{i + 1}", vals))

    scan_text("신호", spec.signal)
    if spec.entry.type == "limit":
        scan_text("매수가", spec.entry.price)
    numeric = {
        "유효기간": spec.entry.valid_days,
        "목표": spec.exit.target_pct,
        "손절": spec.exit.stop_pct,
        "보유기간": spec.exit.max_days,
    }
    for k, v in numeric.items():
        if isinstance(v, list):
            axes.append((k, [float(x) for x in v]))

    if not axes:
        return [({}, spec)]
    combos = list(itertools.product(*[a[1] for a in axes]))
    if len(combos) > MAX_COMBOS:
        raise ValueError(f"비교 조합이 {len(combos)}개입니다. {MAX_COMBOS}개 이하로 줄여 주세요")

    out = []
    for combo in combos:
        params = {axes[i][0]: combo[i] for i in range(len(axes))}
        d = spec.model_dump()

        def sub_text(label: str, text: str) -> str:
            idx = iter(range(1, 1000))

            def rep(_m):
                return _fmt(params[f"{label}#{next(idx)}"])

            return _SWEEP_RE.sub(rep, text)

        d["signal"] = sub_text("신호", spec.signal)
        if spec.entry.type == "limit":
            d["entry"]["price"] = sub_text("매수가", spec.entry.price)
        if "유효기간" in params:
            d["entry"]["valid_days"] = int(params["유효기간"])
        if "목표" in params:
            d["exit"]["target_pct"] = params["목표"]
        if "손절" in params:
            d["exit"]["stop_pct"] = params["손절"]
        if "보유기간" in params:
            d["exit"]["max_days"] = int(params["보유기간"])
        out.append((params, Spec.model_validate(d)))
    return out


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def validate(spec: Spec) -> list[str]:
    """실행 전 검사. 문제 목록을 돌려준다 (비어 있으면 통과)."""
    errs = []
    for params, s in expand(spec):
        try:
            dsl.validate(s.signal)
        except dsl.DSLError as e:
            errs.append(f"신호 조건식: {e.message}" + (f" (위치 {e.pos + 1})" if e.pos is not None else ""))
        if s.entry.type == "limit":
            try:
                dsl.validate(s.entry.price)
            except dsl.DSLError as e:
                errs.append(f"매수 가격식: {e.message}")
            if not 1 <= int(s.entry.valid_days) <= 60:
                errs.append("유효기간은 1~60 거래일이어야 합니다")
        if not 1 <= int(s.exit.max_days) <= 250:
            errs.append("보유기간은 1~250 거래일이어야 합니다")
        if s.exit.target_pct is not None and s.exit.target_pct <= 0:
            errs.append("목표 수익률은 0보다 커야 합니다")
        if s.exit.stop_pct is not None and not 0 < s.exit.stop_pct < 100:
            errs.append("손절 비율은 0~100 사이여야 합니다 (예: 5 = -5%)")
        if errs:
            break
    return errs


# ---------------------------------------------------------------- 텍스트 형식

SECTION_ALIASES = {
    "신호": "signal", "조건": "signal", "signal": "signal",
    "매수": "entry", "진입": "entry", "entry": "entry",
    "청산": "exit", "매도": "exit", "성공": "exit", "exit": "exit",
    "대상": "universe", "universe": "universe",
    "비용": "costs", "costs": "costs",
    "옵션": "options", "options": "options",
}
_SECTION_RE = re.compile(r"^\s*\[\s*([^\]\d,][^\]]*)\]\s*$")
_NONE_WORDS = {"없음", "none", "off", "-", ""}


def _num_or_list(v: str):
    v = v.strip().rstrip("%").replace("일", "").strip()
    if v.lower() in _NONE_WORDS:
        return None
    m = re.fullmatch(r"\[(.*)\]", v)
    if m:
        return [float(x) for x in m.group(1).split(",")]
    return float(v)


def _int_or_list(v: str):
    r = _num_or_list(v)
    if isinstance(r, list):
        return [int(x) for x in r]
    return None if r is None else int(r)


def parse_text(text: str) -> Spec:
    sections: dict[str, list[str]] = {}
    cur = "signal"
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        m = _SECTION_RE.match(line)
        if m:
            key = m.group(1).strip()
            if key.lower() not in SECTION_ALIASES and key not in SECTION_ALIASES:
                raise ValueError(f"알 수 없는 구역 [{key}]. 사용 가능: [신호] [매수] [청산] [대상] [비용] [옵션]")
            cur = SECTION_ALIASES.get(key, SECTION_ALIASES.get(key.lower()))
            sections.setdefault(cur, [])
            continue
        sections.setdefault(cur, []).append(line.strip())

    if not sections.get("signal"):
        raise ValueError("[신호] 조건식이 없습니다")
    d: dict = {"signal": " ".join(sections["signal"])}

    def kv(lines: list[str]) -> list[tuple[str, str]]:
        out = []
        for ln in lines:
            if "=" in ln:
                k, v = ln.split("=", 1)
                out.append((k.strip(), v.strip()))
            else:
                out.append((ln.strip(), ""))
        return out

    entry: dict = {}
    for k, v in kv(sections.get("entry", [])):
        if k in ("지정가", "limit"):
            entry["type"], entry["price"] = "limit", v
        elif k in ("다음날시가", "익일시가", "next_open"):
            entry["type"] = "next_open"
        elif k in ("당일종가", "종가매수", "close"):
            entry["type"] = "close"
        elif k in ("유효기간", "valid_days"):
            entry["valid_days"] = _int_or_list(v)
        else:
            raise ValueError(f"[매수] 알 수 없는 항목 '{k}'. 사용 가능: 지정가 = 식, 다음날시가, 당일종가, 유효기간 = n")
    if entry:
        d["entry"] = entry

    ex: dict = {}
    for k, v in kv(sections.get("exit", [])):
        if k in ("목표", "익절", "target"):
            ex["target_pct"] = _num_or_list(v)
        elif k in ("손절", "stop"):
            ex["stop_pct"] = _num_or_list(v)
        elif k in ("보유기간", "기간", "max_days"):
            ex["max_days"] = _int_or_list(v)
        elif k in ("기준", "basis"):
            ex["target_basis"] = {"고가": "high", "종가": "close"}.get(v, v)
        else:
            raise ValueError(f"[청산] 알 수 없는 항목 '{k}'. 사용 가능: 목표, 손절, 보유기간, 기준")
    if ex:
        d["exit"] = ex

    uni: dict = {}
    for k, v in kv(sections.get("universe", [])):
        if k in ("시장", "markets"):
            uni["markets"] = [x.strip() for x in v.split(",") if x.strip()]
        elif k in ("시작", "start"):
            uni["start"] = v
        elif k in ("종료", "끝", "end"):
            uni["end"] = None if v in ("오늘", "") else v
        elif k in ("기간",):
            a, _, b = v.partition("~")
            uni["start"] = a.strip()
            b = b.strip()
            uni["end"] = None if b in ("", "오늘") else b
        elif k in ("제외", "exclude"):
            items = {x.strip() for x in v.split(",")}
            uni["exclude_spac"] = "스팩" in items
            uni["exclude_preferred"] = "우선주" in items
        else:
            raise ValueError(f"[대상] 알 수 없는 항목 '{k}'. 사용 가능: 시장, 시작, 종료, 기간, 제외")
    if uni:
        d["universe"] = uni

    costs: dict = {}
    cmap = {"매수수수료": "buy_fee_pct", "매도수수료": "sell_fee_pct", "세금": "sell_tax_pct",
            "거래세": "sell_tax_pct", "슬리피지": "slippage_pct"}
    for k, v in kv(sections.get("costs", [])):
        if k not in cmap:
            raise ValueError(f"[비용] 알 수 없는 항목 '{k}'. 사용 가능: {', '.join(cmap)}")
        costs[cmap[k]] = float(v.rstrip("%"))
    if costs:
        d["costs"] = costs

    for k, v in kv(sections.get("options", [])):
        if k in ("중복제외", "dedupe_days"):
            d["dedupe_days"] = int(_num_or_list(v) or 0)
        elif k in ("이름", "name"):
            d["name"] = v
        elif k in ("원화환산", "fx_krw"):
            d["fx_krw"] = v.strip() in ("예", "yes", "true", "1", "켜기", "on")
        else:
            raise ValueError(f"[옵션] 알 수 없는 항목 '{k}'. 사용 가능: 이름, 중복제외, 원화환산")

    return Spec.model_validate(d)


def _v(x) -> str:
    if x is None:
        return "없음"
    if isinstance(x, list):
        return "[" + ", ".join(_fmt(float(i)) for i in x) + "]"
    return _fmt(float(x))


def to_text(spec: Spec) -> str:
    lines = ["[신호]"]
    sig = re.sub(r"\s+(AND|OR|그리고|또는)\s+", r"\n\1 ", spec.signal.strip())
    lines += sig.splitlines()
    lines += ["", "[매수]"]
    e = spec.entry
    if e.type == "limit":
        lines += [f"지정가 = {e.price}", f"유효기간 = {_v(e.valid_days)}"]
    elif e.type == "next_open":
        lines += ["다음날시가"]
    else:
        lines += ["당일종가"]
    x = spec.exit
    lines += ["", "[청산]", f"목표 = {_v(x.target_pct)}", f"기준 = {'고가' if x.target_basis == 'high' else '종가'}",
              f"손절 = {_v(x.stop_pct)}", f"보유기간 = {_v(x.max_days)}"]
    u = spec.universe
    excl = [w for w, on in (("스팩", u.exclude_spac), ("우선주", u.exclude_preferred)) if on]
    lines += ["", "[대상]", "시장 = " + ", ".join(MARKET_LABEL.get(m, m) for m in u.markets),
              f"기간 = {u.start.isoformat()} ~ {u.end.isoformat() if u.end else '오늘'}",
              "제외 = " + (", ".join(excl) if excl else "없음")]
    c = spec.costs
    lines += ["", "[비용]", f"매수수수료 = {_fmt(c.buy_fee_pct)}", f"매도수수료 = {_fmt(c.sell_fee_pct)}",
              f"세금 = {_fmt(c.sell_tax_pct)}", f"슬리피지 = {_fmt(c.slippage_pct)}"]
    if spec.dedupe_days or spec.name or spec.fx_krw:
        lines += ["", "[옵션]"]
        if spec.name:
            lines.append(f"이름 = {spec.name}")
        if spec.dedupe_days:
            lines.append(f"중복제외 = {spec.dedupe_days}")
        if spec.fx_krw:
            lines.append("원화환산 = 예")
    return "\n".join(lines) + "\n"
