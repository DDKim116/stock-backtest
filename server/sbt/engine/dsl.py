"""조건식 언어.

예)  거래량 >= 거래량(1) * 5 AND 등락률 >= 12
     종가 > 이평(종가, 20) 그리고 거래대금 >= 100억

- 필드: 시가 고가 저가 종가 거래량 거래대금 시가총액 등락률 (영문: open high low close volume value mktcap change)
- 필드(n): n 거래일 전 값.  예) 거래량(1) = 전일 거래량
- 함수: 이평/ma, 지수이평/ema, 최고/highest, 최저/lowest, 합계/sum, 표준편차/stdev,
        이전/ref, 수익률/ret, rsi, 절대값/abs, 최대/max, 최소/min,
        상향돌파/crossup, 하향돌파/crossdown, 횟수/count
- 논리: AND/그리고, OR/또는, NOT/아님
- 숫자 뒤 만/억/조 단위 사용 가능 (100억 = 10,000,000,000)

계산은 Panel 의 평평한 배열 전체에 대해 벡터로 수행한다.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
import numpy as np
import pandas as pd

from ..data.panel import Panel


class DSLError(ValueError):
    def __init__(self, message: str, pos: int | None = None):
        super().__init__(message)
        self.message = message
        self.pos = pos


FIELDS: dict[str, str] = {}
for canon, aliases in {
    "open": ["시가", "open", "o"],
    "high": ["고가", "high", "h"],
    "low": ["저가", "low", "l"],
    "close": ["종가", "close", "c"],
    "volume": ["거래량", "volume", "vol", "v"],
    "value": ["거래대금", "value", "amount"],
    "mktcap": ["시가총액", "시총", "mktcap"],
    "change": ["등락률", "change", "chg"],
}.items():
    for a in aliases:
        FIELDS[a.lower()] = canon

FIELD_LABEL = {
    "open": "시가", "high": "고가", "low": "저가", "close": "종가", "volume": "거래량",
    "value": "거래대금", "mktcap": "시가총액", "change": "등락률",
}

FUNC_ALIASES: dict[str, str] = {}
for canon, aliases in {
    "ma": ["이평", "평균", "ma", "sma", "avg"],
    "ema": ["지수이평", "ema"],
    "highest": ["최고", "highest"],
    "lowest": ["최저", "lowest"],
    "sum": ["합계", "sum"],
    "stdev": ["표준편차", "stdev", "std"],
    "ref": ["이전", "ref"],
    "ret": ["수익률", "ret"],
    "rsi": ["rsi"],
    "abs": ["절대값", "abs"],
    "max": ["최대", "max"],
    "min": ["최소", "min"],
    "crossup": ["상향돌파", "crossup"],
    "crossdown": ["하향돌파", "crossdown"],
    "count": ["횟수", "count"],
}.items():
    for a in aliases:
        FUNC_ALIASES[a.lower()] = canon

ARITY = {"ma": (2,), "ema": (2,), "highest": (2,), "lowest": (2,), "sum": (2,), "stdev": (2,), "ref": (2,),
         "ret": (1,), "rsi": (1, 2), "abs": (1,), "max": (2,), "min": (2,), "crossup": (2,), "crossdown": (2,),
         "count": (2,)}
EXAMPLE = {"ma": "이평(종가, 20)", "ema": "지수이평(종가, 12)", "highest": "최고(고가, 20)", "lowest": "최저(저가, 20)",
           "sum": "합계(거래대금, 5)", "stdev": "표준편차(종가, 20)", "ref": "이전(이평(종가, 5), 1)",
           "ret": "수익률(20)", "rsi": "rsi(14)", "abs": "절대값(등락률)", "max": "최대(시가, 종가)",
           "min": "최소(시가, 종가)", "crossup": "상향돌파(이평(종가,5), 이평(종가,20))",
           "crossdown": "하향돌파(종가, 이평(종가,60))", "count": "횟수(등락률 > 0, 5)"}

KEYWORDS = {
    "and": "AND", "그리고": "AND", "&&": "AND",
    "or": "OR", "또는": "OR", "||": "OR",
    "not": "NOT", "아님": "NOT", "!": "NOT",
}
UNITS = {"만": 1e4, "억": 1e8, "조": 1e12}

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?)(?P<unit>[만억조])?
  | (?P<op>>=|<=|==|!=|&&|\|\||[-+*/()<>,=!])
  | (?P<ident>[A-Za-z_가-힣][A-Za-z0-9_가-힣]*)
  | (?P<bad>.)
    """,
    re.VERBOSE,
)


@dataclass
class Tok:
    kind: str  # num | op | ident | kw | end
    value: object
    pos: int


def tokenize(src: str) -> list[Tok]:
    toks: list[Tok] = []
    for m in _TOKEN_RE.finditer(src):
        if m.lastgroup == "ws":
            continue
        p = m.start()
        if m.group("num") is not None:
            v = float(m.group("num"))
            if m.group("unit"):
                v *= UNITS[m.group("unit")]
            toks.append(Tok("num", v, p))
        elif m.group("op") is not None:
            op = m.group("op")
            if op in ("&&", "||", "!"):
                toks.append(Tok("kw", KEYWORDS[op], p))
            elif op == "=":
                toks.append(Tok("op", "==", p))
            else:
                toks.append(Tok("op", op, p))
        elif m.group("ident") is not None:
            word = m.group("ident")
            # 한글은 조사나 단위가 붙어서 들어오지 않도록 그대로 비교한다.
            low = word.lower()
            if low in KEYWORDS:
                toks.append(Tok("kw", KEYWORDS[low], p))
            else:
                toks.append(Tok("ident", word, p))
        else:
            ch = m.group("bad")
            if ch == "%":
                raise DSLError("% 기호는 쓰지 않습니다. 등락률·수익률은 이미 % 단위입니다 (예: 등락률 >= 12).", p)
            raise DSLError(f"알 수 없는 문자 '{ch}'", p)
    toks.append(Tok("end", None, len(src)))
    return toks


# ---------------------------------------------------------------- AST

@dataclass
class Num:
    value: float


@dataclass
class Field:
    name: str


@dataclass
class Call:
    func: str
    args: list
    pos: int


@dataclass
class BinOp:
    op: str
    left: object
    right: object


@dataclass
class Unary:
    op: str
    operand: object


class Parser:
    def __init__(self, src: str):
        self.src = src
        self.toks = tokenize(src)
        self.i = 0

    def peek(self) -> Tok:
        return self.toks[self.i]

    def next(self) -> Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, kind: str, value=None) -> Tok:
        t = self.next()
        if t.kind != kind or (value is not None and t.value != value):
            want = value if value is not None else kind
            raise DSLError(f"'{want}' 가 필요합니다", t.pos)
        return t

    def parse(self):
        if self.peek().kind == "end":
            raise DSLError("조건식이 비어 있습니다", 0)
        node = self.parse_or()
        t = self.peek()
        if t.kind != "end":
            raise DSLError("해석할 수 없는 부분이 있습니다. AND/OR 를 빠뜨리지 않았는지 확인하세요", t.pos)
        return node

    def parse_or(self):
        node = self.parse_and()
        while self.peek().kind == "kw" and self.peek().value == "OR":
            self.next()
            node = BinOp("OR", node, self.parse_and())
        return node

    def parse_and(self):
        node = self.parse_not()
        while self.peek().kind == "kw" and self.peek().value == "AND":
            self.next()
            node = BinOp("AND", node, self.parse_not())
        return node

    def parse_not(self):
        if self.peek().kind == "kw" and self.peek().value == "NOT":
            self.next()
            return Unary("NOT", self.parse_not())
        return self.parse_cmp()

    def parse_cmp(self):
        node = self.parse_add()
        t = self.peek()
        if t.kind == "op" and t.value in (">=", "<=", ">", "<", "==", "!="):
            self.next()
            node = BinOp(t.value, node, self.parse_add())
            t2 = self.peek()
            if t2.kind == "op" and t2.value in (">=", "<=", ">", "<", "==", "!="):
                raise DSLError("비교는 한 번에 하나만 쓸 수 있습니다. AND 로 나눠 주세요", t2.pos)
        return node

    def parse_add(self):
        node = self.parse_mul()
        while self.peek().kind == "op" and self.peek().value in ("+", "-"):
            op = self.next().value
            node = BinOp(op, node, self.parse_mul())
        return node

    def parse_mul(self):
        node = self.parse_unary()
        while self.peek().kind == "op" and self.peek().value in ("*", "/"):
            op = self.next().value
            node = BinOp(op, node, self.parse_unary())
        return node

    def parse_unary(self):
        t = self.peek()
        if t.kind == "op" and t.value == "-":
            self.next()
            return Unary("-", self.parse_unary())
        if t.kind == "op" and t.value == "+":
            self.next()
            return self.parse_unary()
        return self.parse_primary()

    def parse_args(self) -> list:
        self.expect("op", "(")
        args = []
        if self.peek().kind == "op" and self.peek().value == ")":
            self.next()
            return args
        while True:
            args.append(self.parse_or())
            t = self.next()
            if t.kind == "op" and t.value == ")":
                return args
            if not (t.kind == "op" and t.value == ","):
                raise DSLError("')' 또는 ',' 가 필요합니다", t.pos)

    def parse_primary(self):
        t = self.next()
        if t.kind == "num":
            return Num(t.value)
        if t.kind == "op" and t.value == "(":
            node = self.parse_or()
            self.expect("op", ")")
            return node
        if t.kind == "ident":
            low = t.value.lower()
            has_args = self.peek().kind == "op" and self.peek().value == "("
            if low in FIELDS:
                f = Field(FIELDS[low])
                if has_args:
                    args = self.parse_args()
                    if len(args) != 1 or not isinstance(args[0], Num):
                        raise DSLError(f"{t.value}(n) 의 n 은 숫자여야 합니다 (n 거래일 전)", t.pos)
                    return Call("ref", [f, args[0]], t.pos)
                return f
            if low in FUNC_ALIASES:
                if not has_args:
                    raise DSLError(f"함수 {t.value} 뒤에 ( ) 가 필요합니다", t.pos)
                func = FUNC_ALIASES[low]
                args = self.parse_args()
                if len(args) not in ARITY[func]:
                    need = " 또는 ".join(str(n) for n in ARITY[func])
                    raise DSLError(f"{t.value} 함수는 인자가 {need}개 필요합니다 (예: {EXAMPLE[func]})", t.pos)
                return Call(func, args, t.pos)
            raise DSLError(f"알 수 없는 이름 '{t.value}'", t.pos)
        if t.kind == "end":
            raise DSLError("식이 중간에 끝났습니다", t.pos)
        raise DSLError(f"'{t.value}' 위치가 올바르지 않습니다", t.pos)


def parse(src: str):
    return Parser(src).parse()


# ---------------------------------------------------------------- 평가

def _int_arg(node, name: str, pos: int) -> int:
    if not isinstance(node, Num) or node.value != int(node.value) or node.value < 0:
        raise DSLError(f"{name} 의 기간은 0 이상의 정수여야 합니다", pos)
    return int(node.value)


class Evaluator:
    """Panel 위에서 AST 를 계산한다. 같은 하위식은 캐시한다."""

    def __init__(self, panel: Panel, max_cache: int = 40):
        self.p = panel
        self.cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self.max_cache = max_cache
        self._change: np.ndarray | None = None

    def _remember(self, key: str, val: np.ndarray) -> None:
        self.cache[key] = val
        self.cache.move_to_end(key)
        while len(self.cache) > self.max_cache:
            self.cache.popitem(last=False)

    # 종목 경계를 넘는 값을 지운다.
    def _mask_warmup(self, x: np.ndarray, k: int) -> np.ndarray:
        if k > 0:
            x = x.copy()
            x[self.p.pos < k] = np.nan
        return x

    def shift(self, x: np.ndarray, k: int) -> np.ndarray:
        if k == 0:
            return x
        out = np.full_like(x, np.nan, dtype=np.float64)
        out[k:] = x[:-k]
        return self._mask_warmup(out, k)

    def rolling(self, x: np.ndarray, n: int, how: str) -> np.ndarray:
        if n < 1:
            raise DSLError("기간은 1 이상이어야 합니다")
        r = pd.Series(x).rolling(n, min_periods=n)
        out = getattr(r, how)().to_numpy()
        return self._mask_warmup(out, n - 1)

    def ewm(self, x: np.ndarray, alpha: float, warmup: int) -> np.ndarray:
        s = pd.Series(x)
        out = s.groupby(self.p.gid).transform(lambda g: g.ewm(alpha=alpha, adjust=False).mean()).to_numpy()
        return self._mask_warmup(out, warmup)

    def field(self, name: str) -> np.ndarray:
        if name == "change":
            if self._change is None:
                c = self.p.cols["close"]
                # 경계값(예: 정확히 12%)이 부동소수점 오차로 빠지지 않게 반올림
                self._change = np.round((c / self.shift(c, 1) - 1.0) * 100.0, 6)
            return self._change
        if name not in self.p.cols:
            raise DSLError(f"이 데이터에는 {FIELD_LABEL.get(name, name)} 값이 없습니다")
        return self.p.cols[name]

    def eval(self, node) -> np.ndarray | float:
        key = repr(node)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        val = self._eval(node)
        if isinstance(val, np.ndarray):
            self._remember(key, val)
        return val

    def _eval(self, node):
        if isinstance(node, Num):
            return node.value
        if isinstance(node, Field):
            return self.field(node.name)
        if isinstance(node, Unary):
            v = self.eval(node.operand)
            if node.op == "-":
                return -_num(v)
            return ~_bool(v)
        if isinstance(node, BinOp):
            a, b = self.eval(node.left), self.eval(node.right)
            op = node.op
            if op == "AND":
                return _bool(a) & _bool(b)
            if op == "OR":
                return _bool(a) | _bool(b)
            a, b = _num(a), _num(b)
            with np.errstate(divide="ignore", invalid="ignore"):
                if op == "+":
                    return a + b
                if op == "-":
                    return a - b
                if op == "*":
                    return a * b
                if op == "/":
                    r = np.divide(a, b)
                    return np.where(np.isfinite(r), r, np.nan) if isinstance(r, np.ndarray) else r
                cmp = {
                    ">=": np.greater_equal, "<=": np.less_equal, ">": np.greater,
                    "<": np.less, "==": np.equal, "!=": np.not_equal,
                }[op]
                return cmp(a, b)  # NaN 비교는 False
        if isinstance(node, Call):
            return self.call(node)
        raise DSLError("내부 오류: 알 수 없는 노드")

    def call(self, node: Call):
        f, args, pos = node.func, node.args, node.pos

        def need(n: int):
            if len(args) != n:
                raise DSLError(f"{f} 함수는 인자가 {n}개 필요합니다", pos)

        if f in ("ma", "highest", "lowest", "sum", "stdev"):
            need(2)
            n = _int_arg(args[1], f, pos)
            how = {"ma": "mean", "highest": "max", "lowest": "min", "sum": "sum", "stdev": "std"}[f]
            return self.rolling(_num_arr(self.eval(args[0]), len(self.p)), n, how)
        if f == "ema":
            need(2)
            n = _int_arg(args[1], f, pos)
            return self.ewm(_num_arr(self.eval(args[0]), len(self.p)), 2.0 / (n + 1), n - 1)
        if f == "ref":
            need(2)
            return self.shift(_num_arr(self.eval(args[0]), len(self.p)), _int_arg(args[1], "이전", pos))
        if f == "ret":
            need(1)
            n = _int_arg(args[0], "수익률", pos)
            c = self.field("close")
            return np.round((c / self.shift(c, n) - 1.0) * 100.0, 6)
        if f == "rsi":
            if len(args) == 1:
                src, n = self.field("close"), _int_arg(args[0], "rsi", pos)
            else:
                need(2)
                src, n = _num_arr(self.eval(args[0]), len(self.p)), _int_arg(args[1], "rsi", pos)
            d = src - self.shift(src, 1)
            gain = np.where(d > 0, d, 0.0)
            loss = np.where(d < 0, -d, 0.0)
            gain[np.isnan(d)] = np.nan
            loss[np.isnan(d)] = np.nan
            ag = self.ewm(gain, 1.0 / n, n)
            al = self.ewm(loss, 1.0 / n, n)
            with np.errstate(divide="ignore", invalid="ignore"):
                rs = ag / al
                out = 100.0 - 100.0 / (1.0 + rs)
            out = np.where((al == 0) & (ag > 0), 100.0, out)
            return out
        if f == "abs":
            need(1)
            return np.abs(_num(self.eval(args[0])))
        if f in ("max", "min"):
            need(2)
            fn = np.fmax if f == "max" else np.fmin
            return fn(_num(self.eval(args[0])), _num(self.eval(args[1])))
        if f in ("crossup", "crossdown"):
            need(2)
            a = _num_arr(self.eval(args[0]), len(self.p))
            b = _num_arr(self.eval(args[1]), len(self.p))
            pa, pb = self.shift(a, 1), self.shift(b, 1)
            if f == "crossup":
                return (a > b) & (pa <= pb)
            return (a < b) & (pa >= pb)
        if f == "count":
            need(2)
            n = _int_arg(args[1], "횟수", pos)
            cond = _bool(self.eval(args[0])).astype(np.float64)
            return self.rolling(cond, n, "sum")
        raise DSLError(f"알 수 없는 함수 {f}", pos)


def _num(v):
    if isinstance(v, np.ndarray) and v.dtype == bool:
        return v.astype(np.float64)
    return v


def _num_arr(v, n: int) -> np.ndarray:
    v = _num(v)
    if isinstance(v, np.ndarray):
        return v
    return np.full(n, float(v))


def _bool(v):
    if isinstance(v, np.ndarray):
        if v.dtype == bool:
            return v
        return np.nan_to_num(v, nan=0.0) != 0
    return bool(v)


def evaluate(src: str, panel: Panel, evaluator: Evaluator | None = None) -> np.ndarray:
    """식을 계산해 배열로 돌려준다 (조건식이면 bool, 수식이면 float)."""
    node = parse(src)
    ev = evaluator or Evaluator(panel)
    v = ev.eval(node)
    if not isinstance(v, np.ndarray):
        if isinstance(v, bool):
            return np.full(len(panel), v)
        return np.full(len(panel), float(v))
    return v


def validate(src: str) -> None:
    parse(src)


def referenced_fields(src: str) -> set[str]:
    out: set[str] = set()

    def walk(n):
        if isinstance(n, Field):
            out.add(n.name)
        elif isinstance(n, Call):
            if n.func in ("ret", "rsi") and len(n.args) <= 1:
                out.add("close")
            for a in n.args:
                walk(a)
        elif isinstance(n, BinOp):
            walk(n.left)
            walk(n.right)
        elif isinstance(n, Unary):
            walk(n.operand)

    walk(parse(src))
    return out

