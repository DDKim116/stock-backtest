"""분석 서버 API.

실행: uvicorn sbt.api.app:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .. import config
from ..ai import client as ai_client
from ..ai import service as ai_service
from ..data.panel import Panel
from .. import journal, paper, push
from ..engine import dsl
from ..engine.run import Runner, SpecError
from ..engine.spec import MARKET_LABEL, Spec, expand, parse_text, to_text, validate
from .store import Store

KR_COSTS = {"buy_fee_pct": 0.015, "sell_fee_pct": 0.015, "sell_tax_pct": 0.20, "slippage_pct": 0.0}
# 국내 증권사 미국 주식 온라인 수수료 수준. 세금(양도세)은 매매 단위로 계산할 수 없어 제외
US_COSTS = {"buy_fee_pct": 0.07, "sell_fee_pct": 0.07, "sell_tax_pct": 0.0, "slippage_pct": 0.0}
DATASETS = {
    "kr": {"label": "국내 (KRX)", "synthetic": False, "survivorship": False, "currency": "KRW",
           "markets": ["KOSPI", "KOSDAQ"], "costs": KR_COSTS},
    "us": {"label": "미국 (NASDAQ·NYSE·AMEX)", "synthetic": False, "survivorship": True, "currency": "USD",
           "markets": ["NASDAQ", "NYSE", "AMEX"], "costs": US_COSTS},
    "demo": {"label": "데모 (가상 데이터)", "synthetic": True, "survivorship": False, "currency": "KRW",
             "markets": ["KOSPI", "KOSDAQ"], "costs": KR_COSTS},
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
        self.items: dict[str, tuple[tuple, Runner]] = {}

    def get(self, name: str) -> Runner:
        if name not in DATASETS:
            raise HTTPException(404, f"알 수 없는 데이터셋 {name}")
        path = config.dataset_dir(name) / "prices.parquet"
        if not path.exists():
            hint = {"demo": "python -m sbt.data.cli demo", "us": "python -m sbt.data.cli us-collect"}.get(
                name, "python -m sbt.data.cli kr-collect")
            raise HTTPException(409, f"{DATASETS[name]['label']} 데이터가 아직 없습니다. 서버에서 '{hint}' 를 실행하세요.")
        from ..data import indices as _ix

        mtime = (path.stat().st_mtime, _ix.mtime(name))  # 지수 파일이 바뀌어도 다시 읽는다
        with self.lock:
            cur = self.items.get(name)
            if cur and cur[0] == mtime:
                return cur[1]
            meta_p = path.parent / "meta.json"
            meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
            panel = Panel.load(path)
            _ix.attach(panel, name)
            from .. import breadth as _br

            _br.attach(panel)  # 조건식의 상승비율·동일가중
            runner = Runner(panel, {"name": name, **DATASETS[name], "meta": meta})
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
    return {"ok": True, "auth_required": bool(config.APP_PASSWORD), "ai_enabled": ai_client.enabled()}


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
            {"name": "거래량", "desc": "당일 거래량(주)"}, {"name": "거래대금", "desc": "당일 거래대금(국내 원, 미국 달러). 예: 거래대금 >= 100억"},
            {"name": "시가총액", "desc": "당일 시가총액(원). 미국 데이터에는 없음"}, {"name": "등락률", "desc": "전일 종가 대비 당일 종가 변화율(%)"},
            {"name": "지수", "desc": "그 종목이 속한 시장 지수 (코스피·코스닥, 미국은 나스닥·S&P 500). 예: 지수 > 이평(지수, 60) (상승장), 지수 <= 이전(지수, 5) * 0.95 (5일간 5% 넘게 하락)"},
            {"name": "상승비율", "desc": "그날 그 시장에서 오른 종목 비율(%). 지수와 달리 종목 수로 본 시장 분위기. 예: 상승비율 <= 20 (대부분 하락), 이평(상승비율, 5) <= 30"},
            {"name": "동일가중", "desc": "그 시장 모든 종목을 같은 비중으로 친 지수. 예: 동일가중 <= 이전(동일가중, 5) * 0.95 (중소형주까지 5일간 5% 넘게 하락)"},
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


class ScanIn(RunIn):
    days: int | None = None


@app.post("/api/scan", dependencies=[Depends(auth)])
def scan(body: ScanIn):
    if body.spec is None and not body.text:
        raise HTTPException(422, "spec 또는 text 가 필요합니다")
    try:
        spec = body.spec or parse_text(body.text or "")
    except Exception as e:
        raise HTTPException(422, _err(e))
    runner = loaded.get(body.dataset)
    try:
        return runner.scan(spec, body.days)
    except SpecError as e:
        raise HTTPException(422, "\n".join(e.errors))
    except dsl.DSLError as e:
        raise HTTPException(422, e.message)


# ---------------------------------------------------------------- 전략 탐색 (오래 걸려서 뒤에서 돌림)

class ExploreIn(RunIn):
    split: str | None = None


_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _spec_of(body: RunIn) -> Spec:
    if body.spec is None and not body.text:
        raise HTTPException(422, "spec 또는 text 가 필요합니다")
    try:
        return body.spec or parse_text(body.text or "")
    except Exception as e:
        raise HTTPException(422, _err(e))


@app.post("/api/explore/plan", dependencies=[Depends(auth)])
def explore_plan(body: ExploreIn):
    spec = _spec_of(body)
    runner = loaded.get(body.dataset)
    try:
        plan, cut = runner.explore_plan(spec, body.split)
    except SpecError as e:
        raise HTTPException(422, "\n".join(e.errors))
    except dsl.DSLError as e:
        raise HTTPException(422, e.message)
    return {"axes": [{"key": a.key, "kind": a.kind, "values": a.values} for a in plan.axes],
            "n_combos": len(plan.combos), "user_defined": plan.user_defined, "notes": plan.notes,
            "split": str(cut)}


@app.post("/api/explore", dependencies=[Depends(auth)])
def explore_start(body: ExploreIn):
    spec = _spec_of(body)
    runner = loaded.get(body.dataset)
    try:
        plan, _ = runner.explore_plan(spec, body.split)  # 잘못된 조건은 바로 알려 준다
    except SpecError as e:
        raise HTTPException(422, "\n".join(e.errors))
    except dsl.DSLError as e:
        raise HTTPException(422, e.message)
    with _jobs_lock:
        if any(j["status"] == "running" for j in _jobs.values()):
            raise HTTPException(409, "다른 탐색이 진행 중입니다. 끝난 뒤 다시 시도하세요.")
        job_id = secrets.token_hex(6)
        job = {"id": job_id, "status": "running", "spec_text": to_text(spec), "dataset": body.dataset, "done": 0, "total": len(plan.combos), "result": None,
               "error": None, "started": time.time(), "cancel": False}
        _jobs[job_id] = job
        for k in [k for k, j in _jobs.items() if j["status"] != "running" and k != job_id][:-5]:
            _jobs.pop(k, None)  # 오래된 결과는 최근 5개만 남김

    def work():
        def prog(d, n):
            job["done"] = d
        try:
            job["result"] = runner.explore(spec, body.split, prog, lambda: job["cancel"])
            job["status"] = "cancelled" if job["cancel"] else "done"
        except Exception as e:  # noqa: BLE001
            job["status"] = "error"
            job["error"] = getattr(e, "message", None) or str(e)

    threading.Thread(target=work, daemon=True).start()
    return {"id": job_id, "total": len(plan.combos)}


@app.get("/api/explore/{job_id}", dependencies=[Depends(auth)])
def explore_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "탐색 기록이 없습니다 (서버가 다시 시작되었을 수 있습니다)")
    return {k: v for k, v in job.items() if k not in ("cancel", "spec_text")} | {
        "elapsed": round(time.time() - job["started"], 1)}


@app.delete("/api/explore/{job_id}", dependencies=[Depends(auth)])
def explore_cancel(job_id: str):
    job = _jobs.get(job_id)
    if job and job["status"] == "running":
        job["cancel"] = True
    return {"ok": True}


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
    # 그 종목이 속한 시장의 지수 종가 (차트 아래 '지수' 칸)
    ix = c.get("index")
    ix_name = None
    if ix is not None and len(rows):
        from ..data import indices

        key = indices.MARKET_INDEX.get(dataset, {}).get(str(p.markets[rows[-1]]))
        ix_name = indices.INDICES[key]["name"] if key else None
    return {
        "code": code,
        "name": str(p.names[rows[-1]]) if len(rows) else "",
        "market": MARKET_LABEL.get(p.markets[rows[-1]], "") if len(rows) else "",
        "bars": [
            {"t": str(p.dates[i]), "o": _f(c["open"][i]), "h": _f(c["high"][i]), "l": _f(c["low"][i]),
             "c": _f(c["close"][i]), "v": _f(c["volume"][i]),
             "a": _f(c["value"][i]) if "value" in c else None,
             "m": _f(c["mktcap"][i]) if "mktcap" in c else None,
             "x": _f(ix[i]) if ix is not None else None}
            for i in rows
        ],
        "index_name": ix_name,
        "adj_dates": [str(p.dates[i]) for i, e in zip(rows, adj_events) if e],
    }


# ---------------------------------------------------------------- 저장한 전략

class StrategyIn(BaseModel):
    name: str
    text: str
    note: str = ""
    last_summary: dict | None = None


@app.get("/api/krx-status", dependencies=[Depends(auth)])
def get_krx_status():
    from .. import krx_status

    return krx_status.view()


@app.post("/api/krx-status/renewed", dependencies=[Depends(auth)])
def krx_renewed():
    from .. import krx_status

    return {"remind_from": krx_status.set_renewed()}


_BREADTH: dict[tuple, dict] = {}


@app.get("/api/breadth", dependencies=[Depends(auth)])
def get_breadth(market: str = "KOSPI", dataset: str = "kr"):
    """시장 폭: 날마다 상승비율·동일가중 지수, 같은 날 지수 종가."""
    import numpy as np

    from ..data import indices

    runner = loaded.get(dataset)
    b = getattr(runner.panel, "breadth", None) or {}
    if market not in b:
        raise HTTPException(404, f"{market} 종목 데이터가 없습니다")
    key = (id(runner), market)
    if key not in _BREADTH:
        if len(_BREADTH) > 8:
            _BREADTH.clear()
        x = b[market]
        ix_key = indices.MARKET_INDEX.get(dataset, {}).get(market)
        ix = indices.load(ix_key) if ix_key else None
        idx = None
        if ix is not None:
            sd = ix.index.to_numpy(dtype="datetime64[D]")
            k = np.searchsorted(sd, x["dates"], side="right") - 1
            ok = (k >= 0) & (x["dates"] == sd[np.maximum(k, 0)])
            idx = [round(float(v), 2) if o else None for v, o in zip(ix.to_numpy()[np.maximum(k, 0)], ok)]
        _BREADTH[key] = {
            "market": market,
            "index_name": indices.INDICES[ix_key]["name"] if ix_key else None,
            "dates": [str(d) for d in x["dates"]],
            "adv": [None if not np.isfinite(v) else round(float(v), 1) for v in x["adv"]],
            "up": x["up"].tolist(), "n": x["n"].tolist(),
            "ew": [round(float(v), 2) for v in x["ew"]],
            "index": idx,
        }
    return _BREADTH[key]


_CONC: dict[tuple, dict] = {}


@app.get("/api/concentration", dependencies=[Depends(auth)])
def get_concentration(market: str = "KOSPI", dataset: str = "kr"):
    """시총 상위 종목 비중 (월말 기준 흐름, 연말 상위 10)."""
    from .. import concentration

    if market not in ("KOSPI", "KOSDAQ"):
        raise HTTPException(422, "KOSPI 또는 KOSDAQ")
    runner = loaded.get(dataset)
    key = (id(runner), market)
    if key not in _CONC:
        if len(_CONC) > 8:
            _CONC.clear()
        _CONC[key] = concentration.analyze(runner.panel, market)
    return _CONC[key]


# ---------------------------------------------------------------- 연구
class LabRunIn(BaseModel):
    cmd: str


@app.get("/api/lab", dependencies=[Depends(auth)])
def get_lab():
    """연구 목록 (질문·결론·최근 결과)과 지금 돌고 있는 연구."""
    from .. import lab

    return lab.view()


@app.get("/api/lab/report/{name}", dependencies=[Depends(auth)])
def get_lab_report(name: str):
    from .. import lab

    text = lab.read(name)
    if text is None:
        raise HTTPException(404, "결과 파일이 없습니다")
    return {"name": name, "text": text}


@app.post("/api/lab/run", dependencies=[Depends(auth)])
def run_lab(body: LabRunIn):
    """서버 명령으로 연구를 다시 돌린다 (한 번에 하나)."""
    from .. import lab

    try:
        return lab.start(body.cmd)
    except ValueError as e:
        raise HTTPException(409, str(e))


_IX_BARS: dict[str, tuple] = {}


@app.get("/api/indices/bars", dependencies=[Depends(auth)])
def get_index_bars(key: str):
    """지수 일봉 (시장 탭 차트)."""
    from ..data import indices

    if key not in indices.INDICES:
        raise HTTPException(404, "지수가 없습니다")
    if not indices.ohlc_path(key).exists() and key not in _IX_BARS:
        _IX_BARS[key] = (0, [])  # 처음 한 번만 받아 본다 (실패해도 종가로 그린다)
        try:
            indices.update_ohlc(log=lambda *a: None, keys=[key])
        except Exception:  # noqa: BLE001
            pass
    p, o = indices.path(key), indices.ohlc_path(key)
    stamp = (p.stat().st_mtime if p.exists() else 0, o.stat().st_mtime if o.exists() else 0)
    cur = _IX_BARS.get(key)
    if not cur or cur[0] != stamp:
        _IX_BARS[key] = cur = (stamp, indices.bars(key))
    return {"key": key, "name": indices.INDICES[key]["name"], "bars": cur[1], "ohlc": o.exists()}


@app.get("/api/indices", dependencies=[Depends(auth)])
def get_indices(group: str = "kr"):
    from ..data import indices

    if group not in {g["key"] for g in indices.GROUPS}:
        raise HTTPException(422, "group 은 kr 또는 us")
    return indices.summary(group)


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



# ---------------------------------------------------------------- AI (선택 기능)

AI_DEFAULTS = {"model": ai_client.DEFAULT_MODEL, "monthly_limit_usd": 20.0, "krw_rate": 1400.0}
# 사용 기록이 없을 때 1회 예상 비용(달러)
AI_EST = {"claude-opus-5-5": 0.08, "claude-sonnet-5-5": 0.04, "claude-haiku-4-5": 0.02}


def ai_settings() -> dict:
    return {k: store.get_setting(f"ai.{k}", v) for k, v in AI_DEFAULTS.items()}


def ai_status_dict() -> dict:
    st = ai_settings()
    used = store.month_usage()
    base = AI_EST.get(st["model"], 0.08)
    est = {k: round(used["avg_by_kind"].get(k, base) * st["krw_rate"]) for k in ("translate", "explain", "review", "journal")}
    return {
        "enabled": ai_client.enabled(),
        **st,
        "models": ai_client.MODELS,
        "month_count": used["count"],
        "month_usd": round(used["usd"], 4),
        "month_krw": round(used["usd"] * st["krw_rate"]),
        "limit_krw": round(st["monthly_limit_usd"] * st["krw_rate"]),
        "estimate_krw": est,
    }


def _ai_guard() -> dict:
    st = ai_settings()
    if not ai_client.enabled():
        raise HTTPException(503, "AI 기능이 꺼져 있습니다. 서버 .env 에 ANTHROPIC_API_KEY 를 넣어 주세요.")
    if store.month_usage()["usd"] >= st["monthly_limit_usd"]:
        raise HTTPException(402, "이번 달 AI 사용 한도에 도달했습니다. 설정에서 한도를 늘리거나 다음 달에 사용하세요.")
    return st


def _usage_out(u, st) -> dict:
    return {"model": u.model, "input_tokens": u.input_tokens + u.cache_write + u.cache_read,
            "output_tokens": u.output_tokens, "cost_usd": round(u.cost_usd, 4),
            "cost_krw": round(u.cost_usd * st["krw_rate"])}


@app.get("/api/ai/status", dependencies=[Depends(auth)])
def ai_status():
    return ai_status_dict()


class AISettingsIn(BaseModel):
    model: str | None = None
    monthly_limit_usd: float | None = None
    krw_rate: float | None = None


@app.put("/api/ai/settings", dependencies=[Depends(auth)])
def ai_update_settings(body: AISettingsIn):
    if body.model is not None:
        if body.model not in {m["id"] for m in ai_client.MODELS}:
            raise HTTPException(422, "지원하지 않는 모델입니다")
        store.set_setting("ai.model", body.model)
    if body.monthly_limit_usd is not None:
        if not 0 <= body.monthly_limit_usd <= 1000:
            raise HTTPException(422, "월 한도는 0~1000 달러 사이로 정해 주세요")
        store.set_setting("ai.monthly_limit_usd", body.monthly_limit_usd)
    if body.krw_rate is not None:
        if not 500 <= body.krw_rate <= 5000:
            raise HTTPException(422, "환율 값이 이상합니다")
        store.set_setting("ai.krw_rate", body.krw_rate)
    return ai_status_dict()


class TranslateIn(BaseModel):
    question: str
    current_text: str | None = None
    history: list[str] = []
    dataset: str = config.DEFAULT_DATASET


@app.post("/api/ai/translate", dependencies=[Depends(auth)])
def ai_translate(body: TranslateIn):
    if not body.question.strip():
        raise HTTPException(422, "질문을 입력하세요")
    if len(body.question) > 2000:
        raise HTTPException(422, "질문이 너무 깁니다 (2000자 이하)")
    st = _ai_guard()
    try:
        ds = DATASETS.get(body.dataset, DATASETS["kr"])
        out = ai_service.translate(body.question.strip(), body.current_text, st["model"], body.history[-6:],
                                   dataset=ds)
    except ai_client.AIError as e:
        raise HTTPException(e.status, str(e))
    if out.get("usage"):
        store.add_usage("translate", out["usage"])
    res = {k: v for k, v in out.items() if k not in ("usage", "spec")}
    if out.get("spec") is not None:
        res["spec"] = out["spec"].model_dump(mode="json")
    if out.get("usage"):
        res["usage"] = _usage_out(out["usage"], st)
    return res


class ExplainIn(BaseModel):
    dataset: str = config.DEFAULT_DATASET
    spec: Spec


@app.post("/api/ai/explain", dependencies=[Depends(auth)])
def ai_explain(body: ExplainIn):
    st = _ai_guard()
    runner = loaded.get(body.dataset)
    try:
        res = runner.run(body.spec, with_trades=False)
    except (SpecError, dsl.DSLError) as e:
        raise HTTPException(422, str(e))
    try:
        out = ai_service.explain(res, to_text(body.spec), st["model"])
    except ai_client.AIError as e:
        raise HTTPException(e.status, str(e))
    store.add_usage("explain", out["usage"])
    out["usage"] = _usage_out(out["usage"], st)
    return out


class ReviewIn(BaseModel):
    job_id: str


@app.post("/api/ai/explore-review", dependencies=[Depends(auth)])
def ai_explore_review(body: ReviewIn):
    st = _ai_guard()
    job = _jobs.get(body.job_id)
    if not job or not job.get("result"):
        raise HTTPException(404, "탐색 결과가 없습니다. 탐색을 다시 실행해 주세요.")
    if job.get("review"):
        return job["review"]  # 같은 결과는 다시 요금을 내지 않도록
    try:
        out = ai_service.review_explore(job["result"], job["spec_text"], st["model"])
    except ai_client.AIError as e:
        raise HTTPException(e.status, str(e))
    store.add_usage("review", out["usage"])
    out["usage"] = _usage_out(out["usage"], st)
    job["review"] = out
    return out


# ---------------------------------------------------------------- 매매일지

class FillIn(BaseModel):
    side: str
    date: str
    price: float
    qty: float
    fee: float | None = None
    memo: str = ""


class JournalIn(BaseModel):
    dataset: str = config.DEFAULT_DATASET
    code: str
    signal_date: str | None = None
    strategy_id: int | None = None
    strategy_name: str = ""
    strategy_text: str = ""
    memo: str = ""
    fills: list[FillIn] = []


class JournalPatch(BaseModel):
    memo: str | None = None
    signal_date: str | None = None
    strategy_id: int | None = None
    strategy_name: str | None = None
    strategy_text: str | None = None


def _check_fill(f: FillIn) -> dict:
    if f.side not in ("buy", "sell"):
        raise HTTPException(422, "side 는 buy 또는 sell 입니다")
    if f.price <= 0 or f.qty <= 0:
        raise HTTPException(422, "가격과 수량은 0보다 커야 합니다")
    try:
        dt.date.fromisoformat(f.date)
    except ValueError:
        raise HTTPException(422, f"날짜 형식이 올바르지 않습니다: {f.date}")
    return f.model_dump()


def _sorted_fills(fills: list[dict]) -> list[dict]:
    return sorted(fills, key=lambda f: (f["date"], 0 if f["side"] == "buy" else 1, f.get("id") or 1e12))


def _journal_view(t: dict) -> dict:
    """저장된 거래 + 손익 계산 + 전략 비교."""
    ds = DATASETS.get(t["dataset"], {})
    try:
        runner = loaded.get(t["dataset"])
        last_date, last_close, _, _ = runner.last_close(t["code"])
    except HTTPException:
        runner, last_date, last_close = None, None, None
    try:
        pos = journal.position(_sorted_fills(t["fills"]), ds.get("costs", {}), last_close)
    except journal.JournalError as e:
        pos = {"error": str(e), "status": "error", "closed": False, "ret": None, "pnl": None, "invested": 0,
               "unrealized": None, "holding_days": None, "first_buy": None, "last_sell": None}
    comp = None
    if runner and t.get("strategy_text") and t.get("signal_date"):
        try:
            strat = runner.simulate_one(parse_text(t["strategy_text"]), t["code"], t["signal_date"])
            comp = journal.compare(pos, strat) if "error" not in pos else None
        except Exception:  # noqa: BLE001 — 전략 글이 지금 문법과 안 맞아도 일지는 보여 준다
            comp = None
    return {**t, "currency": ds.get("currency", "KRW"), "last_date": last_date, "last_close": last_close,
            "pos": pos, "compare": comp}


@app.get("/api/journal", dependencies=[Depends(auth)])
def journal_list(dataset: str | None = None):
    return [_journal_view(t) for t in store.journal_list(dataset)]


@app.get("/api/journal/stats", dependencies=[Depends(auth)])
def journal_stats(dataset: str = config.DEFAULT_DATASET):
    views = [_journal_view(t) for t in store.journal_list(dataset)]
    views = [v for v in views if v["pos"].get("status") != "error"]
    strategies = {s["id"]: s for s in store.list()}
    return {"dataset": dataset, "currency": DATASETS.get(dataset, {}).get("currency", "KRW"),
            **journal.stats(views, strategies)}


@app.post("/api/journal", dependencies=[Depends(auth)])
def journal_create(body: JournalIn):
    runner = loaded.get(body.dataset)
    _, _, name, market = runner.last_close(body.code)
    if not name:
        raise HTTPException(404, f"{body.code} 종목을 찾을 수 없습니다")
    fills = [_check_fill(f) for f in body.fills]
    try:
        journal.position(_sorted_fills(fills), DATASETS[body.dataset].get("costs", {}), None)
    except journal.JournalError as e:
        raise HTTPException(422, str(e))
    tid = store.journal_create({**body.model_dump(exclude={"fills"}), "name": name, "market": market})
    for f in fills:
        store.fill_add(tid, f)
    return _journal_view(store.journal_get(tid))


def _journal_all(dataset: str) -> tuple[list[dict], dict]:
    views = [_journal_view(t) for t in store.journal_list(dataset)]
    views = [v for v in views if v["pos"].get("status") != "error"]
    st = journal.stats(views, {s["id"]: s for s in store.list()})
    return views, st


@app.get("/api/journal/export", dependencies=[Depends(auth)])
def journal_export(dataset: str = config.DEFAULT_DATASET):
    from urllib.parse import quote

    from fastapi.responses import Response

    views, st = _journal_all(dataset)
    data = journal.to_xlsx(views, st, DATASETS.get(dataset, {}).get("currency", "KRW"))
    # 일부 브라우저가 한글 파일 이름을 'download' 로 바꿔 버려서 영문으로
    name = f"trading-journal_{dataset}_{dt.date.today().isoformat()}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


class JournalReviewIn(BaseModel):
    dataset: str = config.DEFAULT_DATASET


@app.post("/api/ai/journal-review", dependencies=[Depends(auth)])
def ai_journal_review(body: JournalReviewIn):
    st_ai = _ai_guard()
    views, st = _journal_all(body.dataset)
    if st["closed"]["n"] == 0 and st["open"]["n"] == 0:
        raise HTTPException(422, "복기할 매매 기록이 없습니다")
    try:
        out = ai_service.review_journal(views, st, st_ai["model"])
    except ai_client.AIError as e:
        raise HTTPException(e.status, str(e))
    store.add_usage("journal", out["usage"])
    out["usage"] = _usage_out(out["usage"], st_ai)
    out["n_closed"] = st["closed"]["n"]
    return out


@app.get("/api/journal/{tid}", dependencies=[Depends(auth)])
def journal_get(tid: int):
    t = store.journal_get(tid)
    if not t:
        raise HTTPException(404, "기록이 없습니다")
    return _journal_view(t)


@app.put("/api/journal/{tid}", dependencies=[Depends(auth)])
def journal_update(tid: int, body: JournalPatch):
    if not store.journal_get(tid):
        raise HTTPException(404, "기록이 없습니다")
    store.journal_update(tid, body.model_dump(exclude_none=True))
    return _journal_view(store.journal_get(tid))


@app.delete("/api/journal/{tid}", dependencies=[Depends(auth)])
def journal_delete(tid: int):
    store.journal_delete(tid)
    return {"ok": True}


@app.post("/api/journal/{tid}/fills", dependencies=[Depends(auth)])
def journal_add_fill(tid: int, body: FillIn):
    t = store.journal_get(tid)
    if not t:
        raise HTTPException(404, "기록이 없습니다")
    f = _check_fill(body)
    try:  # 넣었을 때 보유 수량이 마이너스가 되지 않는지 먼저 확인
        journal.position(_sorted_fills(t["fills"] + [f]), DATASETS.get(t["dataset"], {}).get("costs", {}), None)
    except journal.JournalError as e:
        raise HTTPException(422, str(e))
    store.fill_add(tid, f)
    return _journal_view(store.journal_get(tid))


@app.delete("/api/journal/fills/{fid}", dependencies=[Depends(auth)])
def journal_delete_fill(fid: int):
    f = store.fill_get(fid)
    if not f:
        raise HTTPException(404, "체결 기록이 없습니다")
    t = store.journal_get(f["trade_id"])
    rest = [x for x in t["fills"] if x["id"] != fid]
    try:
        journal.position(_sorted_fills(rest), DATASETS.get(t["dataset"], {}).get("costs", {}), None)
    except journal.JournalError as e:
        raise HTTPException(422, "이 매수를 지우면 매도 수량이 보유 수량보다 많아집니다. 매도 기록을 먼저 지우세요. (" + str(e) + ")")
    store.fill_delete(fid)
    return _journal_view(store.journal_get(t["id"]))


# ---------------------------------------------------------------- 모의투자 (모투)

class PaperBookIn(BaseModel):
    name: str
    dataset: str = config.DEFAULT_DATASET
    spec: Spec
    capital: float
    per_trade: float
    auto: bool = False
    backtest: dict | None = None
    memo: str = ""


class PaperBookPatch(BaseModel):
    name: str | None = None
    memo: str | None = None
    archived: bool | None = None
    auto: bool | None = None


class PaperCashIn(BaseModel):
    kind: str  # deposit | withdraw | per_trade
    amount: float
    date: str | None = None


class PaperPickIn(BaseModel):
    code: str
    signal_date: str
    name: str = ""
    market: str = ""


class PaperTradePatch(BaseModel):
    manual_exit_date: str | None = None
    reopen: bool = False
    note: str | None = None
    include: bool | None = None  # 소급 거래를 통계·계좌에 넣기


_paper_sync: dict[int, tuple] = {}  # book_id → 마지막으로 자동 담기를 맞춘 (데이터 runner id, 시작일)


def _paper_runner(book: dict) -> Runner | None:
    try:
        return loaded.get(book["dataset"])
    except HTTPException:
        return None


def _paper_auto(book: dict, runner: Runner) -> None:
    key = (id(runner), book["auto_since"])
    if not book["auto"] or _paper_sync.get(book["id"]) == key:
        return
    sig = paper.signals_since(runner, paper.book_spec(book), book["auto_since"] or book["created_at"][:10])
    store.paper_sync_auto(book["id"], sig)
    _paper_sync[book["id"]] = key


def _paper_view(book: dict, with_curve: bool = True) -> dict:
    ds = DATASETS.get(book["dataset"], {})
    try:
        spec_json = paper.book_spec(book).model_dump(mode="json")
    except Exception:  # noqa: BLE001
        spec_json = None
    out = {"book": {**book, "spec": spec_json, "currency": ds.get("currency", "KRW"),
                    "dataset_label": ds.get("label", book["dataset"])},
           "cash": store.paper_cash(book["id"])}
    runner = _paper_runner(book)
    if runner is None:
        return {**out, "error": "데이터가 없습니다"}
    try:
        _paper_auto(book, runner)
        trades = store.paper_trades(book["id"])
        for t in trades:
            t["include_retro"] = False
        v = paper.view(runner, book, trades, out["cash"], with_curve)
    except Exception as e:  # noqa: BLE001 — 조건식 문법이 바뀌어도 목록은 보여 준다
        return {**out, "error": _err(e)}
    if not with_curve:
        v.pop("trades")
    return {**out, **v}


def _auto_since(dataset: str, spec: Spec) -> str:
    """자동 담기 시작일: 지금 담아도 매수 시점 전인 가장 이른 신호일."""
    runner = loaded.get(dataset)
    last = str(runner.panel.dates.max())
    return last if not paper.is_retro(dataset, last, spec.entry.type) else \
        (dt.date.fromisoformat(last) + dt.timedelta(days=1)).isoformat()


@app.get("/api/paper/books", dependencies=[Depends(auth)])
def paper_books(dataset: str | None = None, spec_text: str | None = None):
    canon = None
    if spec_text:
        try:
            canon = paper.canon_text(parse_text(spec_text))
        except Exception:  # noqa: BLE001
            canon = None
    out = []
    for b in store.paper_books():
        if dataset and b["dataset"] != dataset:
            continue
        v = _paper_view(b, with_curve=False)
        v["same_spec"] = canon is not None and b["canon"] == canon
        out.append(v)
    return out


@app.get("/api/paper/news", dependencies=[Depends(auth)])
def paper_news(limit: int = 60):
    """모투 소식: 내 선택 종목의 체결·미체결·청산. seen(마지막으로 본 데이터 날짜) 뒤의 것이 새 소식."""
    seen = store.get_setting("paper_news_seen", {})
    items, as_of = _paper_news_items()
    for it in items:
        it["new"] = it["date"] > seen.get(it["dataset"], "")
    order = {"wait": 0, "expire": 1, "fill": 2, "exit": 3}
    items.sort(key=lambda x: (x["date"], -order[x["kind"]]), reverse=True)
    return {"as_of": as_of, "seen": seen, "unseen": sum(1 for x in items if x["new"]), "items": items[:limit]}


def _paper_news_items(dataset: str | None = None) -> tuple[list[dict], dict]:
    items, as_of = [], {}
    for b in store.paper_books(include_archived=False):
        if dataset and b["dataset"] != dataset:
            continue
        runner = _paper_runner(b)
        if runner is None:
            continue
        cur = DATASETS.get(b["dataset"], {}).get("currency", "KRW")
        try:
            _paper_auto(b, runner)
            trades = store.paper_trades(b["id"])
            sims = paper.simulate_trades(runner, paper.book_spec(b), trades)
        except Exception:  # noqa: BLE001
            continue
        for t, s in zip(trades, sims):
            t.update(s)
        last = str(runner.panel.dates.max())
        as_of[b["dataset"]] = last
        fmt = (lambda x, c=cur: "–" if x is None else (f"${x:,.2f}" if c == "USD" else f"{x:,.0f}원"))
        for it in paper.news(b, trades, last, fmt):
            it["dataset"] = b["dataset"]
            items.append(it)
    return items, as_of


@app.post("/api/paper/news/seen", dependencies=[Depends(auth)])
def paper_news_seen():
    seen = store.get_setting("paper_news_seen", {})
    for name in DATASETS:
        try:
            seen[name] = str(loaded.get(name).panel.dates.max())
        except HTTPException:
            continue
    store.set_setting("paper_news_seen", seen)
    return {"seen": seen}


@app.post("/api/paper/books", dependencies=[Depends(auth)])
def paper_book_create(body: PaperBookIn):
    if body.dataset not in DATASETS:
        raise HTTPException(404, "알 수 없는 데이터셋")
    if not body.name.strip():
        raise HTTPException(422, "카테고리 이름을 넣어 주세요")
    if body.capital <= 0 or body.per_trade <= 0:
        raise HTTPException(422, "총 자본금과 1회 매수금은 0보다 커야 합니다")
    if body.per_trade > body.capital:
        raise HTTPException(422, "1회 매수금이 총 자본금보다 큽니다")
    spec = body.spec.model_copy(update={"universe": body.spec.universe.model_copy(update={"end": None})})
    errs = validate(spec)
    if errs:
        raise HTTPException(422, "; ".join(errs))
    if len(expand(spec)) > 1:
        raise HTTPException(422, "숫자 자리에 값을 하나씩만 넣은 조건으로 만들어 주세요 (비교용 [a, b] 는 안 됩니다)")
    bid = store.paper_book_create({
        "name": body.name.strip(), "dataset": body.dataset, "spec_text": to_text(spec),
        "canon": paper.canon_text(spec), "auto": body.auto, "auto_since": _auto_since(body.dataset, spec),
        "backtest": body.backtest, "memo": body.memo,
        "start": dt.datetime.now(paper.KST).date().isoformat(),
    }, body.capital, body.per_trade)
    if body.backtest is None:
        threading.Thread(target=_paper_backtest, args=(bid, body.dataset, spec), daemon=True).start()
    return _paper_view(store.paper_book(bid))


def _paper_backtest(bid: int, dataset: str, spec: Spec) -> None:
    """카테고리를 만들 때의 검증 결과(나중에 모의 결과와 비교용)."""
    try:
        r = loaded.get(dataset).run(spec, with_trades=False)
        s, b = r["summary"], r["baseline"]
        bt = {k: s.get(k) for k in ("n_complete", "success_rate", "avg_ret", "median_ret", "fill_rate", "avg_days_to_target")}
        bt["baseline_success"] = b.get("success_rate")
        bt["at"] = dt.date.today().isoformat()
        store.paper_set_backtest(bid, bt)
    except Exception:  # noqa: BLE001
        pass


def _book_or_404(bid: int) -> dict:
    b = store.paper_book(bid)
    if not b:
        raise HTTPException(404, "모의 카테고리가 없습니다")
    return b


@app.get("/api/paper/books/{bid}", dependencies=[Depends(auth)])
def paper_book_get(bid: int):
    return _paper_view(_book_or_404(bid))


@app.put("/api/paper/books/{bid}", dependencies=[Depends(auth)])
def paper_book_update(bid: int, body: PaperBookPatch):
    b = _book_or_404(bid)
    d = {k: v for k, v in body.model_dump().items() if v is not None}
    if "name" in d and not d["name"].strip():
        raise HTTPException(422, "이름이 비었습니다")
    if "archived" in d:
        d["archived"] = int(d["archived"])
    if "auto" in d:
        d["auto"] = int(d["auto"])
        if d["auto"] and not b["auto"]:
            d["auto_since"] = _auto_since(b["dataset"], paper.book_spec(b))
            _paper_sync.pop(bid, None)
    store.paper_book_update(bid, d)
    return _paper_view(store.paper_book(bid))


@app.delete("/api/paper/books/{bid}", dependencies=[Depends(auth)])
def paper_book_delete(bid: int):
    _book_or_404(bid)
    store.paper_book_delete(bid)
    return {"ok": True}


@app.post("/api/paper/books/{bid}/cash", dependencies=[Depends(auth)])
def paper_cash_add(bid: int, body: PaperCashIn):
    _book_or_404(bid)
    if body.kind not in ("deposit", "withdraw", "per_trade"):
        raise HTTPException(422, "kind 는 deposit, withdraw, per_trade")
    if body.amount <= 0:
        raise HTTPException(422, "금액은 0보다 커야 합니다")
    date = body.date or dt.datetime.now(paper.KST).date().isoformat()
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(422, f"날짜 형식이 올바르지 않습니다: {date}")
    store.paper_cash_add(bid, date, body.kind, body.amount)
    return _paper_view(store.paper_book(bid))


@app.delete("/api/paper/cash/{cid}", dependencies=[Depends(auth)])
def paper_cash_delete(cid: int):
    store.paper_cash_delete(cid)
    return {"ok": True}


@app.post("/api/paper/books/{bid}/trades", dependencies=[Depends(auth)])
def paper_pick(bid: int, body: PaperPickIn):
    b = _book_or_404(bid)
    runner = loaded.get(b["dataset"])
    _, _, name, market = runner.last_close(body.code)
    if not name:
        raise HTTPException(404, f"{body.code} 종목을 찾을 수 없습니다")
    spec = paper.book_spec(b)
    retro = paper.is_retro(b["dataset"], body.signal_date, spec.entry.type)
    r = store.paper_pick(bid, {"code": body.code, "signal_date": body.signal_date, "name": name, "market": market},
                         retro)
    return {**r, "retro": retro, "book": b["name"]}


@app.put("/api/paper/trades/{tid}", dependencies=[Depends(auth)])
def paper_trade_update(tid: int, body: PaperTradePatch):
    t = store.paper_trade(tid)
    if not t:
        raise HTTPException(404, "거래가 없습니다")
    d: dict = {}
    if body.reopen:
        d["manual_exit_date"] = None
    elif body.manual_exit_date:
        try:
            dt.date.fromisoformat(body.manual_exit_date)
        except ValueError:
            raise HTTPException(422, "날짜 형식이 올바르지 않습니다")
        d["manual_exit_date"] = body.manual_exit_date
    if body.note is not None:
        d["note"] = body.note
    if body.include is not None:
        d["retro"] = 0 if body.include else 1
    store.paper_trade_update(tid, d)
    return _paper_view(store.paper_book(t["book_id"]))


@app.delete("/api/paper/trades/{tid}", dependencies=[Depends(auth)])
def paper_trade_delete(tid: int):
    t = store.paper_trade(tid)
    if not t:
        raise HTTPException(404, "거래가 없습니다")
    store.paper_trade_delete(tid)
    return _paper_view(store.paper_book(t["book_id"]))


@app.get("/api/stocks", dependencies=[Depends(auth)])
def stock_search(q: str, dataset: str = config.DEFAULT_DATASET, limit: int = 20):
    q = q.strip().lower()
    if not q:
        return []
    out = []
    for code, name, market in loaded.get(dataset).stock_list():
        if code.lower().startswith(q) or q in name.lower():
            out.append({"code": code, "name": name, "market": market})
            if len(out) >= limit:
                break
    return out


# ---------------------------------------------------------------- 휴대폰 알림 (웹 푸시)

class PushSubIn(BaseModel):
    subscription: dict
    device: str = ""


class PushEndpointIn(BaseModel):
    endpoint: str


class PushPrefsIn(BaseModel):
    paper: bool = True
    signals: list[int] = []
    signals_empty: bool = False


@app.get("/api/push", dependencies=[Depends(auth)])
def push_info():
    return {"key": push.public_key(), "prefs": push.prefs(store),
            "devices": [{"endpoint": d["endpoint"], "device": d.get("device", ""), "created": d.get("created", "")}
                        for d in store.get_setting("push_subs", [])]}


@app.post("/api/push/subscribe", dependencies=[Depends(auth)])
def push_subscribe(body: PushSubIn):
    try:
        push.add_sub(store, body.subscription, body.device)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return push_info()


@app.post("/api/push/unsubscribe", dependencies=[Depends(auth)])
def push_unsubscribe(body: PushEndpointIn):
    push.remove_sub(store, body.endpoint)
    return push_info()


@app.put("/api/push/prefs", dependencies=[Depends(auth)])
def push_set_prefs(body: PushPrefsIn):
    store.set_setting("push_prefs", body.model_dump())
    return push_info()


@app.post("/api/push/test", dependencies=[Depends(auth)])
def push_test():
    if not store.get_setting("push_subs", []):
        raise HTTPException(409, "알림을 켠 기기가 없습니다. 먼저 '이 기기에서 알림 켜기'를 누르세요.")
    r = push.send(store, "규칙대로", "시험 알림입니다. 이렇게 신호와 모투 소식이 옵니다.", tab="more", tag="test")
    if not r["sent"]:
        raise HTTPException(502, "보내지 못했습니다: " + "; ".join(r["errors"] or ["기기 구독이 끝났습니다. 알림을 다시 켜 주세요."]))
    return r


def _strategy_dataset(spec: Spec) -> str | None:
    """전략의 대상 시장이 들어 있는 데이터셋 (국내·미국). 데모는 다른 데이터가 없을 때만."""
    names = [n for n in DATASETS if n != "demo"] + ["demo"]
    names = [n for n in names if (config.dataset_dir(n) / "prices.parquet").exists()]
    for n in names:
        if set(spec.universe.markets) <= set(DATASETS[n].get("markets", [])):
            return n
    return None


def _kst_now() -> dt.datetime:
    from zoneinfo import ZoneInfo

    return dt.datetime.now(ZoneInfo("Asia/Seoul"))


def _push_tick(now: dt.datetime | None = None) -> list[str]:
    """새 거래일 데이터가 들어오면 (저녁 임시값이든 아침 공식 값이든 먼저 온 쪽) 한 번만 알린다.

    - 저녁 신호: 고른 전략의 그날 신호
    - 모투 소식: 내 선택 종목의 체결·미체결·매도
    밤 11시 ~ 아침 7시에는 보내지 않고 기다린다. 보낸 알림 제목 목록을 돌려준다 (시험용).
    """
    now = now or _kst_now()
    st = store.get_setting("push_state", {"asof": {}, "sent": []})
    has_subs = bool(store.get_setting("push_subs", []))
    pf = push.prefs(store)
    sent_titles: list[str] = []
    changed = False
    for ds in DATASETS:
        try:
            runner = loaded.get(ds)
        except HTTPException:
            continue
        as_of = str(runner.panel.dates.max())
        prev = st["asof"].get(ds)
        if prev == as_of:
            continue
        if prev is None or not has_subs:
            # 처음이거나 알림 받을 기기가 없으면 지금까지의 것은 보낸 것으로 친다
            st["asof"][ds] = as_of
            if pf["paper"]:
                st["sent"] += [push.news_key(it) for it in _paper_news_items(ds)[0]]
            changed = True
            continue
        if not (7 <= now.hour < 23):
            continue
        md = f"{as_of[5:7]}/{as_of[8:10]}"
        results = []
        for sid in pf["signals"]:
            strat = store.get(sid)
            if not strat:
                continue
            try:
                spec = parse_text(strat["text"])
                if _strategy_dataset(spec) != ds:
                    continue
                rows = runner.scan(spec, 1)["rows"]
            except Exception:  # noqa: BLE001 — 전략 하나가 깨져도 나머지는 보낸다
                continue
            results.append((strat["name"], [r for r in rows if r["signal_date"] == as_of]))
        body = push.signal_text(results, pf["signals_empty"])
        if body:
            title = f"{md} 신호 · {DATASETS[ds]['label'].split(' ')[0]}"
            push.send(store, title, body, tab="run", tag=f"signals-{ds}")
            sent_titles.append(title)
        if pf["paper"]:
            done = set(st["sent"])
            fresh = [it for it in _paper_news_items(ds)[0] if push.news_key(it) not in done and it["date"] >= prev]
            if fresh:
                title = f"{md} 모투 소식 {len(fresh)}건"
                push.send(store, title, push.news_text(fresh), tab="paper", tag=f"paper-{ds}")
                sent_titles.append(title)
                st["sent"] += [push.news_key(it) for it in fresh]
        st["asof"][ds] = as_of
        changed = True
    if changed:
        st["sent"] = st["sent"][-2000:]
        store.set_setting("push_state", st)
    return sent_titles


def _push_loop() -> None:
    time.sleep(60)
    while True:
        try:
            _push_tick()
        except Exception as e:  # noqa: BLE001
            print("알림 확인 실패:", e, flush=True)
        time.sleep(180)


if os.environ.get("SBT_PUSH_LOOP", "1") != "0":
    threading.Thread(target=_push_loop, daemon=True, name="push").start()


# ---------------------------------------------------------------- 증권사 연결 (키움, 시세 조회만)

class BrokerKeysIn(BaseModel):
    appkey: str
    secretkey: str


def _broker_view() -> dict:
    from ..broker import kiwoom

    return {"ip": kiwoom.server_ip(), "kiwoom": kiwoom.view()}


@app.get("/api/broker", dependencies=[Depends(auth)])
def broker_info():
    return _broker_view()


@app.put("/api/broker/kiwoom/{mode}", dependencies=[Depends(auth)])
def broker_set_keys(mode: str, body: BrokerKeysIn):
    from ..broker import kiwoom

    try:
        kiwoom.set_keys(mode, body.appkey, body.secretkey)
    except kiwoom.KiwoomError as e:
        raise HTTPException(422, str(e))
    return _broker_view()


@app.delete("/api/broker/kiwoom/{mode}", dependencies=[Depends(auth)])
def broker_clear_keys(mode: str):
    from ..broker import kiwoom

    try:
        kiwoom.clear_keys(mode)
    except kiwoom.KiwoomError as e:
        raise HTTPException(422, str(e))
    return _broker_view()


@app.post("/api/broker/kiwoom/{mode}/test", dependencies=[Depends(auth)])
def broker_test(mode: str):
    from ..broker import kiwoom

    try:
        return kiwoom.test(mode)
    except kiwoom.KiwoomError as e:
        raise HTTPException(502, str(e))


# ---------------------------------------------------------------- 실시간 시세 (키움, 화면용)
_live_cache: dict = {}
_live_lock = threading.Lock()


def _live(key: str, ttl: float, fn):
    """같은 요청은 ttl 초 동안 저장해 둔 값을 준다 (여러 화면이 동시에 열려도 키움 호출은 한 번)."""
    with _live_lock:
        hit = _live_cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    v = fn()
    with _live_lock:
        _live_cache[key] = (time.time(), v)
        if len(_live_cache) > 500:
            for k in sorted(_live_cache, key=lambda k: _live_cache[k][0])[:250]:
                _live_cache.pop(k, None)
    return v


def _live_mode() -> str:
    from ..broker import kiwoom

    v = kiwoom.view()
    if v.get("real", {}).get("saved"):
        return "real"
    if v.get("mock", {}).get("saved"):
        return "mock"
    raise HTTPException(409, "키움 키가 없습니다 (설정 → 키움 연결)")


def _live_call(fn):
    from ..broker import kiwoom

    try:
        return fn()
    except kiwoom.KiwoomError as e:
        raise HTTPException(502, str(e))


@app.get("/api/live/quote", dependencies=[Depends(auth)])
def live_quote(codes: str):
    from ..broker import kiwoom

    cs = sorted({c.strip() for c in codes.split(",") if c.strip().isalnum()})[:50]
    if not cs:
        return {}
    mode = _live_mode()
    return _live_call(lambda: _live("q:" + ",".join(cs), 10, lambda: kiwoom.quotes_full(mode, cs)))


@app.get("/api/live/index", dependencies=[Depends(auth)])
def live_index():
    from ..broker import kiwoom

    mode = _live_mode()
    return _live_call(lambda: _live("index", 15, lambda: kiwoom.index_quotes(mode)))


@app.get("/api/live/minute", dependencies=[Depends(auth)])
def live_minute(code: str, scope: int = 5):
    from ..broker import kiwoom

    if not code.isalnum():
        raise HTTPException(422, "종목코드가 올바르지 않습니다")
    mode = _live_mode()
    return {"bars": _live_call(lambda: _live(f"m:{code}:{scope}", 20, lambda: kiwoom.minute_bars(mode, code, scope)))}


# ---------------------------------------------------------------- 자동 매매 (키움 모의투자)

def _auto():
    global _AUTO
    if _AUTO is None:
        from .. import autotrade
        from ..broker import kiwoom

        _AUTO = autotrade.Auto(store, kiwoom, loaded.get,
                               lambda title, body: push.send(store, title, body, tab="auto"), parse_text)
    return _AUTO


_AUTO = None


class AutoSettingsIn(BaseModel):
    enabled: bool | None = None
    mode: str | None = None
    strategy_id: int | None = None
    per_trade: float | None = None
    daily_max: int | None = None


class AutoApproveIn(BaseModel):
    approve: bool


class AutoDateIn(BaseModel):
    trade_date: str


class AutoStrategyIn(BaseModel):
    enabled: bool | None = None
    per_trade: float | None = None
    daily_max: int | None = None
    color: str | None = None
    size_mode: str | None = None
    pct: float | None = None
    max_per_trade: float | None = None


@app.get("/api/auto", dependencies=[Depends(auth)])
def auto_view():
    return _auto().view(_kst_now().date().isoformat())


@app.get("/api/auto/compare", dependencies=[Depends(auth)])
def auto_compare():
    """전략마다 모의투자 실제 vs 백테스트 기대치."""
    return {"items": _auto().compare(), "cost_pct": _auto().COST_PCT}


@app.put("/api/auto/strategies/{sid}", dependencies=[Depends(auth)])
def auto_strategy_put(sid: int, body: AutoStrategyIn):
    """전략 추가(없으면) 또는 설정 변경. 계좌 돈은 모든 전략이 같이 쓴다."""
    a = _auto()
    now = _kst_now().replace(tzinfo=None)
    was = {x["id"]: x.get("enabled", True) for x in a.settings()["strategies"]}
    try:
        x = a.set_strategy(sid, **body.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e))
    a.log(now, f"전략 설정 #{sid}: " + ", ".join(f"{k}={v}" for k, v in body.model_dump().items() if v is not None))
    # 새로 넣었거나 다시 켠 전략은 다음 데이터를 기다리지 않고 바로 후보를 만든다 (승인 마감 전일 때만)
    if x.get("enabled", True) and not was.get(sid, False) and a.settings()["enabled"]:
        try:
            with a.lock:
                a.make_plans(now, only={sid})
        except Exception as e:  # noqa: BLE001
            a.log(now, f"⚠ 후보 만들기 실패: {e}")
    return auto_view()


@app.delete("/api/auto/strategies/{sid}", dependencies=[Depends(auth)])
def auto_strategy_delete(sid: int):
    """자동 매매에서 뺀다. 이미 산 종목은 계속 규칙대로 판다."""
    a = _auto()
    a.remove_strategy(sid)
    a.log(_kst_now().replace(tzinfo=None), f"전략 빼기 #{sid}")
    return auto_view()


@app.put("/api/auto/settings", dependencies=[Depends(auth)])
def auto_settings(body: AutoSettingsIn):
    try:
        _auto().set_settings(**body.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e))
    a = _auto()
    a.log(_kst_now().replace(tzinfo=None), "설정 변경: " + ", ".join(f"{k}={v}" for k, v in body.model_dump().items() if v is not None))
    return auto_view()


@app.post("/api/auto/plans/{pid}", dependencies=[Depends(auth)])
def auto_approve(pid: int, body: AutoApproveIn):
    try:
        _auto().approve(pid, body.approve, _kst_now().replace(tzinfo=None))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return auto_view()


@app.post("/api/auto/approve-all", dependencies=[Depends(auth)])
def auto_approve_all(body: AutoDateIn):
    try:
        _auto().approve_all(body.trade_date, _kst_now().replace(tzinfo=None))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return auto_view()


@app.post("/api/auto/positions/{pid}/sell", dependencies=[Depends(auth)])
def auto_manual_sell(pid: int):
    try:
        _auto().manual_sell(pid, _kst_now().replace(tzinfo=None))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return auto_view()


@app.post("/api/auto/plan-now", dependencies=[Depends(auth)])
def auto_plan_now():
    """지금 데이터로 다음 거래일 후보를 다시 만든다 (시험용: 주말에도 됨)."""
    try:
        rows = _auto().make_plans(_kst_now().replace(tzinfo=None), force=True)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"count": len(rows), **auto_view()}


@app.get("/api/auto/account", dependencies=[Depends(auth)])
def auto_account():
    from ..broker import kiwoom

    try:
        return {"cash": kiwoom.cash("mock"), "balance": kiwoom.balance("mock")}
    except kiwoom.KiwoomError as e:
        raise HTTPException(502, str(e))


# ---------------------------------------------------------------- 장중 실시간 감시 (알림만)
_WATCH = None


def _watch():
    global _WATCH
    if _WATCH is None:
        from .. import watch
        from ..broker import kiwoom

        _WATCH = watch.Watch(store, kiwoom, loaded.get,
                             lambda title, body: push.send(store, title, body, tab="auto"), parse_text)
    return _WATCH


class WatchSettingsIn(BaseModel):
    strategy_ids: list[int]


@app.get("/api/watch", dependencies=[Depends(auth)])
def watch_view():
    return _watch().view(_kst_now().replace(tzinfo=None))


@app.put("/api/watch/settings", dependencies=[Depends(auth)])
def watch_settings(body: WatchSettingsIn):
    _watch().set_settings(body.strategy_ids)
    return _watch().view(_kst_now().replace(tzinfo=None))


def _auto_loop() -> None:
    time.sleep(30)
    while True:
        try:
            _watch().tick(_kst_now().replace(tzinfo=None))
        except Exception as e:  # noqa: BLE001
            print("실시간 감시 실패:", e, flush=True)
        try:
            _auto().tick(_kst_now().replace(tzinfo=None))
        except Exception as e:  # noqa: BLE001
            print("자동 매매 확인 실패:", e, flush=True)
            try:
                _auto().log(_kst_now().replace(tzinfo=None), f"⚠ 오류: {e}")
            except Exception:  # noqa: BLE001
                pass
        time.sleep(20)


if os.environ.get("SBT_AUTO_LOOP", "1") != "0":
    threading.Thread(target=_auto_loop, daemon=True, name="autotrade").start()


# ---------------------------------------------------------------- 제작 노트 (설정 탭)
_NOTES = Path(__file__).resolve().parents[3] / "docs" / "notes.html"


@app.get("/api/notes", dependencies=[Depends(auth)])
def get_notes():
    # 정적 파일로 두면 비밀번호 없이 열리므로 로그인한 사람에게만 API 로 준다
    if not _NOTES.exists():
        raise HTTPException(404, "제작 노트 파일이 없습니다")
    return {"html": _NOTES.read_text(encoding="utf-8")}


# ---------------------------------------------------------------- 웹 화면 (정적 파일)
# web/ 를 'next build' 하면 생기는 out/ 폴더가 있으면 같은 주소에서 함께 서비스한다.
_WEB_DIR = Path(os.environ.get("SBT_WEB_DIR", Path(__file__).resolve().parents[3] / "web" / "out"))
if _WEB_DIR.exists():
    from fastapi.staticfiles import StaticFiles

    @app.middleware("http")
    async def _web_cache(request, call_next):
        # 화면(HTML)은 매번 새로 확인하게 해서 업데이트가 바로 보이게 하고, 이름에 해시가 붙은 파일은 오래 캐시한다
        resp = await call_next(request)
        path = request.url.path
        if path.startswith("/_next/static/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif not path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")
