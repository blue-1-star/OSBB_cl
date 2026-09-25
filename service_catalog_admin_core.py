"""Audited administration of published OSBB service offers.

One offer consists of a catalog category, a concrete service item, its workflow
and a dated price version.  This module is intentionally the only writer used
by the Streamlit and Telegram catalog screens.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import re
import sqlite3
from typing import Any

from audit_logger import audit_log
from service_orders_core import get_conn, table_exists, text


CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
EXISTING_CODE_RE = re.compile(r"^[A-Za-z0-9_]{3,64}$")


def now_db() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _audit(conn: sqlite3.Connection, actor_id: int | str, action: str, table: str,
           row_id: str, old: Any, new: Any, comment: str) -> None:
    audit_log(
        conn=conn, operator_id=str(actor_id), user_id=str(actor_id),
        actor_type="service_catalog_manager", action_type=action,
        table_name=table, row_id=row_id, field_name="service_catalog",
        old_value=str(old or ""), new_value=str(new or ""),
        source_context="service_catalog_admin_core", comment=comment, commit=False,
    )


def _validate_code(value: str, label: str) -> str:
    code = text(value).upper()
    if not CODE_RE.fullmatch(code):
        raise ValueError(f"{label}: только A–Z, цифры и _, начало с буквы (3–64 символа).")
    return code


def _validate_existing_code(value: str, label: str) -> str:
    """Keep legacy item IDs unchanged when editing an existing DB row.

    Creation uses stricter uppercase IDs, but historic items such as
    ``01_BarrierPhoneConnect`` are valid primary keys and must not be uppercased.
    """
    code = text(value)
    if not EXISTING_CODE_RE.fullmatch(code):
        raise ValueError(f"{label}: допустимы буквы A–Z, цифры и _ (3–64 символа).")
    return code


def list_profiles(conn: sqlite3.Connection | None = None) -> list[dict]:
    owns = conn is None
    conn = conn or get_conn()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT profile_code, profile_name, service_category, description "
            "FROM service_workflow_profiles WHERE is_active=1 ORDER BY service_category, profile_name"
        )]
    finally:
        if owns:
            conn.close()


def describe_profile(profile_code: str, conn: sqlite3.Connection | None = None) -> str:
    """Explain a configured order workflow in terms of its actual steps."""
    owns = conn is None
    conn = conn or get_conn()
    try:
        profile = conn.execute(
            "SELECT profile_name, service_category FROM service_workflow_profiles "
            "WHERE profile_code=? AND is_active=1",
            (profile_code,),
        ).fetchone()
        if profile is None:
            return "Маршрут исполнения не найден."
        steps = conn.execute(
            "SELECT step_name FROM service_workflow_steps WHERE profile_code=? "
            "AND is_required=1 ORDER BY sequence_no, id",
            (profile_code,),
        ).fetchall()
        parts = [f"{index}. {row['step_name']}" for index, row in enumerate(steps, 1)]
        detail = "\n".join(parts) if parts else "Шаги для этого маршрута ещё не настроены."
        if profile_code == "REMOTE_NEW_PREORDER":
            detail += (
                "\nПосле поставки партия учитывается на ЦС; её можно передать на пост O "
                "или консьержу K с подтверждением приёма. Сейчас выдачу жителю "
                "фиксирует оператор; отдельного подтверждения жителя ещё нет."
            )
        return f"{profile['profile_name']} ({profile['service_category']}):\n{detail}"
    finally:
        if owns:
            conn.close()


def list_offers(conn: sqlite3.Connection | None = None) -> list[dict]:
    owns = conn is None
    conn = conn or get_conn()
    try:
        return [dict(row) for row in conn.execute(
            """
            SELECT i.service_item_code, i.service_code, i.service_item_name,
                   i.amount_default, i.currency, i.status AS item_status,
                   i.is_active AS item_active, i.description,
                   c.service_name AS catalog_name, c.category,
                   w.workflow_profile_code, w.resident_request_enabled,
                   w.operator_create_enabled, w.requires_charge, w.payment_timing,
                   w.inventory_mode, w.resident_asset_mode, w.is_active AS workflow_active,
                   p.profile_name,
                   (SELECT amount FROM service_price_versions pv
                     WHERE pv.service_item_code=i.service_item_code AND pv.is_active=1
                       AND pv.effective_from<=date('now')
                       AND (pv.effective_to IS NULL OR pv.effective_to='' OR pv.effective_to>=date('now'))
                     ORDER BY pv.effective_from DESC, pv.id DESC LIMIT 1) AS current_price,
                   (SELECT effective_from FROM service_price_versions pv
                     WHERE pv.service_item_code=i.service_item_code AND pv.is_active=1
                     ORDER BY pv.effective_from DESC, pv.id DESC LIMIT 1) AS price_since
            FROM service_items i
            LEFT JOIN service_catalog c ON c.service_code=i.service_code
            LEFT JOIN service_item_workflows w ON w.service_item_code=i.service_item_code
            LEFT JOIN service_workflow_profiles p ON p.profile_code=w.workflow_profile_code
            ORDER BY COALESCE(c.category, ''), i.service_item_name, i.service_item_code
            """
        )]
    finally:
        if owns:
            conn.close()


def create_offer(*, actor_id: int | str, service_code: str, catalog_name: str,
                 category: str, item_code: str, item_name: str,
                 workflow_profile_code: str, price: float, currency: str = "UAH",
                 resident_request_enabled: bool = False, description: str = "") -> dict:
    service_code = _validate_code(service_code, "Код категории")
    item_code = _validate_code(item_code, "Код позиции")
    profile = _validate_code(workflow_profile_code, "Профиль")
    category = _validate_code(category, "Категория")
    catalog_name, item_name = text(catalog_name), text(item_name)
    if not catalog_name or not item_name:
        raise ValueError("Укажите название категории и название позиции.")
    if price < 0:
        raise ValueError("Цена не может быть отрицательной.")
    currency = text(currency).upper() or "UAH"
    if len(currency) != 3:
        raise ValueError("Валюта указывается трёхбуквенным кодом, например UAH.")

    conn = get_conn()
    try:
        cur = conn.cursor()
        profile_row = cur.execute(
            "SELECT service_category FROM service_workflow_profiles WHERE profile_code=? AND is_active=1", (profile,)
        ).fetchone()
        if not profile_row:
            raise ValueError("Активный профиль исполнения не найден.")
        if text(profile_row[0]).upper() != category:
            raise ValueError("Категория должна соответствовать выбранному профилю исполнения.")
        if cur.execute("SELECT 1 FROM service_items WHERE service_item_code=?", (item_code,)).fetchone():
            raise ValueError("Позиция с таким кодом уже существует.")
        catalog = cur.execute("SELECT service_name, category FROM service_catalog WHERE service_code=?", (service_code,)).fetchone()
        if catalog and text(catalog[1]).upper() != category:
            raise ValueError("У существующей категории другой тип; выберите другой код категории.")
        if not catalog:
            cur.execute(
                """INSERT INTO service_catalog(service_code, service_group, service_name, unit, is_active,
                   comment, service_type, category, is_monthly, is_fundraising, is_commercial,
                   is_access_control, is_cash_collectable, access_policy_enabled, access_policy_scope,
                   access_policy_mode, manual_review_required, created_at, updated_at)
                   VALUES (?, 'ACCESS_CONTROL', ?, 'шт.', 1, ?, 'ONE_TIME', ?, 0,0,0,1,1,0,'NONE','NONE',0,?,?)""",
                (service_code, catalog_name, "Создано через каталог услуг.", category, now_db(), now_db()),
            )
            _audit(conn, actor_id, "service_catalog_created", "service_catalog", service_code, "", catalog_name, "Создана категория услуг")
        cur.execute(
            """INSERT INTO service_items(service_item_code, service_code, service_item_name, service_type,
                 amount_default, currency, status, is_active, description, comment, created_at, updated_at)
               VALUES (?, ?, ?, 'ONE_TIME', ?, ?, 'draft', 1, ?, 'Создано через каталог услуг.', ?, ?)""",
            (item_code, service_code, item_name, float(price), currency, description or None, now_db(), now_db()),
        )
        cur.execute(
            """INSERT INTO service_item_workflows(service_item_code, workflow_profile_code,
                 resident_request_enabled, operator_create_enabled, requires_charge, payment_timing,
                 inventory_mode, resident_asset_mode, is_active, created_at, updated_at)
               VALUES (?, ?, 0, 1, 1, 'BEFORE_FULFILLMENT', 'NONE', 'NONE', 1, ?, ?)""",
            (item_code, profile, now_db(), now_db()),
        )
        cur.execute(
            """INSERT INTO service_price_versions(service_item_code, amount, currency, effective_from,
                 effective_to, is_active, created_by, note, created_at, updated_at)
               VALUES (?, ?, ?, ?, NULL, 1, ?, 'Первичная цена позиции.', ?, ?)""",
            (item_code, float(price), currency, date.today().isoformat(), str(actor_id), now_db(), now_db()),
        )
        _audit(conn, actor_id, "service_item_created", "service_items", item_code, "", item_name,
               "Создан черновик позиции каталога")
        conn.commit()
        return next(row for row in list_offers(conn) if row["service_item_code"] == item_code)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_publication(*, actor_id: int | str, item_code: str, published: bool) -> dict:
    item_code = _validate_existing_code(item_code, "Код позиции")
    conn = get_conn()
    try:
        cur = conn.cursor()
        row = cur.execute("SELECT status FROM service_items WHERE service_item_code=?", (item_code,)).fetchone()
        if not row:
            raise ValueError("Позиция каталога не найдена.")
        old = text(row[0])
        new = "active" if published else "draft"
        cur.execute("UPDATE service_items SET status=?, updated_at=? WHERE service_item_code=?", (new, now_db(), item_code))
        cur.execute("UPDATE service_item_workflows SET resident_request_enabled=?, updated_at=? WHERE service_item_code=?", (int(published), now_db(), item_code))
        _audit(conn, actor_id, "service_item_published" if published else "service_item_unpublished",
               "service_items", item_code, old, new,
               "Позиция опубликована для жителей" if published else "Позиция снята с публикации")
        conn.commit()
        return next(row for row in list_offers(conn) if row["service_item_code"] == item_code)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def change_price(*, actor_id: int | str, item_code: str, price: float, currency: str = "UAH", note: str = "") -> dict:
    item_code = _validate_existing_code(item_code, "Код позиции")
    if price < 0:
        raise ValueError("Цена не может быть отрицательной.")
    currency = text(currency).upper() or "UAH"
    conn = get_conn()
    try:
        cur = conn.cursor()
        if not cur.execute("SELECT 1 FROM service_items WHERE service_item_code=?", (item_code,)).fetchone():
            raise ValueError("Позиция каталога не найдена.")
        old = cur.execute("SELECT amount, currency FROM service_price_versions WHERE service_item_code=? AND is_active=1 ORDER BY id DESC LIMIT 1", (item_code,)).fetchone()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        cur.execute("UPDATE service_price_versions SET is_active=0, effective_to=?, updated_at=? WHERE service_item_code=? AND is_active=1", (yesterday, now_db(), item_code))
        cur.execute("UPDATE service_items SET amount_default=?, currency=?, updated_at=? WHERE service_item_code=?", (float(price), currency, now_db(), item_code))
        cur.execute("""INSERT INTO service_price_versions(service_item_code, amount, currency, effective_from,
                       effective_to, is_active, created_by, note, created_at, updated_at)
                       VALUES (?, ?, ?, ?, NULL, 1, ?, ?, ?, ?)""",
                    (item_code, float(price), currency, date.today().isoformat(), str(actor_id), text(note) or "Изменение цены через каталог услуг.", now_db(), now_db()))
        _audit(conn, actor_id, "service_price_changed", "service_price_versions", item_code,
               f"{old[0]} {old[1]}" if old else "", f"{price} {currency}", text(note) or "Новая версия цены")
        conn.commit()
        return next(row for row in list_offers(conn) if row["service_item_code"] == item_code)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Compatibility names retained for the established service preflight scripts.
# The UI uses the shorter API above; these functions keep older automation
# working while writing the same catalog/item/workflow/price-version records.
def list_service_offers(*, include_retired: bool = False, conn: sqlite3.Connection | None = None) -> list[dict]:
    rows = list_offers(conn)
    if include_retired:
        return rows
    return [row for row in rows if int(row.get("item_active") or 0) == 1 and row.get("item_status") != "archived"]


def add_or_update_service_offer(*, service_code: str, service_name: str, service_group: str,
                                service_type: str, category: str, service_item_code: str,
                                service_item_name: str, workflow_profile_code: str,
                                amount: float | int | str | None, effective_from: str,
                                resident_request_enabled: bool, actor_id: int | str | None,
                                description: str = "", conn: sqlite3.Connection | None = None) -> dict:
    # Historical callers use this for seed/preflight.  Existing entries keep
    # their identity; their current attributes and a dated price are updated.
    existing = next((r for r in list_offers(conn) if r["service_item_code"] == text(service_item_code).upper()), None)
    if not existing:
        result = create_offer(
            actor_id=actor_id or "system", service_code=service_code, catalog_name=service_name,
            category=category, item_code=service_item_code, item_name=service_item_name,
            workflow_profile_code=workflow_profile_code, price=float(amount or 0),
            resident_request_enabled=False, description=description,
        )
    else:
        own = conn is None
        db = conn or get_conn()
        try:
            cur = db.cursor()
            cur.execute("UPDATE service_catalog SET service_name=?, service_group=?, service_type=?, category=?, comment=?, updated_at=? WHERE service_code=?",
                        (text(service_name), text(service_group) or "GENERAL", text(service_type) or "ONE_TIME", text(category) or "GENERAL", text(description) or None, now_db(), text(service_code).upper()))
            cur.execute("UPDATE service_items SET service_item_name=?, service_type=?, description=?, updated_at=? WHERE service_item_code=?",
                        (text(service_item_name), text(service_type) or "ONE_TIME", text(description) or None, now_db(), text(service_item_code).upper()))
            cur.execute("UPDATE service_item_workflows SET workflow_profile_code=?, resident_request_enabled=?, is_active=1, updated_at=? WHERE service_item_code=?",
                        (text(workflow_profile_code).upper(), int(resident_request_enabled), now_db(), text(service_item_code).upper()))
            _audit(db, actor_id or "system", "service_item_updated", "service_items", text(service_item_code).upper(), "", text(service_item_name), "Обновление позиции через совместимый API")
            if own:
                db.commit()
        except Exception:
            if own:
                db.rollback()
            raise
        finally:
            if own:
                db.close()
        result = next(r for r in list_offers(conn) if r["service_item_code"] == text(service_item_code).upper())
    if amount is not None:
        set_service_price(service_item_code=service_item_code, amount=amount,
                          effective_from=effective_from, actor_id=actor_id, conn=conn)
    set_publication(actor_id=actor_id or "system", item_code=service_item_code, published=resident_request_enabled)
    return result


def set_service_price(*, service_item_code: str, amount: float | int | str, effective_from: str,
                      actor_id: int | str | None, note: str = "", conn: sqlite3.Connection | None = None) -> None:
    # Current UI permits a price from today; older scripts may deliberately
    # supply another effective date, so retain that useful administrative path.
    try:
        when = datetime.strptime(text(effective_from), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Дата цены: ГГГГ-ММ-ДД.") from exc
    if when != date.today():
        own = conn is None
        db = conn or get_conn()
        try:
            cur = db.cursor(); code = _validate_existing_code(service_item_code, "Код позиции")
            value = float(str(amount).replace(",", "."))
            cur.execute("UPDATE service_price_versions SET effective_to=?, updated_at=? WHERE service_item_code=? AND is_active=1 AND effective_from<?",
                        ((when - timedelta(days=1)).isoformat(), now_db(), code, when.isoformat()))
            cur.execute("INSERT INTO service_price_versions(service_item_code, amount, currency, effective_from, effective_to, is_active, created_by, note, created_at, updated_at) VALUES (?, ?, 'UAH', ?, NULL, 1, ?, ?, ?, ?)",
                        (code, value, when.isoformat(), str(actor_id or "system"), text(note) or "Версия цены", now_db(), now_db()))
            cur.execute("UPDATE service_items SET amount_default=?, updated_at=? WHERE service_item_code=?", (value, now_db(), code))
            _audit(db, actor_id or "system", "service_price_changed", "service_price_versions", code, "", value, text(note) or "Версия цены")
            if own: db.commit()
        except Exception:
            if own: db.rollback()
            raise
        finally:
            if own: db.close()
        return
    change_price(actor_id=actor_id or "system", item_code=service_item_code, price=float(str(amount).replace(",", ".")), note=note)


def retire_service_offer(*, service_item_code: str, actor_id: int | str | None, reason: str,
                         conn: sqlite3.Connection | None = None) -> None:
    if not text(reason):
        raise ValueError("Для архивирования укажите причину.")
    own = conn is None; db = conn or get_conn()
    try:
        code = _validate_existing_code(service_item_code, "Код позиции")
        db.execute("UPDATE service_items SET is_active=0, status='archived', date_to=?, updated_at=? WHERE service_item_code=?", (date.today().isoformat(), now_db(), code))
        db.execute("UPDATE service_item_workflows SET is_active=0, retired_at=?, retired_reason=?, updated_at=? WHERE service_item_code=?", (now_db(), text(reason), now_db(), code))
        _audit(db, actor_id or "system", "service_item_archived", "service_items", code, "active", "archived", text(reason))
        if own: db.commit()
    except Exception:
        if own: db.rollback()
        raise
    finally:
        if own: db.close()


def restore_service_offer(*, service_item_code: str, actor_id: int | str | None,
                          conn: sqlite3.Connection | None = None) -> None:
    own = conn is None; db = conn or get_conn()
    try:
        code = _validate_existing_code(service_item_code, "Код позиции")
        db.execute("UPDATE service_items SET is_active=1, status='draft', date_to=NULL, updated_at=? WHERE service_item_code=?", (now_db(), code))
        db.execute("UPDATE service_item_workflows SET is_active=1, resident_request_enabled=0, retired_at=NULL, retired_reason=NULL, updated_at=? WHERE service_item_code=?", (now_db(), code))
        _audit(db, actor_id or "system", "service_item_restored", "service_items", code, "archived", "draft", "Восстановлено без автоматической публикации")
        if own: db.commit()
    except Exception:
        if own: db.rollback()
        raise
    finally:
        if own: db.close()
