"""เอเจนต์ 6 — มหภาค (Claude): อ่านข่าวตลาดรวม + ค่าเงิน แล้วให้ 2 อย่าง
1. risk_level: normal | elevated | high  → high = ลดของเสี่ยงลง 30% (ลดได้อย่างเดียว เพิ่มไม่ได้)
2. note: สรุป 1–2 ประโยคสำหรับรายงาน

ไม่มีสิทธิ์เลือกหุ้น ไม่มีสิทธิ์เพิ่มสัดส่วน ล้มเหลว = normal (ไม่มีผล) และบันทึกไว้
"""
import json

from agents import llm

SYSTEM = """คุณคือนักวิเคราะห์มหภาคที่ระมัดระวัง ประเมินจากหัวข่าวตลาดและตัวเลขที่ให้เท่านั้น ห้ามใช้ความรู้นอกข้อมูล
risk_level:
- "high" เฉพาะเมื่อมีเหตุการณ์ระดับระบบใน 7 วัน: วิกฤตการเงิน/ธนาคารล้ม, สงครามใหม่ที่กระทบตลาดโลก, เฟดขึ้นดอกเบี้ยฉุกเฉิน, ตลาดร่วง >5% ในสัปดาห์
- "elevated" เมื่อมีประชุมเฟด/CPI/การจ้างงานภายใน 3 วันทำการ หรือข่าวลบชัดเจนแต่ยังไม่ถึงขั้นวิกฤต
- "normal" นอกเหนือจากนั้น (เป็นค่าปกติ ควรตอบบ่อยที่สุด)
รูปแบบ: {"risk_level":"normal|elevated|high","usd_thb_note":"แนวโน้มค่าเงิน 1 ประโยค","note":"สรุป 1-2 ประโยคภาษาไทย"}"""

CUT = {"normal": 1.0, "elevated": 1.0, "high": 0.7}


def _market_headlines():
    import yfinance as yf
    out = {}
    for t in ("SPY", "^VIX", "TLT"):
        try:
            out[t] = [((i.get("content") or i).get("title") or "")[:160] for i in (yf.Ticker(t).news or [])[:8]]
        except Exception:
            out[t] = []
    return out


def assess(close, fx, date, headlines=None):
    if not llm.enabled():
        return {"enabled": False, "risk_level": "normal", "factor": 1.0, "note": "ยังไม่ได้ใส่ Anthropic API key"}
    spy = close["SPY"].loc[:date].dropna()
    facts = {
        "spy_1w": round(float(spy.iloc[-1] / spy.iloc[-6] - 1), 4) if len(spy) > 6 else None,
        "spy_1m": round(float(spy.iloc[-1] / spy.iloc[-22] - 1), 4) if len(spy) > 22 else None,
        "usd_thb": round(float(fx.loc[:date].iloc[-1]), 2),
        "usd_thb_1m_change": round(float(fx.loc[:date].iloc[-1] / fx.loc[:date].iloc[-22] - 1), 4) if len(fx.loc[:date]) > 22 else None,
        "date": str(date.date()),
    }
    heads = headlines if headlines is not None else _market_headlines()
    data = llm.ask_json("macro", SYSTEM, "ตัวเลข:\n" + json.dumps(facts) + "\n\nหัวข่าว:\n" + json.dumps(heads, ensure_ascii=False), 2000)
    if not data or data.get("risk_level") not in CUT:
        return {"enabled": True, "risk_level": "normal", "factor": 1.0, "error": "Macro Agent ล้มเหลว (ใช้ค่า: ปกติ)"}
    lvl = data["risk_level"]
    return {"enabled": True, "risk_level": lvl, "factor": CUT[lvl],
            "note": str(data.get("note", ""))[:300], "usd_thb_note": str(data.get("usd_thb_note", ""))[:200]}
