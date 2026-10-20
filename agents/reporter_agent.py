"""เอเจนต์ 11 — ผู้แปลผล: เปลี่ยนแพ็กเกจตัวเลขเป็นรายงานภาษาไทยที่อ่านจบใน 5 นาที
ค่าเริ่มต้นใช้ template (ตัวเลขถูกแน่นอน) ถ้าเปิด USE_CLAUDE จะให้ Claude เขียนสรุปภาษาคนเพิ่ม
โดยห้ามสร้างตัวเลขใหม่ — ใช้ได้เฉพาะตัวเลขที่อยู่ในแพ็กเกจ
"""
import json
import os

import config

REGIME_TH = {"risk_on": "ขาขึ้น", "caution": "ระวัง", "risk_off": "ขาลง"}


def template(packet, audit):
    reg = packet["regime"]
    risky = sum(w for t, w in packet["target"].items() if config.UNIVERSE[t]["kind"] in ("etf", "stock"))
    buys = [o for o in packet["orders"] if o["side"] == "BUY"]
    sells = [o for o in packet["orders"] if o["side"] == "SELL"]
    lines = []
    if packet["orders"]:
        lines.append(f"ตลาด{REGIME_TH[reg['regime']]} · เสนอขาย {len(sells)} ซื้อ {len(buys)} รายการ · "
                     f"หลังปรับจะถือของเสี่ยง {risky:.0%} ที่เหลือเป็นสินทรัพย์ป้องกัน/เงินสด")
    else:
        lines.append(f"ตลาด{REGIME_TH[reg['regime']]} · สัปดาห์นี้ไม่ต้องซื้อขาย พอร์ตตรงกับเป้าหมายอยู่แล้ว")
    lines.append("")
    lines.append("เหตุผลด้านสภาวะตลาด: " + " · ".join(reg["reasons"]))
    sma, spy, band = reg.get("spy_sma"), reg.get("spy_price"), config.STRATEGY["regime_band"]
    if sma:
        if reg["spy_above_sma"]:
            lines.append(f"จะเปลี่ยนใจเมื่อ: S&P500 (SPY) ปิดต่ำกว่า {sma * (1 - band):,.2f} "
                         f"(ตอนนี้ {spy:,.2f}) ระบบจะลดของเสี่ยงเหลือ {config.STRATEGY['risk_off_exposure']:.0%}")
        else:
            lines.append(f"จะเปลี่ยนใจเมื่อ: SPY ปิดเหนือ {sma * (1 + band):,.2f} (ตอนนี้ {spy:,.2f}) ระบบจะกลับมาถือของเสี่ยงเต็ม")
    if packet.get("risk_log"):
        lines.append("Risk Manager ปรับ: " + " · ".join(packet["risk_log"]))
    if audit["fails"]:
        lines.append("⛔ ผู้ตรวจสอบไม่ผ่าน: " + " · ".join(audit["fails"]) + " — ห้ามอนุมัติรอบนี้")
    elif audit["warns"]:
        lines.append("ข้อสังเกตจากผู้ตรวจสอบ: " + " · ".join(audit["warns"]))
    else:
        lines.append("ผู้ตรวจสอบ: ผ่านทุกข้อ")
    return "\n".join(lines)


def write(packet, audit):
    text = template(packet, audit)
    if not config.USE_CLAUDE or not os.environ.get("ANTHROPIC_API_KEY"):
        return text
    try:
        import anthropic
        prompt = ("เขียนสรุปแพ็กเกจลงทุนรายสัปดาห์ให้เจ้าของพอร์ตอ่าน เป็นภาษาไทย ไม่เกิน 6 บรรทัด "
                  "ห้ามสร้างตัวเลขใหม่ ใช้เฉพาะตัวเลขในข้อมูล ห้ามแนะนำให้ทำเกินกว่าที่ออเดอร์เสนอ "
                  "บอกความเสี่ยงหลักที่เจ้าของควรรู้ 1-2 ข้อ\n\nข้อมูล:\n"
                  + json.dumps({"summary": text, "orders": packet["orders"], "audit": audit}, ensure_ascii=False))
        msg = anthropic.Anthropic().messages.create(model=config.CLAUDE_MODEL, max_tokens=800,
                                                    messages=[{"role": "user", "content": prompt}])
        return msg.content[0].text.strip() + "\n\n— ตัวเลขอ้างอิง —\n" + text
    except Exception as e:
        return text + f"\n(ผู้แปลผล AI ล้มเหลว ใช้ template แทน: {e})"
