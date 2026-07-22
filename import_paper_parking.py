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


SOURCE_NAME = "paper_parking"
CREATED_BY = "import_paper_parking"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def norm(value):
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


def get_entrance(apartment_number):
    try:
        n = int(str(apartment_number).strip())
    except ValueError:
        return None

    ranges = [
        (1, 16, "1"),
        (17, 142, "2"),
        (143, 158, "3"),
        (159, 174, "4"),
        (175, 190, "5"),
        (191, 206, "6"),
        (207, 214, "7"),
        (215, 222, "8"),
        (223, 230, "9"),
        (231, 238, "10"),
    ]

    for start, end, entrance in ranges:
        if start <= n <= end:
            return entrance

    return None


def get_or_create_apartment(cur, apartment_number, entrance_from_file=None):
    cur.execute(
        "SELECT id, entrance FROM apartments WHERE apartment_number = ?",
        (apartment_number,),
    )
    row = cur.fetchone()

    calculated_entrance = get_entrance(apartment_number)
    entrance = calculated_entrance or entrance_from_file

    if row:
        apartment_id, existing_entrance = row

        if not existing_entrance and entrance:
            cur.execute(
                """
                UPDATE apartments
                SET entrance = ?,
                    entrance_source = ?,
                    updated_at = ?,
                    updated_by = ?
                WHERE id = ?
                """,
                (
                    entrance,
                    "calculated" if calculated_entrance else SOURCE_NAME,
                    now(),
                    CREATED_BY,
                    apartment_id,
                ),
            )

        return apartment_id, False

    cur.execute(
        """
        INSERT INTO apartments (
            apartment_number,
            entrance,
            entrance_source,
            object_type,
            status,
            source,
            created_at,
            created_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            apartment_number,
            entrance,
            "calculated" if calculated_entrance else SOURCE_NAME,
            "parking_participant",
            "active",
            SOURCE_NAME,
            now(),
            CREATED_BY,
        ),
    )

    return cur.lastrowid, True


def insert_person_if_needed(cur, apartment_id, owner_name, phone_number):
    if not owner_name and not phone_number:
        return False

    cur.execute(
        """
        SELECT id
        FROM persons
        WHERE apartment_id = ?
          AND IFNULL(full_name, '') = IFNULL(?, '')
          AND IFNULL(phone_raw, '') = IFNULL(?, '')
          AND source = ?
        """,
        (apartment_id, owner_name, phone_number, SOURCE_NAME),
    )

    if cur.fetchone():
        return False

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
            owner_name,
            phone_number,
            SOURCE_NAME,
            now(),
            CREATED_BY,
        ),
    )

    return True


def insert_vehicle_if_needed(cur, apartment_id, license_plate, car_model, parking_time):
    if not license_plate and not car_model:
        return False

    cur.execute(
        """
        SELECT id
        FROM vehicles
        WHERE apartment_id = ?
          AND IFNULL(license_plate, '') = IFNULL(?, '')
          AND IFNULL(car_model, '') = IFNULL(?, '')
          AND IFNULL(parking_time, '') = IFNULL(?, '')
          AND source = ?
        """,
        (apartment_id, license_plate, car_model, parking_time, SOURCE_NAME),
    )

    if cur.fetchone():
        return False

    cur.execute(
        """
        INSERT INTO vehicles (
            apartment_id,
            license_plate,
            car_model,
            parking_time,
            status,
            source,
            created_at,
            created_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            apartment_id,
            license_plate,
            car_model,
            parking_time,
            "active",
            SOURCE_NAME,
            now(),
            CREATED_BY,
        ),
    )

    return True


def import_paper_parking():
    excel_file = paths.OSBB_PAPER_PARKING_FILE
    db_file = paths.OSBB_DB_FILE

    print("=" * 70)
    print("IMPORT PAPER PARKING BASE")
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

    rows_total = len(df)
    skipped = 0
    created_apartments = 0
    existing_apartments = 0
    inserted_persons = 0
    inserted_vehicles = 0

    for _, row in df.iterrows():
        entrance = norm(row.get("entrance"))
        apartment_number = norm_apartment(row.get("apartment_number"))
        owner_name = norm(row.get("owner_name"))
        phone_number = norm(row.get("phone_number"))
        license_plate = norm(row.get("license_plate"))
        car_model = norm(row.get("car_model"))
        parking_time = norm(row.get("parking_time"))

        if not apartment_number:
            skipped += 1
            continue

        apartment_id, was_created = get_or_create_apartment(
            cur,
            apartment_number,
            entrance,
        )

        if was_created:
            created_apartments += 1
        else:
            existing_apartments += 1

        if insert_person_if_needed(cur, apartment_id, owner_name, phone_number):
            inserted_persons += 1

        if insert_vehicle_if_needed(
            cur,
            apartment_id,
            license_plate,
            car_model,
            parking_time,
        ):
            inserted_vehicles += 1

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM apartments")
    apartments_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM persons")
    persons_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM vehicles")
    vehicles_count = cur.fetchone()[0]

    conn.close()

    print("=" * 70)
    print("IMPORT COMPLETED")
    print("=" * 70)
    print(f"Строк в Excel              : {rows_total}")
    print(f"Пропущено                 : {skipped}")
    print(f"Новых квартир создано      : {created_apartments}")
    print(f"Квартир уже было в БД      : {existing_apartments}")
    print(f"Контактов добавлено        : {inserted_persons}")
    print(f"Автомобилей добавлено      : {inserted_vehicles}")
    print()
    print(f"Всего квартир в БД         : {apartments_count}")
    print(f"Всего контактов в БД       : {persons_count}")
    print(f"Всего автомобилей в БД     : {vehicles_count}")


if __name__ == "__main__":
    import_paper_parking()