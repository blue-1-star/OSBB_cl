"""Read-only reconciliation preview for ``parking_tbot2.xlsx`` quarantine data.

The script never writes to either SQLite database.  It classifies each source
row and produces a text summary plus a CSV suitable for inspection in Excel.

Run:
    .venv/bin/python dry_run_tbot_vehicle_reconciliation.py
"""

from __future__ import annotations

import csv
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from config import paths
from utils import normalize_plate, norm_apartment, norm_text


REPORT_PREFIX = "tbot_vehicle_reconciliation_dry_run"


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def load_apartments(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT id, apartment_number FROM apartments").fetchall()
    return {
        apartment: apartment_id
        for apartment_id, value in rows
        if (apartment := norm_apartment(value))
    }


def load_vehicles(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = defaultdict(list)
    rows = conn.execute(
        """
        SELECT id, apartment_id, license_plate, license_plate_normalized,
               car_model, car_color, status, lifecycle_status, review_status
        FROM vehicles
        """
    ).fetchall()
    for row in rows:
        (
            vehicle_id,
            apartment_id,
            plate_raw,
            plate_normalized,
            model,
            color,
            status,
            lifecycle_status,
            review_status,
        ) = row
        normalized, _ = normalize_plate(plate_normalized or plate_raw)
        if normalized:
            result[normalized].append({
                "id": vehicle_id,
                "apartment_id": apartment_id,
                "plate_raw": plate_raw,
                "model": model,
                "color": color,
                "status": status,
                "lifecycle_status": lifecycle_status,
                "review_status": review_status,
            })
    return result


def vehicle_ids_by_apartment(conn: sqlite3.Connection) -> dict[int, list[int]]:
    """All current vehicle IDs, including rows without a usable plate."""
    result: dict[int, list[int]] = defaultdict(list)
    for vehicle_id, apartment_id in conn.execute(
        "SELECT id, apartment_id FROM vehicles WHERE apartment_id IS NOT NULL"
    ):
        result[apartment_id].append(vehicle_id)
    return result


def classify_rows(main: sqlite3.Connection, quarantine: sqlite3.Connection) -> list[dict]:
    apartments = load_apartments(main)
    vehicles_by_plate = load_vehicles(main)
    vehicles_by_apartment = vehicle_ids_by_apartment(main)

    source_rows = quarantine.execute(
        """
        SELECT id, apartment_number, full_name, phone_raw, car_model, car_color,
               license_plate, status_raw, ownership_type, source, imported_at
        FROM tbot_parking_import
        ORDER BY id
        """
    ).fetchall()

    prepared = []
    duplicate_count: Counter[tuple[str | None, str | None]] = Counter()
    for row in source_rows:
        (
            source_id,
            apartment_raw,
            full_name,
            phone_raw,
            model,
            color,
            plate_raw,
            status_raw,
            ownership_type,
            source,
            imported_at,
        ) = row
        apartment = norm_apartment(apartment_raw)
        plate, plate_format = normalize_plate(plate_raw)
        duplicate_count[(apartment, plate)] += 1
        prepared.append({
            "source_id": source_id,
            "apartment_number": apartment,
            "apartment_raw": apartment_raw,
            "full_name": norm_text(full_name),
            "phone_raw": norm_text(phone_raw),
            "car_model": norm_text(model),
            "car_color": norm_text(color),
            "license_plate": norm_text(plate_raw),
            "license_plate_normalized": plate,
            "plate_format": plate_format,
            "status_raw": norm_text(status_raw),
            "ownership_type": norm_text(ownership_type),
            "source": norm_text(source),
            "imported_at": imported_at,
        })

    results = []
    for item in prepared:
        apartment = item["apartment_number"]
        plate = item["license_plate_normalized"]
        existing = vehicles_by_plate.get(plate, []) if plate else []
        reason = ""

        if apartment not in apartments:
            category = "REVIEW_APARTMENT_NOT_FOUND"
            reason = "Номер квартиры не найден среди физических квартир основной БД."
        elif item["plate_format"] != "STANDARD":
            category = "REVIEW_PLATE_FORMAT"
            reason = "Госномер отсутствует либо не соответствует стандартному формату AA1234BB."
        elif duplicate_count[(apartment, plate)] > 1:
            category = "REVIEW_DUPLICATE_IN_SOURCE"
            reason = "В quarantine есть более одной строки с той же квартирой и нормализованным номером."
        elif existing:
            own = [v for v in existing if v["apartment_id"] == apartments[apartment]]
            other = [v for v in existing if v["apartment_id"] != apartments[apartment]]
            if own and not other:
                category = "ALREADY_IN_REGISTRY"
                reason = "Автомобиль с этим номером уже зарегистрирован за той же квартирой."
            else:
                category = "REVIEW_PLATE_CONFLICT"
                reason = "Такой нормализованный госномер уже есть в другой квартире или в нескольких записях."
        else:
            target_vehicle_ids = vehicles_by_apartment.get(apartments[apartment], [])
            if target_vehicle_ids:
                category = "REVIEW_NEW_VEHICLE_FOR_POPULATED_APARTMENT"
                reason = (
                    "Номер отсутствует в реестре, но за квартирой уже есть автомобили. "
                    "Старый источник нельзя считать достаточным подтверждением новой записи."
                )
            else:
                category = "CANDIDATE_FOR_FIRST_IMPORT"
                reason = (
                    "Квартира и госномер корректны, а автомобилей в реестре пока нет. "
                    "Это кандидат для первичного импорта, но до подтверждения не переносится автоматически."
                )

        item.update({
            "category": category,
            "reason": reason,
            "target_apartment_id": apartments.get(apartment),
            "existing_vehicle_ids": ", ".join(str(v["id"]) for v in existing) or None,
            "existing_apartment_ids": ", ".join(str(v["apartment_id"]) for v in existing) or None,
            "target_apartment_vehicle_ids": ", ".join(
                str(vehicle_id) for vehicle_id in vehicles_by_apartment.get(apartments.get(apartment), [])
            ) or None,
        })
        results.append(item)
    return results


def write_reports(rows: list[dict]) -> tuple[Path, Path]:
    report_dir = paths.OSBB_EXPORTS_DIR / "audits"
    report_dir.mkdir(parents=True, exist_ok=True)
    suffix = timestamp()
    text_file = report_dir / f"{REPORT_PREFIX}_{suffix}.txt"
    csv_file = report_dir / f"{REPORT_PREFIX}_{suffix}.csv"
    categories = Counter(row["category"] for row in rows)

    lines = [
        "СУХОЙ ПРОГОН: сверка parking_tbot2.xlsx → реестр автомобилей",
        "",
        f"Основная БД: {paths.OSBB_DB_FILE}",
        f"Карантинная БД: {paths.OSBB_QUARANTINE_DB_FILE}",
        "Режим: ТОЛЬКО ЧТЕНИЕ. Ни одна запись БД не была изменена.",
        "",
        f"Всего строк из parking_tbot2.xlsx: {len(rows)}",
        "",
        "ОТБОР:",
    ]
    labels = {
        "CANDIDATE_FOR_FIRST_IMPORT": "кандидат для первичного импорта — требуется подтверждение",
        "REVIEW_NEW_VEHICLE_FOR_POPULATED_APARTMENT": "проверить: новая машина у квартиры с уже зарегистрированными авто",
        "ALREADY_IN_REGISTRY": "уже есть в реестре — не переносить",
        "REVIEW_APARTMENT_NOT_FOUND": "проверить: квартира не найдена",
        "REVIEW_PLATE_FORMAT": "проверить: формат госномера",
        "REVIEW_DUPLICATE_IN_SOURCE": "проверить: дубль в исходнике",
        "REVIEW_PLATE_CONFLICT": "проверить: конфликт госномера",
    }
    for category in labels:
        lines.append(f"- {labels[category]}: {categories[category]}")

    for category in labels:
        selected = [row for row in rows if row["category"] == category]
        if not selected:
            continue
        lines.extend(["", "=" * 80, f"{category} — {labels[category]}", "=" * 80])
        for row in selected:
            lines.append(
                f"#{row['source_id']} | кв.{row['apartment_number'] or '-'} | "
                f"{row['license_plate'] or '-'} → {row['license_plate_normalized'] or '-'} | "
                f"{row['car_model'] or '-'} | {row['full_name'] or '-'} | {row['reason']}"
            )

    text_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    fields = [
        "category", "reason", "source_id", "apartment_number", "target_apartment_id",
        "license_plate", "license_plate_normalized", "plate_format", "car_model", "car_color",
        "full_name", "phone_raw", "status_raw", "ownership_type", "existing_vehicle_ids",
        "existing_apartment_ids", "target_apartment_vehicle_ids", "source", "imported_at",
    ]
    with csv_file.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in fields} for row in rows)
    return text_file, csv_file


def main() -> None:
    with sqlite3.connect(paths.OSBB_DB_FILE) as main_db, sqlite3.connect(paths.OSBB_QUARANTINE_DB_FILE) as quarantine_db:
        rows = classify_rows(main_db, quarantine_db)
    text_file, csv_file = write_reports(rows)
    categories = Counter(row["category"] for row in rows)
    print("Dry run completed. No database records were changed.")
    print(f"Total rows: {len(rows)}")
    for category, count in sorted(categories.items()):
        print(f"{category}: {count}")
    print(f"Text report: {text_file}")
    print(f"CSV report : {csv_file}")


if __name__ == "__main__":
    main()
