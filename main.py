import os
import logging
import requests
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def get_tgstat_data(channel_id):
    url = "https://api.tgstat.ru/channels/stat"
    params = {"token": TGSTAT_TOKEN, "channelId": channel_id}
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "ok":
                logger.info(f"RAW DATA: {json.dumps(data.get('response'), ensure_ascii=False)}")
                return data.get("response")
        return None
    except Exception as e:
        logger.error(f"TGStat Error: {e}")
        return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text or len(text) > 100: return

    clean_id = text.strip().replace("@", "")
    status_msg = await update.message.reply_text(f"🔍 Проверяю {clean_id}...")

    raw_data = get_tgstat_data(clean_id)

    if not raw_data:
        await status_msg.edit_text("❌ Ошибка получения данных. Проверь лимиты тарифа S.")
        return

    # НОВАЯ ЛОГИКА ПРОМПТА: Запрет на паранойю
    analysis_prompt = (
        "Ты — опытный медиа-аналитик. Твоя цель: найти РЕАЛЬНО ЖИВЫЕ каналы.\n\n"
        "ЖЕСТКИЕ ПРАВИЛА АНАЛИЗА:\n"
        "1. ВЫСОКИЙ ОХВАТ — ЭТО КРУТО: Если охват поста > 15%, это признак лояльной аудитории. "
        "Если охват > 30% (как у @taknaglo или @supervhs), это ЭТАЛОН качества. "
        "НИКОГДА не называй высокий охват признаком накрутки или ботов.\n"
        "2. ERR: Для каналов до 100к сабов ERR 3-10% — это абсолютная норма. "
        "Признак ботов — это когда ERR КРИТИЧЕСКИ НИЗКИЙ (меньше 1%).\n"
        "3. ЦИТИРУЕМОСТЬ: Высокий CI при хорошем охвате — это виральность и успех контента.\n"
        "4. ВЕРДИКТ: Если ты видишь охват > 20% и ERR > 2%, твой вердикт всегда 'ЧИСТ'.\n\n"
        f"ДАННЫЕ КАНАЛА: {json.dumps(raw_data, ensure_ascii=False)}\n\n"
        "Напиши вердикт (Чист / Подозрителен / Накручен) и кратко обоснуй цифрами, "
        "соблюдая позитивный подход к высокой активности."
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": "Ты — объективный аналитик."},
                      {"role": "user", "content": analysis_prompt}],
            temperature=0.1
        )
        await status_msg.edit_text(f"📊 **Анализ @{clean_id}:**\n\n{completion.choices[0].message.content}")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка нейросети: {e}")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    print("Бот запущен...")
    app.run_polling()
