"""กลยุทธ์ 4 — AI Pick แบบ agent: Claude ค้นข้อมูลเองด้วยเครื่องมือ แล้วจัดพอร์ต (ภายใต้ Risk Manager เดียวกัน)

เครื่องมือ (ทุกตัวเห็นราคาถึงวันที่ส่งสัญญาณเท่านั้น — ไม่มีข้อมูลอนาคต):
- screen         คัดหุ้น/ETF จาก S&P 500 + ETF ตามโมเมนตัม แนวโน้ม ความผันผวน กลุ่มธุรกิจ
- price_history  ราคารายสัปดาห์ + ผลตอบแทน/ลงหนักสุด/ระยะจากจุดสูงสุด ของตัวที่สนใจ
- fundamentals   ตัวเลขพื้นฐานจาก Finnhub (P/E, การเติบโต, อัตรากำไร, ROE, หนี้)
- news           ประเด็นข่าว 7 วัน (จากตัวอ่านข่าว ถ้าไม่มีดึงหัวข่าวสด)
- check_risk     ลองส่งพอร์ตผ่าน Risk Manager ดูว่าถูกตัดตรงไหนก่อนส่งจริง
- submit         ส่งพอร์ตสุดท้าย + เหตุผลต่อตัว
โค้ดบังคับหลังจากนั้น: ตัวที่ไม่อยู่ในรายชื่อ/ไม่มีราคาถูกตัด น้ำหนักติดลบถูกตัด ส่วนที่เหลือเป็นเงินสด
แล้ว Risk Manager จับเพดานเหมือนพอร์ตอื่น — AI เพิ่มความเสี่ยงเกินกฎไม่ได้

ล้มเหลว (ไม่มี key / agent ไม่ส่งคำตอบ) → ถือพอร์ตเดิม ไม่เปลี่ยน
"""
import json
import time

import numpy as np

import config
from agents import llm

CASH = "SHY"
MAX_CALLS = 25

SYSTEM = """คุณคือผู้จัดการกองทุนที่ลงทุนระยะกลาง (ถือเป็นสัปดาห์ถึงหลายเดือน) ในหุ้น S&P 500 และ ETF สหรัฐ
เป้าหมาย: ชนะ S&P 500 (SPY) ในระยะยาว โดยลงหนักสุด (drawdown) น้อยกว่า — เงินจริงของเจ้าของพอร์ตเป็นบาท ซื้อผ่าน Dime

วิธีทำงาน (คุณเลือกลำดับและจำนวนครั้งเองได้ แต่รวมไม่เกิน 20 ครั้ง):
1. screen หาตัวเลือก (เช่น โมเมนตัมสูงที่ยังอยู่เหนือเส้น 200 วัน, หรือกลุ่มที่แข็งแรง) และดูของที่ถืออยู่
2. เจาะตัวที่สนใจด้วย price_history / fundamentals / news — อย่าเลือกตัวที่ยังไม่ได้ดูข้อมูลอย่างน้อย 1 อย่าง
3. check_risk ลองพอร์ตก่อน แล้วปรับถ้าถูกตัด
4. submit ส่งพอร์ตสุดท้าย

ข้อห้าม/กติกา:
- ใช้ข้อมูลจากเครื่องมือเท่านั้นสำหรับราคา ข่าว และตัวเลข ห้ามใช้ความจำของคุณเรื่องราคาหรือเหตุการณ์หลังวันที่ date
  (ความรู้ทั่วไปว่าบริษัททำธุรกิจอะไรใช้ได้)
- 3–10 ตัว · หุ้นรายตัว ≤ 0.15 · ETF ≤ 0.25 · กลุ่มเดียวกัน (เช่น tech) รวม ≤ 0.40 · เงินสด (SHY) ≥ 0.05
- ตลาดขาลง (regime risk_off) หรือ exposure_hint ต่ำ → ถือของเสี่ยงน้อยลง เพิ่มพันธบัตร (TLT/IEF) ทอง (GLD) หรือ SHY
- งบใกล้ออก = ราคาอาจกระโดด, ข่าว flag block = ห้ามเลือก, หุ้นที่ขึ้นแรงมาก (เช่น +200% ใน 12 เดือน) ผันผวนสูง ให้น้ำหนักน้อย
- อย่าเปลี่ยนพอร์ตทั้งหมดทุกสัปดาห์ ถ้าของเดิมยังดีให้ถือต่อ (ซื้อขายมีต้นทุน)
- why ของแต่ละตัวต้องอ้างหลักฐานจากเครื่องมือ (ตัวเลข/ข่าว) เป็นภาษาไทยสั้นๆ"""

TOOLS = [
    {"name": "screen", "description": "คัดหุ้น/ETF ณ วันที่ส่งสัญญาณ เรียงตามเกณฑ์ที่เลือก คืนตารางตัวเลข",
     "input_schema": {"type": "object", "properties": {
         "sort": {"type": "string", "enum": ["momentum", "return_1m", "vs_sma200", "low_vol"], "description": "momentum = ผลตอบแทน 6–12 เดือน"},
         "kind": {"type": "string", "enum": ["stock", "etf", "all"]},
         "group": {"type": "string", "description": "กลุ่ม เช่น tech, comm, health, financial, energy, industrial, consumer, staples, utilities, materials, real_estate"},
         "only_uptrend": {"type": "boolean", "description": "เฉพาะตัวที่ราคาอยู่เหนือเส้น 200 วัน"},
         "n": {"type": "integer", "description": "จำนวนแถว (สูงสุด 40)"}}}},
    {"name": "price_history", "description": "ราคารายสัปดาห์ย้อนหลังและสถิติของตัวเดียว",
     "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}, "months": {"type": "integer", "description": "ย้อนหลังกี่เดือน (สูงสุด 24)"}},
                      "required": ["ticker"]}},
    {"name": "fundamentals", "description": "ตัวเลขพื้นฐานของหุ้น (P/E, P/S, การเติบโตรายได้/กำไร, อัตรากำไร, ROE, หนี้ต่อทุน, beta, มูลค่าตลาด)",
     "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
    {"name": "news", "description": "ประเด็นข่าว 7 วันล่าสุดของหุ้น",
     "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}},
    {"name": "check_risk", "description": "ลองส่งพอร์ตผ่าน Risk Manager (ไม่ใช่การส่งจริง) คืนน้ำหนักหลังบังคับกฎและสิ่งที่ถูกตัด",
     "input_schema": {"type": "object", "properties": {"weights": {"type": "object", "additionalProperties": {"type": "number"}}},
                      "required": ["weights"]}},
    {"name": "submit", "description": "ส่งพอร์ตสุดท้าย (จบงาน)",
     "input_schema": {"type": "object", "properties": {
         "weights": {"type": "object", "additionalProperties": {"type": "number"}},
         "why": {"type": "object", "additionalProperties": {"type": "string"}, "description": "เหตุผลต่อตัว อ้างหลักฐาน"},
         "view": {"type": "string", "description": "มุมมองตลาด/พอร์ต 1–3 ประโยค"}},
         "required": ["weights", "why", "view"]}},
]

FUND_KEYS = {"marketCapitalization": "market_cap_musd", "peTTM": "pe_ttm", "psTTM": "ps_ttm", "pbAnnual": "pb",
             "revenueGrowthTTMYoy": "revenue_growth_yoy_pct", "revenueGrowth5Y": "revenue_growth_5y_pct",
             "epsGrowthTTMYoy": "eps_growth_yoy_pct", "grossMarginTTM": "gross_margin_pct",
             "operatingMarginTTM": "operating_margin_pct", "netProfitMarginTTM": "net_margin_pct", "roeTTM": "roe_pct",
             "totalDebt/totalEquityQuarterly": "debt_to_equity", "currentRatioQuarterly": "current_ratio",
             "dividendYieldIndicatedAnnual": "dividend_yield_pct", "beta": "beta",
             "52WeekHigh": "high_52w", "52WeekLow": "low_52w"}


def _r(x, n=3):
    return None if x is None or x != x else round(float(x), n)


class Research:
    """เครื่องมือของ AI — ทุกตัวตัดข้อมูลราคาถึง `date` เท่านั้น"""

    def __init__(self, close, ind, date, held, news=None, drawdown=0.0, blocked=()):
        self.close = close.loc[:date]
        self.px, self.sma, self.mom, self.vol = (x.loc[date] for x in (close, ind["sma"], ind["mom"], ind["vol"]))
        self.date, self.held, self.news_digest = date, set(held), news or {}
        self.drawdown, self.blocked = drawdown, list(blocked)
        self._fund = {}

    def valid(self, t):
        return t in config.UNIVERSE and t in self.close.columns and self.px[t] == self.px[t] and self.vol[t] == self.vol[t]

    def _row(self, t):
        c = self.close[t].dropna()
        m = config.UNIVERSE[t]
        return {"ticker": t, "name": m.get("name") or "", "kind": m["kind"], "group": m["group"],
                "momentum_6_12m": _r(self.mom[t]), "return_1m": _r(c.iloc[-1] / c.iloc[-22] - 1) if len(c) > 22 else None,
                "vol_3m": _r(self.vol[t]), "vs_sma200": _r(self.px[t] / self.sma[t] - 1) if self.sma[t] == self.sma[t] else None,
                "held": t in self.held}

    def screen(self, sort="momentum", kind="stock", group=None, only_uptrend=False, n=20):
        n = max(1, min(int(n or 20), 40))
        rows = []
        for t, m in config.UNIVERSE.items():
            if m["kind"] == "cash" or not self.valid(t):
                continue
            if kind != "all" and m["kind"] != kind and not (kind == "etf" and m["kind"] == "defensive"):
                continue
            if group and m["group"] != group:
                continue
            r = self._row(t)
            if only_uptrend and not (r["vs_sma200"] or -1) > 0:
                continue
            rows.append(r)
        key = {"momentum": lambda r: -(r["momentum_6_12m"] if r["momentum_6_12m"] is not None else -9),
               "return_1m": lambda r: -(r["return_1m"] if r["return_1m"] is not None else -9),
               "vs_sma200": lambda r: -(r["vs_sma200"] if r["vs_sma200"] is not None else -9),
               "low_vol": lambda r: r["vol_3m"] if r["vol_3m"] is not None else 9}.get(sort)
        rows.sort(key=key)
        return {"date": str(self.date.date()), "count_matched": len(rows), "rows": rows[:n]}

    def price_history(self, ticker, months=12):
        t = str(ticker).upper()
        if not self.valid(t):
            return {"error": f"ไม่มีข้อมูลราคา {t} หรือไม่อยู่ในรายชื่อ"}
        months = max(1, min(int(months or 12), 24))
        c = self.close[t].dropna()
        win = c.iloc[-months * 21:]
        wk = win.groupby(win.index.to_period("W-FRI")).tail(1)   # วันซื้อขายสุดท้ายของแต่ละสัปดาห์ (วันที่จริง ไม่ใช่วันศุกร์ล่วงหน้า)
        ret = lambda d: _r(c.iloc[-1] / c.iloc[-d - 1] - 1) if len(c) > d else None
        peak = win.cummax()
        return {"ticker": t, "name": config.UNIVERSE[t].get("name") or "", "last": _r(c.iloc[-1], 2),
                "return": {"1m": ret(21), "3m": ret(63), "6m": ret(126), "12m": ret(252)},
                "max_drawdown_window": _r((win / peak - 1).min()), "from_high_window": _r(c.iloc[-1] / win.max() - 1),
                "vol_3m": _r(self.vol[t]), "vs_sma200": _r(self.px[t] / self.sma[t] - 1) if self.sma[t] == self.sma[t] else None,
                "weekly_close": {str(d.date()): _r(v, 2) for d, v in wk.iloc[-60:].items()}}

    def fundamentals(self, ticker):
        t = str(ticker).upper()
        if config.info(t)["kind"] != "stock":
            return {"error": "ตัวเลขพื้นฐานมีเฉพาะหุ้นรายตัว"}
        if t in self._fund:
            return self._fund[t]
        from agents import news_agent
        key = news_agent._finnhub_key()
        if not key:
            return {"error": "ยังไม่ได้ใส่ Finnhub key"}
        time.sleep(1.1)   # Finnhub ฟรี 60 ครั้ง/นาที
        m = (news_agent._get(f"https://finnhub.io/api/v1/stock/metric?symbol={t.replace('-', '.')}&metric=all&token={key}") or {}).get("metric") or {}
        out = {"ticker": t, "name": config.info(t).get("name"), **{v: _r(m.get(k), 2) for k, v in FUND_KEYS.items() if m.get(k) is not None}}
        self._fund[t] = out if len(out) > 2 else {"error": f"ไม่มีตัวเลขพื้นฐานของ {t}"}
        return self._fund[t]

    def news(self, ticker):
        t = str(ticker).upper()
        s = self.news_digest.get(t)
        if s is not None:
            return {"ticker": t, "source": "ตัวอ่านข่าวประจำวัน", **s}
        from agents import news_agent
        key = news_agent._finnhub_key()
        if not key:
            return {"ticker": t, "items": [], "note": "ไม่มีข่าวในสรุปประจำวัน และยังไม่ได้ใส่ Finnhub key"}
        from datetime import timedelta
        today = self.date.date()
        time.sleep(1.1)
        rows = news_agent._finnhub_news(t, key, str(today - timedelta(days=7)), str(today))
        return {"ticker": t, "source": "หัวข่าวสด (ยังไม่ผ่านตัวอ่านข่าว)",
                "headlines": [{"date": r["date"], "title": r["title"], "summary": r["summary"][:200]} for r in rows[:6]]}

    def clean(self, weights):
        """แปลงน้ำหนักที่ AI ส่งมา: ตัดตัวที่ไม่รู้จัก/ไม่มีราคา/ติดลบ, รวมเกิน 1 → ย่อ, ขาด → เงินสด"""
        w, dropped = {}, []
        for t, v in (weights or {}).items():
            t = str(t).upper()
            try:
                v = float(v)
            except (TypeError, ValueError):
                dropped.append(t)
                continue
            if v > 0 and self.valid(t):
                w[t] = w.get(t, 0) + v
            elif v > 0:
                dropped.append(t)
        tot = sum(w.values())
        if tot > 1:
            w = {t: v / tot for t, v in w.items()}
        if sum(w.values()) < 1:
            w[CASH] = w.get(CASH, 0) + 1 - sum(w.values())
        return w, dropped

    def check_risk(self, weights):
        from agents import risk_agent
        w, dropped = self.clean(weights)
        fin = risk_agent.apply(w, drawdown=self.drawdown, blocked=self.blocked, portfolio="ai_pick")
        return {"final_weights": fin["weights"], "risk_manager_changes": fin["log"],
                "dropped_unknown_or_no_price": dropped}

    def handlers(self):
        return {"screen": self.screen, "price_history": self.price_history, "fundamentals": self.fundamentals,
                "news": self.news, "check_risk": self.check_risk}


def _compact_trail(trail):
    out = []
    for x in trail:
        if "think" in x:
            out.append({"think": x["think"][:300]})
        else:
            out.append({"tool": x["tool"], "input": x.get("input"), "result": (x.get("result") or "")[:200]})
    return out[:60]


def propose(close, ind, date, regime, p=None, held=(), extra=None):
    extra = extra or {}
    from agents import regime_agent
    px, sma, mom = (x.loc[date] for x in (close, ind["sma"], ind["mom"]))
    ranked = [t for t in regime_agent.top_stocks(close, ind, date, 12)]
    ranking = [{"ticker": t, "rank": i + 1, "mom": round(float(mom[t]), 3), "above_sma": bool(px[t] > sma[t])}
               for i, t in enumerate(ranked)]
    cur = extra.get("current_weights") or {}
    hold_w = cur or ({t: 1.0 / len(held) for t in held} if held else {CASH: 1.0})
    fallback = {"weights": hold_w, "why": {t: "AI ไม่ได้ส่งพอร์ต → ถือพอร์ตเดิม" for t in hold_w}, "ranking": ranking}
    if not llm.enabled():
        fallback["why"] = {t: "ยังไม่ได้ใส่ Anthropic API key → ถือเงินสด/พอร์ตเดิม" for t in hold_w}
        return fallback

    rs = Research(close, ind, date, held, news=extra.get("news"), drawdown=extra.get("drawdown", 0.0),
                  blocked=extra.get("blocked", ()))
    user = json.dumps({"date": str(date.date()), "regime": regime.get("regime"), "exposure_hint": regime.get("exposure"),
                       "regime_reasons": regime.get("reasons"), "macro": extra.get("macro"),
                       "current_weights": {t: round(v, 3) for t, v in cur.items()} or "ยังไม่มี (เงินสดทั้งหมด)",
                       "news_flags": {t: s.get("flag") for t, s in (extra.get("news") or {}).items() if s.get("flag") != "ok"},
                       "budget_tool_calls": MAX_CALLS - 5}, ensure_ascii=False)
    data, trail = llm.run_agent("ai_pick", SYSTEM, user, TOOLS, rs.handlers(), final="submit", max_calls=MAX_CALLS)
    fallback["research"] = _compact_trail(trail)
    if not data or not isinstance(data.get("weights"), dict):
        return fallback
    w, dropped = rs.clean(data["weights"])
    if set(w) == {CASH}:
        return fallback
    why_in = {str(k).upper(): str(v) for k, v in (data.get("why") or {}).items()}
    why = {t: why_in.get(t, "AI เลือก")[:160] for t in w}
    if CASH not in why_in:
        why[CASH] = "ส่วนที่ AI ไม่ได้จัดสรร ถือเป็นเงินสด"
    return {"weights": {t: round(v, 4) for t, v in w.items()}, "why": why, "ranking": ranking,
            "view": str(data.get("view", ""))[:400], "research": fallback["research"], "dropped": dropped}
