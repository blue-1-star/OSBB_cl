"""
Миграция: добавляем PARKING_UNSPECIFIED в service_catalog
Номер: 2026-07-26_003

Назначение: честная услуга для платежа за парковку, когда режим
(Day/Night) авто неизвестен на момент приёма оплаты. Раньше отсутствие
такой записи вынуждало кассира либо гадать между Day/Night, либо
случайно попадать на постороннюю услугу через нечёткий текстовый поиск
choose_service().
"""

import sys
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import paths, USE_TEST_DB


SERVICE_CODE = "PARKING_UNSPECIFIED"


def get_db_path():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def service_exists(db_path: str, service_code: str) -> bool:
    """Проверяет, есть ли уже такая услуга в service_catalog."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM service_catalog WHERE service_code = ?", (service_code,))
    row = cur.fetchone()
    conn.close()
    return row is not None


def up():
    db_path = get_db_path()
    print(f"🔧 Миграция 2026-07-26_003: добавляем {SERVICE_CODE} в service_catalog")
    print(f"📁 БД: {db_path}")

    if service_exists(db_path, SERVICE_CODE):
        print(f"⏭ {SERVICE_CODE} уже существует в service_catalog. Ничего не делаю.")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    try:
        print(f"  ➜ Вставляю запись {SERVICE_CODE}...")
        cur.execute(
            """
            INSERT INTO service_catalog (
                service_code, service_group, service_name, unit,
                is_active, service_type, category,
                is_monthly, is_fundraising, is_commercial, is_access_control,
                is_cash_collectable, access_policy_enabled,
                access_policy_scope, access_policy_mode,
                manual_review_required
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                SERVICE_CODE,
                "MONTHLY",
                "Парковка (режим не определён)",
                "service",
                1,          # is_active
                "MONTHLY",  # service_type
                "PARKING",  # category
                1,          # is_monthly       — как у PARKING_DAY
                0,          # is_fundraising
                0,          # is_commercial
                0,          # is_access_control
                1,          # is_cash_collectable — как у PARKING_DAY
                0,          # access_policy_enabled
                "NONE",     # access_policy_scope
                "NONE",     # access_policy_mode
                # manual_review_required = 1, сознательно (не как у PARKING_DAY):
                # любой платёж с этим кодом по смыслу требует последующего разбора.
                1,
            ),
        )
        conn.commit()
        print(f"✅ Готово: {SERVICE_CODE} добавлена в service_catalog.")
    except Exception as e:
        conn.rollback()
        print(f"❌ Ошибка миграции, откат: {e}")
        raise
    finally:
        conn.close()


def down():
    """Откат — удаляет запись, только если по ней ещё нет реальных
    платежей (чтобы не оставить payments/cashbox_operations без
    справочной записи)."""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT COUNT(*) FROM payments WHERE base_service_code = ?", (SERVICE_CODE,)
        )
        used_count = cur.fetchone()[0]
        if used_count > 0:
            print(f"⚠ Не удаляю {SERVICE_CODE} — на неё уже ссылаются {used_count} платеж(ей).")
            return
        cur.execute("DELETE FROM service_catalog WHERE service_code = ?", (SERVICE_CODE,))
        conn.commit()
        print(f"✅ {SERVICE_CODE} удалена из service_catalog.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()