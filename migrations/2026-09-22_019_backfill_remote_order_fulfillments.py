#!/usr/bin/env python3
"""Backfill generic fulfillment records for existing remote service orders."""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from order_fulfillment_core import PHYSICAL_ITEM, attach_fulfillment_item, ensure_fulfillment_schema, ensure_order_fulfillment


DEFAULT_DB = ROOT / "Data" / "db" / "osbb_test.db"
BACKUPS = ROOT / "Data" / "db" / "backups"
MIGRATION = "2026-09-22_019_backfill_remote_order_fulfillments"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")
    with sqlite3.connect(db) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM service_orders WHERE workflow_profile_code LIKE 'REMOTE_%'"
        ).fetchone()[0]
    print(f"DB: {db}")
    print(f"Remote orders eligible for backfill: {count}")
    if not args.apply:
        print("DRY RUN ONLY — no changes saved.")
        return 0

    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / f"before_{MIGRATION}_{datetime.now():%Y%m%d-%H%M%S}.db"
    shutil.copy2(db, backup)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        ensure_fulfillment_schema(conn)
        orders = conn.execute(
            "SELECT * FROM service_orders WHERE workflow_profile_code LIKE 'REMOTE_%' ORDER BY id"
        ).fetchall()
        created = 0
        attached = 0
        for order in orders:
            order = dict(order)
            assets = conn.execute(
                """
                SELECT ria.remote_asset_id, ra.asset_number
                FROM remote_order_issued_assets ria
                LEFT JOIN remote_assets ra ON ra.id=ria.remote_asset_id
                WHERE ria.service_order_id=? ORDER BY ria.id
                """,
                (int(order["id"]),),
            ).fetchall()
            if order.get("order_status") == "CANCELLED":
                status = "CANCELLED"
            elif assets or order.get("order_status") == "COMPLETED":
                status = "HANDED_OVER"
            else:
                status = "NOT_STARTED"
            before = conn.execute(
                "SELECT id FROM order_fulfillments WHERE service_order_id=?", (int(order["id"]),)
            ).fetchone()
            fulfillment = ensure_order_fulfillment(
                conn,
                service_order_id=int(order["id"]),
                fulfillment_kind=PHYSICAL_ITEM,
                initial_status=status,
                source_location_code="WAREHOUSE",
                actor_id="migration",
                note="Исторический пультовый заказ перенесён в общий контур исполнения.",
            )
            created += 0 if before else 1
            for asset in assets:
                attach_fulfillment_item(
                    conn,
                    fulfillment_id=int(fulfillment["id"]),
                    item_kind="REMOTE_ASSET",
                    external_asset_id=int(asset["remote_asset_id"]),
                    item_label=str(asset["asset_number"] or f"REMOTE-{asset['remote_asset_id']}"),
                    item_status=status,
                    actor_id="migration",
                    note="Связь с историческим экземпляром пульта.",
                )
                attached += 1
        conn.execute(
            """
            INSERT INTO audit_log(event_time, username, table_name, record_id, action, field_name,
                old_value, new_value, comment, actor_role, actor_name, source)
            VALUES (?, 'system', 'order_fulfillments', 'backfill', 'migration', '*', '', ?, ?,
                    'system', 'migration', ?)
            """,
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), MIGRATION,
             f"Перенесено исполнений: {created}; связей с пультами: {attached}.", MIGRATION),
        )
        conn.commit()
    print(f"APPLIED. Fulfillments created: {created}; items linked: {attached}. Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
