import os
import logging
import requests
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные из Railway
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

# Инициализация OpenAI
if not OPENAI_API_KEY:
    logger.error("КРИТИЧЕСКАЯ ОШИБКА: OPENAI_API_KEY не найден!")
    client = None
else:
    client = OpenAI(api_key=OPENAI_API_KEY)

def get_tgstat_data(channel_id):
    """
    Используем метод channels/stat для получения сводной статистики.
    """
    url = "https://api.tgstat.ru/channels/stat"
    
    # В TGStat токен можно передавать прямо в параметрах запроса
    params = {
        "token": TGSTAT_TOKEN,
        "channelId": channel_id
    }
    
    try:
        response = requests.get(url, params=params, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "ok":
                return data.get("response")
            else:
                logger.error(f"TGStat API Error: {data.get('error_query')}")
                return f"Error_{data.get('error_query')}"
        else:
            logger.error(f"HTTP Error {response.status_code}")
            return f"HTTP_Error_{response.status_code}"
    except Exception as e:
        logger.error(f"Request failed: {e}")
        return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text or len(text) > 150: return

    # Очищаем ввод для TGStat (принимает @username или ссылки)
    clean_id = text.strip()

    status_msg = await update.message.reply_text(f"📊 TGStat: Анализирую канал {clean_id}...")

    # Получаем данные от TGStat
    raw_data = get_tgstat_data(clean_id)

    # Обработка ошибок
    if str(raw_data).startswith("Error_") or str(raw_data).startswith("HTTP_"):
        await status_msg.edit_text(
            f"❌ Ошибка TGStat: {raw_data}.\n"
            "На Free-тарифе убедитесь, что это ваш канал или лимит не исчерпан."
        )
        return
    elif not raw_data:
        await status_msg.edit_text("❓ Не удалось получить данные от TGStat.")
        return

    # Анализ через GPT
    if client:
        try:
            # Подготавливаем данные для GPT (выбираем самое важное из ответа TGStat)
            analysis_prompt = (
                "Ты эксперт по аналитике Telegram. Проверь данные на признаки накрутки: "
                "посмотри на ERR, индекс цитируемости и средний охват. "
                "Сделай краткий и обоснованный вывод.\n\n"
                f"ДАННЫЕ ОТ TGSTAT: {json.dumps(raw_data, ensure_ascii=False)[:3500]}"
            )
            
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": analysis_prompt}],
                temperature=0.2
            )
            await status_msg.edit_text(f"✅ **Анализ TGStat для {clean_id}:**\n\n{completion.choices[0].message.content}")
        except Exception as e:
            logger.error(f"GPT Error: {e}")
            await status_msg.edit_text("❌ Данные получены, но OpenAI не смог их обработать.")
    else:
        await status_msg.edit_text(f"📊 Ответ API получен:\n`{raw_data}`")

if __name__ == '__main__':
    if not TOKEN:
        print("ОШИБКА: BOT_TOKEN не найден!")
    else:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        print("Бот запущен на базе TGStat...")
        app.run_polling()
