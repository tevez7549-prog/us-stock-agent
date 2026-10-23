"""งานวิจัย 2026-10-23 (ต่อจาก random_baseline): Momentum ที่ซื้อหุ้นได้เฉพาะหลังวันที่เข้า S&P 500 จริง
(ตัด bias "ซื้อก่อนได้เข้าดัชนี" — ยังเหลือ bias จากหุ้นที่ถูกถอดออก เพราะไม่มีรายชื่อในอดีต)
ผลเก็บใน D1 shared state 'research_momentum_pit'"""
import csv
import io
import json

import config
import paper
from agents import backtest_agent, data_agent
from strategies import momentum
from research.random_baseline import periods

ADDED = {}


class MomentumPIT:
    """momentum.propose เดิมทุกอย่าง แต่หุ้นที่ยังไม่เข้าดัชนี ณ วันนั้นถูกเอาออกจากรายชื่อชั่วคราว"""

    def propose(self, close, ind, date, regime, p=config.STRATEGY, held=()):
        d = str(date.date())
        hide = [t for t, m in config.UNIVERSE.items() if m["kind"] == "stock" and ADDED.get(t, "") > d]
        saved = {t: config.UNIVERSE.pop(t) for t in hide}
        try:
            return momentum.propose(close, ind, date, regime, p, held)
        finally:
            config.UNIVERSE.update(saved)


def main():
    text = open(config.SP500_FILE, encoding="utf-8").read()
    for r in csv.DictReader(io.StringIO(text)):
        ADDED[config.yf_symbol(r["Symbol"])] = (r.get("Date added") or "")[:10]
    d, src = data_agent.load("yfinance", allow_synthetic=False) if paper.remote() else (data_agent.clean(data_agent.synthetic(seed=5)), "synthetic")
    res = backtest_agent.run(d, strategy_module=MomentumPIT(), cadence="weekly", portfolio="momentum")
    picks = {}
    for x in res["weights_log"]:
        for t in x["weights"]:
            if config.info(t)["kind"] == "stock":
                picks[t] = picks.get(t, 0) + 1
    out = {"created": paper.now_th(), "data_last": str(d["close"].index[-1].date()), "source": src,
           "E_momentum_pit": {"turnover": res["stats"]["turnover_per_year"], **periods(res["equity"])},
           "most_held": sorted(picks.items(), key=lambda kv: -kv[1])[:15],
           "added_last_2y": sum(1 for v in ADDED.values() if v >= "2024-10-23")}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    paper.put("research_momentum_pit", out, shared=True)


if __name__ == "__main__":
    main()
