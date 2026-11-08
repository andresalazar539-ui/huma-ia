// Lista os apps privados da conta Make (nome real que a Make deu, versão, público?).
// Lê o token de .env.local; nunca imprime o token.
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Make } from '@makehq/sdk';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const env = Object.fromEntries(
  readFileSync(join(ROOT, '.env.local'), 'utf8').split(/\r?\n/)
    .filter((l) => l && !l.startsWith('#') && l.includes('='))
    .map((l) => [l.slice(0, l.indexOf('=')).trim(), l.slice(l.indexOf('=') + 1).trim()]),
);
const make = new Make(env.MAKE_API_KEY, env.MAKE_ZONE);
const apps = await make.sdk.apps.list({ cols: ['name', 'version', 'public', 'label', 'approved'] });
console.log(JSON.stringify(apps, null, 2));
