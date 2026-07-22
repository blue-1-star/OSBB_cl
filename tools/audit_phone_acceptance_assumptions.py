#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
audit_phone_acceptance_assumptions.py

OSBB phone acceptance assumptions audit - READ ONLY.

Purpose:
  Compare assumptions used by phone-access acceptance tools with the actual
  production/legacy code and DB schema.

Why:
  We found that acceptance tests checked service_interests, but the real
  create_service_interest() writes to service_order_interests. We also found
  that selected barriers are persisted in phone_access_request_points.

This tool does not modify anything.

It audits:
  1) DB schema presence for expected/actual phone tables.
  2) Source-code evidence:
       - create_service_interest() target INSERT table
       - create_phone_access_request_from_interest() target INSERT tables
  3) Current acceptance scripts under OSBB/tools and what table names they mention.
  4) Mismatches / recommendations.

PowerShell:

  python .\OSBB\tools\audit_phone_acceptance_assumptions.py --db "G:\Programming\Py\OSBB\Data\db\sandbox\osbb_test_live_services_2026-06-26_20-13-26.db"

Optional:
  python .\OSBB\tools\audit_phone_acceptance_assumptions.py --db "..." --out "G:\Programming\Py\OSBB\Data\exports\code\phone_acceptance_assumptions_audit.txt"
"""

from __future__ import annotations

import argparse
import ast
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"

ASSUMPTION_TABLES = [
    # older/newer generic interest assumptions
    "service_interests",
    "service_order_interests",
    # phone legacy request model
    "phone_access_requests",
    "phone_access_request_points",
    # newer/foundation phone-barrier model
    "phone_barrier_access_interests",
    "phone_barrier_access_interest_points",
    "phone_barrier_access_order_points",
    "phone_barrier_access_points",
    # operational links
    "service_orders",
    "service_access_credentials",
    "access_operation_journal",
]

ACCEPTANCE_SCRIPT_PATTERNS = [
    "test_phone_access",
    "inspect_phone_access",
    "trace_phone_interest",
    "audit_phone_acceptance",
]


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def table_columns(cur: sqlite3.Cursor, table: str) -> list[str]:
    if not table_exists(cur, table):
        return []
    cur.execute(f'PRAGMA table_info("{table}")')
    return [row["name"] for row in cur.fetchall()]


def count_rows(cur: sqlite3.Cursor, table: str) -> int:
    if not table_exists(cur, table):
        return 0
    return int(cur.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] or 0)


def find_function_source(path: Path, func_name: str) -> tuple[int, int, str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            body = "\n".join(lines[start - 1:end])
            return start, end, body
    raise RuntimeError(f"Function {func_name} not found in {path}")


def extract_insert_tables(source: str) -> list[str]:
    # Good enough for triple-quoted SQL in the codebase.
    matches = re.findall(r"\bINSERT\s+INTO\s+([A-Za-z0-9_]+)", source, flags=re.IGNORECASE)
    return sorted(set(matches))


def extract_select_tables(source: str) -> list[str]:
    matches = re.findall(r"\bFROM\s+([A-Za-z0-9_]+)", source, flags=re.IGNORECASE)
    return sorted(set(matches))


def find_tool_scripts() -> list[Path]:
    if not TOOLS_DIR.exists():
        return []
    result = []
    for p in TOOLS_DIR.glob("*.py"):
        low = p.name.lower()
        if any(pattern in low for pattern in ACCEPTANCE_SCRIPT_PATTERNS):
            result.append(p)
    return sorted(result)


def tables_mentioned_in_file(path: Path) -> set[str]:
    try:
        data = path.read_text(encoding="utf-8")
    except Exception:
        return set()
    return {table for table in ASSUMPTION_TABLES if table in data}


def section(out: list[str], title: str) -> None:
    out.append("")
    out.append("=" * 100)
    out.append(title)
    out.append("=" * 100)


def build_report(db_path: Path) -> tuple[str, int]:
    warnings = 0
    out: list[str] = []

    out.append("=" * 100)
    out.append("OSBB phone acceptance assumptions audit - READ ONLY")
    out.append("=" * 100)
    out.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    out.append(f"Root: {ROOT}")
    out.append(f"DB: {db_path}")

    conn = connect_ro(db_path)
    try:
        cur = conn.cursor()

        section(out, "1. DB TABLE REALITY")
        for table in ASSUMPTION_TABLES:
            if table_exists(cur, table):
                cols = table_columns(cur, table)
                out.append(f"OK   {table:<40} rows={count_rows(cur, table):<5} cols={len(cols)}")
            else:
                out.append(f"MISS {table}")

        section(out, "2. SOURCE-CODE WRITE TARGETS")
        source_checks = [
            ("service_preorders_core.py", "create_service_interest"),
            ("phone_barrier_access_core.py", "create_phone_access_request_from_interest"),
        ]

        actual_insert_targets: dict[str, list[str]] = {}

        for rel, func in source_checks:
            path = ROOT / rel
            out.append("")
            out.append(f"{rel} :: {func}()")
            if not path.exists():
                out.append("FAIL source file missing")
                warnings += 1
                continue
            try:
                start, end, body = find_function_source(path, func)
                inserts = extract_insert_tables(body)
                selects = extract_select_tables(body)
                actual_insert_targets[func] = inserts
                out.append(f"Lines: {start}-{end}")
                out.append("INSERT targets: " + (", ".join(inserts) if inserts else "-"))
                out.append("SELECT/FROM targets: " + (", ".join(selects) if selects else "-"))
            except Exception as exc:
                out.append(f"FAIL {exc}")
                warnings += 1

        section(out, "3. ACCEPTANCE / INSPECTION TOOL ASSUMPTIONS")
        scripts = find_tool_scripts()
        if not scripts:
            out.append("WARN no acceptance/inspection scripts found")
            warnings += 1
        else:
            for script_path in scripts:
                mentioned = sorted(tables_mentioned_in_file(script_path))
                out.append(f"{script_path.name}:")
                out.append("  mentions: " + (", ".join(mentioned) if mentioned else "-"))

        section(out, "4. MISMATCHES")
        mismatches: list[str] = []

        create_interest_targets = actual_insert_targets.get("create_service_interest", [])
        if "service_order_interests" in create_interest_targets:
            if any("service_interests" in tables_mentioned_in_file(p) for p in scripts):
                mismatches.append(
                    "Acceptance tools mention service_interests, but create_service_interest() writes to service_order_interests."
                )
        elif "service_interests" in create_interest_targets:
            mismatches.append(
                "create_service_interest() writes to service_interests. This differs from current legacy trace expectation."
            )
        else:
            mismatches.append(
                "Could not confirm generic interest INSERT target from create_service_interest()."
            )

        phone_targets = actual_insert_targets.get("create_phone_access_request_from_interest", [])
        if "phone_access_request_points" in phone_targets:
            if not any("phone_access_request_points" in tables_mentioned_in_file(p) for p in scripts):
                mismatches.append(
                    "Phone pipeline writes phone_access_request_points, but acceptance/inspection tools do not consistently check it."
                )
        else:
            mismatches.append(
                "Could not confirm INSERT into phone_access_request_points."
            )

        if table_exists(cur, "service_interests") and count_rows(cur, "service_interests") == 0 and table_exists(cur, "service_order_interests"):
            mismatches.append(
                "service_interests exists and is empty; service_order_interests exists and is likely the active generic interest table."
            )

        if table_exists(cur, "phone_barrier_access_interest_points") and count_rows(cur, "phone_barrier_access_interest_points") == 0:
            if table_exists(cur, "phone_access_request_points"):
                mismatches.append(
                    "phone_barrier_access_interest_points is empty, while legacy phone_access_request_points is the active selected-points table."
                )

        if mismatches:
            for m in mismatches:
                out.append("WARN " + m)
            warnings += len(mismatches)
        else:
            out.append("OK no mismatches found")

        section(out, "5. RECOMMENDED CURRENT ACCEPTANCE MODEL")
        out.append("For Production Sprint #1, acceptance tests should treat this as current reality:")
        out.append("")
        out.append("Generic resident intention:")
        out.append(" - service_order_interests")
        out.append("")
        out.append("Phone request:")
        out.append(" - phone_access_requests")
        out.append(" - phone_access_request_points")
        out.append("")
        out.append("Order / activation:")
        out.append(" - service_orders")
        out.append(" - service_access_credentials")
        out.append("")
        out.append("Future/unified tables may exist but should not be blockers unless the live flow uses them:")
        out.append(" - service_interests")
        out.append(" - phone_barrier_access_interests")
        out.append(" - phone_barrier_access_interest_points")
        out.append(" - phone_barrier_access_order_points")

        section(out, "6. NEXT PROGRAMMING ACTIONS")
        out.append("1. Patch test_phone_access_acceptance.py:")
        out.append("   - check service_order_interests instead of service_interests")
        out.append("   - check phone_access_request_points")
        out.append("   - downgrade unused future tables to WARN, not FAIL")
        out.append("")
        out.append("2. Patch test_phone_access_legacy_create_request.py:")
        out.append("   - count service_order_interests")
        out.append("   - count phone_access_request_points")
        out.append("   - verify returned phone_access_request.id by reopening the temp DB")
        out.append("")
        out.append("3. Keep the source DB untouched and continue write tests only on temporary DB copies.")

        out.append("")
        out.append(f"RESULT: {'WARN' if warnings else 'PASS'} ({warnings} warning(s))")
        out.append("READ ONLY COMPLETED")
        return "\n".join(out), warnings

    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Explicit SQLite DB path.")
    ap.add_argument("--out", default="", help="Optional output TXT path.")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    report, warnings = build_report(db_path)
    print(report)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print("")
        print("Output:", out_path)

    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
