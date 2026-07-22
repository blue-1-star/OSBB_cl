"""
Безопасная проверка отдельного кабинета охранника.

Исходную БД не меняет:
1. Берёт уже подготовленную sandbox-копию v2.
2. Делает её новую независимую копию.
3. Применяет access/guard migration ТОЛЬКО к новой копии.
4. Проверяет права GUARD_O на искусственном Telegram ID в этой копии.
5. Проверяет импорты, таблицы и сборку parking_bot.py с v2 + guard patch
   только в памяти.

Никакие реальные сотрудники и никакие рабочие базы не получают доступ
автоматически.
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
HANDLERS = BOTS / "handlers"
SANDBOX_DIR = ROOT / "Data" / "db" / "sandbox"


def stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Не удалось подготовить модуль: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def copy_snapshot(source: Path, target: Path) -> None:
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def clone_paths(original, sandbox_db: Path):
    try:
        cloned = copy.copy(original)
        if cloned is original:
            raise RuntimeError
    except Exception:
        values = {}
        for name in dir(original):
            if name.startswith("_"):
                continue
            value = getattr(original, name)
            if not callable(value):
                values[name] = value
        cloned = SimpleNamespace(**values)
    try:
        setattr(cloned, "OSBB_TEST_DB_FILE", sandbox_db)
    except Exception:
        object.__setattr__(cloned, "OSBB_TEST_DB_FILE", sandbox_db)
    return cloned


def validate_tables(conn: sqlite3.Connection) -> list[str]:
    cur = conn.cursor()
    needed = {
        "cashier_receipts",
        "cashbox_operations",
        "payment_notices",
        "remote_requests",
        "remote_handover_events",
        "staff_principals",
        "access_roles",
        "access_role_permissions",
        "access_user_roles",
        "access_user_permissions",
        "access_audit_log",
    }
    return sorted(name for name in needed if not table_exists(cur, name))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sandbox",
        required=True,
        help="Уже существующая .db внутри Data\\db\\sandbox",
    )
    args = parser.parse_args()

    source = Path(args.sandbox).resolve()
    try:
        source.relative_to(SANDBOX_DIR.resolve())
    except ValueError:
        raise SystemExit("Разрешены только базы внутри Data\\db\\sandbox.")
    if not source.exists():
        raise SystemExit(f"Не найдена sandbox-БД: {source}")

    target = SANDBOX_DIR / f"{source.stem}_guard_check_{stamp()}{source.suffix}"
    report = SANDBOX_DIR / f"guard_workspace_preflight_{stamp()}.txt"
    files = {
        "access core": ROOT / "access_control.py",
        "access migration": ROOT / "migrate_access_control_and_guard.py",
        "access manager": ROOT / "manage_staff_access.py",
        "guard handler": HANDLERS / "guard_workspace.py",
        "guard bot patcher": ROOT / "patch_parking_bot_guard_workspace_v3.py",
        "cashier switcher": ROOT / "switch_parking_bot_to_cashier_v2.py",
        "cashier core v2": ROOT / "cashier_v2_core.py",
        "client portal v2": HANDLERS / "client_portal_v2.py",
        "parking bot": BOTS / "parking_bot.py",
    }

    lines = [
        "=" * 100,
        "GUARD WORKSPACE PREFLIGHT V2 — SANDBOX ONLY",
        "=" * 100,
        f"Source sandbox (read-only): {source}",
        f"New guard sandbox: {target}",
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
        copy_snapshot(source, target)
        lines.append("   OK  clone created")
    except Exception:
        errors.append("Не удалось создать копию sandbox")
        lines.append(traceback.format_exc())

    if target.exists():
        lines.append("")
        lines.append("3. Роли и таблицы охраны только в новой копии")
        try:
            migration = load_module(files["access migration"], "_guard_migration_preflight")
            conn = sqlite3.connect(target)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                result = migration.ensure_access_schema(conn)
                conn.commit()
            finally:
                conn.close()
            lines.append("   OK  access/guard migration applied to clone")
            lines.append(f"       tables: {result['tables_created']}")
            lines.append(f"       seed: {result['seed']}")
        except Exception:
            errors.append("Миграция доступа не прошла на копии")
            lines.append(traceback.format_exc())

        lines.append("")
        lines.append("4. Проверка индивидуальных прав GUARD_O на тестовом ID")
        try:
            for folder in (ROOT, BOTS, ROOT.parent):
                if str(folder) not in sys.path:
                    sys.path.insert(0, str(folder))

            import config
            config.paths = clone_paths(config.paths, target)
            config.USE_TEST_DB = True
            os.environ["OSBB_SANDBOX_DB"] = str(target)
            os.environ["OSBB_SANDBOX_MODE"] = "1"

            # Make access_control import bind to the cloned DB, not the live DB.
            sys.modules.pop("access_control", None)
            access = load_module(files["access core"], "access_control")

            conn = sqlite3.connect(target)
            conn.row_factory = sqlite3.Row
            try:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO staff_principals (
                        telegram_user_id, display_name, is_active, created_at
                    )
                    VALUES ('999999991', 'PREFLIGHT GUARD', 1, ?)
                    ON CONFLICT(telegram_user_id) DO UPDATE SET is_active = 1
                """, (stamp(),))
                cur.execute("""
                    INSERT INTO access_user_roles (
                        telegram_user_id, role_code, scope_type, scope_value,
                        is_active, granted_by, note, created_at
                    )
                    VALUES ('999999991', 'GUARD_O', 'ALL', '*', 1,
                            'PREFLIGHT', 'technical test only', ?)
                    ON CONFLICT(telegram_user_id, role_code, scope_type, scope_value)
                    DO UPDATE SET is_active = 1
                """, (stamp(),))
                conn.commit()

                checks = {
                    "workspace enter O": access.has_permission(
                        "999999991", "guard_workspace", "ENTER",
                        scope_type="CASHBOX", scope_value="O", conn=conn,
                    ),
                    "notice confirm O": access.has_permission(
                        "999999991", "payment_notices", "CONFIRM",
                        scope_type="CASHBOX", scope_value="O", conn=conn,
                    ),
                    "manual cash O": access.has_permission(
                        "999999991", "cashier_receipts", "CREATE",
                        scope_type="CASHBOX", scope_value="O", conn=conn,
                    ),
                    "remote issue post O": access.has_permission(
                        "999999991", "remote_requests", "ISSUE",
                        scope_type="POST", scope_value="O", conn=conn,
                    ),
                    "bank forbidden": not access.has_permission(
                        "999999991", "bank_transactions", "CREATE",
                        scope_type="ALL", scope_value="*", conn=conn,
                    ),
                    "other cashbox forbidden": not access.has_permission(
                        "999999991", "cashier_receipts", "CREATE",
                        scope_type="CASHBOX", scope_value="K4", conn=conn,
                    ),
                }
            finally:
                conn.close()

            failed = [name for name, value in checks.items() if not value]
            if failed:
                errors.append("Не прошли проверки прав: " + ", ".join(failed))
                for name, value in checks.items():
                    lines.append(f"   {'OK' if value else 'ERR'}  {name}")
            else:
                for name in checks:
                    lines.append(f"   OK  {name}")
        except Exception:
            errors.append("Проверка прав GUARD_O не прошла")
            lines.append(traceback.format_exc())

        lines.append("")
        lines.append("5. Проверка обработчика и сборки бота в памяти")
        try:
            # Existing v2 switch is applied first; guard patch follows it.
            switch = load_module(files["cashier switcher"], "_cashier_switch_preflight")
            guard_patch = load_module(files["guard bot patcher"], "_guard_patch_preflight")
            source_text = files["parking bot"].read_text(encoding="utf-8")
            source_text, cashier_changes = switch.patch(source_text)
            source_text, guard_changes = guard_patch.patch(source_text)
            compile(source_text, "parking_bot_guard_sandbox_memory.py", "exec")

            sys.modules.pop("handlers.guard_workspace", None)
            load_module(files["guard handler"], "handlers.guard_workspace")
            lines.append("   OK  guard_workspace.py import")
            lines.append("   OK  parking_bot v2 + guard patch compiles in memory")
            lines.append("       cashier: " + "; ".join(cashier_changes))
            lines.append("       guard: " + "; ".join(guard_changes))
        except Exception:
            errors.append("Не прошёл импорт guard handler или сборка бота")
            lines.append(traceback.format_exc())

        lines.append("")
        lines.append("6. Проверка структуры новой копии")
        try:
            conn = sqlite3.connect(target)
            try:
                missing = validate_tables(conn)
            finally:
                conn.close()
            if missing:
                errors.append("В guard sandbox нет таблиц: " + ", ".join(missing))
                lines.append("   ERR " + ", ".join(missing))
            else:
                lines.append("   OK  все таблицы guard workspace существуют")
        except Exception:
            errors.append("Не удалось проверить таблицы guard sandbox")
            lines.append(traceback.format_exc())

    lines.append("")
    lines.append("=" * 100)
    if errors:
        lines.append("RESULT: NOT READY — ACTIVE DB AND BOT WERE NOT MODIFIED")
        lines.extend("  - " + item for item in errors)
        code = 1
    else:
        lines.append("RESULT: READY FOR GUARD SANDBOX TEST")
        lines.append("Новая guard sandbox создана; реальным пользователям доступ не выдан.")
        code = 0
    lines.append("=" * 100)

    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print("Report:", report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
