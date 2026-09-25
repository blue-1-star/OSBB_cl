"""Telegram proposal and SUPER_ADMIN decision flow for data gaps."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

from telegram import ReplyKeyboardMarkup, Update

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from access_control import has_permission
from data_quality_report import build_quality_issues
from data_quality_proposals import (
    DIRECT_FIELDS, approve_proposal, create_proposal, get_proposal,
    is_super_admin, list_proposals, reject_proposal, reply_to_clarification,
    request_clarification,
)
from service_orders_core import get_conn


ENTRY = "✍️ Предложить исправление"
MY = "📝 Мои предложения"
REVIEW = "✅ Решения по данным"
BACK = "⬅️ К меню"
ASK = "❓ Запросить уточнение"
APPROVE = "✅ Принять исправление"
REJECT = "❌ Отклонить"
MODULE = "quality_proposals"


def kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _suggest_allowed(user_id: int, is_admin: bool) -> bool:
    return is_admin or has_permission(user_id, "data_quality_proposals", "SUGGEST")


def _menu(user_id: int) -> ReplyKeyboardMarkup:
    rows = [[ENTRY], [MY]]
    if is_super_admin(user_id):
        rows.append([REVIEW])
    rows.append([BACK])
    return kb(rows)


def _state(user_states: dict, user_id: int) -> dict:
    current = user_states.get(user_id)
    if isinstance(current, dict) and current.get("_module") == MODULE:
        return current
    current = {"_module": MODULE, "mode": "menu"}
    user_states[user_id] = current
    return current


async def _show_my(update: Update, state: dict, user_id: int) -> None:
    rows = [r for r in list_proposals() if str(r.get("telegram_user_id") or "") == str(user_id)]
    state["mode"] = "my"
    if not rows:
        await update.message.reply_text("У вас пока нет предложений.", reply_markup=_menu(user_id))
        return
    lines = ["📝 Мои предложения", ""]
    answer_buttons = []
    for row in rows[:15]:
        payload = json.loads(row["payload_json"] or "{}")
        lines.append(f"#{row['id']} · кв.{row['apartment_number']} · {payload.get('field_name')} · {row['status']}")
        if row["status"] == "NEEDS_CLARIFICATION":
            dialogue = payload.get("dialogue") or []
            question = next((m["text"] for m in reversed(dialogue) if m.get("kind") == "QUESTION"), "")
            lines.append(f"  Вопрос: {question}")
            answer_buttons.append([f"💬 Ответить #{row['id']}"])
        elif row["close_note"]:
            lines.append(f"  Решение: {row['close_note']}")
    await update.message.reply_text("\n".join(lines), reply_markup=kb(answer_buttons + [[ENTRY], [BACK]]))


async def _show_review(update: Update, state: dict, user_id: int) -> None:
    if not is_super_admin(user_id):
        await update.message.reply_text("Решения доступны только SUPER_ADMIN.")
        return
    rows = [r for r in list_proposals() if r["status"] in {"PENDING", "NEEDS_CLARIFICATION"}]
    state["mode"] = "review_list"
    if not rows:
        await update.message.reply_text("Ожидающих предложений нет.", reply_markup=_menu(user_id))
        return
    lines = ["✅ Предложения на рассмотрении", ""]
    buttons = []
    for row in rows[:15]:
        payload = json.loads(row["payload_json"] or "{}")
        lines.append(f"#{row['id']} · кв.{row['apartment_number']} · {payload.get('field_name')} · {row['status']}")
        buttons.append([f"📄 #{row['id']}"])
    await update.message.reply_text("\n".join(lines), reply_markup=kb(buttons + [[BACK]]))


async def _show_card(update: Update, state: dict, user_id: int, task_id: int) -> None:
    if not is_super_admin(user_id):
        await update.message.reply_text("Нет доступа к решению.")
        return
    row = get_proposal(task_id)
    if not row:
        await update.message.reply_text("Предложение не найдено.")
        return
    payload = json.loads(row["payload_json"] or "{}")
    state.update({"mode": "review_card", "task_id": task_id})
    lines = [
        f"📝 Предложение #{task_id} · кв.{row['apartment_number']}",
        f"Статус: {row['status']}",
        f"Поле: {payload.get('object_table')} #{payload.get('object_id')} · {payload.get('field_name')}",
        f"Было: {payload.get('old_value') or '—'}",
        f"Предложено: {payload.get('proposed_value') or '—'}",
        f"Источник: {payload.get('evidence') or '—'}",
        f"Автор: {row['created_by']}",
    ]
    for message in payload.get("dialogue") or []:
        lines.append(f"{message.get('kind')}: {message.get('text')}")
    buttons = []
    if row["status"] == "PENDING":
        if payload.get("object_table") == "vehicles" and payload.get("field_name") in DIRECT_FIELDS:
            buttons.append([APPROVE])
        else:
            lines.append("Для этого поля нет безопасного автоматического применения; нужен профильный сценарий.")
        buttons.append([ASK])
    if row["status"] in {"PENDING", "NEEDS_CLARIFICATION"}:
        buttons.append([REJECT])
    buttons.append([REVIEW])
    await update.message.reply_text("\n".join(lines), reply_markup=kb(buttons))


async def handle_quality_proposal_text(
    update: Update, user_states: dict, user_id: int, message_text: str,
    *, is_admin: bool = False, bot=None,
) -> bool:
    existing = user_states.get(user_id)
    active = isinstance(existing, dict) and existing.get("_module") == MODULE
    if not active and message_text not in {ENTRY, MY, REVIEW}:
        return False
    if message_text == REVIEW:
        state = _state(user_states, user_id)
        await _show_review(update, state, user_id)
        return True
    if not _suggest_allowed(user_id, is_admin):
        await update.message.reply_text("Нет права предлагать исправления.")
        if active:
            user_states.pop(user_id, None)
        return True
    state = _state(user_states, user_id)
    if message_text == BACK:
        user_states.pop(user_id, None)
        await update.message.reply_text("Вернулись к меню действий.")
        return True
    if message_text == MY:
        await _show_my(update, state, user_id)
        return True
    if message_text == ENTRY:
        state.clear()
        state.update({"_module": MODULE, "mode": "apartment"})
        await update.message.reply_text("Укажите квартиру, данные которой хотите дополнить.", reply_markup=kb([[BACK]]))
        return True
    mode = state.get("mode")
    if mode == "apartment":
        apartment = message_text.strip()
        if not re.fullmatch(r"\d+[A-Za-zА-Яа-яІЇЄҐіїєґ]?", apartment):
            await update.message.reply_text("Введите номер квартиры, например 160.")
            return True
        conn = get_conn()
        try:
            issues = [x for x in build_quality_issues(conn) if x["apartment"] == apartment]
        finally:
            conn.close()
        if not issues:
            await update.message.reply_text("По этой квартире пробелов из текущего отчёта нет. Укажите другую квартиру.")
            return True
        choices = {f"#{i['object_id']} · {i['field']} ({i['plate']})": i for i in issues}
        state.update({"mode": "choose", "apartment": apartment, "choices": choices})
        await update.message.reply_text("Что исправить?", reply_markup=kb([[label] for label in choices] + [[BACK]]))
        return True
    if mode == "choose":
        issue = (state.get("choices") or {}).get(message_text)
        if not issue:
            await update.message.reply_text("Выберите строку кнопкой.")
            return True
        state.update({"mode": "value", "issue": issue})
        await update.message.reply_text(f"Введите предлагаемое значение для поля «{issue['field']}».")
        return True
    if mode == "value":
        state.update({"mode": "evidence", "value": message_text.strip()})
        await update.message.reply_text("Откуда известно исправление? Укажите источник или обстоятельства проверки.")
        return True
    if mode == "evidence":
        issue = state["issue"]
        try:
            task_id = create_proposal(
                rule_code=issue["rule_code"], object_id=int(issue["object_id"]),
                apartment=issue["apartment"], proposed_value=state["value"],
                evidence=message_text, actor=f"telegram:{user_id}", telegram_user_id=user_id,
            )
        except Exception as exc:
            await update.message.reply_text(f"Предложение не записано: {exc}")
            return True
        state["mode"] = "menu"
        await update.message.reply_text(
            f"✅ Предложение #{task_id} зарегистрировано. Реестр не изменён; ожидается решение SUPER_ADMIN.",
            reply_markup=_menu(user_id),
        )
        return True
    if mode == "my":
        match = re.fullmatch(r"💬 Ответить #(\d+)", message_text)
        if match:
            state.update({"mode": "reply", "task_id": int(match.group(1))})
            await update.message.reply_text("Напишите ответ на вопрос супер­админа.")
        else:
            await _show_my(update, state, user_id)
        return True
    if mode == "reply":
        try:
            reply_to_clarification(task_id=int(state["task_id"]), actor=f"telegram:{user_id}",
                                   reply=message_text, telegram_user_id=user_id)
        except Exception as exc:
            await update.message.reply_text(f"Ответ не сохранён: {exc}")
            return True
        await _show_my(update, state, user_id)
        return True
    if mode == "review_list":
        match = re.fullmatch(r"📄 #(\d+)", message_text)
        if match:
            await _show_card(update, state, user_id, int(match.group(1)))
        else:
            await _show_review(update, state, user_id)
        return True
    if mode == "review_card":
        if not is_super_admin(user_id):
            await update.message.reply_text("Нет доступа к решению.")
            return True
        if message_text in {ASK, APPROVE, REJECT}:
            state["mode"] = {ASK: "question", APPROVE: "approve_note", REJECT: "reject_note"}[message_text]
            await update.message.reply_text("Введите вопрос или основание решения.")
            return True
        await _show_card(update, state, user_id, int(state["task_id"]))
        return True
    if mode in {"question", "approve_note", "reject_note"}:
        task_id = int(state["task_id"])
        notification_error = None
        try:
            if mode == "question":
                request_clarification(task_id=task_id, reviewer_id=user_id, question=message_text)
                row = get_proposal(task_id)
                if bot and row and row["telegram_user_id"]:
                    try:
                        await bot.send_message(chat_id=int(row["telegram_user_id"]), text=f"❓ Уточнение по предложению #{task_id}: {message_text}\nОтветьте через «📝 Мои предложения».")
                    except Exception as exc:
                        notification_error = str(exc)
            elif mode == "approve_note":
                approve_proposal(task_id=task_id, reviewer_id=user_id, note=message_text)
            else:
                reject_proposal(task_id=task_id, reviewer_id=user_id, note=message_text)
            if mode != "question":
                row = get_proposal(task_id)
                if bot and row and row["telegram_user_id"]:
                    outcome = "принято" if mode == "approve_note" else "отклонено"
                    try:
                        await bot.send_message(
                            chat_id=int(row["telegram_user_id"]),
                            text=f"Решение по предложению #{task_id}: {outcome}. {message_text}",
                        )
                    except Exception as exc:
                        notification_error = str(exc)
        except Exception as exc:
            await update.message.reply_text(f"Решение не сохранено: {exc}")
            return True
        if notification_error:
            await update.message.reply_text(
                f"Решение сохранено, но Telegram-уведомление не доставлено: {notification_error}. "
                "Автор увидит результат в «Мои предложения»."
            )
        await _show_review(update, state, user_id)
        return True
    await update.message.reply_text("Выберите действие.", reply_markup=_menu(user_id))
    return True
