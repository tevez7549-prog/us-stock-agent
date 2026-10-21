"""ค่าทั้งหมดของระบบ — ลิมิตความเสี่ยงอยู่ที่นี่ที่เดียว ห้ามให้เอเจนต์ AI แก้ไฟล์นี้"""
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
UNIVERSE = {
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
    # หุ้นรายตัวขนาดใหญ่
    "AAPL": {"kind": "stock", "group": "tech"},
    "MSFT": {"kind": "stock", "group": "tech"},
    "NVDA": {"kind": "stock", "group": "tech"},
    "GOOGL":{"kind": "stock", "group": "tech"},
    "META": {"kind": "stock", "group": "tech"},
    "AMZN": {"kind": "stock", "group": "consumer"},
    "JPM":  {"kind": "stock", "group": "financial"},
    "V":    {"kind": "stock", "group": "financial"},
    "UNH":  {"kind": "stock", "group": "health"},
    "LLY":  {"kind": "stock", "group": "health"},
    "COST": {"kind": "stock", "group": "staples"},
    "XOM":  {"kind": "stock", "group": "energy"},
    # สินทรัพย์ป้องกัน
    "TLT":  {"kind": "defensive", "group": "bond"},
    "IEF":  {"kind": "defensive", "group": "bond"},
    "GLD":  {"kind": "defensive", "group": "gold"},
    "SHY":  {"kind": "cash",      "group": "cash"},   # พันธบัตรระยะสั้น = แทนเงินสด
}
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
