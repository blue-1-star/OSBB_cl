"""
Довнесение vehicle_id для платежей, где он ошибочно не был проставлен —
только для ОДНОЗНАЧНЫХ случаев: у квартиры ровно одно известное авто,
платёж явно за парковку (base_service_code LIKE 'PARKING_%'), не похож
на тестовые данные. Правит синхронно payments и связанную
cashbox_operations (через payments.cashbox_operation_id).

Гипотеза (от пользователя): до 11.07.2026 работала версия программы,
не проставлявшая vehicle_id корректно; позже это было исправлено.

Использование:
    python backfill_single_vehicle.py

Показывает список кандидатов, просит подтверждение ОДИН раз на всю
пачку, потом применяет. Ничего не меняет без подтверждения.
"""

import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import paths, USE_TEST_DB


def get_db_path():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def find_backfill_candidates(conn):
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT id, payment_date, apartment_number, amount, base_service_code,
               cashbox_code, comment, cashbox_operation_id
        FROM payments WHERE vehicle_id IS NULL
    """).fetchall()

    candidates = []
    for r in rows:
        comment = (r["comment"] or "").lower()
        service = r["base_service_code"] or ""
        cashbox = r["cashbox_code"]

        if "тест" in comment or "test" in comment or cashbox is None:
            continue
        if not service.startswith("PARKING"):
            continue

        apt = cur.execute(
            "SELECT id FROM apartments WHERE apartment_number=?", (r["apartment_number"],)
        ).fetchone()
        if not apt:
            continue
        vehicles = cur.execute(
            "SELECT id, license_plate_normalized FROM vehicles WHERE apartment_id=?", (apt["id"],)
        ).fetchall()
        if len(vehicles) != 1:
            continue

        candidates.append({
            "payment": dict(r),
            "vehicle_id": vehicles[0]["id"],
            "plate": vehicles[0]["license_plate_normalized"],
        })
    return candidates


def apply_backfill(conn, candidates):
    cur = conn.cursor()
    for c in candidates:
        cur.execute("UPDATE payments SET vehicle_id=? WHERE id=?", (c["vehicle_id"], c["payment"]["id"]))
        cbo_id = c["payment"]["cashbox_operation_id"]
        if cbo_id:
            cur.execute("UPDATE cashbox_operations SET vehicle_id=? WHERE id=?", (c["vehicle_id"], cbo_id))
    conn.commit()


def main():
    db_path = get_db_path()
    print(f"📁 БД: {db_path}\n")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    candidates = find_backfill_candidates(conn)
    if not candidates:
        print("Однозначных кандидатов на довнесение не найдено.")
        conn.close()
        return

    print(f"Найдено кандидатов: {len(candidates)}\n")
    for c in candidates:
        p = c["payment"]
        print(f"  #{p['id']} {p['payment_date']} | кв.{p['apartment_number']} | "
              f"{p['amount']} грн -> vehicle_id={c['vehicle_id']} ({c['plate']})")

    print()
    answer = input(f"Применить все {len(candidates)} правок? (да/нет): ").strip().lower()
    if answer not in ("да", "yes", "y"):
        print("Отменено, ничего не изменено.")
        conn.close()
        return

    apply_backfill(conn, candidates)
    print(f"\n✅ Готово. Обновлено платежей: {len(candidates)}.")

    conn.close()


if __name__ == "__main__":
    main()