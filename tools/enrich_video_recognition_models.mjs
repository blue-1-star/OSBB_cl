import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = process.argv[2];
const jsonPath = process.argv[3];
const outputPath = process.argv[4];

if (!inputPath || !jsonPath || !outputPath) {
  throw new Error("Usage: node enrich_video_recognition_models.mjs INPUT.xlsx DATA.json OUTPUT.xlsx");
}

const source = JSON.parse(await fs.readFile(jsonPath, "utf8"));
const observations = source.observations;

const groups = new Map();
for (const row of observations) {
  if (row.plate_status !== "STANDARD" || !row.plate_normalized) continue;
  if (!groups.has(row.plate_normalized)) groups.set(row.plate_normalized, new Map());
  if (row.make_model_normalized) {
    const models = groups.get(row.plate_normalized);
    models.set(row.make_model_normalized, (models.get(row.make_model_normalized) ?? 0) + 1);
  }
}

const evidenceFor = (row) => {
  if (row.plate_status !== "STANDARD" || !row.plate_normalized) {
    return { model: "", matches: "", models: "", agreement: "", status: "CHECK PLATE" };
  }
  const models = groups.get(row.plate_normalized) ?? new Map();
  const entries = [...models.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  if (!total) return { model: "", matches: "", models: "", agreement: "", status: "NO MODEL" };
  const [model, winningCount] = entries[0];
  const tied = entries.filter(([, count]) => count === winningCount).length > 1;
  const agreement = winningCount / total;
  if (tied) return { model: "", matches: winningCount, models: total, agreement, status: "CONFLICT" };
  if (winningCount < 2) return { model: "", matches: winningCount, models: total, agreement, status: "ONE OBSERVATION" };
  if (agreement < 0.75) return { model, matches: winningCount, models: total, agreement, status: "DOMINANT — REVIEW" };
  return { model, matches: winningCount, models: total, agreement, status: "CONSENSUS" };
};

const evidence = observations.map(evidenceFor);
const usableConsensus = evidence.filter((item) => ["CONSENSUS", "DOMINANT — REVIEW"].includes(item.status));
const filledFromEvidence = observations.filter((row, index) => !row.make_model && ["CONSENSUS", "DOMINANT — REVIEW"].includes(evidence[index].status)).length;

const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const sheet = workbook.worksheets.getItem("Observations");

const headers = [
  "Make/Model from observations", "Matching observations", "All model observations",
  "Agreement", "Model evidence status",
];
sheet.getRange("Q4:U4").values = [headers];
sheet.getRange("Q4:U4").format = {
  fill: "#5B9BD5",
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "all", style: "thin", color: "#D9E2F3" },
};
sheet.getRange("Q4:U4").format.rowHeight = 34;

const values = evidence.map((item) => [
  item.model,
  item.matches,
  item.models,
  item.agreement === "" ? "" : item.agreement,
  item.status,
]);
const endRow = values.length + 4;
sheet.getRange(`Q5:U${endRow}`).values = values;
sheet.getRange(`Q5:U${endRow}`).format = {
  verticalAlignment: "center",
  borders: { preset: "all", style: "thin", color: "#E7E6E6" },
};
sheet.getRange(`T5:T${endRow}`).setNumberFormat("0%");
sheet.getRange(`U5:U${endRow}`).conditionalFormats.add("containsText", {
  text: "CONFLICT", format: { fill: "#FCE4D6", font: { bold: true, color: "#C00000" } },
});
sheet.getRange(`U5:U${endRow}`).conditionalFormats.add("containsText", {
  text: "CONSENSUS", format: { fill: "#E2F0D9", font: { bold: true, color: "#375623" } },
});
sheet.getRange(`U5:U${endRow}`).conditionalFormats.add("containsText", {
  text: "DOMINANT", format: { fill: "#FFF2CC", font: { bold: true, color: "#7F6000" } },
});
for (const [column, width] of [["Q", 32], ["R", 16], ["S", 20], ["T", 12], ["U", 22]]) {
  sheet.getRange(`${column}:${column}`).format.columnWidth = width;
}

const review = workbook.worksheets.getItem("Review");
review.getRange("D3:D10").clear({ applyTo: "all" });
review.getRange("D3:D3").values = [["Model enrichment"]];
review.getRange("D3:D3").format = {
  fill: "#5B9BD5", font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center",
  borders: { preset: "all", style: "thin", color: "#D9E2F3" },
};
review.getRange("D4:D7").values = [
  [`Rows with observed model: ${usableConsensus.length}`],
  [`Blank raw rows enriched: ${filledFromEvidence}`],
  [`No model evidence: ${evidence.filter((item) => item.status === "NO MODEL").length}`],
  [`Ties / one observation: ${evidence.filter((item) => ["CONFLICT", "ONE OBSERVATION"].includes(item.status)).length}`],
];
review.getRange("D4:D7").format = { borders: { preset: "all", style: "thin", color: "#E7E6E6" } };
review.getRange("D3:D7").format.columnWidth = 30;

await workbook.recalculate();
console.log((await workbook.inspect({
  kind: "region", sheetId: "Observations", range: "P4:U10", maxChars: 2500,
})).ndjson);
console.log((await workbook.inspect({
  kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 }, maxChars: 1000,
})).ndjson);

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
const preview = await workbook.render({ sheetName: "Observations", range: "P1:U12", scale: 2, format: "png" });
await fs.writeFile(`${outputPath}.model-evidence-preview.png`, new Uint8Array(await preview.arrayBuffer()));
console.log(JSON.stringify({
  consensusRows: usableConsensus.length,
  filledFromEvidence,
  noModel: evidence.filter((item) => item.status === "NO MODEL").length,
  unresolved: evidence.filter((item) => ["CONFLICT", "ONE OBSERVATION"].includes(item.status)).length,
}));
