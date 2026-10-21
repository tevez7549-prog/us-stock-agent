"""แหล่งข่าวทางการ: SEC EDGAR แบบ 8-K (บริษัทต้องยื่นภายใน 4 วันทำการเมื่อมีเหตุการณ์สำคัญ)

- รายชื่อเอกสารล่าสุด: https://data.sec.gov/submissions/CIK##########.json (ฟรี ไม่ต้องมี key)
- เนื้อหา: ถ้าเป็นข่าวประชาสัมพันธ์ (Exhibit 99.x) ดึงข้อความต้นๆ มาให้ Claude อ่าน เช่น ข่าวเปิดตัวสินค้า
- SEC ขอให้ใส่ User-Agent ที่ติดต่อได้ และไม่เกิน 10 ครั้ง/วินาที (เราหน่วง 0.15 วินาทีต่อครั้ง)
- ดึงไม่ได้ = คืนว่าง ไม่ทำให้รอบล้ม (ยังมีข่าวจาก Finnhub)
"""
import html
import json
import re
import time
import urllib.request
from datetime import date as _date, timedelta

import config

ITEMS = {
    "1.01": "ทำสัญญาสำคัญ", "1.02": "ยกเลิกสัญญาสำคัญ", "1.03": "ล้มละลาย/พิทักษ์ทรัพย์",
    "2.01": "ซื้อ/ขายกิจการเสร็จสิ้น", "2.02": "ประกาศผลประกอบการ", "2.03": "ก่อหนี้สำคัญ",
    "2.04": "เหตุเร่งชำระหนี้", "2.05": "ปรับโครงสร้าง/ปิดกิจการบางส่วน", "2.06": "ด้อยค่าสินทรัพย์",
    "3.01": "ถูกแจ้งเตือนเรื่องคุณสมบัติจดทะเบียน", "3.02": "ขายหุ้นเพิ่มทุนนอกตลาด", "3.03": "เปลี่ยนสิทธิผู้ถือหุ้น",
    "4.01": "เปลี่ยนผู้สอบบัญชี", "4.02": "งบที่ประกาศไปแล้วเชื่อถือไม่ได้ (ต้องแก้งบ)",
    "5.01": "เปลี่ยนผู้ควบคุมบริษัท", "5.02": "ผู้บริหาร/กรรมการเข้า-ออก", "5.03": "แก้ข้อบังคับ/รอบบัญชี",
    "5.07": "ผลโหวตผู้ถือหุ้น", "7.01": "เปิดเผยข้อมูลต่อสาธารณะ (Reg FD)", "8.01": "เหตุการณ์อื่น (มักเป็นข่าวประชาสัมพันธ์)",
    "9.01": "เอกสารแนบ",
}
BLOCK_ITEMS = {"1.03": "ยื่น 8-K ล้มละลาย/พิทักษ์ทรัพย์", "4.02": "ยื่น 8-K ว่างบเดิมเชื่อถือไม่ได้ (ต้องแก้งบ)"}
CAUTION_ITEMS = {"3.01": "ยื่น 8-K ถูกตลาดแจ้งเตือนคุณสมบัติจดทะเบียน", "4.01": "ยื่น 8-K เปลี่ยนผู้สอบบัญชี",
                 "2.04": "ยื่น 8-K เหตุเร่งชำระหนี้"}
READ_TEXT = {"1.01", "2.01", "2.02", "5.02", "7.01", "8.01"}   # เปิดอ่านข่าวประชาสัมพันธ์แนบ


def _ua():
    return config.SEC_USER_AGENT


def _get(url, as_json=True):
    time.sleep(0.15)
    req = urllib.request.Request(url, headers={"user-agent": _ua(), "accept-encoding": "identity"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    return json.loads(raw) if as_json else raw.decode("utf-8", "replace")


def _text(htm, n=1500):
    htm = re.sub(r"(?is)<(script|style).*?</\1>", " ", htm)
    t = html.unescape(re.sub(r"(?s)<[^>]+>", " ", htm))
    return re.sub(r"\s+", " ", t).strip()[:n]


def _press_release(cik, acc):
    """ข้อความต้นๆ ของ Exhibit 99.x (ข่าวประชาสัมพันธ์) ถ้ามี"""
    folder = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}"
    files = [f["name"] for f in _get(folder + "/index.json").get("directory", {}).get("item", [])]
    ex = [f for f in files if re.search(r"ex-?99", f, re.I) and f.lower().endswith((".htm", ".html", ".txt"))]
    return _text(_get(f"{folder}/{ex[0]}", as_json=False)) if ex else ""


def filings(ticker, cik, today=None, days=7, max_docs=3):
    """8-K ใน N วันล่าสุดของบริษัท → [{date, items, title, summary, url, source}]"""
    if not cik:
        return []
    today = today or _date.today()
    since = str(today - timedelta(days=days))
    sub = _get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    rec = sub.get("filings", {}).get("recent", {})
    out = []
    for i, form in enumerate(rec.get("form", [])):
        d = rec["filingDate"][i]
        if d < since:
            break   # เรียงใหม่ → เก่าอยู่แล้ว
        if form not in ("8-K", "8-K/A"):
            continue
        items = [x.strip() for x in (rec.get("items", [""] * (i + 1))[i] or "").split(",") if x.strip()]
        acc = rec["accessionNumber"][i]
        url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/"
               f"{rec.get('primaryDocument', [''] * (i + 1))[i]}")
        body = ""
        if READ_TEXT & set(items) and len(out) < max_docs:
            try:
                body = _press_release(cik, acc)
            except Exception as e:
                print("EDGAR เอกสารแนบ", ticker, e)
        desc = ", ".join(ITEMS.get(x, x) for x in items if x != "9.01") or "8-K"
        out.append({"date": d, "items": items, "title": f"[SEC {form}] {desc}", "summary": body,
                    "url": url, "source": "SEC 8-K", "official": True})
        if len(out) >= max_docs:
            break
    return out


LAST = {}   # สรุปรอบล่าสุด ให้ news_agent บันทึกลง journal (log ของ GitHub เปิดดูยาก)


def fetch(tickers, today=None):
    """{ticker: [8-K]} ของหุ้นที่มี CIK — ดึงไม่ได้ตัวไหนก็ข้ามตัวนั้น"""
    out, errs = {}, []
    for t in tickers:
        cik = config.info(t).get("cik")
        try:
            out[t] = filings(t, cik, today)
        except Exception as e:
            out[t] = []
            errs.append(f"{t}: {str(e)[:120]}")
            if len(errs) >= 5 and not any(out.values()):
                errs.append("ล้ม 5 ตัวติด ข้ามที่เหลือ")
                break
    LAST.clear()
    LAST.update({"checked": len(out), "docs": sum(len(v) for v in out.values()),
                 "no_cik": [t for t in tickers if not config.info(t).get("cik")][:10], "errors": errs[:6]})
    return out


def hard_flags(docs):
    """ธงที่โค้ดตัดสินเองจากชนิดเอกสาร (ไม่ให้ AI ตีความ) → (flag, reason) หรือ None"""
    items = {x for d in docs for x in d.get("items", [])}
    for k, why in BLOCK_ITEMS.items():
        if k in items:
            return "block", why
    for k, why in CAUTION_ITEMS.items():
        if k in items:
            return "caution", why
    return None
