"""เอเจนต์ 2 — ข้อมูล: ดึง → ทำความสะอาด → ตรวจคุณภาพ → เก็บ
ผลลัพธ์: dict {"close", "open"} เป็นตาราง วัน × ticker (ราคาปรับปันผล/แตกพาร์แล้ว) + "fx" (บาทต่อดอลลาร์)
"""
import os
import numpy as np
import pandas as pd

import config

CACHE = "data/prices.pkl"


def fetch_yfinance(start=config.DATA_START):
    import yfinance as yf
    tickers = list(config.UNIVERSE) + [config.FX_TICKER]
    raw = yf.download(tickers, start=start, auto_adjust=True, progress=False, group_by="column", threads=True)
    if raw is None or raw.empty:
        raise RuntimeError("yfinance ไม่ได้ข้อมูลกลับมา (เช็คอินเทอร์เน็ต)")
    close, open_ = raw["Close"].copy(), raw["Open"].copy()
    fx = close.pop(config.FX_TICKER)
    open_ = open_.drop(columns=[config.FX_TICKER], errors="ignore")
    return {"close": close, "open": open_, "fx": fx}


def synthetic(start="2012-01-02", years=14, seed=11, drift=True):
    """ข้อมูลจำลองหลายสินทรัพย์ (มีความสัมพันธ์กันแบบตลาดจริงคร่าวๆ) — ใช้ทดสอบระบบเท่านั้น"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=252 * years)
    n = len(dates)
    mkt = rng.normal(0.0003 if drift else 0, 0.011, n)
    # ช่วงวิกฤตจำลอง 2 ครั้ง
    for s in (int(n * 0.35), int(n * 0.7)):
        mkt[s:s + 60] += rng.normal(-0.004, 0.012, 60)
    groups = {g: rng.normal(0, 0.006, n) for g in sorted({v["group"] for v in config.UNIVERSE.values()})}
    close, open_ = {}, {}
    for t, meta in config.UNIVERSE.items():
        k = meta["kind"]
        beta = {"stock": 1.2, "etf": 1.0, "defensive": -0.25, "cash": 0.0}[k]
        idio = {"stock": 0.014, "etf": 0.004, "defensive": 0.007, "cash": 0.0003}[k]
        mu = (rng.normal(0.0001, 0.0002) if drift else 0) if k != "cash" else (0.00008 if drift else 0)
        r = mu + beta * mkt + (groups[meta["group"]] if k not in ("cash",) else 0) * (1 if k != "defensive" else 0.5) \
            + rng.normal(0, idio, n)
        c = 50 * np.exp(np.cumsum(r))
        gap = rng.normal(0, idio * 0.3 + 0.001, n)
        close[t] = c
        open_[t] = np.r_[c[0], c[:-1]] * (1 + gap)
    fx = 33 * np.exp(np.cumsum(rng.normal(0, 0.004, n)) - 0.0004 * mkt.cumsum())
    return {"close": pd.DataFrame(close, index=dates), "open": pd.DataFrame(open_, index=dates),
            "fx": pd.Series(fx, index=dates)}


def drop_unfinished_day(d, now=None):
    """ตัดแท่งของวันที่ตลาดสหรัฐยังไม่ปิด (ก่อน 16:30 เวลานิวยอร์ก) — ราคาระหว่างวันไม่ใช่ราคาปิด"""
    from zoneinfo import ZoneInfo
    ny = (now or pd.Timestamp.now(tz="UTC")).tz_convert(ZoneInfo("America/New_York"))
    last = d["close"].index[-1]
    if last.date() == ny.date() and (ny.hour, ny.minute) < (16, 30):
        d = {k: v.iloc[:-1] if hasattr(v, "iloc") else v for k, v in d.items()}
    return d


def clean(d):
    """เติมช่องว่างสั้นๆ (≤3 วัน) ไม่เติมยาว ไม่เติมย้อนหลัง (กันข้อมูลอนาคตรั่ว)"""
    out = {}
    for k in ("close", "open"):
        out[k] = d[k].sort_index().ffill(limit=3)
    out["fx"] = d["fx"].sort_index().reindex(out["close"].index).ffill()
    return out


def quality_report(d, as_of=None):
    c = d["close"]
    last = c.index[-1]
    rep = {"last_date": str(last.date()), "rows": len(c), "issues": []}
    for t in c.columns:
        s = c[t]
        first = s.first_valid_index()
        if first is None:
            rep["issues"].append({"ticker": t, "issue": "ไม่มีข้อมูลเลย"})
            continue
        miss = s.loc[first:].isna().mean()
        if miss > 0.02:
            rep["issues"].append({"ticker": t, "issue": f"ข้อมูลหาย {miss:.1%}"})
        jump = s.pct_change(fill_method=None).abs().max()
        if jump > 0.4:
            rep["issues"].append({"ticker": t, "issue": f"ราคากระโดดวันเดียว {jump:.0%} (เช็คการแตกพาร์)"})
        if pd.isna(s.iloc[-1]):
            rep["issues"].append({"ticker": t, "issue": "ไม่มีราคาวันล่าสุด"})
    if as_of is not None:
        age = (pd.Timestamp(as_of) - last).days
        rep["age_days"] = age
        if age > 5:
            rep["issues"].append({"ticker": "*", "issue": f"ข้อมูลเก่า {age} วัน"})
    rep["ok"] = not any(i["ticker"] == "*" or "ไม่มี" in i["issue"] for i in rep["issues"])
    return rep


def load(source="auto", refresh=False, allow_synthetic=True):
    """auto = ใช้ cache ถ้าอัปเดตวันนี้แล้ว, ไม่งั้นดึง yfinance, ดึงไม่ได้ใช้ cache เก่า, ไม่มีเลยใช้ข้อมูลจำลอง
    allow_synthetic=False (บนคลาวด์) → ดึงไม่ได้ให้ล้มเหลวเลย ห้ามใช้ข้อมูลปลอมกับพอร์ตจริง"""
    os.makedirs("data", exist_ok=True)
    if source == "synthetic":
        return clean(synthetic()), "synthetic"
    if not refresh and os.path.exists(CACHE):
        cached = pd.read_pickle(CACHE)
        fresh = pd.Timestamp.now().normalize() - cached["close"].index[-1] <= pd.Timedelta(days=1)
        if fresh or source == "cache":
            return clean(cached), "cache"
    try:
        d = drop_unfinished_day(fetch_yfinance())
        pd.to_pickle(d, CACHE)
        return clean(d), "yfinance"
    except Exception as e:
        print("ดึงข้อมูลไม่ได้:", e)
        if os.path.exists(CACHE):
            return clean(pd.read_pickle(CACHE)), "cache"
        if source == "yfinance" or not allow_synthetic:
            raise
        return clean(synthetic()), "synthetic"


def truncate(d, as_of):
    """ตัดข้อมูลให้เหลือถึงวัน as_of — ใช้ทดสอบ forward test ย้อนหลังโดยไม่เห็นอนาคต"""
    as_of = pd.Timestamp(as_of)
    return {"close": d["close"].loc[:as_of], "open": d["open"].loc[:as_of], "fx": d["fx"].loc[:as_of]}
