"""งานวิจัย 2026-10-24 (8): หุ้นเติบโต ในตลาดที่กว้างกว่า S&P 500 (คำขอเจ้าของ) — กติกากำหนดก่อนรัน

ตลาด 2 แบบ
  U1 = สมาชิก S&P 500 + S&P 400 ปัจจุบัน (ไม่มีวันเข้า/ออกของ S&P 400 → ข้อมูลเอียง จึงเทียบกับ "สุ่ม" ในตลาดเดียวกัน)
  U2 = หุ้นสหรัฐ (NYSE/Nasdaq, ยื่นงบ US-GAAP กับ SEC) ที่มูลค่าตลาด ณ วันนั้น ≥ $10,000 ล้าน
       มูลค่าตลาด = จำนวนหุ้นจากหน้าปกงบ SEC (dei:EntityCommonStockSharesOutstanding, frames รายไตรมาส ใช้ได้หลังวันที่ระบุ 7 วัน)
       × ราคาปิด (ปรับแตกพาร์ ไม่ปรับปันผล); บริษัทหลายคลาสหุ้นที่ไม่มีจำนวนหุ้นใน frames → นับเฉพาะช่วงที่อยู่ใน S&P 500
       คัดเบื้องต้น: เคยมีสินทรัพย์รวม ≥ $2,000 ล้าน (frames Assets) · มีแต่บริษัทที่ยังจดทะเบียนวันนี้ (ข้อมูลเอียงเหมือนกัน)
สัญญาณ (งบรายไตรมาสจาก companyfacts ใช้ได้ตั้งแต่วันยื่นครั้งแรก; ไตรมาส 4 = งบปี − 3 ไตรมาส):
  g = รายได้ไตรมาสล่าสุดเทียบไตรมาสเดียวกันปีก่อน (ไตรมาสล่าสุดต้องจบไม่เกิน 200 วันก่อน)
  G1 รายได้เร่งตัว: g0 > 10% และ g0 > g1 > g2 (เร่งขึ้น 2 ไตรมาสติด) → 20 ตัวที่ g0 สูงสุด
  G2 = G1 + ราคาเหนือเส้น 200 วัน + กำไรสุทธิไตรมาสล่าสุดสูงกว่าไตรมาสเดียวกันปีก่อน → 20 ตัวที่ g0 สูงสุด
  G3 = เงื่อนไข G2 → 20 ตัวที่โมเมนตัม 6/12 เดือนสูงสุด
  EW = ทุกตัวในตลาดเท่ากัน (ตัวเทียบ) · R = สุ่ม 20 ตัว 20 seed (ตัวเทียบ ข้อมูลเอียงเท่ากัน)
ปรับพอร์ตรายเดือน (สัปดาห์สุดท้ายของเดือน) เทรดราคาเปิดถัดไป, น้ำหนักเท่ากัน × exposure สภาวะตลาด, Risk Manager + ค่าธรรมเนียมเหมือนระบบจริง
ผ่าน = Sharpe ≥ SPY และ DD เล็กกว่าชัดเจน ทั้งช่วงหาสูตร/ทดสอบซ้ำ และผลตอบแทนสูงกว่าสุ่มอันดับ 90% ทั้งสองช่วง · holdout ปิดไว้
ผล: D1 'research_growth_wide'"""
import csv
import io
import json
import os
import re
import time
import urllib.request
from datetime import date as _date

import numpy as np
import pandas as pd

import config
import paper
from agents import backtest_agent, data_agent
from research.lowvol_monthly import ADDED
from research.random_baseline import periods
from research.quality import _get, DUR

N = 20
SEEDS = int(os.environ.get("SEEDS", 20))
MCAP_MIN = 10e9
SIGNAL = {}      # วันส่งสัญญาณ → DataFrame(g0, accel, ni_up)
ELIG = {}        # ชื่อตลาด → ฟังก์ชัน(date) -> set ของหุ้นที่อยู่ในตลาด
MCAP = None
S500, S400 = set(), set()


# ---------- รายชื่อ ----------
def _fetch(url, ua=None):
    req = urllib.request.Request(url, headers={"user-agent": ua or "Mozilla/5.0 (research script)", "accept-encoding": "identity"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def sp400_rows():
    """Wikipedia: List of S&P 400 companies → [(symbol, name, GICS sector)]"""
    html = _fetch("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies")
    for t in pd.read_html(io.StringIO(html)):
        cols = [str(c) for c in t.columns]
        sym = next((c for c in cols if c.lower() in ("symbol", "ticker symbol", "ticker")), None)
        sec = next((c for c in cols if "sector" in c.lower()), None)
        name = next((c for c in cols if c.lower() in ("security", "company")), None)
        if sym and sec and len(t) > 300:
            return [(config.yf_symbol(str(r[sym])), str(r[name]) if name else "", str(r[sec])) for _, r in t.iterrows()]
    raise RuntimeError("หา S&P 400 table ไม่เจอ")


SIC_SECTOR = [((1300, 1399), "Energy"), ((2900, 2999), "Energy"), ((1000, 1499), "Materials"), ((2600, 2699), "Materials"),
              ((2800, 2829), "Materials"), ((2830, 2836), "Health Care"), ((2840, 2899), "Materials"), ((3300, 3399), "Materials"),
              ((2000, 2199), "Consumer Staples"), ((3570, 3579), "Information Technology"), ((3600, 3699), "Information Technology"),
              ((3820, 3829), "Information Technology"), ((3840, 3851), "Health Care"), ((3710, 3716), "Consumer Discretionary"),
              ((3720, 3799), "Industrials"), ((3400, 3569), "Industrials"), ((3580, 3599), "Industrials"), ((4000, 4799), "Industrials"),
              ((4800, 4899), "Communication Services"), ((4900, 4999), "Utilities"), ((5400, 5499), "Consumer Staples"),
              ((5000, 5999), "Consumer Discretionary"), ((6798, 6798), "Real Estate"), ((6500, 6599), "Real Estate"),
              ((6000, 6799), "Financials"), ((7370, 7379), "Information Technology"), ((7800, 7899), "Communication Services"),
              ((8000, 8099), "Health Care"), ((7000, 8999), "Industrials"), ((2200, 3999), "Consumer Discretionary")]


def sic_sector(sic):
    try:
        s = int(sic)
    except (TypeError, ValueError):
        return "Industrials"
    return next((g for (a, b), g in SIC_SECTOR if a <= s <= b), "Industrials")


# ---------- SEC: มูลค่าตลาด ----------
def frames(concept, unit, periods_):
    out = {}
    for p in periods_:
        j = _get(f"https://data.sec.gov/api/xbrl/frames/{concept}/{unit}/{p}.json")
        for r in (j or {}).get("data", []):
            out.setdefault(int(r["cik"]), []).append((r["end"], float(r["val"])))
    return out


def raw_close(tickers):
    """ราคาปิดที่ปรับแตกพาร์แล้วแต่ไม่ปรับปันผล (auto_adjust=False 'Close') + ตารางแตกพาร์ จากการดึงครั้งเดียว"""
    import yfinance as yf
    cs, ss = [], []
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        for k in range(3):
            try:
                raw = yf.download(chunk, start=config.DATA_START, auto_adjust=False, actions=True, progress=False,
                                  group_by="column", threads=True)
                c, sp = raw["Close"], raw["Stock Splits"]
                cs.append(c.to_frame(chunk[0]) if isinstance(c, pd.Series) else c)
                ss.append(sp.to_frame(chunk[0]) if isinstance(sp, pd.Series) else sp)
                break
            except Exception:
                time.sleep(5)
    c, sp = pd.concat(cs, axis=1), pd.concat(ss, axis=1)
    c, sp = c.loc[:, ~c.columns.duplicated()], sp.loc[:, ~sp.columns.duplicated()]
    splits = {t: sp[t][sp[t].fillna(0) > 0] for t in sp.columns if (sp[t].fillna(0) > 0).any()}
    return c, splits


def mcap_table(close_sa, shares_by_ticker, splits):
    idx = close_sa.index
    out = {}
    for t, rows in shares_by_ticker.items():
        if t not in close_sa.columns or not rows:
            continue
        s = pd.Series({pd.Timestamp(e) + pd.Timedelta(days=7): v for e, v in rows}).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        sh = s.reindex(idx.union(s.index)).ffill().reindex(idx)
        sp = splits.get(t)
        if sp is not None and len(sp):
            # ตัวคูณแตกพาร์สะสม "หลัง" แต่ละวัน: ราคาที่ปรับแล้ว × ตัวคูณนี้ = ราคาจริงตอนนั้น
            f = pd.Series(1.0, index=idx)
            for d, r in sp.items():
                if r and r > 0:
                    f[idx < d] *= r
            px = close_sa[t] * f
        else:
            px = close_sa[t]
        out[t] = sh * px
    return pd.DataFrame(out, index=idx)


# ---------- SEC: รายได้/กำไร รายไตรมาส ----------
def quarterly(facts, names):
    """{วันจบไตรมาส: (มูลค่า, วันยื่นครั้งแรก)} รวมหลาย concept (concept แรกในรายการได้ก่อน), ไตรมาส 4 = ปี − 3 ไตรมาส"""
    q, a = {}, {}
    for name in names:
        for f in (facts.get(name) or {}).get("units", {}).get("USD") or []:
            if f.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A") or not {"start", "end", "filed"} <= set(f):
                continue
            days = (_date.fromisoformat(f["end"]) - _date.fromisoformat(f["start"])).days
            tgt = q if 80 <= days <= 100 else a if 330 <= days <= 400 else None
            if tgt is None:
                continue
            key = f["end"] if tgt is q else (f["start"], f["end"])
            cur = tgt.get(key)
            if cur is None:
                tgt[key] = (float(f["val"]), f["filed"], name)
            elif cur[2] == name and f["filed"] < cur[1]:
                tgt[key] = (float(f["val"]), f["filed"], name)
    for (s, e), (v, fl, _) in a.items():
        if e in q:
            continue
        s0, e0 = _date.fromisoformat(s), _date.fromisoformat(e)
        inside = [k for k in q if 60 <= (_date.fromisoformat(k) - s0).days and (e0 - _date.fromisoformat(k)).days >= 60]
        if len(inside) == 3:
            q[e] = (v - sum(q[k][0] for k in inside), max([fl] + [q[k][1] for k in inside]), "derived")
    return {e: (v, fl) for e, (v, fl, _) in q.items()}


def company_quarters(cik):
    j = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json")
    g = ((j or {}).get("facts") or {}).get("us-gaap") or {}
    if not g:
        return None
    return {"rev": quarterly(g, DUR["rev"]), "ni": quarterly(g, DUR["ni"])}


def _yoy(series, end):
    """ค่าของไตรมาสเดียวกันปีก่อน (วันจบห่าง 340–390 วัน) → (มูลค่า, วันยื่น)"""
    e = _date.fromisoformat(end)
    for k, v in series.items():
        if 340 <= (e - _date.fromisoformat(k)).days <= 390:
            return v
    return None


def records(qtrs):
    """ต่อไตรมาส: (วันจบ, ใช้ได้ตั้งแต่, g, กำไรโต) — ใช้ได้เมื่อยื่นทั้งไตรมาสนี้และไตรมาสปีก่อนแล้ว"""
    out = []
    for e, (v, fl) in qtrs["rev"].items():
        prev = _yoy(qtrs["rev"], e)
        if prev is None or prev[0] <= 0 or v <= 0:
            continue
        n0, np_ = qtrs["ni"].get(e), _yoy(qtrs["ni"], e)
        out.append((e, max(fl, prev[1]), v / prev[0] - 1, n0 is not None and np_ is not None and n0[0] > np_[0]))
    return sorted(out)


def signals_at(recs, d):
    """ณ วันที่ d: ใช้ 3 ไตรมาสล่าสุดที่ยื่นแล้ว"""
    ds = str(d.date())
    ok = [r for r in recs if r[1] <= ds][-3:]
    if len(ok) < 3 or (d.date() - _date.fromisoformat(ok[-1][0])).days > 200:
        return None
    g0, g1, g2 = ok[-1][2], ok[-2][2], ok[-3][2]
    return {"g0": g0, "accel": g0 > 0.10 and g0 > g1 > g2, "ni_up": ok[-1][3]}


# ---------- กลยุทธ์ ----------
class Universe:
    """ซ่อนหุ้นที่ไม่อยู่ในตลาด ณ วันนั้น แล้วเรียกกลยุทธ์ข้างใน"""

    def __init__(self, inner, uname):
        self.inner, self.uname = inner, uname

    def propose(self, close, ind, date, regime, p=config.STRATEGY, held=()):
        ok = ELIG[self.uname](date)
        hide = [t for t, m in config.UNIVERSE.items() if m["kind"] == "stock" and t not in ok]
        saved = {t: config.UNIVERSE.pop(t) for t in hide}
        try:
            return self.inner.propose(close, ind, date, regime, p, held)
        finally:
            config.UNIVERSE.update(saved)


def _weights(picks, regime, n):
    w = {t: regime["exposure"] * (len(picks) / n) / len(picks) for t in picks} if picks else {}
    w[config.CASH_PROXY] = w.get(config.CASH_PROXY, 0) + max(0.0, 1 - sum(w.values()))
    return {"weights": {t: round(float(v), 4) for t, v in w.items() if v > 1e-4}, "why": {}, "ranking": []}


def _stocks(close, date):
    px = close.loc[date]
    return [t for t, m in config.UNIVERSE.items() if m["kind"] == "stock" and t in close.columns and px[t] == px[t]]


class Growth:
    def __init__(self, mode):
        self.mode = mode

    def propose(self, close, ind, date, regime, p=config.STRATEGY, held=()):
        sig = SIGNAL.get(date)
        px, sma, mom = close.loc[date], ind["sma"].loc[date], ind["mom"].loc[date]
        cand = [t for t in _stocks(close, date) if sig is not None and t in sig.index and bool(sig.at[t, "accel"])]
        if self.mode in ("G2", "G3"):
            cand = [t for t in cand if bool(sig.at[t, "ni_up"]) and sma[t] == sma[t] and px[t] > sma[t]]
        if self.mode == "G3":
            picks = sorted([t for t in cand if mom[t] == mom[t]], key=lambda t: -mom[t])[:N]
        else:
            picks = sorted(cand, key=lambda t: -sig.at[t, "g0"])[:N]
        return _weights(picks, regime, N)


class EqualAll:
    def propose(self, close, ind, date, regime, p=config.STRATEGY, held=()):
        picks = _stocks(close, date)
        w = {t: regime["exposure"] / len(picks) for t in picks} if picks else {}
        w[config.CASH_PROXY] = w.get(config.CASH_PROXY, 0) + max(0.0, 1 - sum(w.values()))
        return {"weights": w, "why": {}, "ranking": []}


class Random20:
    def __init__(self, seed):
        self.rng = np.random.default_rng(seed)

    def propose(self, close, ind, date, regime, p=config.STRATEGY, held=()):
        c = sorted(_stocks(close, date))
        picks = list(self.rng.choice(c, min(N, len(c)), replace=False)) if c else []
        return _weights(picks, regime, N)


# ---------- main ----------
def build_remote():
    global MCAP
    tick = _get("https://www.sec.gov/files/company_tickers_exchange.json")
    f = tick["fields"]
    sec_rows = [dict(zip(f, r)) for r in tick["data"]]
    by_ticker = {config.yf_symbol(r["ticker"]): r for r in sec_rows if r.get("exchange") in ("NYSE", "Nasdaq")}
    rows500 = config.read_sp500()
    for r in csv.DictReader(io.StringIO(open(config.SP500_FILE, encoding="utf-8").read())):
        ADDED[config.yf_symbol(r["Symbol"])] = (r.get("Date added") or "")[:10]
    S500.update(r[0] for r in rows500)
    rows = {r[0]: r for r in rows500}
    for sym, name, sec in sp400_rows():
        if sym in rows:
            continue
        cik = str(by_ticker.get(sym, {}).get("cik", ""))
        rows[sym] = (sym, name, sec if sec in config.GICS_GROUP else "Industrials", cik)
        S400.add(sym)
    print(f"S&P 500 {len(S500)} · S&P 400 {len(S400)}", flush=True)

    assets = frames("us-gaap/Assets", "USD", [f"CY{y}Q4I" for y in range(2010, 2026)])
    big = {cik for cik, v in assets.items() if max(x for _, x in v) >= 2e9}
    cik_of = {t: int(r["cik"]) for t, r in by_ticker.items()}
    extra = [t for t, c in cik_of.items() if c in big and t not in rows and re.fullmatch(r"[A-Z]{1,5}(-[A-Z])?", t)]
    print(f"extra candidates (assets ≥ $2B, not in S&P 500/400): {len(extra)}", flush=True)
    shares = frames("dei/EntityCommonStockSharesOutstanding", "shares",
                    [f"CY{y}Q{q}I" for y in range(2010, 2027) for q in range(1, 5) if (y, q) <= (2026, 3)])
    all_t = list(rows) + extra
    cik_all = {t: int(rows[t][3]) if t in rows and str(rows[t][3]).isdigit() else cik_of.get(t) for t in all_t}
    close_sa, spl = raw_close(all_t)
    MCAP = mcap_table(close_sa, {t: shares.get(c, []) for t, c in cik_all.items() if c}, spl)
    ever = [t for t in extra if t in MCAP.columns and (MCAP[t] >= MCAP_MIN).any()]
    print(f"extra ever ≥ $10B: {len(ever)}", flush=True)
    for t in ever:
        sub = _get(f"https://data.sec.gov/submissions/CIK{cik_all[t]:010d}.json") or {}
        rows[t] = (t, sub.get("name") or by_ticker[t].get("name", ""), sic_sector(sub.get("sic")), str(cik_all[t]))
    config.set_stocks(list(rows.values()))
    QTR = {}
    t0 = time.time()
    for t, r in rows.items():
        if str(r[3]).isdigit():
            q = company_quarters(r[3])
            if q:
                QTR[t] = q
    print(f"quarterly financials: {len(QTR)} of {len(rows)} ({time.time() - t0:.0f}s)", flush=True)
    d, src = data_agent.load("yfinance", refresh=True, allow_synthetic=False)
    return d, src, QTR


def build_local():
    """ทดสอบโค้ดในเครื่อง (ข้อมูลจำลอง ห้ามใช้สรุปผล)"""
    global MCAP
    d = data_agent.clean(data_agent.synthetic(seed=5))
    stocks = [t for t, m in config.UNIVERSE.items() if m["kind"] == "stock" and t in d["close"].columns]
    S500.update(stocks[: len(stocks) // 2])
    S400.update(stocks[len(stocks) // 2: len(stocks) * 3 // 4])
    rng = np.random.default_rng(2)
    MCAP = pd.DataFrame({t: d["close"][t] * rng.uniform(5e7, 6e8) for t in stocks}, index=d["close"].index)
    QTR = {}
    for t in stocks:
        rev, ni, v = {}, {}, 1e9
        for y in range(2009, 2027):
            for m, dd in ((3, 31), (6, 30), (9, 30), (12, 31)):
                v *= 1 + rng.normal(0.02, 0.05)
                e = f"{y}-{m:02d}-{dd}"
                fl = str((pd.Timestamp(e) + pd.Timedelta(days=40)).date())
                rev[e], ni[e] = (v, fl), (v * rng.normal(0.1, 0.05), fl)
        QTR[t] = {"rev": rev, "ni": ni}
    return d, "synthetic", QTR


def main():
    remote = paper.remote()
    d, src, QTR = build_remote() if remote else build_local()
    close = d["close"]
    mc = MCAP.reindex(index=close.index).ffill(limit=5)
    u1 = S500 | S400
    ELIG["U1"] = lambda date: u1
    ELIG["U2"] = lambda date: ({t for t, v in mc.loc[date].items() if v == v and v >= MCAP_MIN}
                               | {t for t in S500 if t not in mc.columns or mc.at[date, t] != mc.at[date, t]
                                  if ADDED.get(t, "") <= str(date.date())})
    sig_days = backtest_agent.signal_dates(close.index, max(config.STRATEGY["mom_long"], config.STRATEGY["trend_sma"]) + 1, "monthly")
    REC = {t: records(q) for t, q in QTR.items()}
    for date in sorted(sig_days):
        rec = {t: s for t, r in REC.items() if (s := signals_at(r, date))}
        SIGNAL[date] = pd.DataFrame(rec).T if rec else pd.DataFrame(columns=["g0", "accel", "ni_up"])
    sizes = {u: [len(ELIG[u](x)) for x in sorted(sig_days)[::24]] for u in ELIG}
    print("universe size (every 2 years):", sizes, flush=True)
    out = {"created": paper.now_th(), "source": src, "data_last": str(close.index[-1].date()), "n_quarterly": len(QTR),
           "sizes": sizes, "variants": {}, "random": {}}
    spy = None
    for u in ("U1", "U2"):
        for name, mod in (("EW", EqualAll()), ("G1", Growth("G1")), ("G2", Growth("G2")), ("G3", Growth("G3"))):
            risk = {"min_trade_weight": 0.0} if name == "EW" else None   # ถือหลายร้อยตัว ตัวละไม่ถึง 2% ต้องให้เทรดได้
            res = backtest_agent.run(d, strategy_module=Universe(mod, u), risk=risk, cadence="monthly", portfolio="momentum")
            per = periods(res["equity"])
            spy = spy or periods(res["bench"])
            last = res["weights_log"][-1]["weights"] if res["weights_log"] else {}
            out["variants"][f"{u}-{name}"] = {"turnover": res["stats"]["turnover_per_year"], "search": per["search"],
                                              "validate": per["validate"], "holdout_sealed": per["holdout"],
                                              "by_year": {y["year"]: y["ret"] for y in res["stats"]["by_year"]},
                                              "last_picks": sorted((t for t in last if t != config.CASH_PROXY), key=lambda t: -last[t])[:20]}
            print(u, name, json.dumps(per["search"]), json.dumps(per["validate"]), flush=True)
        rows = []
        for seed in range(SEEDS):
            res = backtest_agent.run(d, strategy_module=Universe(Random20(seed), u), cadence="monthly", portfolio="momentum")
            rows.append(periods(res["equity"]))
        summ = {}
        for k in ("search", "validate", "holdout"):
            v = [r[k]["cagr"] for r in rows if r[k]]
            s = [r[k]["sharpe"] for r in rows if r[k] and r[k]["sharpe"] is not None]
            summ[k if k != "holdout" else "holdout_sealed"] = {"cagr_median": round(float(np.median(v)), 2), "cagr_p90": round(float(np.percentile(v, 90)), 2),
                                                              "sharpe_median": round(float(np.median(s)), 2), "sharpe_p90": round(float(np.percentile(s, 90)), 2)}
        out["random"][u] = summ
        print(u, "random", json.dumps(summ["search"]), json.dumps(summ["validate"]), flush=True)
    out["spy"] = {"search": spy["search"], "validate": spy["validate"], "holdout_sealed": spy["holdout"]}
    paper.put("research_growth_wide", out, shared=True)


if __name__ == "__main__":
    main()
