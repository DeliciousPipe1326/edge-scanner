const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const html = fs.readFileSync(path.join(__dirname, '../templates/index.html'), 'utf8');
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];

function page() {
  const elements = new Map();
  const element = () => ({
    value: '100', textContent: '', innerHTML: '', children: [], dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {}, querySelectorAll: () => [],
    appendChild(child) { this.children.push(child); },
  });
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const context = vm.createContext({
    window: { edgeScannerConfig: {} },
    document: { getElementById: get, querySelector: get, querySelectorAll: () => [],
                createElement: element, addEventListener() {} },
    localStorage: { getItem: () => null },
  });
  vm.runInContext(script, context);
  return { context, get };
}

test('cent-rounded stakes spend the requested amount', () => {
  const { context } = page();
  const opp = { best_odds: [{ outcome: 'Home', price: 2.1 }, { outcome: 'Away', price: 2.1 }] };
  for (const total of [0.01, 0.03, 99.99, 100]) {
    const result = context.calculateStakes(opp, total);
    assert.equal(Number(result.breakdown.reduce((sum, o) => sum + o.stake, 0).toFixed(2)), total);
  }
});

test('zero bankroll and edge are not replaced with defaults', () => {
  const { context } = page();
  assert.equal(context.numberOrDefault('0', 1000), 0);
  assert.equal(context.numberOrDefault('', 1000), 1000);
  assert.equal(context.calculateKellyStake(0.6, 2, 0, .25).recommended, 0);
});

test('external names and error messages are escaped in HTML renderers', () => {
  const { context, get } = page();
  const text = '<img src=x onerror=alert(1)>';
  for (const output of [
    context.formatOutcome({ outcome: text, bookmaker: text, point: text, price: 2.1 }),
    context.formatPlusBet({ bet: { outcome: text, soft_book: text } }),
    context.formatMiddleSide({ team: text, bookmaker: text, price: 2.1 }, 50),
    context.formatMarket({ market: text }),
    context.formatExchangeCell({ has_exchange: true, exchange_books: [text] }),
  ]) {
    assert.ok(!output.includes('<img'), output);
    assert.ok(output.includes('&lt;img'), output);
  }
  context.renderPartialWarning({ partial: true, sport_errors: [{ sport: text, error: text }] });
  assert.ok(!get('partial-warning').innerHTML.includes('<img'));
  context.renderBars(get('bars'), { [text]: 2 }, 'sport');
  assert.ok(!get('bars').innerHTML.includes('<img'));
});

test('all result tables escape event and sport names', () => {
  const { context, get } = page();
  const text = '<img src=x onerror=alert(1)>';
  get('min-gap-input').value = '0';
  get('min-edge-input').value = '0';
  const shared = { event: text, sport_display: text, market: 'h2h' };
  context.renderScanData({
    arbitrage: { opportunities: [{ ...shared, roi_percent: 5, best_odds: [
      { outcome: text, bookmaker: text, price: 2.1 },
      { outcome: 'Away', bookmaker: 'b', price: 2.1 },
    ] }] },
    middles: { stake_amount: 100, opportunities: [{ ...shared, ev_percent: 5, middle_zone: text }] },
    plus_ev: { opportunities: [{ ...shared, edge_percent: 5, bet: { outcome: text, soft_odds: 2.1 } }] },
  });
  for (const id of ['#results-table tbody', '#middles-table tbody', '#plus-ev-table tbody']) {
    assert.equal(get(id).children.length, 1, id);
    const output = get(id).children[0].innerHTML;
    assert.ok(!output.includes('<img'), output);
    assert.ok(output.includes('&lt;img'), output);
  }
});

test('finishing a scan preserves the success or error status', async () => {
  const { context, get } = page();
  context.fetch = async () => ({ json: async () => ({ success: true, scan_time: '2026-10-27T00:00:00Z' }) });
  await context.runScan({});
  assert.match(get('status').textContent, /Scan complete/);
  assert.equal(get('scan-btn').disabled, false);
  context.fetch = async () => ({ json: async () => ({ success: false, error: 'API unavailable' }) });
  await context.runScan({});
  assert.equal(get('status').textContent, 'API unavailable');
  assert.equal(get('scan-btn').disabled, false);
});
