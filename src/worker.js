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

// ---------- encryption at rest (AES-GCM, key derived from the session secret stored in docs) ----------
async function aesKey(env) {
  const a = await authDoc(env);
  const raw = await crypto.subtle.digest('SHA-256', enc.encode('aes:' + (env.SESSION_SECRET || a.sessionKey)));
  return crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['encrypt', 'decrypt']);
}
async function encrypt(env, plain) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  return b64(iv) + '.' + b64(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, await aesKey(env), enc.encode(plain)));
}
async function decrypt(env, blob) {
  const [iv, ct] = blob.split('.');
  return new TextDecoder().decode(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: unb64(iv) }, await aesKey(env), unb64(ct)));
}
async function getSecret(env, name) {
  const d = await getDoc(env, 'secrets', name);
  if (!d || !d.enc) return null;
  try { return await decrypt(env, d.enc); } catch (e) { return null; }
}

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
async function jwks(iss, force = false, env = {}) {
  const ttl = env.JWKS_TTL_MS ? Number(env.JWKS_TTL_MS) : (force ? 60e3 : 3600e3);   // JWKS_TTL_MS ใช้เฉพาะตอนทดสอบบนเครื่อง
  if (Date.now() - JWKS.at < ttl && JWKS.iss === iss) return JWKS.keys;
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
  let jwk = (await jwks(iss, false, env)).find(k => k.kid === header.kid);
  if (!jwk) jwk = (await jwks(iss, true, env)).find(k => k.kid === header.kid);   // GitHub หมุนคีย์ใหม่
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
  const r = await first(env, "SELECT value FROM state WHERE portfolio='' AND key=?", key);
  return r ? JSON.parse(r.value) : dflt;
}
const putState = (env, key, value) => env.DB.prepare("INSERT OR REPLACE INTO state (portfolio, key, value) VALUES ('',?,?)").bind(key, JSON.stringify(value));
const logStmt = (env, agent, event, detail) =>
  env.DB.prepare("INSERT INTO journal (portfolio, time, agent, event, detail) VALUES ('',?,?,?,?)").bind(nowIso(), agent, event, detail == null ? null : JSON.stringify(detail));
async function finnhubKey(env) {
  if (env.FINNHUB_KEY) return env.FINNHUB_KEY;
  const s = await getDoc(env, 'secrets', 'finnhub');
  if (!s) return null;
  if (s.key) { await putDoc(env, 'secrets', 'finnhub', { enc: await encrypt(env, s.key) }); return s.key; } // ย้ายของเก่าเป็นแบบเข้ารหัส
  return getSecret(env, 'finnhub');
}
const PORTFOLIOS = ['momentum', 'dual_momentum', 'risk_parity', 'ai_pick'];

// ---------- API: อ่าน ----------
async function status(env) {
  const rows = await all(env, "SELECT portfolio, key, value FROM state");
  const shared = {}, per = {};
  for (const r of rows) {
    const v = JSON.parse(r.value);
    if (r.portfolio === '') shared[r.key] = v; else (per[r.portfolio] = per[r.portfolio] || {})[r.key] = v;
  }
  const positions = await all(env, 'SELECT portfolio, ticker, shares, cost_usd FROM positions WHERE shares > 1e-9');
  const equity = await all(env, 'SELECT portfolio, date, usd, thb, bench_usd FROM equity ORDER BY date');
  const counts = await all(env, "SELECT portfolio, SUM(status IN ('proposed','held')) pending, SUM(status='approved') approved FROM orders GROUP BY portfolio");
  const lastPk = await all(env, "SELECT portfolio, id, signal_date, status, json_extract(data, '$.target') target FROM packets WHERE id IN (SELECT MAX(id) FROM packets GROUP BY portfolio)");
  for (const x of lastPk) x.target = x.target ? JSON.parse(x.target) : null;
  const open = await all(env, "SELECT portfolio, ticker, side, status FROM orders WHERE status IN ('proposed','approved','held')");
  const lastRun = await first(env, "SELECT time, event FROM journal WHERE agent IN ('orchestrator','data') ORDER BY id DESC LIMIT 1");
  const portfolios = {};
  for (const p of PORTFOLIOS) {
    const c = counts.find(x => x.portfolio === p) || {};
    portfolios[p] = { ...(per[p] || {}), positions: positions.filter(x => x.portfolio === p), equity: equity.filter(x => x.portfolio === p),
                      pending: c.pending || 0, approved: c.approved || 0, last_packet: lastPk.find(x => x.portfolio === p) || null,
                      open_orders: open.filter(x => x.portfolio === p) };
  }
  const pending = counts.reduce((a, c) => a + (c.pending || 0), 0);
  return { ...shared, portfolios, pending, last_run: lastRun,
           can_run: Boolean(env.AGENT_REPO && (await ghToken(env))), live_quotes: Boolean(await finnhubKey(env)),
           ai_enabled: Boolean(await getSecret(env, 'anthropic')), password_env: Boolean(env.APP_PASSWORD) };
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
  const paused = await first(env, "SELECT COUNT(*) n FROM state WHERE key='paused' AND value='true'");
  if (paused.n) return json({ error: "ระบบอยู่ในโหมดพัก กด 'ทำงานต่อ' ก่อน" }, 400);
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
    ...PORTFOLIOS.map(p => env.DB.prepare('INSERT OR REPLACE INTO state (portfolio, key, value) VALUES (?,?,?)').bind(p, 'paused', 'true')),
    env.DB.prepare("UPDATE orders SET status='cancelled', note='Kill switch' WHERE status IN ('approved','proposed','held')"),
    env.DB.prepare("UPDATE packets SET status='cancelled' WHERE status='awaiting'"),
  ]);
  const n = res[PORTFOLIOS.length].meta.changes;
  await logStmt(env, 'owner', 'Kill switch: หยุดระบบ (เว็บ)', { cancelled_orders: n }).run();
  return json({ cancelled: n });
}
async function resume(env) {
  await env.DB.batch([...PORTFOLIOS.map(p => env.DB.prepare('INSERT OR REPLACE INTO state (portfolio, key, value) VALUES (?,?,?)').bind(p, 'paused', 'false')),
                      logStmt(env, 'owner', 'กลับมาทำงานต่อ (เว็บ)')]);
  return json({ ok: true });
}
async function saveSettings(req, env) {
  const body = await req.json().catch(() => ({}));
  if (typeof body.finnhub_key === 'string') {
    const k = body.finnhub_key.trim();
    if (k && !/^[A-Za-z0-9]{10,64}$/.test(k)) return json({ error: 'รูปแบบ Finnhub key ไม่ถูกต้อง' }, 400);
    await putDoc(env, 'secrets', 'finnhub', k ? { enc: await encrypt(env, k) } : {});
    await logStmt(env, 'owner', k ? 'ตั้ง Finnhub key (เว็บ)' : 'ลบ Finnhub key (เว็บ)').run();
  }
  if (typeof body.anthropic_key === 'string') {
    const k = body.anthropic_key.trim();
    if (k && !/^sk-ant-[A-Za-z0-9_\-]{20,200}$/.test(k)) return json({ error: 'รูปแบบ Anthropic key ไม่ถูกต้อง (ขึ้นต้น sk-ant-)' }, 400);
    await putDoc(env, 'secrets', 'anthropic', k ? { enc: await encrypt(env, k) } : {});
    await logStmt(env, 'owner', k ? 'ตั้ง Anthropic key (เว็บ) — เปิดเอเจนต์ AI' : 'ลบ Anthropic key (เว็บ) — ปิดเอเจนต์ AI').run();
  }
  if (typeof body.github_token === 'string') {
    const k = body.github_token.trim();
    if (k) {
      if (!/^(github_pat_[A-Za-z0-9_]{20,255}|ghp_[A-Za-z0-9]{30,255})$/.test(k)) return json({ error: 'รูปแบบ GitHub token ไม่ถูกต้อง (ขึ้นต้น github_pat_ หรือ ghp_)' }, 400);
      const t = await gh(env, k, '/actions/workflows/agents.yml');   // ทดสอบก่อนเก็บ: ต้องเห็น workflow ของ repo นี้
      if (!t.ok) return json({ error: `token ใช้กับ ${env.AGENT_REPO} ไม่ได้ (GitHub ตอบ ${t.status}) — เช็คว่าเลือก repo นี้และให้สิทธิ์ Actions` }, 400);
    }
    await putDoc(env, 'secrets', 'github', k ? { enc: await encrypt(env, k) } : {});
    await logStmt(env, 'owner', k ? 'ตั้ง GitHub token (เว็บ) — สั่งรันเอเจนต์จากเว็บได้' : 'ลบ GitHub token (เว็บ)').run();
  }
  return json({ ok: true });
}

// ---------- สั่งรันเอเจนต์ (GitHub Actions) ----------
const ghToken = async env => env.GH_TOKEN || (await getSecret(env, 'github'));
const gh = (env, token, path, init = {}) => fetch(`https://api.github.com/repos/${env.AGENT_REPO}${path}`, {
  ...init, headers: { authorization: `Bearer ${token}`, accept: 'application/vnd.github+json', 'user-agent': 'us-stock-agent', 'x-github-api-version': '2022-11-28' } });

async function runStatus(env) {
  const token = await ghToken(env);
  if (!token || !env.AGENT_REPO) return json({ enabled: false, runs: [] });
  const r = await gh(env, token, '/actions/workflows/agents.yml/runs?per_page=5');
  if (!r.ok) return json({ enabled: true, error: `GitHub ตอบ ${r.status}`, runs: [] });
  const d = await r.json();
  return json({ enabled: true, runs: (d.workflow_runs || []).map(x => ({ id: x.id, status: x.status, conclusion: x.conclusion,
    event: x.event, created: x.created_at, updated: x.updated_at, url: x.html_url })) });
}

async function runAgents(req, env) {
  const token = await ghToken(env);
  if (!token || !env.AGENT_REPO) return json({ error: 'ยังไม่ได้ใส่ GitHub token (แท็บตั้งค่า)' }, 400);
  const body = await req.json().catch(() => ({}));
  const mode = ['daily', 'weekly', 'backtest'].includes(body.mode) ? body.mode : 'daily';
  const busy = await gh(env, token, '/actions/workflows/agents.yml/runs?per_page=1&status=in_progress');
  if (busy.ok && ((await busy.json()).total_count || 0) > 0) return json({ error: 'มีรอบที่กำลังรันอยู่ รอให้เสร็จก่อน' }, 409);
  const r = await gh(env, token, '/actions/workflows/agents.yml/dispatches', {
    method: 'POST', body: JSON.stringify({ ref: 'main', inputs: { mode } }) });
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

async function agentSecret(req, env, url) {
  if (!(await agentAuthed(req, env))) return json({ error: 'unauthorized' }, 401);
  const name = url.searchParams.get('name');
  if (name === 'anthropic') return json({ value: await getSecret(env, name) });
  if (name === 'finnhub') return json({ value: await finnhubKey(env) });     // ข่าว + ปฏิทินงบ
  return json({ error: 'unknown secret' }, 400);   // เอเจนต์อ่านได้เฉพาะ 2 key นี้
}

// ---------- ตัวเฝ้าระวังตารางเวลา (Cloudflare Cron) ----------
// GitHub Actions แบบตั้งเวลาเลื่อน/ข้ามได้ (ช่วงคนใช้เยอะ) → Worker เช็คทุก 30 นาทีช่วง 06:00–10:59 ไทย
// ถ้าถึงเวลาแล้วยังไม่มีบันทึกว่ารัน และไม่มีรอบที่กำลังรัน/รอคิว → สั่งรันแทน (วันละไม่เกิน 1 ครั้งต่อโหมด)
async function watchdog(env) {
  const now = nowIso(), today = now.slice(0, 10), hm = now.slice(11, 16);
  const dow = new Date(today + 'T00:00:00Z').getUTCDay();          // 0=อาทิตย์ ... 6=เสาร์ (วันไทย)
  const due = [];
  if (dow === 6 && hm >= '09:20') due.push(['weekly', "agent='orchestrator' AND event='เริ่มรอบรายสัปดาห์'", `${today}T09:00:00`]);
  if (dow >= 2 && dow <= 6 && hm >= '06:50') due.push(['daily', "agent='data'", `${today}T06:00:00`]);
  if (!due.length) return;
  const token = await ghToken(env);
  if (!token || !env.AGENT_REPO) return;
  for (const [mode, cond, since] of due) {
    const ran = await first(env, `SELECT COUNT(*) n FROM journal WHERE ${cond} AND time >= ?`, since);
    if (ran.n) continue;
    const tried = await first(env, "SELECT COUNT(*) n FROM journal WHERE agent='system' AND event=? AND time >= ?", `watchdog: สั่งรัน ${mode}`, `${today}T00:00:00`);
    if (tried.n) continue;
    for (const st of ['in_progress', 'queued']) {
      const r = await gh(env, token, `/actions/workflows/agents.yml/runs?per_page=1&status=${st}`);
      if (r.ok && ((await r.json()).total_count || 0) > 0) return;   // มีรอบกำลังทำอยู่ รอรอบเช็คถัดไป
    }
    const r = await gh(env, token, '/actions/workflows/agents.yml/dispatches', { method: 'POST', body: JSON.stringify({ ref: 'main', inputs: { mode } }) });
    await logStmt(env, 'system', `watchdog: สั่งรัน ${mode}`, { reason: 'GitHub ไม่ได้รันตามเวลา', github_status: r.status }).run();
    return;   // ครั้งละโหมดเดียว (weekly รันรายวันให้ด้วยอยู่แล้ว)
  }
}

export default {
  async scheduled(event, env, ctx) { ctx.waitUntil(watchdog(env)); },
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const p = url.pathname;
    try {
      if (p === '/agent/sql' && req.method === 'POST') return agentSql(req, env);
      if (p === '/agent/secret' && req.method === 'GET') return agentSecret(req, env, url);
      if (!p.startsWith('/api/')) return env.ASSETS.fetch(req);
      if (p === '/api/login' && req.method === 'POST') return login(req, env);
      if (p === '/api/logout' && req.method === 'POST')
        return json({ ok: true }, 200, { 'set-cookie': `${COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0` });
      if (!(await isAuthed(req, env))) return json({ error: 'ต้องเข้าสู่ระบบ' }, 401);

      if (req.method === 'GET') {
        if (p === '/api/status') return json(await status(env));
        if (p === '/api/packets') return json(await all(env, 'SELECT id, portfolio, created, signal_date, status FROM packets ORDER BY id DESC LIMIT 120'));
        const m = p.match(/^\/api\/packets\/(\d+)$/);
        if (m) { const pk = await packet(env, Number(m[1])); return pk ? json(pk) : json({ error: 'ไม่พบแพ็กเกจ' }, 404); }
        if (p === '/api/run/status') return runStatus(env);
        if (p === '/api/backtest') {
          const rows = await all(env, 'SELECT portfolio, id, created, source, data FROM backtests WHERE id IN (SELECT MAX(id) FROM backtests GROUP BY portfolio)');
          return json(Object.fromEntries(rows.map(b => [b.portfolio, { ...b, data: JSON.parse(b.data) }])));
        }
        if (p === '/api/journal') return json(await all(env, 'SELECT * FROM journal ORDER BY id DESC LIMIT 400'));
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
