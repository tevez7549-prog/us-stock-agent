"""ค่าทั้งหมดของระบบ — ลิมิตความเสี่ยงอยู่ที่นี่ที่เดียว ห้ามให้เอเจนต์ AI แก้ไฟล์นี้"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------- ทุน ----------
START_CAPITAL_USD = 10_000.0          # พอร์ตจำลอง (~3 แสนบาท)
BASE_CCY = "USD"

# ---------- จักรวาลสินทรัพย์ (ซื้อได้ใน Dime) ----------
# group = ใช้คุมสัดส่วนรวม เช่น หุ้นเทคทุกตัว + QQQ + XLK นับเป็นกลุ่ม "tech" เดียวกัน
# ETF + สินทรัพย์ป้องกัน = รายชื่อตายตัว / หุ้นรายตัว = สมาชิก S&P 500 ทั้งหมด (universe/sp500.csv)
FIXED = {
    # ETF ตลาดกว้าง
    "SPY":  {"kind": "etf",   "group": "us_broad"},
    "QQQ":  {"kind": "etf",   "group": "tech"},
    "IWM":  {"kind": "etf",   "group": "us_small"},
    "EFA":  {"kind": "etf",   "group": "intl"},
    "EEM":  {"kind": "etf",   "group": "intl"},
    # ETF รายอุตสาหกรรม
    "XLK":  {"kind": "etf",   "group": "tech"},
    "XLV":  {"kind": "etf",   "group": "health"},
    "XLF":  {"kind": "etf",   "group": "financial"},
    "XLE":  {"kind": "etf",   "group": "energy"},
    "XLI":  {"kind": "etf",   "group": "industrial"},
    "XLP":  {"kind": "etf",   "group": "staples"},
    "XLU":  {"kind": "etf",   "group": "utilities"},
    "XLY":  {"kind": "etf",   "group": "consumer"},
    # สินทรัพย์ป้องกัน
    "TLT":  {"kind": "defensive", "group": "bond"},
    "IEF":  {"kind": "defensive", "group": "bond"},
    "GLD":  {"kind": "defensive", "group": "gold"},
    "SHY":  {"kind": "cash",      "group": "cash"},   # พันธบัตรระยะสั้น = แทนเงินสด
}
GICS_GROUP = {
    "Information Technology": "tech", "Communication Services": "comm", "Consumer Discretionary": "consumer",
    "Consumer Staples": "staples", "Health Care": "health", "Financials": "financial", "Energy": "energy",
    "Industrials": "industrial", "Utilities": "utilities", "Materials": "materials", "Real Estate": "real_estate",
}
SP500_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "universe", "sp500.csv")
SP500_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
UNIVERSE = {}


def yf_symbol(sym):
    return sym.strip().upper().replace(".", "-")   # BRK.B → BRK-B (แบบ Yahoo)


def read_sp500(text=None):
    """แถว (symbol แบบ Yahoo, ชื่อบริษัท, sector) จาก CSV ของ S&P 500"""
    import csv
    import io
    if text is None:
        with open(SP500_FILE, encoding="utf-8") as f:
            text = f.read()
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        sym, sec = (r.get("Symbol") or "").strip(), (r.get("GICS Sector") or "").strip()
        if sym and sec in GICS_GROUP:
            rows.append((yf_symbol(sym), (r.get("Security") or "").strip(), sec))
    return rows


def set_stocks(rows):
    """แทนที่รายชื่อหุ้นทั้งหมด (แก้ dict เดิม เพื่อให้ทุกโมดูลเห็นพร้อมกัน)"""
    stocks = {sym: {"kind": "stock", "group": GICS_GROUP[sec], "name": name} for sym, name, sec in rows if sym not in FIXED}
    UNIVERSE.clear()
    UNIVERSE.update(FIXED)
    UNIVERSE.update(stocks)


def info(t):
    """ข้อมูลของ ticker — ตัวที่หลุดจาก S&P 500 แต่ยังถืออยู่ นับเป็นหุ้นกลุ่ม other (Risk Manager จะให้ขายออก)"""
    return UNIVERSE.get(t) or {"kind": "stock", "group": "other", "name": t, "removed": True}


set_stocks(read_sp500())
BENCHMARK = "SPY"
FX_TICKER = "THB=X"                    # บาทต่อ 1 ดอลลาร์ (Yahoo)
CASH_PROXY = "SHY"

# ---------- กลยุทธ์ (เอเจนต์ 3 + 4) ----------
STRATEGY = {
    "mom_short": 126,      # โมเมนตัม 6 เดือน (วันทำการ)
    "mom_long": 252,       # โมเมนตัม 12 เดือน
    "trend_sma": 200,      # ต้องอยู่เหนือเส้น 200 วัน
    "vol_window": 63,      # ความผันผวน 3 เดือน ใช้ถ่วงน้ำหนัก
    "top_n": 6,            # ถือสินทรัพย์เสี่ยงสูงสุด 6 ตัว
    "rebalance": "W-FRI",  # สัญญาณทุกวันศุกร์ เทรดวันทำการถัดไป
    "hold_buffer": 3,      # ตัวที่ถืออยู่ ถือต่อได้ถ้าอันดับยังไม่หลุด top_n + 3 (ลดการซื้อขายถี่)
    "regime_band": 0.02,   # ต้องทะลุเส้น 200 วันเกิน 2% ถึงจะเปลี่ยนสภาวะ (กันสัญญาณหลอก)
    "risk_off_exposure": 0.3,   # ตลาดขาลง ถือของเสี่ยงได้แค่ 30%
    "weak_breadth_cut": 0.7,    # หุ้นส่วนใหญ่หลุดเทรนด์ ลดลงอีก 30%
}

# ---------- Risk Manager (เอเจนต์ 7) ----------
RISK = {
    "max_stock": 0.15,          # หุ้นรายตัว ไม่เกิน 15%
    "max_etf": 0.25,            # ETF ตัวเดียว ไม่เกิน 25%
    "max_group": 0.40,          # กลุ่มเดียวกัน (เช่น tech) ไม่เกิน 40%
    # กลยุทธ์ที่ "ถือทั้งก้อนเป็นกลุ่มสินทรัพย์" (ไม่ได้เลือกหุ้นรายตัว) ใช้เพดานของตัวเองสำหรับ ETF ตลาดกว้าง
    # ไม่งั้น Dual Momentum (ถือ SPY ตัวเดียวโดยออกแบบ) จะกลายเป็นเงินสด 75% ซึ่งไม่ใช่กลยุทธ์นั้นแล้ว
    "per_strategy": {
        "dual_momentum": {"max_etf": 1.0, "max_group": 1.0},
        "risk_parity":   {"max_etf": 0.40, "max_group": 0.40},
    },
    "min_cash": 0.05,           # เงินสด/SHY อย่างน้อย 5%
    "dd_cut_level": 0.15,       # พอร์ตลงจากจุดสูงสุด 15% → ลดความเสี่ยงครึ่งหนึ่ง
    "dd_cut_factor": 0.5,
    "min_trade_weight": 0.02,   # เปลี่ยนน้ำหนักไม่ถึง 2% ไม่ต้องเทรด (ประหยัดค่าธรรมเนียม)
}

# ---------- ต้นทุน (ปรับให้ตรงกับค่าธรรมเนียม Dime ปัจจุบันก่อนใช้จริง) ----------
COSTS = {
    "commission_pct": 0.0015,   # ต่อมูลค่าซื้อขาย (ค่าสมมติ — เช็คในแอป)
    "slippage_pct": 0.0005,
    "fx_spread_pct": 0.0025,    # ค่าแลกบาท→ดอลลาร์ ตอนฝากเงินครั้งแรก
}

# ---------- Forward test ----------
APPROVAL_EXPIRE_DAYS = 3        # อนุมัติแล้วต้องเทรดภายใน 3 วันทำการ ไม่งั้นหมดอายุ
MAX_PRICE_MOVE = 0.03           # ราคาขยับเกิน 3% จากตอนเสนอ = พักออเดอร์

# ---------- เอเจนต์ AI ----------
# เปิดอัตโนมัติเมื่อมี Anthropic API key: ใส่ในหน้าตั้งค่าของเว็บ (เก็บใน D1) หรือ env ANTHROPIC_API_KEY
CLAUDE_MODEL = "claude-sonnet-5-5"

DATA_START = "2010-01-01"
DB_PATH = "data/invest.db"
