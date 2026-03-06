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

def run_deep_audit(posts, subs):
    """Математический анализ с защитой авторских каналов"""
    if not posts: return ["Недостаточно данных для анализа"], 0, 0
    
    # Собираем просмотры и пересылки, фильтруя пустые значения
    reaches = [p.get('views_count', 0) for p in posts if p.get('views_count') is not None]
    forwards = [p.get('forwards_count', 0) for p in posts if p.get('forwards_count') is not None]
    deleted_count = sum(1 for p in posts if p.get('is_deleted'))
    
    if not reaches or len(reaches) < 3:
        return ["Слишком мало свежих постов для анализа"], 0, 0

    score = 0
    findings = []
    
    avg_reach = statistics.mean(reaches)
    er = (avg_reach / subs * 100) if subs > 0 else 0

    # 1. АНАЛИЗ РОВНОСТИ (CV) — Ищем 'причесанные' цифры
    if len(reaches) >= 5:
        stdev_r = statistics.stdev(reaches)
        cv = (stdev_r / avg_reach * 100) if avg_reach > 0 else 0
        
        if cv < 5: # Слишком ровно = боты
            score += 4
            findings.append(f"Стерильная ровность (CV {round(cv,1)}%)")
        elif cv > 35: # Хаотично = живой автор
            score -= 3
            findings.append(f"Естественный хаос охватов (CV {round(cv,1)}%)")

    # 2. АНАЛИЗ ВИРАЛЬНОСТИ
    total_fwd = sum(forwards)
    fwd_ratio = (total_fwd / sum(reaches) * 100) if sum(reaches) > 0 else 0
    
    if fwd_ratio > 0.5: # Если хотя бы 1 из 200 человек репостнул — это жизнь
        score -= 3
        findings.append(f"Хорошая виральность (репосты: {total_fwd})")
    elif total_fwd == 0 and avg_reach > 1000:
        score += 2
        findings.append("Просмотры есть, а пересылок ноль")

    # 3. АНАЛИЗ УДАЛЕНИЙ
    if deleted_count > 4:
        score += 2
        findings.append(f"Много удаленных постов ({deleted_count})")

    # 4. КОРРЕКЦИЯ ПО РАЗМЕРУ
    if subs < 50000 and er > 20: # Маленький авторский канал с высоким вовлечением
        score -= 2
        findings.append("Высокая лояльность аудитории")

    return findings, score, er

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text or len(text) > 100: return
    
    clean_id = text.strip().replace("@", "").split('/')[-1]
    status_msg = await update.message.reply_text(f"🔍 Анализирую @{clean_id}...")

    try:
        # Получаем данные о канале
        info_url = f"https://api.tgstat.ru/channels/stat?token={TGSTAT_TOKEN}&channelId={clean_id}"
        info_res = requests.get(info_url).json()
        
        if info_res.get('status') != 'ok':
            await status_msg.edit_text("❌ Канал не найден в базе TGStat.")
            return

        ch_data = info_res.get('response', {})
        subs = ch_data.get('participants_count', 0)
        red_label = ch_data.get('red_label', False)
        
        # Получаем посты
        posts_url = f"https://api.tgstat.ru/posts/list?token={TGSTAT_TOKEN}&channelId={clean_id}&limit=20"
        posts_res = requests.get(posts_url).json()
        posts = posts_res.get('response', {}).get('items', [])

        findings, score, er = run_deep_audit(posts, subs)

        # Если есть Red Label от самого TGStat — это авто-бан
        if red_label:
            score = 10
            findings.append("МЕТКА TGSTAT: КАНАЛ В ЧЕРНОМ СПИСКЕ")

        prompt = (
            f"Ты — антифрод-эксперт. Вынеси вердикт каналу @{clean_id}.\n"
            f"Данные: сабы={subs}, средний ER={round(er,1)}%.\n"
            f"Результаты теста: {'; '.join(findings)}.\n"
            f"Балл подозрительности: {score}/10.\n\n"
            "Твои эталоны: @taknaglo (чист, много пересылок, хаос), @shumim_media (накручен, стерилен).\n"
            "Напиши: База, Ровность, Виральность и финальный Итог."
        )

        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        await status_msg.edit_text(f"🏁 **Экспертиза @{clean_id}:**\n\n{res.choices[0].message.content}")

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await status_msg.edit_text(f"❌ Произошла ошибка при анализе.")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
