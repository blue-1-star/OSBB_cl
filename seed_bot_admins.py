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

sys.path.insert(0, str(paths.SECRETS_DIR))

try:
    from telegram_osbb import SUPER_ADMIN_IDS, ADMIN_IDS
except ImportError as e:
    raise ImportError(
        "Не удалось импортировать SUPER_ADMIN_IDS / ADMIN_IDS "
        "из telegram_osbb.py"
    ) from e


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_ids(value):
    if not value:
        return []

    result = []

    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            print(f"Skipped invalid Telegram ID: {item}")

    return sorted(set(result))


def upsert_admin(cur, telegram_user_id, role):
    if role == "super_admin":
        can_read = 1
        can_write = 1
        can_manage_users = 1
        can_manage_payments = 1
        can_manage_bot = 1
    elif role == "operator":
        can_read = 1
        can_write = 1
        can_manage_users = 0
        can_manage_payments = 1
        can_manage_bot = 0
    else:
        can_read = 1
        can_write = 0
        can_manage_users = 0
        can_manage_payments = 0
        can_manage_bot = 0

    cur.execute("""
        INSERT INTO bot_admins (
            telegram_user_id,
            role,
            can_read,
            can_write,
            can_manage_users,
            can_manage_payments,
            can_manage_bot,
            is_active,
            created_at,
            updated_at,
            notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)

        ON CONFLICT(telegram_user_id)
        DO UPDATE SET
            role = excluded.role,
            can_read = excluded.can_read,
            can_write = excluded.can_write,
            can_manage_users = excluded.can_manage_users,
            can_manage_payments = excluded.can_manage_payments,
            can_manage_bot = excluded.can_manage_bot,
            is_active = 1,
            updated_at = excluded.updated_at,
            notes = excluded.notes
    """, (
        telegram_user_id,
        role,
        can_read,
        can_write,
        can_manage_users,
        can_manage_payments,
        can_manage_bot,
        now(),
        now(),
        "Seeded from telegram_osbb.py",
    ))


def main():
    super_admin_ids = normalize_ids(SUPER_ADMIN_IDS)
    admin_ids = normalize_ids(ADMIN_IDS)

    conn = sqlite3.connect(paths.OSBB_DB_FILE)
    cur = conn.cursor()

    for user_id in admin_ids:
        upsert_admin(cur, user_id, "operator")

    for user_id in super_admin_ids:
        upsert_admin(cur, user_id, "super_admin")

    conn.commit()

    cur.execute("""
        SELECT
            telegram_user_id,
            role,
            can_read,
            can_write,
            can_manage_users,
            can_manage_payments,
            can_manage_bot,
            is_active
        FROM bot_admins
        ORDER BY
            CASE role
                WHEN 'super_admin' THEN 1
                WHEN 'admin' THEN 2
                WHEN 'operator' THEN 3
                WHEN 'viewer' THEN 4
                ELSE 9
            END,
            telegram_user_id
    """)

    rows = cur.fetchall()

    conn.close()

    print()
    print("=" * 70)
    print("BOT ADMINS SEEDED")
    print("=" * 70)

    for row in rows:
        (
            telegram_user_id,
            role,
            can_read,
            can_write,
            can_manage_users,
            can_manage_payments,
            can_manage_bot,
            is_active,
        ) = row

        print(
            f"{telegram_user_id} | "
            f"{role:12} | "
            f"read={can_read} "
            f"write={can_write} "
            f"users={can_manage_users} "
            f"payments={can_manage_payments} "
            f"bot={can_manage_bot} "
            f"active={is_active}"
        )

    print()


if __name__ == "__main__":
    main()