#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "Data" / "db" / "osbb_test.db"

BTN_PAYMENTS = "💳 Платежи"
BTN_CASHIER_EDITOR = "💰 Кассовый редактор"
BTN_NEW_PAYMENT = "➕ Внести оплату"
BTN_LAST_PAYMENTS = "📜 Последние оплаты"
BTN_SEARCH_APARTMENT = "🔎 По квартире"
BTN_CONFIRM_SAVE = "✅ Сохранить оплату"
BTN_CANCEL = "❌ Отмена"
BTN_BACK = "⬅ Назад"
BTN_MAIN_MENU = "🏠 Главное меню"

DEFAULT_CURRENCY = "UAH"
DEFAULT_SOURCE = "cashier_editor_v0_1"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(DEFAULT_DB))
    con.row_factory = sqlite3.Row
    return con


def keyboard(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}


def cashier_menu_keyboard() -> ReplyKeyboardMarkup:
    return keyboard([
        [BTN_NEW_PAYMENT],
        [BTN_LAST_PAYMENTS, BTN_SEARCH_APARTMENT],
        [BTN_BACK, BTN_MAIN_MENU],
    ])


def payment_confirm_keyboard() -> ReplyKeyboardMarkup:
    return keyboard([[BTN_CONFIRM_SAVE], [BTN_CANCEL]])


def back_keyboard() -> ReplyKeyboardMarkup:
    return keyboard([[BTN_BACK, BTN_MAIN_MENU]])


def now_ref() -> str:
    return "MANUAL-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def parse_amount(text: str) -> float | None:
    try:
        value = float(text.strip().replace(",", "."))
    except ValueError:
        return None
    return value if value > 0 else None


def parse_payment_line(text: str) -> dict[str, Any] | None:
    parts = text.strip().split(maxsplit=3)
    if len(parts) < 2:
        return None
    amount = parse_amount(parts[1])
    if amount is None:
        return None
    return {
        "apartment_number": parts[0].strip(),
        "amount": amount,
        "period_code": parts[2].strip() if len(parts) >= 3 else None,
        "comment": parts[3].strip() if len(parts) >= 4 else None,
    }


def short_payment_type_hint() -> str:
    return (
        "➕ Внести оплату\n\n"
        "Введите одной строкой:\n\n"
        "квартира сумма период комментарий\n\n"
        "Пример:\n"
        "174 400 05-06_26 парковка день\n\n"
        "Минимально можно:\n"
        "174 400"
    )


def format_payment_draft(d: dict[str, Any]) -> str:
    return (
        "💰 Новая оплата\n\n"
        f"Квартира: {d.get('apartment_number') or '—'}\n"
        f"Сумма: {d.get('amount'):.2f} {DEFAULT_CURRENCY}\n"
        f"Период: {d.get('period_code') or '—'}\n"
        "Метод: cash\n"
        f"Комментарий: {d.get('comment') or '—'}\n\n"
        "Проверьте данные и сохраните."
    )


def insert_payment(d: dict[str, Any], user_id: int | str) -> tuple[bool, str]:
    con = connect()
    try:
        if not table_exists(con, "payments"):
            return False, "Таблица payments не найдена."

        cols = columns(con, "payments")
        candidates = {
            "payment_date": datetime.now().strftime("%Y-%m-%d"),
            "period_code": d.get("period_code"),
            "apartment_number": d.get("apartment_number"),
            "amount": d.get("amount"),
            "currency": DEFAULT_CURRENCY,
            "payment_method": "cash",
            "source": DEFAULT_SOURCE,
            "created_by": str(user_id),
            "comment": d.get("comment") or "Внесено через кассовый редактор Telegram",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "cashbox_code": "O",
            "operator_id": str(user_id),
            "cashier_entry_status": "CONFIRMED",
            "payment_channel": "CASH",
            "source_ref": now_ref(),
        }
        values = {k: v for k, v in candidates.items() if k in cols}

        if "amount" not in values or "apartment_number" not in values:
            return False, "В payments нет обязательных колонок amount/apartment_number."

        names = list(values.keys())
        sql = f"INSERT INTO payments ({','.join(names)}) VALUES ({','.join(['?'] * len(names))})"
        cur = con.execute(sql, [values[n] for n in names])
        con.commit()
        return True, f"Оплата сохранена. ID: {cur.lastrowid}"
    finally:
        con.close()


def format_payment_row(row: sqlite3.Row, n: int | None = None) -> str:
    prefix = f"{n}. " if n is not None else ""
    created = row["created_at"] if "created_at" in row.keys() else None
    payment_date = row["payment_date"] if "payment_date" in row.keys() else None
    status = row["cashier_entry_status"] if "cashier_entry_status" in row.keys() else None
    source_ref = row["source_ref"] if "source_ref" in row.keys() else None
    comment = row["comment"] if "comment" in row.keys() else None
    currency = row["currency"] if "currency" in row.keys() and row["currency"] else DEFAULT_CURRENCY

    lines = [
        f"{prefix}Оплата #{row['id']}",
        f"Квартира: {row['apartment_number'] or '—'}",
        f"Сумма: {float(row['amount'] or 0):.2f} {currency}",
        f"Дата: {created or payment_date or '—'}",
    ]
    if status:
        lines.append(f"Статус: {status}")
    if source_ref:
        lines.append(f"Основание: {source_ref}")
    if comment:
        lines.append("Комментарий: " + str(comment))
    return "\n".join(lines)


def last_payments(limit: int = 7) -> list[sqlite3.Row]:
    con = connect()
    try:
        if not table_exists(con, "payments"):
            return []
        return con.execute("SELECT * FROM payments ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        con.close()


def payments_by_apartment(apartment: str, limit: int = 10) -> list[sqlite3.Row]:
    con = connect()
    try:
        if not table_exists(con, "payments"):
            return []
        return con.execute(
            "SELECT * FROM payments WHERE apartment_number=? ORDER BY id DESC LIMIT ?",
            (apartment, limit),
        ).fetchall()
    finally:
        con.close()


def format_payments_list(title: str, rows: list[sqlite3.Row]) -> str:
    if not rows:
        return title + "\n\nЗаписей не найдено."
    parts = [title, ""]
    for i, row in enumerate(rows, 1):
        parts.append(format_payment_row(row, i))
        parts.append("────────────────")
    return "\n".join(parts).rstrip("─\n")


async def show_cashier_editor(update: Update, user_states: dict[int, Any], user_id: int) -> None:
    user_states[user_id] = {"mode": "cashier_editor", "screen": "menu"}
    await update.message.reply_text(
        "💰 Кассовый редактор\n\n"
        "Ручной ввод оплат и просмотр последних записей.\n"
        "Первая версия рассчитана на быстрый ввод бумажных оплат.",
        reply_markup=cashier_menu_keyboard(),
    )


async def handle_cashier_editor_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_states: dict[int, Any],
    user_id: int,
) -> bool:
    text = (update.message.text or "").strip()
    state = user_states.get(user_id, {})

    if state.get("mode") != "cashier_editor" and text not in {BTN_PAYMENTS, BTN_CASHIER_EDITOR}:
        return False

    if text in {BTN_PAYMENTS, BTN_CASHIER_EDITOR}:
        await show_cashier_editor(update, user_states, user_id)
        return True

    if text == BTN_NEW_PAYMENT:
        user_states[user_id] = {"mode": "cashier_editor", "screen": "new_payment_wait_line"}
        await update.message.reply_text(short_payment_type_hint(), reply_markup=back_keyboard())
        return True

    if text == BTN_LAST_PAYMENTS:
        await update.message.reply_text(
            format_payments_list("📜 Последние оплаты", last_payments()),
            reply_markup=cashier_menu_keyboard(),
        )
        return True

    if text == BTN_SEARCH_APARTMENT:
        user_states[user_id] = {"mode": "cashier_editor", "screen": "search_apartment"}
        await update.message.reply_text("🔎 Введите номер квартиры:", reply_markup=back_keyboard())
        return True

    if text == BTN_BACK:
        await show_cashier_editor(update, user_states, user_id)
        return True

    if text == BTN_MAIN_MENU:
        user_states[user_id] = {}
        await update.message.reply_text("🏠 Главное меню")
        return True

    if text == BTN_CANCEL:
        await show_cashier_editor(update, user_states, user_id)
        return True

    if state.get("screen") == "new_payment_wait_line":
        draft = parse_payment_line(text)
        if not draft:
            await update.message.reply_text(
                "⚠ Не понял оплату. Формат:\n174 400 05-06_26 парковка день",
                reply_markup=back_keyboard(),
            )
            return True
        user_states[user_id] = {"mode": "cashier_editor", "screen": "new_payment_confirm", "draft": draft}
        await update.message.reply_text(format_payment_draft(draft), reply_markup=payment_confirm_keyboard())
        return True

    if state.get("screen") == "new_payment_confirm":
        if text == BTN_CONFIRM_SAVE:
            ok, msg = insert_payment(state.get("draft") or {}, user_id)
            await update.message.reply_text(("✅ " if ok else "⚠ ") + msg)
            await show_cashier_editor(update, user_states, user_id)
            return True
        await update.message.reply_text("Нажмите «Сохранить оплату» или «Отмена».", reply_markup=payment_confirm_keyboard())
        return True

    if state.get("screen") == "search_apartment":
        apartment = text.strip()
        await update.message.reply_text(
            format_payments_list(f"🔎 Оплаты квартиры {apartment}", payments_by_apartment(apartment)),
            reply_markup=cashier_menu_keyboard(),
        )
        return True

    return False
