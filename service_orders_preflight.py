"""
Проверка общего контура услуг, пультов и телефонного доступа — только sandbox.

Скрипт:
1. Читает исходную sandbox-БД.
2. Создаёт новую независимую копию.
3. Применяет миграцию общего контура только к этой новой копии.
4. Создаёт технические тестовые предложения услуг:
   - перепрошивка собственного пульта;
   - подключение телефонного доступа.
5. Проверяет связанные шаги заказа:
   деньги / приём пульта / перепрошивка / возврат;
   деньги / активация телефонного доступа.
6. Архивирует один тестовый сервис вместо удаления.
7. Ничего не меняет в исходной sandbox, osbb_test.db, osbb.db или parking_bot.py.

Запуск:
g:\Programming\Py\venv\Scripts\python.exe G:\Programming\Py\OSBB\service_orders_preflight.py --sandbox "G:\Programming\Py\OSBB\Data\db\sandbox\ИМЯ_ТЕКУЩЕЙ_GUARD_SANDBOX.db"
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import os
import sqlite3
import sys
import traceback
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
BOTS = ROOT / "Bots"
SANDBOX_DIR = ROOT / "Data" / "db" / "sandbox"


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Не удалось подготовить модуль: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def clone_database(source: Path, target: Path) -> None:
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def clone_paths(original, sandbox_db: Path):
    try:
        cloned = copy.copy(original)
        if cloned is original:
            raise RuntimeError
    except Exception:
        data = {}
        for name in dir(original):
            if name.startswith("_"):
                continue
            value = getattr(original, name)
            if not callable(value):
                data[name] = value
        cloned = SimpleNamespace(**data)
    setattr(cloned, "OSBB_TEST_DB_FILE", sandbox_db)
    return cloned


def table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def table_columns(cur: sqlite3.Cursor, name: str) -> set[str]:
    if not table_exists(cur, name):
        return set()
    cur.execute(f'PRAGMA table_info("{name}")')
    return {row[1] for row in cur.fetchall()}


def find_unit(cur: sqlite3.Cursor, apartment_number: str) -> tuple[int | None, str]:
    if not table_exists(cur, "apartments"):
        return None, apartment_number
    cols = table_columns(cur, "apartments")
    if "apartment_number" not in cols:
        return None, apartment_number
    cur.execute(
        """
        SELECT id, apartment_number
        FROM apartments
        WHERE CAST(apartment_number AS TEXT) = ?
        LIMIT 1
        """,
        (apartment_number,),
    )
    row = cur.fetchone()
    return (int(row[0]), str(row[1])) if row else (None, apartment_number)


def find_resident_account(cur: sqlite3.Cursor, apartment_id: int | None) -> int | None:
    if apartment_id is None or not table_exists(cur, "resident_accounts"):
        return None
    cols = table_columns(cur, "resident_accounts")
    if "apartment_id" not in cols:
        return None
    cur.execute(
        """
        SELECT id
        FROM resident_accounts
        WHERE apartment_id = ?
        ORDER BY id
        LIMIT 1
        """,
        (apartment_id,),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sandbox", required=True)
    args = parser.parse_args()

    source = Path(args.sandbox).resolve()
    try:
        source.relative_to(SANDBOX_DIR.resolve())
    except ValueError:
        raise SystemExit("Разрешены только БД внутри Data\\db\\sandbox.")
    if not source.exists():
        raise SystemExit(f"Не найдена sandbox-БД: {source}")

    target = SANDBOX_DIR / f"{source.stem}_service_orders_check_{now_stamp()}.db"
    report = SANDBOX_DIR / f"service_orders_preflight_{now_stamp()}.txt"

    files = {
        "migration": ROOT / "migrate_service_orders_and_fulfillment.py",
        "orders core": ROOT / "service_orders_core.py",
        "catalog core": ROOT / "service_catalog_admin_core.py",
    }
    lines = [
        "=" * 100,
        "SERVICE ORDERS / REMOTES / PHONE ACCESS PREFLIGHT — SANDBOX ONLY",
        "=" * 100,
        f"Source sandbox (read-only): {source}",
        f"New service-orders sandbox: {target}",
        "",
    ]
    errors: list[str] = []

    for label, path in files.items():
        if not path.exists():
            errors.append(f"Не найден {label}: {path}")

    if errors:
        lines.extend(["RESULT: NOT READY — nothing changed", *errors])
        report.write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines))
        print("Report:", report)
        return 1

    lines.append("1. Компиляция")
    for label, path in files.items():
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
            lines.append(f"   OK  {label}: {path.name}")
        except Exception as exc:
            errors.append(f"Compile {path.name}: {exc}")
            lines.append(f"   ERR {label}: {exc}")

    lines.append("")
    lines.append("2. Независимая копия sandbox")
    try:
        clone_database(source, target)
        lines.append("   OK  clone created")
    except Exception:
        errors.append("Не удалось создать копию sandbox")
        lines.append(traceback.format_exc())

    if target.exists():
        lines.append("")
        lines.append("3. Миграция общего контура только в новой копии")
        try:
            migration = load_module(files["migration"], "_service_orders_migration_preflight")
            conn = sqlite3.connect(target)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                migration_result = migration.ensure_schema(conn)
                conn.commit()
            finally:
                conn.close()
            lines.append("   OK  migration applied to clone")
            lines.append(f"       workflow: {migration_result['workflow_seed']}")
            lines.append(
                f"       phone mapping: {migration_result['phone_access_mapping_seeded']}"
            )
        except Exception:
            errors.append("Миграция общего контура не прошла на копии")
            lines.append(traceback.format_exc())

        lines.append("")
        lines.append("4. Общая модель заказов: пульт и телефонный доступ")
        try:
            for folder in (ROOT, BOTS, ROOT.parent):
                if str(folder) not in sys.path:
                    sys.path.insert(0, str(folder))

            import config
            config.paths = clone_paths(config.paths, target)
            config.USE_TEST_DB = True
            os.environ["OSBB_SANDBOX_DB"] = str(target)
            os.environ["OSBB_SANDBOX_MODE"] = "1"

            for module_name in [
                "service_orders_core",
                "service_catalog_admin_core",
                "access_control",
                "audit_logger",
            ]:
                sys.modules.pop(module_name, None)

            orders = load_module(files["orders core"], "service_orders_core")
            catalog = load_module(files["catalog core"], "service_catalog_admin_core")

            conn = sqlite3.connect(target)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.cursor()
                apartment_id, apartment_number = find_unit(cur, "174")
                resident_account_id = find_resident_account(cur, apartment_id)

                # Technical offer: resident's own remote reprogramming.
                remote_offer = catalog.add_or_update_service_offer(
                    service_code="PREFLIGHT_REMOTE_REPROGRAM",
                    service_name="PREFLIGHT — перепрошивка пульта жильца",
                    service_group="ACCESS_CONTROL",
                    service_type="ONE_TIME",
                    category="REMOTE",
                    service_item_code="PREFLIGHT_REMOTE_REPROGRAM",
                    service_item_name="PREFLIGHT — перепрошивка пульта жильца",
                    workflow_profile_code="REMOTE_REPROGRAM_OWN",
                    amount=123,
                    effective_from="2026-07-01",
                    resident_request_enabled=True,
                    actor_id=None,
                    description="Техническая проверка. Не рабочая услуга.",
                    conn=conn,
                )
                remote_order = orders.create_service_order(
                    resident_account_id=resident_account_id,
                    telegram_user_id=None,
                    apartment_id=apartment_id,
                    apartment_number=apartment_number,
                    service_item_code=remote_offer["service_item_code"],
                    quantity=1,
                    resident_comment="PREFLIGHT",
                    actor_id=None,
                    source_context="service_orders_preflight",
                    conn=conn,
                )

                asset = orders.create_remote_asset(
                    asset_number=f"PREFLIGHT-OWN-{now_stamp()}",
                    ownership_type="RESIDENT",
                    inventory_status="IN_SERVICE",
                    condition_status="UNKNOWN",
                    apartment_id=apartment_id,
                    apartment_number=apartment_number,
                    actor_id=None,
                    note="Техническая проверка",
                    conn=conn,
                )
                orders.record_remote_movement(
                    remote_asset_id=asset["id"],
                    service_order_id=remote_order["id"],
                    movement_type="RECEIVED_FROM_RESIDENT",
                    to_state="IN_SERVICE",
                    actor_id=None,
                    apartment_id=apartment_id,
                    apartment_number=apartment_number,
                    confirm_step_code="RESIDENT_REMOTE_RECEIVED",
                    note="PREFLIGHT",
                    conn=conn,
                )
                orders.confirm_order_step(
                    order_id=remote_order["id"],
                    step_code="PAYMENT_CONFIRMED",
                    actor_id=None,
                    note="PREFLIGHT payment only",
                    conn=conn,
                )
                orders.record_remote_movement(
                    remote_asset_id=asset["id"],
                    service_order_id=remote_order["id"],
                    movement_type="PROGRAMMED",
                    to_state="PROGRAMMED",
                    actor_id=None,
                    confirm_step_code="REMOTE_PROGRAMMED",
                    note="PREFLIGHT",
                    conn=conn,
                )
                remote_done = orders.record_remote_movement(
                    remote_asset_id=asset["id"],
                    service_order_id=remote_order["id"],
                    movement_type="RETURNED_TO_RESIDENT",
                    to_state="RETURNED_TO_RESIDENT",
                    actor_id=None,
                    apartment_id=apartment_id,
                    apartment_number=apartment_number,
                    confirm_step_code="RESIDENT_REMOTE_RETURNED",
                    note="PREFLIGHT",
                    conn=conn,
                )["order"]

                phone_offer = catalog.add_or_update_service_offer(
                    service_code="PREFLIGHT_PHONE_CONNECT",
                    service_name="PREFLIGHT — подключение телефонного доступа",
                    service_group="ACCESS_CONTROL",
                    service_type="ONE_TIME",
                    category="ACCESS",
                    service_item_code="PREFLIGHT_PHONE_CONNECT",
                    service_item_name="PREFLIGHT — подключение телефонного доступа",
                    workflow_profile_code="PHONE_ACCESS_CONNECT",
                    amount=1,
                    effective_from="2026-07-01",
                    resident_request_enabled=True,
                    actor_id=None,
                    description="Техническая проверка. Не рабочая услуга.",
                    conn=conn,
                )
                phone_order = orders.create_service_order(
                    resident_account_id=resident_account_id,
                    telegram_user_id=None,
                    apartment_id=apartment_id,
                    apartment_number=apartment_number,
                    service_item_code=phone_offer["service_item_code"],
                    quantity=1,
                    resident_comment="PREFLIGHT",
                    actor_id=None,
                    source_context="service_orders_preflight",
                    conn=conn,
                )
                orders.confirm_order_step(
                    order_id=phone_order["id"],
                    step_code="PAYMENT_CONFIRMED",
                    actor_id=None,
                    note="PREFLIGHT payment only",
                    conn=conn,
                )
                phone_done = orders.activate_access_credential(
                    order_id=phone_order["id"],
                    credential_value="+380000000000",
                    actor_id=None,
                    apartment_id=apartment_id,
                    apartment_number=apartment_number,
                    external_reference="PREFLIGHT",
                    note="Техническая проверка",
                    conn=conn,
                )["order"]

                catalog.retire_service_offer(
                    service_item_code="PREFLIGHT_REMOTE_REPROGRAM",
                    actor_id=None,
                    reason="PREFLIGHT archive check",
                    conn=conn,
                )
                active_codes = {
                    row["service_item_code"]
                    for row in catalog.list_service_offers(
                        include_retired=False,
                        conn=conn,
                    )
                }
                conn.commit()
            finally:
                conn.close()

            checks = {
                "remote order completed": remote_done["order_status"] == "COMPLETED",
                "remote payment confirmed": remote_done["payment_status"] == "CONFIRMED",
                "phone order completed": phone_done["order_status"] == "COMPLETED",
                "phone payment confirmed": phone_done["payment_status"] == "CONFIRMED",
                "retired offer hidden from active list": (
                    "PREFLIGHT_REMOTE_REPROGRAM" not in active_codes
                ),
                "phone offer remains active": "PREFLIGHT_PHONE_CONNECT" in active_codes,
            }
            failed = [name for name, passed in checks.items() if not passed]
            for name, passed in checks.items():
                lines.append(f"   {'OK' if passed else 'ERR'}  {name}")
            if failed:
                errors.append("Не прошли проверки: " + ", ".join(failed))
        except Exception:
            errors.append("Не прошёл сквозной тест заказов услуг")
            lines.append(traceback.format_exc())

    lines.append("")
    lines.append("=" * 100)
    if errors:
        lines.append("RESULT: NOT READY — ACTIVE DB AND BOT WERE NOT MODIFIED")
        lines.extend("  - " + item for item in errors)
        code = 1
    else:
        lines.append("RESULT: READY FOR SERVICE-ORDERS SANDBOX TEST")
        lines.append(
            "Новая копия содержит только технические PREFLIGHT-заказы и "
            "не затрагивает реальных пользователей или исходную sandbox."
        )
        code = 0
    lines.append("=" * 100)

    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print("Report:", report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
