"""Local console upload into the shared document inbox."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import getpass
import pandas as pd
import streamlit as st
from document_intake_core import accept_document, ALLOWED
from service_orders_core import get_conn

st.set_page_config(page_title='Документы ОСББ',page_icon='📥',layout='wide')
st.title('📥 Документы ОСББ — Входящие')
st.caption('Локальный однопользовательский режим. Документы сохраняются на разбор; анализ ИИ пока не подключён.')
files=st.file_uploader('Загрузить документы или перетащить сюда',type=sorted(s.lstrip('.') for s in ALLOWED),accept_multiple_files=True,max_upload_size=20)
if st.button('📥 Сохранить во входящих',disabled=not files):
    for uploaded in files:
        try:
            result=accept_document(filename=uploaded.name,data=uploaded.getvalue(),actor=f'local_mac:{getpass.getuser()}',source='CONSOLE')
            st.success(f"{uploaded.name}: {'уже был принят' if result['duplicate'] else 'сохранён'} · документ #{result['id']}")
        except Exception as exc: st.error(f'{uploaded.name}: {exc}')
conn=get_conn()
try:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='osbb_documents'").fetchone():
        rows=[dict(r) for r in conn.execute("SELECT id AS Номер,original_filename AS Файл,status AS Состояние,created_at AS Получен,created_by AS Исполнитель FROM osbb_documents WHERE status='INBOX' ORDER BY id DESC")]
        st.dataframe(pd.DataFrame(rows),hide_index=True,width='stretch')
    else: st.info('Документы ещё не зарегистрированы.')
finally: conn.close()
