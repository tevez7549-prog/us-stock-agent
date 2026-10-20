"""หน้าเว็บ: python server.py แล้วเปิด http://localhost:8000"""
import json
import os
import threading
from typing import List, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

import config  # noqa: E402
import orchestrator  # noqa: E402
import paper  # noqa: E402
from agents import backtest_agent  # noqa: E402

app = FastAPI(title="Invest Agent")
LOCK = threading.Lock()
_DATA = {"d": None}


def data(refresh=False, source="auto"):
    if refresh or _DATA["d"] is None:
        _DATA["d"] = orchestrator.load_data(source)
    return _DATA["d"]


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))


@app.get("/api/status")
def status():
    paper.init()
    d = data()
    px = d["close"].iloc[-1]
    pos, cash = paper.positions(), paper.get("cash")
    c = paper.conn()
    eq = [dict(r) for r in c.execute("SELECT * FROM equity ORDER BY date")]
    total = cash + sum(p["shares"] * px[t] for t, p in pos.items())
    rows = []
    for t, p in sorted(pos.items(), key=lambda kv: -kv[1]["shares"] * px[kv[0]]):
        v = p["shares"] * px[t]
        rows.append({"ticker": t, "kind": config.UNIVERSE[t]["kind"], "group": config.UNIVERSE[t]["group"],
                     "shares": round(p["shares"], 4), "price": round(float(px[t]), 2), "value": round(float(v), 2),
                     "weight": round(float(v / total), 4), "cost": round(p["cost_usd"], 2),
                     "pnl_pct": round(float(v / p["cost_usd"] - 1) * 100, 2) if p["cost_usd"] else None})
    last_pkt = c.execute("SELECT id, signal_date, status FROM packets ORDER BY id DESC LIMIT 1").fetchone()
    peak = max((e["usd"] for e in eq), default=total)
    return {
        "paused": paper.get("paused"), "cash": round(cash, 2), "total_usd": round(float(total), 2),
        "fx": round(float(d["fx"].iloc[-1]), 3), "capital": paper.get("capital"),
        "drawdown": round(float(1 - total / peak), 4) if peak else 0,
        "last_data": str(d["close"].index[-1].date()), "source": d.get("source"),
        "last_marked": paper.get("last_marked"), "regime": paper.get("prev_regime"),
        "positions": rows, "equity": eq, "last_packet": dict(last_pkt) if last_pkt else None,
        "pending": c.execute("SELECT COUNT(*) n FROM orders WHERE status IN ('proposed','held')").fetchone()["n"],
        "approved": c.execute("SELECT COUNT(*) n FROM orders WHERE status='approved'").fetchone()["n"],
    }


@app.get("/api/packets")
def packets():
    c = paper.conn()
    return [dict(r) for r in c.execute("SELECT id, created, signal_date, status FROM packets ORDER BY id DESC LIMIT 60")]


@app.get("/api/packets/{pid}")
def packet(pid: int):
    c = paper.conn()
    r = c.execute("SELECT * FROM packets WHERE id=?", (pid,)).fetchone()
    if not r:
        raise HTTPException(404, "ไม่พบแพ็กเกจ")
    out = dict(r)
    out["data"], out["audit"] = json.loads(out["data"]), json.loads(out["audit"])
    out["orders"] = [dict(o) for o in c.execute("SELECT * FROM orders WHERE packet_id=? ORDER BY side DESC, usd DESC", (pid,))]
    return out


class Decision(BaseModel):
    ids: List[int]
    decision: str


@app.post("/api/orders/decide")
def decide(req: Decision):
    if req.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision ต้องเป็น approve หรือ reject")
    if paper.get("paused"):
        raise HTTPException(400, "ระบบอยู่ในโหมดพัก กด 'ทำงานต่อ' ก่อน")
    with LOCK:
        n = orchestrator.decide(req.ids, req.decision)
    return {"updated": n}


class RunReq(BaseModel):
    mode: str = "daily"
    source: str = "auto"


@app.post("/api/run")
def run(req: RunReq):
    with LOCK:
        d = data(refresh=True, source=req.source)
        if req.mode == "weekly":
            return {"packet": orchestrator.weekly(d), "source": d.get("source")}
        orchestrator.daily(d)
        return {"ok": True, "source": d.get("source")}


@app.post("/api/kill")
def kill():
    with LOCK:
        return {"cancelled": orchestrator.kill()}


@app.post("/api/resume")
def resume():
    orchestrator.resume()
    return {"ok": True}


@app.post("/api/simulate")
def simulate():
    """ล้างพอร์ตจำลอง แล้วเดินระบบย้อนหลัง 52 สัปดาห์ (ไว้ดูว่าระบบทำงานยังไง)"""
    with LOCK:
        d = data()
        orchestrator.simulate(d, weeks=52)
    return {"ok": True}


class BtReq(BaseModel):
    source: str = "auto"
    top_n: Optional[int] = None
    trend_sma: Optional[int] = None
    mom_short: Optional[int] = None
    mom_long: Optional[int] = None
    robustness: bool = False


@app.post("/api/backtest")
def backtest(req: BtReq):
    d = data(source=req.source) if req.source != "synthetic" else orchestrator.load_data("synthetic")
    params = {k: v for k, v in req.model_dump().items() if k in config.STRATEGY and v is not None}
    res = backtest_agent.run(d, strategy=params)
    wk = lambda s: [{"time": str(i.date()), "value": round(float(v), 2)} for i, v in s.resample("W-FRI").last().dropna().items()]
    out = {"source": d.get("source"), "stats": res["stats"], "params": res["params"],
           "equity": wk(res["equity"]), "bench": wk(res["bench"]),
           "last_weights": res["weights_log"][-1] if res["weights_log"] else None}
    if req.robustness:
        out["robustness"] = backtest_agent.robustness(d)
    return out


@app.get("/api/universe")
def universe():
    return {t: m["kind"] for t, m in config.UNIVERSE.items()}


@app.get("/api/journal")
def journal(limit: int = 200):
    c = paper.conn()
    return [dict(r) for r in c.execute("SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,))]


app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")

if __name__ == "__main__":
    import uvicorn
    print("เปิดเบราว์เซอร์ที่ http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)
