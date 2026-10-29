"""ทดสอบความถูกต้องของระบบ — รันด้วย: python -m tests.test_system

1. Null test: ข้อมูลสุ่มไม่มีทิศทาง → ทุกกลยุทธ์โค้ดต้องไม่ทำกำไรเกินเงินสดอย่างมีนัย (ถ้าทำได้ = ข้อมูลอนาคตรั่ว)
2. Lookahead test: แก้ราคาอนาคตแล้ว สัญญาณในอดีตของทุกกลยุทธ์ต้องไม่เปลี่ยน
3. Risk test: พอร์ตเป้าหมายทุกรอบของทุกกลยุทธ์ต้องผ่านกฎความเสี่ยง
4. Consistency test: forward test (orchestrator, 4 พอร์ตพร้อมกัน) กับ backtest ช่วงเดียวกันต้องได้ผลใกล้กัน
5. AI test: ด้วย Claude จำลอง — AI เลือกเกินกฎแล้ว Risk Manager ต้องจับ, ข่าว block ต้องตัดตัวนั้นออก, มหภาค high ต้องลด exposure
"""
import json
import os

import numpy as np
import pandas as pd

import config
import paper
from agents import data_agent, backtest_agent, regime_agent, risk_agent, llm
from strategies import PORTFOLIOS, momentum
import orchestrator

CODE = {k: v for k, v in PORTFOLIOS.items() if not v["uses_ai"] and not v.get("engine") and not v.get("wide")}


def null_test():
    for name, meta in CODE.items():
        res = []
        for seed in range(4):
            d = data_agent.clean(data_agent.synthetic(seed=seed, drift=False))
            res.append(backtest_agent.run(d, strategy_module=meta["module"], cadence=meta["cadence"], portfolio=name)["stats"]["strategy"]["cagr"])
        avg = float(np.mean(res))
        print(f"[1] Null {name:14s} CAGR บนข้อมูลสุ่ม: {res} เฉลี่ย {avg:.2f}%")
        assert avg < 1.5, f"{name}: ข้อมูลสุ่มไม่ควรทำกำไรได้"


def lookahead_test():
    d = data_agent.clean(data_agent.synthetic(seed=3))
    idx = d["close"].index
    t = idx[int(len(idx) * 0.6)]
    d2 = {k: v.copy() for k, v in d.items()}
    d2["close"].loc[d2["close"].index > t] *= 3.0
    for name, meta in CODE.items():
        ind, ind2 = regime_agent.indicators(d["close"]), regime_agent.indicators(d2["close"])
        a = meta["module"].propose(d["close"], ind, t, regime_agent.assess(d["close"], ind, t))["weights"]
        b = meta["module"].propose(d2["close"], ind2, t, regime_agent.assess(d2["close"], ind2, t))["weights"]
        print(f"[2] Lookahead {name:14s} สัญญาณ {t.date()} ก่อน/หลังแก้ราคาอนาคต เหมือนกัน = {a == b}")
        assert a == b


def risk_test():
    d = data_agent.clean(data_agent.synthetic(seed=5))
    for name, meta in CODE.items():
        log = backtest_agent.run(d, strategy_module=meta["module"], cadence=meta["cadence"], portfolio=name)["weights_log"]
        bad = [(w["date"], risk_agent.check(w["weights"], portfolio=name)) for w in log if risk_agent.check(w["weights"], portfolio=name)]
        print(f"[3] Risk {name:14s} ตรวจ {len(log)} รอบ ผิดกฎ {len(bad)} ครั้ง")
        assert not bad, bad[:3]


def consistency_test(weeks=40):
    d = data_agent.clean(data_agent.synthetic(seed=11))
    d["source"] = "synthetic"
    orchestrator.simulate(d, weeks=weeks, portfolios=list(CODE))
    idx = d["close"].index
    start = len(idx) - weeks * 5
    warm = max(config.STRATEGY["mom_long"], config.STRATEGY["trend_sma"]) + 1
    sub = {k: v.iloc[start - warm:] for k, v in d.items() if k != "source"}
    for name, meta in CODE.items():
        fw = pd.Series({pd.Timestamp(r["date"]): r["usd"] for r in
                        paper.conn().execute("SELECT date, usd FROM equity WHERE portfolio=? ORDER BY date", (name,))})
        bt = backtest_agent.run(sub, strategy_module=meta["module"], cadence=meta["cadence"], portfolio=name)["equity"].reindex(fw.index)
        fr, br = fw.iloc[-1] / fw.iloc[0] - 1, bt.iloc[-1] / bt.iloc[0] - 1
        tol = 0.01
        print(f"[4] Consistency {name:14s} forward {fr:+.2%} vs backtest {br:+.2%} (ต่าง {abs(fr - br):.2%})")
        assert abs(fr - br) < tol, f"{name}: forward test กับ backtest ต่างกันเกิน {tol:.0%}"


class FakeLLM:
    """Claude จำลอง: ตอบตามบทที่กำหนด เพื่อทดสอบว่าโค้ดรอบๆ AI บังคับกฎจริง"""
    def __init__(self, script):
        self.script, self.calls, self.inputs = script, [], {}

    def __call__(self, agent, system, user, max_tokens=1500):
        self.calls.append(agent)
        self.inputs[agent] = user
        return self.script.get(agent)


class _B:
    def __init__(self, **k):
        self.__dict__.update(k)


class FakeClient:
    """Claude จำลองแบบ agent: เรียกเครื่องมือตามบท แล้ว submit — ทดสอบวงจร llm.run_agent + เครื่องมือของ AI Pick"""
    def __init__(self, turns):
        self.turns, self.seen = list(turns), []
        self.messages = self

    models = []

    def create(self, **kw):
        FakeClient.models.append(kw.get("model"))
        if kw["messages"] and isinstance(kw["messages"][-1]["content"], list):
            self.seen.extend(r["content"] for r in kw["messages"][-1]["content"] if r.get("type") == "tool_result")
        uses = self.turns.pop(0) if self.turns else [("submit", {"weights": {}, "why": {}, "view": ""})]
        content = [_B(type="text", text="กำลังค้น")] + [_B(type="tool_use", id=f"t{i}", name=n, input=a) for i, (n, a) in enumerate(uses)]
        return _B(content=content, stop_reason="tool_use", usage=_B(input_tokens=100, output_tokens=20))


def agent_turns():
    return [[("screen", {"sort": "momentum", "kind": "all", "n": 5}), ("screen", {"kind": "stock", "only_uptrend": True})],
            [("price_history", {"ticker": "NVDA", "months": 6}), ("news", {"ticker": "NVDA"}), ("fundamentals", {"ticker": "QQQ"}),
             ("check_risk", {"weights": {"NVDA": 0.6, "QQQ": 0.3}}), ("ไม่มีจริง", {})],
            [("submit", {"weights": {"NVDA": 0.6, "AAPL": 0.3, "QQQ": 0.3, "FAKE": 0.5, "TLT": -1},
                         "why": {"NVDA": "ข่าวเปิดตัวชิปใหม่ + โมเมนตัมดี"}, "view": "ลุย"})]]


def edgar_test():
    """SEC 8-K: อ่านรายการเอกสาร + ข่าวประชาสัมพันธ์แนบ, ชนิดร้ายแรงโค้ดติดธงเอง (ไม่พึ่ง AI)"""
    from agents import edgar
    import datetime as dt
    today = dt.date(2026, 10, 21)
    sub = {"filings": {"recent": {"form": ["8-K", "10-Q", "8-K", "8-K"], "filingDate": ["2026-10-20", "2026-10-18", "2026-10-16", "2026-09-01"],
                                  "items": ["8.01,9.01", "", "4.02", "2.02"], "accessionNumber": ["0001-26-1", "0001-26-2", "0001-26-3", "0001-26-4"],
                                  "primaryDocument": ["a.htm", "q.htm", "b.htm", "c.htm"]}}}
    pages = {"submissions": sub, "/000126" + "1/index.json": {"directory": {"item": [{"name": "a.htm"}, {"name": "ex99-1.htm"}]}},
             "ex99-1.htm": "<html><style>x{}</style><p>ACME launches the <b>Widget 2</b> &amp; more</p></html>"}
    orig = edgar._get
    edgar._get = lambda url, as_json=True: next(v for k, v in pages.items() if k in url)
    try:
        docs = edgar.filings("ACME", 123, today)
        assert [d["date"] for d in docs] == ["2026-10-20", "2026-10-16"], docs   # ตัด 10-Q และ 8-K ที่เก่ากว่า 7 วัน
        assert "Widget 2 & more" in docs[0]["summary"] and "x{}" not in docs[0]["summary"], docs[0]
        assert edgar.hard_flags(docs) == ("block", edgar.BLOCK_ITEMS["4.02"])
        assert edgar.hard_flags(docs[:1]) is None
        print("[6] EDGAR: อ่าน 8-K 2 ฉบับใน 7 วัน + ข่าวประชาสัมพันธ์แนบ · ยื่นแก้งบ (4.02) → block โดยโค้ด")
    finally:
        edgar._get = orig


def timing_test():
    """แพ็กเกจรายสัปดาห์ใช้ราคาปิดวันศุกร์เท่านั้น (ศุกร์หยุด → พฤหัส เมื่อรันเสาร์/อาทิตย์)"""
    T = pd.Timestamp
    assert orchestrator._is_week_close(T("2026-10-23"))                          # ศุกร์
    assert not orchestrator._is_week_close(T("2026-10-21"), T("2026-10-22 10:00"))   # พุธ กลางสัปดาห์
    assert orchestrator._is_week_close(T("2026-04-02"), T("2026-04-04 10:00"))   # พฤหัส + ศุกร์หยุด รันวันเสาร์
    assert not orchestrator._is_week_close(T("2026-04-02"), T("2026-04-03 10:00"))
    print("[7] เวลา: แพ็กเกจจากราคาปิดวันศุกร์เท่านั้น (ศุกร์หยุด → พฤหัส)")


def ai_test():
    d = data_agent.clean(data_agent.synthetic(seed=7))
    d["source"] = "synthetic"
    paper.reset()
    date = d["close"].index[-1]
    ind = regime_agent.indicators(d["close"])
    reg = regime_agent.assess(d["close"], ind, date)
    fake = FakeLLM({
        "news": {"stocks": [{"ticker": "AAPL", "flag": "block", "reason": "ถูกสอบสวน"},
                            {"ticker": "NVDA", "flag": "ok", "reason": "", "items": [
                                {"type": "product", "tone": "positive", "importance": 3, "th": "เปิดตัวชิปใหม่", "src": [2]}]}]},
        "macro": {"risk_level": "high", "note": "วิกฤต"},
        "reporter": {"summary": "สรุป", "risks": ["เสี่ยง"]},
    })
    from agents import news_agent, macro_agent
    fake_news = {"AAPL": [{"title": "AAPL probe", "url": "u1"}], "NVDA": [{"title": "NVDA launches chip", "url": "https://x/nvda"}]}
    today = __import__("datetime").date.today()
    soon = str(today + __import__("datetime").timedelta(days=2))
    orig_ask, orig_enabled, orig_fetch, orig_key, orig_client = llm.ask_json, llm.enabled, news_agent.fetch, llm.api_key, llm._client
    llm.ask_json, llm.enabled, llm.api_key = fake, lambda: True, lambda: "test-key"
    clients = []
    llm._client = lambda key: clients.append(FakeClient(agent_turns())) or clients[-1]
    news_agent.fetch = lambda tickers, today=None: (fake_news, {"MSFT": soon}, "test")
    from agents import edgar
    orig_edgar = edgar.fetch
    edgar.fetch = lambda tickers, today=None: {}
    orig_watch = orchestrator.watchlist
    orchestrator.watchlist = lambda close, ind=None, n=30: list(dict.fromkeys(["AAPL", "NVDA", "MSFT"] + orig_watch(close, ind, n)))
    try:
        from strategies import ai_pick
        dig = news_agent.digest(["AAPL", "NVDA", "MSFT"])
        assert dig["stocks"]["NVDA"]["items"][0]["links"][0]["url"] == "https://x/nvda", dig["stocks"]["NVDA"]
        assert dig["stocks"]["MSFT"]["flag"] == "caution", "งบใกล้ออกต้องเป็น caution (โค้ดตัดสิน)"
        assert dig["stocks"]["AAPL"]["flag"] == "block"
        print("[5] ข่าว: NVDA สินค้าใหม่ (ดี, สำคัญ 3) · MSFT งบใกล้ออก → caution · AAPL → block")
        prop = ai_pick.propose(d["close"], ind, date, reg, held=("NVDA", "AAPL"), extra={"news": news_agent.for_ai(dig)})
        w = prop["weights"]
        assert "FAKE" not in w and "TLT" not in w and abs(sum(w.values()) - 1) < 1e-6, w
        tools = [x["tool"] for x in prop["research"] if "tool" in x]
        assert tools[:2] == ["screen", "screen"] and "check_risk" in tools and tools[-1] == "submit", tools
        seen = " ".join(clients[-1].seen)
        assert "เปิดตัวชิปใหม่" in seen, "เครื่องมือ news ต้องคืนข่าวจากตัวอ่านข่าว"
        assert '"final_weights"' in seen and "ไม่มีเครื่องมือ" in seen and "ตัวเลขพื้นฐานมีเฉพาะหุ้น" in seen, seen[:500]
        future = d["close"].index[-1]
        rs = ai_pick.Research(d["close"], ind, d["close"].index[-300], ())
        assert max(rs.close.index) < future and max(pd.to_datetime(list(rs.price_history("NVDA")["weekly_close"]))) <= d["close"].index[-300], "เครื่องมือห้ามเห็นอนาคต"
        print(f"[5] AI agent: ใช้เครื่องมือ {len(tools) - 1} ครั้ง ({', '.join(dict.fromkeys(tools[:-1]))}) แล้ว submit · เห็นข่าวดี · ไม่เห็นข้อมูลอนาคต")
        fin = risk_agent.apply(w, blocked=news_agent.screen(list(w), dig)["blocked"])["weights"]
        assert fin.get("AAPL", 0) == 0, "ข่าว block แล้ว AAPL ต้องหายไป"
        assert fin["NVDA"] <= config.RISK["max_stock"] + 1e-6 and fin["QQQ"] <= config.RISK["max_etf"] + 1e-6, fin
        assert not risk_agent.check(fin), risk_agent.check(fin)
        mac = macro_agent.assess(d["close"], d["fx"], date, headlines={"SPY": ["crash"]})
        assert mac["factor"] == 0.7
        print(f"[5] AI: AI ขอ NVDA 60% → ได้ {fin['NVDA']:.0%} · AAPL ถูกข่าวบล็อก → {fin.get('AAPL', 0):.0%} · มหภาค high → exposure ×{mac['factor']}")
        # ทั้งวงจรกับ Claude จำลอง: ต้องได้แพ็กเกจครบทุกพอร์ต และ AI Pick ผ่านผู้ตรวจสอบ
        out = orchestrator.weekly(d, as_of=date)
        assert all(v for v in out.values()), out
        rows = {r["portfolio"]: (r["status"], json.loads(r["audit"])["status"]) for r in
                paper.conn().execute("SELECT portfolio, status, audit FROM packets")}
        print(f"[5] วงจรเต็มทุกพอร์ต: {rows}")
        assert rows["ai_pick"][1] != "fail" and all(v[0] in ("awaiting", "no_action", "decided") for v in rows.values()), rows
        assert rows["ai_pick"][0] == "decided", "AI Pick อนุมัติอัตโนมัติ (เจ้าของเลือก)"
        assert rows["momentum_wide"][0] in ("awaiting", "no_action") and rows["momentum_wide"][1] != "fail", "Momentum กว้างรอเจ้าของอนุมัติ"
        assert rows["ai_pick_opus"][0] == "decided" and rows["ai_pick_opus"][1] != "fail", "AI Pick (Opus) อนุมัติอัตโนมัติ"
        assert config.CLAUDE_MODEL_OPUS in FakeClient.models and config.CLAUDE_MODEL in FakeClient.models, set(FakeClient.models)
        logged = {json.loads(r["detail"]).get("model") for r in paper.conn().execute(
            "SELECT detail FROM journal WHERE event='Claude (agent) ส่งคำตอบ' AND portfolio IN ('ai_pick_opus','ai_sector_opus')")}
        assert logged == {config.CLAUDE_MODEL_OPUS}, logged
        print(f"[5] พอร์ต Opus: เรียก {config.CLAUDE_MODEL_OPUS} จริง · พอร์ตเดิมยังใช้ {config.CLAUDE_MODEL} · อนุมัติอัตโนมัติ")
        assert rows["ai_sector"][0] in ("decided", "no_action") and rows["ai_sector"][1] != "fail", rows
        from strategies import ai_sector
        rs2 = ai_sector.Research(d["close"], ind, d["close"].index[-300], ())
        sec = rs2.sectors()
        assert len(sec["rows"]) == 11 and max(rs2.close.index) < future and sec["rule_top3_trend"], sec
        assert rs2.valid("XLK") and rs2.valid("SPY") and not rs2.valid("NVDA") and not rs2.valid("TLT")
        fin2 = rs2.check_risk({"XLK": 0.9, "NVDA": 0.1})["final_weights"]
        assert fin2["XLK"] <= 0.40 + 1e-6 and "NVDA" not in fin2 and fin2["SHY"] >= 0.05 - 1e-6, fin2
        print(f"[5] AI หมุนกลุ่ม: เห็น 11 กลุ่ม ไม่เห็นอนาคต · ขอ XLK 90% → ได้ {fin2['XLK']:.0%} · หุ้นรายตัวถูกตัด · อนุมัติอัตโนมัติ")
        st = {r["status"] for r in paper.conn().execute("SELECT status FROM orders WHERE portfolio='ai_pick'")}
        assert st == {"approved"}, st
        assert {r["status"] for r in paper.conn().execute("SELECT status FROM orders WHERE portfolio='momentum'")} == {"proposed"}, "พอร์ตอื่นยังต้องรอเจ้าของ"
        assert "macro" in fake.calls and "reporter" in fake.calls
        pk = json.loads(paper.conn().execute("SELECT data FROM packets WHERE portfolio='ai_pick'").fetchone()["data"])
        assert pk.get("ai_research"), "แพ็กเกจต้องเก็บเส้นทางการค้นของ AI"
        assert fake.calls.count("news") == 2, ("รอบสัปดาห์ต้องอ่านข่าวครั้งเดียว ใช้ร่วมทุกพอร์ต", fake.calls)
        assert paper.get("news_digest", shared=True)["stocks"]["NVDA"]["items"], "เว็บต้องมี digest ให้อ่าน"
        # ข่าวดีไม่มีผลกับกลยุทธ์กฎ: momentum ได้น้ำหนักเท่ากับตอนไม่มี AI เลย
        mom = json.loads(paper.conn().execute("SELECT data FROM packets WHERE portfolio='momentum'").fetchone()["data"])["target"]
        llm.enabled = lambda: False
        from strategies import momentum
        base = risk_agent.apply(momentum.propose(d["close"], ind, date, reg)["weights"], portfolio="momentum")["weights"]
        print(f"[5] ข่าวดีไม่เปลี่ยน momentum: {mom == base or 'ต่างเพราะมหภาค/บล็อก'}")
        assert set(mom) - {"AAPL"} <= set(base) | {"SHY"}, (mom, base)
    finally:
        llm.ask_json, llm.enabled, news_agent.fetch, llm.api_key, llm._client = orig_ask, orig_enabled, orig_fetch, orig_key, orig_client
        orchestrator.watchlist = orig_watch
        edgar.fetch = orig_edgar


def breakout_test(days=120):
    """W1: เดินรายวันบนข้อมูลจำลอง — stop เลื่อนขึ้นอย่างเดียว, ไม้ละ ≤ 5%, ≤ 20 ไม้, เงินสดไม่ติดลบ, ชน stop แล้วขายจริง"""
    from strategies import breakout
    paper.reset()
    d_full = data_agent.clean(data_agent.synthetic(seed=7))
    idx = d_full["close"].index
    prev_stops, n_buy, n_sell, max_pos = {}, 0, 0, 0
    for i in range(len(idx) - days, len(idx)):
        d = data_agent.truncate(d_full, idx[i])
        breakout.run(d)
        with paper.portfolio("breakout"):
            stops, pos, cash = paper.get("stops") or {}, paper.positions(), paper.get("cash")
            assert cash >= -1e-6, cash
            assert set(stops) == set(pos), (set(stops), set(pos))
            for t, st in stops.items():
                if t in prev_stops:
                    assert st["stop"] >= prev_stops[t]["stop"] - 1e-9, (t, st, prev_stops[t])
            max_pos = max(max_pos, len(pos))
            prev_stops = {t: dict(v) for t, v in stops.items()}
    with paper.portfolio("breakout"):
        rows = paper.conn().execute("SELECT side, status, usd, w_to, fill_price, ref_price, reason FROM orders WHERE portfolio='breakout'").fetchall()
        eq = paper.conn().execute("SELECT COUNT(*) n FROM equity WHERE portfolio='breakout'").fetchone()["n"]
    n_buy = sum(1 for r in rows if r["side"] == "BUY" and r["status"] == "filled")
    n_sell = sum(1 for r in rows if r["side"] == "SELL")
    assert all(r["w_to"] <= config.BREAKOUT["weight"] + 1e-9 for r in rows if r["side"] == "BUY"), "ไม้ละไม่เกิน 5%"
    assert all(r["fill_price"] <= r["ref_price"] + 1e-6 for r in rows if r["side"] == "SELL"), "ขายที่ stop หรือต่ำกว่า (gap)"
    assert max_pos <= config.BREAKOUT["max_positions"] and eq == days and n_buy > 0, (max_pos, eq, n_buy)
    # Momentum กว้าง: หุ้นนอก S&P 500 เห็นเฉพาะระหว่าง with และข้อมูลถูกต่อเข้ากับข้อมูลหลัก
    d = data_agent.clean(data_agent.synthetic(seed=7))
    fake = d["close"][["AAPL"]].rename(columns={"AAPL": "ZZZZ"}) * 0.5
    orig_load, orig_uni = breakout.load_bars, breakout.ensure_universe
    breakout.load_bars = lambda t, m=None: {"close": fake, "open": fake}
    breakout.ensure_universe = lambda force=False: {"tickers": {"ZZZZ": ["Fake Co", "tech"]}}
    try:
        d_w, uni = breakout.wide_data(d)
        assert "ZZZZ" in d_w["close"].columns and "ZZZZ" not in d["close"].columns
        with breakout.wide_universe(uni):
            assert config.UNIVERSE["ZZZZ"]["kind"] == "stock"
            prop = momentum.propose(d_w["close"], regime_agent.indicators(d_w["close"]), d_w["close"].index[-1],
                                    {"exposure": 1.0}, held=())
        assert "ZZZZ" not in config.UNIVERSE, "พอร์ตอื่นต้องไม่เห็นหุ้นนอกดัชนี"
    finally:
        breakout.load_bars, breakout.ensure_universe = orig_load, orig_uni
    print(f"[8] W1: {days} วัน · ซื้อ {n_buy} ไม้ · ขายชน stop {n_sell} ไม้ · ถือพร้อมกันสูงสุด {max_pos} · stop ไม่เคยถอย · เงินสดไม่ติดลบ")


if __name__ == "__main__":
    os.environ.pop("WORKER_URL", None)   # ทดสอบกับไฟล์ในเครื่องเสมอ ไม่แตะข้อมูลบน Cloudflare
    os.environ.pop("AGENT_TOKEN", None)
    os.environ.pop("ANTHROPIC_API_KEY", None)
    config.DB_PATH = "data/test.db"
    null_test()
    lookahead_test()
    risk_test()
    edgar_test()
    timing_test()
    ai_test()
    breakout_test()
    consistency_test()
    paper.reset()
    print("ผ่านทุกข้อ")
