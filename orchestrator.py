"""เอเจนต์ 1 — ผู้จัดการ: รันทุกพอร์ต (4 กลยุทธ์) ตามลำดับเอเจนต์ ถ้าพอร์ตไหนพังให้ข้ามพอร์ตนั้นและบันทึกไว้

ใช้งาน:
  python orchestrator.py daily     # ทุกเช้า: อัปเดตข้อมูล → เทรดออเดอร์ที่อนุมัติแล้ว → บันทึกมูลค่าทุกพอร์ต
  python orchestrator.py weekly    # วันเสาร์: daily + สร้างแพ็กเกจตัดสินใจของทุกพอร์ต (รายเดือนเฉพาะสัปดาห์สุดท้ายของเดือน)
  python orchestrator.py backtest  # backtest 3 กลยุทธ์โค้ด + ความทนทาน
  python orchestrator.py simulate  # ทดสอบทั้งวงจรย้อนหลัง 1 ปีด้วยการอนุมัติอัตโนมัติ (ล้างพอร์ตจำลองก่อน, เฉพาะบนเครื่อง)
"""
import json
import sys
import traceback
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import config
import paper
from agents import data_agent, regime_agent, news_agent, macro_agent, risk_agent, auditor_agent, reporter_agent
from strategies import PORTFOLIOS

RISKY = ("etf", "stock")
NEWS_TOP = 30   # อ่านข่าวหุ้นโมเมนตัมสูงสุดกี่ตัว (นอกจากตัวที่ถืออยู่)


# ---------------------------------------------------------------- เทรดออเดอร์ที่อนุมัติ (จำลองการกดใน Dime)
def _fill_orders(c, day, open_px, close_prev):
    P = paper.CURRENT
    rows = c.execute("SELECT * FROM orders WHERE portfolio=? AND status='approved' ORDER BY side DESC, id", (P,)).fetchall()
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
        c.execute("INSERT OR REPLACE INTO positions (portfolio, ticker, shares, cost_usd) VALUES (?,?,?,?)",
                  (P, o["ticker"], held["shares"], held["cost_usd"]))
        c.execute("UPDATE orders SET status='filled', fill_date=?, fill_price=?, shares=?, fee=?, realized_usd=? WHERE id=?",
                  (str(day.date()), px, sh, fee, realized, o["id"]))
        paper.put("cash", cash)
        paper.log("execution", f"{o['side']} {o['ticker']} {sh:.4f} หุ้น @ {px:.2f}",
                  {"order_id": o["id"], "usd": round(sh * px, 2), "fee": round(fee, 2)}, time=str(day.date()))


def _daily_one(d):
    """พอร์ตปัจจุบัน: เดินวันต่อวันตั้งแต่วันที่บันทึกล่าสุด → เทรดที่ราคาเปิด → บันทึกมูลค่าตอนปิด"""
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
            px = close.loc[:day].ffill().iloc[-1]   # ตัวที่ไม่มีราคาวันนี้ ใช้ราคาล่าสุดที่มี
            usd = cash + sum(p["shares"] * (px[t] if px[t] == px[t] else 0) for t, p in pos.items())
            bench = paper.get("capital") * (1 - config.COSTS["fx_spread_pct"]) * px[config.BENCHMARK] / paper.get("bench_start")
            c.execute("INSERT OR REPLACE INTO equity (portfolio, date, usd, thb, bench_usd) VALUES (?,?,?,?,?)",
                      (paper.CURRENT, str(day.date()), usd, usd * float(fx.loc[day]), float(bench)))
        if len(days):
            paper.put("last_marked", str(days[-1].date()))


def daily(d):
    for name in PORTFOLIOS:
        with paper.portfolio(name):
            try:
                _daily_one(d)
            except Exception as e:
                paper.log("orchestrator", "รอบรายวันล้มเหลว", {"error": str(e)[:300], "trace": traceback.format_exc()[-600:]})
    # ค่าล่าสุดให้หน้าเว็บใช้ (เว็บไม่มี pandas คำนวณเองไม่ได้)
    close, fx = d["close"], d["fx"]
    last = close.iloc[-1]
    paper.put("last_prices", {t: round(float(v), 4) for t, v in last.items() if v == v}, shared=True)
    paper.put("fx", round(float(fx.iloc[-1]), 4), shared=True)
    paper.put("last_data", str(close.index[-1].date()), shared=True)
    paper.put("data_source", d.get("source"), shared=True)
    try:
        publish_prices(close)
    except Exception as e:
        paper.log("data", "บันทึกราคารายตัวไม่สำเร็จ", {"error": str(e)[:200]}, shared=True)
    uni = {t: m["kind"] for t, m in config.UNIVERSE.items()}
    if paper.get("universe", shared=True) != uni:
        paper.put("universe", uni, shared=True)
        paper.put("names", {t: m.get("name", "") for t, m in config.UNIVERSE.items() if m["kind"] == "stock"}, shared=True)
    return news_today(d)


def publish_prices(close):
    """ราคาปิดย้อนหลังของตัวที่ถือ + ตัวในแพ็กเกจล่าสุด + SPY ลง D1 ให้เว็บวาดกราฟรายตัว
    ตัวใหม่เขียนย้อนหลัง ~1.5 ปี ตัวที่มีแล้วเขียนแค่ 10 วันล่าสุด (เติม/แก้ราคาที่ปรับปันผล)"""
    import re
    with paper.conn() as c:
        want = set(held_everywhere()) | {config.BENCHMARK}
        for r in c.execute("SELECT data FROM packets WHERE id IN (SELECT MAX(id) FROM packets GROUP BY portfolio)").fetchall():
            want |= set((json.loads(r["data"]) or {}).get("target", {}))
        have = {r["ticker"] for r in c.execute("SELECT DISTINCT ticker FROM prices").fetchall()}
        for t in sorted(want):
            if t not in close.columns or not re.fullmatch(r"[A-Z.\-]{1,10}", t):
                continue
            s = close[t].dropna().iloc[-(380 if t not in have else 10):]
            if s.empty:
                continue
            vals = ",".join(f"('{t}','{d.date()}',{float(v):.6g})" for d, v in s.items())
            c.execute(f"INSERT OR REPLACE INTO prices (ticker, date, close) VALUES {vals}")
        c.execute("DELETE FROM prices WHERE date < date('now', '-800 day')")


def held_everywhere():
    """ทุก ticker ที่พอร์ตไหนก็ตามถืออยู่"""
    with paper.conn() as c:
        return sorted({r["ticker"] for r in c.execute("SELECT DISTINCT ticker FROM positions WHERE shares > 1e-9")})


def watchlist(close, ind=None, n=NEWS_TOP):
    """หุ้นที่ต้องอ่านข่าว: ที่ถืออยู่ทุกพอร์ต + หุ้นโมเมนตัมสูงสุด n ตัว (ตัวที่มีโอกาสถูกเลือก)
    อ่านทั้ง 500 ตัวทุกวันเปลืองเกินไปและข่าวส่วนใหญ่ไม่เกี่ยวกับการตัดสินใจ"""
    ind = ind or regime_agent.indicators(close)
    date = close.index[-1]
    held = [t for t in held_everywhere() if config.info(t)["kind"] == "stock"]
    return list(dict.fromkeys(held + regime_agent.top_stocks(close, ind, date, n)))


def news_today(d=None, ind=None):
    """อ่านข่าววันละครั้ง (Claude 1 ครั้ง) เก็บไว้ให้เว็บ + รอบรายสัปดาห์ใช้ร่วมกัน"""
    old = paper.get("news_digest", shared=True) or {}
    today = str(datetime.now(timezone.utc).date())
    if old.get("date") == today and old.get("enabled") and not old.get("error") and old.get("stocks"):
        return old
    try:
        dig = news_agent.digest(watchlist(d["close"], ind) if d is not None else None)
    except Exception as e:
        dig = {"enabled": True, "date": today, "stocks": {}, "error": f"News Agent ล้มเหลว: {str(e)[:150]}"}
    if dig.get("enabled"):
        hot = [f"{t} {i['th'][:40]}" for t, s in dig["stocks"].items() for i in s["items"] if i["importance"] >= 3]
        paper.log("news", "สรุปข่าวประจำวัน", {"source": dig.get("source"), "flags": {t: s["flag"] for t, s in dig["stocks"].items() if s["flag"] != "ok"},
                                              "important": hot[:8], "error": dig.get("error")}, shared=True)
    paper.put("news_digest", dig, shared=True)
    return dig


# ---------------------------------------------------------------- สร้างแพ็กเกจตัดสินใจ
def _is_month_end_week(date):
    """สัญญาณสัปดาห์นี้เป็นสัปดาห์สุดท้ายของเดือนไหม = วันศุกร์หน้า (หรือวันทำการถัดไปอีก 7 วัน) อยู่คนละเดือน
    ใช้ปฏิทิน ไม่ใช้ข้อมูลราคา เพื่อให้ forward test กับ backtest ตรงกัน"""
    return (date + pd.Timedelta(days=7)).month != date.month


def _is_week_close(date, now=None):
    """แพ็กเกจรายสัปดาห์ใช้ราคาปิด "วันซื้อขายสุดท้ายของสัปดาห์" เท่านั้น (ปกติคือวันศุกร์)
    กันไม่ให้รอบกลางสัปดาห์เสนอเปลี่ยนพอร์ตก่อนเจ้าของได้เห็นผลของชุดที่เพิ่งซื้อ (เจ้าของขอ 2026-10-23)
    ศุกร์เป็นวันหยุดตลาด → ยอมรับราคาปิดวันพฤหัส เมื่อรันตอนเสาร์/อาทิตย์ของสัปดาห์นั้น"""
    if date.weekday() == 4:
        return True
    ny = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="America/New_York").tz_localize(None)
    return date.weekday() == 3 and ny.weekday() >= 5 and (ny.normalize() - date.normalize()).days <= 3


def _shared_context(d, date, as_of):
    """สิ่งที่ทุกพอร์ตใช้ร่วมกันในรอบนี้ (คำนวณครั้งเดียว): คุณภาพข้อมูล ตัวชี้วัด มหภาค"""
    close = d["close"]
    quality = data_agent.quality_report(d, as_of or datetime.now())
    ind = regime_agent.indicators(close)
    macro = macro_agent.assess(close, d["fx"], date)
    return {"quality": quality, "ind": ind, "macro": macro, "news": news_today(d, ind)}


def _weekly_one(d, ctx, as_of):
    P, meta = paper.CURRENT, PORTFOLIOS[paper.CURRENT]
    close = d["close"]
    date = close.index[-1]
    if paper.get("paused"):
        paper.log("orchestrator", "โหมดพัก: ไม่สร้างแพ็กเกจ")
        return None
    with paper.conn() as c:
        if c.execute("SELECT 1 FROM packets WHERE portfolio=? AND signal_date=?", (P, str(date.date()))).fetchone():
            return None
        waiting = c.execute("SELECT COUNT(*) n FROM orders WHERE portfolio=? AND status IN ('approved','held')", (P,)).fetchone()["n"]
    if waiting:   # แพ็กเกจก่อนยังเทรดไม่เสร็จ → พอร์ตยังไม่ใช่ของจริง สร้างใหม่ตอนนี้จะเสนอซื้อซ้ำ
        paper.log("orchestrator", "ข้ามการสร้างแพ็กเกจ: ยังมีออเดอร์ที่อนุมัติแล้วรอเทรด", {"orders": waiting})
        return None
    if meta["cadence"] == "monthly" and not _is_month_end_week(date) and paper.get("started"):
        return None   # กลยุทธ์รายเดือน: ตัดสินใจเฉพาะสัปดาห์สุดท้ายของเดือน (ยกเว้นรอบแรกตอนพอร์ตยังว่าง)
    if paper.get("started") and not _is_week_close(date):
        paper.log("orchestrator", "ไม่สร้างแพ็กเกจ: ใช้ราคาปิดวันศุกร์เท่านั้น (รอบเสาร์ราว 11:00)", {"data": str(date.date())})
        return None
    paper.put("started", True)
    with paper.conn() as c:
        # แพ็กเกจเก่าที่ยังไม่ตัดสินใจ → หมดอายุ
        c.execute("UPDATE orders SET status='expired', note='มีแพ็กเกจใหม่แทน' WHERE portfolio=? AND status='proposed'", (P,))
        c.execute("UPDATE packets SET status='expired' WHERE portfolio=? AND status='awaiting'", (P,))

    ind, quality, macro = ctx["ind"], ctx["quality"], ctx["macro"]
    prev_up = paper.get("prev_up")
    reg = regime_agent.assess(close, ind, date, prev_up=prev_up)
    reg.update(prev_up=prev_up, spy_price=float(close[config.BENCHMARK].iloc[-1]),
               spy_sma=float(ind["sma"][config.BENCHMARK].iloc[-1]))
    if macro.get("factor", 1.0) < 1:
        reg["exposure"] = round(reg["exposure"] * macro["factor"], 3)
        reg["reasons"].append(f"มหภาคเสี่ยงสูง ({macro.get('note', '')[:60]}) ลดของเสี่ยงอีก {1 - macro['factor']:.0%}")

    pos, cash = paper.positions(), paper.get("cash")
    px = close.ffill().loc[date]
    vals = {t: p["shares"] * px[t] for t, p in pos.items()}
    equity = cash + sum(vals.values())
    held_risky = [t for t in pos if config.info(t)["kind"] in RISKY]

    # กลยุทธ์เสนอ → ข่าวกรอง → Risk Manager บังคับกฎ
    dig = ctx.get("news")
    with paper.conn() as c:
        peak = c.execute("SELECT MAX(usd) m FROM equity WHERE portfolio=?", (P,)).fetchone()["m"] or equity
    dd = max(0.0, 1 - equity / peak)
    if meta["uses_ai"]:   # AI Pick (agent) ค้นข้อมูลเอง + เห็นประเด็นข่าวของหุ้นที่ติดตาม (รวมข่าวดี เช่น สินค้าใหม่)
        prop = meta["module"].propose(close, ind, date, reg, held=held_risky, extra={
            "news": news_agent.for_ai(dig), "macro": macro.get("note"), "drawdown": dd,
            "current_weights": {t: v / equity for t, v in vals.items() if v > 0},
            "blocked": [t for t, s in ((dig or {}).get("stocks") or {}).items() if s.get("flag") == "block"]})
    else:                 # กลยุทธ์กฎ: ข่าวไม่มีผลกับการเลือก มีแค่ธง block ที่ตัดออก
        prop = meta["module"].propose(close, ind, date, reg, held=held_risky)
    news = news_agent.screen([t for t in prop["weights"] if config.info(t)["kind"] in RISKY], dig)
    fin = risk_agent.apply(prop["weights"], drawdown=dd, blocked=news.get("blocked", []), portfolio=P)
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
            reason = (f"หลุดเกณฑ์ (อันดับ {rank.get(t, '>12')} หรือต่ำกว่าเส้น 200 วัน)" if P == "momentum"
                      else "ไม่อยู่ในพอร์ตเป้าหมายแล้ว")
            if t in news.get("blocked", []):
                reason = "ข่าวร้าย: " + "; ".join(f["reason"] for f in news["flags"] if f["ticker"] == t)[:100]
        else:
            reason = prop["why"].get(t, "ปรับน้ำหนักตามความผันผวน/สภาวะตลาด")
        orders.append({"ticker": t, "side": "BUY" if tgt_w > cur_w else "SELL",
                       "usd": round(abs(tgt_w - cur_w) * equity, 2), "ref_price": float(px[t]),
                       "w_from": round(cur_w, 4), "w_to": round(tgt_w, 4), "reason": reason})

    packet = {"portfolio": P, "strategy": meta["name"], "signal_date": str(date.date()), "regime": reg, "target": target,
              "orders": orders, "equity": round(equity, 2), "cash": round(cash, 2), "drawdown": round(dd, 4),
              "held_risky": held_risky, "news": news, "macro": macro,
              "news_items": {t: (dig or {}).get("stocks", {}).get(t, {}).get("items", [])
                             for t in sorted(set(target) | set(pos)) if (dig or {}).get("stocks", {}).get(t, {}).get("items")}, "risk_log": fin["log"], "ranking": prop["ranking"],
              "ai_view": prop.get("view"), "ai_research": prop.get("research"), "prev_regime": paper.get("prev_regime"), "quality": quality,
              "data_source": d.get("source")}
    audit = auditor_agent.audit(packet, d, quality)
    report = reporter_agent.write(packet, audit)
    status = "blocked" if audit["status"] == "fail" else ("awaiting" if orders else "no_action")

    with paper.conn() as c:
        cur = c.execute("INSERT INTO packets (portfolio, created, signal_date, status, data, audit, report) VALUES (?,?,?,?,?,?,?)",
                        (P, paper.now_th(), packet["signal_date"], status,
                         json.dumps(packet, ensure_ascii=False, default=float), json.dumps(audit, ensure_ascii=False), report))
        pid = cur.lastrowid
        for o in orders:
            c.execute("INSERT INTO orders (portfolio, packet_id, ticker, side, usd, ref_price, w_from, w_to, reason, status) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (P, pid, o["ticker"], o["side"], o["usd"], o["ref_price"], o["w_from"], o["w_to"], o["reason"],
                       "proposed" if status == "awaiting" else "blocked"))
        if status == "awaiting" and P in config.AUTO_APPROVE:   # เจ้าของเลือกให้พอร์ตนี้ไม่ต้องรออนุมัติ (ผู้ตรวจสอบต้องผ่านก่อน)
            c.execute("UPDATE orders SET status='approved', decided_at=?, note='อนุมัติอัตโนมัติ' WHERE packet_id=? AND status='proposed'",
                      (paper.now_th(), pid))
            c.execute("UPDATE packets SET status='decided' WHERE id=?", (pid,))
            status = "auto"
    paper.put("prev_up", reg["spy_above_sma"])
    paper.put("prev_regime", reg["regime"])
    paper.log("orchestrator", f"สร้างแพ็กเกจ #{pid} ({status})",
              {"orders": len(orders), "audit": audit["status"], "regime": reg["regime"]}, time=packet["signal_date"])
    return pid


def weekly(d, as_of=None, portfolios=None):
    paper.log("orchestrator", "เริ่มรอบรายสัปดาห์", {"data": str(d["close"].index[-1].date())}, shared=True)  # ตัวเฝ้าระวังบน Worker ใช้เช็คว่ารันแล้ว
    daily(d)
    date = d["close"].index[-1]
    ctx = _shared_context(d, date, as_of)
    out = {}
    for name in (portfolios or PORTFOLIOS):
        with paper.portfolio(name):
            try:
                out[name] = _weekly_one(d, ctx, as_of)
            except Exception as e:
                paper.log("orchestrator", "สร้างแพ็กเกจล้มเหลว", {"error": str(e)[:300], "trace": traceback.format_exc()[-800:]})
                out[name] = None
    return out


def decide(order_ids, decision, when=None):
    """คุณกดอนุมัติ/ปฏิเสธ — ใช้ได้เฉพาะออเดอร์ที่อยู่สถานะ proposed หรือ held (ทุกพอร์ต)"""
    when = when or paper.now_th()
    new = {"approve": "approved", "reject": "rejected"}[decision]
    with paper.conn() as c:
        n = 0
        for oid in order_ids:
            cur = c.execute("UPDATE orders SET status=?, decided_at=? WHERE id=? AND status IN ('proposed','held')",
                            (new, when, oid))
            n += cur.rowcount
        c.execute("UPDATE packets SET status='decided' WHERE status='awaiting' AND id NOT IN "
                  "(SELECT packet_id FROM orders WHERE status='proposed')")
    paper.log("owner", f"{'อนุมัติ' if decision == 'approve' else 'ปฏิเสธ'} {n} ออเดอร์", {"ids": order_ids}, time=when[:10], shared=True)
    return n


def kill():
    with paper.conn() as c:
        for name in PORTFOLIOS:
            with paper.portfolio(name):
                paper.put("paused", True)
        n = c.execute("UPDATE orders SET status='cancelled', note='Kill switch' WHERE status IN ('approved','proposed','held')").rowcount
        c.execute("UPDATE packets SET status='cancelled' WHERE status='awaiting'")
    paper.log("owner", "Kill switch: หยุดทุกพอร์ต", {"cancelled_orders": n}, shared=True)
    return n


def resume():
    for name in PORTFOLIOS:
        with paper.portfolio(name):
            paper.put("paused", False)
    paper.log("owner", "กลับมาทำงานต่อ", shared=True)


def _holdings_history(log):
    """ประวัติการถือใน backtest (เก็บเฉพาะรอบที่พอร์ตเปลี่ยน) ให้เว็บแสดงว่าแต่ละช่วงถือตัวไหน"""
    out, prev = [], None
    for x in log:
        w = {t: round(float(v), 3) for t, v in x["weights"].items() if v >= 0.005}
        if prev is None or set(w) != set(prev) or max(abs(w[t] - prev[t]) for t in w) > 0.02:
            out.append({"d": x["date"], "r": x["regime"], "w": w})
            prev = w
    return out


def publish_backtest(d):
    """backtest 3 กลยุทธ์โค้ด (AI Pick ทดสอบย้อนหลังไม่ได้ เพราะ Claude รู้อนาคต) + ความทนทานของ Momentum"""
    from agents import backtest_agent
    wk = lambda s: [{"time": str(i.date()), "value": round(float(v), 2)} for i, v in s.resample("W-FRI").last().dropna().items()]
    out = {}
    for name, meta in PORTFOLIOS.items():
        if meta["uses_ai"]:
            continue
        res = backtest_agent.run(d, strategy_module=meta["module"], cadence=meta["cadence"], portfolio=name)
        o = {"stats": res["stats"], "params": res["params"], "equity": wk(res["equity"]), "bench": wk(res["bench"]),
             "last_weights": res["weights_log"][-1] if res["weights_log"] else None,
             "history": _holdings_history(res["weights_log"])}
        if name == "momentum":
            o["robustness"] = backtest_agent.robustness(d)
        with paper.portfolio(name):
            with paper.conn() as c:
                c.execute("INSERT INTO backtests (portfolio, created, source, data) VALUES (?,?,?,?)",
                          (name, paper.now_th(), d.get("source"), json.dumps(o, ensure_ascii=False, default=float)))
            paper.log("backtest", "อัปเดตผล backtest", {"cagr": o["stats"]["strategy"].get("cagr"),
                                                         "max_dd": o["stats"]["strategy"].get("max_dd")})
        out[name] = o
    return out


def refresh_universe():
    """อัปเดตรายชื่อ S&P 500 ล่าสุด (บนคลาวด์) ดึงไม่ได้ = ใช้ไฟล์ในโปรแกรม (universe/sp500.csv)"""
    import urllib.request
    src, n = "file", sum(1 for m in config.UNIVERSE.values() if m["kind"] == "stock")
    if paper.remote():
        try:
            with urllib.request.urlopen(urllib.request.Request(config.SP500_URL, headers={"user-agent": "us-stock-agent/1"}), timeout=30) as r:
                rows = config.read_sp500(r.read().decode("utf-8"))
            if len(rows) >= 450:
                config.set_stocks(rows)
                src, n = "online", len(rows)
        except Exception as e:
            print("อัปเดตรายชื่อ S&P 500 ไม่ได้ ใช้ไฟล์เดิม:", e)
    paper.put("universe_info", {"source": src, "stocks": n, "date": paper.now_th()[:10]}, shared=True)


def load_data(source="auto"):
    if paper.remote() and source in ("auto", "synthetic"):
        source = "yfinance"   # บนคลาวด์ใช้ข้อมูลจริงเท่านั้น
    refresh_universe()
    extra = [t for t in held_everywhere() if t not in config.UNIVERSE]   # หลุดจาก S&P 500 แต่ยังถือ → ต้องมีราคาไว้ขาย
    d, src = data_agent.load(source, allow_synthetic=not paper.remote(), extra=extra)
    d["source"] = src
    paper.log("data", f"โหลดข้อมูลจาก {src}", {"last": str(d["close"].index[-1].date()), **data_agent.DIAG}, shared=True)
    return d


# ---------------------------------------------------------------- ทดสอบทั้งวงจรย้อนหลัง
def simulate(d_full, weeks=52, portfolios=None):
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
            pids = weekly(d, as_of=day, portfolios=portfolios)
            if i < len(idx) - 1:
                with paper.conn() as c:
                    ids = [r["id"] for r in c.execute("SELECT id FROM orders WHERE status='proposed'")]
                if ids:
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
        for k, v in publish_backtest(data).items():
            print(k, v["stats"]["strategy"])
    elif mode == "simulate":
        simulate(data)
        print("จำลองเสร็จ ดูผลที่หน้าเว็บ")
