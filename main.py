import os
import logging
import requests
import statistics
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

def calculate_audit(posts, subs):
    """Математика, которая не обижает авторов"""
    if not posts:
        return ["Нет данных по свежим постам"], 0, 0
    
    # Собираем просмотры, только если они есть в ответе
    reaches = [p.get('views_count', 0) for p in posts if p.get('views_count') is not None]
    
    if not reaches:
        return ["API не отдало данные по просмотрам"], 0, 0

    avg_reach = statistics.mean(reaches)
    er = (avg_reach / subs * 100) if subs > 0 else 0
    
    score = 0
    findings = []

    # 1. Проверка на ровность (Коэффициент вариации)
    if len(reaches) >= 5:
        cv = (statistics.stdev(reaches) / avg_reach * 100) if avg_reach > 0 else 0
        if cv < 7: # Слишком ровно — признак софта
            score += 4
            findings.append(f"Стерильные охваты (CV {round(cv, 1)}%)")
        elif cv > 35: # Хаотично — признак жизни
            score -= 3
            findings.append(f"Естественная динамика (CV {round(cv, 1)}%)")

    # 2. Коррекция вердикта (Защита @taknaglo)
    if subs < 50000 and er > 25:
        score -= 2
        findings.append("Высокая лояльность малого канала")

    return findings, score, er

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return
    
    clean_id = text.strip().replace("@", "").split('/')[-1]
    status_msg = await update.message.reply_text(f"📊 Анализ @{clean_id} через TGStat...")

    try:
        # Шаг 1: Общая стата (сабы)
        info_url = f"https://api.tgstat.ru/channels/stat?token={TGSTAT_TOKEN}&channelId={clean_id}"
        info_data = requests.get(info_url, timeout=10).json()
        
        if info_data.get('status') != 'ok':
            await status_msg.edit_text(f"❌ Ошибка TGStat: {info_data.get('error', 'Канал не найден')}")
            return

        subs = info_data.get('response', {}).get('participants_count', 0)

        # Шаг 2: Последние посты
        posts_url = f"https://api.tgstat.ru/posts/list?token={TGSTAT_TOKEN}&channelId={clean_id}&limit=15"
        posts_data = requests.get(posts_url, timeout=10).json()
        posts = posts_data.get('response', {}).get('items', [])

        findings, score, er = calculate_audit(posts, subs)

        # Шаг 3: Вердикт через GPT
        prompt = (
            f"Ты — антифрод-эксперт. Канал @{clean_id}.\n"
            f"Данные: {subs} сабов, ERR {round(er, 1)}%.\n"
            f"Мат. анализ: {', '.join(findings)}. Балл фрода: {score}/10.\n"
            "Вынеси вердикт: ЧИСТ (балл < 3), ПОДОЗРИТЕЛЕН (3-5) или НАКРУЧЕН (>5)."
        )

        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        await status_msg.edit_text(f"🏁 **Результат @{clean_id}:**\n\n{res.choices[0].message.content}")

    except Exception as e:
        logger.error(f"Ошибка в боте: {e}")
        await status_msg.edit_text("📛 Ошибка API или связи. Попробуй позже.")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
