"""Confirm a reported cash handover against one specific service interest.

The claim is not a payment. Explicit confirmation creates a cash receipt and
payment; a paid order is created only when the received amount covers its price.
"""

from __future__ import annotations

from datetime import date
import sqlite3
from uuid import uuid4

from audit_logger import audit_log
from cash_claim_points_core import active_collector_for_telegram, custodian_at
from cashier_v2_core import calc_cashbox_balance, create_cash_receipt, insert_dynamic
from service_orders_core import create_service_order, get_conn, link_payment_to_order, text
from service_preorders_core import PAID_ORDER_CREATED, get_service_interest, now_db


def confirm_claim_cash(*, interest_id: int, receiving_point: str, actor: str,
                       evidence: str, actual_amount: float | None = None,
                       collector_telegram_id: int | None = None,
                       conn: sqlite3.Connection | None = None) -> dict:
    """Book actual cash and promote the interest only on an exact-price match.

    A mobile collector uses a dedicated MC<assignment-id> balance. The caller
    must authenticate/authorize the actor before invoking this writer.
    """
    actor, evidence = text(actor), text(evidence)
    receiving_point = text(receiving_point).upper()
    if not actor or not evidence:
        raise ValueError("Укажите исполнителя и документ/основание фактического приёма денег.")
    owns = conn is None
    conn = conn or get_conn()
    try:
        conn.row_factory = sqlite3.Row
        if owns:
            conn.execute("BEGIN IMMEDIATE")
        interest = get_service_interest(int(interest_id), conn=conn)
        claim = conn.execute(
            "SELECT * FROM service_interest_intake WHERE interest_id=?", (int(interest_id),)
        ).fetchone()
        if not claim or not int(claim["claimed_cash_handover"] or 0):
            raise ValueError("У этого намерения нет заявления о передаче наличных.")
        if claim["verification_status"] != "UNVERIFIED" or interest["interest_status"] != "INTEREST":
            raise ValueError("Заявление уже обработано или намерение более не открыто.")
        if interest.get("payment_id") or interest.get("service_order_id"):
            raise ValueError("У намерения уже есть платёж или заказ.")
        due = float(interest["amount_due_snapshot"] or 0)
        if due <= 0 or interest["currency"] != "UAH":
            raise ValueError("Для автоматического подтверждения нужна положительная цена в UAH.")
        claimed_amount = float(claim["claimed_amount"] if "claimed_amount" in claim.keys()
                               and claim["claimed_amount"] is not None else due)
        amount = round(float(actual_amount if actual_amount is not None else claimed_amount), 2)
        if amount <= 0:
            raise ValueError("Фактически полученная сумма должна быть положительной.")
        today = date.today().isoformat()
        claimed_point = text(claim["claimed_cashbox"]).upper()
        if claimed_point.startswith("KAS") and receiving_point not in {claimed_point, "C"}:
            raise ValueError("Заявлена передача другому сборщику; подтвердите её у указанного лица или в C.")
        if receiving_point == "O":
            if claimed_point != "O":
                raise ValueError("На посту O можно подтвердить только заявление о передаче в O.")
            cashbox = "O"
            custodian_name = "Пост охраны O"
        elif receiving_point == "C":
            cashbox = "C"
            custodian_name = "Центральная касса"
        elif receiving_point.startswith("KAS"):
            assignment = custodian_at(conn, receiving_point, today)
            if not assignment:
                raise ValueError("На сегодня нет действующего назначения этого сборщика.")
            if collector_telegram_id is not None:
                bound = active_collector_for_telegram(collector_telegram_id, conn=conn)
                if not bound or int(bound["id"]) != int(assignment["id"]):
                    raise ValueError("Telegram ID не совпадает с действующим назначением сборщика.")
            cashbox = f"MC{int(assignment['id'])}"
            custodian_name = assignment["person_name"]
            conn.execute(
                """INSERT OR IGNORE INTO cashboxes
                   (cashbox_code,cashbox_name,currency,initial_balance,current_balance,is_active,comment)
                   VALUES (?,?, 'UAH',0,0,1,?)""",
                (cashbox, f"Мобильный сборщик: {custodian_name}",
                 f"Назначение #{assignment['id']} в {receiving_point}"),
            )
        else:
            raise ValueError("Фактический приём возможен в O, C или у назначенного сборщика KAS…")
        if collector_telegram_id is not None and not receiving_point.startswith("KAS"):
            raise ValueError("Мобильный сборщик может подтвердить только своё назначение KAS.")

        # A matching unlinked cashier payment may already represent this cash.
        # Do not silently create a second receipt for the same reported handover.
        prior = conn.execute(
            """SELECT id FROM payments WHERE apartment_id=? AND service_item_code=?
                 AND amount BETWEEN ? AND ? AND payment_date>=?
                 AND COALESCE(cashier_entry_status,'CONFIRMED')='CONFIRMED'
                 AND NOT EXISTS (SELECT 1 FROM service_order_payment_links l WHERE l.payment_id=payments.id)
                 ORDER BY id DESC LIMIT 1""",
            (interest["apartment_id"], interest["service_item_code"], due - 0.005, due + 0.005,
             str(claim["message_received_at"])[:10]),
        ).fetchone()
        if prior:
            raise ValueError(f"Найден похожий уже учтённый платёж #{prior['id']}; проверьте его перед новым приёмом.")
        unit = conn.execute("SELECT id,apartment_number FROM apartments WHERE id=?",
                            (interest["apartment_id"],)).fetchone()
        if not unit:
            raise ValueError("Квартира намерения не найдена.")
        source = (f"Намерение {interest['interest_number']}; заявлено: {claimed_point}; "
                  f"фактически принял: {custodian_name}; основание: {evidence}")
        receipt = create_cash_receipt(
            conn.cursor(), apartment=dict(unit), cashbox_code=cashbox,
            receipt_date=today, period_code=None,
            service={"service_code": interest["service_code"] or interest["service_item_code"],
                     "service_item_code": interest["service_item_code"], "service_type": "ONE_TIME"},
            amount=amount, source_text=source, operator_id=actor,
            origin_kind="EXTERNAL_SERVICE_INTEREST",
            resident_account_id=interest["resident_account_id"],
        )
        if abs(amount - due) > 0.005:
            conn.execute(
                """UPDATE service_order_interests SET payment_id=?,updated_at=? WHERE id=?""",
                (int(receipt["payment_id"]), now_db(), int(interest_id)),
            )
            conn.execute(
                """UPDATE service_interest_intake SET verification_status='AMOUNT_MISMATCH'
                   WHERE interest_id=? AND verification_status='UNVERIFIED'""", (int(interest_id),),
            )
            audit_log(
                conn=conn, operator_id=actor, user_id=actor, actor_type="operator",
                action_type="service_interest_cash_amount_mismatch",
                table_name="service_order_interests", row_id=int(interest_id),
                field_name="payment_id", old_value="", new_value=str(receipt["payment_id"]),
                source_context="service_cash_claims_core",
                comment=f"Получено {amount:.2f}, заявлено {claimed_amount:.2f}, к оплате {due:.2f}; заказ не создан. {source}",
                commit=False,
            )
            if owns:
                conn.commit()
            return {"interest_number": interest["interest_number"],
                    "receipt_number": receipt["receipt_number"], "payment_id": receipt["payment_id"],
                    "order_number": None, "cashbox_code": cashbox, "amount": amount,
                    "amount_mismatch": True}
        order = create_service_order(
            resident_account_id=interest["resident_account_id"],
            telegram_user_id=interest["telegram_user_id"],
            apartment_id=interest["apartment_id"],
            apartment_number=interest["apartment_number"],
            service_item_code=interest["service_item_code"], quantity=float(interest["quantity"]),
            resident_comment=interest.get("resident_comment") or "",
            service_name_snapshot_override=interest["service_name_snapshot"],
            unit_price_snapshot_override=float(interest["unit_price_snapshot"]),
            amount_due_snapshot_override=due,
            currency_snapshot_override=interest["currency"],
            actor_id=None, source_context=f"confirmed_claim:{interest['interest_number']}",
            existing_interest_id=int(interest_id), conn=conn,
        )
        linked = link_payment_to_order(
            order_id=int(order["id"]), payment_id=int(receipt["payment_id"]),
            amount=due, actor_id=None, note=f"Наличные по {interest['interest_number']}", conn=conn,
        )
        from phone_barrier_access_service import promote_paid_phone_barrier_access_interest
        promote_paid_phone_barrier_access_interest(
            interest=interest, order=linked["order"], payment_id=int(receipt["payment_id"]), conn=conn,
        )
        conn.execute(
            """UPDATE service_order_interests SET interest_status=?, payment_id=?,
               service_order_id=?, paid_at=?, updated_at=? WHERE id=? AND interest_status='INTEREST'""",
            (PAID_ORDER_CREATED, int(receipt["payment_id"]), int(order["id"]), now_db(), now_db(), int(interest_id)),
        )
        conn.execute(
            """UPDATE service_interest_intake SET verification_status='CONFIRMED'
               WHERE interest_id=? AND verification_status='UNVERIFIED'""",
            (int(interest_id),),
        )
        from service_order_notifications import enqueue_paid_order_confirmation
        enqueue_paid_order_confirmation(int(order["id"]), conn=conn)
        audit_log(
            conn=conn, operator_id=actor, user_id=actor, actor_type="operator",
            action_type="service_interest_cash_claim_confirmed",
            table_name="service_order_interests", row_id=int(interest_id),
            field_name="interest_status,payment_id", old_value="INTEREST",
            new_value=f"{PAID_ORDER_CREATED},{receipt['payment_id']}",
            source_context="service_cash_claims_core", comment=source, commit=False,
        )
        if owns:
            conn.commit()
        return {"interest_number": interest["interest_number"], "receipt_number": receipt["receipt_number"],
                "payment_id": receipt["payment_id"], "order_number": linked["order"]["order_number"],
                "cashbox_code": cashbox, "amount": due}
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()


def transfer_collector_cash(*, cashbox_code: str, amount: float, actor: str,
                            evidence: str, conn: sqlite3.Connection | None = None) -> dict:
    """Move physically surrendered cash from one mobile balance to central C."""
    cashbox_code, actor, evidence = text(cashbox_code).upper(), text(actor), text(evidence)
    amount = round(float(amount), 2)
    if not cashbox_code.startswith("MC") or not cashbox_code[2:].isdigit():
        raise ValueError("Выберите баланс мобильного сборщика.")
    if amount <= 0 or not actor or not evidence:
        raise ValueError("Нужны положительная сумма, исполнитель и номер документа передачи.")
    owns = conn is None
    conn = conn or get_conn()
    try:
        conn.row_factory = sqlite3.Row
        if owns:
            conn.execute("BEGIN IMMEDIATE")
        for code in (cashbox_code, "C"):
            row = conn.execute("SELECT is_active FROM cashboxes WHERE cashbox_code=?", (code,)).fetchone()
            if not row or not int(row["is_active"]):
                raise ValueError(f"Касса {code} не действует.")
        if conn.execute(
            """SELECT 1 FROM cashbox_operations WHERE source_type='mobile_collector_transfer'
               AND cashbox_code=? AND source_ref=? LIMIT 1""", (cashbox_code, evidence),
        ).fetchone():
            raise ValueError("Передача с таким номером документа уже зарегистрирована.")
        balance = calc_cashbox_balance(conn.cursor(), cashbox_code)
        if amount - balance > 0.00001:
            raise ValueError(f"Недостаточно денег у сборщика: учтённый остаток {balance:.2f} UAH.")
        ref = f"MCT-{uuid4().hex.upper()}"
        for code, direction, kind in ((cashbox_code, "out", "cash_transfer_out"),
                                      ("C", "in", "cash_transfer_in")):
            insert_dynamic(conn.cursor(), "cashbox_operations", {
                "operation_date": date.today().isoformat(), "cashbox_code": code,
                "operation_type": kind, "direction": direction, "amount": amount,
                "currency": "UAH", "service_code": "CASH_TRANSFER",
                "base_service_code": "CASH_TRANSFER", "service_type": "INTERNAL",
                "source_type": "mobile_collector_transfer", "source_ref": evidence,
                "transfer_group_ref": ref, "operator_id": actor, "actor_type": "operator",
                "comment": f"{cashbox_code} → C; документ {evidence}", "created_at": now_db(),
            })
        from_balance = calc_cashbox_balance(conn.cursor(), cashbox_code)
        central_balance = calc_cashbox_balance(conn.cursor(), "C")
        audit_log(
            conn=conn, operator_id=actor, user_id=actor, actor_type="operator",
            action_type="mobile_collector_cash_transferred", table_name="cashbox_operations",
            row_id=ref, field_name="cashbox_code,amount", old_value=cashbox_code,
            new_value=f"C,{amount:.2f}", source_context="service_cash_claims_core",
            comment=f"Передача по документу {evidence}", commit=False,
        )
        if owns:
            conn.commit()
        return {"transfer_ref": ref, "from_balance": from_balance,
                "central_balance": central_balance, "amount": amount}
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns:
            conn.close()
