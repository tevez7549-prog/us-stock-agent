"""งานวิจัย 2026-10-23 (3): หุ้นคุณภาพ จากงบการเงิน SEC แบบไม่รู้อนาคต (point-in-time)

ข้อมูล: data.sec.gov/api/xbrl/companyfacts ของแต่ละบริษัท (CIK จาก universe/sp500.csv)
- ใช้เฉพาะงบปี (10-K, ช่วง 330–400 วัน) และใช้ได้ตั้งแต่วันที่ "ยื่น" (filed) ครั้งแรกเท่านั้น
- ณ วันส่งสัญญาณ: งบปีล่าสุดที่ยื่นแล้ว และปีงบจบไม่เกิน 550 วันก่อน
คะแนนคุณภาพ = ค่าเฉลี่ยเปอร์เซ็นไทล์ของ (1) กำไรขั้นต้น/สินทรัพย์ (2) กำไรสุทธิ/สินทรัพย์ (3) −หนี้สิน/สินทรัพย์
แบบที่ทดสอบ (กำหนดก่อนรัน, รายเดือน, ซื้อได้หลังเข้า S&P 500): Q1 top20, Q2 top20 + เหนือ SMA200,
Q3 คุณภาพ 25% บน → โมเมนตัมสูงสุด 20, Q4 top40 (แผนเดิม 50 แต่ 2%/ตัวไม่ถึงขั้นต่ำการเทรด) · holdout ปิดไว้ เปิดเฉพาะแบบที่ผ่าน · ผล: D1 'research_quality'
"""
import json
import time
import urllib.request
from datetime import date as _date

import numpy as np
import pandas as pd

import config
import paper
from agents import backtest_agent, data_agent
from research.lowvol_monthly import PIT, ADDED
from research.random_baseline import periods
import research.lowvol_monthly as lv

DUR = {"gp": ["GrossProfit"], "rev": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
       "RevenueFromContractWithCustomerIncludingAssessedTax"], "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
       "ni": ["NetIncomeLoss", "ProfitLoss"]}
INST = {"assets": ["Assets"], "liab": ["Liabilities"]}


def _get(url, tries=4):
    for k in range(tries):
        try:
            time.sleep(0.12)   # SEC: ไม่เกิน 10 ครั้ง/วินาที
            req = urllib.request.Request(url, headers={"user-agent": config.SEC_USER_AGENT, "accept-encoding": "identity"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 * (k + 1))
        except Exception:
            time.sleep(2 * (k + 1))
    return None


def _annual(facts, names, duration):
    """{วันจบปีงบ: (มูลค่า, วันยื่นครั้งแรก)} ของ concept แรกที่มีข้อมูล"""
    best = {}
    for name in names:
        units = (facts.get(name) or {}).get("units", {}).get("USD") or []
        for f in units:
            if f.get("form") not in ("10-K", "10-K/A") or "end" not in f or "filed" not in f:
                continue
            if duration:
                if "start" not in f:
                    continue
                days = (_date.fromisoformat(f["end"]) - _date.fromisoformat(f["start"])).days
                if not 330 <= days <= 400:
                    continue
            cur = best.get(f["end"])
            if cur is None or f["filed"] < cur[1]:
                best[f["end"]] = (float(f["val"]), f["filed"])
        if best:
            break
    return best


def company_records(cik):
    j = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json")
    if not j:
        return []
    g = (j.get("facts") or {}).get("us-gaap") or {}
    parts = {k: _annual(g, v, True) for k, v in DUR.items()} | {k: _annual(g, v, False) for k, v in INST.items()}
    rows = []
    for end in sorted(parts["assets"]):
        a = parts["assets"][end][0]
        if a <= 0:
            continue
        rec = {"end": end, "filed": parts["assets"][end][1]}
        ni = parts["ni"].get(end)
        if ni is None:
            continue
        rec["roa"] = ni[0] / a
        rec["filed"] = max(rec["filed"], ni[1])
        gp = parts["gp"].get(end)
        if gp is None and end in parts["rev"] and end in parts["cogs"]:
            gp = (parts["rev"][end][0] - parts["cogs"][end][0], max(parts["rev"][end][1], parts["cogs"][end][1]))
        if gp is not None:
            rec["gpa"] = gp[0] / a
            rec["filed"] = max(rec["filed"], gp[1])
        lb = parts["liab"].get(end)
        if lb is not None:
            rec["lev"] = lb[0] / a
            rec["filed"] = max(rec["filed"], lb[1])
        rows.append(rec)
    return rows


FUND = {}   # ticker → DataFrame(end, filed, roa, gpa, lev)


def snapshot(date):
    """ตัวเลขคุณภาพของทุกตัว ณ วันนั้น (เฉพาะงบที่ยื่นแล้ว)"""
    d = str(date.date())
    lim = str((date - pd.Timedelta(days=550)).date())
    out = {}
    for t, df in FUND.items():
        ok = df[(df["filed"] <= d) & (df["end"] >= lim)]
        if len(ok):
            out[t] = ok.iloc[-1]
    if not out:
        return pd.DataFrame()
    snap = pd.DataFrame(out).T
    ranks = pd.DataFrame({"gpa": snap.get("gpa").astype(float).rank(pct=True) if "gpa" in snap else np.nan,
                          "roa": snap["roa"].astype(float).rank(pct=True),
                          "lev": -snap["lev"].astype(float).rank(pct=True) + 1 if "lev" in snap else np.nan})
    snap["score"] = ranks.mean(axis=1, skipna=True)
    return snap


class Quality:
    def __init__(self, n, mode):
        self.n, self.mode = n, mode

    def propose(self, close, ind, date, regime, p=config.STRATEGY, held=()):
        snap = snapshot(date)
        px, sma, mom = close.loc[date], ind["sma"].loc[date], ind["mom"].loc[date]
        cand = [t for t in snap.index if t in config.UNIVERSE and t in close.columns and px[t] == px[t]]
        sc = snap.loc[cand, "score"].astype(float).sort_values(ascending=False) if cand else pd.Series(dtype=float)
        if self.mode == "trend":
            sc = sc[[px[t] > sma[t] for t in sc.index]] if len(sc) else sc
            picks = list(sc.index[: self.n])
        elif self.mode == "qm":
            top = [t for t in sc.index[: max(self.n, len(sc) // 4)] if mom[t] == mom[t]]
            picks = sorted(top, key=lambda t: -mom[t])[: self.n]
        else:
            picks = list(sc.index[: self.n])
        w = {t: regime["exposure"] * (len(picks) / self.n) / len(picks) for t in picks} if picks else {}
        w[config.CASH_PROXY] = w.get(config.CASH_PROXY, 0) + max(0.0, 1 - sum(w.values()))
        return {"weights": {t: round(float(v), 4) for t, v in w.items() if v > 1e-4}, "why": {}, "ranking": []}


def main():
    import csv, io
    for r in csv.DictReader(io.StringIO(open(config.SP500_FILE, encoding="utf-8").read())):
        ADDED[config.yf_symbol(r["Symbol"])] = (r.get("Date added") or "")[:10]
    remote = paper.remote()
    d, src = data_agent.load("yfinance", allow_synthetic=False) if remote else (data_agent.clean(data_agent.synthetic(seed=5)), "synthetic")
    stocks = [(t, m.get("cik")) for t, m in config.UNIVERSE.items() if m["kind"] == "stock"]
    t0, miss = time.time(), []
    rng = np.random.default_rng(1)
    for t, cik in stocks:
        if remote:
            rows = company_records(cik) if cik else []
        else:   # ทดสอบโค้ดในเครื่อง: งบจำลอง
            rows = [{"end": f"{y}-12-31", "filed": f"{y + 1}-02-20", "roa": rng.normal(.05, .05), "gpa": rng.normal(.3, .1),
                     "lev": rng.uniform(.2, .8)} for y in range(2009, 2026)]
        if rows:
            FUND[t] = pd.DataFrame(rows)
        else:
            miss.append(t)
    print(f"fundamentals: {len(FUND)} companies, missing {len(miss)}, {time.time() - t0:.0f}s", flush=True)
    grid = {"Q1": Quality(20, "q"), "Q2": Quality(20, "trend"), "Q3": Quality(20, "qm"), "Q4": Quality(40, "q")}   # 50 ตัว = 2% ต่อตัว ไม่ถึงขั้นต่ำการเทรด (min_trade_weight 2%) จึงใช้ 40
    out = {"created": paper.now_th(), "data_last": str(d["close"].index[-1].date()), "source": src,
           "companies": len(FUND), "missing": miss[:40], "variants": {}}
    spy = None
    for name, mod in grid.items():
        res = backtest_agent.run(d, strategy_module=PIT(mod), cadence="monthly", portfolio="momentum")
        per = periods(res["equity"])
        spy = spy or periods(res["bench"])
        last = res["weights_log"][-1]["weights"] if res["weights_log"] else {}
        out["variants"][name] = {"turnover": res["stats"]["turnover_per_year"], "search": per["search"], "validate": per["validate"],
                                 "holdout_sealed": per["holdout"], "last_picks": sorted(last, key=lambda t: -last[t])[:20]}
        print(name, json.dumps(per["search"]), json.dumps(per["validate"]), flush=True)
    out["spy"] = {"search": spy["search"], "validate": spy["validate"], "holdout_sealed": spy["holdout"]}
    paper.put("research_quality", out, shared=True)


if __name__ == "__main__":
    main()
