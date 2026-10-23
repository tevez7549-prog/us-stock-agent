"""ตรวจว่า SEC EDGAR ตอบเราได้ (User-Agent มีอีเมล) — ผลเก็บใน D1 shared state 'research_sec_check'"""
import json
import urllib.request

import config
import paper


def get(url):
    req = urllib.request.Request(url, headers={"user-agent": config.SEC_USER_AGENT, "accept-encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, len(r.read())
    except urllib.error.HTTPError as e:
        return e.code, 0
    except Exception as e:
        return str(e)[:80], 0


if __name__ == "__main__":
    out = {"created": paper.now_th(),
           "submissions_AAPL": get("https://data.sec.gov/submissions/CIK0000320193.json"),
           "companyfacts_AAPL": get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")}
    print(json.dumps(out))
    paper.put("research_sec_check", out, shared=True)
