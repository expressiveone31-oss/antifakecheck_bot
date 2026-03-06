import os
import logging
import requests
import json
import statistics
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def analyze_patterns(posts):
    """Математический анализ последних постов на накрутку"""
    reaches = [p.get('view_count', 0) for p in posts if not p.get('is_deleted')]
    forwards = [p.get('forward_count', 0) for p in posts]
    
    if len(reaches) < 5: return "Мало данных для CV", 0
    
    # 1. Считаем Коэффициент Вариации (CV) - проверка на 'стерильность'
    mean_reach = statistics.mean(reaches)
    stdev_reach = statistics.stdev(reaches)
    cv = (stdev_reach / mean_reach) * 100 if mean_reach > 0 else 0
    
    # 2. Считаем баллы (шкала 0-10)
    score = 0
    reasons = []
    
    if cv < 2: # Слишком ровно
        score += 4
        reasons.append(f"Стерильность (CV: {round(cv, 2)}%): Просмотры подозрительно ровные.")
    elif cv > 50: # Очень хаотично (обычно органика)
        score -= 1
        reasons.append("Живой хаос: Охваты скачут, как у автора.")

    avg_forwards = statistics.mean(forwards)
    if avg_forwards < 1 and mean_reach > 1000:
        score += 3
        reasons.append("Нулевая виральность: Посты смотрят, но не пересылают.")
    
    return reasons, score

def get_tgstat_data(channel_id):
    # Используем эндпоинт постов для анализа динамики (последние 20 штук)
    url = "https://api.tgstat.ru/posts/list"
    params = {"token": TGSTAT_TOKEN, "channelId": channel_id, "limit": 20}
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "ok":
                return data.get('response', {}).get('items', [])
        return None
    except Exception as e:
        logger.error(f"API Error: {e}")
        return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text or len(text) > 100: return

    clean_id = text.strip().replace("@", "").split('/')[-1]
    status_msg = await update.message.reply_text(f"🔬 Провожу глубокий аудит @{clean_id}...")

    posts = get_tgstat_data(clean_id)
    if not posts:
        await status_msg.edit_text("❌ Не удалось получить данные о постах.")
        return

    # Математическая экспертиза
    reasons, score = analyze_patterns(posts)
    
    # Запрос к GPT для финальной упаковки
    analysis_prompt = (
        f"Ты — эксперт-криминалист Telegram. Проанализируй результаты математического теста:\n\n"
        f"Канал: @{clean_id}\n"
        f"Выявленные паттерны: {', '.join(reasons)}\n"
        f"Баллы подозрительности: {score}/10\n\n"
        "ТВОЯ ЗАДАЧА:\n"
        "1. Если баллы < 3 — канал ЧИСТ (приветствуй авторский контент).\n"
        "2. Если баллы > 6 — канал НАКРУЧЕН (аргументируй 'стерильностью' или отсутствием пересылок).\n"
        "3. Если 3-6 — ПОДОЗРИТЕЛЕН.\n\n"
        "Напиши вердикт в стиле твоих предыдущих разборов: База, Ровность, Виральность, Итог."
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": analysis_prompt}],
            temperature=0.1
        )
        await status_msg.edit_text(f"🏁 **Экспертиза @{clean_id}:**\n\n{completion.choices[0].message.content}")
    except Exception:
        await status_msg.edit_text("❌ Ошибка нейросети.")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
