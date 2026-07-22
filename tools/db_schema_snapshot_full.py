# db_schema_snapshot_full.py
# OSBB database schema snapshot - READ ONLY
#
# Назначение:
#   Полный снимок структуры SQLite БД OSBB без выгрузки персональных данных.
#   Выводит ВСЕ таблицы, views, triggers, indexes, PRAGMA table_info,
#   foreign keys, row counts и словарь использования колонок.
#
# Запуск из корня проекта OSBB:
#   cd /d G:\Programming\Py\OSBB
#   python tools\db_schema_snapshot_full.py --which test
#   python tools\db_schema_snapshot_full.py --which prod
#   python tools\db_schema_snapshot_full.py --which test --json
#
# Ничего не изменяет в базе.

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


KEY_TABLES = [
    "apartments",
    "vehicles",
    "persons",
    "resident_accounts",
    "contact_methods",
    "charges",
    "payments",
    "payment_allocations",
    "cashboxes",
    "cashbox_operations",
    "cashier_batches",
    "cashier_batch_items",
    "cashier_receipts",
    "cashier_reconciliation_cases",
    "service_catalog",
    "service_items",
    "service_tariffs",
    "operator_audit_log",
    "parking_time_review_tasks",
]


def find_py_root() -> Path:
    """Find G:/Programming/Py-like root by walking up until config.py exists."""
    here = Path(__file__).resolve()
    for parent in [here.parent] + list(here.parents):
        if (parent / "config.py").exists():
            return parent
    # Common case: script is in OSBB/tools, config.py is one level above OSBB.
    candidate = here.parents[2] if len(here.parents) > 2 else here.parent
    return candidate


def load_config_paths() -> Tuple[Any, bool, Path]:
    py_root = find_py_root()
    if str(py_root) not in sys.path:
        sys.path.insert(0, str(py_root))
    try:
        from config import paths, USE_TEST_DB  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            f"Не удалось импортировать config.py из {py_root}. "
            f"Запускайте скрипт из проекта OSBB или положите его в OSBB/tools. Ошибка: {exc}"
        )
    return paths, bool(USE_TEST_DB), py_root


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"DB file not found: {db_path}")
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def fetch_master(conn: sqlite3.Connection, type_name: str) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE type = ?
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """,
        (type_name,),
    )
    return [dict(row) for row in cur.fetchall()]


def table_row_count(conn: sqlite3.Connection, table: str) -> Any:
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) FROM {qident(table)}")
        return cur.fetchone()[0]
    except Exception as exc:
        return f"ERROR: {exc}"


def table_info(conn: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({qident(table)})")
    return [dict(row) for row in cur.fetchall()]


def index_list(conn: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA index_list({qident(table)})")
    indexes = []
    for row in cur.fetchall():
        item = dict(row)
        idx_name = item.get("name")
        try:
            cur.execute(f"PRAGMA index_info({qident(idx_name)})")
            item["columns"] = [r[2] for r in cur.fetchall()]
        except Exception as exc:
            item["columns_error"] = str(exc)
        indexes.append(item)
    return indexes


def foreign_keys(conn: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA foreign_key_list({qident(table)})")
    return [dict(row) for row in cur.fetchall()]


def get_create_sql(conn: sqlite3.Connection, type_name: str, name: str) -> str:
    cur = conn.cursor()
    cur.execute(
        "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?",
        (type_name, name),
    )
    row = cur.fetchone()
    return (row[0] if row and row[0] else "")


def collect_snapshot(conn: sqlite3.Connection, db_path: Path, which: str) -> Dict[str, Any]:
    cur = conn.cursor()
    cur.execute("SELECT sqlite_version()")
    sqlite_version = cur.fetchone()[0]

    tables = fetch_master(conn, "table")
    views = fetch_master(conn, "view")
    triggers = fetch_master(conn, "trigger")
    standalone_indexes = fetch_master(conn, "index")

    table_details: Dict[str, Any] = {}
    column_usage: Dict[str, List[str]] = defaultdict(list)

    for table in tables:
        name = table["name"]
        cols = table_info(conn, name)
        for col in cols:
            column_usage[col["name"]].append(name)
        table_details[name] = {
            "name": name,
            "row_count": table_row_count(conn, name),
            "create_sql": table.get("sql") or "",
            "columns": cols,
            "indexes": index_list(conn, name),
            "foreign_keys": foreign_keys(conn, name),
        }

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "which": which,
        "db_path": str(db_path),
        "sqlite_version": sqlite_version,
        "summary": {
            "tables": len(tables),
            "views": len(views),
            "triggers": len(triggers),
            "standalone_indexes": len(standalone_indexes),
        },
        "tables": table_details,
        "views": {v["name"]: v.get("sql") or "" for v in views},
        "triggers": {t["name"]: t.get("sql") or "" for t in triggers},
        "standalone_indexes": {i["name"]: i.get("sql") or "" for i in standalone_indexes},
        "column_usage": {k: sorted(v) for k, v in sorted(column_usage.items())},
        "key_tables_present": [t for t in KEY_TABLES if t in table_details],
        "key_tables_missing": [t for t in KEY_TABLES if t not in table_details],
    }


def fmt_columns(cols: List[Dict[str, Any]]) -> List[str]:
    lines = []
    lines.append("cid | name | type | notnull | default | pk")
    lines.append("-" * 100)
    for c in cols:
        lines.append(
            f"{c.get('cid')} | {c.get('name')} | {c.get('type') or ''} | "
            f"{c.get('notnull')} | {c.get('dflt_value') if c.get('dflt_value') is not None else ''} | {c.get('pk')}"
        )
    return lines


def fmt_indexes(indexes: List[Dict[str, Any]]) -> List[str]:
    if not indexes:
        return ["(none)"]
    lines = []
    for idx in indexes:
        cols = ", ".join(idx.get("columns", []))
        lines.append(
            f"- {idx.get('name')} | unique={idx.get('unique')} | origin={idx.get('origin')} | "
            f"partial={idx.get('partial')} | columns=[{cols}]"
        )
    return lines


def fmt_foreign_keys(fks: List[Dict[str, Any]]) -> List[str]:
    if not fks:
        return ["(none)"]
    lines = []
    for fk in fks:
        lines.append(
            f"- id={fk.get('id')} seq={fk.get('seq')} table={fk.get('table')} "
            f"from={fk.get('from')} to={fk.get('to')} "
            f"on_update={fk.get('on_update')} on_delete={fk.get('on_delete')} match={fk.get('match')}"
        )
    return lines


def render_text(snapshot: Dict[str, Any]) -> str:
    lines: List[str] = []
    sep = "=" * 120
    sub = "-" * 120

    lines.extend([
        sep,
        "OSBB DATABASE SCHEMA SNAPSHOT - FULL / READ ONLY",
        sep,
        f"Generated : {snapshot['generated_at']}",
        f"Mode      : {snapshot['which']}",
        f"DB        : {snapshot['db_path']}",
        f"SQLite    : {snapshot['sqlite_version']}",
        "",
        "SUMMARY",
        sub,
    ])
    for key, val in snapshot["summary"].items():
        lines.append(f"{key}: {val}")

    lines.extend([
        "",
        "KEY TABLES CHECK (not a filter; full report below includes ALL tables)",
        sub,
        "Present: " + (", ".join(snapshot["key_tables_present"]) or "(none)"),
        "Missing: " + (", ".join(snapshot["key_tables_missing"]) or "(none)"),
    ])

    lines.extend(["", "TABLE LIST", sub])
    for name in sorted(snapshot["tables"].keys()):
        row_count = snapshot["tables"][name]["row_count"]
        lines.append(f"- {name} ({row_count} rows)")

    for name in sorted(snapshot["tables"].keys()):
        t = snapshot["tables"][name]
        lines.extend(["", sep, f"TABLE {name}", sep, f"Rows: {t['row_count']}", ""])
        lines.extend(["CREATE TABLE", sub, t.get("create_sql") or "(no SQL)", ""])
        lines.extend(["COLUMNS", sub])
        lines.extend(fmt_columns(t["columns"]))
        lines.extend(["", "INDEXES", sub])
        lines.extend(fmt_indexes(t["indexes"]))
        lines.extend(["", "FOREIGN KEYS", sub])
        lines.extend(fmt_foreign_keys(t["foreign_keys"]))

    lines.extend(["", sep, "VIEWS", sep])
    if snapshot["views"]:
        for name, sql in snapshot["views"].items():
            lines.extend(["", f"VIEW {name}", sub, sql or "(no SQL)"])
    else:
        lines.append("(none)")

    lines.extend(["", sep, "TRIGGERS", sep])
    if snapshot["triggers"]:
        for name, sql in snapshot["triggers"].items():
            lines.extend(["", f"TRIGGER {name}", sub, sql or "(no SQL)"])
    else:
        lines.append("(none)")

    lines.extend(["", sep, "STANDALONE INDEXES", sep])
    if snapshot["standalone_indexes"]:
        for name, sql in snapshot["standalone_indexes"].items():
            lines.extend(["", f"INDEX {name}", sub, sql or "(auto/internal or no SQL)"])
    else:
        lines.append("(none)")

    lines.extend(["", sep, "COLUMN USAGE", sep])
    for col, tables in snapshot["column_usage"].items():
        lines.append(f"{col}: {', '.join(tables)}")

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="OSBB full SQLite schema snapshot - READ ONLY")
    parser.add_argument("--which", choices=["test", "prod"], default="test", help="Which DB from config.py to inspect")
    parser.add_argument("--json", action="store_true", help="Also write JSON report")
    parser.add_argument("--out-dir", default="", help="Optional output directory")
    args = parser.parse_args()

    paths, use_test_db, py_root = load_config_paths()
    db_path = Path(paths.OSBB_TEST_DB_FILE if args.which == "test" else paths.OSBB_DB_FILE)

    out_dir = Path(args.out_dir) if args.out_dir else Path(paths.OSBB_EXPORTS_DIR) / "schema"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    txt_path = out_dir / f"schema_snapshot_{args.which}_{stamp}.txt"
    json_path = out_dir / f"schema_snapshot_{args.which}_{stamp}.json"

    print("OSBB full schema snapshot - READ ONLY")
    print(f"Path source: config.py from {py_root}")
    print(f"USE_TEST_DB in config.py: {use_test_db}")
    print(f"Selected DB ({args.which}): {db_path}")
    print("")

    with connect_readonly(db_path) as conn:
        snapshot = collect_snapshot(conn, db_path, args.which)

    txt_path.write_text(render_text(snapshot), encoding="utf-8")
    if args.json:
        json_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"TXT report : {txt_path}")
    if args.json:
        print(f"JSON report: {json_path}")
    print("")
    print("Summary:")
    for key, val in snapshot["summary"].items():
        print(f" - {key}: {val}")
    print(f" - key tables present: {len(snapshot['key_tables_present'])}")
    print(f" - key tables missing: {len(snapshot['key_tables_missing'])}")
    if snapshot["key_tables_missing"]:
        print("Missing key tables:")
        for name in snapshot["key_tables_missing"]:
            print(f" - {name}")


if __name__ == "__main__":
    main()
