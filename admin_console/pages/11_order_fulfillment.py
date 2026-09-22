"""Read-only operator view of the canonical OSBB order-fulfillment model."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

STREAMLIT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = STREAMLIT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from admin_console.utils.db import get_conn


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
        st.dataframe(pd.DataFrame(balances), hide_index=True, use_container_width=True) if balances else st.info("Учтённых остатков пока нет.")
        st.markdown("**В пути и с расхождениями**")
        st.dataframe(pd.DataFrame(pending_transfers), hide_index=True, use_container_width=True) if pending_transfers else st.caption("Передач в пути нет.")
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
