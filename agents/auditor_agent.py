"""เอเจนต์ 10 — ผู้ตรวจสอบ
ตรวจแพ็กเกจโดย "ไม่เชื่อ" ตัวเลขของผู้ทำ: คำนวณซ้ำเองจากข้อมูลดิบ + เทียบกับกฎใน config
ไม่อ่านข้อความเหตุผล (why) ของเอเจนต์อื่นเลย ดูแค่ตัวเลข

ผล: status = pass | warn | fail
- fail = ห้ามเสนอออเดอร์ (แพ็กเกจยังแสดงให้คุณดู แต่ปุ่มอนุมัติจะถูกปิด)
- warn = เสนอได้ แต่ต้องอ่านข้อสังเกตก่อน
"""
import pandas as pd

import config
from agents import risk_agent, regime_agent, signal_agent


def audit(packet, d, quality):
    fails, warns = [], []
    date = pd.Timestamp(packet["signal_date"])
    close = d["close"].loc[:date]

    # 1) คุณภาพข้อมูล
    if not quality.get("ok", False):
        fails.append("ข้อมูลไม่ผ่านการตรวจคุณภาพ: " + "; ".join(i["issue"] for i in quality["issues"][:3]))
    elif quality["issues"]:
        warns.append(f"ข้อมูลมีข้อสังเกต {len(quality['issues'])} จุด")

    # 2) พอร์ตเป้าหมายผ่านกฎความเสี่ยงไหม (ตรวจอิสระ)
    for b in risk_agent.check(packet["target"]):
        fails.append("ผิดกฎความเสี่ยง: " + b)

    # 3) คำนวณสัญญาณซ้ำจากข้อมูลดิบ ต้องได้ผลเท่ากัน (จับบั๊กหรือการแก้ตัวเลขระหว่างทาง)
    ind = regime_agent.indicators(close)
    reg = regime_agent.assess(close, ind, date, prev_up=packet["regime"].get("prev_up"))
    if abs(reg["exposure"] - packet["regime"]["exposure"]) > 1e-6:
        fails.append(f"สัดส่วนของเสี่ยงไม่ตรง: คำนวณซ้ำได้ {reg['exposure']} แต่แพ็กเกจบอก {packet['regime']['exposure']}")
    again = signal_agent.propose(close, ind, date, reg, held=packet.get("held_risky", []))
    again = risk_agent.apply(again["weights"], drawdown=packet.get("drawdown", 0),
                             blocked=packet.get("news", {}).get("blocked", []))["weights"]
    diff = {t: abs(again.get(t, 0) - packet["target"].get(t, 0)) for t in set(again) | set(packet["target"])}
    worst = max(diff.values(), default=0)
    if worst > 0.005:
        t = max(diff, key=diff.get)
        fails.append(f"น้ำหนัก {t} ไม่ตรงกับที่คำนวณซ้ำ (ต่าง {worst:.1%})")

    # 4) ออเดอร์สอดคล้องกับพอร์ตเป้าหมายและราคา
    px = close.iloc[-1]
    for o in packet["orders"]:
        p = px.get(o["ticker"])
        if p is None or p != p:
            fails.append(f"{o['ticker']}: ไม่มีราคาล่าสุด")
            continue
        if abs(o["ref_price"] - p) / p > 1e-6:
            fails.append(f"{o['ticker']}: ราคาอ้างอิงในออเดอร์ไม่ตรงกับข้อมูล")
    turnover = sum(o["usd"] for o in packet["orders"]) / max(packet["equity"], 1) / 2
    if turnover > 0.6:
        warns.append(f"รอบนี้ซื้อขายเยอะผิดปกติ ({turnover:.0%} ของพอร์ต)")
    if packet["regime"]["regime"] != packet.get("prev_regime") and packet.get("prev_regime"):
        warns.append(f"สภาวะตลาดเปลี่ยนจาก {packet['prev_regime']} เป็น {packet['regime']['regime']}")

    # 5) เอเจนต์ที่ล้มเหลว
    if packet.get("news", {}).get("error"):
        warns.append(packet["news"]["error"])

    status = "fail" if fails else "warn" if warns else "pass"
    return {"status": status, "fails": fails, "warns": warns}
