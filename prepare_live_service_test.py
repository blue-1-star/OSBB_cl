"""
Подготовка изолированной live sandbox к кликабельному тесту услуг.

Создаёт ТОЛЬКО в указанной sandbox:
- роли REMOTE_SERVICE_OPERATOR и ACCESS_SERVICE_OPERATOR для тестового Telegram ID;
- четыре явно тестовые услуги с тестовыми ценами;
- два тестовых новых и два тестовых восстановленных пульта на складе.

Ничего не меняет в osbb_test.db, osbb.db, parking_bot.py и прежних sandbox.

Цены 1/2/3/4 грн — технические, не утверждённые тарифы и не реальные цены.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import os
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent
BOTS = ROOT / "Bots"
SANDBOX_DIR = ROOT / "Data" / "db" / "sandbox"
DEFAULT_LAUNCHER = ROOT / "Start_OSBB_Live_Service_Sandbox_Bot.bat"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Не удалось загрузить: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def clone_paths(original: Any, sandbox_db: Path):
    try:
        cloned = copy.copy(original)
        if cloned is original:
            raise RuntimeError
    except Exception:
        values = {}
        for key in dir(original):
            if key.startswith("_"):
                continue
            value = getattr(original, key)
            if not callable(value):
                values[key] = value
        cloned = SimpleNamespace(**values)
    setattr(cloned, "OSBB_TEST_DB_FILE", sandbox_db)
    return cloned


def inside_sandbox(value: str | Path) -> Path:
    path = Path(value).resolve()
    try:
        path.relative_to(SANDBOX_DIR.resolve())
    except ValueError as exc:
        raise ValueError("Разрешены только БД внутри Data\\db\\sandbox.") from exc
    if not path.exists():
        raise FileNotFoundError(f"Не найдена sandbox-БД: {path}")
    return path


def sandbox_from_launcher() -> Path:
    if not DEFAULT_LAUNCHER.exists():
        raise FileNotFoundError(f"Не найден launcher: {DEFAULT_LAUNCHER}")
    raw = DEFAULT_LAUNCHER.read_text(encoding="utf-8-sig")
    match = re.search(r'^set\s+"SANDBOX=(.+?)"\s*$', raw, flags=re.MULTILINE | re.IGNORECASE)
    if not match:
        raise RuntimeError("В launcher не найдена строка set \"SANDBOX=...\".")
    return inside_sandbox(match.group(1))


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_principal_and_role(
    cur: sqlite3.Cursor,
    *,
    telegram_id: str,
    name: str,
    role_code: str,
) -> None:
    cur.execute("SELECT 1 FROM access_roles WHERE role_code = ?", (role_code,))
    if not cur.fetchone():
        raise RuntimeError(f"В sandbox нет роли: {role_code}")

    cur.execute(
        """
        INSERT INTO staff_principals (
            telegram_user_id, display_name, is_active, note, created_at, updated_at
        )
        VALUES (?, ?, 1, ?, ?, ?)
        ON CONFLICT(telegram_user_id) DO UPDATE SET
            display_name = COALESCE(excluded.display_name, staff_principals.display_name),
            is_active = 1,
            note = excluded.note,
            updated_at = excluded.updated_at
        """,
        (
            telegram_id,
            name or None,
            "Только live sandbox: тест услуг/пультов.",
            now_db(),
            now_db(),
        ),
    )
    cur.execute(
        """
        INSERT INTO access_user_roles (
            telegram_user_id, role_code, scope_type, scope_value,
            is_active, granted_by, note, created_at, updated_at
        )
        VALUES (?, ?, 'ALL', '*', 1, 'LIVE_SERVICE_SANDBOX',
                'Только live sandbox: тестовый оператор услуг.', ?, ?)
        ON CONFLICT(telegram_user_id, role_code, scope_type, scope_value)
        DO UPDATE SET
            is_active = 1,
            granted_by = excluded.granted_by,
            note = excluded.note,
            updated_at = excluded.updated_at
        """,
        (telegram_id, role_code, now_db(), now_db()),
    )


def asset_exists(cur: sqlite3.Cursor, asset_number: str) -> bool:
    cur.execute("SELECT 1 FROM remote_assets WHERE asset_number = ?", (asset_number,))
    return cur.fetchone() is not None


def main() -> int:
    parser = argparse.ArgumentParser()
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--sandbox")
    target.add_argument("--from-launcher", action="store_true")
    parser.add_argument("--user", required=True, help="Telegram ID тестового сотрудника.")
    parser.add_argument("--name", default="Тестовый оператор услуг")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not args.apply:
        print("Для записи в sandbox добавьте --apply. Без него изменения не выполняются.")
        return 2

    sandbox = inside_sandbox(args.sandbox) if args.sandbox else sandbox_from_launcher()
    files = {
        "service core": ROOT / "service_orders_core.py",
        "catalog core": ROOT / "service_catalog_admin_core.py",
        "workspace": BOTS / "handlers" / "service_orders_workspace.py",
    }
    missing = [f"{label}: {path}" for label, path in files.items() if not path.exists()]
    if missing:
        print("Не найдены файлы:")
        print("\n".join(missing))
        return 1

    if "# SAFE_PAYMENT_LINK_POLICY_V1" not in files["service core"].read_text(encoding="utf-8"):
        print("Не установлена безопасная политика оплаты. Сначала install_service_orders_ui.py --apply.")
        return 1

    for folder in (ROOT, BOTS, ROOT.parent):
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))

    import config
    config.paths = clone_paths(config.paths, sandbox)
    config.USE_TEST_DB = True
    os.environ["OSBB_SANDBOX_DB"] = str(sandbox)
    os.environ["OSBB_SANDBOX_MODE"] = "1"

    for name in [
        "service_orders_core",
        "service_catalog_admin_core",
        "access_control",
        "audit_logger",
    ]:
        sys.modules.pop(name, None)

    orders = load_module(files["service core"], "service_orders_core")
    catalog = load_module(files["catalog core"], "service_catalog_admin_core")
    access = load_module(ROOT / "access_control.py", "access_control")

    ready, reason = orders.schema_ready()
    if not ready:
        print("Sandbox не готова:", reason)
        return 1

    today = date.today().strftime("%Y-%m-%d")
    offers = [
        {
            "service_code": "TEST_REMOTE_REPROGRAM_OWN",
            "service_name": "ТЕСТ — перепрошивка собственного пульта",
            "service_item_code": "TEST_REMOTE_REPROGRAM_OWN",
            "service_item_name": "ТЕСТ — перепрошивка собственного пульта",
            "category": "REMOTE",
            "workflow": "REMOTE_REPROGRAM_OWN",
            "price": 1,
        },
        {
            "service_code": "TEST_REMOTE_NEW",
            "service_name": "ТЕСТ — новый пульт",
            "service_item_code": "TEST_REMOTE_NEW",
            "service_item_name": "ТЕСТ — новый пульт",
            "category": "REMOTE",
            "workflow": "REMOTE_NEW_FROM_STOCK",
            "price": 2,
        },
        {
            "service_code": "TEST_REMOTE_REFURBISHED",
            "service_name": "ТЕСТ — восстановленный пульт",
            "service_item_code": "TEST_REMOTE_REFURBISHED",
            "service_item_name": "ТЕСТ — восстановленный пульт",
            "category": "REMOTE",
            "workflow": "REMOTE_REFURBISHED_FROM_STOCK",
            "price": 3,
        },
        {
            "service_code": "TEST_PHONE_ACCESS_CONNECT",
            "service_name": "ТЕСТ — подключение телефонного доступа",
            "service_item_code": "TEST_PHONE_ACCESS_CONNECT",
            "service_item_name": "ТЕСТ — подключение телефонного доступа",
            "category": "ACCESS",
            "workflow": "PHONE_ACCESS_CONNECT",
            "price": 4,
        },
    ]
    stock = [
        ("TEST-NEW-001", "NEW"),
        ("TEST-NEW-002", "NEW"),
        ("TEST-REF-001", "REFURBISHED"),
        ("TEST-REF-002", "REFURBISHED"),
    ]

    conn = sqlite3.connect(sandbox)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        cur = conn.cursor()
        ensure_principal_and_role(
            cur, telegram_id=str(args.user), name=args.name,
            role_code="REMOTE_SERVICE_OPERATOR",
        )
        ensure_principal_and_role(
            cur, telegram_id=str(args.user), name=args.name,
            role_code="ACCESS_SERVICE_OPERATOR",
        )

        for item in offers:
            catalog.add_or_update_service_offer(
                service_code=item["service_code"],
                service_name=item["service_name"],
                service_group="ACCESS_CONTROL",
                service_type="ONE_TIME",
                category=item["category"],
                service_item_code=item["service_item_code"],
                service_item_name=item["service_item_name"],
                workflow_profile_code=item["workflow"],
                amount=item["price"],
                effective_from=today,
                resident_request_enabled=True,
                actor_id=None,
                description="Только live sandbox. Не утверждённый тариф.",
                conn=conn,
            )

        new_assets = []
        for asset_number, condition in stock:
            if asset_exists(cur, asset_number):
                continue
            asset = orders.create_remote_asset(
                asset_number=asset_number,
                ownership_type="OSBB_STOCK",
                inventory_status="AVAILABLE",
                condition_status=condition,
                hardware_model="TEST",
                serial_number=asset_number,
                actor_id=None,
                note="Только live sandbox: тестовый складской пульт.",
                conn=conn,
            )
            new_assets.append(asset["asset_number"])

        cur.execute(
            """
            INSERT INTO access_audit_log (
                created_at, actor_telegram_user_id, action_type,
                resource, action, scope_type, scope_value,
                target_table, target_id, success, details
            )
            VALUES (?, 'LIVE_SERVICE_SANDBOX', 'service_test_setup',
                    'service_orders', 'SETUP', 'ALL', '*',
                    'service_catalog', '', 1, ?)
            """,
            (
                now_db(),
                f"user={args.user}; offers={len(offers)}; new_assets={new_assets}",
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    remote_view = access.has_permission(
        str(args.user), "service_orders", "VIEW",
        scope_type="SERVICE_CATEGORY", scope_value="REMOTE",
    )
    access_view = access.has_permission(
        str(args.user), "service_orders", "VIEW",
        scope_type="SERVICE_CATEGORY", scope_value="ACCESS",
    )

    print("=" * 100)
    print("LIVE SERVICE SANDBOX PREPARED")
    print("=" * 100)
    print("Sandbox:", sandbox)
    print("Test operator:", args.user)
    print("REMOTE_SERVICE_OPERATOR view:", remote_view)
    print("ACCESS_SERVICE_OPERATOR view:", access_view)
    print("Test offers:")
    for item in offers:
        print(f"  {item['service_item_code']} | {item['price']} грн | {item['workflow']}")
    print("New test stock assets:", ", ".join(new_assets) if new_assets else "already present")
    print()
    print("APPLIED ONLY TO THIS SANDBOX")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
