#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any


def period_display(period: str | None) -> str:
    if not period:
        return '—'
    parts = str(period).split('-')
    if len(parts) == 2 and len(parts[0]) == 4:
        return f"{parts[1]}-{parts[0]}"
    return str(period)


def money(value: Any) -> str:
    if value is None or value == '':
        return 'не определена'
    try:
        amount = float(value)
    except Exception:
        return 'не определена'
    return f"{amount:.2f}"


def payer_title(payer: dict) -> str:
    if payer.get('kind') == 'vehicle':
        return f"🚗 {payer.get('plate') or '—'}"
    if payer.get('kind') == 'commercial':
        return f"🏢 {payer.get('counterparty_name') or 'Без названия'}"
    return f"🏠 Квартира {payer.get('apartment_number') or '—'}"


def payment_card(draft: dict) -> str:
    payer = draft.get('payer') or {}
    service = draft.get('service') or {}
    vehicle = payer.get('vehicle') or {}
    parking = payer.get('parking_time') or vehicle.get('parking_time') or draft.get('service_group') or '—'
    service_name = service.get('service_name') or service.get('service_item_name') or service.get('service_code') or '—'
    comment = str(draft.get('comment') or '').strip()

    lines = [payer_title(payer)]

    if payer.get('kind') == 'vehicle':
        apartment = payer.get('apartment_number') or '—'
        lines.append(f"Кв. {apartment} · {parking}")
        model = vehicle.get('car_model')
        if model:
            lines.append(str(model))
    elif payer.get('kind') == 'commercial':
        unit_code = payer.get('unit_code') or payer.get('apartment_number') or '—'
        lines.append(f"Помещение: {unit_code}")
        contract_number = payer.get('contract_number')
        if contract_number:
            lines.append(f"Договор: {contract_number}")
    else:
        lines.append(f"Квартира: {payer.get('apartment_number') or '—'}")

    lines.extend([
        '',
        str(service_name),
        f"Период: {period_display(draft.get('period_code'))}",
        f"Сумма: {money(draft.get('amount'))} грн",
    ])

    latest_paid_period = draft.get('latest_paid_period')
    if latest_paid_period:
        lines.insert(-2, f"Оплачено по: {period_display(latest_paid_period)}")
    if comment:
        lines.append(f"Комментарий: {comment}")

    return '\n'.join(lines)


def success_card(result: dict, draft: dict) -> str:
    payer = draft.get('payer') or {}
    return '\n'.join([
        '✅ Оплата принята',
        payer_title(payer),
        f"Период: {period_display(draft.get('period_code'))}",
        f"Сумма: {money(draft.get('amount'))} грн",
        f"Чек: {result.get('receipt_number') or '—'}",
    ])
