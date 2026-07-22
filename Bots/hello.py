from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram import Update, ReplyKeyboardMarkup
TOKEN = "8924565536:AAGdbQelxteLlpacpK3AFRWC6mV36VwUvKY"
TOKEN = "8924565536:AAGdbQelxteLlpacpK3AFRWC6mV36VwUvKY"

SUPER_ADMIN_IDS = [210312208]  # сюда вставить ваш Telegram ID
ADMIN_IDS = [210312208]
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    keyboard = [
        ["🚗 Парковка", "🔑 Пульты"],
        ["🏗 Благоустройство"],
        ["📢 Объявления", "📞 Контакты"]
    ]

    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )

    await update.message.reply_text(
        "Добро пожаловать в систему управления парковкой.\n\n"
        "Выберите нужный раздел:",
        reply_markup=reply_markup
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text

    if text == "🚗 Парковка":
        await update.message.reply_text(
            "Раздел оплаты парковки."
        )

    elif text == "🔑 Пульты":
        await update.message.reply_text(
            "Раздел заказа и перепрошивки пультов."
        )

    elif text == "🏗 Благоустройство":
        await update.message.reply_text(
            "Раздел взносов на благоустройство парковки."
        )

    elif text == "📢 Объявления":
        await update.message.reply_text(
            "Раздел объявлений."
        )

    elif text == "📞 Контакты":
        await update.message.reply_text(
            "Контакты администрации."
        )


def main():

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))

    print("Бот запущен...")

    app.run_polling()


if __name__ == "__main__":
    main()