import fs from "node:fs/promises";
import { FileBlob, PresentationFile } from "@oai/artifact-tool";

const ROOT = "/Users/lipengyuan/PycharmProjects/alpaca-hackathon";
const TMP = `${ROOT}/tmp/presentation-stable-income-generator-paper-update`;
const INPUT = `${TMP}/template-starter.pptx`;
const SNAPSHOT_PATH = `${TMP}/live-paper-snapshot.json`;
const OUTPUT = `${ROOT}/output/presentation/stable-income-generator-hackathon-deck-v3.pptx`;
const PAPER_PAGE = "https://lipengyuan1994.github.io/alpaca-hackathon/paper-performance.html";
const PAPER_JSON = "https://lipengyuan1994.github.io/alpaca-hackathon/assets/data/live-paper-snapshot.json";

const locs = {
  slide6: {
    subtitle: [72, 137, 1050, 40],
    equity: [72, 225, 500, 78],
    totalPnl: [72, 373, 230, 52],
    baseline: [76, 426, 320, 30],
    dayPnl: [72, 485, 330, 38],
    account: [72, 565, 500, 28],
    fillsTitle: [660, 210, 400, 28],
    rows: [
      [[660, 265, 90, 26], [760, 265, 175, 26], [940, 265, 170, 26], [1120, 265, 75, 26]],
      [[660, 327, 90, 26], [760, 327, 175, 26], [940, 327, 170, 26], [1120, 327, 75, 26]],
      [[660, 389, 90, 26], [760, 389, 175, 26], [940, 389, 170, 26], [1120, 389, 75, 26]],
      [[660, 451, 90, 26], [760, 451, 175, 26], [940, 451, 170, 26], [1120, 451, 75, 26]],
      [[660, 513, 90, 26], [760, 513, 175, 26], [940, 513, 170, 26], [1120, 513, 75, 26]],
    ],
    captured: [660, 596, 535, 30],
  },
  slide9: {
    operating: [400, 325, 475, 34],
    proofUrl: [925, 397, 260, 58],
  },
};

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const percent = new Intl.NumberFormat("en-US", {
  style: "percent",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const timeEt = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "numeric",
  minute: "2-digit",
  hour12: false,
});
const captureEt = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  hour12: true,
  timeZoneName: "short",
});

function signedMoney(value) {
  const amount = Number(value);
  return `${amount >= 0 ? "+" : "-"}${money.format(Math.abs(amount))}`;
}

function signedPercent(value) {
  const amount = Number(value);
  return `${amount >= 0 ? "+" : "-"}${percent.format(Math.abs(amount))}`;
}

function resolveShape(deck, records, slideNumber, bbox) {
  const matches = records.filter((record) =>
    record.kind === "textbox" &&
    record.slide === slideNumber &&
    Array.isArray(record.bbox) &&
    record.bbox.every((value, index) => Math.abs(value - bbox[index]) < 0.5),
  );
  if (matches.length !== 1) {
    throw new Error(`Expected one textbox on slide ${slideNumber} at ${bbox}; found ${matches.length}`);
  }
  const shape = deck.resolve(matches[0].id);
  if (!shape?.text) throw new Error(`Text shape not found: ${matches[0].id}`);
  return shape;
}

function rewrite(deck, records, slideNumber, bbox, value) {
  resolveShape(deck, records, slideNumber, bbox).text.set(value);
}

function actionLabel(action) {
  return String(action).replaceAll("_", " ");
}

function contractLabel(order) {
  const strike = Number(order.contract.strike);
  const strikeLabel = Number.isInteger(strike) ? strike.toFixed(0) : String(strike);
  return `${order.contract.underlying} $${strikeLabel} ${order.contract.option_type}`;
}

async function writeBlob(path, blob) {
  await fs.writeFile(path, new Uint8Array(await blob.arrayBuffer()));
}

async function main() {
  const snapshot = JSON.parse(await fs.readFile(SNAPSHOT_PATH, "utf8"));
  if (snapshot.schema_version !== "stable-income-generator-live-paper/v3") {
    throw new Error(`Unexpected snapshot schema: ${snapshot.schema_version}`);
  }
  if (snapshot.source !== "broker_reported_paper" || snapshot.publication_scope?.paper_only !== true) {
    throw new Error("Snapshot is not approved broker-reported paper evidence");
  }
  if (snapshot.strategy?.strategy_id !== "v13.5" || snapshot.strategy?.underlying !== "QQQ") {
    throw new Error("Snapshot does not describe the active V13.5 QQQ paper strategy");
  }

  const account = snapshot.account;
  const orders = snapshot.recent_filled_system_orders;
  if (!Array.isArray(orders) || orders.length < 5) {
    throw new Error(`Expected at least five recent system fills; found ${orders?.length ?? 0}`);
  }

  await fs.mkdir(`${TMP}/final-renders`, { recursive: true });
  await fs.mkdir(`${ROOT}/output/presentation`, { recursive: true });

  const deck = await PresentationFile.importPptx(await FileBlob.load(INPUT));
  if (deck.slides.items.length !== 9) {
    throw new Error(`Expected 9 slides, found ${deck.slides.items.length}`);
  }

  const before = await deck.inspect({ kind: "textbox", maxChars: 120000 });
  const records = before.ndjson
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));

  rewrite(
    deck,
    records,
    6,
    locs.slide6.subtitle,
    "The new public dashboard exposes a hash-bound snapshot, P&L history, and the ten latest V13.5 system fills.",
  );
  rewrite(deck, records, 6, locs.slide6.equity, money.format(Number(account.equity)));
  rewrite(deck, records, 6, locs.slide6.totalPnl, signedMoney(account.total_pnl));
  rewrite(deck, records, 6, locs.slide6.baseline, `${signedPercent(account.total_return)} since fresh $100K baseline`);
  rewrite(deck, records, 6, locs.slide6.dayPnl, `${signedMoney(account.day_pnl)} today (${signedPercent(account.day_return)})`);
  rewrite(deck, records, 6, locs.slide6.account, `${account.status} | Account ${account.account_id}`);
  rewrite(deck, records, 6, locs.slide6.fillsTitle, `LATEST 5 OF ${orders.length} SYSTEM FILLS`);

  orders.slice(0, 5).forEach((order, index) => {
    const [timeBox, actionBox, contractBox, priceBox] = locs.slide6.rows[index];
    rewrite(deck, records, 6, timeBox, `${timeEt.format(new Date(order.filled_at))} ET`);
    rewrite(deck, records, 6, actionBox, actionLabel(order.action));
    rewrite(deck, records, 6, contractBox, contractLabel(order));
    rewrite(deck, records, 6, priceBox, money.format(Number(order.average_fill_price)));
  });

  const generated = new Date(snapshot.generated_at);
  rewrite(
    deck,
    records,
    6,
    locs.slide6.captured,
    `Captured ${captureEt.format(generated)} | Paper trading is simulated`,
  );

  const slide6 = deck.slides.getItem(5);
  slide6.speakerNotes.setText([
    "[Sources]",
    `- ${PAPER_PAGE}`,
    `- ${PAPER_JSON}`,
    `- ${SNAPSHOT_PATH} (generated_at ${snapshot.generated_at}; artifact_hash ${snapshot.artifact_hash})`,
    `- Broker-reported paper snapshot: equity ${money.format(account.equity)}, cumulative P&L ${signedMoney(account.total_pnl)} (${signedPercent(account.total_return)}), day P&L ${signedMoney(account.day_pnl)} (${signedPercent(account.day_return)}).`,
    `- ${orders.length} recent filled system orders were published; the slide shows the latest five.`,
    "- Paper execution and capital are simulated. Figures on the public dashboard continue to refresh after this slide's frozen capture time.",
  ].join("\n"));

  rewrite(deck, records, 9, locs.slide9.operating, "V13.5 on Alpaca paper with public P&L + fill ledger");
  rewrite(deck, records, 9, locs.slide9.proofUrl, "alpaca-hackathon/\npaper-performance.html");
  const proofUrlShape = resolveShape(deck, records, 9, locs.slide9.proofUrl);
  for (const label of ["alpaca-hackathon/", "paper-performance.html"]) {
    const range = proofUrlShape.text.get(label);
    range.link = { uri: PAPER_PAGE, isExternal: true };
    range.fill = "#A8F0C6";
    range.bold = true;
    range.underline = "sng";
  }

  const slide9 = deck.slides.getItem(8);
  slide9.speakerNotes.setText([
    "[Sources]",
    `- ${PAPER_PAGE}`,
    "- https://lipengyuan1994.github.io/alpaca-hackathon/",
    "- https://github.com/lipengyuan1994/alpaca-hackathon",
    `- ${ROOT}/docs/deployment/judge-reproduce.md`,
  ].join("\n"));

  for (const [index, slide] of deck.slides.items.entries()) {
    const stem = `slide-${String(index + 1).padStart(2, "0")}`;
    await writeBlob(`${TMP}/final-renders/${stem}.png`, await deck.export({ slide, format: "png", scale: 1 }));
    const layout = await slide.export({ format: "layout" });
    await fs.writeFile(`${TMP}/final-renders/${stem}.layout.json`, await layout.text());
  }
  await writeBlob(`${TMP}/final-montage.webp`, await deck.export({ format: "webp", montage: true, scale: 1 }));

  const inspection = await deck.inspect({
    kind: "slide,textbox,shape,image,notes,layout",
    maxChars: 120000,
  });
  await fs.writeFile(`${TMP}/final-inspection.ndjson`, inspection.ndjson);

  const pptx = await PresentationFile.exportPptx(deck);
  await pptx.save(OUTPUT);
  console.log(`WROTE ${OUTPUT}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
