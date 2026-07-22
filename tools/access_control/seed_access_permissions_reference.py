#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "Data" / "db" / "osbb_test.db"

STATIC_REFERENCE = [
    ("guard_workspace.ENTER", "Рабочее место охраны", "Охрана", "Вход в рабочее место охраны"),
    ("payment_notices.VIEW", "Просмотр уведомлений оплат", "Касса", "Просмотр уведомлений об оплатах"),
    ("payment_notices.CONFIRM", "Подтверждение оплат", "Касса", "Подтверждение уведомлений оплат"),
    ("cashier_receipts.VIEW", "Просмотр кассовых чеков", "Касса", "Просмотр чеков и квитанций"),
    ("cashier_receipts.CREATE", "Создание кассовых чеков", "Касса", "Создание чеков и квитанций"),
    ("service_orders.CREATE", "Создание сервисных заявок", "Услуги", "Создание заявок на услуги"),
    ("service_orders.VIEW", "Просмотр сервисных заявок", "Услуги", "Просмотр заявок на услуги"),
    ("service_orders.CONFIRM", "Подтверждение сервисных заявок", "Услуги", "Подтверждение выполнения заявок"),
    ("vehicles.SEARCH", "Поиск автомобиля", "Авто", "Поиск автомобиля по номеру"),
    ("vehicles.VIEW", "Просмотр автомобилей", "Авто", "Просмотр списка и карточек автомобилей"),
    ("vehicles.EDIT", "Редактирование автомобилей", "Авто", "Изменение карточек автомобилей"),
    ("vehicles.ARCHIVE", "Архивирование автомобиля", "Авто", "Перевод авто в архив"),
    ("reports.VIEW", "Просмотр отчётов", "Отчёты", "Доступ к разделу отчётов"),
    ("reports.DEBTORS", "Отчёт должников", "Отчёты", "Просмотр отчёта должников"),
    ("parking_registry.VIEW", "Реестр парковки", "Парковка", "Просмотр Parking Registry"),
    ("operator_tasks.VIEW", "Очередь оператора", "Оператор", "Просмотр задач оператора"),
    ("operator_tasks.EDIT", "Изменение задач оператора", "Оператор", "Взять, закрыть или изменить задачу"),
    ("users.VIEW", "Просмотр пользователей", "Пользователи", "Просмотр пользователей и ролей"),
    ("users.MANAGE", "Управление пользователями", "Пользователи", "Добавление и отключение пользователей"),
    ("residents.CONFIRM", "Подтверждение жителей", "Пользователи", "Подтверждение новых жителей"),
    ("admins.MANAGE", "Администраторы", "Администрирование", "Управление администраторами"),
    ("permissions.VIEW", "Просмотр прав", "Администрирование", "Просмотр справочника и матрицы прав"),
    ("permissions.MANAGE", "Управление правами", "Администрирование", "Включение и выключение прав ролей"),
]


def resolve(text: str) -> Path:
    p = Path(text)
    if p.is_absolute():
        return p
    return (PROJECT_ROOT.parent / p).resolve() if str(p).startswith("OSBB") else (PROJECT_ROOT / p).resolve()


def ensure_table(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS access_permissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        permission_code TEXT UNIQUE NOT NULL,
        permission_name TEXT NOT NULL,
        category TEXT,
        description TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT
    )""")


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed access_permissions reference without rebuilding RBAC/ACL tables.")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db = resolve(args.db)

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        dynamic = []
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='access_role_permissions'").fetchone():
            dynamic = con.execute(
                """
                SELECT DISTINCT resource, action
                FROM access_role_permissions
                WHERE COALESCE(is_active, 1)=1
                ORDER BY resource, action
                """
            ).fetchall()

        items = {code: (code, name, category, desc) for code, name, category, desc in STATIC_REFERENCE}
        for r in dynamic:
            code = f"{r['resource']}.{r['action']}"
            if code not in items:
                items[code] = (code, code, "Из существующих правил", f"Existing rule: {r['resource']} / {r['action']}")

        print("=" * 90)
        print("Seed access_permissions reference")
        print("=" * 90)
        print("DB:", db)
        print("Items:", len(items))
        print("Apply:", args.apply)

        if not args.apply:
            print("DRY RUN ONLY - no changes saved.")
            print("To apply:")
            print("python .\\OSBB\\tools\\access_control\\seed_access_permissions_reference.py --apply")
            return 0

        ensure_table(con)
        for code, name, category, desc in sorted(items.values()):
            con.execute(
                """
                INSERT INTO access_permissions(permission_code, permission_name, category, description, is_active)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(permission_code) DO UPDATE SET
                    permission_name=excluded.permission_name,
                    category=excluded.category,
                    description=excluded.description,
                    is_active=1,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (code, name, category, desc),
            )
        con.commit()
        print("APPLIED")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
