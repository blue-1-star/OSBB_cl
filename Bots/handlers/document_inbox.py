"""Authorized private-chat attachment intake; files are never executed."""
import asyncio
from access_control import has_permission
from document_intake_core import accept_document, MAX_BYTES

ENTRY='📥 Передать документ ОСББ'

def allowed(user_id, is_admin):
    return has_permission(user_id,'osbb_documents','CREATE') or is_admin(user_id)

async def show_upload_help(update, is_admin):
    if not allowed(update.effective_user.id,is_admin):
        await update.message.reply_text('⛔ Нет права передавать документы ОСББ.'); return
    await update.message.reply_text('Отправьте или перешлите сюда документ либо фото в личном чате с ботом.\nФайл до 20 МБ попадёт во «Входящие» на разбор. Для исходного качества изображения отправляйте его как файл.')

async def receive_document(update, context, is_admin):
    message=update.effective_message
    if not message or not update.effective_user: return
    if update.effective_chat.type!='private':
        await message.reply_text('Документы ОСББ принимаются только в личном чате с ботом.'); return
    if not allowed(update.effective_user.id,is_admin):
        await message.reply_text('⛔ Нет права передавать документы ОСББ.'); return
    attachment=message.document or (message.photo[-1] if message.photo else None)
    if not attachment: return
    if attachment.file_size and attachment.file_size>MAX_BYTES:
        await message.reply_text('Файл больше 20 МБ. Загрузите уменьшенную копию.'); return
    try:
        file=await attachment.get_file()
        data=bytes(await file.download_as_bytearray())
        filename=message.document.file_name if message.document else f'photo_{message.message_id}.jpg'
        result=await asyncio.to_thread(accept_document,filename=filename or 'document.pdf',data=data,
            actor=str(update.effective_user.id),source='TELEGRAM',
            source_reference=f'chat:{message.chat_id};message:{message.message_id};file:{attachment.file_unique_id}')
        await message.reply_text(f"📥 Документ #{result['id']} — {filename}\n" +
            ('Такой файл уже зарегистрирован; повторное получение отмечено.' if result['duplicate'] else 'Сохранён во «Входящие» на разбор.'))
    except Exception as exc:
        await message.reply_text(f'⚠️ Не удалось принять документ: {exc}')
