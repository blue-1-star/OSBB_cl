"""
Безопасное восстановление привязки Telegram-кабинета к квартире.

Изменяет только resident_accounts указанного Telegram ID.
Карточка случайно открытой квартиры, её автомобили, контакты, начисления,
оплаты и данные других жильцов не меняются.

По умолчанию dry-run.

Пример: вернуть тестовый кабинет с 166 на 174
  g:/Programming/Py/venv/Scripts/python.exe ^
    G:/Programming/Py/OSBB/restore_resident_apartment_link.py ^
    --telegram-id 210312208 ^
    --target-apartment 174

Применить:
  g:/Programming/Py/venv/Scripts/python.exe ^
    G:/Programming/Py/OSBB/restore_resident_apartment_link.py ^
    --telegram-id 210312208 ^
    --target-apartment 174 ^
    --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
for folder in (ROOT, ROOT.parent):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB

try:
    from audit_logger import audit_log
except Exception:
    audit_log = None


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    cur.execute(f'PRAGMA table_info("{table}")')
    return {row[1] for row in cur.fetchall()}


def fetch_one_as_dict(cur: sqlite3.Cursor) -> dict | None:
    row = cur.fetchone()
    if row is None:
        return None
    names = [item[0] for item in cur.description]
    return dict(zip(names, row))


def get_account(cur: sqlite3.Cursor, telegram_id: str) -> dict | None:
    cur.execute(
        "SELECT * FROM resident_accounts WHERE telegram_user_id = ? LIMIT 1",
        (telegram_id,),
    )
    return fetch_one_as_dict(cur)


def get_unit_by_id(cur: sqlite3.Cursor, unit_id: Any) -> dict | None:
    if not unit_id:
        return None
    cur.execute("SELECT * FROM apartments WHERE id = ?", (int(unit_id),))
    return fetch_one_as_dict(cur)


def get_target_unit(cur: sqlite3.Cursor, number: str) -> dict | None:
    unit_cols = columns(cur, "apartments")
    predicates: list[str] = []
    params: list[Any] = []

    if "apartment_number" in unit_cols:
        predicates.append("apartment_number = ?")
        params.append(number)
    if "unit_code" in unit_cols:
        predicates.append("unit_code = ?")
        params.append(number)

    if not predicates:
        return None

    cur.execute(
        f"SELECT * FROM apartments WHERE {' OR '.join(predicates)} ORDER BY id LIMIT 1",
        tuple(params),
    )
    return fetch_one_as_dict(cur)


def unit_label(unit: dict | None) -> str:
    if not unit:
        return "не привязана"
    return str(
        unit.get("apartment_number")
        or unit.get("unit_code")
        or unit.get("id")
        or "-"
    )


def get_latest_account_audit(cur: sqlite3.Cursor, account_id: int) -> list[dict]:
    if not table_exists(cur, "operator_audit_log"):
        return []

    cur.execute(
        """
        SELECT
            id,
            created_at,
            actor_type,
            operator_id,
            action_type,
            table_name,
            row_id,
            field_name,
            old_value,
            new_value,
            source_context,
            comment
        FROM operator_audit_log
        WHERE table_name = 'resident_accounts'
          AND row_id = ?
        ORDER BY id DESC
        LIMIT 10
        """,
        (str(account_id),),
    )
    names = [item[0] for item in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def update_account_link(cur: sqlite3.Cursor, account: dict, target: dict) -> None:
    cols = columns(cur, "resident_accounts")
    assignments: list[str] = []
    params: list[Any] = []

    if "apartment_id" in cols:
        assignments.append("apartment_id = ?")
        params.append(int(target["id"]))

    if "apartment_number" in cols:
        target_number = target.get("apartment_number") or target.get("unit_code")
        assignments.append("apartment_number = ?")
        params.append(str(target_number))

    if "updated_at" in cols:
        assignments.append("updated_at = ?")
        params.append(now_db())

    if not assignments:
        raise RuntimeError("В resident_accounts нет полей apartment_id/apartment_number.")

    params.append(int(account["id"]))
    cur.execute(
        f"UPDATE resident_accounts SET {', '.join(assignments)} WHERE id = ?",
        tuple(params),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--telegram-id", required=True)
    parser.add_argument("--target-apartment", required=True)
    parser.add_argument("--operator-id", default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db = get_db_file()
    if not db.exists():
        raise SystemExit(f"Не найдена БД: {db}")

    operator_id = str(args.operator_id or args.telegram_id)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    if not table_exists(cur, "resident_accounts"):
        raise SystemExit("Не найдена таблица resident_accounts.")
    if not table_exists(cur, "apartments"):
        raise SystemExit("Не найдена таблица apartments.")

    account = get_account(cur, str(args.telegram_id))
    if not account:
        raise SystemExit(
            f"Не найден resident_accounts для Telegram ID {args.telegram_id}."
        )

    current_unit = get_unit_by_id(cur, account.get("apartment_id"))
    target_unit = get_target_unit(cur, str(args.target_apartment))
    if not target_unit:
        raise SystemExit(f"Не найдена целевая квартира: {args.target_apartment}.")

    target_type = str(target_unit.get("unit_type") or "")
    if target_type and target_type != "RESIDENTIAL":
        raise SystemExit(
            f"Цель {unit_label(target_unit)} имеет тип {target_type}; "
            "нужна жилая квартира."
        )

    report_dir = paths.OSBB_EXPORTS_DIR / "users"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / (
        f"restore_resident_link_{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"
    )

    lines = [
        "=" * 100,
        "RESTORE RESIDENT APARTMENT LINK",
        "=" * 100,
        f"DB: {db}",
        f"MODE: {'TEST/WORK' if USE_TEST_DB else 'PROD'}",
        f"Apply: {args.apply}",
        f"Telegram ID: {args.telegram_id}",
        f"Resident account id: {account['id']}",
        f"Current linked apartment: {unit_label(current_unit)}",
        f"Target apartment: {unit_label(target_unit)}",
        "",
        "Что изменяется:",
        "  Только resident_accounts.apartment_id / apartment_number указанного Telegram-кабинета.",
        "",
        "Что НЕ изменяется:",
        "  Карточка квартиры, автомобили, контакты, начисления, оплаты,",
        "  коммерческие договоры и данные других пользователей.",
        "",
        "Последние записи аудита по этому resident_account:",
    ]

    audit_rows = get_latest_account_audit(cur, int(account["id"]))
    if audit_rows:
        for row in audit_rows:
            lines.append(
                f"  #{row['id']} {row['created_at']} | {row['action_type']} | "
                f"{row['field_name']} | {row['old_value']} -> {row['new_value']}"
            )
    else:
        lines.append("  Нет.")

    audit_id = None
    if args.apply:
        update_account_link(cur, account, target_unit)

        if audit_log:
            audit_id = audit_log(
                conn=conn,
                operator_id=operator_id,
                user_id=operator_id,
                actor_type="operator",
                action_type="resident_account_apartment_link_restored",
                table_name="resident_accounts",
                row_id=account["id"],
                field_name="apartment_id,apartment_number",
                old_value=(
                    f"{account.get('apartment_id') or ''},"
                    f"{account.get('apartment_number') or ''}"
                ),
                new_value=(
                    f"{target_unit.get('id')},"
                    f"{target_unit.get('apartment_number') or target_unit.get('unit_code')}"
                ),
                source_context="restore_resident_apartment_link.py",
                comment=(
                    "Восстановлена привязка Telegram-кабинета после "
                    "случайного тестового выбора другой квартиры. "
                    "Карточка случайной квартиры не изменялась."
                ),
                extra={
                    "telegram_user_id": str(args.telegram_id),
                    "old_apartment": unit_label(current_unit),
                    "target_apartment": unit_label(target_unit),
                },
                commit=False,
            )

        conn.commit()
        lines.extend(["", f"APPLIED. audit_id={audit_id if audit_id is not None else '-'}"])
    else:
        conn.rollback()
        lines.extend(["", "DRY RUN COMPLETED - NO CHANGES SAVED"])

    conn.close()
    report.write_text("\n".join(lines), encoding="utf-8")

    print("=" * 100)
    print("RESTORE RESIDENT APARTMENT LINK")
    print("=" * 100)
    print("DB:", db)
    print("MODE:", "TEST/WORK" if USE_TEST_DB else "PROD")
    print("Apply:", args.apply)
    print("Telegram ID:", args.telegram_id)
    print("Current linked apartment:", unit_label(current_unit))
    print("Target apartment:", unit_label(target_unit))
    print("Report:", report)
    print()
    print("APPLIED" if args.apply else "DRY RUN COMPLETED - NO CHANGES SAVED")


if __name__ == "__main__":
    main()
