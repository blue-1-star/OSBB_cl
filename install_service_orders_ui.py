"""
Installer for the service-orders user interface.

It changes only source code and the isolated live-sandbox launcher:
- makes payment linking cover the exact order amount before payment confirmation;
- switches the live sandbox launcher from v3 to v4;
- does not modify Bots/parking_bot.py or any database.
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CORE = ROOT / "service_orders_core.py"
HANDLER = ROOT / "Bots" / "handlers" / "service_orders_workspace.py"
PORTAL = ROOT / "Bots" / "handlers" / "client_portal_v3.py"
RUNNER = ROOT / "run_bot_live_service_sandbox_v4.py"
LIVE_LAUNCHER = ROOT / "Start_OSBB_Live_Service_Sandbox_Bot.bat"

MARKER = "# SAFE_PAYMENT_LINK_POLICY_V1"
SAFE_PAYMENT_FUNCTION = 'def link_payment_to_order(\n    *,\n    order_id: int,\n    payment_id: int,\n    amount: float | None,\n    actor_id: int | str | None,\n    note: str = "",\n    conn: sqlite3.Connection | None = None,\n) -> dict:\n    # SAFE_PAYMENT_LINK_POLICY_V1\n    #\n    # Payment is evidence of money received, not a generic button that may\n    # close an order. A link is allowed only for the same unit/service and\n    # only up to the remaining amount. PAYMENT_CONFIRMED is set only when\n    # linked money fully covers the order amount.\n    owns = conn is None\n    conn = conn or get_conn()\n    try:\n        ready, reason = schema_ready(conn)\n        if not ready:\n            raise RuntimeError(reason)\n\n        cur = conn.cursor()\n        order = get_service_order(cur, order_id)\n        if text(order.get("order_status")) in {"COMPLETED", "CANCELLED"}:\n            raise ValueError("Нельзя привязать оплату к завершённой или отменённой заявке.")\n\n        profile = _fetchone_dict(\n            cur,\n            """\n            SELECT service_category\n            FROM service_workflow_profiles\n            WHERE profile_code = ?\n            """,\n            (order["workflow_profile_code"],),\n        ) or {"service_category": "GENERAL"}\n\n        _permission_or_raise(\n            actor_id,\n            "service_order_steps",\n            "CONFIRM",\n            "SERVICE_CATEGORY",\n            text(profile.get("service_category")) or "GENERAL",\n        )\n\n        payment_step = _fetchone_dict(\n            cur,\n            """\n            SELECT *\n            FROM service_order_steps\n            WHERE service_order_id = ?\n              AND step_code = \'PAYMENT_CONFIRMED\'\n            """,\n            (int(order_id),),\n        )\n        if not payment_step:\n            raise ValueError("В этой заявке нет шага подтверждения оплаты.")\n\n        payment = _fetchone_dict(\n            cur,\n            """\n            SELECT\n                id, amount, apartment_id, apartment_number,\n                service_code, service_item_code, currency\n            FROM payments\n            WHERE id = ?\n            """,\n            (int(payment_id),),\n        )\n        if not payment:\n            raise ValueError("Подтверждённый платёж не найден.")\n\n        same_unit = (\n            order.get("apartment_id") is not None\n            and payment.get("apartment_id") is not None\n            and int(order["apartment_id"]) == int(payment["apartment_id"])\n        )\n        if not same_unit:\n            same_unit = (\n                text(order.get("apartment_number"))\n                and text(order.get("apartment_number"))\n                == text(payment.get("apartment_number"))\n            )\n        if not same_unit:\n            raise ValueError("Платёж относится к другой квартире.")\n\n        order_item = text(order.get("service_item_code"))\n        payment_item = text(payment.get("service_item_code"))\n        if order_item and payment_item != order_item:\n            raise ValueError("Платёж относится к другой статье услуги.")\n\n        cur.execute(\n            """\n            SELECT service_order_id\n            FROM service_order_payment_links\n            WHERE payment_id = ?\n              AND service_order_id <> ?\n            LIMIT 1\n            """,\n            (int(payment_id), int(order_id)),\n        )\n        if cur.fetchone():\n            raise ValueError("Этот платёж уже привязан к другой заявке.")\n\n        cur.execute(\n            """\n            SELECT COALESCE(SUM(amount), 0)\n            FROM service_order_payment_links\n            WHERE service_order_id = ?\n            """,\n            (int(order_id),),\n        )\n        linked_before = float(cur.fetchone()[0] or 0)\n        due = order.get("amount_due_snapshot")\n        if due is None:\n            raise ValueError("У заявки нет зафиксированной цены; оплату нельзя закрыть автоматически.")\n        due = float(due)\n        if due < 0:\n            raise ValueError("Некорректная сумма заявки.")\n        remaining = max(0.0, due - linked_before)\n\n        payment_total = float(payment.get("amount") or 0)\n        link_amount = payment_total if amount is None else float(amount)\n        if link_amount <= 0:\n            raise ValueError("Сумма привязки должна быть больше нуля.")\n        if link_amount - payment_total > 0.00001:\n            raise ValueError("Нельзя привязать больше, чем сумма подтверждённого платежа.")\n        if link_amount - remaining > 0.00001:\n            raise ValueError("Нельзя привязать больше, чем остаток по заявке.")\n\n        cur.execute(\n            """\n            SELECT amount\n            FROM service_order_payment_links\n            WHERE service_order_id = ?\n              AND payment_id = ?\n            """,\n            (int(order_id), int(payment_id)),\n        )\n        existing = cur.fetchone()\n        if existing:\n            if abs(float(existing[0] or 0) - link_amount) > 0.00001:\n                raise ValueError("Этот платёж уже привязан к заявке с другой суммой.")\n            result_order = recompute_order_status(cur, int(order_id))\n            if owns:\n                conn.commit()\n            return {\n                "order": result_order,\n                "linked_total": linked_before,\n                "remaining": max(0.0, due - linked_before),\n                "payment_confirmed": text(result_order.get("payment_status")) == "CONFIRMED",\n            }\n\n        cur.execute(\n            """\n            INSERT INTO service_order_payment_links (\n                service_order_id, payment_id, amount, linked_at, linked_by, note\n            )\n            VALUES (?, ?, ?, ?, ?, ?)\n            """,\n            (\n                int(order_id), int(payment_id), link_amount, now_db(),\n                str(actor_id) if actor_id is not None else "system",\n                text(note) or None,\n            ),\n        )\n        linked_after = linked_before + link_amount\n        _event(\n            cur,\n            order_id=order_id,\n            event_type="PAYMENT_LINKED",\n            actor_id=actor_id,\n            source_context="payment_link",\n            details=(\n                f"payment_id={payment_id}; amount={link_amount}; "\n                f"linked_total={linked_after}; due={due}"\n            ),\n        )\n\n        if linked_after + 0.00001 >= due:\n            result_order = confirm_order_step(\n                order_id=order_id,\n                step_code="PAYMENT_CONFIRMED",\n                actor_id=actor_id,\n                note=note,\n                source_context="payment_link",\n                conn=conn,\n            )\n            confirmed = True\n        else:\n            _event(\n                cur,\n                order_id=order_id,\n                event_type="PAYMENT_PARTIALLY_LINKED",\n                actor_id=actor_id,\n                source_context="payment_link",\n                details=(\n                    f"linked_total={linked_after}; due={due}; "\n                    f"remaining={due - linked_after}"\n                ),\n            )\n            result_order = recompute_order_status(cur, int(order_id))\n            confirmed = False\n\n        _audit_order(\n            conn,\n            actor_id=actor_id,\n            action_type="service_order_payment_linked",\n            order_id=order_id,\n            details=(\n                f"payment={payment_id}; linked={link_amount}; "\n                f"total={linked_after}; due={due}; confirmed={confirmed}"\n            ),\n        )\n\n        if owns:\n            conn.commit()\n        return {\n            "order": result_order,\n            "linked_total": linked_after,\n            "remaining": max(0.0, due - linked_after),\n            "payment_confirmed": confirmed,\n        }\n    except Exception:\n        if owns:\n            conn.rollback()\n        raise\n    finally:\n        if owns:\n            conn.close()\n'


def replace_between(source: str, start: str, end: str, replacement: str) -> str:
    left = source.find(start)
    if left < 0:
        raise RuntimeError(f"Не найдено начало: {start}")
    right = source.find(end, left)
    if right < 0:
        raise RuntimeError(f"Не найден конец: {end}")
    return source[:left] + replacement.rstrip() + "\n\n" + source[right:]


def patch_core(source: str) -> tuple[str, bool]:
    if MARKER in source:
        return source, False
    patched = replace_between(
        source,
        "def link_payment_to_order(",
        "def link_charge_to_order(",
        SAFE_PAYMENT_FUNCTION,
    )
    if MARKER not in patched:
        raise RuntimeError("Не вставлен маркер безопасной политики оплаты.")
    return patched, True


def patch_live_launcher(source: str) -> tuple[str, bool]:
    old = "run_bot_guard_sandbox_v3.py"
    new = "run_bot_live_service_sandbox_v4.py"
    if new in source:
        return source, False
    if old not in source:
        raise RuntimeError(
            "В launcher не найден run_bot_guard_sandbox_v3.py. "
            "Автоматически менять неизвестный .bat небезопасно."
        )
    return source.replace(old, new, 1), True


def backup(path: Path, suffix: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    result = path.with_name(f"{path.stem}_{suffix}_{stamp}{path.suffix}")
    shutil.copy2(path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    print("=" * 100)
    print("INSTALL SERVICE ORDERS UI")
    print("=" * 100)
    print("Apply:", args.apply)

    required = [CORE, HANDLER, PORTAL, RUNNER, LIVE_LAUNCHER]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        print("Не найдены обязательные файлы:")
        for path in missing:
            print(" -", path)
        return 1

    for path in (CORE, HANDLER, PORTAL, RUNNER):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
            print("Compile OK:", path.name)
        except Exception as exc:
            print("Compile FAILED:", path.name, exc)
            return 1

    core_original = CORE.read_text(encoding="utf-8")
    launcher_original = LIVE_LAUNCHER.read_text(encoding="utf-8-sig")
    try:
        core_patched, core_changed = patch_core(core_original)
        launcher_patched, launcher_changed = patch_live_launcher(launcher_original)
        compile(core_patched, str(CORE), "exec")
    except Exception as exc:
        print("Patch check FAILED:", exc)
        print("Ни один файл не изменён.")
        return 1

    print("Payment safety patch:", "needed" if core_changed else "already installed")
    print("Live launcher update:", "needed" if launcher_changed else "already installed")

    if not args.apply:
        print("DRY RUN COMPLETED - NO FILES OR DATABASES WERE CHANGED")
        return 0

    if core_changed:
        core_backup = backup(CORE, "before_safe_payment_policy")
        CORE.write_text(core_patched, encoding="utf-8")
        print("Core backup:", core_backup)
        print("Core updated:", CORE)

    if launcher_changed:
        launcher_backup = backup(LIVE_LAUNCHER, "before_service_ui")
        LIVE_LAUNCHER.write_text(launcher_patched, encoding="utf-8-sig")
        print("Launcher backup:", launcher_backup)
        print("Launcher updated:", LIVE_LAUNCHER)

    print("APPLIED")
    print("Bots/parking_bot.py and all databases were not modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
