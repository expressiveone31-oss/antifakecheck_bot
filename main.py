import os
import logging
import requests
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

# Настройка логирования для Railway
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def get_tgstat_data(channel_id):
    """Получение данных из TGStat API (Метод channels/stat)"""
    url = "https://api.tgstat.ru/channels/stat"
    params = {"token": TGSTAT_TOKEN, "channelId": channel_id}
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "ok":
                res = data.get('response')
                # Логируем важные для нас зацепки: охват, ERR и цитируемость
                logger.info(f"АНАЛИЗ {channel_id}: сабы={res.get('participants_count')}, ERR={res.get('err')}, CI={res.get('ci_index')}")
                return res
        return None
    except Exception as e:
        logger.error(f"Ошибка TGStat API: {e}")
        return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text or len(text) > 100: return

    # Очистка юзернейма
    clean_id = text.strip().replace("@", "").split('/')[-1]
    status_msg = await update.message.reply_text(f"🕵️‍♂️ Провожу экспертизу @{clean_id}...")

    raw_data = get_tgstat_data(clean_id)

    if not raw_data:
        await status_msg.edit_text("❌ Данные не получены. Проверь лимиты тарифа S или корректность юзернейма.")
        return

    # ФИНАЛЬНЫЙ СЛЕДСТВЕННЫЙ ПРОМПТ
    analysis_prompt = (
        "Ты — ведущий эксперт по выявлению кибер-фрода в Telegram. Твоя задача: "
        "распознать профессиональную накрутку, которая имитирует живую активность.\n\n"
        
        "1. ЧЕРНЫЙ СПИСОК ПАТТЕРНОВ (Кейсы @TRND_X, @trendswhat, @shumim_media):\n"
        "- Эти каналы используют 'стерильную' накрутку. Если ты видишь ERR в районе 5-10% "
        "и охват 7-15%, который держится подозрительно ровно — это ПРИЗНАК БОТОВ.\n"
        "- Особое внимание на Индекс цитируемости (CI): если он высокий, но репосты идут "
        "из пустых или однотипных каналов-мусорщиков — это накрутка рейтинга для продажи рекламы.\n\n"
        
        "2. БЕЛЫЙ СПИСОК ПАТТЕРНОВ (Кейс @taknaglo):\n"
        "- Это живой авторский контент. Здесь охват может быть аномально высоким (30-70%), "
        "а ERR — скачущим. Для маленьких авторских каналов 'слишком много' просмотров — "
        "это ПРИЗНАК ВИРАЛЬНОСТИ, а не ботов. Живые люди активно репостят такой контент.\n\n"
        
        "3. ТЕСТ НА 'ЖИВОЕ ДЫХАНИЕ':\n"
        "- У накрученных каналов (как @shumim_media) соотношение охвата к подписчикам "
        "всегда выглядит математически выверенным и слишком 'причесанным'.\n"
        "- У живых каналов всегда есть хаос в цифрах: один пост взлетает, другой нет.\n\n"
        
        f"ДАННЫЕ ДЛЯ ЭКСПЕРТИЗЫ: {json.dumps(raw_data, ensure_ascii=False)}\n\n"
        "Вынеси вердикт: ЧИСТ (как @taknaglo) / НАКРУЧЕН (как сетки выше) / ПОДОЗРИТЕЛЕН. "
        "Обоснуй, почему цифры выглядят либо как органический хаос, либо как 'стерильный' налив."
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты — беспристрастный эксперт-аналитик."},
                {"role": "user", "content": analysis_prompt}
            ],
            temperature=0.1
        )
        await status_msg.edit_text(f"📊 **Вердикт экспертизы для @{clean_id}:**\n\n{completion.choices[0].message.content}")
    except Exception as e:
        logger.error(f"OpenAI Error: {e}")
        await status_msg.edit_text("❌ Ошибка при анализе нейросетью.")

if __name__ == '__main__':
    if not TOKEN:
        print("ОШИБКА: BOT_TOKEN не установлен!")
    else:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        print("Анти-фрод бот запущен...")
        app.run_polling()
