from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

TOKEN = "8924565536:AAGdbQelxteLlpacpK3AFRWC6mV36VwUvKY"


# ==========================================
# Тексты
# ==========================================

TEXTS = {
    "ru": {
        "choose_language": "Выберите язык:",
        "welcome": "Добро пожаловать в систему управления парковкой.",
        "parking": "🚗 Парковка",
        "remotes": "🔑 Пульты",
        "improvement": "🏗 Благоустройство",
        "news": "📢 Объявления",
        "contacts": "📞 Контакты",
    },

    "uk": {
        "choose_language": "Оберіть мову:",
        "welcome": "Ласкаво просимо до системи керування паркуванням.",
        "parking": "🚗 Паркування",
        "remotes": "🔑 Пульти",
        "improvement": "🏗 Благоустрій",
        "news": "📢 Оголошення",
        "contacts": "📞 Контакти",
    },

    "en": {
        "choose_language": "Choose language:",
        "welcome": "Welcome to the parking management system.",
        "parking": "🚗 Parking",
        "remotes": "🔑 Remotes",
        "improvement": "🏗 Improvements",
        "news": "📢 Announcements",
        "contacts": "📞 Contacts",
    }
}

# Пока храним язык в памяти.
# Позже заменим на SQLite.
user_languages = {}


# ==========================================
# Меню выбора языка
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    keyboard = [
        ["🇺🇦 Українська"],
        ["🇷🇺 Русский"],
        ["🇬🇧 English"]
    ]

    await update.message.reply_text(
        "Оберіть мову / Выберите язык / Choose language",
        reply_markup=ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True
        )
    )


# ==========================================
# Главное меню
# ==========================================

async def show_main_menu(update, lang):

    t = TEXTS[lang]

    keyboard = [
        [t["parking"], t["remotes"]],
        [t["improvement"]],
        [t["news"], t["contacts"]]
    ]

    await update.message.reply_text(
        t["welcome"],
        reply_markup=ReplyKeyboardMarkup(
            keyboard,
            resize_keyboard=True
        )
    )


# ==========================================
# Обработка сообщений
# ==========================================

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id
    text = update.message.text

    # Выбор языка

    if text == "🇷🇺 Русский":
        user_languages[user_id] = "ru"
        await show_main_menu(update, "ru")
        return

    if text == "🇺🇦 Українська":
        user_languages[user_id] = "uk"
        await show_main_menu(update, "uk")
        return

    if text == "🇬🇧 English":
        user_languages[user_id] = "en"
        await show_main_menu(update, "en")
        return

    # Пока просто заглушка

    lang = user_languages.get(user_id, "ru")

    await update.message.reply_text(
        f"Вы нажали: {text}"
    )


# ==========================================
# Запуск
# ==========================================

def main():

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            message_handler
        )
    )

    print("Бот запущен...")

    app.run_polling()


if __name__ == "__main__":
    main()