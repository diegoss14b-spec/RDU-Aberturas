// Read-only smoke regression: execute the real board renderer with a minimal DOM.
// Usage: node test_mesa_frontend_uncapped.js [path/to/valor/js/board.js]
// This checks emitted rows, not browser layout or a production deployment.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

class Element {
  constructor() {
    this.children = [];
    this.attrs = {};
    this.style = {};
    this.queries = {};
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

const now = Date.now();
const future = new Date(now + 86400000).toISOString();
const past = new Date(now - 86400000).toISOString();
const lines = [{ linha: 23.5, over: 1.9, under: 1.9 }];
const games = Array.from({ length: 1205 }, (_, i) => ({
  jogo: `Eligible ${i}`, inicio: '13/09 20:00', inicio_iso: future,
  mercados: { Faltas: { Superbet: lines } }, valor: []
}));
games.push({
  jogo: 'Only team market', inicio: '13/09 20:00', inicio_iso: future,
  mercados: {}, times: { Faltas: { home: { nome: 'A', casas: { Superbet: lines } } } }, valor: []
});
games.push({
  jogo: 'Next week', inicio: '20/09 20:00',
  inicio_iso: new Date(now + 8 * 86400000).toISOString(),
  mercados: { Faltas: { Superbet: lines } }, valor: []
});
games.unshift({ jogo: 'Other house', inicio_iso: future, mercados: { Faltas: { Betano: lines } }, valor: [] });
games.unshift({ jogo: 'Other market', inicio_iso: future, mercados: { Escanteios: { Superbet: lines } }, valor: [] });
games.unshift({ jogo: 'Started', inicio_iso: past, mercados: { Faltas: { Superbet: lines } }, valor: [] });

const elements = { filtros: new Element(), lista: new Element(), meta: new Element() };
const context = {
  window: {
    BOARD: { jogos: games, mercados: ['Faltas', 'Escanteios'], casas: ['Superbet', 'Betano'], gerado_iso: new Date(now).toISOString() },
    setInterval() {}
  },
  document: {
    getElementById: id => elements[id] || null,
    createElement: () => new Element(),
    querySelector: () => null
  },
  localStorage: {
    getItem: () => JSON.stringify({ mercado: 'Faltas', casa: 'Superbet', ordem: 'horario' }),
    setItem() {}
  },
  console
};
const source = process.argv[2] || path.join(__dirname, 'valor/js/board.js');
vm.runInNewContext(fs.readFileSync(source, 'utf8'), context);

assert.equal(elements.lista.children.length, 1207);
assert.match(elements.lista.children.at(-1).innerHTML, /Next week/);
for (const excluded of ['Started', 'Other market', 'Other house']) {
  assert(!elements.lista.children.some(element => element.innerHTML.includes(excluded)), excluded);
}
assert(elements.lista.children.some(element => element.innerHTML.includes('Only team market')));
console.log(JSON.stringify({
  inputGames: games.length, expectedVisible: 1207, actualRendered: elements.lista.children.length,
  lateGameVisible: true, onlyTeamMarketVisible: true,
  excludedStartedWrongHouseWrongMarket: true
}));
