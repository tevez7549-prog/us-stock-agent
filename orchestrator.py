"""เอเจนต์ 1 — ผู้จัดการ: สั่งเอเจนต์ตามลำดับ ถ้าตัวไหนพังให้หยุดรอบนั้นและบันทึกไว้

ใช้งาน:
  python orchestrator.py daily     # ทุกเช้า: อัปเดตข้อมูล → เทรดออเดอร์ที่อนุมัติแล้ว → บันทึกมูลค่าพอร์ต
  python orchestrator.py weekly    # วันเสาร์: daily + สร้างแพ็กเกจตัดสินใจ
  python orchestrator.py simulate  # ทดสอบระบบทั้งวงจรย้อนหลัง 1 ปีด้วยการอนุมัติอัตโนมัติ (ล้างพอร์ตจำลองก่อน)
"""
import sys
from datetime import datetime

import numpy as np
import pandas as pd

import config
import paper
from agents import data_agent, regime_agent, signal_agent, news_agent, risk_agent, auditor_agent, reporter_agent

RISKY = ("etf", "stock")


# ---------------------------------------------------------------- เทรดออเดอร์ที่อนุมัติ (จำลองการกดใน Dime)
def _fill_orders(c, day, open_px, close_prev):
    rows = c.execute("SELECT * FROM orders WHERE status='approved' ORDER BY side DESC, id").fetchall()  # SELL ก่อน BUY
    if not rows:
        return
    cash = paper.get("cash")
    pos = paper.positions()
    fee_rate = config.COSTS["commission_pct"] + config.COSTS["slippage_pct"]
    for o in rows:
        # decided_at เป็นเวลาไทย → แปลงเป็นเวลานิวยอร์ก แล้วเทรดได้ที่ราคาเปิด (09:30 NY) แรกหลังเวลาอนุมัติ
        decided = pd.Timestamp(o["decided_at"]).tz_localize("Asia/Bangkok").tz_convert("America/New_York")
        first_day = decided.date() if (decided.hour, decided.minute) < (9, 30) else (decided + pd.Timedelta(days=1)).date()
        if day.date() < first_day:
            continue
        age = np.busday_count(first_day, day.date())
        if age > config.APPROVAL_EXPIRE_DAYS:
            c.execute("UPDATE orders SET status='expired', note=? WHERE id=?", ("อนุมัติแล้วแต่ไม่ได้เทรดภายในกำหนด", o["id"]))
            continue
        px = open_px.get(o["ticker"])
        if px is None or px != px:
            px = close_prev.get(o["ticker"])
        if px is None or px != px:
            continue
        move = px / o["ref_price"] - 1
        if abs(move) > config.MAX_PRICE_MOVE:
            c.execute("UPDATE orders SET status='held', note=? WHERE id=?",
                      (f"ราคาเปิด {px:.2f} ห่างจากราคาตอนเสนอ {move:+.1%} เกิน {config.MAX_PRICE_MOVE:.0%} — พักไว้ ต้องตัดสินใจใหม่", o["id"]))
            continue
        held = pos.get(o["ticker"], {"shares": 0.0, "cost_usd": 0.0})
        if o["side"] == "SELL":
            sh = held["shares"] if o["w_to"] == 0 else min(held["shares"], o["usd"] / px)
            if sh <= 0:
                c.execute("UPDATE orders SET status='expired', note='ไม่มีหุ้นให้ขาย' WHERE id=?", (o["id"],))
                continue
            gross = sh * px
            fee = gross * fee_rate
            cost_part = held["cost_usd"] * sh / held["shares"]
            cash += gross - fee
            held = {"shares": held["shares"] - sh, "cost_usd": held["cost_usd"] - cost_part}
            realized = gross - fee - cost_part
        else:
            amt = min(o["usd"], cash / (1 + fee_rate))
            if amt < 1:
                c.execute("UPDATE orders SET status='expired', note='เงินสดไม่พอ' WHERE id=?", (o["id"],))
                continue
            sh, fee = amt / px, amt * fee_rate
            cash -= amt + fee
            held = {"shares": held["shares"] + sh, "cost_usd": held["cost_usd"] + amt + fee}
            realized = 0.0
        pos[o["ticker"]] = held
        c.execute("INSERT OR REPLACE INTO positions VALUES (?,?,?)", (o["ticker"], held["shares"], held["cost_usd"]))
        c.execute("UPDATE orders SET status='filled', fill_date=?, fill_price=?, shares=?, fee=?, realized_usd=? WHERE id=?",
                  (str(day.date()), px, sh, fee, realized, o["id"]))
        paper.put("cash", cash)
        paper.log("execution", f"{o['side']} {o['ticker']} {sh:.4f} หุ้น @ {px:.2f}",
                  {"order_id": o["id"], "usd": round(sh * px, 2), "fee": round(fee, 2)}, time=str(day.date()))


def daily(d):
    """เดินวันต่อวันตั้งแต่วันที่บันทึกล่าสุด: เทรดที่ราคาเปิด → บันทึกมูลค่าตอนปิด"""
    paper.init()
    close, open_, fx = d["close"], d["open"], d["fx"]
    last = paper.get("last_marked")
    days = close.index[close.index > pd.Timestamp(last)] if last else close.index[-1:]
    if paper.get("bench_start") is None:
        paper.put("bench_start", float(close[config.BENCHMARK].iloc[-1]))
    with paper.conn() as c:
        for day in days:
            i = close.index.get_loc(day)
            if not paper.get("paused"):
                _fill_orders(c, day, open_.loc[day], close.iloc[i - 1] if i > 0 else close.loc[day])
            pos, cash = paper.positions(), paper.get("cash")
            px = close.loc[day]
            usd = cash + sum(p["shares"] * (px[t] if px[t] == px[t] else 0) for t, p in pos.items())
            bench = paper.get("capital") * (1 - config.COSTS["fx_spread_pct"]) * px[config.BENCHMARK] / paper.get("bench_start")
            c.execute("INSERT OR REPLACE INTO equity VALUES (?,?,?,?)",
                      (str(day.date()), usd, usd * float(fx.loc[day]), float(bench)))
        if len(days):
            paper.put("last_marked", str(days[-1].date()))
    # ค่าล่าสุดให้หน้าเว็บใช้ (เว็บไม่มี pandas คำนวณเองไม่ได้)
    last = close.iloc[-1]
    paper.put("last_prices", {t: round(float(v), 4) for t, v in last.items() if v == v})
    paper.put("fx", round(float(fx.iloc[-1]), 4))
    paper.put("last_data", str(close.index[-1].date()))
    paper.put("data_source", d.get("source"))


# ---------------------------------------------------------------- สร้างแพ็กเกจตัดสินใจรายสัปดาห์
def weekly(d, as_of=None):
    daily(d)
    close = d["close"]
    date = close.index[-1]
    if paper.get("paused"):
        paper.log("orchestrator", "โหมดพัก: ไม่สร้างแพ็กเกจ")
        return None
    with paper.conn() as c:
        if c.execute("SELECT 1 FROM packets WHERE signal_date=?", (str(date.date()),)).fetchone():
            return None
        # แพ็กเกจเก่าที่ยังไม่ตัดสินใจ → หมดอายุ
        c.execute("UPDATE orders SET status='expired', note='มีแพ็กเกจใหม่แทน' WHERE status='proposed'")
        c.execute("UPDATE packets SET status='expired' WHERE status='awaiting'")

    quality = data_agent.quality_report(d, as_of or datetime.now())
    ind = regime_agent.indicators(close)
    prev_up = paper.get("prev_up")
    reg = regime_agent.assess(close, ind, date, prev_up=prev_up)
    reg.update(prev_up=prev_up, spy_price=float(close[config.BENCHMARK].iloc[-1]),
               spy_sma=float(ind["sma"][config.BENCHMARK].iloc[-1]))

    pos, cash = paper.positions(), paper.get("cash")
    px = close.loc[date]
    vals = {t: p["shares"] * px[t] for t, p in pos.items()}
    equity = cash + sum(vals.values())
    held_risky = [t for t in pos if config.UNIVERSE[t]["kind"] in RISKY]

    prop = signal_agent.propose(close, ind, date, reg, held=held_risky)
    news = news_agent.screen([t for t in prop["weights"] if config.UNIVERSE[t]["kind"] in RISKY])
    with paper.conn() as c:
        peak = c.execute("SELECT MAX(usd) m FROM equity").fetchone()["m"] or equity
    dd = max(0.0, 1 - equity / peak)
    fin = risk_agent.apply(prop["weights"], drawdown=dd, blocked=news.get("blocked", []))
    target = fin["weights"]
    rank = {r["ticker"]: r["rank"] for r in prop["ranking"]}

    orders = []
    for t in sorted(set(target) | set(vals)):
        cur_w, tgt_w = vals.get(t, 0) / equity, target.get(t, 0)
        full_exit = tgt_w == 0 and vals.get(t, 0) > 0
        if abs(tgt_w - cur_w) < config.RISK["min_trade_weight"] and not full_exit:
            continue
        if t in prop["why"] and cur_w == 0:
            reason = prop["why"][t]
        elif full_exit:
            reason = f"หลุดเกณฑ์ (อันดับโมเมนตัม {rank.get(t, '>12')} หรือต่ำกว่าเส้น 200 วัน)" \
                if config.UNIVERSE[t]["kind"] in RISKY else "ไม่อยู่ในพอร์ตเป้าหมายแล้ว"
        else:
            reason = "ปรับน้ำหนักตามความผันผวน/สภาวะตลาด"
        orders.append({"ticker": t, "side": "BUY" if tgt_w > cur_w else "SELL",
                       "usd": round(abs(tgt_w - cur_w) * equity, 2), "ref_price": float(px[t]),
                       "w_from": round(cur_w, 4), "w_to": round(tgt_w, 4), "reason": reason})

    packet = {"signal_date": str(date.date()), "regime": reg, "target": target, "orders": orders,
              "equity": round(equity, 2), "cash": round(cash, 2), "drawdown": round(dd, 4),
              "held_risky": held_risky, "news": news, "risk_log": fin["log"], "ranking": prop["ranking"],
              "prev_regime": paper.get("prev_regime"), "quality": quality, "data_source": d.get("source")}
    audit = auditor_agent.audit(packet, d, quality)
    report = reporter_agent.write(packet, audit)
    status = "blocked" if audit["status"] == "fail" else ("awaiting" if orders else "no_action")

    with paper.conn() as c:
        import json
        cur = c.execute("INSERT INTO packets (created, signal_date, status, data, audit, report) VALUES (?,?,?,?,?,?)",
                        (paper.now_th(), packet["signal_date"], status,
                         json.dumps(packet, ensure_ascii=False, default=float), json.dumps(audit, ensure_ascii=False), report))
        pid = cur.lastrowid
        for o in orders:
            c.execute("INSERT INTO orders (packet_id, ticker, side, usd, ref_price, w_from, w_to, reason, status) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (pid, o["ticker"], o["side"], o["usd"], o["ref_price"], o["w_from"], o["w_to"], o["reason"],
                       "proposed" if status == "awaiting" else "blocked"))
    paper.put("prev_up", reg["spy_above_sma"])
    paper.put("prev_regime", reg["regime"])
    paper.log("orchestrator", f"สร้างแพ็กเกจ #{pid} ({status})",
              {"orders": len(orders), "audit": audit["status"], "regime": reg["regime"]}, time=packet["signal_date"])
    return pid


def decide(order_ids, decision, when=None):
    """คุณกดอนุมัติ/ปฏิเสธ — ใช้ได้เฉพาะออเดอร์ที่อยู่สถานะ proposed หรือ held"""
    when = when or paper.now_th()
    new = {"approve": "approved", "reject": "rejected"}[decision]
    with paper.conn() as c:
        n = 0
        for oid in order_ids:
            cur = c.execute("UPDATE orders SET status=?, decided_at=? WHERE id=? AND status IN ('proposed','held')",
                            (new, when, oid))
            n += cur.rowcount
        # ตัดสินใจครบทุกออเดอร์แล้ว → แพ็กเกจปิด
        c.execute("UPDATE packets SET status='decided' WHERE status='awaiting' AND id NOT IN "
                  "(SELECT packet_id FROM orders WHERE status='proposed')")
    paper.log("owner", f"{'อนุมัติ' if decision == 'approve' else 'ปฏิเสธ'} {n} ออเดอร์", {"ids": order_ids}, time=when[:10])
    return n


def kill():
    paper.put("paused", True)
    with paper.conn() as c:
        n = c.execute("UPDATE orders SET status='cancelled', note='Kill switch' WHERE status IN ('approved','proposed','held')").rowcount
        c.execute("UPDATE packets SET status='cancelled' WHERE status='awaiting'")
    paper.log("owner", "Kill switch: หยุดระบบ", {"cancelled_orders": n})
    return n


def resume():
    paper.put("paused", False)
    paper.log("owner", "กลับมาทำงานต่อ")


def publish_backtest(d):
    """รัน backtest + ทดสอบความทนทานด้วยข้อมูลจริง แล้วเก็บผลให้หน้าเว็บแสดง"""
    import json
    from agents import backtest_agent
    res = backtest_agent.run(d)
    wk = lambda s: [{"time": str(i.date()), "value": round(float(v), 2)} for i, v in s.resample("W-FRI").last().dropna().items()]
    out = {"stats": res["stats"], "params": res["params"], "equity": wk(res["equity"]), "bench": wk(res["bench"]),
           "last_weights": res["weights_log"][-1] if res["weights_log"] else None,
           "robustness": backtest_agent.robustness(d)}
    with paper.conn() as c:
        c.execute("INSERT INTO backtests (created, source, data) VALUES (?,?,?)",
                  (paper.now_th(), d.get("source"), json.dumps(out, ensure_ascii=False, default=float)))
    paper.log("backtest", "อัปเดตผล backtest", {"cagr": out["stats"]["strategy"].get("cagr"),
                                                 "max_dd": out["stats"]["strategy"].get("max_dd")})
    return out


def load_data(source="auto"):
    if paper.remote() and source in ("auto", "synthetic"):
        source = "yfinance"   # บนคลาวด์ใช้ข้อมูลจริงเท่านั้น
    d, src = data_agent.load(source, allow_synthetic=not paper.remote())
    d["source"] = src
    paper.log("data", f"โหลดข้อมูลจาก {src}", {"last": str(d["close"].index[-1].date())})
    return d


# ---------------------------------------------------------------- ทดสอบทั้งวงจรย้อนหลัง
def simulate(d_full, weeks=52):
    """เดินระบบวันต่อวันย้อนหลัง N สัปดาห์ด้วยข้อมูลที่ตัดถึงวันนั้นเท่านั้น และอนุมัติทุกออเดอร์อัตโนมัติ
    ยกเว้นแพ็กเกจสุดท้าย ปล่อยไว้ให้คุณลองกดตัดสินใจเอง"""
    paper.reset()
    idx = d_full["close"].index
    start = len(idx) - weeks * 5
    week_end = set(idx.to_series().groupby(idx.to_period("W-FRI")).max())
    for i in range(start, len(idx)):
        day = idx[i]
        d = data_agent.truncate(d_full, day)
        d["source"] = d_full.get("source")
        if day in week_end:
            pid = weekly(d, as_of=day)
            if pid and i < len(idx) - 1:
                with paper.conn() as c:
                    ids = [r["id"] for r in c.execute("SELECT id FROM orders WHERE packet_id=? AND status='proposed'", (pid,))]
                decide(ids, "approve", when=str(day.date()) + "T23:00:00")
        else:
            daily(d)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    src = sys.argv[2] if len(sys.argv) > 2 else "auto"
    if mode == "simulate" and paper.remote():
        sys.exit("simulate ใช้ได้เฉพาะบนเครื่อง (ไม่ได้ตั้ง WORKER_URL)")
    data = load_data("synthetic" if mode == "simulate" and src == "auto" else src)
    if mode == "daily":
        daily(data)
    elif mode == "weekly":
        print("แพ็กเกจ:", weekly(data))
    elif mode == "backtest":
        st = publish_backtest(data)["stats"]["strategy"]
        print("backtest:", st)
    elif mode == "simulate":
        simulate(data)
        print("จำลองเสร็จ ดูผลที่หน้าเว็บ")
