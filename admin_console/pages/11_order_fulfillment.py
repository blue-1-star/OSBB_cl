"""Read-only operator view of the canonical OSBB order-fulfillment model."""

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
from cash_claim_points_core import assign_collector, custodian_at, end_collector_assignment, list_claim_points
from service_interest_intake_core import record_external_interest


st.set_page_config(page_title="Исполнение заказов", page_icon="📦", layout="wide")
st.title("📦 Исполнение заказов")
st.caption(
    "Единый контур: предмет, цифровой доступ или работа. Пульты отображаются как первый "
    "адаптер физического предмета; старые remote_* таблицы показаны только как техническая история."
)


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
        with st.expander("👤 Кто уполномочен принимать деньги в ячейках И1/И2"):
            st.caption(
                "И1 и И2 — постоянные коды ячеек; ФИО назначенного человека меняется по датам. "
                "Назначение здесь не создаёт кассовую операцию."
            )
            if table_exists(conn, "cash_claim_custodians"):
                assignments = [dict(row) for row in conn.execute(
                    """SELECT point_code,person_name,valid_from,valid_to,assigned_by,note
                       FROM cash_claim_custodians ORDER BY point_code,valid_from DESC"""
                )]
                if assignments:
                    st.dataframe(pd.DataFrame(assignments), hide_index=True, use_container_width=True)
                else:
                    st.info("Для И1/И2 пока никто не назначен; эти ячейки недоступны для новых заявлений.")
            with st.form("assign_cash_collector"):
                slot_col, name_col, date_col = st.columns(3)
                with slot_col:
                    collector_slot = st.selectbox("Ячейка", ["I1", "I2"])
                with name_col:
                    collector_name = st.text_input("ФИО уполномоченного *")
                with date_col:
                    collector_from = st.date_input("Действует с")
                collector_actor = st.text_input("Кто внёс назначение *")
                collector_note = st.text_input("Основание / примечание")
                collector_submit = st.form_submit_button("Сохранить назначение")
            if collector_submit:
                try:
                    saved_assignment = assign_collector(
                        point_code=collector_slot, person_name=collector_name,
                        valid_from=collector_from.isoformat(), assigned_by=collector_actor,
                        note=collector_note,
                    )
                    st.success(
                        f"{saved_assignment['point_code']}: {saved_assignment['person_name']} "
                        f"с {saved_assignment['valid_from']}. Предыдущее назначение завершено накануне."
                    )
                except Exception as exc:
                    st.error(str(exc))
            with st.form("end_cash_collector"):
                st.caption("Если уполномоченный ушёл без замены, закройте назначение: после указанной даты ячейка исчезнет из выбора.")
                end_col1, end_col2 = st.columns(2)
                with end_col1:
                    end_slot = st.selectbox("Освободить ячейку", ["I1", "I2"])
                with end_col2:
                    end_last_day = st.date_input("Последний день полномочий")
                end_actor = st.text_input("Кто прекратил полномочия *")
                end_submit = st.form_submit_button("Закрыть назначение")
            if end_submit:
                try:
                    ended = end_collector_assignment(
                        point_code=end_slot, last_day=end_last_day.isoformat(), ended_by=end_actor,
                    )
                    st.success(f"Назначение {ended['person_name']} в {end_slot} завершено {ended['valid_to']}.")
                except Exception as exc:
                    st.error(str(exc))

        with st.expander("➕ Внести сообщение жителя без регистрации в боте"):
            st.caption(
                "Оператор переносит исходное сообщение в намерение. Квартира пока указана со слов отправителя. "
                "Фраза «деньги сдал» сохраняется как заявление, но не создаёт платёж и не подтверждает кассу."
            )
            with st.form("external_service_interest"):
                a1, a2, a3 = st.columns(3)
                with a1:
                    intake_apartment = st.text_input("Квартира, названная отправителем")
                    intake_quantity = st.number_input("Количество пультов", min_value=1, value=1, step=1)
                with a2:
                    intake_channel = st.selectbox("Откуда сообщение", ["TELEGRAM", "VIBER", "OTHER_MESSENGER", "PHONE", "PAPER", "OTHER"])
                    intake_sender = st.text_input("Имя / контакт отправителя (если известен)")
                with a3:
                    intake_operator = st.text_input("Кто внёс сообщение *")
                    intake_reference = st.text_input("Ссылка / ID сообщения (если есть)")
                intake_message_date = st.date_input("Дата исходного сообщения", max_value=date.today())
                intake_message = st.text_area("Исходный текст сообщения *", placeholder="Хочу 2 пульта, деньги сдал консьержу")
                intake_claimed_cash = st.checkbox("Отправитель утверждает, что передал деньги")
                claim_points = list_claim_points(conn=conn, include_historical_collectors=True)
                point_labels = {
                    p["point_code"]: f"{p['point_code']} — {p['point_name']}"
                    + (" (ФИО проверяется по дате сообщения)"
                       if p["point_code"].startswith("I") else "")
                    for p in claim_points
                }
                intake_cashbox = st.selectbox(
                    "Пункт, куда, по словам отправителя, переданы деньги",
                    [""] + list(point_labels),
                    format_func=lambda code: point_labels.get(code, "Не указан"),
                    help="K1–K6, O; И1/И2 доступны только после назначения уполномоченного. Это ещё не подтверждённая оплата.",
                )
                intake_duplicate = st.checkbox("Это отдельное обращение, хотя по квартире уже может быть открыто намерение")
                intake_submit = st.form_submit_button("Записать намерение", type="primary")
            if intake_submit:
                try:
                    created_interest = record_external_interest(
                        apartment_number=intake_apartment, quantity=int(intake_quantity),
                        original_message=intake_message, source_channel=intake_channel,
                        entered_by=intake_operator, sender_label=intake_sender,
                        source_reference=intake_reference, claimed_cash_handover=intake_claimed_cash,
                        claimed_cashbox=intake_cashbox, allow_duplicate=intake_duplicate,
                        message_received_at=intake_message_date.isoformat(),
                    )
                    st.success(
                        f"Намерение {created_interest['interest_number']} записано. "
                        "Оплата не подтверждена; сверка с кассой выполняется отдельно."
                    )
                except Exception as exc:
                    st.error(str(exc))

        has_intake = table_exists(conn, "service_interest_intake")
        intake_fields = (
            "x.source_channel, x.original_message, x.claimed_cash_handover, "
            "x.claimed_cashbox, x.message_received_at, x.verification_status, x.entered_by"
            if has_intake else
            "NULL AS source_channel, NULL AS original_message, NULL AS claimed_cash_handover, "
            "NULL AS claimed_cashbox, NULL AS message_received_at, NULL AS verification_status, NULL AS entered_by"
        )
        intake_join = "LEFT JOIN service_interest_intake x ON x.interest_id=i.id" if has_intake else ""
        interest_rows = [dict(row) for row in conn.execute(
            f"""SELECT i.id, i.interest_number, i.apartment_number, i.service_item_code,
                       i.service_name_snapshot, i.quantity, i.amount_due_snapshot, i.currency,
                       i.interest_status, i.payment_notice_number, i.payment_id,
                       i.service_order_id, i.resident_comment, i.created_at,
                       {intake_fields}
                FROM service_order_interests i {intake_join} ORDER BY i.id DESC"""
        )]
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
            st.caption(
                "Предварительный спрос = неоплаченные намерения + подтверждённые оплаченные заказы, "
                "ещё не включённые в партию. Одно и то же обращение не считается дважды. "
                "Сообщение жителя о передаче денег само по себе не считается оплатой."
            )
            if selected_interests:
                st.dataframe(pd.DataFrame([{
                    "Намерение": r["interest_number"], "Квартира": r["apartment_number"],
                    "Кол-во": r["quantity"], "Сумма": f"{float(r['amount_due_snapshot']):.2f} {r['currency']}",
                    "Статус": r["interest_status"], "Уведомление об оплате": r["payment_notice_number"] or "—",
                    "Заказ": r["service_order_id"] or "—", "Получено": r["created_at"],
                    "Источник": r["source_channel"] or "Бот",
                    "Сообщение": r["original_message"] or r["resident_comment"] or "—",
                    "Оплата со слов": "да, не подтверждена" if r["claimed_cash_handover"] else "—",
                    "Пункт со слов": r["claimed_cashbox"] or "—",
                    "Ответственный на дату сообщения": (
                        (custodian_at(conn, r["claimed_cashbox"], r["message_received_at"] or r["created_at"]) or {}).get("person_name", "—")
                        if r["claimed_cashbox"] in {"I1", "I2"} else "—"
                    ),
                    "Внёс": r["entered_by"] or "—",
                } for r in selected_interests]), hide_index=True, use_container_width=True)
            else:
                st.info("Намерений по этой позиции ещё нет.")

            minimum = st.number_input(
                "Минимальная партия поставщика, шт. (0 — пока неизвестна)",
                min_value=0, value=0, step=1, key="fulfillment_supplier_minimum",
            )
            if minimum:
                shortfall = max(int(minimum) - demand_quantity, 0)
                if shortfall:
                    st.info(f"До минимальной партии по предварительному спросу не хватает {shortfall} шт.")
                else:
                    st.success("Предварительный спрос достиг минимальной партии. Для заказа поставщику отдельно проверьте подтверждённую оплату.")
                announcement = (
                    f"📦 Нові пульти: зафіксовано попередній попит на {demand_quantity} шт. "
                    f"(намірів без підтвердженої оплати — {open_quantity} шт.; "
                    f"оплачених замовлень поза партією — {paid_quantity} шт.). "
                    f"Мінімальна партія постачальника — {int(minimum)} шт. "
                    + (f"До неї поки бракує {shortfall} шт. Продовжуємо збір заявок."
                       if shortfall else "Попередній попит досяг мінімуму; формування замовлення залежить від підтвердження оплат.")
                )
                st.markdown("**Черновик объявления для копирования**")
                st.code(announcement, language=None)
                st.caption("Порог введён только для текущего просмотра. Объявление автоматически не публикуется.")
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
