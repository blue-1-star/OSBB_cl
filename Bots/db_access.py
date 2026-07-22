from pathlib import Path
import sys
import sqlite3
import re
from datetime import datetime


BOT_DIR = Path(__file__).resolve().parent
OSBB_ROOT = BOT_DIR.parent
PY_ROOT = OSBB_ROOT.parent

for p in (OSBB_ROOT, PY_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# from config import paths
from config import paths, USE_TEST_DB

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# USE_TEST_DB = True
# USE_TEST_DB = False

def get_conn():
    if USE_TEST_DB:
        return sqlite3.connect(paths.OSBB_TEST_DB_FILE)
    return sqlite3.connect(paths.OSBB_DB_FILE)


# ==========================================================
# Admin / permissions
# ==========================================================

def get_admin_record(telegram_user_id):
    conn = get_conn()
    cur = conn.cursor()

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
            is_active
        FROM bot_admins
        WHERE telegram_user_id = ?
          AND is_active = 1
    """, (int(telegram_user_id),))

    row = cur.fetchone()
    conn.close()

    return row


def is_admin(telegram_user_id):
    return get_admin_record(telegram_user_id) is not None


def is_super_admin(telegram_user_id):
    row = get_admin_record(telegram_user_id)

    if not row:
        return False

    return row[3] == "super_admin"


def can_write(telegram_user_id):
    row = get_admin_record(telegram_user_id)

    if not row:
        return False

    return bool(row[5])


def can_manage_users(telegram_user_id):
    row = get_admin_record(telegram_user_id)

    if not row:
        return False

    return bool(row[6])


# ==========================================================
# Resident accounts
# ==========================================================

def upsert_resident_account_from_telegram(user, language_code="ru"):
    """
    Создаёт/обновляет resident_accounts по Telegram user.
    Квартиру не привязывает.
    """

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO resident_accounts (
            telegram_user_id,
            telegram_username,
            telegram_first_name,
            telegram_last_name,
            role,
            status,
            language_code,
            created_at,
            updated_at,
            last_seen_at
        )
        VALUES (?, ?, ?, ?, 'resident', 'new', ?, ?, ?, ?)

        ON CONFLICT(telegram_user_id)
        DO UPDATE SET
            telegram_username = excluded.telegram_username,
            telegram_first_name = excluded.telegram_first_name,
            telegram_last_name = excluded.telegram_last_name,
            language_code = excluded.language_code,
            updated_at = excluded.updated_at,
            last_seen_at = excluded.last_seen_at
    """, (
        int(user.id),
        user.username,
        user.first_name,
        user.last_name,
        language_code,
        now(),
        now(),
        now(),
    ))
    #    

    conn.commit()
    conn.close()


def get_resident_account(telegram_user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            telegram_user_id,
            telegram_username,
            telegram_first_name,
            telegram_last_name,
            apartment_id,
            apartment_number,
            role,
            status,
            language_code,
            created_at,
            updated_at,
            verified_at,
            last_seen_at,
            notes
        FROM resident_accounts
        WHERE telegram_user_id = ?
    """, (int(telegram_user_id),))

    row = cur.fetchone()
    conn.close()

    return row


def get_accounts_by_apartment(apartment_number):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            telegram_user_id,
            telegram_username,
            telegram_first_name,
            telegram_last_name,
            role,
            status,
            verified_at
        FROM resident_accounts
        WHERE apartment_number = ?
        ORDER BY verified_at, telegram_user_id
    """, (str(apartment_number),))

    rows = cur.fetchall()
    conn.close()

    return rows

# 
# 
def get_resident_accounts_summary(limit=10):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*)
        FROM resident_accounts
    """)
    total = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM resident_accounts
        WHERE apartment_number IS NOT NULL
          AND TRIM(apartment_number) <> ''
    """)
    linked = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM resident_accounts
        WHERE apartment_number IS NULL
           OR TRIM(apartment_number) = ''
    """)
    unlinked = cur.fetchone()[0]

    cur.execute("""
        SELECT
            telegram_user_id,
            telegram_username,
            telegram_first_name,
            telegram_last_name,
            apartment_number,
            status,
            last_seen_at
        FROM resident_accounts
        ORDER BY
            COALESCE(last_seen_at, updated_at, created_at) DESC
        LIMIT ?
    """, (limit,))

    rows = cur.fetchall()
    conn.close()

    return {
        "total": total,
        "linked": linked,
        "unlinked": unlinked,
        "rows": rows,
    }


def format_resident_accounts_summary(summary):
    lines = []

    lines.append("👥 Пользователи")
    lines.append("")
    lines.append(f"Всего: {summary['total']}")
    lines.append(f"С квартирой: {summary['linked']}")
    lines.append(f"Без квартиры: {summary['unlinked']}")
    lines.append("")
    lines.append("Последние:")

    if not summary["rows"]:
        lines.append("пока нет пользователей")
        return "\n".join(lines)

    for row in summary["rows"]:
        (
            telegram_user_id,
            username,
            first_name,
            last_name,
            apartment_number,
            status,
            last_seen_at,
        ) = row

        name_parts = [
            x for x in [first_name, last_name]
            if x
        ]

        display_name = " ".join(name_parts) or "-"
        username_text = f"@{username}" if username else "-"
        apt = apartment_number or "-"
        status = status or "-"

        lines.append(
            f"• кв.{apt} | {display_name} | "
            f"{username_text} | {status}"
        )

    return "\n".join(lines)


def find_apartment(apartment_number):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            apartment_number,
            entrance
        FROM apartments
        WHERE apartment_number = ?
    """, (str(apartment_number),))

    row = cur.fetchone()
    conn.close()

    return row


def link_resident_to_apartment(telegram_user_id, apartment_number, status="apartment_confirmed"):
    apt = find_apartment(apartment_number)

    if not apt:
        return False, "apartment_not_found"

    apartment_id = apt[0]

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE resident_accounts
        SET
            apartment_id = ?,
            apartment_number = ?,
            status = ?,
            verified_at = ?,
            updated_at = ?
        WHERE telegram_user_id = ?
    """, (
        apartment_id,
        str(apartment_number),
        status,
        now(),
        now(),
        int(telegram_user_id),
    ))

    conn.commit()
    conn.close()

    return True, "linked"


# ==========================================================
# Apartments / vehicles
# ==========================================================

def get_apartment_vehicles(apartment_number):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM apartments
        WHERE apartment_number = ?
    """, (str(apartment_number),))

    apt = cur.fetchone()

    if not apt:
        conn.close()
        return []

    apartment_id = apt[0]

    cur.execute("""
        SELECT
            id,
            license_plate_normalized,
            license_plate,
            car_model_normalized,
            car_model,
            parking_time
        FROM vehicles
        WHERE apartment_id = ?
        ORDER BY id
    """, (apartment_id,))

    rows = cur.fetchall()
    conn.close()

    return rows


def format_vehicle_list(vehicles):
    if not vehicles:
        return "Автомобили пока не найдены."

    lines = []

    for (
        vehicle_id,
        plate_norm,
        plate_raw,
        model_norm,
        model_raw,
        parking_time,
    ) in vehicles:
        plate = plate_norm or plate_raw or "-"
        model = model_norm or model_raw or "-"
        pt = parking_time or "не указан"

        lines.append(
            f"• {plate} | {model} | тариф: {pt}"
        )

    return "\n".join(lines)


# ==========================================================
# Audit log
# ==========================================================

def write_audit_log(
    telegram_user_id,
    action_type,
    table_name=None,
    record_id=None,
    old_value=None,
    new_value=None,
    actor_role=None,
    actor_name=None,
    comment=None,
    source="bot",
):
    conn = get_conn()
    cur = conn.cursor()

    # В вашей БД audit_log уже имеет старую структуру:
    # event_time, username, action, field_name, comment...
    cur.execute("""
        INSERT INTO audit_log (
            event_time,
            telegram_user_id,
            username,
            actor_role,
            actor_name,
            action,
            action_type,
            table_name,
            record_id,
            old_value,
            new_value,
            comment,
            source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now(),
        int(telegram_user_id) if telegram_user_id else None,
        str(telegram_user_id) if telegram_user_id else None,
        actor_role,
        actor_name,
        action_type,
        action_type,
        table_name,
        str(record_id) if record_id is not None else None,
        old_value,
        new_value,
        comment,
        source,
    ))

    conn.commit()
    conn.close()
#  -----------
# append 
# ==========================================================
# Resident accounts - admin lists and operator verification
# Add this block into Bots\db_access.py after resident account functions.
# ==========================================================

def get_resident_accounts_by_filter(filter_name="all", limit=30):
    conn = get_conn()
    cur = conn.cursor()

    where = ""

    if filter_name == "without_apartment":
        where = """
        WHERE apartment_number IS NULL
           OR TRIM(apartment_number) = ''
        """
    elif filter_name == "self_confirmed":
        where = """
        WHERE status = 'apartment_confirmed'
        """
    elif filter_name == "operator_verified":
        where = """
        WHERE status = 'operator_verified'
        """

    cur.execute(f"""
        SELECT
            telegram_user_id,
            telegram_username,
            telegram_first_name,
            telegram_last_name,
            apartment_number,
            status,
            last_seen_at
        FROM resident_accounts
        {where}
        ORDER BY
            COALESCE(last_seen_at, updated_at, created_at) DESC
        LIMIT ?
    """, (limit,))

    rows = cur.fetchall()
    conn.close()
    return rows


def format_resident_accounts_list(title, rows):
    lines = [f"👥 {title}", ""]

    if not rows:
        lines.append("Список пуст.")
        return "\n".join(lines)

    for row in rows:
        (
            telegram_user_id,
            username,
            first_name,
            last_name,
            apartment_number,
            status,
            last_seen_at,
        ) = row

        name = " ".join(x for x in [first_name, last_name] if x) or "-"
        username_text = f"@{username}" if username else "-"
        apt = apartment_number or "-"
        status = status or "-"

        lines.append(
            f"• ID:{telegram_user_id} | кв.{apt} | "
            f"{name} | {username_text} | {status}"
        )

    return "\n".join(lines)


def get_resident_account_by_telegram_id(telegram_user_id):
    return get_resident_account(int(telegram_user_id))


def format_resident_account_card(account):
    if not account:
        return "Пользователь не найден."

    (
        account_id,
        telegram_user_id,
        telegram_username,
        telegram_first_name,
        telegram_last_name,
        apartment_id,
        apartment_number,
        role,
        status,
        language_code,
        created_at,
        updated_at,
        verified_at,
        last_seen_at,
        notes,
    ) = account

    name = " ".join(
        x for x in [telegram_first_name, telegram_last_name]
        if x
    ) or "-"

    username = f"@{telegram_username}" if telegram_username else "-"

    vehicles = []
    if apartment_number:
        vehicles = get_apartment_vehicles(apartment_number)

    lines = []
    lines.append("👤 Карточка пользователя")
    lines.append("")
    lines.append(f"Telegram ID : {telegram_user_id}")
    lines.append(f"Имя         : {name}")
    lines.append(f"Username    : {username}")
    lines.append(f"Квартира    : {apartment_number or '-'}")
    lines.append(f"Роль        : {role or '-'}")
    lines.append(f"Статус      : {status or '-'}")
    lines.append(f"Язык        : {language_code or '-'}")
    lines.append("")
    lines.append("Автомобили:")
    lines.append(format_vehicle_list(vehicles))

    return "\n".join(lines)


def mark_resident_operator_verified(telegram_user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE resident_accounts
        SET
            status = 'operator_verified',
            updated_at = ?,
            notes = COALESCE(notes, '') || ' | operator_verified'
        WHERE telegram_user_id = ?
    """, (
        now(),
        int(telegram_user_id),
    ))

    changed = cur.rowcount
    conn.commit()
    conn.close()

    if changed:
        return True, "operator_verified"

    return False, "user_not_found"


def unlink_resident_account(telegram_user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE resident_accounts
        SET
            apartment_id = NULL,
            apartment_number = NULL,
            status = 'new',
            updated_at = ?,
            notes = COALESCE(notes, '') || ' | apartment_unlinked'
        WHERE telegram_user_id = ?
    """, (
        now(),
        int(telegram_user_id),
    ))

    changed = cur.rowcount
    conn.commit()
    conn.close()

    if changed:
        return True, "unlinked"

    return False, "user_not_found"


# ==========================================================
# Vehicle edit workflow
# ==========================================================

def get_vehicle_by_id_for_apartment(vehicle_id, apartment_number=None):
    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return None

    if apartment_number is not None and str(vehicle[1]) != str(apartment_number):
        return None

    return vehicle


def format_vehicle_card_for_edit(vehicle):
    if not vehicle:
        return "Автомобиль не найден."

    (
        vehicle_id,
        apartment_number,
        plate_norm,
        plate_raw,
        model_norm,
        model_raw,
        parking_time,
    ) = vehicle

    plate = plate_norm or plate_raw or "-"
    model = model_norm or model_raw or "-"
    status = parking_time or "NULL"

    return (
        "🚗 Автомобиль\n\n"
        f"Квартира: {apartment_number}\n"
        f"Номер: {plate}\n"
        f"Марка: {model}\n"
        f"Статус: {status}"
    )


def update_vehicle_parking_status(vehicle_id, status):
    if status == "NULL":
        new_value = None
    elif status in ["Day", "Night", "Inactive"]:
        new_value = status
    else:
        return False, "invalid_status"

    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return False, "vehicle_not_found"

    old_value = vehicle[6]

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE vehicles
        SET parking_time = ?
        WHERE id = ?
    """, (
        new_value,
        int(vehicle_id),
    ))

    changed = cur.rowcount
    conn.commit()
    conn.close()

    if not changed:
        return False, "not_updated"

    return True, {
        "vehicle_id": int(vehicle_id),
        "old_value": old_value,
        "new_value": new_value,
    }


def update_vehicle_plate(vehicle_id, plate):
    plate = str(plate).strip().upper()

    if not plate:
        return False, "empty_plate"

    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return False, "vehicle_not_found"

    old_plate = vehicle[2]
    old_plate_norm = vehicle[3]

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE vehicles
        SET
            license_plate = ?,
            license_plate_normalized = ?
        WHERE id = ?
    """, (
        plate,
        plate,
        int(vehicle_id),
    ))

    changed = cur.rowcount
    conn.commit()
    conn.close()

    if not changed:
        return False, "not_updated"

    return True, {
        "vehicle_id": int(vehicle_id),
        "old_value": old_plate_norm or old_plate,
        "new_value": plate,
    }


def update_vehicle_model(vehicle_id, model):
    model = str(model).strip().upper()

    if not model:
        return False, "empty_model"

    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return False, "vehicle_not_found"

    old_model = vehicle[4]
    old_model_norm = vehicle[5]

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE vehicles
        SET
            car_model = ?,
            car_model_normalized = ?
        WHERE id = ?
    """, (
        model,
        model,
        int(vehicle_id),
    ))

    changed = cur.rowcount
    conn.commit()
    conn.close()

    if not changed:
        return False, "not_updated"

    return True, {
        "vehicle_id": int(vehicle_id),
        "old_value": old_model_norm or old_model,
        "new_value": model,
    }


# ==========================================================
# Compatibility: vehicle review functions
# ==========================================================

def get_next_vehicle_for_review():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            v.id,
            a.apartment_number,
            v.license_plate_normalized,
            v.license_plate,
            v.car_model_normalized,
            v.car_model,
            v.parking_time
        FROM vehicles v
        JOIN apartments a
            ON a.id = v.apartment_id
        WHERE
            v.parking_time IS NULL
            OR TRIM(v.parking_time) = ''
        ORDER BY
            CASE
                WHEN a.apartment_number GLOB '[0-9]*'
                THEN CAST(a.apartment_number AS INTEGER)
                ELSE 999999
            END,
            a.apartment_number,
            v.id
        LIMIT 1
    """)

    row = cur.fetchone()
    conn.close()
    return row


def set_vehicle_day(vehicle_id):
    return update_vehicle_parking_status(vehicle_id, "Day")


def set_vehicle_night(vehicle_id):
    return update_vehicle_parking_status(vehicle_id, "Night")


def set_vehicle_inactive(vehicle_id):
    return update_vehicle_parking_status(vehicle_id, "Inactive")


def skip_vehicle_review(vehicle_id):
    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return False, "vehicle_not_found"

    return True, {
        "vehicle_id": int(vehicle_id),
        "action": "skipped",
    }


def get_vehicle_review_stats():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM vehicles")
    total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM vehicles WHERE parking_time = 'Day'")
    day_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM vehicles WHERE parking_time = 'Night'")
    night_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM vehicles WHERE parking_time = 'Inactive'")
    inactive_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM vehicles
        WHERE parking_time IS NULL
           OR TRIM(parking_time) = ''
    """)
    missing_count = cur.fetchone()[0]

    conn.close()

    return {
        "total": total,
        "Day": day_count,
        "Night": night_count,
        "Inactive": inactive_count,
        "missing": missing_count,
    }


def format_vehicle_review_card(vehicle):
    if not vehicle:
        return "Все автомобили уже имеют статус Day / Night / Inactive."

    (
        vehicle_id,
        apartment_number,
        plate_norm,
        plate_raw,
        model_norm,
        model_raw,
        parking_time,
    ) = vehicle

    plate = plate_norm or plate_raw or "-"
    model = model_norm or model_raw or "-"
    status = parking_time or "NULL"

    return (
        "🚗 Проверка автомобиля\n\n"
        f"ID: {vehicle_id}\n"
        f"Квартира: {apartment_number}\n\n"
        f"Номер: {plate}\n"
        f"Марка: {model}\n"
        f"Статус: {status}\n\n"
        "Выберите действие:"
    )


# ==========================================================
# Compatibility: vehicle status lists
# ==========================================================

def get_vehicles_by_status(status_filter="all", limit=50):
    conn = get_conn()
    cur = conn.cursor()

    where = ""

    if status_filter == "missing":
        where = """
        WHERE v.parking_time IS NULL
           OR TRIM(v.parking_time) = ''
        """
    elif status_filter in ["Day", "Night", "Inactive"]:
        where = "WHERE v.parking_time = ?"

    params = []

    if status_filter in ["Day", "Night", "Inactive"]:
        params.append(status_filter)

    params.append(limit)

    cur.execute(f"""
        SELECT
            v.id,
            a.apartment_number,
            v.license_plate_normalized,
            v.license_plate,
            v.car_model_normalized,
            v.car_model,
            v.parking_time
        FROM vehicles v
        JOIN apartments a
            ON a.id = v.apartment_id
        {where}
        ORDER BY
            CASE
                WHEN a.apartment_number GLOB '[0-9]*'
                THEN CAST(a.apartment_number AS INTEGER)
                ELSE 999999
            END,
            a.apartment_number,
            v.id
        LIMIT ?
    """, tuple(params))

    rows = cur.fetchall()
    conn.close()
    return rows


def format_vehicle_admin_line(row):
    if not row:
        return "Автомобиль не найден."

    (
        vehicle_id,
        apartment_number,
        plate_norm,
        plate_raw,
        model_norm,
        model_raw,
        parking_time,
    ) = row

    plate = plate_norm or plate_raw or "-"
    model = model_norm or model_raw or "-"
    status = parking_time or "NULL"

    return (
        f"кв.{apartment_number} | "
        f"{plate} | {model} | {status}"
    )


def format_vehicles_admin_list(title, rows):
    count = len(rows)

    # lines = [f"🚗 {title}: {count}", ""]
    lines = [
    f"🚗 {title}",
    f"Показано: {count} из базы (вывод ограничен 50 строками)",
    ""
]
    if not rows:
        lines.append("Список пуст.")
        return "\n".join(lines)

    for row in rows:
        lines.append("• " + format_vehicle_admin_line(row))

    return "\n".join(lines)


# ==========================================================
# REPAIR BLOCK: apartments and vehicle edit compatibility
# ==========================================================

def get_vehicle_by_id(vehicle_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            v.id,
            a.apartment_number,
            v.license_plate_normalized,
            v.license_plate,
            v.car_model_normalized,
            v.car_model,
            v.parking_time
        FROM vehicles v
        JOIN apartments a
            ON a.id = v.apartment_id
        WHERE v.id = ?
    """, (int(vehicle_id),))

    row = cur.fetchone()
    conn.close()
    return row


def get_apartment_card(apartment_number):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            apartment_number,
            entrance
        FROM apartments
        WHERE apartment_number = ?
    """, (str(apartment_number),))

    apartment = cur.fetchone()

    if not apartment:
        conn.close()
        return None

    cur.execute("""
        SELECT
            telegram_first_name,
            telegram_last_name,
            telegram_username,
            status
        FROM resident_accounts
        WHERE apartment_number = ?
        ORDER BY telegram_user_id
    """, (str(apartment_number),))

    residents = cur.fetchall()
    conn.close()

    vehicles = get_apartment_vehicles(apartment_number)

    return {
        "apartment_number": apartment[1],
        "entrance": apartment[2],
        "residents": residents,
        "vehicles": vehicles,
    }


def format_apartment_card(card):
    if not card:
        return "Квартира не найдена."

    residents_count = len(card["residents"])
    vehicles_count = len(card["vehicles"])

    lines = []
    lines.append(f"🏠 Квартира {card['apartment_number']}")
    lines.append("")
    lines.append(f"👥 Жильцы: {residents_count}")
    lines.append(f"🚗 Авто: {vehicles_count}")
    lines.append("")
    lines.append("Выберите раздел:")

    return "\n".join(lines)


def get_vehicle_by_id_for_apartment(vehicle_id, apartment_number=None):
    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return None

    if apartment_number is not None and str(vehicle[1]) != str(apartment_number):
        return None

    return vehicle


def format_vehicle_card_for_edit(vehicle):
    if not vehicle:
        return "Автомобиль не найден."

    (
        vehicle_id,
        apartment_number,
        plate_norm,
        plate_raw,
        model_norm,
        model_raw,
        parking_time,
    ) = vehicle

    plate = plate_norm or plate_raw or "-"
    model = model_norm or model_raw or "-"
    status = parking_time or "NULL"

    return (
        "🚗 Автомобиль\n\n"
        f"Квартира: {apartment_number}\n"
        f"Номер: {plate}\n"
        f"Марка: {model}\n"
        f"Статус: {status}"
    )


def update_vehicle_parking_status(vehicle_id, status):
    if status == "NULL":
        new_value = None
    elif status in ["Day", "Night", "Inactive"]:
        new_value = status
    else:
        return False, "invalid_status"

    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return False, "vehicle_not_found"

    old_value = vehicle[6]

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE vehicles
        SET parking_time = ?
        WHERE id = ?
    """, (
        new_value,
        int(vehicle_id),
    ))

    changed = cur.rowcount
    conn.commit()
    conn.close()

    if not changed:
        return False, "not_updated"

    return True, {
        "vehicle_id": int(vehicle_id),
        "old_value": old_value,
        "new_value": new_value,
    }


def update_vehicle_plate(vehicle_id, plate):
    plate = str(plate).strip().upper()

    if not plate:
        return False, "empty_plate"

    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return False, "vehicle_not_found"

    old_plate = vehicle[2] or vehicle[3]

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE vehicles
        SET
            license_plate = ?,
            license_plate_normalized = ?
        WHERE id = ?
    """, (
        plate,
        plate,
        int(vehicle_id),
    ))

    changed = cur.rowcount
    conn.commit()
    conn.close()

    if not changed:
        return False, "not_updated"

    return True, {
        "vehicle_id": int(vehicle_id),
        "old_value": old_plate,
        "new_value": plate,
    }


def update_vehicle_model(vehicle_id, model):
    model = str(model).strip().upper()

    if not model:
        return False, "empty_model"

    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return False, "vehicle_not_found"

    old_model = vehicle[4] or vehicle[5]

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE vehicles
        SET
            car_model = ?,
            car_model_normalized = ?
        WHERE id = ?
    """, (
        model,
        model,
        int(vehicle_id),
    ))

    changed = cur.rowcount
    conn.commit()
    conn.close()

    if not changed:
        return False, "not_updated"

    return True, {
        "vehicle_id": int(vehicle_id),
        "old_value": old_model,
        "new_value": model,
    }


def get_next_vehicle_for_review():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            v.id,
            a.apartment_number,
            v.license_plate_normalized,
            v.license_plate,
            v.car_model_normalized,
            v.car_model,
            v.parking_time
        FROM vehicles v
        JOIN apartments a
            ON a.id = v.apartment_id
        WHERE
            v.parking_time IS NULL
            OR TRIM(v.parking_time) = ''
        ORDER BY
            CASE
                WHEN a.apartment_number GLOB '[0-9]*'
                THEN CAST(a.apartment_number AS INTEGER)
                ELSE 999999
            END,
            a.apartment_number,
            v.id
        LIMIT 1
    """)

    row = cur.fetchone()
    conn.close()
    return row


def set_vehicle_day(vehicle_id):
    return update_vehicle_parking_status(vehicle_id, "Day")


def set_vehicle_night(vehicle_id):
    return update_vehicle_parking_status(vehicle_id, "Night")


def set_vehicle_inactive(vehicle_id):
    return update_vehicle_parking_status(vehicle_id, "Inactive")


def skip_vehicle_review(vehicle_id):
    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return False, "vehicle_not_found"

    return True, {
        "vehicle_id": int(vehicle_id),
        "action": "skipped",
    }


def get_vehicle_review_stats():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM vehicles")
    total = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM vehicles WHERE parking_time = 'Day'")
    day_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM vehicles WHERE parking_time = 'Night'")
    night_count = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM vehicles WHERE parking_time = 'Inactive'")
    inactive_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM vehicles
        WHERE parking_time IS NULL
           OR TRIM(parking_time) = ''
    """)
    missing_count = cur.fetchone()[0]

    conn.close()

    return {
        "total": total,
        "Day": day_count,
        "Night": night_count,
        "Inactive": inactive_count,
        "missing": missing_count,
    }


def format_vehicle_review_card(vehicle):
    if not vehicle:
        return "Все автомобили уже имеют статус Day / Night / Inactive."

    (
        vehicle_id,
        apartment_number,
        plate_norm,
        plate_raw,
        model_norm,
        model_raw,
        parking_time,
    ) = vehicle

    plate = plate_norm or plate_raw or "-"
    model = model_norm or model_raw or "-"
    status = parking_time or "NULL"

    return (
        "🚗 Проверка автомобиля\n\n"
        f"ID: {vehicle_id}\n"
        f"Квартира: {apartment_number}\n\n"
        f"Номер: {plate}\n"
        f"Марка: {model}\n"
        f"Статус: {status}\n\n"
        "Выберите действие:"
    )


def get_vehicles_by_status(status_filter="all", limit=50):
    conn = get_conn()
    cur = conn.cursor()

    where = ""

    if status_filter == "missing":
        where = """
        WHERE v.parking_time IS NULL
           OR TRIM(v.parking_time) = ''
        """
    elif status_filter in ["Day", "Night", "Inactive"]:
        where = "WHERE v.parking_time = ?"

    params = []

    if status_filter in ["Day", "Night", "Inactive"]:
        params.append(status_filter)

    params.append(limit)

    cur.execute(f"""
        SELECT
            v.id,
            a.apartment_number,
            v.license_plate_normalized,
            v.license_plate,
            v.car_model_normalized,
            v.car_model,
            v.parking_time
        FROM vehicles v
        JOIN apartments a
            ON a.id = v.apartment_id
        {where}
        ORDER BY
            CASE
                WHEN a.apartment_number GLOB '[0-9]*'
                THEN CAST(a.apartment_number AS INTEGER)
                ELSE 999999
            END,
            a.apartment_number,
            v.id
        LIMIT ?
    """, tuple(params))

    rows = cur.fetchall()
    conn.close()
    return rows


def format_vehicle_admin_line(row):
    if not row:
        return "Автомобиль не найден."

    (
        vehicle_id,
        apartment_number,
        plate_norm,
        plate_raw,
        model_norm,
        model_raw,
        parking_time,
    ) = row

    plate = plate_norm or plate_raw or "-"
    model = model_norm or model_raw or "-"
    status = parking_time or "NULL"

    return (
        f"кв.{apartment_number} | "
        f"{plate} | {model} | {status}"
    )


def format_vehicles_admin_list(title, rows):
    count = len(rows)

    lines = [f"🚗 {title}: {count}", ""]

    if not rows:
        lines.append("Список пуст.")
        return "\n".join(lines)

    for row in rows:
        lines.append("• " + format_vehicle_admin_line(row))

    return "\n".join(lines)

# ==========================================================
# Apartment verification / agreement workflow
# ==========================================================

VERIFICATION_STATUSES = {
    "new": "🆕 Не согласована",
    "confirmed": "✅ Согласована",
    "deferred": "⏳ Отложена",
    "conflict": "⚠️ Конфликт",
    "in_progress": "🔄 В работе",   
}


def get_verification_stats():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM apartment_verification")
    total = cur.fetchone()[0]

    result = {
        "total": total,
        "new": 0,
        "confirmed": 0,
        "deferred": 0,
        "conflict": 0,
    }

    cur.execute("""
        SELECT status, COUNT(*)
        FROM apartment_verification
        GROUP BY status
    """)

    for status, count in cur.fetchall():
        result[status] = count

    conn.close()

    result["done"] = result["confirmed"] + result["deferred"] + result["conflict"]
    result["remaining"] = result["new"]
    result["percent"] = round(result["done"] / total * 100, 1) if total else 0

    return result


def format_verification_stats(stats):
    return (
        "🤝 Согласование квартир\n\n"
        f"Всего квартир: {stats['total']}\n\n"
        f"✅ Согласовано: {stats['confirmed']}\n"
        f"⏳ Отложено: {stats['deferred']}\n"
        f"⚠️ Конфликт: {stats['conflict']}\n"
        f"🆕 Осталось: {stats['new']}\n\n"
        f"Готовность: {stats['percent']}%"
    )


def get_apartment_verification_status(apartment_number):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            av.id,
            av.apartment_id,
            av.apartment_number,
            av.status,
            av.comment,
            av.verified_by,
            av.verified_at,
            av.updated_at
        FROM apartment_verification av
        WHERE av.apartment_number = ?
    """, (str(apartment_number),))

    row = cur.fetchone()
    conn.close()
    return row


def get_next_apartment_for_verification(skip_apartment_numbers=None):
    skip_apartment_numbers = [
        str(x) for x in (skip_apartment_numbers or [])
        if str(x).strip()
    ]

    conn = get_conn()
    cur = conn.cursor()

    params = []
    skip_sql = ""

    if skip_apartment_numbers:
        placeholders = ",".join("?" for _ in skip_apartment_numbers)
        skip_sql = f"AND av.apartment_number NOT IN ({placeholders})"
        params.extend(skip_apartment_numbers)

    cur.execute(f"""
        SELECT
            av.id,
            av.apartment_id,
            av.apartment_number,
            av.status,
            av.comment,
            av.verified_by,
            av.verified_at,
            av.updated_at
        FROM apartment_verification av
        WHERE av.status = 'new'
        {skip_sql}
        ORDER BY
            CASE
                WHEN av.apartment_number GLOB '[0-9]*'
                THEN CAST(av.apartment_number AS INTEGER)
                ELSE 999999
            END,
            av.apartment_number
        LIMIT 1
    """, tuple(params))

    row = cur.fetchone()
    conn.close()
    return row


def get_apartments_by_verification_status(status, limit=30):
    if status not in VERIFICATION_STATUSES:
        return []

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            av.id,
            av.apartment_id,
            av.apartment_number,
            av.status,
            av.comment,
            av.verified_by,
            av.verified_at,
            av.updated_at
        FROM apartment_verification av
        WHERE av.status = ?
        ORDER BY
            CASE
                WHEN av.apartment_number GLOB '[0-9]*'
                THEN CAST(av.apartment_number AS INTEGER)
                ELSE 999999
            END,
            av.apartment_number
        LIMIT ?
    """, (status, int(limit)))

    rows = cur.fetchall()
    conn.close()
    return rows


def set_apartment_verification_status(
    apartment_number,
    status,
    verified_by=None,
    comment=None,
):
    if status not in VERIFICATION_STATUSES:
        return False, "invalid_status"

    apt = find_apartment(apartment_number)

    if not apt:
        return False, "apartment_not_found"

    apartment_id = apt[0]
    apartment_number = str(apt[1])
    verified_at = now() if status in ["confirmed", "deferred", "conflict"] else None

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO apartment_verification (
            apartment_id,
            apartment_number,
            status,
            comment,
            verified_by,
            verified_at,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(apartment_id)
        DO UPDATE SET
            apartment_number = excluded.apartment_number,
            status = excluded.status,
            comment = excluded.comment,
            verified_by = excluded.verified_by,
            verified_at = excluded.verified_at,
            updated_at = excluded.updated_at
    """, (
        apartment_id,
        apartment_number,
        status,
        comment,
        int(verified_by) if verified_by else None,
        verified_at,
        now(),
        now(),
    ))

    conn.commit()
    conn.close()

    return True, status


def format_verification_apartment_list(title, rows):
    lines = [f"🤝 {title}: {len(rows)}", ""]

    if not rows:
        lines.append("Список пуст.")
        return "\n".join(lines)

    for row in rows:
        (
            verification_id,
            apartment_id,
            apartment_number,
            status,
            comment,
            verified_by,
            verified_at,
            updated_at,
        ) = row

        label = VERIFICATION_STATUSES.get(status, status)
        suffix = f" | {comment}" if comment else ""
        lines.append(f"• кв.{apartment_number} | {label}{suffix}")

    return "\n".join(lines)



# ==========================================================
# Telegram bot quarantine source for agreement
# ==========================================================

def get_tbot_source_rows(apartment_number):
    db_file = paths.OSBB_QUARANTINE_DB_FILE

    if not db_file.exists():
        return {
            "ok": False,
            "error": f"quarantine_db_not_found: {db_file}",
            "rows": [],
        }

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'tbot_parking_import'
    """)

    if not cur.fetchone():
        conn.close()
        return {
            "ok": False,
            "error": "table_tbot_parking_import_not_found",
            "rows": [],
        }

    cur.execute("PRAGMA table_info(tbot_parking_import)")
    columns = [row[1] for row in cur.fetchall()]

    def pick(*names):
        for name in names:
            if name in columns:
                return name
        return None

    apartment_col = pick("apartment_number", "Номер квартири", "Номер квартиры", "Квартира")
    plate_norm_col = pick("license_plate_normalized")
    plate_raw_col = pick("license_plate", "Номер Авто", "Номер авто", "plate")
    model_norm_col = pick("car_model_normalized")
    model_raw_col = pick("car_model", "Марка авто", "Марка", "model")
    phone_col = pick("phone_normalized", "phone_number", "Телефон", "phone")
    status_col = pick("status", "Статус", "parking_time", "Тариф")

    if not apartment_col:
        conn.close()
        return {
            "ok": False,
            "error": "apartment_column_not_found",
            "rows": [],
        }

    select_parts = [
        f"{apartment_col} AS apartment_number"
    ]

    if plate_norm_col:
        select_parts.append(f"{plate_norm_col} AS license_plate_normalized")
    else:
        select_parts.append("NULL AS license_plate_normalized")

    if plate_raw_col:
        select_parts.append(f"{plate_raw_col} AS license_plate")
    else:
        select_parts.append("NULL AS license_plate")

    if model_norm_col:
        select_parts.append(f"{model_norm_col} AS car_model_normalized")
    else:
        select_parts.append("NULL AS car_model_normalized")

    if model_raw_col:
        select_parts.append(f"{model_raw_col} AS car_model")
    else:
        select_parts.append("NULL AS car_model")

    if phone_col:
        select_parts.append(f"{phone_col} AS phone_normalized")
    else:
        select_parts.append("NULL AS phone_normalized")

    if status_col:
        select_parts.append(f"{status_col} AS status")
    else:
        select_parts.append("NULL AS status")

    sql = f"""
        SELECT
            {", ".join(select_parts)}
        FROM tbot_parking_import
        WHERE CAST({apartment_col} AS TEXT) = ?
        ORDER BY rowid
    """

    cur.execute(sql, (str(apartment_number),))

    rows = cur.fetchall()
    conn.close()

    return {
        "ok": True,
        "error": None,
        "rows": rows,
    }


def format_tbot_source_rows(source):
    lines = ["🤖 Бот"]

    if not source["ok"]:
        lines.append(f"источник недоступен: {source['error']}")
        return "\n".join(lines)

    if not source["rows"]:
        lines.append("нет записей")
        return "\n".join(lines)

    for row in source["rows"]:
        (
            apartment_number,
            plate_norm,
            plate_raw,
            model_norm,
            model_raw,
            phone_norm,
            status,
        ) = row

        plate = plate_norm or plate_raw or "-"
        model = model_norm or model_raw or "-"
        phone = phone_norm or "-"
        status = status or "-"

        lines.append(f"• {plate} | {model} | {phone} | {status}")

    return "\n".join(lines)


def get_main_db_vehicle_rows_for_compare(apartment_number):
    vehicles = get_apartment_vehicles(apartment_number)
    result = []

    for row in vehicles:
        (
            vehicle_id,
            plate_norm,
            plate_raw,
            model_norm,
            model_raw,
            parking_time,
        ) = row

        result.append({
            "plate": plate_norm or plate_raw or "-",
            "model": model_norm or model_raw or "-",
            "parking_time": parking_time or "NULL",
        })

    return result



# ==========================================================
# Telegram message facts source for agreement
# ==========================================================

PARKING_DAY_WORDS = [
    "day",
    "день",
    "денна",
    "днев",
    "денной",
]

PARKING_NIGHT_WORDS = [
    "night",
    "ніч",
    "ноч",
    "ночная",
    "нічна",
]


def detect_parking_time_from_text(text):
    if not text:
        return None

    lower = str(text).lower()

    if any(word in lower for word in PARKING_NIGHT_WORDS):
        return "Night"

    if any(word in lower for word in PARKING_DAY_WORDS):
        return "Day"

    return None


def get_telegram_facts_by_apartment(apartment_number):
    """
    Читает источник из Telegram-сообщений:
    osbb_telegram.db / telegram_facts + telegram_messages.
    """
    db_file = paths.OSBB_TELEGRAM_DB_FILE

    if not db_file.exists():
        return {
            "ok": False,
            "error": f"telegram_db_not_found: {db_file}",
            "rows": [],
        }

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'telegram_facts'
    """)

    if not cur.fetchone():
        conn.close()
        return {
            "ok": False,
            "error": "table_telegram_facts_not_found",
            "rows": [],
        }

    cur.execute("""
        SELECT
            f.id,
            f.fact_type,
            f.apartment_number,
            f.person_name,
            f.phone_normalized,
            f.license_plate_normalized,
            f.license_plate,
            f.car_brand,
            f.car_model,
            f.car_color_normalized,
            f.amount,
            f.remote_count,
            f.fact_status,
            f.comment,
            m.text_raw,
            m.message_date,
            m.sender_name
        FROM telegram_facts f
        LEFT JOIN telegram_messages m
            ON m.id = f.telegram_message_db_id
        WHERE CAST(f.apartment_number AS TEXT) = ?
        ORDER BY f.id
    """, (str(apartment_number),))

    raw_rows = cur.fetchall()
    conn.close()

    rows = []

    for row in raw_rows:
        (
            fact_id,
            fact_type,
            apt,
            person_name,
            phone_normalized,
            plate_norm,
            plate_raw,
            car_brand,
            car_model,
            car_color,
            amount,
            remote_count,
            fact_status,
            comment,
            text_raw,
            message_date,
            sender_name,
        ) = row

        parking_hint = (
            detect_parking_time_from_text(fact_type)
            or detect_parking_time_from_text(comment)
            or detect_parking_time_from_text(text_raw)
        )

        rows.append({
            "fact_id": fact_id,
            "fact_type": fact_type,
            "apartment_number": apt,
            "person_name": person_name,
            "phone": phone_normalized,
            "plate": plate_norm or plate_raw,
            "model": " ".join(
                x for x in [car_brand, car_model]
                if x
            ) or None,
            "color": car_color,
            "amount": amount,
            "remote_count": remote_count,
            "fact_status": fact_status,
            "comment": comment,
            "text_raw": text_raw,
            "message_date": message_date,
            "sender_name": sender_name,
            "parking_hint": parking_hint,
        })

    return {
        "ok": True,
        "error": None,
        "rows": rows,
    }


def format_telegram_facts_source_rows(source):
    lines = ["💬 Сообщения"]

    if not source["ok"]:
        lines.append(f"источник недоступен: {source['error']}")
        return "\n".join(lines)

    rows = source["rows"]

    if not rows:
        lines.append("нет фактов")
        return "\n".join(lines)

    for item in rows:
        fact_type = item.get("fact_type") or "-"
        plate = item.get("plate")
        model = item.get("model")
        phone = item.get("phone")
        parking_hint = item.get("parking_hint")
        remote_count = item.get("remote_count")
        amount = item.get("amount")

        parts = []

        if plate:
            parts.append(str(plate))

        if model:
            parts.append(str(model))

        if parking_hint:
            parts.append(f"parking: {parking_hint}")

        if phone:
            parts.append(str(phone))

        if remote_count:
            parts.append(f"пультов: {remote_count}")

        if amount:
            parts.append(f"сумма: {amount}")

        if not parts:
            comment = item.get("comment")
            text_raw = item.get("text_raw")
            if comment:
                parts.append(str(comment)[:80])
            elif text_raw:
                parts.append(str(text_raw).replace("\n", " ")[:80])
            else:
                parts.append(fact_type)

        lines.append("• " + " | ".join(parts))

    return "\n".join(lines)


def get_telegram_message_plates(apartment_number):
    source = get_telegram_facts_by_apartment(apartment_number)

    plates = set()

    if not source["ok"]:
        return plates

    for item in source["rows"]:
        plate = item.get("plate")
        if plate:
            plates.add(str(plate).upper().strip())

    return plates


def get_telegram_parking_hints(apartment_number):
    source = get_telegram_facts_by_apartment(apartment_number)

    hints = []

    if not source["ok"]:
        return hints

    for item in source["rows"]:
        if item.get("parking_hint"):
            plate = item.get("plate") or "-"
            hints.append((plate, item["parking_hint"]))

    return hints



# ==========================================================
# Agreement suggestions and cleaned message facts
# ==========================================================

def normalize_source_plate(value):
    if not value:
        return None
    return str(value).upper().replace(" ", "").strip()


def get_clean_telegram_facts_by_apartment(apartment_number):
    source = get_telegram_facts_by_apartment(apartment_number)

    if not source["ok"]:
        return source

    seen = set()
    cleaned = []

    for item in source["rows"]:
        plate = normalize_source_plate(item.get("plate"))
        parking_hint = item.get("parking_hint")

        compact_key = (
            item.get("apartment_number"),
            plate,
            parking_hint,
            item.get("remote_count"),
            item.get("amount"),
            item.get("text_raw"),
        )

        if compact_key in seen:
            continue

        seen.add(compact_key)

        comment = item.get("comment")
        if plate and comment and "no plate found" in str(comment).lower():
            item = dict(item)
            item["comment"] = None

        cleaned.append(item)

    return {
        "ok": True,
        "error": None,
        "rows": cleaned,
    }


def format_telegram_facts_source_rows(source):
    lines = ["💬 Сообщения"]

    if not source["ok"]:
        lines.append(f"источник недоступен: {source['error']}")
        return "\n".join(lines)

    rows = source["rows"]

    if not rows:
        lines.append("нет фактов")
        return "\n".join(lines)

    for item in rows:
        fact_type = item.get("fact_type") or "-"
        plate = item.get("plate")
        model = item.get("model")
        phone = item.get("phone")
        parking_hint = item.get("parking_hint")
        remote_count = item.get("remote_count")
        amount = item.get("amount")
        comment = item.get("comment")
        text_raw = item.get("text_raw")

        parts = []

        if plate:
            parts.append(str(plate))

        if model:
            parts.append(str(model))

        if parking_hint:
            parts.append(f"parking: {parking_hint}")

        if phone:
            parts.append(str(phone))

        if remote_count:
            parts.append(f"пультов: {remote_count}")

        if amount:
            parts.append(f"сумма: {amount}")

        if comment:
            parts.append(str(comment)[:80])
        elif not parts and text_raw:
            parts.append(str(text_raw).replace("\n", " ")[:80])

        if not parts:
            parts.append(fact_type)

        lines.append("• " + " | ".join(parts))

    return "\n".join(lines)


def build_agreement_suggestion(apartment_number):
    tbot = get_tbot_source_rows(apartment_number)
    msg_source = get_clean_telegram_facts_by_apartment(apartment_number)
    db_rows = get_main_db_vehicle_rows_for_compare(apartment_number)

    candidates = {}

    def ensure(plate):
        plate = normalize_source_plate(plate)
        if not plate:
            return None
        if plate not in candidates:
            candidates[plate] = {
                "plate": plate,
                "sources": set(),
                "models": [],
                "parking_hints": [],
                "phones": [],
            }
        return candidates[plate]

    for item in db_rows:
        c = ensure(item.get("plate"))
        if not c:
            continue
        c["sources"].add("БД")
        if item.get("model") and item.get("model") != "-":
            c["models"].append(str(item["model"]))
        if item.get("parking_time") and item.get("parking_time") != "NULL":
            c["parking_hints"].append(str(item["parking_time"]))

    if tbot["ok"]:
        for row in tbot["rows"]:
            c = ensure(row[1] or row[2])
            if not c:
                continue
            c["sources"].add("Бот")
            model = row[3] or row[4]
            if model:
                c["models"].append(str(model))
            if row[5]:
                c["phones"].append(str(row[5]))
            if row[6]:
                c["parking_hints"].append(str(row[6]))

    if msg_source["ok"]:
        for item in msg_source["rows"]:
            c = ensure(item.get("plate"))
            if not c:
                continue
            c["sources"].add("Сообщения")
            if item.get("model"):
                c["models"].append(str(item["model"]))
            if item.get("phone"):
                c["phones"].append(str(item["phone"]))
            if item.get("parking_hint"):
                c["parking_hints"].append(str(item["parking_hint"]))

    def most_common(values):
        clean = [str(v).strip() for v in values if v and str(v).strip() and str(v).strip() != "-"]
        if not clean:
            return None
        counts = {}
        for value in clean:
            counts[value] = counts.get(value, 0) + 1
        return sorted(counts.items(), key=lambda x: (-x[1], x[0]))[0][0]

    suggestions = []

    for plate, data in candidates.items():
        if len(data["sources"]) < 2:
            continue

        suggestions.append({
            "plate": plate,
            "model": most_common(data["models"]),
            "parking_time": most_common(data["parking_hints"]),
            "phone": most_common(data["phones"]),
            "sources": sorted(data["sources"]),
            "confidence": len(data["sources"]),
        })

    suggestions.sort(key=lambda x: (-x["confidence"], x["plate"]))
    return suggestions


def format_agreement_suggestion(apartment_number):
    suggestions = build_agreement_suggestion(apartment_number)

    lines = ["🟢 Предложение"]

    if not suggestions:
        lines.append("пока нет сильного согласованного варианта")
        return "\n".join(lines)

    for item in suggestions:
        parts = [item["plate"]]

        if item.get("model"):
            parts.append(item["model"])

        if item.get("parking_time"):
            parts.append(item["parking_time"])

        if item.get("phone"):
            parts.append(item["phone"])

        lines.append("• " + " | ".join(parts))
        lines.append("  основание: " + " + ".join(item["sources"]))

    return "\n".join(lines)


def get_agreement_compare_summary(apartment_number):
    tbot = get_tbot_source_rows(apartment_number)
    db_rows = get_main_db_vehicle_rows_for_compare(apartment_number)
    msg_source = get_clean_telegram_facts_by_apartment(apartment_number)

    tbot_plates = set()
    if tbot["ok"]:
        for row in tbot["rows"]:
            plate = row[1] or row[2]
            if plate:
                tbot_plates.add(normalize_source_plate(plate))

    db_plates = set()
    for item in db_rows:
        plate = item["plate"]
        if plate and plate != "-":
            db_plates.add(normalize_source_plate(plate))

    message_plates = set()
    if msg_source["ok"]:
        for item in msg_source["rows"]:
            plate = item.get("plate")
            if plate:
                message_plates.add(normalize_source_plate(plate))

    all_external = tbot_plates | message_plates

    only_tbot = sorted(tbot_plates - db_plates)
    only_messages = sorted(message_plates - db_plates)
    only_db = sorted(db_plates - all_external)
    confirmed = sorted(db_plates & all_external)

    lines = ["🔎 Сравнение"]

    if not tbot["ok"]:
        lines.append("бот-анкета недоступна")

    if not msg_source["ok"]:
        lines.append("сообщения недоступны")

    if not tbot_plates and not message_plates and not db_plates:
        lines.append("нет авто ни в источниках, ни в БД")
        return "\n".join(lines)

    lines.append(f"Совпали с БД: {len(confirmed)}")

    if only_tbot:
        lines.append("Только в боте: " + ", ".join(only_tbot))

    if only_messages:
        lines.append("Только в сообщениях: " + ", ".join(only_messages))

    if only_db:
        lines.append("Только в БД: " + ", ".join(only_db))

    parking_hints = []
    if msg_source["ok"]:
        for item in msg_source["rows"]:
            if item.get("parking_hint"):
                plate = normalize_source_plate(item.get("plate")) or "-"
                value = (plate, item["parking_hint"])
                if value not in parking_hints:
                    parking_hints.append(value)

    if parking_hints:
        hints_text = ", ".join(f"{plate}: {hint}" for plate, hint in parking_hints)
        lines.append("Parking из сообщений: " + hints_text)

    if not only_tbot and not only_messages and not only_db:
        lines.append("Номера авто по источникам совпадают.")

    return "\n".join(lines)


def format_apartment_agreement_card(apartment_number):
    card = get_apartment_card(apartment_number)

    if not card:
        return "Квартира не найдена."

    verification = get_apartment_verification_status(apartment_number)

    if verification:
        status = verification[3]
        comment = verification[4]
    else:
        status = "new"
        comment = None

    status_label = VERIFICATION_STATUSES.get(status, status)
    tbot_source = get_tbot_source_rows(apartment_number)
    telegram_facts_source = get_clean_telegram_facts_by_apartment(apartment_number)

    lines = []
    lines.append(f"🏠 Квартира {card['apartment_number']}")
    lines.append(f"Статус: {status_label}")

    if comment:
        lines.append(f"Комментарий: {comment}")

    lines.append("")
    lines.append(format_tbot_source_rows(tbot_source))

    lines.append("")
    lines.append(format_telegram_facts_source_rows(telegram_facts_source))

    lines.append("")
    lines.append("💾 БД")
    lines.append("Авто:")
    lines.append(format_vehicle_list(card["vehicles"]))

    lines.append("")
    lines.append(get_agreement_compare_summary(apartment_number))

    lines.append("")
    lines.append(format_agreement_suggestion(apartment_number))

    lines.append("")
    lines.append("👥 Жильцы:")
    if card["residents"]:
        for first_name, last_name, username, resident_status in card["residents"]:
            name = " ".join(x for x in [first_name, last_name] if x) or "-"
            username = f"@{username}" if username else "-"
            lines.append(f"• {name} | {username} | {resident_status or '-'}")
    else:
        lines.append("нет пользователей")

    return "\n".join(lines)


def set_apartment_verification_in_progress(apartment_number, verified_by=None):
    current = get_apartment_verification_status(apartment_number)

    if current and current[3] != "new":
        return True, current[3]

    return set_apartment_verification_status(
        apartment_number,
        "in_progress",
        verified_by=verified_by,
    )


# ==========================================================
# Agreement dashboard
# ==========================================================

def is_composite_apartment(value):
    if not value:
        return False

    value = str(value).strip()

    return bool(
        re.match(r"^\d+\s*[._/,]\s*\d+$", value)
    )


def normalize_composite_apartment(value):
    apt = str(value).strip()

    special_cases = {
        "89.9": "89_90",
    }

    if apt in special_cases:
        return special_cases[apt]

    return re.sub(r"[./,]", "_", apt)


def composite_parts(apartment_number):
    apt = str(apartment_number).strip()

    special_cases = {
        "89.9": ["89", "90"],
    }

    if apt in special_cases:
        return special_cases[apt]

    return [
        part.strip()
        for part in re.split(r"[._/,]", apt)
        if part.strip()
    ]


def composite_exists_in_db(apartment_number, main_apartments):
    parts = composite_parts(apartment_number)

    if not parts:
        return False

    return all(
        part in main_apartments
        for part in parts
    )


def sort_tuple_set(values):
    return sorted(
        values,
        key=lambda x: tuple("" if v is None else str(v) for v in x)
    )


def _safe_table_exists(db_file, table_name):
    if not db_file.exists():
        return False

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
    """, (table_name,))

    exists = cur.fetchone() is not None
    conn.close()
    return exists


def _get_main_apartment_numbers():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT apartment_number
        FROM apartments
    """)

    result = {
        str(row[0]).strip()
        for row in cur.fetchall()
        if row[0] is not None and str(row[0]).strip()
    }

    conn.close()
    return result


def _get_main_vehicle_plates():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            COALESCE(license_plate_normalized, license_plate)
        FROM vehicles
        WHERE COALESCE(license_plate_normalized, license_plate) IS NOT NULL
          AND TRIM(COALESCE(license_plate_normalized, license_plate)) <> ''
    """)

    result = {
        normalize_source_plate(row[0])
        for row in cur.fetchall()
        if normalize_source_plate(row[0])
    }

    conn.close()
    return result


def _get_tbot_dashboard_rows():
    db_file = paths.OSBB_QUARANTINE_DB_FILE

    if not _safe_table_exists(db_file, "tbot_parking_import"):
        return {
            "ok": False,
            "error": "tbot_parking_import_not_found",
            "rows": [],
        }

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(tbot_parking_import)")
    columns = [row[1] for row in cur.fetchall()]

    def pick(*names):
        for name in names:
            if name in columns:
                return name
        return None

    apartment_col = pick("apartment_number", "Номер квартири", "Номер квартиры", "Квартира")
    plate_norm_col = pick("license_plate_normalized")
    plate_raw_col = pick("license_plate", "Номер Авто", "Номер авто", "plate")
    model_norm_col = pick("car_model_normalized")
    model_raw_col = pick("car_model", "Марка авто", "Марка", "model")
    phone_col = pick("phone_normalized", "phone_number", "Телефон", "phone")

    if not apartment_col:
        conn.close()
        return {
            "ok": False,
            "error": "apartment_column_not_found",
            "rows": [],
        }

    select_parts = [
        f"{apartment_col} AS apartment_number",
        f"{plate_norm_col} AS license_plate_normalized" if plate_norm_col else "NULL AS license_plate_normalized",
        f"{plate_raw_col} AS license_plate" if plate_raw_col else "NULL AS license_plate",
        f"{model_norm_col} AS car_model_normalized" if model_norm_col else "NULL AS car_model_normalized",
        f"{model_raw_col} AS car_model" if model_raw_col else "NULL AS car_model",
        f"{phone_col} AS phone" if phone_col else "NULL AS phone",
    ]

    cur.execute(f"""
        SELECT {", ".join(select_parts)}
        FROM tbot_parking_import
        ORDER BY rowid
    """)

    rows = []
    for row in cur.fetchall():
        apt, plate_norm, plate_raw, model_norm, model_raw, phone = row
        rows.append({
            "apartment_number": str(apt).strip() if apt is not None else None,
            "plate": normalize_source_plate(plate_norm or plate_raw),
            "model": model_norm or model_raw,
            "phone": phone,
        })

    conn.close()

    return {
        "ok": True,
        "error": None,
        "rows": rows,
    }


def _get_telegram_fact_dashboard_rows():
    db_file = paths.OSBB_TELEGRAM_DB_FILE

    if not _safe_table_exists(db_file, "telegram_facts"):
        return {
            "ok": False,
            "error": "telegram_facts_not_found",
            "rows": [],
        }

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("""
        SELECT
            f.apartment_number,
            f.license_plate_normalized,
            f.license_plate,
            f.car_brand,
            f.car_model,
            f.comment,
            m.text_raw
        FROM telegram_facts f
        LEFT JOIN telegram_messages m
            ON m.id = f.telegram_message_db_id
        ORDER BY f.id
    """)

    rows = []

    for row in cur.fetchall():
        apt, plate_norm, plate_raw, car_brand, car_model, comment, text_raw = row
        plate = normalize_source_plate(plate_norm or plate_raw)

        rows.append({
            "apartment_number": str(apt).strip() if apt is not None else None,
            "plate": plate,
            "model": " ".join(x for x in [car_brand, car_model] if x) or None,
            "comment": comment,
            "text_raw": text_raw,
            "parking_hint": (
                detect_parking_time_from_text(comment)
                or detect_parking_time_from_text(text_raw)
            ),
        })

    conn.close()

    return {
        "ok": True,
        "error": None,
        "rows": rows,
    }


def _get_ambiguous_telegram_messages(limit=20):
    db_file = paths.OSBB_TELEGRAM_DB_FILE

    if not _safe_table_exists(db_file, "telegram_messages"):
        return {
            "ok": False,
            "error": "telegram_messages_not_found",
            "count": 0,
            "examples": [],
        }

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            telegram_message_id,
            text_raw
        FROM telegram_messages
        WHERE text_raw IS NOT NULL
          AND TRIM(text_raw) <> ''
    """)

    ambiguous = []

    # грубая, но безопасная эвристика:
    # в одном сообщении есть "кв" и "п/под/подъезд", и минимум два числа.
    apt_word_re = re.compile(r"\bкв\.?\b|квартира", re.IGNORECASE)
    entrance_word_re = re.compile(r"\bп\.?\b|под\.?|подъезд|під'?їзд", re.IGNORECASE)
    number_re = re.compile(r"\d+")

    for msg_db_id, msg_id, text in cur.fetchall():
        if not text:
            continue

        numbers = number_re.findall(text)

        if (
            len(numbers) >= 2
            and apt_word_re.search(text)
            and entrance_word_re.search(text)
        ):
            ambiguous.append({
                "message_db_id": msg_db_id,
                "telegram_message_id": msg_id,
                "text": str(text).replace("\n", " ")[:160],
            })

    conn.close()

    return {
        "ok": True,
        "error": None,
        "count": len(ambiguous),
        "examples": ambiguous[:limit],
    }


def get_agreement_dashboard():
    main_apartments = _get_main_apartment_numbers()
    main_plates = _get_main_vehicle_plates()

    tbot = _get_tbot_dashboard_rows()
    telegram = _get_telegram_fact_dashboard_rows()
    ambiguous = _get_ambiguous_telegram_messages(limit=10)
    verification = get_verification_stats()

    dashboard = {
        "verification": verification,
        "tbot": {
            "ok": tbot["ok"],
            "error": tbot["error"],
            "apartments_not_in_db": [],
            "plates_not_in_db": [],
            "composite_apartments": [],
        },
        "telegram": {
            "ok": telegram["ok"],
            "error": telegram["error"],
            "plates_not_in_db": [],
            "parking_hints": [],
        },
        "ambiguous_messages": ambiguous,
    }

    if tbot["ok"]:
        seen_apts = set()
        seen_plates = set()
        composite_apartments = set()

        for item in tbot["rows"]:
            apt = item.get("apartment_number")
            plate = item.get("plate")

            if apt:
                if is_composite_apartment(apt):
                    if composite_exists_in_db(apt, main_apartments):
                        composite_apartments.add(
                            normalize_composite_apartment(apt)
                        )
                    else:
                        seen_apts.add(apt)

                elif apt not in main_apartments:
                    seen_apts.add(apt)

            if plate and plate not in main_plates:
                display_apt = (
                    normalize_composite_apartment(apt)
                    if apt and is_composite_apartment(apt)
                    else (apt or "-")
                )
                seen_plates.add(
                    (
                        display_apt,
                        plate,
                        item.get("model"),
                        item.get("phone"),
                    )
                )

        dashboard["tbot"]["apartments_not_in_db"] = sorted(seen_apts)
        dashboard["tbot"]["plates_not_in_db"] = sort_tuple_set(seen_plates)
        dashboard["tbot"]["composite_apartments"] = sorted(composite_apartments)

    if telegram["ok"]:
        seen_plates = set()
        seen_hints = set()

        for item in telegram["rows"]:
            apt = item.get("apartment_number") or "-"
            plate = item.get("plate")

            if plate and plate not in main_plates:
                seen_plates.add((apt, plate, item.get("model")))

            if item.get("parking_hint"):
                seen_hints.add((apt, plate or "-", item["parking_hint"]))

        dashboard["telegram"]["plates_not_in_db"] = sort_tuple_set(seen_plates)
        dashboard["telegram"]["parking_hints"] = sort_tuple_set(seen_hints)

    return dashboard


def _format_examples(rows, limit=8):
    if not rows:
        return []

    lines = []
    for row in rows[:limit]:
        if isinstance(row, tuple):
            lines.append("  • " + " | ".join(str(x) for x in row if x))
        else:
            lines.append("  • " + str(row))

    if len(rows) > limit:
        lines.append(f"  … ещё {len(rows) - limit}")

    return lines


def format_agreement_dashboard(data):
    lines = []

    lines.append("📌 Сводка дня")
    lines.append("")

    verification = data["verification"]
    lines.append("🤝 Согласование")
    lines.append(f"• новых квартир: {verification.get('new', 0)}")
    lines.append(f"• согласовано: {verification.get('confirmed', 0)}")
    lines.append(f"• отложено: {verification.get('deferred', 0)}")
    lines.append(f"• конфликтов: {verification.get('conflict', 0)}")
    lines.append("")

    lines.append("🤖 Бот-анкета")
    if not data["tbot"]["ok"]:
        lines.append(f"• источник недоступен: {data['tbot']['error']}")
    else:
        apts = data["tbot"]["apartments_not_in_db"]
        plates = data["tbot"]["plates_not_in_db"]
        composite = data["tbot"].get("composite_apartments", [])

        lines.append(f"• квартир есть в боте, нет в БД: {len(apts)}")
        lines.append(f"• составных квартир: {len(composite)}")
        lines.append(f"• авто есть в боте, нет в БД: {len(plates)}")

        if composite:
            lines.append("• составные квартиры:")
            for apt in composite[:10]:
                lines.append(f"  • {apt}")

        if plates:
            lines.append("• первые авто к разбору:")
            lines.extend(_format_examples(plates, limit=5))
    lines.append("")

    lines.append("💬 Сообщения")
    if not data["telegram"]["ok"]:
        lines.append(f"• источник недоступен: {data['telegram']['error']}")
    else:
        plates = data["telegram"]["plates_not_in_db"]
        hints = data["telegram"]["parking_hints"]

        lines.append(f"• номеров из сообщений нет в БД: {len(plates)}")
        lines.append(f"• найден parking_time в сообщениях: {len(hints)}")

        if plates:
            lines.append("• первые номера к разбору:")
            lines.extend(_format_examples(plates, limit=5))
    lines.append("")

    lines.append("⚠️ Проверить вручную")
    amb = data["ambiguous_messages"]

    if not amb["ok"]:
        lines.append(f"• источник недоступен: {amb['error']}")
    else:
        lines.append(f"• неоднозначных сообщений: {amb['count']}")

        for item in amb["examples"][:3]:
            lines.append(f"  • msg {item['telegram_message_id']}: {item['text']}")

        if amb["count"] > 3:
            lines.append(f"  … ещё {amb['count'] - 3}")

    return "\n".join(lines)


# ==========================================================
# Agreement V2: stricter telegram fact cleaning, similar plates, accept action
# ==========================================================

def get_clean_telegram_facts_by_apartment(apartment_number):
    """
    Чистая версия фактов из сообщений для карточки согласования:
    - убирает дубли;
    - полностью скрывает строки "no plate found" без номера;
    - скрывает misleading comment "no plate found", если номер найден;
    - не использует такие технические строки для предложения.
    """
    source = get_telegram_facts_by_apartment(apartment_number)

    if not source["ok"]:
        return source

    seen = set()
    cleaned = []

    for item in source["rows"]:
        plate = normalize_source_plate(item.get("plate"))
        parking_hint = item.get("parking_hint")
        comment = item.get("comment")
        text_raw = item.get("text_raw")

        comment_lower = str(comment or "").lower()
        text_lower = str(text_raw or "").lower()

        # Технический мусор экстрактора: "no plate found" без номера.
        # В карточке квартиры он только сбивает оператора.
        if not plate and (
            "no plate found" in comment_lower
            or "no plate found" in text_lower
        ):
            continue

        if plate and comment and "no plate found" in comment_lower:
            item = dict(item)
            item["comment"] = None

        compact_key = (
            item.get("apartment_number"),
            plate,
            parking_hint,
            item.get("remote_count"),
            item.get("amount"),
            normalize_source_plate(item.get("phone")),
            str(item.get("text_raw") or "").strip(),
        )

        if compact_key in seen:
            continue

        seen.add(compact_key)
        cleaned.append(item)

    return {
        "ok": True,
        "error": None,
        "rows": cleaned,
    }


def plate_similarity(a, b):
    a = normalize_source_plate(a)
    b = normalize_source_plate(b)

    if not a or not b:
        return 0.0

    # простая Levenshtein distance без внешних библиотек
    if a == b:
        return 1.0

    m = len(a)
    n = len(b)

    if not m or not n:
        return 0.0

    dp = list(range(n + 1))

    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i

        for j in range(1, n + 1):
            old = dp[j]
            cost = 0 if a[i - 1] == b[j - 1] else 1

            dp[j] = min(
                dp[j] + 1,
                dp[j - 1] + 1,
                prev + cost,
            )
            prev = old

    distance = dp[n]
    return 1 - distance / max(m, n)


def get_similar_plate_pairs(apartment_number, threshold=0.72):
    """
    Ищет похожие, но не одинаковые номера между внешними источниками и БД.
    Например AA8098MM ↔ AA8008M.
    """
    tbot = get_tbot_source_rows(apartment_number)
    msg_source = get_clean_telegram_facts_by_apartment(apartment_number)
    db_rows = get_main_db_vehicle_rows_for_compare(apartment_number)

    external = set()
    db_plates = set()

    if tbot["ok"]:
        for row in tbot["rows"]:
            plate = normalize_source_plate(row[1] or row[2])
            if plate:
                external.add(plate)

    if msg_source["ok"]:
        for item in msg_source["rows"]:
            plate = normalize_source_plate(item.get("plate"))
            if plate:
                external.add(plate)

    for item in db_rows:
        plate = normalize_source_plate(item.get("plate"))
        if plate:
            db_plates.add(plate)

    pairs = []

    for ext_plate in external:
        for db_plate in db_plates:
            if ext_plate == db_plate:
                continue

            score = plate_similarity(ext_plate, db_plate)

            if score >= threshold:
                pairs.append((ext_plate, db_plate, round(score, 2)))

    pairs.sort(key=lambda x: (-x[2], x[0], x[1]))
    return pairs


def get_top_agreement_suggestion(apartment_number):
    suggestions = build_agreement_suggestion(apartment_number)

    if not suggestions:
        return None

    return suggestions[0]


def find_vehicle_id_by_plate_in_apartment(apartment_number, plate):
    plate = normalize_source_plate(plate)

    if not plate:
        return None

    vehicles = get_apartment_vehicles(apartment_number)

    for row in vehicles:
        (
            vehicle_id,
            plate_norm,
            plate_raw,
            model_norm,
            model_raw,
            parking_time,
        ) = row

        current = normalize_source_plate(plate_norm or plate_raw)

        if current == plate:
            return vehicle_id

    return None


def create_vehicle_for_apartment(apartment_number, plate, model=None, parking_time=None):
    apt = find_apartment(apartment_number)

    if not apt:
        return False, "apartment_not_found"

    apartment_id = apt[0]
    plate = normalize_source_plate(plate)
    model = str(model).strip().upper() if model else None

    if not plate:
        return False, "empty_plate"

    if parking_time not in ["Day", "Night", "Inactive"]:
        parking_time = None

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO vehicles (
            apartment_id,
            license_plate,
            license_plate_normalized,
            car_model,
            car_model_normalized,
            parking_time
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        apartment_id,
        plate,
        plate,
        model,
        model,
        parking_time,
    ))

    vehicle_id = cur.lastrowid
    conn.commit()
    conn.close()

    return True, {
        "vehicle_id": vehicle_id,
        "action": "created",
        "plate": plate,
        "model": model,
        "parking_time": parking_time,
    }


def update_vehicle_from_suggestion(vehicle_id, suggestion):
    model = suggestion.get("model")
    parking_time = suggestion.get("parking_time")

    if parking_time not in ["Day", "Night", "Inactive"]:
        parking_time = None

    model = str(model).strip().upper() if model else None

    conn = get_conn()
    cur = conn.cursor()

    if model and parking_time:
        cur.execute("""
            UPDATE vehicles
            SET
                car_model = COALESCE(car_model, ?),
                car_model_normalized = COALESCE(car_model_normalized, ?),
                parking_time = COALESCE(parking_time, ?)
            WHERE id = ?
        """, (model, model, parking_time, int(vehicle_id)))
    elif model:
        cur.execute("""
            UPDATE vehicles
            SET
                car_model = COALESCE(car_model, ?),
                car_model_normalized = COALESCE(car_model_normalized, ?)
            WHERE id = ?
        """, (model, model, int(vehicle_id)))
    elif parking_time:
        cur.execute("""
            UPDATE vehicles
            SET parking_time = COALESCE(parking_time, ?)
            WHERE id = ?
        """, (parking_time, int(vehicle_id)))
    else:
        conn.close()
        return True, {
            "vehicle_id": int(vehicle_id),
            "action": "nothing_to_update",
        }

    changed = cur.rowcount
    conn.commit()
    conn.close()

    return True, {
        "vehicle_id": int(vehicle_id),
        "action": "updated",
        "changed": changed,
        "model": model,
        "parking_time": parking_time,
    }


def apply_agreement_suggestion(apartment_number, verified_by=None):
    """
    Автодействие по верхнему согласованному предложению:
    - если авто с таким номером уже есть в квартире — дозаполняет модель/parking_time;
    - если нет — создаёт авто;
    - затем ставит apartment_verification = confirmed.

    Если сильного предложения нет, только подтверждает квартиру.
    """
    suggestion = get_top_agreement_suggestion(apartment_number)

    applied = None

    if suggestion:
        vehicle_id = find_vehicle_id_by_plate_in_apartment(
            apartment_number,
            suggestion["plate"],
        )

        if vehicle_id:
            ok, applied = update_vehicle_from_suggestion(vehicle_id, suggestion)
            if not ok:
                return False, applied
        else:
            ok, applied = create_vehicle_for_apartment(
                apartment_number,
                suggestion["plate"],
                model=suggestion.get("model"),
                parking_time=suggestion.get("parking_time"),
            )
            if not ok:
                return False, applied

    ok, status = set_apartment_verification_status(
        apartment_number,
        "confirmed",
        verified_by=verified_by,
    )

    if not ok:
        return False, status

    return True, {
        "status": "confirmed",
        "suggestion": suggestion,
        "applied": applied,
    }


def get_agreement_compare_summary(apartment_number):
    tbot = get_tbot_source_rows(apartment_number)
    db_rows = get_main_db_vehicle_rows_for_compare(apartment_number)
    msg_source = get_clean_telegram_facts_by_apartment(apartment_number)

    tbot_plates = set()
    if tbot["ok"]:
        for row in tbot["rows"]:
            plate = row[1] or row[2]
            if plate:
                tbot_plates.add(normalize_source_plate(plate))

    db_plates = set()
    for item in db_rows:
        plate = item["plate"]
        if plate and plate != "-":
            db_plates.add(normalize_source_plate(plate))

    message_plates = set()
    if msg_source["ok"]:
        for item in msg_source["rows"]:
            plate = item.get("plate")
            if plate:
                message_plates.add(normalize_source_plate(plate))

    all_external = tbot_plates | message_plates

    only_tbot = sorted(tbot_plates - db_plates)
    only_messages = sorted(message_plates - db_plates)
    only_db = sorted(db_plates - all_external)
    confirmed = sorted(db_plates & all_external)
    similar = get_similar_plate_pairs(apartment_number)

    lines = ["🔎 Сравнение"]

    if not tbot["ok"]:
        lines.append("бот-анкета недоступна")

    if not msg_source["ok"]:
        lines.append("сообщения недоступны")

    if not tbot_plates and not message_plates and not db_plates:
        lines.append("нет авто ни в источниках, ни в БД")
        return "\n".join(lines)

    lines.append(f"Совпали с БД: {len(confirmed)}")

    if only_tbot:
        lines.append("Только в боте: " + ", ".join(only_tbot))

    if only_messages:
        lines.append("Только в сообщениях: " + ", ".join(only_messages))

    if only_db:
        lines.append("Только в БД: " + ", ".join(only_db))

    if similar:
        lines.append("🔶 Возможная опечатка:")
        for ext_plate, db_plate, score in similar[:5]:
            lines.append(f"• {ext_plate} ↔ {db_plate} ({score})")

    parking_hints = []
    if msg_source["ok"]:
        for item in msg_source["rows"]:
            if item.get("parking_hint"):
                plate = normalize_source_plate(item.get("plate")) or "-"
                value = (plate, item["parking_hint"])
                if value not in parking_hints:
                    parking_hints.append(value)

    if parking_hints:
        hints_text = ", ".join(
            f"{plate}: {hint}" for plate, hint in parking_hints
        )
        lines.append("Parking из сообщений: " + hints_text)

    if not only_tbot and not only_messages and not only_db:
        lines.append("Номера авто по источникам совпадают.")

    return "\n".join(lines)


# ==========================================================
# Agreement V3: composite apartment lookup/display
# ==========================================================

def _normalize_apartment_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _normalize_composite_display(value):
    apt = _normalize_apartment_text(value)

    special_cases = {
        "89.9": "89_90",
        "89.90": "89_90",
    }

    if apt in special_cases:
        return special_cases[apt]

    return re.sub(r"[./,]", "_", apt)


def _composite_parts_from_ref(value):
    apt = _normalize_apartment_text(value)

    special_cases = {
        "89.9": ["89", "90"],
        "89.90": ["89", "90"],
    }

    if apt in special_cases:
        return special_cases[apt]

    parts = [
        part.strip()
        for part in re.split(r"[._/,]", apt)
        if part.strip()
    ]

    return parts


def _is_composite_ref(value):
    apt = _normalize_apartment_text(value)
    return bool(re.match(r"^\d+\s*[._/,]\s*\d+$", apt))


def _get_tbot_composite_refs():
    """
    Ищет составные квартиры в tbot_parking_import.
    Возвращает список:
    {
        raw: '105.106',
        display: '105_106',
        parts: ['105', '106']
    }
    """
    tbot = _get_tbot_dashboard_rows()

    if not tbot.get("ok"):
        return []

    result = {}
    for item in tbot["rows"]:
        apt = _normalize_apartment_text(item.get("apartment_number"))

        if not apt or not _is_composite_ref(apt):
            continue

        display = _normalize_composite_display(apt)
        parts = _composite_parts_from_ref(apt)

        if not parts:
            continue

        result[display] = {
            "raw": apt,
            "display": display,
            "parts": parts,
        }

    return sorted(result.values(), key=lambda x: x["display"])


def resolve_agreement_apartment_ref(apartment_number):
    """
    Единая точка для согласования:
    - 106 -> 105_106, если в бот-базе есть 105.106;
    - 105 -> 105_106;
    - 105_106 -> 105_106;
    - 105.106 -> 105_106;
    - обычная квартира -> сама себя.
    """
    requested = _normalize_apartment_text(apartment_number)
    requested_norm = _normalize_composite_display(requested)

    for item in _get_tbot_composite_refs():
        raw = item["raw"]
        display = item["display"]
        parts = item["parts"]

        if (
            requested == raw
            or requested_norm == display
            or requested in parts
        ):
            return {
                "requested": requested,
                "display": display,
                "lookup_numbers": parts,
                "tbot_refs": [raw],
                "is_composite": True,
            }

    return {
        "requested": requested,
        "display": requested,
        "lookup_numbers": [requested],
        "tbot_refs": [requested],
        "is_composite": False,
    }


def find_apartment(apartment_number):
    resolved = resolve_agreement_apartment_ref(apartment_number)

    # Для составной квартиры возвращаем первую существующую часть.
    # Это сохраняет совместимость с кодом, который ожидает одну строку apartments.
    candidates = resolved["lookup_numbers"]

    conn = get_conn()
    cur = conn.cursor()

    for apt_number in candidates:
        cur.execute("""
            SELECT
                id,
                apartment_number,
                entrance
            FROM apartments
            WHERE apartment_number = ?
        """, (str(apt_number),))

        row = cur.fetchone()

        if row:
            conn.close()
            return row

    conn.close()
    return None


def get_apartment_vehicles(apartment_number):
    resolved = resolve_agreement_apartment_ref(apartment_number)
    lookup_numbers = resolved["lookup_numbers"]

    if not lookup_numbers:
        return []

    placeholders = ",".join("?" for _ in lookup_numbers)

    conn = get_conn()
    cur = conn.cursor()

    cur.execute(f"""
        SELECT id
        FROM apartments
        WHERE apartment_number IN ({placeholders})
        ORDER BY
            CASE
                WHEN apartment_number GLOB '[0-9]*'
                THEN CAST(apartment_number AS INTEGER)
                ELSE 999999
            END,
            apartment_number
    """, tuple(lookup_numbers))

    apt_rows = cur.fetchall()

    if not apt_rows:
        conn.close()
        return []

    apartment_ids = [row[0] for row in apt_rows]
    placeholders = ",".join("?" for _ in apartment_ids)

    cur.execute(f"""
        SELECT
            id,
            license_plate_normalized,
            license_plate,
            car_model_normalized,
            car_model,
            parking_time
        FROM vehicles
        WHERE apartment_id IN ({placeholders})
        ORDER BY id
    """, tuple(apartment_ids))

    rows = cur.fetchall()
    conn.close()

    return rows


def get_apartment_card(apartment_number):
    resolved = resolve_agreement_apartment_ref(apartment_number)
    lookup_numbers = resolved["lookup_numbers"]
    display_number = resolved["display"]

    if not lookup_numbers:
        return None

    placeholders = ",".join("?" for _ in lookup_numbers)

    conn = get_conn()
    cur = conn.cursor()

    cur.execute(f"""
        SELECT
            id,
            apartment_number,
            entrance
        FROM apartments
        WHERE apartment_number IN ({placeholders})
        ORDER BY
            CASE
                WHEN apartment_number GLOB '[0-9]*'
                THEN CAST(apartment_number AS INTEGER)
                ELSE 999999
            END,
            apartment_number
    """, tuple(lookup_numbers))

    apartments = cur.fetchall()

    if not apartments:
        conn.close()
        return None

    cur.execute(f"""
        SELECT
            telegram_first_name,
            telegram_last_name,
            telegram_username,
            status
        FROM resident_accounts
        WHERE apartment_number IN ({placeholders})
        ORDER BY telegram_user_id
    """, tuple(lookup_numbers))

    residents = cur.fetchall()
    conn.close()

    vehicles = get_apartment_vehicles(apartment_number)

    return {
        "apartment": apartments[0],
        "apartment_number": display_number,
        "lookup_numbers": lookup_numbers,
        "is_composite": resolved["is_composite"],
        "residents": residents,
        "vehicles": vehicles,
    }


def get_tbot_source_rows(apartment_number):
    db_file = paths.OSBB_QUARANTINE_DB_FILE

    if not db_file.exists():
        return {
            "ok": False,
            "error": f"quarantine_db_not_found: {db_file}",
            "rows": [],
        }

    resolved = resolve_agreement_apartment_ref(apartment_number)
    search_refs = list(dict.fromkeys(
        resolved["tbot_refs"]
        + resolved["lookup_numbers"]
        + [resolved["display"], resolved["requested"]]
    ))

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'tbot_parking_import'
    """)

    if not cur.fetchone():
        conn.close()
        return {
            "ok": False,
            "error": "table_tbot_parking_import_not_found",
            "rows": [],
        }

    cur.execute("PRAGMA table_info(tbot_parking_import)")
    columns = [row[1] for row in cur.fetchall()]

    def pick(*names):
        for name in names:
            if name in columns:
                return name
        return None

    apartment_col = pick("apartment_number", "Номер квартири", "Номер квартиры", "Квартира")
    plate_norm_col = pick("license_plate_normalized")
    plate_raw_col = pick("license_plate", "Номер Авто", "Номер авто", "plate")
    model_norm_col = pick("car_model_normalized")
    model_raw_col = pick("car_model", "Марка авто", "Марка", "model")
    phone_col = pick("phone_normalized", "phone_number", "Телефон", "phone")
    status_col = pick("status", "Статус", "parking_time", "Тариф")

    if not apartment_col:
        conn.close()
        return {
            "ok": False,
            "error": "apartment_column_not_found",
            "rows": [],
        }

    select_parts = [
        f"{apartment_col} AS apartment_number",
        f"{plate_norm_col} AS license_plate_normalized" if plate_norm_col else "NULL AS license_plate_normalized",
        f"{plate_raw_col} AS license_plate" if plate_raw_col else "NULL AS license_plate",
        f"{model_norm_col} AS car_model_normalized" if model_norm_col else "NULL AS car_model_normalized",
        f"{model_raw_col} AS car_model" if model_raw_col else "NULL AS car_model",
        f"{phone_col} AS phone_normalized" if phone_col else "NULL AS phone_normalized",
        f"{status_col} AS status" if status_col else "NULL AS status",
    ]

    placeholders = ",".join("?" for _ in search_refs)

    cur.execute(f"""
        SELECT
            {", ".join(select_parts)}
        FROM tbot_parking_import
        WHERE CAST({apartment_col} AS TEXT) IN ({placeholders})
        ORDER BY rowid
    """, tuple(search_refs))

    rows = cur.fetchall()
    conn.close()

    return {
        "ok": True,
        "error": None,
        "rows": rows,
    }


def get_telegram_facts_by_apartment(apartment_number):
    db_file = paths.OSBB_TELEGRAM_DB_FILE

    if not db_file.exists():
        return {
            "ok": False,
            "error": f"telegram_db_not_found: {db_file}",
            "rows": [],
        }

    resolved = resolve_agreement_apartment_ref(apartment_number)
    search_refs = list(dict.fromkeys(
        resolved["lookup_numbers"]
        + [resolved["display"], resolved["requested"]]
    ))

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'telegram_facts'
    """)

    if not cur.fetchone():
        conn.close()
        return {
            "ok": False,
            "error": "table_telegram_facts_not_found",
            "rows": [],
        }

    placeholders = ",".join("?" for _ in search_refs)

    cur.execute(f"""
        SELECT
            f.id,
            f.fact_type,
            f.apartment_number,
            f.person_name,
            f.phone_normalized,
            f.license_plate_normalized,
            f.license_plate,
            f.car_brand,
            f.car_model,
            f.car_color_normalized,
            f.amount,
            f.remote_count,
            f.fact_status,
            f.comment,
            m.text_raw,
            m.message_date,
            m.sender_name
        FROM telegram_facts f
        LEFT JOIN telegram_messages m
            ON m.id = f.telegram_message_db_id
        WHERE CAST(f.apartment_number AS TEXT) IN ({placeholders})
        ORDER BY f.id
    """, tuple(search_refs))

    raw_rows = cur.fetchall()
    conn.close()

    rows = []

    for row in raw_rows:
        (
            fact_id,
            fact_type,
            apt,
            person_name,
            phone_normalized,
            plate_norm,
            plate_raw,
            car_brand,
            car_model,
            car_color,
            amount,
            remote_count,
            fact_status,
            comment,
            text_raw,
            message_date,
            sender_name,
        ) = row

        parking_hint = (
            detect_parking_time_from_text(fact_type)
            or detect_parking_time_from_text(comment)
            or detect_parking_time_from_text(text_raw)
        )

        rows.append({
            "fact_id": fact_id,
            "fact_type": fact_type,
            "apartment_number": apt,
            "person_name": person_name,
            "phone": phone_normalized,
            "plate": plate_norm or plate_raw,
            "model": " ".join(
                x for x in [car_brand, car_model]
                if x
            ) or None,
            "color": car_color,
            "amount": amount,
            "remote_count": remote_count,
            "fact_status": fact_status,
            "comment": comment,
            "text_raw": text_raw,
            "message_date": message_date,
            "sender_name": sender_name,
            "parking_hint": parking_hint,
        })

    return {
        "ok": True,
        "error": None,
        "rows": rows,
    }


def get_main_db_vehicle_rows_for_compare(apartment_number):
    vehicles = get_apartment_vehicles(apartment_number)

    result = []

    for row in vehicles:
        (
            vehicle_id,
            plate_norm,
            plate_raw,
            model_norm,
            model_raw,
            parking_time,
        ) = row

        result.append({
            "plate": plate_norm or plate_raw or "-",
            "model": model_norm or model_raw or "-",
            "parking_time": parking_time or "NULL",
        })

    return result


def format_apartment_agreement_card(apartment_number):
    resolved = resolve_agreement_apartment_ref(apartment_number)
    card = get_apartment_card(apartment_number)

    if not card:
        return "Квартира не найдена."

    # Статус берём по первому реальному номеру квартиры.
    # Для составной карточки это компромисс без перестройки схемы БД.
    status_lookup_number = (
        resolved["lookup_numbers"][0]
        if resolved["lookup_numbers"]
        else apartment_number
    )

    verification = get_apartment_verification_status(status_lookup_number)

    if verification:
        status = verification[3]
        comment = verification[4]
    else:
        status = "new"
        comment = None

    status_label = VERIFICATION_STATUSES.get(status, status)
    tbot_source = get_tbot_source_rows(apartment_number)
    telegram_facts_source = get_clean_telegram_facts_by_apartment(apartment_number)

    lines = []
    lines.append(f"🏠 Квартира {card['apartment_number']}")

    if card.get("is_composite"):
        lines.append("Связка: " + ", ".join(card.get("lookup_numbers", [])))

    lines.append(f"Статус: {status_label}")

    if comment:
        lines.append(f"Комментарий: {comment}")

    lines.append("")
    lines.append(format_tbot_source_rows(tbot_source))

    lines.append("")
    lines.append(format_telegram_facts_source_rows(telegram_facts_source))

    lines.append("")
    lines.append("💾 БД")
    lines.append("Авто:")
    lines.append(format_vehicle_list(card["vehicles"]))

    lines.append("")
    lines.append(get_agreement_compare_summary(apartment_number))

    lines.append("")
    lines.append(format_agreement_suggestion(apartment_number))

    lines.append("")
    lines.append("👥 Жильцы:")
    if card["residents"]:
        for first_name, last_name, username, resident_status in card["residents"]:
            name = " ".join(x for x in [first_name, last_name] if x) or "-"
            username = f"@{username}" if username else "-"
            lines.append(f"• {name} | {username} | {resident_status or '-'}")
    else:
        lines.append("нет пользователей")

    return "\n".join(lines)


# ==========================================================
# Tariff review mode: fill NULL parking_time
# ==========================================================

def _is_empty_tariff(value):
    if value is None:
        return True

    text = str(value).strip()

    return text == "" or text.upper() == "NULL"


def get_tariff_review_stats():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM vehicles")
    total = cur.fetchone()[0]

    cur.execute("""
        SELECT
            COALESCE(NULLIF(TRIM(parking_time), ''), 'NULL') AS status,
            COUNT(*)
        FROM vehicles
        GROUP BY COALESCE(NULLIF(TRIM(parking_time), ''), 'NULL')
    """)

    stats = {
        "total": total,
        "Day": 0,
        "Night": 0,
        "Inactive": 0,
        "NULL": 0,
    }

    for status, count in cur.fetchall():
        if status in stats:
            stats[status] = count
        elif status is None:
            stats["NULL"] += count
        else:
            # неизвестные значения считаем незаполненными,
            # чтобы оператор их тоже увидел.
            stats["NULL"] += count

    conn.close()

    filled = stats["Day"] + stats["Night"] + stats["Inactive"]
    stats["filled"] = filled
    stats["percent"] = round(filled / total * 100, 1) if total else 0

    return stats


def format_tariff_review_stats(stats):
    return (
        "🚦 Проверка тарифов\n\n"
        f"Всего авто: {stats['total']}\n"
        f"🌞 Day: {stats['Day']}\n"
        f"🌙 Night: {stats['Night']}\n"
        f"🚫 Не паркуется: {stats['Inactive']}\n"
        f"❓ Без тарифа: {stats['NULL']}\n\n"
        f"Заполнено: {stats['filled']} из {stats['total']} "
        f"({stats['percent']}%)"
    )


def get_next_vehicle_without_tariff(skip_vehicle_ids=None):
    skip_vehicle_ids = [
        int(x) for x in (skip_vehicle_ids or [])
        if str(x).strip().isdigit()
    ]

    conn = get_conn()
    cur = conn.cursor()

    params = []
    skip_sql = ""

    if skip_vehicle_ids:
        placeholders = ",".join("?" for _ in skip_vehicle_ids)
        skip_sql = f"AND v.id NOT IN ({placeholders})"
        params.extend(skip_vehicle_ids)

    cur.execute(f"""
        SELECT
            v.id,
            a.apartment_number,
            v.license_plate_normalized,
            v.license_plate,
            v.car_model_normalized,
            v.car_model,
            v.parking_time
        FROM vehicles v
        JOIN apartments a
            ON a.id = v.apartment_id
        WHERE (
            v.parking_time IS NULL
            OR TRIM(v.parking_time) = ''
            OR UPPER(TRIM(v.parking_time)) = 'NULL'
        )
        {skip_sql}
        ORDER BY
            CASE
                WHEN a.apartment_number GLOB '[0-9]*'
                THEN CAST(a.apartment_number AS INTEGER)
                ELSE 999999
            END,
            a.apartment_number,
            v.id
        LIMIT 1
    """, tuple(params))

    row = cur.fetchone()
    conn.close()
    return row


def _get_all_tbot_rows_by_plate(plate):
    plate = normalize_source_plate(plate)

    if not plate:
        return []

    db_file = paths.OSBB_QUARANTINE_DB_FILE

    if not db_file.exists():
        return []

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'tbot_parking_import'
    """)

    if not cur.fetchone():
        conn.close()
        return []

    cur.execute("PRAGMA table_info(tbot_parking_import)")
    columns = [row[1] for row in cur.fetchall()]

    def pick(*names):
        for name in names:
            if name in columns:
                return name
        return None

    apartment_col = pick("apartment_number", "Номер квартири", "Номер квартиры", "Квартира")
    plate_norm_col = pick("license_plate_normalized")
    plate_raw_col = pick("license_plate", "Номер Авто", "Номер авто", "plate")
    model_norm_col = pick("car_model_normalized")
    model_raw_col = pick("car_model", "Марка авто", "Марка", "model")
    phone_col = pick("phone_normalized", "phone_number", "Телефон", "phone")
    status_col = pick("status", "Статус", "parking_time", "Тариф")

    if not plate_norm_col and not plate_raw_col:
        conn.close()
        return []

    select_parts = [
        f"{apartment_col} AS apartment_number" if apartment_col else "NULL AS apartment_number",
        f"{plate_norm_col} AS license_plate_normalized" if plate_norm_col else "NULL AS license_plate_normalized",
        f"{plate_raw_col} AS license_plate" if plate_raw_col else "NULL AS license_plate",
        f"{model_norm_col} AS car_model_normalized" if model_norm_col else "NULL AS car_model_normalized",
        f"{model_raw_col} AS car_model" if model_raw_col else "NULL AS car_model",
        f"{phone_col} AS phone_normalized" if phone_col else "NULL AS phone_normalized",
        f"{status_col} AS status" if status_col else "NULL AS status",
    ]

    where_parts = []
    params = []

    if plate_norm_col:
        where_parts.append(f"UPPER(REPLACE(CAST({plate_norm_col} AS TEXT), ' ', '')) = ?")
        params.append(plate)

    if plate_raw_col:
        where_parts.append(f"UPPER(REPLACE(CAST({plate_raw_col} AS TEXT), ' ', '')) = ?")
        params.append(plate)

    cur.execute(f"""
        SELECT {", ".join(select_parts)}
        FROM tbot_parking_import
        WHERE {" OR ".join(where_parts)}
        ORDER BY rowid
    """, tuple(params))

    rows = cur.fetchall()
    conn.close()
    return rows


def _get_all_telegram_facts_by_plate(plate):
    plate = normalize_source_plate(plate)

    if not plate:
        return []

    db_file = paths.OSBB_TELEGRAM_DB_FILE

    if not db_file.exists():
        return []

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'telegram_facts'
    """)

    if not cur.fetchone():
        conn.close()
        return []

    cur.execute("""
        SELECT
            f.apartment_number,
            f.license_plate_normalized,
            f.license_plate,
            f.car_brand,
            f.car_model,
            f.comment,
            m.text_raw,
            m.telegram_message_id,
            m.sender_name
        FROM telegram_facts f
        LEFT JOIN telegram_messages m
            ON m.id = f.telegram_message_db_id
        WHERE UPPER(REPLACE(CAST(COALESCE(f.license_plate_normalized, f.license_plate) AS TEXT), ' ', '')) = ?
        ORDER BY f.id
    """, (plate,))

    rows = cur.fetchall()
    conn.close()
    return rows


def get_tariff_hints_for_vehicle(vehicle):
    if not vehicle:
        return []

    (
        vehicle_id,
        apartment_number,
        plate_norm,
        plate_raw,
        model_norm,
        model_raw,
        parking_time,
    ) = vehicle

    plate = normalize_source_plate(plate_norm or plate_raw)

    hints = []

    # 1. Сообщения Telegram: самый ценный источник для Day/Night
    seen = set()
    for row in _get_all_telegram_facts_by_plate(plate):
        (
            apt,
            fact_plate_norm,
            fact_plate_raw,
            car_brand,
            car_model,
            comment,
            text_raw,
            msg_id,
            sender_name,
        ) = row

        hint = (
            detect_parking_time_from_text(comment)
            or detect_parking_time_from_text(text_raw)
        )

        if not hint:
            continue

        key = ("messages", apt, plate, hint, msg_id)

        if key in seen:
            continue

        seen.add(key)

        hints.append({
            "source": "Сообщения",
            "apartment": apt,
            "parking_time": hint,
            "details": f"msg_id={msg_id}" if msg_id else None,
        })

    # 2. Бот-анкета: если вдруг статус/тариф там заполнен
    for row in _get_all_tbot_rows_by_plate(plate):
        (
            apt,
            plate_norm2,
            plate_raw2,
            model_norm2,
            model_raw2,
            phone,
            status,
        ) = row

        hint = detect_parking_time_from_text(status)

        if not hint:
            continue

        hints.append({
            "source": "Бот",
            "apartment": apt,
            "parking_time": hint,
            "details": phone,
        })

    return hints


def get_best_tariff_hint_for_vehicle(vehicle):
    hints = get_tariff_hints_for_vehicle(vehicle)

    if not hints:
        return None

    counts = {}

    for item in hints:
        tariff = item["parking_time"]

        if tariff not in ["Day", "Night"]:
            continue

        counts[tariff] = counts.get(tariff, 0) + 1

    if not counts:
        return None

    best = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[0][0]

    sources = [
        item["source"]
        for item in hints
        if item["parking_time"] == best
    ]

    return {
        "parking_time": best,
        "count": counts[best],
        "sources": sorted(set(sources)),
    }


def format_tariff_vehicle_card(vehicle):
    if not vehicle:
        return "Все автомобили уже имеют Day / Night / Не паркуется."

    (
        vehicle_id,
        apartment_number,
        plate_norm,
        plate_raw,
        model_norm,
        model_raw,
        parking_time,
    ) = vehicle

    plate = plate_norm or plate_raw or "-"
    model = model_norm or model_raw or "-"
    status = parking_time or "NULL"

    hints = get_tariff_hints_for_vehicle(vehicle)
    best_hint = get_best_tariff_hint_for_vehicle(vehicle)

    lines = []
    lines.append("🚦 Тариф автомобиля")
    lines.append("")
    lines.append(f"ID: {vehicle_id}")
    lines.append(f"Квартира: {apartment_number}")
    lines.append(f"Номер: {plate}")
    lines.append(f"Марка: {model}")
    lines.append(f"Текущий тариф: {status}")
    lines.append("")

    if best_hint:
        lines.append("🟢 Подсказка")
        lines.append(
            f"{best_hint['parking_time']} "
            f"({'+'.join(best_hint['sources'])})"
        )
        lines.append("")
    else:
        lines.append("🟡 Подсказка")
        lines.append("не найдена")
        lines.append("")

    if hints:
        lines.append("Источники:")
        for item in hints[:5]:
            extra = f" | {item['details']}" if item.get("details") else ""
            lines.append(
                f"• {item['source']} | кв.{item.get('apartment') or '-'} | "
                f"{item['parking_time']}{extra}"
            )

        if len(hints) > 5:
            lines.append(f"… ещё {len(hints) - 5}")

        lines.append("")

    lines.append("Выберите тариф:")

    return "\n".join(lines)


def apply_vehicle_tariff(vehicle_id, tariff):
    if tariff == "Day":
        status = "Day"
    elif tariff == "Night":
        status = "Night"
    elif tariff in ["Inactive", "Не паркуется"]:
        status = "Inactive"
    else:
        return False, "invalid_tariff"

    return update_vehicle_parking_status(vehicle_id, status)


def apply_best_tariff_hint(vehicle_id):
    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return False, "vehicle_not_found"

    best = get_best_tariff_hint_for_vehicle(vehicle)

    if not best:
        return False, "no_hint"

    return apply_vehicle_tariff(vehicle_id, best["parking_time"])


# ==========================================================
# Minimal vehicle edit helpers for tariff mode
# ==========================================================

def format_vehicle_edit_menu_card(vehicle_id):
    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return "Автомобиль не найден."

    return (
        format_vehicle_card_for_edit(vehicle)
        + "\n\nЧто изменить?"
    )


def set_vehicle_plate_from_text(vehicle_id, plate_text):
    return update_vehicle_plate(vehicle_id, plate_text)


def set_vehicle_model_from_text(vehicle_id, model_text):
    return update_vehicle_model(vehicle_id, model_text)


def set_vehicle_tariff_from_text(vehicle_id, tariff_text):
    return update_vehicle_parking_status(vehicle_id, tariff_text)


# ==========================================================
# Operator audit log
# ==========================================================

def _audit_value(value):
    if value is None:
        return "NULL"
    return str(value)


def log_operator_action(
    operator_telegram_id=None,
    action_type=None,
    entity_type=None,
    entity_id=None,
    apartment_number=None,
    vehicle_id=None,
    old_value=None,
    new_value=None,
    comment=None,
    operator_username=None,
    operator_name=None,
):
    """
    Пишет действие оператора в operator_audit_log.
    Журналирование не должно ломать основное действие, поэтому ошибки
    записи в журнал возвращаются как False, но не выбрасываются наружу.
    """
    if not action_type:
        return False, "empty_action_type"

    try:
        conn = get_conn()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO operator_audit_log (
                created_at,

                operator_telegram_id,
                operator_username,
                operator_name,

                action_type,
                entity_type,
                entity_id,

                apartment_number,
                vehicle_id,

                old_value,
                new_value,

                comment
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now(),

            str(operator_telegram_id) if operator_telegram_id is not None else None,
            operator_username,
            operator_name,

            action_type,
            entity_type,
            str(entity_id) if entity_id is not None else None,

            str(apartment_number) if apartment_number is not None else None,
            int(vehicle_id) if vehicle_id is not None else None,

            _audit_value(old_value),
            _audit_value(new_value),

            comment,
        ))

        audit_id = cur.lastrowid
        conn.commit()
        conn.close()

        return True, audit_id

    except Exception as exc:
        try:
            conn.close()
        except Exception:
            pass

        return False, f"audit_log_error: {exc}"


def update_vehicle_parking_status(
    vehicle_id,
    status,
    operator_telegram_id=None,
    operator_username=None,
    operator_name=None,
):
    if status == "NULL":
        new_value = None
    elif status in ["Day", "Night", "Inactive"]:
        new_value = status
    else:
        return False, "invalid_status"

    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return False, "vehicle_not_found"

    old_value = vehicle[6]
    apartment_number = vehicle[1]

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE vehicles
        SET parking_time = ?
        WHERE id = ?
    """, (
        new_value,
        int(vehicle_id),
    ))

    changed = cur.rowcount
    conn.commit()
    conn.close()

    if not changed:
        return False, "not_updated"

    log_operator_action(
        operator_telegram_id=operator_telegram_id,
        operator_username=operator_username,
        operator_name=operator_name,
        action_type="vehicle_tariff_update",
        entity_type="vehicle",
        entity_id=vehicle_id,
        apartment_number=apartment_number,
        vehicle_id=vehicle_id,
        old_value=old_value,
        new_value=new_value,
        comment="parking_time updated",
    )

    return True, {
        "vehicle_id": int(vehicle_id),
        "old_value": old_value,
        "new_value": new_value,
    }


def update_vehicle_plate(
    vehicle_id,
    plate,
    operator_telegram_id=None,
    operator_username=None,
    operator_name=None,
):
    plate = str(plate).strip().upper()

    if not plate:
        return False, "empty_plate"

    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return False, "vehicle_not_found"

    apartment_number = vehicle[1]
    old_plate = vehicle[2]
    old_plate_raw = vehicle[3]
    old_value = old_plate or old_plate_raw

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE vehicles
        SET
            license_plate = ?,
            license_plate_normalized = ?
        WHERE id = ?
    """, (
        plate,
        plate,
        int(vehicle_id),
    ))

    changed = cur.rowcount
    conn.commit()
    conn.close()

    if not changed:
        return False, "not_updated"

    log_operator_action(
        operator_telegram_id=operator_telegram_id,
        operator_username=operator_username,
        operator_name=operator_name,
        action_type="vehicle_plate_update",
        entity_type="vehicle",
        entity_id=vehicle_id,
        apartment_number=apartment_number,
        vehicle_id=vehicle_id,
        old_value=old_value,
        new_value=plate,
        comment="license plate updated",
    )

    return True, {
        "vehicle_id": int(vehicle_id),
        "old_value": old_value,
        "new_value": plate,
    }


def update_vehicle_model(
    vehicle_id,
    model,
    operator_telegram_id=None,
    operator_username=None,
    operator_name=None,
):
    model = str(model).strip().upper()

    if not model:
        return False, "empty_model"

    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return False, "vehicle_not_found"

    apartment_number = vehicle[1]
    old_model = vehicle[4]
    old_model_raw = vehicle[5]
    old_value = old_model or old_model_raw

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE vehicles
        SET
            car_model = ?,
            car_model_normalized = ?
        WHERE id = ?
    """, (
        model,
        model,
        int(vehicle_id),
    ))

    changed = cur.rowcount
    conn.commit()
    conn.close()

    if not changed:
        return False, "not_updated"

    log_operator_action(
        operator_telegram_id=operator_telegram_id,
        operator_username=operator_username,
        operator_name=operator_name,
        action_type="vehicle_model_update",
        entity_type="vehicle",
        entity_id=vehicle_id,
        apartment_number=apartment_number,
        vehicle_id=vehicle_id,
        old_value=old_value,
        new_value=model,
        comment="car model updated",
    )

    return True, {
        "vehicle_id": int(vehicle_id),
        "old_value": old_value,
        "new_value": model,
    }


def set_apartment_verification_status(
    apartment_number,
    status,
    verified_by=None,
    comment=None,
    operator_username=None,
    operator_name=None,
):
    if status not in VERIFICATION_STATUSES:
        return False, "invalid_status"

    apt = find_apartment(apartment_number)

    if not apt:
        return False, "apartment_not_found"

    apartment_id = apt[0]
    apartment_number = str(apt[1])

    old_verification = get_apartment_verification_status(apartment_number)
    old_status = old_verification[3] if old_verification else None

    verified_at = now() if status in ["confirmed", "deferred", "conflict"] else None

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO apartment_verification (
            apartment_id,
            apartment_number,
            status,
            comment,
            verified_by,
            verified_at,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(apartment_id)
        DO UPDATE SET
            apartment_number = excluded.apartment_number,
            status = excluded.status,
            comment = excluded.comment,
            verified_by = excluded.verified_by,
            verified_at = excluded.verified_at,
            updated_at = excluded.updated_at
    """, (
        apartment_id,
        apartment_number,
        status,
        comment,
        int(verified_by) if verified_by else None,
        verified_at,
        now(),
        now(),
    ))

    conn.commit()
    conn.close()

    log_operator_action(
        operator_telegram_id=verified_by,
        operator_username=operator_username,
        operator_name=operator_name,
        action_type="apartment_verification_status_update",
        entity_type="apartment",
        entity_id=apartment_id,
        apartment_number=apartment_number,
        old_value=old_status,
        new_value=status,
        comment=comment,
    )

    return True, status


def set_vehicle_plate_from_text(vehicle_id, plate_text, operator_telegram_id=None):
    return update_vehicle_plate(
        vehicle_id,
        plate_text,
        operator_telegram_id=operator_telegram_id,
    )


def set_vehicle_model_from_text(vehicle_id, model_text, operator_telegram_id=None):
    return update_vehicle_model(
        vehicle_id,
        model_text,
        operator_telegram_id=operator_telegram_id,
    )


def set_vehicle_tariff_from_text(vehicle_id, tariff_text, operator_telegram_id=None):
    return update_vehicle_parking_status(
        vehicle_id,
        tariff_text,
        operator_telegram_id=operator_telegram_id,
    )


def apply_vehicle_tariff(vehicle_id, tariff, operator_telegram_id=None):
    if tariff == "Day":
        status = "Day"
    elif tariff == "Night":
        status = "Night"
    elif tariff in ["Inactive", "Не паркуется"]:
        status = "Inactive"
    else:
        return False, "invalid_tariff"

    return update_vehicle_parking_status(
        vehicle_id,
        status,
        operator_telegram_id=operator_telegram_id,
    )


def apply_best_tariff_hint(vehicle_id, operator_telegram_id=None):
    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return False, "vehicle_not_found"

    best = get_best_tariff_hint_for_vehicle(vehicle)

    if not best:
        return False, "no_hint"

    return apply_vehicle_tariff(
        vehicle_id,
        best["parking_time"],
        operator_telegram_id=operator_telegram_id,
    )


# ==========================================================
# UNIT RESOLVER INTEGRATION — ACTIVE OVERRIDES
# ==========================================================

# Сохраняем предыдущую одиночную реализацию для обратной совместимости.
_legacy_apply_agreement_suggestion = apply_agreement_suggestion
#
# This block supersedes the earlier local composite resolver
# (`_get_tbot_composite_refs` / `resolve_agreement_apartment_ref`).
#
# The old implementation inferred composite references from the Telegram
# quarantine database every time and returned the first physical apartment
# for compatibility. The shared `unit_resolver.py` now reads the approved,
# persistent unit_groups / unit_group_members / unit_group_aliases structure.
#
# Integrity rule:
# - READ operations may aggregate a logical group.
# - WRITE operations that change a resident link or verification status MUST
#   select one physical unit explicitly. A logical group is not a legal merge.

try:
    from unit_resolver import resolve_unit_ref
except ImportError as exc:
    raise ImportError(
        "Не найден unit_resolver.py в корне проекта OSBB. "
        "Сначала установите этот модуль рядом с config.py."
    ) from exc


def _ur_q(identifier):
    return '"' + str(identifier).replace('"', '""') + '"'


def _ur_text(value):
    return "" if value is None else str(value).strip()


def _ur_table_exists(conn, table_name):
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    )
    return cur.fetchone() is not None


def _ur_table_columns(conn, table_name):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({_ur_q(table_name)})")
    return {row[1] for row in cur.fetchall()}


def _ur_unique(values):
    """Сохраняет порядок, удаляя пустые и повторяющиеся значения."""
    result = []
    seen = set()

    for value in values:
        value = _ur_text(value)
        if not value or value in seen:
            continue

        seen.add(value)
        result.append(value)

    return result


def _ur_apartment_row_tuple(conn, apartment_id):
    """
    Legacy tuple contract:
      (id, apartment_number, entrance)

    Многие старые обработчики ожидают именно такой tuple. Поэтому сохраняем
    контракт, но источник apartment_id теперь выбирается общим resolver.
    """
    cols = _ur_table_columns(conn, "apartments")

    if "entrance" in cols:
        entrance_expr = _ur_q("entrance")
    elif "entrance_number" in cols:
        entrance_expr = _ur_q("entrance_number")
    else:
        entrance_expr = "NULL"

    cur = conn.cursor()
    cur.execute(f"""
        SELECT
            id,
            apartment_number,
            {entrance_expr} AS entrance
        FROM apartments
        WHERE id = ?
    """, (int(apartment_id),))

    return cur.fetchone()


def resolve_unit_context(unit_ref):
    """
    Общий контекст единицы для db_access.

    Возвращает dict:
      resolution            — UnitResolution из unit_resolver
      display               — 31_32 для группы или номер отдельной единицы
      lookup_numbers        — номера всех физических units
      lookup_apartment_ids  — id всех физических units
      source_refs           — все допустимые формы для поиска во внешних источниках
      is_group              — логическая группа?
      group_aliases         — raw aliases, сохранённые в БД

    Этот helper предназначен для чтения и отображения. Для записи используйте
    resolve_physical_unit_for_write(), который отвергнет логическую группу.
    """
    requested = _ur_text(unit_ref)
    conn = get_conn()
    conn.row_factory = sqlite3.Row

    try:
        resolution = resolve_unit_ref(conn, requested)

        if not resolution.found:
            return {
                "requested": requested,
                "display": requested,
                "resolution": resolution,
                "lookup_numbers": [],
                "lookup_apartment_ids": [],
                "source_refs": _ur_unique([requested]),
                "group_aliases": [],
                "is_group": False,
            }

        group_aliases = []

        if resolution.is_group and resolution.group_id is not None:
            cur = conn.cursor()
            cur.execute("""
                SELECT alias_text
                FROM unit_group_aliases
                WHERE group_id = ?
                  AND is_active = 1
                ORDER BY
                    CASE alias_kind
                        WHEN 'COMPONENT' THEN 1
                        WHEN 'COMPOSITE' THEN 2
                        ELSE 3
                    END,
                    alias_text
            """, (int(resolution.group_id),))
            group_aliases = [_ur_text(row[0]) for row in cur.fetchall()]

        lookup_numbers = resolution.member_apartment_numbers
        lookup_ids = resolution.member_apartment_ids
        display = (
            resolution.group_code
            if resolution.is_group
            else (lookup_numbers[0] if lookup_numbers else requested)
        )

        source_refs = _ur_unique(
            group_aliases
            + lookup_numbers
            + [
                resolution.group_code,
                display,
                requested,
            ]
        )

        return {
            "requested": requested,
            "display": display,
            "resolution": resolution,
            "lookup_numbers": lookup_numbers,
            "lookup_apartment_ids": lookup_ids,
            "source_refs": source_refs,
            "group_aliases": group_aliases,
            "is_group": resolution.is_group,
        }
    finally:
        conn.close()


def resolve_physical_unit_for_write(unit_ref, selected_apartment_number=None):
    """
    Безопасное разрешение физической единицы перед записью.

    Для отдельной квартиры/помещения возвращает её member.
    Для логической группы:
      - без selected_apartment_number возвращает ошибку;
      - с явным участником возвращает именно выбранную физическую единицу.

    Это предотвращает ошибку старой логики: «взяли первую часть группы».
    """
    context = resolve_unit_context(unit_ref)
    resolution = context["resolution"]

    if not resolution.found:
        return None, "unit_not_found", context

    if not resolution.is_group:
        return resolution.members[0], "ok", context

    if not selected_apartment_number:
        return None, "group_requires_member_selection", context

    wanted = _ur_text(selected_apartment_number)

    for member in resolution.members:
        if member.apartment_number == wanted:
            return member, "ok", context

    return None, "selected_member_not_in_group", context


def resolve_agreement_apartment_ref(apartment_number):
    """
    Compatibility API for older agreement code.

    New code should prefer resolve_unit_context(). This wrapper retains the
    old dict shape while now using the persistent resolver structure.
    """
    context = resolve_unit_context(apartment_number)
    resolution = context["resolution"]

    return {
        "requested": context["requested"],
        "display": context["display"],
        "lookup_numbers": context["lookup_numbers"],
        "lookup_apartment_ids": context["lookup_apartment_ids"],
        "tbot_refs": context["source_refs"],
        "is_composite": context["is_group"],
        "resolution_kind": resolution.kind,
        "group_id": resolution.group_id,
        "group_code": resolution.group_code,
        "legal_status": resolution.legal_status,
    }


def find_apartment(apartment_number):
    """
    Compatibility lookup returning a single legacy apartments tuple.

    For a group it returns the first member only because some old callers
    require one tuple. NEW write operations must not use this function for
    group updates; they use resolve_physical_unit_for_write().
    """
    context = resolve_unit_context(apartment_number)

    if not context["lookup_apartment_ids"]:
        return None

    conn = get_conn()
    try:
        return _ur_apartment_row_tuple(conn, context["lookup_apartment_ids"][0])
    finally:
        conn.close()


def get_accounts_by_apartment(apartment_number):
    """Read all resident accounts belonging to the resolved physical units."""
    context = resolve_unit_context(apartment_number)

    if not context["lookup_numbers"]:
        return []

    placeholders = ",".join("?" for _ in context["lookup_numbers"])
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(f"""
        SELECT
            telegram_user_id,
            telegram_username,
            telegram_first_name,
            telegram_last_name,
            apartment_number,
            status,
            verified_at
        FROM resident_accounts
        WHERE apartment_number IN ({placeholders})
        ORDER BY verified_at, telegram_user_id
    """, tuple(context["lookup_numbers"]))

    rows = cur.fetchall()
    conn.close()
    return rows


def link_resident_to_apartment(
    telegram_user_id,
    apartment_number,
    status="apartment_confirmed",
    selected_apartment_number=None,
):
    """
    Связывает Telegram-пользователя только с одной физической единицей.

    Для 31_32 без явного selected_apartment_number запись не выполняется.
    """
    member, state, context = resolve_physical_unit_for_write(
        apartment_number,
        selected_apartment_number=selected_apartment_number,
    )

    if state != "ok":
        return False, state

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE resident_accounts
        SET
            apartment_id = ?,
            apartment_number = ?,
            status = ?,
            verified_at = ?,
            updated_at = ?
        WHERE telegram_user_id = ?
    """, (
        member.apartment_id,
        member.apartment_number,
        status,
        now(),
        now(),
        int(telegram_user_id),
    ))

    changed = cur.rowcount
    conn.commit()
    conn.close()

    if not changed:
        return False, "resident_not_found"

    return True, {
        "status": "linked",
        "apartment_id": member.apartment_id,
        "apartment_number": member.apartment_number,
        "group_code": context["resolution"].group_code,
    }


def get_apartment_vehicles(apartment_number):
    """
    Возвращает машины по одной physical unit либо по всем участникам группы.

    Формат результата сохранён:
      (id, plate_norm, plate_raw, model_norm, model_raw, parking_time)
    """
    context = resolve_unit_context(apartment_number)
    apartment_ids = context["lookup_apartment_ids"]

    if not apartment_ids:
        return []

    placeholders = ",".join("?" for _ in apartment_ids)
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(f"""
        SELECT
            v.id,
            v.license_plate_normalized,
            v.license_plate,
            v.car_model_normalized,
            v.car_model,
            v.parking_time
        FROM vehicles v
        WHERE v.apartment_id IN ({placeholders})
        ORDER BY v.apartment_id, v.id
    """, tuple(apartment_ids))

    rows = cur.fetchall()
    conn.close()
    return rows


def get_vehicle_by_id_for_apartment(vehicle_id, apartment_number=None):
    """
    Проверяет принадлежность авто одной единице или участнику логической группы.
    """
    vehicle = get_vehicle_by_id(vehicle_id)

    if not vehicle:
        return None

    if apartment_number is None:
        return vehicle

    context = resolve_unit_context(apartment_number)
    if not context["lookup_numbers"]:
        return None

    return vehicle if _ur_text(vehicle[1]) in context["lookup_numbers"] else None


def _ur_fetch_member_rows(conn, apartment_ids):
    if not apartment_ids:
        return []

    cols = _ur_table_columns(conn, "apartments")

    if "entrance" in cols:
        entrance_expr = f'a.{_ur_q("entrance")}'
    elif "entrance_number" in cols:
        entrance_expr = f'a.{_ur_q("entrance_number")}'
    else:
        entrance_expr = "NULL"

    unit_type_expr = (
        f'a.{_ur_q("unit_type")}'
        if "unit_type" in cols else "NULL"
    )
    unit_code_expr = (
        f'a.{_ur_q("unit_code")}'
        if "unit_code" in cols else "NULL"
    )
    display_name_expr = (
        f'a.{_ur_q("display_name")}'
        if "display_name" in cols else "NULL"
    )

    placeholders = ",".join("?" for _ in apartment_ids)
    cur = conn.cursor()
    cur.execute(f"""
        SELECT
            a.id,
            a.apartment_number,
            {entrance_expr} AS entrance,
            {unit_code_expr} AS unit_code,
            {unit_type_expr} AS unit_type,
            {display_name_expr} AS display_name
        FROM apartments a
        WHERE a.id IN ({placeholders})
        ORDER BY
            CASE
                WHEN a.apartment_number GLOB '[0-9]*'
                THEN CAST(a.apartment_number AS INTEGER)
                ELSE 999999
            END,
            a.apartment_number,
            a.id
    """, tuple(apartment_ids))

    return cur.fetchall()


def get_apartment_card(apartment_number):
    """
    Универсальная карточка отдельной единицы или составной группы.

    Existing keys are preserved:
      apartment, apartment_number, lookup_numbers, is_composite, residents, vehicles

    New keys:
      unit_resolution, member_apartments, lookup_apartment_ids, group_code,
      group_legal_status.
    """
    context = resolve_unit_context(apartment_number)
    resolution = context["resolution"]
    apartment_ids = context["lookup_apartment_ids"]

    if not apartment_ids:
        return None

    conn = get_conn()
    member_apartments = _ur_fetch_member_rows(conn, apartment_ids)

    if not member_apartments:
        conn.close()
        return None

    placeholders_ids = ",".join("?" for _ in apartment_ids)
    placeholders_numbers = ",".join("?" for _ in context["lookup_numbers"])
    cur = conn.cursor()

    # apartment_id — основной современный ключ. apartment_number нужен как
    # fallback для старых resident_accounts, где apartment_id был пустым.
    cur.execute(f"""
        SELECT
            telegram_first_name,
            telegram_last_name,
            telegram_username,
            status
        FROM resident_accounts
        WHERE apartment_id IN ({placeholders_ids})
           OR (
               apartment_id IS NULL
               AND apartment_number IN ({placeholders_numbers})
           )
        ORDER BY telegram_user_id
    """, tuple(apartment_ids) + tuple(context["lookup_numbers"]))

    residents = cur.fetchall()
    conn.close()

    vehicles = get_apartment_vehicles(apartment_number)

    return {
        "apartment": member_apartments[0][:3],
        "apartment_number": context["display"],
        "lookup_numbers": context["lookup_numbers"],
        "lookup_apartment_ids": apartment_ids,
        "is_composite": context["is_group"],
        "group_code": resolution.group_code,
        "group_legal_status": resolution.legal_status,
        "unit_resolution": resolution,
        "member_apartments": member_apartments,
        "residents": residents,
        "vehicles": vehicles,
    }


def format_apartment_card(card):
    """Короткая карточка для обычного раздела «Квартиры»."""
    if not card:
        return "Квартира или помещение не найдено."

    residents_count = len(card["residents"])
    vehicles_count = len(card["vehicles"])

    lines = []
    if card.get("is_composite"):
        lines.append(f"🏠 Связка {card['apartment_number']}")
        lines.append(
            "Физические единицы: " + ", ".join(card.get("lookup_numbers", []))
        )
        lines.append(
            "Статус связи: "
            + (
                "юридическое объединение не подтверждено"
                if card.get("group_legal_status") == "UNKNOWN"
                else str(card.get("group_legal_status") or "-")
            )
        )
    else:
        lines.append(f"🏠 Квартира / помещение {card['apartment_number']}")

    lines.append("")
    lines.append(f"👥 Пользователи: {residents_count}")
    lines.append(f"🚗 Авто: {vehicles_count}")
    lines.append("")
    lines.append("Выберите раздел:")

    return "\n".join(lines)


def _ur_pick_column(columns, *candidates):
    for name in candidates:
        if name in columns:
            return name
    return None


def get_tbot_source_rows(apartment_number):
    """
    Выбирает данные tbot по всем алиасам группы и её физическим units.
    """
    db_file = paths.OSBB_QUARANTINE_DB_FILE

    if not db_file.exists():
        return {
            "ok": False,
            "error": f"quarantine_db_not_found: {db_file}",
            "rows": [],
        }

    context = resolve_unit_context(apartment_number)
    search_refs = context["source_refs"]

    if not search_refs:
        return {
            "ok": True,
            "error": None,
            "rows": [],
        }

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    try:
        if not _ur_table_exists(conn, "tbot_parking_import"):
            return {
                "ok": False,
                "error": "table_tbot_parking_import_not_found",
                "rows": [],
            }

        cols = _ur_table_columns(conn, "tbot_parking_import")
        apartment_col = _ur_pick_column(
            cols,
            "apartment_number",
            "Номер квартири",
            "Номер квартиры",
            "Квартира",
        )
        plate_norm_col = _ur_pick_column(cols, "license_plate_normalized")
        plate_raw_col = _ur_pick_column(
            cols,
            "license_plate", "Номер Авто", "Номер авто", "plate",
        )
        model_norm_col = _ur_pick_column(cols, "car_model_normalized")
        model_raw_col = _ur_pick_column(
            cols,
            "car_model", "Марка авто", "Марка", "model",
        )
        phone_col = _ur_pick_column(
            cols,
            "phone_normalized", "phone_number", "Телефон", "phone",
        )
        status_col = _ur_pick_column(
            cols,
            "status", "Статус", "parking_time", "Тариф",
        )

        if not apartment_col:
            return {
                "ok": False,
                "error": "apartment_column_not_found",
                "rows": [],
            }

        def select_expr(column, alias):
            return (
                f"{_ur_q(column)} AS {_ur_q(alias)}"
                if column else f"NULL AS {_ur_q(alias)}"
            )

        placeholders = ",".join("?" for _ in search_refs)
        cur.execute(f"""
            SELECT
                {_ur_q(apartment_col)} AS apartment_number,
                {select_expr(plate_norm_col, "license_plate_normalized")},
                {select_expr(plate_raw_col, "license_plate")},
                {select_expr(model_norm_col, "car_model_normalized")},
                {select_expr(model_raw_col, "car_model")},
                {select_expr(phone_col, "phone_normalized")},
                {select_expr(status_col, "status")}
            FROM {_ur_q("tbot_parking_import")}
            WHERE TRIM(CAST({_ur_q(apartment_col)} AS TEXT)) IN ({placeholders})
            ORDER BY rowid
        """, tuple(search_refs))

        return {
            "ok": True,
            "error": None,
            "rows": cur.fetchall(),
        }
    finally:
        conn.close()


def get_telegram_facts_by_apartment(apartment_number):
    """
    Выбирает Telegram facts по всем алиасам группы и физическим units.
    """
    db_file = paths.OSBB_TELEGRAM_DB_FILE

    if not db_file.exists():
        return {
            "ok": False,
            "error": f"telegram_db_not_found: {db_file}",
            "rows": [],
        }

    context = resolve_unit_context(apartment_number)
    search_refs = context["source_refs"]

    if not search_refs:
        return {
            "ok": True,
            "error": None,
            "rows": [],
        }

    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    try:
        if not _ur_table_exists(conn, "telegram_facts"):
            return {
                "ok": False,
                "error": "table_telegram_facts_not_found",
                "rows": [],
            }

        placeholders = ",".join("?" for _ in search_refs)
        cur.execute(f"""
            SELECT
                f.id,
                f.fact_type,
                f.apartment_number,
                f.person_name,
                f.phone_normalized,
                f.license_plate_normalized,
                f.license_plate,
                f.car_brand,
                f.car_model,
                f.car_color_normalized,
                f.amount,
                f.remote_count,
                f.fact_status,
                f.comment,
                m.text_raw,
                m.message_date,
                m.sender_name
            FROM telegram_facts f
            LEFT JOIN telegram_messages m
                ON m.id = f.telegram_message_db_id
            WHERE TRIM(CAST(f.apartment_number AS TEXT)) IN ({placeholders})
            ORDER BY f.id
        """, tuple(search_refs))

        raw_rows = cur.fetchall()
    finally:
        conn.close()

    rows = []
    for row in raw_rows:
        (
            fact_id,
            fact_type,
            apt,
            person_name,
            phone_normalized,
            plate_norm,
            plate_raw,
            car_brand,
            car_model,
            car_color,
            amount,
            remote_count,
            fact_status,
            comment,
            text_raw,
            message_date,
            sender_name,
        ) = row

        parking_hint = (
            detect_parking_time_from_text(fact_type)
            or detect_parking_time_from_text(comment)
            or detect_parking_time_from_text(text_raw)
        )

        rows.append({
            "fact_id": fact_id,
            "fact_type": fact_type,
            "apartment_number": apt,
            "person_name": person_name,
            "phone": phone_normalized,
            "plate": plate_norm or plate_raw,
            "model": " ".join(
                item for item in [car_brand, car_model] if item
            ) or None,
            "color": car_color,
            "amount": amount,
            "remote_count": remote_count,
            "fact_status": fact_status,
            "comment": comment,
            "text_raw": text_raw,
            "message_date": message_date,
            "sender_name": sender_name,
            "parking_hint": parking_hint,
        })

    return {
        "ok": True,
        "error": None,
        "rows": rows,
    }


def get_main_db_vehicle_rows_for_compare(apartment_number):
    vehicles = get_apartment_vehicles(apartment_number)
    result = []

    for row in vehicles:
        (
            vehicle_id,
            plate_norm,
            plate_raw,
            model_norm,
            model_raw,
            parking_time,
        ) = row

        result.append({
            "plate": plate_norm or plate_raw or "-",
            "model": model_norm or model_raw or "-",
            "parking_time": parking_time or "NULL",
        })

    return result


def get_unit_verification_summary(apartment_number):
    """
    Возвращает статусы согласования по каждой физической единице группы.
    """
    context = resolve_unit_context(apartment_number)
    members = context["resolution"].members

    if not members:
        return []

    conn = get_conn()
    cur = conn.cursor()
    result = []

    try:
        for member in members:
            cur.execute("""
                SELECT
                    status,
                    comment,
                    verified_by,
                    verified_at,
                    updated_at
                FROM apartment_verification
                WHERE apartment_id = ?
            """, (member.apartment_id,))
            row = cur.fetchone()

            if row:
                status, comment, verified_by, verified_at, updated_at = row
            else:
                status, comment, verified_by, verified_at, updated_at = (
                    "new", None, None, None, None
                )

            result.append({
                "apartment_id": member.apartment_id,
                "apartment_number": member.apartment_number,
                "status": status,
                "comment": comment,
                "verified_by": verified_by,
                "verified_at": verified_at,
                "updated_at": updated_at,
            })
    finally:
        conn.close()

    return result


def format_apartment_agreement_card(apartment_number):
    """
    Согласовательная карточка с отображением всех physical members.

    Для составной группы показываются статусы каждой квартиры отдельно.
    Финансовых либо подтверждающих действий по группе функция не выполняет.
    """
    context = resolve_unit_context(apartment_number)
    card = get_apartment_card(apartment_number)

    if not card:
        return "Квартира или помещение не найдено."

    verification_rows = get_unit_verification_summary(apartment_number)
    tbot_source = get_tbot_source_rows(apartment_number)
    telegram_facts_source = get_clean_telegram_facts_by_apartment(apartment_number)

    lines = []

    if card.get("is_composite"):
        lines.append(f"🏠 Связка {card['apartment_number']}")
        lines.append("Физические единицы: " + ", ".join(card["lookup_numbers"]))
        lines.append(
            "Юридический статус: "
            + (
                "не подтверждён"
                if card.get("group_legal_status") == "UNKNOWN"
                else str(card.get("group_legal_status") or "-")
            )
        )
        lines.append(
            "Внимание: подтверждение и изменение данных выполняются "
            "только после выбора конкретной физической единицы."
        )
    else:
        lines.append(f"🏠 Квартира / помещение {card['apartment_number']}")

    lines.append("")
    lines.append("🤝 Статус согласования:")

    for item in verification_rows:
        status_label = VERIFICATION_STATUSES.get(item["status"], item["status"])
        comment = f" | {item['comment']}" if item.get("comment") else ""
        lines.append(
            f"• {item['apartment_number']}: {status_label}{comment}"
        )

    lines.append("")
    lines.append(format_tbot_source_rows(tbot_source))

    lines.append("")
    lines.append(format_telegram_facts_source_rows(telegram_facts_source))

    lines.append("")
    lines.append("💾 БД")
    lines.append("Авто:")
    lines.append(format_vehicle_list(card["vehicles"]))

    lines.append("")
    lines.append(get_agreement_compare_summary(apartment_number))

    lines.append("")
    lines.append(format_agreement_suggestion(apartment_number))

    lines.append("")
    lines.append("👥 Жильцы:")
    if card["residents"]:
        for first_name, last_name, username, resident_status in card["residents"]:
            name = " ".join(item for item in [first_name, last_name] if item) or "-"
            username = f"@{username}" if username else "-"
            lines.append(f"• {name} | {username} | {resident_status or '-'}")
    else:
        lines.append("нет пользователей")

    return "\n".join(lines)


def set_apartment_verification_status(
    apartment_number,
    status,
    verified_by=None,
    comment=None,
    operator_username=None,
    operator_name=None,
    selected_apartment_number=None,
):
    """
    Меняет статус только одной физической единицы.

    Для группы параметр selected_apartment_number обязателен.
    """
    if status not in VERIFICATION_STATUSES:
        return False, "invalid_status"

    member, state, context = resolve_physical_unit_for_write(
        apartment_number,
        selected_apartment_number=selected_apartment_number,
    )

    if state != "ok":
        return False, state

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT status
        FROM apartment_verification
        WHERE apartment_id = ?
    """, (member.apartment_id,))
    row = cur.fetchone()
    old_status = row[0] if row else None

    verified_at = now() if status in ["confirmed", "deferred", "conflict"] else None

    cur.execute("""
        INSERT INTO apartment_verification (
            apartment_id,
            apartment_number,
            status,
            comment,
            verified_by,
            verified_at,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(apartment_id)
        DO UPDATE SET
            apartment_number = excluded.apartment_number,
            status = excluded.status,
            comment = excluded.comment,
            verified_by = excluded.verified_by,
            verified_at = excluded.verified_at,
            updated_at = excluded.updated_at
    """, (
        member.apartment_id,
        member.apartment_number,
        status,
        comment,
        int(verified_by) if verified_by else None,
        verified_at,
        now(),
        now(),
    ))

    conn.commit()
    conn.close()

    log_operator_action(
        operator_telegram_id=verified_by,
        operator_username=operator_username,
        operator_name=operator_name,
        action_type="apartment_verification_status_update",
        entity_type="apartment",
        entity_id=member.apartment_id,
        apartment_number=member.apartment_number,
        old_value=old_status,
        new_value=status,
        comment=comment,
    )

    return True, {
        "status": status,
        "apartment_id": member.apartment_id,
        "apartment_number": member.apartment_number,
        "group_code": context["resolution"].group_code,
    }


def apply_agreement_suggestion(
    apartment_number,
    verified_by=None,
    selected_apartment_number=None,
):
    """
    Защита от массового действия.

    Автоприменение предложения к логической группе запрещено, пока оператор
    не выберет одного physical member. Для обычной единицы сохраняется
    ранее существовавшая логика.
    """
    member, state, _context = resolve_physical_unit_for_write(
        apartment_number,
        selected_apartment_number=selected_apartment_number,
    )

    if state != "ok":
        return False, state

    # В legacy коде member номер мог снова раскрыться в группу. Поэтому
    # для group-сценария не вызываем старый авто-apply: это отдельная
    # будущая форма с явным выбором и отдельными источниками по member.
    if _context["is_group"]:
        return False, "group_auto_apply_requires_dedicated_member_workflow"

    # Для одиночной unit прежняя проверенная логика сохраняется.
    return _legacy_apply_agreement_suggestion(
        member.apartment_number,
        verified_by=verified_by,
    )


def unit_resolver_db_access_self_test():
    """
    Read-only диагностика интеграции db_access -> unit_resolver.
    Используется из небольшой CLI команды или интерактивной проверки.
    """
    conn = get_conn()
    conn.row_factory = sqlite3.Row

    try:
        if not _ur_table_exists(conn, "unit_group_aliases"):
            return {
                "ok": False,
                "error": "unit_group_aliases_not_found",
                "tests": [],
            }

        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT alias_text
            FROM unit_group_aliases
            WHERE is_active = 1
            ORDER BY alias_text
        """)
        aliases = [_ur_text(row[0]) for row in cur.fetchall()]
    finally:
        conn.close()

    tests = []
    for alias in aliases:
        context = resolve_unit_context(alias)
        result = context["resolution"]
        tests.append({
            "alias": alias,
            "kind": result.kind,
            "group_code": result.group_code,
            "members": context["lookup_numbers"],
            "ok": result.is_group and len(context["lookup_numbers"]) >= 2,
        })

    return {
        "ok": all(item["ok"] for item in tests),
        "error": None,
        "tests": tests,
    }
