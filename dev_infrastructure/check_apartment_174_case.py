"""
Прицельная диагностика: почему у конкретной квартиры (по умолчанию 174)
платёж создался без vehicle_id, хотя авто этой квартиры уже много раз
привязывалось раньше.

Показывает: (1) сам подозрительный платёж целиком, (2) все известные
авто этой квартиры и их parking_time, (3) прошлые платежи ЭТОЙ ЖЕ
квартиры, где vehicle_id был проставлен — для сравнения.

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
    apartment_number = sys.argv[1] if len(sys.argv) > 1 else "174"
    db_path = get_db_path()
    print(f"📁 БД: {db_path}")
    print(f"Квартира: {apartment_number}\n")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("=== Все известные авто этой квартиры ===")
    apt = cur.execute("SELECT id FROM apartments WHERE apartment_number=?", (apartment_number,)).fetchone()
    if apt:
        vehicles = cur.execute(
            "SELECT id, license_plate_normalized, parking_time FROM vehicles WHERE apartment_id=?",
            (apt["id"],),
        ).fetchall()
        for v in vehicles:
            print(f"  vehicle_id={v['id']} | {v['license_plate_normalized']} | режим: {v['parking_time'] or '—'}")
    else:
        print("  Квартира не найдена в apartments.")
    print()

    print("=== ВСЕ платежи этой квартиры (последние 15), с vehicle_id и без ===")
    payments = cur.execute(
        """
        SELECT id, payment_date, amount, period_code, base_service_code, vehicle_id, cashbox_code, comment
        FROM payments
        WHERE apartment_number = ?
        ORDER BY id DESC LIMIT 15
        """,
        (apartment_number,),
    ).fetchall()
    for p in payments:
        marker = "❓ БЕЗ АВТО" if p["vehicle_id"] is None else f"vehicle_id={p['vehicle_id']}"
        print(f"  #{p['id']} {p['payment_date']} | {p['amount']} грн | {p['period_code']} | "
              f"{p['base_service_code'] or '—'} | касса {p['cashbox_code']} | {marker}")
        if p["comment"]:
            print(f"        комментарий: {p['comment']}")

    conn.close()


if __name__ == "__main__":
    main()