from pathlib import Path
import sys
import sqlite3
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


def normalize_ownership(value):
    text = norm_text(value)

    if text == "Власник":
        return "OWNER"
    if text == "Орендар":
        return "TENANT"
    if text == "Комерція":
        return "COMMERCIAL"

    return text


def import_tbot_quarantine():
    excel_file = paths.OSBB_TBOT_PARKING_FILE
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

    cur.execute("DELETE FROM tbot_parking_import")
    cur.execute("DELETE FROM source_files WHERE source_name = ?", (SOURCE_NAME,))

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
                imported_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        ))

        inserted += 1

    cur.execute("""
        INSERT INTO source_files (
            source_name,
            file_path,
            records_count,
            imported_at,
            imported_by,
            notes
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        SOURCE_NAME,
        str(excel_file),
        inserted,
        now(),
        CREATED_BY,
        f"Skipped rows: {skipped}",
    ))

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
    print(f"Всего в tbot_parking_import : {total}")


if __name__ == "__main__":
    import_tbot_quarantine()