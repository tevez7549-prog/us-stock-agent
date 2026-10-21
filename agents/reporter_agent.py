"""เอเจนต์ 11 — ผู้แปลผล: เปลี่ยนแพ็กเกจตัวเลขเป็นรายงานภาษาไทยที่อ่านจบใน 5 นาที
ค่าเริ่มต้นใช้ template (ตัวเลขถูกแน่นอน) ถ้ามี Anthropic key จะให้ Claude เขียนสรุปภาษาคนเพิ่ม
โดยห้ามสร้างตัวเลขใหม่ — ใช้ได้เฉพาะตัวเลขที่อยู่ในแพ็กเกจ
"""
import json

import config

REGIME_TH = {"risk_on": "ขาขึ้น", "caution": "ระวัง", "risk_off": "ขาลง"}


def template(packet, audit):
    reg = packet["regime"]
    risky = sum(w for t, w in packet["target"].items() if config.UNIVERSE[t]["kind"] in ("etf", "stock"))
    buys = [o for o in packet["orders"] if o["side"] == "BUY"]
    sells = [o for o in packet["orders"] if o["side"] == "SELL"]
    lines = [f"[{packet.get('strategy', 'Momentum')}]"]
    if packet.get("ai_view"):
        lines.append("มุมมอง AI: " + packet["ai_view"])
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
    m = packet.get("macro") or {}
    if m.get("enabled") and m.get("note"):
        lines.append(f"มหภาค ({ {'normal': 'ปกติ', 'elevated': 'ระวัง', 'high': 'เสี่ยงสูง'}.get(m.get('risk_level'), '') }): {m['note']}")
    n = packet.get("news") or {}
    if n.get("enabled") and n.get("flags"):
        flagged = [f for f in n["flags"] if f.get("flag") != "ok"]
        if flagged:
            lines.append("ธงข่าว: " + " · ".join(f"{f['ticker']} {f.get('flag')} ({f.get('reason', '')[:50]})" for f in flagged))
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
    from agents import llm
    if not llm.enabled():
        return text
    data = llm.ask_json("reporter",
        "คุณคือผู้แปลผลให้เจ้าของพอร์ตที่ไม่ใช่นักการเงิน เขียนภาษาไทยง่ายๆ ห้ามสร้างตัวเลขใหม่ ใช้เฉพาะตัวเลขในข้อมูล "
        "ห้ามแนะนำให้ทำเกินกว่าที่ออเดอร์เสนอ รูปแบบ: {\"summary\":\"สรุป 3-5 ประโยค\",\"risks\":[\"ความเสี่ยง 1\",\"ความเสี่ยง 2\"]}",
        json.dumps({"summary": text, "orders": packet["orders"], "audit": audit}, ensure_ascii=False), 800)
    if not data or not data.get("summary"):
        return text
    risks = "".join(f"\n• {r}" for r in (data.get("risks") or [])[:3])
    return str(data["summary"])[:800] + ("\n\nสิ่งที่ควรรู้:" + risks if risks else "") + "\n\n— ตัวเลขอ้างอิง —\n" + text
