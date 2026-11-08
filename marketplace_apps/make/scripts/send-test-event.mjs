#!/usr/bin/env node
// Sends the EXAMPLE payload of one HUMA IA event to a Make webhook URL, exactly as HUMA delivers it.
// Use it only for the review test scenarios of events that are hard to cause for real
// (payment.approved, contract.signed, contract.refused). The data is fictitious; no personal data.
//
//   node scripts/send-test-event.mjs <make-webhook-url> <event>
//   events: lead.new | lead.qualified | appointment.confirmed | payment.approved | contract.signed | contract.refused
//
// Where is the URL? In Make, open the HUMA IA trigger in the scenario: the webhook shows its address
// (or Webhooks menu > the HUMA IA webhook > Copy address).

import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';

const APP = join(dirname(fileURLToPath(import.meta.url)), '..', 'app');
const [url, event] = process.argv.slice(2);

const byEvent = {};
for (const dir of readdirSync(join(APP, 'webhooks'))) {
  const meta = JSON.parse(readFileSync(join(APP, 'webhooks', dir, 'metadata.json'), 'utf8'));
  byEvent[meta.event] = dir;
}
const moduleFor = {};
for (const dir of readdirSync(join(APP, 'modules'))) {
  const meta = JSON.parse(readFileSync(join(APP, 'modules', dir, 'metadata.json'), 'utf8'));
  if (meta.webhook) moduleFor[meta.webhook] = dir;
}

if (!url || !event || !byEvent[event]) {
  console.error('Usage: node scripts/send-test-event.mjs <make-webhook-url> <event>');
  console.error(`Events: ${Object.keys(byEvent).sort().join(' | ')}`);
  process.exit(2);
}
let parsed;
try { parsed = new URL(url); } catch { parsed = null; }
if (!parsed || parsed.protocol !== 'https:' || !/(^|\.)make\.com$|(^|\.)make\.celonis\.com$/.test(parsed.hostname)) {
  console.error('The URL must be an https Make webhook address (for example https://hook.eu2.make.com/...).');
  process.exit(2);
}

const payload = JSON.parse(readFileSync(join(APP, 'modules', moduleFor[byEvent[event]], 'samples.json'), 'utf8'));
payload.id = `evt_teste_${randomUUID().slice(0, 8)}`;
payload.at = new Date().toISOString();

const res = await fetch(url, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'User-Agent': 'HUMA-IA-Automacao/1.0',
    'X-HUMA-Event': event,
    'X-HUMA-Hook-Id': 'ahk_teste',
  },
  body: JSON.stringify(payload),
});
console.log(`Sent ${event} (example data) to Make: HTTP ${res.status}`);
process.exit(res.ok ? 0 : 1);
