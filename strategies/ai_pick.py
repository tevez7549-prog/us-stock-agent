"""กลยุทธ์ 4 — AI Pick: ให้ Claude เลือกพอร์ตเองจากข้อมูลตัวเลข (ภายใต้ Risk Manager เดียวกัน)

Claude ได้เห็น: โมเมนตัม / ความผันผวน / ตำแหน่งเทียบเส้น 200 วัน ของทุกตัว, สภาวะตลาด, สิ่งที่ถืออยู่
Claude ตอบ: น้ำหนัก (รวม ≤ 1) + เหตุผลต่อตัว
สิ่งที่โค้ดบังคับหลังจากนั้น: ตัวที่ไม่อยู่ในรายชื่อถูกตัด, น้ำหนักติดลบถูกตัด, ส่วนที่เหลือเป็นเงินสด,
แล้ว Risk Manager จับเพดานรายตัว/กลุ่ม/เงินสด/drawdown เหมือนพอร์ตอื่น — AI เพิ่มความเสี่ยงเกินกฎไม่ได้

ล้มเหลว (ไม่มี key / ตอบผิดรูปแบบ) → ถือพอร์ตเดิม ไม่เปลี่ยน
"""
import json

import config
from agents import llm

CASH = "SHY"

SYSTEM = """คุณคือผู้จัดการกองทุนที่ลงทุนระยะกลาง (ถือเป็นสัปดาห์ถึงเดือน) ในหุ้น/ETF สหรัฐจากรายชื่อที่กำหนดเท่านั้น
เป้าหมาย: ผลตอบแทนที่ดีโดยขาดทุนหนักสุด (drawdown) ต่ำ ไม่ใช่ผลตอบแทนสูงสุด
ข้อมูลที่ให้คือทั้งหมดที่คุณรู้ ห้ามใช้ความรู้เรื่องราคาหรือเหตุการณ์ในอนาคต
กติกา:
- เลือกได้ 3–8 ตัว น้ำหนักรวมไม่เกิน 1.0 ส่วนที่เหลือระบบถือเป็นเงินสดให้
- หุ้นรายตัวไม่เกิน 0.15 / ETF ไม่เกิน 0.25 (เกินแล้วระบบจะตัดทิ้ง)
- ถ้าสภาวะตลาดเป็น risk_off ควรลดของเสี่ยงและเพิ่มพันธบัตร/ทอง
- อย่าเปลี่ยนพอร์ตทั้งหมดทุกสัปดาห์ ถ้าของเดิมยังดีให้ถือต่อ (ซื้อขายมีต้นทุน)
รูปแบบ: {"weights":{"TICKER":0.15,...},"why":{"TICKER":"เหตุผลสั้นๆ ภาษาไทย",...},"view":"มุมมอง 1-2 ประโยค"}"""


def propose(close, ind, date, regime, p=None, held=(), extra=None):
    px, sma, mom, vol = (x.loc[date] for x in (close, ind["sma"], ind["mom"], ind["vol"]))
    table = []
    for t, m in config.UNIVERSE.items():
        if t not in close.columns or not all(v == v for v in (px[t], sma[t], mom[t], vol[t])):
            continue
        table.append({"ticker": t, "kind": m["kind"], "group": m["group"],
                      "mom_6_12m": round(float(mom[t]), 3), "vol_3m": round(float(vol[t]), 3),
                      "vs_sma200": round(float(px[t] / sma[t] - 1), 3), "held": t in held})
    ranked = sorted((r for r in table if r["kind"] in ("etf", "stock")), key=lambda r: -r["mom_6_12m"])
    ranking = [{"ticker": r["ticker"], "rank": i + 1, "mom": r["mom_6_12m"], "above_sma": r["vs_sma200"] > 0}
               for i, r in enumerate(ranked[:12])]
    hold_w = {t: 1.0 / max(len(held), 1) for t in held} or {CASH: 1.0}
    fallback = {"weights": hold_w, "why": {t: "AI ล้มเหลว → ถือพอร์ตเดิม" for t in hold_w}, "ranking": ranking}

    if not llm.enabled():
        fallback["why"] = {t: "ยังไม่ได้ใส่ Anthropic API key → ถือเงินสด/พอร์ตเดิม" for t in hold_w}
        return fallback
    user = json.dumps({"date": str(date.date()), "regime": regime.get("regime"), "exposure_hint": regime.get("exposure"),
                       "currently_held": list(held), "news": (extra or {}).get("news"), "macro": (extra or {}).get("macro"),
                       "assets": table}, ensure_ascii=False)
    data = llm.ask_json("ai_pick", SYSTEM, user, 1200)
    if not data or not isinstance(data.get("weights"), dict):
        return fallback
    w = {}
    for t, v in data["weights"].items():
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if t in config.UNIVERSE and t in close.columns and v > 0:
            w[t] = v
    tot = sum(w.values())
    if not w or tot <= 0:
        return fallback
    if tot > 1:
        w = {t: v / tot for t, v in w.items()}
    if sum(w.values()) < 1:
        w[CASH] = w.get(CASH, 0) + 1 - sum(w.values())
    why = {t: str((data.get("why") or {}).get(t, "AI เลือก"))[:120] for t in w}
    if CASH in w and CASH not in (data.get("why") or {}):
        why[CASH] = "ส่วนที่ AI ไม่ได้จัดสรร ถือเป็นเงินสด"
    return {"weights": {t: round(v, 4) for t, v in w.items()}, "why": why, "ranking": ranking,
            "view": str(data.get("view", ""))[:300]}
