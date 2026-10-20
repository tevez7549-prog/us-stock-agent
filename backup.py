"""สำรองข้อมูลจาก Cloudflare D1 ลงคอม → data/backup/us-stock-YYYY-MM-DD.db (SQLite เปิดดูด้วย DB Browser ได้)
ใช้: ดับเบิลคลิก backup.bat (หรือตั้ง Task Scheduler ทุกคืน)
รหัสอ่านจาก AGENT_TOKEN ใน .env หรือไฟล์ .agent_token ในโฟลเดอร์นี้ (ไฟล์ทั้งสองไม่ขึ้น GitHub)
"""
import os
import sqlite3
import sys
from datetime import date

import config  # noqa: F401  (โหลด .env)
import paper

TABLES = ["state", "positions", "packets", "orders", "equity", "journal", "backtests"]
KEEP = 60  # เก็บไฟล์สำรองล่าสุด 60 ไฟล์


WORKER_URL = "https://us-stock-agent.tevez7549.workers.dev"


def main():
    if not os.environ.get("AGENT_TOKEN") and os.path.exists(".agent_token"):
        os.environ["AGENT_TOKEN"] = open(".agent_token").read().strip()
    os.environ.setdefault("WORKER_URL", WORKER_URL)
    if not paper.remote():
        sys.exit("ไม่พบรหัส: ใส่ AGENT_TOKEN ใน .env หรือวางไฟล์ .agent_token ไว้ในโฟลเดอร์นี้")
    src = paper.conn()
    os.makedirs("data/backup", exist_ok=True)
    path = f"data/backup/us-stock-{date.today()}.db"
    tmp = path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    dst = sqlite3.connect(tmp)
    dst.executescript(paper.SCHEMA)
    total = 0
    for t in TABLES:
        rows = src.execute(f"SELECT * FROM {t}").fetchall()
        if rows:
            cols = list(rows[0].keys())
            dst.executemany(f"INSERT INTO {t} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            [tuple(r[c] for c in cols) for r in rows])
        total += len(rows)
        print(f"{t}: {len(rows)} แถว")
    dst.commit()
    dst.close()
    os.replace(tmp, path)
    old = sorted(f for f in os.listdir("data/backup") if f.endswith(".db"))[:-KEEP]
    for f in old:
        os.remove(os.path.join("data/backup", f))
    print(f"สำรองแล้ว {total} แถว → {path}")


if __name__ == "__main__":
    main()
