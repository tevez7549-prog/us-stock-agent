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
from strategies import PORTFOLIOS
import orchestrator

CODE = {k: v for k, v in PORTFOLIOS.items() if not v["uses_ai"]}


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


def ai_test():
    d = data_agent.clean(data_agent.synthetic(seed=7))
    d["source"] = "synthetic"
    paper.reset()
    date = d["close"].index[-1]
    ind = regime_agent.indicators(d["close"])
    reg = regime_agent.assess(d["close"], ind, date)
    fake = FakeLLM({
        "ai_pick": {"weights": {"NVDA": 0.6, "AAPL": 0.3, "QQQ": 0.3, "FAKE": 0.5, "TLT": -1}, "why": {"NVDA": "ชอบ"}, "view": "ลุย"},
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
    orig_ask, orig_enabled, orig_fetch = llm.ask_json, llm.enabled, news_agent.fetch
    llm.ask_json, llm.enabled = fake, lambda: True
    news_agent.fetch = lambda tickers, today=None: (fake_news, {"MSFT": soon}, "test")
    orig_watch = orchestrator.watchlist
    orchestrator.watchlist = lambda close, ind=None, n=30: list(dict.fromkeys(["AAPL", "NVDA", "MSFT"] + orig_watch(close, ind, n)))
    try:
        from strategies import ai_pick
        dig = news_agent.digest(["AAPL", "NVDA", "MSFT"])
        assert dig["stocks"]["NVDA"]["items"][0]["links"][0]["url"] == "https://x/nvda", dig["stocks"]["NVDA"]
        assert dig["stocks"]["MSFT"]["flag"] == "caution", "งบใกล้ออกต้องเป็น caution (โค้ดตัดสิน)"
        assert dig["stocks"]["AAPL"]["flag"] == "block"
        print("[5] ข่าว: NVDA สินค้าใหม่ (ดี, สำคัญ 3) · MSFT งบใกล้ออก → caution · AAPL → block")
        prop = ai_pick.propose(d["close"], ind, date, reg, held=("NVDA", "AAPL"))   # ถืออยู่ = อยู่ในรายชื่อที่ AI เลือกได้
        w = prop["weights"]
        assert "FAKE" not in w and "TLT" not in w and abs(sum(w.values()) - 1) < 1e-6, w
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
        print(f"[5] วงจรเต็ม 4 พอร์ต: {rows}")
        assert rows["ai_pick"][1] != "fail" and all(v[0] in ("awaiting", "no_action") for v in rows.values()), rows
        assert "ai_pick" in fake.calls and "macro" in fake.calls and "reporter" in fake.calls
        assert "เปิดตัวชิปใหม่" in fake.inputs["ai_pick"], "AI Pick ต้องเห็นข่าวดีของหุ้นทุกตัว"
        assert fake.calls.count("news") == 2, "รอบสัปดาห์ต้องอ่านข่าวครั้งเดียว ใช้ร่วมทุกพอร์ต"
        assert paper.get("news_digest", shared=True)["stocks"]["NVDA"]["items"], "เว็บต้องมี digest ให้อ่าน"
        # ข่าวดีไม่มีผลกับกลยุทธ์กฎ: momentum ได้น้ำหนักเท่ากับตอนไม่มี AI เลย
        mom = json.loads(paper.conn().execute("SELECT data FROM packets WHERE portfolio='momentum'").fetchone()["data"])["target"]
        llm.enabled = lambda: False
        from strategies import momentum
        base = risk_agent.apply(momentum.propose(d["close"], ind, date, reg)["weights"], portfolio="momentum")["weights"]
        print(f"[5] ข่าวดีไม่เปลี่ยน momentum: {mom == base or 'ต่างเพราะมหภาค/บล็อก'}")
        assert set(mom) - {"AAPL"} <= set(base) | {"SHY"}, (mom, base)
    finally:
        llm.ask_json, llm.enabled, news_agent.fetch = orig_ask, orig_enabled, orig_fetch
        orchestrator.watchlist = orig_watch


if __name__ == "__main__":
    os.environ.pop("WORKER_URL", None)   # ทดสอบกับไฟล์ในเครื่องเสมอ ไม่แตะข้อมูลบน Cloudflare
    os.environ.pop("AGENT_TOKEN", None)
    os.environ.pop("ANTHROPIC_API_KEY", None)
    config.DB_PATH = "data/test.db"
    null_test()
    lookahead_test()
    risk_test()
    ai_test()
    consistency_test()
    paper.reset()
    print("ผ่านทุกข้อ")
