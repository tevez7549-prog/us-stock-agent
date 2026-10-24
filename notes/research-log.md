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

## 2026-10-23 (3) — Quality from SEC annual reports (point-in-time by filing date)

Data: SEC XBRL companyfacts for 500 of 503 S&P members (missing XOM, HONA, VYLR), 10-K annual figures only,
usable from the first filing date; latest report with fiscal year end ≤ 550 days old. Score = mean percentile of
gross profit/assets, ROA, −liabilities/assets. Monthly, point-in-time S&P membership by "Date added". Script: research/quality.py.
Grid fixed before running; Q4 was planned as top 50 but 2% per stock never clears min_trade_weight (2%), so 40 was used.

| Variant | Search CAGR / Sharpe / MaxDD | Validate CAGR / Sharpe / MaxDD | Pass? |
|---|---|---|---|
| SPY | 13.2% / 0.92 / -19.4% | 14.7% / 0.69 / -33.7% | – |
| Q1 quality top 20 | 8.6% / 0.66 / -17.7% | 8.9% / 0.65 / -22.5% | no |
| Q2 quality top 20, above SMA200 | 9.8% / 0.74 / -15.9% | 6.2% / 0.47 / -25.2% | no |
| Q3 top-quartile quality → top 20 momentum | 12.2% / 0.84 / -17.0% | 8.6% / 0.50 / -18.0% | no |
| Q4 quality top 40 | 10.4% / 0.95 / -16.2% | 6.1% / 0.47 / -22.6% | no (validate) |

Verdict: no variant passes; holdout stays sealed. Quality as defined here does not beat SPY on large US stocks
after costs. Caveats: annual data only (slow to update), equal weights, regime cut applies, removed constituents
missing. Overall conclusion of the day: none of momentum, low-vol or quality stock picking beat SPY honestly.
Recommendation given to the owner: SPY core (70–90%) + small satellite for systems still being forward-tested.

## 2026-10-23 (4) — Donchian and EMA 20/50/100/200 + RSI + Stochastic (owner's idea)

Weekly check (signals inside the last 5 trading days count), next-open fills, max 10 stocks equal weight × regime exposure,
lowest-volatility first when more signals than slots, point-in-time S&P membership, Risk Manager + costs.
Only open/close prices are available, so Donchian channels and Stochastic use closes. Script: research/trend_signals.py.
Holdout sealed (no variant passed).

| Variant | Search CAGR / Sharpe / MaxDD | Validate CAGR / Sharpe / MaxDD | Turnover/yr | Pass? |
|---|---|---|---|---|
| SPY | 13.2% / 0.92 / -19.4% | 14.7% / 0.69 / -33.7% | – | – |
| DC1 Donchian 55/20 | 4.5% / 0.49 / -14.7% | 2.1% / 0.22 / -17.8% | 746% | no |
| DC2 Donchian 100/50 | 7.1% / 0.73 / -14.8% | 0.7% / 0.07 / -18.1% | 367% | no |
| DC3 Donchian 20/10 | 3.5% / 0.39 / -15.3% | -1.1% / -0.11 / -18.5% | 1226% | no |
| DC4 Donchian 55/20 on SPY only | -0.7% / -0.11 / -24.0% | 0.9% / 0.13 / -20.3% | 537% | no |
| E1 EMA 20>50>100>200 | 9.2% / 1.02 / -12.3% | 5.5% / 0.52 / -14.7% | 374% | no (validate) |
| E2 + RSI14 < 40 | 8.6% / 0.87 / -12.4% | 2.5% / 0.24 / -19.4% | 554% | no |
| E3 + Stoch %K crosses %D from < 20 | 10.2% / 1.05 / -12.5% | 0.8% / 0.09 / -20.9% | 436% | no |
| E4 + RSI and Stoch | 9.8% / 0.95 / -13.4% | 4.1% / 0.41 / -18.2% | 538% | no |

DC4 looked implausibly low, so it was re-checked with an independent vectorized simulation (research/spy_donchian_check.py):
weekly 55/20 on SPY with 0.2% per side ≈ 2.2%/yr (0.8% in 2011–2019), daily ≈ 3.3%/yr, no-cost weekly ≈ 3.6%/yr,
vs SPY 14%/yr — consistent: in 2011–2026 SPY's many 20-day-low pullbacks forced exits and re-entries at higher
55-day highs (whipsaw). Verdict: none pass; EMA-stack variants looked good in 2011–2019 (Sharpe ≈ 1.0, DD −12%)
but broke down in 2020–2024. Adding RSI/Stoch timing did not help versus the EMA stack alone.

## 2026-10-23 (5) By-year / Covid impact and sector-only systems (research/by_year.py, research/sectors.py)
By year (PIT, net of costs): 2020 SPY +18.3% vs Mom +3.0, LV3 +7.1, Q3 +18.3, E1 −3.9, E3 −9.7;
2022 SPY −18.2% vs LV3 −0.2, Q3 −15.3, E1 −6.1, Mom −4.7. Ex-Covid 2021-01..2024-10 CAGR/Sharpe/DD:
Mom 6.2/0.37/−24.9, LV3 8.3/0.95/−11.2, Q3 7.3/0.43/−17.6, E1 8.3/0.87/−14.4, E3 3.9/0.47/−20.7, SPY 14.4/0.87/−24.5.
→ Covid hurt trend systems, but the bigger gap is strong up-years (2019, 2023). None beat SPY even ex-2020.
Sectors (11 GICS groups × MOM and EMA, vs SPY and equal-weight of the group): none pass. In every group the
equal-weight hold of the group beat the stock-picking rule (e.g. tech EW 21.1%/yr). Staples EMA passed validate
but failed search. Holdouts stay sealed (not reported).

## 2026-10-23 (6) Classic indicators — owner's list (research/classic_indicators.py), grid fixed before running
BB1 pullback below lower band (20,2) above SMA200 → exit above mid; BB2 close above upper band → exit below mid;
SAR Parabolic SAR (0.02/0.2) flip up → exit on flip down; ICH close above cloud + Tenkan>Kijun → exit below Kijun;
FIB above SMA200 and inside 50–61.8% retracement of the 60-day swing → exit above prior high or below 78.6%.
Weekly check, max 10 equal-weight × regime exposure, low-vol first, PIT, same Risk Manager/costs. High/low from Yahoo.
Result (search 2011–19 / validate 2020–24, CAGR/Sharpe/DD; SPY 13.2/0.92/−19.4 and 14.7/0.69/−33.7):
BB1 2.1/0.23/−19.5 · 0.7/0.08/−16.3; BB2 4.2/0.44/−16.5 · 5.0/0.50/−18.8; SAR 2.3/0.26/−17.0 · −1.2/−0.13/−18.4;
ICH 8.0/0.86/−13.3 · 4.6/0.46/−16.8; FIB 8.4/0.85/−11.7 · 3.5/0.32/−18.5. Turnover 850–1500%/yr (one-sided).
Verdict: none pass (all fail validate Sharpe; ICH/FIB were close in search only). Holdouts stay sealed.

## 2026-10-24 (7) Sector ETF rotation — owner's request (research/sector_etfs.py), grid fixed before running
Families A = Select Sector SPDR (S&P 500 only), B = Vanguard sector (total market), C = mix (per sector the
stronger of the A/B pair). Rules EW (all sectors equal), TOP3 (score = mean of 3/6/12-month returns), TOP3T
(TOP3, sector below its 200-day SMA → SHY). Rebalance every 1/3/6 months, signal at period-end close, trade next
open, config.COSTS per side, ETFs only after they exist (XLRE 2015, XLC 2018). 27 variants. AI not backtested
(it knows which sectors won).
Result (search 2011–19 / validate 2020-01..2024-10, CAGR/Sharpe/DD; SPY 13.1/0.92/−19.4 · 14.7/0.69/−33.7):
EW: A-1m 11.8/0.87/−18.5 · 12.6/0.61/−36.1; B-1m 11.1/0.81/−19.9 · 12.4/0.58/−37.4; C-6m 10.4/0.79/−19.6 · 12.6/0.60/−36.5
TOP3: A-1m 7.9/0.57/−19.8 · 15.1/0.70/−31.9; B-3m 6.4/0.44/−22.9 · 18.2/0.80/−34.9 (best validate CAGR)
TOP3T: A-1m 5.7/0.42/−19.8 · 11.9/0.71/−20.9; B-1m 6.3/0.45/−23.6 · 15.0/0.86/−18.5; C-1m 5.6/0.40/−23.8 · 15.5/0.90/−17.1
3m/6m rebalancing was not better than 1m for TOP3T. Verdict: none pass — every rotation failed search (Sharpe ≤ 0.57
vs 0.92, 2011–2016 lagged badly); TOP3T-1m looked strong 2020–24 only (2022 +6% vs SPY −18%). A vs B vs mix: no real
difference. Holdouts stay sealed. (First run had a trailing-NaN bug that only affected the last days; fixed and rerun,
search/validate identical.)

## 2026-10-24 (8) Growth stocks in wider markets — owner's request (research/growth_wide.py), grid fixed before running
U1 = current S&P 500 + S&P 400 members (no S&P 400 history → survivorship; judged against random picks in the same
universe). U2 = NYSE/Nasdaq US-GAAP filers with point-in-time market cap ≥ $10B (SEC cover-page shares, frames,
usable 7 days after the as-of date, × split-adjusted price without dividend adjustment; pre-filter: total assets
≥ $2B at some year-end; multi-class firms missing from frames count only while in the S&P 500; only firms listed
today → survivorship). Signals from SEC quarterly revenue/net income, usable from first filed date, Q4 = annual − 3
quarters; g = YoY growth of the latest quarter (≤ 200 days old). G1 g0 > 10% and g0 > g1 > g2 → top 20 by g0;
G2 = G1 + above SMA200 + net income up YoY → top 20 by g0; G3 = G2 filter → top 20 by 6/12m momentum. Baselines EW
(all eligible, min trade weight 0) and R (random 20, 20 seeds). Monthly, next open, equal weight × regime exposure,
same Risk Manager/costs. Pass = Sharpe ≥ SPY and clearly smaller DD in search and validate, and CAGR above the
random p90 in both. Holdout sealed.
Result (search 2011–19 / validate 2020-01..2024-10, CAGR/Sharpe/DD; SPY 13.2/0.92/−19.4 · 14.7/0.69/−33.7):
U1 (903 names): EW 11.3/0.91/−19.7 · 19.2/1.03/−16.1; G1 17.6/1.13/−20.3 · 20.9/1.13/−18.1; G2 13.5/0.89/−19.4 ·
17.4/1.02/−16.2; G3 14.9/0.93/−19.4 · 20.0/1.02/−17.9; random-20 median 5.0 · 6.4 (p90 6.6 · 12.0).
U2 (254 → 670 names over time): EW 9.3/0.80/−17.5 · 7.8/0.61/−16.7; G1 11.4/0.85/−17.7 · 10.0/0.60/−21.9;
G2 11.1/0.95/−13.0 · 15.5/0.96/−15.8; G3 10.6/0.90/−12.8 · 11.2/0.66/−17.6; random median 3.7 · 3.8 (p90 5.6 · 7.7).
U1-G1 and U1-G3 failed only on DD in search (not smaller than SPY). U2-G2 passed every pre-registered test → holdout
opened per the rule: U2-G2 holdout (2024-10-23..2026-10-23) 1.0%/0.04/−25.2% vs SPY 16.0/0.97/−18.8, EW U2 6.5%,
random median 2.2% → FAILS the holdout. Verdict: no variant passes. Caveat: random-20 re-draws every month
(much higher turnover/costs than G*), so the random gap is overstated by roughly 3%/yr; verdict unchanged.
Growth signals clearly beat random and EW in 2011–2024 but not in the last two years (mega-cap led market).

## 2026-10-24 (9) forex-agent G1 rules on S&P 500 stocks, daily, long-only (research/trade_stops.py), grid fixed before running
Owner's forex doc: Donchian 55 (H4) + D1 EMA200 filter, SL 3 ATR, trail 5 ATR, 1% risk per trade, no TP; the doc itself
reports G1 failed on 11 stock indices. New per-trade simulator (daily bars, stops checked against Low, gap through stop
exits at Open, entries/exits at next open, ≤ 20 open positions, size = 1% equity ÷ 3 ATR, PIT S&P 500, SPY > SMA200
market filter). S1 N55 trail5 · S2 N20 trail5 · S3 N55 trail6 · S4 N55 fixed SL + TP at prior 250-day high (else 2R)
(owner prefers TP at resistance; the doc says the trailing exit gave half the edge — the grid lets the data decide) ·
R random entries, same exit/sizing, 20 seeds (the doc's method to split entry edge from exit edge). Pass = Sharpe ≥ SPY
and clearly smaller DD in search and validate, beats random ≥ 95%. Holdout sealed.
Result (search 2011–19 / validate 2020-01..2024-10, CAGR/Sharpe/DD; SPY 13.1/0.92/−19.4 · 14.7/0.69/−33.7):
S1 7.3/0.54/−26.2 · 11.7/0.75/−18.2 (PF 1.72/1.67, 62 trades/yr, win 38%, avg +0.40R, worst −2.3R gap);
S2 8.2/0.60/−23.5 · 6.3/0.40/−22.4; S3 10.2/0.74/−25.2 · 14.2/0.84/−18.9 (PF 1.87/1.87, avg +0.71R);
S4 (TP at resistance) 8.7/0.67/−26.4 · 13.5/0.79/−21.0 (PF 1.51/1.66, 96 trades/yr, win 48%, avg +0.30R).
Random entries, same exit/sizing: CAGR median 7.9 · 7.8, p95 11.5 · 10.8; PF median 1.76 ≈ the Donchian variants.
Verdict: none pass. Search Sharpe 0.54–0.74 < SPY 0.92 and DD deeper (−23 to −26% vs −19%). Validate: S3/S4
Sharpe > SPY with half the DD, but CAGR still below SPY. Key finding (same as the forex doc): the exit/sizing rules
carry the edge — random entries reach PF 1.76 and 7.9%/yr; Donchian entries add little (S1 below random median
in search). TP-at-resistance (S4) had higher win rate but lower avg R than trailing (S3), roughly equal Sharpe.
Positive PF every period (1.5–2.5) means the rule makes money in R terms but, long-only with ≤20 × 1% risk, it stays
under-invested versus buy-and-hold in strong up-years (2014, 2024). Holdouts stay sealed.
Round 2 (owner 2026-10-24: drop the 1% sizing, size like the portfolios): W1 = S3 rules, 5% of equity per position,
≤ 20 (fully invested possible); W2 = S4 rules, 5% × 20; W3 = S3 rules, 10% × 10. Random entries re-run with 5%
sizing + trail 6 as the baseline. Same pass rule. Results in D1 'research_trade_stops_w'.
