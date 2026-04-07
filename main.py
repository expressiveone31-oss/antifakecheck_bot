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
    """Проверка через метод stat + глубокий поиск скрытых меток"""
    # Метод stat часто содержит больше 'живых' данных о нарушениях
    url = "https://api.tgstat.ru/channels/stat"
    clean_id = get_clean_id(channel_id)
    params = {"token": TGSTAT_TOKEN, "channelId": clean_id}
    
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            
            if data.get('status') == 'error':
                # Если канал удален из базы за накрутку, TGStat вернет ошибку 'channel not found'
                return f"🚩 @{clean_id}: *ФРОД* (TGStat: Удален или забанен)"
            
            ch_data = data.get('response', {})
            
            # 1. Проверка явных флагов (теперь ловим и через метод stat)
            # Иногда они приходят как строки "0"/"1", поэтому делаем int()
            is_scam = int(ch_data.get('is_scam', 0))
            red_label = int(ch_data.get('red_label', 0))
            
            if is_scam == 1 or red_label == 1:
                return f"🚩 @{clean_id}: *ФРОД* (TGStat: Метка накрутки)"
            
            # 2. Проверка аномалий (ИЦ = 0 при большом охвате или скрытая статистика)
            # Если у канала > 1000 сабов, но ИЦ (ci_index) равен 0 — это подозрительно
            participants = ch_data.get('participants_count', 0)
            ci_index = ch_data.get('ci_index', 0)
            
            if participants > 1000 and ci_index == 0:
                return f"🚩 @{clean_id}: *ФРОД* (TGStat: Аномальный ИЦ)"

            return f"✅ @{clean_id}: Чисто (Проверен везде)"
            
        return f"⚠️ @{clean_id}: Ошибка TGStat ({response.status_code})"
    except Exception as e:
        logger.error(f"TGStat Error: {e}")
        return f"❌ @{clean_id}: Ошибка связи"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return

    # Улучшенный поиск ссылок, который не ломается от кривых доменов
    potential = re.findall(r'(?:@|t\.me\/|https?:\/\/)?([a-zA-Z0-9_]{5,})', text)
    
    channels = []
    for p in potential:
        cid = get_clean_id(p)
        if cid and cid not in channels:
            channels.append(cid)

    if not channels:
        await update.message.reply_text("Не нашел ссылок на каналы.")
        return

    status_msg = await update.message.reply_text(f"🔎 Проверка {len(channels)} каналов...")
    results = []

    for index, channel in enumerate(channels, 1):
        state, report = await check_telemetr(channel)
        if state == "CLEAN":
            final_report = await check_tgstat(channel)
            results.append(final_report)
        else:
            results.append(report)
        
        if index % 2 == 0 or index == len(channels):
            # Самое важное: экранируем весь текст перед отправкой
            current_status = f"⏳ Прогресс: {index}/{len(channels)}\n\n" + "\n".join(results)
            safe_text = escape_markdown(current_status) 
            try:
                await status_msg.edit_text(safe_text, parse_mode='MarkdownV2')
            except Exception as e:
                # Если Markdown всё равно подвел — шлем обычным текстом
                logger.error(f"Markdown error: {e}")
                await status_msg.edit_text(current_status)
        await asyncio.sleep(1.2)

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Бот готов! Присылай список.")))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling(drop_pending_updates=True)
