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
        r = requests.get(url, headers=headers, params={"channelId": channel_id}, timeout=10)
        if r.status_code == 200:
            data = r.json().get('response', {})
            if data.get('is_fake') or data.get('restrictions'):
                return "FRAUD", f"🚩 @{channel_id}: ФРОД (Telemetr)"
            return "CLEAN", None
        return "SKIP", None
    except:
        return "SKIP", None

async def check_tgstat_fraud_only(channel_id):
    """Прицельная проверка меток через тяжелый метод get"""
    url = "https://api.tgstat.ru/channels/get"
    params = {"token": TGSTAT_TOKEN, "channelId": channel_id}
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json().get('response', {})
            res = data.get('tgstat_restrictions', {})
            # Проверяем все варианты меток, которые подтвердила поддержка
            if res.get('red_label') is True or data.get('red_label') == 1:
                return f"🚩 @{channel_id}: ФРОД (TGStat: метка)"
            if res.get('black_label') is True:
                return f"🚩 @{channel_id}: МОШЕННИЧЕСТВО (TGStat)"
        return None
    except:
        return None

async def check_tgstat_full(channel_id):
    """Гибридный метод: сначала легкий stat, потом прицельный поиск фрода"""
    url_stat = "https://api.tgstat.ru/channels/stat"
    params = {"token": TGSTAT_TOKEN, "channelId": channel_id}
    
    try:
        # 1. Быстрая проверка
        r = requests.get(url_stat, params=params, timeout=10)
        if r.status_code == 200:
            # 2. Если по stat всё ок, идем проверять именно на метку фрода
            fraud_report = await check_tgstat_fraud_only(channel_id)
            if fraud_report:
                return fraud_report
            
            return f"✅ @{channel_id}: Чисто"
        
        return f"⚠️ @{channel_id}: Ошибка API"
    except:
        return f"❌ @{channel_id}: Ошибка связи"

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот готов! Присылай список каналов.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text or update.message.text.startswith('/'): return
    
    raw_found = re.findall(r'(?:@|t\.me\/|https?:\/\/)?([a-zA-Z0-9_]{4,})', update.message.text)
    channels = []
    for p in raw_found:
        cid = get_clean_id(p)
        if cid and cid not in channels: channels.append(cid)

    if not channels:
        await update.message.reply_text("Не нашел каналов.")
        return

    status_msg = await update.message.reply_text(f"🔎 Проверяю {len(channels)}...")
    results = []

    for c in channels:
        state, report = await check_telemetr(c)
        
        if state == "FRAUD":
            results.append(report)
        else:
            # Если Телеметр промолчал, делаем гибридный ТГСтат
            results.append(await check_tgstat_full(c))
        
        # Обновляем сообщение
        progress = f"⏳ Прогресс: {len(results)}/{len(channels)}\n\n" + "\n".join(results)
        try:
            await status_msg.edit_text(progress)
        except: pass
        
        # Пауза 1.5 сек для стабильности между разными каналами
        await asyncio.sleep(1.5)

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling(drop_pending_updates=True)
