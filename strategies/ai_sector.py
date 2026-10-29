"""กลยุทธ์ 5 — AI หมุนกลุ่ม (เจ้าของสั่ง 2026-10-24): Claude เลือกสัดส่วน ETF รายกลุ่มธุรกิจ เดือนละครั้ง

ตัวเลือก: Select Sector SPDR 11 กลุ่ม (เฉพาะหุ้นใน S&P 500) + SPY + SHY (เงินสด)
เครื่องมือ (เห็นราคาถึงวันที่ส่งสัญญาณเท่านั้น):
- sectors        ตาราง 11 กลุ่ม: ผลตอบแทน 1/3/6/12 เดือน, คะแนนโมเมนตัม, เหนือ/ต่ำกว่าเส้น 200 วัน, ความผันผวน,
                 สัดส่วนหุ้นในกลุ่มที่ยังเป็นขาขึ้น + พอร์ตตามกฎ "Top 3 + กันขาลง" (ตัวเทียบจากงานวิจัย)
- price_history  ราคารายสัปดาห์ของ ETF ตัวเดียว
- sector_stocks  หุ้นในกลุ่มนั้นที่โมเมนตัมสูงสุด + ประเด็นข่าว 7 วัน (เท่าที่ตัวอ่านข่าวติดตาม)
- check_risk     ลองผ่าน Risk Manager · submit ส่งพอร์ต
Risk Manager: ETF ตัวเดียว ≤ 40%, กลุ่มเดียว ≤ 40%, เงินสด ≥ 5%, drawdown 15% → ลดครึ่ง (เหมือนพอร์ตอื่น)
ทดสอบย้อนหลังไม่ได้ (AI รู้ว่าที่ผ่านมากลุ่มไหนชนะ) → forward test อย่างเดียว
ล้มเหลว → ถือพอร์ตเดิม
"""
import json

import numpy as np

import config
from agents import llm
from strategies import ai_pick

CASH = config.CASH_PROXY
MAX_CALLS = 15
SECTORS = config.SECTOR_ETFS            # {ticker: ชื่อกลุ่มภาษาไทย}
CANDIDATES = list(SECTORS) + ["SPY", CASH]

SYSTEM = """คุณคือผู้จัดการกองทุนที่หมุนเงินระหว่างกลุ่มธุรกิจของตลาดหุ้นสหรัฐ ด้วย ETF รายกลุ่ม (Select Sector SPDR) ตัดสินใจเดือนละครั้ง
เป้าหมาย: ชนะ SPY ในระยะยาว และลงหนักสุด (drawdown) น้อยกว่า — เจ้าของซื้อผ่าน Dime เงินจริงเป็นบาท

ตัวเลือกมีเท่านี้: ETF 11 กลุ่ม, SPY (ทั้งตลาด), SHY (เงินสด)

วิธีทำงาน (รวมไม่เกิน 12 ครั้ง):
1. sectors ดูภาพรวมทุกกลุ่ม (ผลตอบแทน แนวโน้ม ความกว้างของขาขึ้น และพอร์ตตามกฎที่ใช้เป็นตัวเทียบ)
2. เจาะกลุ่มที่สนใจด้วย sector_stocks (หุ้นนำกลุ่ม + ข่าว) หรือ price_history
3. check_risk แล้ว submit

ข้อมูลจากงานวิจัยของระบบนี้ (2011–2024, หลังหักค่าธรรมเนียม):
- กฎ "Top 3 + กันขาลง" ทุกเดือน ได้ราว 15%/ปี ลงหนักสุด −17% ในปี 2020–2024 แต่ได้แค่ราว 6%/ปี ในปี 2011–2019
  (สลับกลุ่มไปมาเสียค่าธรรมเนียมตอนตลาดขึ้นพร้อมกันทุกกลุ่ม) — ถือทุกกลุ่มเท่ากันได้ใกล้ SPY
- แปลว่าการหมุนกลุ่มช่วยตอนตลาดแยกทาง/ขาลง แต่เสียเปรียบตอนตลาดขึ้นทั้งกระดาน ให้คิดเรื่องนี้ด้วย

กติกา:
- ใช้ราคา ข่าว และตัวเลขจากเครื่องมือเท่านั้น ห้ามใช้ความจำเรื่องราคา/เหตุการณ์หลังวันที่ date
- ETF ตัวเดียว ≤ 0.40 · SHY ≥ 0.05 · น้ำหนักรวม = 1
- ตลาดขาลง (regime risk_off) หรือ exposure_hint ต่ำ → ถือ SHY มากขึ้น
- ถ้าภาพไม่ชัด ถือ SPY เป็นหลักได้ ไม่จำเป็นต้องหมุนกลุ่มทุกเดือน — ซื้อขายมีต้นทุน ของเดิมยังดีให้ถือต่อ
- why ต่อตัวอ้างหลักฐานจากเครื่องมือ เป็นภาษาไทยสั้นๆ"""

TOOLS = [
    {"name": "sectors", "description": "ตารางทุกกลุ่ม ณ วันที่ส่งสัญญาณ + พอร์ตตามกฎ Top 3 + กันขาลง",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "price_history", "description": "ราคารายสัปดาห์ย้อนหลังและสถิติของ ETF ตัวเดียว",
     "input_schema": {"type": "object", "properties": {"ticker": {"type": "string"}, "months": {"type": "integer", "description": "สูงสุด 24"}},
                      "required": ["ticker"]}},
    {"name": "sector_stocks", "description": "หุ้น S&P 500 ในกลุ่มของ ETF นั้น ที่โมเมนตัมสูงสุด 8 ตัว + ประเด็นข่าว 7 วันของหุ้นในกลุ่มที่ตัวอ่านข่าวติดตาม",
     "input_schema": {"type": "object", "properties": {"ticker": {"type": "string", "description": "ETF ของกลุ่ม เช่น XLK"}}, "required": ["ticker"]}},
    ai_pick.TOOLS[4],   # check_risk
    {"name": "submit", "description": "ส่งพอร์ตสุดท้าย (จบงาน)",
     "input_schema": {"type": "object", "properties": {
         "weights": {"type": "object", "additionalProperties": {"type": "number"}},
         "why": {"type": "object", "additionalProperties": {"type": "string"}},
         "view": {"type": "string", "description": "มุมมองกลุ่มธุรกิจ/ตลาด 1–3 ประโยค"}},
         "required": ["weights", "why", "view"]}},
]


def _ret(c, d):
    return float(c.iloc[-1] / c.iloc[-d - 1] - 1) if len(c) > d and c.iloc[-d - 1] > 0 else None


def scores(close, date):
    """คะแนน = เฉลี่ยผลตอบแทน 3/6/12 เดือน (กฎเดียวกับ research/sector_etfs.py)"""
    out = {}
    for t in SECTORS:
        if t not in close.columns:
            continue
        c = close[t].loc[:date].dropna()
        r = [_ret(c, 21 * m) for m in (3, 6, 12)]
        if None not in r:
            out[t] = sum(r) / 3
    return out


def rule_top3t(close, date):
    """ตัวเทียบ: 3 กลุ่มคะแนนสูงสุด คนละ 1/3 กลุ่มที่ต่ำกว่าเส้น 200 วัน → SHY"""
    s = scores(close, date)
    w = {}
    for t in sorted(s, key=lambda t: -s[t])[:3]:
        c = close[t].loc[:date].dropna()
        k = CASH if len(c) >= 200 and c.iloc[-1] < c.iloc[-200:].mean() else t
        w[k] = w.get(k, 0) + 1 / 3
    return w or {CASH: 1.0}


class Research(ai_pick.Research):
    portfolio = "ai_sector"

    def valid(self, t):
        return t in CANDIDATES and super().valid(t)

    def sectors(self):
        s = scores(self.close, self.date)
        rank = {t: i + 1 for i, t in enumerate(sorted(s, key=lambda t: -s[t]))}
        rows = []
        for t, th in SECTORS.items():
            if not self.valid(t):
                continue
            c = self.close[t].dropna()
            g = config.UNIVERSE[t]["group"]
            mem = [x for x, m in config.UNIVERSE.items() if m["kind"] == "stock" and m["group"] == g and ai_pick.Research.valid(self, x)
                   and self.sma[x] == self.sma[x]]
            up = sum(1 for x in mem if self.px[x] > self.sma[x])
            rows.append({"ticker": t, "sector": th, "score_3_6_12m": ai_pick._r(s.get(t)), "rank": rank.get(t),
                         **{f"return_{k}": ai_pick._r(_ret(c, d)) for k, d in (("1m", 21), ("3m", 63), ("6m", 126), ("12m", 252))},
                         "vs_sma200": ai_pick._r(self.px[t] / self.sma[t] - 1) if self.sma[t] == self.sma[t] else None,
                         "vol_3m": ai_pick._r(self.vol[t]),
                         "stocks_in_uptrend": f"{up}/{len(mem)}" if mem else None, "held": t in self.held})
        rows.sort(key=lambda r: r["rank"] or 99)
        spy = self.close["SPY"].dropna()
        return {"date": str(self.date.date()), "rows": rows,
                "SPY": {"return_3m": ai_pick._r(_ret(spy, 63)), "return_12m": ai_pick._r(_ret(spy, 252)),
                        "vs_sma200": ai_pick._r(self.px["SPY"] / self.sma["SPY"] - 1)},
                "rule_top3_trend": {t: round(v, 3) for t, v in rule_top3t(self.close, self.date).items()}}

    def sector_stocks(self, ticker):
        t = str(ticker).upper()
        if t not in SECTORS:
            return {"error": f"{t} ไม่ใช่ ETF รายกลุ่ม ใช้ได้: {', '.join(SECTORS)}"}
        g = config.UNIVERSE[t]["group"]
        mem = [x for x, m in config.UNIVERSE.items() if m["kind"] == "stock" and m["group"] == g and ai_pick.Research.valid(self, x)
               and self.mom[x] == self.mom[x]]
        top = sorted(mem, key=lambda x: -self.mom[x])[:8]
        news = {x: s for x, s in self.news_digest.items() if config.info(x)["group"] == g and (s.get("items") or s.get("flag") != "ok")}
        return {"sector": SECTORS[t], "top_momentum": [{"ticker": x, "name": config.UNIVERSE[x].get("name"),
                                                        "momentum_6_12m": ai_pick._r(self.mom[x]),
                                                        "vs_sma200": ai_pick._r(self.px[x] / self.sma[x] - 1) if self.sma[x] == self.sma[x] else None}
                                                       for x in top],
                "news": {x: {"flag": s.get("flag"), "items": [i for i in s.get("items", []) if i.get("importance", 1) >= 2][:3]}
                         for x, s in list(news.items())[:10]},
                "note": "ข่าวมีเฉพาะหุ้นที่ระบบติดตาม (ถืออยู่ + โมเมนตัมสูงสุด 30 ตัว) ไม่ใช่ทั้งกลุ่ม"}

    def check_risk(self, weights):
        from agents import risk_agent
        w, dropped = self.clean(weights)
        fin = risk_agent.apply(w, drawdown=self.drawdown, portfolio=self.portfolio)
        return {"final_weights": fin["weights"], "risk_manager_changes": fin["log"], "dropped_not_allowed_or_no_price": dropped}

    def handlers(self):
        return {"sectors": self.sectors, "price_history": self.price_history, "sector_stocks": self.sector_stocks,
                "check_risk": self.check_risk}


def propose(close, ind, date, regime, p=None, held=(), extra=None):
    extra = extra or {}
    s = scores(close, date)
    ranking = [{"ticker": t, "rank": i + 1, "mom": round(float(s[t]), 3), "above_sma": bool(close.at[date, t] > ind["sma"].at[date, t])}
               for i, t in enumerate(sorted(s, key=lambda t: -s[t]))]
    cur = extra.get("current_weights") or {}
    hold_w = cur or ({t: 1.0 / len(held) for t in held} if held else {CASH: 1.0})
    fallback = {"weights": hold_w, "why": {t: "AI ไม่ได้ส่งพอร์ต → ถือพอร์ตเดิม" for t in hold_w}, "ranking": ranking,
                "rule": rule_top3t(close, date)}
    if not llm.enabled():
        fallback["why"] = {t: "ยังไม่ได้ใส่ Anthropic API key → ถือเงินสด/พอร์ตเดิม" for t in hold_w}
        return fallback
    rs = Research(close, ind, date, held, news=extra.get("news"), drawdown=extra.get("drawdown", 0.0))
    rs.portfolio = extra.get("agent", "ai_sector")
    user = json.dumps({"date": str(date.date()), "regime": regime.get("regime"), "exposure_hint": regime.get("exposure"),
                       "regime_reasons": regime.get("reasons"), "macro": extra.get("macro"),
                       "current_weights": {t: round(v, 3) for t, v in cur.items()} or "ยังไม่มี (เงินสดทั้งหมด)",
                       "budget_tool_calls": MAX_CALLS - 3}, ensure_ascii=False)
    data, trail = llm.run_agent(extra.get("agent", "ai_sector"), SYSTEM, user, TOOLS, rs.handlers(), final="submit", max_turns=10,
                                max_calls=MAX_CALLS, model=extra.get("model"))
    fallback["research"] = ai_pick._compact_trail(trail)
    if not data or not isinstance(data.get("weights"), dict):
        return fallback
    w, dropped = rs.clean(data["weights"])
    if set(w) == {CASH}:
        return fallback
    why_in = {str(k).upper(): str(v) for k, v in (data.get("why") or {}).items()}
    why = {t: why_in.get(t, "AI เลือก")[:160] for t in w}
    if CASH not in why_in:
        why[CASH] = "ส่วนที่ AI ไม่ได้จัดสรร ถือเป็นเงินสด"
    return {"weights": {t: round(v, 4) for t, v in w.items()}, "why": why, "ranking": ranking, "rule": fallback["rule"],
            "view": str(data.get("view", ""))[:400], "research": fallback["research"], "dropped": dropped}
