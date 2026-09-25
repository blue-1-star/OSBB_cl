/**
 * Build operator reports from standardized video observations and a registry
 * snapshot. One row represents one normalized plate. The script never writes
 * to the OSBB database or changes the raw recognition workbooks.
 *
 * Usage:
 *   node build_video_observation_reports.mjs OBSERVATIONS.json REGISTRY.json OUTPUT.xlsx
 */

import fs from "node:fs/promises";
import path from "node:path";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const [observationsPath, registryPath, outputPath] = process.argv.slice(2);
if (!observationsPath || !registryPath || !outputPath) {
  throw new Error("Usage: node build_video_observation_reports.mjs OBSERVATIONS.json REGISTRY.json OUTPUT.xlsx");
}

const source = JSON.parse(await fs.readFile(observationsPath, "utf8"));
const registryRows = JSON.parse(await fs.readFile(registryPath, "utf8"));

const registry = new Map();
for (const row of registryRows) {
  const key = String(row.plate_normalized || "").trim().toUpperCase();
  if (key && !registry.has(key)) registry.set(key, row);
}

const groups = new Map();
for (const row of source.observations) {
  if (row.plate_status !== "STANDARD" || !row.plate_normalized) continue;
  const key = row.plate_normalized;
  if (!groups.has(key)) {
    groups.set(key, { plate: key, total: 0, day: 0, night: 0, models: new Map() });
  }
  const item = groups.get(key);
  item.total += 1;
  if (row.recording_period === "NIGHT") item.night += 1;
  else if (row.recording_period === "DAY") item.day += 1;
  if (row.make_model_normalized) {
    item.models.set(row.make_model_normalized, (item.models.get(row.make_model_normalized) ?? 0) + 1);
  }
}

const dominantModel = (models) => {
  const entries = [...models.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (!entries.length) return "";
  const [model, count] = entries[0];
  return entries.filter(([, value]) => value === count).length === 1 ? model : "";
};

const rows = [...groups.values()].map((item) => {
  const db = registry.get(item.plate);
  return {
    plate: item.plate,
    count: item.total,
    apartment: db?.apartment_number ?? "",
    fullName: db?.full_name ?? "",
    model: dominantModel(item.models) || db?.registry_model || "",
    night: item.night,
    day: item.day,
    found: Boolean(db),
  };
});

const byCount = (a, b) => b.count - a.count || a.plate.localeCompare(b.plate);
const allRows = rows.slice().sort(byCount);
const foundRows = allRows.filter((row) => row.found);
const missingRows = allRows.filter((row) => !row.found);
const nightRows = allRows.filter((row) => row.night > 0).sort((a, b) => b.night - a.night || b.count - a.count || a.plate.localeCompare(b.plate));

const wb = Workbook.create();
const overview = wb.worksheets.add("Сводка");
const all = wb.worksheets.add("Все номера");
const night = wb.worksheets.add("Ночные наблюдения");
const found = wb.worksheets.add("Найдено в БД");
const missing = wb.worksheets.add("Не найдено в БД");

const headerFormat = {
  fill: "#1F4E78",
  font: { bold: true, color: "#FFFFFF", size: 10 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "all", style: "thin", color: "#FFFFFF" },
};
const titleFormat = { font: { bold: true, color: "#1F1F1F", size: 14 } };
const thinBorder = { preset: "insideHorizontal", style: "thin", color: "#D9E2F3" };

for (const sheet of [overview, all, night, found, missing]) {
  sheet.showGridLines = false;
}

overview.getRange("A2").values = [["Отчёты по видео-наблюдениям парковки"]];
overview.getRange("A2").format = titleFormat;
overview.getRange("A4:B4").values = [["Показатель", "Значение"]];
overview.getRange("A4:B4").format = headerFormat;
overview.getRange("A5:B11").values = [
  ["Файлов видео-распознавания", source.sources.length],
  ["Всего наблюдений", source.observations.length],
  ["Наблюдений со стандартным номером", source.observations.filter((row) => row.plate_status === "STANDARD").length],
  ["Уникальных номеров", allRows.length],
  ["Номеров найдено в реестре", foundRows.length],
  ["Номеров не найдено в реестре", missingRows.length],
  ["Номеров в ночных наблюдениях", nightRows.length],
];
overview.getRange("A5:B11").format.borders = thinBorder;
overview.getRange("A13").values = [[`Источник: результаты распознавания видео. Число файлов: ${source.sources.length}. Снимок реестра автомобилей ОСББ.`]];
overview.getRange("A13").format = { font: { italic: true, color: "#595959" } };
overview.mergeCells("A14:F15");
overview.getRange("A14").values = [["Квартира и ФИО заполняются только при точном совпадении нормализованного номера с реестром. Марка — наиболее частая распознанная модель; при отсутствии или конфликте берётся марка из реестра, если она есть."]];
overview.getRange("A14").format = { font: { italic: true, color: "#595959" }, wrapText: true, verticalAlignment: "top" };
overview.getRange("A:A").format.columnWidth = 38;
overview.getRange("B:B").format.columnWidth = 16;
overview.getRange("A14:F15").format.rowHeight = 26;

const renderTable = (sheet, title, inputRows, countKind = "total") => {
  sheet.getRange("A2").values = [[title]];
  sheet.getRange("A2").format = titleFormat;
  sheet.getRange("A4:F4").values = [["Номер", "Количество", "Квартира", "ФИО", "Марка", "Ночь / День"]];
  sheet.getRange("A4:F4").format = headerFormat;
  const values = inputRows.map((row) => [
    row.plate,
    countKind === "night" ? row.night : row.count,
    row.apartment,
    row.fullName,
    row.model,
    `${row.night} / ${row.day}`,
  ]);
  if (values.length) {
    const end = values.length + 4;
    sheet.getRange(`A5:F${end}`).values = values;
    sheet.getRange(`A5:F${end}`).format = { verticalAlignment: "center", borders: thinBorder };
    sheet.getRange(`D5:D${end}`).format.wrapText = true;
    sheet.getRange(`A5:F${end}`).format.autofitRows();
    sheet.getRange(`B5:B${end}`).setNumberFormat("#,##0");
  }
  sheet.getRange("A:A").format.columnWidth = 18;
  sheet.getRange("B:B").format.columnWidth = 14;
  sheet.getRange("C:C").format.columnWidth = 13;
  sheet.getRange("D:D").format.columnWidth = 42;
  sheet.getRange("E:E").format.columnWidth = 26;
  sheet.getRange("F:F").format.columnWidth = 15;
  sheet.getRange("A4:F4").format.rowHeight = 28;
  sheet.freezePanes.freezeRows(4);
  sheet.freezePanes.freezeColumns(1);
};

renderTable(all, "Все распознанные номера", allRows);
renderTable(night, "Наблюдения ночью", nightRows, "night");
renderTable(found, "Номера, найденные в реестре автомобилей", foundRows);
renderTable(missing, "Номера, не найденные в реестре автомобилей", missingRows);

wb.recalculate();
console.log((await wb.inspect({ kind: "table", range: "Сводка!A2:B14", include: "values,formulas", tableMaxRows: 20, tableMaxCols: 6 })).ndjson);
console.log((await wb.inspect({ kind: "table", range: "Все номера!A2:F12", include: "values,formulas", tableMaxRows: 12, tableMaxCols: 6 })).ndjson);
console.log((await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 100 }, summary: "final formula error scan" })).ndjson);

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(wb);
await output.save(outputPath);
for (const sheetName of ["Сводка", "Все номера", "Ночные наблюдения", "Найдено в БД", "Не найдено в БД"]) {
  const image = await wb.render({ sheetName, range: sheetName === "Сводка" ? "A1:F16" : "A1:F18", scale: 1, format: "png" });
  await fs.writeFile(`${outputPath}.${sheetName}.png`, new Uint8Array(await image.arrayBuffer()));
}
