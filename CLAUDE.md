# us-stock-agent

Multi-agent paper-trading (forward test) system for US stocks/ETFs the owner would buy via Dime (Thai UI). Owner approves every order. Python agents + Cloudflare Worker + static assets + D1.

- Deploy: push to `main` → Cloudflare Workers Builds runs `npx wrangler deploy` (web only). Python agents run on GitHub Actions (`.github/workflows/agents.yml`): daily Tue–Sat 06:30 TH, weekly Sat 09:00 TH (+ backtest).
- D1 `us-stock-agent` id `34da2387-758f-4ebd-b51e-a4717151be72`. Tables: state(key,value JSON), positions, packets, orders, equity, journal, backtests, docs(coll,id,data) for secrets only.
- Agents write through POST /agent/sql (SQLite statements, D1 batch). Auth: GitHub Actions OIDC token (iss token.actions.githubusercontent.com, aud `us-stock-agent`, repository = env.AGENT_REPO, ref refs/heads/main) — no GitHub secrets needed; or Bearer token whose SHA-256 (base64url) = docs secrets/agent.hash (owner's PC backup, token in local `.env`). /agent/sql refuses any statement touching `docs`.
- `paper.py` switches backend: WORKER_URL + (AGENT_TOKEN or ACTIONS_ID_TOKEN_REQUEST_URL) → D1 via Worker; otherwise local SQLite data/invest.db. Same SQL both ways (cloud and local runs verified identical).
- In the cloud the data agent never falls back to synthetic data; a failed yfinance download stops the run.
- Web login: env.APP_PASSWORD if set, else PBKDF2 hash in docs secrets/auth (changeable on the settings tab). Finnhub key (live quotes) is entered on the settings tab, stored in docs secrets/finnhub, never sent to the browser.
- Web can only: read, approve/reject orders (not for packets whose auditor failed), kill switch/resume, settings. Risk limits live in config.py only.
- Agents: orchestrator(1) data(2) regime(3) signal(4) news(5, Claude, off) risk(7) backtest(9) auditor(10) reporter(11). Weekly signal on Friday close, fills at next open after approval; approvals expire after 3 business days; >3% price move holds the order.
- Tests: `python -m tests.test_system` (null test on random data, lookahead, risk limits, forward-vs-backtest consistency). Local web: start.bat → server.py (localhost:8000, has simulate + editable backtest).
- Owner's local copy: D:\AI\Invest Agent\US Stock (keep in sync).
