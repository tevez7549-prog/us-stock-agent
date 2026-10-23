"""ตรวจ DC4 ด้วยวิธีที่สอง (vectorized ง่ายๆ ไม่ผ่าน backtest_agent): Donchian 55/20 บน SPY
เช็ครายสัปดาห์ (ศุกร์) และรายวัน, เทรดราคาเปิดวันถัดไป, ไม่มีค่าธรรมเนียม/มีค่าธรรมเนียม 0.2% ต่อข้าง — ผล: D1 'research_spy_donchian_check'"""
import json

import numpy as np
import pandas as pd

import paper
from agents import backtest_agent, data_agent


def sim(close, open_, weekly, cost):
    c, o = close["SPY"], open_["SPY"]
    cash_r = (close["SHY"] / close["SHY"].shift(1)).fillna(1)
    hi = (c >= c.rolling(55).max()).astype(int)
    lo = (c <= c.rolling(20).min()).astype(int)
    if weekly:
        hi, lo = hi.rolling(5, min_periods=1).max(), lo.rolling(5, min_periods=1).max()
        fri = c.index.to_series().groupby(c.index.to_period("W-FRI")).transform("max") == c.index.to_series()
    else:
        fri = pd.Series(True, index=c.index)
    state, pos, eq, trades = 0, [], 1.0, 0
    curve = []
    prev_state = 0
    for i, d in enumerate(c.index):
        if i > 0:
            if prev_state:   # ถือ SPY: วันที่เข้าใหม่ได้ผลตอบแทนจากราคาเปิด
                r = c.iloc[i] / (o.iloc[i] if entered_today else c.iloc[i - 1])
            else:
                r = cash_r.iloc[i]
            eq *= r
        entered_today = False
        curve.append(eq)
        if fri.iloc[i] and i + 1 < len(c):
            new = 1 if (not state and hi.iloc[i]) else 0 if (state and lo.iloc[i]) else state
            if new != state:
                trades += 1
                eq *= (1 - cost)
                state = new
        # สถานะใหม่มีผลตั้งแต่ราคาเปิดวันถัดไป
        if state != prev_state:
            entered_today = state == 1
        prev_state = state
    return pd.Series(curve, index=c.index), trades


if __name__ == "__main__":
    d, src = data_agent.load("yfinance", allow_synthetic=False)
    out = {"created": paper.now_th()}
    for name, weekly, cost in (("weekly_nocost", True, 0), ("weekly_cost", True, .002), ("daily_cost", False, .002)):
        eq, n = sim(d["close"], d["open"], weekly, cost)
        eq = eq.loc["2011-01-01":]
        out[name] = {"trades": n, "all": backtest_agent._block(eq), "search": backtest_agent._block(eq.loc[:"2019-12-31"]),
                     "validate": backtest_agent._block(eq.loc["2020-01-01":"2024-10-22"])}
    out["spy_bh"] = backtest_agent._block(d["close"]["SPY"].loc["2011-01-01":])
    print(json.dumps(out))
    paper.put("research_spy_donchian_check", out, shared=True)
