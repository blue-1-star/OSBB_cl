"""
Операторский редактор реестра помещений.

Работает через Telegram-бот:
- показывает коммерческие и технические помещения;
- создаёт новые помещения;
- позволяет оператору заполнить название, контакт, телефон, площадь,
  официальный номер и заметку;
- подтверждает/возвращает в черновик;
- фиксирует все изменения в operator_audit_log.

Интеграция:
    from handlers.unit_registry_editor import handle_unit_registry_editor_text

    if await handle_unit_registry_editor_text(update, user_states, user_id, text):
        return

Кнопка входа:
    🏢 Помещения
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

from telegram import Update, ReplyKeyboardMarkup

BOT_HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = BOT_HANDLERS_DIR.parent
OSBB_ROOT = BOTS_DIR.parent
PY_ROOT = OSBB_ROOT.parent

for folder in (BOTS_DIR, OSBB_ROOT, PY_ROOT):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from config import paths, USE_TEST_DB
from audit_logger import audit_field_change, audit_log

try:
    from handlers.commercial_contract_editor import handle_commercial_contract_editor_text
except Exception:
    try:
        from commercial_contract_editor import handle_commercial_contract_editor_text
    except Exception:
        handle_commercial_contract_editor_text = None

try:
    from Bots.db_access import can_write
except Exception:
    try:
        from db_access import can_write
    except Exception:
        can_write = None


UNIT_REGISTRY_MENU = [
    ["🏪 Коммерческие", "🛠 Технические"],
    ["➕ Добавить помещение"],
    ["⬅️ Назад", "🏠 Главное меню"],
]

def unit_card_menu(unit: dict | None) -> list[list[str]]:
    rows = [
        ["✏️ Название", "✏️ Контакт"],
        ["✏️ Телефон", "✏️ Площадь"],
        ["✏️ Офиц. №", "📝 Заметка"],
    ]
    if unit and unit.get("unit_type") == "COMMERCIAL":
        rows.append(["📄 Договоры"])
    rows.extend([
        ["✅ Подтвердить", "↩️ Черновик"],
        ["📋 К списку", "🏢 Помещения"],
        ["⬅️ Назад", "🏠 Главное меню"],
    ])
    return rows

UNIT_TYPE_MENU = [
    ["🏪 Коммерческое", "🛠 Техническое"],
    ["⬅️ Назад"],
]

ENTRANCE_MENU = [
    ["1", "2", "3"],
    ["4", "5", "6"],
    ["⬅️ Назад"],
]


def kb(rows):
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# user_states в основном боте исторически содержит разные типы:
# строки, tuple и словари. Поэтому редактор помещений хранит своё
# состояние строго в собственном словаре-маркере и не трогает чужие состояния.
REGISTRY_STATE_MARKER = "unit_registry"


def _is_registry_state(value) -> bool:
    return (
        isinstance(value, dict)
        and value.get("_module") == REGISTRY_STATE_MARKER
    )


def _registry_state(user_states: dict, user_id: int, create: bool = False):
    current = user_states.get(user_id)

    if _is_registry_state(current):
        return current

    if not create:
        return None

    state = {
        "_module": REGISTRY_STATE_MARKER,
        "mode": "unit_registry_menu",
    }
    user_states[user_id] = state
    return state


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_file())
    conn.row_factory = sqlite3.Row
    return conn


def q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cur.fetchone() is not None


def table_columns(cur: sqlite3.Cursor, table_name: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({q(table_name)})")
    return {row[1] for row in cur.fetchall()}


def schema_ready() -> tuple[bool, str]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        if not table_exists(cur, "apartments"):
            return False, "Таблица apartments не найдена."

        fields = table_columns(cur, "apartments")
        required = {
            "unit_type",
            "unit_code",
            "entrance_number",
            "display_name",
            "official_number",
            "area_sqm",
            "record_status",
            "source_note",
            "internal_note",
        }
        missing = sorted(required - fields)
        if missing:
            return (
                False,
                "Не выполнена миграция реестра помещений. "
                "Отсутствуют поля: " + ", ".join(missing),
            )

        if not table_exists(cur, "unit_contacts"):
            return (
                False,
                "Не создана таблица unit_contacts. "
                "Сначала выполните seed_commercial_unit_placeholders.py.",
            )

        return True, ""
    finally:
        conn.close()


def operator_can_edit(user_id: int) -> bool:
    if can_write is None:
        return True
    try:
        return bool(can_write(user_id))
    except Exception:
        return False


def normalize_code(value: Any) -> str:
    raw = text(value).upper().replace(" ", "")
    raw = raw.translate(str.maketrans({
        "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M",
        "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
        "Х": "X", "І": "I",
    }))
    raw = raw.replace("-", "_").replace("/", "_").replace(".", "_")
    raw = re.sub(r"_+", "_", raw)
    return raw.strip("_")


def unit_kind_label(unit_type: str | None) -> str:
    return {
        "RESIDENTIAL": "Жилое",
        "COMMERCIAL": "Коммерческое",
        "TECHNICAL": "Техническое",
    }.get(text(unit_type), text(unit_type) or "-")


def status_label(record_status: str | None) -> str:
    return {
        "DRAFT": "🟡 Черновик",
        "CONFIRMED": "✅ Подтверждено оператором",
        "OFFICIAL": "🔵 Подтверждено документом",
        "LEGACY": "⚪ Историческая запись",
    }.get(text(record_status), text(record_status) or "-")


def get_primary_contact(cur: sqlite3.Cursor, apartment_id: int) -> sqlite3.Row | None:
    cur.execute("""
        SELECT
            id,
            contact_name,
            contact_phone,
            contact_role,
            record_status,
            note
        FROM unit_contacts
        WHERE apartment_id = ?
          AND is_primary = 1
        ORDER BY id
        LIMIT 1
    """, (apartment_id,))
    return cur.fetchone()


def get_unit_by_id(unit_id: int) -> dict | None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                id,
                apartment_number,
                unit_code,
                unit_type,
                entrance_number,
                display_name,
                official_number,
                area_sqm,
                record_status,
                source_note,
                internal_note
            FROM apartments
            WHERE id = ?
        """, (int(unit_id),))
        row = cur.fetchone()
        if not row:
            return None

        unit = dict(row)
        contact = get_primary_contact(cur, int(unit_id))
        unit["primary_contact"] = dict(contact) if contact else None
        return unit
    finally:
        conn.close()


def list_units(unit_type: str) -> list[dict]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                a.id,
                a.apartment_number,
                a.unit_code,
                a.unit_type,
                a.entrance_number,
                a.display_name,
                a.record_status,
                (
                    SELECT uc.contact_name
                    FROM unit_contacts uc
                    WHERE uc.apartment_id = a.id
                      AND uc.is_primary = 1
                    ORDER BY uc.id
                    LIMIT 1
                ) AS contact_name
            FROM apartments a
            WHERE a.unit_type = ?
            ORDER BY
                COALESCE(a.entrance_number, 999),
                a.unit_code,
                a.id
        """, (unit_type,))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_unit_by_code(code: str) -> dict | None:
    norm = normalize_code(code)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id
            FROM apartments
            WHERE unit_code = ?
               OR apartment_number = ?
            ORDER BY id
            LIMIT 1
        """, (norm, norm))
        row = cur.fetchone()
        return get_unit_by_id(int(row["id"])) if row else None
    finally:
        conn.close()


def format_unit_list(unit_type: str, rows: list[dict]) -> str:
    title = "🏪 Коммерческие помещения" if unit_type == "COMMERCIAL" else "🛠 Технические помещения"
    lines = [title, ""]

    if not rows:
        lines.append("Записей пока нет.")
        return "\n".join(lines)

    for index, row in enumerate(rows, start=1):
        code = row.get("unit_code") or row.get("apartment_number") or "-"
        name = row.get("display_name") or "название не указано"
        entrance = row.get("entrance_number") or "-"
        status = status_label(row.get("record_status"))
        lines.append(f"{index}. {code} | подъезд {entrance} | {name} | {status}")

    lines.extend([
        "",
        "Нажмите кнопку с кодом помещения или введите его вручную.",
    ])
    return "\n".join(lines)


def list_buttons(rows: list[dict]) -> list[list[str]]:
    codes = [
        row.get("unit_code") or row.get("apartment_number")
        for row in rows
        if row.get("unit_code") or row.get("apartment_number")
    ]
    buttons = []
    for start in range(0, len(codes), 3):
        buttons.append(codes[start:start + 3])
    buttons.append(["➕ Добавить помещение"])
    buttons.append(["⬅️ Назад", "🏠 Главное меню"])
    return buttons


def format_unit_card(unit: dict | None) -> str:
    if not unit:
        return "Помещение не найдено."

    contact = unit.get("primary_contact") or {}
    code = unit.get("unit_code") or unit.get("apartment_number") or "-"
    name = unit.get("display_name") or "не указано"
    official = unit.get("official_number") or "не указан"
    area = unit.get("area_sqm")
    area_text = f"{area:g} м²" if isinstance(area, (int, float)) else "не указана"

    lines = [
        f"🏢 Помещение {code}",
        "",
        f"Тип: {unit_kind_label(unit.get('unit_type'))}",
        f"Подъезд: {unit.get('entrance_number') or '-'}",
        f"Статус: {status_label(unit.get('record_status'))}",
        "",
        f"Название: {name}",
        f"Официальный №: {official}",
        f"Площадь: {area_text}",
        "",
        f"Контакт: {contact.get('contact_name') or 'не указан'}",
        f"Телефон: {contact.get('contact_phone') or 'не указан'}",
        f"Заметка: {unit.get('internal_note') or 'нет'}",
        "",
        "Код помещения является стабильным и в этой форме не меняется.",
    ]
    return "\n".join(lines)


def update_unit_field(
    unit_id: int,
    field_name: str,
    new_value: Any,
    operator_id: int,
) -> tuple[Any, Any, int]:
    """
    Возвращает также audit_id, чтобы оператор сразу видел номер записи журнала.

    Все изменения карточки КП/технического помещения продолжают писать
    в operator_audit_log той же TEST/WORK или PROD БД, что и сам редактор.
    """
    allowed = {
        "display_name",
        "official_number",
        "area_sqm",
        "internal_note",
        "record_status",
    }
    if field_name not in allowed:
        raise ValueError("Недопустимое поле.")

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {q(field_name)} FROM apartments WHERE id = ?",
            (int(unit_id),),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("Помещение не найдено.")

        old_value = row[0]
        cur.execute(
            f"UPDATE apartments SET {q(field_name)} = ?, unit_updated_at = ? WHERE id = ?",
            (new_value, now_db(), int(unit_id)),
        )

        audit_id = audit_field_change(
            conn=conn,
            table_name="apartments",
            row_id=unit_id,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            operator_id=operator_id,
            user_id=operator_id,
            actor_type="operator",
            action_type="unit_registry_field_update",
            source_context="unit_registry_editor_bot",
            comment="Редактирование карточки помещения оператором.",
        )

        conn.commit()
        return old_value, new_value, int(audit_id)
    finally:
        conn.close()


def update_primary_contact(
    unit_id: int,
    field_name: str,
    new_value: str | None,
    operator_id: int,
) -> tuple[Any, Any, int]:
    if field_name not in {"contact_name", "contact_phone"}:
        raise ValueError("Недопустимое поле контакта.")

    conn = get_conn()
    try:
        cur = conn.cursor()
        contact = get_primary_contact(cur, int(unit_id))

        if contact:
            contact_id = int(contact["id"])
            old_value = contact[field_name]
            cur.execute(
                f"UPDATE unit_contacts SET {q(field_name)} = ?, updated_at = ? WHERE id = ?",
                (new_value, now_db(), contact_id),
            )
            row_id = contact_id
        else:
            values = {
                "apartment_id": int(unit_id),
                "contact_name": new_value if field_name == "contact_name" else None,
                "contact_phone": new_value if field_name == "contact_phone" else None,
                "contact_role": "PRIMARY",
                "is_primary": 1,
                "record_status": "DRAFT",
                "created_at": now_db(),
                "updated_at": now_db(),
            }
            fields = list(values)
            cur.execute(
                f"INSERT INTO unit_contacts ({', '.join(q(key) for key in fields)}) "
                f"VALUES ({','.join('?' for _ in fields)})",
                tuple(values[key] for key in fields),
            )
            row_id = int(cur.lastrowid)
            old_value = None

        audit_id = audit_field_change(
            conn=conn,
            table_name="unit_contacts",
            row_id=row_id,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            operator_id=operator_id,
            user_id=operator_id,
            actor_type="operator",
            action_type="unit_contact_update",
            source_context="unit_registry_editor_bot",
            comment="Изменение основного контакта помещения оператором.",
            extra={"apartment_id": unit_id},
        )
        conn.commit()
        return old_value, new_value, int(audit_id)
    finally:
        conn.close()


def create_new_unit(
    unit_type: str,
    entrance_number: int,
    raw_code: str,
    operator_id: int,
) -> int:
    code = normalize_code(raw_code)

    if unit_type == "COMMERCIAL":
        pattern = rf"^{entrance_number}_[A-Z][A-Z0-9_]*$"
        example = f"{entrance_number}_B"
    elif unit_type == "TECHNICAL":
        pattern = rf"^{entrance_number}_T\d+$"
        example = f"{entrance_number}_T1"
    else:
        raise ValueError("Неизвестный тип помещения.")

    if not re.fullmatch(pattern, code):
        raise ValueError(
            f"Неверный код. Для выбранного типа и подъезда ожидается, например: {example}"
        )

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT id
            FROM apartments
            WHERE apartment_number = ?
               OR unit_code = ?
        """, (code, code))
        if cur.fetchone():
            raise ValueError(f"Код {code} уже существует.")

        columns = table_columns(cur, "apartments")
        timestamp = now_db()
        values: dict[str, Any] = {
            "apartment_number": code,
            "unit_code": code,
            "unit_type": unit_type,
            "entrance_number": int(entrance_number),
            "record_status": "DRAFT",
            "source_note": "Создано оператором через реестр помещений.",
            "internal_note": "Требуется заполнение карточки.",
            "unit_updated_at": timestamp,
        }
        if "entrance" in columns:
            values["entrance"] = int(entrance_number)
        if "created_at" in columns:
            values["created_at"] = timestamp
        if "updated_at" in columns:
            values["updated_at"] = timestamp

        missing_required = []
        cur.execute("PRAGMA table_info(apartments)")
        for row in cur.fetchall():
            _cid, col_name, _col_type, notnull, default_value, pk = row[:6]
            if pk:
                continue
            if int(notnull or 0) and default_value is None and col_name not in values:
                missing_required.append(col_name)
        if missing_required:
            raise ValueError(
                "Не удаётся создать запись: обязательные поля не поддержаны: "
                + ", ".join(missing_required)
            )

        fields = list(values)
        cur.execute(
            f"INSERT INTO apartments ({', '.join(q(key) for key in fields)}) "
            f"VALUES ({','.join('?' for _ in fields)})",
            tuple(values[key] for key in fields),
        )
        unit_id = int(cur.lastrowid)

        audit_log(
            conn=conn,
            operator_id=operator_id,
            user_id=operator_id,
            actor_type="operator",
            action_type="unit_registry_unit_created",
            table_name="apartments",
            row_id=unit_id,
            field_name="unit_code,unit_type,entrance_number,record_status",
            old_value="",
            new_value=f"{code}, {unit_type}, {entrance_number}, DRAFT",
            source_context="unit_registry_editor_bot",
            comment="Оператор создал новую единицу в реестре помещений.",
            extra={"unit_code": code, "unit_type": unit_type},
            commit=False,
        )
        conn.commit()
        return unit_id
    finally:
        conn.close()


async def show_registry_menu(update: Update, user_states: dict, user_id: int):
    state = _registry_state(user_states, user_id, create=True)
    state["mode"] = "unit_registry_menu"
    state.pop("unit_registry_list_type", None)
    state.pop("unit_registry_unit_id", None)
    state.pop("unit_registry_edit", None)
    await update.message.reply_text(
        "🏢 Реестр помещений\n\n"
        "Здесь оператор ведёт коммерческие и технические помещения.\n"
        "Жилые квартиры в этом разделе не меняются.",
        reply_markup=kb(UNIT_REGISTRY_MENU),
    )


async def show_unit_list(
    update: Update,
    user_states: dict,
    user_id: int,
    unit_type: str,
):
    rows = list_units(unit_type)
    state = _registry_state(user_states, user_id, create=True)
    state["mode"] = "unit_registry_list"
    state["unit_registry_list_type"] = unit_type
    state["unit_registry_list_codes"] = [
        row.get("unit_code") or row.get("apartment_number")
        for row in rows
    ]
    await update.message.reply_text(
        format_unit_list(unit_type, rows),
        reply_markup=kb(list_buttons(rows)),
    )


async def show_unit_card(
    update: Update,
    user_states: dict,
    user_id: int,
    unit_id: int,
):
    unit = get_unit_by_id(unit_id)
    if not unit:
        await update.message.reply_text("Помещение не найдено.")
        return

    state = _registry_state(user_states, user_id, create=True)
    state["mode"] = "unit_registry_card"
    state["unit_registry_unit_id"] = int(unit_id)
    state.pop("unit_registry_edit", None)

    await update.message.reply_text(
        format_unit_card(unit),
        reply_markup=kb(unit_card_menu(unit)),
    )


async def start_create_unit(update: Update, user_states: dict, user_id: int):
    state = _registry_state(user_states, user_id, create=True)
    state["mode"] = "unit_registry_create_type"
    state.pop("unit_registry_create_type", None)
    state.pop("unit_registry_create_entrance", None)
    await update.message.reply_text(
        "➕ Добавить помещение\n\nВыберите тип.",
        reply_markup=kb(UNIT_TYPE_MENU),
    )


async def ask_edit_value(
    update: Update,
    user_states: dict,
    user_id: int,
    action: str,
):
    state = _registry_state(user_states, user_id, create=True)
    unit = get_unit_by_id(state.get("unit_registry_unit_id"))
    if not unit:
        await update.message.reply_text("Помещение не найдено.")
        return

    prompts = {
        "display_name": (
            "✏️ Название\n\n"
            f"Сейчас: {unit.get('display_name') or 'не указано'}\n"
            "Введите фактическое название или «-» чтобы очистить."
        ),
        "contact_name": (
            "✏️ Контакт\n\n"
            f"Сейчас: {(unit.get('primary_contact') or {}).get('contact_name') or 'не указан'}\n"
            "Введите имя/название контактного лица или «-» чтобы очистить."
        ),
        "contact_phone": (
            "✏️ Телефон\n\n"
            f"Сейчас: {(unit.get('primary_contact') or {}).get('contact_phone') or 'не указан'}\n"
            "Введите телефон или «-» чтобы очистить."
        ),
        "area_sqm": (
            "✏️ Площадь\n\n"
            f"Сейчас: {unit.get('area_sqm') if unit.get('area_sqm') is not None else 'не указана'}\n"
            "Введите площадь числом, например 52.5. Или «-» чтобы очистить."
        ),
        "official_number": (
            "✏️ Официальный номер\n\n"
            f"Сейчас: {unit.get('official_number') or 'не указан'}\n"
            "Введите официальный номер или «-» чтобы очистить."
        ),
        "internal_note": (
            "📝 Заметка\n\n"
            f"Сейчас: {unit.get('internal_note') or 'нет'}\n"
            "Введите заметку или «-» чтобы очистить."
        ),
    }

    state["mode"] = "unit_registry_wait_value"
    state["unit_registry_edit"] = action
    await update.message.reply_text(prompts[action], reply_markup=kb([["⬅️ Назад"]]))


async def apply_edit_value(
    update: Update,
    user_states: dict,
    user_id: int,
    value: str,
):
    state = _registry_state(user_states, user_id, create=True)
    unit_id = state.get("unit_registry_unit_id")
    action = state.get("unit_registry_edit")

    if not unit_id or not action:
        await update.message.reply_text("Нет активного редактирования.")
        return

    raw = text(value)
    new_value: Any = None if raw == "-" else raw

    try:
        if action == "area_sqm":
            if new_value is not None:
                new_value = float(str(new_value).replace(",", "."))
                if new_value <= 0:
                    raise ValueError("Площадь должна быть больше нуля.")
            old, new, audit_id = update_unit_field(unit_id, "area_sqm", new_value, user_id)

        elif action in {"display_name", "official_number", "internal_note"}:
            old, new, audit_id = update_unit_field(unit_id, action, new_value, user_id)

        elif action in {"contact_name", "contact_phone"}:
            old, new, audit_id = update_primary_contact(unit_id, action, new_value, user_id)

        else:
            raise ValueError("Неизвестное действие.")
    except Exception as exc:
        await update.message.reply_text(f"Ошибка сохранения: {exc}")
        return

    state["mode"] = "unit_registry_card"
    state.pop("unit_registry_edit", None)

    old_text = old if old not in (None, "") else "не указано"
    new_text = new if new not in (None, "") else "не указано"

    await update.message.reply_text(
        f"✅ Изменение сохранено\n\n"
        f"Было: {old_text}\n"
        f"Стало: {new_text}\n\n"
        f"🧾 Аудит: #{audit_id}"
    )
    await show_unit_card(update, user_states, user_id, unit_id)


async def set_unit_status(
    update: Update,
    user_states: dict,
    user_id: int,
    new_status: str,
):
    state = _registry_state(user_states, user_id, create=True)
    unit_id = state.get("unit_registry_unit_id")
    if not unit_id:
        await update.message.reply_text("Нет открытого помещения.")
        return

    old, new, status_audit_id = update_unit_field(
        unit_id,
        "record_status",
        new_status,
        user_id,
    )

    # Программная заметка о заготовке актуальна только у черновика.
    # Ручные заметки оператора не переписываем.
    note_audit_id = None
    if new_status == "CONFIRMED":
        unit = get_unit_by_id(unit_id)
        old_note = text(unit.get("internal_note")) if unit else ""
        generated_prefix = "Создано программно как черновик."
        if old_note.startswith(generated_prefix):
            confirmed_note = (
                "Существование и официальный номер подтверждены оператором. "
                "Требуется уточнить площадь, контакты и договорные условия."
            )
            _old_note, _new_note, note_audit_id = update_unit_field(
                unit_id,
                "internal_note",
                confirmed_note,
                user_id,
            )

    reply = (
        f"✅ Статус изменён\n\n"
        f"Было: {status_label(old)}\n"
        f"Стало: {status_label(new)}\n\n"
        f"🧾 Аудит статуса: #{status_audit_id}"
    )
    if note_audit_id is not None:
        reply += f"\n🧾 Аудит служебной заметки: #{note_audit_id}"

    await update.message.reply_text(reply)
    await show_unit_card(update, user_states, user_id, unit_id)


async def handle_create_flow(
    update: Update,
    user_states: dict,
    user_id: int,
    normalized: str,
) -> bool:
    state = _registry_state(user_states, user_id, create=True)
    mode = state.get("mode")

    if mode == "unit_registry_create_type":
        mapping = {
            "🏪 Коммерческое": "COMMERCIAL",
            "🛠 Техническое": "TECHNICAL",
        }
        if normalized not in mapping:
            await update.message.reply_text("Выберите тип кнопкой.")
            return True

        state["unit_registry_create_type"] = mapping[normalized]
        state["mode"] = "unit_registry_create_entrance"
        await update.message.reply_text(
            "Выберите подъезд.",
            reply_markup=kb(ENTRANCE_MENU),
        )
        return True

    if mode == "unit_registry_create_entrance":
        if normalized not in {"1", "2", "3", "4", "5", "6"}:
            await update.message.reply_text("Выберите подъезд кнопкой.")
            return True

        entrance = int(normalized)
        unit_type = state.get("unit_registry_create_type")
        state["unit_registry_create_entrance"] = entrance
        state["mode"] = "unit_registry_create_code"

        example = f"{entrance}_B" if unit_type == "COMMERCIAL" else f"{entrance}_T1"
        await update.message.reply_text(
            "Введите стабильный код нового помещения.\n\n"
            f"Например: {example}\n"
            "Код должен соответствовать выбранному подъезду.",
            reply_markup=kb([["⬅️ Назад"]]),
        )
        return True

    if mode == "unit_registry_create_code":
        unit_type = state.get("unit_registry_create_type")
        entrance = state.get("unit_registry_create_entrance")
        try:
            unit_id = create_new_unit(unit_type, int(entrance), normalized, user_id)
        except Exception as exc:
            await update.message.reply_text(f"Не удалось создать помещение: {exc}")
            return True

        await update.message.reply_text("✅ Черновик помещения создан.")
        await show_unit_card(update, user_states, user_id, unit_id)
        return True

    return False


async def handle_unit_registry_editor_text(
    update: Update,
    user_states: dict,
    user_id: int,
    text_value: str,
) -> bool:
    """
    Верхний router редактора помещений.

    Возвращает True только если сообщение действительно обработано модулем.
    Это позволяет вызывать функцию в начале message_handler, не ломая старые
    строковые и tuple-состояния основного бота.
    """
    normalized = text(text_value)

    # Договоры КП должны отработать раньше общего router реестра помещений,
    # иначе базовая карточка воспримет кнопку «📄 Договоры» как неизвестную.
    if handle_commercial_contract_editor_text is not None:
        if await handle_commercial_contract_editor_text(
            update, user_states, user_id, normalized
        ):
            return True

    # Вход в раздел возможен из админ-меню и создаёт изолированное состояние.
    if normalized == "🏢 Помещения":
        if not operator_can_edit(user_id):
            await update.message.reply_text(
                "Этот раздел доступен оператору или администратору."
            )
            return True

        ready, message = schema_ready()
        if not ready:
            await update.message.reply_text("⚠️ " + message)
            return True

        await show_registry_menu(update, user_states, user_id)
        return True

    # Не забираем сообщения, пока пользователь не находится именно в нашем режиме.
    state = _registry_state(user_states, user_id, create=False)
    if state is None:
        return False

    mode = state.get("mode", "")

    if not operator_can_edit(user_id):
        await update.message.reply_text("Недостаточно прав на изменение реестра.")
        return True

    # Главное меню сознательно отдаём основному bot-router.
    if normalized == "🏠 Главное меню":
        user_states.pop(user_id, None)
        return False

    if normalized == "⬅️ Назад":
        if mode == "unit_registry_menu":
            user_states.pop(user_id, None)
            return False

        if mode in {
            "unit_registry_create_type",
            "unit_registry_list",
            "unit_registry_card",
        }:
            await show_registry_menu(update, user_states, user_id)
            return True

        if mode == "unit_registry_create_entrance":
            state["mode"] = "unit_registry_create_type"
            await update.message.reply_text(
                "Выберите тип.",
                reply_markup=kb(UNIT_TYPE_MENU),
            )
            return True

        if mode == "unit_registry_create_code":
            state["mode"] = "unit_registry_create_entrance"
            await update.message.reply_text(
                "Выберите подъезд.",
                reply_markup=kb(ENTRANCE_MENU),
            )
            return True

        if mode == "unit_registry_wait_value":
            await show_unit_card(
                update,
                user_states,
                user_id,
                state.get("unit_registry_unit_id"),
            )
            return True

    if normalized == "🏪 Коммерческие":
        await show_unit_list(update, user_states, user_id, "COMMERCIAL")
        return True

    if normalized == "🛠 Технические":
        await show_unit_list(update, user_states, user_id, "TECHNICAL")
        return True

    if normalized == "➕ Добавить помещение":
        await start_create_unit(update, user_states, user_id)
        return True

    if await handle_create_flow(update, user_states, user_id, normalized):
        return True

    if mode == "unit_registry_list":
        unit = get_unit_by_code(normalized)
        selected_type = state.get("unit_registry_list_type")
        if unit and unit.get("unit_type") == selected_type:
            await show_unit_card(update, user_states, user_id, int(unit["id"]))
            return True

        await update.message.reply_text(
            "Выберите код помещения из списка или нажмите ⬅️ Назад."
        )
        return True

    if mode == "unit_registry_card":
        action_map = {
            "✏️ Название": "display_name",
            "✏️ Контакт": "contact_name",
            "✏️ Телефон": "contact_phone",
            "✏️ Площадь": "area_sqm",
            "✏️ Офиц. №": "official_number",
            "📝 Заметка": "internal_note",
        }

        if normalized in action_map:
            await ask_edit_value(
                update,
                user_states,
                user_id,
                action_map[normalized],
            )
            return True

        if normalized == "✅ Подтвердить":
            await set_unit_status(update, user_states, user_id, "CONFIRMED")
            return True

        if normalized == "↩️ Черновик":
            await set_unit_status(update, user_states, user_id, "DRAFT")
            return True

        if normalized == "📋 К списку":
            unit = get_unit_by_id(state.get("unit_registry_unit_id"))
            unit_type = unit.get("unit_type") if unit else "COMMERCIAL"
            await show_unit_list(update, user_states, user_id, unit_type)
            return True

        await update.message.reply_text(
            "Выберите действие кнопкой.",
            reply_markup=kb(UNIT_CARD_MENU),
        )
        return True

    if mode == "unit_registry_wait_value":
        await apply_edit_value(update, user_states, user_id, normalized)
        return True

    # На случай повреждённого/устаревшего режима освобождаем пользователя.
    user_states.pop(user_id, None)
    return False
