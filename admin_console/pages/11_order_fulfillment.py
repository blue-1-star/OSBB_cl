"""Order fulfillment overview and operator intake of preliminary demand."""

from __future__ import annotations

from datetime import date
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

STREAMLIT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STREAMLIT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from admin_console.utils.db import get_conn
from cash_claim_points_core import assign_collector, bind_collector_telegram, create_collector_slot, custodian_at, end_collector_assignment, list_claim_points
from service_cash_claims_core import confirm_claim_cash, transfer_collector_cash
from service_interest_intake_core import record_external_interest, record_cash_handover_claim
from resident_identity_core import apartment_telegram_accounts, link_interest_telegram_recipient
from supplier_terms_core import current_supplier_minimum, order_preorder_context, set_supplier_minimum


st.set_page_config(page_title="Исполнение заказов", page_icon="📦", layout="wide")
st.title("📦 Исполнение заказов")
session_actor = st.sidebar.text_input("Оператор этой сессии", key="order_fulfillment_actor")
st.sidebar.caption("Админ-консоль пока без персонального входа. Это имя записывается в журнал действий.")


def table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


conn = get_conn()
try:
    required = {"order_fulfillments", "order_fulfillment_items", "order_fulfillment_events", "service_orders"}
    missing = [name for name in sorted(required) if not table_exists(conn, name)]
    if missing:
        st.error("Общая модель исполнения ещё не подключена: " + ", ".join(missing))
        st.stop()

    st.subheader("📝 Намерения и потребность")
    if table_exists(conn, "service_order_interests"):
        with st.expander("👤 Уполномоченные сборщики"):
            st.caption("Число сборщиков не ограничено. К1–К6 остаются местами передачи со слов жителя; здесь назначаются люди, которые собирают и сверяют деньги.")
            with st.form("new_collector_slot"):
                new_collector_label = st.text_input("Название новой роли / точки", placeholder="Например, представитель 3-го подъезда")
                create_collector = st.form_submit_button("Добавить сборщика")
            if create_collector:
                try:
                    created = create_collector_slot(point_name=new_collector_label, created_by=session_actor)
                    st.success(f"Добавлена точка {created['point_code']}: {created['point_name']}. Теперь назначьте ФИО ниже.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
            collector_slots = [dict(row) for row in conn.execute(
                "SELECT point_code,point_name FROM cash_claim_points "
                "WHERE point_kind='COLLECTOR_SLOT' AND is_active=1 ORDER BY point_code"
            )]
            current = {row["point_code"]: custodian_at(conn, row["point_code"], date.today().isoformat())
                       for row in collector_slots}
            if table_exists(conn, "cash_claim_custodians"):
                assignments = [dict(row) for row in conn.execute(
                    """SELECT point_code,person_name,valid_from,valid_to,assigned_by,note
                       FROM cash_claim_custodians ORDER BY point_code,valid_from DESC"""
                )]
                if assignments:
                    with st.expander("История назначений"):
                        st.dataframe(pd.DataFrame(assignments), hide_index=True, use_container_width=True)
            for slot in collector_slots:
                collector_slot, collector_label = slot["point_code"], slot["point_name"]
                assigned = current[collector_slot]
                if assigned:
                    with st.form(f"collector_telegram_{collector_slot}"):
                        tg_id = st.text_input(
                            f"{collector_label} · {assigned['person_name']} · Telegram ID",
                            value=assigned.get("telegram_user_id") or "",
                            help="Только этот Telegram-пользователь увидит поступления данного назначения в боте.",
                        )
                        bind_submit = st.form_submit_button("Сохранить привязку к боту")
                    if bind_submit:
                        try:
                            bind_collector_telegram(point_code=collector_slot,
                                telegram_user_id=tg_id, actor=session_actor)
                            st.success("Telegram ID сборщика сохранён.")
                            st.rerun()
                        except Exception as exc:
                            st.error(str(exc))
                with st.form(f"collector_{collector_slot}"):
                    name_col, date_col, button_col = st.columns((3, 2, 2))
                    with name_col:
                        collector_name = st.text_input(
                            f"{collector_label} (ФИО)", value=assigned["person_name"] if assigned else "",
                            key=f"collector_name_{collector_slot}",
                        )
                    with date_col:
                        collector_day = st.date_input("Дата начала нового назначения / последний день при снятии", key=f"collector_date_{collector_slot}")
                    with button_col:
                        st.write("")
                        collector_submit = st.form_submit_button("Сохранить")
                if collector_submit:
                    try:
                        if collector_name.strip():
                            if assigned and collector_name.strip() == assigned["person_name"]:
                                st.info("ФИО не изменилось.")
                            else:
                                assign_collector(
                                    point_code=collector_slot, person_name=collector_name,
                                    valid_from=collector_day.isoformat(), assigned_by=session_actor,
                                )
                                st.success(f"{collector_label}: {collector_name.strip()} с {collector_day.isoformat()}.")
                                st.rerun()
                        elif assigned:
                            end_collector_assignment(
                                point_code=collector_slot, last_day=collector_day.isoformat(), ended_by=session_actor,
                            )
                            st.success(f"{collector_label}: полномочия завершены {collector_day.isoformat()}.")
                            st.rerun()
                        else:
                            st.info("ФИО не указано; назначение не менялось.")
                    except Exception as exc:
                        st.error(str(exc))

        with st.expander("➕ Внести сообщение жителя из другого канала"):
            st.caption("Слова о передаче денег сохраняются как сообщение, не как подтверждённый платёж.")
            intake_apartment = st.text_input("Квартира, названная отправителем", key="intake_apartment_lookup")
            unit_matches = [row[0] for row in conn.execute(
                """SELECT id FROM apartments WHERE apartment_number=?
                   AND COALESCE(unit_type,'')<>'TECHNICAL'
                   AND COALESCE(record_status,'')<>'TEST'""", (intake_apartment.strip(),),
            )] if intake_apartment.strip() else []
            candidates = (apartment_telegram_accounts(apartment_id=int(unit_matches[0]),
                           apartment_number=intake_apartment.strip(), conn=conn)
                          if len(unit_matches) == 1 else [])
            accounts_by_id = {str(row["id"]): row for row in candidates}
            account_options = [""] + list(accounts_by_id)
            selected_account = st.selectbox(
                "Telegram-адресат этого сообщения",
                account_options, index=1 if len(candidates) == 1 else 0,
                format_func=lambda key: (
                    "Не определён — не отправлять автоматически" if not key else
                    f"{' '.join(x for x in [accounts_by_id[key]['telegram_first_name'], accounts_by_id[key]['telegram_last_name']] if x) or 'Пользователь'} "
                    f"@{accounts_by_id[key]['telegram_username'] or '—'} · ID {accounts_by_id[key]['telegram_user_id']}"
                ),
                help="Если в квартире несколько пользователей, выберите конкретного отправителя. По одному номеру квартиры адресат не угадывается.",
            )
            if intake_apartment and not candidates:
                st.caption("Для этой квартиры нет подтверждённого Telegram-пользователя; адресата можно добавить позже после регистрации.")
            with st.form("external_service_interest"):
                a1, a2, a3 = st.columns(3)
                with a1:
                    intake_quantity = st.number_input("Количество пультов", min_value=1, value=1, step=1)
                with a2:
                    intake_channel = st.selectbox("Откуда сообщение", ["TELEGRAM", "VIBER", "OTHER_MESSENGER", "PHONE", "PAPER", "OTHER"])
                    intake_sender = st.text_input("Имя / контакт отправителя (если известен)")
                with a3:
                    intake_reference = st.text_input("Ссылка / ID сообщения (если есть)")
                intake_message_date = st.date_input("Дата исходного сообщения", max_value=date.today())
                intake_message = st.text_area("Исходный текст сообщения *", placeholder="Хочу 2 пульта, деньги сдал консьержу")
                intake_claimed_cash = st.checkbox("Отправитель утверждает, что передал деньги")
                claim_points = list_claim_points(conn=conn, include_historical_collectors=True)
                point_labels = {
                    p["point_code"]: f"{p['point_code']} — {p['point_name']}"
                    + (f" ({p['custodian']})" if p["point_code"].startswith("KAS") else "")
                    for p in claim_points
                }
                intake_cashbox = st.selectbox(
                    "Пункт, куда, по словам отправителя, переданы деньги",
                    [""] + list(point_labels),
                    format_func=lambda code: point_labels.get(code, "Не указан"),
                    help="K1–K6 — консьержи, O — охрана, KAS… — назначенные сборщики. Это не подтверждённая оплата.",
                )
                intake_duplicate = st.checkbox("Это отдельное обращение, хотя по квартире уже может быть открыто намерение")
                intake_submit = st.form_submit_button("Записать намерение", type="primary")
            if intake_submit:
                try:
                    created_interest = record_external_interest(
                        apartment_number=intake_apartment, quantity=int(intake_quantity),
                        original_message=intake_message, source_channel=intake_channel,
                        entered_by=session_actor, sender_label=intake_sender,
                        source_reference=intake_reference, claimed_cash_handover=intake_claimed_cash,
                        claimed_cashbox=intake_cashbox, allow_duplicate=intake_duplicate,
                        resident_account_id=int(selected_account) if selected_account else None,
                        auto_resolve_telegram=False,
                        message_received_at=intake_message_date.isoformat(),
                    )
                    st.success(
                        f"Намерение {created_interest['interest_number']} записано. "
                        "Оплата не подтверждена; сверка с кассой выполняется отдельно."
                    )
                except Exception as exc:
                    st.error(str(exc))

        has_intake = table_exists(conn, "service_interest_intake")
        has_claimed_amount = has_intake and any(
            row[1] == "claimed_amount" for row in conn.execute("PRAGMA table_info(service_interest_intake)")
        )
        intake_fields = (
            "x.source_channel, x.original_message, x.sender_label, x.claimed_cash_handover, "
            "x.claimed_cashbox, "
            + ("x.claimed_amount" if has_claimed_amount else "NULL AS claimed_amount")
            + ", x.message_received_at, x.verification_status, x.entered_by"
            if has_intake else
            "NULL AS source_channel, NULL AS original_message, NULL AS sender_label, NULL AS claimed_cash_handover, "
            "NULL AS claimed_cashbox, NULL AS claimed_amount, NULL AS message_received_at, NULL AS verification_status, NULL AS entered_by"
        )
        intake_join = "LEFT JOIN service_interest_intake x ON x.interest_id=i.id" if has_intake else ""
        interest_rows = [dict(row) for row in conn.execute(
            f"""SELECT i.id, i.interest_number, i.apartment_id, i.apartment_number, i.service_item_code,
                       i.service_name_snapshot, i.quantity, i.amount_due_snapshot, i.currency,
                       i.interest_status, i.telegram_user_id, i.payment_notice_number, i.payment_id,
                       i.service_order_id, i.resident_comment, i.created_at,
                       {intake_fields}
                FROM service_order_interests i {intake_join} ORDER BY i.id DESC"""
        )]
        cash_claims = [row for row in interest_rows if row["interest_status"] == "INTEREST"]
        st.markdown("#### 💵 Открытые намерения — оплата")
        st.caption("Выберите любое намерение. Сначала можно записать заявление о передаче денег; кассовая проводка появится только после отдельного подтверждения фактического получения.")
        st.warning("Админ-консоль пока без персонального входа: работайте здесь только локально. Назначенный сборщик может подтверждать свои поступления в боте после привязки Telegram ID.")
        if notice := st.session_state.pop("cash_claim_confirmed_notice", None):
            st.success(notice)
        if cash_claims:
            claim_generation = st.session_state.get("cash_claim_selection_generation", 0)
            claim_selection = st.dataframe(pd.DataFrame([{
                "Намерение": row["interest_number"], "Квартира": row["apartment_number"],
                "Позиция": row["service_name_snapshot"], "Количество": row["quantity"],
                "Заявленная сумма": f"{float(row['amount_due_snapshot']):.2f} {row['currency']}",
                "Состояние оплаты": (
                    "⚠️ принято, сумма расходится" if row["verification_status"] == "AMOUNT_MISMATCH" else
                    "ждёт подтверждения" if row["claimed_cash_handover"] else "не заявлена"
                ),
                "Заявленная передача": (
                    f"{row['claimed_cashbox']} · {float(row['claimed_amount'] if row['claimed_amount'] is not None else row['amount_due_snapshot']):.2f}"
                    if row["claimed_cash_handover"] else "не указана"
                ),
                "Когда заявлено": row["message_received_at"],
                "Источник": row["source_channel"] or "—",
            } for row in cash_claims]), hide_index=True, use_container_width=True,
                on_select="rerun", selection_mode="single-row",
                key=f"cash_claim_selection_{claim_generation}")
            selected_rows = claim_selection.selection.rows
            if selected_rows and 0 <= selected_rows[0] < len(cash_claims):
                claim = cash_claims[selected_rows[0]]
                st.markdown(f"**Намерение {claim['interest_number']} · кв. {claim['apartment_number']}**")
                st.write(f"{claim['quantity']} × {claim['service_name_snapshot']} · к оплате "
                         f"**{float(claim['amount_due_snapshot']):.2f} {claim['currency']}**")
                st.write(f"Сообщение отправителя: {claim['original_message'] or claim['resident_comment'] or '—'}")
                if not claim["claimed_cash_handover"]:
                    st.info("Передача денег ещё не заявлена. Запись ниже не создаёт платёж и заказ.")
                    points = [p for p in list_claim_points(conn=conn) if p["point_kind"] == "COLLECTOR_SLOT" or p["point_code"] == "O"]
                    points_by_code = {p["point_code"]: p for p in points}
                    with st.form(f"stage_cash_claim_{claim['id']}"):
                        stage_actor = st.text_input("Кто записывает заявление *", value=session_actor)
                        stage_point = st.selectbox("Где ожидается получение денег", ["C"] + list(points_by_code),
                            format_func=lambda code: "Центральная касса C" if code == "C" else
                            f"{points_by_code[code]['point_name']} — {points_by_code[code].get('custodian') or 'не назначен'}")
                        stage_amount = st.number_input("Заявленная сумма, UAH", min_value=0.01,
                            value=float(claim["amount_due_snapshot"]), step=1.0, key=f"stage_amount_{claim['id']}")
                        stage_note = st.text_input("Сообщение / основание заявления")
                        stage_submit = st.form_submit_button("Записать заявление о передаче денег")
                    if stage_submit:
                        try:
                            record_cash_handover_claim(interest_id=int(claim["id"]), point_code=stage_point,
                                amount=stage_amount, actor=stage_actor, source_note=stage_note)
                            st.success("Заявление записано. Ожидается фактическое подтверждение получателя.")
                            st.rerun()
                        except Exception as exc:
                            st.error(str(exc))
                elif not claim["payment_id"]:
                    st.write(f"Заявлено: **{claim['claimed_cashbox']}**, "
                             f"**{float(claim['claimed_amount'] if claim['claimed_amount'] is not None else claim['amount_due_snapshot']):.2f} UAH**. "
                             "Это ещё не подтверждённый платёж.")
                    with st.expander("✏️ Исправить место или заявленную сумму до подтверждения"):
                        points = [p for p in list_claim_points(conn=conn)
                                  if p["point_kind"] == "COLLECTOR_SLOT" or p["point_code"] == "O"]
                        labels = {"C": "Центральная касса C"}
                        labels.update({p["point_code"]: p["point_name"] for p in points})
                        with st.form(f"amend_cash_claim_{claim['id']}"):
                            amend_actor = st.text_input("Кто исправляет заявление *", value=session_actor)
                            amend_point = st.selectbox("Пункт передачи", list(labels),
                                index=list(labels).index(claim["claimed_cashbox"]) if claim["claimed_cashbox"] in labels else 0,
                                format_func=lambda code: labels[code])
                            amend_amount = st.number_input("Заявленная сумма, UAH", min_value=0.01,
                                value=float(claim["claimed_amount"] if claim["claimed_amount"] is not None else claim["amount_due_snapshot"]),
                                step=1.0, key=f"amend_claim_amount_{claim['id']}")
                            amend_note = st.text_input("Основание исправления *")
                            amend_submit = st.form_submit_button("Сохранить исправление заявления")
                        if amend_submit:
                            if not amend_note.strip():
                                st.error("Укажите причину исправления ранее записанного заявления.")
                            else:
                                try:
                                    record_cash_handover_claim(interest_id=int(claim["id"]),
                                        point_code=amend_point, amount=amend_amount,
                                        actor=amend_actor, source_note=amend_note)
                                    st.success("Заявление исправлено; платёж по-прежнему не создан.")
                                    st.rerun()
                                except Exception as exc:
                                    st.error(str(exc))
                if claim["payment_id"]:
                    st.warning(f"Получение уже учтено платежом #{claim['payment_id']}; "
                               "если заказ не создан, сумма требует отдельной сверки. Повторный приём здесь запрещён.")
                    receivers = []
                elif not claim["claimed_cash_handover"]:
                    receivers = []
                elif claim["claimed_cashbox"] == "O":
                    receivers = ["O"]
                elif str(claim["claimed_cashbox"] or "").startswith("KAS"):
                    active_codes = {p["point_code"] for p in list_claim_points(conn=conn)
                                    if p["point_kind"] == "COLLECTOR_SLOT"}
                    receivers = ["C"] + ([claim["claimed_cashbox"]] if claim["claimed_cashbox"] in active_codes else [])
                else:
                    receivers = ["C"] + [p["point_code"] for p in list_claim_points(conn=conn)
                                           if p["point_kind"] == "COLLECTOR_SLOT"]
                receiver_labels = {"O": "O — охрана", "C": "C — центральная касса"}
                receiver_labels.update({p["point_code"]: f"{p['point_name']} — {p['custodian']}"
                                        for p in list_claim_points(conn=conn)
                                        if p["point_kind"] == "COLLECTOR_SLOT"})
                if receivers:
                  with st.form(f"confirm_cash_claim_{claim['id']}"):
                    confirming_actor = st.text_input(
                        "Кто подтверждает получение *",
                        value=session_actor,
                        help="Укажите своё имя: оно будет записано в журнал действий."
                    )
                    receiving_point = st.selectbox(
                        "Кто фактически принял деньги", receivers,
                        format_func=lambda code: receiver_labels.get(code, code),
                    )
                    evidence = st.text_input(
                        "Основание фактического приёма *",
                        placeholder="Номер бумажной квитанции, ведомости или акта передачи",
                    )
                    actual_amount = st.number_input(
                        "Фактически получено, UAH", min_value=0.01,
                        value=float(claim["claimed_amount"] if claim["claimed_amount"] is not None else claim["amount_due_snapshot"]),
                        step=1.0, key=f"actual_cash_amount_{claim['id']}",
                    )
                    st.caption("Если сумма отличается от стоимости заказа, деньги будут учтены в кассе, но оплаченный заказ не создастся до отдельной сверки.")
                    actually_received = st.checkbox("Подтверждаю: деньги фактически получены в указанной полной сумме")
                    confirm = st.form_submit_button(
                        "Подтвердить фактический приём", type="primary",
                    )
                  if confirm:
                    if not confirming_actor.strip():
                        st.error("Укажите, кто подтверждает фактическое получение денег.")
                    elif not evidence.strip():
                        st.error("Укажите номер бумажной квитанции, ведомости или акта передачи.")
                    elif not actually_received:
                        st.error("Сначала подтвердите фактическое получение денег.")
                    else:
                        try:
                            result = confirm_claim_cash(
                                interest_id=int(claim["id"]), receiving_point=receiving_point,
                                actor=confirming_actor, evidence=evidence,
                                actual_amount=actual_amount,
                            )
                            st.session_state["cash_claim_confirmed_notice"] = (
                                f"{result['interest_number']}: принято {result['amount']:.2f} UAH "
                                f"в {result['cashbox_code']}; квитанция {result['receipt_number']}; "
                                + (f"заказ {result['order_number']}." if result['order_number'] else
                                   "сумма расходится, заказ не создан — нужна сверка.")
                            )
                            st.session_state["cash_claim_selection_generation"] = claim_generation + 1
                            st.rerun()
                        except Exception as exc:
                            st.error(str(exc))
            else:
                st.caption("Выберите строку заявления, чтобы сверить и подтвердить приём.")
        else:
            st.info("Непроверенных заявлений о передаче денег нет.")
        with st.expander("💼 Остатки мобильных сборщиков и сдача в центральную кассу"):
            mobile_boxes = [dict(row) for row in conn.execute(
                "SELECT cashbox_code,cashbox_name,current_balance FROM cashboxes "
                "WHERE cashbox_code GLOB 'MC[0-9]*' AND is_active=1 AND current_balance>0 "
                "ORDER BY cashbox_code"
            )]
            if transfer_notice := st.session_state.pop("mobile_cash_transfer_notice", None):
                st.success(transfer_notice)
            if mobile_boxes:
                st.dataframe(pd.DataFrame(mobile_boxes), hide_index=True, use_container_width=True)
                boxes_by_code = {row["cashbox_code"]: row for row in mobile_boxes}
                with st.form("mobile_cash_transfer"):
                    selected_box_code = st.selectbox(
                        "Кто сдаёт наличные", list(boxes_by_code),
                        format_func=lambda code: (
                            f"{boxes_by_code[code]['cashbox_name']} · {code} · "
                            f"остаток {boxes_by_code[code]['current_balance']:.2f} UAH"
                        ),
                    )
                    selected_box = boxes_by_code[selected_box_code]
                    transfer_amount = st.number_input("Сумма передачи в C", min_value=0.01,
                                                      max_value=float(selected_box["current_balance"]),
                                                      value=float(selected_box["current_balance"]), step=1.0)
                    transfer_evidence = st.text_input("Номер акта / ведомости передачи *")
                    transfer_confirmed = st.checkbox("Подтверждаю физическую передачу в центральную кассу")
                    transfer_submit = st.form_submit_button("Оформить передачу", disabled=not bool(session_actor.strip()))
                if transfer_submit:
                    if not transfer_confirmed:
                        st.error("Подтвердите физическую передачу денег.")
                    else:
                        try:
                            transfer = transfer_collector_cash(
                                cashbox_code=selected_box["cashbox_code"], amount=transfer_amount,
                                actor=session_actor, evidence=transfer_evidence,
                            )
                            st.session_state["mobile_cash_transfer_notice"] = (
                                f"В C передано {transfer['amount']:.2f} UAH. "
                                f"Остаток сборщика: {transfer['from_balance']:.2f} UAH."
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(str(exc))
            else:
                st.caption("У мобильных сборщиков нет учтённых остатков для передачи.")
        item_codes = sorted({row["service_item_code"] for row in interest_rows})
        if "REMOTE_NEW" not in item_codes and conn.execute(
            "SELECT 1 FROM service_items WHERE service_item_code='REMOTE_NEW'"
        ).fetchone():
            item_codes.insert(0, "REMOTE_NEW")
        if item_codes:
            selected_item = st.selectbox(
                "Позиция для сводки спроса", item_codes,
                index=item_codes.index("REMOTE_NEW") if "REMOTE_NEW" in item_codes else 0,
                key="fulfillment_demand_item",
            )
            selected_interests = [r for r in interest_rows if r["service_item_code"] == selected_item]
            open_interests = [r for r in selected_interests
                              if r["interest_status"] in {"INTEREST", "PAYMENT_NOTICE"}]
            open_quantity = sum(int(r["quantity"] or 0) for r in open_interests)
            paid_unbatched = conn.execute(
                """SELECT COUNT(*) AS orders_count,
                          COALESCE(SUM(CAST(o.quantity AS INTEGER)),0) AS quantity
                   FROM service_orders o
                   JOIN service_order_steps p ON p.service_order_id=o.id
                    AND p.step_code='PAYMENT_CONFIRMED'
                    AND p.step_status IN ('CONFIRMED','WAIVED')
                   WHERE o.service_item_code=?
                    AND o.order_status NOT IN ('COMPLETED','CANCELLED')
                    AND NOT EXISTS (SELECT 1 FROM remote_supplier_batch_links l
                                    WHERE l.service_order_id=o.id)""",
                (selected_item,),
            ).fetchone()
            paid_quantity = int(paid_unbatched["quantity"] or 0)
            demand_quantity = open_quantity + paid_quantity
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Открытых намерений", len(open_interests))
            m2.metric("Пультов по намерениям", open_quantity)
            m3.metric("Оплачено, ещё не в партии", paid_quantity)
            m4.metric("Предварительный спрос", demand_quantity)
            st.caption("Предварительный спрос: намерения + оплаченные заказы вне партии. Слова о передаче денег оплатой не считаются.")
            if selected_interests:
                st.dataframe(pd.DataFrame([{
                    "Намерение": r["interest_number"], "Квартира": r["apartment_number"],
                    "Кол-во": r["quantity"], "Сумма": f"{float(r['amount_due_snapshot']):.2f} {r['currency']}",
                    "Статус": r["interest_status"], "Уведомление об оплате": r["payment_notice_number"] or "—",
                    "Заказ": str(r["service_order_id"]) if r["service_order_id"] else "—", "Получено": r["created_at"],
                    "Источник": r["source_channel"] or "Бот",
                    "Сообщение": r["original_message"] or r["resident_comment"] or "—",
                    "Заявление о передаче": "да" if r["claimed_cash_handover"] else "—",
                    "Оплата": (
                        "подтверждена" if r["payment_id"] and r["service_order_id"]
                        and r["interest_status"] == "PAID_ORDER_CREATED" else
                        "принята, сумма расходится" if r["verification_status"] == "AMOUNT_MISMATCH"
                        and r["payment_id"] else
                        "платёж учтён, заказ не создан" if r["payment_id"] else
                        "ожидает подтверждения" if r["claimed_cash_handover"] else "не подтверждена"
                    ),
                    "Платёж": str(r["payment_id"]) if r["payment_id"] else "—",
                    "Пункт со слов": r["claimed_cashbox"] or "—",
                    "Ответственный на дату сообщения": (
                        (custodian_at(conn, r["claimed_cashbox"], r["message_received_at"] or r["created_at"]) or {}).get("person_name", "—")
                        if str(r["claimed_cashbox"] or "").startswith("KAS") else "—"
                    ),
                    "Внёс": r["entered_by"] or "—",
                } for r in selected_interests]), hide_index=True, use_container_width=True)
            else:
                st.info("Намерений по этой позиции ещё нет.")

            term = current_supplier_minimum(selected_item, conn=conn)
            minimum = int(term["minimum_quantity"]) if term else None
            st.markdown("#### Условие поставщика — минимальная партия")
            if minimum:
                st.write(f"Действующий минимум: **{minimum} шт.** · установлен {term['valid_from'][:10]}.")
                shortfall = max(minimum - paid_quantity, 0)
                if shortfall:
                    st.info(f"Оплачено {paid_quantity} из {minimum} шт.; до минимума не хватает {shortfall} шт. "
                            f"Неподтверждённые намерения ({open_quantity} шт.) в оплаченный пакет не входят.")
                else:
                    st.success(f"Оплачено {paid_quantity} шт. — минимум {minimum} достигнут. "
                               "Пакет предзаказов готов к оформлению поставщику, но ещё не является заказом поставщику.")
                announcement = (
                    f"📦 Нові пульти: зафіксовано попередній попит на {demand_quantity} шт. "
                    f"(намірів без підтвердженої оплати — {open_quantity} шт.; "
                    f"оплачених замовлень поза партією — {paid_quantity} шт.). "
                    f"Мінімальна партія постачальника — {minimum} шт. "
                    + (f"До оплаченої партії бракує {shortfall} шт. Продовжуємо збір заявок."
                       if shortfall else "Оплачений пакет досяг мінімуму; замовлення постачальнику ще не оформлене.")
                )
                st.markdown("**Черновик объявления для копирования**")
                st.code(announcement, language=None)
                st.caption("Объявление автоматически не публикуется.")
            else:
                st.info("Минимальная партия ещё не задана для этой позиции.")
            no_telegram = [r for r in selected_interests
                           if selected_item == "REMOTE_NEW"
                           if r["interest_status"] == "PAID_ORDER_CREATED"
                           and r["service_order_id"] and not r["telegram_user_id"]]
            if no_telegram:
                with st.expander(f"📨 Подтверждения для отправки вручную ({len(no_telegram)})"):
                    st.caption("У этих заказчиков нет Telegram ID в записи намерения; бот не может отправить им сообщение сам.")
                    for row in no_telegram:
                        st.write(f"Кв. {row['apartment_number']} · {row['sender_label'] or 'контакт не указан'}")
                        possible_accounts = apartment_telegram_accounts(
                            apartment_id=int(row["apartment_id"]),
                            apartment_number=str(row["apartment_number"]), conn=conn,
                        )
                        if possible_accounts:
                            account_by_id = {str(account["id"]): account for account in possible_accounts}
                            with st.form(f"link_interest_recipient_{row['id']}"):
                                account_id = st.selectbox(
                                    "Привязать заказ к конкретному Telegram-пользователю",
                                    [""] + list(account_by_id),
                                    index=1 if len(possible_accounts) == 1 else 0,
                                    format_func=lambda key: "Не выбран" if not key else
                                    f"{account_by_id[key]['telegram_first_name'] or 'Пользователь'} · ID {account_by_id[key]['telegram_user_id']}",
                                )
                                link_actor = st.text_input("Кто подтверждает адресата *", value=session_actor)
                                link_submit = st.form_submit_button("Сохранить адресата и подготовить уведомление")
                            if link_submit:
                                try:
                                    if not account_id:
                                        raise ValueError("Выберите конкретного пользователя этой квартиры.")
                                    link_interest_telegram_recipient(
                                        interest_id=int(row["id"]), resident_account_id=int(account_id),
                                        actor=link_actor,
                                    )
                                    st.success("Адресат сохранён; уведомление поставлено в очередь бота.")
                                    st.rerun()
                                except Exception as exc:
                                    st.error(str(exc))
                        context = order_preorder_context(int(row["service_order_id"]), conn=conn)
                        manual_text = (
                            f"✅ Оплату за нові пульти підтверджено. Кв. {row['apartment_number']}: "
                            f"{int(row['quantity'])} шт., отримано {float(row['amount_due_snapshot']):.2f} грн. "
                            + (f"Партія постачальника: {context['batch_number']}."
                               if context["batch_number"] else
                               f"Оплачений пакет зараз — {context['quantity']} із мінімальних {context['minimum']} шт. "
                               "Замовлення постачальнику ще не оформлено."
                               if context["minimum"] else
                               "Мінімальна партія постачальника ще уточнюється.")
                        )
                        st.code(manual_text, language=None)
            if table_exists(conn, "service_order_notifications"):
                notice_rows = [dict(row) for row in conn.execute(
                    """SELECT n.service_order_id,n.delivery_status,n.sent_at,n.delivery_error
                       FROM service_order_notifications n
                       JOIN service_orders o ON o.id=n.service_order_id
                       WHERE o.service_item_code=? AND n.notification_kind='PAYMENT_CONFIRMED'
                       ORDER BY n.id DESC LIMIT 30""", (selected_item,),
                )]
                if notice_rows:
                    with st.expander("📨 Доставка подтверждений в бот"):
                        st.dataframe(pd.DataFrame(notice_rows), hide_index=True, use_container_width=True)
            with st.expander("✏️ Изменить условие поставщика"):
                with st.form(f"supplier_minimum_{selected_item}"):
                    new_minimum = st.number_input("Минимум, шт.", min_value=1,
                        value=minimum or 1, step=1)
                    minimum_actor = st.text_input("Кто меняет условие *", value=session_actor)
                    minimum_reason = st.text_input("Основание изменения *",
                        placeholder="Например, новые условия поставщика")
                    minimum_submit = st.form_submit_button("Сохранить минимум")
                if minimum_submit:
                    try:
                        set_supplier_minimum(service_item_code=selected_item,
                            minimum_quantity=int(new_minimum), actor=minimum_actor,
                            reason=minimum_reason)
                        st.success("Условие сохранено с историей изменений.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
                if table_exists(conn, "service_supplier_minimum_history"):
                    history = [dict(row) for row in conn.execute(
                        """SELECT minimum_quantity,valid_from,valid_to,changed_by,reason
                           FROM service_supplier_minimum_history WHERE service_item_code=?
                           ORDER BY id DESC""", (selected_item,),
                    )]
                    if history:
                        st.dataframe(pd.DataFrame(history), hide_index=True, use_container_width=True)
        else:
            st.info("Позиции услуг и намерения пока отсутствуют.")
    else:
        st.info("Таблица намерений ещё не создана.")

    st.subheader("📦 Остатки и передачи")
    if table_exists(conn, "inventory_balances"):
        balances = [dict(row) for row in conn.execute(
            """SELECT l.source_id AS batch_id, s.batch_number, b.location_code,
                      b.quantity, l.service_item_code
               FROM inventory_balances b JOIN inventory_lots l ON l.id=b.lot_id
               LEFT JOIN remote_supplier_batches s ON s.id=l.source_id
               WHERE b.quantity>0 ORDER BY b.location_code, s.batch_number"""
        )]
        pending_transfers = [dict(row) for row in conn.execute(
            """SELECT t.id, s.batch_number, t.from_location_code, t.to_location_code,
                      t.quantity, t.transfer_status, t.reported_quantity,
                      t.reported_by, t.reported_at, t.note, t.sent_by, t.sent_at
               FROM inventory_transfers t JOIN inventory_lots l ON l.id=t.lot_id
               LEFT JOIN remote_supplier_batches s ON s.id=l.source_id
               WHERE t.transfer_status IN ('SENT','DISPUTED') ORDER BY t.id DESC"""
        )]
        st.caption("ЦС — центральный склад; O — охрана; K — консьерж. В пути — списано у отправителя, но не принято получателем.")
        if balances:
            st.dataframe(pd.DataFrame(balances), hide_index=True, use_container_width=True)
        else:
            st.info("Учтённых остатков пока нет.")
        st.markdown("**В пути и с расхождениями**")
        if pending_transfers:
            st.dataframe(pd.DataFrame(pending_transfers), hide_index=True, use_container_width=True)
        else:
            st.caption("Передач в пути нет.")
    else:
        st.info("Учёт пунктов передачи ещё не подключён. Примените миграцию 022.")

    rows = conn.execute(
        """
        SELECT f.id, f.fulfillment_number, f.fulfillment_kind, f.fulfillment_status,
               f.source_location_code, f.pickup_point_code,
               f.recipient_apartment_number, f.planned_quantity,
               f.created_at, f.updated_at, f.prepared_at, f.handed_over_at,
               f.receipt_confirmed_at,
               o.order_number, o.service_name_snapshot, o.service_item_code,
               o.order_status, o.payment_status, o.fulfillment_status AS order_fulfillment_status,
               COUNT(i.id) AS item_count
        FROM order_fulfillments f
        JOIN service_orders o ON o.id=f.service_order_id
        LEFT JOIN order_fulfillment_items i ON i.fulfillment_id=f.id
        GROUP BY f.id
        ORDER BY f.updated_at DESC, f.id DESC
        """
    ).fetchall()
    data = [dict(row) for row in rows]

    total = len(data)
    active = sum(item["fulfillment_status"] not in {"CANCELLED", "RECEIPT_CONFIRMED"} for item in data)
    physical = sum(item["fulfillment_kind"] == "PHYSICAL_ITEM" for item in data)
    ready = sum(item["fulfillment_status"] == "READY_FOR_PICKUP" for item in data)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Всего исполнений", total)
    col2.metric("Открытые", active)
    col3.metric("Физические предметы", physical)
    col4.metric("Готовы к выдаче", ready)

    if not data:
        st.info("Исполнений заказов пока нет.")
        st.stop()

    statuses = ["Все"] + sorted({str(item["fulfillment_status"]) for item in data})
    kinds = ["Все"] + sorted({str(item["fulfillment_kind"]) for item in data})
    filter_col1, filter_col2 = st.columns(2)
    with filter_col1:
        status_filter = st.selectbox("Статус исполнения", statuses)
    with filter_col2:
        kind_filter = st.selectbox("Вид исполнения", kinds)
    filtered = [
        item for item in data
        if (status_filter == "Все" or item["fulfillment_status"] == status_filter)
        and (kind_filter == "Все" or item["fulfillment_kind"] == kind_filter)
    ]

    display = pd.DataFrame([
        {
            "Исполнение": item["fulfillment_number"],
            "Заказ": item["order_number"],
            "Услуга": item["service_name_snapshot"],
            "Квартира": item["recipient_apartment_number"] or "—",
            "Вид": item["fulfillment_kind"],
            "Статус": item["fulfillment_status"],
            "Точка выдачи": item["pickup_point_code"] or "—",
            "Предметов": item["item_count"],
        }
        for item in filtered
    ])
    st.dataframe(display, hide_index=True, use_container_width=True)

    if not filtered:
        st.info("По выбранному фильтру исполнений нет.")
        st.stop()

    options = {int(item["id"]): f"{item['fulfillment_number']} · {item['order_number']} · {item['fulfillment_status']}" for item in filtered}
    selected_id = st.selectbox("Открыть исполнение", list(options), format_func=options.get)
    selected = next(item for item in filtered if int(item["id"]) == int(selected_id))

    st.markdown(f"## {selected['fulfillment_number']} · {selected['service_name_snapshot']}")
    info_col1, info_col2, info_col3 = st.columns(3)
    info_col1.write(f"**Заказ:** {selected['order_number']}")
    info_col1.write(f"**Квартира:** {selected['recipient_apartment_number'] or '—'}")
    info_col2.write(f"**Вид:** {selected['fulfillment_kind']}")
    info_col2.write(f"**Статус:** {selected['fulfillment_status']}")
    info_col3.write(f"**Источник:** {selected['source_location_code'] or '—'}")
    info_col3.write(f"**Точка выдачи:** {selected['pickup_point_code'] or 'не назначена'}")

    st.markdown("### Позиции исполнения")
    items = conn.execute(
        """
        SELECT i.id, i.item_kind, i.item_label, i.quantity, i.item_status,
               ra.asset_number, ra.serial_number, ra.inventory_status, ra.ownership_type
        FROM order_fulfillment_items i
        LEFT JOIN remote_assets ra ON i.item_kind='REMOTE_ASSET' AND ra.id=i.external_asset_id
        WHERE i.fulfillment_id=? ORDER BY i.id
        """,
        (int(selected_id),),
    ).fetchall()
    if items:
        st.dataframe(pd.DataFrame([{
            "Тип": row["item_kind"],
            "Предмет": row["item_label"],
            "Серийный номер": row["serial_number"] or "—",
            "Статус позиции": row["item_status"],
            "Статус инвентаря": row["inventory_status"] or "—",
            "Владелец": row["ownership_type"] or "—",
        } for row in items]), hide_index=True, use_container_width=True)
    else:
        st.info("Конкретные предметы ещё не назначены. Для услуги это может быть нормальным состоянием.")

    st.markdown("### Журнал исполнения")
    events = conn.execute(
        """
        SELECT e.created_at, e.event_code, e.from_status, e.to_status, e.actor_id, e.note,
               i.item_label
        FROM order_fulfillment_events e
        LEFT JOIN order_fulfillment_items i ON i.id=e.fulfillment_item_id
        WHERE e.fulfillment_id=? ORDER BY e.id
        """,
        (int(selected_id),),
    ).fetchall()
    st.dataframe(pd.DataFrame([{
        "Когда": row["created_at"],
        "Событие": row["event_code"],
        "Было": row["from_status"] or "—",
        "Стало": row["to_status"] or "—",
        "Исполнитель": row["actor_id"] or "—",
        "Предмет": row["item_label"] or "—",
        "Комментарий": row["note"] or "—",
    } for row in events]), hide_index=True, use_container_width=True)

    with st.expander("Техническая история адаптера пульта"):
        remote_rows = conn.execute(
            """
            SELECT m.created_at, ra.asset_number, m.movement_type, m.from_state, m.to_state,
                   m.post_code, m.actor_id, m.note
            FROM order_fulfillment_items i
            JOIN remote_asset_movements m ON m.remote_asset_id=i.external_asset_id
            LEFT JOIN remote_assets ra ON ra.id=m.remote_asset_id
            WHERE i.fulfillment_id=? AND i.item_kind='REMOTE_ASSET'
            ORDER BY m.id
            """,
            (int(selected_id),),
        ).fetchall()
        if remote_rows:
            st.dataframe(pd.DataFrame([dict(row) for row in remote_rows]), hide_index=True, use_container_width=True)
        else:
            st.caption("Для этого исполнения нет технических движений пульта.")

    st.info(
        "Этот экран пока не создаёт новую параллельную выдачу. Следующим шагом операции "
        "«на точку выдачи → выдано → получено» будут переведены на единый переход "
        "статусов и станут доступны здесь и в боте одинаково."
    )
finally:
    conn.close()
