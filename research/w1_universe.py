"""สร้างรายชื่อหุ้นของ W1 / Momentum กว้างครั้งแรก (S&P 500 + หุ้นนอกดัชนี ≥ $2,000 ล้าน, ซื้อขาย ≥ $10 ล้าน/วัน) เก็บใน D1 shared 'breakout_universe'"""
import json
from collections import Counter

import orchestrator
import paper
from strategies import breakout

if __name__ == "__main__":
    orchestrator.refresh_universe()
    u = breakout.ensure_universe(force=True)
    t = u["tickers"]
    extra = [k for k in t if k not in breakout.config.UNIVERSE]
    print(json.dumps({"total": len(t), "extra": len(extra), "groups_extra": Counter(t[k][1] for k in extra).most_common(),
                      "sample_extra": extra[:40]}, ensure_ascii=False), flush=True)
