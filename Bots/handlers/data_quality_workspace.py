"""Bot pages for the shared read-only data-gap report."""

from __future__ import annotations

from pathlib import Path
import re
import sys

from telegram import ReplyKeyboardMarkup, Update

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from access_control import has_permission
from data_quality_report import build_quality_issues, quality_summary
from service_orders_core import get_conn


ENTRY = "🧩 Пробелы в данных"
BACK = "⬅️ К админ-меню"
ALL = "📋 Все пробелы"
PREV = "⬅️ Раньше"
NEXT = "➡️ Далее"
PAGE_SIZE = 15


def _keyboard(page: int, more: bool) -> ReplyKeyboardMarkup:
    nav = ([PREV] if page else []) + ([NEXT] if more else [])
    rows = [nav] if nav else []
    rows += [[ALL], [BACK]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


async def _show(update: Update, state: dict, *, apartment: str = "", page: int = 0) -> None:
    conn = get_conn()
    try:
        issues = build_quality_issues(conn)
    finally:
        conn.close()
    if apartment:
        issues = [item for item in issues if item["apartment"] == apartment]
    summary = quality_summary(issues)
    page = max(0, min(page, max(0, (len(issues) - 1) // PAGE_SIZE)))
    start = page * PAGE_SIZE
    rows = issues[start:start + PAGE_SIZE]
    more = start + PAGE_SIZE < len(issues)
    state.update({"mode": "data_quality", "quality_apartment": apartment, "quality_page": page})
    lines = [
        "🧩 Пробелы в данных" + (f" · кв.{apartment}" if apartment else ""),
        f"Пробелов: {summary['issues']} · записей: {summary['records']}",
        "Напишите номер квартиры для поиска. Исправления здесь не вносятся.",
        "",
    ]
    lines.extend(
        f"• кв.{item['apartment']} | {item['plate']} | {item['field']} ({item['severity'].lower()})"
        for item in rows
    )
    if not rows:
        lines.append("Пробелов по этому фильтру нет.")
    lines.append(f"\nСтраница {page + 1}")
    await update.message.reply_text("\n".join(lines), reply_markup=_keyboard(page, more))


async def handle_data_quality_text(
    update: Update, user_states: dict, user_id: int, message_text: str,
    *, back_markup: ReplyKeyboardMarkup,
) -> bool:
    state = user_states.get(user_id)
    active = isinstance(state, dict) and state.get("mode") == "data_quality"
    if message_text != ENTRY and not active:
        return False
    if not has_permission(user_id, "reports", "VIEW"):
        await update.message.reply_text("Нет права просмотра отчёта.", reply_markup=back_markup)
        if active:
            user_states.pop(user_id, None)
        return True
    if not active:
        state = user_states.setdefault(user_id, {})
        await _show(update, state)
        return True
    if message_text == BACK:
        user_states.pop(user_id, None)
        await update.message.reply_text("Админ-меню", reply_markup=back_markup)
        return True
    if message_text in (ALL, ENTRY):
        await _show(update, state)
        return True
    apartment = state.get("quality_apartment", "")
    page = int(state.get("quality_page") or 0)
    if message_text == PREV:
        await _show(update, state, apartment=apartment, page=page - 1)
        return True
    if message_text == NEXT:
        await _show(update, state, apartment=apartment, page=page + 1)
        return True
    match = re.fullmatch(r"(?:кв\.?\s*)?(\d+[A-Za-zА-Яа-яІЇЄҐіїєґ]?)", message_text.strip(), re.IGNORECASE)
    if match:
        await _show(update, state, apartment=match.group(1))
    else:
        await update.message.reply_text("Введите номер квартиры или выберите кнопку.", reply_markup=_keyboard(page, True))
    return True
