import os
import logging
import asyncio
import requests
import re
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Токены из переменных окружения Railway
TOKEN = os.getenv("BOT_TOKEN")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

def get_clean_id(channel_input):
    """Извлекает чистый юзернейм и фильтрует мусор"""
    if not channel_input: return None
    # Убираем протоколы и домены
    clean = re.sub(r'https?:\/\/(?:t\.me|tgstat\.ru|shumim\.me|shumim_media)\/', '', channel_input)
    # Оставляем только хвост ссылки и убираем @
    clean = clean.replace('@', '').strip().split('/')[0]
    
    # Список слов-исключений, которые не являются каналами
    stop_words = ['https', 'http', 't.me', 'start', 'help', 'bot', 'channel']
    if clean.lower() in stop_words or len(clean) < 4:
        return None
    return clean

async def check_telemetr(channel_id):
    """Проверка через API Telemetr"""
    url = "https://api.telemetr.me/channels/get"
    headers = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}
    try:
        r = requests.get(url, headers=headers, params={"channelId": channel_id}, timeout=10)
        if r.status_code == 200:
            data = r.json().get('response', {})
            # Проверяем метки фейка или ограничений
            if data.get('is_fake') or data.get('restrictions'):
                return "FRAUD", f"🚩 @{channel_id}: ФРОД (Telemetr)"
            return "CLEAN", None
        return "ERROR", f"⚠️ @{channel_id}: Telemetr Error"
    except:
        return "ERROR", f"❌ @{channel_id}: Ошибка связи"

async def check_tgstat(channel_id):
    """Проверка через API TGStat с учетом новых данных от поддержки"""
    url = "https://api.tgstat.ru/channels/get"
    params = {"token": TGSTAT_TOKEN, "channelId": channel_id}
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            ch_info = data.get('response', {})
            
            # Новый объект, где теперь лежат метки (согласно ответу поддержки)
            restrictions = ch_info.get('tgstat_restrictions', {})
            
            if restrictions.get('red_label') is True:
                return f"🚩 @{channel_id}: ФРОД (TGStat: красная метка)"
            
            if restrictions.get('black_label') is True:
                return f"🚩 @{channel_id}: МОШЕННИЧЕСТВО (TGStat: черная метка)"
            
            # Запасная проверка старых полей
            if ch_info.get('red_label') == 1 or ch_info.get('is_scam') == 1:
                return f"🚩 @{channel_id}: ФРОД (TGStat: старая метка)"

            return f"✅ @{channel_id}: Чисто"
        return f"⚠️ @{channel_id}: TGStat Error"
    except:
        return f"❌ @{channel_id}: Ошибка связи"

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие по команде /start"""
    await update.message.reply_text(
        "Бот готов к работе! 🛡\nПришли ссылки на каналы или список юзернеймов через @. "
        "Я проверю их по базам Telemetr и TGStat на наличие меток накрутки."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка входящих сообщений с каналами"""
    if not update.message.text or update.message.text.startswith('/'): return
    
    # Поиск всех потенциальных юзернеймов/ссылок
    raw_found = re.findall(r'(?:@|t\.me\/|https?:\/\/)?([a-zA-Z0-9_]{4,})', update.message.text)
    
    channels = []
    for p in raw_found:
        cid = get_clean_id(p)
        if cid and cid not in channels:
            channels.append(cid)

    if not channels:
        await update.message.reply_text("Не удалось распознать названия каналов. Попробуй прислать ссылки.")
        return

    status_msg = await update.message.reply_text(f"🔎 Запускаю проверку ({len(channels)})...")
    results = []

    for c in channels:
        # Сначала проверяем в Telemetr
        state, report = await check_telemetr(c)
        
        if state == "CLEAN":
            # Если чисто, идем в TGStat за финальным вердиктом
            final_report = await check_tgstat(c)
            results.append(final_report)
        else:
            results.append(report)
        
        # Обновляем сообщение в реальном времени
        progress_text = f"⏳ Прогресс: {len(results)}/{len(channels)}\n\n" + "\n".join(results)
        try:
            await status_msg.edit_text(progress_text)
        except: pass
        
        # Небольшая пауза, чтобы не злить API
        await asyncio.sleep(1.2)

if __name__ == '__main__':
    # Проверяем наличие токена перед стартом
    if not TOKEN:
        logger.error("BOT_TOKEN не найден!")
        exit(1)
        
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    logger.info("Бот успешно запущен!")
    # drop_pending_updates=True очищает очередь старых сообщений
    app.run_polling(drop_pending_updates=True)
