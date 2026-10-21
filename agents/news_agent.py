"""เอเจนต์ 5 — ข่าวบริษัท (Claude)

อ่านข่าว 7 วันล่าสุดของหุ้นรายตัว "ทุกตัว" ในรายชื่อ (ไม่ใช่แค่ตัวที่ถืออยู่) วันละครั้ง:
- แหล่งข่าวทางการ: SEC EDGAR 8-K (agents/edgar.py) — ใส่ไว้หน้าสุด, ชนิดเอกสารร้ายแรง (ล้มละลาย/แก้งบ) โค้ดติดธงเอง
- แหล่งข่าว: Finnhub company-news (หัวข่าว + เนื้อย่อ + แหล่ง + ลิงก์) ถ้าไม่มี key/ดึงไม่ได้ → หัวข่าวจาก yfinance
- วันประกาศงบ: Finnhub earnings calendar → ภายใน 5 วันทำการ = "caution" (โค้ดตัดสิน ไม่ให้ AI เดา)
- Claude สรุปเป็น "ประเด็นข่าว" ต่อหุ้น: ประเภท (สินค้าใหม่/งบ/ดีล/กฎหมาย/ผู้บริหาร/อื่นๆ), ดี/ร้าย/กลาง, ความสำคัญ 1–3
  และติดธง ok / caution / block

ผลต่อพอร์ต (แยกชัด):
- ธง block = ห้ามซื้อเพิ่ม → ใช้กับทุกพอร์ต (กรองความเสี่ยงเท่านั้น)
- ข่าวดี (เช่น สินค้าใหม่) → ไม่มีผลกับ 3 กลยุทธ์ที่เป็นกฎ (ให้เป็นตัวเปรียบเทียบที่สะอาด)
  ส่งให้ AI Pick ใช้ตัดสินใจได้ ภายใต้ Risk Manager เดิม และแสดงบนเว็บ/รายงานให้เจ้าของอ่าน
ล้มเหลว → "ไม่มีธง" + บันทึกเหตุผล (ไม่เดาเอง)
"""
import json
import os
import time
import urllib.request
from datetime import date as _date, datetime, timedelta, timezone

import config
from agents import llm

TYPES = {"product": "สินค้า/บริการใหม่", "earnings": "งบ/คาดการณ์", "deal": "ดีล/สัญญา/ซื้อกิจการ",
         "regulatory": "กฎหมาย/คดี/ทางการ", "management": "ผู้บริหาร", "analyst": "นักวิเคราะห์", "other": "อื่นๆ"}

SYSTEM = """คุณคือนักวิเคราะห์ข่าวบริษัทจดทะเบียนสหรัฐ อ่านข่าว 7 วันล่าสุดของแต่ละหุ้นแล้วสรุปเป็น "ประเด็น"
- รวมข่าวที่พูดเรื่องเดียวกันเป็นประเด็นเดียว ตัดข่าวที่แค่บอกว่าราคาขึ้น/ลง หรือข่าวตลาดรวมที่ไม่เฉพาะบริษัท
- type: product (เปิดตัวสินค้า/บริการ/เทคโนโลยีใหม่, ได้รับอนุมัติยา), earnings (งบ, คาดการณ์รายได้), deal (สัญญาใหญ่, พันธมิตร, ซื้อกิจการ),
  regulatory (คดี, สอบสวน, แบน, ภาษี/กฎหมายที่กระทบตรง), management (ผู้บริหารเปลี่ยน), analyst (ปรับเป้า/คำแนะนำ), other
- tone: positive / negative / neutral ต่อธุรกิจของบริษัท (ไม่ใช่ต่อราคาวันนี้)
- importance: 3 = เปลี่ยนรายได้/กำไรได้จริงในระดับบริษัท, 2 = มีผลบ้าง, 1 = เล็กน้อย
- flag: "block" เฉพาะข่าวร้ายร้ายแรงและเฉพาะเจาะจง (ถูกสอบสวนฉ้อโกง, ผู้บริหารลาออกกะทันหัน, ลดคาดการณ์หนัก, คดีใหญ่,
  สินค้าหลักถูกแบน, ผิดนัดชำระหนี้), "caution" = ข่าวลบที่ไม่ร้ายแรง, ไม่งั้น "ok"
- ห้ามแต่งข้อมูลที่ไม่มีในข่าว ถ้าหุ้นไหนไม่มีประเด็นจริงให้ items เป็น []
- กระชับ: ไม่เกิน 3 ประเด็นต่อหุ้น, th ไม่เกิน 1 ประโยคสั้น, reason ไม่เกิน 10 คำ
- ข่าวที่ขึ้นต้น [SEC 8-K] คือเอกสารที่บริษัทยื่นต่อ ก.ล.ต. สหรัฐเอง (เชื่อถือได้สูงสุด) ถ้าขัดกับข่าวจากเว็บ ให้เชื่อ SEC
- src = เลขลำดับข่าว (ตัวเลข n ในข้อมูล) ที่ใช้อ้างอิง
รูปแบบ: {"stocks":[{"ticker":"AAPL","flag":"ok|caution|block","reason":"สั้นๆ ภาษาไทย",
 "items":[{"type":"product","tone":"positive","importance":2,"th":"สรุป 1 ประโยค ภาษาไทย","src":[3,5]}]}]}"""


# ---------------------------------------------------------------- ดึงข่าว
def _finnhub_key():
    return os.environ.get("FINNHUB_KEY") or llm.secret("finnhub")


def _get(url):
    req = urllib.request.Request(url, headers={"user-agent": "us-stock-agent/1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _finnhub_news(t, key, start, end):
    rows = _get(f"https://finnhub.io/api/v1/company-news?symbol={t.replace('-', '.')}&from={start}&to={end}&token={key}") or []
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: -(r.get("datetime") or 0)):
        h = (r.get("headline") or "").strip()
        if not h or h.lower() in seen:
            continue
        seen.add(h.lower())
        out.append({"title": h[:200], "summary": (r.get("summary") or "")[:260], "source": r.get("source") or "",
                    "url": r.get("url") or "",
                    "date": datetime.fromtimestamp(r.get("datetime") or 0, timezone.utc).strftime("%Y-%m-%d")})
        if len(out) >= 8:
            break
    return out


def _yf_news(t):
    import yfinance as yf
    out = []
    for i in (yf.Ticker(t).news or [])[:10]:
        c = i.get("content") or i
        url = ((c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else None) or c.get("link") or ""
        out.append({"title": (c.get("title") or "")[:200], "summary": (c.get("summary") or "")[:350],
                    "source": ((c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else c.get("publisher")) or "",
                    "url": url, "date": str(c.get("pubDate") or "")[:10]})
    return [x for x in out if x["title"]]


def fetch(tickers, today=None):
    """ข่าวต่อหุ้น + วันประกาศงบ คืน ({ticker: [ข่าว]}, {ticker: 'YYYY-MM-DD'}, แหล่ง)"""
    today = today or _date.today()
    start, end = str(today - timedelta(days=7)), str(today)
    key = _finnhub_key()
    news, src = {}, "finnhub" if key else "yfinance"
    for t in tickers:
        items = []
        if key:
            try:
                time.sleep(1.1)   # Finnhub ฟรี: 60 ครั้ง/นาที
                items = _finnhub_news(t, key, start, end)
            except Exception as e:
                print("Finnhub ข่าว", t, e)
        if not items:
            try:
                items = _yf_news(t)
            except Exception:
                items = []
        news[t] = items
    try:   # เอกสารทางการจาก SEC ไว้หน้าสุด
        from agents import edgar
        sec = edgar.fetch(tickers, today)
        for t, docs in sec.items():
            news[t] = docs + news.get(t, [])
        if any(sec.values()):
            src += "+sec"
        llm.paper.log("news", "SEC EDGAR", edgar.LAST)
    except Exception as e:
        llm.paper.log("news", "SEC EDGAR ล้มเหลว", {"error": str(e)[:300]})
    earn = {}
    if key:
        try:
            cal = _get(f"https://finnhub.io/api/v1/calendar/earnings?from={end}&to={today + timedelta(days=14)}&token={key}")
            for e in cal.get("earningsCalendar", []):
                sym = config.yf_symbol(e.get("symbol") or "")
                if sym in tickers and e.get("date"):
                    earn.setdefault(sym, e["date"])
        except Exception as e:
            print("Finnhub ปฏิทินงบ", e)
    return news, earn, src


def _bdays_until(d, today):
    try:
        target = _date.fromisoformat(d)
    except (TypeError, ValueError):
        return None
    n, cur = 0, today
    while cur < target:
        cur += timedelta(days=1)
        n += cur.weekday() < 5
    return n


# ---------------------------------------------------------------- สรุปข่าว (เรียก Claude ครั้งเดียวต่อวัน)
BATCH = 10   # หุ้นต่อการเรียก Claude 1 ครั้ง


def stocks():
    return [t for t, m in config.UNIVERSE.items() if m["kind"] == "stock"]


def digest(tickers=None, news=None, earnings=None, today=None):
    """สรุปข่าวทุกหุ้น คืน {"enabled","date","source","stocks":{t:{flag,reason,items,earnings,news}}}"""
    tickers = tickers or stocks()[:40]   # ปกติ orchestrator ส่งรายชื่อที่ถือ + โมเมนตัมสูงสุดมาให้
    today = today or _date.today()
    base = {"date": str(today), "stocks": {}}
    if not llm.enabled():
        return {**base, "enabled": False, "note": "ยังไม่ได้ใส่ Anthropic API key"}
    src = "given"
    if news is None:
        news, fetched_earn, src = fetch(tickers, today)
        earnings = fetched_earn if earnings is None else earnings
    earnings = earnings or {}
    base["source"] = src
    numbered, idx = {}, []
    for t in tickers:
        rows = []
        for a in news.get(t, []):
            if isinstance(a, str):
                a = {"title": a}
            idx.append(a)
            rows.append({"n": len(idx), "date": a.get("date", ""), "title": a.get("title", ""), "summary": a.get("summary", "")})
        numbered[t] = rows
    if not idx:
        return {**base, "enabled": True, "note": "ดึงข่าวไม่ได้"}

    got, failed = {}, 0
    keys = [t for t in tickers if numbered.get(t)]
    for i in range(0, len(keys), BATCH):   # ทีละ 10 ตัว ไม่งั้นคำตอบภาษาไทยยาวเกินขีดจำกัด
        part = {t: numbered[t] for t in keys[i:i + BATCH]}
        data = llm.ask_json("news", SYSTEM, "ข่าวแยกตาม ticker:\n" + json.dumps(part, ensure_ascii=False), 8000)
        if not data:
            failed += 1
            continue
        got.update({s.get("ticker"): s for s in data.get("stocks", []) if isinstance(s, dict) and s.get("ticker") in part})
    if failed:   # AI ล้มบางชุด: ชุดนั้นใช้ "ไม่มีธง" ยกเว้นเอกสาร SEC ร้ายแรงที่โค้ดตัดสินเอง
        base["error"] = f"News Agent ล้มเหลว {failed} จาก {-(-len(keys) // BATCH)} ชุด (ชุดนั้นใช้ค่า: ไม่มีธง ยกเว้นเอกสาร SEC ร้ายแรง)"
    out = {}
    for t in tickers:
        s = got.get(t) or {}
        flag = s.get("flag") if s.get("flag") in ("ok", "caution", "block") else "ok"
        reason = str(s.get("reason", ""))[:120]
        items = []
        for it in (s.get("items") or [])[:6]:
            if not isinstance(it, dict) or not it.get("th"):
                continue
            links = []
            for n in (it.get("src") or [])[:3]:
                if isinstance(n, int) and 1 <= n <= len(idx) and str(idx[n - 1].get("url", "")).startswith(("https://", "http://")):
                    a = idx[n - 1]
                    links.append({"title": a.get("title", "")[:120], "url": a["url"], "source": a.get("source", "")})
            items.append({"type": it.get("type") if it.get("type") in TYPES else "other",
                          "tone": it.get("tone") if it.get("tone") in ("positive", "negative", "neutral") else "neutral",
                          "importance": max(1, min(3, int(it.get("importance") or 1))) if str(it.get("importance", "1")).isdigit() else 1,
                          "th": str(it["th"])[:200], "links": links})
        official = [a for a in news.get(t, []) if isinstance(a, dict) and a.get("official")]
        from agents import edgar
        hard = edgar.hard_flags(official)
        if hard and (hard[0] == "block" or flag == "ok"):   # เอกสารทางการร้ายแรง: โค้ดตัดสิน ไม่ขึ้นกับ AI
            flag, reason = hard
        e = earnings.get(t)
        nb = _bdays_until(e, today) if e else None
        if nb is not None and 0 <= nb <= 5 and flag == "ok":   # งบใกล้ออก: โค้ดตัดสิน ไม่ใช่ AI
            flag, reason = "caution", f"ประกาศงบ {e} (อีก {nb} วันทำการ)"
        out[t] = {"flag": flag, "reason": reason, "items": items, "earnings": e, "n_news": len(news.get(t, [])),
                  "filings": [{"date": a["date"], "title": a["title"], "url": a["url"]} for a in official][:3]}
    return {**base, "enabled": True, "stocks": out}   # base อาจมี error (AI ล้ม) แต่ stocks ยังมีธงจาก SEC


def screen(tickers, dig=None, headlines=None):
    """ธงความเสี่ยงของหุ้นที่สนใจ (ใช้กับทุกพอร์ต) จาก digest ของวันนี้ — ข่าวดีไม่มีผลตรงนี้"""
    want = [t for t in tickers if config.UNIVERSE.get(t, {}).get("kind") == "stock"]
    if dig is None:
        dig = digest(want, news=headlines) if headlines is not None else digest(want)
    if not dig.get("enabled"):
        return {"enabled": False, "blocked": [], "caution": [], "flags": [], "note": dig.get("note", "")}
    if not want:
        return {"enabled": True, "blocked": [], "caution": [], "flags": [], "note": "ไม่มีหุ้นรายตัวให้ตรวจ"}
    flags = [{"ticker": t, "flag": dig["stocks"][t]["flag"], "reason": dig["stocks"][t]["reason"]}
             for t in want if t in dig.get("stocks", {})]
    return {"enabled": True, "flags": flags, "note": dig.get("note"), "error": dig.get("error"),
            "blocked": [f["ticker"] for f in flags if f["flag"] == "block"],
            "caution": [f["ticker"] for f in flags if f["flag"] == "caution"]}


def for_ai(dig):
    """ย่อ digest ให้ AI Pick อ่าน (ไม่มีลิงก์ ประหยัด token)"""
    if not dig or not dig.get("enabled"):
        return None
    return {t: {"flag": s["flag"], "earnings": s.get("earnings"),
                "items": [{"type": i["type"], "tone": i["tone"], "importance": i["importance"], "th": i["th"]} for i in s["items"]]}
            for t, s in dig.get("stocks", {}).items()}
