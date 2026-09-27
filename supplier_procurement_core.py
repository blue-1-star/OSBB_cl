"""Generic counterparties, item-specific supply agreements and durable orders.

No external delivery is inferred from document creation. A future channel
adapter reads QUEUED documents, records attempts and marks SENT only on success.
"""
import json
from uuid import uuid4
from audit_logger import audit_log
from service_orders_core import get_conn, now_db
CONTRACT_DATE_FORMATS=('%Y-%m-%d','%d.%m.%Y')
CONTRACT_DATE_EMPTY_MARKERS=True
LIFECYCLE_RESPONSE_NOTIFICATIONS=True

def normalize_contract_date(value):
    from datetime import datetime
    if not value or not str(value).strip(): return None
    value=str(value).strip()
    if value in {'-', '—', '–'}: return None
    for fmt in CONTRACT_DATE_FORMATS:
        try: return datetime.strptime(value,fmt).date().isoformat()
        except ValueError: pass
    raise ValueError('Дата договора: введите ДД.ММ.ГГГГ или ГГГГ-ММ-ДД, например 18.05.2026.')


def update_supplier_record(*, actor, supplier_id, values, conn=None):
    return _update_record(actor=actor,table='suppliers',record_id=supplier_id,values=values,conn=conn)


def update_contract_record(*, actor, contract_id, values, conn=None):
    return _update_record(actor=actor,table='supplier_contracts',record_id=contract_id,values=values,conn=conn)


def _update_record(*, actor, table, record_id, values, conn=None):
    allowed={'suppliers':{'name','legal_name','tax_id','contact_person','phone','email','address','bank_details','note'},
             'supplier_contracts':{'contract_number','subject','valid_from','valid_to','note'}}
    if not str(actor).strip(): raise ValueError('Не определён исполнитель.')
    if table not in allowed or set(values)-allowed[table]: raise ValueError('Недопустимые поля редактирования.')
    owns=conn is None
    conn=conn or get_conn()
    try:
        old=conn.execute(f'SELECT * FROM {table} WHERE id=?',(record_id,)).fetchone()
        if not old: raise ValueError('Запись не найдена.')
        normalized={k:normalize_contract_date(v) if k in {'valid_from','valid_to'} else str(v or '').strip() for k,v in values.items()}
        merged={**dict(old),**normalized}
        required='name' if table=='suppliers' else 'subject'
        if not merged[required].strip(): raise ValueError('Название / предмет не может быть пустым.')
        if table=='supplier_contracts' and merged['valid_from'] and merged['valid_to'] and merged['valid_to']<merged['valid_from']:
            raise ValueError('Окончание срока раньше начала.')
        changed=0
        for field,value in normalized.items():
            if old[field]==value or (old[field] is None and value==''): continue
            conn.execute(f'UPDATE {table} SET {field}=? WHERE id=?',(value,record_id))
            audit_log(conn=conn,operator_id=actor,user_id=actor,actor_type='operator',
                action_type='supplier_updated' if table=='suppliers' else 'supplier_contract_updated',
                table_name=table,row_id=record_id,field_name=field,old_value=old[field],new_value=value,
                source_context='supplier_editor',comment='Редактирование карточки; документы заказов не изменены',commit=False)
            changed+=1
        if owns: conn.commit()
        return changed
    except Exception:
        if owns: conn.rollback()
        raise
    finally:
        if owns: conn.close()


def ensure_schema(conn):
    statements = [
        '''CREATE TABLE IF NOT EXISTS supplier_batch_assignments(
           supplier_batch_id INTEGER PRIMARY KEY REFERENCES remote_supplier_batches(id),
           supplier_id INTEGER NOT NULL REFERENCES suppliers(id), contract_id INTEGER REFERENCES supplier_contracts(id),
           unit_price REAL CHECK(unit_price>=0), currency TEXT NOT NULL DEFAULT 'UAH', delivery_terms TEXT,
           assigned_by TEXT NOT NULL, assigned_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS supplier_order_responses(id INTEGER PRIMARY KEY,
           purchase_order_id INTEGER NOT NULL REFERENCES supplier_purchase_orders(id),
           received_at TEXT NOT NULL, response_text TEXT NOT NULL, quantity REAL NOT NULL CHECK(quantity>0),
           expected_delivery_date TEXT NOT NULL, supplier_reference TEXT,
           recorded_by TEXT NOT NULL, created_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS suppliers(id INTEGER PRIMARY KEY, name TEXT NOT NULL,
           legal_name TEXT, tax_id TEXT, contact_person TEXT, phone TEXT, email TEXT,
           address TEXT, bank_details TEXT, note TEXT, is_active INTEGER NOT NULL DEFAULT 1,
           created_by TEXT NOT NULL, created_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS supplier_contracts(id INTEGER PRIMARY KEY,
           supplier_id INTEGER NOT NULL REFERENCES suppliers(id), contract_number TEXT,
           subject TEXT NOT NULL, valid_from TEXT, valid_to TEXT, document_reference TEXT,
           note TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS service_item_suppliers(id INTEGER PRIMARY KEY,
           service_item_code TEXT NOT NULL REFERENCES service_items(service_item_code),
           supplier_id INTEGER NOT NULL REFERENCES suppliers(id), contract_id INTEGER REFERENCES supplier_contracts(id),
           minimum_quantity INTEGER CHECK(minimum_quantity>0), unit_price REAL CHECK(unit_price>=0),
           currency TEXT NOT NULL DEFAULT 'UAH', delivery_terms TEXT, is_active INTEGER NOT NULL DEFAULT 1,
           created_by TEXT NOT NULL, created_at TEXT NOT NULL)''',
        '''CREATE UNIQUE INDEX IF NOT EXISTS ux_item_current_supplier ON service_item_suppliers(service_item_code) WHERE is_active=1''',
        '''CREATE TABLE IF NOT EXISTS supplier_purchase_orders(id INTEGER PRIMARY KEY,
           document_number TEXT NOT NULL UNIQUE, supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
           contract_id INTEGER REFERENCES supplier_contracts(id), supplier_batch_id INTEGER UNIQUE,
           service_item_code TEXT NOT NULL, quantity REAL NOT NULL, unit_price REAL,
           currency TEXT NOT NULL, document_text TEXT NOT NULL, snapshot_json TEXT NOT NULL,
           delivery_status TEXT NOT NULL DEFAULT 'INTERNAL', channel TEXT, destination TEXT,
           sent_at TEXT, delivery_error TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS supplier_order_delivery_attempts(id INTEGER PRIMARY KEY,
           purchase_order_id INTEGER NOT NULL REFERENCES supplier_purchase_orders(id),
           attempted_at TEXT NOT NULL, channel TEXT NOT NULL, destination TEXT NOT NULL,
           result TEXT NOT NULL, error_text TEXT)''',
    ]
    for sql in statements: conn.execute(sql)
    if 'document_id' not in {r[1] for r in conn.execute('PRAGMA table_info(supplier_contracts)')}:
        conn.execute('ALTER TABLE supplier_contracts ADD COLUMN document_id INTEGER REFERENCES osbb_documents(id)')
    if 'requested_delivery_date' not in {r[1] for r in conn.execute('PRAGMA table_info(supplier_batch_assignments)')}:
        conn.execute('ALTER TABLE supplier_batch_assignments ADD COLUMN requested_delivery_date TEXT')


def _audit(conn, actor, action, table, key, detail):
    audit_log(conn=conn, operator_id=actor, user_id=actor, actor_type='operator',
              action_type=action, table_name=table, row_id=key, field_name='record',
              old_value='', new_value=str(detail), source_context='supplier_procurement_core', commit=False)


def create_supplier(*, actor, name, **fields):
    if not str(actor).strip(): raise ValueError('Не определён оператор сессии.')
    if not str(name).strip(): raise ValueError('Заполните поле «Название» в форме нового контрагента.')
    conn = get_conn()
    try:
        ensure_schema(conn)
        cols = ['legal_name','tax_id','contact_person','phone','email','address','bank_details','note']
        cur = conn.execute(f"INSERT INTO suppliers(name,{','.join(cols)},created_by,created_at) VALUES ({','.join('?' for _ in range(11))})",
                           (name.strip(), *(str(fields.get(k) or '').strip() for k in cols), actor, now_db()))
        _audit(conn, actor, 'supplier_created', 'suppliers', cur.lastrowid, name)
        conn.commit(); return cur.lastrowid
    except Exception:
        conn.rollback(); raise
    finally: conn.close()


def add_contract(*, actor, supplier_id, subject, number='', valid_from=None, valid_to=None, reference='', note='', document_id=None):
    valid_from=normalize_contract_date(valid_from)
    valid_to=normalize_contract_date(valid_to)
    if not actor or not subject.strip(): raise ValueError('Нужны исполнитель и предмет договора.')
    if valid_from and valid_to and valid_to < valid_from: raise ValueError('Неверный срок договора.')
    conn = get_conn()
    try:
        ensure_schema(conn)
        if document_id is not None:
            doc=conn.execute('SELECT storage_key,relative_path FROM osbb_documents WHERE id=?',(document_id,)).fetchone()
            if not doc: raise ValueError('Документ не найден в журнале поступлений.')
            reference=json.dumps({'storage':doc['storage_key'],'path':doc['relative_path']},ensure_ascii=False)
        cur = conn.execute('INSERT INTO supplier_contracts(supplier_id,contract_number,subject,valid_from,valid_to,document_reference,note,created_by,created_at,document_id) VALUES (?,?,?,?,?,?,?,?,?,?)',
                           (supplier_id,number,subject,valid_from,valid_to,reference,note,actor,now_db(),document_id))
        _audit(conn,actor,'supplier_contract_created','supplier_contracts',cur.lastrowid,subject)
        conn.commit(); return cur.lastrowid
    except Exception:
        conn.rollback(); raise
    finally: conn.close()


def bind_supplier(*, actor, item_code, supplier_id, contract_id=None, minimum=None, unit_price=None, terms=''):
    if not actor: raise ValueError('Укажите исполнителя.')
    conn = get_conn()
    try:
        ensure_schema(conn)
        if not conn.execute('SELECT 1 FROM suppliers WHERE id=? AND is_active=1',(supplier_id,)).fetchone(): raise ValueError('Поставщик не активен.')
        if contract_id and not conn.execute('SELECT 1 FROM supplier_contracts WHERE id=? AND supplier_id=?',(contract_id,supplier_id)).fetchone(): raise ValueError('Договор принадлежит другому поставщику.')
        conn.execute('UPDATE service_item_suppliers SET is_active=0 WHERE service_item_code=? AND is_active=1',(item_code,))
        cur = conn.execute('INSERT INTO service_item_suppliers(service_item_code,supplier_id,contract_id,minimum_quantity,unit_price,delivery_terms,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)',
                           (item_code,supplier_id,contract_id,minimum,unit_price,terms,actor,now_db()))
        _audit(conn,actor,'item_supplier_bound','service_item_suppliers',cur.lastrowid,f'{item_code} → {supplier_id}')
        if minimum:
            from supplier_terms_core import set_supplier_minimum
            set_supplier_minimum(service_item_code=item_code,minimum_quantity=minimum,actor=actor,reason=f'Условия поставщика #{supplier_id}',conn=conn)
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally: conn.close()


def create_purchase_document(conn, *, batch, actor):
    """Snapshot current supplier/terms; no implicit communication or bank debit."""
    ensure_schema(conn)
    existing = conn.execute('SELECT * FROM supplier_purchase_orders WHERE supplier_batch_id=?',(batch['id'],)).fetchone()
    if existing: return dict(existing)
    binding = conn.execute('''SELECT b.*,s.name,s.legal_name,s.email,s.phone FROM service_item_suppliers b
                  JOIN suppliers s ON s.id=b.supplier_id WHERE b.service_item_code=? AND b.is_active=1 AND s.is_active=1''',(batch['service_item_code'],)).fetchone()
    assigned=conn.execute('''SELECT a.*,s.name,s.legal_name,s.email,s.phone FROM supplier_batch_assignments a
          JOIN suppliers s ON s.id=a.supplier_id WHERE a.supplier_batch_id=?''',(batch['id'],)).fetchone()
    if assigned: binding=assigned
    if not binding: raise ValueError('Сначала назначьте поставщика конкретной позиции каталога.')
    snapshot = dict(binding)
    contract = conn.execute('SELECT * FROM supplier_contracts WHERE id=?',(snapshot['contract_id'],)).fetchone() if snapshot['contract_id'] else None
    snapshot['contract'] = dict(contract) if contract else None
    qty = batch['quantity_requested']
    number = f"PO-{uuid4().hex[:12].upper()}"
    price = snapshot['unit_price']
    amount = f"{qty * price:.2f} {snapshot['currency']}" if price is not None else 'цена требует согласования'
    contract_text = f"{contract['contract_number'] or 'без номера'} — {contract['subject']}" if contract else 'не указан'
    body = f"Заказ ОСББ {number}\nПо состоянию на: {now_db()}\nПоставщик: {snapshot['name']}\nДоговор: {contract_text}\nПозиция: {batch['service_name_snapshot']} ({batch['service_item_code']})\nКоличество: {qty} шт.\nК оплате поставщику: {amount}\nУсловия: {snapshot['delivery_terms'] or 'не указаны'}\n"
    if snapshot.get('requested_delivery_date'):
        body+=f"Желаемая дата поставки: {snapshot['requested_delivery_date']} (ещё не подтверждена поставщиком)\n"
    cur = conn.execute('''INSERT INTO supplier_purchase_orders(document_number,supplier_id,contract_id,supplier_batch_id,service_item_code,quantity,unit_price,currency,document_text,snapshot_json,created_by,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',(number,snapshot['supplier_id'],snapshot['contract_id'],batch['id'],batch['service_item_code'],qty,price,snapshot['currency'],body,json.dumps(snapshot,ensure_ascii=False),actor,now_db()))
    _audit(conn,actor,'supplier_purchase_document_created','supplier_purchase_orders',cur.lastrowid,number)
    return dict(conn.execute('SELECT * FROM supplier_purchase_orders WHERE id=?',(cur.lastrowid,)).fetchone())


def assign_supplier_to_batch(conn, *, batch_id, supplier_id, contract_id, actor, unit_price=None, terms='', requested_delivery_date=None):
    """Assign an existing batch without changing its quantity or creating another batch."""
    if not str(actor).strip(): raise ValueError('Укажите исполнителя.')
    if requested_delivery_date:
        from datetime import date
        date.fromisoformat(requested_delivery_date)
    ensure_schema(conn)
    batch=conn.execute('SELECT * FROM remote_supplier_batches WHERE id=?',(batch_id,)).fetchone()
    supplier=conn.execute('SELECT * FROM suppliers WHERE id=? AND is_active=1',(supplier_id,)).fetchone()
    if not batch or not supplier: raise ValueError('Партия или активный поставщик не найдены.')
    if conn.execute('SELECT 1 FROM supplier_purchase_orders WHERE supplier_batch_id=?',(batch_id,)).fetchone():
        raise ValueError('Для партии уже сформирован документ. Смена поставщика требует отдельного пересмотра, старый документ не перезаписывается.')
    if contract_id and not conn.execute('SELECT 1 FROM supplier_contracts WHERE id=? AND supplier_id=?',(contract_id,supplier_id)).fetchone():
        raise ValueError('Выбранный договор принадлежит другому поставщику.')
    if unit_price is not None and float(unit_price)<0: raise ValueError('Цена не может быть отрицательной.')
    conn.execute('''INSERT INTO supplier_batch_assignments(supplier_batch_id,supplier_id,contract_id,unit_price,delivery_terms,assigned_by,assigned_at)
         VALUES (?,?,?,?,?,?,?) ON CONFLICT(supplier_batch_id) DO UPDATE SET supplier_id=excluded.supplier_id,
         contract_id=excluded.contract_id,unit_price=excluded.unit_price,delivery_terms=excluded.delivery_terms,
         assigned_by=excluded.assigned_by,assigned_at=excluded.assigned_at''',
         (batch_id,supplier_id,contract_id,unit_price,terms,actor,now_db()))
    conn.execute('UPDATE supplier_batch_assignments SET requested_delivery_date=? WHERE supplier_batch_id=?',(requested_delivery_date,batch_id))
    conn.execute('UPDATE remote_supplier_batches SET supplier_name=?,updated_at=? WHERE id=?',(supplier['name'],now_db(),batch_id))
    _audit(conn,actor,'supplier_assigned_to_batch','supplier_batch_assignments',batch_id,f"{supplier_id}; договор {contract_id}")
    return create_purchase_document(conn,batch=dict(batch),actor=actor)


def queue_purchase_delivery(conn, *, document_id, channel, destination, actor):
    """Explicit consent to delivery; an adapter must be configured separately."""
    if not actor or not channel.strip() or not destination.strip():
        raise ValueError('Нужны исполнитель, канал и адресат.')
    row = conn.execute('SELECT delivery_status FROM supplier_purchase_orders WHERE id=?',(document_id,)).fetchone()
    if not row or row['delivery_status'] == 'SENT': raise ValueError('Документ не найден или уже отправлен.')
    conn.execute("UPDATE supplier_purchase_orders SET delivery_status='QUEUED',channel=?,destination=?,delivery_error=NULL WHERE id=?",(channel,destination,document_id))
    _audit(conn,actor,'supplier_delivery_queued','supplier_purchase_orders',document_id,f'{channel}: {destination}')


def record_delivery_attempt(conn, *, document_id, channel, destination, success, error=''):
    """Called by channel adapters only after the actual communication attempt."""
    row=conn.execute("SELECT * FROM supplier_purchase_orders WHERE id=? AND delivery_status='QUEUED'",(document_id,)).fetchone()
    if not row or row['channel'] != channel or row['destination'] != destination:
        raise ValueError('Нет соответствующей разрешённой отправки.')
    status='SENT' if success else 'FAILED'
    conn.execute('INSERT INTO supplier_order_delivery_attempts(purchase_order_id,attempted_at,channel,destination,result,error_text) VALUES (?,?,?,?,?,?)',(document_id,now_db(),channel,destination,status,error))
    conn.execute('UPDATE supplier_purchase_orders SET delivery_status=?,sent_at=?,delivery_error=? WHERE id=?',(status,now_db() if success else None,error or None,document_id))
    _audit(conn,'channel_adapter','supplier_delivery_result','supplier_purchase_orders',document_id,status)


def record_manual_dispatch(conn, *, document_id, actor, channel, destination, evidence):
    if not evidence.strip(): raise ValueError('Укажите подтверждение отправки: ссылку, ID сообщения или описание передачи.')
    queue_purchase_delivery(conn,document_id=document_id,channel=channel,destination=destination,actor=actor)
    record_delivery_attempt(conn,document_id=document_id,channel=channel,destination=destination,success=True)
    _audit(conn,actor,'supplier_order_manually_sent','supplier_purchase_orders',document_id,evidence)


def record_supplier_response(conn, *, document_id, actor, response, quantity, expected_date, received_date, reference=''):
    from datetime import date
    if not actor.strip() or not response.strip() or float(quantity)<=0:
        raise ValueError('Нужны исполнитель, ответ и положительное количество.')
    date.fromisoformat(expected_date); date.fromisoformat(received_date)
    if not conn.execute('SELECT 1 FROM supplier_purchase_orders WHERE id=?',(document_id,)).fetchone():
        raise ValueError('Документ не найден.')
    cur=conn.execute('INSERT INTO supplier_order_responses(purchase_order_id,received_at,response_text,quantity,expected_delivery_date,supplier_reference,recorded_by,created_at) VALUES (?,?,?,?,?,?,?,?)',
                     (document_id,received_date,response,quantity,expected_date,reference,actor,now_db()))
    _audit(conn,actor,'supplier_response_recorded','supplier_order_responses',cur.lastrowid,response)
    batch=conn.execute('SELECT supplier_batch_id FROM supplier_purchase_orders WHERE id=?',(document_id,)).fetchone()
    if batch and batch[0]:
        from supplier_lifecycle_core import notify_batch
        notify_batch(conn,batch[0],f'SUPPLIER_DATE_{cur.lastrowid}',f'Постачальник повідомив: очікувана дата поставки — {expected_date}.')
