"""ฐานข้อมูลพอร์ตจำลอง (forward test) + Journal

มี 2 โหมด เลือกอัตโนมัติจาก environment:
- WORKER_URL + (AGENT_TOKEN หรือรันใน GitHub Actions ที่เปิด id-token) → ใช้ D1 บน Cloudflare ผ่าน Worker /agent/sql
  บน GitHub Actions ใช้ OIDC token ของ GitHub (ไม่ต้องมี secret) / บนคอมใช้ AGENT_TOKEN จากไฟล์ .env
- ไม่ได้ตั้ง                         → ใช้ SQLite ไฟล์ data/invest.db บนเครื่อง     ← ใช้ทดสอบ/simulate
SQL ทั้งหมดเป็น SQLite ใช้ได้ทั้งสองที่โดยไม่ต้องแก้
"""
import json
import os
import sqlite3
import urllib.request
from datetime import datetime, timedelta, timezone

import config

# ทุกตารางที่เป็น "ของพอร์ต" มีคอลัมน์ portfolio (ชื่อกลยุทธ์) · state ที่ไม่ขึ้นกับพอร์ตใช้ portfolio=''
SCHEMA = """
CREATE TABLE IF NOT EXISTS state (portfolio TEXT NOT NULL DEFAULT '', key TEXT, value TEXT, PRIMARY KEY (portfolio, key));
CREATE TABLE IF NOT EXISTS positions (portfolio TEXT NOT NULL DEFAULT '', ticker TEXT, shares REAL, cost_usd REAL, PRIMARY KEY (portfolio, ticker));
CREATE TABLE IF NOT EXISTS packets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, portfolio TEXT NOT NULL DEFAULT '', created TEXT, signal_date TEXT, status TEXT,
  data TEXT, audit TEXT, report TEXT, UNIQUE (portfolio, signal_date));
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, portfolio TEXT NOT NULL DEFAULT '', packet_id INTEGER, ticker TEXT, side TEXT, usd REAL,
  ref_price REAL, w_from REAL, w_to REAL, reason TEXT, status TEXT, decided_at TEXT,
  fill_date TEXT, fill_price REAL, shares REAL, fee REAL, realized_usd REAL, note TEXT);
CREATE TABLE IF NOT EXISTS equity (portfolio TEXT NOT NULL DEFAULT '', date TEXT, usd REAL, thb REAL, bench_usd REAL, PRIMARY KEY (portfolio, date));
CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY AUTOINCREMENT, portfolio TEXT NOT NULL DEFAULT '', time TEXT, agent TEXT, event TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS backtests (id INTEGER PRIMARY KEY AUTOINCREMENT, portfolio TEXT NOT NULL DEFAULT '', created TEXT, source TEXT, data TEXT);
"""

# พอร์ตที่กำลังทำงาน — orchestrator ตั้งค่าก่อนเรียกฟังก์ชันใดๆ ของพอร์ตนั้น
CURRENT = ""


class portfolio:
    """with paper.portfolio("momentum"): ... → ทุก get/put/positions/log ในบล็อกนี้เป็นของพอร์ตนั้น"""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        global CURRENT
        self.prev, CURRENT = CURRENT, self.name
        return self

    def __exit__(self, *a):
        global CURRENT
        CURRENT = self.prev
        return False

TH = timezone(timedelta(hours=7))


def now_th():
    return datetime.now(TH).replace(tzinfo=None).isoformat(timespec="seconds")


# ---------------------------------------------------------------- D1 ผ่าน Worker
class _Cursor:
    def __init__(self, res):
        self._rows = res.get("results", [])
        self.rowcount = res.get("changes", 0)
        self.lastrowid = res.get("last_row_id", 0)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


OIDC_AUDIENCE = "us-stock-agent"


class RemoteConn:
    """หน้าตาเหมือน sqlite3.Connection เท่าที่ระบบใช้: execute / executemany / with-block"""

    def __init__(self, url, token=None):
        self.url = url.rstrip("/") + "/agent/sql"
        self.static_token = token
        self._oidc, self._oidc_at = None, 0.0

    def _token(self):
        if self.static_token:
            return self.static_token
        import time
        if self._oidc is None or time.time() - self._oidc_at > 240:   # token ของ GitHub อายุสั้น ขอใหม่ทุก 4 นาที
            req = urllib.request.Request(
                os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + f"&audience={OIDC_AUDIENCE}",
                headers={"authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})
            with urllib.request.urlopen(req, timeout=30) as r:
                self._oidc = json.loads(r.read())["value"]
            self._oidc_at = time.time()
        return self._oidc

    def _send(self, statements):
        req = urllib.request.Request(
            self.url, data=json.dumps({"statements": statements}, default=float).encode(),
            headers={"content-type": "application/json", "authorization": f"Bearer {self._token()}",
                     "user-agent": "us-stock-agent/1"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    @staticmethod
    def _clean(params):
        out = []
        for p in params or ():
            if hasattr(p, "item"):      # numpy → python
                p = p.item()
            out.append(p)
        return out

    def execute(self, sql, params=()):
        return _Cursor(self._send([{"sql": sql, "params": self._clean(params)}])[0])

    def executemany(self, sql, seq):
        rows = [{"sql": sql, "params": self._clean(p)} for p in seq]
        for i in range(0, len(rows), 100):
            self._send(rows[i:i + 100])

    def executescript(self, script):
        stmts = [s.strip() for s in script.split(";") if s.strip()]
        self._send([{"sql": s, "params": []} for s in stmts])

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def remote():
    return bool(os.environ.get("WORKER_URL") and (os.environ.get("AGENT_TOKEN") or os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")))


_CONN = None


def conn():
    """การเชื่อมต่อร่วมตัวเดียว (with conn() as c: = commit อัตโนมัติเมื่อจบบล็อกในโหมด SQLite)"""
    global _CONN
    if _CONN is None:
        if remote():
            _CONN = RemoteConn(os.environ["WORKER_URL"], os.environ.get("AGENT_TOKEN"))
        else:
            os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
            _CONN = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=30)
            _CONN.row_factory = sqlite3.Row
            _CONN.executescript(SCHEMA)
    return _CONN


def get(key, default=None, shared=False):
    r = conn().execute("SELECT value FROM state WHERE portfolio=? AND key=?", ("" if shared else CURRENT, key)).fetchone()
    return json.loads(r["value"]) if r else default


def put(key, value, shared=False):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO state (portfolio, key, value) VALUES (?,?,?)",
                  ("" if shared else CURRENT, key, json.dumps(value, default=float)))


def log(agent, event, detail=None, time=None, shared=False):
    with conn() as c:
        c.execute("INSERT INTO journal (portfolio, time, agent, event, detail) VALUES (?,?,?,?,?)",
                  ("" if shared else CURRENT, time or now_th(), agent, event,
                   json.dumps(detail, ensure_ascii=False, default=str) if detail is not None else None))


def positions():
    return {r["ticker"]: {"shares": r["shares"], "cost_usd": r["cost_usd"]}
            for r in conn().execute("SELECT * FROM positions WHERE portfolio=? AND shares > 1e-9", (CURRENT,))}


def init(capital=config.START_CAPITAL_USD):
    """เริ่มพอร์ตปัจจุบันถ้ายังไม่มี"""
    assert CURRENT, "ต้องอยู่ใน with paper.portfolio(...)"
    if get("cash") is None:
        put("cash", capital * (1 - config.COSTS["fx_spread_pct"]))
        put("capital", capital)
        put("paused", False)
        log("system", "เริ่มพอร์ตจำลอง", {"capital_usd": capital})
    if get("universe", shared=True) is None:
        put("universe", {t: m["kind"] for t, m in config.UNIVERSE.items()}, shared=True)


def reset():
    """ล้างพอร์ตจำลอง — ใช้ได้เฉพาะ SQLite บนเครื่อง (กันลบข้อมูลจริงบน Cloudflare โดยไม่ตั้งใจ)"""
    global _CONN
    if remote():
        raise RuntimeError("reset() ใช้กับฐานข้อมูลบน Cloudflare ไม่ได้")
    if _CONN is not None:
        _CONN.close()
        _CONN = None
    if os.path.exists(config.DB_PATH):
        os.remove(config.DB_PATH)
