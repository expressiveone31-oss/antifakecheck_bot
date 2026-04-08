import os
import logging
import asyncio
import requests
import re
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

def get_clean_id(channel_input):
    """Строгая очистка юзернейма"""
    if not channel_input: return None
    # Убираем протоколы и домены
    clean = re.sub(r'https?:\/\/(?:t\.me|tgstat\.ru|shumim\.me)\/', '', channel_input)
    clean = clean.replace('@', '').strip().split('/')[0]
    
    # Игнорируем технический мусор
    if clean.lower() in ['https', 'http', 't.me', 'start', 'help', '']:
        return None
    return clean

async def check_telemetr(channel_id):
    url = "https://api.telemetr.me/channels/get"
    headers = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}
    try:
        r = requests.get(url, headers=headers, params={"channelId": channel_id}, timeout=10)
        if r.status_code == 200:
            data = r.json().get('response', {})
            if data.get('is_fake') or data.get('restrictions'):
                return "FRAUD", f"🚩 @{channel_id}: ФРОД (Telemetr)"
            return "CLEAN", None
        return "ERROR", f"⚠️ @{channel_id}: Telemetr Error"
    except:
        return "ERROR", f"❌ @{channel_id}: Ошибка связи"

async def check_tgstat(channel_id):
    """Глубокая проверка TGStat на фрод"""
    url = "https://api.tgstat.ru/channels/get"
    try:
        r = requests.get(url, params={"token": TGSTAT_TOKEN, "channelId": channel_id}, timeout=10)
        if r.status_code == 200:
            res = r.json().get('response', {})
            
            # Проверяем ВСЕ возможные признаки накрутки
            is_scam = res.get('is_scam')
            red_label = res.get('red_label')
            
            if red_label == 1 or is_scam == 1:
                return f"🚩 @{channel_id}: ФРОД (TGStat)"
            
            return f"✅ @{channel_id}: Чисто"
        return f"⚠️ @{channel_id}: TGStat Error"
    except:
        return f"❌ @{channel_id}: Ошибка связи"

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветственное сообщение"""
    await update.message.reply_text(
        "Привет! Кидай список каналов (ссылками или через @), и я проверю их на накрутку через Telemetr и TGStat."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text or update.message.text.startswith('/'): return
    
    logger.info(f"Проверка списка от пользователя")
    
    # Ищем юзернеймы, исключая протоколы
    raw_found = re.findall(r'(?:@|t\.me\/|https?:\/\/)?([a-zA-Z0-9_]{5,})', update.message.text)
    
    channels = []
    for p in raw_found:
        cid = get_clean_id(p)
        if cid and cid not in channels:
            channels.append(cid)

    if not channels:
        await update.message.reply_text("Не нашел каналов для проверки.")
        return

    status_msg = await update.message.reply_text(f"🔎 Проверяю {len(channels)} каналов...")
    results = []

    for c in channels:
        state, report = await check_telemetr(c)
        if state == "CLEAN":
            results.append(await check_tgstat(c))
        else:
            results.append(report)
        
        # Обновляем сообщение (простой текст без Markdown)
        progress = f"⏳ Прогресс: {len(results)}/{len(channels)}\n\n" + "\n".join(results)
        try:
            await status_msg.edit_text(progress)
        except: pass
        await asyncio.sleep(1.2)

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Сначала обработчик команды /start
    app.add_handler(CommandHandler("start", start_command))
    # Затем обработчик текста (кроме команд)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    logger.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)
