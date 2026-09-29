"""Alternative, order-first view of the existing service fulfillment workflow."""

from datetime import date
from pathlib import Path
import sys

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from admin_console.utils.db import get_conn
from admin_console.utils.session_actor import session_actor
from cash_claim_points_core import list_claim_points
from service_interest_intake_core import record_cash_handover_claim
from service_cash_claims_core import confirm_claim_cash
from supplier_lifecycle_core import set_pickup, issue_local
import supplier_readiness_core
if getattr(supplier_readiness_core, 'ORDER_REGISTER_VERSION', 0) < 3:
    from importlib import reload
    supplier_readiness_core = reload(supplier_readiness_core)
supplier_order_register = supplier_readiness_core.supplier_order_register
from service_order_manual_contacts import ensure_schema as ensure_manual_contacts, record_manual_contact
from service_order_pickup_schedule import ensure_schema as ensure_pickup_schedule, schedule_pickup
from ui_dates import display_date


def mark(ok):
    return '☑' if ok else '☐'


def notification_state(telegram, manual, recipient):
    if telegram and telegram['delivery_status'] == 'SENT':
        return f"☑ Бот · {display_date(telegram['sent_at'], with_time=True)}"
    if manual:
        return f"☑ {manual['channel']} · {display_date(manual['contacted_at'], with_time=True)}"
    if telegram:
        return f"◷ Бот: {telegram['delivery_status']}"
    return '☐ Нет адресата' if not recipient else '☐ Не отправлено'


st.set_page_config(page_title='Сквозная ведомость заказов',page_icon='📋',layout='wide')
st.title('📋 Сквозная ведомость заказов — вариант интерфейса')
st.caption('Сравнительная версия рядом с «📦 Исполнение заказов». Те же заказы и БД; отдельной очереди нет.')
actor=session_actor()
conn=get_conn()
conn.row_factory=__import__('sqlite3').Row
ensure_manual_contacts(conn)
ensure_pickup_schedule(conn)
conn.commit()

items=[r[0] for r in conn.execute('SELECT DISTINCT service_item_code FROM service_order_interests ORDER BY service_item_code')]
if not items:
    st.info('Заказов и намерений пока нет.')
    conn.close(); st.stop()
item=st.selectbox('Товар / услуга',items,index=items.index('REMOTE_NEW') if 'REMOTE_NEW' in items else 0)
view=st.radio('Показать',['Весь путь','В работе','Выданные'],horizontal=True)
records=supplier_order_register(conn,item)
manuals={}
for record in records:
    if record['service_order_id']:
        manuals[record['service_order_id']]=[dict(r) for r in conn.execute(
            '''SELECT notice_kind,channel,recipient,contacted_at,evidence,message_text
               FROM service_order_manual_contacts WHERE service_order_id=? ORDER BY id DESC''',
            (record['service_order_id'],))]

def manual_for(record,kind):
    return next((r for r in manuals.get(record['service_order_id'],[]) if r['notice_kind']==kind),None)

def stage(record):
    if record['service_order_id'] and int(record.get('issued_quantity') or 0)>=int(record['quantity']):
        return 'Выдано'
    if record['batch_number']:
        if record['link_status']=='READY':
            remaining=int(record['quantity'])-int(record.get('issued_quantity') or 0)
            return ('Готово к выдаче' if record.get('point_stock',0)>=remaining
                    else 'Поставка получена · доставить в точку')
        if record['expected_delivery_date']:
            return 'Ожидается поставка'
        if record['supplier_sent_at']:
            return 'Заказано поставщику'
        return 'В партии'
    if record['service_order_id']:
        return 'Оплачено · сбор партии'
    if record['interest_status']=='PAYMENT_NOTICE':
        return 'Ожидается подтверждение оплаты'
    return 'Намерение'

shown=[r for r in records if view=='Весь путь' or
       (view=='Выданные' and stage(r)=='Выдано') or
       (view=='В работе' and stage(r)!='Выдано')]
st.caption('Строка остаётся в ведомости при смене статуса. ☑ означает подтверждённое событие, ◷ — сообщение ожидает доставки.')
table=[{
    'Намерение':r['interest_id'],'Квартира':r['apartment_number'],'Кол-во':r['quantity'],
    'Этап':stage(r),'Оплата':mark(bool(r['service_order_id'])),
    'Принято':r['cash_points'] or ('Банк' if r['bank'] else '—'),
    'Отметил приём':r.get('payment_actors') or '—',
    'Ответ об оплате':notification_state(r['payment_notice'],manual_for(r,'PAYMENT_CONFIRMED'),r['telegram_user_id']),
    'Партия':r['batch_number'] or '—',
    'Дата поставки':display_date(r['expected_delivery_date']),
    'Ответ о поставке':notification_state(r['supplier_date_notice'],manual_for(r,'SUPPLIER_DATE'),r['telegram_user_id']) if r['expected_delivery_date'] else '—',
    'Выдача назначена':f"{display_date(r['pickup_date'])} · {r['scheduled_pickup_point']}" if r['pickup_date'] else '—',
    'Сообщение о выдаче':notification_state(r['pickup_notice'],manual_for(r,'PICKUP_READY'),r['telegram_user_id']) if r['pickup_date'] else '—',
    'Остаток в точке':f"{r.get('point_stock',0)} · {r.get('pickup_point') or '—'}" if r['batch_number'] else '—',
    'Выдано':f"{int(r.get('issued_quantity') or 0)}/{r['quantity']}",
} for r in shown]
selection=st.dataframe(pd.DataFrame(table),hide_index=True,width='stretch',
    on_select='rerun',selection_mode='single-row',key='journey_selection')
selected_indices=selection.selection.rows
if not selected_indices:
    st.info('Выберите строку ведомости: откроется карточка заказа и доступные для его этапа действия.')
    conn.close(); st.stop()
row=shown[selected_indices[0]]
order_id=row['service_order_id']
st.markdown(f"### Квартира {row['apartment_number']} · {row['quantity']} шт. · {stage(row)}")
st.write(f"Намерение #{row['interest_id']} · Заказ {order_id or 'ещё не создан'} · Партия {row['batch_number'] or 'ещё не сформирована'}")
st.write(f"К оплате: **{float(row['amount_due_snapshot']):.2f} грн** · Получено: **{row['cash']+row['bank']:.2f} грн**")
if row['batch_number']:
    st.write(f"Заказ поставщику отправлен: {display_date(row['supplier_sent_at'],with_time=True)}. "
             f"Ожидаемая дата: {display_date(row['expected_delivery_date'])}.")
    st.write(f"В точке {row['pickup_point']} сейчас **{row['point_stock']} шт.** этой партии; заказу нужно {int(row['quantity'])-int(row.get('issued_quantity') or 0)} шт.")

intake=conn.execute('SELECT * FROM service_interest_intake WHERE interest_id=?',(row['interest_id'],)).fetchone()
if intake:
    st.caption(f"Исходное сообщение: {intake['original_message']} · источник: {intake['source_channel']}")

if not order_id and row['interest_status']=='INTEREST':
    with st.expander('💰 Уточнить сообщение о передаче денег'):
        points=[p['point_code'] for p in list_claim_points(conn=conn)]
        with st.form(f'claim_{row["interest_id"]}'):
            point=st.selectbox('По словам жителя, куда переданы деньги',points)
            amount=st.number_input('Названная сумма, грн',min_value=0.01,value=float(row['amount_due_snapshot']))
            note=st.text_input('Основание / уточнение сообщения')
            save=st.form_submit_button('Записать заявление — не проводить оплату')
        if save:
            try:
                record_cash_handover_claim(interest_id=row['interest_id'],point_code=point,
                    amount=amount,actor=actor,source_note=note,conn=conn)
                conn.commit(); st.rerun()
            except Exception as exc:
                conn.rollback(); st.error(str(exc))

if intake and intake['claimed_cash_handover'] and intake['verification_status']=='UNVERIFIED' and not order_id:
    with st.expander('✅ Подтвердить фактический приём денег'):
        st.caption('Это кассовая операция, а не отметка в ведомости. Проводка и заказ создаются существующим модулем.')
        with st.form(f'confirm_{row["interest_id"]}'):
            receiving=st.selectbox('Кто фактически принял',list(dict.fromkeys([intake['claimed_cashbox'],'C','BANK','O']+
                [p['point_code'] for p in list_claim_points(conn=conn) if p['point_code'].startswith('KAS')])))
            actual=st.number_input('Фактически получено, грн',min_value=0.01,value=float(row['amount_due_snapshot']))
            evidence=st.text_input('Квитанция / акт / ID банковской операции *')
            bank_day=st.date_input('Дата банковской операции',max_value=date.today(),format='DD.MM.YYYY')
            checked=st.checkbox('Деньги фактически получены')
            confirm=st.form_submit_button('Подтвердить приём и провести оплату')
        if confirm:
            try:
                if not checked: raise ValueError('Подтвердите получение денег.')
                confirm_claim_cash(interest_id=row['interest_id'],receiving_point=receiving,
                    actor=actor,evidence=evidence,actual_amount=actual,
                    transaction_date=bank_day.isoformat() if receiving=='BANK' else None,conn=conn)
                conn.commit(); st.rerun()
            except Exception as exc:
                conn.rollback(); st.error(str(exc))

if order_id:
    st.markdown('#### Связь с жителем')
    st.write('Подтверждение оплаты: '+notification_state(row['payment_notice'],manual_for(row,'PAYMENT_CONFIRMED'),row['telegram_user_id']))
    if row['expected_delivery_date']:
        st.write('Дата поставки: '+notification_state(row['supplier_date_notice'],manual_for(row,'SUPPLIER_DATE'),row['telegram_user_id']))
    if not row['telegram_user_id'] or (row['payment_notice'] and row['payment_notice']['delivery_status']=='FAILED'):
        with st.expander('📨 Зафиксировать сообщение вне бота'):
            st.caption('Только после реального звонка/отправки. Запись не меняет статус Telegram-доставки.')
            kinds=['PAYMENT_CONFIRMED']
            if row['expected_delivery_date']: kinds.append('SUPPLIER_DATE')
            if row['pickup_date']: kinds.append('PICKUP_READY')
            with st.form(f'manual_{order_id}'):
                kind=st.selectbox('О чём сообщили',kinds,format_func=lambda v:{'PAYMENT_CONFIRMED':'Оплата подтверждена','SUPPLIER_DATE':'Дата поставки','PICKUP_READY':'Дата и место выдачи'}[v])
                channel=st.selectbox('Канал',['PHONE','VIBER','TELEGRAM_PERSONAL','PAPER','OTHER'])
                recipient=st.text_input('Кому сообщили *')
                message=st.text_area('Что сообщили *')
                evidence=st.text_input('Подтверждение фактической передачи *')
                sent=st.checkbox('Сообщение действительно передано')
                save=st.form_submit_button('Записать ручное уведомление')
            if save:
                try:
                    if not sent: raise ValueError('Нельзя отмечать неотправленное сообщение как доставленное.')
                    record_manual_contact(conn,order_id=order_id,kind=kind,channel=channel,
                        recipient=recipient,message=message,evidence=evidence,actor=actor)
                    conn.commit(); st.rerun()
                except Exception as exc:
                    conn.rollback(); st.error(str(exc))

if order_id and row['batch_number']:
    st.markdown('#### Партия и выдача')
    plan=conn.execute('SELECT point_code FROM service_order_pickup_plans WHERE service_order_id=?',(order_id,)).fetchone()
    points=[r[0] for r in conn.execute(
        "SELECT location_code FROM inventory_locations WHERE is_active=1 "
        "AND location_code NOT IN ('BANK','K') ORDER BY location_code")]
    current_point=plan[0] if plan else intake['claimed_cashbox'] if intake and intake['claimed_cashbox'] in points else 'CS'
    with st.form(f'pickup_{order_id}'):
        pickup=st.selectbox('Точка выдачи',points,index=points.index(current_point) if current_point in points else 0)
        save=st.form_submit_button('Сохранить точку выдачи')
    if save:
        try:
            set_pickup(conn,order_id=order_id,point=pickup,actor=actor)
            conn.commit(); st.rerun()
        except Exception as exc:
            conn.rollback(); st.error(str(exc))
    with st.form(f'schedule_{order_id}'):
        pickup_day=st.date_input('Дата выдачи жителю',value=date.fromisoformat(row['pickup_date']) if row['pickup_date'] else None,format='DD.MM.YYYY')
        schedule=st.form_submit_button('Назначить выдачу и подготовить уведомление',disabled=not pickup_day)
    if schedule:
        try:
            result=schedule_pickup(conn,order_id=order_id,point=current_point,
                pickup_date=pickup_day.isoformat(),actor=actor)
            conn.commit()
            if not result['changed']:
                st.info('Дата и место выдачи уже сохранены.')
            else:
                st.rerun()
        except Exception as exc:
            conn.rollback(); st.error(str(exc))
    st.caption('Поступление партии и передача между точками пока выполняются в карточке партии на странице «Поставщики и закупки».')
    st.info('Для поступления партии и складской передачи откройте в меню «Поставщики и закупки» и выберите документ этой партии.')
    if row['link_status']=='READY' and int(row.get('issued_quantity') or 0)<int(row['quantity']):
        with st.form(f'issue_{order_id}'):
            evidence=st.text_input('Подтверждение фактической выдачи жителю *')
            checked=st.checkbox('Пульты действительно выданы')
            issue=st.form_submit_button('Зафиксировать выдачу',
                disabled=row['point_stock']<int(row['quantity'])-int(row.get('issued_quantity') or 0))
        if issue:
            try:
                if not checked: raise ValueError('Подтвердите выдачу.')
                issue_local(conn,order_id=order_id,point=current_point,actor=actor,evidence=evidence)
                conn.commit(); st.rerun()
            except Exception as exc:
                conn.rollback(); st.error(str(exc))
conn.close()
