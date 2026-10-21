"""กลยุทธ์ 1 — Momentum: จัดอันดับโมเมนตัม แล้วเสนอพอร์ตเป้าหมาย (ยังไม่ผ่าน Risk Manager)

กฎ:
1. สินทรัพย์เสี่ยง (ETF/หุ้น) ต้อง โมเมนตัม > 0 และราคา > SMA200
2. เลือก top_n ตัวที่โมเมนตัมสูงสุด ถ่วงน้ำหนักแบบ 1/ความผันผวน (ตัวผันผวนมากได้น้อย)
3. รวมน้ำหนักของเสี่ยง = exposure จากเอเจนต์ 3
4. ส่วนที่เหลือ → สินทรัพย์ป้องกัน (TLT/IEF/GLD) ที่ยังเป็นขาขึ้น ไม่มีก็ถือ SHY (เงินสด)
"""
import config


def propose(close, ind, date, regime, p=config.STRATEGY, held=()):
    """held = สินทรัพย์เสี่ยงที่ถืออยู่ตอนนี้ (ใช้กับ buffer อันดับ)"""
    px, sma, mom, vol = (x.loc[date] for x in (close, ind["sma"], ind["mom"], ind["vol"]))

    def ok(t):
        return all(v == v for v in (px[t], sma[t], mom[t], vol[t])) and vol[t] > 0

    risky = [t for t, m in config.UNIVERSE.items() if m["kind"] in ("etf", "stock") and t in close.columns and ok(t)]
    ranked = sorted(risky, key=lambda t: mom[t], reverse=True)
    rank_of = {t: i + 1 for i, t in enumerate(ranked)}
    qualify = [t for t in ranked if mom[t] > 0 and px[t] > sma[t]]
    keep_rank = p["top_n"] + p.get("hold_buffer", 0)
    picks = [t for t in qualify if t in held and rank_of[t] <= keep_rank][: p["top_n"]]
    picks += [t for t in qualify if t not in picks][: p["top_n"] - len(picks)]
    picks = sorted(picks, key=lambda t: rank_of[t])

    w = {}
    exposure = regime["exposure"] * (len(picks) / p["top_n"])  # หาตัวผ่านเกณฑ์ไม่ครบ = ถือของเสี่ยงน้อยลง
    if picks:
        inv = {t: 1 / vol[t] for t in picks}
        s = sum(inv.values())
        w = {t: exposure * inv[t] / s for t in picks}

    rest = 1 - sum(w.values())
    defensive = [t for t, m in config.UNIVERSE.items() if m["kind"] == "defensive" and t in close.columns and ok(t)]
    good_def = sorted([t for t in defensive if mom[t] > 0 and px[t] > sma[t]], key=lambda t: mom[t], reverse=True)[:2]
    # ป้องกันขาขึ้น 2 ตัว = แบ่งส่วนที่เหลือคนละครึ่ง, 1 ตัว = ได้ครึ่งเดียว อีกครึ่งเป็นเงินสด, 0 ตัว = เงินสดทั้งหมด
    for t in good_def:
        w[t] = rest * 0.5
    w[config.CASH_PROXY] = w.get(config.CASH_PROXY, 0) + max(0.0, 1 - sum(w.values()))

    why = {}
    for t in w:
        if t in picks:
            why[t] = f"โมเมนตัมอันดับ {rank_of[t]} ({mom[t]:+.0%}) อยู่เหนือเส้น 200 วัน"
        elif t == config.CASH_PROXY:
            why[t] = "ส่วนที่เหลือถือเป็นเงินสด"
        else:
            why[t] = f"สินทรัพย์ป้องกันที่ยังเป็นขาขึ้น ({mom[t]:+.0%})"
    return {"weights": {t: round(float(v), 4) for t, v in w.items() if v > 1e-4},
            "why": why,
            "ranking": [{"ticker": t, "rank": rank_of[t], "mom": round(float(mom[t]), 4),
                         "above_sma": bool(px[t] > sma[t])} for t in ranked[:12]]}
