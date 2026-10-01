"""분석 서버 API.

실행: uvicorn sbt.api.app:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .. import config
from ..data.panel import Panel
from ..engine import dsl
from ..engine.run import Runner, SpecError
from ..engine.spec import MARKET_LABEL, Spec, parse_text, to_text
from .store import Store

DATASETS = {
    "kr": {"label": "국내 (KRX)", "synthetic": False, "survivorship": False},
    "demo": {"label": "데모 (가상 데이터)", "synthetic": True, "survivorship": False},
}

app = FastAPI(title="stock-backtest")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.environ.get("SBT_CORS_ORIGINS", "*").split(",") if o],
    allow_methods=["*"],
    allow_headers=["*"],
)
store = Store(config.DATA_DIR / "app.sqlite3")


# ---------------------------------------------------------------- 데이터셋 로딩 (파일이 바뀌면 다시 읽음)

class _Loaded:
    def __init__(self):
        self.lock = threading.Lock()
        self.items: dict[str, tuple[float, Runner]] = {}

    def get(self, name: str) -> Runner:
        if name not in DATASETS:
            raise HTTPException(404, f"알 수 없는 데이터셋 {name}")
        path = config.dataset_dir(name) / "prices.parquet"
        if not path.exists():
            hint = "python -m sbt.data.cli demo" if name == "demo" else "python -m sbt.data.cli kr-collect"
            raise HTTPException(409, f"{DATASETS[name]['label']} 데이터가 아직 없습니다. 서버에서 '{hint}' 를 실행하세요.")
        mtime = path.stat().st_mtime
        with self.lock:
            cur = self.items.get(name)
            if cur and cur[0] == mtime:
                return cur[1]
            meta_p = path.parent / "meta.json"
            meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
            runner = Runner(Panel.load(path), {"name": name, **DATASETS[name], "meta": meta})
            self.items[name] = (mtime, runner)
            return runner


loaded = _Loaded()


# ---------------------------------------------------------------- 인증 (단일 비밀번호)

def _sign(payload: str) -> str:
    key = hashlib.sha256(("sbt:" + config.APP_PASSWORD).encode()).digest()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def make_token(days: int = 90) -> str:
    exp = str(int(time.time()) + days * 86400)
    return base64.urlsafe_b64encode(f"{exp}.{_sign(exp)}".encode()).decode()


def check_token(token: str) -> bool:
    try:
        exp, sig = base64.urlsafe_b64decode(token.encode()).decode().split(".", 1)
    except Exception:
        return False
    return hmac.compare_digest(sig, _sign(exp)) and int(exp) > time.time()


def auth(request: Request) -> None:
    if not config.APP_PASSWORD:
        return
    h = request.headers.get("authorization", "")
    if not (h.startswith("Bearer ") and check_token(h[7:])):
        raise HTTPException(401, "로그인이 필요합니다")


class LoginIn(BaseModel):
    password: str


@app.post("/api/login")
def login(body: LoginIn):
    if not config.APP_PASSWORD:
        return {"token": "", "auth": False}
    if not hmac.compare_digest(body.password, config.APP_PASSWORD):
        time.sleep(1)
        raise HTTPException(401, "비밀번호가 틀렸습니다")
    return {"token": make_token(), "auth": True}


@app.get("/api/health")
def health():
    return {"ok": True, "auth_required": bool(config.APP_PASSWORD)}


# ---------------------------------------------------------------- 정보

@app.get("/api/datasets", dependencies=[Depends(auth)])
def datasets():
    out = []
    for name, info in DATASETS.items():
        p = config.dataset_dir(name)
        meta = json.loads((p / "meta.json").read_text()) if (p / "meta.json").exists() else None
        out.append({"name": name, **info, "ready": (p / "prices.parquet").exists(), "meta": meta})
    return out


@app.get("/api/reference")
def reference():
    """조건식 도움말 (화면의 '도움말'과 조립 화면에서 사용)."""
    return {
        "fields": [
            {"name": "시가", "desc": "당일 시가"}, {"name": "고가", "desc": "당일 고가"},
            {"name": "저가", "desc": "당일 저가"}, {"name": "종가", "desc": "당일 종가"},
            {"name": "거래량", "desc": "당일 거래량(주)"}, {"name": "거래대금", "desc": "당일 거래대금(원). 예: 거래대금 >= 100억"},
            {"name": "시가총액", "desc": "당일 시가총액(원)"}, {"name": "등락률", "desc": "전일 종가 대비 당일 종가 변화율(%)"},
        ],
        "functions": [
            {"name": "필드(n)", "desc": "n 거래일 전 값", "example": "거래량(1)"},
            {"name": "이평(식, n)", "desc": "n일 단순이동평균", "example": "이평(종가, 20)"},
            {"name": "지수이평(식, n)", "desc": "n일 지수이동평균", "example": "지수이평(종가, 12)"},
            {"name": "최고(식, n)", "desc": "최근 n일 최고값(당일 포함)", "example": "최고(고가, 20)"},
            {"name": "최저(식, n)", "desc": "최근 n일 최저값(당일 포함)", "example": "최저(저가, 20)"},
            {"name": "합계(식, n)", "desc": "최근 n일 합계", "example": "합계(거래대금, 5)"},
            {"name": "표준편차(식, n)", "desc": "최근 n일 표준편차", "example": "표준편차(종가, 20)"},
            {"name": "이전(식, n)", "desc": "식의 n 거래일 전 값", "example": "이전(이평(종가, 5), 1)"},
            {"name": "수익률(n)", "desc": "n 거래일 전 종가 대비 수익률(%)", "example": "수익률(20) >= 30"},
            {"name": "rsi(n)", "desc": "RSI 지표", "example": "rsi(14) < 30"},
            {"name": "상향돌파(a, b)", "desc": "a 가 b 를 아래에서 위로 돌파한 날", "example": "상향돌파(이평(종가,5), 이평(종가,20))"},
            {"name": "하향돌파(a, b)", "desc": "a 가 b 를 위에서 아래로 돌파한 날", "example": "하향돌파(종가, 이평(종가,60))"},
            {"name": "횟수(조건, n)", "desc": "최근 n일 중 조건을 만족한 날 수", "example": "횟수(등락률 > 0, 5) >= 4"},
            {"name": "절대값(식)", "desc": "절대값", "example": "절대값(등락률) < 1"},
            {"name": "최대(a, b) / 최소(a, b)", "desc": "둘 중 큰/작은 값", "example": "최대(시가, 종가)"},
        ],
        "logic": ["AND / 그리고", "OR / 또는", "NOT / 아님", "괄호 ( )"],
        "units": ["만", "억", "조"],
        "markets": [{"code": k, "label": v} for k, v in MARKET_LABEL.items()],
    }


# ---------------------------------------------------------------- 변환·검증

class TextIn(BaseModel):
    text: str


@app.post("/api/parse", dependencies=[Depends(auth)])
def parse(body: TextIn):
    try:
        s = parse_text(body.text)
    except Exception as e:  # pydantic/ValueError
        raise HTTPException(422, _err(e))
    return {"spec": s.model_dump(mode="json"), "text": to_text(s)}


@app.post("/api/to-text", dependencies=[Depends(auth)])
def spec_to_text(spec: Spec):
    return {"text": to_text(spec)}


class ExprIn(BaseModel):
    expr: str


@app.post("/api/check-expr", dependencies=[Depends(auth)])
def check_expr(body: ExprIn):
    try:
        dsl.validate(body.expr)
        return {"ok": True}
    except dsl.DSLError as e:
        return {"ok": False, "error": e.message, "pos": e.pos}


# ---------------------------------------------------------------- 실행

class RunIn(BaseModel):
    dataset: str = config.DEFAULT_DATASET
    spec: Spec | None = None
    text: str | None = None


@app.post("/api/run", dependencies=[Depends(auth)])
def run(body: RunIn):
    if body.spec is None and not body.text:
        raise HTTPException(422, "spec 또는 text 가 필요합니다")
    try:
        spec = body.spec or parse_text(body.text or "")
    except Exception as e:
        raise HTTPException(422, _err(e))
    runner = loaded.get(body.dataset)
    try:
        res = runner.run(spec)
    except SpecError as e:
        raise HTTPException(422, "\n".join(e.errors))
    except dsl.DSLError as e:
        raise HTTPException(422, e.message)
    res["text"] = to_text(spec)
    return res


@app.get("/api/chart", dependencies=[Depends(auth)])
def chart(code: str, dataset: str = config.DEFAULT_DATASET, start: str | None = None, end: str | None = None):
    runner = loaded.get(dataset)
    p = runner.panel
    rows = p.rows_for(code)
    if len(rows) == 0:
        raise HTTPException(404, f"{code} 데이터가 없습니다")
    d = p.dates[rows]
    m = np.ones(len(rows), dtype=bool)
    if start:
        m &= d >= np.datetime64(start)
    if end:
        m &= d <= np.datetime64(end)
    rows = rows[m]
    c = p.cols
    adj_events = p.df["is_adj_event"].to_numpy()[rows] if "is_adj_event" in p.df else np.zeros(len(rows), bool)
    return {
        "code": code,
        "name": str(p.names[rows[-1]]) if len(rows) else "",
        "market": MARKET_LABEL.get(p.markets[rows[-1]], "") if len(rows) else "",
        "bars": [
            {"t": str(p.dates[i]), "o": _f(c["open"][i]), "h": _f(c["high"][i]), "l": _f(c["low"][i]),
             "c": _f(c["close"][i]), "v": _f(c["volume"][i])}
            for i in rows
        ],
        "adj_dates": [str(p.dates[i]) for i, e in zip(rows, adj_events) if e],
    }


# ---------------------------------------------------------------- 저장한 전략

class StrategyIn(BaseModel):
    name: str
    text: str
    note: str = ""
    last_summary: dict | None = None


@app.get("/api/strategies", dependencies=[Depends(auth)])
def list_strategies():
    return store.list()


@app.post("/api/strategies", dependencies=[Depends(auth)])
def create_strategy(body: StrategyIn):
    _check_text(body.text)
    return store.save(body.name, body.text, body.note, last_summary=body.last_summary)


@app.put("/api/strategies/{sid}", dependencies=[Depends(auth)])
def update_strategy(sid: int, body: StrategyIn):
    if not store.get(sid):
        raise HTTPException(404, "없는 전략입니다")
    _check_text(body.text)
    return store.save(body.name, body.text, body.note, sid=sid, last_summary=body.last_summary)


@app.delete("/api/strategies/{sid}", dependencies=[Depends(auth)])
def delete_strategy(sid: int):
    store.delete(sid)
    return {"ok": True}


# ---------------------------------------------------------------- helpers

def _check_text(text: str) -> None:
    try:
        parse_text(text)
    except Exception as e:
        raise HTTPException(422, _err(e))


def _err(e: Exception) -> str:
    if hasattr(e, "errors") and callable(e.errors):  # pydantic ValidationError
        msgs = []
        for x in e.errors():
            loc = ".".join(str(i) for i in x.get("loc", []))
            msgs.append(f"{loc}: {x.get('msg')}")
        return "; ".join(msgs)
    return str(e)


def _f(x: float):
    x = float(x)
    return None if not np.isfinite(x) else round(x, 2)

