"""กลยุทธ์ 3 — Risk Parity: กระจายความเสี่ยง ไม่ทายทิศทาง ไม่เลือกหุ้นรายตัว

กฎ (ปรับรายเดือน):
1. สินทรัพย์ 4 ก้อน: หุ้นสหรัฐ (SPY), หุ้นต่างประเทศ (EFA), พันธบัตรยาว (TLT), ทอง (GLD)
2. น้ำหนัก ∝ 1 / ความผันผวน 3 เดือน  → ก้อนที่แกว่งแรงได้เงินน้อย ทุกก้อนมีผลต่อพอร์ตพอๆ กัน
3. จำกัดเพดานรายตัว 40% (ให้ไม่กลายเป็นพอร์ตพันธบัตรล้วนตอนหุ้นผันผวน)
4. สภาวะตลาดขาลง: ตัดส่วนหุ้นลงตาม exposure แล้วโยกไปเงินสด

จุดประสงค์: เป็น "ตัวเทียบ" ว่ากลยุทธ์ที่พยายามเลือกตัวดีๆ ชนะการกระจายความเสี่ยงเฉยๆ ได้ไหม
"""
import config

ASSETS = {"SPY": "หุ้นสหรัฐ", "EFA": "หุ้นต่างประเทศ", "TLT": "พันธบัตรยาว", "GLD": "ทอง"}
CAP = 0.40
CASH = "SHY"


def propose(close, ind, date, regime, p=None, held=()):
    vol = ind["vol"].loc[date]
    ok = {t for t in ASSETS if vol.get(t) == vol.get(t) and vol.get(t, 0) > 0}
    if len(ok) < 3:
        return {"weights": {CASH: 1.0}, "why": {CASH: "ข้อมูลความผันผวนยังไม่ครบ"}, "ranking": []}

    inv = {t: 1 / vol[t] for t in ok}
    w = {t: v / sum(inv.values()) for t, v in inv.items()}
    # เพดานรายตัว — ส่วนเกินเฉลี่ยให้ตัวที่เหลือ (ทำซ้ำจนนิ่ง)
    for _ in range(5):
        over = {t: w[t] - CAP for t in w if w[t] > CAP}
        if not over:
            break
        spill = sum(over.values())
        under = [t for t in w if t not in over]
        for t in over:
            w[t] = CAP
        tot = sum(w[t] for t in under)
        for t in under:
            w[t] += spill * w[t] / tot

    # ตลาดขาลง → ตัดหุ้นลงตาม exposure
    expo = regime["exposure"]
    cash = 0.0
    for t in ("SPY", "EFA"):
        if t in w and expo < 1:
            cut = w[t] * (1 - expo)
            w[t] -= cut
            cash += cut
    if cash > 0:
        w[CASH] = cash

    why = {t: f"{ASSETS[t]} · ความผันผวน {vol[t]:.0%}/ปี → น้ำหนัก {w[t]:.0%}" for t in w if t in ASSETS}
    if CASH in w:
        why[CASH] = "สภาวะตลาดขาลง ตัดส่วนหุ้นไปเงินสด"
    ranking = sorted(({"ticker": t, "rank": 0, "mom": round(float(vol[t]), 4), "above_sma": True} for t in ok),
                     key=lambda x: x["mom"])
    for i, r in enumerate(ranking):
        r["rank"] = i + 1
    return {"weights": {t: round(v, 4) for t, v in w.items() if v > 1e-4}, "why": why, "ranking": ranking}
