"use strict";

const fs = require("fs");
const path = require("path");
const PptxGenJS = require("pptxgenjs");
const {
  warnIfSlideHasOverlaps,
  warnIfSlideElementsOutOfBounds,
} = require("../pptxgenjs_helpers/layout");

const palette = {
  ink: "1F2937",
  muted: "6B7280",
  line: "D1D5DB",
  panel: "F8FAFC",
  accent: "0F766E",
  accentSoft: "CCFBF1",
};

function addTitleSlide(pptx, snapshot) {
  const slide = pptx.addSlide();
  slide.background = { color: "F4F7FB" };
  slide.addText(snapshot.name, {
    x: 0.6,
    y: 0.7,
    w: 10.8,
    h: 0.7,
    fontFace: "Aptos Display",
    fontSize: 24,
    bold: true,
    color: palette.ink,
    margin: 0,
  });
  slide.addText(`Generated ${snapshot.generated_at}`, {
    x: 0.6,
    y: 1.5,
    w: 4.8,
    h: 0.3,
    fontFace: "Aptos",
    fontSize: 10,
    color: palette.muted,
    margin: 0,
  });
  slide.addShape(pptx.ShapeType.roundRect, {
    x: 0.6,
    y: 2.0,
    w: 12.0,
    h: 1.2,
    rectRadius: 0.08,
    line: { color: palette.line, width: 1 },
    fill: { color: palette.accentSoft },
  });
  slide.addText(snapshot.summary || "No summary available.", {
    x: 0.85,
    y: 2.28,
    w: 11.5,
    h: 0.55,
    fontFace: "Aptos",
    fontSize: 15,
    color: palette.ink,
    bold: false,
    margin: 0,
    valign: "mid",
  });
  slide.addText(`${snapshot.sections.length} section(s) included`, {
    x: 0.6,
    y: 6.5,
    w: 3.5,
    h: 0.3,
    fontFace: "Aptos",
    fontSize: 10,
    color: palette.muted,
    margin: 0,
  });
  warnIfSlideHasOverlaps(slide, pptx);
  warnIfSlideElementsOutOfBounds(slide, pptx);
}

function normalizeCell(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return Number.isFinite(value) ? value.toFixed(2).replace(/\.00$/, "") : "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function addSectionSlide(pptx, section, index) {
  const slide = pptx.addSlide();
  slide.background = { color: "FFFFFF" };

  slide.addText(`${index + 1}. ${section.title}`, {
    x: 0.5,
    y: 0.45,
    w: 8.5,
    h: 0.45,
    fontFace: "Aptos Display",
    fontSize: 22,
    bold: true,
    color: palette.ink,
    margin: 0,
  });
  slide.addText(section.kind, {
    x: 11.0,
    y: 0.5,
    w: 1.3,
    h: 0.25,
    fontFace: "Aptos",
    fontSize: 9,
    bold: true,
    color: palette.accent,
    align: "right",
    margin: 0,
  });
  slide.addText(section.content || "", {
    x: 0.5,
    y: 1.0,
    w: 12.2,
    h: 0.55,
    fontFace: "Aptos",
    fontSize: 12,
    color: palette.muted,
    margin: 0,
  });

  const rows = Array.isArray(section.rows) ? section.rows : [];
  if (!rows.length) {
    slide.addShape(pptx.ShapeType.roundRect, {
      x: 0.7,
      y: 2.0,
      w: 11.6,
      h: 3.0,
      rectRadius: 0.06,
      line: { color: palette.line, width: 1 },
      fill: { color: palette.panel },
    });
    slide.addText("No rows available for this section.", {
      x: 1.0,
      y: 3.15,
      w: 11.0,
      h: 0.4,
      fontFace: "Aptos",
      fontSize: 16,
      color: palette.muted,
      align: "center",
      margin: 0,
    });
  } else {
    const allColumns = Object.keys(rows[0]);
    const columns = allColumns.slice(0, 6);
    const tableRows = [
      columns.map((column) => ({
        text: column,
        options: {
          bold: true,
          color: "FFFFFF",
          fill: palette.accent,
          align: "center",
        },
      })),
      ...rows.slice(0, 10).map((row, rowIndex) =>
        columns.map((column) => ({
          text: normalizeCell(row[column]),
          options: {
            fill: rowIndex % 2 === 0 ? "FFFFFF" : "F8FAFC",
            color: palette.ink,
            fontSize: 10,
          },
        }))
      ),
    ];
    const columnWidth = 11.8 / columns.length;
    slide.addTable(tableRows, {
      x: 0.5,
      y: 1.8,
      w: 11.8,
      h: 4.8,
      colW: columns.map(() => columnWidth),
      rowH: 0.38,
      border: { type: "solid", color: palette.line, pt: 1 },
      margin: 0.06,
      fontFace: "Aptos",
      fontSize: 10,
      color: palette.ink,
      valign: "mid",
      autoFit: false,
    });
    slide.addText(`Showing ${Math.min(rows.length, 10)} of ${rows.length} row(s) and ${columns.length} of ${allColumns.length} column(s).`, {
      x: 0.6,
      y: 6.75,
      w: 6.0,
      h: 0.2,
      fontFace: "Aptos",
      fontSize: 9,
      color: palette.muted,
      margin: 0,
    });
  }

  warnIfSlideHasOverlaps(slide, pptx);
  warnIfSlideElementsOutOfBounds(slide, pptx);
}

async function renderSnapshotReport(snapshotPath, outputPptxPath) {
  if (!snapshotPath || !outputPptxPath) {
    throw new Error("Usage: node render_snapshot_report.js <snapshot.json> <output.pptx>");
  }

  const snapshot = JSON.parse(fs.readFileSync(snapshotPath, "utf-8"));
  const pptx = new PptxGenJS();
  pptx.layout = "LAYOUT_WIDE";
  pptx.author = "OpenAI Codex";
  pptx.company = "Macro Platform";
  pptx.subject = "Macro research snapshot";
  pptx.title = snapshot.name;
  pptx.lang = "en-US";
  pptx.theme = {
    headFontFace: "Aptos Display",
    bodyFontFace: "Aptos",
    lang: "en-US",
  };

  addTitleSlide(pptx, snapshot);
  snapshot.sections.forEach((section, index) => addSectionSlide(pptx, section, index));
  await pptx.writeFile({ fileName: outputPptxPath });
}

module.exports = renderSnapshotReport;

if (require.main === module) {
  const [snapshotPath, outputPptxPath] = process.argv.slice(2);
  renderSnapshotReport(snapshotPath, outputPptxPath).catch((error) => {
    console.error(error);
    process.exit(1);
  });
}
