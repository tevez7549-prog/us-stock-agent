"""งานวิจัย 2026-11-05 (10): สูตรเทรดหุ้นแบบ Dan Zanger (โพสต์ The Investmin 5 ข้อ ที่เจ้าของส่งมา) — กริดกำหนดก่อนรัน

กฎที่แปลงเป็นตัวเลข (ส่วนที่ Dan ใช้ตา/ข้อมูลรายชั่วโมง ต้องประมาณ):
  ฐานราคา  : 25 วันก่อนวันทะลุ (สูงสุด − ต่ำสุด) ≤ 20% ของจุดสูงสุดฐาน · วอลุ่มเฉลี่ย 5 วันสุดท้ายในฐาน ≤ 70% ของเฉลี่ย 50 วัน
  หุ้นนำ    : ปิด > SMA50 > SMA200 และขึ้น ≥ 30% ใน 126 วัน
  วันทะลุ   : ปิด > จุดสูงสุดฐาน (High สูงสุด 25 วันก่อนหน้า) และวอลุ่ม ≥ 3 เท่าของเฉลี่ย 20 วันก่อนหน้า
  ซื้อ      : ราคาเปิดวันถัดไป (ไม่มีข้อมูลรายชั่วโมง) · ถ้าเปิดสูงกว่าจุดทะลุเกิน 5% → ข้าม
  ตัดขาดทุน : ต่ำกว่าจุดทะลุ 7% (เช็คกับ Low ของวัน, gap ข้าม → ออกที่ราคาเปิด)
  ขายหมด   : ปิดต่ำกว่า SMA20 (แทนเส้นแนวรับขาขึ้นที่ลากด้วยตา) → ขายราคาเปิดวันถัดไป
  ขายครึ่ง  : (Z3/Z4) ขึ้นแรง 3 วันติด (ปิดเพิ่ม ≥ 1 ATR ทุกวัน) หรือวอลุ่ม ≥ 3 เท่าแต่ปิดลง ≥ 1 ATR → ขายครึ่งราคาเปิดถัดไป (ครั้งเดียวต่อไม้, ไม่ซื้อคืน)
  งบ        : (Z1/Z2/Z4) รายได้ไตรมาสล่าสุดโตจากปีก่อน ≥ 25% และโตเร็วกว่าไตรมาสก่อน และกำไรสุทธิสูงกว่าไตรมาสเดียวกันปีก่อน
              (SEC companyfacts ใช้ได้ตั้งแต่วันยื่น, ไตรมาสล่าสุดต้องจบไม่เกิน 200 วัน) — ใช้ 25% แทน 40% เพราะ 40% แทบไม่เหลือหุ้นใหญ่
  กลุ่มนำ   : (Z2/Z4) อยู่ใน 2 sector ที่ค่ามัธยฐานผลตอบแทน 63 วันสูงสุด ณ วันนั้น
  ตลาด      : SPY > SMA200 เท่านั้นถึงเปิดไม้ใหม่ (เหมือน W1)
  ขนาด      : ไม้ละ 5% ของพอร์ต ถือได้ ≤ 20 ตัว (เหมือน W1) · สัญญาณเกินโควตา → เลือกวอลุ่มพุ่งมากสุด
กริด (ทุกแบบ ทั้ง 2 ตลาด):
  W1  = ระบบที่รันจริง (ทะลุ 55 วัน + SMA200, stop 3 ATR, trailing 6 ATR) เป็นตัวเทียบ
  Z0  = ฐาน + หุ้นนำ + ทะลุ + วอลุ่ม 3 เท่า + ห้ามไล่ 5% + stop 7% + ออกใต้ SMA20
  Z0n = Z0 ตัดกฎวอลุ่มทั้งหมด (ดูว่าวอลุ่มช่วยไหม)
  Z0w = จุดเข้า Z0 แต่ใช้กฎออกของ W1 (แยกผลจุดเข้า/จุดออก)
  Z1  = Z0 + งบ · Z2 = Z1 + กลุ่มนำ · Z3 = Z0 + ขายครึ่ง · Z4 = Z2 + ขายครึ่ง (ครบทุกข้อของ Dan)
  R   = เข้าสุ่ม (หุ้นเหนือ SMA200) ใช้กฎออก/ขนาดของ Z0, 20 seed
ตลาด A = S&P 500 ตามวันที่เข้าดัชนีจริง · B = A + สมาชิก S&P 400 ปัจจุบัน (เอียงเพราะไม่มีหุ้นที่หลุดไป)
ผ่าน = Sharpe ≥ SPY และ DD เล็กกว่าชัดเจน ทั้ง search และ validate + ชนะสุ่ม p95 · holdout ปิดไว้ · ผล D1 'research_zanger'"""
import csv
import io
import json
import os
import time

import numpy as np
import pandas as pd

import config
import paper
from research.random_baseline import periods, PERIODS
from research.trade_stops import atr14

CAP, W, MAXPOS = 10000.0, 0.05, 20
FEE = config.COSTS["commission_pct"] + config.COSTS["slippage_pct"]
SEEDS = int(os.environ.get("SEEDS", 20))
P = {"base": 25, "tight": 0.20, "dry": 0.70, "volx": 3.0, "chase": 0.05, "stop": 0.07, "lead": 0.30, "g": 0.25, "sectors": 2}


# ---------- ข้อมูล ----------
def ohlcv(tickers):
    import yfinance as yf
    parts = {k: [] for k in ("Open", "High", "Low", "Close", "Volume")}
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        for k in range(3):
            try:
                raw = yf.download(chunk, start=config.DATA_START, auto_adjust=True, progress=False, group_by="column", threads=True)
                for f in parts:
                    x = raw[f]
                    parts[f].append(x.to_frame(chunk[0]) if isinstance(x, pd.Series) else x)
                break
            except Exception:
                time.sleep(5)
    out = {}
    for f, xs in parts.items():
        x = pd.concat(xs, axis=1)
        out[f] = x.loc[:, ~x.columns.duplicated()].sort_index()
    idx = out["Close"].dropna(how="all").index
    return {f.lower(): x.reindex(idx).astype(float) for f, x in out.items()}


def fund_mask(recs, idx):
    """Series bool รายวัน: งบล่าสุดที่ยื่นแล้วผ่านเกณฑ์ (โต ≥ g, เร่งขึ้น, กำไรโต) และไตรมาสจบไม่เกิน 200 วัน"""
    s = pd.Series(False, index=idx)
    rs = sorted(recs)                                   # (end, avail, g, ni_up)
    ev = []
    for k in range(1, len(rs)):
        e, av, g, up = rs[k]
        ok = g >= P["g"] and g > rs[k - 1][2] and up
        ev.append((pd.Timestamp(av), pd.Timestamp(e) + pd.Timedelta(days=200), ok))
    ev.sort()
    for n, (a, z, ok) in enumerate(ev):
        nxt = ev[n + 1][0] if n + 1 < len(ev) else pd.Timestamp("2100-01-01")
        if ok:
            s[(idx >= a) & (idx < min(nxt, z))] = True
    return s


def load_remote():
    from research.growth_wide import sp400_rows, company_quarters, records
    from research.quality import _get
    added = {config.yf_symbol(r["Symbol"]): (r.get("Date added") or "")[:10]
             for r in csv.DictReader(io.StringIO(open(config.SP500_FILE, encoding="utf-8").read()))}
    rows = {r[0]: {"sector": r[2], "cik": r[3], "added": added.get(r[0]) or "1900-01-01", "u": "A"} for r in config.read_sp500()}
    tick = _get("https://www.sec.gov/files/company_tickers_exchange.json")
    by_t = {config.yf_symbol(r[2]): r[0] for r in tick["data"]}        # fields: cik, name, ticker, exchange
    for sym, _, sec in sp400_rows():
        if sym not in rows:
            rows[sym] = {"sector": sec, "cik": str(by_t.get(sym, "")), "added": "1900-01-01", "u": "B"}
    print(f"S&P 500 {sum(r['u'] == 'A' for r in rows.values())} · S&P 400 extra {sum(r['u'] == 'B' for r in rows.values())}", flush=True)
    d = ohlcv(sorted(rows) + [config.BENCHMARK])
    t0, n = time.time(), 0
    fund = {}
    for t, r in rows.items():
        if str(r["cik"]).isdigit() and t in d["close"].columns:
            q = company_quarters(r["cik"])
            if q:
                fund[t] = fund_mask(records(q), d["close"].index)
                n += 1
    print(f"quarterly financials {n} of {len(rows)} ({time.time() - t0:.0f}s)", flush=True)
    return d, rows, fund, "yfinance"


def load_local():
    """ทดสอบโค้ดในเครื่องเท่านั้น (ข้อมูลจำลอง ห้ามใช้สรุปผล)"""
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2010-01-01", "2026-10-30")
    tick = [f"S{i:03d}" for i in range(120)] + [config.BENCHMARK]
    ret = rng.normal(0.0004, 0.02, (len(idx), len(tick)))
    C = pd.DataFrame(100 * np.exp(np.cumsum(ret, axis=0)), index=idx, columns=tick)
    O = C.shift(1).fillna(C) * (1 + rng.normal(0, 0.005, C.shape))
    H = np.maximum(C, O) * (1 + np.abs(rng.normal(0, 0.01, C.shape)))
    L = np.minimum(C, O) * (1 - np.abs(rng.normal(0, 0.01, C.shape)))
    V = pd.DataFrame(rng.lognormal(13, 0.6, C.shape), index=idx, columns=tick)
    secs = list(config.GICS_GROUP)
    rows = {t: {"sector": secs[i % len(secs)], "cik": "", "added": "2012-01-01" if i % 7 == 0 else "1900-01-01", "u": "A" if i < 90 else "B"}
            for i, t in enumerate(tick[:-1])}
    fund = {t: pd.Series(rng.random(len(idx)) < 0.3, index=idx) for t in tick[:-1]}
    return {"open": O, "high": H, "low": L, "close": C, "volume": V}, rows, fund, "synthetic"


# ---------- จำลองการเทรดต่อไม้ ----------
def simulate(d, entry, score, spy_ok, mode, start="2011-01-01", rng=None, maxpos=MAXPOS, half=False, pivot=None):
    """mode 'z' = stop ต่ำกว่าจุดทะลุ 7% + ออกใต้ SMA20 + ห้ามไล่ 5% · 'w1' = stop 3 ATR + trailing 6 ATR"""
    C, O, H, L, V, A, S20, VA = (d[k] for k in ("C", "O", "H", "L", "V", "A", "S20", "VA"))
    E, SC = entry.to_numpy(), score.to_numpy()
    PV = pivot.to_numpy() if pivot is not None else C
    idx = d["idx"]
    tick = d["tick"]
    i0 = int(np.searchsorted(idx.values, np.datetime64(pd.Timestamp(start))))
    cash, eq, inv, trades, pos, pending = CAP * (1 - config.COSTS["fx_spread_pct"]), [], [], [], {}, []
    for i in range(i0, len(idx)):
        # 1) ออกตามสัญญาณเมื่อวาน (ราคาเปิด) แล้วเปิดไม้ใหม่
        for j in list(pos):
            p = pos[j]
            o = O[i, j]
            if p.get("sell") and o == o:
                frac = p.pop("sell")
                sh = p["sh"] if frac >= 1 else p["sh"] // 2
                cash += sh * o * (1 - FEE)
                p["pnl"] += sh * (o - p["px"]) - sh * o * FEE
                p["sh"] -= sh
                if p["sh"] <= 0:
                    trades.append({"t": tick[j], "in": str(idx[p["i"]].date()), "out": str(idx[i].date()), "R": round(p["pnl"] / p["risk"], 3)})
                    del pos[j]
        for j, piv in pending:
            px = O[i, j]
            if px != px or len(pos) >= maxpos or j in pos:
                continue
            if mode == "z":
                stop = piv * (1 - P["stop"])
                if px > piv * (1 + P["chase"]) or px <= stop:
                    continue
            else:
                stop = px - 3 * A[i - 1, j]
            equity_now = cash + sum(q["sh"] * (C[i - 1, k] if C[i - 1, k] == C[i - 1, k] else q["px"]) for k, q in pos.items())
            sh = int(min(equity_now * W, cash) // (px * (1 + FEE)))
            if sh <= 0:
                continue
            cash -= sh * px * (1 + FEE)
            pos[j] = {"sh": sh, "px": px, "stop": stop, "risk": max(sh * (px - stop), 1e-9), "i": i, "pnl": -sh * px * FEE, "halved": False}
        pending = []
        # 2) stop ภายในวัน
        for j in list(pos):
            p = pos[j]
            o, l, c = O[i, j], L[i, j], C[i, j]
            if c != c:
                continue
            x = o if (o == o and o <= p["stop"]) else p["stop"] if (l == l and l <= p["stop"]) else None
            if x is not None:
                cash += p["sh"] * x * (1 - FEE)
                p["pnl"] += p["sh"] * (x - p["px"]) - p["sh"] * x * FEE
                trades.append({"t": tick[j], "in": str(idx[p["i"]].date()), "out": str(idx[i].date()), "R": round(p["pnl"] / p["risk"], 3)})
                del pos[j]
                continue
            # 3) สัญญาณออกตอนปิด → ขายราคาเปิดพรุ่งนี้
            if mode == "w1":
                if A[i, j] == A[i, j]:
                    p["stop"] = max(p["stop"], c - 6 * A[i, j])
            else:
                if S20[i, j] == S20[i, j] and c < S20[i, j]:
                    p["sell"] = 1.0
                elif half and not p["halved"] and i - p["i"] >= 2:
                    a = A[i - 1, j]
                    up3 = a == a and all(C[i - k, j] - C[i - k - 1, j] >= a for k in range(3))
                    dist = a == a and VA[i, j] == VA[i, j] and V[i, j] >= P["volx"] * VA[i, j] and c <= C[i - 1, j] - a
                    if up3 or dist:
                        p["sell"], p["halved"] = 0.5, True
        held = sum(p["sh"] * C[i, k] for k, p in pos.items() if C[i, k] == C[i, k])
        eq.append(cash + held)
        inv.append(held / max(cash + held, 1e-9))
        # 4) สัญญาณซื้อวันนี้ → เปิดพรุ่งนี้
        if not spy_ok[i]:
            continue
        cands = [j for j in np.nonzero(E[i])[0] if j not in pos and A[i, j] == A[i, j]]
        room = maxpos - len(pos)
        if room <= 0 or not cands:
            continue
        if rng is not None:
            cands = list(rng.choice(cands, min(room, len(cands)), replace=False))
        else:
            cands = sorted(cands, key=lambda j: -SC[i, j])[:room]
        pending = [(j, PV[i, j]) for j in cands]
    e = pd.Series(eq, index=idx[i0:])
    R = [t["R"] for t in trades]
    yrs = max((e.index[-1] - e.index[0]).days / 365.25, 1e-9)
    st = {"trades": len(R), "per_year": round(len(R) / yrs, 1), "invested_avg": round(float(np.mean(inv)), 2),
          "win_rate": round(float(np.mean([r > 0 for r in R])), 3) if R else None,
          "avg_R": round(float(np.mean(R)), 3) if R else None,
          "pf": round(float(sum(r for r in R if r > 0) / max(1e-9, -sum(r for r in R if r < 0))), 2) if R else None}
    for k, (a, b) in PERIODS.items():
        rr = [t["R"] for t in trades if a <= t["out"] <= b]
        st[f"pf_{k}"] = round(float(sum(r for r in rr if r > 0) / max(1e-9, -sum(r for r in rr if r < 0))), 2) if rr else None
        st[f"n_{k}"] = len(rr)
    return e, st, trades


def main():
    d0, rows, fund, src = load_remote() if paper.remote() else load_local()
    spy = d0["close"][config.BENCHMARK]
    spy_ok = (spy > spy.rolling(200).mean()).to_numpy()
    out = {"created": paper.now_th(), "source": src, "data_last": str(spy.index[-1].date()), "params": P, "universes": {}}
    for uname in ("A", "B"):
        tick = [t for t, r in rows.items() if (r["u"] == "A" or uname == "B") and t in d0["close"].columns]
        C, O, H, L, V = (d0[k][tick] for k in ("close", "open", "high", "low", "volume"))
        H, L = H.fillna(C), L.fillna(C)
        idx = C.index
        pit = pd.DataFrame({t: idx >= pd.Timestamp(rows[t]["added"]) for t in tick}, index=idx)
        A = atr14(H, L, C)
        s20, s50, s200 = (C.rolling(n).mean() for n in (20, 50, 200))
        va20 = V.shift(1).rolling(20).mean()
        piv = H.shift(1).rolling(P["base"]).max()
        tight = (piv - L.shift(1).rolling(P["base"]).min()) / piv <= P["tight"]
        dry = V.shift(1).rolling(5).mean() <= P["dry"] * V.shift(1).rolling(50).mean()
        lead = (C > s50) & (s50 > s200) & (C / C.shift(126) - 1 >= P["lead"])
        volx = V / va20
        brk = (C > piv) & pit & C.notna()
        z_nv = brk & tight & lead
        z0 = z_nv & dry & (volx >= P["volx"])
        F = pd.DataFrame({t: fund.get(t, pd.Series(False, index=idx)).reindex(idx).fillna(False) for t in tick}, index=idx).astype(bool)
        sec = pd.Series({t: rows[t]["sector"] for t in tick})
        r63 = (C / C.shift(63) - 1).where(pit)
        med = r63.T.groupby(sec).median().T
        rank = med.rank(axis=1, ascending=False)
        topsec = pd.DataFrame({t: (rank[sec[t]] <= P["sectors"]).to_numpy() for t in tick}, index=idx)
        z1 = z0 & F
        z2 = z1 & topsec
        dn55 = H.shift(1).rolling(55).max()
        w1 = (C > dn55) & (C > s200) & pit & C.notna()
        base = (C > s200) & pit & C.notna()
        dd = {"C": C.to_numpy(), "O": O.to_numpy(), "H": H.to_numpy(), "L": L.to_numpy(), "V": V.to_numpy(), "A": A.to_numpy(),
              "S20": s20.to_numpy(), "VA": va20.to_numpy(), "idx": idx, "tick": tick}
        vs = volx.fillna(0)
        grid = {"W1": (w1, C / dn55 - 1, "w1", False, None),
                "Z0": (z0, vs, "z", False, piv), "Z0n": (z_nv, C / piv - 1, "z", False, piv), "Z0w": (z0, vs, "w1", False, piv),
                "Z1": (z1, vs, "z", False, piv), "Z2": (z2, vs, "z", False, piv),
                "Z3": (z0, vs, "z", True, piv), "Z4": (z2, vs, "z", True, piv)}
        U = {"stocks": len(tick), "fund_pass_share": round(float(F.where(pit).stack().mean()), 3),
             "signals_per_year": {k: round(float(g[0].loc["2011":].sum().sum()) / 15.8, 1) for k, g in grid.items()}, "variants": {}}
        print(uname, "signals/yr", json.dumps(U["signals_per_year"]), flush=True)
        for name, (ent, sc, mode, half, pv) in grid.items():
            e, st, tr = simulate(dd, ent, sc, spy_ok, mode, half=half, pivot=pv)
            per = periods(e)
            U["variants"][name] = {**st, "search": per["search"], "validate": per["validate"], "holdout_sealed": per["holdout"],
                                   "by_year": {int(y.year): round(float(v), 2) for y, v in (e.resample("YE").last().pct_change() * 100).dropna().items()},
                                   "last_trades": tr[-8:]}
            print(uname, name, json.dumps(st), json.dumps(per["search"]), json.dumps(per["validate"]), flush=True)
        rws = []
        for seed in range(SEEDS):
            e, st, _ = simulate(dd, base, C * 0, spy_ok, "z", rng=np.random.default_rng(seed), pivot=C)
            rws.append({"stats": st, **periods(e)})
        summ = {}
        for k in ("search", "validate", "holdout"):
            v = [r[k]["cagr"] for r in rws if r[k]]
            s = [r[k]["sharpe"] for r in rws if r[k] and r[k]["sharpe"] is not None]
            summ[k if k != "holdout" else "holdout_sealed"] = {"cagr_median": round(float(np.median(v)), 2), "cagr_p95": round(float(np.percentile(v, 95)), 2),
                                                              "sharpe_median": round(float(np.median(s)), 2), "sharpe_p95": round(float(np.percentile(s, 95)), 2)}
        summ["pf_median"] = round(float(np.median([r["stats"]["pf"] for r in rws if r["stats"]["pf"]])), 2)
        U["random"] = summ
        print(uname, "random", json.dumps(summ["search"]), json.dumps(summ["validate"]), flush=True)
        out["universes"][uname] = U
    sp = spy.loc[spy.index >= pd.Timestamp("2011-01-01")]
    sper = periods(sp / sp.iloc[0] * CAP)
    out["spy"] = {"search": sper["search"], "validate": sper["validate"], "holdout_sealed": sper["holdout"]}
    print("SPY", json.dumps(out["spy"]["search"]), json.dumps(out["spy"]["validate"]), flush=True)
    paper.put("research_zanger", out, shared=True)


if __name__ == "__main__":
    main()
