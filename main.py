import os
import logging
import asyncio
import requests
import re
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токены из Railway
TOKEN = os.getenv("BOT_TOKEN")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN") # Добавь этот токен в настройки Railway!

def escape_markdown(text):
    parse_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(parse_chars)}])', r'\\\1', text)

async def check_telemetr(channel_id):
    """Проверка в Telemetr (Первый фильтр)"""
    url = "https://api.telemetr.me/channels/get"
    headers = {"Authorization": f"Bearer {TELEMETR_TOKEN}", "Accept": "application/json"}
    params = {"channelId": channel_id}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            info = response.json().get('response', {})
            if info.get('is_fake') or info.get('restrictions'):
                return "FRAUD", f"🚩 @{channel_id}: ФРОД (Telemetr)"
            return "CLEAN", None
        return "ERROR", f"⚠️ @{channel_id}: Ошибка Telemetr ({response.status_code})"
    except:
        return "ERROR", f"❌ @{channel_id}: Ошибка соединения (Telemetr)"

async def check_tgstat(channel_id):
    """Проверка в TGStat (Второй фильтр)"""
    # Используем метод канала для проверки меток
    url = f"https://api.tgstat.ru/channel/stat?token={TGSTAT_TOKEN}&channelId={channel_id}"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'error':
                return f"⚠️ @{channel_id}: TGStat не нашел канал"
            
            # В TGStat ищем пометки 'red_border' или подобные метки в статусе
            # Обычно это поля 'is_scam' или специфические теги в объекте канала
            channel_info = data.get('response', {}).get('channel', {})
            if channel_info.get('is_scam') or channel_info.get('red_label'):
                return f"🚩 @{channel_id}: ФРОД (TGStat)"
            
            return f"✅ @{channel_id}: Чисто (Прошел обе проверки)"
        
        return f"⚠️ @{channel_id}: Ошибка TGStat ({response.status_code})"
    except:
        return f"❌ @{channel_id}: Ошибка соединения (TGStat)"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    raw_channels = re.findall(r'(?:@|t\.me\/|https:\/\/t\.me\/)([a-zA-Z0-9_]{5,})', text)
    channels = list(dict.fromkeys(raw_channels))

    if not channels:
        await update.message.reply_text("Не нашел ссылок на каналы.")
        return

    status_msg = await update.message.reply_text(f"🔎 Двойная проверка {len(channels)} каналов...")
    results = []

    for index, channel in enumerate(channels, 1):
        # 1. Сначала Telemetr
        state, report = await check_telemetr(channel)
        
        if state == "CLEAN":
            # 2. Если в Телеметре чисто, идем в TGStat
            final_report = await check_tgstat(channel)
            results.append(final_report)
        else:
            # Если фрод или ошибка — сразу в результат
            results.append(report)
        
        if index % 2 == 0 or index == len(channels):
            current_status = f"⏳ Прогресс: {index}/{len(channels)}\n\n" + "\n".join(results)
            try:
                await status_msg.edit_text(escape_markdown(current_status), parse_mode='MarkdownV2')
            except:
                await status_msg.edit_text(current_status)
        
        await asyncio.sleep(1.5) # Пауза важна для обоих API

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Пришли список для двойной проверки!")))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling(drop_pending_updates=True)
