"""
Диагностика: авто-жертвы бага со сдвигом периода.

Находит квартиры с 2+ авто ОДНОГО режима парковки (Day/Day или
Night/Night) и считает, сколько у КАЖДОЙ машины реально СВОИХ платежей
за парковку (по vehicle_id, не по квартире в целом).

Машина с подозрительно малым числом платежей рядом с "богатой" соседкой
той же квартиры — вероятная жертва бага (latest_paid_period/
suggested_charge считали "оплачено по" по квартире+услуге, без учёта
конкретного авто, из-за чего оплата одной машины сдвигала подсказку
периода вперёд и для другой, которая не платила вовсе).

Ничего не меняет в базе — только читает и печатает. Ручная сверка
с бумажной ведомостью остаётся за оператором/админом.
"""

import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import paths, USE_TEST_DB


def get_db_path():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def find_suspect_vehicles(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT a.apartment_number, v.license_plate_normalized, v.parking_time,
               COUNT(p.id) AS payments_count, MAX(p.period_code) AS last_period
        FROM vehicles v
        JOIN apartments a ON a.id = v.apartment_id
        LEFT JOIN payments p ON p.vehicle_id = v.id AND p.base_service_code LIKE 'PARKING_%'
        WHERE v.parking_time IN ('Day', 'Night')
        GROUP BY v.id
        HAVING (
            SELECT COUNT(*) FROM vehicles v2
            WHERE v2.apartment_id = v.apartment_id AND v2.parking_time = v.parking_time
        ) > 1
        ORDER BY a.apartment_number, v.parking_time, payments_count
    """).fetchall()
    conn.close()
    return rows


def main():
    db_path = get_db_path()
    print(f"📁 БД: {db_path}")
    print()

    rows = find_suspect_vehicles(db_path)
    if not rows:
        print("Квартир с несколькими авто одного режима не найдено (или у всех всё в порядке).")
        return

    print(f"{'Квартира':<10} {'Номер':<12} {'Режим':<7} {'Платежей':<10} {'Последний период'}")
    print("-" * 60)
    for r in rows:
        print(
            f"{r['apartment_number'] or '—':<10} "
            f"{r['license_plate_normalized'] or '—':<12} "
            f"{r['parking_time'] or '—':<7} "
            f"{r['payments_count']:<10} "
            f"{r['last_period'] or '—'}"
        )

    print()
    print("⚠ Машины с 0 (или заметно меньше, чем у соседки той же квартиры) —")
    print("  вероятные жертвы бага. Сверьте с бумажной ведомостью вручную,")
    print("  ничего не исправляйте автоматически.")


if __name__ == "__main__":
    main()