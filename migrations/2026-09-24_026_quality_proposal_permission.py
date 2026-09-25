#!/usr/bin/env python3
"""Allow staff to suggest data fixes without granting registry write access."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "Data" / "db" / "osbb_test.db"
BACKUPS = ROOT / "Data" / "db" / "backups"
RESOURCE = "data_quality_proposals"
ROLES = ("GUARD_O", "CONCIERGE_K", "FINANCE_OPERATOR", "DATA_QUALITY_CONTRIBUTOR")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.db.resolve()
    if not db.is_file():
        raise SystemExit(f"DB not found: {db}")
    print("DB:", db)
    print("Grant SUGGEST to:", ", ".join(ROLES))
    if not args.apply:
        print("DRY RUN ONLY")
        return 0
    BACKUPS.mkdir(parents=True, exist_ok=True)
    backup = BACKUPS / f"before_2026-09-24_026_quality_proposal_permission_{datetime.now():%Y%m%d-%H%M%S}.db"
    with sqlite3.connect(db) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO access_roles(role_code,role_name,description,is_active,created_at,updated_at)
               VALUES ('DATA_QUALITY_CONTRIBUTOR','Помощник по качеству данных',
                       'Может предлагать исправления без записи в реестр',1,?,?)
               ON CONFLICT(role_code) DO UPDATE SET is_active=1,updated_at=excluded.updated_at""",
            (timestamp, timestamp),
        )
        conn.execute(
            """INSERT INTO access_permissions(permission_code,permission_name,category,
                   description,is_active,created_at,updated_at)
               VALUES ('data_quality_proposals.SUGGEST','Предложить исправление данных',
                       'DATA_QUALITY','Создание предложения без изменения реестра',1,?,?)
               ON CONFLICT(permission_code) DO UPDATE SET is_active=1,updated_at=excluded.updated_at""",
            (timestamp, timestamp),
        )
        for role in ROLES:
            conn.execute(
                """INSERT INTO access_role_permissions(role_code,resource,action,scope_type,
                       scope_value,effect,is_active,note,created_at,updated_at)
                   VALUES (?,?,'SUGGEST','ALL','*','ALLOW',1,
                           'Предложение не меняет реестр; решение SUPER_ADMIN.',?,?)
                   ON CONFLICT(role_code,resource,action,scope_type,scope_value)
                   DO UPDATE SET effect='ALLOW',is_active=1,updated_at=excluded.updated_at""",
                (role, RESOURCE, timestamp, timestamp),
            )
        conn.execute(
            """INSERT INTO audit_log(event_time,username,table_name,record_id,action,
                   field_name,old_value,new_value,comment,actor_role,actor_name,source)
               VALUES (?,'system','access_role_permissions','data_quality_proposals.SUGGEST',
                       'migration','role_grants','',?,'Предложения не изменяют реестр.',
                       'system','migration','2026-09-24_026_quality_proposal_permission')""",
            (timestamp, ",".join(ROLES)),
        )
        conn.commit()
    print("APPLIED; backup:", backup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
