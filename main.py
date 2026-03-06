import os
import logging
import requests
import statistics
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

def run_deep_audit(posts, subs):
    """Математика с защитой от пустых значений"""
    if not posts:
        return ["Нет данных по постам"], 0, 0
    
    # Пытаемся достать просмотры и пересылки (учитываем разные имена ключей API)
    reaches = []
    forwards = []
    for p in posts:
        v = p.get('views_count') or p.get('views') or 0
        f = p.get('forwards_count') or p.get('forwards') or 0
        if not p.get('is_deleted'):
            reaches.append(v)
            forwards.append(f)
    
    if not reaches:
        return ["Охваты не найдены в ответе API"], 0, 0

    score = 0
    findings = []
    avg_reach = statistics.mean(reaches)
    er = (avg_reach / subs * 100) if subs > 0 else 0

    # Проверка на Хаос (CV)
    if len(reaches) >= 5:
        stdev_r = statistics.stdev(reaches)
        cv = (stdev_r / avg_reach * 100) if avg_reach > 0 else 0
        if cv > 40:
            score -= 4
            findings.append(f"Живой хаос (CV {round(cv,1)}%)")
        elif cv < 5:
            score += 5
            findings.append(f"Стерильная ровность (CV {round(cv,1)}%)")

    # Проверка виральности
    total_fwd = sum(forwards)
    if total_fwd > 5:
        score -= 2
        findings.append(f"Есть репосты ({total_fwd})")

    return findings, score, er

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return
    
    clean_id = text.strip().replace("@", "").split('/')[-1]
    status_msg = await update.message.reply_text(f"📡 Запрос к TGStat для @{clean_id}...")

    try:
        # 1. Получаем инфо о канале
        info_url = f"https://api.tgstat.ru/channels/stat?token={TGSTAT_TOKEN}&channelId={clean_id}"
        info_data = requests.get(info_url).json()
        logger.info(f"INFO RESPONSE: {info_data}") # Лог в Railway

        if info_data.get('status') != 'ok':
            await status_msg.edit_text(f"❌ Ошибка TGStat: {info_data.get('error', 'Канал не найден')}")
            return

        res_info = info_data.get('response', {})
        subs = res_info.get('participants_count', 0)

        # 2. Получаем посты
        posts_url = f"https://api.tgstat.ru/posts/list?token={TGSTAT_TOKEN}&channelId={clean_id}&limit=20"
        posts_data = requests.get(posts_url).json()
        logger.info(f"POSTS RESPONSE: {str(posts_data)[:500]}...") # Лог начала ответа

        posts = posts_data.get('response', {}).get('items', [])
        
        findings, score, er = run_deep_audit(posts, subs)

        prompt = (
            f"Ты — антифрод-эксперт. Канал @{clean_id}.\n"
            f"Метрики: сабы {subs}, ERR {round(er,1)}%.\n"
            f"Факты: {', '.join(findings)}. Балл фрода: {score}/10.\n"
            "Напиши коротко: Чист или Накручен, и почему."
        )

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        
        await status_msg.edit_text(f"🏁 **@{clean_id}**\n\n{completion.choices[0].message.content}")

    except Exception as e:
        logger.error(f"CRITICAL ERROR: {e}")
        await status_msg.edit_text(f"📛 Ошибка: {str(e)}")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
