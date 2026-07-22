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


def now_ts():
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def yes_no(value):
    return "YES" if value else "NO"


def main():
    conn = sqlite3.connect(paths.OSBB_DB_FILE)
    cur = conn.cursor()

    report_dir = paths.OSBB_EXPORTS_DIR / "audits"
    report_dir.mkdir(parents=True, exist_ok=True)

    report_file = report_dir / f"bot_admins_report_{now_ts()}.txt"

    lines = []
    lines.append("=" * 80)
    lines.append("BOT ADMINS REPORT")
    lines.append("=" * 80)
    lines.append(f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append("")

    cur.execute("""
        SELECT COUNT(*)
        FROM bot_admins
    """)
    total = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM bot_admins
        WHERE is_active = 1
    """)
    active = cur.fetchone()[0]

    lines.append("=" * 80)
    lines.append("SUMMARY")
    lines.append("=" * 80)
    lines.append(f"Total admins : {total}")
    lines.append(f"Active admins: {active}")
    lines.append("")

    cur.execute("""
        SELECT
            role,
            COUNT(*)
        FROM bot_admins
        GROUP BY role
        ORDER BY
            CASE role
                WHEN 'super_admin' THEN 1
                WHEN 'admin' THEN 2
                WHEN 'operator' THEN 3
                WHEN 'viewer' THEN 4
                ELSE 9
            END
    """)

    lines.append("By role:")
    for role, count in cur.fetchall():
        lines.append(f"  {role:12}: {count}")

    lines.append("")
    lines.append("=" * 80)
    lines.append("ADMINS")
    lines.append("=" * 80)

    cur.execute("""
        SELECT
            telegram_user_id,
            telegram_username,
            display_name,
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

    for row in rows:
        (
            telegram_user_id,
            telegram_username,
            display_name,
            role,

            can_read,
            can_write,
            can_manage_users,
            can_manage_payments,
            can_manage_bot,

            is_active,
            created_at,
            updated_at,
            notes,
        ) = row

        lines.append("-" * 80)
        lines.append(f"Telegram ID : {telegram_user_id}")
        lines.append(f"Username    : @{telegram_username}" if telegram_username else "Username    : -")
        lines.append(f"Display name: {display_name or '-'}")
        lines.append(f"Role        : {role}")
        lines.append(f"Active      : {yes_no(is_active)}")
        lines.append("")
        lines.append("Permissions:")
        lines.append(f"  can_read           : {yes_no(can_read)}")
        lines.append(f"  can_write          : {yes_no(can_write)}")
        lines.append(f"  can_manage_users   : {yes_no(can_manage_users)}")
        lines.append(f"  can_manage_payments: {yes_no(can_manage_payments)}")
        lines.append(f"  can_manage_bot     : {yes_no(can_manage_bot)}")
        lines.append("")
        lines.append(f"Created at : {created_at or '-'}")
        lines.append(f"Updated at : {updated_at or '-'}")
        lines.append(f"Notes      : {notes or '-'}")

    conn.close()

    report_file.write_text("\n".join(lines), encoding="utf-8")

    print("Bot admins report created.")
    print("Report:")
    print(report_file)


if __name__ == "__main__":
    main()