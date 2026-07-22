# -*- coding: utf-8 -*-
r"""
OSBB — read-only locator for the actual service-order workflow database.

Place this file directly in:
    G:\Programming\Py\OSBB\

What it does:
1) lists currently running Python processes related to OSBB;
2) scans every SQLite database below G:\Programming\Py\OSBB;
3) finds databases that really contain service_orders;
4) prints the latest service orders and all of their workflow steps;
5) reports whether payments.source_ref exists.

It changes nothing. It only creates a report in:
    Data\db\logs\
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "Data" / "db" / "logs"

REPORT: list[str] = []


def say(value: object = "") -> None:
    line = str(value)
    print(line)
    REPORT.append(line)


def separator(char: str = "=") -> None:
    say(char * 110)


def quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def sqlite_connect_readonly(path: Path) -> sqlite3.Connection:
    uri = "file:" + path.resolve().as_posix() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
    }


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({quote_identifier(table)})").fetchall()
    ]


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {quote_identifier(table)}").fetchone()[0])


def choose_columns(available: set[str], wanted: Iterable[str]) -> list[str]:
    selected = [column for column in wanted if column in available]
    return selected or sorted(available)[:15]


def select_rows(
    conn: sqlite3.Connection,
    table: str,
    wanted: Iterable[str],
    *,
    where_sql: str = "",
    where_params: tuple[Any, ...] = (),
    order_candidates: Iterable[str] = ("id",),
    limit: int = 30,
) -> list[dict[str, Any]]:
    available_list = table_columns(conn, table)
    available = set(available_list)
    selected = choose_columns(available, wanted)
    order_column = next((column for column in order_candidates if column in available), None)

    sql = (
        "SELECT "
        + ", ".join(quote_identifier(column) for column in selected)
        + f" FROM {quote_identifier(table)}"
    )
    if where_sql:
        sql += " WHERE " + where_sql
    if order_column:
        sql += f" ORDER BY {quote_identifier(order_column)} DESC"
    sql += " LIMIT ?"
    rows = conn.execute(sql, (*where_params, int(limit))).fetchall()
    return [dict(row) for row in rows]


def show_rows(title: str, rows: list[dict[str, Any]]) -> None:
    say(title)
    if not rows:
        say("  (no rows)")
        return
    for row in rows:
        say("  " + " | ".join(f"{key}={value!r}" for key, value in row.items()))


def scan_python_processes() -> None:
    separator()
    say("RUNNING PYTHON PROCESSES")
    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe' } | "
        "ForEach-Object { "
        "'PID=' + $_.ProcessId + ' | NAME=' + $_.Name + ' | CMD=' + $_.CommandLine "
        "}"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        lines = [
            row.strip()
            for row in (result.stdout or "").splitlines()
            if row.strip()
        ]
        osbb_lines = [
            row for row in lines
            if "OSBB" in row.upper()
            or "parking_bot" in row.casefold()
            or "run_bot" in row.casefold()
        ]
        if osbb_lines:
            for row in osbb_lines:
                say(row)
        elif lines:
            say("No Python command line explicitly contains OSBB / parking_bot / run_bot.")
            say("All currently visible Python processes:")
            for row in lines:
                say("  " + row)
        else:
            say("(No python.exe/pythonw.exe process found, or process command lines are unavailable.)")
        if result.returncode != 0:
            say("PowerShell diagnostic return code: " + str(result.returncode))
            if result.stderr:
                say(result.stderr.strip())
    except Exception as exc:  # process listing must not stop DB scan
        say(f"Could not list running Python processes: {type(exc).__name__}: {exc}")


def find_sqlite_files() -> list[Path]:
    suffixes = {".db", ".sqlite", ".sqlite3"}
    ignored_parts = {".git", "__pycache__", ".venv", "venv"}
    found: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in suffixes:
            continue
        if any(part.casefold() in ignored_parts for part in path.parts):
            continue
        found.append(path)
    return sorted(found, key=lambda item: item.stat().st_mtime, reverse=True)


def inspect_one_database(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": path,
        "ok": False,
        "error": "",
        "tables": set(),
        "service_orders_count": None,
        "remote_requests_count": None,
        "payments_count": None,
        "source_ref": None,
    }
    try:
        conn = sqlite_connect_readonly(path)
        try:
            tables = table_names(conn)
            result["tables"] = tables
            result["ok"] = True

            if "service_orders" in tables:
                result["service_orders_count"] = count_rows(conn, "service_orders")
            if "remote_requests" in tables:
                result["remote_requests_count"] = count_rows(conn, "remote_requests")
            if "payments" in tables:
                result["payments_count"] = count_rows(conn, "payments")
                result["source_ref"] = "source_ref" in set(table_columns(conn, "payments"))
        finally:
            conn.close()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def print_service_order_details(path: Path) -> None:
    conn = sqlite_connect_readonly(path)
    try:
        tables = table_names(conn)
        if "service_orders" not in tables:
            return

        separator("-")
        say("SERVICE-ORDER DATABASE")
        say(f"Path: {path}")
        say(f"File size: {path.stat().st_size:,} bytes")
        say(f"Modified: {datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')}")

        order_rows = select_rows(
            conn,
            "service_orders",
            (
                "id", "order_number", "apartment_id", "apartment_number",
                "service_item_code", "service_name_snapshot", "workflow_profile_code",
                "order_status", "payment_status", "fulfillment_status",
                "requested_at", "created_at", "updated_at",
            ),
            order_candidates=("id", "requested_at", "created_at"),
            limit=60,
        )
        show_rows("Latest service orders:", order_rows)

        order_ids = [
            int(row["id"])
            for row in order_rows
            if row.get("id") is not None
        ]

        if "service_order_steps" in tables:
            if order_ids:
                columns = set(table_columns(conn, "service_order_steps"))
                order_id_field = (
                    "service_order_id"
                    if "service_order_id" in columns
                    else None
                )
                if order_id_field:
                    placeholders = ",".join("?" for _ in order_ids)
                    step_rows = select_rows(
                        conn,
                        "service_order_steps",
                        (
                            "id", "service_order_id", "step_code", "step_name",
                            "sequence_no", "is_required", "step_status",
                            "confirmed_at", "confirmed_by", "updated_at",
                        ),
                        where_sql=f"{quote_identifier(order_id_field)} IN ({placeholders})",
                        where_params=tuple(order_ids),
                        order_candidates=("service_order_id", "sequence_no", "id"),
                        limit=500,
                    )
                else:
                    step_rows = []
            else:
                step_rows = []
            show_rows("Steps for listed service orders:", step_rows)
        else:
            say("service_order_steps table: MISSING")

        if "service_order_payment_links" in tables:
            link_rows = select_rows(
                conn,
                "service_order_payment_links",
                (
                    "id", "service_order_id", "payment_id", "amount",
                    "linked_at", "linked_by", "note", "created_at",
                ),
                order_candidates=("id", "linked_at", "created_at"),
                limit=200,
            )
            show_rows("Service-order payment links:", link_rows)
        else:
            say("service_order_payment_links table: MISSING")

        if "remote_order_details" in tables:
            detail_rows = select_rows(
                conn,
                "remote_order_details",
                (
                    "id", "service_order_id", "resident_asset_id", "issued_asset_id",
                    "created_at", "updated_at",
                ),
                order_candidates=("id", "service_order_id"),
                limit=200,
            )
            show_rows("Remote-order details:", detail_rows)

        if "payments" in tables:
            columns = set(table_columns(conn, "payments"))
            say(
                "payments.source_ref: "
                + ("PRESENT" if "source_ref" in columns else "MISSING")
            )
    finally:
        conn.close()


def scan_databases() -> None:
    separator()
    say("SQLITE DATABASE SCAN")
    say(f"Project root: {ROOT}")

    files = find_sqlite_files()
    say(f"SQLite files found: {len(files)}")
    if not files:
        return

    results = [inspect_one_database(path) for path in files]
    service_candidates = [
        item for item in results
        if item["ok"] and "service_orders" in item["tables"]
    ]

    say()
    say("Compact inventory:")
    for item in results:
        relative = item["path"].relative_to(ROOT)
        if not item["ok"]:
            say(f"  [UNREADABLE] {relative} | {item['error']}")
            continue
        say(
            f"  [OK] {relative} | "
            f"service_orders={item['service_orders_count']!r} | "
            f"remote_requests={item['remote_requests_count']!r} | "
            f"payments={item['payments_count']!r} | "
            f"payments.source_ref={item['source_ref']!r}"
        )

    separator()
    say(f"DATABASES WITH SERVICE-ORDER SCHEMA: {len(service_candidates)}")
    if not service_candidates:
        say(
            "No database below this project contains the service_orders table. "
            "The earlier reprogramming workflow was therefore run from another "
            "project copy, another drive/folder, or an already-running old process."
        )
        return

    with_rows = [
        item for item in service_candidates
        if int(item["service_orders_count"] or 0) > 0
    ]
    say(f"DATABASES WITH ACTUAL SERVICE-ORDER ROWS: {len(with_rows)}")
    if not with_rows:
        say(
            "The service-order schema exists, but no recorded service order was found "
            "in any scanned database."
        )

    for item in service_candidates:
        print_service_order_details(item["path"])


def inspect_current_sources() -> None:
    separator()
    say("CURRENT RELEVANT SOURCE FILES")
    paths = (
        ROOT / "Bots" / "parking_bot.py",
        ROOT / "Bots" / "handlers" / "service_orders_workspace.py",
        ROOT / "run_bot_guard_sandbox_v3.py",
        ROOT / "patch_parking_bot_guard_workspace_v4.py",
        ROOT / "switch_parking_bot_to_cashier_v2.py",
    )
    for path in paths:
        if path.exists():
            stat = path.stat()
            say(
                f"[OK] {path.relative_to(ROOT)} | "
                f"{stat.st_size:,} bytes | "
                f"modified {datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')}"
            )
        else:
            say(f"[MISSING] {path.relative_to(ROOT)}")


def save_report() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = LOG_DIR / f"actual_service_order_state_{stamp}.txt"
    output.write_text("\n".join(REPORT) + "\n", encoding="utf-8")
    return output


def main() -> int:
    say("OSBB — Actual Service-Order State Locator")
    separator()
    say("Read-only. No database, source code, bot configuration or process is changed.")
    say(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    scan_python_processes()
    inspect_current_sources()
    scan_databases()
    separator()
    say("Completed.")
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as exc:  # noqa: BLE001
        say()
        separator("!")
        say(f"FAILED: {type(exc).__name__}: {exc}")
        say(traceback.format_exc())
        exit_code = 1

    report_path = save_report()
    print()
    print("Report saved to:", report_path)
    raise SystemExit(exit_code)
