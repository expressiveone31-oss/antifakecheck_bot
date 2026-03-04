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

def get_telemetr_data(query):
    """Улучшенный поиск: ищем и по username, и по названию"""
    url = "https://api.telemetr.me/v1/channels/"
    headers = {"Authorization": f"Token {TELEMETR_TOKEN}"}
    
    # Сначала ищем точное совпадение по юзернейму
    try:
        res = requests.get(url, headers=headers, params={"username": query}, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data.get("results"):
                return data["results"][0]
        
        # Если не нашли — пробуем поиск по поисковой строке (более гибкий)
        res_search = requests.get(url, headers=headers, params={"search": query}, timeout=10)
        if res_search.status_code == 200:
            data = res_search.json()
            if data.get("results"):
                return data["results"][0]
    except Exception as e:
        logger.error(f"Ошибка API: {e}")
    return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text or len(text) > 100: return
    
    clean_id = text.replace("https://t.me/", "").replace("@", "").strip().split('/')[0]
    status_msg = await update.message.reply_text(f"📡 Глубокий поиск @{clean_id}...")

    raw_data = get_telemetr_data(clean_id)

    if not raw_data:
        await status_msg.edit_text(f"❌ Даже через глубокий поиск @{clean_id} не найден. Проверь, нет ли опечатки?")
        return

    try:
        # Зовем GPT только если данные реально пришли
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты аналитик Telegram. Проверь данные на накрутку. Пиши вердикт кратко."},
                {"role": "user", "content": f"Данные: {str(raw_data)[:3500]}"}
            ],
            temperature=0
        )
        await status_msg.edit_text(f"✅ **Результат для @{clean_id}:**\n\n{response.choices[0].message.content}")
    except Exception as e:
        await status_msg.edit_text(f"⚠️ Данные получены, но GPT не смог их переварить.")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
