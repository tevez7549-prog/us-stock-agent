"""กลยุทธ์ทั้งหมดที่รันคู่กัน (แต่ละตัว = พอร์ตจำลองแยก ทุน $10,000 เท่ากัน)

ทุกกลยุทธ์มีหน้าตาเดียวกัน:
    propose(close, ind, date, regime, held=()) -> {"weights": {ticker: w}, "why": {ticker: str}, "ranking": [...]}
    ผลลัพธ์ยังไม่ผ่าน Risk Manager — orchestrator จะส่งต่อให้ risk_agent.apply() เหมือนกันทุกพอร์ต

PORTFOLIOS = ลำดับที่แสดงบนเว็บ  key = ชื่อในฐานข้อมูล (ห้ามเปลี่ยนหลังเริ่ม forward test)
"""
from strategies import momentum, dual_momentum, risk_parity, ai_pick, ai_sector, breakout

PORTFOLIOS = {
    "momentum": {
        "name": "Momentum",
        "short": "โมเมนตัม 6 ตัว",
        "desc": "เลือก 6 ตัวที่โมเมนตัม 6+12 เดือนสูงสุด อยู่เหนือเส้น 200 วัน ถ่วงตามความผันผวน ปรับรายสัปดาห์",
        "module": momentum, "cadence": "weekly", "uses_ai": False,
    },
    "dual_momentum": {
        "name": "Dual Momentum",
        "short": "สลับกลุ่มสินทรัพย์",
        "desc": "เทียบหุ้นสหรัฐ / ต่างประเทศ / พันธบัตร ถือกลุ่มที่แรงสุด ถ้าทุกกลุ่มแพ้เงินสดก็ถือพันธบัตรสั้น ปรับรายเดือน",
        "module": dual_momentum, "cadence": "monthly", "uses_ai": False,
    },
    "risk_parity": {
        "name": "Risk Parity",
        "short": "กระจายความเสี่ยง",
        "desc": "หุ้น / พันธบัตร / ทอง ถ่วงน้ำหนักให้แต่ละส่วนมีความเสี่ยงเท่ากัน ไม่เลือกหุ้นรายตัว ปรับรายเดือน",
        "module": risk_parity, "cadence": "monthly", "uses_ai": False,
    },
    "ai_pick": {
        "name": "AI Pick",
        "short": "ให้ Claude เลือก",
        "desc": "Claude เลือกพอร์ตเองจากข้อมูลตัวเลข ข่าว และมหภาค (ภายใต้ Risk Manager เดียวกัน) ปรับรายสัปดาห์",
        "module": ai_pick, "cadence": "weekly", "uses_ai": True,
    },
    "ai_sector": {
        "name": "AI หมุนกลุ่ม",
        "short": "Claude เลือกกลุ่มธุรกิจ",
        "desc": "Claude เลือกสัดส่วน ETF 11 กลุ่มธุรกิจ (Select Sector SPDR) + SPY + SHY จากโมเมนตัม แนวโน้ม ข่าว และมหภาค ปรับรายเดือน อนุมัติอัตโนมัติ",
        "module": ai_sector, "cadence": "monthly", "uses_ai": True,
    },
    "breakout": {
        "name": "W1 เทรดรอบ",
        "short": "ทะลุกรอบ + stop ต่อไม้",
        "desc": "ซื้อหุ้นที่ทะลุจุดสูงสุด 55 วัน (S&P 500 + หุ้นนอกดัชนี ≥ $2,000 ล้าน) ไม้ละ 5% ขายเมื่อชน trailing stop 6 ATR ตรวจทุกวัน อนุมัติอัตโนมัติ",
        "module": breakout, "cadence": "daily", "uses_ai": False, "engine": "daily",
    },
    "momentum_wide": {
        "name": "Momentum กว้าง",
        "short": "โมเมนตัม 6 ตัว ตลาดกว้าง",
        "desc": "กฎเดียวกับ Momentum ทุกอย่าง แต่สแกน S&P 500 + หุ้นนอกดัชนี ≥ $2,000 ล้าน (รายชื่อชุดเดียวกับ W1) ปรับรายสัปดาห์ รอเจ้าของอนุมัติ",
        "module": momentum, "cadence": "weekly", "uses_ai": False, "wide": True,
    },
    "ai_pick_opus": {
        "name": "AI Pick (Opus)",
        "short": "AI Pick รุ่น Opus",
        "desc": "เหมือน AI Pick ทุกอย่าง (เครื่องมือ กฎ Risk Manager) เปลี่ยนแค่รุ่น AI เป็น Claude Opus 5.5 เพื่อเทียบว่ารุ่นที่ฉลาดกว่าเลือกหุ้นได้ดีกว่าไหม อนุมัติอัตโนมัติ",
        "module": ai_pick, "cadence": "weekly", "uses_ai": True, "model": "opus",
    },
    "ai_sector_opus": {
        "name": "AI หมุนกลุ่ม (Opus)",
        "short": "AI หมุนกลุ่ม รุ่น Opus",
        "desc": "เหมือน AI หมุนกลุ่มทุกอย่าง เปลี่ยนแค่รุ่น AI เป็น Claude Opus 5.5 ปรับรายเดือน อนุมัติอัตโนมัติ",
        "module": ai_sector, "cadence": "monthly", "uses_ai": True, "model": "opus",
    },
}
