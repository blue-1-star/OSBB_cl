"""Delivery and issue panel, independent of supplier payment."""
import streamlit as st
import pandas as pd
from supplier_lifecycle_core import batch_residents,set_pickup,receive_batch_local,issue_local

def show_lifecycle(conn,document,actor):
    bid=document['supplier_batch_id']
    if not bid: return
    batch=conn.execute('SELECT * FROM remote_supplier_batches WHERE id=?',(bid,)).fetchone()
    if not batch: return
    st.markdown('### Поступление партии и выдача')
    st.write(f"{batch['batch_number']}: заказано {batch['quantity_requested']}, получено {batch['quantity_received']}, выдано {batch['quantity_issued']} шт.")
    remaining=batch['quantity_requested']-batch['quantity_received']
    if remaining>0:
        with st.form(f"arrival_{bid}"):
            qty=st.number_input('Фактически прибыло сейчас, шт.',min_value=1,max_value=remaining,value=remaining)
            evidence=st.text_input('Накладная / подтверждение поступления')
            checked=st.checkbox('Подтверждаю фактическое получение на центральный склад')
            if st.form_submit_button('📥 Принять партию и уведомить жителей'):
                try:
                    if not checked: raise ValueError('Подтвердите получение.')
                    receive_batch_local(conn,batch_id=bid,quantity=qty,actor=actor,evidence=evidence)
                    conn.commit(); st.rerun()
                except Exception as exc: conn.rollback(); st.error(str(exc))
    residents=batch_residents(conn,bid)
    st.dataframe(pd.DataFrame(residents),hide_index=True,width='stretch')
    if not residents: return
    order_id=st.selectbox('Заказ жителя',[r['id'] for r in residents],format_func=lambda i:next(f"Кв. {r['apartment_number']} · {r['quantity']} шт." for r in residents if r['id']==i),key=f'resident_issue_{bid}')
    row=next(r for r in residents if r['id']==order_id)
    points=list(dict.fromkeys([row['pickup_point']]+[f'K{i}' for i in range(1,7)]+['O','CS']))
    if 'BANK' in points: points.remove('BANK')
    with st.form(f'pickup_{order_id}'):
        point=st.selectbox('Точка выдачи',points)
        st.caption('По умолчанию — место сдачи денег. Банк не является точкой выдачи; выберите её отдельно.')
        if st.form_submit_button('Сохранить точку выдачи'):
            try: set_pickup(conn,order_id=order_id,point=point,actor=actor); conn.commit(); st.rerun()
            except Exception as exc: conn.rollback(); st.error(str(exc))
    if row['link_status']=='READY':
        point=row['pickup_point'] if row['pickup_point']!='BANK' else 'CS'
        if point!='CS':
            from inventory_transfer_core import send_transfer,confirm_transfer
            with st.expander('🚚 Передача пультов со склада в выбранную точку'):
                with st.form(f'transfer_send_{order_id}'):
                    checked_transfer=st.checkbox(f"Фактически передано {row['quantity']} шт. из ЦС в {point}")
                    if st.form_submit_button('Записать передачу в точку'):
                        try:
                            if not checked_transfer: raise ValueError('Подтвердите фактическую передачу.')
                            send_transfer(conn,batch_id=bid,from_location='CS',to_location=point,quantity=row['quantity'],actor_id=actor,note=f'Для заказа #{order_id}')
                            conn.commit(); st.rerun()
                        except Exception as exc: conn.rollback(); st.error(str(exc))
                transfers=[dict(r) for r in conn.execute('''SELECT t.* FROM inventory_transfers t JOIN inventory_lots l ON l.id=t.lot_id
                   WHERE l.source_kind='REMOTE_SUPPLIER_BATCH' AND l.source_id=? AND t.to_location_code=? AND t.transfer_status='SENT' ''',(bid,point))]
                for transfer in transfers:
                    with st.form(f"transfer_receive_{transfer['id']}"):
                        st.write(f"Передача #{transfer['id']}: {transfer['quantity']} шт. в {point}")
                        receiver=st.text_input('ФИО фактического получателя')
                        confirmed_transfer=st.checkbox('Получатель подтвердил поступление и количество')
                        if st.form_submit_button('Подтвердить приём в точке'):
                            try:
                                if not receiver.strip() or not confirmed_transfer: raise ValueError('Укажите получателя и подтвердите приём.')
                                confirm_transfer(conn,transfer_id=transfer['id'],actor_id=receiver,note=f'Подтверждение зарегистрировал {actor}')
                                conn.commit(); st.rerun()
                            except Exception as exc: conn.rollback(); st.error(str(exc))
        st.caption('Выдача возможна только из фактического остатка этой партии в выбранной точке. Для O/K/KAS сначала оформите передачу со склада и подтверждение приёма.')
        with st.form(f'issue_{order_id}'):
            evidence=st.text_input('Подтверждение передачи жителю')
            checked=st.checkbox('Пульты фактически выданы жителю')
            if st.form_submit_button('✅ Зафиксировать выдачу'):
                try:
                    if not checked: raise ValueError('Подтвердите выдачу.')
                    issue_local(conn,order_id=order_id,point=row['pickup_point'] if row['pickup_point']!='BANK' else 'CS',actor=actor,evidence=evidence)
                    conn.commit(); st.rerun()
                except Exception as exc: conn.rollback(); st.error(str(exc))
    st.caption('Если у жителя нет Telegram ID, уведомление нужно передать вручную. Оплата поставщику ведётся отдельно.')
