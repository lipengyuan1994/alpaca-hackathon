const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

// Existing dashboard line: daily P&L + one labeled current observation, USD vs
// $100K, true elapsed-time x axis. Preserve history and signed outcomes. No
// continuous intraday series is inferred from this short paper-account history.
const source = readFileSync(resolve(__dirname, "../../docs/assets/site.js"), "utf8");

function node(tag = "div") {
  return {
    tag, attributes: {}, children: [], textContent: "", hidden: false, style: {},
    classList: { toggle() {} },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
  };
}

function load() {
  const elements = Object.fromEntries([
    "[data-pnl-chart]", "[data-pnl-empty]", "[data-pnl-change]", "[data-pnl-range]",
  ].map(key => [key, node()]));
  const context = vm.createContext({
    URL, Intl, Date,
    document: {
      baseURI: "https://example.test/",
      querySelector: key => elements[key] || null,
      querySelectorAll: key => elements[key] ? [elements[key]] : [],
      createElementNS: (_namespace, tag) => node(tag),
    },
    window: { setInterval() {} },
    fetch: () => new Promise(() => {}), // No network or implicit snapshot update.
  });
  vm.runInContext(source, context);
  return { context, elements };
}

function snapshot(pnl = 243.53) {
  return {
    generated_at: "2026-09-03T20:13:36Z", account: { total_pnl: pnl },
    portfolio_history: { points: [
      { timestamp: "2026-09-02T00:00:00Z", total_pnl: -70.27 },
      { timestamp: "2026-09-03T00:00:00Z", total_pnl: 99.73 },
    ] },
  };
}

test("latest snapshot extends history and replaces stale chart headline", () => {
  const { context, elements } = load();
  const input = snapshot();
  const original = JSON.stringify(input);
  const points = context.pnlChartPoints(input);
  assert.equal(points.length, 3);
  assert.equal(points[1].pnl, 99.73);
  assert.equal(points[2].pnl, 243.53);
  assert.equal(points[2].source, "snapshot");
  assert.equal(JSON.stringify(input), original);
  context.renderPnlChart(input);
  assert.equal(elements["[data-pnl-change]"].textContent, "+$243.53 · latest snapshot");
  assert.match(elements["[data-pnl-range]"].textContent, /2 daily points \+ latest snapshot/);
  assert.equal(elements["[data-pnl-chart]"].children.filter(n => n.attributes["stroke-dasharray"] === "7 5").length, 1);
  const paths = elements["[data-pnl-chart]"].children.filter(n => n.tag === "path");
  assert.ok(paths.every(n => !/NaN|Infinity/.test(n.attributes.d)));
});

test("lower, negative, and zero current P&L are never suppressed", () => {
  const { context, elements } = load();
  for (const value of [25, -100, 0]) {
    assert.equal(context.pnlChartPoints(snapshot(value)).at(-1).pnl, value);
    context.renderPnlChart(snapshot(value));
    assert.equal(elements["[data-pnl-change]"].textContent, `${context.signedMoney(value)} · latest snapshot`);
  }
});

test("same timestamp replaces history point and repeated polling never duplicates", () => {
  const { context } = load();
  const input = snapshot();
  input.generated_at = input.portfolio_history.points[1].timestamp;
  for (let i = 0; i < 3; i++) {
    const points = context.pnlChartPoints(input);
    assert.equal(points.length, 2);
    assert.equal(points.at(-1).pnl, 243.53);
  }
});

test("invalid snapshot values fall back to explicitly labeled daily history", () => {
  const { context, elements } = load();
  for (const invalid of [null, undefined, "", "bad", Infinity]) {
    const input = snapshot();
    input.account.total_pnl = invalid;
    context.renderPnlChart(input);
    assert.equal(elements["[data-pnl-change]"].textContent, "+$99.73 · daily history only");
  }
  for (const invalidDate of [undefined, null, "invalid", "2026-09-01T00:00:00Z"]) {
    const input = snapshot();
    input.generated_at = invalidDate;
    assert.equal(context.pnlChartPoints(input).at(-1).source, "daily");
  }
});

test("missing history keeps latest amount visible without fabricating a trend", () => {
  const { context, elements } = load();
  const input = snapshot();
  delete input.portfolio_history;
  context.renderPnlChart(input);
  assert.equal(elements["[data-pnl-change]"].textContent, "+$243.53 · latest snapshot");
  assert.equal(elements["[data-pnl-chart]"].hidden, true);
  assert.equal(context.pnlChartPoints(input).length, 1);
});

test("history is sorted and malformed observations are excluded", () => {
  const { context } = load();
  const input = snapshot();
  input.portfolio_history.points.reverse();
  input.portfolio_history.points.push({ timestamp: null, total_pnl: 1 }, { timestamp: "2026-09-01", total_pnl: null });
  const points = context.pnlChartPoints(input);
  assert.equal(points.length, 3);
  assert.equal(points[0].pnl, -70.27);
});
