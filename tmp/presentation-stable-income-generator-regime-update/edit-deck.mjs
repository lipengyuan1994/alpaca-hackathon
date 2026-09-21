import fs from "node:fs/promises";
import { FileBlob, PresentationFile } from "@oai/artifact-tool";

const ROOT = "/Users/lipengyuan/PycharmProjects/alpaca-hackathon";
const TMP = `${ROOT}/tmp/presentation-stable-income-generator-regime-update`;
const INPUT = `${TMP}/template-starter.pptx`;
const OUTPUT = `${ROOT}/output/presentation/stable-income-generator-hackathon-deck-v2.pptx`;

const C = {
  bg2: "#0A1D17",
  ink: "#EDF7F1",
  muted: "#A3B8AC",
  faint: "#6F8579",
  mint: "#6EE7B7",
  mint2: "#A8F0C6",
  amber: "#F3C96B",
  line: "#24483A",
};

function shapeText(shape) {
  return shape.text?.toString?.() ?? "";
}

function findShape(slide, predicate, description) {
  const shape = slide.shapes.items.find(predicate);
  if (!shape) throw new Error(`Shape not found: ${description}`);
  return shape;
}

function rewrite(shape, value) {
  shape.text.set(value);
}

function addText(slide, text, position, options = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    name: options.name ?? "",
    position,
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text.set(text);
  shape.text.style = {
    fontSize: options.fontSize ?? 18,
    color: options.color ?? C.ink,
    bold: options.bold ?? false,
    alignment: options.alignment ?? "left",
    fontFamily: options.fontFamily ?? "Calibri",
  };
  return shape;
}

function addBox(slide, position, options = {}) {
  return slide.shapes.add({
    geometry: options.geometry ?? "roundRect",
    name: options.name ?? "",
    position,
    fill: options.fill ?? C.bg2,
    line: options.line ?? { style: "solid", fill: C.line, width: 2 },
    borderRadius: options.borderRadius ?? "rounded-xl",
  });
}

function addRule(slide, left, top, width, color = C.line, thickness = 1) {
  return slide.shapes.add({
    geometry: "line",
    position: { left, top, width, height: 1 },
    fill: "none",
    line: { style: "solid", fill: color, width: thickness },
  });
}

function addVRule(slide, left, top, height, color = C.line, thickness = 1) {
  return slide.shapes.add({
    geometry: "line",
    position: { left, top, width: 1, height },
    fill: "none",
    line: { style: "solid", fill: color, width: thickness },
  });
}

function findPageNumber(slide) {
  return findShape(
    slide,
    (shape) => shape.frame.left >= 1140 && shape.frame.top >= 680,
    `page number on slide ${slide.index + 1}`,
  );
}

async function writeBlob(path, blob) {
  await fs.writeFile(path, new Uint8Array(await blob.arrayBuffer()));
}

async function main() {
  await fs.mkdir(`${TMP}/final-renders`, { recursive: true });
  await fs.mkdir(`${ROOT}/output/presentation`, { recursive: true });

  const deck = await PresentationFile.importPptx(await FileBlob.load(INPUT));
  if (deck.slides.items.length !== 9) {
    throw new Error(`Expected 9 starter slides, found ${deck.slides.items.length}`);
  }

  const slide = deck.slides.getItem(4);
  const kicker = findShape(slide, (shape) => shape.name === "section-kicker", "slide 5 kicker");
  const title = findShape(slide, (shape) => shape.name === "slide-title", "slide 5 title");
  const subtitle = findShape(slide, (shape) => shape.name === "slide-subtitle", "slide 5 subtitle");
  const topMetric = findShape(slide, (shape) => shape.frame.left === 810 && shape.frame.top === 46, "slide 5 top metric");
  const methodology = findShape(slide, (shape) => shape.frame.top === 626, "slide 5 methodology footer");

  rewrite(kicker, "BULL + BEAR EVIDENCE");
  rewrite(title, "Participation in rallies. Resilience in selloffs.");
  rewrite(
    subtitle,
    "One deterministic posture switch was tested across rising conditions and the four largest observed QQQ drawdowns.",
  );
  rewrite(topMetric, "FULL SAMPLE +30.29%");
  rewrite(
    methodology,
    "Exploratory in-sample booked-equity proxy | Prior-close 50-session SMA regime | Not proof of future results",
  );
  rewrite(findPageNumber(slide), "05");

  for (const image of [...slide.images.items]) image.delete();

  addText(slide, "BULL PARTICIPATION", { left: 72, top: 198, width: 260, height: 25 }, {
    fontSize: 14, color: C.mint, bold: true,
  });
  addText(slide, "V13.5 +43.43%", { left: 72, top: 225, width: 350, height: 54 }, {
    fontSize: 36, color: C.mint, bold: true,
  });
  addText(slide, "vs QQQ +37.05%", { left: 415, top: 237, width: 300, height: 40 }, {
    fontSize: 24, color: C.amber, bold: true,
  });
  addText(slide, "428 uptrend-labeled sessions", { left: 825, top: 237, width: 380, height: 38 }, {
    fontSize: 20, color: C.ink, bold: true, alignment: "right",
  });
  addText(
    slide,
    "Conditional compounded return across sessions classified using information available before each decision.",
    { left: 72, top: 282, width: 1000, height: 30 },
    { fontSize: 16, color: C.muted },
  );
  addRule(slide, 72, 322, 1136, C.line, 1);

  addText(slide, "QQQ DRAWDOWN EPISODES", { left: 72, top: 338, width: 320, height: 25 }, {
    fontSize: 14, color: C.amber, bold: true,
  });
  addText(slide, "Peak to trough", { left: 975, top: 338, width: 230, height: 25 }, {
    fontSize: 14, color: C.faint, alignment: "right",
  });

  const bandLeft = 72;
  const bandTop = 370;
  const bandWidth = 1136;
  const bandHeight = 170;
  const columnWidth = bandWidth / 4;
  addBox(slide, { left: bandLeft, top: bandTop, width: bandWidth, height: bandHeight }, {
    fill: "none",
    line: { style: "solid", fill: C.line, width: 2 },
    borderRadius: "rounded-xl",
  });
  for (let i = 1; i < 4; i += 1) {
    addVRule(slide, bandLeft + columnWidth * i, bandTop, bandHeight, C.line, 1);
  }

  const episodes = [
    ["Feb-Apr 2025", "QQQ -22.84%", "V13.5 -6.97%", false],
    ["Jul-Aug 2024", "QQQ -13.54%", "V13.5 +4.14%", true],
    ["Oct 2025-Mar 2026", "QQQ -12.20%", "V13.5 +0.14%", true],
    ["Jun-Jul 2026", "QQQ -11.33%", "V13.5 -1.87%", false],
  ];

  episodes.forEach(([period, qqq, strategy, positive], index) => {
    const x = bandLeft + columnWidth * index + 24;
    addText(slide, period, { left: x, top: bandTop + 17, width: columnWidth - 48, height: 25 }, {
      fontSize: 15, color: C.muted, bold: true,
    });
    addText(slide, qqq, { left: x, top: bandTop + 61, width: columnWidth - 48, height: 38 }, {
      fontSize: 25, color: C.amber, bold: true,
    });
    addText(slide, strategy, { left: x, top: bandTop + 111, width: columnWidth - 48, height: 32 }, {
      fontSize: 18, color: positive ? C.mint : C.mint2, bold: true,
    });
  });

  addText(slide, "+30.29% total return", { left: 72, top: 569, width: 340, height: 32 }, {
    fontSize: 19, color: C.mint, bold: true,
  });
  addText(slide, "-7.15% max booked-equity drawdown", { left: 425, top: 569, width: 410, height: 32 }, {
    fontSize: 19, color: C.ink, bold: true, alignment: "center",
  });
  addText(slide, "2.44 Sortino", { left: 850, top: 569, width: 355, height: 32 }, {
    fontSize: 19, color: C.mint2, bold: true, alignment: "right",
  });

  slide.speakerNotes.setText([
    "[Sources]",
    `- ${ROOT}/docs/assets/data/v13-5-benchmark.json`,
    "- Artifact hash: sha256:f37bbf10e548500670129193500fe2cc2ece646b9992bf18409dbba5fb49ff9a",
    "- Bull comparison: conditional compounded return across 428 UPTREND-labeled sessions; V13.5 +43.4262%, QQQ +37.0478%.",
    "- Bear comparison: four largest observed QQQ peak-to-trough drawdowns above 5% and corresponding V13.5 booked-equity changes.",
    "- Method caveat: exploratory in-sample proxy; historical option bars are non-executable proxies; booked equity is not continuous mark-to-market; not proof of future performance.",
  ].join("\n"));

  [6, 7, 8, 9].forEach((page, index) => {
    rewrite(findPageNumber(deck.slides.getItem(index + 5)), String(page).padStart(2, "0"));
  });

  for (const [index, currentSlide] of deck.slides.items.entries()) {
    const stem = `slide-${String(index + 1).padStart(2, "0")}`;
    await writeBlob(
      `${TMP}/final-renders/${stem}.png`,
      await deck.export({ slide: currentSlide, format: "png", scale: 1 }),
    );
    const layout = await currentSlide.export({ format: "layout" });
    await fs.writeFile(`${TMP}/final-renders/${stem}.layout.json`, await layout.text());
  }
  await writeBlob(`${TMP}/final-montage.webp`, await deck.export({ format: "webp", montage: true, scale: 1 }));

  const pptx = await PresentationFile.exportPptx(deck);
  await pptx.save(OUTPUT);

  const inspection = await deck.inspect({
    kind: "slide,textbox,shape,image,notes,layout",
    maxChars: 100000,
  });
  await fs.writeFile(`${TMP}/final-inspection.ndjson`, inspection.ndjson);
  console.log(`WROTE ${OUTPUT}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
