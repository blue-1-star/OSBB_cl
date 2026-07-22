
from pathlib import Path
import sqlite3
import sys
import re

from telegram import Update, ReplyKeyboardMarkup

BOT_HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = BOT_HANDLERS_DIR.parent
OSBB_ROOT = BOTS_DIR.parent
PY_ROOT = OSBB_ROOT.parent

for p in [str(OSBB_ROOT), str(PY_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from config import paths, USE_TEST_DB
from audit_logger import audit_field_change
from Bots.db_access import create_vehicle_for_apartment


SEARCH_MENU = [
    ["🔎 Найти авто", "➕ Добавить автомобиль"],
    ["📦 Архив автомобилей"],
    ["⬅️ Назад", "🏠 Главное меню"],
]

VEHICLE_CARD_MENU = [
    ["✏️ Номер", "✏️ Марка"],
    ["✏️ Тариф", "🏠 Квартира"],
    ["📦 Архивировать"],
    ["🔎 Найти авто", "📦 Архив автомобилей"],
    ["⬅️ Назад", "🏠 Главное меню"],
]

ARCHIVED_CARD_MENU = [
    ["♻️ Восстановить автомобиль"],
    ["📦 Архив автомобилей", "🔎 Найти авто"],
    ["⬅️ Назад", "🏠 Главное меню"],
]

ARCHIVE_REASON_MENU = [
    ["Продан", "Больше не паркуется"],
    ["Смена владельца", "Дубликат"],
    ["Ошибка импорта", "Другое"],
    ["⬅️ Назад"],
]

VEHICLE_TARIFF_MENU = [
    ["☀️ Day", "🌙 Night"],
    ["🚫 Не паркуется", "❓ NULL"],
    ["⬅️ Назад"],
]


def kb(rows):
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_db_file():
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def get_conn():
    conn = sqlite3.connect(get_db_file())
    conn.row_factory = sqlite3.Row
    return conn



def ensure_vehicle_lifecycle_schema(conn):
    cur = conn.cursor()
    cols = set(table_columns(cur, "vehicles"))
    additions = {
        "lifecycle_status": "TEXT NOT NULL DEFAULT 'ACTIVE'",
        "review_status": "TEXT NOT NULL DEFAULT 'VERIFIED'",
        "archived_at": "TEXT",
        "archive_reason": "TEXT",
        "archived_by": "TEXT",
        "created_source": "TEXT",
        "plate_format_status": "TEXT",
    }
    for name, ddl in additions.items():
        if name not in cols:
            cur.execute(f"ALTER TABLE vehicles ADD COLUMN {name} {ddl}")
    conn.commit()


def get_vehicle_columns(conn):
    ensure_vehicle_lifecycle_schema(conn)
    return set(table_columns(conn.cursor(), "vehicles"))


def audit_vehicle_create(conn, vehicle_id, operator_id, plate, apartment_number, review_status):
    audit_field_change(
        conn=conn,
        table_name="vehicles",
        row_id=vehicle_id,
        field_name="created",
        old_value=None,
        new_value=f"plate={plate}; apartment={apartment_number}; review_status={review_status}",
        operator_id=operator_id,
        user_id=operator_id,
        actor_type="operator",
        action_type="vehicle_create",
        source_context="vehicle_registry_bot",
        comment="Создание автомобиля через реестр автомобилей",
    )


def create_registry_vehicle(apartment_number, plate, model, parking_time, operator_id, needs_review=False):
    ok, result = create_vehicle_for_apartment(
        apartment_number=apartment_number,
        plate=plate,
        model=model,
        parking_time=parking_time,
    )
    if not ok:
        return False, result

    vehicle_id = result["vehicle_id"]
    conn = get_conn()
    try:
        ensure_vehicle_lifecycle_schema(conn)
        review_status = "NEEDS_REVIEW" if needs_review else "VERIFIED"
        conn.execute(
            """
            UPDATE vehicles
               SET lifecycle_status='ACTIVE',
                   review_status=?,
                   archived_at=NULL,
                   archive_reason=NULL,
                   archived_by=NULL,
                   created_source='vehicle_registry_bot'
             WHERE id=?
            """,
            (review_status, vehicle_id),
        )
        audit_vehicle_create(
            conn, vehicle_id, operator_id,
            result.get("plate") or plate,
            apartment_number,
            review_status,
        )
        conn.commit()
    finally:
        conn.close()
    result["review_status"] = review_status
    return True, result


def create_draft_vehicle(plate, model, parking_time, operator_id):
    """Create a vehicle draft when the apartment is still unknown.

    The payment can be linked to this vehicle immediately. Later the same row
    is completed with a full plate and apartment; payment history stays attached.
    """
    plate = normalize_plate(plate)
    model = (model or "").strip().upper() or None
    if not plate:
        return False, "empty_plate"
    if parking_time not in {"Day", "Night", "Inactive"}:
        parking_time = None

    conn = get_conn()
    try:
        ensure_vehicle_lifecycle_schema(conn)
        cols = get_vehicle_columns(conn)
        fields = ["apartment_id", "license_plate", "license_plate_normalized", "car_model", "car_model_normalized", "parking_time"]
        values = [None, plate, plate, model, model, parking_time]
        optional = {
            "status": "DRAFT",
            "source": "cashier_or_registry_draft",
            "notes": "Черновик автомобиля: квартира и/или полный номер требуют уточнения",
            "created_at": None,
            "created_by": str(operator_id),
            "plate_format_status": "VALID" if is_valid_ua_plate(plate) else "PARTIAL",
            "lifecycle_status": "ACTIVE",
            "review_status": "NEEDS_REVIEW",
            "created_source": "vehicle_registry_bot",
        }
        for name, value in optional.items():
            if name not in cols:
                continue
            fields.append(name)
            if name == "created_at":
                values.append(None)
            else:
                values.append(value)
        placeholders = ", ".join("?" for _ in fields)
        conn.execute(
            f"INSERT INTO vehicles ({', '.join(fields)}) VALUES ({placeholders})",
            values,
        )
        vehicle_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        if "created_at" in cols:
            conn.execute("UPDATE vehicles SET created_at=COALESCE(created_at, CURRENT_TIMESTAMP) WHERE id=?", (vehicle_id,))
        audit_vehicle_create(conn, vehicle_id, operator_id, plate, "неизвестна", "NEEDS_REVIEW")
        conn.commit()
        return True, {
            "vehicle_id": vehicle_id,
            "action": "draft_created",
            "plate": plate,
            "model": model,
            "parking_time": parking_time,
            "review_status": "NEEDS_REVIEW",
            "apartment_number": None,
        }
    except Exception as exc:
        conn.rollback()
        return False, str(exc)
    finally:
        conn.close()


def refresh_vehicle_review_status(conn, vehicle_id):
    """Promote a draft to VERIFIED when both full plate and apartment exist."""
    row = conn.execute(
        """
        SELECT apartment_id, COALESCE(license_plate_normalized, license_plate, '') AS plate,
               COALESCE(review_status, 'VERIFIED') AS review_status
          FROM vehicles
         WHERE id=?
        """,
        (vehicle_id,),
    ).fetchone()
    if not row:
        return None
    full = bool(row["apartment_id"]) and is_valid_ua_plate(row["plate"])
    new_status = "VERIFIED" if full else "NEEDS_REVIEW"
    conn.execute(
        "UPDATE vehicles SET review_status=?, plate_format_status=? WHERE id=?",
        (new_status, "VALID" if is_valid_ua_plate(row["plate"]) else "PARTIAL", vehicle_id),
    )
    return new_status


def archive_vehicle(vehicle_id, reason, operator_id):
    conn = get_conn()
    try:
        ensure_vehicle_lifecycle_schema(conn)
        row = conn.execute(
            "SELECT lifecycle_status, archive_reason FROM vehicles WHERE id=?",
            (vehicle_id,),
        ).fetchone()
        if not row:
            raise RuntimeError(f"vehicle_id not found: {vehicle_id}")
        old_status = row["lifecycle_status"] or "ACTIVE"
        conn.execute(
            """
            UPDATE vehicles
               SET lifecycle_status='ARCHIVED',
                   archived_at=CURRENT_TIMESTAMP,
                   archive_reason=?,
                   archived_by=?
             WHERE id=?
            """,
            (reason, str(operator_id), vehicle_id),
        )
        audit_field_change(
            conn=conn,
            table_name="vehicles",
            row_id=vehicle_id,
            field_name="lifecycle_status",
            old_value=old_status,
            new_value="ARCHIVED",
            operator_id=operator_id,
            user_id=operator_id,
            actor_type="operator",
            action_type="vehicle_archive",
            source_context="vehicle_registry_bot",
            comment=f"Автомобиль выведен из активного реестра. Причина: {reason}",
        )
        conn.commit()
    finally:
        conn.close()


def restore_vehicle(vehicle_id, operator_id):
    conn = get_conn()
    try:
        ensure_vehicle_lifecycle_schema(conn)
        row = conn.execute(
            "SELECT lifecycle_status FROM vehicles WHERE id=?",
            (vehicle_id,),
        ).fetchone()
        if not row:
            raise RuntimeError(f"vehicle_id not found: {vehicle_id}")
        old_status = row["lifecycle_status"] or "ARCHIVED"
        conn.execute(
            """
            UPDATE vehicles
               SET lifecycle_status='ACTIVE',
                   archived_at=NULL,
                   archive_reason=NULL,
                   archived_by=NULL
             WHERE id=?
            """,
            (vehicle_id,),
        )
        audit_field_change(
            conn=conn,
            table_name="vehicles",
            row_id=vehicle_id,
            field_name="lifecycle_status",
            old_value=old_status,
            new_value="ACTIVE",
            operator_id=operator_id,
            user_id=operator_id,
            actor_type="operator",
            action_type="vehicle_restore",
            source_context="vehicle_registry_bot",
            comment="Автомобиль восстановлен из архива",
        )
        conn.commit()
    finally:
        conn.close()


def normalize_plate(value):
    text = "" if value is None else str(value).strip().upper()
    cyr_to_lat = str.maketrans({
        "А": "A", "В": "B", "Е": "E", "І": "I", "К": "K",
        "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C",
        "Т": "T", "Х": "X", "У": "Y",
    })
    text = text.translate(cyr_to_lat)
    text = re.sub(r"[^A-Z0-9]", "", text)
    if re.fullmatch(r"O\d{3}", text):
        text = "0" + text[1:]
    return text


def is_valid_ua_plate(plate):
    return re.fullmatch(r"[A-Z]{2}\d{4}[A-Z]{2}", normalize_plate(plate)) is not None


def norm_apartment(value):
    text = "" if value is None else str(value).strip()
    return text[:-2] if text.endswith(".0") else text


def table_columns(cur, table_name):
    cur.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cur.fetchall()]


def get_apartment_id(cur, apartment_number):
    apt = norm_apartment(apartment_number)
    cur.execute("SELECT id FROM apartments WHERE apartment_number = ?", (apt,))
    row = cur.fetchone()
    return row["id"] if row else None


def search_vehicles(query, limit=20, archived=False):
    q = (query or "").strip()
    q_plate = normalize_plate(q)
    q_like = f"%{q}%"
    q_plate_like = f"%{q_plate}%"

    where = []
    params = []

    if q.isdigit():
        where.append("a.apartment_number = ?")
        params.append(q)

    if q_plate:
        where.append("COALESCE(v.license_plate_normalized, v.license_plate, '') LIKE ?")
        params.append(q_plate_like)

    if q:
        where.append("COALESCE(v.car_model_normalized, v.car_model, '') LIKE ?")
        params.append(q_like)

    if not where:
        return []

    conn = get_conn()
    ensure_vehicle_lifecycle_schema(conn)
    cur = conn.cursor()
    lifecycle_clause = "COALESCE(v.lifecycle_status, 'ACTIVE') = 'ARCHIVED'" if archived else "COALESCE(v.lifecycle_status, 'ACTIVE') <> 'ARCHIVED'"
    params.append(limit)

    cur.execute(f"""
        SELECT
            v.id AS vehicle_id,
            a.apartment_number,
            COALESCE(v.license_plate_normalized, v.license_plate, '') AS plate,
            COALESCE(v.car_model_normalized, v.car_model, '') AS model,
            COALESCE(v.parking_time, '') AS parking_time,
            COALESCE(v.lifecycle_status, 'ACTIVE') AS lifecycle_status,
            COALESCE(v.review_status, 'VERIFIED') AS review_status,
            v.archived_at,
            v.archive_reason
        FROM vehicles v
        LEFT JOIN apartments a ON a.id = v.apartment_id
        WHERE ({" OR ".join(where)})
          AND {lifecycle_clause}
        ORDER BY
            CASE WHEN a.apartment_number GLOB '[0-9]*'
                 THEN CAST(a.apartment_number AS INTEGER)
                 ELSE 999999 END,
            a.apartment_number,
            v.id
        LIMIT ?
    """, tuple(params))

    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def get_vehicle(vehicle_id):
    conn = get_conn()
    ensure_vehicle_lifecycle_schema(conn)
    cur = conn.cursor()
    cur.execute("""
        SELECT
            v.id AS vehicle_id,
            v.apartment_id,
            a.apartment_number,
            v.license_plate,
            v.license_plate_normalized,
            COALESCE(v.license_plate_normalized, v.license_plate, '') AS plate,
            v.car_model,
            v.car_model_normalized,
            COALESCE(v.car_model_normalized, v.car_model, '') AS model,
            COALESCE(v.parking_time, '') AS parking_time,
            COALESCE(v.lifecycle_status, 'ACTIVE') AS lifecycle_status,
            COALESCE(v.review_status, 'VERIFIED') AS review_status,
            v.archived_at,
            v.archive_reason,
            v.created_source,
            v.plate_format_status
        FROM vehicles v
        LEFT JOIN apartments a ON a.id = v.apartment_id
        WHERE v.id = ?
    """, (vehicle_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def format_vehicle_line(row, idx=None):
    prefix = f"{idx}. " if idx is not None else ""
    return (
        f"{prefix}id={row['vehicle_id']} | кв.{row.get('apartment_number') or 'неизвестна'} | "
        f"{row.get('plate') or '-'} | {row.get('model') or '-'} | "
        f"{row.get('parking_time') or 'NULL'} | {row.get('lifecycle_status') or 'ACTIVE'}"
    )


def format_search_results(rows):
    if not rows:
        return (
            "🔎 Ничего не найдено.\n\n"
            "Можно искать по номеру авто, квартире или марке.\n"
            "Например: 122, AA4712, BMW"
        )

    lines = ["🔎 Найдены автомобили:", ""]
    for idx, row in enumerate(rows, start=1):
        lines.append(format_vehicle_line(row, idx))
    lines.append("")
    lines.append("Чтобы открыть карточку, отправьте номер строки: 1, 2, 3...")
    return "\n".join(lines)


def format_vehicle_card(vehicle):
    if not vehicle:
        return "Автомобиль не найден."

    lines = [
        "🚗 Карточка авто",
        "",
        f"vehicle_id: {vehicle['vehicle_id']}",
        f"Квартира: {vehicle.get('apartment_number') or 'неизвестна'}",
        f"Номер: {vehicle.get('plate') or '-'}",
        f"Марка: {vehicle.get('model') or '-'}",
        f"Тариф: {vehicle.get('parking_time') or 'NULL'}",
        f"Статус: {vehicle.get('lifecycle_status') or 'ACTIVE'}",
        f"Проверка: {vehicle.get('review_status') or 'VERIFIED'}",
    ]
    if (vehicle.get("review_status") or "VERIFIED") == "NEEDS_REVIEW":
        lines.append("🟡 Черновик: требуется уточнить квартиру и/или полный номер")
    if vehicle.get("archive_reason"):
        lines.append(f"Причина архива: {vehicle.get('archive_reason')}")
    if vehicle.get("archived_at"):
        lines.append(f"Архивирован: {vehicle.get('archived_at')}")
    lines.extend(["", "Выберите действие."])
    return "\n".join(lines)


def update_vehicle_field(vehicle_id, field_name, new_value, operator_id, source_context):
    allowed = {
        "license_plate",
        "license_plate_normalized",
        "car_model",
        "car_model_normalized",
        "parking_time",
        "apartment_id",
    }
    if field_name not in allowed:
        raise RuntimeError(f"Field not allowed: {field_name}")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT {field_name} FROM vehicles WHERE id = ?", (vehicle_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise RuntimeError(f"vehicle_id not found: {vehicle_id}")

    old_value = row[field_name]
    cur.execute(f"UPDATE vehicles SET {field_name} = ? WHERE id = ?", (new_value, vehicle_id))

    audit_field_change(
        conn=conn,
        table_name="vehicles",
        row_id=vehicle_id,
        field_name=field_name,
        old_value=old_value,
        new_value=new_value,
        operator_id=operator_id,
        user_id=operator_id,
        actor_type="operator",
        action_type="vehicle_card_edit",
        source_context=source_context,
        comment="Редактирование карточки автомобиля в боте",
    )

    conn.commit()
    conn.close()
    return old_value, new_value


def update_vehicle_plate(vehicle_id, new_plate, operator_id):
    new_plate = normalize_plate(new_plate)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT license_plate, license_plate_normalized
        FROM vehicles
        WHERE id = ?
    """, (vehicle_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise RuntimeError(f"vehicle_id not found: {vehicle_id}")

    old_plate = row["license_plate_normalized"] or row["license_plate"] or ""

    cols = table_columns(cur, "vehicles")
    sets = []
    params = []
    if "license_plate" in cols:
        sets.append("license_plate = ?")
        params.append(new_plate)
    if "license_plate_normalized" in cols:
        sets.append("license_plate_normalized = ?")
        params.append(new_plate)

    params.append(vehicle_id)
    cur.execute(f"UPDATE vehicles SET {', '.join(sets)} WHERE id = ?", tuple(params))
    refresh_vehicle_review_status(conn, vehicle_id)

    audit_field_change(
        conn=conn,
        table_name="vehicles",
        row_id=vehicle_id,
        field_name="license_plate",
        old_value=old_plate,
        new_value=new_plate,
        operator_id=operator_id,
        user_id=operator_id,
        actor_type="operator",
        action_type="vehicle_card_edit_plate",
        source_context="vehicle_card_editor_bot",
        comment="Исправление номера автомобиля через карточку авто",
    )

    conn.commit()
    conn.close()
    return old_plate, new_plate


def update_vehicle_model(vehicle_id, new_model, operator_id):
    value = (new_model or "").strip().upper()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT car_model, car_model_normalized
        FROM vehicles
        WHERE id = ?
    """, (vehicle_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise RuntimeError(f"vehicle_id not found: {vehicle_id}")

    old_model = row["car_model_normalized"] or row["car_model"] or ""

    cols = table_columns(cur, "vehicles")
    sets = []
    params = []
    if "car_model" in cols:
        sets.append("car_model = ?")
        params.append(value)
    if "car_model_normalized" in cols:
        sets.append("car_model_normalized = ?")
        params.append(value)

    params.append(vehicle_id)
    cur.execute(f"UPDATE vehicles SET {', '.join(sets)} WHERE id = ?", tuple(params))

    audit_field_change(
        conn=conn,
        table_name="vehicles",
        row_id=vehicle_id,
        field_name="car_model",
        old_value=old_model,
        new_value=value,
        operator_id=operator_id,
        user_id=operator_id,
        actor_type="operator",
        action_type="vehicle_card_edit_model",
        source_context="vehicle_card_editor_bot",
        comment="Исправление марки автомобиля через карточку авто",
    )

    conn.commit()
    conn.close()
    return old_model, value


def update_vehicle_apartment(vehicle_id, new_apt, operator_id):
    new_apt = norm_apartment(new_apt)

    conn = get_conn()
    cur = conn.cursor()
    new_apartment_id = get_apartment_id(cur, new_apt)
    if not new_apartment_id:
        conn.close()
        raise RuntimeError(f"Квартира не найдена: {new_apt}")

    cur.execute("""
        SELECT v.apartment_id, a.apartment_number
        FROM vehicles v
        LEFT JOIN apartments a ON a.id = v.apartment_id
        WHERE v.id = ?
    """, (vehicle_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise RuntimeError(f"vehicle_id not found: {vehicle_id}")

    old_apt = row["apartment_number"] or "неизвестна"
    cur.execute("UPDATE vehicles SET apartment_id = ? WHERE id = ?", (new_apartment_id, vehicle_id))
    refresh_vehicle_review_status(conn, vehicle_id)

    audit_field_change(
        conn=conn,
        table_name="vehicles",
        row_id=vehicle_id,
        field_name="apartment_id",
        old_value=old_apt,
        new_value=new_apt,
        operator_id=operator_id,
        user_id=operator_id,
        actor_type="operator",
        action_type="vehicle_card_edit_apartment",
        source_context="vehicle_card_editor_bot",
        comment="Перенос автомобиля в другую квартиру через карточку авто",
    )

    conn.commit()
    conn.close()
    return old_apt, new_apt


async def show_vehicle_search_start(update: Update, user_states, user_id):
    state = user_states.setdefault(user_id, {})
    state["mode"] = "vehicle_card_search"
    state.pop("vehicle_search_results", None)
    state.pop("vehicle_edit_action", None)

    await update.message.reply_text(
        "🔎 Найти авто\n\n"
        "Введите номер авто, номер квартиры или марку.\n"
        "Например:\n"
        "AA4712\n"
        "122\n"
        "BMW",
        reply_markup=kb(SEARCH_MENU),
    )


async def show_vehicle_card(update: Update, user_states, user_id, vehicle_id):
    vehicle = get_vehicle(vehicle_id)
    state = user_states.setdefault(user_id, {})
    state["mode"] = "vehicle_card_open"
    state["vehicle_card_id"] = vehicle_id
    state.pop("vehicle_edit_action", None)

    await update.message.reply_text(
        format_vehicle_card(vehicle),
        reply_markup=kb(ARCHIVED_CARD_MENU if vehicle and vehicle.get("lifecycle_status") == "ARCHIVED" else VEHICLE_CARD_MENU),
    )


async def handle_search_query(update: Update, user_states, user_id, text):
    rows = search_vehicles(text, limit=20)
    state = user_states.setdefault(user_id, {})
    state["vehicle_search_results"] = rows
    state["mode"] = "vehicle_card_search_results"

    await update.message.reply_text(
        format_search_results(rows),
        reply_markup=kb(SEARCH_MENU),
    )


async def ask_edit_value(update: Update, user_states, user_id, action):
    state = user_states.setdefault(user_id, {})
    vehicle_id = state.get("vehicle_card_id")
    vehicle = get_vehicle(vehicle_id)

    if not vehicle:
        await update.message.reply_text("Карточка авто не найдена.")
        return

    state["vehicle_edit_action"] = action
    state["mode"] = "vehicle_card_wait_value"

    if action == "plate":
        prompt = f"✏️ Номер авто\n\nТекущий номер: {vehicle.get('plate') or '-'}\nВведите новый номер."
        menu = [["⬅️ Назад"]]
    elif action == "model":
        prompt = f"✏️ Марка авто\n\nТекущая марка: {vehicle.get('model') or '-'}\nВведите новую марку."
        menu = [["⬅️ Назад"]]
    elif action == "apartment":
        prompt = f"🏠 Квартира\n\nТекущая квартира: {vehicle.get('apartment_number')}\nВведите новую квартиру."
        menu = [["⬅️ Назад"]]
    elif action == "tariff":
        prompt = f"✏️ Тариф / статус парковки\n\nТекущее значение: {vehicle.get('parking_time') or 'NULL'}\nВыберите новое значение."
        menu = VEHICLE_TARIFF_MENU
    else:
        prompt = "Неизвестное действие."
        menu = VEHICLE_CARD_MENU

    await update.message.reply_text(prompt, reply_markup=kb(menu))


async def apply_edit_value(update: Update, user_states, user_id, text):
    state = user_states.setdefault(user_id, {})
    vehicle_id = state.get("vehicle_card_id")
    action = state.get("vehicle_edit_action")

    if not vehicle_id or not action:
        await update.message.reply_text("Нет активного редактирования.")
        return

    value = (text or "").strip()

    try:
        if action == "plate":
            new_plate = normalize_plate(value)
            if not is_valid_ua_plate(new_plate):
                await update.message.reply_text(
                    "⚠️ Номер не похож на полный украинский номер.\n"
                    "Ожидаемый формат: AA1234BB.\n\n"
                    "Введите номер ещё раз или нажмите ⬅️ Назад."
                )
                return
            old, new = update_vehicle_plate(vehicle_id, new_plate, user_id)

        elif action == "model":
            old, new = update_vehicle_model(vehicle_id, value, user_id)

        elif action == "apartment":
            old, new = update_vehicle_apartment(vehicle_id, value, user_id)

        elif action == "tariff":
            mapping = {
                "☀️ Day": "Day",
                "🌙 Night": "Night",
                "🚫 Не паркуется": "Inactive",
                "❓ NULL": None,
                "Day": "Day",
                "Night": "Night",
                "Inactive": "Inactive",
                "NULL": None,
            }
            if value not in mapping:
                await update.message.reply_text("Выберите тариф кнопкой.")
                return
            old, new = update_vehicle_field(
                vehicle_id=vehicle_id,
                field_name="parking_time",
                new_value=mapping[value],
                operator_id=user_id,
                source_context="vehicle_card_editor_bot",
            )
        else:
            await update.message.reply_text("Неизвестное действие.")
            return

    except Exception as exc:
        await update.message.reply_text(f"Ошибка сохранения: {exc}")
        return

    state["mode"] = "vehicle_card_open"
    state.pop("vehicle_edit_action", None)

    await update.message.reply_text(
        "✅ Изменение сохранено\n\n"
        f"Было: {old if old not in [None, ''] else 'NULL'}\n"
        f"Стало: {new if new not in [None, ''] else 'NULL'}"
    )
    await show_vehicle_card(update, user_states, user_id, vehicle_id)



async def start_vehicle_create(update: Update, user_states, user_id):
    state = user_states.setdefault(user_id, {})
    state.clear()
    state.update({"mode": "vehicle_create_plate", "vehicle_create": {}})
    await update.message.reply_text(
        "➕ Новый автомобиль\n\nВведите полный номер или известный фрагмент госномера.\n"
        "Например: 8092 или AA8092KI.",
        reply_markup=kb([["⬅️ Назад", "🏠 Главное меню"]]),
    )


async def handle_vehicle_create_step(update: Update, user_states, user_id, text):
    state = user_states.setdefault(user_id, {})
    draft = state.setdefault("vehicle_create", {})
    mode = state.get("mode")

    if mode == "vehicle_create_plate":
        plate = normalize_plate(text)
        if not plate:
            await update.message.reply_text("Номер пуст. Введите номер или известный фрагмент.")
            return True
        draft["plate"] = plate
        draft["needs_review"] = not is_valid_ua_plate(plate)
        state["mode"] = "vehicle_create_apartment"
        await update.message.reply_text(
            "Введите номер квартиры, если он известен.\n"
            "Если квартира пока неизвестна — нажмите «Пропустить».",
            reply_markup=kb([["⏭ Пропустить"], ["⬅️ Назад", "🏠 Главное меню"]]),
        )
        return True

    if mode == "vehicle_create_apartment":
        if text == "⏭ Пропустить":
            draft["apartment_number"] = None
            draft["needs_review"] = True
            state["mode"] = "vehicle_create_tariff"
            await update.message.reply_text(
                "Выберите режим парковки.",
                reply_markup=kb([["🌙 Night", "☀️ Day"], ["🚫 Не паркуется"], ["⬅️ Назад"]]),
            )
            return True

        conn = get_conn()
        try:
            apt_id = get_apartment_id(conn.cursor(), text)
        finally:
            conn.close()
        if not apt_id:
            await update.message.reply_text(
                "Квартира не найдена. Введите номер ещё раз или нажмите «Пропустить».",
                reply_markup=kb([["⏭ Пропустить"], ["⬅️ Назад", "🏠 Главное меню"]]),
            )
            return True
        draft["apartment_number"] = norm_apartment(text)
        state["mode"] = "vehicle_create_tariff"
        await update.message.reply_text(
            "Выберите режим парковки.",
            reply_markup=kb([["🌙 Night", "☀️ Day"], ["🚫 Не паркуется"], ["⬅️ Назад"]]),
        )
        return True

    if mode == "vehicle_create_tariff":
        mapping = {"🌙 Night": "Night", "☀️ Day": "Day", "🚫 Не паркуется": "Inactive"}
        if text not in mapping:
            await update.message.reply_text("Выберите режим кнопкой.")
            return True
        draft["parking_time"] = mapping[text]
        state["mode"] = "vehicle_create_model"
        await update.message.reply_text(
            "Введите марку/модель или нажмите Пропустить.",
            reply_markup=kb([["⏭ Пропустить"], ["⬅️ Назад"]]),
        )
        return True

    if mode == "vehicle_create_model":
        draft["model"] = None if text == "⏭ Пропустить" else text
        if draft.get("apartment_number"):
            ok, result = create_registry_vehicle(
                apartment_number=draft["apartment_number"],
                plate=draft["plate"],
                model=draft.get("model"),
                parking_time=draft["parking_time"],
                operator_id=user_id,
                needs_review=draft.get("needs_review", False),
            )
        else:
            ok, result = create_draft_vehicle(
                plate=draft["plate"],
                model=draft.get("model"),
                parking_time=draft["parking_time"],
                operator_id=user_id,
            )
        if not ok:
            await update.message.reply_text(f"Ошибка создания: {result}")
            return True
        await update.message.reply_text(
            "✅ Автомобиль создан" +
            ("\n🟡 Требуется уточнить квартиру и/или полный номер."
             if result.get("review_status") == "NEEDS_REVIEW" else "")
        )
        await show_vehicle_card(update, user_states, user_id, result["vehicle_id"])
        return True

    return False


async def show_archive_search(update: Update, user_states, user_id):
    state = user_states.setdefault(user_id, {})
    state.clear()
    state["mode"] = "vehicle_archive_search"
    await update.message.reply_text(
        "📦 Архив автомобилей\n\nВведите номер авто, квартиру или марку.",
        reply_markup=kb([["⬅️ Назад", "🏠 Главное меню"]]),
    )


async def show_archive_reason(update: Update, user_states, user_id):
    state = user_states.setdefault(user_id, {})
    state["mode"] = "vehicle_archive_reason"
    await update.message.reply_text("Выберите причину архивирования.", reply_markup=kb(ARCHIVE_REASON_MENU))

async def handle_vehicle_card_editor_text(update: Update, user_states, user_id, text):
    normalized = (text or "").strip()
    state = user_states.setdefault(user_id, {})
    mode = state.get("mode")

    if normalized == "🔎 Найти авто":
        await show_vehicle_search_start(update, user_states, user_id)
        return True

    if normalized == "➕ Добавить автомобиль":
        await start_vehicle_create(update, user_states, user_id)
        return True

    if normalized == "📦 Архив автомобилей":
        await show_archive_search(update, user_states, user_id)
        return True

    if mode and mode.startswith("vehicle_card"):
        if normalized == "⬅️ Назад":
            if state.get("vehicle_card_id"):
                await show_vehicle_card(update, user_states, user_id, state["vehicle_card_id"])
            else:
                await show_vehicle_search_start(update, user_states, user_id)
            return True

        if normalized in {"🏠 Главное меню", "🚗 Автомобили"}:
            state["mode"] = ""
            return False

    if mode and mode.startswith("vehicle_create_"):
        if normalized == "⬅️ Назад":
            await show_vehicle_search_start(update, user_states, user_id)
            return True
        return await handle_vehicle_create_step(update, user_states, user_id, normalized)

    if mode == "vehicle_archive_search":
        rows = search_vehicles(normalized, limit=20, archived=True)
        state["vehicle_search_results"] = rows
        state["mode"] = "vehicle_archive_results"
        await update.message.reply_text(format_search_results(rows), reply_markup=kb([["⬅️ Назад"]]))
        return True

    if mode == "vehicle_archive_results":
        if normalized.isdigit():
            idx = int(normalized) - 1
            rows = state.get("vehicle_search_results") or []
            if 0 <= idx < len(rows):
                await show_vehicle_card(update, user_states, user_id, rows[idx]["vehicle_id"])
                return True
        await update.message.reply_text("Выберите номер строки из списка.")
        return True

    if mode == "vehicle_archive_reason":
        if normalized == "⬅️ Назад":
            await show_vehicle_card(update, user_states, user_id, state.get("vehicle_card_id"))
            return True
        reason = normalized
        if reason not in {"Продан", "Больше не паркуется", "Смена владельца", "Дубликат", "Ошибка импорта", "Другое"}:
            await update.message.reply_text("Выберите причину кнопкой.", reply_markup=kb(ARCHIVE_REASON_MENU))
            return True
        archive_vehicle(state.get("vehicle_card_id"), reason, user_id)
        await update.message.reply_text("✅ Автомобиль перенесён в архив. История и платежи сохранены.")
        await show_vehicle_card(update, user_states, user_id, state.get("vehicle_card_id"))
        return True

    if mode == "vehicle_card_search":
        await handle_search_query(update, user_states, user_id, normalized)
        return True

    if mode == "vehicle_card_search_results":
        if normalized.isdigit():
            idx = int(normalized) - 1
            rows = state.get("vehicle_search_results") or []
            if 0 <= idx < len(rows):
                await show_vehicle_card(update, user_states, user_id, rows[idx]["vehicle_id"])
                return True
            await update.message.reply_text("Нет такого номера в списке.")
            return True

        await handle_search_query(update, user_states, user_id, normalized)
        return True

    if mode == "vehicle_card_open":
        if normalized == "✏️ Номер":
            await ask_edit_value(update, user_states, user_id, "plate")
            return True
        if normalized == "✏️ Марка":
            await ask_edit_value(update, user_states, user_id, "model")
            return True
        if normalized == "✏️ Тариф":
            await ask_edit_value(update, user_states, user_id, "tariff")
            return True
        if normalized == "🏠 Квартира":
            await ask_edit_value(update, user_states, user_id, "apartment")
            return True
        if normalized == "📦 Архивировать":
            await show_archive_reason(update, user_states, user_id)
            return True
        if normalized == "♻️ Восстановить автомобиль":
            restore_vehicle(state.get("vehicle_card_id"), user_id)
            await update.message.reply_text("✅ Автомобиль восстановлен в активном реестре.")
            await show_vehicle_card(update, user_states, user_id, state.get("vehicle_card_id"))
            return True
        return False

    if mode == "vehicle_card_wait_value":
        await apply_edit_value(update, user_states, user_id, normalized)
        return True

    return False
