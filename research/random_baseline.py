"""งานวิจัย 2026-10-23: Momentum ชนะ "สุ่มเลือกหุ้น" จากรายชื่อเดียวกันไหม (แยกผลของกลยุทธ์ออกจากผลของรายชื่อ S&P 500 ปัจจุบัน)

แบบที่ทดสอบ (กำหนดไว้ก่อนรัน):
  A  ถือ SPY เฉยๆ
  B  Momentum ตัวจริง (S&P 500 + ETF)
  C1 สุ่ม 6 ตัวจากหุ้น/ETF ทั้งหมดที่มีข้อมูล (100 seed)
  C2 สุ่ม 6 ตัวจากตัวที่ผ่านเกณฑ์ Momentum (โมเมนตัม > 0 และเหนือ SMA200) แต่ไม่จัดอันดับ (100 seed)
  D  Momentum เฉพาะ ETF (ไม่มีปัญหา survivorship)
ทุกแบบ: สัญญาณศุกร์ เทรดราคาเปิดถัดไป, ถือตัวเดิมต่อถ้ายังผ่านเกณฑ์ (แทน buffer อันดับ), น้ำหนัก 1/ความผันผวน,
สภาวะตลาด, Risk Manager, ค่าธรรมเนียม config.COSTS เหมือน backtest_agent.run ทุกอย่าง
ผลเก็บใน D1 shared state 'research_random_baseline' (บนคลาวด์) — ห้ามใช้ข้อมูลจำลองสรุปผล
"""
import json
import os
import sys
from multiprocessing import Pool

import numpy as np

import config
import paper
from agents import backtest_agent, data_agent
from strategies import momentum

PERIODS = {"search": ("2011-01-01", "2019-12-31"), "validate": ("2020-01-01", "2024-10-22"), "holdout": ("2024-10-23", "2100-01-01")}
SEEDS = int(os.environ.get("SEEDS", 100))


class RandomPick:
    """เหมือน momentum.propose ทุกอย่าง ยกเว้น "เลือกตัวไหน" = สุ่ม"""

    def __init__(self, seed, filtered):
        self.rng, self.filtered = np.random.default_rng(seed), filtered

    def propose(self, close, ind, date, regime, p=config.STRATEGY, held=()):
        px, sma, mom, vol = (x.loc[date] for x in (close, ind["sma"], ind["mom"], ind["vol"]))
        ok = lambda t: all(v == v for v in (px[t], sma[t], mom[t], vol[t])) and vol[t] > 0
        risky = sorted(t for t, m in config.UNIVERSE.items() if m["kind"] in ("etf", "stock") and t in close.columns and ok(t))
        cand = [t for t in risky if mom[t] > 0 and px[t] > sma[t]] if self.filtered else risky
        keep = [t for t in held if t in cand][: p["top_n"]]
        rest = [t for t in cand if t not in keep]
        n = min(p["top_n"] - len(keep), len(rest))
        picks = keep + (list(self.rng.choice(rest, n, replace=False)) if n > 0 else [])
        w = {}
        exposure = regime["exposure"] * (len(picks) / p["top_n"])
        if picks:
            inv = {t: 1 / vol[t] for t in picks}
            s = sum(inv.values())
            w = {t: exposure * inv[t] / s for t in picks}
        rest_w = 1 - sum(w.values())
        defensive = [t for t, m in config.UNIVERSE.items() if m["kind"] == "defensive" and t in close.columns and ok(t)]
        good_def = sorted([t for t in defensive if mom[t] > 0 and px[t] > sma[t]], key=lambda t: mom[t], reverse=True)[:2]
        for t in good_def:
            w[t] = rest_w * 0.5
        w[config.CASH_PROXY] = w.get(config.CASH_PROXY, 0) + max(0.0, 1 - sum(w.values()))
        return {"weights": {t: round(float(v), 4) for t, v in w.items() if v > 1e-4}, "why": {}, "ranking": []}


def periods(eq):
    out = {}
    for k, (a, b) in PERIODS.items():
        s = eq.loc[a:b]
        out[k] = backtest_agent._block(s) if len(s) > 30 else {}
    out["all"] = backtest_agent._block(eq)
    return out


D = None


def run_random(args):
    seed, filtered = args
    res = backtest_agent.run(D, strategy_module=RandomPick(seed, filtered), cadence="weekly", portfolio="momentum")
    return {"seed": seed, "turnover": res["stats"]["turnover_per_year"], **periods(res["equity"])}


def summarize(rows):
    out = {}
    for k in list(PERIODS) + ["all"]:
        vals = {m: [r[k].get(m) for r in rows if r[k].get(m) is not None] for m in ("cagr", "sharpe", "max_dd")}
        out[k] = {m: {"median": round(float(np.median(v)), 2), "p10": round(float(np.percentile(v, 10)), 2),
                      "p90": round(float(np.percentile(v, 90)), 2)} for m, v in vals.items() if v}
    out["turnover_median"] = round(float(np.median([r["turnover"] for r in rows])), 1)
    return out


def main():
    global D
    if not paper.remote() and os.environ.get("ALLOW_SYNTHETIC") != "1":
        sys.exit("ต้องรันบนคลาวด์ (GitHub Actions) เพื่อใช้ราคาจริง")
    if paper.remote():
        D, src = data_agent.load("yfinance", allow_synthetic=False)
    else:   # ทดสอบโค้ดในเครื่องเท่านั้น ห้ามใช้สรุปผล
        D, src = data_agent.clean(data_agent.synthetic(seed=5)), "synthetic"
    D["source"] = src
    result = {"created": paper.now_th(), "data_last": str(D["close"].index[-1].date()), "periods": PERIODS, "seeds": SEEDS,
              "stocks": sum(1 for m in config.UNIVERSE.values() if m["kind"] == "stock")}
    mom = backtest_agent.run(D, strategy_module=momentum, cadence="weekly", portfolio="momentum")
    result["A_spy"] = periods(mom["bench"])
    result["B_momentum"] = {"turnover": mom["stats"]["turnover_per_year"], **periods(mom["equity"])}
    print("B done", json.dumps(result["B_momentum"]["all"]), flush=True)
    import research.random_baseline as mod   # ให้ pickle หาเจอแม้รันเป็น __main__ (python -m)
    mod.D = D
    with Pool(os.cpu_count() or 2) as pool:
        c1 = pool.map(mod.run_random, [(s, False) for s in range(SEEDS)])
        print("C1 done", flush=True)
        c2 = pool.map(mod.run_random, [(1000 + s, True) for s in range(SEEDS)])
        print("C2 done", flush=True)
    result["C1_random_all"] = summarize(c1)
    result["C2_random_filtered"] = summarize(c2)
    # Momentum เทียบกับการแจกแจงของการสุ่ม: กี่ % ของ seed ที่ชนะ Momentum (ต่อช่วง)
    for name, rows in (("C1", c1), ("C2", c2)):
        result[f"{name}_beats_B_pct"] = {k: round(100 * float(np.mean([(r[k].get("cagr") or -99) >= (result["B_momentum"][k].get("cagr") or 99) for r in rows])), 1)
                                         for k in list(PERIODS) + ["all"]}
    snap = dict(config.UNIVERSE)
    config.set_stocks([])   # D: เฉพาะ ETF
    etf = backtest_agent.run(D, strategy_module=momentum, cadence="weekly", portfolio="momentum")
    config.UNIVERSE.clear()
    config.UNIVERSE.update(snap)
    result["D_momentum_etf_only"] = {"turnover": etf["stats"]["turnover_per_year"], **periods(etf["equity"])}
    print(json.dumps(result, ensure_ascii=False, indent=1))
    paper.put("research_random_baseline", result, shared=True)


if __name__ == "__main__":
    main()
