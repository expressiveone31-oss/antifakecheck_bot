import os
import logging
import asyncio
import requests
import re
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

# Настройка логов, чтобы видеть ошибки в Railway
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

def get_clean_id(channel_input):
    clean = channel_input.split('/')[-1].replace('@', '').strip()
    if clean.lower() in ['https', 'http', 't.me', '']: return None
    return clean

async def check_telemetr(channel_id):
    url = "https://api.telemetr.me/channels/get"
    headers = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}
    try:
        r = requests.get(url, headers=headers, params={"channelId": channel_id}, timeout=10)
        if r.status_code == 200:
            info = r.json().get('response', {})
            if info.get('is_fake') or info.get('restrictions'):
                return "FRAUD", f"🚩 @{channel_id}: *ФРОД* (Telemetr)"
            return "CLEAN", None
        return "ERROR", f"⚠️ @{channel_id}: Ошибка Telemetr"
    except:
        return "ERROR", f"❌ @{channel_id}: Связь"

async def check_tgstat(channel_id):
    # План Б: Метод channels/get часто надежнее отдает метки модерации
    url = "https://api.tgstat.ru/channels/get"
    try:
        r = requests.get(url, params={"token": TGSTAT_TOKEN, "channelId": channel_id}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            ch = data.get('response', {})
            # Проверяем метку напрямую
            if ch.get('red_label') == 1 or ch.get('is_scam') == 1:
                return f"🚩 @{channel_id}: *ФРОД* (TGStat: накрутка)"
            return f"✅ @{channel_id}: Чисто"
        return f"⚠️ @{channel_id}: Ошибка TGStat"
    except:
        return f"❌ @{channel_id}: Связь"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text: return
    
    # Собираем всё, что похоже на юзернеймы
    potential = re.findall(r'(?:@|t\.me\/|https?:\/\/)?([a-zA-Z0-9_]{5,})', update.message.text)
    channels = list(set([get_clean_id(p) for p in potential if get_clean_id(p)]))

    if not channels:
        await update.message.reply_text("Каналы не найдены.")
        return

    msg = await update.message.reply_text(f"🔎 Проверяю {len(channels)}...")
    results = []

    for c in channels:
        state, report = await check_telemetr(c)
        if state == "CLEAN":
            final = await check_tgstat(c)
            results.append(final)
        else:
            results.append(report)
        
        # Обновляем сообщение без Markdown, чтобы не падать от ошибок парсинга
        res_text = f"⏳ Прогресс: {len(results)}/{len(channels)}\n\n" + "\n".join(results)
        try:
            await msg.edit_text(res_text)
        except: pass
        await asyncio.sleep(1)

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    # КРИТИЧНО: Игнорируем все старые сообщения при запуске
    print("Бот запускается...")
    app.run_polling(drop_pending_updates=True)
