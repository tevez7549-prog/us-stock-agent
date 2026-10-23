# Research log (us-stock-agent)

## 2026-10-23 — Momentum on S&P 500: edge or survivorship?

Question (owner): the Momentum backtest on the S&P 500 list shows 27%/yr vs SPY 14%. Is that real?

Grid fixed before running (all weekly Friday signal, next-open fills, hold if still eligible, 1/vol weights,
regime, Risk Manager, config.COSTS). Periods: SEARCH 2011–2019, VALIDATE 2020-01..2024-10-22, HOLDOUT last 24 months.
Data: yfinance through 2026-10-22, 503 stocks + ETFs. Scripts: research/random_baseline.py, research/momentum_pit.py.

| Variant | Search | Validate | Holdout | All | Sharpe (all) | Max DD (all) |
|---|---|---|---|---|---|---|
| A SPY buy & hold | 13.2% | 14.7% | 16.3% | 14.0% | 0.82 | -33.7% |
| B Momentum, today's S&P list | 18.1% | 26.0% | 82.5% | 27.3% | 1.21 | -30.1% |
| C1 random 6, all eligible (median of 100) | 10.6% | 6.9% | 8.1% | 9.1% | 0.79 | -20.3% |
| C2 random 6, momentum filter only (median of 100) | 8.7% | 6.4% | 3.0% | 7.2% | 0.62 | -20.4% |
| D Momentum, ETFs only | 4.9% | 5.0% | 7.6% | 5.3% | 0.47 | -20.6% |
| E Momentum, stock eligible only after its S&P "Date added" (added after viewing B–D) | 10.6% | 5.4% | 25.4% | 10.7% | 0.61 | -25.4% |

0 of 200 random seeds beat B in any period → the ranking does matter versus random picks.
But E (removes "bought before it joined the index") falls to 10.7%/yr, below SPY in SEARCH and VALIDATE.
E still has bias from stocks removed from the index (not in today's list), so the true figure is likely lower.

Verdict: FAILS the pass rule. The 27% backtest is mostly survivorship bias. Realistic expectation for Momentum
on large US stocks ≈ around or below SPY, with high turnover (~435%/yr ≈ 1.7%/yr in costs).
ETF-only momentum is clearly worse than SPY.

Caveats: no point-in-time membership for removals; one cost model; the holdout includes the 2025–26 chip rally.
Next: keep the live paper test running (live trading has no survivorship issue); consider showing E instead of B
on the web Backtest tab; possible research: monthly rebalance / wider hold buffer to cut turnover.

## 2026-10-23 (2) — Low volatility and monthly Momentum (all point-in-time by S&P "Date added")

Grid fixed before running, monthly rebalance (last Friday of the month), regime + Risk Manager + costs as live.
Holdout sealed in the run; opened only for variants that passed SEARCH and VALIDATE. Script: research/lowvol_monthly.py.

| Variant | Search CAGR / Sharpe / MaxDD | Validate CAGR / Sharpe / MaxDD | Pass? | Holdout CAGR / Sharpe / MaxDD |
|---|---|---|---|---|
| SPY | 13.2% / 0.92 / -19.4% | 14.7% / 0.69 / -33.7% | – | 16.3% / 0.99 / -18.8% |
| LV1 low-vol 10, above SMA200 | 8.2% / 0.95 / -11.2% | 7.8% / 0.85 / -10.8% | yes | 4.4% / 0.49 / -10.3% |
| LV2 low-vol 20, above SMA200 | 8.1% / 0.90 / -12.8% | 7.6% / 0.78 / -11.7% | no (search Sharpe < SPY) | sealed |
| LV3 low-vol 20, no trend filter | 9.5% / 1.08 / -10.6% | 8.0% / 0.85 / -12.5% | yes | 5.0% / 0.55 / -8.5% |
| MM1 momentum 6, monthly | 13.2% / 0.78 / -23.6% | 12.4% / 0.58 / -20.8% | no | sealed |
| MM2 momentum 12, monthly | 12.7% / 0.76 / -18.7% | 8.1% / 0.44 / -22.1% | no | sealed |

Verdict: nothing here beats SPY on return. Low-vol (a coherent cluster: LV1–LV3 all similar) roughly halves the return
(~8%/yr vs ~14%) with about a third of SPY's drawdown, and matched/beat SPY's Sharpe in 2011–2024 — but in the
holdout (2024-10..2026-10 bull market) it lagged badly (Sharpe ~0.5 vs 0.99), so it fails the holdout test.
Monthly momentum is below SPY risk-adjusted. Turnover still high (185–370%/yr).
SEC EDGAR access confirmed 2026-10-23 (User-Agent with the owner's email) → quality/value tests with point-in-time
fundamentals are now possible.
