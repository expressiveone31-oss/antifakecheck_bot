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

def analyze_logic(posts, subs):
    """Математика, которая отличает автора от бота"""
    if not posts: return ["Нет данных по постам"], 0, 0
    
    reaches = [p.get('views_count', 0) for p in posts if not p.get('is_deleted')]
    forwards = [p.get('forwards_count', 0) for p in posts]
    
    if not reaches: return ["Охваты не найдены"], 0, 0

    score = 0
    findings = []
    avg_reach = statistics.mean(reaches)
    er = (avg_reach / subs * 100) if subs > 0 else 0

    # 1. ПРОВЕРКА НА ХАОС (CV)
    if len(reaches) >= 5:
        cv = (statistics.stdev(reaches) / avg_reach * 100) if avg_reach > 0 else 0
        if cv > 40: # Рваные охваты = ЖИЗНЬ
            score -= 4
            findings.append(f"Естественный хаос (CV {round(cv,1)}%)")
        elif cv < 5: # Идеально ровно = БОТЫ
            score += 5
            findings.append(f"Стерильная ровность (CV {round(cv,1)}%)")

    # 2. ПРОВЕРКА ВИРАЛЬНОСТИ
    total_fwd = sum(forwards)
    if total_fwd > 10:
        score -= 2
        findings.append(f"Контентом делятся (репосты: {total_fwd})")

    return findings, score, er

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return
    
    clean_id = text.strip().replace("@", "").split('/')[-1]
    status_msg = await update.message.reply_text(f"🔬 Проверяю @{clean_id}...")

    try:
        # 1. Инфо о канале
        info_r = requests.get(f"https://api.tgstat.ru/channels/stat?token={TGSTAT_TOKEN}&channelId={clean_id}", timeout=10)
        if info_r.status_code != 200:
            await status_msg.edit_text("❌ Ошибка сервера TGStat.")
            return
        
        info_data = info_r.json()
        if info_data.get('status') != 'ok':
            await status_msg.edit_text(f"❌ Ошибка: {info_data.get('error')}")
            return

        ch_data = info_data.get('response', {})
        subs = ch_data.get('participants_count', 0)

        # 2. Посты
        posts_r = requests.get(f"https://api.tgstat.ru/posts/list?token={TGSTAT_TOKEN}&channelId={clean_id}&limit=20", timeout=10)
        posts_data = posts_r.json()
        posts = posts_data.get('response', {}).get('items', [])

        findings, score, er = analyze_logic(posts, subs)

        # 3. Вердикт GPT
        prompt = (
            f"Ты — антифрод-эксперт. Канал: @{clean_id}.\n"
            f"Факты: {', '.join(findings)}. Балл фрода: {score}/10.\n"
            f"Метрики: сабы {subs}, ERR {round(er,1)}%.\n"
            "Вердикт: если балл < 2 — ЧИСТ, если > 5 — НАКРУЧЕН. Объясни через хаос или стерильность."
        )

        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        await status_msg.edit_text(f"🏁 **@{clean_id}**\n\n{res.choices[0].message.content}")

    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text("📛 Ошибка связи. Попробуй еще раз.")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
