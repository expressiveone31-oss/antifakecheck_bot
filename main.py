import os, logging, requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")
client = OpenAI(api_key=OPENAI_API_KEY)

def get_telemetr_data(username):
    # Пытаемся найти канал через общий поиск (он стабильнее всего)
    url = "https://api.telemetr.me/v1/channels/"
    headers = {"Authorization": f"Token {TELEMETR_TOKEN}"}
    params = {"username": username}
    
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            data = res.json()
            # Если результаты есть, берем самый подходящий
            if data.get("results") and len(data["results"]) > 0:
                return data["results"][0]
        return None
    except Exception as e:
        logger.error(f"Ошибка API: {e}")
        return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return
    
    # Чистим юзернейм от лишнего
    clean_id = text.replace("https://t.me/", "").replace("@", "").strip().split('/')[0]
    status_msg = await update.message.reply_text(f"🔍 Ищу @{clean_id} в базе Telemetr...")

    raw_data = get_telemetr_data(clean_id)

    if not raw_data:
        await status_msg.edit_text(f"❌ Канал @{clean_id} не найден в Telemetr. Возможно, он скрыт или слишком мал.")
        return

    # Если данные есть, просим GPT сделать короткий и четкий вердикт
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты эксперт по Telegram. Проанализируй данные на накрутку (ERR, динамика). Пиши кратко и по делу."},
                {"role": "user", "content": f"Данные канала: {str(raw_data)[:3500]}"}
            ],
            temperature=0
        )
        await status_msg.edit_text(f"✅ **Анализ @{clean_id}:**\n\n{response.choices[0].message.content}")
    except Exception as e:
        await status_msg.edit_text("❌ Ошибка при анализе текста.")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
