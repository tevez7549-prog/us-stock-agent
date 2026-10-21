"""กลยุทธ์ทั้งหมดที่รันคู่กัน (แต่ละตัว = พอร์ตจำลองแยก ทุน $10,000 เท่ากัน)

ทุกกลยุทธ์มีหน้าตาเดียวกัน:
    propose(close, ind, date, regime, held=()) -> {"weights": {ticker: w}, "why": {ticker: str}, "ranking": [...]}
    ผลลัพธ์ยังไม่ผ่าน Risk Manager — orchestrator จะส่งต่อให้ risk_agent.apply() เหมือนกันทุกพอร์ต

PORTFOLIOS = ลำดับที่แสดงบนเว็บ  key = ชื่อในฐานข้อมูล (ห้ามเปลี่ยนหลังเริ่ม forward test)
"""
from strategies import momentum, dual_momentum, risk_parity, ai_pick

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
}
