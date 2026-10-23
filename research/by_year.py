"""ช่วงโควิดมีผลแค่ไหน: ผลรายปี + ปี 2020 + ช่วง 2021-01..2024-10 (ตัดปี 2020) ของแบบหลักๆ — ผล: D1 'research_by_year'"""
import csv
import io
import json

import config
import paper
from agents import backtest_agent, data_agent
from strategies import momentum
from research.lowvol_monthly import PIT, ADDED, LowVol
from research import trend_signals as ts, quality as q


def main():
    for r in csv.DictReader(io.StringIO(open(config.SP500_FILE, encoding="utf-8").read())):
        ADDED[config.yf_symbol(r["Symbol"])] = (r.get("Date added") or "")[:10]
    d, src = data_agent.load("yfinance", allow_synthetic=False)
    ts.prepare(d["close"])
    for t, m in config.UNIVERSE.items():
        if m["kind"] == "stock" and m.get("cik"):
            rows = q.company_records(m["cik"])
            if rows:
                q.FUND[t] = q.pd.DataFrame(rows)
    grid = {"Momentum": (PIT(momentum), "weekly"), "LV3": (PIT(LowVol(20, False)), "monthly"),
            "Q3": (PIT(q.Quality(20, "qm")), "monthly"), "E1": (PIT(ts.Signals("e1")), "weekly"), "E3": (PIT(ts.Signals("e3")), "weekly")}
    out = {"created": paper.now_th(), "variants": {}}
    for name, (mod, cad) in grid.items():
        res = backtest_agent.run(d, strategy_module=mod, cadence=cad, portfolio="momentum")
        eq, bench = res["equity"], res["bench"]
        out["variants"][name] = {"by_year": {y["year"]: y["ret"] for y in res["stats"]["by_year"]},
                                 "y2020": backtest_agent._block(eq.loc["2020-01-01":"2020-12-31"]),
                                 "ex_covid_2021_2024": backtest_agent._block(eq.loc["2021-01-01":"2024-10-22"])}
        out["spy"] = {"by_year": {y["year"]: y["bench"] for y in res["stats"]["by_year"]},
                      "y2020": backtest_agent._block(bench.loc["2020-01-01":"2020-12-31"]),
                      "ex_covid_2021_2024": backtest_agent._block(bench.loc["2021-01-01":"2024-10-22"])}
        print(name, json.dumps(out["variants"][name]["ex_covid_2021_2024"]), flush=True)
    paper.put("research_by_year", out, shared=True)


if __name__ == "__main__":
    main()
