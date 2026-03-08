import os
import logging
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return
    
    clean_id = text.strip().replace("@", "").split('/')[-1]
    status_msg = await update.message.reply_text(f"🧪 Тестирую канал @{clean_id}...")

    # ПРОВЕРКА ТОКЕНА
    if not TGSTAT_TOKEN:
        await status_msg.edit_text("❌ ОШИБКА: Переменная TGSTAT_TOKEN не найдена в Railway!")
        return

    try:
        # Прямой запрос к API (Метод: channels/stat)
        url = "https://api.tgstat.ru/channels/stat"
        params = {
            "token": TGSTAT_TOKEN,
            "channelId": clean_id
        }
        
        response = requests.get(url, params=params, timeout=15)
        
        # Если сервер прислал не JSON (например, ошибку 404 или 500)
        if response.status_code != 200:
            await status_msg.edit_text(f"❌ API ответил кодом {response.status_code}. \nТекст: {response.text[:100]}")
            return

        data = response.json()

        if data.get('status') != 'ok':
            error_msg = data.get('error', 'Неизвестная ошибка')
            await status_msg.edit_text(f"❌ TGStat отклонил запрос: {error_msg}")
            return

        # Если данные пришли — отдаем GPT
        res = data.get('response', {})
        subs = res.get('participants_count', 0)
        err = res.get('err', 0)
        
        prompt = (
            f"Канал @{clean_id}. Сабы: {subs}, ERR: {err}%.\n"
            f"Если сабов < 50к и ERR > 20% — это ЧИСТЫЙ АВТОР.\n"
            f"Вынеси вердикт: ЧИСТ или НАКРУЧЕН."
        )

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        
        await status_msg.edit_text(f"🏁 **Результат:**\n\n{completion.choices[0].message.content}")

    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text(f"📛 Сбой в коде: {str(e)}")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
