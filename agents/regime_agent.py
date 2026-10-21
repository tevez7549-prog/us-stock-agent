"""เอเจนต์ 3 — สภาวะตลาด: ตอนนี้ควรถือของเสี่ยงได้กี่ %
ใช้แค่ข้อมูลถึงวันที่ถาม (ไม่มีอนาคต)
"""
import config


def indicators(close, p=config.STRATEGY):
    """คำนวณครั้งเดียวทั้งตาราง — ค่าแต่ละวันใช้ข้อมูลถึงวันนั้นเท่านั้น (rolling ไม่มองไปข้างหน้า)"""
    rets = close.pct_change(fill_method=None)
    return {
        "sma": close.rolling(p["trend_sma"], min_periods=p["trend_sma"]).mean(),
        "mom": 0.5 * close.pct_change(p["mom_short"], fill_method=None)
               + 0.5 * close.pct_change(p["mom_long"], fill_method=None),
        "vol": rets.rolling(p["vol_window"], min_periods=p["vol_window"]).std() * (252 ** 0.5),
    }


def assess(close, ind, date, p=config.STRATEGY, prev_up=None):
    """prev_up = สภาวะครั้งก่อน (True/False/None) ใช้ทำแถบกันสัญญาณสลับไปมา"""
    spy = config.BENCHMARK
    px, sma = close.loc[date], ind["sma"].loc[date]
    risky = [t for t, m in config.UNIVERSE.items() if m["kind"] in ("etf", "stock") and t in close.columns]
    valid = [t for t in risky if px[t] == px[t] and sma[t] == sma[t]]
    breadth = sum(px[t] > sma[t] for t in valid) / len(valid) if valid else 0.0
    band = p.get("regime_band", 0)
    if sma[spy] != sma[spy]:
        spy_up = False
    elif prev_up is True:
        spy_up = bool(px[spy] > sma[spy] * (1 - band))   # ขาขึ้นอยู่ → ต้องหลุดลงเกินแถบถึงเปลี่ยน
    elif prev_up is False:
        spy_up = bool(px[spy] > sma[spy] * (1 + band))   # ขาลงอยู่ → ต้องทะลุขึ้นเกินแถบถึงเปลี่ยน
    else:
        spy_up = bool(px[spy] > sma[spy])

    exposure = 1.0 if spy_up else p["risk_off_exposure"]
    if breadth < 0.4:
        exposure *= p["weak_breadth_cut"]
    reasons = [f"S&P500 {'เหนือ' if spy_up else 'ต่ำกว่า'}เส้น {p['trend_sma']} วัน",
               f"สินทรัพย์เสี่ยงอยู่เหนือเทรนด์ {breadth:.0%}"]
    return {
        "regime": "risk_on" if spy_up and breadth >= 0.4 else ("caution" if spy_up or breadth >= 0.4 else "risk_off"),
        "exposure": round(exposure, 3),
        "breadth": round(breadth, 3),
        "spy_above_sma": spy_up,
        "reasons": reasons,
    }


def top_stocks(close, ind, date, n=30):
    """หุ้นรายตัวที่โมเมนตัมสูงสุด n ตัว (มีข้อมูลครบ) — ใช้เลือกว่าจะอ่านข่าว/ให้ AI ดูตัวไหน"""
    mom, sma, px = ind["mom"].loc[date], ind["sma"].loc[date], close.loc[date]
    ok = [t for t, m in config.UNIVERSE.items()
          if m["kind"] == "stock" and t in close.columns and mom[t] == mom[t] and sma[t] == sma[t] and px[t] == px[t]]
    return sorted(ok, key=lambda t: -mom[t])[:n]
