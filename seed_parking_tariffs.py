from pathlib import Path
import sys
import sqlite3

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths, USE_TEST_DB


TARIFFS = [
    {
        "service_code": "PARKING_DAY",
        "amount": 200,
        "valid_from": "2026-05-01",
        "comment": "Parking Day tariff from 01.05.2026",
    },
    {
        "service_code": "PARKING_NIGHT",
        "amount": 500,
        "valid_from": "2026-05-01",
        "comment": "Parking Night tariff from 01.05.2026",
    },
]


def get_db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def ensure_service_exists(cur, service_code):
    cur.execute("""
        SELECT id
        FROM service_catalog
        WHERE service_code = ?
    """, (service_code,))

    return cur.fetchone() is not None


def seed_tariff(cur, service_code, amount, valid_from, comment):
    if not ensure_service_exists(cur, service_code):
        return False, f"service_not_found: {service_code}"

    cur.execute("""
        SELECT id, amount, currency, is_active
        FROM service_tariffs
        WHERE service_code = ?
          AND valid_from = ?
          AND valid_to IS NULL
    """, (
        service_code,
        valid_from,
    ))

    row = cur.fetchone()

    if row:
        tariff_id = row[0]

        cur.execute("""
            UPDATE service_tariffs
            SET
                amount = ?,
                currency = 'UAH',
                is_active = 1,
                comment = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            amount,
            comment,
            tariff_id,
        ))

        return True, f"updated existing tariff id={tariff_id}"

    cur.execute("""
        INSERT INTO service_tariffs (
            service_code,
            amount,
            currency,
            valid_from,
            valid_to,
            is_active,
            comment
        )
        VALUES (?, ?, 'UAH', ?, NULL, 1, ?)
    """, (
        service_code,
        amount,
        valid_from,
        comment,
    ))

    return True, f"inserted tariff id={cur.lastrowid}"


def main():
    paths.ensure_directories()

    db_file = get_db_file()

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    print("=" * 70)
    print("SEED PARKING TARIFFS")
    print("=" * 70)
    print("DB:", db_file)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("")

    for item in TARIFFS:
        ok, result = seed_tariff(
            cur,
            item["service_code"],
            item["amount"],
            item["valid_from"],
            item["comment"],
        )

        print(
            f"{item['service_code']}: "
            f"{item['amount']} UAH from {item['valid_from']} -> {result}"
        )

    conn.commit()

    print("")
    print("Current active tariffs:")
    cur.execute("""
        SELECT
            service_code,
            amount,
            currency,
            valid_from,
            valid_to,
            is_active
        FROM service_tariffs
        WHERE is_active = 1
        ORDER BY service_code, valid_from
    """)

    for row in cur.fetchall():
        service_code, amount, currency, valid_from, valid_to, is_active = row
        print(
            f"  {service_code}: {amount:g} {currency} "
            f"from {valid_from} "
            f"to {valid_to or 'open'}"
        )

    conn.close()

    print("")
    print("=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    main()
