import os
import logging
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY)

def get_telemetr_data(username):
    """Самый стабильный метод: поиск канала по юзернейму"""
    # Используем поиск (channels), он не выдает 500 как метод by_username
    url = "https://api.telemetr.me/v1/channels/"
    params = {"username": username}
    headers = {"Authorization": f"Token {TELEMETR_TOKEN}"}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            # Если в списке результатов что-то есть — берем первый канал
            if data.get("results"):
                return data["results"][0]
            return {"error": "Канал не найден в базе"}
        return {"error": f"API Error {response.status_code}"}
    except Exception as e:
        return {"error": str(e)}

async def ask_gpt_expert(payload):
    if "error" in payload:
        return f"Ошибка данных: {payload['error']}. Попробуй позже."

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты аналитик Telegram. Проверь данные на накрутку. ERR > 10% - ок. Считай математически верно. Формат: Вердикт, Обоснование, Оценка."},
                {"role": "user", "content": f"Данные: {str(payload)[:3500]}"}
            ],
            temperature=0
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Ошибка GPT: {e}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return
    
    # Чистим ник
    clean_id = text.replace("https://t.me/", "").replace("@", "").strip().split('/')[0]
    status_msg = await update.message.reply_text(f"🔍 Ищу и анализирую @{clean_id}...")

    # Получаем данные через СТАБИЛЬНЫЙ поиск
    data = get_telemetr_data(clean_id)
    
    # Анализируем
    verdict = await ask_gpt_expert(data)
    
    await status_msg.edit_text(f"✅ Результат для @{clean_id}:\n\n{verdict}")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
