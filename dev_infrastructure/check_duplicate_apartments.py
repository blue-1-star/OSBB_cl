"""
Диагностика: почему у некоторых квартир (132, 161, 174, 98, 171, 30)
"Ведомость" показывает пустой номер авто, хотя данные в vehicles на
вид корректны при прямой проверке.

Гипотеза: дубликаты — несколько строк apartments с одним и тем же
apartment_number, или несколько строк vehicles на одну apartment_id,
и объединение (JOIN) цепляет не ту связку.

Ничего не меняет, только читает и печатает.
"""

import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import paths, USE_TEST_DB


def get_db_path():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def main():
    apartments_to_check = sys.argv[1:] if len(sys.argv) > 1 else ["132", "161", "174", "98", "171", "30"]

    db_path = get_db_path()
    print(f"📁 БД: {db_path}\n")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    for apt_number in apartments_to_check:
        print(f"=== Квартира {apt_number} ===")

        apt_rows = cur.execute(
            "SELECT id, apartment_number FROM apartments WHERE apartment_number = ?", (apt_number,)
        ).fetchall()
        print(f"  Строк в apartments с этим номером: {len(apt_rows)}")
        for a in apt_rows:
            print(f"    apartment_id={a['id']}")
            vehicles = cur.execute(
                "SELECT id, license_plate, license_plate_normalized, parking_time FROM vehicles WHERE apartment_id=?",
                (a["id"],),
            ).fetchall()
            print(f"    Авто на этот apartment_id: {len(vehicles)}")
            for v in vehicles:
                print(f"      vehicle_id={v['id']} | plate={v['license_plate']!r} | "
                      f"normalized={v['license_plate_normalized']!r} | режим={v['parking_time']!r}")
        print()

    conn.close()


if __name__ == "__main__":
    main()