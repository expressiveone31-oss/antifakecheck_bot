import os, logging, requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")
client = OpenAI(api_key=OPENAI_API_KEY)

def get_telemetr_data(username):
    url = "https://api.telemetr.me/v1/channels/"
    headers = {"Authorization": f"Token {TELEMETR_TOKEN}"}
    try:
        # Пробуем поиск по юзернейму
        res = requests.get(url, headers=headers, params={"username": username}, timeout=10)
        if res.status_code == 200:
            data = res.json()
            return data["results"][0] if data.get("results") else None
        return f"Error_{res.status_code}"
    except: return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text or len(text) > 100: return
    
    clean_id = text.replace("https://t.me/", "").replace("@", "").strip().split('/')[0]
    status_msg = await update.message.reply_text(f"⏳ Жду ответа от Telemetr по @{clean_id}...")

    raw_data = get_telemetr_data(clean_id)

    if raw_data == "Error_500":
        await status_msg.edit_text("❌ Сервер Telemetr временно недоступен (Ошибка 500). Я сообщу, когда они починят API!")
        return
    elif not raw_data:
        await status_msg.edit_text("❓ Данные не найдены. Возможно, канал слишком новый.")
        return

    # Если данные пришли (API ожил), зовем GPT
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": "Ты аналитик TG. Проверь данные на накрутку."},
                      {"role": "user", "content": str(raw_data)[:3500]}],
            temperature=0
        )
        await status_msg.edit_text(f"✅ Анализ @{clean_id}:\n\n{response.choices[0].message.content}")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка нейросети: {e}")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
