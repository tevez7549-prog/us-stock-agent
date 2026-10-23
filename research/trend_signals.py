"""งานวิจัย 2026-10-23 (4): Donchian + EMA 20/50/100/200 + RSI + Stochastic (กำหนดก่อนรัน)

ทุกแบบ: สัญญาณเช็คทุกศุกร์ (สัญญาณที่เกิดใน 5 วันทำการล่าสุดนับ), เทรดราคาเปิดถัดไป, ถือสูงสุด 10 ตัว เท่ากัน
× exposure ของสภาวะตลาด, เกิน 10 ตัวเลือกผันผวนต่ำก่อน, ซื้อหุ้นได้หลังเข้า S&P 500, Risk Manager + ค่าธรรมเนียมเหมือนระบบจริง
ข้อมูลมีแค่ราคาเปิด/ปิด → Donchian และ Stochastic ใช้ราคาปิด (ไม่มี high/low รายวัน)
DC1 เข้า 55/ออก 20 · DC2 100/50 · DC3 20/10 · DC4 SPY ตัวเดียว 55/20 (นอกเทรนด์ = SHY, ไม่ใช้ตัวกรองสภาวะตลาด)
E1 EMA20>50>100>200 · E2 + RSI14<40 · E3 + Stoch %K ตัดขึ้น %D จาก <20 · E4 E2+E3 · ออก E*: EMA20 < EMA50
ผล: D1 'research_trend_signals' (holdout ปิด เปิดเฉพาะแบบที่ผ่าน search + validate)"""
import csv
import io
import json

import numpy as np
import pandas as pd

import config
import paper
from agents import backtest_agent, data_agent
from research.lowvol_monthly import PIT, ADDED
from research.random_baseline import periods

S = {}   # ตัวชี้วัดที่คำนวณล่วงหน้า (rolling ย้อนหลังล้วน ไม่มีข้อมูลอนาคต)


def prepare(close):
    c = close.astype("float32")
    for n in (20, 50, 100, 200):
        S[f"ema{n}"] = c.ewm(span=n, adjust=False, min_periods=n).mean()
    diff = c.diff()
    up, dn = diff.clip(lower=0), -diff.clip(upper=0)
    rs = up.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / dn.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    S["rsi"] = 100 - 100 / (1 + rs)
    lo, hi = c.rolling(14).min(), c.rolling(14).max()
    k = ((c - lo) / (hi - lo).replace(0, np.nan) * 100).rolling(3).mean()
    d = k.rolling(3).mean()
    S["stoch_x"] = ((k.shift(1) <= d.shift(1)) & (k > d) & (k.shift(1) < 20)).astype("float32")
    S["rsi_lo"] = (S["rsi"] < 40).astype("float32")
    for n in (10, 20, 50, 55, 100):
        S[f"hi{n}"] = (c >= c.rolling(n).max()).astype("float32")             # ปิดที่จุดสูงสุด n วัน (ทะลุกรอบบน)
        S[f"lo{n}"] = (c <= c.rolling(n).min()).astype("float32")             # ปิดที่จุดต่ำสุด n วัน (หลุดกรอบล่าง)
    for key in [k for k in S if k.startswith(("hi", "lo", "stoch_x", "rsi_lo"))]:
        S[key + "_wk"] = S[key].rolling(5, min_periods=1).max()               # เกิดใน 5 วันทำการล่าสุด
    S["trend"] = ((S["ema20"] > S["ema50"]) & (S["ema50"] > S["ema100"]) & (S["ema100"] > S["ema200"])).astype("float32")


def _weights(picks, regime, n, use_regime=True):
    exp = regime["exposure"] if use_regime else 1.0
    w = {t: exp / n for t in picks}
    w[config.CASH_PROXY] = w.get(config.CASH_PROXY, 0) + max(0.0, 1 - sum(w.values()))
    return {"weights": {t: round(float(v), 4) for t, v in w.items() if v > 1e-4}, "why": {}, "ranking": []}


class Signals:
    def __init__(self, kind, entry=None, exit_=None, n=10):
        self.kind, self.entry, self.exit_, self.n = kind, entry, exit_, n

    def _row(self, key, date):
        return S[key].loc[date]

    def propose(self, close, ind, date, regime, p=config.STRATEGY, held=()):
        px, vol = close.loc[date], ind["vol"].loc[date]
        stocks = [t for t, m in config.UNIVERSE.items() if m["kind"] == "stock" and t in close.columns and px[t] == px[t] and vol[t] == vol[t]]
        if self.kind == "donchian":
            hi, lo = self._row(f"hi{self.entry}_wk", date), self._row(f"lo{self.exit_}_wk", date)
            keep = [t for t in held if t in stocks and lo.get(t, 1) < 1]
            new = [t for t in stocks if t not in keep and hi.get(t, 0) >= 1]
        else:
            trend, ex = self._row("trend", date), (S["ema20"].loc[date] < S["ema50"].loc[date])
            keep = [t for t in held if t in stocks and not ex.get(t, True)]
            ok = lambda t: trend.get(t, 0) >= 1
            if self.kind == "e2":
                ok = lambda t, f=ok: f(t) and self._row("rsi_lo_wk", date).get(t, 0) >= 1
            elif self.kind == "e3":
                ok = lambda t, f=ok: f(t) and self._row("stoch_x_wk", date).get(t, 0) >= 1
            elif self.kind == "e4":
                ok = lambda t, f=ok: f(t) and self._row("rsi_lo_wk", date).get(t, 0) >= 1 and self._row("stoch_x_wk", date).get(t, 0) >= 1
            new = [t for t in stocks if t not in keep and ok(t)]
        picks = keep[: self.n] + sorted(new, key=lambda t: vol[t])[: max(0, self.n - len(keep))]
        return _weights(picks, regime, self.n)


class SpyDonchian:
    def propose(self, close, ind, date, regime, p=config.STRATEGY, held=()):
        inn = "SPY" in held
        if inn and S["lo20_wk"].loc[date].get("SPY", 0) >= 1:
            inn = False
        elif not inn and S["hi55_wk"].loc[date].get("SPY", 0) >= 1:
            inn = True
        w = {"SPY": 0.95} if inn else {}
        w[config.CASH_PROXY] = 1 - sum(w.values())
        return {"weights": w, "why": {}, "ranking": []}


def main():
    for r in csv.DictReader(io.StringIO(open(config.SP500_FILE, encoding="utf-8").read())):
        ADDED[config.yf_symbol(r["Symbol"])] = (r.get("Date added") or "")[:10]
    d, src = data_agent.load("yfinance", allow_synthetic=False) if paper.remote() else (data_agent.clean(data_agent.synthetic(seed=5)), "synthetic")
    prepare(d["close"])
    grid = {"DC1": (PIT(Signals("donchian", 55, 20)), "momentum"), "DC2": (PIT(Signals("donchian", 100, 50)), "momentum"),
            "DC3": (PIT(Signals("donchian", 20, 10)), "momentum"), "DC4": (SpyDonchian(), "dual_momentum"),
            "E1": (PIT(Signals("e1")), "momentum"), "E2": (PIT(Signals("e2")), "momentum"),
            "E3": (PIT(Signals("e3")), "momentum"), "E4": (PIT(Signals("e4")), "momentum")}
    out = {"created": paper.now_th(), "data_last": str(d["close"].index[-1].date()), "source": src, "variants": {}}
    spy = None
    for name, (mod, port) in grid.items():
        res = backtest_agent.run(d, strategy_module=mod, cadence="weekly", portfolio=port)
        per = periods(res["equity"])
        spy = spy or periods(res["bench"])
        held = [x for x in res["weights_log"] if any(config.info(t)["kind"] == "stock" or t == "SPY" for t in x["weights"])]
        out["variants"][name] = {"turnover": res["stats"]["turnover_per_year"], "trades": res["stats"]["trades"],
                                 "invested_weeks_pct": round(100 * len(held) / max(len(res["weights_log"]), 1), 1),
                                 "search": per["search"], "validate": per["validate"], "holdout_sealed": per["holdout"]}
        print(name, json.dumps(per["search"]), json.dumps(per["validate"]), flush=True)
    out["spy"] = {"search": spy["search"], "validate": spy["validate"], "holdout_sealed": spy["holdout"]}
    paper.put("research_trend_signals", out, shared=True)


if __name__ == "__main__":
    main()
