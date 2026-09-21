import fs from "node:fs/promises";
import { FileBlob, PresentationFile } from "@oai/artifact-tool";

const ROOT = "/Users/lipengyuan/PycharmProjects/alpaca-hackathon";
const TMP = `${ROOT}/tmp/presentation-stable-income-generator-method-appendix`;
const INPUT = `${TMP}/template-starter.pptx`;
const OUTPUT = `${ROOT}/output/presentation/stable-income-generator-hackathon-deck-v4.pptx`;
const BENCHMARK = `${ROOT}/docs/assets/data/v13-5-benchmark.json`;
const CALCULATION = `${ROOT}/packages/research_data/stable_income_generator_benchmark.py`;
const ARTIFACT_HASH = "sha256:f37bbf10e548500670129193500fe2cc2ece646b9992bf18409dbba5fb49ff9a";

const slide5Locs = {
  scopeKicker: [72, 198, 260, 25],
  sessionScope: [825, 237, 380, 38],
  interpretation: [72, 282, 1000, 30],
};

const slide10Locs = {
  kicker: [72, 42, 500, 28],
  title: [72, 78, 1000, 58],
  subtitle: [72, 137, 1050, 40],
  formula: [72, 235, 470, 82],
  formulaDetail: [75, 318, 465, 110],
  step1Title: [710, 216, 445, 34],
  step1Body: [710, 257, 445, 32],
  step2Title: [710, 334, 445, 34],
  step2Body: [710, 375, 445, 32],
  step3Title: [710, 452, 445, 34],
  step3Body: [710, 493, 445, 32],
  page: [1148, 687, 60, 20],
};

function resolveTextShape(deck, records, slideNumber, bbox) {
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
  resolveTextShape(deck, records, slideNumber, bbox).text.set(value);
}

async function writeBlob(path, blob) {
  await fs.writeFile(path, new Uint8Array(await blob.arrayBuffer()));
}

async function main() {
  await fs.mkdir(`${TMP}/final-renders`, { recursive: true });
  await fs.mkdir(`${TMP}/final-layout`, { recursive: true });
  await fs.mkdir(`${ROOT}/output/presentation`, { recursive: true });

  const deck = await PresentationFile.importPptx(await FileBlob.load(INPUT));
  if (deck.slides.items.length !== 10) {
    throw new Error(`Expected 10 slides, found ${deck.slides.items.length}`);
  }

  const before = await deck.inspect({
    kind: "slide,textbox,shape,notes,layout",
    include: "id,slide,name,bbox,textPreview,isPlaceholder,placeholders",
    maxChars: 160000,
  });
  const records = before.ndjson
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));

  rewrite(deck, records, 5, slide5Locs.scopeKicker, "UPTREND-ONLY RETURN");
  rewrite(deck, records, 5, slide5Locs.sessionScope, "428 uptrend sessions only");
  rewrite(
    deck,
    records,
    5,
    slide5Locs.interpretation,
    "Conditional compounded return on UPTREND-labeled sessions; not the full-period backtest result.",
  );

  rewrite(deck, records, 10, slide10Locs.kicker, "APPENDIX · CALCULATION METHOD");
  rewrite(deck, records, 10, slide10Locs.title, "How the +43.43% comparison is calculated");
  rewrite(
    deck,
    records,
    10,
    slide10Locs.subtitle,
    "A regime-conditioned attribution—not the full-period result; V13.5 uses booked equity.",
  );
  rewrite(deck, records, 10, slide10Locs.formula, "∏ (1 + rₜ) − 1");
  rewrite(
    deck,
    records,
    10,
    slide10Locs.formulaDetail,
    "over 428 UPTREND sessions\nV13.5 +43.43% | QQQ +37.05%",
  );
  rewrite(deck, records, 10, slide10Locs.step1Title, "Label each session");
  rewrite(deck, records, 10, slide10Locs.step1Body, "Prior QQQ close > prior 50-session SMA.");
  rewrite(deck, records, 10, slide10Locs.step2Title, "Select and compound");
  rewrite(deck, records, 10, slide10Locs.step2Body, "Keep UPTREND dates; multiply (1 + daily return).");
  rewrite(deck, records, 10, slide10Locs.step3Title, "Read in full-sample context");
  rewrite(deck, records, 10, slide10Locs.step3Body, "644 sessions: V13.5 +30.29% | QQQ +66.71%.");
  rewrite(deck, records, 10, slide10Locs.page, "10");

  deck.slides.getItem(4).speakerNotes.setText([
    "[Sources]",
    `- ${BENCHMARK}`,
    `- ${CALCULATION} (regime labels at lines 125-140; conditional compounding at lines 143-157)`,
    `- Artifact hash: ${ARTIFACT_HASH}`,
    "- The +43.43% versus +37.05% comparison is restricted to 428 UPTREND-labeled sessions.",
    "- It is a conditional return attribution, not the complete 644-session result.",
  ].join("\n"));

  deck.slides.getItem(9).speakerNotes.setText([
    "[Sources]",
    `- ${BENCHMARK}`,
    `- ${CALCULATION} (regime labels at lines 125-140; conditional compounding at lines 143-157)`,
    `- Artifact hash: ${ARTIFACT_HASH}`,
    "- Regime rule: UPTREND when the previous QQQ close is above the previous 50-session simple moving average.",
    "- Conditional return = product over selected sessions of (1 + daily return), minus 1.",
    "- Conditional result: 428 UPTREND sessions; V13.5 +43.4262%; QQQ +37.0478%.",
    "- Full result: 644 sessions; V13.5 +30.2858%; QQQ +66.7091%.",
    "- V13.5 is a frozen booked-equity proxy; open positions are not continuously marked to market.",
    "- QQQ is split-adjusted Alpaca IEX price-only return and excludes dividends, fees, slippage, and taxes.",
    "- The comparison is exploratory and in-sample, not proof of future performance.",
  ].join("\n"));

  for (const [index, slide] of deck.slides.items.entries()) {
    const stem = `slide-${String(index + 1).padStart(2, "0")}`;
    await writeBlob(
      `${TMP}/final-renders/${stem}.png`,
      await deck.export({ slide, format: "png", scale: 1 }),
    );
    const layout = await slide.export({ format: "layout" });
    await fs.writeFile(`${TMP}/final-layout/${stem}.layout.json`, await layout.text());
  }

  await writeBlob(
    `${TMP}/final-montage.webp`,
    await deck.export({ format: "webp", montage: true, scale: 1 }),
  );

  const inspection = await deck.inspect({
    kind: "slide,textbox,shape,image,table,chart,notes,thread,layout",
    include: "id,slide,name,title,bbox,text,textPreview,isPlaceholder,placeholders,comments",
    maxChars: 220000,
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
