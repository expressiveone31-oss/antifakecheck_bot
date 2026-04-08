import os
import logging
import asyncio
import requests
import re
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

async def check_telemetr(channel_id):
    url = "https://api.telemetr.me/channels/get"
    headers = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}
    try:
        r = requests.get(url, headers=headers, params={"channelId": channel_id}, timeout=10)
        if r.status_code == 200:
            data = r.json().get('response', {})
            if data.get('is_fake') or data.get('restrictions'):
                return "FRAUD", f"🚩 @{channel_id}: ФРОД (Telemetr)"
            return "CLEAN", None
        return "ERROR", f"⚠️ @{channel_id}: Ошибка API"
    except:
        return "ERROR", f"❌ @{channel_id}: Связь"

async def check_tgstat(channel_id):
    url = "https://api.tgstat.ru/channels/get"
    try:
        r = requests.get(url, params={"token": TGSTAT_TOKEN, "channelId": channel_id}, timeout=10)
        if r.status_code == 200:
            res = r.json().get('response', {})
            if res.get('red_label') == 1 or res.get('is_scam') == 1:
                return f"🚩 @{channel_id}: ФРОД (TGStat)"
            return f"✅ @{channel_id}: Чисто"
        return f"⚠️ @{channel_id}: Ошибка API"
    except:
        return f"❌ @{channel_id}: Связь"

async def handle_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Лог для проверки: видит ли бот сообщение вообще
    logger.info(f"Получено сообщение: {update.message.text}")
    
    if not update.message.text: return
    
    # Ищем юзернеймы
    potential = re.findall(r'(?:@|t\.me\/|https?:\/\/)?([a-zA-Z0-9_]{5,})', update.message.text)
    channels = list(set([p.split('/')[-1].replace('@', '').strip() for p in potential]))

    if not channels:
        await update.message.reply_text("Пришли ссылку на канал или @username.")
        return

    msg = await update.message.reply_text(f"⌛ Проверяю: {', '.join(channels)}...")
    results = []

    for c in channels:
        state, report = await check_telemetr(c)
        if state == "CLEAN":
            results.append(await check_tgstat(c))
        else:
            results.append(report)
    
    await msg.edit_text("\n".join(results))

if __name__ == '__main__':
    if not TOKEN:
        logger.error("BOT_TOKEN IS MISSING!")
        exit(1)

    app = ApplicationBuilder().token(TOKEN).build()
    
    # Обрабатываем ВООБЩЕ ВСЕ текстовые сообщения
    app.add_handler(MessageHandler(filters.ALL, handle_any_message))
    
    logger.info("Бот запущен. Удаляю вебхуки...")
    app.run_polling(drop_pending_updates=True)
