import os
import logging
import asyncio
import requests
import re
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токены из настроек Railway
TOKEN = os.getenv("BOT_TOKEN")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

async def check_telemetr(channel_id):
    """Функция для проверки одного канала на флаг накрутки"""
    url = f"https://api.telemetr.me/channels/stat?id={channel_id}"
    headers = {"Authorization": f"Bearer {TELEMETR_TOKEN}", "Accept": "application/json"}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            # Достаем основную информацию о канале
            info = data.get('response', {})
            
            # Ищем признаки накрутки/бана (поля могут меняться в API, проверяем основные)
            is_fake = info.get('is_fake', False)
            is_scam = info.get('is_scam', False)
            restrictions = info.get('restrictions', []) # Список меток от Телеметра
            
            if is_fake or is_scam or restrictions:
                reason = ", ".join(restrictions) if restrictions else "Метка накрутки/scam"
                return f"🚩 @{channel_id}: **НАКРУТКА** ({reason})"
            else:
                subs = info.get('participants_count', 0)
                return f"✅ @{channel_id}: Чисто (сабов: {subs})"
        
        elif response.status_code == 404:
            return f"❓ @{channel_id}: Не найден в базе Telemetr"
        else:
            return f"⚠️ @{channel_id}: Ошибка API ({response.status_code})"
    except Exception as e:
        return f"❌ @{channel_id}: Ошибка соединения"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return

    # 1. Извлекаем все юзернеймы из сообщения (ищем слова с @ или ссылки)
    raw_channels = re.findall(r'(?:@|t\.me\/|https:\/\/t\.me\/)([a-zA-Z0-9_]{5,})', text)
    channels = list(set(raw_channels)) # Убираем дубликаты

    if not channels:
        await update.message.reply_text("Пришли мне список каналов (через @ или ссылками), и я их проверю.")
        return

    status_msg = await update.message.reply_text(f"🔎 Начинаю проверку {len(channels)} каналов...")

    results = []
    for channel in channels:
        res = await check_telemetr(channel)
        results.append(res)
        # Небольшая пауза, чтобы не спамить API и в телеграме было видно прогресс
        if len(results) % 3 == 0:
            await status_msg.edit_text(f"⏳ Проверено {len(results)} из {len(channels)}...\n\n" + "\n".join(results))
        await asyncio.sleep(0.5)

    final_text = f"🏁 **Результаты проверки:**\n\n" + "\n".join(results)
    # Если текст слишком длинный, Телеграм его не пропустит (лимит 4096 символов)
    if len(final_text) > 4000:
        final_text = final_text[:3900] + "\n\n...список слишком длинный"
        
    await status_msg.edit_text(final_text, parse_mode='Markdown')

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я готов проверять каналы на накрутку через Telemetr.\n\n"
        "Просто пришли мне юзернейм (через @) или ссылку на канал.\n"
        "Можно прислать сразу **список из нескольких каналов** одним сообщением!"
    )

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Добавляем реакцию на /start
    from telegram.ext import CommandHandler
    app.add_handler(CommandHandler("start", start_command))
    
    # Реакция на обычный текст (списки каналов)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    logger.info("Бот запущен и ждет сообщений...")
    app.run_polling(drop_pending_updates=True)
