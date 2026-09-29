"""Audited record of an actual off-bot notification; never marks Telegram as sent."""

from service_orders_core import now_db
from audit_logger import audit_log


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS service_order_manual_contacts (
        id INTEGER PRIMARY KEY, service_order_id INTEGER NOT NULL,
        notice_kind TEXT NOT NULL, channel TEXT NOT NULL,
        recipient TEXT NOT NULL, message_text TEXT NOT NULL,
        evidence TEXT NOT NULL, actor TEXT NOT NULL, contacted_at TEXT NOT NULL,
        FOREIGN KEY(service_order_id) REFERENCES service_orders(id))''')
    conn.execute('''CREATE INDEX IF NOT EXISTS idx_service_manual_contacts_order
                    ON service_order_manual_contacts(service_order_id,notice_kind,id)''')


def record_manual_contact(conn, *, order_id, kind, channel, recipient, message, evidence, actor):
    if kind not in {'PAYMENT_CONFIRMED','SUPPLIER_DATE','PICKUP_READY'}:
        raise ValueError('Неизвестный вид уведомления.')
    if channel not in {'PHONE','VIBER','TELEGRAM_PERSONAL','PAPER','OTHER'}:
        raise ValueError('Выберите канал фактического сообщения.')
    if not all(str(v).strip() for v in (recipient,message,evidence,actor)):
        raise ValueError('Укажите адресата, текст, подтверждение передачи и исполнителя.')
    if not conn.execute('SELECT 1 FROM service_orders WHERE id=?',(int(order_id),)).fetchone():
        raise ValueError('Заказ не найден.')
    ensure_schema(conn)
    cur=conn.execute('''INSERT INTO service_order_manual_contacts
        (service_order_id,notice_kind,channel,recipient,message_text,evidence,actor,contacted_at)
        VALUES (?,?,?,?,?,?,?,?)''',
        (int(order_id),kind,channel,recipient.strip(),message.strip(),evidence.strip(),actor.strip(),now_db()))
    audit_log(conn=conn,operator_id=actor,user_id=actor,actor_type='local_single_user',
        action_type='service_order_manual_contact_recorded',table_name='service_order_manual_contacts',
        row_id=cur.lastrowid,field_name='notice_kind',old_value='',new_value=kind,
        source_context='order_journey',comment=f'{channel}: {evidence}',commit=False)
    return cur.lastrowid
