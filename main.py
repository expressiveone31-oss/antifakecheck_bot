import os
import logging
import requests
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# 1. Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 2. Переменные
TOKEN = os.getenv("BOT_TOKEN")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

def get_telemetr_raw(channel_id):
    """Просто тянем сырые данные для проверки API"""
    # Пробуем самый базовый метод поиска
    url = f"https://api.telemetr.me/v1/channels/by_username/{channel_id}/"
    headers = {"Authorization": f"Token {TELEMETR_TOKEN}"}
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        # Возвращаем статус и текст ответа, чтобы увидеть ошибку глазами
        return f"Status: {response.status_code}\nResponse: {response.text}"
    except Exception as e:
        return f"Ошибка запроса: {e}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот в режиме отладки. Пришли юзернейм.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return

    # Чистим юзернейм
    clean_id = text.replace("https://t.me/", "").replace("@", "").strip().split('/')[0]
    
    status_msg = await update.message.reply_text(f"📡 Стучусь в Telemetr за @{clean_id}...")

    # Получаем сырой ответ
    raw_response = get_telemetr_raw(clean_id)

    # Выводим всё как есть
    await status_msg.edit_text(f"Результат API для @{clean_id}:\n\n{raw_response}")

if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    application.run_polling()
