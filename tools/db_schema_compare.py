# db_schema_compare.py
# Read-only schema comparison tool for OSBB SQLite databases.
# Place in: G:\Programming\Py\OSBB\tools\db_schema_compare.py
# Run from OSBB project root or Py root.

from __future__ import annotations

import argparse
import difflib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


def find_py_root() -> Path:
    here = Path(__file__).resolve()
    candidates = [Path.cwd(), here.parent, here.parent.parent, here.parent.parent.parent]
    for c in candidates:
        if (c / "config.py").exists():
            return c
        if (c.parent / "config.py").exists():
            return c.parent
    return Path.cwd()


def load_default_paths() -> Tuple[Path, Path, str]:
    py_root = find_py_root()
    if str(py_root) not in sys.path:
        sys.path.insert(0, str(py_root))
    try:
        from config import paths  # type: ignore
        prod = Path(paths.OSBB_DB_FILE)
        test = Path(paths.OSBB_TEST_DB_FILE)
        return prod, test, f"config.py from {py_root}"
    except Exception as exc:
        fallback_root = py_root / "OSBB" / "Data" / "db"
        return fallback_root / "osbb.db", fallback_root / "osbb_test.db", f"fallback paths; config import failed: {exc}"


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_schema(db_path: Path) -> Dict[str, Any]:
    conn = connect_readonly(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys")
    foreign_keys_enabled = cur.fetchone()[0]
    cur.execute("SELECT sqlite_version()")
    sqlite_version = cur.fetchone()[0]

    cur.execute("""
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE type IN ('table', 'index', 'view', 'trigger')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY type, name
    """)
    objects = [dict(row) for row in cur.fetchall()]

    tables: Dict[str, Any] = {}
    for obj in objects:
        if obj["type"] != "table":
            continue
        table = obj["name"]
        cur.execute(f"PRAGMA table_info({quote_ident(table)})")
        columns = [dict(row) for row in cur.fetchall()]

        cur.execute(f"PRAGMA index_list({quote_ident(table)})")
        indexes = [dict(row) for row in cur.fetchall()]
        for idx in indexes:
            idx_name = idx.get("name")
            if idx_name:
                cur.execute(f"PRAGMA index_info({quote_ident(idx_name)})")
                idx["columns"] = [dict(r) for r in cur.fetchall()]

        cur.execute(f"PRAGMA foreign_key_list({quote_ident(table)})")
        foreign_keys = [dict(row) for row in cur.fetchall()]

        try:
            cur.execute(f"SELECT COUNT(*) FROM {quote_ident(table)}")
            row_count = cur.fetchone()[0]
        except Exception as exc:
            row_count = f"ERROR: {exc}"

        tables[table] = {
            "create_sql": obj.get("sql") or "",
            "columns": columns,
            "indexes": indexes,
            "foreign_keys": foreign_keys,
            "row_count": row_count,
        }

    views = {o["name"]: o.get("sql") or "" for o in objects if o["type"] == "view"}
    triggers = {o["name"]: o.get("sql") or "" for o in objects if o["type"] == "trigger"}
    indexes_global = {o["name"]: o.get("sql") or "" for o in objects if o["type"] == "index"}

    conn.close()
    return {
        "db_path": str(db_path),
        "sqlite_version": sqlite_version,
        "foreign_keys_enabled": foreign_keys_enabled,
        "tables": tables,
        "views": views,
        "triggers": triggers,
        "indexes_global": indexes_global,
    }


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def col_signature(col: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        col.get("name"),
        normalize_type(col.get("type")),
        int(col.get("notnull") or 0),
        normalize_default(col.get("dflt_value")),
        int(col.get("pk") or 0),
    )


def normalize_type(value: Any) -> str:
    return str(value or "").strip().upper()


def normalize_default(value: Any) -> str:
    return str(value or "").strip()


def compare_schemas(prod: Dict[str, Any], test: Dict[str, Any]) -> Dict[str, Any]:
    pt = prod["tables"]
    tt = test["tables"]
    prod_tables = set(pt)
    test_tables = set(tt)
    common_tables = sorted(prod_tables & test_tables)

    result: Dict[str, Any] = {
        "tables_only_in_prod": sorted(prod_tables - test_tables),
        "tables_only_in_test": sorted(test_tables - prod_tables),
        "table_differences": {},
        "views_only_in_prod": sorted(set(prod["views"]) - set(test["views"])),
        "views_only_in_test": sorted(set(test["views"]) - set(prod["views"])),
        "views_different": [],
        "triggers_only_in_prod": sorted(set(prod["triggers"]) - set(test["triggers"])),
        "triggers_only_in_test": sorted(set(test["triggers"]) - set(prod["triggers"])),
        "triggers_different": [],
    }

    for table in common_tables:
        pcols = pt[table]["columns"]
        tcols = tt[table]["columns"]
        pnames = [c["name"] for c in pcols]
        tnames = [c["name"] for c in tcols]
        pdict = {c["name"]: c for c in pcols}
        tdict = {c["name"]: c for c in tcols}

        diff: Dict[str, Any] = {}
        only_p = [c for c in pnames if c not in tdict]
        only_t = [c for c in tnames if c not in pdict]
        changed = []
        for name in sorted(set(pdict) & set(tdict)):
            if col_signature(pdict[name]) != col_signature(tdict[name]):
                changed.append({
                    "column": name,
                    "prod": col_signature(pdict[name]),
                    "test": col_signature(tdict[name]),
                })

        if only_p:
            diff["columns_only_in_prod"] = only_p
        if only_t:
            diff["columns_only_in_test"] = only_t
        if changed:
            diff["columns_changed"] = changed
        if pnames != tnames:
            diff["column_order_differs"] = {"prod": pnames, "test": tnames}

        psql = normalize_sql(pt[table]["create_sql"])
        tsql = normalize_sql(tt[table]["create_sql"])
        if psql != tsql:
            diff["create_sql_differs"] = True
            diff["create_sql_unified_diff"] = list(difflib.unified_diff(
                psql.splitlines(), tsql.splitlines(),
                fromfile=f"PROD.{table}", tofile=f"TEST.{table}", lineterm=""
            ))[:200]

        pidx = index_signature(pt[table]["indexes"])
        tidx = index_signature(tt[table]["indexes"])
        if pidx != tidx:
            diff["indexes_differ"] = {"prod": pidx, "test": tidx}

        pfk = fk_signature(pt[table]["foreign_keys"])
        tfk = fk_signature(tt[table]["foreign_keys"])
        if pfk != tfk:
            diff["foreign_keys_differ"] = {"prod": pfk, "test": tfk}

        if diff:
            result["table_differences"][table] = diff

    for name in sorted(set(prod["views"]) & set(test["views"])):
        if normalize_sql(prod["views"][name]) != normalize_sql(test["views"][name]):
            result["views_different"].append(name)
    for name in sorted(set(prod["triggers"]) & set(test["triggers"])):
        if normalize_sql(prod["triggers"][name]) != normalize_sql(test["triggers"][name]):
            result["triggers_different"].append(name)

    return result


def normalize_sql(sql: str) -> str:
    return "\n".join(line.rstrip() for line in str(sql or "").strip().splitlines())


def index_signature(indexes: List[Dict[str, Any]]) -> List[Any]:
    result = []
    for idx in indexes:
        name = idx.get("name")
        if str(name).startswith("sqlite_autoindex"):
            continue
        result.append({
            "name": name,
            "unique": idx.get("unique"),
            "origin": idx.get("origin"),
            "partial": idx.get("partial"),
            "columns": [c.get("name") for c in idx.get("columns", [])],
        })
    return sorted(result, key=lambda x: str(x.get("name")))


def fk_signature(fks: List[Dict[str, Any]]) -> List[Any]:
    return sorted([
        {
            "table": fk.get("table"),
            "from": fk.get("from"),
            "to": fk.get("to"),
            "on_update": fk.get("on_update"),
            "on_delete": fk.get("on_delete"),
            "match": fk.get("match"),
        }
        for fk in fks
    ], key=lambda x: (str(x.get("table")), str(x.get("from")), str(x.get("to"))))


def build_column_usage(schema: Dict[str, Any]) -> Dict[str, List[str]]:
    usage: Dict[str, List[str]] = {}
    for table, info in schema["tables"].items():
        for col in info["columns"]:
            usage.setdefault(col["name"], []).append(table)
    return {k: sorted(v) for k, v in sorted(usage.items())}


def write_report(prod: Dict[str, Any], test: Dict[str, Any], diff: Dict[str, Any], out: Path, include_json: bool) -> None:
    lines: List[str] = []
    add = lines.append
    add("=" * 100)
    add("OSBB DATABASE SCHEMA COMPARISON")
    add("=" * 100)
    add(f"Generated : {datetime.now():%Y-%m-%d %H:%M:%S}")
    add(f"PROD DB   : {prod['db_path']}")
    add(f"TEST DB   : {test['db_path']}")
    add(f"SQLite PROD: {prod['sqlite_version']}")
    add(f"SQLite TEST: {test['sqlite_version']}")
    add("")

    add("SUMMARY")
    add("-" * 100)
    add(f"PROD tables: {len(prod['tables'])}")
    add(f"TEST tables: {len(test['tables'])}")
    add(f"Tables only in PROD: {len(diff['tables_only_in_prod'])}")
    add(f"Tables only in TEST: {len(diff['tables_only_in_test'])}")
    add(f"Tables with structural differences: {len(diff['table_differences'])}")
    add(f"Views different: {len(diff['views_different'])}")
    add(f"Triggers different: {len(diff['triggers_different'])}")
    add("")

    def list_section(title: str, items: List[str]):
        add(title)
        add("-" * 100)
        if not items:
            add("(none)")
        else:
            for item in items:
                add(f"- {item}")
        add("")

    list_section("TABLES ONLY IN PROD", diff["tables_only_in_prod"])
    list_section("TABLES ONLY IN TEST", diff["tables_only_in_test"])
    list_section("VIEWS ONLY IN PROD", diff["views_only_in_prod"])
    list_section("VIEWS ONLY IN TEST", diff["views_only_in_test"])
    list_section("VIEWS DIFFERENT", diff["views_different"])
    list_section("TRIGGERS ONLY IN PROD", diff["triggers_only_in_prod"])
    list_section("TRIGGERS ONLY IN TEST", diff["triggers_only_in_test"])
    list_section("TRIGGERS DIFFERENT", diff["triggers_different"])

    add("TABLE STRUCTURAL DIFFERENCES")
    add("=" * 100)
    if not diff["table_differences"]:
        add("No structural table differences found.")
    else:
        for table, info in diff["table_differences"].items():
            add("")
            add(f"TABLE {table}")
            add("-" * 100)
            if "columns_only_in_prod" in info:
                add("Columns only in PROD: " + ", ".join(info["columns_only_in_prod"]))
            if "columns_only_in_test" in info:
                add("Columns only in TEST: " + ", ".join(info["columns_only_in_test"]))
            if "columns_changed" in info:
                add("Columns changed:")
                for ch in info["columns_changed"]:
                    add(f"  - {ch['column']}")
                    add(f"    PROD: {ch['prod']}")
                    add(f"    TEST: {ch['test']}")
            if info.get("column_order_differs"):
                add("Column order differs.")
            if info.get("indexes_differ"):
                add("Indexes differ:")
                add("  PROD: " + json.dumps(info["indexes_differ"]["prod"], ensure_ascii=False))
                add("  TEST: " + json.dumps(info["indexes_differ"]["test"], ensure_ascii=False))
            if info.get("foreign_keys_differ"):
                add("Foreign keys differ:")
                add("  PROD: " + json.dumps(info["foreign_keys_differ"]["prod"], ensure_ascii=False))
                add("  TEST: " + json.dumps(info["foreign_keys_differ"]["test"], ensure_ascii=False))
            if info.get("create_sql_differs"):
                add("CREATE SQL differs. Unified diff preview:")
                for line in info.get("create_sql_unified_diff", []):
                    add("  " + line)

    add("")
    add("COLUMN USAGE: PROD")
    add("=" * 100)
    for col, tables in build_column_usage(prod).items():
        add(f"{col}: {', '.join(tables)}")

    add("")
    add("COLUMN USAGE: TEST")
    add("=" * 100)
    for col, tables in build_column_usage(test).items():
        add(f"{col}: {', '.join(tables)}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")

    if include_json:
        json_out = out.with_suffix(".json")
        json_out.write_text(json.dumps({"prod": prod, "test": test, "diff": diff}, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    default_prod, default_test, source = load_default_paths()
    parser = argparse.ArgumentParser(description="Read-only OSBB schema comparison: production DB vs test DB.")
    parser.add_argument("--prod", default=str(default_prod), help="Production SQLite DB path")
    parser.add_argument("--test", default=str(default_test), help="Test SQLite DB path")
    parser.add_argument("--out", default="", help="Output report path; default OSBB/Data/exports/schema/schema_compare_TIMESTAMP.txt")
    parser.add_argument("--json", action="store_true", help="Also write machine-readable JSON next to TXT report")
    args = parser.parse_args()

    prod_path = Path(args.prod)
    test_path = Path(args.test)

    if args.out:
        out_path = Path(args.out)
    else:
        try:
            from config import paths  # type: ignore
            export_dir = Path(paths.OSBB_EXPORTS_DIR) / "schema"
        except Exception:
            export_dir = Path.cwd() / "schema_reports"
        out_path = export_dir / f"schema_compare_{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"

    print("OSBB schema comparison - READ ONLY")
    print("Path source:", source)
    print("PROD:", prod_path)
    print("TEST:", test_path)
    print("")

    prod = fetch_schema(prod_path)
    test = fetch_schema(test_path)
    diff = compare_schemas(prod, test)
    write_report(prod, test, diff, out_path, args.json)

    print("Report:", out_path)
    if args.json:
        print("JSON  :", out_path.with_suffix(".json"))
    print("")
    print("Summary:")
    print(" - Tables only in PROD:", len(diff["tables_only_in_prod"]))
    print(" - Tables only in TEST:", len(diff["tables_only_in_test"]))
    print(" - Tables with structural differences:", len(diff["table_differences"]))
    print(" - Views different:", len(diff["views_different"]))
    print(" - Triggers different:", len(diff["triggers_different"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
