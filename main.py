import os
import logging
import asyncio
import requests
import re
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

def escape_markdown(text):
    parse_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(parse_chars)}])', r'\\\1', text)

def get_clean_id(channel_input):
    # Улучшенная очистка: убираем протоколы и домены, оставляем только хвост
    clean = re.sub(r'https?:\/\/(?:t\.me|shumim\.me|tgstat\.ru)\/', '', channel_input)
    clean = clean.split('/')[-1].replace('@', '').strip()
    return clean

async def check_telemetr(channel_id):
    url = "https://api.telemetr.me/channels/get"
    headers = {"Authorization": f"Bearer {TELEMETR_TOKEN}", "Accept": "application/json"}
    params = {"channelId": channel_id}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            info = response.json().get('response', {})
            if info.get('is_fake') or info.get('restrictions'):
                return "FRAUD", f"🚩 @{channel_id}: *ФРОД* (Telemetr)"
            return "CLEAN", None
        return "ERROR", f"⚠️ @{channel_id}: Ошибка Telemetr"
    except:
        return "ERROR", f"❌ @{channel_id}: Ошибка связи"

async def check_tgstat(channel_id):
    url = "https://api.tgstat.ru/channels/stat"
    clean_id = get_clean_id(channel_id)
    
    params = {"token": TGSTAT_TOKEN, "channelId": clean_id}
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'error':
                return f"⚠️ @{clean_id}: TGStat ({data.get('error')})"
            
            # Проверяем метки напрямую в объекте канала
            ch_data = data.get('response', {})
            # TGStat помечает проблемные каналы через эти поля
            if ch_data.get('is_scam') or ch_data.get('red_label') or ch_data.get('is_fake'):
                return f"🚩 @{clean_id}: *ФРОД* (TGStat)"
            
            return f"✅ @{clean_id}: Чисто (Проверен везде)"
        return f"⚠️ @{clean_id}: Ошибка TGStat ({response.status_code})"
    except:
        return f"❌ @{clean_id}: Ошибка связи"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return

    # Улучшенный поиск: ловит юзернеймы и любые ссылки, похожие на тг
    raw_channels = re.findall(r'(?:@|t\.me\/|shumim_media|[\w\d_]{5,})', text)
    # Очищаем и фильтруем мусор
    channels = []
    for c in raw_channels:
        cid = get_clean_id(c)
        if len(cid) >= 5 and cid not in [get_clean_id(x) for x in channels]:
            channels.append(c)

    if not channels:
        await update.message.reply_text("Не нашел каналов.")
        return

    status_msg = await update.message.reply_text(f"🔎 Двойная проверка {len(channels)} каналов...")
    results = []

    for index, channel in enumerate(channels, 1):
        state, report = await check_telemetr(get_clean_id(channel))
        
        if state == "CLEAN":
            final_report = await check_tgstat(channel)
            results.append(final_report)
        else:
            results.append(report)
        
        if index % 2 == 0 or index == len(channels):
            current_status = f"⏳ Прогресс: {index}/{len(channels)}\n\n" + "\n".join(results)
            try:
                await status_msg.edit_text(escape_markdown(current_status), parse_mode='MarkdownV2')
            except:
                await status_msg.edit_text(current_status)
        await asyncio.sleep(1.5)

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Жду список!")))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling(drop_pending_updates=True)
