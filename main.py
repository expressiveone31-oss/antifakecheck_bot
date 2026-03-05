import os
import logging
import requests
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

if not OPENAI_API_KEY:
    client = None
else:
    client = OpenAI(api_key=OPENAI_API_KEY)

def get_telemetr_data(channel_link):
    url = "https://api.telemetr.me/channels/get"
    
    # Пробуем отправить оба варианта заголовков сразу, чтобы наверняка
    headers = {
        "X-Api-Token": TELEMETR_TOKEN,
        "Authorization": f"Token {TELEMETR_TOKEN}"
    }
    
    params = {"link": channel_link}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        
        # Если 403, пробуем еще раз без X-Api-Token, только с Authorization
        if response.status_code == 403:
            headers_alt = {"Authorization": f"Token {TELEMETR_TOKEN}"}
            response = requests.get(url, headers=headers_alt, params=params, timeout=15)

        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Telemetr Error {response.status_code}: {response.text}")
            return f"Error_{response.status_code}"
    except Exception as e:
        logger.error(f"Request failed: {e}")
        return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text or len(text) > 150: return

    clean_input = text.strip()
    if not clean_input.startswith("http"):
        handle = clean_input.replace("@", "")
        clean_input = f"https://t.me/{handle}"

    status_msg = await update.message.reply_text(f"📡 Проверка доступа (403 fix) для {clean_input}...")
    raw_data = get_telemetr_data(clean_input)

    if str(raw_data).startswith("Error_"):
        await status_msg.edit_text(f"❌ Доступ запрещен ({raw_data}). Похоже, тариф API не позволяет использовать этот метод.")
        return
    elif not raw_data:
        await status_msg.edit_text("❓ Пустой ответ от сервера.")
        return

    if client:
        try:
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": f"Анализ на накрутку: {json.dumps(raw_data, ensure_ascii=False)[:3500]}"}],
                temperature=0.2
            )
            await status_msg.edit_text(f"✅ **Результат:**\n\n{completion.choices[0].message.content}")
        except:
            await status_msg.edit_text("❌ Ошибка GPT.")
    else:
        await status_msg.edit_text(f"📊 Ответ API: `{raw_data}`")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
