// scripts/cockpit_smoke/prod_check.js — depois do deploy: abre o Cockpit de
// PRODUÇÃO num jsdom executando o Babel standalone de verdade (mesmo caminho
// do navegador) e diz quais telas ficaram definidas e o que quebrou na carga.
// Uso: node prod_check.js [url]   (sem sessão: só a carga dos scripts, sem dados)
const { JSDOM, VirtualConsole } = require('jsdom');
const url = process.argv[2] || 'https://app.humaia.com.br/cockpit';
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', e => errors.push(String(e.message || e).split('\n')[0]));
vc.on('error', (...a) => errors.push(a.map(String).join(' ').split('\n')[0]));
(async () => {
  const dom = await JSDOM.fromURL(url, { runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc, beforeParse(w) {
    w.matchMedia = () => ({ matches: false, media: '', addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } });
    w.scrollTo = () => {};
    w.fetch = (u, o) => fetch(new URL(String(u), url).toString(), o); // sem sessão as APIs respondem 401: a carga dos scripts é o que importa
  } });
  const w = dom.window;
  await new Promise(r => setTimeout(r, 25000));
  const names = ['IntegrationsScreen', 'AssistantsModal', 'ReportsScreen', 'AgendaFullScreen', 'AdminScreen', 'InicioScreen', 'ConversationList', 'VendasScreen', 'FollowUpScreen', 'ReativacaoScreen', 'SobMedidaScreen', 'EquipeScreen'];
  for (const n of names) console.log((typeof w[n] === 'function' ? 'ok   ' : 'FALTA') + ' ' + n);
  console.log('--- erros de console/carga:', errors.length);
  for (const e of errors.slice(0, 25)) console.log('  ', e.slice(0, 220));
  w.close();
})().catch(e => { console.error('falhou', e); process.exit(1); });
