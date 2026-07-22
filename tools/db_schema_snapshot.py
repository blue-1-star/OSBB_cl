# db_schema_snapshot.py
# OSBB database schema snapshot - READ ONLY
# Put into: G:\\Programming\\Py\\OSBB\\tools\\db_schema_snapshot.py
# Run from OSBB root:
#   python tools\\db_schema_snapshot.py --which test
#   python tools\\db_schema_snapshot.py --which prod
# Optional:
#   python tools\\db_schema_snapshot.py --which test --json

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


def find_py_root() -> Path:
    """Find G:/Programming/Py-like root by walking upward until config.py is found."""
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "config.py"
        if candidate.exists():
            return parent
    # Expected when script is in OSBB/tools and config.py is one level above OSBB.
    for parent in [Path.cwd(), *Path.cwd().parents]:
        candidate = parent / "config.py"
        if candidate.exists():
            return parent
    raise RuntimeError("Не найден config.py. Запускайте из проекта или положите скрипт в OSBB/tools.")


PY_ROOT = find_py_root()
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from config import paths  # noqa: E402


IMPORTANT_TABLES = [
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


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def connect_readonly(db_file: Path) -> sqlite3.Connection:
    if not db_file.exists():
        raise RuntimeError(f"DB file not found: {db_file}")
    uri = db_file.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_objects(cur: sqlite3.Cursor, obj_type: str) -> List[Dict[str, Any]]:
    cur.execute(
        """
        SELECT name, type, sql
        FROM sqlite_master
        WHERE type = ?
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """,
        (obj_type,),
    )
    return [dict(row) for row in cur.fetchall()]


def table_columns(cur: sqlite3.Cursor, table: str) -> List[Dict[str, Any]]:
    cur.execute(f"PRAGMA table_info({safe_identifier(table)})")
    rows = []
    for row in cur.fetchall():
        rows.append(
            {
                "cid": row[0],
                "name": row[1],
                "type": row[2],
                "notnull": row[3],
                "default": row[4],
                "pk": row[5],
            }
        )
    return rows


def table_indexes(cur: sqlite3.Cursor, table: str) -> List[Dict[str, Any]]:
    cur.execute(f"PRAGMA index_list({safe_identifier(table)})")
    indexes = []
    for row in cur.fetchall():
        idx = {
            "seq": row[0],
            "name": row[1],
            "unique": row[2],
            "origin": row[3],
            "partial": row[4],
            "columns": [],
            "sql": None,
        }
        cur.execute(f"PRAGMA index_info({safe_identifier(idx['name'])})")
        idx["columns"] = [r[2] for r in cur.fetchall()]
        cur.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (idx["name"],))
        sql_row = cur.fetchone()
        idx["sql"] = sql_row[0] if sql_row else None
        indexes.append(idx)
    return indexes


def table_foreign_keys(cur: sqlite3.Cursor, table: str) -> List[Dict[str, Any]]:
    cur.execute(f"PRAGMA foreign_key_list({safe_identifier(table)})")
    keys = []
    for row in cur.fetchall():
        keys.append(
            {
                "id": row[0],
                "seq": row[1],
                "table": row[2],
                "from": row[3],
                "to": row[4],
                "on_update": row[5],
                "on_delete": row[6],
                "match": row[7],
            }
        )
    return keys


def row_count(cur: sqlite3.Cursor, table: str) -> int | str:
    try:
        cur.execute(f"SELECT COUNT(*) FROM {safe_identifier(table)}")
        return int(cur.fetchone()[0])
    except Exception as exc:
        return f"ERROR: {exc}"


def collect_schema(db_file: Path) -> Dict[str, Any]:
    conn = connect_readonly(db_file)
    cur = conn.cursor()

    cur.execute("SELECT sqlite_version()")
    sqlite_version = cur.fetchone()[0]

    tables = fetch_objects(cur, "table")
    views = fetch_objects(cur, "view")
    triggers = fetch_objects(cur, "trigger")

    table_details: Dict[str, Any] = {}
    column_usage: Dict[str, List[str]] = defaultdict(list)

    for table in tables:
        name = table["name"]
        cols = table_columns(cur, name)
        for col in cols:
            column_usage[col["name"]].append(name)
        table_details[name] = {
            "name": name,
            "sql": table.get("sql"),
            "row_count": row_count(cur, name),
            "columns": cols,
            "indexes": table_indexes(cur, name),
            "foreign_keys": table_foreign_keys(cur, name),
        }

    conn.close()
    return {
        "generated_at": now_text(),
        "db_file": str(db_file),
        "sqlite_version": sqlite_version,
        "tables": table_details,
        "views": views,
        "triggers": triggers,
        "column_usage": {k: sorted(v) for k, v in sorted(column_usage.items())},
    }


def fmt_bool(value: Any) -> str:
    return "yes" if value else "no"


def render_text(snapshot: Dict[str, Any], important_only: bool = False) -> str:
    tables: Dict[str, Any] = snapshot["tables"]
    table_names = list(tables.keys())
    if important_only:
        table_names = [name for name in IMPORTANT_TABLES if name in tables]

    lines: List[str] = []
    line = "=" * 120

    lines.extend(
        [
            line,
            "OSBB DATABASE SCHEMA SNAPSHOT - READ ONLY",
            line,
            f"Generated     : {snapshot['generated_at']}",
            f"Database      : {snapshot['db_file']}",
            f"SQLite version: {snapshot['sqlite_version']}",
            "",
            "SUMMARY",
            "-" * 120,
            f"Tables : {len(snapshot['tables'])}",
            f"Views  : {len(snapshot['views'])}",
            f"Triggers: {len(snapshot['triggers'])}",
            "",
            "TABLE LIST",
            "-" * 120,
        ]
    )

    for name in sorted(snapshot["tables"]):
        mark = "*" if name in IMPORTANT_TABLES else " "
        lines.append(f"{mark} {name}  rows={tables[name]['row_count']}")

    if snapshot["views"]:
        lines.extend(["", "VIEWS", "-" * 120])
        for view in snapshot["views"]:
            lines.append(view["name"])

    if snapshot["triggers"]:
        lines.extend(["", "TRIGGERS", "-" * 120])
        for trigger in snapshot["triggers"]:
            lines.append(trigger["name"])

    lines.extend(["", "DETAILED TABLES", line])

    for name in table_names:
        detail = tables[name]
        lines.extend(
            [
                "",
                f"TABLE {name}",
                "-" * 120,
                f"Rows: {detail['row_count']}",
                "",
                "CREATE TABLE",
                "~" * 120,
                detail["sql"] or "(no CREATE SQL)",
                "",
                "COLUMNS",
                "~" * 120,
                "cid | name | type | notnull | default | pk",
                "-" * 120,
            ]
        )
        for col in detail["columns"]:
            lines.append(
                f"{col['cid']:>3} | {col['name']} | {col['type'] or ''} | "
                f"{fmt_bool(col['notnull'])} | {col['default']!r} | {col['pk']}"
            )

        lines.extend(["", "INDEXES", "~" * 120])
        if detail["indexes"]:
            for idx in detail["indexes"]:
                lines.append(
                    f"{idx['name']} | unique={idx['unique']} | origin={idx['origin']} | "
                    f"partial={idx['partial']} | columns={', '.join(idx['columns'])}"
                )
                if idx.get("sql"):
                    lines.append(f"  SQL: {idx['sql']}")
        else:
            lines.append("(none)")

        lines.extend(["", "FOREIGN KEYS", "~" * 120])
        if detail["foreign_keys"]:
            for fk in detail["foreign_keys"]:
                lines.append(
                    f"id={fk['id']} seq={fk['seq']} | {fk['from']} -> "
                    f"{fk['table']}.{fk['to']} | on_update={fk['on_update']} | on_delete={fk['on_delete']}"
                )
        else:
            lines.append("(none)")

    lines.extend(["", "COLUMN USAGE", line])
    for col, used_in in snapshot["column_usage"].items():
        lines.append(f"{col}: {', '.join(used_in)}")

    return "\n".join(lines) + "\n"


def choose_db(which: str) -> Tuple[str, Path]:
    if which == "test":
        return "TEST", paths.OSBB_TEST_DB_FILE
    if which == "prod":
        return "PROD", paths.OSBB_DB_FILE
    raise RuntimeError("--which must be test or prod")


def main() -> None:
    parser = argparse.ArgumentParser(description="OSBB schema snapshot, read-only, no data export.")
    parser.add_argument("--which", choices=["test", "prod"], default="test")
    parser.add_argument("--json", action="store_true", help="Also write JSON snapshot.")
    parser.add_argument("--important-only", action="store_true", help="Text report: detail only important OSBB tables.")
    parser.add_argument("--out-dir", default="", help="Optional output directory.")
    args = parser.parse_args()

    label, db_file = choose_db(args.which)
    snapshot = collect_schema(db_file)

    out_dir = Path(args.out_dir) if args.out_dir else (paths.OSBB_EXPORTS_DIR / "schema")
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    txt_file = out_dir / f"schema_snapshot_{label.lower()}_{stamp}.txt"
    txt_file.write_text(render_text(snapshot, important_only=args.important_only), encoding="utf-8")

    json_file = None
    if args.json:
        json_file = out_dir / f"schema_snapshot_{label.lower()}_{stamp}.json"
        json_file.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    print("OSBB schema snapshot - READ ONLY")
    print(f"Mode    : {label}")
    print(f"Database: {db_file}")
    print(f"Report  : {txt_file}")
    if json_file:
        print(f"JSON    : {json_file}")
    print("")
    print("Summary:")
    print(f" - Tables  : {len(snapshot['tables'])}")
    print(f" - Views   : {len(snapshot['views'])}")
    print(f" - Triggers: {len(snapshot['triggers'])}")
    print("")
    missing_important = [name for name in IMPORTANT_TABLES if name not in snapshot["tables"]]
    if missing_important:
        print("Important tables missing:")
        for name in missing_important:
            print(f" - {name}")
    else:
        print("All important tables are present.")


if __name__ == "__main__":
    main()
