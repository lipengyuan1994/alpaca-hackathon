import fs from "node:fs/promises";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const ROOT = "/Users/lipengyuan/PycharmProjects/alpaca-hackathon";
const TMP = `${ROOT}/tmp/presentation-stable-income-generator`;
const OUTPUT = `${ROOT}/output/presentation/stable-income-generator-hackathon-deck.pptx`;
const BANNER = `${ROOT}/docs/assets/stable-income-generator-banner.png`;
const PERFORMANCE = `${TMP}/assets/v13-5-performance.png`;

const C = {
  bg: "#06130F",
  bg2: "#0A1D17",
  bg3: "#102A21",
  ink: "#EDF7F1",
  muted: "#A3B8AC",
  faint: "#6F8579",
  mint: "#6EE7B7",
  mint2: "#A8F0C6",
  amber: "#F3C96B",
  red: "#FF8A7A",
  line: "#24483A",
  white: "#FFFFFF",
};

const FONT_DISPLAY = "Georgia";
const FONT_BODY = "Arial";
const FONT_MONO = "Menlo";

function addText(slide, text, position, options = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    name: options.name,
    position,
    fill: options.fill ?? "none",
    line: options.line ?? { style: "solid", fill: "none", width: 0 },
    ...(options.borderRadius ? { borderRadius: options.borderRadius } : {}),
  });
  shape.text = text;
  shape.text.style = {
    fontSize: options.fontSize ?? 18,
    color: options.color ?? C.ink,
    bold: options.bold ?? false,
    italic: options.italic ?? false,
    alignment: options.alignment ?? "left",
    fontFamily: options.fontFamily ?? FONT_BODY,
  };
  return shape;
}

function addBox(slide, position, options = {}) {
  return slide.shapes.add({
    geometry: options.geometry ?? "roundRect",
    name: options.name,
    position,
    fill: options.fill ?? C.bg2,
    line: options.line ?? { style: "solid", fill: C.line, width: 1 },
    borderRadius: options.borderRadius ?? "rounded-xl",
    ...(options.shadow ? { shadow: options.shadow } : {}),
  });
}

function addRule(slide, left, top, width, color = C.line, thickness = 2) {
  return slide.shapes.add({
    geometry: "line",
    position: { left, top, width, height: 1 },
    fill: "none",
    line: { style: "solid", fill: color, width: thickness },
  });
}

function addVRule(slide, left, top, height, color = C.line, thickness = 2) {
  return slide.shapes.add({
    geometry: "line",
    position: { left, top, width: 1, height },
    fill: "none",
    line: { style: "solid", fill: color, width: thickness },
  });
}

function addArrow(slide, left, top, width = 42) {
  return slide.shapes.add({
    geometry: "rightArrow",
    position: { left, top, width, height: 18 },
    fill: C.mint,
    line: { style: "solid", fill: C.mint, width: 0 },
  });
}

function addDownArrow(slide, left, top) {
  return slide.shapes.add({
    geometry: "downArrow",
    position: { left, top, width: 18, height: 28 },
    fill: C.mint,
    line: { style: "solid", fill: C.mint, width: 0 },
  });
}

function addTitle(slide, kicker, title, subtitle) {
  addText(slide, kicker.toUpperCase(), { left: 72, top: 42, width: 500, height: 28 }, {
    fontSize: 14, color: C.mint, bold: true, name: "section-kicker",
  });
  addText(slide, title, { left: 72, top: 78, width: 1000, height: 58 }, {
    fontSize: 40, color: C.ink, fontFamily: FONT_DISPLAY, name: "slide-title",
  });
  if (subtitle) {
    addText(slide, subtitle, { left: 72, top: 137, width: 1050, height: 40 }, {
      fontSize: 18, color: C.muted, name: "slide-subtitle",
    });
  }
}

function addFooter(slide, page) {
  addRule(slide, 72, 676, 1136, C.line, 1);
  addText(slide, "STABLE INCOME GENERATOR", { left: 72, top: 687, width: 330, height: 20 }, {
    fontSize: 11, color: C.faint, bold: true,
  });
  addText(slide, String(page).padStart(2, "0"), { left: 1148, top: 687, width: 60, height: 20 }, {
    fontSize: 11, color: C.faint, bold: true, alignment: "right",
  });
}

function setSources(slide, sources) {
  slide.speakerNotes.textFrame.setText(
    `[Sources]\n${sources.map((source) => `- ${source}`).join("\n")}`,
  );
}

function addNode(slide, x, y, width, title, detail, accent = C.mint) {
  const node = addBox(slide, { left: x, top: y, width, height: 120 }, {
    fill: C.bg3,
    line: { style: "solid", fill: accent, width: 2 },
    borderRadius: "rounded-xl",
  });
  addText(slide, title, { left: x + 14, top: y + 18, width: width - 28, height: 34 }, {
    fontSize: 20, color: C.ink, bold: true, alignment: "center",
  });
  addText(slide, detail, { left: x + 12, top: y + 62, width: width - 24, height: 42 }, {
    fontSize: 15, color: accent, bold: true, alignment: "center",
  });
  return node;
}

async function writeBlob(path, blob) {
  await fs.writeFile(path, new Uint8Array(await blob.arrayBuffer()));
}

async function readImageBytes(path) {
  return new Uint8Array(await fs.readFile(path));
}

async function build() {
  await fs.mkdir(`${TMP}/renders`, { recursive: true });
  await fs.mkdir(`${ROOT}/output/presentation`, { recursive: true });

  const deck = Presentation.create({ slideSize: { width: 1280, height: 720 } });
  const bannerBytes = await readImageBytes(BANNER);
  const performanceBytes = await readImageBytes(PERFORMANCE);

  // 1. Minimal cover using the website banner.
  {
    const slide = deck.slides.add();
    slide.background.fill = C.bg;
    slide.images.add({
      blob: bannerBytes,
      contentType: "image/png",
      alt: "Emerald growth path above a containerized trading system",
      fit: "cover",
      position: { left: 0, top: 0, width: 1280, height: 720 },
    });
    addBox(slide, { left: 0, top: 0, width: 615, height: 720 }, {
      geometry: "rect", fill: C.bg, line: { style: "solid", fill: "none", width: 0 }, borderRadius: 0,
    });
    addRule(slide, 72, 112, 82, C.mint, 5);
    addText(slide, "ALPACA HACKATHON 2026", { left: 72, top: 58, width: 380, height: 28 }, {
      fontSize: 14, color: C.mint, bold: true,
    });
    addText(slide, "Stable Income\nGenerator", { left: 72, top: 148, width: 500, height: 180 }, {
      fontSize: 60, color: C.ink, fontFamily: FONT_DISPLAY,
    });
    addText(slide, "Automated options income.\nDeterministic authority. Public proof.", { left: 72, top: 365, width: 450, height: 92 }, {
      fontSize: 24, color: C.mint2,
    });
    addText(slide, "V13.5 QQQ wheel | Alpaca paper | Containerized", { left: 72, top: 610, width: 480, height: 30 }, {
      fontSize: 16, color: C.muted,
    });
    setSources(slide, [
      `${ROOT}/docs/index.html`,
      `${ROOT}/docs/assets/stable-income-generator-banner.png`,
      "https://lipengyuan1994.github.io/alpaca-hackathon/",
    ]);
  }

  // 2. The product problem.
  {
    const slide = deck.slides.add();
    slide.background.fill = C.bg;
    addTitle(slide, "The mandate", "Income is a path-quality problem", "Maximum upside and dependable income are different objectives.");
    addText(slide, "STEADY", { left: 72, top: 235, width: 470, height: 82 }, {
      fontSize: 68, color: C.mint, bold: true, fontFamily: FONT_DISPLAY,
    });
    addText(slide, "beats spectacular\nwhen cashflow is the goal.", { left: 75, top: 318, width: 465, height: 110 }, {
      fontSize: 31, color: C.ink, fontFamily: FONT_DISPLAY,
    });
    addVRule(slide, 590, 215, 320, C.line, 2);
    const items = [
      ["01", "Generate repeatable premium", "Fully collateralized option expressions."],
      ["02", "Adapt when QQQ trend changes", "One wheel, two deterministic postures."],
      ["03", "Prove operation, not intention", "Broker telemetry, audit records, and replay."],
    ];
    items.forEach(([index, heading, detail], i) => {
      const top = 220 + i * 118;
      addText(slide, index, { left: 640, top, width: 52, height: 30 }, {
        fontSize: 16, color: C.amber, bold: true,
      });
      addText(slide, heading, { left: 710, top: top - 4, width: 445, height: 34 }, {
        fontSize: 24, color: C.ink, bold: true,
      });
      addText(slide, detail, { left: 710, top: top + 37, width: 445, height: 32 }, {
        fontSize: 17, color: C.muted,
      });
    });
    addFooter(slide, 2);
    setSources(slide, [
      `${ROOT}/docs/index.html`,
      `${ROOT}/docs/assets/data/v13-5-benchmark.json`,
    ]);
  }

  // 3. Strategy mechanics and regime posture.
  {
    const slide = deck.slides.add();
    slide.background.fill = C.bg;
    addTitle(slide, "Inside V13.5", "One QQQ wheel. Two market postures.", "The prior close versus its prior 50-session SMA changes strike distance - not discretion.");
    addVRule(slide, 640, 205, 280, C.line, 2);

    addText(slide, "UPTREND", { left: 90, top: 218, width: 260, height: 32 }, {
      fontSize: 18, color: C.mint, bold: true,
    });
    addText(slide, "1% OTM put", { left: 90, top: 270, width: 420, height: 54 }, {
      fontSize: 40, color: C.ink, fontFamily: FONT_DISPLAY,
    });
    addText(slide, "3% OTM covered call", { left: 90, top: 333, width: 440, height: 45 }, {
      fontSize: 25, color: C.mint2, bold: true,
    });
    addText(slide, "Collect put premium nearer the rising market while preserving more upside room on calls.", { left: 90, top: 410, width: 445, height: 72 }, {
      fontSize: 18, color: C.muted,
    });

    addText(slide, "DOWNTREND", { left: 700, top: 218, width: 280, height: 32 }, {
      fontSize: 18, color: C.amber, bold: true,
    });
    addText(slide, "3% OTM put", { left: 700, top: 270, width: 420, height: 54 }, {
      fontSize: 40, color: C.ink, fontFamily: FONT_DISPLAY,
    });
    addText(slide, "1% OTM covered call", { left: 700, top: 333, width: 440, height: 45 }, {
      fontSize: 25, color: C.amber, bold: true,
    });
    addText(slide, "Move puts farther away while bringing calls closer to monetize a cautious regime.", { left: 700, top: 410, width: 445, height: 72 }, {
      fontSize: 18, color: C.muted,
    });

    addRule(slide, 72, 530, 1136, C.line, 1);
    const facts = ["QQQ ONLY", "7-14 DTE", "1 CONTRACT", ">15% PROFIT", "2% DAILY STOP"];
    facts.forEach((fact, i) => {
      addText(slide, fact, { left: 72 + i * 228, top: 565, width: 200, height: 36 }, {
        fontSize: 17, color: i === 4 ? C.amber : C.mint2, bold: true, alignment: "center",
      });
    });
    addText(slide, "Cash-secured puts and share-covered calls only", { left: 340, top: 618, width: 600, height: 28 }, {
      fontSize: 16, color: C.faint, alignment: "center",
    });
    addFooter(slide, 3);
    setSources(slide, [
      `${ROOT}/docs/index.html#strategy`,
      `${ROOT}/configs/paper/v13_5_qqq.yaml`,
      `${ROOT}/docs/deployment/PAPER_WHEEL_V13_5.md`,
    ]);
  }

  // 4. Backtest chart and honest positioning.
  {
    const slide = deck.slides.add();
    slide.background.fill = C.bg;
    addTitle(slide, "Frozen benchmark study", "The backtest favors steadiness over beta", "V13.5 gave up upside in exchange for a shallower booked-equity drawdown and stronger downside-adjusted return.");
    addText(slide, "-7.15% MDD  |  2.44 SORTINO", { left: 810, top: 46, width: 395, height: 28 }, {
      fontSize: 17, color: C.mint, bold: true, alignment: "right",
    });
    slide.images.add({
      blob: performanceBytes,
      contentType: "image/png",
      alt: "Growth of 100,000 comparing V13.5 booked equity with SPY and QQQ price-only benchmarks",
      fit: "contain",
      position: { left: 70, top: 180, width: 1140, height: 430 },
      geometry: "roundRect",
      borderRadius: "rounded-xl",
    });
    addText(slide, "Exploratory in-sample proxy | 644 sessions | Jan 29, 2024 - Aug 24, 2026 | Not continuous mark-to-market", { left: 75, top: 626, width: 1130, height: 28 }, {
      fontSize: 15, color: C.faint, alignment: "center",
    });
    addFooter(slide, 4);
    setSources(slide, [
      `${ROOT}/docs/assets/data/v13-5-benchmark.json`,
      `${ROOT}/docs/assets/v13-5-performance.svg`,
      `${ROOT}/research/candidates/group_a_wheel_v13_package_20260830/run_manifest.json`,
    ]);
  }

  // 5. Dated public paper evidence.
  {
    const slide = deck.slides.add();
    slide.background.fill = C.bg;
    addTitle(slide, "Broker-reported paper evidence", "The system is already operating on Alpaca paper", "A sanitized public snapshot separates observed paper behavior from historical research.");

    addText(slide, "$100,079.73", { left: 72, top: 225, width: 500, height: 78 }, {
      fontSize: 58, color: C.ink, fontFamily: FONT_DISPLAY,
    });
    addText(slide, "BROKER-REPORTED EQUITY", { left: 76, top: 306, width: 390, height: 26 }, {
      fontSize: 14, color: C.mint, bold: true,
    });
    addText(slide, "+$79.73", { left: 72, top: 373, width: 230, height: 52 }, {
      fontSize: 38, color: C.mint, bold: true,
    });
    addText(slide, "since fresh $100K baseline", { left: 76, top: 426, width: 320, height: 30 }, {
      fontSize: 17, color: C.muted,
    });
    addText(slide, "+$150.00 today", { left: 72, top: 485, width: 330, height: 38 }, {
      fontSize: 24, color: C.mint2, bold: true,
    });
    addText(slide, "Account 4c0db2cd-f24c-419f-a9fb-339a24cdef1c", { left: 72, top: 565, width: 500, height: 28 }, {
      fontSize: 15, color: C.faint, fontFamily: FONT_MONO,
    });

    addVRule(slide, 610, 205, 410, C.line, 2);
    addText(slide, "MOST RECENT SYSTEM FILLS", { left: 660, top: 210, width: 400, height: 28 }, {
      fontSize: 14, color: C.amber, bold: true,
    });
    const fills = [
      ["11:42 ET", "SELL TO OPEN", "QQQ $704 PUT", "$2.78"],
      ["11:41 ET", "BUY TO CLOSE", "QQQ $702 PUT", "$2.37"],
      ["10:31 ET", "SELL TO OPEN", "QQQ $702 PUT", "$2.86"],
      ["10:30 ET", "BUY TO CLOSE", "QQQ $700 PUT", "$2.57"],
      ["10:01 ET", "SELL TO OPEN", "QQQ $700 PUT", "$3.06"],
    ];
    fills.forEach(([time, action, contract, price], i) => {
      const top = 265 + i * 62;
      addText(slide, time, { left: 660, top, width: 90, height: 26 }, { fontSize: 16, color: C.faint });
      addText(slide, action, { left: 760, top, width: 175, height: 26 }, {
        fontSize: 16, color: action.startsWith("SELL") ? C.mint : C.amber, bold: true,
      });
      addText(slide, contract, { left: 940, top, width: 170, height: 26 }, { fontSize: 16, color: C.ink, bold: true });
      addText(slide, price, { left: 1120, top, width: 75, height: 26 }, { fontSize: 16, color: C.ink, alignment: "right" });
      if (i < fills.length - 1) addRule(slide, 660, top + 40, 535, C.line, 1);
    });
    addText(slide, "Captured Sep 2, 2026 at 7:13 PM EDT | Paper trading is simulated", { left: 660, top: 596, width: 535, height: 30 }, {
      fontSize: 15, color: C.faint, alignment: "right",
    });
    addFooter(slide, 5);
    setSources(slide, [
      "https://lipengyuan1994.github.io/alpaca-hackathon/assets/data/live-paper-snapshot.json",
      `${TMP}/live-paper-snapshot.json (generated_at 2026-09-02T23:13:19.732773Z)`,
      `${ROOT}/packages/paper_wheel/public_snapshot.py`,
    ]);
  }

  // 6. Hybrid authority boundary.
  {
    const slide = deck.slides.add();
    slide.background.fill = C.bg;
    addTitle(slide, "Determinism + intelligence", "AI can veto. Deterministic controls retain authority.", "The model contributes context-sensitive restraint without gaining executable control.");

    [238, 468, 698, 928].forEach((x) => addArrow(slide, x, 285, 38));
    addNode(slide, 52, 235, 185, "Market context", "ECONOMIC DATA", C.mint2);
    addNode(slide, 282, 235, 185, "V13.5 proposal", "SEMANTIC ONLY", C.mint);
    addNode(slide, 512, 235, 185, "Gemini 3.6 Flash", "ALLOW / VETO", C.amber);
    addNode(slide, 742, 235, 185, "Risk + preflight", "REVALIDATE", C.mint);
    addNode(slide, 972, 235, 185, "Alpaca paper", "EXECUTION", C.mint2);

    addText(slide, "No model-generated contract, size, price, or execution command.", { left: 170, top: 420, width: 940, height: 52 }, {
      fontSize: 29, color: C.ink, bold: true, alignment: "center", fontFamily: FONT_DISPLAY,
    });
    addBox(slide, { left: 155, top: 510, width: 970, height: 78 }, {
      fill: C.bg2, line: { style: "solid", fill: C.amber, width: 1 }, borderRadius: "rounded-lg",
    });
    addText(slide, "CURRENT LIVE BOUNDARY", { left: 180, top: 525, width: 240, height: 24 }, {
      fontSize: 13, color: C.amber, bold: true,
    });
    addText(slide, "The V13.5 paper canary is deterministic-only. The hybrid gate is implemented and replay-tested in the broader platform.", { left: 400, top: 522, width: 700, height: 48 }, {
      fontSize: 17, color: C.muted,
    });
    addFooter(slide, 6);
    setSources(slide, [
      `${ROOT}/docs/index.html#intelligence`,
      `${ROOT}/docs/architecture/STRATEGY_API.md`,
      `${ROOT}/docs/deployment/ECONOMIC_CONTEXT.md`,
    ]);
  }

  // 7. Container and data architecture.
  {
    const slide = deck.slides.add();
    slide.background.fill = C.bg;
    addTitle(slide, "Portable by construction", "Containers move the system without sharing authority", "Config selects behavior; role boundaries constrain credentials, network access, and broker reach.");

    addBox(slide, { left: 45, top: 195, width: 1190, height: 330 }, {
      fill: C.bg2, line: { style: "solid", fill: C.line, width: 2 }, borderRadius: "rounded-2xl",
    });
    addText(slide, "LOCAL ARM64  /  CLOUD VM  /  CONTAINER RUNNER", { left: 75, top: 215, width: 590, height: 24 }, {
      fontSize: 13, color: C.faint, bold: true,
    });

    [238, 448, 658, 868].forEach((x) => addArrow(slide, x, 300, 29));
    [138, 348, 558, 768, 978].forEach((x) => addDownArrow(slide, x, 385));

    const roles = [
      [60, "Data", "market context"],
      [270, "Strategy", "pure signal"],
      [480, "Agent", "allow / veto"],
      [690, "Risk", "hash-bound"],
      [900, "Execution", "paper key only"],
    ];
    roles.forEach(([x, title, detail], i) => {
      addNode(slide, x, 260, 175, title, detail.toUpperCase(), i === 2 ? C.amber : C.mint);
    });

    addBox(slide, { left: 270, top: 420, width: 740, height: 76 }, {
      fill: C.bg3, line: { style: "solid", fill: C.mint2, width: 2 }, borderRadius: "rounded-full",
    });
    addText(slide, "PostgreSQL 18", { left: 300, top: 438, width: 220, height: 32 }, {
      fontSize: 23, color: C.ink, bold: true, alignment: "center",
    });
    addText(slide, "EVENT LEDGER  |  AUDIT  |  OUTBOX / INBOX  |  RECONCILIATION", { left: 520, top: 443, width: 460, height: 28 }, {
      fontSize: 14, color: C.mint2, bold: true, alignment: "center",
    });

    addText(slide, "Same contracts. Same configuration. New host.", { left: 250, top: 570, width: 780, height: 48 }, {
      fontSize: 30, color: C.ink, fontFamily: FONT_DISPLAY, alignment: "center",
    });
    addFooter(slide, 7);
    setSources(slide, [
      `${ROOT}/docs/architecture/ARCHITECTURE_DESIGN.html`,
      `${ROOT}/docs/architecture/SYSTEM_ARCHITECTURE.md`,
      `${ROOT}/infra/compose.yaml`,
    ]);
  }

  // 8. Evidence-led close.
  {
    const slide = deck.slides.add();
    slide.background.fill = C.bg;
    addTitle(slide, "The submission", "A working system, not a concept.", "Inspect the evidence, replay the controls, and watch the paper account.");
    const proof = [
      ["01", "BACKTESTED", "644 sessions with SPY / QQQ comparison"],
      ["02", "OPERATING", "V13.5 on Alpaca paper with public telemetry"],
      ["03", "PORTABLE", "Config-driven containers and PostgreSQL"],
    ];
    proof.forEach(([index, heading, detail], i) => {
      const top = 220 + i * 105;
      addText(slide, index, { left: 72, top, width: 55, height: 32 }, { fontSize: 16, color: C.amber, bold: true });
      addText(slide, heading, { left: 145, top: top - 5, width: 250, height: 38 }, { fontSize: 28, color: C.mint, bold: true });
      addText(slide, detail, { left: 400, top, width: 475, height: 34 }, { fontSize: 19, color: C.ink });
      if (i < proof.length - 1) addRule(slide, 145, top + 58, 730, C.line, 1);
    });

    addBox(slide, { left: 905, top: 208, width: 300, height: 300 }, {
      fill: C.bg3, line: { style: "solid", fill: C.mint, width: 2 }, borderRadius: "rounded-2xl",
    });
    addText(slide, "INSPECT THE PROOF", { left: 935, top: 238, width: 240, height: 28 }, {
      fontSize: 14, color: C.amber, bold: true, alignment: "center",
    });
    addText(slide, "Stable Income\nGenerator", { left: 930, top: 286, width: 250, height: 86 }, {
      fontSize: 31, color: C.ink, fontFamily: FONT_DISPLAY, alignment: "center",
    });
    addText(slide, "lipengyuan1994.github.io/\nalpaca-hackathon/", { left: 925, top: 397, width: 260, height: 58 }, {
      fontSize: 17, color: C.mint2, bold: true, alignment: "center", fontFamily: FONT_MONO,
    });
    addText(slide, "Credential-free replay", { left: 72, top: 565, width: 250, height: 24 }, {
      fontSize: 14, color: C.faint, bold: true,
    });
    addText(slide, "uv sync --frozen   |   uv run paper-decision-worker   |   uv run pytest -q", { left: 72, top: 598, width: 1000, height: 32 }, {
      fontSize: 17, color: C.mint2, fontFamily: FONT_MONO,
    });
    addFooter(slide, 8);
    setSources(slide, [
      "https://lipengyuan1994.github.io/alpaca-hackathon/",
      "https://github.com/lipengyuan1994/alpaca-hackathon",
      `${ROOT}/docs/deployment/judge-reproduce.md`,
    ]);
  }

  for (const [index, slide] of deck.slides.items.entries()) {
    const stem = `slide-${String(index + 1).padStart(2, "0")}`;
    await writeBlob(`${TMP}/renders/${stem}.png`, await deck.export({ slide, format: "png", scale: 1 }));
    const layout = await slide.export({ format: "layout" });
    await fs.writeFile(`${TMP}/renders/${stem}.layout.json`, await layout.text());
  }
  await writeBlob(`${TMP}/deck-montage.webp`, await deck.export({ format: "webp", montage: true, scale: 1 }));

  const pptx = await PresentationFile.exportPptx(deck);
  await pptx.save(OUTPUT);

  const inspection = await deck.inspect({
    kind: "slide,textbox,shape,image,notes",
    maxChars: 20000,
  });
  await fs.writeFile(`${TMP}/inspection.ndjson`, inspection.ndjson);
  console.log(`WROTE ${OUTPUT}`);
}

build().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
