"""Local operator adapter for delivery, pickup plans and resident messages."""
from service_orders_core import now_db
from audit_logger import audit_log

def ensure_pickup_plans(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS service_order_pickup_plans(
      service_order_id INTEGER PRIMARY KEY REFERENCES service_orders(id), point_code TEXT NOT NULL,
      changed_by TEXT NOT NULL, changed_at TEXT NOT NULL)''')

def batch_residents(conn,batch_id):
    ensure_pickup_plans(conn)
    return [dict(r) for r in conn.execute('''SELECT o.id,o.apartment_number,o.telegram_user_id,l.quantity,l.link_status,
        COALESCE(p.point_code,x.claimed_cashbox,'CS') AS pickup_point
        FROM remote_supplier_batch_links l JOIN service_orders o ON o.id=l.service_order_id
        LEFT JOIN service_order_interests i ON i.service_order_id=o.id
        LEFT JOIN service_interest_intake x ON x.interest_id=i.id
        LEFT JOIN service_order_pickup_plans p ON p.service_order_id=o.id WHERE l.supplier_batch_id=?''',(batch_id,))]

def notify_batch(conn,batch_id,kind,text):
    from service_order_notifications import ensure_order_notification_schema
    ensure_order_notification_schema(conn)
    for resident in batch_residents(conn,batch_id):
        if resident['telegram_user_id']:
            message=f"📦 Нові пульти · кв. {resident['apartment_number']}, {resident['quantity']} шт.\n{text}"
            conn.execute('''INSERT OR IGNORE INTO service_order_notifications(service_order_id,notification_kind,telegram_user_id,message_text,created_at)
                 VALUES (?,?,?,?,?)''',(resident['id'],kind,str(resident['telegram_user_id']),message,now_db()))

def set_pickup(conn,*,order_id,point,actor):
    from inventory_transfer_core import ensure_inventory_schema
    ensure_inventory_schema(conn); ensure_pickup_plans(conn)
    if not actor.strip(): raise ValueError('Не определён оператор.')
    if not conn.execute('SELECT 1 FROM inventory_locations WHERE location_code=? AND is_active=1',(point,)).fetchone(): raise ValueError('Точка выдачи недоступна.')
    old=conn.execute('SELECT point_code FROM service_order_pickup_plans WHERE service_order_id=?',(order_id,)).fetchone()
    conn.execute('''INSERT INTO service_order_pickup_plans VALUES (?,?,?,?) ON CONFLICT(service_order_id)
        DO UPDATE SET point_code=excluded.point_code,changed_by=excluded.changed_by,changed_at=excluded.changed_at''',(order_id,point,actor,now_db()))
    conn.execute('UPDATE order_fulfillments SET pickup_point_code=? WHERE service_order_id=?',(point,order_id))
    audit_log(conn=conn,operator_id=actor,user_id=actor,actor_type='operator',action_type='pickup_point_selected',
      table_name='service_order_pickup_plans',row_id=order_id,field_name='point_code',old_value=old[0] if old else '',new_value=point,source_context='supplier_lifecycle',commit=False)

def receive_batch_local(conn,*,batch_id,quantity,actor,evidence):
    if not actor.strip() or not evidence.strip(): raise ValueError('Укажите оператора и основание получения.')
    from service_preorders_core import receive_supplier_batch
    result=receive_supplier_batch(batch_id=batch_id,received_now=quantity,actor_id=None,note=f'{actor}: {evidence}',conn=conn)
    notify_batch(conn,batch_id,f"ARRIVAL_{result['quantity_received']}",
      'Партія прибула на центральний склад. Про готовність до видачі у вашій точці повідомимо окремо.')
    audit_log(conn=conn,operator_id=actor,user_id=actor,actor_type='local_single_user',action_type='supplier_batch_received_local',table_name='remote_supplier_batches',row_id=batch_id,field_name='quantity_received',old_value='',new_value=result['quantity_received'],source_context='supplier_lifecycle',comment=evidence,commit=False)
    return result

def issue_local(conn,*,order_id,point,actor,evidence):
    if not actor.strip() or not evidence.strip(): raise ValueError('Укажите оператора и подтверждение фактической выдачи.')
    from service_preorders_core import issue_new_remotes_from_batch
    result=issue_new_remotes_from_batch(service_order_id=order_id,actor_id=None,source_location_code=point,note=f'{actor}: {evidence}',conn=conn)
    audit_log(conn=conn,operator_id=actor,user_id=actor,actor_type='local_single_user',action_type='resident_remotes_issued_local',table_name='service_orders',row_id=order_id,field_name='issue',old_value='',new_value=point,source_context='supplier_lifecycle',comment=evidence,commit=False)
    return result
