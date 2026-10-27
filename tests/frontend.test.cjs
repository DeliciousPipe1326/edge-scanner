const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const script = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function page() {
  const elements = new Map();
  const element = () => ({
    value: '100', textContent: '', html: '', children: [], dataset: {},
    get innerHTML() { return this.html; },
    set innerHTML(value) { this.html = value; this.children = []; },
    classList: { add() {}, remove() {}, toggle() {} },
    handlers: {},
    addEventListener(name, handler) { this.handlers[name] = handler; },
    querySelectorAll: () => [],
    showModal() { this.open = true; },
    close() { this.open = false; },
    click() {},
    appendChild(child) { this.children.push(child); },
  });
  const get = (id) => {
    if (!elements.has(id)) {
      const node = element();
      if (id === 'min-roi-input') node.value = '2';
      elements.set(id, node);
    }
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
  const shared = { event: text, sport_display: text, market: 'h2h', valid_until: new Date(Date.now() + 60000).toISOString() };
  context.renderScanData({
    arbitrage: { opportunities: [{ ...shared, roi_percent: 5, best_odds: [
      { outcome: text, bookmaker: text, price: 2.1 },
      { outcome: 'Away', bookmaker: 'b', price: 2.1 },
    ] }] },
    middles: { stake_amount: 100, opportunities: [{ ...shared, side_a: {price: 1.9}, side_b: {price: 1.9}, profile: {scenarios: [{score: 1, side_a: 'win', side_b: 'lose'}, {score: 2, side_a: 'win', side_b: 'win'}, {score: 3, side_a: 'lose', side_b: 'win'}]}, middle_zone: text }] },
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

test('planning requires a second confirmation and sends the exact approved budget', async () => {
  const { context, get } = page();
  const calls = [];
  context.fetch = async (url, options) => {
    calls.push({url, payload: JSON.parse(options.body)});
    return { json: async () => url === '/plan' ? {
      success: true, plan: {odds_requests: 1, regions: ['us', 'eu'], max_credits: 6,
                           sports: [{name: 'NBA', markets: ['h2h', 'spreads', 'totals'], max_credits: 6}]},
      quota: {remaining: 494},
    } : {success: true, scan_time: '2026-10-27T00:00:00Z'} };
  };
  await context.reviewScan({apiKey: 'fake-key', sports: ['basketball_nba']});
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/plan');
  assert.equal(get('plan-dialog').open, true);
  assert.match(get('plan-summary').textContent, /6 credits/);
  await get('confirm-scan').handlers.click();
  assert.equal(calls.length, 2);
  assert.equal(calls[1].url, '/scan');
  assert.equal(calls[1].payload.maxCredits, 6);
  assert.equal(get('plan-dialog').open, false);
});

test('canceling a plan never requests odds', async () => {
  const { context, get } = page();
  let calls = 0;
  context.fetch = async () => { calls++; return {json: async () => ({success: true,
    plan: {odds_requests: 1, regions: ['eu'], max_credits: 3, sports: []}, quota: {remaining: 500}})}; };
  await context.reviewScan({});
  get('cancel-plan').handlers.click();
  await get('confirm-scan').handlers.click();
  assert.equal(calls, 1);
});

test('insufficient quota disables confirmation', async () => {
  const { context, get } = page();
  context.fetch = async () => ({json: async () => ({success: true,
    plan: {odds_requests: 1, regions: ['eu'], max_credits: 3, sports: []}, quota: {remaining: 2}})});
  await context.reviewScan({});
  assert.equal(get('confirm-scan').disabled, true);
  assert.match(get('status').textContent, /insufficient credits/);
});

test('expired snapshots disappear from result tables', () => {
  const { context, get } = page();
  const expired = {valid_until: '2020-01-01T00:00:00Z', best_odds: []};
  context.renderScanData({arbitrage: {opportunities: [expired]}});
  assert.equal(get('#results-table tbody').children.length, 0);
  assert.match(get('scan-meta').textContent, /1 results expired/);
  assert.equal(context.isCurrent({valid_until: new Date(Date.now() + 60000).toISOString()}), true);
  assert.equal(context.isCurrent({}), false);
});

test('middle stake changes recalculate endpoint pushes and threshold in cents', () => {
  const { context } = page();
  const opp = {side_a: {effective_price: 1.9}, side_b: {effective_price: 1.9}, profile: {
    scenarios: [
      {score: 1, side_a: 'lose', side_b: 'win'},
      {score: 2, side_a: 'push', side_b: 'win'},
      {score: 3, side_a: 'win', side_b: 'win'},
      {score: 4, side_a: 'win', side_b: 'push'},
      {score: 5, side_a: 'win', side_b: 'lose'},
    ],
  }};
  const calc = context.calculateMiddle(opp, 100);
  assert.equal(calc.stakeA, 50);
  assert.equal(calc.worst, -5);
  assert.equal(calc.scenarios.find(s => s.score === 2).profit, 45);
  assert.ok(Math.abs(calc.threshold - 100 * 5 / 95) < 0.0001);
  const changed = context.calculateMiddle(opp, 99.99);
  assert.equal(context.roundMoney(changed.stakeA + changed.stakeB), 99.99);
});

test('limit status is recomputed for the displayed stake', () => {
  const { context } = page();
  assert.match(context.formatExecution([{bet_limit: 50}], [60]), /Exceeds provider limit/);
  assert.match(context.formatExecution([{bet_limit: 50}], [40]), /Within reported limit/);
  assert.match(context.formatExecution([{bet_limit: null}], [40]), /Limit unverified/);
});

test('blocked preference storage and non-JSON server errors are recoverable', async () => {
  const { context, get } = page();
  context.localStorage = {getItem() {throw Error('disabled');}, setItem() {throw Error('disabled');}};
  assert.equal(context.readPreference('arb-theme'), null);
  context.savePreference('arb-theme', 'dark');
  context.fetch = async () => ({json: async () => {throw Error('invalid');}});
  await context.runScan({});
  assert.match(get('status').textContent, /unreadable response/);
  assert.equal(get('scan-btn').disabled, false);
});

test('all event window labels distinguish Infinite from bounded windows', () => {
  const { context } = page();
  assert.equal(context.windowLabel(24), '24-hour window');
  assert.equal(context.windowLabel(48), '48-hour window');
  assert.equal(context.windowLabel(168), '7-day window');
  assert.equal(context.windowLabel(null), 'Infinite window');
});

test('minimum net ROI and reported limits filter impractical arbitrage rows', () => {
  const { context, get } = page();
  const candidate = {valid_until: new Date(Date.now() + 60000).toISOString(),
    best_odds: [{outcome: 'Home', price: 2.01}, {outcome: 'Away', price: 2.01}]};
  context.renderScanData({arbitrage: {opportunities: [candidate]}});
  assert.equal(get('#results-table tbody').children.length, 0);
  get('min-roi-input').value = '0';
  context.renderArbitrage();
  assert.equal(get('#results-table tbody').children.length, 1);
  candidate.best_odds[0].bet_limit = 1;
  context.renderArbitrage();
  assert.equal(get('#results-table tbody').children.length, 0);
});

test('expiry polling preserves still-valid rows instead of collapsing details', () => {
  const { context, get } = page();
  const candidate = {valid_until: new Date(Date.now() + 60000).toISOString(),
    best_odds: [{outcome: 'Home', price: 2.1}, {outcome: 'Away', price: 2.1}]};
  context.renderScanData({arbitrage: {opportunities: [candidate]}});
  const original = get('#results-table tbody').children[0];
  context.refreshExpired();
  assert.equal(get('#results-table tbody').children[0], original);
  candidate.valid_until = '2020-01-01T00:00:00Z';
  context.refreshExpired();
  assert.equal(get('#results-table tbody').children.length, 0);
});

test('JSON export contains the response snapshot and never the request key', async () => {
  const { context, get } = page();
  let exported;
  context.Blob = class { constructor(parts) { exported = parts.join(''); } };
  context.URL = {createObjectURL: () => 'blob:fixture', revokeObjectURL() {}};
  context.setTimeout = (fn) => fn();
  context.fetch = async () => ({json: async () => ({success: true, scan_time: '2026-10-27T00:00:00Z', quota: {remaining: 488}})});
  await context.runScan({apiKey: 'request-secret-must-not-export'});
  assert.equal(get('download-results').disabled, false);
  get('download-results').handlers.click();
  assert.equal(JSON.parse(exported).quota.remaining, 488);
  assert.ok(!exported.includes('request-secret-must-not-export'));
});
