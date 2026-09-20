from pathlib import Path
import sys
import sqlite3
import argparse
import hashlib
from datetime import datetime

import pandas as pd

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths
from utils import norm_text, norm_apartment


SOURCE_NAME = "tbot_parking"
CREATED_BY = "import_tbot_quarantine"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_column_if_missing(cur, table_name, column_name, definition):
    columns = {row[1] for row in cur.execute(f"PRAGMA table_info({table_name})")}
    if column_name not in columns:
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def ensure_batch_columns(cur):
    """Keep snapshots of every source file; never erase the previous import."""
    add_column_if_missing(cur, "source_files", "file_sha256", "TEXT")
    add_column_if_missing(cur, "source_files", "original_file_name", "TEXT")
    add_column_if_missing(cur, "tbot_parking_import", "source_file_id", "INTEGER")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_tbot_parking_source_file "
        "ON tbot_parking_import(source_file_id)"
    )


def normalize_ownership(value):
    text = norm_text(value)

    if text == "Власник":
        return "OWNER"
    if text == "Орендар":
        return "TENANT"
    if text == "Комерція":
        return "COMMERCIAL"

    return text


def import_tbot_quarantine(excel_file=None):
    excel_file = Path(excel_file) if excel_file else paths.OSBB_TBOT_PARKING_FILE
    db_file = paths.OSBB_QUARANTINE_DB_FILE

    print("=" * 70)
    print("IMPORT TBOT PARKING TO QUARANTINE DB")
    print("=" * 70)
    print(f"Excel : {excel_file}")
    print(f"DB    : {db_file}")
    print()

    if not excel_file.exists():
        raise FileNotFoundError(f"Файл не найден:\n{excel_file}")

    df = pd.read_excel(excel_file)

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    ensure_batch_columns(cur)

    source_hash = file_sha256(excel_file)
    existing = cur.execute(
        """
        SELECT id, records_count, imported_at
        FROM source_files
        WHERE source_name=?
          AND (
                file_sha256=?
                OR (
                    file_sha256 IS NULL
                    AND original_file_name IS NULL
                    AND file_path LIKE ?
                    AND records_count=?
                )
          )
        ORDER BY id DESC LIMIT 1
        """,
        (SOURCE_NAME, source_hash, f"%{excel_file.name}", len(df)),
    ).fetchone()
    if existing:
        conn.close()
        print("Этот файл уже сохранён в карантине — повторный импорт не выполнен.")
        print(f"Source file id: {existing[0]}, rows: {existing[1]}, imported: {existing[2]}")
        return

    cur.execute(
        """
        INSERT INTO source_files(
            source_name, file_path, file_sha256, original_file_name,
            records_count, imported_at, imported_by, notes
        ) VALUES (?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (
            SOURCE_NAME, str(excel_file), source_hash, excel_file.name,
            now(), CREATED_BY, "Import snapshot; skipped rows will be recorded after parsing.",
        ),
    )
    source_file_id = cur.lastrowid

    inserted = 0
    skipped = 0

    for _, row in df.iterrows():
        ownership_raw = norm_text(row.get("Власність"))
        ownership_type = normalize_ownership(ownership_raw)

        full_name = norm_text(row.get("ПІБ"))
        phone_raw = norm_text(row.get("Телефон"))
        apartment_number = norm_apartment(row.get("Номер квартири"))

        car_model = norm_text(row.get("Марка авто"))
        car_color = norm_text(row.get("Колір авто"))
        license_plate = norm_text(row.get("Номер Авто"))
        status_raw = norm_text(row.get("Статус"))

        if not apartment_number:
            skipped += 1
            continue

        cur.execute("""
            INSERT INTO tbot_parking_import (
                ownership_type,
                ownership_type_raw,
                full_name,
                phone_raw,
                apartment_number,
                car_model,
                car_color,
                license_plate,
                status_raw,
                source,
                imported_at,
                imported_by,
                source_file_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ownership_type,
            ownership_raw,
            full_name,
            phone_raw,
            apartment_number,
            car_model,
            car_color,
            license_plate,
            status_raw,
            SOURCE_NAME,
            now(),
            CREATED_BY,
            source_file_id,
        ))

        inserted += 1

    cur.execute(
        """
        UPDATE source_files
        SET records_count=?, notes=?
        WHERE id=?
        """,
        (inserted, f"Skipped rows: {skipped}", source_file_id),
    )

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM tbot_parking_import")
    total = cur.fetchone()[0]

    conn.close()

    print("=" * 70)
    print("IMPORT COMPLETED")
    print("=" * 70)
    print(f"Строк в Excel               : {len(df)}")
    print(f"Импортировано в карантин    : {inserted}")
    print(f"Пропущено                  : {skipped}")
    print(f"Строк в quarantine всего    : {total}")
    print(f"ID снимка источника          : {source_file_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import one TBot parking spreadsheet as a preserved quarantine snapshot.")
    parser.add_argument("--file", type=Path, help="Путь к parking_tbot3.xlsx или другой версии файла.")
    args = parser.parse_args()
    import_tbot_quarantine(args.file)
