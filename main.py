import os
import logging
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Забираем токены
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text or not text.startswith('@'): return
    
    clean_id = text.strip().replace("@", "").split('/')[-1]
    status_msg = await update.message.reply_text(f"📡 Запрашиваю Telemetr.me для @{clean_id}...")

    # Настройки по инструкции саппорта Telemetr
    url = "https://api.telemetr.me/channels/stat"
    headers = {
        "Authorization": f"Bearer {TELEMETR_TOKEN}",
        "Accept": "application/json"
    }
    params = {"id": clean_id}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        logger.info(f"Telemetr Status: {response.status_code}")

        if response.status_code != 200:
            await status_msg.edit_text(f"❌ Telemetr вернул код {response.status_code}. Проверь токен в Railway.")
            return

        data = response.json()
        # Telemetr может отдавать данные в поле 'data' или сразу в корне
        info = data.get('data', data)
        
        subs = info.get('subscribers_count', 0)
        err = info.get('err', 0)

        if subs == 0:
            await status_msg.edit_text(f"⚠️ Telemetr не нашел данных по @{clean_id}. Проверь правильность юзернейма.")
            return

        # Финальный вердикт от GPT
        prompt = (
            f"Ты — антифрод эксперт. Канал @{clean_id}.\n"
            f"Метрики Telemetr: {subs} сабов, ERR {err}%.\n"
            "Если ERR > 20% при малых сабах — это АВТОРСКИЙ канал (ЧИСТ).\n"
            "Если ERR < 1% при больших сабах — это НАКРУТКА.\n"
            "Дай краткий вердикт и объясни почему."
        )

        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        
        await status_msg.edit_text(f"🏁 **Вердикт Telemetr:**\n\n{res.choices[0].message.content}")

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await status_msg.edit_text("📛 Ошибка связи. Попробуй еще раз через минуту.")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
