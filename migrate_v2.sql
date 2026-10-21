-- ย้ายจากพอร์ตเดียว → หลายพอร์ต: ข้อมูลเดิมทั้งหมดกลายเป็นพอร์ต 'momentum', ค่าที่ใช้ร่วมกันไป portfolio=''
-- รันครั้งเดียวบน D1 (ทำไปแล้ว 2026-10-21) — เก็บไว้เป็นหลักฐาน
CREATE TABLE state_v2 (portfolio TEXT NOT NULL DEFAULT '', key TEXT, value TEXT, PRIMARY KEY (portfolio, key));
INSERT INTO state_v2 SELECT CASE WHEN key IN ('last_prices','fx','last_data','data_source','universe') THEN '' ELSE 'momentum' END, key, value FROM state;
DROP TABLE state; ALTER TABLE state_v2 RENAME TO state;
CREATE TABLE positions_v2 (portfolio TEXT NOT NULL DEFAULT '', ticker TEXT, shares REAL, cost_usd REAL, PRIMARY KEY (portfolio, ticker));
INSERT INTO positions_v2 SELECT 'momentum', ticker, shares, cost_usd FROM positions;
DROP TABLE positions; ALTER TABLE positions_v2 RENAME TO positions;
CREATE TABLE equity_v2 (portfolio TEXT NOT NULL DEFAULT '', date TEXT, usd REAL, thb REAL, bench_usd REAL, PRIMARY KEY (portfolio, date));
INSERT INTO equity_v2 SELECT 'momentum', date, usd, thb, bench_usd FROM equity;
DROP TABLE equity; ALTER TABLE equity_v2 RENAME TO equity;
CREATE TABLE packets_v2 (id INTEGER PRIMARY KEY AUTOINCREMENT, portfolio TEXT NOT NULL DEFAULT '', created TEXT, signal_date TEXT, status TEXT, data TEXT, audit TEXT, report TEXT, UNIQUE (portfolio, signal_date));
INSERT INTO packets_v2 (id, portfolio, created, signal_date, status, data, audit, report) SELECT id, 'momentum', created, signal_date, status, data, audit, report FROM packets;
DROP TABLE packets; ALTER TABLE packets_v2 RENAME TO packets;
ALTER TABLE orders ADD COLUMN portfolio TEXT NOT NULL DEFAULT ''; UPDATE orders SET portfolio='momentum';
ALTER TABLE journal ADD COLUMN portfolio TEXT NOT NULL DEFAULT ''; UPDATE journal SET portfolio=CASE WHEN agent IN ('data','owner') THEN '' ELSE 'momentum' END;
ALTER TABLE backtests ADD COLUMN portfolio TEXT NOT NULL DEFAULT ''; UPDATE backtests SET portfolio='momentum';
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (portfolio, status);
