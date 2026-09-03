const SNAPSHOT_PATH = "assets/data/live-paper-snapshot.json";
const PAPER_LAUNCH_AT = new Date("2026-09-01T00:00:00-04:00");
const ANNUALIZATION_DAYS = 365.2425;

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const easternTime = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

const easternTradeTime = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

const expiryDate = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  month: "short",
  day: "numeric",
  year: "numeric",
});

const easternScheduleClock = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function publisherWindowIsOpen(now) {
  const parts = Object.fromEntries(
    easternScheduleClock
      .formatToParts(now)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  const weekday = ["Mon", "Tue", "Wed", "Thu", "Fri"].includes(parts.weekday);
  const hour = Number(parts.hour);
  const minute = Number(parts.minute);
  return weekday && (hour >= 9 && (hour < 17 || (hour === 17 && minute === 0)));
}

function signedMoney(value) {
  const numeric = Number(value);
  return `${numeric >= 0 ? "+" : "−"}${money.format(Math.abs(numeric))}`;
}

function signedPercent(value) {
  const numeric = Number(value) * 100;
  return `${numeric >= 0 ? "+" : "−"}${Math.abs(numeric).toFixed(2)}%`;
}

function updateText(selector, value) {
  document.querySelectorAll(selector).forEach((element) => {
    element.textContent = value;
  });
}

function updateTone(selector, value) {
  document.querySelectorAll(selector).forEach((element) => {
    element.classList.toggle("positive", Number(value) >= 0);
    element.classList.toggle("negative", Number(value) < 0);
  });
}

function readableAction(value) {
  return String(value || "filled")
    .toLowerCase()
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function renderOrders(orders) {
  const body = document.querySelector("[data-live-orders]");
  if (!body) return;
  body.replaceChildren();
  if (!orders.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.className = "empty-orders";
    cell.textContent = "No filled V13.5 orders are available in the broker history yet.";
    row.append(cell);
    body.append(row);
    return;
  }

  orders.slice(0, 10).forEach((order) => {
    const row = document.createElement("tr");
    const filled = document.createElement("td");
    filled.textContent = easternTradeTime.format(new Date(order.filled_at));

    const actionCell = document.createElement("td");
    const action = document.createElement("span");
    action.className = `trade-action ${String(order.side).toLowerCase()}`;
    action.textContent = readableAction(order.action);
    actionCell.append(action);

    const contractCell = document.createElement("td");
    const contract = document.createElement("strong");
    const strike = Number(order.contract.strike);
    const strikeText = Number.isInteger(strike) ? strike.toFixed(0) : strike.toFixed(2);
    contract.textContent = `${order.contract.underlying || "Option"} $${strikeText} ${order.contract.option_type || ""}`.trim();
    const expiration = document.createElement("small");
    expiration.textContent = order.contract.expiry
      ? `Expires ${expiryDate.format(new Date(`${order.contract.expiry}T12:00:00Z`))}`
      : order.contract.symbol;
    contractCell.append(contract, expiration);

    const quantity = document.createElement("td");
    quantity.textContent = Number(order.quantity).toLocaleString("en-US");
    const average = document.createElement("td");
    average.textContent = money.format(Number(order.average_fill_price));
    const reference = document.createElement("td");
    const referenceCode = document.createElement("code");
    referenceCode.textContent = order.system_ref;
    reference.append(referenceCode);

    row.append(filled, actionCell, contractCell, quantity, average, reference);
    body.append(row);
  });
}

const chartDate = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  month: "short",
  day: "numeric",
});

function svgNode(name, attributes = {}, text = "") {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
  if (text) node.textContent = text;
  return node;
}

function pnlChartPoints(snapshot) {
  const rawPoints = Array.isArray(snapshot.portfolio_history?.points)
    ? snapshot.portfolio_history.points : [];
  const points = rawPoints
    .filter((point) => point.timestamp != null && point.total_pnl != null && point.total_pnl !== "")
    .map((point) => ({
      timestamp: new Date(point.timestamp),
      pnl: Number(point.total_pnl),
      source: "daily",
    }))
    .filter((point) => Number.isFinite(point.timestamp.getTime()) && Number.isFinite(point.pnl))
    .sort((left, right) => left.timestamp - right.timestamp);

  const capturedAt = new Date(snapshot.generated_at);
  const currentPnl = snapshot.account?.total_pnl;
  if (snapshot.generated_at != null && Number.isFinite(capturedAt.getTime())
      && currentPnl != null && currentPnl !== "" && Number.isFinite(Number(currentPnl))
      && (!points.length || capturedAt >= points[points.length - 1].timestamp)) {
    // Same timestamp: prefer the account snapshot without plotting two valuations.
    while (points.length && +points[points.length - 1].timestamp === +capturedAt) points.pop();
    points.push({ timestamp: capturedAt, pnl: Number(currentPnl), source: "snapshot" });
  }
  return points;
}

function renderPnlChart(snapshot) {
  const svg = document.querySelector("[data-pnl-chart]");
  if (!svg) return;
  const empty = document.querySelector("[data-pnl-empty]");
  const points = pnlChartPoints(snapshot);
  const last = points[points.length - 1];
  const hasSnapshot = last?.source === "snapshot";
  const dailyCount = points.filter((point) => point.source === "daily").length;
  updateText("[data-pnl-change]", last
    ? `${signedMoney(last.pnl)} · ${hasSnapshot ? "latest snapshot" : "daily history only"}`
    : "History unavailable");
  if (last) updateTone("[data-pnl-change]", last.pnl);
  updateText("[data-pnl-range]", hasSnapshot
    ? `Captured ${easternTime.format(last.timestamp)} · ${dailyCount} daily points + latest snapshot`
    : last ? `${dailyCount} daily points · current snapshot unavailable` : "History unavailable");

  if (points.length < 2) {
    svg.replaceChildren();
    svg.hidden = true;
    if (empty) empty.style.display = "grid";
    return;
  }

  svg.hidden = false;
  if (empty) empty.style.display = "none";
  svg.replaceChildren();

  const width = 1000;
  const height = 360;
  const padding = { top: 26, right: 34, bottom: 46, left: 88 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const values = points.map((point) => point.pnl);
  let minimum = Math.min(0, ...values);
  let maximum = Math.max(0, ...values);
  const range = maximum - minimum || 1;
  minimum -= range * 0.12;
  maximum += range * 0.12;
  const timeSpan = last.timestamp - points[0].timestamp;
  const scaleX = (index) => padding.left + (timeSpan > 0
    ? (points[index].timestamp - points[0].timestamp) / timeSpan
    : index / (points.length - 1)) * plotWidth;
  const scaleY = (value) => padding.top + ((maximum - value) / (maximum - minimum)) * plotHeight;

  const definitions = svgNode("defs");
  const gradient = svgNode("linearGradient", { id: "pnl-area-gradient", x1: "0", y1: "0", x2: "0", y2: "1" });
  gradient.append(
    svgNode("stop", { offset: "0%", "stop-color": "#42a973", "stop-opacity": ".28" }),
    svgNode("stop", { offset: "100%", "stop-color": "#42a973", "stop-opacity": "0" }),
  );
  definitions.append(gradient);
  svg.append(definitions);

  for (let step = 0; step <= 4; step += 1) {
    const value = minimum + ((maximum - minimum) * step) / 4;
    const y = scaleY(value);
    svg.append(
      svgNode("line", { x1: padding.left, y1: y, x2: width - padding.right, y2: y, class: "pnl-grid-line" }),
      svgNode("text", { x: padding.left - 14, y: y + 4, "text-anchor": "end", class: "pnl-axis-label" }, signedMoney(value)),
    );
  }

  if (minimum <= 0 && maximum >= 0) {
    const zeroY = scaleY(0);
    svg.append(svgNode("line", { x1: padding.left, y1: zeroY, x2: width - padding.right, y2: zeroY, class: "pnl-zero-line" }));
  }

  const linePath = points
    .map((point, index) => `${index === 0 ? "M" : "L"}${scaleX(index).toFixed(2)},${scaleY(point.pnl).toFixed(2)}`)
    .join(" ");
  const areaPath = `${linePath} L${scaleX(points.length - 1).toFixed(2)},${(height - padding.bottom).toFixed(2)} L${scaleX(0).toFixed(2)},${(height - padding.bottom).toFixed(2)} Z`;
  svg.append(
    svgNode("path", { d: areaPath, class: "pnl-area" }),
    svgNode("path", { d: hasSnapshot ? linePath.slice(0, linePath.lastIndexOf(" L")) : linePath, class: "pnl-line" }),
  );
  if (hasSnapshot) {
    const previous = points.length - 2;
    svg.append(svgNode("path", {
      d: `M${scaleX(previous)},${scaleY(points[previous].pnl)} L${scaleX(points.length - 1)},${scaleY(last.pnl)}`,
      class: "pnl-line", "stroke-dasharray": "7 5", "data-pnl-snapshot-segment": "",
    }));
  }

  const lastDot = svgNode("circle", { cx: scaleX(points.length - 1), cy: scaleY(last.pnl), r: 7, class: "pnl-latest-dot" });
  lastDot.append(svgNode("title", {}, `${hasSnapshot ? "Latest account snapshot" : "Daily history"}: ${signedMoney(last.pnl)} · ${easternTime.format(last.timestamp)}`));
  svg.append(lastDot);
  const labelIndexes = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
  labelIndexes.forEach((index) => {
    svg.append(svgNode("text", {
      x: scaleX(index),
      y: height - 16,
      "text-anchor": index === 0 ? "start" : index === points.length - 1 ? "end" : "middle",
      class: "pnl-axis-label",
    }, index === points.length - 1 && hasSnapshot ? "Latest snapshot" : chartDate.format(points[index].timestamp)));
  });
}

function renderPnlMetrics(snapshot) {
  const account = snapshot.account || {};
  const totalPnl = Number(account.total_pnl);
  const baseline = Number(account.starting_baseline);
  const reportedReturn = Number(account.total_return);
  const totalReturn = Number.isFinite(reportedReturn)
    ? reportedReturn
    : totalPnl / baseline;
  const generatedAt = new Date(snapshot.generated_at);
  const elapsedDays = Math.max(
    1,
    (generatedAt.getTime() - PAPER_LAUNCH_AT.getTime()) / 86_400_000,
  );
  const annualizedRate = totalReturn * (ANNUALIZATION_DAYS / elapsedDays);
  const annualizedPnl = totalPnl * (ANNUALIZATION_DAYS / elapsedDays);
  const elapsedLabel = `${elapsedDays.toFixed(1)} elapsed ${elapsedDays < 1.05 ? "day" : "days"}`;

  updateText("[data-pnl-cumulative]", signedMoney(totalPnl));
  updateText("[data-pnl-cumulative-return]", signedPercent(totalReturn));
  updateText("[data-pnl-annualized-rate]", signedPercent(annualizedRate));
  updateText(
    "[data-pnl-annualized-detail]",
    `${signedMoney(annualizedPnl)}/yr · ${elapsedLabel}`,
  );
  updateText(
    "[data-pnl-calculation-note]",
    `Annualized P&L rate scales cumulative return over ${elapsedLabel} since the Sep 1, 2026 launch. It is an early live-paper run-rate, not a forecast.`,
  );
  updateTone(
    "[data-pnl-cumulative], [data-pnl-cumulative-return], [data-pnl-annualized-rate]",
    totalPnl,
  );

  const historyPoints = Array.isArray(snapshot.portfolio_history?.points)
    ? snapshot.portfolio_history.points
        .map((point) => ({
          timestamp: new Date(point.timestamp),
          pnl: Number(point.total_pnl),
        }))
        .filter((point) => Number.isFinite(point.timestamp.getTime()) && Number.isFinite(point.pnl))
        .sort((left, right) => left.timestamp - right.timestamp)
    : [];

  if (historyPoints.length < 2 || !Number.isFinite(baseline) || baseline <= 0) {
    updateText("[data-pnl-max-drawdown]", "History pending");
    updateText("[data-pnl-max-drawdown-detail]", "Needs at least 2 daily observations");
    return;
  }

  let peakEquity = baseline;
  let maxDrawdown = 0;
  let maxDrawdownRate = 0;
  historyPoints.forEach((point) => {
    const equity = baseline + point.pnl;
    peakEquity = Math.max(peakEquity, equity);
    const drawdown = equity - peakEquity;
    if (drawdown < maxDrawdown) {
      maxDrawdown = drawdown;
      maxDrawdownRate = drawdown / peakEquity;
    }
  });
  updateText("[data-pnl-max-drawdown]", signedMoney(maxDrawdown));
  updateText(
    "[data-pnl-max-drawdown-detail]",
    `${signedPercent(maxDrawdownRate)} peak-to-trough · ${historyPoints.length} observations`,
  );
  updateTone("[data-pnl-max-drawdown]", maxDrawdown);
}

function setFreshness(snapshot) {
  const generated = new Date(snapshot.generated_at);
  const timestampValid = Number.isFinite(generated.getTime());
  const ageSeconds = Math.max(0, (Date.now() - generated.getTime()) / 1000);
  const staleAfter = Number(snapshot.refresh_contract.stale_after_seconds || 900);
  const expired = !timestampValid || !Number.isFinite(ageSeconds) || ageSeconds > staleAfter;
  const scheduledPause = expired && timestampValid && !publisherWindowIsOpen(new Date());
  const stale = expired && !scheduledPause;
  const minutes = Math.floor(ageSeconds / 60);
  const label = scheduledPause
    ? `Off hours · ${minutes}m old`
    : stale
      ? `Stale · ${minutes}m old`
      : `Near-live · ${minutes}m old`;

  document.querySelectorAll("[data-live-freshness]").forEach((element) => {
    const dot = document.createElement("span");
    dot.className = "live-dot";
    dot.setAttribute("aria-hidden", "true");
    element.replaceChildren(dot, document.createTextNode(label));
    element.classList.toggle("is-stale", stale);
    element.classList.toggle("is-idle", scheduledPause);
    element.classList.remove("is-error");
  });
  const status = document.querySelector("[data-live-feed-status]");
  if (status) {
    status.textContent = scheduledPause
      ? `OFF HOURS · last broker capture ${easternTime.format(generated)}`
      : stale
        ? `STALE · last broker capture ${easternTime.format(generated)}`
        : `BROKER VERIFIED · refreshed ${easternTime.format(generated)}`;
    status.classList.toggle("is-stale", stale);
    status.classList.toggle("is-idle", scheduledPause);
    status.classList.remove("is-error");
  }
}

function applySnapshot(snapshot) {
  const account = snapshot.account;
  updateText("[data-live-equity]", money.format(Number(account.equity)));
  updateText(
    "[data-live-total-pnl]",
    `${signedMoney(account.total_pnl)} since fresh $100K baseline · live since Sep 1, 2026`,
  );
  updateText("[data-live-total-pnl-short]", signedMoney(account.total_pnl));
  updateTone("[data-live-total-pnl], [data-live-total-pnl-short]", account.total_pnl);
  updateText("[data-live-day-pnl]", signedMoney(account.day_pnl));
  updateTone("[data-live-day-pnl]", account.day_pnl);
  updateText("[data-live-day-return]", signedPercent(account.day_return));
  updateText("[data-live-cash]", money.format(Number(account.cash)));
  updateText("[data-live-account-status]", account.status || "UNKNOWN");
  updateText("[data-live-buying-power-value]", money.format(Number(account.buying_power)));
  updateText(
    "[data-live-buying-power]",
    `${money.format(Number(account.buying_power))} buying power`,
  );
  updateText("[data-live-account-id]", account.account_id);
  document.querySelectorAll("[data-live-generated-at]").forEach((element) => {
    element.textContent = easternTime.format(new Date(snapshot.generated_at));
    element.setAttribute("datetime", snapshot.generated_at);
  });
  updateText(
    "[data-live-generated-short]",
    `Captured ${easternTime.format(new Date(snapshot.generated_at))}`,
  );
  const orders = Array.isArray(snapshot.recent_filled_system_orders)
    ? snapshot.recent_filled_system_orders
    : [];
  updateText(
    "[data-live-order-count]",
    `${orders.length} ${orders.length === 1 ? "fill" : "fills"} available`,
  );
  renderOrders(orders);
  renderPnlMetrics(snapshot);
  renderPnlChart(snapshot);
  setFreshness(snapshot);
}

function setFeedError() {
  document.querySelectorAll("[data-live-freshness]").forEach((element) => {
    const dot = document.createElement("span");
    dot.className = "live-dot";
    dot.setAttribute("aria-hidden", "true");
    element.replaceChildren(dot, document.createTextNode("Refresh unavailable"));
    element.classList.add("is-error");
    element.classList.remove("is-idle");
  });
  const status = document.querySelector("[data-live-feed-status]");
  if (status) {
    status.textContent = "REFRESH UNAVAILABLE · showing last deployed snapshot";
    status.classList.add("is-error");
    status.classList.remove("is-idle");
  }
}

async function refreshSnapshot() {
  const url = new URL(SNAPSHOT_PATH, document.baseURI);
  url.searchParams.set("refresh", Date.now().toString());
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error("snapshot unavailable");
    const snapshot = await response.json();
    applySnapshot(snapshot);
  } catch {
    setFeedError();
  }
}

document.querySelectorAll("[data-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const original = button.textContent;
    try {
      await navigator.clipboard.writeText(button.dataset.copy || "");
      button.textContent = "Copied";
    } catch {
      button.textContent = "Select text";
    }
    window.setTimeout(() => {
      button.textContent = original;
    }, 1600);
  });
});

refreshSnapshot();
window.setInterval(refreshSnapshot, 60_000);
