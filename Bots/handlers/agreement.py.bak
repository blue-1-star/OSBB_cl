from telegram import Update, ReplyKeyboardMarkup

from Bots.db_access import (
    get_verification_stats,
    format_verification_stats,
    get_next_apartment_for_verification,
    get_apartments_by_verification_status,
    set_apartment_verification_status,
    format_verification_apartment_list,
    format_apartment_agreement_card,
    get_agreement_dashboard,
    format_agreement_dashboard,
    apply_agreement_suggestion,
    get_tariff_review_stats,
    format_tariff_review_stats,
    get_next_vehicle_without_tariff,
    format_tariff_vehicle_card,
    apply_vehicle_tariff,
    apply_best_tariff_hint,
    get_vehicle_by_id,
    format_vehicle_edit_menu_card,
    set_vehicle_plate_from_text,
    set_vehicle_model_from_text,
    set_vehicle_tariff_from_text,
)


AGREEMENT_MENU = [
    ["▶️ Продолжить"],
    ["🚦 Тарифы"],
    ["🏠 Найти квартиру"],
    ["📌 Сводка"],
    ["⚠️ Конфликты", "⏳ Отложенные"],
    ["📊 Статистика"],
    ["⬅️ Назад"],
]

AGREEMENT_ACTION_MENU = [
    ["✅ Согласовать"],
    ["⏳ Отложить", "⚠️ Конфликт"],
    ["✏️ Исправить"],
    ["⏭ Пропустить"],
    ["⬅️ Назад"],
]

TARIFF_ACTION_MENU = [
    ["🟢 Принять подсказку"],
    ["🌞 Day", "🌙 Night"],
    ["✏️ Исправить авто"],
    ["🚫 Не паркуется"],
    ["⏭ Пропустить"],
    ["⬅️ Назад"],
]

VEHICLE_EDIT_MENU = [
    ["1️⃣ Номер", "2️⃣ Марка"],
    ["3️⃣ Тариф"],
    ["⬅️ Назад"],
]

VEHICLE_EDIT_TARIFF_MENU = [
    ["🌞 Day", "🌙 Night"],
    ["🚫 Не паркуется", "NULL"],
    ["⬅️ Назад"],
]

user_agreement_skips = {}
user_tariff_skips = {}


def kb(buttons):
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


def get_skipped_apartments(user_id: int):
    return user_agreement_skips.setdefault(user_id, set())


def get_skipped_tariff_vehicles(user_id: int):
    return user_tariff_skips.setdefault(user_id, set())


async def show_tariff_review_menu(update: Update):
    stats = get_tariff_review_stats()

    await update.message.reply_text(
        format_tariff_review_stats(stats) + "\n\nВыберите действие:",
        reply_markup=kb([
            ["▶️ Продолжить тарифы"],
            ["📊 Статистика тарифов"],
            ["⬅️ Назад"],
        ]),
    )


async def show_next_tariff_vehicle(update: Update, user_states: dict, user_id: int):
    skipped = get_skipped_tariff_vehicles(user_id)
    vehicle = get_next_vehicle_without_tariff(skipped)

    if not vehicle:
        user_states.pop(user_id, None)

        await update.message.reply_text(
            "Все доступные автомобили без тарифа в этой сессии просмотрены.",
            reply_markup=kb(AGREEMENT_MENU),
        )
        return

    vehicle_id = vehicle[0]
    user_states[user_id] = ("tariff_vehicle", int(vehicle_id))

    await update.message.reply_text(
        format_tariff_vehicle_card(vehicle),
        reply_markup=kb(TARIFF_ACTION_MENU),
    )


async def show_vehicle_edit_menu(update: Update, user_states: dict, user_id: int, vehicle_id: int):
    user_states[user_id] = ("vehicle_edit_menu", int(vehicle_id))

    await update.message.reply_text(
        format_vehicle_edit_menu_card(vehicle_id),
        reply_markup=kb(VEHICLE_EDIT_MENU),
    )


async def handle_vehicle_edit_flow(update: Update, user_states: dict, user_id: int, text: str):
    state = user_states.get(user_id)

    if not (isinstance(state, tuple) and str(state[0]).startswith("vehicle_edit")):
        return False

    state_name = state[0]
    vehicle_id = int(state[1])

    if text == "⬅️ Назад":
        vehicle = get_vehicle_by_id(vehicle_id)

        if vehicle:
            user_states[user_id] = ("tariff_vehicle", vehicle_id)
            await update.message.reply_text(
                format_tariff_vehicle_card(vehicle),
                reply_markup=kb(TARIFF_ACTION_MENU),
            )
        else:
            user_states.pop(user_id, None)
            await show_tariff_review_menu(update)

        return True

    if state_name == "vehicle_edit_menu":
        if text == "1️⃣ Номер":
            user_states[user_id] = ("vehicle_edit_plate", vehicle_id)
            await update.message.reply_text(
                "Введите новый номер авто:",
                reply_markup=kb([["⬅️ Назад"]]),
            )
            return True

        if text == "2️⃣ Марка":
            user_states[user_id] = ("vehicle_edit_model", vehicle_id)
            await update.message.reply_text(
                "Введите новую марку/модель:",
                reply_markup=kb([["⬅️ Назад"]]),
            )
            return True

        if text == "3️⃣ Тариф":
            user_states[user_id] = ("vehicle_edit_tariff", vehicle_id)
            await update.message.reply_text(
                "Выберите тариф:",
                reply_markup=kb(VEHICLE_EDIT_TARIFF_MENU),
            )
            return True

        await update.message.reply_text(
            "Выберите, что изменить.",
            reply_markup=kb(VEHICLE_EDIT_MENU),
        )
        return True

    if state_name == "vehicle_edit_plate":
        ok, result = set_vehicle_plate_from_text(vehicle_id, text, operator_telegram_id=user_id)

        if ok:
            await update.message.reply_text("✅ Номер обновлён.")
            await show_vehicle_edit_menu(update, user_states, user_id, vehicle_id)
        else:
            await update.message.reply_text(
                f"Ошибка: {result}",
                reply_markup=kb([["⬅️ Назад"]]),
            )

        return True

    if state_name == "vehicle_edit_model":
        ok, result = set_vehicle_model_from_text(vehicle_id, text, operator_telegram_id=user_id)

        if ok:
            await update.message.reply_text("✅ Марка обновлена.")
            await show_vehicle_edit_menu(update, user_states, user_id, vehicle_id)
        else:
            await update.message.reply_text(
                f"Ошибка: {result}",
                reply_markup=kb([["⬅️ Назад"]]),
            )

        return True

    if state_name == "vehicle_edit_tariff":
        tariff_map = {
            "🌞 Day": "Day",
            "🌙 Night": "Night",
            "🚫 Не паркуется": "Inactive",
            "NULL": "NULL",
        }

        if text not in tariff_map:
            await update.message.reply_text(
                "Выберите тариф кнопкой.",
                reply_markup=kb(VEHICLE_EDIT_TARIFF_MENU),
            )
            return True

        ok, result = set_vehicle_tariff_from_text(vehicle_id, tariff_map[text], operator_telegram_id=user_id)

        if ok:
            await update.message.reply_text(f"✅ Тариф обновлён: {tariff_map[text]}")
            await show_vehicle_edit_menu(update, user_states, user_id, vehicle_id)
        else:
            await update.message.reply_text(
                f"Ошибка: {result}",
                reply_markup=kb(VEHICLE_EDIT_TARIFF_MENU),
            )

        return True

    return False



async def handle_tariff_vehicle_action(update: Update, user_states: dict, user_id: int, text: str):
    state = user_states.get(user_id)

    if not (isinstance(state, tuple) and state[0] == "tariff_vehicle"):
        return False

    vehicle_id = int(state[1])

    if text == "⬅️ Назад":
        user_states.pop(user_id, None)
        await show_tariff_review_menu(update)
        return True

    if text == "⏭ Пропустить":
        get_skipped_tariff_vehicles(user_id).add(vehicle_id)
        await show_next_tariff_vehicle(update, user_states, user_id)
        return True

    if text == "✏️ Исправить авто":
        await show_vehicle_edit_menu(update, user_states, user_id, vehicle_id)
        return True

    if text == "🟢 Принять подсказку":
        ok, result = apply_best_tariff_hint(vehicle_id, operator_telegram_id=user_id)

        if ok:
            vehicle = get_vehicle_by_id(vehicle_id)
            tariff = vehicle[6] if vehicle else "?"
            await update.message.reply_text(
                f"✅ Тариф сохранён: {tariff}"
            )
            await show_next_tariff_vehicle(update, user_states, user_id)
        else:
            await update.message.reply_text(
                f"Подсказку применить нельзя: {result}",
                reply_markup=kb(TARIFF_ACTION_MENU),
            )
        return True

    tariff_map = {
        "🌞 Day": "Day",
        "🌙 Night": "Night",
        "🚫 Не паркуется": "Inactive",
    }

    if text in tariff_map:
        ok, result = apply_vehicle_tariff(vehicle_id, tariff_map[text], operator_telegram_id=user_id)

        if ok:
            await update.message.reply_text(
                f"✅ Тариф сохранён: {tariff_map[text]}"
            )
            await show_next_tariff_vehicle(update, user_states, user_id)
        else:
            await update.message.reply_text(
                f"Ошибка: {result}",
                reply_markup=kb(TARIFF_ACTION_MENU),
            )
        return True

    await update.message.reply_text(
        "Выберите тариф кнопкой.",
        reply_markup=kb(TARIFF_ACTION_MENU),
    )
    return True


async def show_agreement_menu(update: Update):
    stats = get_verification_stats()

    await update.message.reply_text(
        format_verification_stats(stats) + "\n\nВыберите действие:",
        reply_markup=kb(AGREEMENT_MENU),
    )


async def show_agreement_stats(update: Update):
    stats = get_verification_stats()

    await update.message.reply_text(
        format_verification_stats(stats),
        reply_markup=kb(AGREEMENT_MENU),
    )


async def show_agreement_dashboard(update: Update):
    dashboard = get_agreement_dashboard()

    await update.message.reply_text(
        format_agreement_dashboard(dashboard),
        reply_markup=kb(AGREEMENT_MENU),
    )


async def show_agreement_card(update: Update, user_states: dict, user_id: int, apartment_number: str):
    user_states[user_id] = ("agreement_card", str(apartment_number))

    await update.message.reply_text(
        format_apartment_agreement_card(apartment_number),
        reply_markup=kb(AGREEMENT_ACTION_MENU),
    )


async def show_next_agreement_apartment(update: Update, user_states: dict, user_id: int):
    skipped = get_skipped_apartments(user_id)
    row = get_next_apartment_for_verification(skipped)

    if not row:
        user_states.pop(user_id, None)

        await update.message.reply_text(
            "Новых квартир в текущей сессии больше нет.\n\n"
            "Если нужно начать просмотр заново, нажмите 🤝 Согласование.",
            reply_markup=kb(AGREEMENT_MENU),
        )
        return

    apartment_number = row[2]
    await show_agreement_card(update, user_states, user_id, apartment_number)


async def show_agreement_list(update: Update, title: str, status: str):
    rows = get_apartments_by_verification_status(status, limit=50)

    await update.message.reply_text(
        format_verification_apartment_list(title, rows),
        reply_markup=kb(AGREEMENT_MENU),
    )


async def handle_waiting_agreement_apartment(update: Update, user_states: dict, user_id: int, text: str):
    if text == "⬅️ Назад":
        user_states.pop(user_id, None)
        await show_agreement_menu(update)
        return

    user_states.pop(user_id, None)
    await show_agreement_card(update, user_states, user_id, text)


async def handle_agreement_card_action(
    update: Update,
    user_states: dict,
    user_id: int,
    text: str,
    show_apartment_card_callback,
):
    state = user_states.get(user_id)

    if not (isinstance(state, tuple) and state[0] == "agreement_card"):
        user_states.pop(user_id, None)
        await show_agreement_menu(update)
        return

    apartment_number = state[1]

    if text == "⬅️ Назад":
        user_states.pop(user_id, None)
        await show_agreement_menu(update)
        return

    if text == "⏭ Пропустить":
        get_skipped_apartments(user_id).add(str(apartment_number))
        await show_next_agreement_apartment(update, user_states, user_id)
        return

    if text == "✏️ Исправить":
        user_states.pop(user_id, None)
        await show_apartment_card_callback(update, user_id, apartment_number)
        return

    if text == "✅ Согласовать":
        ok, result = apply_agreement_suggestion(
            apartment_number,
            verified_by=user_id,
        )

        if ok:
            applied = result.get("applied") if isinstance(result, dict) else None

            if applied and applied.get("action") == "created":
                await update.message.reply_text(
                    f"✅ Квартира {apartment_number} согласована.\n"
                    f"Создано авто: {applied.get('plate')} | "
                    f"{applied.get('model') or '-'} | "
                    f"{applied.get('parking_time') or 'NULL'}"
                )
            elif applied and applied.get("action") == "updated":
                await update.message.reply_text(
                    f"✅ Квартира {apartment_number} согласована.\n"
                    "Данные авто обновлены."
                )
            else:
                await update.message.reply_text(
                    f"✅ Квартира {apartment_number} согласована."
                )

            await show_next_agreement_apartment(update, user_states, user_id)
        else:
            await update.message.reply_text(
                f"Ошибка: {result}",
                reply_markup=kb(AGREEMENT_ACTION_MENU),
            )
        return

    if text == "⏳ Отложить":
        ok, result = set_apartment_verification_status(
            apartment_number,
            "deferred",
            verified_by=user_id,
        )

        if ok:
            await update.message.reply_text(f"⏳ Квартира {apartment_number} отложена.")
            await show_next_agreement_apartment(update, user_states, user_id)
        else:
            await update.message.reply_text(
                f"Ошибка: {result}",
                reply_markup=kb(AGREEMENT_ACTION_MENU),
            )
        return

    if text == "⚠️ Конфликт":
        ok, result = set_apartment_verification_status(
            apartment_number,
            "conflict",
            verified_by=user_id,
        )

        if ok:
            await update.message.reply_text(f"⚠️ Квартира {apartment_number}: конфликт.")
            await show_next_agreement_apartment(update, user_states, user_id)
        else:
            await update.message.reply_text(
                f"Ошибка: {result}",
                reply_markup=kb(AGREEMENT_ACTION_MENU),
            )
        return

    await update.message.reply_text(
        "Выберите действие кнопкой.",
        reply_markup=kb(AGREEMENT_ACTION_MENU),
    )


async def handle_agreement_menu_text(update: Update, user_states: dict, user_id: int, text: str):
    if await handle_vehicle_edit_flow(update, user_states, user_id, text):
        return True

    if await handle_tariff_vehicle_action(update, user_states, user_id, text):
        return True

    if text == "🤝 Согласование":
        user_agreement_skips.pop(user_id, None)
        await show_agreement_menu(update)
        return True

    if text == "▶️ Продолжить":
        await show_next_agreement_apartment(update, user_states, user_id)
        return True

    if text == "🚦 Тарифы":
        user_tariff_skips.pop(user_id, None)
        await show_tariff_review_menu(update)
        return True

    if text == "▶️ Продолжить тарифы":
        await show_next_tariff_vehicle(update, user_states, user_id)
        return True

    if text == "📊 Статистика тарифов":
        await show_tariff_review_menu(update)
        return True

    if text == "🏠 Найти квартиру":
        user_states[user_id] = "admin_waiting_agreement_apartment"

        await update.message.reply_text(
            "Введите номер квартиры:",
            reply_markup=kb([["⬅️ Назад"]]),
        )
        return True

    if text == "📌 Сводка":
        await show_agreement_dashboard(update)
        return True

    if text == "⚠️ Конфликты":
        await show_agreement_list(update, "Конфликты", "conflict")
        return True

    if text == "⏳ Отложенные":
        await show_agreement_list(update, "Отложенные", "deferred")
        return True

    if text == "📊 Статистика":
        await show_agreement_stats(update)
        return True

    return False
