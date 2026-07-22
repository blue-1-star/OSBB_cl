"""
Диагностика журнала аудита ОСББ.

Ничего не изменяет. Показывает:
- какие именно файлы config / audit_logger / handlers загрузил Python;
- какую БД видят редактор помещений и просмотрщик аудита;
- последние записи operator_audit_log;
- состояние помещения 4_A и связанные с ним записи аудита;
- краткую сводку по всем известным БД из config.

Запуск:
  g:/Programming/Py/venv/Scripts/python.exe G:/Programming/Py/OSBB/diagnose_osbb_audit.py
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import importlib
import inspect
import sqlite3
import sys
import traceback


ROOT = Path(__file__).resolve().parent
BOTS = ROOT / "Bots"
PY_ROOT = ROOT.parent

# Та же логика поиска модулей, что используется у бота.
for folder in (BOTS, ROOT, PY_ROOT):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


def line(char: str = "=", width: int = 108) -> str:
    return char * width


def exists_table(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def get_audit_summary(db: Path) -> dict:
    result = {
        "db": str(db),
        "exists": db.exists(),
        "mtime": None,
        "audit_rows": None,
        "max_audit_id": None,
        "latest_audit_time": None,
        "latest_rows": [],
        "unit_4a": None,
        "unit_4a_audit": [],
        "error": None,
    }

    if not db.exists():
        return result

    result["mtime"] = datetime.fromtimestamp(db.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        if exists_table(cur, "operator_audit_log"):
            cur.execute("""
                SELECT COUNT(*) AS cnt, MAX(id) AS max_id, MAX(created_at) AS max_time
                FROM operator_audit_log
            """)
            row = cur.fetchone()
            result["audit_rows"] = int(row["cnt"] or 0)
            result["max_audit_id"] = row["max_id"]
            result["latest_audit_time"] = row["max_time"]

            cur.execute("""
                SELECT
                    id, created_at, actor_type, operator_id,
                    action_type, table_name, row_id,
                    field_name, old_value, new_value, source_context
                FROM operator_audit_log
                ORDER BY id DESC
                LIMIT 10
            """)
            result["latest_rows"] = [dict(r) for r in cur.fetchall()]
        else:
            result["audit_rows"] = "operator_audit_log отсутствует"

        if exists_table(cur, "apartments"):
            columns = {
                r["name"]
                for r in cur.execute("PRAGMA table_info(apartments)").fetchall()
            }
            where_parts = []
            params = []
            if "unit_code" in columns:
                where_parts.append("unit_code = ?")
                params.append("4_A")
            if "apartment_number" in columns:
                where_parts.append("apartment_number = ?")
                params.append("4_A")

            if where_parts:
                selected = [
                    "id",
                    "apartment_number" if "apartment_number" in columns else "NULL AS apartment_number",
                    "unit_code" if "unit_code" in columns else "NULL AS unit_code",
                    "display_name" if "display_name" in columns else "NULL AS display_name",
                    "official_number" if "official_number" in columns else "NULL AS official_number",
                    "record_status" if "record_status" in columns else "NULL AS record_status",
                    "internal_note" if "internal_note" in columns else "NULL AS internal_note",
                ]
                cur.execute(
                    f"SELECT {', '.join(selected)} FROM apartments "
                    f"WHERE {' OR '.join(where_parts)} LIMIT 1",
                    tuple(params),
                )
                unit = cur.fetchone()
                if unit:
                    result["unit_4a"] = dict(unit)
                    if exists_table(cur, "operator_audit_log"):
                        cur.execute("""
                            SELECT
                                id, created_at, actor_type, operator_id,
                                action_type, table_name, row_id,
                                field_name, old_value, new_value,
                                source_context, comment
                            FROM operator_audit_log
                            WHERE table_name = 'apartments'
                              AND row_id = ?
                            ORDER BY id DESC
                            LIMIT 20
                        """, (str(unit["id"]),))
                        result["unit_4a_audit"] = [dict(r) for r in cur.fetchall()]

        conn.close()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result


def print_summary(title: str, data: dict) -> None:
    print(line())
    print(title)
    print(line())
    print("DB:", data["db"])
    print("Exists:", data["exists"])
    print("Modified:", data["mtime"] or "-")
    print("Audit rows:", data["audit_rows"])
    print("Max audit id:", data["max_audit_id"])
    print("Latest audit time:", data["latest_audit_time"] or "-")
    if data["error"]:
        print("ERROR:", data["error"])
        return

    print()
    print("4_A:")
    if data["unit_4a"]:
        for key, value in data["unit_4a"].items():
            print(f"  {key}: {value}")
    else:
        print("  Не найдено.")

    print()
    print("Аудит 4_A:")
    if data["unit_4a_audit"]:
        for row in data["unit_4a_audit"]:
            print(
                f"  #{row['id']} {row['created_at']} | "
                f"{row['actor_type']} {row['operator_id']} | "
                f"{row['action_type']} | {row['field_name']} | "
                f"{row['old_value']} -> {row['new_value']} | "
                f"{row['source_context']}"
            )
    else:
        print("  Записей для 4_A нет.")

    print()
    print("Последние 10 записей:")
    if data["latest_rows"]:
        for row in data["latest_rows"]:
            print(
                f"  #{row['id']} {row['created_at']} | "
                f"{row['actor_type']} {row['operator_id']} | "
                f"{row['action_type']} | {row['table_name']}#{row['row_id']} | "
                f"{row['field_name']}"
            )
    else:
        print("  Нет записей.")


def main() -> None:
    print(line())
    print("DIAGNOSE OSBB AUDIT")
    print(line())

    imported = {}
    errors = {}

    for module_name in (
        "config",
        "audit_logger",
        "handlers.unit_registry_editor",
        "handlers.audit_viewer",
    ):
        try:
            imported[module_name] = importlib.import_module(module_name)
        except Exception as exc:
            errors[module_name] = f"{type(exc).__name__}: {exc}"

    print("Загруженные модули:")
    for module_name, module in imported.items():
        print(f"  {module_name}: {getattr(module, '__file__', '?')}")
    for module_name, error in errors.items():
        print(f"  {module_name}: ERROR {error}")
    print()

    candidate_dbs: list[tuple[str, Path]] = []

    cfg = imported.get("config")
    if cfg and hasattr(cfg, "paths"):
        paths = cfg.paths
        for attr_name in ("OSBB_TEST_DB_FILE", "OSBB_DB_FILE"):
            value = getattr(paths, attr_name, None)
            if value:
                candidate_dbs.append((f"config.paths.{attr_name}", Path(value)))

    handler = imported.get("handlers.unit_registry_editor")
    if handler and hasattr(handler, "get_db_file"):
        try:
            candidate_dbs.append(("handlers.unit_registry_editor.get_db_file()", Path(handler.get_db_file())))
        except Exception as exc:
            print("handler.get_db_file() ERROR:", exc)

    viewer = imported.get("handlers.audit_viewer")
    if viewer and hasattr(viewer, "get_db_file"):
        try:
            candidate_dbs.append(("handlers.audit_viewer.get_db_file()", Path(viewer.get_db_file())))
        except Exception as exc:
            print("viewer.get_db_file() ERROR:", exc)

    logger = imported.get("audit_logger")
    if logger and hasattr(logger, "get_db_file"):
        try:
            candidate_dbs.append(("audit_logger.get_db_file()", Path(logger.get_db_file())))
        except Exception as exc:
            print("audit_logger.get_db_file() ERROR:", exc)

    # Уникальные БД, сохраняя все источники, чтобы увидеть возможное расхождение.
    seen: dict[Path, list[str]] = {}
    for source, db in candidate_dbs:
        seen.setdefault(db.resolve() if db.exists() else db, []).append(source)

    print("Источники БД:")
    for db, sources in seen.items():
        print(f"  {db}")
        for source in sources:
            print(f"    ← {source}")
    print()

    reports_dir = ROOT / "Data" / "exports" / "diagnostics"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report = reports_dir / f"audit_diagnostic_{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"

    report_lines = []

    for db, sources in seen.items():
        data = get_audit_summary(db)
        title = " | ".join(sources)
        print_summary(title, data)

        report_lines.extend([
            line(),
            title,
            line(),
            f"DB: {data['db']}",
            f"Exists: {data['exists']}",
            f"Modified: {data['mtime']}",
            f"Audit rows: {data['audit_rows']}",
            f"Max audit id: {data['max_audit_id']}",
            f"Latest audit time: {data['latest_audit_time']}",
            f"Error: {data['error'] or '-'}",
            "",
            "4_A:",
        ])
        if data["unit_4a"]:
            report_lines.extend(
                f"  {key}: {value}"
                for key, value in data["unit_4a"].items()
            )
        else:
            report_lines.append("  Не найдено.")

        report_lines.extend(["", "Аудит 4_A:"])
        if data["unit_4a_audit"]:
            for row in data["unit_4a_audit"]:
                report_lines.append(
                    f"  #{row['id']} {row['created_at']} | "
                    f"{row['actor_type']} {row['operator_id']} | "
                    f"{row['action_type']} | {row['field_name']} | "
                    f"{row['old_value']} -> {row['new_value']} | "
                    f"{row['source_context']}"
                )
        else:
            report_lines.append("  Записей для 4_A нет.")

        report_lines.extend(["", "Последние 10:"])
        if data["latest_rows"]:
            for row in data["latest_rows"]:
                report_lines.append(
                    f"  #{row['id']} {row['created_at']} | "
                    f"{row['actor_type']} {row['operator_id']} | "
                    f"{row['action_type']} | {row['table_name']}#{row['row_id']} | "
                    f"{row['field_name']}"
                )
        else:
            report_lines.append("  Нет записей.")

    report.write_text("\n".join(report_lines), encoding="utf-8")
    print()
    print("REPORT:", report)


if __name__ == "__main__":
    main()
