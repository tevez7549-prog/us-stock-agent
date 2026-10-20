"""สำรองข้อมูลจาก Cloudflare D1 ลงคอม → data/backup/us-stock-YYYY-MM-DD.db (SQLite เปิดดูด้วย DB Browser ได้)
ใช้: ตั้ง WORKER_URL และ AGENT_TOKEN ในไฟล์ .env แล้วรัน backup.bat (หรือตั้ง Task Scheduler ทุกคืน)
"""
import os
import sqlite3
import sys
from datetime import date

import config  # noqa: F401  (โหลด .env)
import paper

TABLES = ["state", "positions", "packets", "orders", "equity", "journal", "backtests"]
KEEP = 60  # เก็บไฟล์สำรองล่าสุด 60 ไฟล์


def main():
    if not paper.remote():
        sys.exit("ยังไม่ได้ตั้ง WORKER_URL / AGENT_TOKEN ในไฟล์ .env")
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
