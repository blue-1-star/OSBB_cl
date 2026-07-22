from __future__ import annotations
import argparse, shutil, sqlite3
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "Data" / "db" / "osbb_test.db"
BACKUP_DIR = PROJECT_ROOT / "Data" / "backups" / "db"

DDL = [
"""CREATE TABLE IF NOT EXISTS resident_invitations (
id INTEGER PRIMARY KEY AUTOINCREMENT,
invitation_code TEXT UNIQUE,
apartment_number TEXT NOT NULL,
expected_full_name TEXT,
expected_phone TEXT,
status TEXT NOT NULL DEFAULT 'INVITED',
created_by_admin_id TEXT,
created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
accepted_by_telegram_id TEXT,
accepted_at TEXT,
notes TEXT
)""",
"""CREATE TABLE IF NOT EXISTS resident_verification_requests (
id INTEGER PRIMARY KEY AUTOINCREMENT,
request_number TEXT UNIQUE,
invitation_id INTEGER,
telegram_user_id TEXT NOT NULL,
telegram_username TEXT,
apartment_number TEXT,
claimed_full_name TEXT,
claimed_phone TEXT,
resident_says_ok INTEGER NOT NULL DEFAULT 0,
status TEXT NOT NULL DEFAULT 'PENDING_ADMIN_CONFIRMATION',
created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
reviewed_by_admin_id TEXT,
reviewed_at TEXT,
review_note TEXT,
FOREIGN KEY(invitation_id) REFERENCES resident_invitations(id)
)""",
"""CREATE TABLE IF NOT EXISTS resident_access_accounts (
id INTEGER PRIMARY KEY AUTOINCREMENT,
telegram_user_id TEXT UNIQUE NOT NULL,
telegram_username TEXT,
resident_id INTEGER,
apartment_number TEXT,
role TEXT NOT NULL DEFAULT 'resident',
status TEXT NOT NULL DEFAULT 'ACTIVE',
confirmed_by_admin_id TEXT,
confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
notes TEXT
)""",
"""CREATE TABLE IF NOT EXISTS operator_task_queue (
id INTEGER PRIMARY KEY AUTOINCREMENT,
task_number TEXT UNIQUE,
priority TEXT NOT NULL DEFAULT 'NORMAL',
task_type TEXT NOT NULL,
status TEXT NOT NULL DEFAULT 'PENDING',
apartment_number TEXT,
vehicle_id INTEGER,
plate TEXT,
telegram_user_id TEXT,
title TEXT,
description TEXT,
origin TEXT NOT NULL DEFAULT 'SYSTEM',
created_by TEXT,
assigned_to TEXT,
created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
updated_at TEXT,
closed_at TEXT,
close_note TEXT
)""",
"CREATE INDEX IF NOT EXISTS idx_resident_invitations_apartment ON resident_invitations(apartment_number)",
"CREATE INDEX IF NOT EXISTS idx_resident_verification_status ON resident_verification_requests(status)",
"CREATE INDEX IF NOT EXISTS idx_operator_task_status ON operator_task_queue(status, priority)"
]

def resolve_db(text: str) -> Path:
    p = Path(text)
    if p.is_absolute():
        return p
    if str(p).startswith("OSBB"):
        return (PROJECT_ROOT.parent / p).resolve()
    return (PROJECT_ROOT / p).resolve()

def exists(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

def backup(db):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dst = BACKUP_DIR / f"before_user_onboarding_schema_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.db"
    shutil.copy2(db, dst)
    return dst

def main():
    ap = argparse.ArgumentParser(description="Create OSBB resident onboarding schema.")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db = resolve_db(args.db)
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")
    con = sqlite3.connect(str(db))
    try:
        print("=" * 90)
        print("OSBB User Onboarding Schema")
        print("=" * 90)
        print("DB:", db)
        print("Apply:", args.apply)
        print()
        for name in ["resident_invitations","resident_verification_requests","resident_access_accounts","operator_task_queue"]:
            print(f"{name:40s} {'EXISTS' if exists(con,name) else 'MISSING'}")
        if not args.apply:
            print()
            print("DRY RUN ONLY - no changes saved.")
            print("To apply:")
            print("python .\\OSBB\\tools\\user_onboarding\\create_onboarding_schema.py --apply")
            return 0
        b = backup(db)
        for sql in DDL:
            con.execute(sql)
        con.commit()
        print()
        print("Backup:", b)
        print("APPLIED")
        return 0
    finally:
        con.close()

if __name__ == "__main__":
    raise SystemExit(main())
