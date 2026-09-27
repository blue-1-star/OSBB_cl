"""Local counterparty directory and internal purchase documents."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import pandas as pd
import streamlit as st
from service_orders_core import get_conn
import supplier_procurement_core
import inspect
if (not hasattr(supplier_procurement_core,'assign_supplier_to_batch') or
    not hasattr(supplier_procurement_core,'update_supplier_record') or
    not hasattr(supplier_procurement_core,'CONTRACT_DATE_FORMATS') or
    not hasattr(supplier_procurement_core,'CONTRACT_DATE_EMPTY_MARKERS') or
    not hasattr(supplier_procurement_core,'LIFECYCLE_RESPONSE_NOTIFICATIONS') or
    'requested_delivery_date' not in inspect.signature(supplier_procurement_core.assign_supplier_to_batch).parameters):
    from importlib import reload
    supplier_procurement_core=reload(supplier_procurement_core)
from supplier_procurement_core import ensure_schema, create_supplier, add_contract, bind_supplier, create_purchase_document, assign_supplier_to_batch
from supplier_procurement_core import update_supplier_record, update_contract_record
from admin_console.utils.supplier_batch_adapter import create_local_supplier_batch

st.set_page_config(page_title='Поставщики',page_icon='🤝',layout='wide')
st.title('🤝 Поставщики и закупки')
st.caption('Локальная консоль. Формирование документа не означает отправку поставщику или оплату.')
from admin_console.utils.session_actor import session_actor
actor = session_actor()
conn = get_conn()
ensure_schema(conn)
conn.commit()
suppliers = [dict(r) for r in conn.execute('SELECT * FROM suppliers ORDER BY name')]
if notice:=st.session_state.pop('supplier_saved_notice',None):
    st.success(notice)
st.subheader('Сохранённые поставщики')
st.dataframe(pd.DataFrame(suppliers),hide_index=True,width='stretch')
with st.expander('➕ Добавить поставщика',expanded=not suppliers):
    with st.form('new_supplier'):
        name=st.text_input('Название *')
        fields={k:st.text_input(label) for k,label in [('legal_name','Юридическое название'),('tax_id','ЕГРПОУ / налоговый номер'),('contact_person','Контактное лицо'),('phone','Телефон'),('email','Email'),('address','Адрес'),('bank_details','Банковские реквизиты'),('note','Примечание')]}
        if st.form_submit_button('Сохранить контрагента'):
            try:
                supplier_id=create_supplier(actor=actor,name=name,**fields)
                st.session_state['selected_supplier_id']=supplier_id
                st.session_state['supplier_saved_notice']=f'Поставщик #{supplier_id} «{name}» сохранён. Ниже открыта его карточка для договора и партии.'
                st.rerun()
            except Exception as exc: st.error(str(exc))
if not suppliers:
    st.info('В БД пока нет сохранённых поставщиков. Заполните название выше и нажмите «Сохранить контрагента». После успешного сохранения здесь появятся карточка и привязка партии.')
    conn.close(); st.stop()
labels={r['id']:r['name'] for r in suppliers}
sid=st.selectbox('Контрагент',list(labels),format_func=labels.get,key='selected_supplier_id')
selected_supplier=next(r for r in suppliers if r['id']==sid)
with st.expander('✏️ Редактировать данные поставщика',expanded=False):
    st.caption('Дополните контакты и реквизиты. Отправленные заказы и их снимки условий не изменятся.')
    with st.form(f'edit_supplier_{sid}'):
        supplier_values={field:st.text_input(label,value=selected_supplier.get(field) or '') for field,label in [
            ('name','Название *'),('legal_name','Юридическое название'),('tax_id','ЕГРПОУ / налоговый номер'),
            ('contact_person','Контактное лицо'),('phone','Телефон'),('email','Email'),('address','Адрес'),('bank_details','Банковские реквизиты')]}
        supplier_values['note']=st.text_area('Примечание',value=selected_supplier.get('note') or '')
        if st.form_submit_button('Сохранить изменения поставщика'):
            try:
                changed=update_supplier_record(actor=actor,supplier_id=sid,values=supplier_values,conn=conn)
                conn.commit(); st.session_state['supplier_saved_notice']=f'Карточка поставщика сохранена. Изменено полей: {changed}. История записана в аудит.'; st.rerun()
            except Exception as exc: conn.rollback(); st.error(str(exc))
st.caption('Договоры и партии ниже связываются с выбранным контрагентом — повторно вводить его название не нужно.')
st.write({label:selected_supplier.get(field) or '—' for field,label in [('name','Поставщик'),('contact_person','Контактное лицо'),('phone','Телефон'),('email','Email'),('tax_id','ЕГРПОУ / налоговый номер')]})
contracts=[dict(r) for r in conn.execute('SELECT * FROM supplier_contracts WHERE supplier_id=?',(sid,))]
st.dataframe(pd.DataFrame(contracts),hide_index=True,width='stretch')
if contracts:
    with st.expander('✏️ Редактировать сведения договора',expanded=False):
        chosen_id=st.selectbox('Сохранённый договор',[r['id'] for r in contracts],format_func=lambda i:next(f"{r['contract_number'] or 'Без номера'} — {r['subject']}" for r in contracts if r['id']==i),key=f'edit_contract_choice_{sid}')
        current=next(r for r in contracts if r['id']==chosen_id)
        st.caption('Исправление карточки не меняет оригинал договора и уже отправленный заказ. Новые договорённости оформляются отдельным документом.')
        with st.form(f'edit_contract_{chosen_id}'):
            contract_values={'contract_number':st.text_input('Номер договора',value=current['contract_number'] or ''),
                'subject':st.text_area('Предмет договора *',value=current['subject']),
                'valid_from':st.text_input('Начало срока',value=current['valid_from'] or '',placeholder='ДД.ММ.ГГГГ'),
                'valid_to':st.text_input('Окончание срока',value=current['valid_to'] or '',placeholder='Пусто, если не указано'),
                'note':st.text_area('Примечание к договору',value=current['note'] or '')}
            if st.form_submit_button('Сохранить изменения договора'):
                try:
                    changed=update_contract_record(actor=actor,contract_id=chosen_id,values=contract_values,conn=conn)
                    conn.commit(); st.session_state['supplier_saved_notice']=f'Сведения договора сохранены. Изменено полей: {changed}.'; st.rerun()
                except Exception as exc: conn.rollback(); st.error(str(exc))
with st.expander('📎 Связать загруженный договор с поставщиком',expanded=not contracts):
    uploaded_documents=[dict(r) for r in conn.execute('SELECT id,original_filename FROM osbb_documents ORDER BY id DESC')] if conn.execute("SELECT 1 FROM sqlite_master WHERE name='osbb_documents'").fetchone() else []
    document_labels={r['id']:f"#{r['id']} — {r['original_filename']}" for r in uploaded_documents}
    with st.form('supplier_contract'):
        st.write(f"Поставщик: **{selected_supplier['name']}**")
        if len(document_labels)==1:
            document_id=next(iter(document_labels))
            st.write(f"Загруженный документ: **{document_labels[document_id]}**")
        else:
            document_id=st.selectbox('Загруженный файл договора',list(document_labels),format_func=document_labels.get,index=None,placeholder='Выберите уже загруженный документ')
        number=st.text_input('Номер договора')
        subject=st.text_input('Предмет договора *')
        start=st.text_input('Начало срока (если известно)',placeholder='18.05.2026')
        end=st.text_input('Окончание срока (если известно)',placeholder='Оставьте пустым, если дата не указана',help='Пустое поле или прочерк означают отсутствие указанной даты окончания.')
        if st.form_submit_button('Связать договор с этим поставщиком',disabled=not document_labels):
            try:
                if document_id is None: raise ValueError('Выберите загруженный документ.')
                add_contract(actor=actor,supplier_id=sid,subject=subject,number=number,valid_from=start or None,valid_to=end or None,document_id=document_id)
                st.session_state['supplier_saved_notice']='Договор связан с поставщиком и доступен для выбора в партии.'
                st.rerun()
            except Exception as exc: st.error(str(exc))
if contracts:
    with st.expander('📎 Файлы договоров'):
        from osbb_document_storage import resolve_reference
        for contract in contracts:
            if contract.get('document_id'):
                document=conn.execute('SELECT * FROM osbb_documents WHERE id=?',(contract['document_id'],)).fetchone()
                if document:
                    st.write(f"Договор {contract['contract_number'] or 'без номера'} · документ #{document['id']} · {document['original_filename']}")
                    try:
                        file=resolve_reference({'storage':document['storage_key'],'path':document['relative_path']})
                        st.download_button('Скачать договор',file.read_bytes(),file_name=document['original_filename'],key=f"contract_download_{contract['id']}")
                    except (OSError,ValueError,RuntimeError) as exc: st.warning(f'Файл сейчас недоступен: {exc}')
with st.expander('🔗 Назначить поставщика существующей партии',expanded=True):
    if not contracts:
        st.info('Документ загружен, но ещё не связан с этим поставщиком. Выполните действие «Связать договор с этим поставщиком» выше — затем он автоматически появится здесь.')
    batches=[dict(r) for r in conn.execute('''SELECT b.* FROM remote_supplier_batches b
        WHERE NOT EXISTS(SELECT 1 FROM supplier_purchase_orders p WHERE p.supplier_batch_id=b.id)
        AND b.batch_status NOT IN ('CLOSED','CANCELLED') ORDER BY b.id DESC''')]
    if batches:
        with st.form('assign_existing_batch'):
            st.write(f"**Поставщик партии: {selected_supplier['name']}**")
            bid=st.selectbox('Партия',[b['id'] for b in batches],format_func=lambda i:next(f"{b['batch_number']} — {b['service_name_snapshot']}, {b['quantity_requested']} шт." for b in batches if b['id']==i))
            clabels={r['id']:f"{r['contract_number'] or 'Без номера'} — {r['subject']}" for r in contracts}
            cid=st.selectbox('Договор выбранного поставщика',list(clabels),format_func=clabels.get,placeholder='Сначала свяжите загруженный договор с поставщиком',disabled=not clabels,key=f'batch_linked_contract_{sid}')
            known=st.checkbox('Закупочная цена партии известна')
            purchase_price=st.number_input('Закупочная цена одного пульта, грн',min_value=0.0,key='batch_price')
            terms=st.text_area('Условия именно этой поставки')
            confirmed=st.checkbox('Подтверждаю поставщика, договор и формирование документа для выбранной партии')
            if st.form_submit_button('Назначить поставщика партии и сформировать документ',disabled=not contracts):
                try:
                    if not confirmed: raise ValueError('Подтвердите назначение.')
                    assign_supplier_to_batch(conn,batch_id=bid,supplier_id=sid,contract_id=cid,actor=actor,unit_price=purchase_price if known else None,terms=terms)
                    conn.commit(); st.rerun()
                except Exception as exc: conn.rollback(); st.error(str(exc))
    else: st.info('Нет партий без документа закупки. Созданные документы показаны ниже.')
    st.caption('Назначение этой партии не меняет поставщика других услуг или будущих партий. Для будущих заказов назначьте поставщика позиции ниже.')
items={r['service_item_code']:r['service_item_name'] for r in conn.execute('SELECT * FROM service_items WHERE is_active=1')}
with st.expander('🔗 Назначить поставщика товару / услуге'):
    with st.form('bind_supplier'):
        code=st.selectbox('Конкретная позиция',list(items),format_func=lambda c:f'{items[c]} ({c})')
        clabels={r['id']:f"{r['contract_number'] or 'Без номера'} — {r['subject']}" for r in contracts}
        cid=st.selectbox('Договор',list(clabels),format_func=clabels.get,placeholder='Сначала свяжите договор с поставщиком',disabled=not clabels,key=f'item_linked_contract_{sid}')
        minimum=st.number_input('Минимальная партия, шт.',min_value=1,value=5)
        price_known=st.checkbox('Закупочная цена известна')
        price=st.number_input('Закупочная цена за единицу, грн (не цена для жителя)',min_value=0.0)
        terms=st.text_area('Условия доставки / оплаты')
        confirm=st.checkbox('Подтверждаю замену текущего поставщика этой позиции')
        if st.form_submit_button('Назначить поставщика',disabled=not contracts):
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
                    import getpass
                    batch=create_local_supplier_batch(service_item_code=item,actor_id=f'local_mac:{getpass.getuser()}',
                        note=f'Оператор сессии: {actor}',conn=conn)
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
