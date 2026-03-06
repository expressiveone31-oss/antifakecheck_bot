import os
import logging
import requests
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

# Настройка логирования — теперь мы будем видеть ВСЁ в логах Railway
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

if not OPENAI_API_KEY:
    logger.error("КРИТИЧЕСКАЯ ОШИБКА: OPENAI_API_KEY не найден!")
    client = None
else:
    client = OpenAI(api_key=OPENAI_API_KEY)

def get_tgstat_data(channel_id):
    url = "https://api.tgstat.ru/channels/stat"
    params = {
        "token": TGSTAT_TOKEN,
        "channelId": channel_id
    }
    
    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "ok":
                # ВАЖНО: Печатаем сырые данные в логи для ручной калибровки
                logger.info(f"RAW DATA FOR {channel_id}: {json.dumps(data.get('response'), ensure_ascii=False)}")
                return data.get("response")
            else:
                logger.error(f"TGStat API Error: {data.get('error_query')}")
                return f"Error_{data.get('error_query')}"
        return f"HTTP_Error_{response.status_code}"
    except Exception as e:
        logger.error(f"Request failed: {e}")
        return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text or len(text) > 150: return

    clean_id = text.strip()
    status_msg = await update.message.reply_text(f"🧪 Калибровка: Анализирую {clean_id}...")

    raw_data = get_tgstat_data(clean_id)

    if str(raw_data).startswith("Error_") or str(raw_data).startswith("HTTP_"):
        await status_msg.edit_text(f"❌ Ошибка TGStat: {raw_data}")
        return
    elif not raw_data:
        await status_msg.edit_text("❓ Канал не найден или недоступен на Free-тарифе.")
        return

    if client:
        try:
            # Улучшенный промпт с новыми весами
            analysis_prompt = (
                "Ты — элитный аналитик по борьбе с фейками и ботами в Telegram. "
                "Твоя задача: отличить реальную популярность от накрутки.\n\n"
                "ЭТАЛОННЫЕ ВЕСА ДЛЯ КАЛИБРОВКИ:\n"
                "1. Масштаб: Для каналов > 100k сабов ERR 2-5% — это НОРМАЛЬНО. Не называй это накруткой.\n"
                "2. Цитируемость (CI): Высокий CI у крупных каналов — это признак СМИ, а не ботов. "
                "Подозрительно только если CI > 5000, а средний охват поста меньше 500.\n"
                "3. Вовлеченность: ERR ниже 1% — критический сигнал накрутки при любом масштабе.\n"
                "4. Охват: Если охват поста < 2% от аудитории — это 'мертвые души' или боты.\n\n"
                f"ДАННЫЕ: {json.dumps(raw_data, ensure_ascii=False)}\n\n"
                "Сделай краткий вердикт: Накручен / Чист / Подозрителен. Обоснуй цифрами."
            )
            
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": analysis_prompt}],
                temperature=0.1 # Уменьшаем креативность для точности цифр
            )
            await status_msg.edit_text(f"✅ **Результат калибровки для {clean_id}:**\n\n{completion.choices[0].message.content}")
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка нейросети: {e}")
    else:
        await status_msg.edit_text(f"📊 Raw Data получены (см. логи Railway)")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    print("Бот запущен (версия: Калибровка)...")
    app.run_polling()
