
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


SOURCE_NAME = "house_registry"
CREATED_BY = "import_house_registry"

COL_APARTMENT = "№кв-ри"
COL_AREA = "заг.пл-ща"
COL_PHONE = "Телефон"
COL_NAME = "П.І.Б"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_text(value):
    if pd.isna(value):
        return None
    value = str(value).strip()
    return value if value else None


def norm_apartment(value):
    if pd.isna(value):
        return None

    if isinstance(value, float) and value.is_integer():
        return str(int(value))

    text = str(value).strip()

    if text.endswith(".0"):
        text = text[:-2]

    return text if text else None


def import_house_registry():
    excel_file = paths.OSBB_HOUSE_REGISTRY_FILE
    db_file = paths.OSBB_DB_FILE

    print("=" * 70)
    print("IMPORT HOUSE REGISTRY")
    print("=" * 70)
    print(f"Excel : {excel_file}")
    print(f"DB    : {db_file}")
    print()

    if not excel_file.exists():
        raise FileNotFoundError(f"Файл не найден:\n{excel_file}")

    df = pd.read_excel(excel_file)

    print("Столбцы файла:")
    print(list(df.columns))
    print()

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    imported_apartments = 0
    imported_persons = 0
    skipped_rows = 0

    for _, row in df.iterrows():
        apartment_number = norm_apartment(row.get(COL_APARTMENT))

        if not apartment_number:
            skipped_rows += 1
            continue

        total_area = row.get(COL_AREA)
        full_name = normalize_text(row.get(COL_NAME))
        phone_raw = normalize_text(row.get(COL_PHONE))

        cur.execute(
            """
            INSERT OR IGNORE INTO apartments (
                apartment_number,
                total_area,
                source,
                created_at,
                created_by
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                apartment_number,
                total_area,
                SOURCE_NAME,
                now(),
                CREATED_BY,
            ),
        )

        cur.execute(
            "SELECT id FROM apartments WHERE apartment_number = ?",
            (apartment_number,),
        )
        apartment_id = cur.fetchone()[0]

        if full_name or phone_raw:
            cur.execute(
                """
                INSERT INTO persons (
                    apartment_id,
                    full_name,
                    phone_raw,
                    source,
                    created_at,
                    created_by
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    apartment_id,
                    full_name,
                    phone_raw,
                    SOURCE_NAME,
                    now(),
                    CREATED_BY,
                ),
            )
            imported_persons += 1

        imported_apartments += 1

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM apartments")
    apartments_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM persons")
    persons_count = cur.fetchone()[0]

    conn.close()

    print("=" * 70)
    print("IMPORT COMPLETED")
    print("=" * 70)
    print(f"Строк обработано     : {len(df)}")
    print(f"Пропущено            : {skipped_rows}")
    print(f"Импорт квартир       : {imported_apartments}")
    print(f"Импорт контактов     : {imported_persons}")
    print()
    print(f"Всего квартир в БД   : {apartments_count}")
    print(f"Всего контактов в БД : {persons_count}")


if __name__ == "__main__":
    import_house_registry()