#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cashier_v2_core as core
from tools.cashier_v2_telegram.cashier_search import (
    choose_service,
    group_from_payer,
    payer_vehicle_choices,
    proposed_defaults,
    search_payers,
    search_commercial_subjects,
)
from tools.cashier_v2_telegram.cashier_card import payment_card, period_display, success_card
from Bots.handlers.vehicle_card_editor import create_draft_vehicle, create_registry_vehicle

BTN_PAYMENTS = "💳 Платежи"
BTN_CASHIER_V2 = "💰 Касса v2"
BTN_CASH = "💵 Наличные"
BTN_SUMMARY = "📊 Сводка кассы"
BTN_LAST_RECEIPTS = "📜 Последние чеки"
BTN_SETTINGS = "⚙️ Настройки кассы"

BTN_NIGHT = "🌙 Night"
BTN_DAY = "☀️ Day"
BTN_MISC = "📦 Другое"
BTN_ACTUAL = "📌 Актуальный сбор"
BTN_REMOTES = "🔑 Пульты"
BTN_PHONE = "📞 Телефонный доступ"
BTN_COMMON = "🧰 Общие сборы"
BTN_PARKING = "🅿️ Паркоместа"
BTN_COMMERCIAL = "🏢 Коммерческие"

BTN_ACCEPT = "✅ Принять как есть"
BTN_EDIT = "✏️ Изменить"
BTN_EDIT_PERIOD = "📅 Период"
BTN_EDIT_AMOUNT = "💰 Сумму"
BTN_EDIT_COMMENT = "📝 Комментарий"
BTN_SKIP_COMMENT = "⏭ Без комментария"
BTN_BACK_TO_CARD = "⬅️ К карточке"
BTN_NEXT = "➕ Следующая оплата"
BTN_CANCEL = "❌ Отмена"
BTN_BACK = "⬅️ К кассе"
BTN_MAIN = "🏠 Главное меню"
BTN_CUSTOM_PERIOD = "📅 Другой период"
BTN_OTHER_PAYMENT = "📦 Другая услуга"
BTN_RESIDENT_SUBJECT = "🏠 Жильцы / 🚗 Авто"
BTN_COMMERCIAL_SUBJECT = "🏢 Коммерческие фирмы"
BTN_ENTER_VEHICLE = "➕ Ввести данные авто"
BTN_SEARCH_AGAIN = "🔎 Искать ещё"
BTN_SKIP_APARTMENT = "➡️ Пропустить квартиру"

DEFAULT_CASHBOX_CODE = "O"
DEFAULT_SOURCE_TEXT = "Прямая наличная оплата через Telegram"


def kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def month_add(year: int, month: int, delta: int) -> tuple[int, int]:
    month += delta
    while month < 1:
        year -= 1
        month += 12
    while month > 12:
        year += 1
        month -= 12
    return year, month


def default_period() -> str:
    today = date.today()
    delta = 0 if today.day <= 15 else 1
    y, m = month_add(today.year, today.month, delta)
    return f"{y:04d}-{m:02d}"


def period_choices(center: str) -> list[str]:
    y, m = map(int, center.split('-'))
    return [f"{yy:04d}-{mm:02d}" for yy, mm in (month_add(y, m, d) for d in (-2, -1, 0, 1))]


def period_storage(value: str) -> str:
    raw = value.strip()
    m = re.fullmatch(r"(\d{2})-(\d{4})", raw)
    if m:
        return f"{m.group(2)}-{m.group(1)}"
    return core.normalize_period(raw, required=True)


def menu_kb() -> ReplyKeyboardMarkup:
    return kb([
        [BTN_RESIDENT_SUBJECT],
        [BTN_COMMERCIAL_SUBJECT],
        [BTN_OTHER_PAYMENT],
        [BTN_LAST_RECEIPTS, BTN_SUMMARY],
        [BTN_SETTINGS],
        [BTN_BACK, BTN_MAIN],
    ])


def type_kb() -> ReplyKeyboardMarkup:
    return kb([[BTN_NIGHT, BTN_DAY], [BTN_MISC], [BTN_BACK, BTN_MAIN]])


def misc_kb() -> ReplyKeyboardMarkup:
    return kb([[BTN_ACTUAL], [BTN_REMOTES, BTN_PHONE], [BTN_COMMON], [BTN_PARKING], [BTN_COMMERCIAL], [BTN_BACK, BTN_MAIN]])


def payer_kb(items: list[dict]) -> ReplyKeyboardMarkup:
    rows = [[f"{i+1}. {item['label']}"] for i, item in enumerate(items)]
    rows.append([BTN_BACK, BTN_MAIN])
    return kb(rows)


def card_kb(draft: dict | None = None) -> ReplyKeyboardMarkup:
    amount = (draft or {}).get('amount')
    try:
        valid = float(amount) > 0
    except Exception:
        valid = False
    if valid:
        return kb([[BTN_ACCEPT], [BTN_EDIT], [BTN_CANCEL]])
    return kb([[BTN_EDIT_AMOUNT], [BTN_EDIT], [BTN_CANCEL]])


def edit_kb() -> ReplyKeyboardMarkup:
    return kb([[BTN_EDIT_PERIOD, BTN_EDIT_AMOUNT], [BTN_EDIT_COMMENT], [BTN_BACK_TO_CARD, BTN_CANCEL]])


def periods_kb(current: str) -> ReplyKeyboardMarkup:
    p = [period_display(x) for x in period_choices(current)]
    return kb([[p[0], p[1]], [p[2], p[3]], [BTN_CUSTOM_PERIOD], [BTN_BACK_TO_CARD, BTN_CANCEL]])


def service_options(group: str, period: str) -> list[dict]:
    # For non-parking groups keep the catalog grouped, but do not dump the whole catalog.
    words = {
        'remote': ('пульт', 'remote'),
        'phone': ('телефон', 'phone', 'access'),
        'common': ('благо', 'ремонт', 'оборуд', 'сбор', 'збір'),
        'parking': ('паркомест', 'аренд', 'оренд', 'гост'),
        'commercial': ('коммер', 'commercial', 'contract', 'догов'),
        'actual': ('актуаль',),
    }.get(group, (group,))
    out = []
    for opt in core.service_options(period):
        text = ' '.join(str(opt.get(k) or '') for k in ('service_code','service_item_code','service_name','service_item_name','description','comment')).lower()
        if 'test' in text or 'тест' in text:
            continue
        if any(w in text for w in words):
            out.append(opt)
    return out


def service_kb(options: list[dict]) -> ReplyKeyboardMarkup:
    rows = []
    for i, opt in enumerate(options[:12], 1):
        label = core.service_label(opt)
        rows.append([f"{i}. {label[:55]}"])
    rows.append([BTN_BACK, BTN_MAIN])
    return kb(rows)


def parse_amount(value: str) -> float | None:
    try:
        return core.parse_amount(value)
    except Exception:
        return None


def summary_text() -> str:
    try:
        data = core.reconciliation_summary()
        return "\n".join([
            "📊 Сводка кассы",
            "",
            f"Уведомления: {data.get('resident_notices', 0)}",
            f"Бумажные записи: {data.get('paper_notes', 0)}",
            f"Неразнесённая наличка: {data.get('unallocated_cash', 0)}",
            f"Открытые сверки: {data.get('open_cases', 0)}",
        ])
    except Exception as exc:
        return f"⚠ Не удалось получить сводку: {exc}"


def last_receipts_text(limit: int = 10) -> str:
    con = core.get_conn()
    try:
        rows = con.execute("SELECT * FROM cashier_receipts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        if not rows:
            return "📜 Последние чеки\n\nЗаписей нет."
        lines = ["📜 Последние чеки", ""]
        for r in rows:
            lines.append(
                f"#{r['id']} {r['receipt_number']} | кв.{r['apartment_number']} | "
                f"{period_display(r['period_code'])} | {float(r['amount'] or 0):.2f} UAH"
            )
        return '\n'.join(lines)
    finally:
        con.close()


def draft_from_payer(payer: dict, group: str, service: dict) -> dict:
    if payer.get('apartment'):
        defaults = proposed_defaults(payer, service, default_period())
    else:
        amount = service.get('amount_default')
        if amount in (None, '', 0, 0.0):
            for key in ('price', 'amount', 'tariff_amount'):
                if service.get(key) not in (None, '', 0, 0.0):
                    amount = service.get(key)
                    break
        defaults = {
            'period_code': default_period(),
            'latest_paid_period': None,
            'amount': float(amount or 0),
            'charge_id': None,
        }
    return {
        'cashbox_code': DEFAULT_CASHBOX_CODE,
        'service_group': group,
        'service': service,
        'payer': payer,
        'period_code': defaults['period_code'],
        'latest_paid_period': defaults['latest_paid_period'],
        'amount': defaults['amount'],
        'charge_id': defaults['charge_id'],
        'comment': '',
    }


async def show_cashier_v2(update: Update, user_states: dict[int, Any], user_id: int) -> None:
    user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'subject_group'}
    await update.message.reply_text(
        "💰 Касса v0.4.4\n\nВыберите группу субъекта расчётов.",
        reply_markup=menu_kb(),
    )


async def start_cash(update: Update, user_states: dict[int, Any], user_id: int) -> None:
    await show_cashier_v2(update, user_states, user_id)


async def ask_payer(update: Update, user_states: dict[int, Any], user_id: int, group: str, service: dict | None = None) -> None:
    user_states[user_id] = {
        'mode': 'cashier_v2', 'screen': 'payer_query', 'service_group': group, 'service': service
    }
    await update.message.reply_text(
        "🔍 Найдите плательщика\n\nВведите номер квартиры или несколько цифр госномера автомобиля.\n\nНапример: 98 или 3804.",
        reply_markup=kb([[BTN_BACK, BTN_MAIN]]),
    )


async def show_card(update: Update, user_states: dict[int, Any], user_id: int, draft: dict) -> None:
    payer = draft.get('payer') or {}
    subject_mode = 'commercial' if payer.get('kind') == 'commercial' or payer.get('commercial_unit_id') else 'resident'
    user_states[user_id] = {
        'mode': 'cashier_v2',
        'screen': 'card',
        'draft': draft,
        'subject_mode': subject_mode,
    }
    text = payment_card(draft)
    try:
        valid_amount = float(draft.get('amount')) > 0
    except Exception:
        valid_amount = False
    if not valid_amount:
        text += "\n\n⚠ Сумма не определена. Сохранение запрещено — сначала укажите сумму."
    await update.message.reply_text(text, reply_markup=card_kb(draft))


async def choose_service_screen(update: Update, user_states: dict[int, Any], user_id: int, group: str) -> None:
    options = service_options(group, default_period())
    if not options:
        await update.message.reply_text("⚠ В этой группе услуги не найдены.", reply_markup=misc_kb())
        return
    user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'service_select', 'service_group': group, 'service_options': options[:12]}
    await update.message.reply_text("Выберите услугу:", reply_markup=service_kb(options))


async def prepare_parking_card(update: Update, user_states: dict[int, Any], user_id: int, payer: dict) -> None:
    group = group_from_payer(payer)
    if group not in {'night', 'day'}:
        user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'cash_type_after_payer', 'payer': payer}
        await update.message.reply_text(
            "Плательщик найден, но режим парковки не определён. Выберите только в этом случае:",
            reply_markup=type_kb(),
        )
        return
    service = choose_service(group, default_period())
    if not service:
        await update.message.reply_text(
            f"⚠ Для режима {group.title()} не найдена активная услуга в справочнике.",
            reply_markup=menu_kb(),
        )
        return
    draft = draft_from_payer(payer, group, service)
    await show_card(update, user_states, user_id, draft)


async def handle_cashier_v2_text(update: Update, context: ContextTypes.DEFAULT_TYPE, user_states: dict[int, Any], user_id: int) -> bool:
    text = (update.message.text or '').strip()
    state = user_states.get(user_id, {})

    if state.get('mode') != 'cashier_v2' and text not in {BTN_PAYMENTS, BTN_CASHIER_V2, '💰 Касса'}:
        return False

    if text in {BTN_PAYMENTS, BTN_CASHIER_V2, '💰 Касса'}:
        await show_cashier_v2(update, user_states, user_id); return True
    if text == BTN_MAIN:
        user_states[user_id] = {}; await update.message.reply_text(BTN_MAIN); return True
    if text in {BTN_BACK, BTN_CANCEL}:
        await show_cashier_v2(update, user_states, user_id); return True
    if text == BTN_CASH:
        await start_cash(update, user_states, user_id); return True
    if text == BTN_OTHER_PAYMENT:
        user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'misc', 'payer': state.get('payer')}
        await update.message.reply_text("📦 Другая услуга\n\nВыберите группу:", reply_markup=misc_kb()); return True
    if text == BTN_NEXT:
        subject_mode = state.get('subject_mode')
        if not subject_mode:
            draft = state.get('draft') or {}
            payer = draft.get('payer') or {}
            subject_mode = 'commercial' if payer.get('kind') == 'commercial' or payer.get('commercial_unit_id') else 'resident'

        if subject_mode == 'commercial':
            user_states[user_id] = {
                'mode': 'cashier_v2',
                'screen': 'commercial_query',
                'subject_mode': 'commercial',
            }
            await update.message.reply_text(
                "🏢 Коммерческие фирмы\n\nВведите часть названия, номер помещения или номер договора.",
                reply_markup=kb([[BTN_BACK, BTN_MAIN]]),
            )
            return True

        user_states[user_id] = {
            'mode': 'cashier_v2',
            'screen': 'payer_query_first',
            'subject_mode': 'resident',
        }
        await update.message.reply_text(
            "🔍 Жильцы / Авто\n\nВведите номер квартиры или несколько цифр госномера автомобиля.",
            reply_markup=kb([[BTN_BACK, BTN_MAIN]]),
        )
        return True
    if text == BTN_SUMMARY:
        await update.message.reply_text(summary_text(), reply_markup=menu_kb()); return True
    if text == BTN_LAST_RECEIPTS:
        await update.message.reply_text(last_receipts_text(), reply_markup=menu_kb()); return True
    if text == BTN_SETTINGS:
        await update.message.reply_text("⚙️ Настройки кассы\n\nНастройка актуального сбора сохранена для следующего шага.", reply_markup=menu_kb()); return True

    if text == BTN_RESIDENT_SUBJECT:
        user_states[user_id] = {'mode':'cashier_v2','screen':'payer_query_first','subject_mode':'resident'}
        await update.message.reply_text(
            "🔍 Жильцы / Авто\n\nВведите номер квартиры или несколько цифр госномера автомобиля.",
            reply_markup=kb([[BTN_BACK, BTN_MAIN]]),
        )
        return True

    if text == BTN_COMMERCIAL_SUBJECT:
        user_states[user_id] = {'mode':'cashier_v2','screen':'commercial_query','subject_mode':'commercial'}
        await update.message.reply_text(
            "🏢 Коммерческие фирмы\n\nВведите часть названия, номер помещения или номер договора.",
            reply_markup=kb([[BTN_BACK, BTN_MAIN]]),
        )
        return True

    if state.get('screen') == 'commercial_query':
        items = search_commercial_subjects(text)
        if not items:
            await update.message.reply_text("⚠ Коммерческая фирма не найдена. Попробуйте ещё раз.")
            return True
        if len(items) == 1:
            payer = items[0]
            service = payer.get('commercial_service')
            draft = draft_from_payer(payer, 'commercial', service)
            draft['amount'] = float(payer.get('expected_amount') or 0)
            await show_card(update, user_states, user_id, draft)
            return True
        user_states[user_id] = {'mode':'cashier_v2','screen':'commercial_select','payer_options':items,'subject_mode':'commercial'}
        await update.message.reply_text("Найдено несколько фирм. Выберите:", reply_markup=payer_kb(items))
        return True

    if state.get('screen') == 'commercial_select':
        try:
            idx = int(text.split('.',1)[0]) - 1
            payer = state['payer_options'][idx]
        except Exception:
            await update.message.reply_text("Выберите фирму кнопкой.", reply_markup=payer_kb(state.get('payer_options') or []))
            return True
        service = payer.get('commercial_service')
        draft = draft_from_payer(payer, 'commercial', service)
        draft['amount'] = float(payer.get('expected_amount') or 0)
        await show_card(update, user_states, user_id, draft)
        return True

    if state.get('screen') == 'vehicle_not_found':
        if text == BTN_SEARCH_AGAIN:
            user_states[user_id] = {'mode':'cashier_v2','screen':'payer_query_first','subject_mode':'resident'}
            await update.message.reply_text(
                "🔍 Жильцы / Авто\n\nВведите номер квартиры или несколько цифр госномера автомобиля.",
                reply_markup=kb([[BTN_BACK, BTN_MAIN]]),
            )
            return True
        if text == BTN_ENTER_VEHICLE:
            fragment = str(state.get('vehicle_fragment') or '').strip()
            user_states[user_id] = {
                'mode':'cashier_v2',
                'screen':'cashier_vehicle_apartment',
                'vehicle_fragment': fragment,
                'subject_mode':'resident',
            }
            await update.message.reply_text(
                f"🚗 Ввод данных авто\n\nНомер/фрагмент: {fragment}\n\nВведите квартиру, если она известна, или нажмите «Пропустить квартиру».",
                reply_markup=kb([[BTN_SKIP_APARTMENT], [BTN_BACK, BTN_MAIN]]),
            )
            return True
        await update.message.reply_text("Выберите действие кнопкой.", reply_markup=kb([[BTN_ENTER_VEHICLE],[BTN_SEARCH_AGAIN],[BTN_BACK,BTN_MAIN]]))
        return True

    if state.get('screen') == 'cashier_vehicle_apartment':
        apartment_number = None
        apartment = None
        if text != BTN_SKIP_APARTMENT:
            matches = [x for x in search_payers(text) if x.get('kind') == 'apartment' and str(x.get('apartment_number')) == text]
            if not matches:
                await update.message.reply_text(
                    "⚠ Квартира не найдена. Введите номер ещё раз или пропустите этот шаг.",
                    reply_markup=kb([[BTN_SKIP_APARTMENT],[BTN_BACK,BTN_MAIN]]),
                )
                return True
            apartment = matches[0].get('apartment')
            apartment_number = str(matches[0].get('apartment_number') or text)
        user_states[user_id] = {
            'mode':'cashier_v2',
            'screen':'cashier_vehicle_parking',
            'vehicle_fragment': state.get('vehicle_fragment'),
            'apartment': apartment,
            'apartment_number': apartment_number,
            'subject_mode':'resident',
        }
        await update.message.reply_text("Выберите режим парковки:", reply_markup=kb([[BTN_NIGHT, BTN_DAY],[BTN_BACK,BTN_MAIN]]))
        return True

    if state.get('screen') == 'cashier_vehicle_parking':
        if text not in {BTN_NIGHT, BTN_DAY}:
            await update.message.reply_text("Выберите Night или Day.", reply_markup=kb([[BTN_NIGHT,BTN_DAY],[BTN_BACK,BTN_MAIN]]))
            return True
        parking_time = 'Night' if text == BTN_NIGHT else 'Day'
        plate = str(state.get('vehicle_fragment') or '').strip()
        apartment_number = state.get('apartment_number')
        if apartment_number:
            ok, result = create_registry_vehicle(
                apartment_number=apartment_number,
                plate=plate,
                model=None,
                parking_time=parking_time,
                operator_id=user_id,
                needs_review=True,
            )
        else:
            ok, result = create_draft_vehicle(
                plate=plate,
                model=None,
                parking_time=parking_time,
                operator_id=user_id,
            )
        if not ok:
            await update.message.reply_text(f"⚠ Не удалось создать автомобиль: {result}", reply_markup=kb([[BTN_BACK,BTN_MAIN]]))
            return True
        vehicle = {
            'id': result.get('vehicle_id'),
            'license_plate': result.get('plate') or plate,
            'license_plate_normalized': result.get('plate') or plate,
            'car_model': result.get('model'),
            'parking_time': parking_time,
            'review_status': result.get('review_status') or 'NEEDS_REVIEW',
        }
        payer = {
            'kind': 'vehicle',
            'vehicle': vehicle,
            'vehicle_id': int(result.get('vehicle_id')),
            'parking_time': parking_time,
            'apartment': state.get('apartment'),
            'apartment_id': (state.get('apartment') or {}).get('id') if state.get('apartment') else None,
            'apartment_number': apartment_number or '',
            'label': f"🚗 {vehicle['license_plate']} / кв. {apartment_number or '—'} / {parking_time}",
        }
        service = choose_service(parking_time.lower(), default_period())
        if not service:
            await update.message.reply_text(f"⚠ Для режима {parking_time} не найдена активная услуга.", reply_markup=menu_kb())
            return True
        draft = draft_from_payer(payer, parking_time.lower(), service)
        await show_card(update, user_states, user_id, draft)
        return True

    if state.get('screen') == 'payer_query_first':
        items = search_payers(text)
        if not items:
            user_states[user_id] = {
                'mode': 'cashier_v2',
                'screen': 'vehicle_not_found',
                'vehicle_fragment': text,
                'subject_mode': 'resident',
            }
            await update.message.reply_text(
                f"⚠ Автомобиль или квартира не найдены.\n\nИзвестный номер/фрагмент: {text}\n\nМожно сразу ввести данные авто.",
                reply_markup=kb([[BTN_ENTER_VEHICLE], [BTN_SEARCH_AGAIN], [BTN_BACK, BTN_MAIN]]),
            )
            return True

        # UI-019.1: when the query is an exact apartment number, collect all
        # vehicles belonging to that apartment as one explicit search result.
        # Payment is still created for one selected vehicle; grouped payment
        # will be introduced in the following UI-019 steps.
        exact_apartment = next(
            (
                item for item in items
                if item.get('kind') == 'apartment'
                and str(item.get('apartment_number') or '').strip() == text
            ),
            None,
        )

        apartment_context = None
        if exact_apartment:
            apartment_vehicles = payer_vehicle_choices(exact_apartment)
            unique_vehicles = []
            seen_vehicle_ids = set()
            for vehicle in apartment_vehicles:
                vehicle_id = vehicle.get('vehicle_id')
                key = vehicle_id if vehicle_id is not None else vehicle.get('label')
                if key in seen_vehicle_ids:
                    continue
                seen_vehicle_ids.add(key)
                unique_vehicles.append(vehicle)

            if unique_vehicles:
                items = unique_vehicles
                apartment_context = {
                    'apartment': exact_apartment,
                    'apartment_number': str(exact_apartment.get('apartment_number') or text),
                    'vehicles': unique_vehicles,
                }
        else:
            expanded = []
            for item in items:
                if item.get('kind') == 'apartment':
                    vehicle_items = payer_vehicle_choices(item)
                    expanded.extend(vehicle_items or [item])
                else:
                    expanded.append(item)

            # Deduplicate after expanding an apartment into its vehicles.
            unique = []
            seen = set()
            for item in expanded:
                key = (item.get('kind'), item.get('vehicle_id'), item.get('apartment_id'))
                if key in seen:
                    continue
                seen.add(key)
                unique.append(item)
            items = unique

        if len(items) == 1:
            await prepare_parking_card(update, user_states, user_id, items[0]); return True

        user_states[user_id] = {
            'mode': 'cashier_v2',
            'screen': 'payer_select_first',
            'payer_options': items,
            'subject_mode': 'resident',
            'apartment_context': apartment_context,
        }
        if apartment_context:
            prompt = (
                f"Квартира {apartment_context['apartment_number']}. "
                f"Найдено автомобилей: {len(items)}. Выберите автомобиль:"
            )
        else:
            prompt = "Найдено несколько вариантов. Выберите плательщика:"
        await update.message.reply_text(prompt, reply_markup=payer_kb(items)); return True

    if state.get('screen') == 'payer_select_first':
        try:
            idx = int(text.split('.',1)[0]) - 1
            payer = state['payer_options'][idx]
        except Exception:
            await update.message.reply_text("Выберите вариант кнопкой.", reply_markup=payer_kb(state.get('payer_options') or [])); return True
        await prepare_parking_card(update, user_states, user_id, payer); return True

    if state.get('screen') == 'cash_type_after_payer':
        payer = state.get('payer')
        if text == BTN_NIGHT:
            service = choose_service('night', default_period())
            if service:
                await show_card(update, user_states, user_id, draft_from_payer(payer, 'night', service)); return True
        if text == BTN_DAY:
            service = choose_service('day', default_period())
            if service:
                await show_card(update, user_states, user_id, draft_from_payer(payer, 'day', service)); return True
        if text == BTN_MISC:
            user_states[user_id] = {'mode':'cashier_v2','screen':'misc','payer':payer}
            await update.message.reply_text("Выберите группу услуги:", reply_markup=misc_kb()); return True
        await update.message.reply_text("Выберите Night, Day или Другое.", reply_markup=type_kb()); return True

    if state.get('screen') == 'cash_type':
        if text == BTN_NIGHT:
            service = choose_service('night', default_period())
            if not service:
                await update.message.reply_text("⚠ Услуга Night не найдена в справочнике.", reply_markup=type_kb()); return True
            await ask_payer(update, user_states, user_id, 'night', service); return True
        if text == BTN_DAY:
            service = choose_service('day', default_period())
            if not service:
                await update.message.reply_text("⚠ Услуга Day не найдена в справочнике.", reply_markup=type_kb()); return True
            await ask_payer(update, user_states, user_id, 'day', service); return True
        if text == BTN_MISC:
            user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'misc', 'payer': state.get('payer')}
            await update.message.reply_text("📦 Другое\n\nВыберите группу:", reply_markup=misc_kb()); return True
        await update.message.reply_text("Выберите Night, Day или Другое.", reply_markup=type_kb()); return True

    if state.get('screen') == 'misc':
        mapping = {BTN_ACTUAL:'actual', BTN_REMOTES:'remote', BTN_PHONE:'phone', BTN_COMMON:'common', BTN_PARKING:'parking', BTN_COMMERCIAL:'commercial'}
        group = mapping.get(text)
        if not group:
            await update.message.reply_text("Выберите группу кнопкой.", reply_markup=misc_kb()); return True
        
        # If payer was already found, remember it through service selection.
        payer = state.get('payer')
        await choose_service_screen(update, user_states, user_id, group)
        if payer:
            user_states[user_id]['payer'] = payer
        return True

    if state.get('screen') == 'service_select':
        try:
            idx = int(text.split('.', 1)[0]) - 1
            service = state['service_options'][idx]
        except Exception:
            await update.message.reply_text("Выберите услугу кнопкой.", reply_markup=service_kb(state.get('service_options') or [])); return True
        if state.get('payer'):
            draft = draft_from_payer(state['payer'], state.get('service_group') or 'misc', service)
            await show_card(update, user_states, user_id, draft); return True
        await ask_payer(update, user_states, user_id, state.get('service_group') or 'misc', service); return True

    if state.get('screen') == 'payer_query':
        items = search_payers(text)
        if not items:
            await update.message.reply_text("⚠ Не нашёл квартиру или автомобиль. Попробуйте ещё раз."); return True
        # For Night/Day, prefer vehicle result whose parking_time matches selected group.
        group = state.get('service_group')
        if group in {'night','day'}:
            matched = [x for x in items if x.get('kind') == 'vehicle' and group in str(x.get('parking_time') or '').lower()]
            if len(matched) == 1:
                items = matched
        if len(items) == 1:
            draft = draft_from_payer(items[0], group, state['service'])
            await show_card(update, user_states, user_id, draft); return True
        user_states[user_id] = {'mode':'cashier_v2','screen':'payer_select','payer_options':items,'service_group':group,'service':state['service']}
        await update.message.reply_text("Найдено несколько вариантов. Выберите:", reply_markup=payer_kb(items)); return True

    if state.get('screen') == 'payer_select':
        try:
            idx = int(text.split('.',1)[0]) - 1
            payer = state['payer_options'][idx]
        except Exception:
            await update.message.reply_text("Выберите вариант кнопкой.", reply_markup=payer_kb(state.get('payer_options') or [])); return True
        draft = draft_from_payer(payer, state.get('service_group') or 'misc', state['service'])
        await show_card(update, user_states, user_id, draft); return True

    if state.get('screen') == 'card':
        draft = state['draft']
        if text == BTN_ACCEPT:
            try:
                amount_value = float(draft.get('amount'))
            except Exception:
                amount_value = 0.0
            if amount_value <= 0:
                await update.message.reply_text(
                    "⚠ Нельзя сохранить оплату с нулевой суммой. Укажите сумму.",
                    reply_markup=card_kb(draft),
                ); return True
            con = core.get_conn()
            try:
                cur = con.cursor()
                result = core.create_cash_receipt(
                    cur,
                    apartment=draft['payer']['apartment'],
                    cashbox_code=draft.get('cashbox_code') or DEFAULT_CASHBOX_CODE,
                    receipt_date=core.today(),
                    period_code=draft.get('period_code'),
                    service=draft['service'],
                    amount=float(draft.get('amount') or 0),
                    source_text=draft.get('comment') or DEFAULT_SOURCE_TEXT,
                    operator_id=int(user_id),
                    auto_allocate_charge_id=draft.get('charge_id'),
                    commercial_contract_id=draft['payer'].get('commercial_contract_id'),
                    commercial_unit_id=draft['payer'].get('commercial_unit_id'),
                    commercial_contract_item_id=draft['payer'].get('commercial_contract_item_id'),
                    vehicle_id=draft['payer'].get('vehicle_id'),
                )
                con.commit()
            except Exception as exc:
                con.rollback()
                await update.message.reply_text(f"⚠ Ошибка сохранения:\n{type(exc).__name__}: {exc}", reply_markup=card_kb(draft)); return True
            finally:
                con.close()
            user_states[user_id] = {
                'mode':'cashier_v2',
                'screen':'success',
                'draft':draft,
                'result':result,
                'subject_mode': state.get('subject_mode') or (
                    'commercial' if (draft.get('payer') or {}).get('commercial_unit_id') else 'resident'
                ),
            }
            await update.message.reply_text(success_card(result, draft), reply_markup=kb([[BTN_NEXT],[BTN_BACK, BTN_MAIN]])); return True
        if text == BTN_EDIT_AMOUNT:
            user_states[user_id] = {'mode':'cashier_v2','screen':'edit_amount','draft':draft}
            await update.message.reply_text("Введите сумму больше нуля:"); return True
        if text == BTN_EDIT:
            user_states[user_id] = {'mode':'cashier_v2','screen':'edit_menu','draft':draft}
            await update.message.reply_text("Что изменить?", reply_markup=edit_kb()); return True
        await update.message.reply_text("Подтвердите или измените карточку.", reply_markup=card_kb(draft)); return True

    if state.get('screen') == 'edit_menu':
        draft = state['draft']
        if text == BTN_BACK_TO_CARD:
            await show_card(update, user_states, user_id, draft); return True
        if text == BTN_EDIT_PERIOD:
            user_states[user_id] = {'mode':'cashier_v2','screen':'edit_period','draft':draft}
            await update.message.reply_text("Выберите период:", reply_markup=periods_kb(draft.get('period_code') or default_period())); return True
        if text == BTN_EDIT_AMOUNT:
            user_states[user_id] = {'mode':'cashier_v2','screen':'edit_amount','draft':draft}
            await update.message.reply_text("Введите новую сумму:"); return True
        if text == BTN_EDIT_COMMENT:
            user_states[user_id] = {'mode':'cashier_v2','screen':'edit_comment','draft':draft}
            await update.message.reply_text("Введите комментарий или нажмите «Без комментария».", reply_markup=kb([[BTN_SKIP_COMMENT],[BTN_BACK_TO_CARD,BTN_CANCEL]])); return True
        await update.message.reply_text("Выберите поле.", reply_markup=edit_kb()); return True

    if state.get('screen') == 'edit_period':
        draft = state['draft']
        if text == BTN_BACK_TO_CARD:
            await show_card(update, user_states, user_id, draft); return True
        if text == BTN_CUSTOM_PERIOD:
            user_states[user_id] = {'mode':'cashier_v2','screen':'edit_period_manual','draft':draft}
            await update.message.reply_text("Введите период как 06-2026 или 2026-06:"); return True
        try:
            draft['period_code'] = period_storage(text)
            draft['charge_id'] = None  # changed period requires a new reviewed allocation decision
        except Exception:
            await update.message.reply_text("Не понял период.", reply_markup=periods_kb(draft.get('period_code') or default_period())); return True
        await show_card(update, user_states, user_id, draft); return True

    if state.get('screen') == 'edit_period_manual':
        draft = state['draft']
        try:
            draft['period_code'] = period_storage(text); draft['charge_id'] = None
        except Exception:
            await update.message.reply_text("Не понял период. Пример: 06-2026."); return True
        await show_card(update, user_states, user_id, draft); return True

    if state.get('screen') == 'edit_amount':
        draft = state['draft']
        amount = parse_amount(text)
        if amount is None:
            await update.message.reply_text("Введите сумму числом."); return True
        old_amount = draft.get('amount')
        draft['amount'] = amount
        if draft.get('charge_id') and amount != float(old_amount or 0):
            draft['charge_id'] = None
        await show_card(update, user_states, user_id, draft); return True

    if state.get('screen') == 'edit_comment':
        draft = state['draft']
        draft['comment'] = '' if text == BTN_SKIP_COMMENT else text
        await show_card(update, user_states, user_id, draft); return True

    return False
