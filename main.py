import os
import logging
import asyncio
import requests
import re
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токены из Railway
TOKEN = os.getenv("BOT_TOKEN")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

def escape_markdown(text):
    """Экранирует спецсимволы для Telegram MarkdownV2"""
    parse_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(parse_chars)}])', r'\\\1', text)

async def check_telemetr(channel_id):
    """Проверка одного канала через API Telemetr"""
    url = "https://api.telemetr.me/channels/get"
    headers = {
        "Authorization": f"Bearer {TELEMETR_TOKEN}",
        "Accept": "application/json"
    }
    params = {"channelId": channel_id}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            info = data.get('response', {})
            
            # Проверяем метки накрутки
            is_fake = info.get('is_fake', False)
            restrictions = info.get('restrictions', [])
            
            if is_fake or restrictions:
                reason = ", ".join(restrictions) if restrictions else "Метка накрутки"
                return f"🚩 @{channel_id}: *ФРОД* ({reason})"
            else:
                # УБРАЛИ ПОДПИСЧИКОВ: теперь только галочка и статус
                return f"✅ @{channel_id}: Чисто"
        
        elif response.status_code == 403:
            return f"🚫 @{channel_id}: Ошибка 403 (Нет прав API)"
        else:
            return f"⚠️ @{channel_id}: Ошибка {response.status_code}"
            
    except Exception as e:
        logger.error(f"Ошибка API: {e}")
        return f"❌ @{channel_id}: Ошибка соединения"

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие"""
    await update.message.reply_text(
        "👋 Привет! Пришли мне список каналов, и я проверю их на накрутку через Telemetr.\n"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка пачки каналов"""
    text = update.message.text
    if not text: return

    # Вытаскиваем юзернеймы
    raw_channels = re.findall(r'(?:@|t\.me\/|https:\/\/t\.me\/)([a-zA-Z0-9_]{5,})', text)
    channels = list(dict.fromkeys(raw_channels))

    if not channels:
        await update.message.reply_text("Не нашел ссылок на каналы в сообщении.")
        return

    status_msg = await update.message.reply_text(f"🔎 Проверяю {len(channels)} каналов...")

    results = []
    for index, channel in enumerate(channels, 1):
        res = await check_telemetr(channel)
        results.append(res)
        
        if index % 3 == 0 or index == len(channels):
            current_status = f"⏳ Прогресс: {index}/{len(channels)}\n\n" + "\n".join(results)
            try:
                await status_msg.edit_text(escape_markdown(current_status), parse_mode='MarkdownV2')
            except Exception:
                await status_msg.edit_text(current_status)
        
        await asyncio.sleep(1.2)

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling(drop_pending_updates=True)
