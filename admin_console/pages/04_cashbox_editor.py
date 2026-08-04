# G:\Programming\OSBB_cl\admin_console\pages\04_cashbox_editor.py
"""
Правка пункта приёма (cashbox_code) у уже внесённых платежей.

Задача: часть платежей была ошибочно проведена под кодом O (охрана),
хотя реально принята в другом пункте (C — центральная касса, K1..K6 —
консьержи по подъездам). BANK трогать нельзя — банк остаётся банком,
это отдельный канал поступления, не пункт приёма наличных.

Что делает страница:
1. Показывает текущие балансы всех касс.
2. Даёт отфильтровать и точечно поправить cashbox_code у платежей.
3. При сохранении правит СИНХРОННО все три места, где код кассы
   продублирован (payments, cashbox_operations, cashier_receipts),
   пересчитывает cashboxes.current_balance для каждой затронутой кассы
   и пишет запись в audit_log.

Это НЕ хирургическое удаление — правится только код кассы у
существующей, уже подтверждённой цепочки payment/receipt/operation.
Сам платёж, сумма, квартира, период — не меняются.

--------------------------------------------------------------------
ВАЖНО: бизнес-логика (формула баланса, структура audit_log, апдейт
динамических таблиц) НЕ реализована заново в этом файле, а импортирована
напрямую из cashier_v2_core.py — того же модуля, которым пользуется
боевой бот. Это сознательное решение против дублирования: см.
Project_Log.md, "Перенос Streamlit-инструментов из OSBB_util в OSBB_cl",
и Surgical_Delete.md, раздел "5. Архитектурное размещение" — тот же
класс риска, только для правки, а не для удаления.

Используются:
    cashier_v2_core.CASH_CODES            — допустимые коды касс
    cashier_v2_core.calc_cashbox_balance   — реально пересчитывает И
                                              сохраняет cashboxes.current_balance
                                              (это v1.recalc_and_store_cashbox_balance)
    cashier_v2_core.update_dynamic         — тот же безопасный апдейтер
                                              динамической схемы, что и в боте
    cashier_v2_core.audit_log              — тот же audit_logger.audit_log,
                                              с той же сигнатурой полей
    cashier_v2_core.now_db                 — единый формат timestamp
--------------------------------------------------------------------
"""

import sys
from pathlib import Path

STREAMLIT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STREAMLIT_ROOT))

import streamlit as st
import pandas as pd
from utils.db import get_conn

# ВАЖНО: этот импорт должен идти ПОСЛЕ "from utils.db import get_conn" —
# именно db.py вставляет PROJECT_ROOT (корень OSBB_cl) в sys.path,
# а cashier_v2_core.py лежит прямо в корне OSBB_cl.
from cashier_v2_core import (
    CASH_CODES,
    calc_cashbox_balance,
    update_dynamic,
    audit_log,
    now_db,
)


st.set_page_config(page_title="Правка кассы", layout="wide")
st.title("🏦 Правка кассы платежа")

NON_EDITABLE_CODES = ["BANK", "K"]  # BANK — отдельный канал, K — агрегат для отчётности
ALL_FILTER_CODES = list(CASH_CODES) + NON_EDITABLE_CODES

ACTOR_NAME = "streamlit_admin"  # TODO: заменить на реальную идентификацию оператора, когда появится вход


# ==========================================
# ПАНЕЛЬ БАЛАНСОВ
# ==========================================

def show_balances():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT cashbox_code, cashbox_name, current_balance FROM cashboxes ORDER BY cashbox_code")
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        st.info("Справочник cashboxes пуст.")
        return

    cols = st.columns(len(rows))
    for col, row in zip(cols, rows):
        code, name, balance = row["cashbox_code"], row["cashbox_name"], row["current_balance"]
        col.metric(f"{code} · {name or ''}", f"{balance:,.2f} UAH")


st.subheader("Текущие балансы касс")
show_balances()
st.caption(
    "Балансы читаются напрямую из `cashboxes.current_balance`. "
    "Пересчитываются через ту же функцию, что использует бот "
    "(cashier_v2_core.calc_cashbox_balance), поэтому расхождений с "
    "боевой логикой быть не должно."
)

st.divider()

# ==========================================
# ФИЛЬТРЫ
# ==========================================

st.subheader("Найти платежи для правки")

col1, col2, col3, col4 = st.columns([1, 1, 1, 2])
with col1:
    filter_code = st.selectbox("Текущая касса", ["Все"] + ALL_FILTER_CODES, index=(ALL_FILTER_CODES.index("O") + 1))
with col2:
    date_from = st.date_input("С даты", value=None)
with col3:
    date_to = st.date_input("По дату", value=None)
with col4:
    apartment_filter = st.text_input("Квартира (точное совпадение, опционально)", placeholder="например: 105")

limit = st.number_input("Максимум строк", min_value=10, max_value=2000, value=200, step=10)


def load_payments() -> pd.DataFrame:
    conn = get_conn()
    try:
        where = ["1=1"]
        params: list = []

        if filter_code != "Все":
            where.append("p.cashbox_code = ?")
            params.append(filter_code)
        if date_from:
            where.append("date(p.payment_date) >= ?")
            params.append(date_from.strftime("%Y-%m-%d"))
        if date_to:
            where.append("date(p.payment_date) <= ?")
            params.append(date_to.strftime("%Y-%m-%d"))
        if apartment_filter.strip():
            where.append("p.apartment_number = ?")
            params.append(apartment_filter.strip())

        sql = f"""
            SELECT
                p.id                    AS payment_id,
                p.payment_date          AS дата,
                p.apartment_number      AS квартира,
                p.amount                AS сумма,
                p.cashbox_code          AS касса_сейчас,
                p.cashbox_operation_id  AS op_id,
                p.cashier_receipt_id    AS receipt_id,
                p.source_ref            AS чек,
                p.comment               AS комментарий
            FROM payments p
            WHERE {' AND '.join(where)}
            ORDER BY p.payment_date DESC, p.id DESC
            LIMIT ?
        """
        params.append(int(limit))
        df = pd.read_sql_query(sql, conn, params=params)
        return df
    finally:
        conn.close()


df = load_payments()

if df.empty:
    st.info("Ничего не найдено по этим фильтрам.")
    st.stop()

st.caption(f"Найдено: {len(df)} платежей. Столбец «новая касса» — правьте только там, где нужен перенос.")

df_edit = df.copy()
df_edit["новая касса"] = df_edit["касса_сейчас"]

edited = st.data_editor(
    df_edit,
    use_container_width=True,
    disabled=[c for c in df_edit.columns if c != "новая касса"],
    column_config={
        "новая касса": st.column_config.SelectboxColumn(
            "новая касса",
            options=list(CASH_CODES),  # BANK сознательно не в списке — банк не трогаем этим инструментом
            required=True,
        ),
        "payment_id": st.column_config.NumberColumn("ID платежа"),
        "сумма": st.column_config.NumberColumn("сумма", format="%.2f"),
    },
    key="cashbox_editor_table",
    hide_index=True,
)

changed = edited[edited["новая касса"] != edited["касса_сейчас"]]

st.divider()

if changed.empty:
    st.info("Изменений пока нет — поправьте «новая касса» в нужных строках выше.")
    st.stop()

st.subheader(f"⚠️ К сохранению: {len(changed)} платежей")
st.dataframe(
    changed[["payment_id", "дата", "квартира", "сумма", "касса_сейчас", "новая касса", "чек"]],
    use_container_width=True,
    hide_index=True,
)

reason = st.text_input(
    "Причина правки (обязательно, попадёт в audit_log)",
    placeholder="например: сверка с бумажным журналом за июль, кассир перепутал пункт приёма",
)

confirm = st.checkbox("Проверил(а) список выше — переносить именно эти платежи")

if st.button("💾 Сохранить изменения", type="primary", disabled=not (confirm and reason.strip())):
    conn = get_conn()
    affected_cashboxes: set[str] = set()
    errors: list[str] = []
    applied = 0

    try:
        cur = conn.cursor()
        for _, row in changed.iterrows():
            payment_id = int(row["payment_id"])
            old_code = str(row["касса_сейчас"])
            new_code = str(row["новая касса"])
            op_id = row["op_id"]
            receipt_id = row["receipt_id"]

            try:
                # Синхронная правка кода кассы во всех трёх местах, где он
                # продублирован — через тот же update_dynamic, что и в боте.
                update_dynamic(cur, "payments", payment_id, {"cashbox_code": new_code})

                if pd.notna(op_id):
                    update_dynamic(cur, "cashbox_operations", int(op_id), {"cashbox_code": new_code})

                if pd.notna(receipt_id):
                    update_dynamic(cur, "cashier_receipts", int(receipt_id), {"cashbox_code": new_code})

                # Та же audit_log(), с той же сигнатурой полей, что и в
                # cashier_v2_core.py — не самодельная запись в таблицу.
                audit_log(
                    conn=cur.connection,
                    operator_id=ACTOR_NAME,
                    user_id=ACTOR_NAME,
                    actor_type="admin",
                    action_type="admin_console_cashbox_reassigned",
                    table_name="payments",
                    row_id=payment_id,
                    field_name="cashbox_code",
                    old_value=old_code,
                    new_value=new_code,
                    source_context="admin_console_cashbox_editor",
                    comment=reason.strip(),
                    extra={
                        "cashbox_operation_id": int(op_id) if pd.notna(op_id) else None,
                        "cashier_receipt_id": int(receipt_id) if pd.notna(receipt_id) else None,
                    },
                    commit=False,
                )

                affected_cashboxes.add(old_code)
                affected_cashboxes.add(new_code)
                applied += 1

            except Exception as e:
                errors.append(f"Платёж {payment_id}: {e}")

        # calc_cashbox_balance пересчитывает И сохраняет current_balance —
        # та же функция (v1.recalc_and_store_cashbox_balance), что и в боте.
        new_balances = {}
        for code in affected_cashboxes:
            new_balances[code] = calc_cashbox_balance(cur, code)

        if errors:
            conn.rollback()
            st.error("Ничего не сохранено — ошибки при обработке:\n" + "\n".join(errors))
        else:
            conn.commit()
            st.success(f"Сохранено: {applied} платежей перенесены между кассами.")
            st.write("Пересчитанные балансы:")
            for code, bal in sorted(new_balances.items()):
                st.write(f"— **{code}**: {bal:,.2f} UAH")
            st.rerun()

    finally:
        conn.close()