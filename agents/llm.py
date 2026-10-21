"""ตัวกลางเรียก Claude สำหรับเอเจนต์ AI ทุกตัว

- key: env ANTHROPIC_API_KEY ถ้ามี ไม่งั้นขอจาก Worker (/agent/secret?name=anthropic ใช้ OIDC/AGENT_TOKEN)
  key ถูกใส่ไว้ในหน้าตั้งค่าของเว็บ เก็บใน D1 ฝั่งเซิร์ฟเวอร์
- ทุกคำตอบต้องเป็น JSON ตาม schema ที่ผู้เรียกกำหนด ถ้าแปลงไม่ได้ = ล้มเหลว (ผู้เรียกต้องมี fallback)
- บันทึกการใช้ (tokens) ลง journal เพื่อดูค่าใช้จ่าย
"""
import json
import os
import urllib.request

import config
import paper

_KEY = None


_SECRETS = {}


def secret(name):
    """ขอค่าลับจาก Worker (/agent/secret?name=...) ใช้ OIDC/AGENT_TOKEN — ได้เฉพาะ anthropic / finnhub"""
    if name in _SECRETS:
        return _SECRETS[name]
    v = None
    if paper.remote():
        c = paper.conn()
        try:
            req = urllib.request.Request(
                c.url.replace("/agent/sql", f"/agent/secret?name={name}"),
                headers={"authorization": f"Bearer {c._token()}", "user-agent": "us-stock-agent/1"})
            with urllib.request.urlopen(req, timeout=30) as r:
                v = json.loads(r.read()).get("value")
        except Exception as e:
            print(f"ขอ {name} จาก Worker ไม่ได้:", e)
    _SECRETS[name] = v
    return v


def api_key():
    global _KEY
    if _KEY:
        return _KEY
    _KEY = os.environ.get("ANTHROPIC_API_KEY") or secret("anthropic")
    return _KEY


def enabled():
    return bool(api_key())


def _parse(text):
    """JSON จากข้อความ — ถ้าพัง (เช่น เครื่องหมายคำพูดในข้อความ) ใช้ json_repair ซ่อม"""
    body = text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(body)
    except ValueError:
        from json_repair import repair_json
        return json.loads(repair_json(body))


def ask_json(agent, system, user, max_tokens=1500):
    """เรียก Claude แล้วคืน dict หรือ None ถ้าล้มเหลว (บันทึกเหตุผลลง journal)"""
    key = api_key()
    if not key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        # ให้ตอบผ่าน tool → ได้ JSON ที่ parse แล้ว (ข้อความไทยที่มีเครื่องหมายคำพูดไม่ทำให้พัง)
        # โมเดลรุ่นนี้ไม่รองรับบังคับ tool (tool_choice=tool/any) จึงใช้ auto + กำชับใน system และมีตัวซ่อม JSON สำรอง
        msg = client.messages.create(
            model=config.CLAUDE_MODEL, max_tokens=max_tokens,
            system=system + "\n\nส่งคำตอบผ่านเครื่องมือ submit เท่านั้น โดยใช้โครงสร้างตามรูปแบบข้างบน",
            messages=[{"role": "user", "content": user}],
            tools=[{"name": "submit", "description": "ส่งคำตอบเป็น JSON ตามรูปแบบที่กำหนดใน system prompt",
                    "input_schema": {"type": "object", "additionalProperties": True}}],
            tool_choice={"type": "auto"})
        if msg.stop_reason == "max_tokens":
            raise ValueError(f"คำตอบยาวเกิน max_tokens={max_tokens}")
        data = next((b.input for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        if data is None:   # เผื่อโมเดลตอบเป็นข้อความ
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            data = _parse(text)
        if not isinstance(data, dict):
            raise ValueError("คำตอบไม่ใช่ JSON object")
        paper.log(agent, "เรียก Claude", {"in": msg.usage.input_tokens, "out": msg.usage.output_tokens})
        return data
    except Exception as e:
        paper.log(agent, "Claude ล้มเหลว", {"error": str(e)[:300]})
        return None
