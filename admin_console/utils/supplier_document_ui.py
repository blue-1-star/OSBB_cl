"""Shared operator card: purchase document, manual dispatch and supplier reply."""
from datetime import date
import pandas as pd
import streamlit as st
from supplier_procurement_core import record_manual_dispatch, record_supplier_response

def show_supplier_document(conn, document, actor):
    key=f"purchase_{document['id']}"
    st.markdown(f"### Заказ поставщику {document['document_number']}")
    st.code(document['document_text'],language=None)
    st.write(f"Состояние отправки: **{document['delivery_status']}**")
    st.download_button('📄 Скачать заказ для отправки',document['document_text'],file_name=f"{document['document_number']}.txt",key=f'{key}_download')
    if document['delivery_status'] != 'SENT':
        st.caption('Автоматический канал пока не подключён. Отправьте документ вручную и зафиксируйте факт; эта кнопка сама сообщение поставщику не отправляет.')
        supplier=conn.execute('SELECT name,email,phone FROM suppliers WHERE id=?',(document['supplier_id'],)).fetchone()
        st.write(f"Поставщик: **{supplier['name'] if supplier else 'не найден'}**")
        channel=st.selectbox('Как отправлен', ['EMAIL','TELEGRAM','VIBER','PAPER','OTHER'],key=f'{key}_channel')
        default_destination=(supplier['email'] if channel=='EMAIL' else supplier['phone'] if channel=='VIBER' else supplier['name'] if channel=='PAPER' else '') if supplier else ''
        with st.form(f'{key}_dispatch'):
            destination=st.text_input('Адресат / контакт поставщика',value=default_destination or '',key=f'{key}_destination_{channel}')
            evidence=st.text_input('Ссылка / ID сообщения / подтверждение передачи')
            confirmed=st.checkbox('Документ действительно отправлен поставщику')
            if st.form_submit_button('📤 Зафиксировать отправку заказа'):
                try:
                    if not confirmed: raise ValueError('Подтвердите фактическую отправку.')
                    record_manual_dispatch(conn,document_id=document['id'],actor=actor,channel=channel,destination=destination,evidence=evidence)
                    conn.commit(); st.rerun()
                except Exception as exc: conn.rollback(); st.error(str(exc))
    with st.expander('📨 Ответ поставщика / ожидаемая поставка'):
        with st.form(f'{key}_reply'):
            received=st.date_input('Дата ответа',max_value=date.today())
            response=st.text_area('Ответ поставщика')
            quantity=st.number_input('Подтверждено к поставке, шт.',min_value=1,value=int(document['quantity']))
            expected=st.date_input('Дата поставки, сообщённая поставщиком',value=None)
            reference=st.text_input('Ссылка на ответ / номер подтверждения')
            if st.form_submit_button('Сохранить ответ поставщика'):
                try:
                    if expected is None: raise ValueError('Укажите дату из ответа поставщика; автоматически она не назначается.')
                    record_supplier_response(conn,document_id=document['id'],actor=actor,response=response,quantity=quantity,expected_date=expected.isoformat(),received_date=received.isoformat(),reference=reference)
                    conn.commit(); st.rerun()
                except Exception as exc: conn.rollback(); st.error(str(exc))
        replies=[dict(r) for r in conn.execute('SELECT received_at,response_text,quantity,expected_delivery_date,supplier_reference,recorded_by FROM supplier_order_responses WHERE purchase_order_id=? ORDER BY id DESC',(document['id'],))]
        if replies:
            latest=replies[0]
            st.info(f"Поставка {latest['quantity']:g} шт. ожидается {latest['expected_delivery_date']}.")
            st.dataframe(pd.DataFrame(replies),hide_index=True,width='stretch')
    st.caption('К оплате — закупочная сумма документа. Получение партии не требует предварительной оплаты поставщику.')
    from admin_console.utils.supplier_lifecycle_ui import show_lifecycle
    show_lifecycle(conn,document,actor)
