"""เอเจนต์ 5 — ข่าวบริษัท (Claude)
มีสิทธิ์แค่ "ตัดออก" เท่านั้น: คืนรายชื่อ ticker ที่ห้ามซื้อเพิ่ม เพิ่มความเสี่ยงไม่ได้
ดึงหัวข่าวล่าสุดจาก yfinance → Claude ติดธง → ถ้าเรียกไม่สำเร็จ ถือว่า "ไม่มีธง" แต่บันทึกว่าล้มเหลว (ไม่เดาเอง)
"""
import json

import config
from agents import llm

SYSTEM = """คุณคือนักวิเคราะห์ข่าวที่ทำหน้าที่ "กรองความเสี่ยง" เท่านั้น ไม่ใช่เลือกหุ้น
ติดธง "block" เฉพาะเมื่อมีข่าวร้ายร้ายแรงและเฉพาะเจาะจงกับบริษัทนั้นใน 7 วันล่าสุด เช่น
ถูกสอบสวนฉ้อโกง, ผู้บริหารลาออกกะทันหัน, ลดคาดการณ์รายได้หนัก, คดีใหญ่, สินค้าหลักถูกแบน, ผิดนัดชำระหนี้
ติดธง "caution" เมื่อจะประกาศงบภายใน 5 วันทำการ หรือมีข่าวลบแต่ไม่ร้ายแรง
ข่าวทั่วไป / ข่าวตลาดโดยรวม / ราคาขึ้นลง = "ok"
รูปแบบ: {"flags":[{"ticker":"AAPL","flag":"ok|caution|block","reason":"สั้นๆ ภาษาไทย"}]}"""


def _headlines(tickers):
    import yfinance as yf
    out = {}
    for t in tickers:
        try:
            items = yf.Ticker(t).news or []
            out[t] = [((i.get("content") or i).get("title") or "")[:160] for i in items[:8]]
        except Exception:
            out[t] = []
    return out


def screen(tickers, headlines=None):
    stocks = [t for t in tickers if config.UNIVERSE.get(t, {}).get("kind") == "stock"]
    if not llm.enabled():
        return {"enabled": False, "blocked": [], "caution": [], "flags": [], "note": "ยังไม่ได้ใส่ Anthropic API key"}
    if not stocks:
        return {"enabled": True, "blocked": [], "caution": [], "flags": [], "note": "ไม่มีหุ้นรายตัวให้ตรวจ"}
    heads = headlines if headlines is not None else _headlines(stocks)
    if not any(heads.values()):
        return {"enabled": True, "blocked": [], "caution": [], "flags": [], "note": "ดึงหัวข่าวไม่ได้"}
    data = llm.ask_json("news", SYSTEM, "หัวข่าวล่าสุดแยกตาม ticker:\n" + json.dumps(heads, ensure_ascii=False))
    if not data:
        return {"enabled": True, "blocked": [], "caution": [], "flags": [], "error": "News Agent ล้มเหลว (ใช้ค่า: ไม่มีธง)"}
    flags = [f for f in data.get("flags", []) if f.get("ticker") in stocks]
    return {"enabled": True, "flags": flags,
            "blocked": [f["ticker"] for f in flags if f.get("flag") == "block"],
            "caution": [f["ticker"] for f in flags if f.get("flag") == "caution"]}
