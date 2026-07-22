# -*- coding: utf-8 -*-
"""
Telegram UI for resident profile verification.

The handler is intentionally independent from the phone number used as an
access credential. It verifies apartment/vehicle data only; an access phone can
be private and need not match any known resident or Telegram contact number.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
from typing import Any

from telegram import ReplyKeyboardMarkup, Update

HANDLERS_DIR = Path(__file__).resolve().parent
BOTS_DIR = HANDLERS_DIR.parent
ROOT = BOTS_DIR.parent
for folder in (ROOT, BOTS_DIR):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from handlers import client_portal as resident_portal
from service_orders_core import get_conn, text
from profile_verification_core import (
    REQUEST_GENERAL_CORRECTION,
    REQUEST_PARKING_TIME,
    STATUS_READY,
    confirm_profile,
    create_general_correction_request,
    create_parking_time_request,
    declare_no_vehicle,
    ensure_profile_verification_schema,
    get_profile_request,
    list_pending_profile_requests,
    mark_welcome_shown,
    profile_snapshot,
    resolve_profile_request,
)


MODULE = "resident_profile_verification_ui"

HOME_TEXTS = {"🏠 Главное меню", "🏠 Головне меню", "🏠 Main menu", "⬅️ Назад"}
VERIFY_BUTTONS = {
    "📋 Перевірити мої дані",
    "📋 Проверить мои данные",
    "📋 Verify my data",
}
ADMIN_QUEUE_BUTTONS = {
    "📋 Перевірка даних мешканців",
    "📋 Проверка данных жителей",
    "📋 Resident data review",
}

BUTTON_CONFIRM = {
    "ru": "✅ Подтвердить обязательные данные",
    "uk": "✅ Підтвердити обов’язкові дані",
    "en": "✅ Confirm required data",
}
BUTTON_NO_CAR = {
    "ru": "🚫 Подтверждаю: автомобиля нет",
    "uk": "🚫 Підтверджую: автомобіля немає",
    "en": "🚫 I confirm: no vehicle",
}
BUTTON_PARKING = {
    "ru": "🅿️ Уточнить тариф парковки",
    "uk": "🅿️ Уточнити тариф паркування",
    "en": "🅿️ Clarify parking tariff",
}
BUTTON_CORRECTION = {
    "ru": "✏️ Сообщить о неточности",
    "uk": "✏️ Повідомити про неточність",
    "en": "✏️ Report an issue",
}
BUTTON_REFRESH = {
    "ru": "🔄 Обновить данные",
    "uk": "🔄 Оновити дані",
    "en": "🔄 Refresh data",
}
BUTTON_BACK = {
    "ru": "⬅️ В кабинет",
    "uk": "⬅️ До кабінету",
    "en": "⬅️ Back to account",
}
BUTTON_QUEUE = {
    "ru": "📋 Очередь заявок",
    "uk": "📋 Черга заявок",
    "en": "📋 Request queue",
}
BUTTON_APPROVE = {
    "ru": "✅ Подтвердить оператором",
    "uk": "✅ Підтвердити оператором",
    "en": "✅ Approve as operator",
}
BUTTON_REJECT = {
    "ru": "❌ Отклонить",
    "uk": "❌ Відхилити",
    "en": "❌ Reject",
}
BUTTON_OPERATOR_BACK = {
    "ru": "⬅️ К проверке данных",
    "uk": "⬅️ До перевірки даних",
    "en": "⬅️ Back to data review",
}

PARKING_BUTTONS = {
    "☀️ Day": "Day",
    "🌙 Night": "Night",
    "🚫 Не користується паркуванням": "Inactive",
    # Backward-compatible aliases for any already-open keyboard.
    "🚫 Не паркуется": "Inactive",
    "🚫 Не паркується": "Inactive",
}


def kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def _tr(mapping: dict[str, str], lang: str) -> str:
    return mapping.get(lang, mapping["ru"])


def _state(user_states: dict, user_id: int, *, create: bool = False) -> dict | None:
    value = user_states.get(user_id)
    if isinstance(value, dict) and value.get("_module") == MODULE:
        return value
    if create:
        value = {"_module": MODULE, "area": "", "mode": ""}
        user_states[user_id] = value
        return value
    return None


def _home(message_text: str) -> bool:
    return text(message_text) in HOME_TEXTS


def _account_unit(user_id: int) -> dict | None:
    data = resident_portal._account_and_unit(user_id)
    if not data or not data.get("account") or not data.get("unit"):
        return None
    return data


def _snapshot_for(data: dict) -> dict:
    conn = get_conn()
    try:
        ensure_profile_verification_schema(conn)
        snapshot = profile_snapshot(
            resident_account_id=int(data["account"]["id"]),
            apartment_id=data["unit"].get("id"),
            apartment_number=text(data["unit"].get("apartment_number")),
            conn=conn,
        )
        conn.commit()
        return snapshot
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _fmt(value: str | None, missing: str = "⚠️ не вказано") -> str:
    return text(value) or missing


def _parking_display(value: str | None) -> str:
    value = text(value)
    if value == "Day":
        return "☀️ Day"
    if value == "Night":
        return "🌙 Night"
    if value == "Inactive":
        return "🚫 Не користується паркуванням"
    return "⛔ не визначено"


def _vehicle_lines(snapshot: dict) -> list[str]:
    vehicles = snapshot.get("vehicles") or []
    if not vehicles:
        if int(snapshot["profile"].get("no_vehicle_declared") or 0) == 1:
            return ["🚗 Автомобілі: ✅ підтверджено, що автомобіля немає"]
        return [
            "🚗 Автомобілі: ⛔ статус не визначено",
            "   Потрібно явно підтвердити: автомобіля немає, або подати уточнення.",
        ]
    lines = ["🚗 Автомобілі:"]
    for i, vehicle in enumerate(vehicles, 1):
        lines += [
            f"{i}. Номер: {_fmt(vehicle.get('plate'), '⛔ не вказано')}",
            f"   Марка/модель: {_fmt(vehicle.get('model'), 'ℹ️ не вказано')}",
            f"   Колір: {_fmt(vehicle.get('color'), 'ℹ️ не вказано')}",
            f"   Паркування: {_parking_display(vehicle.get('parking_time'))}",
        ]
    return lines


def _profile_card(snapshot: dict, lang: str) -> str:
    profile = snapshot["profile"]
    lines = [
        "📋 Перевірка даних",
        "",
        f"🏠 Квартира: {profile.get('apartment_number') or '⛔ не вказано'}",
        *_vehicle_lines(snapshot),
        "",
    ]

    structural = snapshot.get("structural_critical") or []
    advisory = snapshot.get("advisory") or []
    if structural:
        lines += ["⛔ Обов’язкові дані не завершені:"]
        lines += [f"• {item.get('text')}" for item in structural]
        lines += [
            "",
            "Телефонний доступ: ⛔ недоступний до завершення обов’язкових даних.",
        ]
    elif snapshot.get("resident_confirmation_required"):
        lines += [
            "🟡 Обов’язкові дані заповнені.",
            "Потрібне ваше підтвердження обов’язкових даних.",
            "",
            "Телефонний доступ: 🟡 буде доступний після підтвердження.",
        ]
    else:
        lines += [
            "✅ Обов’язкові дані підтверджені.",
            "Телефонний доступ: ✅ можна підключати.",
        ]

    if advisory:
        lines += ["", "ℹ️ Додаткові дані для уточнення — не блокують підключення:"]
        lines += [f"• {item.get('text')}" for item in advisory]

    status = text(profile.get("verification_status"))
    lines += [
        "",
        f"Стан профілю: {status}",
        "",
        "🔒 Номер для відкриття шлагбаума є окремим непублічним номером доступу.",
        "Він може відрізнятися від контактних номерів у кабінеті та не звіряється з ними.",
    ]
    return "\n".join(lines)


def _gate_message(snapshot: dict, lang: str) -> str:
    structural = snapshot.get("structural_critical") or []
    if structural:
        lines = [
            "⛔ Підключення телефонного доступу неможливе.",
            "",
            "Спочатку завершіть обов’язкові дані у кабінеті:",
        ]
        lines += [f"• {item.get('text')}" for item in structural]
    else:
        lines = [
            "🟡 Обов’язкові дані вже заповнені, але ще не підтверджені.",
            "",
            "Відкрийте «Перевірити мої дані» та натисніть:",
            "«✅ Підтвердити обов’язкові дані».",
        ]
    lines += [
        "",
        "Номер для відкриття може бути окремим і не збігатися з контактними телефонами.",
    ]
    return "\n".join(lines)


async def maybe_show_profile_welcome(
    update: Update,
    user_states: dict,
    user_id: int,
    *,
    lang: str,
) -> None:
    """Show a one-time concise welcome after the client menu is displayed."""
    data = _account_unit(user_id)
    if not data:
        return

    conn = get_conn()
    try:
        ensure_profile_verification_schema(conn)
        snapshot = profile_snapshot(
            resident_account_id=int(data["account"]["id"]),
            apartment_id=data["unit"].get("id"),
            apartment_number=text(data["unit"].get("apartment_number")),
            conn=conn,
        )
        profile = snapshot["profile"]
        should_show = (
            not profile.get("welcome_shown_at")
            and not bool(snapshot.get("phone_access_allowed"))
        )
        if should_show:
            mark_welcome_shown(
                resident_account_id=int(data["account"]["id"]),
                apartment_id=data["unit"].get("id"),
                apartment_number=text(data["unit"].get("apartment_number")),
                conn=conn,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        return
    finally:
        conn.close()

    if should_show:
        await update.message.reply_text(
            "👋 Вітаємо!\n\n"
            "Перед користуванням послугами, будь ласка, перевірте дані у кабінеті: "
            "квартиру, автомобілі, державні номери та тариф паркування.\n\n"
            "Незаповнені критичні дані будуть виділені. До завершення перевірки "
            "підключення телефонного доступу недоступне.",
            reply_markup=kb([[ _tr({"ru": "📋 Проверить мои данные", "uk": "📋 Перевірити мої дані", "en": "📋 Verify my data"}, lang) ], ["🏠 Главное меню"]]),
        )


async def show_profile_verification(
    update: Update,
    user_states: dict,
    user_id: int,
    *,
    lang: str,
) -> None:
    data = _account_unit(user_id)
    if not data:
        await update.message.reply_text(
            "Спочатку прив’яжіть квартиру в особистому кабінеті.",
            reply_markup=kb([[ _tr(BUTTON_BACK, lang) ], ["🏠 Главное меню"]]),
        )
        return
    try:
        snapshot = _snapshot_for(data)
    except Exception as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return

    state = _state(user_states, user_id, create=True)
    state.clear()
    state.update(
        {
            "_module": MODULE,
            "area": "resident",
            "mode": "profile_card",
            "data": data,
            "lang": lang,
        }
    )

    rows: list[list[str]] = []
    # `critical_codes` drives the appropriate correction buttons, while
    # `structural_codes` decides whether mandatory data are complete enough
    # for the resident's confirmation action.
    critical_codes = {
        text(item.get("code"))
        for item in snapshot.get("critical") or []
    }
    structural_codes = {
        text(item.get("code"))
        for item in snapshot.get("structural_critical") or []
    }
    # The confirmation action is available only while the resident still
    # needs to confirm completed mandatory data. Once the profile is READY,
    # the button must disappear rather than inviting a duplicate confirmation.
    if snapshot.get("resident_confirmation_required"):
        rows.append([_tr(BUTTON_CONFIRM, lang)])
    if "CONFIRM_NO_VEHICLE" in critical_codes:
        rows.append([_tr(BUTTON_NO_CAR, lang)])
    if "PARKING_TIME" in critical_codes:
        rows.append([_tr(BUTTON_PARKING, lang)])
    rows += [
        [_tr(BUTTON_CORRECTION, lang)],
        [_tr(BUTTON_REFRESH, lang), _tr(BUTTON_BACK, lang)],
        ["🏠 Главное меню"],
    ]
    await update.message.reply_text(_profile_card(snapshot, lang), reply_markup=kb(rows))


async def show_phone_access_block(
    update: Update,
    user_states: dict,
    user_id: int,
    *,
    lang: str,
    snapshot: dict,
) -> None:
    """Used by the services workspace before it displays phone-access offers."""
    await update.message.reply_text(
        _gate_message(snapshot, lang),
        reply_markup=kb(
            [
                [_tr({"ru": "📋 Проверить мои данные", "uk": "📋 Перевірити мої дані", "en": "📋 Verify my data"}, lang)],
                ["🏠 Главное меню"],
            ]
        ),
    )


async def _show_parking_vehicle_select(update: Update, state: dict, lang: str) -> None:
    data = state["data"]
    snapshot = _snapshot_for(data)
    missing = [
        row for row in snapshot.get("vehicles") or []
        if not text(row.get("parking_time"))
    ]
    if not missing:
        await show_profile_verification(
            update, state["_user_states"], state["_user_id"], lang=lang
        )
        return
    mapping: dict[str, int] = {}
    rows: list[list[str]] = []
    for index, vehicle in enumerate(missing, 1):
        label = f"🚗 {index}. {vehicle.get('plate') or 'без номера'}"
        mapping[label] = int(vehicle["id"])
        rows.append([label])
    state["mode"] = "parking_vehicle_select"
    state["parking_vehicle_buttons"] = mapping
    await update.message.reply_text(
        "Оберіть автомобіль, для якого потрібно вказати тариф паркування:",
        reply_markup=kb(rows + [[_tr(BUTTON_BACK, lang)], ["🏠 Главное меню"]]),
    )


async def _show_operator_queue(
    update: Update,
    user_states: dict,
    user_id: int,
    *,
    lang: str,
) -> None:
    conn = get_conn()
    try:
        ensure_profile_verification_schema(conn)
        rows_data = list_pending_profile_requests(conn=conn)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        await update.message.reply_text(f"⚠️ {exc}")
        return
    finally:
        conn.close()

    state = _state(user_states, user_id, create=True)
    state.clear()
    state.update(
        {
            "_module": MODULE,
            "area": "operator",
            "mode": "operator_queue",
            "lang": lang,
        }
    )
    mapping: dict[str, int] = {}
    rows: list[list[str]] = []
    lines = ["📋 Перевірка даних мешканців", ""]
    for request in rows_data:
        label = (
            f"📝 {request['request_number']} | кв.{request.get('apartment_number') or '-'}"
            f" | {request.get('request_type')}"
        )
        mapping[label] = int(request["id"])
        rows.append([label])
        lines.append(
            f"{request['request_number']} | кв.{request.get('apartment_number') or '-'} | "
            f"{request.get('request_type')}"
        )
    if not rows_data:
        lines.append("Незавершених заявок немає.")
    state["operator_request_buttons"] = mapping
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=kb(rows + [[_tr(BUTTON_REFRESH, lang)], ["🏠 Главное меню"]]),
    )


def _operator_request_card(request: dict) -> str:
    lines = [
        "📝 Заявка на уточнення даних",
        "",
        f"№: {request.get('request_number')}",
        f"Квартира: {request.get('apartment_number') or '-'}",
        f"Тип: {request.get('request_type')}",
        f"Статус: {request.get('request_status')}",
    ]
    if request.get("vehicle_id"):
        lines.append(f"Автомобіль ID: {request.get('vehicle_id')}")
    current = request.get("current_value") or {}
    proposed = request.get("requested_value") or {}
    if current:
        lines.append("Було: " + ", ".join(f"{k}={v}" for k, v in current.items()))
    if proposed:
        lines.append("Запропоновано: " + ", ".join(f"{k}={v}" for k, v in proposed.items()))
    if text(request.get("resident_note")):
        lines.append(f"Коментар мешканця: {request.get('resident_note')}")
    if text(request.get("request_type")) == REQUEST_GENERAL_CORRECTION:
        lines += [
            "",
            "Прийняття такої заявки не змінює поля автоматично.",
            "Оператор застосовує виправлення лише через контрольований редактор.",
        ]
    return "\n".join(lines)


async def _show_operator_request(
    update: Update,
    state: dict,
    request_id: int,
    lang: str,
) -> None:
    conn = get_conn()
    try:
        ensure_profile_verification_schema(conn)
        request = get_profile_request(request_id=int(request_id), conn=conn)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        await update.message.reply_text(f"⚠️ {exc}")
        return
    finally:
        conn.close()
    if not request:
        await update.message.reply_text("Заявку не знайдено.")
        return
    state["mode"] = "operator_request_card"
    state["request_id"] = int(request_id)
    await update.message.reply_text(
        _operator_request_card(request),
        reply_markup=kb(
            [
                [_tr(BUTTON_APPROVE, lang), _tr(BUTTON_REJECT, lang)],
                [_tr(BUTTON_OPERATOR_BACK, lang)],
                ["🏠 Главное меню"],
            ]
        ),
    )


async def handle_profile_verification_text(
    update: Update,
    user_states: dict,
    user_id: int,
    message_text: str,
    *,
    lang: str,
    user_mode: str | None,
    is_admin: bool,
) -> bool:
    """
    Return True only when this module owns the message.

    It is designed to run before the broad client-portal fallback and before
    phone-access service routing.
    """
    message_text = text(message_text)
    lang = lang if lang in {"ru", "uk", "en"} else "ru"
    state = _state(user_states, user_id, create=False)

    if state:
        if _home(message_text) or message_text == _tr(BUTTON_BACK, lang):
            user_states.pop(user_id, None)
            return False

        if state.get("area") == "resident":
            data = state["data"]
            if state.get("mode") == "profile_card":
                if message_text == _tr(BUTTON_REFRESH, lang):
                    await show_profile_verification(update, user_states, user_id, lang=lang)
                    return True
                if message_text == _tr(BUTTON_CONFIRM, lang):
                    conn = get_conn()
                    try:
                        ensure_profile_verification_schema(conn)
                        confirm_profile(
                            resident_account_id=int(data["account"]["id"]),
                            apartment_id=data["unit"].get("id"),
                            apartment_number=text(data["unit"].get("apartment_number")),
                            actor_id=user_id,
                            conn=conn,
                        )
                        conn.commit()
                    except Exception as exc:
                        conn.rollback()
                        await update.message.reply_text(f"⚠️ {exc}")
                        return True
                    finally:
                        conn.close()
                    await update.message.reply_text(
                        "✅ Обов’язкові дані підтверджено. "
                        "Телефонний доступ можна підключати."
                    )
                    await show_profile_verification(update, user_states, user_id, lang=lang)
                    return True
                if message_text == _tr(BUTTON_NO_CAR, lang):
                    conn = get_conn()
                    try:
                        ensure_profile_verification_schema(conn)
                        declare_no_vehicle(
                            resident_account_id=int(data["account"]["id"]),
                            apartment_id=data["unit"].get("id"),
                            apartment_number=text(data["unit"].get("apartment_number")),
                            actor_id=user_id,
                            conn=conn,
                        )
                        conn.commit()
                    except Exception as exc:
                        conn.rollback()
                        await update.message.reply_text(f"⚠️ {exc}")
                        return True
                    finally:
                        conn.close()
                    await update.message.reply_text("✅ Підтвердження збережено: автомобіля немає.")
                    await show_profile_verification(update, user_states, user_id, lang=lang)
                    return True
                if message_text == _tr(BUTTON_PARKING, lang):
                    state["_user_states"] = user_states
                    state["_user_id"] = user_id
                    await _show_parking_vehicle_select(update, state, lang)
                    return True
                if message_text == _tr(BUTTON_CORRECTION, lang):
                    state["mode"] = "general_correction_note"
                    await update.message.reply_text(
                        "Опишіть, будь ласка, що потрібно виправити. "
                        "Заявка надійде оператору; дані не змінюються автоматично.",
                        reply_markup=kb([[_tr(BUTTON_BACK, lang)], ["🏠 Главное меню"]]),
                    )
                    return True
                await update.message.reply_text("Оберіть дію кнопкою.")
                return True

            if state.get("mode") == "parking_vehicle_select":
                vehicle_id = (state.get("parking_vehicle_buttons") or {}).get(message_text)
                if not vehicle_id:
                    await update.message.reply_text("Оберіть автомобіль кнопкою.")
                    return True
                state["vehicle_id"] = int(vehicle_id)
                state["mode"] = "parking_time_choice"
                await update.message.reply_text(
                    "Оберіть тариф/статус паркування:",
                    reply_markup=kb(
                        [
                            ["☀️ Day", "🌙 Night"],
                            ["🚫 Не користується паркуванням"],
                            [_tr(BUTTON_BACK, lang)],
                            ["🏠 Главное меню"],
                        ]
                    ),
                )
                return True

            if state.get("mode") == "parking_time_choice":
                parking_time = PARKING_BUTTONS.get(message_text)
                if not parking_time:
                    await update.message.reply_text("Оберіть значення кнопкою.")
                    return True
                conn = get_conn()
                try:
                    ensure_profile_verification_schema(conn)
                    request = create_parking_time_request(
                        resident_account_id=int(data["account"]["id"]),
                        apartment_id=data["unit"].get("id"),
                        apartment_number=text(data["unit"].get("apartment_number")),
                        vehicle_id=int(state["vehicle_id"]),
                        parking_time=parking_time,
                        actor_id=user_id,
                        conn=conn,
                    )
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    await update.message.reply_text(f"⚠️ {exc}")
                    return True
                finally:
                    conn.close()
                await update.message.reply_text(
                    f"✅ Заявку {request.get('request_number')} передано оператору. "
                    "Поки оператор не завершить перевірку, обов’язкові дані "
                    "вважаються незавершеними і телефонний доступ недоступний."
                )
                await show_profile_verification(update, user_states, user_id, lang=lang)
                return True

            if state.get("mode") == "general_correction_note":
                conn = get_conn()
                try:
                    ensure_profile_verification_schema(conn)
                    request = create_general_correction_request(
                        resident_account_id=int(data["account"]["id"]),
                        apartment_id=data["unit"].get("id"),
                        apartment_number=text(data["unit"].get("apartment_number")),
                        note=message_text,
                        actor_id=user_id,
                        conn=conn,
                    )
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    await update.message.reply_text(f"⚠️ {exc}")
                    return True
                finally:
                    conn.close()
                await update.message.reply_text(
                    f"✅ Заявку {request.get('request_number')} передано оператору."
                )
                await show_profile_verification(update, user_states, user_id, lang=lang)
                return True

        if state.get("area") == "operator":
            if not is_admin:
                user_states.pop(user_id, None)
                await update.message.reply_text("⛔ Немає права на цей розділ.")
                return True
            if state.get("mode") == "operator_queue":
                if message_text == _tr(BUTTON_REFRESH, lang):
                    await _show_operator_queue(update, user_states, user_id, lang=lang)
                    return True
                request_id = (state.get("operator_request_buttons") or {}).get(message_text)
                if request_id:
                    await _show_operator_request(update, state, int(request_id), lang)
                    return True
                await update.message.reply_text("Оберіть заявку кнопкою.")
                return True
            if state.get("mode") == "operator_request_card":
                if message_text == _tr(BUTTON_OPERATOR_BACK, lang):
                    await _show_operator_queue(update, user_states, user_id, lang=lang)
                    return True
                if message_text in {_tr(BUTTON_APPROVE, lang), _tr(BUTTON_REJECT, lang)}:
                    conn = get_conn()
                    try:
                        ensure_profile_verification_schema(conn)
                        result = resolve_profile_request(
                            request_id=int(state["request_id"]),
                            approve=message_text == _tr(BUTTON_APPROVE, lang),
                            actor_id=user_id,
                            conn=conn,
                        )
                        conn.commit()
                    except Exception as exc:
                        conn.rollback()
                        await update.message.reply_text(f"⚠️ {exc}")
                        return True
                    finally:
                        conn.close()
                    await update.message.reply_text(
                        f"✅ Рішення збережено: {result.get('request_status')}."
                    )
                    await _show_operator_queue(update, user_states, user_id, lang=lang)
                    return True
                await update.message.reply_text("Оберіть дію кнопкою.")
                return True

    if user_mode == "client" and message_text in VERIFY_BUTTONS:
        await show_profile_verification(update, user_states, user_id, lang=lang)
        return True

    if user_mode == "admin" and is_admin and message_text in ADMIN_QUEUE_BUTTONS:
        await _show_operator_queue(update, user_states, user_id, lang=lang)
        return True

    return False
