from pathlib import Path
import sys
import sqlite3
from datetime import datetime

OSBB_ROOT = Path(__file__).resolve().parent
PY_ROOT = OSBB_ROOT.parent

for p in (OSBB_ROOT, PY_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from config import paths


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def column_exists(cur, table_name, column_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cur.fetchall()]
    return column_name in columns


def add_column_if_missing(cur, table_name, column_def):
    column_name = column_def.split()[0]

    if not column_exists(cur, table_name, column_name):
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_def}")
        print(f"Added column: {table_name}.{column_name}")


def migrate():
    conn = sqlite3.connect(paths.OSBB_DB_FILE)
    cur = conn.cursor()

    # ==========================================================
    # resident_accounts
    # Telegram user <-> apartment/account binding
    # ==========================================================

    cur.execute("""
    CREATE TABLE IF NOT EXISTS resident_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        telegram_user_id INTEGER UNIQUE,
        telegram_username TEXT,

        telegram_first_name TEXT,
        telegram_last_name TEXT,

        apartment_id INTEGER,
        apartment_number TEXT,

        role TEXT DEFAULT 'resident',
        status TEXT DEFAULT 'new',

        language_code TEXT DEFAULT 'ru',

        created_at TEXT,
        updated_at TEXT,
        verified_at TEXT,
        last_seen_at TEXT,

        notes TEXT,

        FOREIGN KEY(apartment_id) REFERENCES apartments(id)
    )
    """)

    add_column_if_missing(
        cur,
        "resident_accounts",
        "telegram_first_name TEXT"
    )
    add_column_if_missing(
        cur,
        "resident_accounts",
        "telegram_last_name TEXT"
    )
    add_column_if_missing(
        cur,
        "resident_accounts",
        "apartment_id INTEGER"
    )
    add_column_if_missing(
        cur,
        "resident_accounts",
        "apartment_number TEXT"
    )
    add_column_if_missing(
        cur,
        "resident_accounts",
        "role TEXT DEFAULT 'resident'"
    )
    add_column_if_missing(
        cur,
        "resident_accounts",
        "status TEXT DEFAULT 'new'"
    )
    add_column_if_missing(
        cur,
        "resident_accounts",
        "language_code TEXT DEFAULT 'ru'"
    )
    add_column_if_missing(
        cur,
        "resident_accounts",
        "updated_at TEXT"
    )
    add_column_if_missing(
        cur,
        "resident_accounts",
        "verified_at TEXT"
    )
    add_column_if_missing(
        cur,
        "resident_accounts",
        "last_seen_at TEXT"
    )
    add_column_if_missing(
        cur,
        "resident_accounts",
        "notes TEXT"
    )

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_resident_accounts_telegram_user
    ON resident_accounts(telegram_user_id)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_resident_accounts_apartment
    ON resident_accounts(apartment_number)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_resident_accounts_status
    ON resident_accounts(status)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_resident_accounts_role
    ON resident_accounts(role)
    """)

    # ==========================================================
    # bot_admins
    # Bot roles and access rights
    # ==========================================================

    cur.execute("""
    CREATE TABLE IF NOT EXISTS bot_admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        telegram_user_id INTEGER UNIQUE,
        telegram_username TEXT,

        display_name TEXT,

        role TEXT DEFAULT 'viewer',

        can_read INTEGER DEFAULT 1,
        can_write INTEGER DEFAULT 0,
        can_manage_users INTEGER DEFAULT 0,
        can_manage_payments INTEGER DEFAULT 0,
        can_manage_bot INTEGER DEFAULT 0,

        is_active INTEGER DEFAULT 1,

        created_at TEXT,
        updated_at TEXT,

        notes TEXT
    )
    """)

    add_column_if_missing(
        cur,
        "bot_admins",
        "telegram_username TEXT"
    )
    add_column_if_missing(
        cur,
        "bot_admins",
        "display_name TEXT"
    )
    add_column_if_missing(
        cur,
        "bot_admins",
        "role TEXT DEFAULT 'viewer'"
    )
    add_column_if_missing(
        cur,
        "bot_admins",
        "can_read INTEGER DEFAULT 1"
    )
    add_column_if_missing(
        cur,
        "bot_admins",
        "can_write INTEGER DEFAULT 0"
    )
    add_column_if_missing(
        cur,
        "bot_admins",
        "can_manage_users INTEGER DEFAULT 0"
    )
    add_column_if_missing(
        cur,
        "bot_admins",
        "can_manage_payments INTEGER DEFAULT 0"
    )
    add_column_if_missing(
        cur,
        "bot_admins",
        "can_manage_bot INTEGER DEFAULT 0"
    )
    add_column_if_missing(
        cur,
        "bot_admins",
        "is_active INTEGER DEFAULT 1"
    )
    add_column_if_missing(
        cur,
        "bot_admins",
        "updated_at TEXT"
    )
    add_column_if_missing(
        cur,
        "bot_admins",
        "notes TEXT"
    )
    add_column_if_missing(
        cur,
        "audit_log",
        "telegram_user_id INTEGER"
    )

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_bot_admins_telegram_user
    ON bot_admins(telegram_user_id)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_bot_admins_active
    ON bot_admins(is_active)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_bot_admins_role
    ON bot_admins(role)
    """)

    # ==========================================================
    # audit_log
    # All critical changes from bot/operator/admin
    # ==========================================================

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        telegram_user_id INTEGER,
        actor_role TEXT,
        actor_name TEXT,

        action_type TEXT,

        table_name TEXT,
        record_id TEXT,

        old_value TEXT,
        new_value TEXT,

        source TEXT DEFAULT 'bot',

        created_at TEXT
    )
    """)

    add_column_if_missing(
        cur,
        "audit_log",
        "actor_role TEXT"
    )
    add_column_if_missing(
        cur,
        "audit_log",
        "actor_name TEXT"
    )
    add_column_if_missing(
        cur,
        "audit_log",
        "source TEXT DEFAULT 'bot'"
    )

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_audit_log_actor
    ON audit_log(telegram_user_id)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_audit_log_table_record
    ON audit_log(table_name, record_id)
    """)

    if column_exists(cur, "audit_log", "created_at"):
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_log_created
        ON audit_log(created_at)
        """)
    elif column_exists(cur, "audit_log", "event_time"):
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_log_event_time
        ON audit_log(event_time)
        """)

    # ==========================================================
    # bot_user_sessions
    # Lightweight persistent state for bot workflows
    # ==========================================================

    cur.execute("""
    CREATE TABLE IF NOT EXISTS bot_user_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        telegram_user_id INTEGER UNIQUE,

        current_state TEXT,
        context_json TEXT,

        updated_at TEXT
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_bot_user_sessions_user
    ON bot_user_sessions(telegram_user_id)
    """)

    conn.commit()

    print()
    print("=" * 70)
    print("BOT CORE MIGRATION COMPLETED")
    print("=" * 70)

    for table in [
        "resident_accounts",
        "bot_admins",
        "audit_log",
        "bot_user_sessions",
    ]:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        count = cur.fetchone()[0]
        print(f"{table:24}: {count}")

    print()
    print("DB:", paths.OSBB_DB_FILE)

    conn.close()


if __name__ == "__main__":
    migrate()