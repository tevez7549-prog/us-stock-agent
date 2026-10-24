"""เอเจนต์ 2 — ข้อมูล: ดึง → ทำความสะอาด → ตรวจคุณภาพ → เก็บ
ผลลัพธ์: dict {"close", "open"} เป็นตาราง วัน × ticker (ราคาปรับปันผล/แตกพาร์แล้ว) + "fx" (บาทต่อดอลลาร์)
"""
import os
import numpy as np
import pandas as pd

import config

CACHE = "data/prices.pkl"
DIAG = {}   # รายละเอียดการดึงข้อมูลรอบล่าสุด (บันทึกลง journal ให้ตรวจย้อนหลังได้)


def core_tickers():
    """ตัวที่ขาดไม่ได้ (ETF/ป้องกัน/เงินสด/ดัชนีอ้างอิง) — หุ้นรายตัวหายได้บางตัว แค่ไม่ถูกเลือกรอบนั้น"""
    return [t for t, m in config.UNIVERSE.items() if m["kind"] != "stock"] + [config.BENCHMARK]


def _download(tickers, start):
    import yfinance as yf
    raw = yf.download(tickers, start=start, auto_adjust=True, progress=False, group_by="column", threads=True)
    if raw is None or raw.empty:
        return None, None
    c, o = raw["Close"], raw["Open"]
    if isinstance(c, pd.Series):   # ตัวเดียว
        c, o = c.to_frame(tickers[0]), o.to_frame(tickers[0])
    return c, o


def fetch_yfinance(start=config.DATA_START, extra=()):
    """ดึงทีละ 100 ตัว (กัน Yahoo ตัดการเชื่อมต่อ) ตัวที่ได้ข้อมูลว่างลองใหม่อีกรอบ
    extra = ตัวที่ถืออยู่แต่หลุดจากรายชื่อแล้ว (ต้องมีราคาไว้ขาย)"""
    tickers = list(dict.fromkeys(list(config.UNIVERSE) + list(extra) + [config.FX_TICKER]))
    closes, opens = [], []
    for i in range(0, len(tickers), 100):
        c, o = _download(tickers[i:i + 100], start)
        if c is not None:
            closes.append(c)
            opens.append(o)
    if not closes:
        raise RuntimeError("yfinance ไม่ได้ข้อมูลกลับมา (เช็คอินเทอร์เน็ต)")
    close, open_ = pd.concat(closes, axis=1), pd.concat(opens, axis=1)
    close = close.loc[:, ~close.columns.duplicated()]
    open_ = open_.loc[:, ~open_.columns.duplicated()]
    missing = [t for t in tickers if t not in close.columns or close[t].dropna().empty]
    if missing:
        c, o = _download(missing, start)
        if c is not None:
            close = close.drop(columns=[t for t in c.columns if t in close.columns]).join(c, how="outer")
            open_ = open_.drop(columns=[t for t in o.columns if t in open_.columns]).join(o, how="outer")
    close = close.dropna(axis=1, how="all")
    open_ = open_.reindex(columns=close.columns)
    lost = [t for t in core_tickers() + [config.FX_TICKER] if t not in close.columns]
    if lost:
        raise RuntimeError(f"ดึงสินทรัพย์หลักไม่ได้: {', '.join(lost)}")
    fx = close.pop(config.FX_TICKER)
    open_ = open_.drop(columns=[config.FX_TICKER], errors="ignore")
    # ค่าเงิน (THB=X) ซื้อขาย 24 ชม. ทำให้มีแถวของวันที่ตลาดสหรัฐไม่ได้เปิด → เก็บเฉพาะวันที่ SPY มีราคาจริง
    us_day = close[config.BENCHMARK].notna()
    last_row = close.iloc[-1]
    DIAG.update(raw_last=str(close.index[-1].date()), spy_last=str(close[config.BENCHMARK].last_valid_index().date()),
                tickers_on_raw_last=int(last_row.notna().sum()), tickers=int(close.shape[1]))
    return {"close": close[us_day], "open": open_[us_day], "fx": fx.ffill()[us_day]}


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
    # ETF รายกลุ่ม (kind sector) สุ่มทีหลังสุด → ข้อมูลจำลองของตัวอื่นเหมือนเดิมทุกตัว (ผลทดสอบเดิมไม่เปลี่ยน)
    for t, meta in sorted(config.UNIVERSE.items(), key=lambda x: x[1]["kind"] == "sector"):
        k = meta["kind"]
        beta = {"stock": 1.2, "etf": 1.0, "sector": 1.0, "defensive": -0.25, "cash": 0.0}[k]
        idio = {"stock": 0.014, "etf": 0.004, "sector": 0.004, "defensive": 0.007, "cash": 0.0003}[k]
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
    DIAG.update(ny_now=ny.strftime("%Y-%m-%d %H:%M"), dropped_unfinished=bool(last.date() == ny.date() and (ny.hour, ny.minute) < (16, 30)))
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
    """สินทรัพย์หลักมีปัญหา = ไม่ผ่าน (ห้ามอนุมัติ) / หุ้นรายตัวมีปัญหา = แค่ข้อสังเกต (ตัวนั้นไม่ถูกเลือก)"""
    c = d["close"]
    last = c.index[-1]
    core = set(core_tickers())
    rep = {"last_date": str(last.date()), "rows": len(c), "tickers": c.shape[1], "issues": []}
    expected = [t for t in config.UNIVERSE if t not in c.columns]
    if expected:
        rep["issues"].append({"ticker": "+", "issue": f"ไม่มีข้อมูล {len(expected)} ตัว: {', '.join(expected[:8])}"})
    first = c.apply(lambda s: s.first_valid_index())
    tail = c.iloc[-1]
    for t in c.columns:
        s = c[t]
        if first[t] is None:
            rep["issues"].append({"ticker": t, "issue": "ไม่มีข้อมูลเลย"})
            continue
        miss = s.loc[first[t]:].isna().mean()
        if miss > 0.02:
            rep["issues"].append({"ticker": t, "issue": f"ข้อมูลหาย {miss:.1%}"})
        if t in core:   # หุ้นรายตัวกระโดด 40% ในวันเดียวเกิดได้จริง (งบ/ดีล) ตรวจเฉพาะสินทรัพย์หลัก
            jump = s.pct_change(fill_method=None).abs().max()
            if jump > 0.4:
                rep["issues"].append({"ticker": t, "issue": f"ราคากระโดดวันเดียว {jump:.0%} (เช็คการแตกพาร์)"})
        if pd.isna(tail[t]):
            rep["issues"].append({"ticker": t, "issue": "ไม่มีราคาวันล่าสุด"})
    if as_of is not None:
        age = (pd.Timestamp(as_of) - last).days
        rep["age_days"] = age
        if age > 5:
            rep["issues"].append({"ticker": "*", "issue": f"ข้อมูลเก่า {age} วัน"})
    rep["ok"] = not any(i["ticker"] == "*" or (i["ticker"] in core and ("ไม่มี" in i["issue"] or "กระโดด" in i["issue"]))
                        for i in rep["issues"])
    n = len(rep["issues"])
    rep["issues"] = sorted(rep["issues"], key=lambda i: (i["ticker"] not in core and i["ticker"] != "*"))[:40]
    rep["n_issues"] = n
    return rep


def load(source="auto", refresh=False, allow_synthetic=True, extra=()):
    """auto = ใช้ cache ถ้าอัปเดตวันนี้แล้ว, ไม่งั้นดึง yfinance, ดึงไม่ได้ใช้ cache เก่า, ไม่มีเลยใช้ข้อมูลจำลอง
    allow_synthetic=False (บนคลาวด์) → ดึงไม่ได้ให้ล้มเหลวเลย ห้ามใช้ข้อมูลปลอมกับพอร์ตจริง"""
    os.makedirs("data", exist_ok=True)
    if source == "synthetic":
        return clean(synthetic()), "synthetic"
    if not refresh and os.path.exists(CACHE):
        cached = pd.read_pickle(CACHE)
        fresh = (pd.Timestamp.now().normalize() - cached["close"].index[-1] <= pd.Timedelta(days=1)
                 and len(set(config.UNIVERSE) & set(cached["close"].columns)) >= 0.9 * len(config.UNIVERSE))  # cache รายชื่อเก่า = ดึงใหม่
        if fresh or source == "cache":
            return clean(cached), "cache"
    try:
        d = drop_unfinished_day(fetch_yfinance(extra=extra))
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
