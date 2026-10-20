"""เอเจนต์ 7 — Risk Manager (โค้ดล้วน, veto ได้ทุกอย่าง)
รับน้ำหนักที่กลยุทธ์เสนอ → บังคับลิมิต → ส่วนที่เกินย้ายไปเงินสด (ไม่เคยย้ายไปเพิ่มความเสี่ยงที่อื่น)
"""
import config


def apply(weights, drawdown=0.0, blocked=(), r=config.RISK):
    w = dict(weights)
    log = []
    cash = config.CASH_PROXY

    # 0) ตัวที่ News Agent ติดธงห้ามซื้อ → ออก
    for t in blocked:
        if t in w and t != cash:
            log.append(f"{t}: ถูกตัดออกตามธงข่าว")
            w[cash] = w.get(cash, 0) + w.pop(t)

    # 1) ลิมิตต่อตัว
    for t in list(w):
        kind = config.UNIVERSE.get(t, {}).get("kind")
        if kind is None:
            log.append(f"{t}: ไม่อยู่ในรายชื่อที่อนุมัติ ตัดออก")
            w[cash] = w.get(cash, 0) + w.pop(t)
            continue
        cap = r["max_stock"] if kind == "stock" else r["max_etf"] if kind in ("etf", "defensive") else 1.0
        if w[t] > cap:
            log.append(f"{t}: {w[t]:.1%} เกินเพดาน {cap:.0%} ลดลง")
            w[cash] = w.get(cash, 0) + w[t] - cap
            w[t] = cap

    # 2) ลิมิตต่อกลุ่ม
    groups = {}
    for t, v in w.items():
        g = config.UNIVERSE[t]["group"]
        if g != "cash":
            groups.setdefault(g, []).append(t)
    for g, ts in groups.items():
        tot = sum(w[t] for t in ts)
        if tot > r["max_group"]:
            scale = r["max_group"] / tot
            for t in ts:
                w[cash] = w.get(cash, 0) + w[t] * (1 - scale)
                w[t] *= scale
            log.append(f"กลุ่ม {g}: {tot:.1%} เกินเพดาน {r['max_group']:.0%} ลดทุกตัวตามสัดส่วน")

    # 3) Drawdown ของพอร์ต → ลดของเสี่ยงลงครึ่งหนึ่ง
    if drawdown >= r["dd_cut_level"]:
        for t in list(w):
            if config.UNIVERSE[t]["kind"] in ("etf", "stock"):
                cut = w[t] * (1 - r["dd_cut_factor"])
                w[t] -= cut
                w[cash] = w.get(cash, 0) + cut
        log.append(f"พอร์ตลงจากจุดสูงสุด {drawdown:.1%} ≥ {r['dd_cut_level']:.0%} ลดของเสี่ยงลง {1 - r['dd_cut_factor']:.0%}")

    # 4) เงินสดขั้นต่ำ
    if w.get(cash, 0) < r["min_cash"]:
        need = r["min_cash"] - w.get(cash, 0)
        others = sum(v for t, v in w.items() if t != cash)
        for t in list(w):
            if t != cash:
                w[t] -= need * w[t] / others
        w[cash] = r["min_cash"]
        log.append(f"เงินสดต่ำกว่า {r['min_cash']:.0%} ปรับให้ครบ")

    total = sum(w.values())
    w = {t: v / total for t, v in w.items() if v > 1e-4}
    return {"weights": {t: round(float(v), 4) for t, v in w.items()}, "log": log}


def check(weights, r=config.RISK):
    """ตรวจซ้ำแบบอิสระ (ใช้โดยผู้ตรวจสอบ) — คืนรายการที่ผิดกฎ"""
    bad = []
    tol = 1e-3
    if abs(sum(weights.values()) - 1) > 0.01:
        bad.append(f"น้ำหนักรวม {sum(weights.values()):.3f} ไม่เท่ากับ 1")
    grp = {}
    for t, v in weights.items():
        m = config.UNIVERSE.get(t)
        if m is None:
            bad.append(f"{t} ไม่อยู่ในรายชื่อ")
            continue
        if v < -tol:
            bad.append(f"{t} น้ำหนักติดลบ")
        cap = r["max_stock"] if m["kind"] == "stock" else r["max_etf"] if m["kind"] in ("etf", "defensive") else 1
        if v > cap + tol:
            bad.append(f"{t} {v:.1%} เกินเพดาน {cap:.0%}")
        if m["group"] != "cash":
            grp[m["group"]] = grp.get(m["group"], 0) + v
    for g, v in grp.items():
        if v > r["max_group"] + tol:
            bad.append(f"กลุ่ม {g} {v:.1%} เกินเพดาน")
    if weights.get(config.CASH_PROXY, 0) < r["min_cash"] - tol:
        bad.append("เงินสดต่ำกว่าขั้นต่ำ")
    return bad
