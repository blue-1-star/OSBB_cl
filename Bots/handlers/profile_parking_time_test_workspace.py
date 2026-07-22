# -*- coding: utf-8 -*-
"""
Admin-only no-write TEST UI for apartment 40 / missing parking_time.

This is not a resident flow. It never calls resident profile functions and
never opens the target apartment as a resident.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

from telegram import ReplyKeyboardMarkup, Update

HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = HANDLERS_DIR.parent
ROOT = BOTS_DIR.parent
for folder in (ROOT, BOTS_DIR):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from service_orders_core import get_conn, text
from profile_parking_time_test_core import (
    PARKING_TIME_LABELS,
    STATUS_OPEN,
    STATUS_PENDING_OPERATOR,
    TEST_TARGET_APARTMENT,
    close_test_session,
    decide_test_session,
    get_test_session,
    list_test_sessions,
    open_test_session,
    propose_parking_time,
    session_for_display,
    test_candidate,
)


MODULE = "profile_parking_time_test_ui"

OPEN_TEST = "🧪 Відкрити TEST кв. 40"
OPEN_TEST_ALIASES = {
    OPEN_TEST,
    "🧪 Открыть TEST кв. 40",
    "🧪 Тест parking_time",
    "🧪 TEST parking_time",
}
QUEUE = "📋 TEST-черга parking_time"
QUEUE_ALIASES = {QUEUE, "📋 TEST-очередь parking_time"}
DAY = "☀️ Day"
NIGHT = "🌙 Night"
INACTIVE = "🚫 Не користується паркуванням"
APPROVE = "✅ Підтвердити TEST без зміни даних"
REJECT = "❌ Відхилити TEST без зміни даних"
CLOSE = "🧹 Закрити TEST без зміни даних"
BACK = "⬅️ До адмін-режиму"
HOME = "🏠 Главное меню"

CHOICES = {
    DAY: "Day",
    NIGHT: "Night",
    INACTIVE: "Inactive",
}


def kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _state(
    user_states: dict,
    user_id: int,
    *,
    create: bool = False,
) -> dict | None:
    value = user_states.get(user_id)
    if isinstance(value, dict) and value.get("_module") == MODULE:
        return value
    if create:
        value = {"_module": MODULE, "mode": ""}
        user_states[user_id] = value
        return value
    return None


def _with_conn(callback):
    conn = get_conn()
    try:
        result = callback(conn)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _candidate_card(candidate: dict) -> str:
    lines = [
        "🧪 Ізольований TEST: перевірка parking_time",
        "",
        "Мета: перевірити сценарій без входу в кабінет мешканця.",
        "Жоден профіль мешканця, привітання, заявка від мешканця чи доступ не створюються.",
        "",
        f"Квартира: {candidate.get('apartment_number')}",
    ]
    vehicles = candidate.get("vehicles") or []
    if not vehicles:
        lines += [
            "⚠️ У квартирі немає авто з порожнім parking_time.",
            "TEST не буде відкритий.",
        ]
    else:
        lines.append("Автомобілі з невизначеним режимом паркування:")
        for row in vehicles:
            lines += [
                f"• ID {row.get('id')} | {row.get('plate') or 'без номера'}",
                f"  Модель: {row.get('model') or '—'} | Колір: {row.get('color') or '—'}",
                "  parking_time: ⛔ порожньо",
            ]
        lines += [
            "",
            "🧪 Симуляція: телефонний доступ заблокований, доки режим не визначено.",
        ]
    return "\n".join(lines)


def _session_card(session: dict) -> str:
    view = session_for_display(session)
    lines = [
        "🧪 TEST-сесія parking_time",
        "",
        f"№: {view.get('session_number')}",
        f"Квартира: {view.get('target_apartment_number')}",
        f"Автомобіль: {view.get('plate_snapshot') or 'без номера'}",
        f"Модель: {view.get('model_snapshot') or '—'}",
        f"Колір: {view.get('color_snapshot') or '—'}",
        f"Джерельний parking_time: {view.get('source_parking_display')}",
        f"Статус TEST: {view.get('test_status')}",
        f"Запропоновано в TEST: {view.get('proposed_label')}",
        "",
        view["simulation"]["text"],
        "",
        "🔒 Гарантія: vehicles.parking_time, профіль мешканця та телефонний доступ не змінюються.",
    ]
    if view.get("review_note"):
        lines.append(f"Нотатка оператора: {view.get('review_note')}")
    return "\n".join(lines)


async def _show_home(update: Update, user_states: dict, user_id: int) -> None:
    try:
        candidate = _with_conn(
            lambda conn: test_candidate(
                apartment_number=TEST_TARGET_APARTMENT,
                conn=conn,
            )
        )
    except Exception as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return

    state = _state(user_states, user_id, create=True)
    state.clear()
    state.update({"_module": MODULE, "mode": "home"})

    rows: list[list[str]] = []
    if candidate.get("suitable"):
        rows.append([OPEN_TEST])
    rows += [[QUEUE], [BACK], [HOME]]
    await update.message.reply_text(_candidate_card(candidate), reply_markup=kb(rows))


async def _show_session(update: Update, state: dict, session: dict) -> None:
    state["mode"] = "session"
    state["session_id"] = int(session["id"])
    status = text(session.get("test_status"))
    rows: list[list[str]] = []
    if status == STATUS_OPEN:
        rows += [[DAY, NIGHT], [INACTIVE], [CLOSE]]
    elif status == STATUS_PENDING_OPERATOR:
        rows += [[APPROVE], [REJECT], [CLOSE]]
    rows += [[QUEUE], [BACK], [HOME]]
    await update.message.reply_text(_session_card(session), reply_markup=kb(rows))


async def _show_queue(update: Update, user_states: dict, user_id: int) -> None:
    try:
        sessions = _with_conn(lambda conn: list_test_sessions(conn=conn))
    except Exception as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return

    state = _state(user_states, user_id, create=True)
    state.clear()
    state.update({"_module": MODULE, "mode": "queue"})
    mapping: dict[str, int] = {}
    rows: list[list[str]] = []
    lines = ["📋 TEST-черга parking_time", ""]
    if not sessions:
        lines.append("Відкритих TEST-сесій немає.")
    for session in sessions:
        label = (
            f"🧪 {session.get('session_number')} | "
            f"кв.{session.get('target_apartment_number')} | "
            f"{session.get('plate_snapshot') or 'без номера'}"
        )
        mapping[label] = int(session["id"])
        rows.append([label])
        lines.append(
            f"{session.get('session_number')} | кв.{session.get('target_apartment_number')} "
            f"| {session.get('test_status')}"
        )
    state["session_buttons"] = mapping
    rows += [[OPEN_TEST], [BACK], [HOME]]
    await update.message.reply_text("\n".join(lines), reply_markup=kb(rows))


async def handle_profile_parking_time_test_text(
    update: Update,
    user_states: dict,
    user_id: int,
    message_text: str,
    *,
    lang: str,
    is_admin: bool,
) -> bool:
    """
    Return True only for the isolated operator TEST flow.

    This handler intentionally runs before legacy user states. It never
    impersonates a resident and only writes to profile_parking_time_test_*.
    """
    message_text = text(message_text)
    state = _state(user_states, user_id, create=False)

    if not is_admin:
        # Do not disclose test candidate data to residents.
        if message_text in OPEN_TEST_ALIASES or message_text in QUEUE_ALIASES:
            await update.message.reply_text("⛔ TEST-доступ доступний лише оператору.")
            return True
        return False

    if message_text in OPEN_TEST_ALIASES:
        try:
            session = _with_conn(
                lambda conn: open_test_session(
                    apartment_number=TEST_TARGET_APARTMENT,
                    actor_id=user_id,
                    conn=conn,
                )
            )
        except Exception as exc:
            await update.message.reply_text(f"⚠️ {exc}")
            return True
        state = _state(user_states, user_id, create=True)
        await _show_session(update, state, session)
        return True

    if message_text in QUEUE_ALIASES:
        await _show_queue(update, user_states, user_id)
        return True

    if not state:
        return False

    if message_text in {HOME, BACK, "🏠 Головне меню"}:
        user_states.pop(user_id, None)
        return False

    if state.get("mode") == "queue":
        session_id = (state.get("session_buttons") or {}).get(message_text)
        if session_id:
            try:
                session = _with_conn(
                    lambda conn: get_test_session(
                        session_id=int(session_id),
                        conn=conn,
                    )
                )
            except Exception as exc:
                await update.message.reply_text(f"⚠️ {exc}")
                return True
            if not session:
                await update.message.reply_text("⚠️ TEST-сесію не знайдено.")
                return True
            await _show_session(update, state, session)
            return True
        await update.message.reply_text("Оберіть TEST-сесію кнопкою.")
        return True

    if state.get("mode") == "session":
        session_id = int(state["session_id"])
        if message_text in CHOICES:
            try:
                session = _with_conn(
                    lambda conn: propose_parking_time(
                        session_id=session_id,
                        parking_time=CHOICES[message_text],
                        actor_id=user_id,
                        conn=conn,
                    )
                )
            except Exception as exc:
                await update.message.reply_text(f"⚠️ {exc}")
                return True
            await _show_session(update, state, session)
            return True

        if message_text == APPROVE:
            try:
                session = _with_conn(
                    lambda conn: decide_test_session(
                        session_id=session_id,
                        approve=True,
                        actor_id=user_id,
                        conn=conn,
                    )
                )
            except Exception as exc:
                await update.message.reply_text(f"⚠️ {exc}")
                return True
            await _show_session(update, state, session)
            return True

        if message_text == REJECT:
            try:
                session = _with_conn(
                    lambda conn: decide_test_session(
                        session_id=session_id,
                        approve=False,
                        actor_id=user_id,
                        conn=conn,
                    )
                )
            except Exception as exc:
                await update.message.reply_text(f"⚠️ {exc}")
                return True
            await _show_session(update, state, session)
            return True

        if message_text == CLOSE:
            try:
                session = _with_conn(
                    lambda conn: close_test_session(
                        session_id=session_id,
                        actor_id=user_id,
                        conn=conn,
                    )
                )
            except Exception as exc:
                await update.message.reply_text(f"⚠️ {exc}")
                return True
            await _show_session(update, state, session)
            return True

        await update.message.reply_text("Оберіть дію TEST-сесії кнопкою.")
        return True

    await update.message.reply_text("Оберіть дію кнопкою.")
    return True
