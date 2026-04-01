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

# Токены (подтягиваются из переменных Railway)
TOKEN = os.getenv("BOT_TOKEN")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

async def check_telemetr(channel_id):
    """Проверка одного канала на флаги накрутки"""
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
            
            # Проверяем метки накрутки (fake) и ограничения (restrictions)
            is_fake = info.get('is_fake', False)
            restrictions = info.get('restrictions', [])
            
            if is_fake or restrictions:
                reason = ", ".join(restrictions) if restrictions else "Метка накрутки"
                return f"🚩 @{channel_id}: **ФРОД** ({reason})"
            else:
                subs = info.get('participants_count', 0)
                return f"✅ @{channel_id}: Чисто (сабов: {subs})"
        
        elif response.status_code == 403:
            return f"🚫 @{channel_id}: Ошибка 403 (Нет прав API/Лимиты)"
        else:
            # Пытаемся достать текст ошибки из ответа API
            try:
                err_msg = response.json().get('response', {}).get('message', 'Ошибка данных')
            except:
                err_msg = f"Код {response.status_code}"
            return f"⚠️ @{channel_id}: {err_msg}"
            
    except Exception as e:
        logger.error(f"Ошибка при проверке {channel_id}: {e}")
        return f"❌ @{channel_id}: Ошибка соединения"

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие при команде /start"""
    await update.message.reply_text(
        "👋 Привет! Я готов проверять списки каналов на накрутку.\n\n"
        "Просто пришли мне юзернеймы через @ или ссылки на каналы (можно пачкой)."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка входящих сообщений со списками каналов"""
    text = update.message.text
    if not text: return

    # Извлекаем все юзернеймы из текста (минимум 5 символов)
    raw_channels = re.findall(r'(?:@|t\.me\/|https:\/\/t\.me\/)([a-zA-Z0-9_]{5,})', text)
    channels = list(dict.fromkeys(raw_channels)) # Убираем дубликаты, сохраняя порядок

    if not channels:
        await update.message.reply_text("В сообщении не найдено ссылок на каналы. Пришли список через @.")
        return

    status_msg = await update.message.reply_text(f"🔎 Начинаю проверку {len(channels)} каналов...")

    results = []
    for channel in channels:
        res = await check_telemetr(channel)
        results.append(res)
        
        # Обновляем сообщение каждые 3 канала для наглядности
        if len(results) % 3 == 0:
            await status_msg.edit_text(f"⏳ Проверено {len(results)} из {len(channels)}...\n\n" + "\n".join(results))
        
        # Пауза, чтобы не злить Telegram и API
        await asyncio.sleep(1.2)

    # ФИНАЛЬНОЕ ОБНОВЛЕНИЕ: выводим весь список целиком
    final_text = f"🏁 **Результаты проверки ({len(results)} из {len(channels)}):**\n\n" + "\n".join(results)
    
    # Защита от слишком длинных сообщений (лимит TG 4096 симв.)
    if len(final_text) > 4000:
        final_text = final_text[:3950] + "\n\n...список обрезан"

    await status_msg.edit_text(final_text, parse_mode='Markdown')

if __name__ == '__main__':
    # Сборка приложения
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Обработчики
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True)
