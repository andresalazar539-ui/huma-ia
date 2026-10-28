// scripts/cockpit_smoke/run.js — simulador FIEL do Cockpit (2026-10-12)
//
// Sobe um servidor local com o Cockpit.html e os .jsx do repositório, abre a
// página num jsdom que executa o MESMO Babel standalone do navegador (os
// scripts de CDN vêm da rede, como no navegador) e abre cada tela do app
// (?screen=…). Falha se algum arquivo quebrar na carga, se o React cair
// (tela em branco) ou se a tela vier vazia.
//
// Uso:  cd scripts/cockpit_smoke && npm install && node run.js [tela ...]
//       (sem argumentos testa todas as telas; precisa de internet pros CDNs)
// Por que fiel e não um transformador próprio: o Babel standalone injeta um
// <script> clássico por arquivo no mesmo escopo global; um simulador com
// `vm` reprovou colisões que o navegador tolera. A verdade é o navegador.
const fs = require('fs');
const http = require('http');
const path = require('path');
const { JSDOM, VirtualConsole } = require('jsdom');

const STATIC = path.resolve(__dirname, '../../huma/static');
const COCKPIT = path.join(STATIC, 'cockpit', 'Cockpit.html');
const ROUTES = [...fs.readFileSync(COCKPIT, 'utf8').matchAll(/case '([a-z]+)':\s+screen =/g)].map(m => m[1]);
// "HUMA por dentro" mora fora do Cockpit, em /admin (Admin.html); entra como tela 'admin'.
const ADMIN = path.join(STATIC, 'cockpit', 'Admin.html');
for (const sec of ['', 'contas', 'custos', 'trilha', 'pedidos', 'acesso', 'sistema']) ROUTES.push(sec ? `admin:${sec}` : 'admin');
const only = process.argv.slice(2);
const screens = only.length ? only : ROUTES;

const INJECT = '<script>' + [
  'window.HUMA_CLIENT_ID="cli_x"', 'window.HUMA_ROLE="dono"', 'window.HUMA_EMAIL="dona@x.com"',
  'window.HUMA_IS_SUPERADMIN=true', 'window.HUMA_IS_SUBSCRIBER=true',
  'window.HUMA_PERMS=["conversas","relatorios","faturamento","disparos","ajustes","equipe"]', 'window.HUMA_SCREEN_PERMS={}',
].join(';') + ';</script>';

const MIME = { '.html': 'text/html', '.jsx': 'text/plain', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.json': 'application/json' };
const server = http.createServer((req, res) => {
  const u = new URL(req.url, 'http://x');
  if (u.pathname === '/admin') {
    const html = fs.readFileSync(ADMIN, 'utf8').replace(/__ASSET_V__/g, 'smoke').replace('<head>', '<head>' + INJECT);
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }); return res.end(html);
  }
  if (u.pathname === '/cockpit') {
    const html = fs.readFileSync(COCKPIT, 'utf8').replace(/__ASSET_V__/g, 'smoke').replace('<head>', '<head>' + INJECT);
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }); return res.end(html);
  }
  if (u.pathname.startsWith('/static/')) {
    const p = path.join(STATIC, u.pathname.slice('/static/'.length));
    if (fs.existsSync(p) && fs.statSync(p).isFile()) {
      res.writeHead(200, { 'content-type': MIME[path.extname(p)] || 'application/octet-stream' }); return res.end(fs.readFileSync(p));
    }
  }
  if (u.pathname.startsWith('/api/') || u.pathname.startsWith('/whatsapp/') || u.pathname.startsWith('/instagram/') || u.pathname === '/auth/session') {
    res.writeHead(200, { 'content-type': 'application/json' });
    // Resposta "superconjunto": campos que as telas leem direto (sem opcional)
    // pra nenhuma delas cair por formato. Lista vazia = tela no estado inicial.
    return res.end(JSON.stringify({
      status: 'ok', items: [], authenticated: true, available: true, url: 'https://app.humaia.com.br/mcp',
      official: true, max_rows: 10000, price_brl: 0.32, ready: true, has_owner_phone: true, daily_cap: 0,
      gate: { official: true, max_rows: 10000, price_brl: 0.32, trial_sample: 20 }, reactivations: [], team: [],
      plays: [], intensities: [], config: { hour_start: 9, hour_end: 18, intensity: 'padrao', weekend: false },
      summary: {}, summary_days: 7, category: '', custom_window: null,
      periodo: { de: '2026-09-13', ate: '2026-10-12' }, anterior: null, clientes: [], em_risco: [], fontes: { truncado: false },
      mrr: {}, receita_acumulada: {}, top_receita: [], top_custo_ia: [], top_acumulado: [], active_custom: 0, max_active: 3,
    }));
  }
  res.writeHead(404); res.end('');
});

function stubs(window, base) {
  // jsdom não tem fetch: usa o do Node contra o servidor local (que responde JSON vazio pra /api/*)
  window.fetch = (u, o) => fetch(new URL(String(u), base).toString(), o);
  window.matchMedia = () => ({ matches: false, media: '', addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } });
  window.scrollTo = () => {};
  window.HTMLElement.prototype.scrollIntoView = () => {};
  if (!window.ResizeObserver) window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  if (!window.IntersectionObserver) window.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
}

async function openScreen(base, screen) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errors.push(String(e.message || e).split('\n')[0]));
  vc.on('error', (...a) => errors.push(a.map(String).join(' ').split('\n')[0]));
  const dom = await JSDOM.fromURL(screen.startsWith('admin') ? `${base}/admin?section=${screen.split(':')[1] || ''}` : `${base}/cockpit?screen=${screen}`, {
    runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc, beforeParse: w => stubs(w, base),
  });
  const w = dom.window;
  const t0 = Date.now();
  let root = '';
  while (Date.now() - t0 < 40000) {
    await new Promise(r => setTimeout(r, 500));
    root = (w.document.getElementById('root') || {}).innerHTML || '';
    const fatal = errors.some(e => /above error occurred|Uncaught|not defined|is not a function|SyntaxError/i.test(e));
    if (fatal) break;
    if (root.length > 1500 && Date.now() - t0 > 6000) break;
  }
  const fatal = errors.filter(e => /above error occurred|Uncaught|not defined|is not a function|SyntaxError|already been declared/i.test(e));
  w.close();
  return { root: root.length, errors: fatal, allErrors: errors };
}

(async () => {
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  let bad = 0;
  for (const s of screens) {
    const r = await openScreen(base, s);
    const ok = r.root > 1500 && r.errors.length === 0;
    if (!ok) bad++;
    console.log(`${ok ? 'ok  ' : 'ERRO'} ${s.padEnd(12)} root=${String(r.root).padStart(6)} chars${r.errors.length ? ' | ' + r.errors.slice(0, 3).join(' | ').slice(0, 300) : ''}`);
  }
  server.close();
  if (bad) { console.error(bad, 'tela(s) com problema'); process.exit(1); }
  console.log('Cockpit ok:', screens.length, 'telas');
})().catch(e => { console.error('falhou', e); process.exit(1); });
