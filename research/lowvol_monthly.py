"""งานวิจัย 2026-10-23 (2): ผันผวนต่ำ + Momentum รายเดือน — ทุกแบบซื้อหุ้นได้เฉพาะหลังวันที่เข้า S&P 500 (PIT)
แบบที่ทดสอบ (กำหนดก่อนรัน): LV1 ผันผวนต่ำ 10 ตัว + เหนือ SMA200, LV2 = 20 ตัว, LV3 = 20 ตัวไม่กรองแนวโน้ม,
MM1 Momentum 6 ตัวรายเดือน, MM2 Momentum 12 ตัวรายเดือน · สภาวะตลาด/Risk Manager/ค่าธรรมเนียม เหมือนระบบจริง
ช่วง holdout ถูกเก็บแยก (`holdout_sealed`) — รายงานเฉพาะแบบที่ผ่าน search + validate
ผลเก็บใน D1 shared state 'research_lowvol_monthly'"""
import csv
import io
import json

import numpy as np

import config
import paper
from agents import backtest_agent, data_agent
from strategies import momentum
from research.random_baseline import periods

ADDED = {}


class PIT:
    """ซ่อนหุ้นที่ยังไม่เข้าดัชนี ณ วันนั้น แล้วเรียกกลยุทธ์ข้างใน"""

    def __init__(self, inner):
        self.inner = inner

    def propose(self, close, ind, date, regime, p=config.STRATEGY, held=()):
        d = str(date.date())
        hide = [t for t, m in config.UNIVERSE.items() if m["kind"] == "stock" and ADDED.get(t, "") > d]
        saved = {t: config.UNIVERSE.pop(t) for t in hide}
        try:
            return self.inner.propose(close, ind, date, regime, p, held)
        finally:
            config.UNIVERSE.update(saved)


class LowVol:
    """หุ้นที่ความผันผวน 1 ปีต่ำสุด n ตัว น้ำหนักเท่ากัน × exposure ของสภาวะตลาด ส่วนที่เหลือ = สินทรัพย์ป้องกัน/เงินสด (เหมือน momentum)"""

    def __init__(self, n, trend):
        self.n, self.trend = n, trend

    def propose(self, close, ind, date, regime, p=config.STRATEGY, held=()):
        i = close.index.get_loc(date)
        win = close.iloc[max(0, i - 252): i + 1]
        vol1y = win.pct_change(fill_method=None).std() * np.sqrt(252)
        px, sma = close.loc[date], ind["sma"].loc[date]
        cand = [t for t, m in config.UNIVERSE.items() if m["kind"] == "stock" and t in close.columns
                and win[t].notna().sum() > 240 and vol1y[t] == vol1y[t] and px[t] == px[t] and sma[t] == sma[t]
                and (not self.trend or px[t] > sma[t])]
        picks = sorted(cand, key=lambda t: vol1y[t])[: self.n]
        w = {t: regime["exposure"] * (len(picks) / self.n) / len(picks) for t in picks} if picks else {}
        w[config.CASH_PROXY] = w.get(config.CASH_PROXY, 0) + max(0.0, 1 - sum(w.values()))
        return {"weights": {t: round(float(v), 4) for t, v in w.items() if v > 1e-4}, "why": {}, "ranking": []}


def main():
    text = open(config.SP500_FILE, encoding="utf-8").read()
    for r in csv.DictReader(io.StringIO(text)):
        ADDED[config.yf_symbol(r["Symbol"])] = (r.get("Date added") or "")[:10]
    d, src = data_agent.load("yfinance", allow_synthetic=False) if paper.remote() else (data_agent.clean(data_agent.synthetic(seed=5)), "synthetic")
    grid = {"LV1": (PIT(LowVol(10, True)), {}), "LV2": (PIT(LowVol(20, True)), {}), "LV3": (PIT(LowVol(20, False)), {}),
            "MM1": (PIT(momentum), {"top_n": 6}), "MM2": (PIT(momentum), {"top_n": 12})}
    out = {"created": paper.now_th(), "data_last": str(d["close"].index[-1].date()), "source": src, "variants": {}}
    spy = None
    for name, (mod, strat) in grid.items():
        res = backtest_agent.run(d, strategy=strat, strategy_module=mod, cadence="monthly", portfolio="momentum")
        per = periods(res["equity"])
        spy = spy or periods(res["bench"])
        out["variants"][name] = {"turnover": res["stats"]["turnover_per_year"], "search": per["search"], "validate": per["validate"],
                                 "holdout_sealed": per["holdout"], "all_ex_holdout": backtest_agent._block(res["equity"].loc[:"2024-10-22"])}
        print(name, json.dumps(per["search"]), json.dumps(per["validate"]), flush=True)
    out["spy"] = {"search": spy["search"], "validate": spy["validate"], "holdout_sealed": spy["holdout"]}
    paper.put("research_lowvol_monthly", out, shared=True)


if __name__ == "__main__":
    main()
