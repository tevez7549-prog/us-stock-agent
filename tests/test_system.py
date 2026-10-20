"""ทดสอบความถูกต้องของระบบ — รันด้วย: python -m tests.test_system

1. Null test: ข้อมูลสุ่มไม่มีทิศทาง → กลยุทธ์ต้องไม่ทำกำไรเกินเงินสดอย่างมีนัย (ถ้าทำได้ = มีข้อมูลอนาคตรั่ว)
2. Lookahead test: แก้ราคาอนาคตแล้ว สัญญาณในอดีตต้องไม่เปลี่ยน
3. Consistency test: forward test (orchestrator) กับ backtest ช่วงเดียวกันต้องได้ผลใกล้กัน (สองโค้ดคนละชุด)
4. Risk test: พอร์ตเป้าหมายทุกสัปดาห์ต้องผ่านกฎความเสี่ยง
"""
import numpy as np
import pandas as pd

import config
import paper
from agents import data_agent, backtest_agent, regime_agent, signal_agent, risk_agent
import orchestrator


def null_test():
    res = []
    for seed in range(5):
        d = data_agent.clean(data_agent.synthetic(seed=seed, drift=False))
        res.append(backtest_agent.run(d)["stats"]["strategy"]["cagr"])
    avg = float(np.mean(res))
    print(f"[1] Null test CAGR บนข้อมูลสุ่ม 5 ชุด: {res} เฉลี่ย {avg:.2f}%")
    assert avg < 1.0, "ข้อมูลสุ่มไม่ควรทำกำไรได้ — สงสัยข้อมูลอนาคตรั่ว"


def lookahead_test():
    d = data_agent.clean(data_agent.synthetic(seed=3))
    idx = d["close"].index
    t = idx[int(len(idx) * 0.6)]
    ind = regime_agent.indicators(d["close"])
    reg = regime_agent.assess(d["close"], ind, t)
    a = signal_agent.propose(d["close"], ind, t, reg)["weights"]
    d2 = {k: v.copy() for k, v in d.items()}
    d2["close"].loc[d2["close"].index > t] *= 3.0   # ทำลายอนาคต
    ind2 = regime_agent.indicators(d2["close"])
    b = signal_agent.propose(d2["close"], ind2, t, regime_agent.assess(d2["close"], ind2, t))["weights"]
    print(f"[2] Lookahead: สัญญาณวันที่ {t.date()} ก่อน/หลังแก้ราคาอนาคต เหมือนกัน = {a == b}")
    assert a == b


def consistency_test(weeks=52):
    d = data_agent.clean(data_agent.synthetic(seed=11))
    d["source"] = "synthetic"
    orchestrator.simulate(d, weeks=weeks)
    with paper.conn() as c:
        fw = pd.Series({pd.Timestamp(r["date"]): r["usd"] for r in c.execute("SELECT date, usd FROM equity ORDER BY date")})
    idx = d["close"].index
    start = len(idx) - weeks * 5
    warm = max(config.STRATEGY["mom_long"], config.STRATEGY["trend_sma"]) + 1
    sub = {k: v.iloc[start - warm:] for k, v in d.items() if k != "source"}
    bt = backtest_agent.run(sub)["equity"].reindex(fw.index)
    fr, br = fw.iloc[-1] / fw.iloc[0] - 1, bt.iloc[-1] / bt.iloc[0] - 1
    print(f"[3] Forward test {fr:+.2%} vs Backtest {br:+.2%} ช่วงเดียวกัน (ต่าง {abs(fr - br):.2%})")
    assert abs(fr - br) < 0.02, "forward test กับ backtest ต่างกันเกิน 2% — มีบั๊กฝั่งใดฝั่งหนึ่ง"


def risk_test():
    d = data_agent.clean(data_agent.synthetic(seed=5))
    log = backtest_agent.run(d)["weights_log"]
    bad = [(w["date"], risk_agent.check(w["weights"])) for w in log if risk_agent.check(w["weights"])]
    print(f"[4] Risk: ตรวจพอร์ตเป้าหมาย {len(log)} สัปดาห์ ผิดกฎ {len(bad)} ครั้ง")
    assert not bad, bad[:3]


if __name__ == "__main__":
    import os
    os.environ.pop("WORKER_URL", None)   # ทดสอบกับไฟล์ในเครื่องเสมอ ไม่แตะข้อมูลบน Cloudflare
    os.environ.pop("AGENT_TOKEN", None)
    config.DB_PATH = "data/test.db"
    null_test()
    lookahead_test()
    risk_test()
    consistency_test()
    paper.reset()
    print("ผ่านทุกข้อ")
