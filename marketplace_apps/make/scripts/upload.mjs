#!/usr/bin/env node
// Uploads the HUMA IA app definition (../app) to Make as a PRIVATE custom app, then reads it back.
// Management tooling that runs on your computer; it is not Make app runtime code.
//
//   node scripts/upload.mjs          plan only: lists what would be uploaded, no network call
//   node scripts/upload.mjs --live   creates or updates the private app "humaia" in Make
//   node scripts/upload.mjs --mock   runs the full live sequence against an in-memory fake (offline self-test)
//
// Reads MAKE_API_KEY, MAKE_ZONE and MAKE_APP_AUDIENCE from ../.env.local (or the environment).
// Never prints their values. Never publishes, never makes modules public, never creates a connection,
// never runs a scenario, never calls HUMA. Automatic retries are off: after an error, read the report
// and check the app in Make before running again.

import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { isDeepStrictEqual } from 'node:util';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const APP = join(ROOT, 'app');
const ZONE_RE = /^[a-z0-9-]+\.make\.(?:com|celonis\.com)$/;
const MOCK = process.argv.includes('--mock');
const LIVE = process.argv.includes('--live') || MOCK;
const ALLOW_PUBLIC = process.argv.includes('--allow-public-app');

// ---------------------------------------------------------------- local sources

const readText = (rel) => readFileSync(join(APP, rel), 'utf8');
const readJson = (rel) => JSON.parse(readText(rel));
const listDirs = (rel) => {
  const dir = join(APP, rel);
  if (!existsSync(dir)) return [];
  return readdirSync(dir, { withFileTypes: true }).filter((e) => e.isDirectory()).map((e) => e.name).sort();
};

function loadPlan() {
  const meta = readJson('metadata.json');
  const connections = listDirs('connections').map((name) => ({
    local: name,
    meta: readJson(`connections/${name}/metadata.json`),
    common: readJson(`connections/${name}/common.json`),
    sections: {
      api: readText(`connections/${name}/api.json`),
      parameters: readText(`connections/${name}/parameters.json`),
      scope: JSON.stringify(readJson(`connections/${name}/metadata.json`).scope ?? []),
      scopes: JSON.stringify(readJson(`connections/${name}/metadata.json`).scopes ?? {}),
    },
  }));
  const webhooks = listDirs('webhooks').map((name) => ({
    local: name,
    meta: readJson(`webhooks/${name}/metadata.json`),
    sections: {
      api: readText(`webhooks/${name}/api.json`),
      parameters: readText(`webhooks/${name}/parameters.json`),
      attach: readText(`webhooks/${name}/attach.json`),
      detach: readText(`webhooks/${name}/detach.json`),
      scope: readText(`webhooks/${name}/scope.json`),
    },
  }));
  const rpcs = listDirs('rpcs').map((name) => ({
    local: name,
    meta: readJson(`rpcs/${name}/metadata.json`),
    sections: {
      api: readText(`rpcs/${name}/api.json`),
      parameters: readText(`rpcs/${name}/parameters.json`),
    },
  }));
  const modules = listDirs('modules').map((name) => ({
    local: name,
    meta: readJson(`modules/${name}/metadata.json`),
    sections: {
      api: readText(`modules/${name}/api.json`),
      parameters: readText(`modules/${name}/parameters.json`),
      expect: readText(`modules/${name}/expect.json`),
      interface: readText(`modules/${name}/interface.json`),
      samples: readText(`modules/${name}/samples.json`),
    },
  }));
  return {
    meta,
    appSections: {
      base: readText('base.imljson'),
      groups: readText('groups.json'),
      install: readText('install.json'),
      installSpec: readText('installSpec.json'),
    },
    docs: readText('readme.md'),
    icon: readFileSync(join(ROOT, 'assets', 'icon-512.png')),
    connections,
    webhooks,
    rpcs,
    modules,
  };
}

// ---------------------------------------------------------------- env (.env.local, no dependency)

function loadEnv() {
  const env = { ...process.env };
  const file = join(ROOT, '.env.local');
  if (existsSync(file)) {
    for (const raw of readFileSync(file, 'utf8').split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith('#') || !line.includes('=')) continue;
      const i = line.indexOf('=');
      const key = line.slice(0, i).trim();
      let value = line.slice(i + 1).trim();
      if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1, -1);
      if (!(key in process.env)) env[key] = value;
    }
  }
  return env;
}

// ---------------------------------------------------------------- report helpers

const steps = [];
class CheckFailure extends Error {}

function describe(error) {
  if (error instanceof CheckFailure) return error.message;
  const status = typeof error?.statusCode === 'number' ? error.statusCode : undefined;
  // Never print the message body: it can echo section source or credentials.
  if (status === undefined) return `request failed without an HTTP status (${error?.name || 'error'}). Outcome uncertain: check the app in Make before running again.`;
  const meaning = status === 400 || status === 422 ? 'rejected as invalid'
    : status === 401 ? 'authentication rejected (check MAKE_API_KEY and MAKE_ZONE)'
    : status === 403 ? 'permission denied (token needs sdk-apps:read and sdk-apps:write)'
    : status === 404 ? 'not found'
    : status === 409 ? 'conflict (already exists?)'
    : status === 429 ? 'rate limited, not retried'
    : status >= 500 ? 'Make server error. Outcome uncertain: check the app in Make before running again'
    : 'unexpected response';
  const sub = Array.isArray(error?.subErrors) && error.subErrors.length ? ` Details: ${error.subErrors.slice(0, 3).join(' | ').slice(0, 300)}` : '';
  return `HTTP ${status}: ${meaning}.${sub}`;
}

async function step(name, action) {
  try {
    const detail = await action();
    steps.push({ name, status: 'ok', detail: typeof detail === 'string' ? detail : '' });
    return true;
  } catch (error) {
    steps.push({ name, status: 'FAILED', detail: describe(error) });
    return false;
  }
}

// Make may return a section as parsed JSON or as JSONC text. Compare meaning, not bytes.
function sameSection(expectedText, actual) {
  const expected = JSON.parse(expectedText);
  if (typeof actual === 'string') {
    if (actual === expectedText) return true;
    try { return isDeepStrictEqual(JSON.parse(actual), expected); } catch { return false; }
  }
  if (actual === null || actual === undefined) return expected === null;
  return isDeepStrictEqual(actual, expected);
}

async function writeAndReadBack(label, write, read, expectedText) {
  if (!(await step(`${label} write`, write))) return false;
  return step(`${label} readback`, async () => {
    const actual = await read();
    if (!sameSection(expectedText, actual)) throw new CheckFailure('readback differs from the local file');
  });
}

function printReport(mode, extra = []) {
  console.log(`\nMode: ${mode}`);
  for (const line of extra) console.log(line);
  for (const s of steps) console.log(`  ${s.status.padEnd(6)} ${s.name}${s.detail ? `: ${s.detail}` : ''}`);
  const failed = steps.filter((s) => s.status !== 'ok').length;
  console.log(failed ? `\nResult: ${failed} step(s) FAILED. Nothing is rolled back. Fix and run again (the script updates what exists).` : '\nResult: all steps passed.');
  console.log(MOCK
    ? 'Evidence: script sequence against an in-memory fake only. Make was not contacted.'
    : 'Evidence: readback equality only. The app is PRIVATE and unpublished. No connection was created, no scenario ran, HUMA was not called.');
  return failed ? 1 : 0;
}

// ---------------------------------------------------------------- plan mode

const plan = loadPlan();

if (!LIVE) {
  console.log(`Plan for app "${plan.meta.name}" (${plan.meta.label}), private:`);
  console.log(`  app sections: ${Object.keys(plan.appSections).join(', ')} + docs + icon`);
  for (const c of plan.connections) console.log(`  connection "${c.meta.label}" (${c.meta.type}): ${Object.keys(c.sections).join(', ')} + common`);
  for (const w of plan.webhooks) console.log(`  webhook "${w.meta.label}" (${w.meta.type}, event ${w.meta.event}): ${Object.keys(w.sections).join(', ')}`);
  for (const r of plan.rpcs) console.log(`  rpc ${r.local}: ${Object.keys(r.sections).join(', ')}`);
  for (const m of plan.modules) console.log(`  module ${m.local} (typeId ${m.meta.typeId}${m.meta.webhook ? `, webhook ${m.meta.webhook}` : ''}): ${Object.keys(m.sections).join(', ')}`);
  console.log('\nNo network call was made. Run with --live to upload.');
  process.exit(0);
}

// ---------------------------------------------------------------- live mode

const env = MOCK
  ? { MAKE_API_KEY: 'mock', MAKE_ZONE: 'eu1.make.com', MAKE_APP_AUDIENCE: 'mock-audience' }
  : loadEnv();
const problems = [];
if (!env.MAKE_API_KEY) problems.push('MAKE_API_KEY is missing (put it in marketplace_apps/make/.env.local).');
if (!env.MAKE_ZONE) problems.push('MAKE_ZONE is missing (for example us2.make.com or eu1.make.com).');
else if (!ZONE_RE.test(env.MAKE_ZONE)) problems.push('MAKE_ZONE must be a bare hostname such as eu1.make.com (no https://, no /api/v2).');
if (!env.MAKE_APP_AUDIENCE) problems.push('MAKE_APP_AUDIENCE is missing (use the value written in README.md, normally global).');
if (problems.length) {
  for (const p of problems) console.error(p);
  process.exit(2);
}

const { Make } = MOCK ? await import('./mock-make.mjs') : await import('@makehq/sdk');
const make = new Make(env.MAKE_API_KEY, env.MAKE_ZONE, { retry: { maxRetries: 0, onRateLimit: false, onServerError: false } });
const sdk = make.sdk;
const APP_NAME = plan.meta.name;
let version;
const remote = { connections: {}, webhooks: {} };

async function run() {
  // Identity and scopes.
  if (!(await step('make.authorization', async () => {
    const auth = await make.users.currentAuthorization();
    const scope = Array.isArray(auth?.scope) ? auth.scope : [];
    const missing = ['sdk-apps:read', 'sdk-apps:write'].filter((s) => !scope.includes(s));
    if (missing.length) throw new CheckFailure(`API token is missing scopes: ${missing.join(', ')}`);
    return `zone ${env.MAKE_ZONE}`;
  }))) return;

  // App: find or create (private).
  if (!(await step('app.findOrCreate', async () => {
    const apps = await sdk.apps.list({ cols: ['name', 'version', 'public', 'label'] });
    const found = (apps || []).filter((a) => a.name === APP_NAME).sort((a, b) => b.version - a.version)[0];
    if (found) {
      version = found.version;
      if (found.public === true && !ALLOW_PUBLIC) {
        throw new CheckFailure('the app is already PUBLISHED; changes would reach users immediately. Re-run with --allow-public-app only if you really mean it.');
      }
      await sdk.apps.update(APP_NAME, version, {
        label: plan.meta.label, description: plan.meta.description, theme: plan.meta.theme, language: plan.meta.language,
      });
      return `existing app, version ${version}`;
    }
    const created = await sdk.apps.create({
      name: APP_NAME,
      label: plan.meta.label,
      description: plan.meta.description,
      theme: plan.meta.theme,
      language: plan.meta.language,
      private: true,
      audience: env.MAKE_APP_AUDIENCE,
    });
    if (created?.name !== APP_NAME) throw new CheckFailure('Make acknowledged a different app name');
    version = created.version;
    if (created.public === true) throw new CheckFailure('Make reported the new app as public; stopped before writing any section');
    return `created, version ${version}`;
  }))) return;

  if (!(await step('app.verifyPrivateOrAllowed', async () => {
    const full = await sdk.apps.get(APP_NAME, version, { cols: ['name', 'version', 'public'] });
    if (full?.name !== APP_NAME || full?.version !== version) throw new CheckFailure('full GET describes another app or version');
    if (full.public !== false && !ALLOW_PUBLIC) throw new CheckFailure('full GET did not confirm the app is private; no section written');
    return full.public === false ? 'private confirmed' : 'public app, update allowed by flag';
  }))) return;

  // App sections, docs, icon.
  for (const [section, text] of Object.entries(plan.appSections)) {
    await writeAndReadBack(`app.${section}`,
      () => sdk.apps.setSection(APP_NAME, version, section, text),
      () => sdk.apps.getSection(APP_NAME, version, section), text);
  }
  await step('app.docs write+readback', async () => {
    await sdk.apps.setDocs(APP_NAME, version, plan.docs);
    const back = await sdk.apps.getDocs(APP_NAME, version);
    if (String(back ?? '').trim() !== plan.docs.trim()) throw new CheckFailure('docs readback differs');
  });
  await step('app.icon', async () => { await sdk.apps.setIcon(APP_NAME, version, new Uint8Array(plan.icon)); });

  // Connections (app-global; Make picks the name, we match by label).
  const existingConnections = await safeList(() => sdk.connections.list(APP_NAME), 'connections.list');
  for (const c of plan.connections) {
    const ok = await step(`connection "${c.meta.label}" findOrCreate`, async () => {
      const found = (existingConnections || []).find((x) => x.label === c.meta.label);
      if (found) {
        if (found.type && found.type !== c.meta.type) throw new CheckFailure(`existing connection has type ${found.type}, expected ${c.meta.type}`);
        remote.connections[c.local] = found.name;
        return `existing ${found.name}`;
      }
      const created = await sdk.connections.create(APP_NAME, { type: c.meta.type, label: c.meta.label });
      remote.connections[c.local] = created.name;
      return `created ${created.name}`;
    });
    if (!ok) return;
    const name = remote.connections[c.local];
    for (const [section, text] of Object.entries(c.sections)) {
      await writeAndReadBack(`connection.${section}`,
        () => sdk.connections.setSection(name, section, text),
        () => sdk.connections.getSection(name, section), text);
    }
    await step('connection.common write+readback', async () => {
      await sdk.connections.setCommon(name, c.common);
      const back = await sdk.connections.getCommon(name);
      if (back?.clientId !== c.common.clientId) throw new CheckFailure('common clientId readback differs');
    });
  }

  // Webhooks (app-global; matched by label; bound to the connection).
  const existingHooks = await safeList(() => sdk.webhooks.list(APP_NAME), 'webhooks.list');
  for (const w of plan.webhooks) {
    const connection = remote.connections[w.meta.connection];
    const ok = await step(`webhook "${w.meta.label}" findOrCreate`, async () => {
      if (!connection) throw new CheckFailure(`connection ${w.meta.connection} was not resolved`);
      const found = (existingHooks || []).find((x) => x.label === w.meta.label);
      if (found) {
        remote.webhooks[w.local] = found.name;
        await sdk.webhooks.update(found.name, { label: w.meta.label, connection });
        return `existing ${found.name}`;
      }
      const created = await sdk.webhooks.create(APP_NAME, { type: w.meta.type, label: w.meta.label, connection });
      if (!created?.name) throw new CheckFailure('Make did not return the webhook name');
      remote.webhooks[w.local] = created.name;
      return `created ${created.name}`;
    });
    if (!ok) continue;
    const name = remote.webhooks[w.local];
    for (const [section, text] of Object.entries(w.sections)) {
      await writeAndReadBack(`webhook ${w.local}.${section}`,
        () => sdk.webhooks.setSection(name, section, text),
        () => sdk.webhooks.getSection(name, section), text);
    }
    await step(`webhook ${w.local} binding`, async () => {
      const full = await sdk.webhooks.get(name);
      if (full?.type && full.type !== w.meta.type) throw new CheckFailure(`type is ${full.type}, expected ${w.meta.type}`);
      if (full?.connection === undefined) return 'connection binding not reported by Make (unknown, check in the editor)';
      if (full.connection !== connection) throw new CheckFailure(`bound to "${full.connection}", expected "${connection}"`);
    });
  }

  // RPCs (versioned, our names).
  const existingRpcs = await safeList(() => sdk.rpcs.list(APP_NAME, version), 'rpcs.list');
  for (const r of plan.rpcs) {
    const connection = remote.connections[r.meta.connection];
    const ok = await step(`rpc ${r.local} createOrUpdate`, async () => {
      if (!connection) throw new CheckFailure(`connection ${r.meta.connection} was not resolved`);
      if ((existingRpcs || []).some((x) => x.name === r.local)) {
        await sdk.rpcs.update(APP_NAME, version, r.local, { label: r.meta.label, connection });
        return 'updated';
      }
      await sdk.rpcs.create(APP_NAME, version, { name: r.local, label: r.meta.label, connection });
      // Make documents setting the connection after creation; repeat it explicitly.
      await sdk.rpcs.update(APP_NAME, version, r.local, { connection });
      return 'created';
    });
    if (!ok) continue;
    for (const [section, text] of Object.entries(r.sections)) {
      await writeAndReadBack(`rpc ${r.local}.${section}`,
        () => sdk.rpcs.setSection(APP_NAME, version, r.local, section, text),
        () => sdk.rpcs.getSection(APP_NAME, version, r.local, section), text);
    }
    await step(`rpc ${r.local} binding`, async () => {
      const full = await sdk.rpcs.get(APP_NAME, version, r.local);
      if (full?.connection === undefined) return 'connection binding not reported by Make (unknown)';
      if (full.connection !== connection) throw new CheckFailure(`bound to "${full.connection}", expected "${connection}"`);
    });
  }

  // Modules (versioned, our names). Instant triggers bind to a webhook; the rest to the connection.
  const existingModules = await safeList(() => sdk.modules.list(APP_NAME, version), 'modules.list');
  for (const m of plan.modules) {
    const connection = m.meta.connection ? remote.connections[m.meta.connection] : null;
    const webhook = m.meta.webhook ? remote.webhooks[m.meta.webhook] : null;
    const ok = await step(`module ${m.local} createOrUpdate`, async () => {
      if (m.meta.connection && !connection) throw new CheckFailure(`connection ${m.meta.connection} was not resolved`);
      if (m.meta.webhook && !webhook) throw new CheckFailure(`webhook ${m.meta.webhook} was not resolved`);
      const found = (existingModules || []).find((x) => x.name === m.local);
      if (found) {
        if (found.typeId !== undefined && found.typeId !== m.meta.typeId) {
          throw new CheckFailure(`exists with typeId ${found.typeId}, expected ${m.meta.typeId}; Make cannot change a module type. Delete it in Make (private app only) and run again.`);
        }
        const body = { label: m.meta.label, description: m.meta.description };
        if (connection) body.connection = connection;
        await sdk.modules.update(APP_NAME, version, m.local, body);
        return 'updated';
      }
      const body = {
        name: m.local,
        typeId: m.meta.typeId,
        label: m.meta.label,
        description: m.meta.description,
        moduleInitMode: 'blank',
      };
      // The Make API accepts connection, webhook and crud on module creation (not all are in the SDK types).
      if (connection) body.connection = connection;
      if (webhook) body.webhook = webhook;
      if (m.meta.crud) body.crud = m.meta.crud;
      const created = await sdk.modules.create(APP_NAME, version, body);
      if (created?.name && created.name !== m.local) throw new CheckFailure('Make acknowledged a different module name');
      return 'created';
    });
    if (!ok) continue;
    for (const [section, text] of Object.entries(m.sections)) {
      await writeAndReadBack(`module ${m.local}.${section}`,
        () => sdk.modules.setSection(APP_NAME, version, m.local, section, text),
        () => sdk.modules.getSection(APP_NAME, version, m.local, section), text);
    }
    await step(`module ${m.local} verify`, async () => {
      const full = await sdk.modules.get(APP_NAME, version, m.local);
      if (full?.typeId !== m.meta.typeId) throw new CheckFailure(`typeId is ${full?.typeId}, expected ${m.meta.typeId}`);
      const notes = [];
      if (connection) {
        if (full.connection === undefined) notes.push('connection binding not reported');
        else if (full.connection !== connection) throw new CheckFailure(`bound to connection "${full.connection}", expected "${connection}"`);
      }
      if (webhook) {
        if (full.webhook === undefined) notes.push('webhook binding not reported');
        else if (full.webhook !== webhook) throw new CheckFailure(`bound to webhook "${full.webhook}", expected "${webhook}". Open the module in the Make editor and select the webhook "${m.meta.webhook}".`);
      }
      if (full.public === true) notes.push('module is visible (public)');
      return notes.join('; ');
    });
  }

  // Final counts.
  await step('counts', async () => {
    const [mods, rp, hooks, conns] = await Promise.all([
      sdk.modules.list(APP_NAME, version), sdk.rpcs.list(APP_NAME, version), sdk.webhooks.list(APP_NAME), sdk.connections.list(APP_NAME),
    ]);
    const missingModules = plan.modules.filter((m) => !(mods || []).some((x) => x.name === m.local)).map((m) => m.local);
    const missingRpcs = plan.rpcs.filter((r) => !(rp || []).some((x) => x.name === r.local)).map((r) => r.local);
    const missingHooks = plan.webhooks.filter((w) => !(hooks || []).some((x) => x.name === remote.webhooks[w.local])).map((w) => w.local);
    if (missingModules.length || missingRpcs.length || missingHooks.length) {
      throw new CheckFailure(`missing in Make: modules [${missingModules}] rpcs [${missingRpcs}] webhooks [${missingHooks}]`);
    }
    const extras = (mods || []).filter((x) => !plan.modules.some((m) => m.local === x.name)).map((x) => x.name);
    return `modules ${mods.length}, rpcs ${rp.length}, webhooks ${hooks.length}, connections ${conns.length}${extras.length ? `; extra modules in Make not in files: ${extras.join(', ')}` : ''}`;
  });
}

async function safeList(fn, label) {
  let out = [];
  await step(label, async () => { out = (await fn()) || []; return `${out.length} found`; });
  return out;
}

await run();
const mapping = [
  ...Object.entries(remote.connections).map(([l, r]) => `  connection ${l} -> ${r}`),
  ...Object.entries(remote.webhooks).map(([l, r]) => `  webhook ${l} -> ${r}`),
];
process.exitCode = printReport(MOCK ? 'mock (in-memory fake, no network)' : 'live (Make API)', [`App: ${APP_NAME}${version ? ` version ${version}` : ''}`, ...(mapping.length ? ['Remote names:', ...mapping] : [])]);
