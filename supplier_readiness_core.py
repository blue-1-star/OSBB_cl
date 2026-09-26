"""Read-only waiting list and payment channels; not an order sent to supplier."""
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
