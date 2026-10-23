"""งานวิจัย 2026-10-23 (6): Bollinger, Parabolic SAR, Ichimoku, Fibonacci (คำขอเจ้าของ, กำหนดก่อนรัน)
กติการ่วม: เช็คทุกศุกร์ (สัญญาณแบบเหตุการณ์นับถ้าเกิดใน 5 วันทำการล่าสุด), เทรดราคาเปิดถัดไป, ถือสูงสุด 10 ตัว เท่ากัน
× exposure สภาวะตลาด, เกิน 10 ตัวเลือกผันผวนต่ำก่อน, ซื้อหุ้นได้หลังเข้า S&P 500, Risk Manager + ค่าธรรมเนียมเหมือนระบบจริง
BB1 ย่อในขาขึ้น: เหนือ SMA200 + ปิดต่ำกว่า BB ล่าง (20,2) → ออกเมื่อปิดเหนือเส้นกลาง
BB2 ทะลุขึ้น: ปิดเหนือ BB บน (20,2) → ออกเมื่อปิดต่ำกว่าเส้นกลาง
SAR Parabolic SAR (0.02, 0.2) พลิกเป็นขาขึ้น → ออกเมื่อพลิกเป็นขาลง
ICH ปิดเหนือเมฆ + Tenkan(9) > Kijun(26) → ออกเมื่อปิดต่ำกว่า Kijun
FIB เหนือ SMA200 + ราคาย่อมาอยู่ระหว่าง 50%–61.8% ของช่วงสวิง 60 วัน (ต่ำสุด → สูงสุด) → ออกเมื่อปิดเหนือจุดสูงสุดเดิม
    (เป้า) หรือต่ำกว่า 78.6% (ตัดขาดทุน)
ผล: D1 'research_classic' (holdout ปิด เปิดเฉพาะแบบที่ผ่าน)"""
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

X = {}


def highs_lows(d):
    """ราคาสูง/ต่ำรายวัน (ปรับปันผล/แตกพาร์) จาก Yahoo ให้ตรงกับตาราง close เดิม"""
    import yfinance as yf
    cols = list(d["close"].columns)
    hs, ls = [], []
    for i in range(0, len(cols), 100):
        raw = yf.download(cols[i:i + 100], start=config.DATA_START, auto_adjust=True, progress=False, group_by="column", threads=True)
        h, l = raw["High"], raw["Low"]
        if isinstance(h, pd.Series):
            h, l = h.to_frame(cols[i]), l.to_frame(cols[i])
        hs.append(h)
        ls.append(l)
    H = pd.concat(hs, axis=1).reindex(index=d["close"].index, columns=cols)
    L = pd.concat(ls, axis=1).reindex(index=d["close"].index, columns=cols)
    return H.fillna(d["close"]), L.fillna(d["close"])


def psar(h, l, af0=0.02, step=0.02, amax=0.2):
    """คืน +1 ขาขึ้น / -1 ขาลง ต่อวัน (คำนวณย้อนหลังล้วน)"""
    n = len(h)
    trend = np.zeros(n)
    if n < 3 or np.isnan(h[:2]).any():
        return trend
    up, sar, ep, af = True, l[0], h[0], af0
    for i in range(1, n):
        if np.isnan(h[i]) or np.isnan(l[i]):
            trend[i] = 1 if up else -1
            continue
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
            if l[i] < sar:
                up, sar, ep, af = False, ep, l[i], af0
            elif h[i] > ep:
                ep, af = h[i], min(af + step, amax)
        else:
            sar = max(sar, h[i - 1], h[i - 2] if i > 1 else h[i - 1])
            if h[i] > sar:
                up, sar, ep, af = True, ep, h[i], af0
            elif l[i] < ep:
                ep, af = l[i], min(af + step, amax)
        trend[i] = 1 if up else -1
    return trend


def prepare(d, H, L):
    c = d["close"].astype("float64")
    sma200 = c.rolling(200).mean()
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    X["bb1_in"] = ((c > sma200) & (c < mid - 2 * sd)).astype(float).rolling(5, min_periods=1).max()
    X["bb1_out"] = (c > mid).astype(float)
    X["bb2_in"] = (c > mid + 2 * sd).astype(float).rolling(5, min_periods=1).max()
    X["bb2_out"] = (c < mid).astype(float)
    tr = pd.DataFrame({t: psar(H[t].to_numpy(), L[t].to_numpy()) for t in c.columns}, index=c.index)
    X["sar_in"] = ((tr > 0) & (tr.shift(1) < 0)).astype(float).rolling(5, min_periods=1).max()
    X["sar_out"] = (tr < 0).astype(float)
    tenkan = (H.rolling(9).max() + L.rolling(9).min()) / 2
    kijun = (H.rolling(26).max() + L.rolling(26).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(26)
    span_b = ((H.rolling(52).max() + L.rolling(52).min()) / 2).shift(26)
    X["ich_in"] = ((c > np.maximum(span_a, span_b)) & (tenkan > kijun)).astype(float)
    X["ich_out"] = (c < kijun).astype(float)
    hi60, lo60 = c.rolling(60).max(), c.rolling(60).min()
    rng = hi60 - lo60
    X["fib_in"] = ((c > sma200) & (c <= hi60 - 0.5 * rng) & (c >= hi60 - 0.618 * rng) & (rng > 0)).astype(float)
    X["fib_hi"], X["fib_stop"] = hi60, hi60 - 0.786 * rng


class Rule:
    def __init__(self, name, n=10):
        self.name, self.n, self.entry_ref = name, n, {}

    def propose(self, close, ind, date, regime, p=config.STRATEGY, held=()):
        px, vol = close.loc[date], ind["vol"].loc[date]
        stocks = [t for t, m in config.UNIVERSE.items() if m["kind"] == "stock" and t in close.columns and px[t] == px[t] and vol[t] == vol[t]]
        g = lambda k: X[k].loc[date]
        if self.name == "fib":
            keep = []
            for t in held:
                if t not in stocks:
                    continue
                ref = self.entry_ref.get(t)
                if ref and (px[t] > ref[0] or px[t] < ref[1]):
                    self.entry_ref.pop(t, None)
                    continue
                keep.append(t)
            new = [t for t in stocks if t not in keep and g("fib_in").get(t, 0) >= 1]
        else:
            keep = [t for t in held if t in stocks and g(f"{self.name}_out").get(t, 1) < 1]
            new = [t for t in stocks if t not in keep and g(f"{self.name}_in").get(t, 0) >= 1]
        add = sorted(new, key=lambda t: vol[t])[: max(0, self.n - len(keep))]
        if self.name == "fib":
            for t in add:
                self.entry_ref[t] = (float(X["fib_hi"].loc[date, t]), float(X["fib_stop"].loc[date, t]))
        picks = keep[: self.n] + add
        w = {t: regime["exposure"] / self.n for t in picks}
        w[config.CASH_PROXY] = w.get(config.CASH_PROXY, 0) + max(0.0, 1 - sum(w.values()))
        return {"weights": {t: round(float(v), 4) for t, v in w.items() if v > 1e-4}, "why": {}, "ranking": []}


def main():
    for r in csv.DictReader(io.StringIO(open(config.SP500_FILE, encoding="utf-8").read())):
        ADDED[config.yf_symbol(r["Symbol"])] = (r.get("Date added") or "")[:10]
    if paper.remote():
        d, src = data_agent.load("yfinance", allow_synthetic=False)
        H, L = highs_lows(d)
    else:
        d, src = data_agent.clean(data_agent.synthetic(seed=5)), "synthetic"
        H, L = d["close"] * 1.01, d["close"] * 0.99
    prepare(d, H, L)
    out = {"created": paper.now_th(), "source": src, "variants": {}}
    spy = None
    for name in ("bb1", "bb2", "sar", "ich", "fib"):
        res = backtest_agent.run(d, strategy_module=PIT(Rule(name)), cadence="weekly", portfolio="momentum")
        per = periods(res["equity"])
        spy = spy or periods(res["bench"])
        out["variants"][name] = {"turnover": res["stats"]["turnover_per_year"], "trades": res["stats"]["trades"],
                                 "by_year": {y["year"]: y["ret"] for y in res["stats"]["by_year"]},
                                 "search": per["search"], "validate": per["validate"], "holdout_sealed": per["holdout"]}
        print(name, json.dumps(per["search"]), json.dumps(per["validate"]), flush=True)
    out["spy"] = {"search": spy["search"], "validate": spy["validate"], "holdout_sealed": spy["holdout"]}
    paper.put("research_classic", out, shared=True)


if __name__ == "__main__":
    main()
