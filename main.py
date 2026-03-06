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
    status_msg = await update.message.reply_text(f"📡 Проверяю @{clean_id}...")

    try:
        # 1. Получаем статистику канала
        url = f"https://api.tgstat.ru/channels/stat?token={TGSTAT_TOKEN}&channelId={clean_id}"
        response = requests.get(url, timeout=15)
        data = response.json()

        if data.get('status') != 'ok':
            await status_msg.edit_text(f"❌ Ошибка API: {data.get('error', 'Канал не найден')}")
            return

        res = data.get('response', {})
        subs = res.get('participants_count', 0)
        err = res.get('err', 0)
        
        # 2. Формируем простой промпт для GPT
        # Мы явно говорим GPT, что высокий ERR у малых каналов — это норма!
        prompt = (
            f"Анализируй канал @{clean_id}.\n"
            f"Подписчиков: {subs}\n"
            f"ERR (вовлеченность): {err}%\n\n"
            "ПРАВИЛО: Если подписчиков меньше 50к и ERR выше 20% — это АВТОРСКИЙ КАНАЛ (ЧИСТ).\n"
            "Если ERR подозрительно ровный или канал помечен red_label — это НАКРУТКА.\n"
            "Вынеси вердикт: ЧИСТ или НАКРУЧЕН. Объясни кратко."
        )

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        
        await status_msg.edit_text(f"🏁 **Результат для @{clean_id}:**\n\n{completion.choices[0].message.content}")

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await status_msg.edit_text("📛 Ошибка соединения с API. Попробуй еще раз через минуту.")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
