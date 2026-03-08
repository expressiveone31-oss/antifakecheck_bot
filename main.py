import os
import logging
import requests
import statistics
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

# Настройка логирования для Railway
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def calculate_audit(posts, subs):
    if not posts:
        return None, 0, 0
    
    reaches = [p.get('views_count', 0) for p in posts if p.get('views_count') is not None]
    
    if not reaches:
        return None, 0, 0

    avg_reach = statistics.mean(reaches)
    er = (avg_reach / subs * 100) if subs > 0 else 0
    
    score = 0
    findings = []

    # Проверка вариативности охватов
    if len(reaches) >= 5:
        cv = (statistics.stdev(reaches) / avg_reach * 100) if avg_reach > 0 else 0
        if cv < 7:
            score += 4
            findings.append(f"Стерильные охваты (CV {round(cv, 1)}%)")
        elif cv > 35:
            score -= 3
            findings.append(f"Естественная динамика (CV {round(cv, 1)}%)")

    # Защита малых авторских каналов
    if subs < 50000 and er > 20:
        score -= 2
        findings.append("Высокий вовлеченный микро-канал")

    return findings, score, er

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return
    
    clean_id = text.strip().replace("@", "").split('/')[-1]
    status_msg = await update.message.reply_text(f"🔍 Запрос данных @{clean_id}...")

    try:
        # 1. Запрос основной статистики
        info_url = f"https://api.tgstat.ru/channels/stat?token={TGSTAT_TOKEN}&channelId={clean_id}"
        info_resp = requests.get(info_url, timeout=12)
        
        if info_resp.status_code != 200:
            logger.error(f"TGStat Info Error {info_resp.status_code}: {info_resp.text}")
            await status_msg.edit_text(f"❌ Ошибка API ({info_resp.status_code}). Проверь лимиты или токен.")
            return

        info_data = info_resp.json()
        if info_data.get('status') != 'ok':
            await status_msg.edit_text(f"❌ TGStat говорит: {info_data.get('error', 'ошибка')}")
            return

        subs = info_data.get('response', {}).get('participants_count', 0)

        # 2. Запрос постов
        posts_url = f"https://api.tgstat.ru/posts/list?token={TGSTAT_TOKEN}&channelId={clean_id}&limit=15"
        posts_resp = requests.get(posts_url, timeout=12)
        posts_data = posts_resp.json()
        posts = posts_data.get('response', {}).get('items', [])

        findings, score, er = calculate_audit(posts, subs)

        if findings is None:
            await status_msg.edit_text(f"⚠️ У канала @{clean_id} нет свежих постов для анализа охвата.")
            return

        # 3. Вердикт через GPT
        prompt = (
            f"Ты — антифрод-эксперт. Анализ канала @{clean_id}.\n"
            f"Статистика: {subs} сабов, ERR {round(er, 1)}%.\n"
            f"Тех. анализ: {', '.join(findings)}. Итоговый балл подозрительности: {score}/10.\n"
            "Дай краткий вердикт: ЧИСТ, ПОДОЗРИТЕЛЕН или НАКРУЧЕН. Объясни одной фразой."
        )

        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        
        await status_msg.edit_text(f"🏁 **Результат для @{clean_id}:**\n\n{res.choices[0].message.content}")

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        await status_msg.edit_text(f"📛 Ошибка: {str(e)[:50]}... Проверь логи Railway.")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling()
