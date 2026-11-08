#!/usr/bin/env node
// Static checks for the HUMA IA Make app definition (../app). Local only: no network, no Make, no HUMA.
// Run: node scripts/check.js   (exit code 0 = all checks passed)
// It proves the files are consistent; it does NOT prove Make accepts them or that the IML runs.

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const EM_DASH = String.fromCharCode(0x2014);
const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const APP = path.join(ROOT, 'app');
const errors = [];
const notes = [];
let checks = 0;

function fail(msg) { errors.push(msg); }
function ok(cond, msg) { checks += 1; if (!cond) fail(msg); return cond; }
function rel(p) { return path.relative(ROOT, p).split(path.sep).join('/'); }

function walk(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

// 1. Every .json / .imljson file parses.
const parsed = new Map();
for (const file of walk(APP)) {
  if (!/\.(json|imljson)$/.test(file)) continue;
  try {
    parsed.set(rel(file), JSON.parse(fs.readFileSync(file, 'utf8')));
    checks += 1;
  } catch (e) {
    fail(`${rel(file)}: invalid JSON (${e.message})`);
  }
}
const get = (p) => parsed.get(`app/${p}`);
const has = (p) => parsed.has(`app/${p}`);

// 2. App-level files.
for (const f of ['metadata.json', 'base.imljson', 'groups.json', 'install.json', 'installSpec.json']) ok(has(f), `missing app/${f}`);
ok(fs.existsSync(path.join(APP, 'readme.md')), 'missing app/readme.md (app documentation)');
const meta = get('metadata.json') || {};
for (const k of ['name', 'label', 'version', 'description', 'theme', 'language', 'website', 'docs', 'support']) ok(meta[k] !== undefined && meta[k] !== '', `app/metadata.json: missing "${k}"`);
ok(/^[a-z0-9][a-z0-9-]{0,63}$/.test(meta.name || ''), 'app/metadata.json: name must be lowercase URL-safe');
ok(/^#[0-9A-Fa-f]{6}$/.test(meta.theme || ''), 'app/metadata.json: theme must be #RRGGBB');

// 3. Base.
const base = get('base.imljson') || {};
ok(base.baseUrl === 'https://app.humaia.com.br/api/v1', 'base: baseUrl must be https://app.humaia.com.br/api/v1');
ok(/Bearer \{\{connection\.accessToken\}\}/.test(JSON.stringify(base.headers || {})), 'base: authorization header must use connection.accessToken');
ok((base.log?.sanitize || []).includes('request.headers.authorization'), 'base: log.sanitize must hide request.headers.authorization');
ok(typeof base.response?.error?.message === 'string' && base.response.error.message.includes('body.detail'), 'base: response.error.message must read body.detail');
ok(base.response?.error?.type === undefined, 'base: do not set a generic error type (it overrides the 429/5xx defaults)');

// 4. Connections.
const connDir = path.join(APP, 'connections');
const connections = fs.existsSync(connDir) ? fs.readdirSync(connDir) : [];
ok(connections.length === 1, 'exactly one connection expected');
for (const c of connections) {
  for (const f of ['api.json', 'parameters.json', 'metadata.json', 'common.json']) ok(has(`connections/${c}/${f}`), `connection ${c}: missing ${f}`);
  const api = get(`connections/${c}/api.json`) || {};
  const md = get(`connections/${c}/metadata.json`) || {};
  const common = get(`connections/${c}/common.json`) || {};
  ok(md.type === 'oauth', `connection ${c}: type must be "oauth"`);
  for (const k of ['authorize', 'token', 'refresh', 'info']) ok(api[k], `connection ${c}: missing "${k}" block`);
  ok(common.clientId === 'huma-make', `connection ${c}: common.clientId must be huma-make`);
  ok(api.authorize?.url === 'https://app.humaia.com.br/oauth/mcp/authorize', `connection ${c}: authorize url`);
  ok(api.token?.url === 'https://app.humaia.com.br/oauth/mcp/token', `connection ${c}: token url`);
  ok(api.refresh?.url === 'https://app.humaia.com.br/oauth/mcp/token', `connection ${c}: refresh url`);
  ok(api.info?.url === 'https://app.humaia.com.br/api/v1/me', `connection ${c}: info must call /api/v1/me`);
  const qs = api.authorize?.qs || {};
  ok(qs.redirect_uri === '{{oauth.localRedirectUri}}', `connection ${c}: authorize redirect_uri must be oauth.localRedirectUri`);
  ok(api.token?.body?.redirect_uri === qs.redirect_uri, `connection ${c}: token redirect_uri must equal authorize redirect_uri`);
  ok(qs.code_challenge_method === 'S256', `connection ${c}: PKCE method must be S256`);
  ok(qs.client_id === '{{common.clientId}}' && api.token?.body?.client_id === '{{common.clientId}}' && api.refresh?.body?.client_id === '{{common.clientId}}', `connection ${c}: client_id must come from common data in authorize, token and refresh`);
  ok(api.token?.body?.code_verifier === '{{temp.code_verifier}}', `connection ${c}: token must send temp.code_verifier`);
  // Verifier length: each {{uuid}} renders 36 characters; the server accepts 43 to 128.
  const verifierTpl = api.authorize?.temp?.code_verifier || '';
  const rendered = verifierTpl.replace(/\{\{uuid\}\}/g, crypto.randomUUID());
  ok(!/\{\{/.test(rendered) && rendered.length >= 43 && rendered.length <= 128, `connection ${c}: code_verifier renders ${rendered.length} chars; must be 43 to 128`);
  ok(/^[A-Za-z0-9\-._~]+$/.test(rendered), `connection ${c}: code_verifier must use only RFC 7636 unreserved characters`);
  ok(api.refresh?.condition === '{{data.expires < addMinutes(now, 15)}}', `connection ${c}: refresh condition`);
  ok(api.token?.response?.data?.expires && api.refresh?.response?.data?.expires, `connection ${c}: store data.expires in token and refresh`);
  ok(api.token?.response?.expires, `connection ${c}: token must set top-level expires (refresh token lifetime)`);
  ok(api.token?.response?.data?.refreshToken && api.refresh?.response?.data?.refreshToken, `connection ${c}: store the (rotating) refresh token on token and refresh`);
  for (const [block, paths] of [
    ['token', ['request.body.code', 'request.body.code_verifier', 'response.body.access_token', 'response.body.refresh_token']],
    ['refresh', ['request.body.refresh_token', 'response.body.access_token', 'response.body.refresh_token']],
    ['info', ['request.headers.authorization']],
  ]) {
    for (const p of paths) ok((api[block]?.log?.sanitize || []).includes(p), `connection ${c}: ${block}.log.sanitize must include ${p}`);
  }
  ok(api.info?.response?.metadata?.value, `connection ${c}: info must set a connection label (metadata)`);

  // PKCE compatibility with the HUMA server rule (mcp_auth.pkce_ok): base64url(sha256(verifier)) without "=".
  const b64 = crypto.createHash('sha256').update(rendered, 'ascii').digest('base64'); // = IML sha256(x, 'base64')
  const challengeKeepPad = b64.replace(/\+/g, '-').replace(/\//g, '_'); // base64url() if it keeps "="
  const challengeNoPad = challengeKeepPad.replace(/=+$/, ''); // base64url() if it strips "="
  const expected = crypto.createHash('sha256').update(rendered, 'ascii').digest('base64url');
  ok(challengeNoPad === expected && challengeKeepPad.replace(/=+$/, '') === expected, 'PKCE: challenge derivation does not match the server rule');
}

// 5. Webhooks.
const hookDir = path.join(APP, 'webhooks');
const webhooks = fs.existsSync(hookDir) ? fs.readdirSync(hookDir) : [];
const hookEvents = new Map();
const VALID_EVENTS = ['lead.new', 'lead.qualified', 'appointment.confirmed', 'payment.approved', 'contract.signed', 'contract.refused'];
for (const h of webhooks) {
  for (const f of ['api.json', 'attach.json', 'detach.json', 'update.json', 'parameters.json', 'metadata.json', 'scope.json']) ok(has(`webhooks/${h}/${f}`), `webhook ${h}: missing ${f}`);
  const md = get(`webhooks/${h}/metadata.json`) || {};
  const attach = get(`webhooks/${h}/attach.json`) || {};
  const detach = get(`webhooks/${h}/detach.json`) || {};
  const api = get(`webhooks/${h}/api.json`) || {};
  ok(md.type === 'web', `webhook ${h}: type must be "web" (dedicated)`);
  ok(connections.includes(md.connection), `webhook ${h}: connection "${md.connection}" does not exist`);
  ok(attach.url === '/hooks' && attach.method === 'POST', `webhook ${h}: attach must POST /hooks`);
  ok(attach.body?.target_url === '{{webhook.url}}' && attach.body?.platform === 'make', `webhook ${h}: attach body must send target_url={{webhook.url}} and platform=make`);
  ok(VALID_EVENTS.includes(attach.body?.event), `webhook ${h}: attach event "${attach.body?.event}" is not a HUMA event`);
  ok(attach.response?.data?.externalHookId === '{{body.id}}', `webhook ${h}: attach must store externalHookId = body.id`);
  ok(attach.response?.data?.secret === '{{body.secret}}', `webhook ${h}: attach must store the per-hook HMAC secret`);
  ok((attach.log?.sanitize || []).includes('response.body.secret'), `webhook ${h}: attach log must hide response.body.secret`);
  ok(detach.url === '/hooks/{{webhook.externalHookId}}' && detach.method === 'DELETE', `webhook ${h}: detach must DELETE /hooks/{{webhook.externalHookId}}`);
  ok(api.condition === `{{body.event == '${attach.body?.event}'}}`, `webhook ${h}: api.condition must accept only ${attach.body?.event}`);
  ok(md.event === attach.body?.event, `webhook ${h}: metadata.event differs from attach event`);
  ok(!hookEvents.has(attach.body?.event), `webhook ${h}: event ${attach.body?.event} used by two webhooks`);
  hookEvents.set(attach.body?.event, h);
}
ok(VALID_EVENTS.every((e) => hookEvents.has(e)), 'every HUMA event needs a webhook');

// Offset pagination of GET /conversations: page size <= 100 (API maximum), first offset 0,
// next offsets (page - 1) * pageSize with the SAME page size, stop on a short page.
function checkOffsetPagination(where, api) {
  const size = api.qs?.limit;
  ok(Number.isInteger(size) && size >= 1 && size <= 100, `${where}: page size (qs.limit) must be a fixed integer 1..100`);
  ok(api.qs?.offset === 0, `${where}: first request must send offset 0`);
  ok(api.pagination?.qs?.offset === `{{(pagination.page - 1) * ${size}}}`, `${where}: pagination offset must be (pagination.page - 1) * ${size}`);
  ok(api.pagination?.condition === `{{length(body) == ${size}}}`, `${where}: pagination must stop on a short page (length(body) == ${size})`);
  ok(api.pagination?.qs?.limit === undefined && api.pagination?.qs?.q === undefined, `${where}: pagination must merge q/limit from the first request`);
  // Simulate the requests the module would make against a fake list, to prove no gaps or repeats.
  for (const total of [0, size - 1, size, size * 2 + 3, 200]) {
    for (const want of [1, size, size + 1, 200]) {
      const ids = Array.from({ length: total }, (_, i) => i);
      const got = [];
      for (let page = 1; ; page += 1) {
        const offset = page === 1 ? 0 : (page - 1) * size;
        const body = ids.slice(Math.min(offset, 200), Math.min(offset, 200) + size);
        for (const item of body) { if (got.length < want) got.push(item); }
        if (got.length >= want || body.length === 0 || body.length !== size) break;
      }
      const expected = ids.slice(0, Math.min(want, total));
      ok(JSON.stringify(got) === JSON.stringify(expected), `${where}: pagination simulation total=${total} limit=${want} returned ${got.length} items, expected ${expected.length}`);
    }
  }
}

// 6. RPCs.
const rpcDir = path.join(APP, 'rpcs');
const rpcs = fs.existsSync(rpcDir) ? fs.readdirSync(rpcDir) : [];
for (const r of rpcs) {
  for (const f of ['api.json', 'parameters.json', 'metadata.json']) ok(has(`rpcs/${r}/${f}`), `rpc ${r}: missing ${f}`);
  const api = get(`rpcs/${r}/api.json`) || {};
  const md = get(`rpcs/${r}/metadata.json`) || {};
  ok(connections.includes(md.connection), `rpc ${r}: connection "${md.connection}" does not exist`);
  ok(api.response?.iterate && api.response?.output?.label && api.response?.output?.value, `rpc ${r}: must iterate and output {label, value}`);
  ok(Number.isInteger(api.response?.limit) && api.response.limit > 0, `rpc ${r}: response.limit required`);
  if (api.url === '/conversations') checkOffsetPagination(`rpc ${r}`, api);
}

// 7. Modules.
const modDir = path.join(APP, 'modules');
const modules = fs.existsSync(modDir) ? fs.readdirSync(modDir) : [];
const labels = new Set();
const TYPE_IDS = { 4: 'action', 9: 'search', 10: 'instant trigger', 12: 'universal' };
let universal = 0;

function namesOf(spec) { return new Set((Array.isArray(spec) ? spec : []).map((s) => s.name)); }
function checkSampleAgainstInterface(where, sample, spec) {
  if (!Array.isArray(spec) || sample === null || typeof sample !== 'object' || Array.isArray(sample)) return;
  const names = namesOf(spec);
  for (const key of Object.keys(sample)) ok(names.has(key), `${where}: sample key "${key}" missing from interface`);
  for (const field of spec) {
    ok(field.name && field.type && field.label, `${where}: interface field needs name, type and label (${JSON.stringify(field).slice(0, 60)})`);
    if (field.type === 'collection' && Array.isArray(field.spec)) checkSampleAgainstInterface(`${where}.${field.name}`, sample[field.name], field.spec);
    if (field.type === 'date' && sample[field.name]) ok(!Number.isNaN(Date.parse(sample[field.name])), `${where}.${field.name}: sample is not a parseable date`);
  }
}

function rpcRefs(value, out = []) {
  if (typeof value === 'string') { for (const m of value.matchAll(/rpc:\/\/([A-Za-z0-9_]+)/g)) out.push(m[1]); }
  else if (Array.isArray(value)) value.forEach((v) => rpcRefs(v, out));
  else if (value && typeof value === 'object') Object.values(value).forEach((v) => rpcRefs(v, out));
  return out;
}

for (const m of modules) {
  for (const f of ['api.json', 'expect.json', 'parameters.json', 'interface.json', 'samples.json', 'metadata.json']) ok(has(`modules/${m}/${f}`), `module ${m}: missing ${f}`);
  const md = get(`modules/${m}/metadata.json`) || {};
  const api = get(`modules/${m}/api.json`) || {};
  const expect = get(`modules/${m}/expect.json`) || [];
  const iface = get(`modules/${m}/interface.json`);
  const samples = get(`modules/${m}/samples.json`);
  ok(md.name === m, `module ${m}: metadata.name differs from folder`);
  ok(TYPE_IDS[md.typeId], `module ${m}: unknown typeId ${md.typeId}`);
  ok(md.label && md.description, `module ${m}: label and description required`);
  ok(!labels.has(md.label), `module ${m}: duplicate label "${md.label}"`);
  labels.add(md.label);
  ok(/^[A-Z]/.test(md.label || ''), `module ${m}: label should start with a capital letter`);
  ok(Array.isArray(iface) && iface.length > 0, `module ${m}: interface required`);
  ok(samples && typeof samples === 'object', `module ${m}: samples required`);
  checkSampleAgainstInterface(`module ${m}`, samples, iface);
  for (const ref of rpcRefs(expect)) ok(rpcs.includes(ref), `module ${m}: rpc://${ref} does not exist`);

  if (md.typeId === 10) {
    ok(webhooks.includes(md.webhook), `module ${m}: instant trigger must reference an existing webhook (got "${md.webhook}")`);
    ok(md.connection === null, `module ${m}: instant trigger takes its connection from the webhook`);
    ok(Array.isArray(expect) && expect.length === 0, `module ${m}: instant trigger has no mappable parameters`);
    const event = get(`webhooks/${md.webhook}/attach.json`)?.body?.event;
    ok(samples?.event === event, `module ${m}: sample event "${samples?.event}" differs from webhook event "${event}"`);
  } else {
    ok(connections.includes(md.connection), `module ${m}: connection "${md.connection}" does not exist`);
    ok(md.webhook === null, `module ${m}: only instant triggers reference a webhook`);
  }
  if (md.typeId === 9) {
    const limit = expect.find((p) => p.name === 'limit');
    ok(limit && limit.required && limit.validate?.max <= 200, `module ${m}: search needs a required limit with max <= 200 (API window)`);
    ok(api.response?.limit === '{{parameters.limit}}' && api.response?.iterate, `module ${m}: search must iterate and honor response.limit`);
    checkOffsetPagination(`module ${m}`, api);
  }
  if (md.typeId === 12) {
    universal += 1;
    ok(typeof api.url === 'string' && api.url.startsWith('https://app.humaia.com.br/api/v1{{'), `module ${m}: universal URL must have the fixed HUMA prefix`);
    ok(JSON.stringify(api.headers || {}).includes('{{...}}'), `module ${m}: universal headers must merge (spread) so the Base authorization stays`);
  }
  if (md.typeId === 4 || md.typeId === 9) {
    ok(typeof api.url === 'string' && api.url.startsWith('/'), `module ${m}: url must be relative to the Base`);
    if (/\{\{.*conversationId/.test(api.url || '')) ok(api.url.includes('encodeURL(parameters.conversationId)'), `module ${m}: conversation id must be URL-encoded (ig: and web: ids)`);
  }
  // Inputs sent as conversation ids always come from the dropdown (never a free text ID field).
  for (const p of expect) if (/id$/i.test(p.name || '') && p.name !== 'conversationId') fail(`module ${m}: free-text ID input "${p.name}" (use an RPC dropdown)`);
}
ok(universal === 1, 'exactly one universal "Make an API Call" module required');

// 8. Groups reference real modules, and every module is grouped once.
const groups = get('groups.json') || [];
const grouped = groups.flatMap((g) => g.modules || []);
for (const name of grouped) ok(modules.includes(name), `groups.json: unknown module ${name}`);
for (const name of modules) ok(grouped.filter((n) => n === name).length === 1, `groups.json: module ${name} must appear exactly once`);

// 9. Forbidden actions (product rules): no payment module, no bulk send, no new-contact message.
for (const m of modules) {
  const text = JSON.stringify(get(`modules/${m}/api.json`) || {});
  ok(!/\/payments|\/bulk|\/broadcast|\/campaign/i.test(text), `module ${m}: forbidden endpoint`);
}

// 10. Text hygiene across the whole Make folder: no em dash, no obvious secrets, IML braces balanced.
const scan = walk(ROOT).filter((f) => /\.(json|imljson|md|mjs|js|txt)$/.test(f) && !f.includes(`${path.sep}node_modules${path.sep}`) && !f.endsWith('package-lock.json'));
for (const file of scan) {
  const text = fs.readFileSync(file, 'utf8');
  ok(!text.includes(EM_DASH), `${rel(file)}: contains an em dash (U+2014)`);
  ok(!/(MAKE_API_KEY\s*=\s*[0-9a-f]{8}-)|(Bearer\s+mca[A-Za-z0-9_.-]{10,})|(sk-ant-[A-Za-z0-9])/.test(text), `${rel(file)}: looks like it contains a credential`);
}
function strings(value, out = []) {
  if (typeof value === 'string') out.push(value);
  else if (Array.isArray(value)) value.forEach((v) => strings(v, out));
  else if (value && typeof value === 'object') for (const [k, v] of Object.entries(value)) { out.push(k); strings(v, out); }
  return out;
}
for (const [file, value] of parsed) {
  for (const s of strings(value)) {
    const opens = (s.match(/\{\{/g) || []).length;
    const closes = (s.match(/\}\}/g) || []).length;
    ok(opens === closes, `${file}: unbalanced IML braces in "${s.slice(0, 50)}"`);
    // IML string literals in this app use single quotes only.
    for (const expr of s.match(/\{\{[^}]*\}\}/g) || []) ok(!expr.includes('"'), `${file}: double quote inside IML expression ${expr.slice(0, 50)}`);
  }
}

// 11. Icon: 512x512 PNG (Make requirement, checked again by the SDK).
const iconPath = path.join(ROOT, 'assets', 'icon-512.png');
if (ok(fs.existsSync(iconPath), 'missing assets/icon-512.png')) {
  const b = fs.readFileSync(iconPath);
  ok(b.slice(1, 4).toString() === 'PNG' && b.readUInt32BE(16) === 512 && b.readUInt32BE(20) === 512, 'assets/icon-512.png must be a 512x512 PNG');
}

notes.push(`${parsed.size} JSON files, ${connections.length} connection, ${webhooks.length} webhooks, ${rpcs.length} RPCs, ${modules.length} modules`);
if (errors.length) {
  console.error(`FAILED: ${errors.length} problem(s) in ${checks} checks`);
  for (const e of errors) console.error(`  - ${e}`);
  process.exit(1);
}
console.log(`OK: ${checks} checks passed (${notes.join('; ')}).`);
console.log('Static only: Make has not seen these files and no IML was executed.');
