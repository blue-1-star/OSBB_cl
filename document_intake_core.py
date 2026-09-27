"""Shared non-executing document intake for console and Telegram."""
from pathlib import Path
from uuid import uuid4
import hashlib
import os
from config import paths
from service_orders_core import get_conn, now_db
from osbb_document_storage import ensure_directory
from audit_logger import audit_log

MAX_BYTES=20*1024*1024
ALLOWED={'.pdf','.doc','.docx','.xls','.xlsx','.csv','.txt','.rtf','.odt','.jpg','.jpeg','.png','.heic'}

def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS osbb_documents(id INTEGER PRIMARY KEY,
        storage_key TEXT NOT NULL, relative_path TEXT NOT NULL UNIQUE,
        original_filename TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE, size_bytes INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'INBOX', created_by TEXT NOT NULL, created_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS osbb_document_receipts(id INTEGER PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES osbb_documents(id), actor TEXT NOT NULL,
        source_channel TEXT NOT NULL, source_reference TEXT, original_filename TEXT,
        received_at TEXT NOT NULL)''')

def accept_document(*, filename, data, actor, source, source_reference='', root=None, conn=None):
    if not actor or source not in {'TELEGRAM','CONSOLE'}: raise ValueError('Нужны исполнитель и источник.')
    if not filename or Path(filename).name!=filename or '\\' in filename:
        raise ValueError('Недопустимое имя файла.')
    suffix=Path(filename).suffix.lower()
    if suffix not in ALLOWED: raise ValueError('Этот тип файла не принимается. Отправьте документ или изображение, не программу/архив.')
    if not data or len(data)>MAX_BYTES: raise ValueError('Нужен непустой файл размером не более 20 МБ.')
    base=Path(root if root is not None else paths.OSBB_DOCUMENTS_ROOT)
    inbox=ensure_directory('inbox',root=base)
    owns=conn is None
    conn=conn or get_conn()
    created_file=None
    try:
        ensure_schema(conn)
        digest=hashlib.sha256(data).hexdigest()
        old=conn.execute('SELECT * FROM osbb_documents WHERE sha256=?',(digest,)).fetchone()
        if old:
            record=dict(old); duplicate=True
        else:
            destination=inbox/f'{uuid4().hex}{suffix}'
            fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            created_file=destination
            with os.fdopen(fd,'wb') as handle: handle.write(data)
            relative=destination.relative_to(base.resolve()).as_posix()
            cur=conn.execute('INSERT INTO osbb_documents(storage_key,relative_path,original_filename,sha256,size_bytes,created_by,created_at) VALUES (?,?,?,?,?,?,?)',
                ('OSBB_DOCUMENTS',relative,filename,digest,len(data),actor,now_db()))
            record=dict(conn.execute('SELECT * FROM osbb_documents WHERE id=?',(cur.lastrowid,)).fetchone()); duplicate=False
        conn.execute('INSERT INTO osbb_document_receipts(document_id,actor,source_channel,source_reference,original_filename,received_at) VALUES (?,?,?,?,?,?)',
                     (record['id'],actor,source,source_reference,filename,now_db()))
        audit_log(conn=conn,operator_id=actor,user_id=actor,actor_type='document_operator',
            action_type='document_received_duplicate' if duplicate else 'document_received',
            table_name='osbb_documents',row_id=record['id'],field_name='status',old_value='',new_value='INBOX',
            source_context='document_intake',comment=f'{source}; {filename}; {source_reference}',commit=False)
        if owns: conn.commit()
        return {**record,'duplicate':duplicate}
    except Exception:
        if owns: conn.rollback()
        if created_file: created_file.unlink(missing_ok=True)
        raise
    finally:
        if owns: conn.close()
