"""Generic counterparties, item-specific supply agreements and durable orders.

No external delivery is inferred from document creation. A future channel
adapter reads QUEUED documents, records attempts and marks SENT only on success.
"""
import json
from uuid import uuid4
from audit_logger import audit_log
from service_orders_core import get_conn, now_db


def ensure_schema(conn):
    statements = [
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


def _audit(conn, actor, action, table, key, detail):
    audit_log(conn=conn, operator_id=actor, user_id=actor, actor_type='operator',
              action_type=action, table_name=table, row_id=key, field_name='record',
              old_value='', new_value=str(detail), source_context='supplier_procurement_core', commit=False)


def create_supplier(*, actor, name, **fields):
    if not str(actor).strip() or not str(name).strip(): raise ValueError('Нужны исполнитель и название поставщика.')
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


def add_contract(*, actor, supplier_id, subject, number='', valid_from=None, valid_to=None, reference='', note=''):
    from datetime import date
    for value in (valid_from, valid_to):
        if value: date.fromisoformat(value)
    if not actor or not subject.strip(): raise ValueError('Нужны исполнитель и предмет договора.')
    if valid_from and valid_to and valid_to < valid_from: raise ValueError('Неверный срок договора.')
    conn = get_conn()
    try:
        ensure_schema(conn)
        cur = conn.execute('INSERT INTO supplier_contracts(supplier_id,contract_number,subject,valid_from,valid_to,document_reference,note,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                           (supplier_id,number,subject,valid_from,valid_to,reference,note,actor,now_db()))
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
    cur = conn.execute('''INSERT INTO supplier_purchase_orders(document_number,supplier_id,contract_id,supplier_batch_id,service_item_code,quantity,unit_price,currency,document_text,snapshot_json,created_by,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',(number,snapshot['supplier_id'],snapshot['contract_id'],batch['id'],batch['service_item_code'],qty,price,snapshot['currency'],body,json.dumps(snapshot,ensure_ascii=False),actor,now_db()))
    _audit(conn,actor,'supplier_purchase_document_created','supplier_purchase_orders',cur.lastrowid,number)
    return dict(conn.execute('SELECT * FROM supplier_purchase_orders WHERE id=?',(cur.lastrowid,)).fetchone())


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
