"""Local counterparty directory and internal purchase documents."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import pandas as pd
import streamlit as st
from service_orders_core import get_conn
from supplier_procurement_core import ensure_schema, create_supplier, add_contract, bind_supplier, create_purchase_document
from service_preorders_core import create_supplier_batch

st.set_page_config(page_title='Поставщики',page_icon='🤝',layout='wide')
st.title('🤝 Поставщики и закупки')
st.caption('Локальная консоль. Формирование документа не означает отправку поставщику или оплату.')
actor = st.sidebar.text_input('Исполнитель',key='supplier_actor')
conn = get_conn()
ensure_schema(conn)
conn.commit()
suppliers = [dict(r) for r in conn.execute('SELECT * FROM suppliers ORDER BY name')]
st.dataframe(pd.DataFrame(suppliers),hide_index=True,width='stretch')
with st.expander('➕ Контрагент'):
    with st.form('new_supplier'):
        name=st.text_input('Название *')
        fields={k:st.text_input(label) for k,label in [('legal_name','Юридическое название'),('tax_id','ЕГРПОУ / налоговый номер'),('contact_person','Контактное лицо'),('phone','Телефон'),('email','Email'),('address','Адрес'),('bank_details','Банковские реквизиты'),('note','Примечание')]}
        if st.form_submit_button('Сохранить контрагента'):
            try: create_supplier(actor=actor,name=name,**fields); st.rerun()
            except Exception as exc: st.error(str(exc))
if not suppliers:
    conn.close(); st.stop()
labels={r['id']:r['name'] for r in suppliers}
sid=st.selectbox('Контрагент',list(labels),format_func=labels.get)
contracts=[dict(r) for r in conn.execute('SELECT * FROM supplier_contracts WHERE supplier_id=?',(sid,))]
st.dataframe(pd.DataFrame(contracts),hide_index=True,width='stretch')
with st.expander('➕ Договор / соглашение'):
    with st.form('supplier_contract'):
        number=st.text_input('Номер договора')
        subject=st.text_input('Предмет договора *')
        start=st.text_input('Начало срока, ГГГГ-ММ-ДД (если известно)')
        end=st.text_input('Окончание срока, ГГГГ-ММ-ДД (если известно)')
        ref=st.text_input('Ссылка / путь к документу')
        if st.form_submit_button('Записать договор'):
            try: add_contract(actor=actor,supplier_id=sid,subject=subject,number=number,valid_from=start or None,valid_to=end or None,reference=ref); st.rerun()
            except Exception as exc: st.error(str(exc))
items={r['service_item_code']:r['service_item_name'] for r in conn.execute('SELECT * FROM service_items WHERE is_active=1')}
with st.expander('🔗 Назначить поставщика товару / услуге'):
    with st.form('bind_supplier'):
        code=st.selectbox('Конкретная позиция',list(items),format_func=lambda c:f'{items[c]} ({c})')
        clabels={r['id']:f"{r['contract_number'] or 'Без номера'} — {r['subject']}" for r in contracts}
        cid=st.selectbox('Договор', [None]+list(clabels),format_func=lambda i:clabels.get(i,'Без договора'))
        minimum=st.number_input('Минимальная партия, шт.',min_value=1,value=5)
        price_known=st.checkbox('Закупочная цена известна')
        price=st.number_input('Закупочная цена за единицу, грн (не цена для жителя)',min_value=0.0)
        terms=st.text_area('Условия доставки / оплаты')
        confirm=st.checkbox('Подтверждаю замену текущего поставщика этой позиции')
        if st.form_submit_button('Назначить поставщика'):
            try:
                if not confirm: raise ValueError('Подтвердите назначение.')
                bind_supplier(actor=actor,item_code=code,supplier_id=sid,contract_id=cid,minimum=int(minimum),unit_price=price if price_known else None,terms=terms); st.rerun()
            except Exception as exc: st.error(str(exc))
bindings=[dict(r) for r in conn.execute('SELECT b.*,s.name FROM service_item_suppliers b JOIN suppliers s ON s.id=b.supplier_id WHERE b.is_active=1')]
st.subheader('Действующие поставщики позиций')
st.dataframe(pd.DataFrame(bindings),hide_index=True,width='stretch')
with st.expander('📄 Сформировать внутренний заказ из оплаченных предзаказов'):
    options=[r['service_item_code'] for r in bindings]
    if options:
        with st.form('purchase_document'):
            item=st.selectbox('Позиция заказа',options)
            checked=st.checkbox('Подтверждаю формирование партии; это ещё не отправка и не оплата поставщику')
            if st.form_submit_button('Сформировать документ заказа'):
                try:
                    if not checked or not actor.strip(): raise ValueError('Укажите исполнителя и подтвердите действие.')
                    batch=create_supplier_batch(service_item_code=item,actor_id=actor,conn=conn)
                    doc=create_purchase_document(conn,batch=batch,actor=actor)
                    conn.commit(); st.success(f"Создан {doc['document_number']}"); st.rerun()
                except Exception as exc: conn.rollback(); st.error(str(exc))
    else: st.info('Сначала назначьте поставщика позиции.')
docs=[dict(r) for r in conn.execute('SELECT * FROM supplier_purchase_orders ORDER BY id DESC')]
st.subheader('Документы закупки')
if docs:
    selected=st.selectbox('Документ',docs,format_func=lambda r:f"{r['document_number']} · {r['delivery_status']}")
    from admin_console.utils.supplier_document_ui import show_supplier_document
    show_supplier_document(conn,selected,actor)
conn.close()
