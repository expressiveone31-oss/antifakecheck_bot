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
    """Математическая матрица адекватности ERR с защитой от нулевых значений"""
    if not err or err == 0:
        return f"Размер: {subs} сабов. Данные по ERR отсутствуют или равны 0. Требуется ручной анализ охватов."
    
    if subs < 10000:
        expect, status = "20-60%", ("АВТОРСКИЙ" if err > 20 else "НИЗКИЙ")
    elif 10000 <= subs < 100000:
        expect, status = "10-30%", ("ОРГАНИКА" if err > 8 else "ПОДОЗРИТЕЛЬНО НИЗКИЙ")
    elif 100000 <= subs < 500000:
        expect, status = "5-15%", ("МЕДИА" if err > 4 else "ВЯЛЫЙ")
    else:
        expect, status = "2-8%", ("ГИГАНТ" if err > 1.5 else "НИЗКИЙ")
    
    return f"Размер: {subs} сабов. Ожидаемый ERR: {expect}. Текущий ERR: {err}%. Статус: {status}."

def get_tgstat_data(channel_id):
    url = "https://api.tgstat.ru/channels/stat"
    params = {"token": TGSTAT_TOKEN, "channelId": channel_id}
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "ok":
                res = data.get('response', {})
                # НОРМАЛИЗАЦИЯ: TGStat может отдавать ERR в разных полях
                # Пробуем вытащить хоть какое-то значение вовлеченности
                res['err_fixed'] = res.get('err') or res.get('err_percent') or res.get('avg_post_reach', 0) / res.get('participants_count', 1) * 100
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
    status_msg = await update.message.reply_text(f"📊 Анализирую метрики @{clean_id}...")

    raw_data = get_tgstat_data(clean_id)
    if not raw_data:
        await status_msg.edit_text("❌ Ошибка API или канал не найден.")
        return

    # Используем исправленный ERR
    subs = raw_data.get('participants_count', 0)
    err = round(float(raw_data.get('err_fixed', 0)), 2)
    
    math_context = get_engagement_context(subs, err)

    analysis_prompt = (
        "Ты — аналитик трафика. Твоя задача: найти ботов.\n\n"
        f"КОНТЕКСТ: {math_context}\n"
        f"RED LABEL (TGStat): {raw_data['red_label_status']}\n\n"
        "ПРИНЦИПЫ:\n"
        "1. Если RED LABEL = True -> НАКРУЧЕН.\n"
        "2. Если статус 'ОРГАНИКА' или 'АВТОРСКИЙ' -> ЧИСТ. Высокие цифры — это успех контента.\n"
        "3. Если статус 'НИЗКИЙ' у крупного канала — это норма, но у нового (до 100к) — признак ботов.\n"
        "4. Сравнивай: @taknaglo (живой, высокий ERR), @shumim_media (накручен, стерильные цифры).\n\n"
        "Напиши вердикт (ЧИСТ / НАКРУЧЕН) и коротко поясни."
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": analysis_prompt}],
            temperature=0.1
        )
        await status_msg.edit_text(f"🏁 **Результат для @{clean_id}:** (ERR: {err}%)\n\n{completion.choices[0].message.content}")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка нейросети: {e}")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
