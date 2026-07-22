# -*- coding: utf-8 -*-
r"""
Retire only obsolete, unpaid TEST new-remote orders in the live-services sandbox.

It never deletes records. It marks the old stock-reservation test orders as
CANCELLED and releases only TEST-NEW-* assets that are still RESERVED solely
for those test orders. A backup is created first.
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "Data" / "db" / "sandbox" / "osbb_test_live_services_2026-06-26_20-13-26.db"
BACKUPS = ROOT / "Data" / "db" / "sandbox" / "backups"


def main() -> int:
    if not DB.is_file():
        raise FileNotFoundError(DB)
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup = BACKUPS / f"{DB.stem}_before_retire_legacy_new_remote_tests_{stamp}{DB.suffix}"
    shutil.copy2(DB, backup)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT o.id, o.order_number
            FROM service_orders o
            WHERE o.workflow_profile_code = 'REMOTE_NEW_FROM_STOCK'
              AND o.service_name_snapshot LIKE 'ТЕСТ%'
              AND o.order_status NOT IN ('COMPLETED', 'CANCELLED')
              AND NOT EXISTS (
                  SELECT 1 FROM service_order_payment_links l
                  WHERE l.service_order_id = o.id
              )
            ORDER BY o.id
            """
        ).fetchall()
        if not rows:
            print("No matching unpaid legacy TEST new-remote orders were found.")
            return 0
        ids = [int(row['id']) for row in rows]
        places = ','.join('?' for _ in ids)
        conn.execute(
            f"""
            UPDATE service_orders
            SET order_status = 'CANCELLED',
                fulfillment_status = 'CANCELLED',
                updated_at = ?
            WHERE id IN ({places})
            """,
            (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), *ids),
        )
        conn.execute(
            f"""
            UPDATE service_order_steps
            SET step_status = CASE
                  WHEN step_status = 'WAITING' THEN 'WAIVED'
                  ELSE step_status
                END,
                note = COALESCE(note, 'Старий sandbox-тест резерву скасовано.'),
                updated_at = ?
            WHERE service_order_id IN ({places})
            """,
            (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), *ids),
        )
        # Release TEST assets only when their current state is still RESERVED.
        released = conn.execute(
            f"""
            SELECT DISTINCT a.id
            FROM remote_assets a
            JOIN remote_asset_movements m ON m.remote_asset_id = a.id
            WHERE m.service_order_id IN ({places})
              AND a.inventory_status = 'RESERVED'
              AND a.asset_number LIKE 'TEST-NEW-%'
            """,
            tuple(ids),
        ).fetchall()
        for row in released:
            asset_id = int(row['id'])
            conn.execute(
                "UPDATE remote_assets SET inventory_status='AVAILABLE', updated_at=? WHERE id=?",
                (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), asset_id),
            )
            conn.execute(
                """
                INSERT INTO remote_asset_movements (
                    remote_asset_id, movement_type, from_state, to_state,
                    post_code, actor_id, note, created_at
                ) VALUES (?, 'LEGACY_TEST_RESERVATION_RELEASED', 'RESERVED', 'AVAILABLE', 'O', 'system', ?, ?)
                """,
                (asset_id, 'Simplified paid-preorder workflow replaced the old test reservation.', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            )
        conn.commit()
        print('Backup:', backup)
        print('Retired legacy test orders:', ', '.join(row['order_number'] for row in rows))
        print('Released TEST assets:', len(released))
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('RETIRE FAILED:', type(exc).__name__ + ':', exc)
        raise SystemExit(1)
