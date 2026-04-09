import os
import logging
import asyncio
import requests
import re
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

def get_clean_id(channel_input):
    if not channel_input: return None
    clean = re.sub(r'https?:\/\/(?:t\.me|tgstat\.ru|shumim\.me|shumim_media)\/', '', channel_input)
    clean = clean.replace('@', '').strip().split('/')[0]
    stop_words = ['https', 'http', 't.me', 'start', 'help', 'bot']
    if clean.lower() in stop_words or len(clean) < 4: return None
    return clean

async def check_telemetr(channel_id):
    url = "https://api.telemetr.me/channels/get"
    headers = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}
    try:
        # Пытаемся сделать запрос с таймаутом побольше
        r = requests.get(url, headers=headers, params={"channelId": channel_id}, timeout=15)
        if r.status_code == 200:
            data = r.json().get('response', {})
            if data.get('is_fake') or data.get('restrictions'):
                return "FRAUD", f"🚩 @{channel_id}: ФРОД (Telemetr)"
            return "CLEAN", None
        elif r.status_code == 429:
            return "RETRY", "⚠️ Пауза (Лимит API)"
        return "ERROR", f"⚠️ @{channel_id}: Ошибка API ({r.status_code})"
    except:
        return "ERROR", f"❌ @{channel_id}: Ошибка связи"

async def check_tgstat(channel_id):
    url = "https://api.tgstat.ru/channels/get"
    params = {"token": TGSTAT_TOKEN, "channelId": channel_id}
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code == 200:
            data = r.json()
            ch_info = data.get('response', {})
            restrictions = ch_info.get('tgstat_restrictions', {})
            
            if restrictions.get('red_label') is True:
                return f"🚩 @{channel_id}: ФРОД (TGStat: метка)"
            if restrictions.get('black_label') is True:
                return f"🚩 @{channel_id}: МОШЕННИЧЕСТВО (TGStat)"
            return f"✅ @{channel_id}: Чисто"
        elif r.status_code == 429:
            return "⚠️ Лимит запросов (TGStat)"
        return f"⚠️ @{channel_id}: Ошибка API ({r.status_code})"
    except:
        return f"❌ @{channel_id}: Ошибка связи"

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот в строю! Кидай список каналов.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text or update.message.text.startswith('/'): return
    
    raw_found = re.findall(r'(?:@|t\.me\/|https?:\/\/)?([a-zA-Z0-9_]{4,})', update.message.text)
    channels = []
    for p in raw_found:
        cid = get_clean_id(p)
        if cid and cid not in channels: channels.append(cid)

    if not channels:
        await update.message.reply_text("Каналы не найдены.")
        return

    status_msg = await update.message.reply_text(f"🔎 Проверяю {len(channels)} каналов...")
    results = []

    for c in channels:
        state, report = await check_telemetr(c)
        
        # Если словили лимит, ждем чуть дольше и идем дальше
        if state == "RETRY":
            await asyncio.sleep(3)
            state, report = await check_telemetr(c)

        if state == "CLEAN":
            final_report = await check_tgstat(c)
            results.append(final_report)
        else:
            results.append(report)
        
        progress_text = f"⏳ Прогресс: {len(results)}/{len(channels)}\n\n" + "\n".join(results)
        try:
            await status_msg.edit_text(progress_text)
        except: pass
        
        # Увеличиваем паузу между каналами до 2 секунд для стабильности
        await asyncio.sleep(2.0)

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling(drop_pending_updates=True)
