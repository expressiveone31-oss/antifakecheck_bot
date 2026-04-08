import os
import logging
import asyncio
import requests
import re
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# Настройка логирования для Railway
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка токенов
TOKEN = os.getenv("BOT_TOKEN")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

# Проверка токенов при старте (защита от краша)
if not TOKEN:
    print("ОШИБКА: BOT_TOKEN не найден в переменных окружения!")

def get_clean_id(channel_input):
    """Извлекает чистый юзернейм из ссылки или @тега"""
    clean = channel_input.split('/')[-1].replace('@', '').strip()
    return clean if clean else None

async def check_telemetr(channel_id):
    """Проверка через Telemetr"""
    url = "https://api.telemetr.me/channels/get"
    headers = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}
    try:
        r = requests.get(url, headers=headers, params={"channelId": channel_id}, timeout=10)
        if r.status_code == 200:
            data = r.json().get('response', {})
            # Проверяем явные метки фрода
            if data.get('is_fake') or data.get('restrictions'):
                return "FRAUD", f"🚩 @{channel_id}: ФРОД (Telemetr)"
            return "CLEAN", None
        return "ERROR", f"⚠️ @{channel_id}: Ошибка Telemetr"
    except Exception as e:
        return "ERROR", f"❌ @{channel_id}: Ошибка связи"

async def check_tgstat(channel_id):
    """Проверка через TGStat (Ловим red_label для Лови Тренд)"""
    url = "https://api.tgstat.ru/channels/get"
    try:
        r = requests.get(url, params={"token": TGSTAT_TOKEN, "channelId": channel_id}, timeout=10)
        if r.status_code == 200:
            res = r.json().get('response', {})
            # Прямая проверка красной метки
            if res.get('red_label') == 1 or res.get('is_scam') == 1:
                return f"🚩 @{channel_id}: ФРОД (TGStat: метка накрутки)"
            return f"✅ @{channel_id}: Чисто"
        return f"⚠️ @{channel_id}: Ошибка TGStat"
    except Exception as e:
        return f"❌ @{channel_id}: Ошибка связи"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    
    # Поиск юзернеймов в тексте
    potential = re.findall(r'(?:@|t\.me\/|https?:\/\/)?([a-zA-Z0-9_]{5,})', update.message.text)
    channels = list(set([get_clean_id(p) for p in potential if get_clean_id(p)]))

    if not channels:
        await update.message.reply_text("Каналы не найдены в сообщении.")
        return

    status_msg = await update.message.reply_text(f"🔎 Начинаю проверку {len(channels)} каналов...")
    results = []

    for idx, channel in enumerate(channels, 1):
        # 1. Сначала Telemetr
        state, report = await check_telemetr(channel)
        
        if state == "CLEAN":
            # 2. Если Telemetr ок, проверяем в TGStat
            final_report = await check_tgstat(channel)
            results.append(final_report)
        else:
            results.append(report)
        
        # Обновляем статус каждые 2 канала (чтобы не спамить в API Telegram)
        if idx % 2 == 0 or idx == len(channels):
            text = f"⏳ Прогресс: {idx}/{len(channels)}\n\n" + "\n".join(results)
            try:
                await status_msg.edit_text(text)
            except: pass
        
        await asyncio.sleep(1.5) # Пауза для стабильности

if __name__ == '__main__':
    if not TOKEN:
        exit(1)
        
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("Бот запущен и готов к работе...")
    # drop_pending_updates=True критически важен после простоев!
    app.run_polling(drop_pending_updates=True)
