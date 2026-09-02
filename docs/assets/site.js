const SNAPSHOT_PATH = "assets/data/live-paper-snapshot.json";

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

function setFreshness(snapshot) {
  const generated = new Date(snapshot.generated_at);
  const ageSeconds = Math.max(0, (Date.now() - generated.getTime()) / 1000);
  const staleAfter = Number(snapshot.refresh_contract.stale_after_seconds || 900);
  const stale = !Number.isFinite(ageSeconds) || ageSeconds > staleAfter;
  const minutes = Math.floor(ageSeconds / 60);
  const label = stale ? `Stale · ${minutes}m old` : `Near-live · ${minutes}m old`;

  document.querySelectorAll("[data-live-freshness]").forEach((element) => {
    const dot = document.createElement("span");
    dot.className = "live-dot";
    dot.setAttribute("aria-hidden", "true");
    element.replaceChildren(dot, document.createTextNode(label));
    element.classList.toggle("is-stale", stale);
    element.classList.remove("is-error");
  });
  const status = document.querySelector("[data-live-feed-status]");
  if (status) {
    status.textContent = stale
      ? `STALE · last broker capture ${easternTime.format(generated)}`
      : `BROKER VERIFIED · refreshed ${easternTime.format(generated)}`;
    status.classList.toggle("is-stale", stale);
    status.classList.remove("is-error");
  }
}

function applySnapshot(snapshot) {
  const account = snapshot.account;
  updateText("[data-live-equity]", money.format(Number(account.equity)));
  updateText(
    "[data-live-total-pnl]",
    `${signedMoney(account.total_pnl)} since fresh $100K baseline`,
  );
  updateText("[data-live-total-pnl-short]", signedMoney(account.total_pnl));
  updateTone("[data-live-total-pnl], [data-live-total-pnl-short]", account.total_pnl);
  updateText("[data-live-day-pnl]", signedMoney(account.day_pnl));
  updateTone("[data-live-day-pnl]", account.day_pnl);
  updateText("[data-live-day-return]", signedPercent(account.day_return));
  updateText("[data-live-cash]", money.format(Number(account.cash)));
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
  setFreshness(snapshot);
}

function setFeedError() {
  document.querySelectorAll("[data-live-freshness]").forEach((element) => {
    const dot = document.createElement("span");
    dot.className = "live-dot";
    dot.setAttribute("aria-hidden", "true");
    element.replaceChildren(dot, document.createTextNode("Refresh unavailable"));
    element.classList.add("is-error");
  });
  const status = document.querySelector("[data-live-feed-status]");
  if (status) {
    status.textContent = "REFRESH UNAVAILABLE · showing last deployed snapshot";
    status.classList.add("is-error");
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
