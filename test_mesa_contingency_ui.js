// Execute the actual renderers offline: clocks, localStorage, expiry and +EV safety.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const boardSource = fs.readFileSync(path.join(__dirname, 'valor/js/board.js'), 'utf8');
const valueSource = fs.readFileSync(path.join(__dirname, 'valor/js/valor.js'), 'utf8');
const linesSource = fs.readFileSync(path.join(__dirname, 'valor/js/lines.js'), 'utf8');
const opsSource = fs.readFileSync(path.join(__dirname, 'valor/js/ops.js'), 'utf8');
const BASE = Date.parse('2026-09-30T13:00:00Z');
const MIN = 60000;
const iso = t => new Date(t).toISOString();
let checks = 0;

class Element {
  constructor() {
    this.children = []; this.attrs = {}; this.style = {}; this.queries = {};
    this.hidden = false;
    this.classList = { toggle() {}, add() {}, remove() {}, contains() { return false; } };
  }
  set innerHTML(value) { this.html = value; this.children = []; }
  get innerHTML() { return this.html || ''; }
  appendChild(element) { this.children.push(element); return element; }
  setAttribute(key, value) { this.attrs[key] = value; }
  getAttribute(key) { return this.attrs[key]; }
  querySelector(query) { return this.queries[query] ||= new Element(); }
  querySelectorAll() { return []; }
}

function fixture() {
  const captured = iso(BASE - 10 * MIN), expires = iso(BASE + 110 * MIN);
  return {
    mode: 'odds_only', gerado_iso: captured, rebuilt_at: iso(BASE),
    contingency: { expires_at: expires, max_age_minutes: 120 },
    model: { status: 'unavailable', source: 'candidate_pricer' },
    mercados: ['Faltas'], casas: ['Superbet'],
    jogos: [{
      jogo: 'Casa X — Fora Y', home: 'Casa X', away: 'Fora Y', liga: 'Liga de teste',
      inicio: '01/10 10:00', inicio_iso: iso(BASE + 24 * 60 * MIN),
      captured_at: captured, expires_at: expires,
      mercados: { Faltas: { Superbet: [{ linha: 23.5, over: 1.9, under: 1.9 }] } },
      // Deliberately poisoned payload: frontend must not trust an accidental old signal.
      valor: [{ mercado: 'Faltas', casa: 'Superbet', linha: 23.5, lado: 'Mais', odd: 1.9, ev_pct: 88 }],
      tem_valor: true
    }]
  };
}

function harness(board = fixture()) {
  let now = BASE, saved = null;
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  const elements = Object.fromEntries([
    'filtros', 'lista', 'meta', 'boardage', 'capstatus', 'contingency-banner',
    'model-badge', 'view-board', 'view-valor', 'mesa-tagline', 'view-lines', 'view-ops'
  ].map(id => [id, new Element()]));
  const queries = {}, timers = [], events = {};
  const document = {
    hidden: false,
    getElementById: id => elements[id] || null,
    createElement: () => new Element(),
    querySelector: query => queries[query] ||= new Element(),
    addEventListener: (name, cb) => { events[name] = cb; }
  };
  const context = {
    Date: Clock, console, document,
    window: {
      BOARD: board, setInterval: cb => timers.push(cb), scrollTo() {},
      addEventListener: (name, cb) => { events[name] = cb; }
    },
    localStorage: {
      getItem: () => JSON.stringify({ mercado: 'Faltas', casa: 'Superbet', ordem: 'valor', soValor: true }),
      setItem: (_, text) => { saved = JSON.parse(text); }
    }
  };
  vm.runInNewContext(boardSource, context);
  vm.runInNewContext(valueSource, context);
  vm.runInNewContext(linesSource, context);
  vm.runInNewContext(opsSource, context);
  return { elements, queries, timers, events, context, saved: () => saved,
    advance: ms => { now += ms; }, tick: () => timers.forEach(cb => cb()) };
}

function check(name, fn) { fn(); checks++; }

check('fresh odds remain despite saved +EV filters', () => {
  const h = harness();
  assert.equal(h.elements.lista.children.length, 1);
  assert.equal(h.saved().soValor, false);
  assert.equal(h.saved().ordem, 'horario');
  assert.equal(h.saved().casa, 'Superbet');
  const labels = h.elements.filtros.children.flatMap(row => row.children.map(c => c.textContent || ''));
  assert(!labels.some(label => /só com valor|mais valor|ao vivo/.test(label)));
});
check('badges and banner say contingency, not candidate', () => {
  const h = harness();
  assert.equal(h.elements['model-badge'].textContent, 'SOMENTE ODDS');
  assert.equal(h.elements['contingency-banner'].hidden, false);
  assert.match(h.elements['contingency-banner'].innerHTML, /Modo contingência/);
  assert.match(h.elements['contingency-banner'].innerHTML, /suspensão da operação/);
  assert.match(h.elements.meta.innerHTML, /captura mais antiga/);
});
check('all +EV paths blocked even if payload contains old values', () => {
  const h = harness(), row = h.elements.lista.children[0];
  assert(!/gr-val|88%/.test(row.innerHTML));
  row.querySelector('.gr-head').onclick();
  const expanded = row.querySelector('.gr-body').innerHTML;
  assert.match(expanded, /1\.90/);
  assert(!/vtag|val-strip|88%/.test(expanded));
  h.context.window.renderValor();
  assert.match(h.elements['view-valor'].innerHTML, /Cálculos de valor temporariamente indisponíveis/);
  assert(!/class="vbet"|88%/.test(h.elements['view-valor'].innerHTML));
});
check('expires on an already-open page', () => {
  const h = harness();
  h.advance(110 * MIN); h.tick();
  assert.equal(h.elements.lista.children.length, 0);
  assert.match(h.elements.lista.innerHTML, /Sem odds recentes verificadas/);
});
check('per-game deadline is enforced before board deadline', () => {
  const b = fixture(); b.jogos[0].expires_at = iso(BASE + MIN);
  const h = harness(b); h.advance(MIN); h.tick();
  assert.equal(h.elements.lista.children.length, 0);
});
check('global deadline is enforced before game deadline', () => {
  const b = fixture(); b.contingency.expires_at = iso(BASE + MIN);
  const h = harness(b); h.advance(MIN); h.tick();
  assert.equal(h.elements.lista.children.length, 0);
});
check('rebuild and forged expiry cannot extend capture TTL', () => {
  const b = fixture();
  b.gerado_iso = b.rebuilt_at = iso(BASE);
  b.jogos[0].captured_at = iso(BASE - 120 * MIN);
  b.contingency.expires_at = b.jogos[0].expires_at = iso(BASE + 999 * MIN);
  assert.equal(harness(b).elements.lista.children.length, 0);
});
check('global oldest capture also caps the TTL', () => {
  const b = fixture(); b.gerado_iso = iso(BASE - 120 * MIN);
  assert.equal(harness(b).elements.lista.children.length, 0);
});
for (const [name, invalidate] of [
  ['global expiry', b => delete b.contingency.expires_at],
  ['game expiry', b => delete b.jogos[0].expires_at],
  ['capture clock', b => delete b.jogos[0].captured_at],
  ['global capture clock', b => delete b.gerado_iso],
  ['kickoff clock', b => delete b.jogos[0].inicio_iso],
  ['timezone', b => { b.jogos[0].captured_at = '2026-09-30T12:50:00'; }],
  ['invalid clock', b => { b.jogos[0].expires_at = 'not-a-date'; }],
  ['future capture clock', b => { b.jogos[0].captured_at = iso(BASE + 10 * MIN); }]
]) check('missing/invalid ' + name + ' fails closed', () => {
  const b = fixture(); invalidate(b);
  assert.equal(harness(b).elements.lista.children.length, 0);
});
check('kickoff removes rows on the minute timer', () => {
  const b = fixture(); b.jogos[0].inicio_iso = iso(BASE + MIN);
  const h = harness(b); h.advance(MIN); h.tick();
  assert.equal(h.elements.lista.children.length, 0);
});
check('returning to the visible page refreshes expiry immediately', () => {
  const h = harness(); h.advance(120 * MIN); h.events.visibilitychange();
  assert.equal(h.elements.lista.children.length, 0);
});
check('contingency expires while a different Mesa tab is selected', () => {
  const h = harness(); h.elements['view-board'].hidden = true;
  h.advance(120 * MIN); h.tick();
  assert.equal(h.elements.lista.children.length, 0);
});
check('selecting the board again can refresh it synchronously', () => {
  const h = harness(); h.advance(120 * MIN); h.context.window.refreshMesaBoard();
  assert.equal(h.elements.lista.children.length, 0);
});
check('opening an expired row cannot reveal stale odds', () => {
  const h = harness(), row = h.elements.lista.children[0];
  h.advance(120 * MIN); row.querySelector('.gr-head').onclick();
  assert.equal(h.elements.lista.children.length, 0);
  assert.equal(row.querySelector('.gr-body').innerHTML, '');
});
check('unavailable model blocks value independently of board mode', () => {
  const b = fixture(); delete b.mode; delete b.contingency;
  const h = harness(b);
  assert.equal(h.elements.lista.children.length, 1);
  assert.equal(h.elements['model-badge'].textContent, 'MODELO INDISPONÍVEL');
  h.context.window.renderValor();
  assert(!/class="vbet"/.test(h.elements['view-valor'].innerHTML));
});
check('contingency banner escapes untrusted capture labels', () => {
  const b = fixture(); b.gerado = '<img src=x onerror=alert(1)>';
  const h = harness(b);
  assert(!h.elements['contingency-banner'].innerHTML.includes('<img'));
  assert(h.elements['contingency-banner'].innerHTML.includes('&lt;img'));
});
check('normal board keeps old display policy; no contingency clocks required', () => {
  const b = fixture(); delete b.mode; delete b.contingency;
  b.model = { status: 'promoted', source: 'candidate_pricer' };
  delete b.jogos[0].captured_at; delete b.jogos[0].expires_at;
  const h = harness(b);
  assert.equal(h.elements.lista.children.length, 1);
  assert.equal(h.elements['model-badge'].textContent, 'CANDIDATE');
  assert.equal(h.elements['contingency-banner'].hidden, true);
});
check('Lines contingency retains past only and never calls a historical quote current', () => {
  const h = harness();
  const series = { Faltas: { Superbet: [[BASE / MIN - 20, 23.5, 1.9, 1.9]] } };
  h.context.window.LINES = {
    s: { past: series, future: series, unknown: series, naive: series },
    games: {
      past: { h: 'Historical home', a: 'Away', ko: iso(BASE - MIN) },
      future: { h: 'Future offer', a: 'Away', ko: iso(BASE + MIN) },
      unknown: { h: 'Unknown time', a: 'Away' },
      naive: { h: 'Naive clock', a: 'Away', ko: '2026-09-30T12:00:00' }
    }
  };
  h.context.window.renderLines();
  const html = h.elements['view-lines'].innerHTML;
  assert.match(html, /Histórico de odds, não ofertas atuais/);
  assert.match(html, /Historical home/);
  assert(!/Future offer|Unknown time|Naive clock/.test(html));
  assert(!html.includes('>agora<'));
  assert.match(html, /última observação histórica/);
});
check('Operation suppresses stale +EV marker and identifies its own snapshot clock', () => {
  const h = harness();
  h.context.window.OPS = {
    gerado: '2026-09-29 11:00',
    board: { proximos_24h: [{ jogo: 'Old flagged game', inicio: '01/10 10:00', tem_valor: true }] }
  };
  h.context.window.renderOps();
  const html = h.elements['view-ops'].innerHTML;
  assert.match(html, /Operação em contingência/);
  assert.match(html, /2026-09-29 11:00/);
  assert(!html.includes('>+EV<'));
});

console.log(JSON.stringify({ checks, contingencyUiPassed: true }));
