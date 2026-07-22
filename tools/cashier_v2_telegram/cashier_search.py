#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import sqlite3
from typing import Any

import cashier_v2_core as core


def _cols(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _table(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def normalize_plate_fragment(value: str) -> str:
    return re.sub(r"[^0-9A-ZА-ЯІЇЄ]", "", value.upper())


def apartment_by_id(con: sqlite3.Connection, apartment_id: int) -> dict | None:
    if not _table(con, 'apartments'):
        return None
    row = con.execute("SELECT * FROM apartments WHERE id=?", (int(apartment_id),)).fetchone()
    return dict(row) if row else None


def apartment_by_number(con: sqlite3.Connection, number: str) -> list[dict]:
    if not _table(con, 'apartments') or 'apartment_number' not in _cols(con, 'apartments'):
        return []
    rows = con.execute(
        "SELECT * FROM apartments WHERE CAST(apartment_number AS TEXT)=? ORDER BY id",
        (str(number).strip(),),
    ).fetchall()
    return [dict(r) for r in rows]


def vehicles_for_apartment(con: sqlite3.Connection, apartment_id: int) -> list[dict]:
    if not _table(con, 'vehicles'):
        return []
    cols = _cols(con, 'vehicles')
    status_filter = ""
    if 'status' in cols:
        status_filter = "AND COALESCE(status,'Active') NOT IN ('Inactive','ARCHIVED','DELETED')"
    rows = con.execute(
        f"SELECT * FROM vehicles WHERE apartment_id=? {status_filter} ORDER BY id",
        (int(apartment_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def search_payers(query: str, limit: int = 12) -> list[dict[str, Any]]:
    q = str(query or '').strip()
    if not q:
        return []

    con = core.get_conn()
    try:
        results: list[dict[str, Any]] = []

        # Exact apartment number gets priority.
        for apartment in apartment_by_number(con, q):
            vehicles = vehicles_for_apartment(con, int(apartment['id']))
            results.append({
                'kind': 'apartment',
                'apartment': apartment,
                'apartment_id': int(apartment['id']),
                'apartment_number': str(apartment.get('apartment_number') or q),
                'vehicles': vehicles,
                'label': f"🏠 квартира {apartment.get('apartment_number') or q}",
            })

        # Vehicle search accepts any fragment, especially the numeric part.
        if _table(con, 'vehicles'):
            vcols = _cols(con, 'vehicles')
            plate_cols = [c for c in ('license_plate_normalized', 'license_plate') if c in vcols]
            if plate_cols:
                fragment = normalize_plate_fragment(q)
                where = " OR ".join(
                    [f"UPPER(REPLACE(REPLACE(COALESCE(v.{c},''),' ',''),'-','')) LIKE ?" for c in plate_cols]
                )
                params = [f"%{fragment}%"] * len(plate_cols)
                rows = con.execute(
                    f"""
                    SELECT v.*, a.apartment_number
                    FROM vehicles v
                    LEFT JOIN apartments a ON a.id=v.apartment_id
                    WHERE ({where})
                    ORDER BY a.apartment_number, v.id
                    LIMIT ?
                    """,
                    (*params, int(limit)),
                ).fetchall()
                for row in rows:
                    vehicle = dict(row)
                    apartment = apartment_by_id(con, int(vehicle['apartment_id'])) if vehicle.get('apartment_id') else None
                    if not apartment:
                        continue
                    plate = vehicle.get('license_plate') or vehicle.get('license_plate_normalized') or q
                    parking = vehicle.get('parking_time') or '—'
                    results.append({
                        'kind': 'vehicle',
                        'vehicle': vehicle,
                        'vehicle_id': int(vehicle['id']),
                        'plate': plate,
                        'parking_time': parking,
                        'apartment': apartment,
                        'apartment_id': int(apartment['id']),
                        'apartment_number': str(apartment.get('apartment_number') or ''),
                        'vehicles': vehicles_for_apartment(con, int(apartment['id'])),
                        'label': f"🚗 {plate} / кв. {apartment.get('apartment_number') or '—'} / {parking}",
                    })

        # Deduplicate. Vehicle rows are more specific than apartment rows.
        seen: set[tuple] = set()
        out: list[dict[str, Any]] = []
        for item in results:
            key = (item.get('kind'), item.get('vehicle_id'), item.get('apartment_id'))
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out[:limit]
    finally:
        con.close()



def search_commercial_subjects(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search commercial units first, then enrich with contract and contract items.

    Commercial premises live in apartments. A contract may be DRAFT and may have
    zero or several individual payment items. One returned payer corresponds to
    one contract item, so the cashier gets a concrete service and amount.
    """
    q = str(query or '').strip()
    if not q:
        return []
    con = core.get_conn()
    try:
        if not _table(con, 'apartments'):
            return []
        like = f"%{q}%"
        unit_rows = con.execute(
            """
            SELECT a.*
            FROM apartments a
            WHERE (
                    COALESCE(a.unit_type,'') <> 'RESIDENTIAL'
                 OR COALESCE(a.unit_code,'') <> ''
                 OR COALESCE(a.display_name,'') <> ''
            )
              AND (
                    COALESCE(a.unit_code,'') LIKE ?
                 OR COALESCE(a.display_name,'') LIKE ?
                 OR COALESCE(a.official_number,'') LIKE ?
                 OR CAST(a.apartment_number AS TEXT) LIKE ?
                 OR CAST(COALESCE(a.entrance_number,a.entrance) AS TEXT) = ?
              )
            ORDER BY COALESCE(a.display_name,''), COALESCE(a.unit_code,''), a.apartment_number
            LIMIT ?
            """,
            (like, like, like, like, q, int(limit)),
        ).fetchall()

        out: list[dict[str, Any]] = []
        for unit_row in unit_rows:
            unit = dict(unit_row)
            contracts = []
            if _table(con, 'commercial_contracts'):
                contracts = con.execute(
                    """
                    SELECT * FROM commercial_contracts
                    WHERE unit_id=?
                    ORDER BY CASE status WHEN 'ACTIVE' THEN 0 WHEN 'DRAFT' THEN 1 ELSE 2 END, id DESC
                    """,
                    (int(unit['id']),),
                ).fetchall()

            if not contracts:
                company = unit.get('display_name') or unit.get('unit_code') or f"Помещение {unit.get('apartment_number')}"
                out.append({
                    'kind':'commercial', 'commercial_contract_id':None,
                    'commercial_unit_id':int(unit['id']), 'counterparty_name':company,
                    'contract_number':None, 'contract_status':None,
                    'expected_amount':0.0, 'item_names':None,
                    'commercial_contract_item_id':None,
                    'commercial_service':{
                        'service_code':'COMMERCIAL_MANUAL', 'base_service_code':'COMMERCIAL_MANUAL',
                        'service_item_code':None, 'service_type':'COMMERCIAL',
                        'service_name':'Коммерческое помещение — ручной платёж',
                    },
                    'apartment':unit, 'apartment_id':int(unit['id']),
                    'apartment_number':str(unit.get('apartment_number') or ''),
                    'unit_code':unit.get('unit_code'), 'display_name':unit.get('display_name'),
                    'label':f"🏢 {company} / {unit.get('unit_code') or unit.get('apartment_number') or '—'}",
                })
                continue

            for contract_row in contracts:
                contract=dict(contract_row)
                items=[]
                if _table(con,'commercial_contract_items'):
                    items=con.execute(
                        """
                        SELECT * FROM commercial_contract_items
                        WHERE contract_id=? AND COALESCE(is_active,1)=1
                        ORDER BY id
                        """, (int(contract['id']),)
                    ).fetchall()
                company=contract.get('counterparty_name') or unit.get('display_name') or unit.get('unit_code') or 'Без названия'
                if not items:
                    items=[None]
                for item_row in items:
                    item=dict(item_row) if item_row is not None else {}
                    mode=str(item.get('calculation_mode') or 'FIXED_MONTHLY')
                    if mode=='FIXED_MONTHLY':
                        amount=float(item.get('fixed_amount') or 0)
                    else:
                        amount=float(item.get('rate_amount') or 0)*float(item.get('quantity_default') or 1)
                    item_name=item.get('item_name') or 'Условие договора не заполнено'
                    service_code=item.get('reference_service_code') or item.get('item_code') or 'COMMERCIAL_CONTRACT'
                    out.append({
                        'kind':'commercial', 'commercial_contract_id':int(contract['id']),
                        'commercial_unit_id':int(unit['id']),
                        'commercial_contract_item_id':int(item['id']) if item.get('id') is not None else None,
                        'counterparty_name':company, 'contract_number':contract.get('contract_number'),
                        'contract_status':contract.get('status'), 'valid_from':contract.get('valid_from'),
                        'valid_to':contract.get('valid_to'), 'payment_due_day':contract.get('payment_due_day'),
                        'expected_amount':amount, 'item_names':item_name,
                        'commercial_service':{
                            'service_code':service_code, 'base_service_code':service_code,
                            'service_item_code':item.get('item_code'), 'service_type':'COMMERCIAL',
                            'service_name':item_name, 'service_item_name':item_name,
                        },
                        'apartment':unit, 'apartment_id':int(unit['id']),
                        'apartment_number':str(unit.get('apartment_number') or ''),
                        'unit_code':unit.get('unit_code'), 'display_name':unit.get('display_name'),
                        'label':f"🏢 {company} / {item_name} / {unit.get('unit_code') or unit.get('apartment_number') or '—'}",
                    })
        return out[:limit]
    finally:
        con.close()

def service_text(service: dict) -> str:
    return ' '.join(str(service.get(k) or '') for k in (
        'service_item_code', 'service_code', 'service_name', 'service_item_name',
        'service_type', 'description', 'comment'
    )).lower()


def choose_service(group: str, period_code: str | None) -> dict | None:
    options = list(core.service_options(period_code))
    wanted = 'night' if group == 'night' else 'day' if group == 'day' else group

    ranked: list[tuple[int, dict]] = []
    for opt in options:
        t = service_text(opt)
        if 'test' in t or 'тест' in t:
            continue
        score = 0
        if wanted == 'night' and ('night' in t or 'ноч' in t):
            score += 100
        elif wanted == 'day' and ('day' in t or 'днев' in t or 'день' in t):
            score += 100
        elif wanted not in {'night', 'day'} and wanted in t:
            score += 60
        if opt.get('period_code') == period_code:
            score += 20
        if opt.get('service_item_code'):
            score += 10
        if opt.get('amount_default') is not None:
            score += 5
        if score > 0:
            ranked.append((score, opt))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[0][1] if ranked else None


def previous_month(period_code: str) -> str:
    y, m = map(int, period_code.split('-'))
    m -= 1
    if m == 0:
        y -= 1
        m = 12
    return f"{y:04d}-{m:02d}"


def next_month(period_code: str) -> str:
    y, m = map(int, period_code.split('-'))
    m += 1
    if m == 13:
        y += 1
        m = 1
    return f"{y:04d}-{m:02d}"


def latest_paid_period(apartment_id: int, service: dict) -> str | None:
    con = core.get_conn()
    try:
        if not _table(con, 'payments'):
            return None
        cols = _cols(con, 'payments')
        filters = []
        params: list[Any] = []
        if 'apartment_id' in cols:
            filters.append('apartment_id=?')
            params.append(int(apartment_id))
        else:
            apartment = apartment_by_id(con, apartment_id)
            filters.append('CAST(apartment_number AS TEXT)=?')
            params.append(str(apartment.get('apartment_number') if apartment else ''))
        if 'service_item_code' in cols and service.get('service_item_code'):
            filters.append('service_item_code=?')
            params.append(service.get('service_item_code'))
        elif 'service_code' in cols:
            filters.append('service_code=?')
            params.append(service.get('service_code'))
        if 'period_code' not in cols:
            return None
        row = con.execute(
            'SELECT period_code FROM payments WHERE ' + ' AND '.join(filters) +
            " AND period_code GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]' " +
            'ORDER BY period_code DESC, id DESC LIMIT 1',
            params,
        ).fetchone()
        return str(row['period_code']) if row and row['period_code'] else None
    finally:
        con.close()



def vehicle_payer(apartment: dict, vehicle: dict) -> dict[str, Any]:
    plate = vehicle.get('license_plate') or vehicle.get('license_plate_normalized') or '—'
    parking = vehicle.get('parking_time') or '—'
    return {
        'kind': 'vehicle',
        'vehicle': vehicle,
        'vehicle_id': int(vehicle['id']),
        'plate': plate,
        'parking_time': parking,
        'apartment': apartment,
        'apartment_id': int(apartment['id']),
        'apartment_number': str(apartment.get('apartment_number') or ''),
        'vehicles': [],
        'label': f"🚗 {plate} / кв. {apartment.get('apartment_number') or '—'} / {parking}",
    }


def payer_vehicle_choices(payer: dict) -> list[dict[str, Any]]:
    if payer.get('kind') == 'vehicle':
        return [payer]
    apartment = payer.get('apartment') or {}
    return [vehicle_payer(apartment, v) for v in (payer.get('vehicles') or [])]


def group_from_payer(payer: dict) -> str | None:
    parking = str(payer.get('parking_time') or (payer.get('vehicle') or {}).get('parking_time') or '').strip().lower()
    if 'night' in parking or 'ноч' in parking:
        return 'night'
    if 'day' in parking or 'днев' in parking or 'день' in parking:
        return 'day'
    return None


def current_tariff(service: dict, period_code: str) -> float | None:
    con = core.get_conn()
    try:
        today = core.today()
        item_code = service.get('service_item_code')
        service_code = service.get('service_code')

        if item_code and _table(con, 'service_price_versions'):
            cols = _cols(con, 'service_price_versions')
            if {'service_item_code', 'amount'} <= cols:
                active_clause = "AND COALESCE(is_active,1)=1" if 'is_active' in cols else ''
                row = con.execute(
                    f"""
                    SELECT amount
                    FROM service_price_versions
                    WHERE service_item_code=?
                      {active_clause}
                      AND (effective_from IS NULL OR effective_from<=?)
                      AND (effective_to IS NULL OR effective_to='' OR effective_to>=?)
                    ORDER BY effective_from DESC, id DESC
                    LIMIT 1
                    """,
                    (item_code, today, today),
                ).fetchone()
                if row and row['amount'] is not None and float(row['amount']) > 0:
                    return float(row['amount'])

        if service_code and _table(con, 'service_tariffs'):
            cols = _cols(con, 'service_tariffs')
            if {'service_code', 'amount'} <= cols:
                active_clause = "AND COALESCE(is_active,1)=1" if 'is_active' in cols else ''
                row = con.execute(
                    f"""
                    SELECT amount
                    FROM service_tariffs
                    WHERE service_code=?
                      {active_clause}
                      AND (valid_from IS NULL OR valid_from<=?)
                      AND (valid_to IS NULL OR valid_to='' OR valid_to>=?)
                    ORDER BY valid_from DESC, id DESC
                    LIMIT 1
                    """,
                    (service_code, today, today),
                ).fetchone()
                if row and row['amount'] is not None and float(row['amount']) > 0:
                    return float(row['amount'])
        return None
    finally:
        con.close()


def proposed_defaults(payer: dict, service: dict, fallback_period: str) -> dict[str, Any]:
    apartment = payer['apartment']
    latest = latest_paid_period(int(apartment['id']), service)
    proposed_period = next_month(latest) if latest else fallback_period

    charge = core.suggested_charge(
        apartment,
        period_code=proposed_period,
        service_code=service['service_code'],
        service_item_code=service.get('service_item_code'),
    )
    amount = None
    charge_id = None
    if charge:
        amount = float(charge.get('outstanding_amount') or 0)
        charge_id = charge.get('charge_id')
    if not amount:
        default = service.get('amount_default')
        if default is not None and float(default) > 0:
            amount = float(default)
    if not amount:
        amount = current_tariff(service, proposed_period)

    return {
        'period_code': proposed_period,
        'latest_paid_period': latest,
        'amount': amount,
        'charge_id': charge_id,
    }
