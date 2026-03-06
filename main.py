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
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def get_engagement_context(subs, err):
    """Математическая матрица адекватности ERR"""
    if subs < 10000:
        expect = "20-60%"
        status = "АВТОРСКИЙ/МИКРО" if err > 20 else "НИЗКИЙ"
    elif 10000 <= subs < 100000:
        expect = "10-30%"
        status = "ОРГАНИКА/СРЕДНИЙ" if err > 10 else "ПОДОЗРИТЕЛЬНО НИЗКИЙ"
    elif 100000 <= subs < 500000:
        expect = "5-15%"
        status = "КРУПНЫЙ МЕДИА" if err > 5 else "ВЯЛЫЙ"
    else:
        expect = "2-8%"
        status = "ГИГАНТ" if err > 2 else "НИЗКИЙ"
    
    return f"Размер: {subs} сабов. Ожидаемый ERR: {expect}. Текущий ERR: {err}%. Статус: {status}."

def get_tgstat_data(channel_id):
    url = "https://api.tgstat.ru/channels/stat"
    params = {"token": TGSTAT_TOKEN, "channelId": channel_id}
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "ok":
                res = data.get('response')
                res['red_label_status'] = res.get('red_label', False)
                return res
        return None
    except Exception as e:
        logger.error(f"API Error: {e}")
        return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text or len(text) > 100: return

    clean_id = text.strip().replace("@", "").split('/')[-1]
    status_msg = await update.message.reply_text(f"📊 Калибрую матрицу для @{clean_id}...")

    raw_data = get_tgstat_data(clean_id)
    if not raw_data:
        await status_msg.edit_text("❌ Ошибка данных.")
        return

    # Получаем математический контекст
    subs = raw_data.get('participants_count', 0)
    err = raw_data.get('err', 0)
    math_context = get_engagement_context(subs, err)

    analysis_prompt = (
        "Ты — аудитор-криминалист. Используй предоставленную математическую матрицу для вынесения вердикта.\n\n"
        f"МАТЕМАТИЧЕСКИЙ КОНТЕКСТ: {math_context}\n"
        f"RED LABEL ОТ TGSTAT: {raw_data['red_label_status']}\n\n"
        "ПРАВИЛА:\n"
        "1. Если RED LABEL = True -> ВЕРДИКТ: НАКРУЧЕН (без обсуждений).\n"
        "2. Если статус 'ОРГАНИКА' или 'АВТОРСКИЙ' -> ВЕРДИКТ: ЧИСТ. Высокий ERR здесь — признак жизни, а не ботов.\n"
        "3. Если статус 'НИЗКИЙ' или 'ПОДОЗРИТЕЛЬНО НИЗКИЙ' у нового канала -> ВЕРДИКТ: ПОДОЗРИТЕЛЬНЫЙ (вероятна накрутка ботами для имитации массы).\n"
        "4. Сравнивай с эталонами: @taknaglo (чист, высокая органика), @shumim_media (накручен, стерильные цифры).\n\n"
        f"СЫРЫЕ ДАННЫЕ ДЛЯ СПРАВКИ: {json.dumps(raw_data, ensure_ascii=False)}\n\n"
        "Напиши финальный вердикт и коротко поясни логику через охват и пересылки."
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": "Ты — объективный аналитик-математик."},
                      {"role": "user", "content": analysis_prompt}],
            temperature=0.1
        )
        await status_msg.edit_text(f"🏁 **Вердикт для @{clean_id}:**\n\n{completion.choices[0].message.content}")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка нейросети: {e}")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
