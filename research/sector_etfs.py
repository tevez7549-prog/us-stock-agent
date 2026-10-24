"""งานวิจัย 2026-10-24 (7): หมุนกลุ่มธุรกิจด้วย ETF (คำขอเจ้าของ) — กติกากำหนดก่อนรัน
ชุด A = Select Sector SPDR (เฉพาะหุ้นใน S&P 500), B = Vanguard sector (หุ้นทั้งตลาด),
    C = ผสม: ต่อกลุ่มเลือกตัวที่โมเมนตัมดีกว่าระหว่าง A กับ B (ถือได้กลุ่มละ 1 ตัว)
กติกา: EW = ถือทุกกลุ่มเท่ากัน, TOP3 = 3 กลุ่มที่คะแนนสูงสุด (เฉลี่ยผลตอบแทน 3/6/12 เดือน),
    TOP3T = TOP3 แต่กลุ่มที่ราคาต่ำกว่าเส้น 200 วัน → ถือ SHY แทน
ความถี่ปรับพอร์ต: ทุก 1 / 3 / 6 เดือน (สัญญาณจากราคาปิดวันทำการสุดท้ายของรอบ, เทรดราคาเปิดวันถัดไป)
ค่าธรรมเนียม config.COSTS ต่อด้าน, ETF ที่ยังไม่เกิดถือว่าไม่มีให้เลือก (XLRE 2015, XLC 2018)
ผ่าน = Sharpe ≥ SPY และ DD เล็กกว่าชัดเจน ทั้งช่วงหาสูตรและช่วงทดสอบซ้ำ; holdout ปิดไว้
ผล: D1 'research_sector_etfs'"""
import json

import numpy as np
import pandas as pd

import config
import paper
from agents import backtest_agent
from research.random_baseline import periods

SECTORS = ["tech", "health", "financial", "energy", "consumer", "staples", "industrial", "materials", "utilities", "realestate", "comm"]
A = dict(zip(SECTORS, ["XLK", "XLV", "XLF", "XLE", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC"]))
B = dict(zip(SECTORS, ["VGT", "VHT", "VFH", "VDE", "VCR", "VDC", "VIS", "VAW", "VPU", "VNQ", "VOX"]))
FEE = config.COSTS["commission_pct"] + config.COSTS["slippage_pct"]
START = "2011-01-01"


def download():
    import yfinance as yf
    tk = list(A.values()) + list(B.values()) + ["SPY", "SHY"]
    raw = yf.download(tk, start=config.DATA_START, auto_adjust=True, progress=False, group_by="column", threads=True)
    return raw["Close"][tk], raw["Open"][tk]


def rebalance_days(idx, months):
    s = pd.Series(idx, index=idx)
    last = s.groupby(idx.to_period("M")).max()
    last = [d for d in last if d >= pd.Timestamp(START) and d != idx[-1]]
    return {d for d in last if (d.month - 1) % months == months - 1 or months == 1}


def score(C):
    r = lambda m: C / C.shift(21 * m) - 1
    return (r(3) + r(6) + r(12)) / 3


def targets(date, fam, rule, C, S, sma):
    cand = {}
    for g in SECTORS:
        opts = [f[g] for f in ({"A": [A], "B": [B], "C": [A, B]}[fam])]
        opts = [t for t in opts if S.at[date, t] == S.at[date, t]]
        if opts:
            cand[g] = max(opts, key=lambda t: S.at[date, t])
    if not cand:
        return {"SHY": 1.0}
    if rule == "EW":
        return {t: 1 / len(cand) for t in cand.values()}
    top = sorted(cand.values(), key=lambda t: -S.at[date, t])[:3]
    w = {}
    for t in top:
        k = "SHY" if rule == "TOP3T" and C.at[date, t] < sma.at[date, t] else t
        w[k] = w.get(k, 0) + 1 / 3
    return w


def simulate(C, O, fam, rule, months, cap=10000.0):
    S, sma = score(C), C.rolling(200).mean()
    idx = C.index[C.index >= pd.Timestamp(START)]
    rdays = rebalance_days(C.index, months)
    cash, sh, pending = cap * (1 - config.COSTS["fx_spread_pct"]), {}, None
    eq, turnover = [], 0.0
    for d in idx:
        if pending is not None:
            px = {t: (O.at[d, t] if O.at[d, t] == O.at[d, t] else C.shift(1).at[d, t]) for t in set(sh) | set(pending)}
            val = cash + sum(n * px[t] for t, n in sh.items())
            for t in sorted(set(sh) | set(pending), key=lambda t: pending.get(t, 0) * val - sh.get(t, 0) * px[t]):
                delta = pending.get(t, 0) * val - sh.get(t, 0) * px[t]
                if abs(delta) < 1:
                    continue
                if delta > 0:
                    delta = min(delta, cash / (1 + FEE))
                sh[t] = sh.get(t, 0) + delta / px[t]
                cash -= delta + abs(delta) * FEE
                turnover += abs(delta) / val / 2
            sh = {t: n for t, n in sh.items() if n > 1e-9}
            pending = None
        eq.append(cash + sum(n * C.at[d, t] for t, n in sh.items() if C.at[d, t] == C.at[d, t]))
        if d in rdays:
            pending = targets(d, fam, rule, C, S, sma)
    e = pd.Series(eq, index=idx)
    yrs = (idx[-1] - idx[0]).days / 365.25
    return e, round(turnover / yrs * 100, 1)


def main():
    C, O = download() if paper.remote() else synthetic()
    out = {"created": paper.now_th(), "variants": {}}
    for fam in "ABC":
        for rule in ("EW", "TOP3", "TOP3T"):
            for m in (1, 3, 6):
                e, to = simulate(C, O, fam, rule, m)
                per = periods(e)
                yr = e.resample("YE").last()
                yr = pd.concat([e.iloc[:1], yr])
                name = f"{fam}-{rule}-{m}m"
                out["variants"][name] = {"turnover": to, "search": per["search"], "validate": per["validate"],
                                         "all": per["all"], "holdout_sealed": per["holdout"],
                                         "by_year": {int(d.year): round(float(yr.iloc[k] / yr.iloc[k - 1] - 1) * 100, 2) for k, d in enumerate(yr.index) if k}}
                print(name, json.dumps(per["search"]), json.dumps(per["validate"]), flush=True)
    spy = C["SPY"].loc[C.index >= pd.Timestamp(START)]
    sp = periods(spy / spy.iloc[0] * 10000)
    out["spy"] = {"search": sp["search"], "validate": sp["validate"], "all": sp["all"], "holdout_sealed": sp["holdout"]}
    paper.put("research_sector_etfs", out, shared=True)


def synthetic():
    idx = pd.bdate_range("2010-01-01", "2026-10-23")
    rng = np.random.default_rng(3)
    tk = list(A.values()) + list(B.values()) + ["SPY", "SHY"]
    C = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(3e-4, 0.012, (len(idx), len(tk))), 0)), idx, tk)
    C.loc[:"2015-10-07", "XLRE"] = np.nan
    C.loc[:"2018-06-18", "XLC"] = np.nan
    return C, C * (1 + rng.normal(0, 0.002, C.shape))


if __name__ == "__main__":
    main()
