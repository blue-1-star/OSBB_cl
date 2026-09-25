#!/usr/bin/env python3
"""Rename collector claim points I1/I2 to KAS1/KAS2, preserving references."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "Data" / "db" / "osbb_test.db"
BACKUPS = ROOT / "Data" / "db" / "backups"
RENAMES = (("I1", "KAS1", "Кассир 1"), ("I2", "KAS2", "Кассир 2"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.is_file():
        raise SystemExit(f"DB not found: {db}")
    with sqlite3.connect(db) as conn:
        old = [row[0] for row in conn.execute(
            "SELECT point_code FROM cash_claim_points WHERE point_code IN ('I1','I2')"
        )]
        new = [row[0] for row in conn.execute(
            "SELECT point_code FROM cash_claim_points WHERE point_code IN ('KAS1','KAS2')"
        )]
    print(f"DB: {db}; old: {old}; new: {new}")
    if not args.apply:
        print("DRY RUN ONLY")
        return 0
    if new and old:
        raise SystemExit("Mixed old/new collector points; inspect manually before migration.")
    if not old:
        print("Already migrated; no changes.")
        return 0
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / f"before_2026-09-24_025_rename_claim_collectors_{datetime.now():%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(db) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    with sqlite3.connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for old_code, new_code, name in RENAMES:
            conn.execute(
                "UPDATE cash_claim_points SET point_code=?, point_name=? WHERE point_code=?",
                (new_code, name, old_code),
            )
            conn.execute(
                "UPDATE cash_claim_custodians SET point_code=? WHERE point_code=?",
                (new_code, old_code),
            )
            conn.execute(
                "UPDATE service_interest_intake SET claimed_cashbox=? WHERE claimed_cashbox=?",
                (new_code, old_code),
            )
        conn.commit()
    print(f"APPLIED; backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
