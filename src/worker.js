// us-stock-agent — Cloudflare Worker: หน้าเว็บ + API บน D1 + ราคาสด (Finnhub) + ช่องให้เอเจนต์ Python เขียนข้อมูล
//
// ล็อกอินเว็บ: env.APP_PASSWORD ถ้าตั้งไว้ ไม่งั้นใช้ PBKDF2 hash ใน docs('secrets','auth') (เปลี่ยนได้จากหน้าเว็บ)
// เอเจนต์ (POST /agent/sql) ยืนยันตัวได้ 2 แบบ:
//   1) GitHub Actions OIDC token — ต้องมาจาก repo env.AGENT_REPO สาขา main (ไม่ต้องเก็บ secret ใน GitHub)
//   2) Bearer token ที่ SHA-256 ตรงกับ docs('secrets','agent').hash — ใช้กับ backup.py บนคอม
// หน้าเว็บทำได้แค่: อ่าน, อนุมัติ/ปฏิเสธออเดอร์, Kill switch/ทำงานต่อ, ตั้ง Finnhub key, เปลี่ยนรหัสผ่าน — แก้พอร์ตหรือลิมิตไม่ได้

const COOKIE = 'usa_session';
const SESSION_DAYS = 30;
const enc = new TextEncoder();
const TICKER_RE = /^[A-Z.\-=]{1,10}$/;

const json = (data, status = 200, headers = {}) =>
  new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...headers } });
const b64 = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const unb64 = s => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((s.length + 3) % 4)), c => c.charCodeAt(0));
function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}
async function hmac(secret, msg) {
  const key = await crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return b64(await crypto.subtle.sign('HMAC', key, enc.encode(msg)));
}
async function pbkdf2(pw, salt) {
  const key = await crypto.subtle.importKey('raw', enc.encode(pw), 'PBKDF2', false, ['deriveBits']);
  return b64(await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt: enc.encode(salt), iterations: 100000 }, key, 256));
}
const sha256 = async s => b64(await crypto.subtle.digest('SHA-256', enc.encode(s)));
const rand = () => b64(crypto.getRandomValues(new Uint8Array(24)));
const nowIso = () => new Date(Date.now() + 7 * 3600e3).toISOString().slice(0, 19); // เวลาไทย

// ---------- docs (secrets) ----------
async function getDoc(env, coll, id) {
  const row = await env.DB.prepare('SELECT data FROM docs WHERE coll=? AND id=?').bind(coll, id).first();
  return row ? JSON.parse(row.data) : null;
}
async function putDoc(env, coll, id, data) {
  await env.DB.prepare('INSERT INTO docs (coll,id,data,updated_at) VALUES (?,?,?,?) ON CONFLICT(coll,id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at')
    .bind(coll, id, JSON.stringify(data), nowIso()).run();
}

// ---------- web auth ----------
async function authDoc(env) {
  let a = (await getDoc(env, 'secrets', 'auth')) || {};
  if (!a.sessionKey) { a.sessionKey = rand(); await putDoc(env, 'secrets', 'auth', a); }
  return a;
}
function getCookie(req, name) {
  const m = (req.headers.get('cookie') || '').match(new RegExp('(?:^|; )' + name + '=([^;]+)'));
  return m ? m[1] : null;
}
async function isAuthed(req, env) {
  const t = getCookie(req, COOKIE);
  if (!t) return false;
  const [exp, sig] = t.split('.');
  if (!exp || !sig || Number(exp) < Date.now()) return false;
  const a = await authDoc(env);
  return safeEqual(sig, await hmac(env.SESSION_SECRET || a.sessionKey, exp + (a.gen || '')));
}
async function checkPassword(env, pw) {
  if (env.APP_PASSWORD) return safeEqual(await hmac('pw', pw), await hmac('pw', env.APP_PASSWORD));
  const a = await authDoc(env);
  if (!a.hash || !a.salt) return false;
  return safeEqual(await pbkdf2(pw, a.salt), a.hash);
}
async function sessionCookie(env, a) {
  const exp = String(Date.now() + SESSION_DAYS * 864e5);
  const token = exp + '.' + (await hmac(env.SESSION_SECRET || a.sessionKey, exp + (a.gen || '')));
  return `${COOKIE}=${token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${SESSION_DAYS * 86400}`;
}
async function login(req, env) {
  const body = await req.json().catch(() => ({}));
  const a = await authDoc(env);
  if (!env.APP_PASSWORD && !a.hash) return json({ error: 'ยังไม่ได้ตั้งรหัสผ่าน' }, 500);
  if (!(await checkPassword(env, String(body.password || '')))) {
    await new Promise(r => setTimeout(r, 800));
    return json({ error: 'รหัสผ่านไม่ถูกต้อง' }, 401);
  }
  return json({ ok: true }, 200, { 'set-cookie': await sessionCookie(env, a) });
}
async function changePassword(req, env) {
  if (env.APP_PASSWORD) return json({ error: 'รหัสผ่านถูกตั้งไว้ใน Cloudflare เปลี่ยนในหน้านี้ไม่ได้' }, 400);
  const body = await req.json().catch(() => ({}));
  const next = String(body.next || '');
  if (!(await checkPassword(env, String(body.current || '')))) return json({ error: 'รหัสผ่านเดิมไม่ถูกต้อง' }, 400);
  if (next.length < 8) return json({ error: 'รหัสผ่านใหม่ต้องยาวอย่างน้อย 8 ตัว' }, 400);
  const a = await authDoc(env);
  a.salt = rand(); a.hash = await pbkdf2(next, a.salt); a.gen = rand(); // gen ใหม่ = ออกจากระบบทุกเครื่องอื่น
  await putDoc(env, 'secrets', 'auth', a);
  return json({ ok: true }, 200, { 'set-cookie': await sessionCookie(env, a) });
}

// ---------- agent auth ----------
let JWKS = { at: 0, keys: [] };
async function jwks(iss, force = false) {
  if (Date.now() - JWKS.at < (force ? 60e3 : 3600e3) && JWKS.iss === iss) return JWKS.keys;
  const r = await fetch(iss + '/.well-known/jwks');
  if (!r.ok) throw new Error('jwks ' + r.status);
  JWKS = { at: Date.now(), iss, keys: (await r.json()).keys || [] };
  return JWKS.keys;
}
async function verifyOidc(token, env) {
  const parts = token.split('.');
  if (parts.length !== 3 || !env.AGENT_REPO) return false;
  let header, p;
  try { header = JSON.parse(new TextDecoder().decode(unb64(parts[0]))); p = JSON.parse(new TextDecoder().decode(unb64(parts[1]))); }
  catch (e) { return false; }
  const iss = env.OIDC_ISSUER || 'https://token.actions.githubusercontent.com';
  const now = Date.now() / 1000;
  if (header.alg !== 'RS256' || p.iss !== iss || p.aud !== (env.OIDC_AUDIENCE || 'us-stock-agent')) return false;
  if (p.repository !== env.AGENT_REPO || p.ref !== 'refs/heads/main') return false;
  if (!(p.exp > now) || (p.nbf && p.nbf > now + 60)) return false;
  let jwk = (await jwks(iss)).find(k => k.kid === header.kid);
  if (!jwk) jwk = (await jwks(iss, true)).find(k => k.kid === header.kid);   // GitHub หมุนคีย์ใหม่
  if (!jwk) return false;
  const key = await crypto.subtle.importKey('jwk', jwk, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify']);
  return crypto.subtle.verify('RSASSA-PKCS1-v1_5', key, unb64(parts[2]), enc.encode(parts[0] + '.' + parts[1]));
}
async function agentAuthed(req, env) {
  const m = (req.headers.get('authorization') || '').match(/^Bearer (.+)$/);
  if (!m) return false;
  const tok = m[1];
  if (tok.split('.').length === 3) return verifyOidc(tok, env).catch(() => false);
  if (tok.length < 32) return false;
  if (env.AGENT_TOKEN && safeEqual(tok, env.AGENT_TOKEN)) return true;
  const s = await getDoc(env, 'secrets', 'agent');
  return Boolean(s && s.hash && safeEqual(await sha256(tok), s.hash));
}

// ---------- helpers ----------
const all = async (env, sql, ...args) => (await env.DB.prepare(sql).bind(...args).all()).results;
const first = (env, sql, ...args) => env.DB.prepare(sql).bind(...args).first();
async function getState(env, key, dflt = null) {
  const r = await first(env, 'SELECT value FROM state WHERE key=?', key);
  return r ? JSON.parse(r.value) : dflt;
}
const putState = (env, key, value) => env.DB.prepare('INSERT OR REPLACE INTO state VALUES (?,?)').bind(key, JSON.stringify(value));
const logStmt = (env, agent, event, detail) =>
  env.DB.prepare('INSERT INTO journal (time, agent, event, detail) VALUES (?,?,?,?)').bind(nowIso(), agent, event, detail == null ? null : JSON.stringify(detail));
async function finnhubKey(env) {
  if (env.FINNHUB_KEY) return env.FINNHUB_KEY;
  const s = await getDoc(env, 'secrets', 'finnhub');
  return s && s.key;
}

// ---------- API: อ่าน ----------
async function status(env) {
  const keys = ['cash', 'capital', 'paused', 'prev_regime', 'last_marked', 'last_prices', 'fx', 'last_data', 'data_source', 'universe'];
  const rows = await all(env, `SELECT key, value FROM state WHERE key IN (${keys.map(() => '?').join(',')})`, ...keys);
  const s = Object.fromEntries(rows.map(r => [r.key, JSON.parse(r.value)]));
  const positions = await all(env, 'SELECT ticker, shares, cost_usd FROM positions WHERE shares > 1e-9');
  const equity = await all(env, 'SELECT date, usd, thb, bench_usd FROM equity ORDER BY date');
  const lastPacket = await first(env, 'SELECT id, signal_date, status FROM packets ORDER BY id DESC LIMIT 1');
  const counts = await first(env, "SELECT SUM(status IN ('proposed','held')) pending, SUM(status='approved') approved FROM orders");
  const lastRun = await first(env, "SELECT time, event FROM journal WHERE agent IN ('orchestrator','data') ORDER BY id DESC LIMIT 1");
  return { ...s, positions, equity, last_packet: lastPacket, last_run: lastRun, pending: counts?.pending || 0, approved: counts?.approved || 0,
           can_run: Boolean(env.GH_TOKEN && env.AGENT_REPO), live_quotes: Boolean(await finnhubKey(env)),
           password_env: Boolean(env.APP_PASSWORD) };
}
async function packet(env, id) {
  const p = await first(env, 'SELECT * FROM packets WHERE id=?', id);
  if (!p) return null;
  p.data = JSON.parse(p.data); p.audit = JSON.parse(p.audit);
  p.orders = await all(env, 'SELECT * FROM orders WHERE packet_id=? ORDER BY side DESC, usd DESC', id);
  return p;
}

// ---------- API: ตัดสินใจ (ตรรกะเดียวกับ orchestrator.decide) ----------
async function decide(req, env) {
  const body = await req.json().catch(() => ({}));
  const ids = Array.isArray(body.ids) ? body.ids.filter(Number.isInteger).slice(0, 100) : [];
  const decision = body.decision;
  if (!ids.length || !['approve', 'reject'].includes(decision)) return json({ error: 'bad body' }, 400);
  if (await getState(env, 'paused', false)) return json({ error: "ระบบอยู่ในโหมดพัก กด 'ทำงานต่อ' ก่อน" }, 400);
  const blocked = await first(env, `SELECT COUNT(*) n FROM orders o JOIN packets p ON p.id=o.packet_id WHERE o.id IN (${ids.map(() => '?').join(',')}) AND p.status='blocked'`, ...ids);
  if (blocked.n) return json({ error: 'แพ็กเกจนี้ไม่ผ่านผู้ตรวจสอบ อนุมัติไม่ได้' }, 400);
  const st = decision === 'approve' ? 'approved' : 'rejected';
  const when = nowIso();
  const res = await env.DB.batch([
    ...ids.map(id => env.DB.prepare("UPDATE orders SET status=?, decided_at=? WHERE id=? AND status IN ('proposed','held')").bind(st, when, id)),
    env.DB.prepare("UPDATE packets SET status='decided' WHERE status='awaiting' AND id NOT IN (SELECT packet_id FROM orders WHERE status='proposed')"),
  ]);
  const n = res.slice(0, ids.length).reduce((a, r) => a + (r.meta.changes || 0), 0);
  await logStmt(env, 'owner', `${decision === 'approve' ? 'อนุมัติ' : 'ปฏิเสธ'} ${n} ออเดอร์ (เว็บ)`, { ids }).run();
  return json({ updated: n });
}
async function kill(env) {
  const res = await env.DB.batch([
    putState(env, 'paused', true),
    env.DB.prepare("UPDATE orders SET status='cancelled', note='Kill switch' WHERE status IN ('approved','proposed','held')"),
    env.DB.prepare("UPDATE packets SET status='cancelled' WHERE status='awaiting'"),
  ]);
  await logStmt(env, 'owner', 'Kill switch: หยุดระบบ (เว็บ)', { cancelled_orders: res[1].meta.changes }).run();
  return json({ cancelled: res[1].meta.changes });
}
async function resume(env) {
  await env.DB.batch([putState(env, 'paused', false), logStmt(env, 'owner', 'กลับมาทำงานต่อ (เว็บ)')]);
  return json({ ok: true });
}
async function saveSettings(req, env) {
  const body = await req.json().catch(() => ({}));
  if (typeof body.finnhub_key === 'string') {
    const k = body.finnhub_key.trim();
    if (k && !/^[A-Za-z0-9]{10,64}$/.test(k)) return json({ error: 'รูปแบบ Finnhub key ไม่ถูกต้อง' }, 400);
    await putDoc(env, 'secrets', 'finnhub', { key: k });
    await logStmt(env, 'owner', k ? 'ตั้ง Finnhub key (เว็บ)' : 'ลบ Finnhub key (เว็บ)').run();
  }
  return json({ ok: true });
}

// ---------- สั่งรันเอเจนต์ (GitHub Actions) ----------
async function runAgents(req, env) {
  if (!env.GH_TOKEN || !env.AGENT_REPO) return json({ error: 'ยังไม่ได้ตั้ง GH_TOKEN' }, 400);
  const body = await req.json().catch(() => ({}));
  const mode = ['daily', 'weekly', 'backtest'].includes(body.mode) ? body.mode : 'daily';
  const r = await fetch(`https://api.github.com/repos/${env.AGENT_REPO}/actions/workflows/agents.yml/dispatches`, {
    method: 'POST',
    headers: { authorization: `Bearer ${env.GH_TOKEN}`, accept: 'application/vnd.github+json', 'user-agent': 'us-stock-agent' },
    body: JSON.stringify({ ref: 'main', inputs: { mode } }),
  });
  if (!r.ok) return json({ error: `GitHub ตอบ ${r.status}` }, 502);
  await logStmt(env, 'owner', `สั่งรันเอเจนต์โหมด ${mode} (เว็บ)`).run();
  return json({ ok: true, mode });
}

// ---------- ราคาสด ----------
async function quotes(url, env, ctx) {
  const key = await finnhubKey(env);
  if (!key) return json({ enabled: false, quotes: {} });
  const syms = (url.searchParams.get('symbols') || '').split(',').map(s => s.trim().toUpperCase()).filter(s => TICKER_RE.test(s)).slice(0, 40);
  const cache = caches.default;
  const out = {};
  await Promise.all(syms.map(async s => {
    const ck = new Request(`https://quote-cache.internal/${s}`);
    let hit = await cache.match(ck);
    if (!hit) {
      const r = await fetch(`https://finnhub.io/api/v1/quote?symbol=${encodeURIComponent(s)}&token=${key}`);
      if (!r.ok) return;
      const q = await r.json();
      if (!q || !q.c) return;
      hit = new Response(JSON.stringify({ price: q.c, prev_close: q.pc, change_pct: q.dp, time: q.t }),
                         { headers: { 'cache-control': 'max-age=30', 'content-type': 'application/json' } });
      ctx.waitUntil(cache.put(ck, hit.clone()));
    }
    out[s] = await hit.json();
  }));
  return json({ enabled: true, quotes: out, fetched: new Date().toISOString() });
}

// ---------- ช่องสำหรับเอเจนต์ Python ----------
async function agentSql(req, env) {
  if (!(await agentAuthed(req, env))) return json({ error: 'unauthorized' }, 401);
  const body = await req.json().catch(() => null);
  const stmts = body && Array.isArray(body.statements) ? body.statements : null;
  if (!stmts || !stmts.length || stmts.length > 200) return json({ error: 'bad body' }, 400);
  if (stmts.some(s => /\bdocs\b/i.test(String(s.sql)))) return json({ error: 'ตาราง docs ใช้ผ่านช่องนี้ไม่ได้' }, 403);
  const res = await env.DB.batch(stmts.map(s => env.DB.prepare(String(s.sql)).bind(...(Array.isArray(s.params) ? s.params : []))));
  return json(res.map(r => ({ results: r.results || [], changes: r.meta?.changes || 0, last_row_id: r.meta?.last_row_id || 0 })));
}

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const p = url.pathname;
    try {
      if (p === '/agent/sql' && req.method === 'POST') return agentSql(req, env);
      if (!p.startsWith('/api/')) return env.ASSETS.fetch(req);
      if (p === '/api/login' && req.method === 'POST') return login(req, env);
      if (p === '/api/logout' && req.method === 'POST')
        return json({ ok: true }, 200, { 'set-cookie': `${COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0` });
      if (!(await isAuthed(req, env))) return json({ error: 'ต้องเข้าสู่ระบบ' }, 401);

      if (req.method === 'GET') {
        if (p === '/api/status') return json(await status(env));
        if (p === '/api/packets') return json(await all(env, 'SELECT id, created, signal_date, status FROM packets ORDER BY id DESC LIMIT 60'));
        const m = p.match(/^\/api\/packets\/(\d+)$/);
        if (m) { const pk = await packet(env, Number(m[1])); return pk ? json(pk) : json({ error: 'ไม่พบแพ็กเกจ' }, 404); }
        if (p === '/api/backtest') {
          const b = await first(env, 'SELECT id, created, source, data FROM backtests ORDER BY id DESC LIMIT 1');
          return json(b ? { ...b, data: JSON.parse(b.data) } : null);
        }
        if (p === '/api/journal') return json(await all(env, 'SELECT * FROM journal ORDER BY id DESC LIMIT 300'));
        if (p === '/api/quotes') return quotes(url, env, ctx);
      }
      if (req.method === 'POST') {
        if (p === '/api/orders/decide') return decide(req, env);
        if (p === '/api/kill') return kill(env);
        if (p === '/api/resume') return resume(env);
        if (p === '/api/run') return runAgents(req, env);
        if (p === '/api/settings') return saveSettings(req, env);
        if (p === '/api/password') return changePassword(req, env);
      }
      return json({ error: 'not found' }, 404);
    } catch (e) {
      return json({ error: String((e && e.message) || e) }, 500);
    }
  },
};
