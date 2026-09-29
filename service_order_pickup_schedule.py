"""Schedule an actual pickup after batch stock reaches the chosen point."""

from datetime import date
from uuid import uuid4
from service_orders_core import now_db
from audit_logger import audit_log
from service_order_notifications import ensure_order_notification_schema


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS service_order_pickup_schedules (
        service_order_id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL,
        point_code TEXT NOT NULL, pickup_date TEXT NOT NULL,
        scheduled_by TEXT NOT NULL, scheduled_at TEXT NOT NULL,
        FOREIGN KEY(service_order_id) REFERENCES service_orders(id))''')


def schedule_pickup(conn, *, order_id, point, pickup_date, actor):
    day=date.fromisoformat(pickup_date)
    if day < date.today():
        raise ValueError('Дата выдачи не может быть в прошлом.')
    if not actor.strip():
        raise ValueError('Не указан исполнитель.')
    row=conn.execute('''SELECT o.apartment_number,o.telegram_user_id,l.supplier_batch_id,l.quantity,
        l.issued_quantity,b.batch_number,p.point_code
        FROM service_orders o JOIN remote_supplier_batch_links l ON l.service_order_id=o.id
        JOIN remote_supplier_batches b ON b.id=l.supplier_batch_id
        LEFT JOIN service_order_pickup_plans p ON p.service_order_id=o.id
        WHERE o.id=?''',(order_id,)).fetchone()
    if not row:
        raise ValueError('Заказ не включён в партию.')
    if row['point_code'] != point:
        raise ValueError('Сначала сохраните эту точку выдачи в карточке заказа.')
    remaining=int(row['quantity'])-int(row['issued_quantity'])
    if remaining<=0:
        raise ValueError('Заказ уже выдан.')
    stock=conn.execute('''SELECT COALESCE(SUM(ib.quantity),0) FROM inventory_balances ib
        JOIN inventory_lots lot ON lot.id=ib.lot_id
        WHERE lot.source_kind='REMOTE_SUPPLIER_BATCH' AND lot.source_id=?
          AND ib.location_code=?''',(row['supplier_batch_id'],point)).fetchone()[0]
    if int(stock or 0)<remaining:
        raise ValueError('В выбранной точке ещё нет нужного количества этой партии.')
    ensure_schema(conn)
    old=conn.execute('SELECT point_code,pickup_date FROM service_order_pickup_schedules WHERE service_order_id=?',(order_id,)).fetchone()
    if old and old['point_code']==point and old['pickup_date']==pickup_date:
        return {'changed':False,'queued':False}
    conn.execute('''INSERT INTO service_order_pickup_schedules
        (service_order_id,batch_id,point_code,pickup_date,scheduled_by,scheduled_at)
        VALUES (?,?,?,?,?,?) ON CONFLICT(service_order_id) DO UPDATE SET
        batch_id=excluded.batch_id,point_code=excluded.point_code,
        pickup_date=excluded.pickup_date,scheduled_by=excluded.scheduled_by,
        scheduled_at=excluded.scheduled_at''',
        (order_id,row['supplier_batch_id'],point,pickup_date,actor,now_db()))
    queued=False
    if row['telegram_user_id']:
        ensure_order_notification_schema(conn)
        conn.execute('''UPDATE service_order_notifications SET delivery_status='CANCELLED'
            WHERE service_order_id=? AND notification_kind LIKE 'PICKUP_READY_%'
              AND delivery_status IN ('READY','FAILED')''',(order_id,))
        message=(f"📦 Пульти з партії {row['batch_number']} готові до видачі.\n"
                 f"Квартира {row['apartment_number']} · {remaining} шт.\n"
                 f"Дата: {day:%d.%m.%Y}. Місце видачі: {point}.")
        cur=conn.execute('''INSERT OR IGNORE INTO service_order_notifications
            (service_order_id,notification_kind,telegram_user_id,message_text,created_at)
            VALUES (?,?,?,?,?)''',
            (order_id,f'PICKUP_READY_{uuid4().hex}',str(row['telegram_user_id']),message,now_db()))
        queued=bool(cur.rowcount)
    audit_log(conn=conn,operator_id=actor,user_id=actor,actor_type='local_single_user',
        action_type='service_order_pickup_scheduled',table_name='service_order_pickup_schedules',
        row_id=order_id,field_name='point_code,pickup_date',
        old_value=f"{old['point_code']},{old['pickup_date']}" if old else '',
        new_value=f'{point},{pickup_date}',source_context='order_journey',commit=False)
    return {'changed':True,'queued':queued}
