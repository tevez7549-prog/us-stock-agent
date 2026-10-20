"""เอเจนต์ 9 — Backtest: ใช้กลยุทธ์ + Risk Manager ตัวเดียวกับของจริงทุกบรรทัด

กันการแอบดูอนาคต:
- สัญญาณคำนวณจากราคาปิด "วันศุกร์" (ข้อมูลถึงวันนั้น)
- เทรดที่ราคาเปิด "วันทำการถัดไป"
- ตัวชี้วัดทั้งหมดเป็น rolling ย้อนหลัง
"""
import numpy as np
import pandas as pd

import config
from agents import regime_agent, signal_agent, risk_agent


def signal_dates(index, start_pos):
    """วันทำการสุดท้ายของแต่ละสัปดาห์"""
    s = pd.Series(index, index=index)
    last = s.groupby(index.to_period("W-FRI")).max()
    return set(d for d in last if index.get_loc(d) >= start_pos and d != index[-1])


def run(d, strategy=None, risk=None, costs=None, capital=config.START_CAPITAL_USD):
    p = {**config.STRATEGY, **(strategy or {})}
    r = {**config.RISK, **(risk or {})}
    c = {**config.COSTS, **(costs or {})}
    close, open_, fx = d["close"], d["open"], d["fx"]
    tickers = list(close.columns)
    col = {t: i for i, t in enumerate(tickers)}
    C, O = close.to_numpy(), open_.to_numpy()
    ind = regime_agent.indicators(close, p)
    warm = max(p["mom_long"], p["trend_sma"]) + 1
    sig_days = signal_dates(close.index, warm)

    cash = capital * (1 - c["fx_spread_pct"])  # แลกบาทเป็นดอลลาร์ครั้งแรก
    sh = np.zeros(len(tickers))
    pending, peak, prev_up = None, None, None
    eq, trades, weights_log = [], [], []
    turnover = 0.0
    fee_rate = c["commission_pct"] + c["slippage_pct"]

    for i, date in enumerate(close.index):
        if i < warm:
            continue
        # 1) เทรดตามสัญญาณเมื่อวาน ที่ราคาเปิดวันนี้
        if pending is not None:
            px = O[i]
            px = np.where(np.isnan(px), C[i - 1], px)
            val = sh * px
            equity = cash + np.nansum(val)
            tgt = np.zeros(len(tickers))
            for t, w in pending.items():
                tgt[col[t]] = w * equity
            cur_w = np.where(equity > 0, val / equity, 0)
            delta = tgt - val
            order = np.argsort(delta)  # ขายก่อน (delta ติดลบ) แล้วค่อยซื้อ
            for j in order:
                if np.isnan(px[j]) or px[j] <= 0:
                    continue
                full_exit = tgt[j] == 0 and sh[j] > 0
                if abs(delta[j]) / equity < r["min_trade_weight"] and not full_exit:
                    continue
                amt = delta[j]
                if amt > 0:
                    amt = min(amt, cash / (1 + fee_rate))
                    if amt <= 1:
                        continue
                fee = abs(amt) * fee_rate
                sh[j] += amt / px[j]
                cash -= amt + fee
                turnover += abs(amt) / equity / 2   # นับแบบด้านเดียว (มาตรฐานกองทุน)
                trades.append({"date": str(date.date()), "ticker": tickers[j],
                               "side": "BUY" if amt > 0 else "SELL", "usd": round(float(abs(amt)), 2),
                               "price": round(float(px[j]), 4), "fee": round(float(fee), 2),
                               "w_from": round(float(cur_w[j]), 4), "w_to": round(float(tgt[j] / equity), 4)})
            pending = None

        # 2) มูลค่าพอร์ตตอนปิด
        equity = cash + np.nansum(sh * np.nan_to_num(C[i]))
        peak = equity if peak is None else max(peak, equity)
        eq.append((date, equity))

        # 3) วันศุกร์ = สร้างสัญญาณ (ใช้ข้อมูลถึงวันนี้)
        if date in sig_days:
            reg = regime_agent.assess(close, ind, date, p, prev_up)
            prev_up = reg["spy_above_sma"]
            held = [tickers[j] for j in np.nonzero(sh > 1e-9)[0]]
            prop = signal_agent.propose(close, ind, date, reg, p, held)
            dd = 1 - equity / peak
            fin = risk_agent.apply(prop["weights"], drawdown=dd, r=r)
            pending = fin["weights"]
            weights_log.append({"date": str(date.date()), "regime": reg["regime"],
                                "exposure": reg["exposure"], "weights": fin["weights"]})

    eq = pd.Series([v for _, v in eq], index=[k for k, _ in eq])
    bench = close[config.BENCHMARK].loc[eq.index]
    bench_eq = capital * (1 - c["fx_spread_pct"]) * bench / bench.iloc[0]
    fx_al = fx.reindex(eq.index).ffill().bfill()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    return {
        "equity": eq, "equity_thb": eq * fx_al, "bench": bench_eq,
        "trades": trades, "weights_log": weights_log,
        "stats": stats(eq, bench_eq, fx_al, capital, turnover / max(years, 1e-9), len(trades)),
        "params": {"strategy": p, "risk": r, "costs": c},
    }


def _block(eq):
    if len(eq) < 30:
        return {}
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    rets = eq.pct_change().dropna()
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else 0
    vol = rets.std() * np.sqrt(252)
    dd = (eq / eq.cummax() - 1).min()
    return {"cagr": round(float(cagr) * 100, 2), "vol": round(float(vol) * 100, 2),
            "sharpe": round(float(cagr / vol), 2) if vol > 0 else None, "max_dd": round(float(dd) * 100, 2)}


def stats(eq, bench, fx, capital, turnover_yr, n_trades):
    cut = eq.index[int(len(eq) * 0.7)]
    yearly = eq.resample("YE").last()
    yearly = pd.concat([pd.Series([eq.iloc[0]], index=[eq.index[0]]), yearly])
    byear = bench.resample("YE").last()
    byear = pd.concat([pd.Series([bench.iloc[0]], index=[bench.index[0]]), byear])
    by_year = [{"year": int(d.year), "ret": round(float(yearly.iloc[k] / yearly.iloc[k - 1] - 1) * 100, 2),
                "bench": round(float(byear.iloc[k] / byear.iloc[k - 1] - 1) * 100, 2)}
               for k, d in enumerate(yearly.index) if k > 0]
    thb = eq * fx
    return {
        "period": f"{eq.index[0]:%Y-%m-%d} → {eq.index[-1]:%Y-%m-%d}",
        "final_usd": round(float(eq.iloc[-1]), 2),
        "strategy": _block(eq), "benchmark": _block(bench), "strategy_thb": _block(thb),
        "in_sample": _block(eq.loc[:cut]), "out_sample": _block(eq.loc[cut:]),
        "bench_out_sample": _block(bench.loc[cut:]),
        "worst_year": min((y["ret"] for y in by_year), default=None),
        "by_year": by_year,
        "turnover_per_year": round(float(turnover_yr) * 100, 1),
        "trades": n_trades,
    }


def robustness(d, grid=None):
    """ปรับพารามิเตอร์ ±~25% แล้วดูว่าผลยังไปทางเดียวกันไหม — ถ้าเปลี่ยนนิดเดียวแล้วพัง = overfit"""
    grid = grid or [{"top_n": n, "trend_sma": s} for n in (4, 6, 8) for s in (150, 200, 250)]
    out = []
    for g in grid:
        st = run(d, strategy=g)["stats"]
        out.append({**g, **st["strategy"], "out_cagr": st["out_sample"].get("cagr")})
    return out
