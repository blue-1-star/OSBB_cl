#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import sys
from uuid import uuid4
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
from query_lib.queries import vehicles_by_apartment, log_verification_task, last_parking_pattern

BTN_PAYMENTS = "💳 Платежи"
BTN_CASHIER_V2 = "💰 Касса v2"
BTN_CASH = "💵 Наличные"
BTN_SUMMARY = "📊 Сводка кассы"
BTN_LAST_RECEIPTS = "📜 Последние чеки"
BTN_SETTINGS = "⚙️ Настройки кассы"
BTN_VEHICLES_BY_APARTMENT = "🚗 Авто квартиры"

BTN_NIGHT = "🌙 Night"
BTN_DAY = "☀️ Day"
BTN_PARKING_UNSPECIFIED = "🅿️ Режим не определён"
BTN_UNKNOWN_MODE = "❓ Не знаю (не гадать)"
BTN_MISC = "📦 Другое"
BTN_ACTUAL = "📌 Актуальный сбор"
BTN_REMOTES = "🔑 Пульты"
BTN_PHONE = "📞 Телефонный доступ"
BTN_COMMON = "🧰 Общие сборы"
BTN_PARKING = "🅿️ Паркоместа"
BTN_COMMERCIAL = "🏢 Коммерческие"

BTN_ACCEPT = "✅ Принять как есть"
BTN_ACCEPT_BANK = "💳 Провести как банк"
BTN_FLAG_AFTER_SUCCESS = "❗ Пометить проблему"

CONCERNS_FIELD_OPTIONS = [
    ("vehicle_plate", "🔢 Номер авто"),
    ("parking_mode", "🅿️ Режим парковки"),
    ("apartment_number", "🏠 Квартира"),
    ("full_name", "👤 ФИО"),
    ("phone", "📞 Телефон"),
    ("amount", "💰 Сумма"),
    ("other", "❓ Другое"),
]
CONCERNS_FIELD_LABELS = dict(CONCERNS_FIELD_OPTIONS)
# Для большинства категорий "какого поля касается" и так очевидно из
# самого типа проблемы — спрашивать отдельно излишне ("масло масляное").
# Спрашиваем только там, где реально неоднозначно (VEHICLE_UNLINKED —
# может быть и авто, и квартира, и ФИО).
DEFAULT_CONCERNS_FIELD = {
    "VEHICLE_UNLINKED": None,
    "MISSING_VEHICLE": "vehicle_plate",
    "CHECK_PLATE": "vehicle_plate",
    "MISSING_PARKING_MODE": "parking_mode",
    "AMOUNT_MISMATCH": "amount",
    "OTHER": "other",
}
BTN_EDIT = "✏️ Изменить"
BTN_FLAG_FOR_REVIEW = "❗ На проверку"

ISSUE_TYPE_OPTIONS = [
    ("VEHICLE_UNLINKED", "🚗 Авто/квартира не та"),
    ("MISSING_VEHICLE", "🆕 Авто нет в базе (по ведомости есть)"),
    ("CHECK_PLATE", "🔢 Номер неполный/некорректный"),
    ("MISSING_PARKING_MODE", "🅿️ Режим парковки неопределён/расходится"),
    ("AMOUNT_MISMATCH", "💰 Сумма не сходится с тарифом"),
    ("OTHER", "❓ Другое"),
]
ISSUE_TYPE_LABELS = dict(ISSUE_TYPE_OPTIONS)
ISSUE_TYPE_NOTE_PROMPTS = {
    "VEHICLE_UNLINKED": "Какой номер/квартира правильные, если знаете? Или «-», чтобы пропустить:",
    "MISSING_VEHICLE": "Если знаете номер авто из ведомости — укажите его сейчас, чтобы не искать заново. Или «-», чтобы пропустить:",
    "CHECK_PLATE": "Как должен выглядеть номер целиком, если помните? Или «-», чтобы пропустить:",
    "MISSING_PARKING_MODE": "Знаете фактический режим (день/ночь)? Укажите, если да. Или «-», чтобы пропустить:",
    "AMOUNT_MISMATCH": "Какая сумма ожидалась по тарифу, если знаете? Или «-», чтобы пропустить:",
    "OTHER": "Уточните одной фразой (например: «ожидали AA1234BB, а по факту другой номер»), или отправьте «-», чтобы пропустить:",
}


def issue_type_kb() -> ReplyKeyboardMarkup:
    rows = [[label] for _, label in ISSUE_TYPE_OPTIONS]
    rows.append([BTN_BACK_TO_CARD, BTN_CANCEL])
    return kb(rows)


def _extract_apartment_number(draft: dict) -> str | None:
    apartment_raw = (draft.get('payer') or {}).get('apartment')
    if isinstance(apartment_raw, dict):
        return str(
            apartment_raw.get('apartment_number')
            or apartment_raw.get('number')
            or apartment_raw.get('id')
            or ''
        ) or None
    return str(apartment_raw) if apartment_raw is not None else None
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
DEFAULT_BANK_SOURCE_TEXT = "Банковский платёж, зарегистрирован оператором вручную"


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
        [BTN_VEHICLES_BY_APARTMENT],
        [BTN_SETTINGS],
        [BTN_BACK, BTN_MAIN],
    ])


def type_kb() -> ReplyKeyboardMarkup:
    return kb([[BTN_NIGHT, BTN_DAY], [BTN_PARKING_UNSPECIFIED], [BTN_MISC], [BTN_BACK, BTN_MAIN]])


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
        return kb([[BTN_ACCEPT], [BTN_ACCEPT_BANK], [BTN_EDIT], [BTN_FLAG_FOR_REVIEW], [BTN_CANCEL]])
    return kb([[BTN_EDIT_AMOUNT], [BTN_EDIT], [BTN_FLAG_FOR_REVIEW], [BTN_CANCEL]])


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


def _pattern_preview_text(apartment_number: str, pattern: dict) -> tuple[str, float]:
    """Текстовое превью 'как в прошлый раз' + суммарная сумма (нужна
    только чтобы card_kb() посчитала карточку валидной для приёма)."""
    lines = [f"📋 Как в {pattern['period']} (кв.{apartment_number}):", ""]
    total = 0.0
    for r in pattern['rows']:
        plate = r.get('plate') or '—'
        lines.append(f"  {plate} — {r['base_service_code']}: {float(r['amount']):.2f} грн")
        total += float(r['amount'])
        for f in r.get('carry_forward_flags') or []:
            lines.append(f"    ⚠ перенесётся вопрос: {f['description']}")
    lines.append(f"\nИтого: {total:.2f} грн")
    return "\n".join(lines), total


def _create_payments_from_pattern(
    cur, pattern_rows: list[dict], apartment: dict, period_code: str,
    operator_id: int, channel: str, cashbox_code: str | None = None,
    transaction_ref: str | None = None,
) -> list[dict]:
    """
    Создаёт по одному честному платежу на каждую строку паттерна
    (channel='cash' или 'bank'). Для банка все строки одного повтора
    получают ОДИН И ТОТ ЖЕ transaction_ref — это был один реальный
    перевод, разнесённый на несколько строк учёта, не несколько
    отдельных переводов (иначе сверка с выпиской не сойдётся).
    Возвращает список result-словарей (как create_cash_receipt/
    create_bank_payment), в том же порядке, что и pattern_rows —
    нужно для последующего переноса carry_forward_flags на новые id.
    """
    results = []
    for row in pattern_rows:
        service = {
            "service_code": row["base_service_code"],
            "service_item_code": None,
            "service_type": "GENERAL",
        }
        if channel == "bank":
            result = core.create_bank_payment(
                cur,
                apartment=apartment,
                transaction_ref=transaction_ref,
                transaction_date=core.today(),
                period_code=period_code,
                service=service,
                amount=float(row["amount"]),
                payer_text=DEFAULT_BANK_SOURCE_TEXT,
                operator_id=operator_id,
            )
        else:
            result = core.create_cash_receipt(
                cur,
                apartment=apartment,
                cashbox_code=cashbox_code or DEFAULT_CASHBOX_CODE,
                receipt_date=core.today(),
                period_code=period_code,
                service=service,
                amount=float(row["amount"]),
                source_text=DEFAULT_SOURCE_TEXT,
                operator_id=operator_id,
            )
        results.append(result)
    return results


def _solve_and_attribute(amount: float, vehicle_tariffs: list[tuple[dict, float]]):
    """
    Как _solve_vehicle_puzzle, но не просто "решено да/нет" для отображения,
    а с точной раскладкой — какому конкретно авто сколько начислить, чтобы
    можно было создать по одному честному платежу на каждое, без выдуманного
    "комбинированного" кода услуги.

    Пробует сначала более крупные подмножества авто (естественный случай —
    заплатили сразу за все), потом меньшие. Использует решение, только если
    оно единственно на своём уровне — при неоднозначности не гадает,
    возвращает None.
    """
    from itertools import combinations
    n = len(vehicle_tariffs)
    if n == 0 or not amount or amount <= 0:
        return None

    for size in range(n, 0, -1):
        candidates = []
        for combo in combinations(vehicle_tariffs, size):
            subset_sum = sum(t for _, t in combo)
            if subset_sum > 0 and amount % subset_sum == 0:
                months = int(amount // subset_sum)
                candidates.append((combo, months))
        if len(candidates) == 1:
            combo, months = candidates[0]
            return [(v, t, months) for v, t in combo]
        if len(candidates) > 1:
            return None

    return None


def _solve_vehicle_puzzle(amount: float, vehicle_tariffs: list[tuple[str, float]]):
    """Пытается объяснить сумму платежа тарифами известных авто квартиры —
    включая оплату за несколько месяцев сразу (сумма = тариф x N).
    Возвращает (solved, label). Если решений несколько (сумма совпадает
    сразу с несколькими машинами по отдельности) — не называем конкретную,
    честно показываем количество, а не гадаем."""
    from itertools import combinations
    n = len(vehicle_tariffs)
    if n == 0 or not amount or amount <= 0:
        return False, None

    single_matches = []
    for plate, tariff in vehicle_tariffs:
        if tariff and amount % tariff == 0:
            single_matches.append((plate, int(amount // tariff)))

    if len(single_matches) == 1:
        plate, months = single_matches[0]
        return True, plate if months == 1 else f"{plate} x{months}"
    if len(single_matches) > 1:
        return True, f"{n}авто"

    for size in range(2, n + 1):
        for combo in combinations(vehicle_tariffs, size):
            subset_sum = sum(t for _, t in combo)
            if subset_sum > 0 and amount % subset_sum == 0:
                return True, f"{n}авто"

    return False, None


def last_receipts_text(limit: int = 10) -> str:
    con = core.get_conn()
    try:
        cur = con.cursor()
        # ВАЖНО: строим от payments, не от cashier_receipts — у банковских
        # платежей (💳 Провести как банк) чек не создаётся вообще, и они
        # были структурно невидимы в списке, пока он отталкивался от
        # cashier_receipts. payments содержит и наличные, и банковские.
        rows = con.execute("""
            SELECT p.id, p.apartment_number, p.period_code, p.amount, p.cashbox_code,
                   direct_v.license_plate_normalized AS direct_plate
            FROM payments p
            LEFT JOIN vehicles direct_v ON direct_v.id = p.vehicle_id
            ORDER BY p.id DESC
            LIMIT ?
        """, (limit,)).fetchall()
        if not rows:
            return "📜 Последние чеки\n\nЗаписей нет."

        # Тарифы известных режимов — один раз, не на каждую строку.
        tariff_by_mode = {}
        for mode, code in (("Day", "PARKING_DAY"), ("Night", "PARKING_NIGHT")):
            tariff_by_mode[mode] = core.get_current_tariff_amount(cur, code)

        # Авто по квартирам, встретившимся в выборке — одним запросом.
        # vehicles хранит apartment_id (ссылку), а не сам номер квартиры —
        # номер берём через JOIN на apartments, как и везде в проекте.
        # Два списка: с известным тарифом (для решения пазла) и вообще
        # все — номер машины сам по себе факт, известный независимо от
        # того, задан ли у неё режим парковки (день/ночь).
        apartments_in_view = sorted({r['apartment_number'] for r in rows if r['apartment_number']})
        priced_by_apartment: dict[str, list[tuple[str, float]]] = {}
        all_plates_by_apartment: dict[str, list[str]] = {}
        if apartments_in_view:
            marks = ",".join("?" * len(apartments_in_view))
            for vr in con.execute(f"""
                SELECT a.apartment_number AS apartment_number,
                       v.license_plate_normalized AS license_plate_normalized,
                       v.parking_time AS parking_time
                FROM vehicles v
                JOIN apartments a ON a.id = v.apartment_id
                WHERE CAST(a.apartment_number AS TEXT) IN ({marks})
            """, apartments_in_view):
                apt_key = str(vr['apartment_number'])
                plate = vr['license_plate_normalized']
                all_plates_by_apartment.setdefault(apt_key, []).append(plate)
                tariff = tariff_by_mode.get(vr['parking_time'])
                if tariff:
                    priced_by_apartment.setdefault(apt_key, []).append((plate, tariff))

        # Моноширинный блок (```...```) — иначе Telegram рисует обычный
        # текст непропорциональным шрифтом, и колонки "плавают", даже
        # если дополнены пробелами до одинакового числа символов.
        lines = ["📜 Последние чеки", "```"]
        for r in rows:
            apt = r['apartment_number'] if r['apartment_number'] else '—'
            if r['direct_plate']:
                plate_display = r['direct_plate']
            else:
                priced = priced_by_apartment.get(str(apt), [])
                solved, label = _solve_vehicle_puzzle(float(r['amount'] or 0), priced)
                if solved:
                    plate_display = label
                else:
                    # Пазл не сошёлся (или режим парковки неизвестен —
                    # не с чем сравнивать), но номер машины — факт сам
                    # по себе, его прятать за прочерком неправильно.
                    all_plates = all_plates_by_apartment.get(str(apt), [])
                    if len(all_plates) == 1:
                        plate_display = all_plates[0] + '?'
                    elif len(all_plates) > 1:
                        plate_display = f"{len(all_plates)}авто?"
                    else:
                        plate_display = '—'
            channel = '🏦' if (r['cashbox_code'] or '').upper() == 'BANK' else '💵'
            lines.append(
                f"#{r['id']:<3} {channel} кв.{apt:<5.5} {plate_display:<13.13} "
                f"{r['period_code']} {float(r['amount'] or 0):>8.2f}"
            )
        lines.append("```")
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

    # Эмпирика: платёж за парковку почти всегда такой же, как в прошлый
    # раз. Если для ЭТОЙ конкретной машины есть недавняя парковочная
    # запись — подставляем её сумму вместо пересчёта по текущему
    # тарифу, и переносим любой ещё не решённый вопрос по ней же.
    apartment_number = payer.get('apartment_number')
    plate = payer.get('plate')
    if apartment_number and plate:
        pattern = last_parking_pattern(apartment_number)
        if pattern:
            for row in pattern['rows']:
                if row.get('plate') == plate:
                    draft['amount'] = float(row['amount'])
                    draft['pattern_note'] = f"как в {pattern['period']}"
                    if row.get('carry_forward_flags'):
                        draft['carry_forward_flags'] = row['carry_forward_flags']
                    break

    await show_card(update, user_states, user_id, draft)
    if draft.get('pattern_note'):
        note = f"ℹ️ Сумма и услуга — {draft['pattern_note']}."
        if draft.get('carry_forward_flags'):
            for f in draft['carry_forward_flags']:
                note += f"\n⚠ Перенесётся нерешённый вопрос: {f['description']}"
        await update.message.reply_text(note)


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
        await update.message.reply_text(last_receipts_text(), reply_markup=menu_kb(), parse_mode='Markdown'); return True
    if text == BTN_VEHICLES_BY_APARTMENT:
        user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'vehicles_by_apartment_waiting'}
        await update.message.reply_text(
            "🚗 Авто квартиры\n\nВведите номер квартиры:",
            reply_markup=kb([[BTN_BACK, BTN_MAIN]]),
        )
        return True

    if state.get('screen') == 'vehicles_by_apartment_waiting':
        apartment_number = text.strip()
        rows = vehicles_by_apartment(apartment_number)
        if not rows:
            body = f"🚗 Квартира {apartment_number}\n\nАвто не найдено."
        else:
            lines = [f"🚗 Квартира {apartment_number} — авто: {len(rows)}", ""]
            for r in rows:
                model = f" ({r['марка']})" if r['марка'] else ""
                mode = f", режим: {r['режим']}" if r['режим'] else ""
                lines.append(f"кв.{apartment_number} — {r['номер']}{model}{mode}")
            body = "\n".join(lines)
        await update.message.reply_text(body, reply_markup=menu_kb())
        user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'menu'}
        return True
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
        await update.message.reply_text("Выберите режим парковки:", reply_markup=kb([[BTN_NIGHT, BTN_DAY],[BTN_UNKNOWN_MODE],[BTN_BACK,BTN_MAIN]]))
        return True

    if state.get('screen') == 'cashier_vehicle_parking':
        if text not in {BTN_NIGHT, BTN_DAY, BTN_UNKNOWN_MODE}:
            await update.message.reply_text("Выберите Night, Day или «Не знаю».", reply_markup=kb([[BTN_NIGHT,BTN_DAY],[BTN_UNKNOWN_MODE],[BTN_BACK,BTN_MAIN]]))
            return True
        if text == BTN_UNKNOWN_MODE:
            parking_time = None  # честно неизвестно — не гадаем, не пишем Day/Night наугад
        else:
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
        if parking_time:
            service = choose_service(parking_time.lower(), default_period())
            if not service:
                await update.message.reply_text(f"⚠ Для режима {parking_time} не найдена активная услуга.", reply_markup=menu_kb())
                return True
            draft = draft_from_payer(payer, parking_time.lower(), service)
            await show_card(update, user_states, user_id, draft)
            return True

        # Режим парковки неизвестен — не гадаем ни услугу, ни сумму сами.
        # Авто в реестре уже создано с parking_time=NULL (честно). Для
        # самого платежа переиспользуем уже готовый, проверенный путь
        # ручного выбора услуги (тот же, что и для существующих авто
        # без известного режима — Night/Day/«Другое»).
        user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'cash_type_after_payer', 'payer': payer}
        await update.message.reply_text(
            "Автомобиль создан. Режим парковки не указан — выберите услугу вручную:",
            reply_markup=type_kb(),
        )
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

        # Если у квартиры несколько авто с известными тарифами — сначала
        # пробуем узнать сумму и разложить её автоматически (оплата сразу
        # за несколько авто — частый случай, вручную выбирать не нужно,
        # если раскладка однозначна). Ручной выбор — только если авто
        # не найдены как единая квартира, или раскладка неоднозначна.
        if apartment_context and len(apartment_context['vehicles']) > 1:
            apt_number = apartment_context['apartment_number']
            pattern = last_parking_pattern(apt_number)
            if pattern and len(pattern['rows']) > 1:
                preview_text, total = _pattern_preview_text(apt_number, pattern)
                draft = {
                    'payer': {'apartment': apartment_context['apartment']},
                    'amount': total,  # синтетическая сумма — только чтобы карточка считалась валидной
                    'period_code': pattern['period'],
                    'service': None,
                    'comment': '',
                    'pattern_rows': pattern['rows'],
                }
                user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'card', 'draft': draft}
                await update.message.reply_text(preview_text, reply_markup=card_kb(draft))
                return True

            user_states[user_id] = {
                'mode': 'cashier_v2',
                'screen': 'apartment_smart_amount',
                'apartment_context': apartment_context,
                'fallback_items': items,
            }
            await update.message.reply_text(
                f"Квартира {apartment_context['apartment_number']}, авто: {len(apartment_context['vehicles'])}.\n"
                f"Введите сумму — если она покрывает несколько авто сразу, распознаю сама:",
                reply_markup=kb([[BTN_BACK, BTN_MAIN]]),
            )
            return True

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

    if state.get('screen') == 'apartment_smart_amount':
        if text in {BTN_BACK, BTN_MAIN}:
            return False  # пусть общий обработчик уведёт в кассу/главное меню

        try:
            amount_value = float(text.replace(',', '.'))
        except Exception:
            await update.message.reply_text("Введите сумму числом (например 700 или 700.50):")
            return True
        if amount_value <= 0:
            await update.message.reply_text("Сумма должна быть больше нуля.")
            return True

        apartment_context = state['apartment_context']
        period = default_period()

        # Собираем (авто, тариф) только для тех, у кого известен режим.
        vehicle_tariffs = []
        for v in apartment_context['vehicles']:
            mode = v.get('parking_time')
            if mode not in {'Day', 'Night'}:
                continue
            service = choose_service('day' if mode == 'Day' else 'night', period)
            if service and service.get('amount_default'):
                vehicle_tariffs.append((v, float(service['amount_default']), mode, service))

        solved = _solve_and_attribute(amount_value, [(v, t) for v, t, _, _ in vehicle_tariffs])

        if solved is None:
            # Не разложилось однозначно — не гадаем, отдаём на ручной выбор,
            # как и раньше, до этой правки.
            items = state['fallback_items']
            user_states[user_id] = {
                'mode': 'cashier_v2',
                'screen': 'payer_select_first',
                'payer_options': items,
                'subject_mode': 'resident',
                'apartment_context': apartment_context,
            }
            await update.message.reply_text(
                f"Сумма {amount_value:.2f} не раскладывается однозначно по известным тарифам. "
                f"Выберите автомобиль вручную:",
                reply_markup=payer_kb(items),
            )
            return True

        # Однозначно решено — создаём по одному честному платежу на каждое
        # авто из решения, одной транзакцией (либо всё, либо ничего).
        lookup = {id(v): (t, mode, service) for v, t, mode, service in vehicle_tariffs}
        con = core.get_conn()
        created = []
        try:
            cur = con.cursor()
            for v, tariff, months in solved:
                _, mode, service = lookup[id(v)]
                result = core.create_cash_receipt(
                    cur,
                    apartment=v.get('apartment') or apartment_context['apartment'],
                    cashbox_code=DEFAULT_CASHBOX_CODE,
                    receipt_date=core.today(),
                    period_code=period,
                    service=service,
                    amount=tariff * months,
                    source_text=DEFAULT_SOURCE_TEXT,
                    operator_id=int(user_id),
                    vehicle_id=v.get('vehicle_id'),
                )
                created.append((v, tariff, months, result))
            con.commit()
        except Exception as exc:
            con.rollback()
            await update.message.reply_text(f"⚠ Ошибка сохранения:\n{type(exc).__name__}: {exc}")
            return True
        finally:
            con.close()

        lines = [f"✅ Оплата принята — {len(created)} авто квартиры {apartment_context['apartment_number']}:", ""]
        for v, tariff, months, result in created:
            label = v.get('label') or v.get('plate') or '—'
            months_note = f" x{months}" if months > 1 else ""
            lines.append(f"  {label}: {tariff * months:.2f} грн{months_note}")
        user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'menu'}
        await update.message.reply_text("\n".join(lines), reply_markup=menu_kb())
        return True

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
        if text == BTN_PARKING_UNSPECIFIED:
            service = choose_service('unspecified', default_period())
            if service:
                await show_card(update, user_states, user_id, draft_from_payer(payer, 'unspecified', service)); return True
            await update.message.reply_text(
                "⚠ Услуга «режим не определён» не найдена в справочнике (service_catalog). "
                "Нужно добавить PARKING_UNSPECIFIED.",
                reply_markup=type_kb(),
            ); return True
        if text == BTN_MISC:
            user_states[user_id] = {'mode':'cashier_v2','screen':'misc','payer':payer}
            await update.message.reply_text("Выберите группу услуги:", reply_markup=misc_kb()); return True
        await update.message.reply_text("Выберите Night, Day, «Режим не определён» или Другое.", reply_markup=type_kb()); return True

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
        if text == BTN_PARKING_UNSPECIFIED:
            service = choose_service('unspecified', default_period())
            if not service:
                await update.message.reply_text(
                    "⚠ Услуга «режим не определён» не найдена в справочнике (service_catalog). "
                    "Нужно добавить PARKING_UNSPECIFIED.",
                    reply_markup=type_kb(),
                ); return True
            await ask_payer(update, user_states, user_id, 'unspecified', service); return True
        if text == BTN_MISC:
            user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'misc', 'payer': state.get('payer')}
            await update.message.reply_text("📦 Другое\n\nВыберите группу:", reply_markup=misc_kb()); return True
        await update.message.reply_text("Выберите Night, Day, «Режим не определён» или Другое.", reply_markup=type_kb()); return True

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
            apartment_number = (draft.get('payer') or {}).get('apartment') if isinstance(draft.get('payer'), dict) else None
            if isinstance(apartment_number, dict):
                apartment_number = apartment_number.get('apartment_number')

            if draft.get('pattern_rows'):
                con = core.get_conn()
                try:
                    cur = con.cursor()
                    results = _create_payments_from_pattern(
                        cur, draft['pattern_rows'], draft['payer']['apartment'],
                        draft.get('period_code'), int(user_id), channel='cash',
                        cashbox_code=draft.get('cashbox_code') or DEFAULT_CASHBOX_CODE,
                    )
                    con.commit()
                except Exception as exc:
                    con.rollback()
                    await update.message.reply_text(f"⚠ Ошибка сохранения:\n{type(exc).__name__}: {exc}", reply_markup=card_kb(draft)); return True
                finally:
                    con.close()
                for row, result in zip(draft['pattern_rows'], results):
                    for f in row.get('carry_forward_flags') or []:
                        log_verification_task(
                            apartment_number=apartment_number, issue_type=f['issue_type'],
                            description=f"[перенесено с прошлого периода] {f['description']}",
                            related_payment_id=result.get('payment_id'), related_receipt_id=result.get('receipt_id'),
                            raised_by=str(user_id), assigned_role=None, concerns_field=f.get('concerns_field'),
                        )
                total = sum(float(r['amount']) for r in draft['pattern_rows'])
                user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'menu'}
                await update.message.reply_text(
                    f"✅ Оплата принята — {len(results)} авто, {total:.2f} грн всего.", reply_markup=menu_kb(),
                )
                return True

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
            for f in draft.get('carry_forward_flags') or []:
                log_verification_task(
                    apartment_number=apartment_number, issue_type=f['issue_type'],
                    description=f"[перенесено с прошлого периода] {f['description']}",
                    related_payment_id=result.get('payment_id'), related_receipt_id=result.get('receipt_id'),
                    raised_by=str(user_id), assigned_role=None, concerns_field=f.get('concerns_field'),
                )
            user_states[user_id] = {
                'mode':'cashier_v2',
                'screen':'success',
                'draft':draft,
                'result':result,
                'subject_mode': state.get('subject_mode') or (
                    'commercial' if (draft.get('payer') or {}).get('commercial_unit_id') else 'resident'
                ),
            }
            await update.message.reply_text(success_card(result, draft), reply_markup=kb([[BTN_NEXT],[BTN_FLAG_AFTER_SUCCESS],[BTN_BACK, BTN_MAIN]])); return True
        if text == BTN_ACCEPT_BANK:
            apartment_number = (draft.get('payer') or {}).get('apartment') if isinstance(draft.get('payer'), dict) else None
            if isinstance(apartment_number, dict):
                apartment_number = apartment_number.get('apartment_number')

            if draft.get('pattern_rows'):
                temp_ref = f"TMP-{core.today()}-{uuid4().hex[:6].upper()}"
                con = core.get_conn()
                try:
                    cur = con.cursor()
                    results = _create_payments_from_pattern(
                        cur, draft['pattern_rows'], draft['payer']['apartment'],
                        draft.get('period_code'), int(user_id), channel='bank',
                        transaction_ref=temp_ref,
                    )
                    con.commit()
                except Exception as exc:
                    con.rollback()
                    await update.message.reply_text(f"⚠ Ошибка сохранения:\n{type(exc).__name__}: {exc}", reply_markup=card_kb(draft)); return True
                finally:
                    con.close()
                for row, result in zip(draft['pattern_rows'], results):
                    for f in row.get('carry_forward_flags') or []:
                        log_verification_task(
                            apartment_number=apartment_number, issue_type=f['issue_type'],
                            description=f"[перенесено с прошлого периода] {f['description']}",
                            related_payment_id=result.get('payment_id'), related_receipt_id=result.get('receipt_id'),
                            raised_by=str(user_id), assigned_role=None, concerns_field=f.get('concerns_field'),
                        )
                total = sum(float(r['amount']) for r in draft['pattern_rows'])
                user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'menu'}
                await update.message.reply_text(
                    f"💳 Банковский платёж принят — {len(results)} авто, {total:.2f} грн всего (черновой, один TMP-номер на все строки).",
                    reply_markup=menu_kb(),
                )
                return True

            try:
                amount_value = float(draft.get('amount'))
            except Exception:
                amount_value = 0.0
            if amount_value <= 0:
                await update.message.reply_text(
                    "⚠ Нельзя сохранить оплату с нулевой суммой. Укажите сумму.",
                    reply_markup=card_kb(draft),
                ); return True
            # Минимальная версия: временный идентификатор выписки —
            # деталь (реальный transaction_ref/дата операции) добавляется
            # позже отдельным шагом сверки, не блокирует сам приём.
            temp_ref = f"TMP-{core.today()}-{uuid4().hex[:6].upper()}"
            con = core.get_conn()
            try:
                cur = con.cursor()
                result = core.create_bank_payment(
                    cur,
                    apartment=draft['payer']['apartment'],
                    transaction_ref=temp_ref,
                    transaction_date=core.today(),
                    period_code=draft.get('period_code'),
                    service=draft['service'],
                    amount=amount_value,
                    payer_text=draft.get('comment') or DEFAULT_BANK_SOURCE_TEXT,
                    operator_id=int(user_id),
                    auto_allocate_charge_id=draft.get('charge_id'),
                    commercial_contract_id=draft['payer'].get('commercial_contract_id'),
                    commercial_unit_id=draft['payer'].get('commercial_unit_id'),
                    commercial_contract_item_id=draft['payer'].get('commercial_contract_item_id'),
                )
                con.commit()
            except Exception as exc:
                con.rollback()
                await update.message.reply_text(f"⚠ Ошибка сохранения:\n{type(exc).__name__}: {exc}", reply_markup=card_kb(draft)); return True
            finally:
                con.close()
            for f in draft.get('carry_forward_flags') or []:
                log_verification_task(
                    apartment_number=apartment_number, issue_type=f['issue_type'],
                    description=f"[перенесено с прошлого периода] {f['description']}",
                    related_payment_id=result.get('payment_id'), related_receipt_id=result.get('receipt_id'),
                    raised_by=str(user_id), assigned_role=None, concerns_field=f.get('concerns_field'),
                )
            user_states[user_id] = {
                'mode': 'cashier_v2',
                'screen': 'success',
                'draft': draft,
                'result': result,
                'subject_mode': state.get('subject_mode') or (
                    'commercial' if (draft.get('payer') or {}).get('commercial_unit_id') else 'resident'
                ),
            }
            await update.message.reply_text(
                f"💳 Банковский платёж принят (черновой, TMP-номер)\n\n{success_card(result, draft)}\n\n"
                f"⚠ Реквизиты выписки не указаны — потребуется сверка позже.",
                reply_markup=kb([[BTN_NEXT], [BTN_FLAG_AFTER_SUCCESS], [BTN_BACK, BTN_MAIN]]),
            ); return True
        if text == BTN_EDIT_AMOUNT:
            user_states[user_id] = {'mode':'cashier_v2','screen':'edit_amount','draft':draft}
            await update.message.reply_text("Введите сумму больше нуля:"); return True
        if text == BTN_EDIT:
            user_states[user_id] = {'mode':'cashier_v2','screen':'edit_menu','draft':draft}
            await update.message.reply_text("Что изменить?", reply_markup=edit_kb()); return True
        if text == BTN_FLAG_FOR_REVIEW:
            user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'flag_issue_type', 'draft': draft}
            await update.message.reply_text("Что именно требует проверки?", reply_markup=issue_type_kb())
            return True
        await update.message.reply_text("Подтвердите или измените карточку.", reply_markup=card_kb(draft)); return True

    if state.get('screen') == 'flag_issue_type':
        draft = state['draft']
        if text == BTN_BACK_TO_CARD:
            await show_card(update, user_states, user_id, draft); return True
        if text == BTN_CANCEL:
            await show_card(update, user_states, user_id, draft); return True
        issue_type = None
        for code, label in ISSUE_TYPE_OPTIONS:
            if text == label:
                issue_type = code
                break
        if issue_type is None:
            await update.message.reply_text("Выберите один из вариантов на клавиатуре.", reply_markup=issue_type_kb())
            return True
        user_states[user_id] = {
            'mode': 'cashier_v2', 'screen': 'flag_issue_note',
            'draft': draft, 'issue_type': issue_type,
        }
        note_prompt = ISSUE_TYPE_NOTE_PROMPTS.get(issue_type, ISSUE_TYPE_NOTE_PROMPTS['OTHER'])
        await update.message.reply_text(
            note_prompt,
            reply_markup=kb([[BTN_BACK_TO_CARD, BTN_CANCEL]]),
        )
        return True

    if state.get('screen') == 'flag_issue_note':
        draft = state['draft']
        issue_type = state['issue_type']
        if text == BTN_BACK_TO_CARD:
            await show_card(update, user_states, user_id, draft); return True
        if text == BTN_CANCEL:
            await show_card(update, user_states, user_id, draft); return True
        note = "" if text.strip() == "-" else text.strip()

        apartment_number = _extract_apartment_number(draft)
        try:
            amount_value = float(draft.get('amount') or 0)
        except Exception:
            amount_value = 0.0
        description = (
            f"[{ISSUE_TYPE_LABELS[issue_type]}] сумма {amount_value:.2f}, "
            f"период {draft.get('period_code')}"
            + (f" — {note}" if note else "")
        )
        task_id = log_verification_task(
            apartment_number=apartment_number,
            issue_type=issue_type,
            description=description,
            raised_by=str(user_id),
            assigned_role=None,  # виден админу; при необходимости уточним роль позже
        )
        await update.message.reply_text(
            f"❗ Записано в журнал согласования (#{task_id}). Ввод платежа продолжается.",
        )
        await show_card(update, user_states, user_id, draft)
        return True

    if state.get('screen') == 'success':
        result = state.get('result') or {}
        draft = state.get('draft') or {}
        if text == BTN_FLAG_AFTER_SUCCESS:
            user_states[user_id] = {
                'mode': 'cashier_v2', 'screen': 'success_flag_type',
                'result': result, 'draft': draft,
            }
            await update.message.reply_text("Что именно требует проверки?", reply_markup=issue_type_kb())
            return True
        return False  # BTN_NEXT/BTN_BACK/BTN_MAIN — обрабатываются выше по стеку

    if state.get('screen') == 'success_flag_type':
        result = state['result']
        draft = state['draft']
        if text in {BTN_BACK, BTN_CANCEL}:
            await update.message.reply_text(success_card(result, draft), reply_markup=kb([[BTN_NEXT],[BTN_FLAG_AFTER_SUCCESS],[BTN_BACK, BTN_MAIN]]))
            user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'success', 'result': result, 'draft': draft}
            return True
        issue_type = None
        for code, label in ISSUE_TYPE_OPTIONS:
            if text == label:
                issue_type = code
                break
        if issue_type is None:
            await update.message.reply_text("Выберите один из вариантов на клавиатуре.", reply_markup=issue_type_kb())
            return True

        default_field = DEFAULT_CONCERNS_FIELD.get(issue_type)
        if default_field is not None:
            # Поле и так очевидно из типа проблемы — не переспрашиваем,
            # сразу к уточнению.
            user_states[user_id] = {
                'mode': 'cashier_v2', 'screen': 'success_flag_note',
                'result': result, 'draft': draft,
                'issue_type': issue_type, 'concerns_field': default_field,
            }
            note_prompt = ISSUE_TYPE_NOTE_PROMPTS.get(issue_type, ISSUE_TYPE_NOTE_PROMPTS['OTHER'])
            await update.message.reply_text(note_prompt, reply_markup=kb([[BTN_BACK, BTN_CANCEL]]))
            return True

        user_states[user_id] = {
            'mode': 'cashier_v2', 'screen': 'success_flag_concerns',
            'result': result, 'draft': draft, 'issue_type': issue_type,
        }
        rows = [[label] for _, label in CONCERNS_FIELD_OPTIONS]
        rows.append([BTN_BACK, BTN_CANCEL])
        await update.message.reply_text("Какого поля это касается?", reply_markup=kb(rows))
        return True

    if state.get('screen') == 'success_flag_concerns':
        result = state['result']
        draft = state['draft']
        if text in {BTN_BACK, BTN_CANCEL}:
            await update.message.reply_text("Что именно требует проверки?", reply_markup=issue_type_kb())
            user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'success_flag_type', 'result': result, 'draft': draft}
            return True
        concerns_field = None
        for code, label in CONCERNS_FIELD_OPTIONS:
            if text == label:
                concerns_field = code
                break
        if concerns_field is None:
            rows = [[label] for _, label in CONCERNS_FIELD_OPTIONS]
            rows.append([BTN_BACK, BTN_CANCEL])
            await update.message.reply_text("Выберите один из вариантов на клавиатуре.", reply_markup=kb(rows))
            return True
        user_states[user_id] = {
            'mode': 'cashier_v2', 'screen': 'success_flag_note',
            'result': result, 'draft': draft,
            'issue_type': state['issue_type'], 'concerns_field': concerns_field,
        }
        note_prompt = ISSUE_TYPE_NOTE_PROMPTS.get(state['issue_type'], ISSUE_TYPE_NOTE_PROMPTS['OTHER'])
        await update.message.reply_text(note_prompt, reply_markup=kb([[BTN_BACK, BTN_CANCEL]]))
        return True

    if state.get('screen') == 'success_flag_note':
        result = state['result']
        draft = state['draft']
        issue_type = state['issue_type']
        concerns_field = state['concerns_field']
        if text in {BTN_BACK, BTN_CANCEL}:
            rows = [[label] for _, label in CONCERNS_FIELD_OPTIONS]
            rows.append([BTN_BACK, BTN_CANCEL])
            await update.message.reply_text("Какого поля это касается?", reply_markup=kb(rows))
            user_states[user_id] = {
                'mode': 'cashier_v2', 'screen': 'success_flag_concerns',
                'result': result, 'draft': draft, 'issue_type': issue_type,
            }
            return True

        note = "" if text.strip() == "-" else text.strip()
        apartment_number = _extract_apartment_number(draft)
        description = (
            f"[{ISSUE_TYPE_LABELS[issue_type]} / {CONCERNS_FIELD_LABELS[concerns_field]}]"
            + (f" — {note}" if note else "")
        )
        task_id = log_verification_task(
            apartment_number=apartment_number,
            issue_type=issue_type,
            description=description,
            related_payment_id=result.get('payment_id'),
            related_receipt_id=result.get('receipt_id'),
            raised_by=str(user_id),
            assigned_role=None,
            concerns_field=concerns_field,
        )
        await update.message.reply_text(f"❗ Записано в журнал согласования (#{task_id}), привязано к платежу #{result.get('payment_id')}.")
        await update.message.reply_text(success_card(result, draft), reply_markup=kb([[BTN_NEXT],[BTN_FLAG_AFTER_SUCCESS],[BTN_BACK, BTN_MAIN]]))
        user_states[user_id] = {'mode': 'cashier_v2', 'screen': 'success', 'result': result, 'draft': draft}
        return True

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