#!/usr/bin/env python3
"""Purge the unused TEST_REMOTE_* training branch, with one DB backup."""
from __future__ import annotations
import argparse, shutil, sqlite3
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB_DEFAULT=ROOT/'Data/db/osbb_test.db'
BACKUPS=ROOT/'Data/db/backups'
MIGRATION='2026-09-22_021_purge_legacy_remote_tests'

def exists(c,t): return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None
def cols(c,t): return {r[1] for r in c.execute(f'PRAGMA table_info("{t}")')}
def ids(c,sql,args=()): return [int(r[0]) for r in c.execute(sql,args).fetchall() if r[0] is not None]
def delete_ids(c,t,col,values):
    if values and exists(c,t) and col in cols(c,t):
        c.execute(f'DELETE FROM "{t}" WHERE "{col}" IN ({",".join("?"*len(values))})',values)

def plan(c):
    orders=ids(c,"SELECT id FROM service_orders WHERE service_item_code LIKE 'TEST_REMOTE_%'")
    interests=ids(c,"SELECT id FROM service_order_interests WHERE service_item_code LIKE 'TEST_REMOTE_%'")
    notices=ids(c,"SELECT payment_notice_id FROM service_order_interests WHERE id IN (%s)" % ','.join('?'*len(interests)),interests) if interests else []
    payments=ids(c,"SELECT payment_id FROM service_order_interests WHERE id IN (%s)" % ','.join('?'*len(interests)),interests) if interests else []
    if orders and exists(c,'service_order_payment_links'):
        payments+=ids(c,"SELECT payment_id FROM service_order_payment_links WHERE service_order_id IN (%s)" % ','.join('?'*len(orders)),orders)
    payments=sorted(set(payments)); notices=sorted(set(notices))
    batches=ids(c,"SELECT id FROM remote_supplier_batches WHERE service_item_code LIKE 'TEST_REMOTE_%'")
    assets=ids(c,"SELECT id FROM remote_assets WHERE asset_number LIKE 'TEST-%' OR note LIKE '%тестовый%' OR note LIKE '%live sandbox%'")
    if orders and exists(c,'remote_order_issued_assets'):
        assets+=ids(c,"SELECT remote_asset_id FROM remote_order_issued_assets WHERE service_order_id IN (%s)" % ','.join('?'*len(orders)),orders)
    return {'orders':orders,'interests':interests,'notices':notices,'payments':payments,'batches':batches,'assets':sorted(set(assets))}

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--db',type=Path,default=DB_DEFAULT); p.add_argument('--apply',action='store_true'); a=p.parse_args(); db=a.db.resolve()
    with sqlite3.connect(db) as c: x=plan(c)
    print('DB:',db); print('Plan:',', '.join(f'{k}={len(v)}' for k,v in x.items()))
    if not a.apply: print('DRY RUN ONLY — no changes saved.'); return
    BACKUPS.mkdir(parents=True,exist_ok=True); backup=BACKUPS/f'before_{MIGRATION}_{datetime.now():%Y%m%d-%H%M%S}.db'; shutil.copy2(db,backup)
    with sqlite3.connect(db) as c:
        c.execute('PRAGMA foreign_keys=OFF'); x=plan(c); o,i,n,pay,b,aids=(x[k] for k in ('orders','interests','notices','payments','batches','assets'))
        fulfills=ids(c,"SELECT id FROM order_fulfillments WHERE service_order_id IN (%s)" % ','.join('?'*len(o)),o) if o else []
        fi=ids(c,"SELECT id FROM order_fulfillment_items WHERE fulfillment_id IN (%s)" % ','.join('?'*len(fulfills)),fulfills) if fulfills else []
        delete_ids(c,'order_fulfillment_events','fulfillment_item_id',fi); delete_ids(c,'order_fulfillment_events','fulfillment_id',fulfills); delete_ids(c,'order_fulfillment_items','fulfillment_id',fulfills); delete_ids(c,'order_fulfillments','id',fulfills)
        for t,col in [('remote_handover_events','service_order_id'),('remote_asset_movements','service_order_id'),('remote_order_issued_assets','service_order_id'),('remote_supplier_batch_links','service_order_id'),('service_order_events','service_order_id'),('service_order_steps','service_order_id'),('service_order_charge_links','service_order_id'),('service_order_payment_links','service_order_id')]: delete_ids(c,t,col,o)
        delete_ids(c,'remote_asset_movements','remote_asset_id',aids); delete_ids(c,'remote_handover_events','remote_asset_id',aids); delete_ids(c,'remote_assets','id',aids); delete_ids(c,'remote_supplier_batch_links','supplier_batch_id',b); delete_ids(c,'remote_supplier_batches','id',b)
        for t,col in [('payment_allocations','payment_id'),('cashier_receipts','payment_id'),('cashbox_operations','payment_id')]: delete_ids(c,t,col,pay)
        delete_ids(c,'payments','id',pay); delete_ids(c,'payment_notices','id',n); delete_ids(c,'service_orders','id',o); delete_ids(c,'service_order_interests','id',i)
        codes=[r[0] for r in c.execute("SELECT service_item_code FROM service_items WHERE service_item_code LIKE 'TEST_REMOTE_%'")];
        for t,col in [('service_price_versions','service_item_code'),('service_item_workflows','service_item_code'),('service_items','service_item_code')]:
            if codes: c.execute(f'DELETE FROM {t} WHERE {col} IN ({",".join("?"*len(codes))})',codes)
        c.execute("DELETE FROM service_catalog WHERE service_code LIKE 'TEST_REMOTE_%'")
        c.execute("INSERT INTO audit_log(event_time,username,table_name,record_id,action,field_name,old_value,new_value,comment,actor_role,actor_name,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(datetime.now().strftime('%Y-%m-%d %H:%M:%S'),'system','legacy_remote_tests','TEST_REMOTE_*','migration','*','',str(x),'Удалён неиспользованный учебный контур пультов.','system','migration',MIGRATION))
        c.commit()
    print('APPLIED. Backup:',backup)
if __name__=='__main__': main()
