"""Video registry reports in the public and administrator bot menus."""

from html import escape
from pathlib import Path
import sys

from telegram import ReplyKeyboardMarkup, Update

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from access_control import has_permission
from service_orders_core import get_conn
from video_registry_reports import video_period, video_registry_rows

MISSING='🎥 Не найдены в реестре'
FOUND='🎥 Найдены в реестре'
PUBLIC_ENTRY='🎥 Открытые видеоотчёты'
BACK='⬅️ К админ-меню'
PUBLIC_BACK='⬅️ К выбору режима'
PREV='⬅️ Раньше'
NEXT='➡️ Далее'
PAGE_SIZE=12


async def show_video_report(update: Update, state: dict, *, found: bool, page: int=0,
                            public: bool=False) -> None:
    conn=get_conn()
    try:
        rows=video_registry_rows(conn,found=found)
        period=video_period(conn)
    finally:
        conn.close()
    page_count=max(1,(len(rows)+PAGE_SIZE-1)//PAGE_SIZE)
    page=max(0,min(page,page_count-1))
    start=page*PAGE_SIZE
    state.update({'mode':'video_public' if public else 'video_reports',
                  'video_found':found,'video_page':page})
    lines=[escape(FOUND if found else MISSING),
           f'Период съёмок: {escape(str(period))}',
           f'Номеров: {len(rows)} · Стр. {page+1} / {page_count}',
           'По частоте: от большего к меньшему.','']
    table=['НОМЕР     РАЗ  Н  Д', '────────  ─── ─── ───']
    for row in rows[start:start+PAGE_SIZE]:
        plate=str(row['Номер'] or '—')
        count=str(row['Количество'] or 0)
        night_day=str(row['Ночь / День'] or '0 / 0').split('/')
        night=night_day[0].strip()
        day=night_day[1].strip() if len(night_day)>1 else '0'
        table.append(f'{plate:<8} {count:>4} {night:>3} {day:>3}')
        if found:
            table.append(f"  кв.{row['Квартира']} · {row['ФИО']}")
        table.append(f"  {row['Марка'] or 'Марка не определена'}")
        table.append('')
    if not rows:
        table.append('Номеров нет.')
    lines.append('<pre>'+escape('\n'.join(table).rstrip())+'</pre>')
    lines.append('Н — ночь, Д — день.')
    nav=([PREV] if page else [])+([NEXT] if start+PAGE_SIZE<len(rows) else [])
    keyboard=[[MISSING,FOUND]]+([nav] if nav else [])+[[PUBLIC_BACK if public else BACK]]
    await update.message.reply_text('\n'.join(lines),reply_markup=ReplyKeyboardMarkup(keyboard,resize_keyboard=True),parse_mode='HTML')


async def handle_video_reports_text(update: Update, user_states: dict, user_id: int,
                                    message_text: str, *, back_markup) -> bool:
    state=user_states.get(user_id)
    active=isinstance(state,dict) and state.get('mode')=='video_reports'
    if message_text not in {MISSING,FOUND} and not active:
        return False
    if not has_permission(user_id,'reports','VIEW'):
        await update.message.reply_text('Нет права просмотра отчётов.',reply_markup=back_markup)
        if active:
            user_states.pop(user_id,None)
        return True
    if message_text==BACK:
        user_states.pop(user_id,None)
        await update.message.reply_text('Админ-меню',reply_markup=back_markup)
        return True
    if message_text in {MISSING,FOUND}:
        state=user_states.setdefault(user_id,{})
        await show_video_report(update,state,found=message_text==FOUND)
        return True
    if active:
        page=int(state.get('video_page') or 0)+(1 if message_text==NEXT else -1 if message_text==PREV else 0)
        await show_video_report(update,state,found=bool(state.get('video_found')),page=page)
        return True
    return False


async def handle_public_video_text(update: Update, user_states: dict, user_id: int,
                                   message_text: str, *, back_to_modes) -> bool:
    """Anyone who opened the bot may browse the public video reports."""
    state=user_states.get(user_id)
    active=isinstance(state,dict) and state.get('mode')=='video_public'
    if message_text!=PUBLIC_ENTRY and not active:
        return False
    if message_text==PUBLIC_BACK:
        user_states.pop(user_id,None)
        await back_to_modes()
        return True
    state=user_states.setdefault(user_id,{})
    if message_text==PUBLIC_ENTRY:
        state.update({'mode':'video_public','video_page':0})
        await update.message.reply_text(
            '🎥 Видеоотчёты парковки\n\nВыберите список распознанных номеров.',
            reply_markup=ReplyKeyboardMarkup([[MISSING],[FOUND],[PUBLIC_BACK]],resize_keyboard=True),
        )
        return True
    if message_text in {MISSING,FOUND}:
        await show_video_report(update,state,found=message_text==FOUND,public=True)
        return True
    page=int(state.get('video_page') or 0)+(1 if message_text==NEXT else -1 if message_text==PREV else 0)
    await show_video_report(update,state,found=bool(state.get('video_found')),page=page,public=True)
    return True
