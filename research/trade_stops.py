"""งานวิจัย 2026-10-24 (9): กฎ G1 ของ forex-agent (Donchian + ATR stop + เสี่ยง 1%/ไม้) บนหุ้น S&P 500 รายวัน long-only
(คำขอเจ้าของ จากเอกสาร "สรุปกฎทุกระบบ forex-agent") — กริดกำหนดก่อนรัน

ตัวจำลองใหม่: ระบบต่อไม้ (ไม่ใช่น้ำหนักพอร์ต) ตรวจทุกวันที่ปิด เข้า/ออกที่ราคาเปิดวันถัดไป
  เข้า: ปิด > High สูงสุด N วันก่อนหน้า (ไม่รวมวันนี้) + หุ้น > SMA200 + SPY > SMA200 + หุ้นอยู่ใน S&P 500 แล้ว (PIT)
        ถือ 1 ไม้ต่อหุ้น · ถือพร้อมกันไม่เกิน 20 ไม้ (วันที่มีสัญญาณเกินโควตา เลือกตัวที่ปิดเหนือ Donchian เป็น % มากสุด)
  ขนาด: จำนวนหุ้น = (ทุน × 1%) ÷ (3 × ATR14 ของวันสัญญาณ) ปัดลงเป็นจำนวนเต็ม, ไม่เกินเงินสดที่มี
  stop: เริ่ม = เข้า − 3 ATR · trailing = ปิด − k×ATR ของวันนั้น เลื่อนทางเดียว · เช็คกับ Low ของวัน
        ถ้า Open กระโดดต่ำกว่า stop → ออกที่ Open (ขาดทุนเกิน 1R ได้) ไม่งั้นออกที่ราคา stop
  S1 N=55 trail 5 · S2 N=20 trail 5 · S3 N=55 trail 6
  S4 N=55, SL 3 ATR ไม่เลื่อน, TP = จุดสูงสุด 250 วันก่อนหน้า (ถ้าอยู่เหนือราคาเข้า ≥ 1 ATR) ไม่งั้น TP = เข้า + 2×SL
  R  เข้าสุ่ม (ใช้โควตา/ขนาด/กฎออกของ S1 ทุกอย่าง) 20 seed — เอกสาร forex ใช้วิธีนี้แยก edge ของจุดเข้าออกจากกฎออก
ค่าธรรมเนียม config.COSTS ต่อด้าน · ทุน $10,000 · เทียบ SPY · ผ่าน = Sharpe ≥ SPY และ DD เล็กกว่าชัดเจน ทั้ง search และ validate
และชนะตัวสุ่ม ≥ 95% · holdout ปิดไว้ · ผล: D1 'research_trade_stops' (ยังมี survivorship เหมือนทุกรอบ)"""
import csv
import io
import json
import os

import numpy as np
import pandas as pd

import config
import paper
from agents import backtest_agent, data_agent
from research.classic_indicators import highs_lows
from research.lowvol_monthly import ADDED
from research.random_baseline import periods, PERIODS

RISK, MAXPOS, CAP = 0.01, 20, 10000.0
FEE = config.COSTS["commission_pct"] + config.COSTS["slippage_pct"]
SEEDS = int(os.environ.get("SEEDS", 20))


def atr14(H, L, C):
    pc = C.shift(1)
    tr = pd.concat([H - L, (H - pc).abs(), (L - pc).abs()]).groupby(level=0).max()
    return tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()


def simulate(C, O, H, L, A, spy_ok, entry_ok, trail=5.0, tp_mode=None, start="2011-01-01", rng=None, weight=None, maxpos=MAXPOS):
    """entry_ok: DataFrame bool (วันสัญญาณ) · คืน equity รายวัน + รายการไม้"""
    tickers = list(C.columns)
    Cn, On, Hn, Ln, An = (x.to_numpy() for x in (C, O, H, L, A))
    E = entry_ok.to_numpy()
    hi250 = H.shift(1).rolling(250).max().to_numpy()
    dn = H.shift(1).rolling(55).max().to_numpy()
    idx = C.index
    i0 = idx.get_loc(idx[idx >= pd.Timestamp(start)][0])
    cash, eq, trades, pos = CAP * (1 - config.COSTS["fx_spread_pct"]), [], [], {}
    pending = []
    for i in range(i0, len(idx)):
        # 1) เปิดไม้ที่สัญญาณเมื่อวาน ที่ราคาเปิดวันนี้
        for j, sl_dist, tp in pending:
            px = On[i, j]
            if px != px or len(pos) >= maxpos or j in pos:
                continue
            equity_now = cash + sum(p["sh"] * (Cn[i - 1, k] if Cn[i - 1, k] == Cn[i - 1, k] else p["px"]) for k, p in pos.items())
            sh = int(equity_now * weight // (px * (1 + FEE))) if weight else int(equity_now * RISK // sl_dist)   # weight = แบ่งทุนเท่ากัน (แนวพอร์ตเดิม)
            cost = sh * px * (1 + FEE)
            if sh <= 0 or cost > cash:
                sh = int(cash / (px * (1 + FEE)))
                if sh <= 0:
                    continue
            cash -= sh * px * (1 + FEE)
            pos[j] = {"sh": sh, "px": px, "stop": px - sl_dist, "risk": sh * sl_dist, "tp": tp, "i": i}
        pending = []
        # 2) เช็ค stop/TP ภายในวัน
        for j in list(pos):
            p = pos[j]
            o, h, l, c = On[i, j], Hn[i, j], Ln[i, j], Cn[i, j]
            if c != c:
                continue
            exit_px = None
            if o == o and o <= p["stop"]:
                exit_px = o                                   # gap ข้าม stop → ออกที่ราคาเปิด
            elif l == l and l <= p["stop"]:
                exit_px = p["stop"]
            elif p["tp"] is not None and h == h and h >= p["tp"]:
                exit_px = max(o, p["tp"]) if o == o else p["tp"]
            if exit_px is not None:
                cash += p["sh"] * exit_px * (1 - FEE)
                trades.append({"t": tickers[j], "in": str(idx[p["i"]].date()), "out": str(idx[i].date()),
                               "R": round((exit_px - p["px"]) * p["sh"] / p["risk"] - 2 * FEE * p["px"] * p["sh"] / p["risk"], 3)})
                del pos[j]
            elif p["tp"] is None and An[i, j] == An[i, j]:    # trailing เลื่อนทางเดียว
                p["stop"] = max(p["stop"], c - trail * An[i, j])
        # 3) มูลค่าพอร์ตตอนปิด
        equity = cash + sum(p["sh"] * Cn[i, k] for k, p in pos.items() if Cn[i, k] == Cn[i, k])
        eq.append(equity)
        # 4) สัญญาณวันนี้ → เปิดพรุ่งนี้
        if not spy_ok[i]:
            continue
        cands = [j for j in np.nonzero(E[i])[0] if j not in pos and An[i, j] == An[i, j] and An[i, j] > 0]
        room = maxpos - len(pos)
        if room <= 0 or not cands:
            continue
        if rng is not None:
            cands = list(rng.choice(cands, min(room, len(cands)), replace=False))
        else:
            cands = sorted(cands, key=lambda j: -(Cn[i, j] / dn[i, j] - 1) if dn[i, j] == dn[i, j] else 0)[:room]
        for j in cands:
            sl = 3 * An[i, j]
            tp = None
            if tp_mode:
                r = hi250[i, j]
                tp = r if r == r and r >= Cn[i, j] + An[i, j] else Cn[i, j] + 2 * sl
            pending.append((j, sl, tp))
    e = pd.Series(eq, index=idx[i0:])
    R = [t["R"] for t in trades]
    yrs = max((e.index[-1] - e.index[0]).days / 365.25, 1e-9)
    stats = {"trades": len(R), "per_year": round(len(R) / yrs, 1), "win_rate": round(float(np.mean([r > 0 for r in R])), 3) if R else None,
             "avg_R": round(float(np.mean(R)), 3) if R else None,
             "pf": round(float(sum(r for r in R if r > 0) / max(1e-9, -sum(r for r in R if r < 0))), 2) if R else None,
             "worst_R": round(float(min(R)), 2) if R else None}
    for k, (a, b) in PERIODS.items():
        rr = [t["R"] for t in trades if a <= t["out"] <= b]
        stats[f"pf_{k}"] = round(float(sum(r for r in rr if r > 0) / max(1e-9, -sum(r for r in rr if r < 0))), 2) if rr else None
        stats[f"n_{k}"] = len(rr)
    return e, stats, trades


def main():
    for r in csv.DictReader(io.StringIO(open(config.SP500_FILE, encoding="utf-8").read())):
        ADDED[config.yf_symbol(r["Symbol"])] = (r.get("Date added") or "")[:10]
    if paper.remote():
        d, src = data_agent.load("yfinance", allow_synthetic=False)
        H, L = highs_lows(d)
    else:
        d, src = data_agent.clean(data_agent.synthetic(seed=5)), "synthetic"
        rng0 = np.random.default_rng(0)
        H = d["close"] * (1 + np.abs(rng0.normal(0, 0.01, d["close"].shape)))
        L = d["close"] * (1 - np.abs(rng0.normal(0, 0.01, d["close"].shape)))
    stocks = [t for t, m in config.UNIVERSE.items() if m["kind"] == "stock" and t in d["close"].columns]
    C, O = d["close"][stocks].astype(float), d["open"][stocks].astype(float)
    H, L = H[stocks].astype(float).fillna(C), L[stocks].astype(float).fillna(C)
    A = atr14(H, L, C)
    sma = C.rolling(200).mean()
    spy = d["close"][config.BENCHMARK]
    spy_ok = (spy > spy.rolling(200).mean()).to_numpy()
    pit = pd.DataFrame({t: C.index >= pd.Timestamp(ADDED.get(t) or "1900-01-01") for t in stocks}, index=C.index)
    base = (C > sma) & pit & C.notna()
    brk = {n: (C > H.shift(1).rolling(n).max()) & base for n in (20, 55)}
    out = {"created": paper.now_th(), "source": src, "data_last": str(C.index[-1].date()), "stocks": len(stocks), "variants": {}}
    grid = {"S1": dict(entry=brk[55], trail=5.0), "S2": dict(entry=brk[20], trail=5.0), "S3": dict(entry=brk[55], trail=6.0),
            "S4": dict(entry=brk[55], trail=5.0, tp_mode="res"),
            # รอบ 2 (เจ้าของ 2026-10-24 20:17): ตัด 1% ออก แบ่งทุนเท่ากันแบบพอร์ตเดิม ถือเต็มได้ 100%
            "W1": dict(entry=brk[55], trail=6.0, weight=0.05, maxpos=20),               # S3 แต่ไม้ละ 5%
            "W2": dict(entry=brk[55], trail=5.0, tp_mode="res", weight=0.05, maxpos=20), # S4 แต่ไม้ละ 5%
            "W3": dict(entry=brk[55], trail=6.0, weight=0.10, maxpos=10)}               # 10 ไม้ × 10%
    only = os.environ.get("ONLY")
    for name, g in grid.items():
        if only and name not in only.split(","):
            continue
        e, st, tr = simulate(C, O, H, L, A, spy_ok, g["entry"], trail=g["trail"], tp_mode=g.get("tp_mode"),
                             weight=g.get("weight"), maxpos=g.get("maxpos", MAXPOS))
        per = periods(e)
        out["variants"][name] = {**st, "search": per["search"], "validate": per["validate"], "holdout_sealed": per["holdout"],
                                 "by_year": {int(y.year): round(float(v), 2) for y, v in (e.resample("YE").last().pct_change() * 100).dropna().items()},
                                 "last_trades": tr[-10:]}
        print(name, json.dumps(st), json.dumps(per["search"]), json.dumps(per["validate"]), flush=True)
    rows = []
    rweight = 0.05 if only and only.startswith("W") else None
    for seed in range(SEEDS):
        e, st, _ = simulate(C, O, H, L, A, spy_ok, base, trail=6.0 if rweight else 5.0, rng=np.random.default_rng(seed), weight=rweight)
        rows.append({"stats": st, **periods(e)})
    summ = {"sizing": "weight 5%" if rweight else "risk 1%"}
    for k in ("search", "validate", "holdout"):
        v = [r[k]["cagr"] for r in rows if r[k]]
        s = [r[k]["sharpe"] for r in rows if r[k] and r[k]["sharpe"] is not None]
        summ[k if k != "holdout" else "holdout_sealed"] = {"cagr_median": round(float(np.median(v)), 2), "cagr_p95": round(float(np.percentile(v, 95)), 2),
                                                          "sharpe_median": round(float(np.median(s)), 2), "sharpe_p95": round(float(np.percentile(s, 95)), 2)}
    summ["pf_median"] = round(float(np.median([r["stats"]["pf"] for r in rows if r["stats"]["pf"]])), 2)
    out["random"] = summ
    print("random", json.dumps(summ), flush=True)
    sp = spy.loc[spy.index >= pd.Timestamp("2011-01-01")]
    sper = periods(sp / sp.iloc[0] * CAP)
    out["spy"] = {"search": sper["search"], "validate": sper["validate"], "holdout_sealed": sper["holdout"]}
    paper.put("research_trade_stops" + ("_w" if only and only.startswith("W") else ""), out, shared=True)


if __name__ == "__main__":
    main()
