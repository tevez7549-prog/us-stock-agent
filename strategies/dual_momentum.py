"""กลยุทธ์ 2 — Dual Momentum (แบบ Antonacci): ซื้อขายน้อย ตัดสินใจระดับ "กลุ่มสินทรัพย์"

กฎ (ปรับรายเดือน):
1. เทียบผลตอบแทน 12 เดือนของ 3 กลุ่ม: หุ้นสหรัฐ (SPY) / หุ้นต่างประเทศ (EFA) / พันธบัตรยาว (TLT)
2. "absolute momentum": กลุ่มหุ้นต้องชนะพันธบัตรสั้น (SHY) ถึงจะถือได้ ไม่งั้นถือ IEF (พันธบัตรกลาง)
3. "relative momentum": ถ้าหุ้นผ่านข้อ 2 ให้ถือกลุ่มหุ้นที่แรงกว่าระหว่าง SPY กับ EFA ทั้งก้อน
4. ส่วน exposure จากเอเจนต์สภาวะตลาดใช้แค่ลดสัดส่วนเวลาตลาดขาลง (ไม่ใช้เลือกตัว)

ถือครั้งละ 1–2 ตัวเท่านั้น → ซื้อขายน้อยมาก เหมาะเทียบกับ Momentum ที่ซื้อขายถี่
"""
import config

LOOKBACK = 252          # 12 เดือน
EQUITY_US, EQUITY_INTL, BOND_LONG, BOND_MID, CASH = "SPY", "EFA", "TLT", "IEF", "SHY"


def propose(close, ind, date, regime, p=None, held=()):
    def ret(t):
        s = close[t].loc[:date].dropna()
        return float(s.iloc[-1] / s.iloc[-LOOKBACK - 1] - 1) if len(s) > LOOKBACK else None

    r = {t: ret(t) for t in (EQUITY_US, EQUITY_INTL, BOND_LONG, CASH)}
    if any(v is None for v in r.values()):
        return {"weights": {CASH: 1.0}, "why": {CASH: "ข้อมูลยังไม่ครบ 12 เดือน"}, "ranking": []}

    ranking = sorted(((t, r[t]) for t in (EQUITY_US, EQUITY_INTL, BOND_LONG)), key=lambda x: -x[1])
    best_eq = EQUITY_US if r[EQUITY_US] >= r[EQUITY_INTL] else EQUITY_INTL
    why = {}
    if r[best_eq] > r[CASH]:
        pick = best_eq
        why[pick] = (f"หุ้น{'สหรัฐ' if pick == EQUITY_US else 'ต่างประเทศ'}แรงกว่าอีกกลุ่ม "
                     f"({r[pick]:+.0%} 12 เดือน) และชนะพันธบัตรสั้น ({r[CASH]:+.0%})")
    else:
        pick = BOND_MID
        why[pick] = f"หุ้นทั้งสองกลุ่มแพ้พันธบัตรสั้น ({r[best_eq]:+.0%} vs {r[CASH]:+.0%}) → หลบไปพันธบัตร"

    expo = regime["exposure"] if pick != BOND_MID else 1.0
    w = {pick: expo}
    if expo < 1:
        w[CASH] = 1 - expo
        why[CASH] = "สภาวะตลาดขาลง ถือเงินสดส่วนหนึ่ง"
    return {"weights": {t: round(v, 4) for t, v in w.items()},
            "why": why,
            "ranking": [{"ticker": t, "rank": i + 1, "mom": round(v, 4), "above_sma": True} for i, (t, v) in enumerate(ranking)]
                       + [{"ticker": CASH, "rank": 0, "mom": round(r[CASH], 4), "above_sma": True}]}
