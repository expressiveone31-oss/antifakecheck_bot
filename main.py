import os
import logging
import requests
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Берем данные из Railway
TOKEN = os.getenv("BOT_TOKEN")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

def get_telemetr_debug(username):
    """Проверяем метод поиска (search) — он обычно самый живучий"""
    url = "https://api.telemetr.me/v1/channels/"
    params = {"username": username}
    headers = {"Authorization": f"Token {TELEMETR_TOKEN}"}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        # Собираем подробный отчет для тебя
        debug_info = {
            "status_code": response.status_code,
            "url_sent": response.url,
            "raw_response": response.text
        }
        return debug_info
    except Exception as e:
        return {"error": str(e)}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return
    
    # Чистим ник
    clean_id = text.replace("https://t.me/", "").replace("@", "").strip().split('/')[0]
    status_msg = await update.message.reply_text(f"🛠 Отладка API для @{clean_id}...")

    # Получаем "сырье"
    debug_data = get_telemetr_debug(clean_id)
    
    # Форматируем ответ, чтобы удобно было читать в Телеграме
    report = (
        f"📊 **ОТЧЕТ ОТЛАДКИ**\n\n"
        f"🔹 **Статус:** {debug_data.get('status_code')}\n"
        f"🔹 **Запрос:** `{debug_data.get('url_sent')}`\n\n"
        f"🔹 **Ответ:**\n`{debug_data.get('raw_response')}`"
    )
    
    # Если текст слишком длинный, обрезаем, чтобы телега не ругалась
    await status_msg.edit_text(report[:4000])

if __name__ == '__main__':
    if not TOKEN or not TELEMETR_TOKEN:
        print("ОШИБКА: Проверь BOT_TOKEN и TELEMETR_TOKEN в настройках Railway!")
    else:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT, handle_message))
        app.run_polling()
