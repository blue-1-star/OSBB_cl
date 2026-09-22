#!/usr/bin/env python3
"""Add CS, O and K as accountable stock points for physical goods."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inventory_transfer_core import ensure_inventory_schema

DEFAULT_DB = ROOT / "Data" / "db" / "osbb_test.db"
BACKUPS = ROOT / "Data" / "db" / "backups"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")
    print("DB:", db)
    print("Stock points: CS, O, K")
    if not args.apply:
        print("DRY RUN ONLY — no changes saved.")
        return 0
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / f"before_2026-09-22_022_inventory_transfer_points_{datetime.now():%Y%m%d-%H%M%S}.db"
    # SQLite's backup API includes uncheckpointed WAL data consistently.
    with sqlite3.connect(db) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    with sqlite3.connect(db) as conn:
        ensure_inventory_schema(conn)
        # Existing batches (if any) enter the new ledger with their unissued
        # balance only. Previously issued units are historical, not new stock.
        for batch_id, code, received, issued in conn.execute(
            "SELECT id,service_item_code,quantity_received,quantity_issued FROM remote_supplier_batches"
        ):
            if conn.execute(
                "SELECT 1 FROM inventory_lots WHERE source_kind='REMOTE_SUPPLIER_BATCH' AND source_id=?",
                (batch_id,),
            ).fetchone():
                continue
            if int(received or 0) < int(issued or 0):
                raise ValueError(f"Партия #{batch_id}: выдано больше, чем получено; миграция остановлена.")
            if int(received or 0) == 0:
                continue
            lot_id = conn.execute(
                """INSERT INTO inventory_lots(source_kind,source_id,service_item_code,
                   quantity_received,created_at,updated_at)
                   VALUES ('REMOTE_SUPPLIER_BATCH',?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                (batch_id, code, received),
            ).lastrowid
            available = int(received) - int(issued or 0)
            if available:
                conn.execute(
                    """INSERT INTO inventory_balances(lot_id,location_code,quantity,updated_at)
                       VALUES (?,'CS',?,CURRENT_TIMESTAMP)""", (lot_id, available),
                )
            conn.execute(
                """INSERT INTO inventory_events(lot_id,event_code,location_code,quantity_delta,
                   actor_id,note,created_at) VALUES (?,'MIGRATION_OPENING_BALANCE','CS',?,
                   'migration',?,CURRENT_TIMESTAMP)""",
                (lot_id, available, f"Получено {received}; ранее выдано {issued}."),
            )
        rules = [
            ("REMOTE_SERVICE_OPERATOR", "inventory_transfers", "SEND", "ALL", "*"),
            ("REMOTE_SERVICE_OPERATOR", "inventory_transfers", "VIEW", "ALL", "*"),
            ("REMOTE_SERVICE_OPERATOR", "inventory_transfers", "CONFIRM", "STOCK_LOCATION", "CS"),
            ("REMOTE_SERVICE_OPERATOR", "remote_assets", "MOVE", "POST", "CS"),
            ("REMOTE_SERVICE_OPERATOR", "remote_assets", "MOVE", "POST", "K"),
            ("GUARD_O", "inventory_transfers", "CONFIRM", "STOCK_LOCATION", "O"),
            ("GUARD_O", "inventory_transfers", "VIEW", "STOCK_LOCATION", "O"),
            ("CONCIERGE_K", "inventory_transfers", "CONFIRM", "STOCK_LOCATION", "K"),
            ("CONCIERGE_K", "inventory_transfers", "VIEW", "STOCK_LOCATION", "K"),
        ]
        for role, resource, action, scope_type, scope_value in rules:
            conn.execute(
                """INSERT INTO access_role_permissions(role_code,resource,action,scope_type,
                   scope_value,effect,is_active,note,created_at,updated_at)
                   SELECT ?,?,?,?,?,'ALLOW',1,'Пункты передачи физических товаров.',
                          CURRENT_TIMESTAMP,CURRENT_TIMESTAMP
                   WHERE NOT EXISTS(SELECT 1 FROM access_role_permissions WHERE role_code=?
                     AND resource=? AND action=? AND scope_type=? AND scope_value=?)""",
                (role, resource, action, scope_type, scope_value,
                 role, resource, action, scope_type, scope_value),
            )
        conn.execute(
            """INSERT INTO audit_log(event_time,username,table_name,record_id,action,field_name,
               old_value,new_value,comment,actor_role,actor_name,source)
               VALUES (CURRENT_TIMESTAMP,'system','inventory_locations','CS,O,K','migration','*',
                       '','CS,O,K','Добавлен учёт ответственных передач между пунктами.',
                       'system','migration','2026-09-22_022_inventory_transfer_points')"""
        )
        conn.commit()
    print("APPLIED. Backup:", backup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
