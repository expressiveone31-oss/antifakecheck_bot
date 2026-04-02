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
    """Строгая очистка юзернейма"""
    # Убираем все, что до последнего слеша и символ @
    clean = channel_input.split('/')[-1].replace('@', '').strip()
    # Если после очистки осталось 'https' или пустота — это мусор
    if clean.lower() in ['https', 'http', 't.me', '']:
        return None
    return clean

async def check_tgstat(channel_id):
    """Глубокая проверка меток накрутки в TGStat"""
    url = "https://api.tgstat.ru/channels/get"
    clean_id = get_clean_id(channel_id)
    params = {"token": TGSTAT_TOKEN, "channelId": clean_id}
    
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            
            # Если API вернул ошибку (например, канал забанен в базе)
            if data.get('status') == 'error':
                return f"🚩 @{clean_id}: *ФРОД* (TGStat: {data.get('error')})"
            
            ch_info = data.get('response', {})
            
            # Проверяем ВСЕ возможные маркеры проблем
            # 0 — чисто, 1 — есть метка
            is_scam = ch_info.get('is_scam', 0)
            red_label = ch_info.get('red_label', 0)
            is_fake = ch_info.get('is_fake', 0)
            
            # Ловим 'lovi_trand' и подобные
            if is_scam == 1 or red_label == 1 or is_fake == 1:
                return f"🚩 @{clean_id}: *ФРОД* (TGStat: метка накрутки)"
            
            return f"✅ @{clean_id}: Чисто (Проверен везде)"
            
        return f"⚠️ @{clean_id}: Ошибка TGStat ({response.status_code})"
    except Exception as e:
        logger.error(f"TGStat Error: {e}")
        return f"❌ @{clean_id}: Ошибка связи"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return

    # Ищем потенциальные ссылки и юзернеймы
    potential = re.findall(r'(?:@|t\.me\/|https?:\/\/)?([a-zA-Z0-9_]{5,})', text)
    
    channels = []
    for p in potential:
        cid = get_clean_id(p)
        if cid and cid not in channels:
            channels.append(cid)

    if not channels:
        await update.message.reply_text("Не нашел корректных юзернеймов каналов.")
        return

    status_msg = await update.message.reply_text(f"🔎 Двойная проверка {len(channels)} каналов...")
    results = []

    for index, channel in enumerate(channels, 1):
        # 1. Telemetr
        state, report = await check_telemetr(channel)
        
        if state == "CLEAN":
            # 2. TGStat (метод get)
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
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Бот готов! Присылай список.")))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling(drop_pending_updates=True)
