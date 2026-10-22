-- ทุกตารางที่เป็น "ของพอร์ต" มีคอลัมน์ portfolio (ชื่อกลยุทธ์: momentum | dual_momentum | risk_parity | ai_pick) · ค่าที่ใช้ร่วมกันใช้ portfolio=''
CREATE TABLE IF NOT EXISTS state (portfolio TEXT NOT NULL DEFAULT '', key TEXT, value TEXT, PRIMARY KEY (portfolio, key));
CREATE TABLE IF NOT EXISTS positions (portfolio TEXT NOT NULL DEFAULT '', ticker TEXT, shares REAL, cost_usd REAL, PRIMARY KEY (portfolio, ticker));
CREATE TABLE IF NOT EXISTS packets (id INTEGER PRIMARY KEY AUTOINCREMENT, portfolio TEXT NOT NULL DEFAULT '', created TEXT, signal_date TEXT, status TEXT, data TEXT, audit TEXT, report TEXT, UNIQUE (portfolio, signal_date));
CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, portfolio TEXT NOT NULL DEFAULT '', packet_id INTEGER, ticker TEXT, side TEXT, usd REAL, ref_price REAL, w_from REAL, w_to REAL, reason TEXT, status TEXT, decided_at TEXT, fill_date TEXT, fill_price REAL, shares REAL, fee REAL, realized_usd REAL, note TEXT);
CREATE TABLE IF NOT EXISTS equity (portfolio TEXT NOT NULL DEFAULT '', date TEXT, usd REAL, thb REAL, bench_usd REAL, PRIMARY KEY (portfolio, date));
CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY AUTOINCREMENT, portfolio TEXT NOT NULL DEFAULT '', time TEXT, agent TEXT, event TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS backtests (id INTEGER PRIMARY KEY AUTOINCREMENT, portfolio TEXT NOT NULL DEFAULT '', created TEXT, source TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS docs (coll TEXT, id TEXT, data TEXT, updated_at TEXT, PRIMARY KEY (coll, id));
CREATE INDEX IF NOT EXISTS idx_orders_packet ON orders (packet_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (portfolio, status);

-- มูลค่าพอร์ตระหว่างวัน (Worker cron ทุก 15 นาทีตอนตลาดเปิด, เก็บ 10 วัน) portfolio='SPY' = ถือ SPY เฉยๆ
CREATE TABLE IF NOT EXISTS intraday (portfolio TEXT NOT NULL, ts TEXT NOT NULL, usd REAL NOT NULL, PRIMARY KEY (portfolio, ts));
