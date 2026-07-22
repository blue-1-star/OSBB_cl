# audit_composite_apartments.py

from pathlib import Path
import sys

PY_ROOT = Path(__file__).resolve().parent.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))
import re
import sqlite3

from config import paths


COMPOSITE_RE = re.compile(
    r"^\d+\s*[._/,]\s*\d+$"
)


def is_composite_apartment(value):
    if value is None:
        return False

    value = str(value).strip()

    return bool(COMPOSITE_RE.match(value))


def get_main_apartments():
    conn = sqlite3.connect(paths.OSBB_DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        SELECT apartment_number
        FROM apartments
        ORDER BY apartment_number
    """)

    rows = [str(r[0]).strip() for r in cur.fetchall()]

    conn.close()

    return rows


def get_tbot_rows():
    conn = sqlite3.connect(paths.OSBB_QUARANTINE_DB_FILE)
    cur = conn.cursor()

    cur.execute("""
        SELECT
            apartment_number,
            license_plate,
            license_plate_normalized,
            car_model,
            car_model_normalized,
            
            phone_normalized    
        FROM tbot_parking_import
    """)

    rows = cur.fetchall()

    conn.close()

    return rows


def apartment_exists(apartment_set, apartment_number):
    return str(apartment_number).strip() in apartment_set


def normalize_proposal(apartment_number):
    apt = str(apartment_number).strip()

    if apt == "89.9":
        return "89_90"

    apt = re.sub(r"[./,]", "_", apt)

    return apt


def split_apartments(apartment_number):
    apt = str(apartment_number).strip()

    parts = re.split(r"[._/,]", apt)

    return [p.strip() for p in parts if p.strip()]


def main():

    print("=" * 70)
    print("COMPOSITE APARTMENT AUDIT")
    print("=" * 70)

    main_apartments = set(get_main_apartments())

    rows = get_tbot_rows()

    composite_rows = []

    for row in rows:

        (
            apartment_number,
            plate_raw,
            plate_norm,
            model_raw,
            model_norm,
            phone,
        ) = row

        if not is_composite_apartment(apartment_number):
            continue

        composite_rows.append(row)

    if not composite_rows:

        print()
        print("No composite apartments found.")
        return

    print()
    print("FOUND IN TBOT")
    print("-" * 70)

    for row in composite_rows:

        (
            apartment_number,
            plate_raw,
            plate_norm,
            model_raw,
            model_norm,
            phone,
        ) = row

        proposal = normalize_proposal(apartment_number)

        print()
        print(f"Apartment : {apartment_number}")
        print(f"Normalize : {proposal}")

        parts = split_apartments(apartment_number)

        for part in parts:

            exists = apartment_exists(
                main_apartments,
                part
            )

            print(
                f"  {part:<6} -> "
                f"{'FOUND' if exists else 'NOT FOUND'}"
            )

        print(f"Plate     : {plate_norm or plate_raw}")
        print(f"Model     : {model_norm or model_raw}")
        print(f"Phone     : {phone}")

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print(
        f"Composite records found: "
        f"{len(composite_rows)}"
    )


if __name__ == "__main__":
    main()