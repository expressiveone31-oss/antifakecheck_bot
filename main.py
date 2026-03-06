import os
import logging
import requests
import json
import statistics
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Константы
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def get_tgstat_posts(channel_id):
    """Получаем список последних 20 постов для анализа динамики"""
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
        logger.error(f"TGStat API Error: {e}")
        return None

def run_deep_audit(posts, subs):
    """Математический анализ паттернов накрутки"""
    if not posts: return "Нет данных", 0
    
    reaches = [p.get('views_count', 0) for p in posts if not p.get('is_deleted')]
    forwards = [p.get('forwards_count', 0) for p in posts]
    deleted_count = sum(1 for p in posts if p.get('is_deleted'))
    
    score = 0
    findings = []

    # 1. Проверка на Ровность (Коэффициент Вариации)
    if len(reaches) >= 5:
        mean_r = statistics.mean(reaches)
        cv = (statistics.stdev(reaches) / mean_r * 100) if mean_r > 0 else 0
        
        if cv < 3: # Стерильно ровные охваты
            score += 4
            findings.append(f"Стерильность: CV {round(cv, 1)}% (охваты слишком ровные для человека)")
        elif cv > 40: # Здоровый хаос
            score -= 2
            findings.append(f"Живой хаос: CV {round(cv, 1)}% (типично для автора)")

    # 2. Проверка Базы (Охват к подписчикам)
    avg_reach = statistics.mean(reaches) if reaches else 0
    er = (avg_reach / subs * 100) if subs > 0 else 0
    
    if er < 1: # Канал-кладбище
        score += 4
        findings.append(f"Коллапс базы: охват {round(er, 2)}% от сабов")
    elif er > 40 and subs > 10000: # Высокая виральность
        score -= 1 # Бонус доверия, если есть пересылки
        findings.append(f"Высокая вовлеченность: ER {round(er, 1)}%")

    # 3. Виральность (Пересылки)
    avg_fwd = statistics.mean(forwards) if forwards else 0
    if avg_fwd < 1 and avg_reach > 1000:
        score += 3
        findings.append("Нулевая виральность: контентом не делятся")
    
    # 4. Удаления
    if deleted_count > 3:
        score += 2
        findings.append(f"Удаления: {deleted_count} постов из 20 скрыты")

    return findings, score, er

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text or len(text) > 100: return
    
    clean_id = text.strip().replace("@", "").split('/')[-1]
    status_msg = await update.message.reply_text(f"🚀 Запускаю глубокий аудит @{clean_id}...")

    # Сначала получаем общую инфу о канале (сабы)
    info_url = f"https://api.tgstat.ru/channels/stat?token={TGSTAT_TOKEN}&channelId={clean_id}"
    ch_data = requests.get(info_url).json().get('response', {})
    subs = ch_data.get('participants_count', 0)
    
    posts = get_tgstat_posts(clean_id)
    if not posts:
        await status_msg.edit_text("❌ Данные не найдены.")
        return

    findings, score, er = run_deep_audit(posts, subs)

    prompt = (
        f"Ты — эксперт по выявлению фрода в Telegram. Твой вердикт основан на цифрах:\n"
        f"Канал: @{clean_id} (сабов: {subs})\n"
        f"Анализ: {'; '.join(findings)}\n"
        f"Итоговый балл подозрительности: {score}/10\n\n"
        "Твои эталоны: @taknaglo (чист, хаотичен), @shumim_media (накручен, стерилен).\n"
        "Разложи всё по полочкам: Ровность, Виральность, База и Итог (ЧИСТ/НАКРУЧЕН)."
    )

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        await status_msg.edit_text(f"🏁 **Результат экспертизы @{clean_id}:**\n\n{res.choices[0].message.content}")
    except Exception as e:
        await status_msg.edit_text(f"Ошибка нейросети: {e}")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
