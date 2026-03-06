
import os
import logging
import requests
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

def calculate_score(posts, subs):
    """Упрощенная математика: ищем только явные признаки ботов"""
    if not posts: return [], 0, 0
    
    reaches = [p.get('views_count', 0) for p in posts if p.get('views_count') is not None]
    forwards = [p.get('forwards_count', 0) for p in posts if p.get('forwards_count') is not None]
    
    avg_reach = statistics.mean(reaches) if reaches else 0
    er = (avg_reach / subs * 100) if subs > 0 else 0
    
    findings = []
    score = 0

    # 1. Проверка на 'стерильность' (если все посты одинаковые до 5%)
    if len(reaches) >= 5:
        cv = (statistics.stdev(reaches) / avg_reach * 100) if avg_reach > 0 else 0
        if cv < 7: # Слишком ровно
            score += 4
            findings.append("Подозрительно ровные охваты")
        elif cv > 35: # Живой разброс
            score -= 3
            findings.append("Естественная динамика (охваты скачут)")

    # 2. Виральность
    total_fwd = sum(forwards)
    if total_fwd > 0:
        score -= 2
        findings.append(f"Есть репосты ({total_fwd})")
    
    return findings, score, er

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return
    
    clean_id = text.strip().replace("@", "").split('/')[-1]
    status_msg = await update.message.reply_text(f"🔎 Проверяю @{clean_id}...")

    try:
        # Запрос данных
        info_url = f"https://api.tgstat.ru/channels/stat?token={TGSTAT_TOKEN}&channelId={clean_id}"
        info_res = requests.get(info_url).json()
        
        if info_res.get('status') != 'ok':
            await status_msg.edit_text("❌ Канал не найден в TGStat.")
            return

        ch_data = info_res.get('response', {})
        subs = ch_data.get('participants_count', 0)
        
        posts_url = f"https://api.tgstat.ru/posts/list?token={TGSTAT_TOKEN}&channelId={clean_id}&limit=15"
        posts_res = requests.get(posts_url).json()
        posts = posts_res.get('response', {}).get('items', [])

        findings, score, er = calculate_score(posts, subs)

        # Формируем простой запрос для GPT
        prompt = (
            f"Проанализируй канал @{clean_id}. Подписчиков: {subs}, ERR: {round(er,1)}%.\n"
            f"Факты: {', '.join(findings)}. Балл подозрительности: {score}/10.\n"
            "Вердикт: если балл < 3 — ЧИСТ, если > 5 — НАКРУЧЕН. В остальном — ПОДОЗРИТЕЛЕН.\n"
            "Напиши кратко, почему."
        )

        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        await status_msg.edit_text(f"🏁 **Результат @{clean_id}:**\n\n{res.choices[0].message.content}")

    except Exception as e:
        logger.error(f"Error: {e}")
        await status_msg.edit_text("📛 Ошибка API. Попробуйте другой канал или позже.")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
