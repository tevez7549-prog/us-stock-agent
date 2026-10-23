"""งานวิจัย 2026-10-23 (5): ระบบเหมาะกับหุ้นกลุ่มไหน (กำหนดก่อนรัน)
แต่ละกลุ่ม GICS 11 กลุ่ม: MOM = Momentum 7 ตัว (รายสัปดาห์), EMA = EMA20>50>100>200 ถือสูงสุด 7 ตัว (รายสัปดาห์)
เฉพาะหุ้นในกลุ่ม + ซื้อได้หลังเข้า S&P 500, Risk Manager ใช้เพดานกลุ่ม 100% (เพราะทั้งพอร์ตอยู่กลุ่มเดียว) หุ้นละ ≤15%
เทียบกับ EW = ถือหุ้นทุกตัวในกลุ่มเท่ากัน (ปรับทุกวัน ไม่มีค่าธรรมเนียม) และ SPY
ผ่าน = ทั้ง search และ validate: Sharpe ≥ EW ของกลุ่มนั้น และ ≥ SPY · เชื่อเฉพาะกลุ่มที่ผ่านทั้ง 2 ระบบ
ผล: D1 'research_sectors' (holdout ปิดไว้)"""
import csv
import io
import json

import numpy as np
import pandas as pd

import config
import paper
from agents import backtest_agent, data_agent
from strategies import momentum
from research.lowvol_monthly import ADDED
from research.random_baseline import periods
from research import trend_signals as ts


class Sector:
    """ซ่อนทุกอย่างนอกกลุ่ม (เหลือหุ้นในกลุ่มที่เข้าดัชนีแล้ว + สินทรัพย์ป้องกัน/เงินสด) แล้วเรียกกลยุทธ์ข้างใน"""

    def __init__(self, group, inner):
        self.group, self.inner = group, inner

    def propose(self, close, ind, date, regime, p=config.STRATEGY, held=()):
        d = str(date.date())
        hide = [t for t, m in config.UNIVERSE.items()
                if (m["kind"] == "stock" and (m["group"] != self.group or ADDED.get(t, "") > d)) or m["kind"] == "etf"]
        saved = {t: config.UNIVERSE.pop(t) for t in hide}
        try:
            return self.inner.propose(close, ind, date, regime, p, held)
        finally:
            config.UNIVERSE.update(saved)


def ew_index(close, group):
    """หุ้นทุกตัวในกลุ่ม (ที่เข้าดัชนีแล้ว ณ วันนั้น) น้ำหนักเท่ากัน ปรับทุกวัน"""
    cols = [t for t, m in config.UNIVERSE.items() if m["kind"] == "stock" and m["group"] == group and t in close.columns]
    r = close[cols].pct_change(fill_method=None)
    added = pd.Series({t: ADDED.get(t, "") for t in cols})
    mask = pd.DataFrame({t: close.index.strftime("%Y-%m-%d") >= added[t] for t in cols}, index=close.index)
    r = r.where(mask)
    return (1 + r.mean(axis=1, skipna=True).fillna(0)).cumprod(), len(cols)


def main():
    for r in csv.DictReader(io.StringIO(open(config.SP500_FILE, encoding="utf-8").read())):
        ADDED[config.yf_symbol(r["Symbol"])] = (r.get("Date added") or "")[:10]
    d, src = data_agent.load("yfinance", allow_synthetic=False) if paper.remote() else (data_agent.clean(data_agent.synthetic(seed=5)), "synthetic")
    ts.prepare(d["close"])
    groups = sorted({m["group"] for m in config.UNIVERSE.values() if m["kind"] == "stock"})
    out = {"created": paper.now_th(), "source": src, "groups": {}}
    spy = None
    for g in groups:
        ew, n = ew_index(d["close"], g)
        row = {"stocks": n, "EW": {k: v for k, v in periods(ew.loc["2011-01-01":]).items() if k != "holdout"}}
        row["EW_holdout_sealed"] = periods(ew.loc["2011-01-01":])["holdout"]
        for name, mod, strat in (("MOM", momentum, {"top_n": 7}), ("EMA", ts.Signals("e1", n=7), {})):
            res = backtest_agent.run(d, strategy=strat, strategy_module=Sector(g, mod), cadence="weekly", portfolio="dual_momentum")
            per = periods(res["equity"])
            spy = spy or periods(res["bench"])
            row[name] = {"turnover": res["stats"]["turnover_per_year"], "search": per["search"], "validate": per["validate"],
                         "holdout_sealed": per["holdout"]}
        ok = lambda s, k: (row[s][k].get("sharpe") or -9) >= max(row["EW"][k].get("sharpe") or 9, spy[k].get("sharpe") or 9)
        row["pass"] = {s: ok(s, "search") and ok(s, "validate") for s in ("MOM", "EMA")}
        out["groups"][g] = row
        print(g, n, json.dumps(row["pass"]), json.dumps({s: (row[s]["search"].get("sharpe"), row[s]["validate"].get("sharpe")) for s in ("MOM", "EMA")}),
              "EW", row["EW"]["search"].get("sharpe"), row["EW"]["validate"].get("sharpe"), flush=True)
    out["spy"] = {"search": spy["search"], "validate": spy["validate"], "holdout_sealed": spy["holdout"]}
    paper.put("research_sectors", out, shared=True)


if __name__ == "__main__":
    main()
