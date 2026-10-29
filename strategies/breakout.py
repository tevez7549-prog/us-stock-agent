"""กลยุทธ์ 6 — W1 เทรดรอบ (เจ้าของสั่ง 2026-10-29): กฎ W1 จากงานวิจัย research/trade_stops.py ตรงทุกค่า รันไปข้างหน้ารายวัน

ตลาด: หุ้น S&P 500 + หุ้นสหรัฐนอกดัชนี (NYSE/Nasdaq) ที่มูลค่าตลาด ≥ $2,000 ล้าน และซื้อขายเฉลี่ย ≥ $10 ล้าน/วัน
      (รายชื่อสร้างใหม่สัปดาห์ละครั้งจาก SEC: จำนวนหุ้นหน้าปกงบ × ราคาล่าสุด) — รันไปข้างหน้าจึงไม่มีข้อมูลเอียง
เข้า: ราคาปิด > High สูงสุด 55 วันก่อนหน้า + ราคา > SMA200 + SPY > SMA200 + ไม่ติดธงข่าว block
      สัญญาณจากราคาปิด → ซื้อที่ราคาเปิดวันทำการถัดไป · ไม้ละ 5% ของมูลค่าพอร์ต · ถือพร้อมกัน ≤ 20 ไม้
      สัญญาณเกินโควตา → เลือกตัวที่ปิดเหนือจุดสูงสุด 55 วันเป็น % มากสุด
ออก: stop เริ่ม = ราคาเข้า − 3 ATR14 (ของวันสัญญาณ) · trailing = ราคาปิด − 6 ATR14 เลื่อนขึ้นอย่างเดียว · ไม่มี TP
     ทุกวันเช็คกับราคาต่ำสุดของวัน (เหมือนตั้ง stop order ไว้ที่โบรกเกอร์): เปิดต่ำกว่า stop → ขายที่ราคาเปิด, ไม่งั้นขายที่ stop
Risk Manager ของพอร์ตนี้ (config.BREAKOUT): เงินสด ≥ 5%, กลุ่มธุรกิจเดียว ≤ 40%, พอร์ตลงจากจุดสูงสุด ≥ config.RISK dd_cut_level (20%) → ไม้ใหม่ครึ่งขนาด
โหมดพัก (kill switch): ไม่เปิดไม้ใหม่ แต่ stop ของไม้ที่ถืออยู่ยังทำงาน (ลดความเสี่ยงเท่านั้น)
ต่างจากพอร์ตอื่น: ไม่มีกฎพักออเดอร์เมื่อราคาเปิดห่างเกิน 3% (หุ้นทะลุกรอบมัก gap ขึ้น งานวิจัยซื้อที่ราคาเปิดเสมอ)
"""
import json
import re
from datetime import date as _date, timedelta

import numpy as np
import pandas as pd

import config
import paper

P = "breakout"
B = config.BREAKOUT
FEE = config.COSTS["commission_pct"] + config.COSTS["slippage_pct"]


# ---------------------------------------------------------------- รายชื่อหุ้น (สร้างใหม่สัปดาห์ละครั้ง)
def _sec(url):
    from research.quality import _get   # User-Agent + จำกัด 10 ครั้ง/วินาที ตามกฎ SEC
    return _get(url)


def _latest_shares():
    """จำนวนหุ้นหน้าปกงบล่าสุดของทุกบริษัท (SEC frames 4 ไตรมาสล่าสุด เอาค่าใหม่สุดต่อบริษัท)"""
    out, d = {}, _date.today()
    qs = []
    for k in range(6):
        y, q = d.year, (d.month - 1) // 3 + 1 - k
        while q <= 0:
            q += 4
            y -= 1
        qs.append(f"CY{y}Q{q}I")
    for p in reversed(qs):
        j = _sec(f"https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/{p}.json") or {}
        for r in j.get("data", []):
            out[int(r["cik"])] = float(r["val"])
    return out


def build_universe(prev=None):
    import yfinance as yf
    from research.growth_wide import sic_sector
    prev = (prev or {}).get("tickers") or {}
    tick = _sec("https://www.sec.gov/files/company_tickers_exchange.json")
    f = tick["fields"]
    rows = [dict(zip(f, r)) for r in tick["data"]]
    sp = {t: m for t, m in config.UNIVERSE.items() if m["kind"] == "stock"}
    shares = _latest_shares()
    cand = {}
    for r in rows:
        t = config.yf_symbol(str(r.get("ticker") or ""))
        if r.get("exchange") not in ("NYSE", "Nasdaq") or not re.fullmatch(r"[A-Z]{1,5}(-[A-Z])?", t) or t in sp:
            continue
        cik = int(r["cik"])
        if cik in shares and t not in cand:
            cand[t] = (cik, r.get("name") or "")
    tickers = list(cand)
    keep = {}
    for i in range(0, len(tickers), 200):
        chunk = tickers[i:i + 200]
        try:
            raw = yf.download(chunk, period="1mo", auto_adjust=False, progress=False, group_by="column", threads=True)
        except Exception:
            continue
        c, v = raw["Close"], raw["Volume"]
        if isinstance(c, pd.Series):
            c, v = c.to_frame(chunk[0]), v.to_frame(chunk[0])
        for t in chunk:
            if t not in c.columns or c[t].dropna().empty:
                continue
            last = float(c[t].dropna().iloc[-1])
            adv = float((c[t] * v[t]).tail(20).mean())
            if last * shares[cand[t][0]] >= B["min_mcap"] and adv >= B["min_dollar_volume"]:
                keep[t] = cand[t]
    out = {t: [m.get("name") or "", m["group"]] for t, m in sp.items()}
    for t, (cik, name) in keep.items():
        if t in prev:
            out[t] = prev[t]
            continue
        sub = _sec(f"https://data.sec.gov/submissions/CIK{cik:010d}.json") or {}
        out[t] = [sub.get("name") or name, config.GICS_GROUP.get(sic_sector(sub.get("sic")), "other")]
    return {"date": str(_date.today()), "tickers": out, "n_sp500": len(sp), "n_extra": len(keep)}


def ensure_universe(force=False):
    u = paper.get("breakout_universe", shared=True)
    stale = not u or (pd.Timestamp(_date.today()) - pd.Timestamp(u["date"])).days >= 7
    if (force or stale) and paper.remote():
        try:
            u = build_universe(u)
            paper.put("breakout_universe", u, shared=True)
            paper.log("data", f"W1: อัปเดตรายชื่อหุ้น {len(u['tickers'])} ตัว (นอก S&P 500 {u['n_extra']})", {"date": u["date"]}, shared=True)
        except Exception as e:
            paper.log("data", "W1: สร้างรายชื่อหุ้นไม่สำเร็จ ใช้รายชื่อเดิม", {"error": str(e)[:300]}, shared=True)
    if not u:   # ในเครื่อง/ยังไม่มีรายชื่อ: ใช้ S&P 500
        u = {"date": str(_date.today()), "tickers": {t: [m.get("name") or "", m["group"]] for t, m in config.UNIVERSE.items()
                                                    if m["kind"] == "stock"}, "n_extra": 0}
    return u


# ---------------------------------------------------------------- ข้อมูลราคา
_CACHE = {}


def load_bars(tickers, main_d=None):
    """OHLC รายวันราว 1.75 ปี (พอสำหรับ SMA200 / Donchian 55 / ATR14 / โมเมนตัม 12 เดือน) — เก็บไว้ใช้ซ้ำในรอบเดียวกัน"""
    key = (frozenset(tickers), str(main_d["close"].index[-1]) if main_d is not None else "")
    if key in _CACHE:
        return _CACHE[key]
    _CACHE.clear()
    _CACHE[key] = _load_bars(tickers, main_d)
    return _CACHE[key]


def _load_bars(tickers, main_d=None):
    if not paper.remote():
        c = main_d["close"][[t for t in tickers if t in main_d["close"].columns]].iloc[-420:]
        o = main_d["open"][c.columns].iloc[-420:]
        rng = np.random.default_rng(0)
        h = c * (1 + np.abs(rng.normal(0, 0.008, c.shape)))
        lo = c * (1 - np.abs(rng.normal(0, 0.008, c.shape)))
        return {"close": c, "open": o, "high": np.maximum(h, o), "low": np.minimum(lo, o)}
    import yfinance as yf
    start = str((_date.today() - timedelta(days=640)))
    parts = {k: [] for k in ("Close", "Open", "High", "Low")}
    tickers = list(tickers)
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        for k in range(3):
            try:
                raw = yf.download(chunk, start=start, auto_adjust=True, progress=False, group_by="column", threads=True)
                for f in parts:
                    x = raw[f]
                    parts[f].append(x.to_frame(chunk[0]) if isinstance(x, pd.Series) else x)
                break
            except Exception:
                pass
    out = {}
    for f, k in (("Close", "close"), ("Open", "open"), ("High", "high"), ("Low", "low")):
        x = pd.concat(parts[f], axis=1)
        out[k] = x.loc[:, ~x.columns.duplicated()].sort_index()
    us_day = out["close"][config.BENCHMARK].notna()
    return {k: v[us_day] for k, v in out.items()}


def atr14(H, L, C):
    pc = C.shift(1)
    tr = pd.concat([H - L, (H - pc).abs(), (L - pc).abs()]).groupby(level=0).max()
    return tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()


# ---------------------------------------------------------------- เดินพอร์ตวันต่อวัน
def _order(c, pid, t, side, usd, ref, w_from, w_to, reason, status, decided=None, fill=None, note=None):
    cur = c.execute("INSERT INTO orders (portfolio, packet_id, ticker, side, usd, ref_price, w_from, w_to, reason, status, decided_at, note) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (P, pid, t, side, round(usd, 2), float(ref), round(w_from, 4), round(w_to, 4),
                                                       reason, status, decided, note))
    if fill:
        c.execute("UPDATE orders SET fill_date=?, fill_price=?, shares=?, fee=?, realized_usd=? WHERE id=?", (*fill, cur.lastrowid))
    return cur.lastrowid


def _packet(c, day, data, report):
    row = c.execute("SELECT id FROM packets WHERE portfolio=? AND signal_date=?", (P, day)).fetchone()
    if row:
        return row["id"]
    audit = data.pop("_audit")
    cur = c.execute("INSERT INTO packets (portfolio, created, signal_date, status, data, audit, report) VALUES (?,?,?,?,?,?,?)",
                    (P, paper.now_th(), day, data.pop("_status"), json.dumps(data, ensure_ascii=False, default=float),
                     json.dumps(audit, ensure_ascii=False), report))
    return cur.lastrowid


def run(main_d):
    """เรียกจาก orchestrator.daily ทุกเช้า"""
    with paper.portfolio(P):
        paper.init()
        u = ensure_universe()
        uni = u["tickers"]
        pos = paper.positions()
        bars = load_bars(set(uni) | set(pos) | {config.BENCHMARK}, main_d)
        C, O, H, L = (bars[k].astype(float) for k in ("close", "open", "high", "low"))
        H, L = H.fillna(C), L.fillna(C)
        A = atr14(H, L, C)
        sma = C.rolling(200).mean()
        dn = H.shift(1).rolling(B["donchian"]).max()
        idx = C.index
        last = paper.get("last_marked")
        days = idx[idx > pd.Timestamp(last)] if last else idx[-1:]
        if paper.get("bench_start") is None:
            paper.put("bench_start", float(C[config.BENCHMARK].iloc[-1]))
        stops = paper.get("stops") or {}
        pend_info = paper.get("pending") or {}   # order id → {signal, sl} (ระยะ stop จากวันสัญญาณ)
        cash = paper.get("cash")
        fx = main_d["fx"]
        with paper.conn() as c:
            for day in days:
                ds = str(day.date())
                events = []
                # 1) ซื้อตามสัญญาณ (ออเดอร์ที่อนุมัติไว้) ที่ราคาเปิดวันนี้
                for o in c.execute("SELECT * FROM orders WHERE portfolio=? AND status='approved' AND side='BUY'", (P,)).fetchall():
                    sig = pend_info.get(str(o["id"]))
                    if not sig or sig["signal"] >= ds:
                        continue
                    if np.busday_count(sig["signal"], ds) > config.APPROVAL_EXPIRE_DAYS:
                        c.execute("UPDATE orders SET status='expired', note=? WHERE id=?", ("ไม่ได้ซื้อภายในกำหนด", o["id"]))
                        pend_info.pop(str(o["id"]), None)
                        continue
                    px = O.at[day, o["ticker"]] if o["ticker"] in O.columns else np.nan
                    if px != px or paper.get("paused"):
                        continue
                    amt = min(o["usd"], cash / (1 + FEE))
                    if amt < 1:
                        c.execute("UPDATE orders SET status='expired', note='เงินสดไม่พอ' WHERE id=?", (o["id"],))
                        pend_info.pop(str(o["id"]), None)
                        continue
                    sh, fee = amt / px, amt * FEE
                    cash -= amt + fee
                    held = pos.get(o["ticker"], {"shares": 0.0, "cost_usd": 0.0})
                    pos[o["ticker"]] = {"shares": held["shares"] + sh, "cost_usd": held["cost_usd"] + amt + fee}
                    stops[o["ticker"]] = {"stop": round(px - sig["sl"], 4), "entry": round(px, 4), "date": ds, "sl": round(sig["sl"], 4)}
                    c.execute("INSERT OR REPLACE INTO positions (portfolio, ticker, shares, cost_usd) VALUES (?,?,?,?)",
                              (P, o["ticker"], pos[o["ticker"]]["shares"], pos[o["ticker"]]["cost_usd"]))
                    c.execute("UPDATE orders SET status='filled', fill_date=?, fill_price=?, shares=?, fee=?, realized_usd=0, note=? WHERE id=?",
                              (ds, px, sh, fee, f"stop เริ่มต้น {px - sig['sl']:.2f}", o["id"]))
                    pend_info.pop(str(o["id"]), None)
                    paper.log("execution", f"BUY {o['ticker']} {sh:.4f} หุ้น @ {px:.2f} · stop {px - sig['sl']:.2f}", {"order_id": o["id"]}, time=ds)
                # 2) stop ภายในวัน (ทำงานแม้โหมดพัก)
                for t in list(pos):
                    p, st = pos[t], stops.get(t)
                    if t not in C.columns or st is None or C.at[day, t] != C.at[day, t]:
                        continue
                    o_, l_ = O.at[day, t], L.at[day, t]
                    exit_px = o_ if o_ == o_ and o_ <= st["stop"] else (st["stop"] if l_ == l_ and l_ <= st["stop"] else None)
                    if exit_px is None:
                        continue
                    gross = p["shares"] * exit_px
                    fee = gross * FEE
                    cash += gross - fee
                    realized = gross - fee - p["cost_usd"]
                    r_mult = (exit_px - st["entry"]) / st["sl"] if st.get("sl") else None
                    events.append({"ticker": t, "px": exit_px, "shares": p["shares"], "fee": fee, "realized": realized, "R": r_mult,
                                   "gap": exit_px == o_, "stop": st["stop"], "entry": st["entry"], "cost": p["cost_usd"]})
                    c.execute("DELETE FROM positions WHERE portfolio=? AND ticker=?", (P, t))
                    del pos[t], stops[t]
                    paper.log("execution", f"SELL {t} ชน stop @ {exit_px:.2f} ({r_mult:+.2f}R)", {"realized": round(realized, 2)}, time=ds)
                # 3) เลื่อน trailing stop
                for t in pos:
                    if t in stops and t in A.columns and A.at[day, t] == A.at[day, t] and C.at[day, t] == C.at[day, t]:
                        stops[t]["stop"] = round(max(stops[t]["stop"], C.at[day, t] - B["trail_atr"] * A.at[day, t]), 4)
                # 4) มูลค่าตอนปิด
                px_close = C.loc[:day].ffill().iloc[-1]
                vals = {t: p["shares"] * px_close[t] for t, p in pos.items() if t in px_close and px_close[t] == px_close[t]}
                equity = cash + sum(vals.values())
                bench = paper.get("capital") * (1 - config.COSTS["fx_spread_pct"]) * px_close[config.BENCHMARK] / paper.get("bench_start")
                fxs = fx.loc[:day]
                fxv = float(fxs.iloc[-1]) if len(fxs) else 0.0
                c.execute("INSERT OR REPLACE INTO equity (portfolio, date, usd, thb, bench_usd) VALUES (?,?,?,?,?)",
                          (P, ds, equity, equity * fxv, float(bench)))
                # 5) สัญญาณจากราคาปิดวันล่าสุดเท่านั้น
                signals = []
                if day == idx[-1] and not paper.get("paused"):
                    signals = _signals(c, day, C, A, sma, dn, uni, pos, vals, equity, cash)
                if events or signals:
                    for oid, info in _write_packet(c, ds, events, signals, pos, vals, equity, cash, stops, uni, C, day, sma).items():
                        pend_info[str(oid)] = info
            for k in [k for k in pend_info if not c.execute("SELECT 1 FROM orders WHERE id=? AND status='approved'", (int(k),)).fetchone()]:
                pend_info.pop(k)   # ยกเลิก/หมดอายุ
            paper.put("pending", pend_info)
            paper.put("cash", cash)
            paper.put("stops", stops)
            if len(days):
                paper.put("last_marked", str(days[-1].date()))


def _signals(c, day, C, A, sma, dn, uni, pos, vals, equity, cash):
    spy = C[config.BENCHMARK]
    spy_ok = bool(spy.at[day] > spy.loc[:day].tail(200).mean()) if spy.loc[:day].notna().sum() >= 200 else False
    pending = c.execute("SELECT ticker, usd FROM orders WHERE portfolio=? AND status='approved' AND side='BUY'", (P,)).fetchall()
    pend = {r["ticker"]: r["usd"] for r in pending}
    room = B["max_positions"] - len(pos) - len(pend)
    paper.put("w1_market", {"date": str(day.date()), "spy_above_sma200": spy_ok})
    if not spy_ok or room <= 0:
        return []
    dig = paper.get("news_digest", shared=True) or {}
    blocked = {t for t, s in (dig.get("stocks") or {}).items() if s.get("flag") == "block"}
    cands = []
    for t in uni:
        if t in pos or t in pend or t in blocked or t not in C.columns:
            continue
        cl, d_, s_, a_ = C.at[day, t], dn.at[day, t], sma.at[day, t], A.at[day, t]
        if all(v == v for v in (cl, d_, s_, a_)) and a_ > 0 and cl > d_ and cl > s_:
            cands.append((t, cl / d_ - 1))
    cands.sort(key=lambda x: -x[1])
    peak = c.execute("SELECT MAX(usd) m FROM equity WHERE portfolio=?", (P,)).fetchone()["m"] or equity
    dd = max(0.0, 1 - equity / peak)
    w = B["weight"] * (0.5 if dd >= config.RISK["dd_cut_level"] else 1.0)
    budget = cash - config.RISK["min_cash"] * equity - sum(pend.values())
    grp = {}
    for t, v in vals.items():
        g = (uni.get(t) or ["", "other"])[1]
        grp[g] = grp.get(g, 0) + v / equity
    for t, us in pend.items():
        g = (uni.get(t) or ["", "other"])[1]
        grp[g] = grp.get(g, 0) + us / equity
    out = []
    for t, above in cands:
        if len(out) >= room:
            break
        usd = w * equity
        g = (uni.get(t) or ["", "other"])[1]
        if usd > budget or grp.get(g, 0) + w > config.RISK["max_group"] + 1e-9:
            continue
        budget -= usd
        grp[g] = grp.get(g, 0) + w
        out.append({"ticker": t, "usd": usd, "ref": float(C.at[day, t]), "sl": float(B["sl_atr"] * A.at[day, t]),
                    "above": above, "group": g, "w": w, "atr": float(A.at[day, t])})
    return out


def _write_packet(c, ds, events, signals, pos, vals, equity, cash, stops, uni, C, day, sma):
    inv = {t: v / equity for t, v in vals.items()}
    target = dict(inv)
    for s in signals:
        target[s["ticker"]] = target.get(s["ticker"], 0) + s["w"]
    target[config.CASH_PROXY] = max(0.0, 1 - sum(target.values()))
    ranking = [{"ticker": s["ticker"], "rank": i + 1, "mom": round(s["above"], 4), "above_sma": True} for i, s in enumerate(signals)]
    warns = [f"{e['ticker']}: ราคาเปิดกระโดดต่ำกว่า stop ขายได้ {e['px']:.2f} (stop {e['stop']:.2f})" for e in events if e["gap"]]
    data = {"portfolio": P, "strategy": "W1 เทรดรอบ", "signal_date": ds, "target": {t: round(v, 4) for t, v in target.items()},
            "equity": round(equity, 2), "cash": round(cash, 2), "ranking": ranking, "stops": stops,
            "regime": {"regime": "risk_on" if signals else "", "reasons": []},
            "news": {"enabled": True, "blocked": [], "caution": []}, "macro": None,
            "_status": "decided" if signals else "no_action",
            "_audit": {"status": "warn" if warns else "pass", "fails": [], "warns": warns,
                       "checks": ["ไม้ละ ≤ 5% · เงินสด ≥ 5% · กลุ่มเดียว ≤ 40% · stop ทุกไม้"]}}
    lines = [f"W1 เทรดรอบ · ข้อมูลปิด {ds}"]
    for e in events:
        lines.append(f"ขาย {e['ticker']} ชน stop ที่ {e['px']:.2f} ({e['R']:+.2f}R, {'กำไร' if e['realized'] >= 0 else 'ขาดทุน'} ${abs(e['realized']):,.0f})"
                     + (" — ราคาเปิดกระโดดต่ำกว่า stop" if e["gap"] else ""))
    for s in signals:
        lines.append(f"ซื้อ {s['ticker']} ({(uni.get(s['ticker']) or [''])[0][:40]}) ${s['usd']:,.0f} — ปิด {s['ref']:.2f} ทะลุจุดสูงสุด 55 วัน "
                     f"{s['above']:+.1%} · stop เริ่มต้นราว {s['ref'] - s['sl']:.2f} ({-s['sl'] / s['ref']:.1%})")
    if not signals and events:
        lines.append("ไม่มีสัญญาณซื้อใหม่วันนี้")
    pid = _packet(c, ds, data, "\n".join(lines))
    for e in events:
        w_from = (e["shares"] * e["px"]) / max(equity, 1)
        _order(c, pid, e["ticker"], "SELL", e["shares"] * e["px"], e["stop"], w_from, 0.0,
               f"ชน trailing stop ({e['R']:+.2f}R จากราคาเข้า {e['entry']:.2f})" + (" · ราคาเปิดกระโดดต่ำกว่า stop" if e["gap"] else ""),
               "filled", paper.now_th(), (ds, e["px"], e["shares"], e["fee"], e["realized"]), "stop")
    info = {}
    for s in signals:
        oid = _order(c, pid, s["ticker"], "BUY", s["usd"], s["ref"], 0.0, s["w"],
                     f"ทะลุจุดสูงสุด 55 วัน {s['above']:+.1%} · เหนือเส้น 200 วัน · stop 3 ATR ≈ {s['sl'] / s['ref']:.1%}",
                     "approved", paper.now_th(), None, "อนุมัติอัตโนมัติ")
        info[oid] = {"signal": ds, "sl": round(s["sl"], 4)}
    paper.log("orchestrator", f"W1: แพ็กเกจ #{pid} ซื้อ {len(signals)} ขาย {len(events)}", {"date": ds}, time=ds)
    return info


# ---------------------------------------------------------------- Momentum กว้าง (พอร์ต momentum_wide)
class wide_universe:
    """ระหว่าง with: config.UNIVERSE มีหุ้นนอก S&P 500 ของ W1 เพิ่มเป็น kind stock (กฎ Momentum/Risk Manager/ผู้ตรวจสอบเห็นเหมือนหุ้นปกติ)
    ออกจาก with แล้วคืนค่าเดิม — พอร์ตอื่นไม่เห็นหุ้นเหล่านี้"""

    def __init__(self, uni):
        self.add = {t: {"kind": "stock", "group": g, "name": n} for t, (n, g) in uni.items() if t not in config.UNIVERSE}

    def __enter__(self):
        config.UNIVERSE.update(self.add)
        return self

    def __exit__(self, *a):
        for t in self.add:
            config.UNIVERSE.pop(t, None)


def wide_data(main_d):
    """ข้อมูลหลัก + ราคาหุ้นนอก S&P 500 (จากชุดเดียวกับ W1) จัดวันให้ตรงกับข้อมูลหลัก"""
    u = ensure_universe()
    bars = load_bars(set(u["tickers"]) | {config.BENCHMARK} | set(_held_all()), main_d)
    idx = main_d["close"].index
    new = [t for t in bars["close"].columns if t not in main_d["close"].columns]
    d = dict(main_d)
    d["close"] = main_d["close"].join(bars["close"][new].reindex(idx).ffill(limit=3))
    d["open"] = main_d["open"].join(bars["open"][new].reindex(idx))
    return d, u["tickers"]


def _held_all():
    with paper.conn() as c:
        return [r["ticker"] for r in c.execute("SELECT DISTINCT ticker FROM positions WHERE shares > 1e-9")]
