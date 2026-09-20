import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const inputPath = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath || !outputPath) {
  throw new Error("Usage: node build_video_recognition_workbook.mjs INPUT.json OUTPUT.xlsx");
}

const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
const observations = data.observations;
const sources = data.sources;

const asDate = (value) => (value ? new Date(`${value}T00:00:00`) : null);
const asCell = (value) => (value === undefined || value === null ? "" : value);
const columnLetter = (column) => {
  let result = "";
  let index = column;
  while (index > 0) {
    const remainder = (index - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    index = Math.floor((index - 1) / 26);
  }
  return result;
};

const titleFormat = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF", size: 14 },
  verticalAlignment: "center",
};
const headerFormat = {
  fill: "#5B9BD5",
  font: { bold: true, color: "#FFFFFF" },
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "all", style: "thin", color: "#D9E2F3" },
};
const bodyBorders = { preset: "all", style: "thin", color: "#E7E6E6" };

const workbook = Workbook.create();
const review = workbook.worksheets.add("Review");
const records = workbook.worksheets.add("Observations");
const sourceSheet = workbook.worksheets.add("Sources");

for (const sheet of [review, records, sourceSheet]) {
  sheet.showGridLines = false;
  sheet.tabColor = "#1F4E78";
}

// Review sheet: a compact handover and quality check before analysis.
const periodCounts = Object.fromEntries(["DAY", "NIGHT"].map((period) => [
  period,
  observations.filter((row) => row.recording_period === period).length,
]));
const standardPlates = observations.filter((row) => row.plate_status === "STANDARD").length;
const checkPlates = observations.length - standardPlates;
const missingTimes = observations.filter((row) => !row.video_time).length;

review.mergeCells("A1:D1");
review.getRange("A1").values = [["Video recognition — standardized data"]];
review.getRange("A1:D1").format = titleFormat;
review.getRange("A1:D1").format.rowHeight = 26;
review.getRange("A3:B3").values = [["Indicator", "Value"]];
review.getRange("A3:B3").format = headerFormat;
review.getRange("A4:B10").values = [
  ["Source files processed", sources.length],
  ["Observations", observations.length],
  ["Day observations", periodCounts.DAY],
  ["Night observations", periodCounts.NIGHT],
  ["Standardized plates", standardPlates],
  ["Plates needing review", checkPlates],
  ["Rows without recording start time", missingTimes],
];
review.getRange("A4:B10").format.borders = bodyBorders;
review.getRange("A4:A10").format.font = { bold: true };

review.mergeCells("A12:D12");
review.getRange("A12").values = [["How to use this workbook"]];
review.getRange("A12:D12").format = headerFormat;
review.getRange("A13:D16").values = [
  ["1", "Observations", "One canonical row per recognized vehicle. Filter by date, period, plate status, or source.", ""],
  ["2", "Sources", "Provenance for every input file: detected headers, rows accepted, and rows skipped.", ""],
  ["3", "Plate Status = CHECK", "The plate did not match the standard Ukrainian pattern after Cyrillic-to-Latin normalization.", ""],
  ["4", "Blank Video Time", "The source file did not provide a recording start time; Timestamp is retained unchanged.", ""],
];
review.getRange("A13:D16").format.wrapText = true;
review.getRange("A13:D16").format.verticalAlignment = "center";
review.getRange("A13:D16").format.borders = bodyBorders;
review.getRange("A13:A16").format.font = { bold: true };
review.getRange("A13:A16").format.horizontalAlignment = "center";
review.getRange("A1:D16").format.autofitColumns();
review.getRange("B13:B16").format.columnWidth = 18;
review.getRange("C13:C16").format.columnWidth = 58;
review.getRange("A13:D16").format.rowHeight = 34;
review.freezePanes.freezeRows(3);

// Main standardized observations sheet.
const observationHeaders = [
  "Row", "Car Make/Model", "Make/Model Normalized", "Plate", "Plate Normalized",
  "Plate Status", "Video Date", "Video Time", "Timestamp", "Video Part Number",
  "Period", "Source File", "Source Sheet", "Source Row", "Source Video Date", "Source Video Time",
];
records.mergeCells("A1:P1");
records.getRange("A1").values = [["Standardized vehicle recognition observations"]];
records.getRange("A1:P1").format = titleFormat;
records.getRange("A1:P1").format.rowHeight = 26;
records.mergeCells("A2:P2");
records.getRange("A2").values = [["Raw source files are preserved. Every row below retains its exact source file, sheet, and row number."]];
records.getRange("A2:P2").format = { font: { italic: true, color: "#44546A" } };
records.getRange("A4:P4").values = [observationHeaders];
records.getRange("A4:P4").format = headerFormat;
records.getRange("A4:P4").format.rowHeight = 34;

const observationRows = observations.map((row) => [
  asCell(row.row), asCell(row.make_model), asCell(row.make_model_normalized),
  asCell(row.plate), asCell(row.plate_normalized), asCell(row.plate_status),
  asDate(row.video_date), asCell(row.video_time), asCell(row.timestamp), asCell(row.video_part),
  asCell(row.recording_period), asCell(row.source_file), asCell(row.source_sheet),
  asCell(row.source_row), asDate(row.source_video_date), asCell(row.source_video_time),
]);
const observationEndRow = observationRows.length + 4;
records.getRange(`A5:P${observationEndRow}`).values = observationRows;
records.getRange(`A5:P${observationEndRow}`).format.borders = bodyBorders;
records.getRange(`A5:P${observationEndRow}`).format.verticalAlignment = "center";
records.getRange(`G5:G${observationEndRow}`).setNumberFormat("yyyy-mm-dd");
records.getRange(`O5:O${observationEndRow}`).setNumberFormat("yyyy-mm-dd");
records.getRange(`F5:F${observationEndRow}`).conditionalFormats.add("containsText", {
  text: "CHECK", format: { fill: "#FCE4D6", font: { bold: true, color: "#C00000" } },
});
records.getRange(`A4:P${observationEndRow}`).format.autofitRows();

const observationWidths = [8, 24, 28, 16, 18, 15, 14, 14, 14, 17, 10, 30, 26, 12, 18, 18];
observationWidths.forEach((width, index) => {
  records.getRange(`${columnLetter(index + 1)}:${columnLetter(index + 1)}`).format.columnWidth = width;
});
records.freezePanes.freezeRows(4);
records.freezePanes.freezeColumns(1);

// Source-level provenance sheet.
const sourceHeaders = [
  "Source File", "Period", "Derived Video Date", "Source Sheet", "Header Row",
  "Recognized Columns", "Observations", "Skipped Rows", "Detected Start Time",
];
sourceSheet.mergeCells("A1:I1");
sourceSheet.getRange("A1").values = [["Source file provenance"]];
sourceSheet.getRange("A1:I1").format = titleFormat;
sourceSheet.getRange("A1:I1").format.rowHeight = 26;
sourceSheet.getRange("A3:I3").values = [sourceHeaders];
sourceSheet.getRange("A3:I3").format = headerFormat;
sourceSheet.getRange("A3:I3").format.rowHeight = 32;
const sourceRows = sources.map((source) => [
  asCell(source.source_file), asCell(source.recording_period), asDate(source.video_date),
  asCell(source.source_sheet), asCell(source.header_row), asCell(source.columns_found),
  asCell(source.records), asCell(source.skipped_rows), asCell(source.title_time),
]);
const sourceEndRow = sourceRows.length + 3;
sourceSheet.getRange(`A4:I${sourceEndRow}`).values = sourceRows;
sourceSheet.getRange(`A4:I${sourceEndRow}`).format.borders = bodyBorders;
sourceSheet.getRange(`C4:C${sourceEndRow}`).setNumberFormat("yyyy-mm-dd");
sourceSheet.getRange(`A4:I${sourceEndRow}`).format.autofitRows();
const sourceWidths = [32, 12, 18, 28, 12, 42, 15, 14, 20];
sourceWidths.forEach((width, index) => {
  sourceSheet.getRange(`${columnLetter(index + 1)}:${columnLetter(index + 1)}`).format.columnWidth = width;
});
sourceSheet.freezePanes.freezeRows(3);
sourceSheet.freezePanes.freezeColumns(1);

await workbook.recalculate();

console.log((await workbook.inspect({
  kind: "workbook,sheet,region",
  maxChars: 3000,
  tableMaxRows: 6,
  tableMaxCols: 16,
  sheetId: "Observations",
  range: "A1:P8",
})).ndjson);

const outputDir = path.dirname(outputPath);
await fs.mkdir(outputDir, { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

for (const sheetName of ["Review", "Observations", "Sources"]) {
  const rendered = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
  const bytes = new Uint8Array(await rendered.arrayBuffer());
  await fs.writeFile(path.join(outputDir, `${sheetName.toLowerCase()}_preview.png`), bytes);
}

console.log(`Created ${outputPath}`);
