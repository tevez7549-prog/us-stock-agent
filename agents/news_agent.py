"""เอเจนต์ 5 — ข่าวบริษัท (Claude) — ปิดไว้ก่อนจนถึงเฟส 3
มีสิทธิ์แค่ "ตัดออก" เท่านั้น (คืนรายชื่อ ticker ที่ห้ามซื้อเพิ่ม) เพิ่มความเสี่ยงไม่ได้

ตอนเปิดใช้: ดึงหัวข่าวล่าสุดจาก yfinance → ส่งให้ Claude ตอบเป็น JSON ตาม schema
ถ้าเรียกไม่สำเร็จ / ตอบผิดรูปแบบ → ถือว่า "ไม่มีธง" แต่บันทึกว่าเอเจนต์ล้มเหลว (ไม่เดาเอง)
"""
import json
import os

import config

PROMPT = """คุณคือนักวิเคราะห์ข่าวที่ทำหน้าที่ "กรองความเสี่ยง" เท่านั้น
ด้านล่างคือหัวข่าวล่าสุดของสินทรัพย์ที่ระบบกำลังจะซื้อ ให้ติดธง "block" เฉพาะเมื่อมีข่าวร้ายร้ายแรงและเฉพาะเจาะจง
(เช่น ถูกสอบสวนฉ้อโกง, ผู้บริหารลาออกกะทันหัน, ลดคาดการณ์รายได้หนัก, คดีใหญ่, สินค้าหลักถูกแบน)
ข่าวทั่วไปหรือข่าวตลาดโดยรวม = "ok"
ตอบเป็น JSON อย่างเดียว: {"flags": [{"ticker": "...", "flag": "ok"|"block", "reason": "สั้นๆ ภาษาไทย"}]}

ข่าว:
"""


def _headlines(tickers):
    import yfinance as yf
    out = {}
    for t in tickers:
        try:
            items = yf.Ticker(t).news or []
            out[t] = [(i.get("content") or i).get("title", "") for i in items[:8]]
        except Exception:
            out[t] = []
    return out


def screen(tickers):
    if not config.USE_CLAUDE or not os.environ.get("ANTHROPIC_API_KEY"):
        return {"enabled": False, "blocked": [], "flags": [], "note": "ยังไม่เปิดใช้ (เฟส 3)"}
    try:
        import anthropic
        heads = _headlines([t for t in tickers if config.UNIVERSE[t]["kind"] == "stock"])
        if not any(heads.values()):
            return {"enabled": True, "blocked": [], "flags": [], "note": "ไม่มีข่าวให้อ่าน"}
        msg = anthropic.Anthropic().messages.create(
            model=config.CLAUDE_MODEL, max_tokens=1500,
            messages=[{"role": "user", "content": PROMPT + json.dumps(heads, ensure_ascii=False)}])
        text = msg.content[0].text
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        flags = [f for f in data.get("flags", []) if f.get("ticker") in tickers]
        return {"enabled": True, "flags": flags,
                "blocked": [f["ticker"] for f in flags if f.get("flag") == "block"]}
    except Exception as e:
        return {"enabled": True, "blocked": [], "flags": [], "error": f"News Agent ล้มเหลว: {e}"}
