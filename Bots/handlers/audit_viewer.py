"""
Постраничный просмотр основного журнала аудита ОСББ.

Источник данных: ТОЛЬКО operator_audit_log.
Таблица audit_log является прежней/пустой и здесь намеренно не используется.

Зачем страницы:
- старый просмотр запрашивал 30 строк и делил длинный ответ Telegram на несколько
  сообщений;
- первое сообщение содержало самые новые записи, а последнее — самые старые
  из этих 30, поэтому внизу чата визуально оказывались старые audit_id;
- теперь на одном экране только 5 записей, начиная всегда с самых новых.

Совместим с существующим parking_bot.py:
    from handlers.audit_viewer import handle_audit_viewer_text
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
from typing import Any

from telegram import Update, ReplyKeyboardMarkup

BOT_HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = BOT_HANDLERS_DIR.parent
OSBB_ROOT = BOTS_DIR.parent
PY_ROOT = OSBB_ROOT.parent

for path in (str(OSBB_ROOT), str(PY_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from config import paths, USE_TEST_DB


PAGE_SIZE = 5

AUDIT_MENU = [
    ["🕘 Последние правки"],
    ["👤 Правки операторов", "⚙️ Системные правки"],
    ["⬅️ Назад", "🏠 Главное меню"],
]

BTN_NEWER = "⬅️ Новее"
BTN_OLDER = "➡️ Старее"


def kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def get_db_file() -> Path:
    return paths.OSBB_TEST_DB_FILE if USE_TEST_DB else paths.OSBB_DB_FILE


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_file())
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_state(user_states: dict, user_id: int) -> dict:
    """
    user_states в старом боте бывает строкой, tuple или dict.
    Для аудита безопасно используем собственный словарь только при входе
    в экран журнала.
    """
    current = user_states.get(user_id)
    if not isinstance(current, dict):
        current = {"_module": "audit_viewer"}
        user_states[user_id] = current
    return current


def table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cur.fetchone() is not None


def safe(value: Any) -> str:
    return "" if value is None else str(value)


def count_audit(cur: sqlite3.Cursor, actor_filter: str | None = None) -> int:
    if not table_exists(cur, "operator_audit_log"):
        return 0

    if actor_filter:
        cur.execute(
            "SELECT COUNT(*) FROM operator_audit_log WHERE actor_type=?",
            (actor_filter,),
        )
    else:
        cur.execute("SELECT COUNT(*) FROM operator_audit_log")

    return int(cur.fetchone()[0] or 0)


def load_audit_page(
    *,
    offset: int = 0,
    limit: int = PAGE_SIZE,
    actor_filter: str | None = None,
) -> tuple[list[dict], int]:
    conn = get_conn()
    try:
        cur = conn.cursor()

        if not table_exists(cur, "operator_audit_log"):
            return [], 0

        where_sql = ""
        params: list[Any] = []

        if actor_filter:
            where_sql = "WHERE actor_type = ?"
            params.append(actor_filter)

        total = count_audit(cur, actor_filter)

        params.extend([int(limit), int(offset)])
        cur.execute(f"""
            SELECT
                id,
                created_at,
                actor_type,
                operator_id,
                user_id,
                action_type,
                table_name,
                row_id,
                field_name,
                old_value,
                new_value,
                action_status,
                review_status,
                source_context,
                comment
            FROM operator_audit_log
            {where_sql}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """, tuple(params))

        return [dict(row) for row in cur.fetchall()], total
    finally:
        conn.close()


def format_audit_row(row: dict, index: int) -> str:
    old_value = safe(row.get("old_value")) or "∅"
    new_value = safe(row.get("new_value")) or "∅"

    operator = safe(row.get("operator_id")) or safe(row.get("user_id")) or "-"
    actor = safe(row.get("actor_type")) or "-"
    action = safe(row.get("action_type")) or "-"
    table = safe(row.get("table_name")) or "-"
    record_id = safe(row.get("row_id")) or "-"
    field = safe(row.get("field_name")) or "-"
    created = safe(row.get("created_at")) or "-"
    review = safe(row.get("review_status")) or "-"
    source = safe(row.get("source_context"))
    comment = safe(row.get("comment"))

    lines = [
        f"{index}. {created}",
        f"   {actor} | {operator}",
        f"   {action}",
        f"   {table} #{record_id} | {field}",
        f"   {old_value} → {new_value}",
        f"   проверка: {review}",
    ]

    if comment:
        lines.append(f"   {comment}")

    if source:
        lines.append(f"   модуль: {source}")

    lines.append(f"   audit_id={row.get('id')}")
    return "\n".join(lines)


def title_for_filter(actor_filter: str | None) -> str:
    return {
        None: "Последние правки",
        "operator": "Правки операторов",
        "system": "Системные правки",
    }.get(actor_filter, "Журнал действий")


def page_keyboard(
    *,
    offset: int,
    total: int,
) -> list[list[str]]:
    rows: list[list[str]] = []

    nav: list[str] = []
    if offset > 0:
        nav.append(BTN_NEWER)
    if offset + PAGE_SIZE < total:
        nav.append(BTN_OLDER)
    if nav:
        rows.append(nav)

    rows.extend(AUDIT_MENU)
    return rows


def format_audit_page(
    rows: list[dict],
    *,
    total: int,
    offset: int,
    actor_filter: str | None,
) -> str:
    title = title_for_filter(actor_filter)
    start_number = offset + 1
    end_number = offset + len(rows)

    lines = [
        f"🧾 {title}",
        "",
        f"База: {'TEST/WORK' if USE_TEST_DB else 'PROD'}",
        "Источник: operator_audit_log",
        f"Показаны записи: {start_number}–{end_number} из {total}" if rows else f"Записей: 0 из {total}",
        "",
    ]

    if not rows:
        lines.append("Записей нет.")
        return "\n".join(lines)

    for local_index, row in enumerate(rows, start=start_number):
        lines.append(format_audit_row(row, local_index))
        lines.append("")

    return "\n".join(lines)


async def show_audit_dashboard(
    update: Update,
    user_states: dict,
    user_id: int,
) -> None:
    state = _ensure_state(user_states, user_id)
    state["mode"] = "audit_viewer"
    state["audit_filter"] = None
    state["audit_offset"] = 0

    await update.message.reply_text(
        "🧾 Журнал действий\n\n"
        "Основной журнал: operator_audit_log.\n"
        "Таблица audit_log не используется: она является старой и пустой.\n\n"
        "Последние записи показываются страницами по 5, "
        "поэтому самые новые всегда находятся на первом экране.",
        reply_markup=kb(AUDIT_MENU),
    )


async def show_audit_page(
    update: Update,
    user_states: dict,
    user_id: int,
    *,
    actor_filter: str | None,
    offset: int,
) -> None:
    rows, total = load_audit_page(
        offset=max(0, offset),
        limit=PAGE_SIZE,
        actor_filter=actor_filter,
    )

    # Если после удаления/очистки сместились за последнюю страницу.
    if not rows and total > 0 and offset > 0:
        offset = max(0, ((total - 1) // PAGE_SIZE) * PAGE_SIZE)
        rows, total = load_audit_page(
            offset=offset,
            limit=PAGE_SIZE,
            actor_filter=actor_filter,
        )

    state = _ensure_state(user_states, user_id)
    state["mode"] = "audit_viewer"
    state["audit_filter"] = actor_filter
    state["audit_offset"] = offset

    await update.message.reply_text(
        format_audit_page(
            rows,
            total=total,
            offset=offset,
            actor_filter=actor_filter,
        ),
        reply_markup=kb(page_keyboard(offset=offset, total=total)),
    )


async def handle_audit_viewer_text(
    update: Update,
    user_states: dict,
    user_id: int,
    text: str,
) -> bool:
    normalized = (text or "").strip()
    state = _ensure_state(user_states, user_id)
    mode = state.get("mode")

    if normalized in {"🧾 Журнал действий", "🧾 Аудит", "👁 Правки операторов"}:
        await show_audit_dashboard(update, user_states, user_id)
        return True

    audit_buttons = {
        "🕘 Последние правки": None,
        "👤 Правки операторов": "operator",
        "⚙️ Системные правки": "system",
    }

    if normalized in audit_buttons:
        await show_audit_page(
            update,
            user_states,
            user_id,
            actor_filter=audit_buttons[normalized],
            offset=0,
        )
        return True

    if mode == "audit_viewer":
        current_filter = state.get("audit_filter")
        current_offset = int(state.get("audit_offset") or 0)

        if normalized == BTN_OLDER:
            await show_audit_page(
                update,
                user_states,
                user_id,
                actor_filter=current_filter,
                offset=current_offset + PAGE_SIZE,
            )
            return True

        if normalized == BTN_NEWER:
            await show_audit_page(
                update,
                user_states,
                user_id,
                actor_filter=current_filter,
                offset=max(0, current_offset - PAGE_SIZE),
            )
            return True

        if normalized in {"⬅️ Назад", "🏠 Главное меню"}:
            state["mode"] = ""
            return False

    return False
