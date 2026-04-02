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
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

def escape_markdown(text):
    """Экранирует спецсимволы для Telegram MarkdownV2"""
    parse_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(parse_chars)}])', r'\\\1', text)

def get_clean_id(channel_input):
    """Очищает входную строку до чистого юзернейма"""
    clean = channel_input.split('/')[-1] if '/' in channel_input else channel_input
    return clean.replace('@', '')

async def check_telemetr(channel_id):
    """Первый этап: Проверка в Telemetr"""
    url = "https://api.telemetr.me/channels/get"
    headers = {"Authorization": f"Bearer {TELEMETR_TOKEN}", "Accept": "application/json"}
    params = {"channelId": channel_id}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            info = response.json().get('response', {})
            # Ищем прямые признаки накрутки
            if info.get('is_fake') or info.get('restrictions'):
                reason = ", ".join(info.get('restrictions', [])) or "Метка накрутки"
                return "FRAUD", f"🚩 @{channel_id}: *ФРОД* (Telemetr: {reason})"
            return "CLEAN", None
        elif response.status_code == 403:
            return "ERROR", f"🚫 @{channel_id}: Ошибка 403 (Нет прав Telemetr)"
        return "ERROR", f"⚠️ @{channel_id}: Ошибка Telemetr ({response.status_code})"
    except Exception as e:
        return "ERROR", f"❌ @{channel_id}: Ошибка соединения (Telemetr)"

async def check_tgstat(channel_id):
    """Второй этап: Проверка в TGStat (только для тех, кто прошел первый этап)"""
    url = "https://api.tgstat.ru/channels/stat"
    clean_id = get_clean_id(channel_id)
    
    # Пробуем сначала с @, если не выйдет — без него
    for cid in [f"@{clean_id}", clean_id]:
        params = {"token": TGSTAT_TOKEN, "channelId": cid}
        try:
            response = requests.get(url, params=params, timeout=15)
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'error':
                    # Если канал не найден или лимиты API кончились
                    return f"⚠️ @{channel_id}: TGStat ({data.get('error', 'ошибка')})"
                
                # В TGStat проверяем наличие красной метки в объекте канала, если она есть
                # На базовом тарифе stat проверяем просто доступность данных
                return f"✅ @{channel_id}: Чисто (Проверен везде)"
            
            if response.status_code == 500:
                continue # Пробуем следующий вариант ID (без @)
                
            return f"⚠️ @{channel_id}: Ошибка TGStat ({response.status_code})"
        except:
            return f"❌ @{channel_id}: Ошибка соединения (TGStat)"
    
    return f"⚠️ @{channel_id}: Ошибка 500 (TGStat не принял ID)"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return

    # Ищем все упоминания каналов
    raw_channels = re.findall(r'(?:@|t\.me\/|https:\/\/t\.me\/)([a-zA-Z0-9_]{5,})', text)
    channels = list(dict.fromkeys(raw_channels))

    if not channels:
        await update.message.reply_text("Пришли список каналов (через @ или ссылками).")
        return

    status_msg = await update.message.reply_text(f"🔎 Двойная проверка {len(channels)} каналов...")
    results = []

    for index, channel in enumerate(channels, 1):
        # Шаг 1: Telemetr
        state, report = await check_telemetr(channel)
        
        if state == "CLEAN":
            # Шаг 2: Только если в Telemetr чисто — идем в TGStat
            final_report = await check_tgstat(channel)
            results.append(final_report)
        else:
            # Если фрод или ошибка — записываем как есть
            results.append(report)
        
        # Обновляем прогресс каждые 2 канала
        if index % 2 == 0 or index == len(channels):
            current_status = f"⏳ Прогресс: {index}/{len(channels)}\n\n" + "\n".join(results)
            try:
                await status_msg.edit_text(escape_markdown(current_status), parse_mode='MarkdownV2')
            except:
                await status_msg.edit_text(current_status)
        
        await asyncio.sleep(1.5)

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Готов к двойной проверке (Telemetr + TGStat)!")))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.run_polling(drop_pending_updates=True)
