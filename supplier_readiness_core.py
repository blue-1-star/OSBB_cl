"""Read-only waiting list and payment channels; not an order sent to supplier."""
ORDER_REGISTER_VERSION = 3
def waiting_cash_by_account(conn, item_code):
    """Original cash receipts for this waiting pool, not current reserved funds."""
    from cash_claim_points_core import cash_account_label
    rows=conn.execute('''SELECT p.cashbox_code,SUM(l.amount) AS amount
        FROM service_order_interests i JOIN service_order_payment_links l ON l.service_order_id=i.service_order_id
        JOIN payments p ON p.id=l.payment_id WHERE i.service_item_code=? AND i.interest_status<>'CANCELLED'
        AND p.cashbox_code<>'BANK' AND COALESCE(p.payment_method,'')<>'bank'
        AND NOT EXISTS(SELECT 1 FROM remote_supplier_batch_links b WHERE b.service_order_id=i.service_order_id)
        GROUP BY p.cashbox_code''',(item_code,))
    return {cash_account_label(conn,r['cashbox_code']):float(r['amount']) for r in rows}

def supplier_waiting_list(conn, item_code):
    from cash_claim_points_core import cash_account_label
    result = []
    for row in conn.execute('''SELECT i.id,i.apartment_number,i.quantity,i.amount_due_snapshot,
           i.service_order_id,i.payment_id FROM service_order_interests i
           WHERE i.service_item_code=? AND i.interest_status<>'CANCELLED'
           AND NOT EXISTS (SELECT 1 FROM remote_supplier_batch_links b WHERE b.service_order_id=i.service_order_id)
           ORDER BY CAST(i.apartment_number AS INTEGER),i.id''', (item_code,)):
        payments = [dict(p) for p in conn.execute('''SELECT p.id,p.payment_method,p.cashbox_code,
                    SUM(l.amount) AS amount FROM service_order_payment_links l
                    JOIN payments p ON p.id=l.payment_id WHERE l.service_order_id=? GROUP BY p.id''',
                    (row['service_order_id'],))] if row['service_order_id'] else []
        if not payments and row['payment_id']:
            payments = [dict(p) for p in conn.execute('SELECT id,payment_method,cashbox_code,amount FROM payments WHERE id=?', (row['payment_id'],))]
        bank = sum(float(p['amount']) for p in payments if p['payment_method']=='bank' or p['cashbox_code']=='BANK')
        cash = sum(float(p['amount']) for p in payments if p['payment_method']!='bank' and p['cashbox_code']!='BANK')
        result.append({'Квартира':row['apartment_number'],'Количество':row['quantity'],
                       'К оплате':float(row['amount_due_snapshot']), 'Наличные':cash,'Банк':bank,
                       'Осталось':max(0,round(float(row['amount_due_snapshot'])-cash-bank,2)),
                       'Кассы приёма':', '.join(sorted({cash_account_label(conn,p['cashbox_code']) or 'не указана' for p in payments if p['cashbox_code']!='BANK'})),
                       'Состояние':'Оплаченный заказ' if row['service_order_id'] else 'Ожидает / сверка'})
    return result


def supplier_order_register(conn, item_code):
    """Persistent customer ledger: a batch moves orders out of collection, not out of history."""
    from cash_claim_points_core import cash_account_label
    from service_order_pickup_schedule import ensure_schema as ensure_pickup_schedule
    ensure_pickup_schedule(conn)
    rows = conn.execute('''SELECT i.id AS interest_id,i.apartment_number,i.quantity,
            i.amount_due_snapshot,i.service_order_id,i.payment_id,i.interest_status,
            o.telegram_user_id,o.order_status,
            b.id AS batch_id,b.batch_number,b.batch_status,b.ordered_at,
            l.created_at AS batch_linked_at,l.link_status,l.issued_quantity,
            po.sent_at AS supplier_sent_at,
            ps.pickup_date,ps.point_code AS scheduled_pickup_point,
            COALESCE(pp.point_code,x.claimed_cashbox,'CS') AS pickup_point,
            (SELECT MAX(p.created_at) FROM service_order_payment_links pl
             JOIN payments p ON p.id=pl.payment_id
             WHERE pl.service_order_id=o.id) AS payment_recorded_at,
            (SELECT r.expected_delivery_date FROM supplier_order_responses r
             WHERE r.purchase_order_id=po.id ORDER BY r.id DESC LIMIT 1) AS expected_delivery_date,
            (SELECT r.received_at FROM supplier_order_responses r
             WHERE r.purchase_order_id=po.id ORDER BY r.id DESC LIMIT 1) AS supplier_replied_at
        FROM service_order_interests i
        LEFT JOIN service_orders o ON o.id=i.service_order_id
        LEFT JOIN remote_supplier_batch_links l ON l.service_order_id=o.id
        LEFT JOIN remote_supplier_batches b ON b.id=l.supplier_batch_id
        LEFT JOIN supplier_purchase_orders po ON po.supplier_batch_id=b.id
        LEFT JOIN service_order_pickup_schedules ps ON ps.service_order_id=o.id
        LEFT JOIN service_order_pickup_plans pp ON pp.service_order_id=o.id
        LEFT JOIN service_interest_intake x ON x.interest_id=i.id
        WHERE i.service_item_code=? AND i.interest_status<>'CANCELLED'
        ORDER BY b.id DESC,CAST(i.apartment_number AS INTEGER),i.id''',(item_code,)).fetchall()
    result=[]
    for row in rows:
        item=dict(row)
        order_id=item['service_order_id']
        item['point_stock']=0
        if item['batch_id']:
            item['point_stock']=int(conn.execute('''SELECT COALESCE(SUM(ib.quantity),0)
                FROM inventory_balances ib JOIN inventory_lots lot ON lot.id=ib.lot_id
                WHERE lot.source_kind='REMOTE_SUPPLIER_BATCH' AND lot.source_id=?
                  AND ib.location_code=?''',(item['batch_id'],item['pickup_point'])).fetchone()[0] or 0)
        payments=[dict(p) for p in conn.execute('''SELECT p.payment_method,p.cashbox_code,p.operator_id,
                SUM(l.amount) AS amount FROM service_order_payment_links l
                JOIN payments p ON p.id=l.payment_id WHERE l.service_order_id=? GROUP BY p.id''',
                (order_id,))] if order_id else []
        if not payments and item['payment_id']:
            payments=[dict(p) for p in conn.execute(
                'SELECT payment_method,cashbox_code,operator_id,amount FROM payments WHERE id=?',
                (item['payment_id'],))]
        item['cash']=sum(float(p['amount']) for p in payments if p['cashbox_code']!='BANK' and p['payment_method']!='bank')
        item['bank']=sum(float(p['amount']) for p in payments if p['cashbox_code']=='BANK' or p['payment_method']=='bank')
        item['cash_points']=', '.join(sorted({cash_account_label(conn,p['cashbox_code']) for p in payments
                                             if p['cashbox_code']!='BANK'}))
        item['payment_actors']=', '.join(sorted({str(p['operator_id']) for p in payments if p['operator_id']}))
        notifications=[dict(n) for n in conn.execute('''SELECT notification_kind,delivery_status,created_at,sent_at,delivery_error
            FROM service_order_notifications WHERE service_order_id=? ORDER BY id DESC''',(order_id,))] if order_id else []
        item['payment_notice']=next((n for n in notifications if n['notification_kind']=='PAYMENT_CONFIRMED'),None)
        item['supplier_date_notice']=next((n for n in notifications if n['notification_kind'].startswith('SUPPLIER_DATE_')),None)
        item['pickup_notice']=next((n for n in notifications if n['notification_kind'].startswith('PICKUP_READY_')),None)
        result.append(item)
    return result
