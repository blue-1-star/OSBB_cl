# -*- coding: utf-8 -*-
"""
Read-only compatibility check for the two-barrier phone-access sandbox.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SANDBOX_DB = (
    ROOT / "Data" / "db" / "sandbox" / "osbb_test_live_services_2026-06-26_20-13-26.db"
)


def columns(cur: sqlite3.Cursor, table: str) -> set[str]:
    cur.execute(f'PRAGMA table_info("{table}")')
    return {str(row[1]) for row in cur.fetchall()}


def main() -> int:
    print("OSBB service-code compatibility check")
    print("Read-only check.")
    print()

    if not SANDBOX_DB.is_file():
        raise FileNotFoundError(f"Sandbox database not found:\n{SANDBOX_DB}")

    core = ROOT / "service_orders_core.py"
    preorders = ROOT / "service_preorders_core.py"
    workspace = ROOT / "Bots" / "handlers" / "service_orders_workspace.py"
    for path in (core, preorders, workspace):
        if not path.is_file():
            raise FileNotFoundError(f"Expected source missing:\n{path}")

    required_source_markers = {
        core: [
            "base_service_code AS service_code",
            "def _payment_for_service_order_link(",
        ],
        preorders: ["base_service_code AS service_code"],
        workspace: [
            "base_service_code AS service_code",
            "def _current_offers()",
        ],
    }
    for path, markers in required_source_markers.items():
        source = path.read_text(encoding="utf-8")
        missing = [marker for marker in markers if marker not in source]
        if missing:
            raise RuntimeError(
                f"Compatibility code is absent in {path.name}: " + ", ".join(missing)
            )

    conn = sqlite3.connect(SANDBOX_DB)
    try:
        cur = conn.cursor()
        item_cols = columns(cur, "service_items")
        payment_cols = columns(cur, "payments")
        item_code_col = (
            "service_code" if "service_code" in item_cols
            else "base_service_code" if "base_service_code" in item_cols
            else None
        )
        payment_code_col = (
            "service_code" if "service_code" in payment_cols
            else "base_service_code" if "base_service_code" in payment_cols
            else None
        )

        if not item_code_col:
            raise RuntimeError("service_items has neither service_code nor base_service_code.")
        if not payment_code_col:
            raise RuntimeError("payments has neither service_code nor base_service_code.")

        print("Database:", SANDBOX_DB)
        print("service_items broad-code column:", item_code_col)
        print("payments broad-code column:", payment_code_col)
        print()

        rows = cur.execute(
            f"""
            SELECT service_item_code, {item_code_col}, service_item_name
            FROM service_items
            ORDER BY service_item_code
            LIMIT 10
            """
        ).fetchall()
        print("Catalog sample:")
        for row in rows:
            print(f" - {row[0]} | {row[1]} | {row[2]}")

        si = cur.execute(
            """
            SELECT interest_number, interest_status, amount_due_snapshot, payment_notice_id
            FROM service_order_interests
            WHERE interest_number = 'SI-20260627-000003'
            """
        ).fetchone()
        if si:
            print()
            print("Current two-barrier test intent:")
            print(
                f" - {si[0]} | {si[1]} | {si[2]} UAH "
                f"| payment_notice_id={si[3]}"
            )
        else:
            print()
            print("Current two-barrier test intent: not found (not an error).")

        print()
        print("CHECK PASSED")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
