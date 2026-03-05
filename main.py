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

# Загрузка переменных окружения
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

# Инициализация OpenAI с защитой от пустых переменных
if not OPENAI_API_KEY:
    logger.error("ОШИБКА: OPENAI_API_KEY не установлен!")
    client = None
else:
    client = OpenAI(api_key=OPENAI_API_KEY)

def get_telemetr_data(channel_link):
    """
    Финальная версия под новый эндпоинт и метод GET (лечит ошибку 405)
    """
    url = "https://api.telemetr.me/channels/get"
    
    # Используем X-Api-Token, который обычно требуется для этого адреса
    headers = {
        "X-Api-Token": TELEMETR_TOKEN
    }
    
    # Передаем ссылку параметром в URL
    params = {"link": channel_link}
    
    try:
        # Теперь используем .get() вместо .post()
        response = requests.get(url, headers=headers, params=params, timeout=15)
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Telemetr Error {response.status_code}: {response.text}")
            return f"Error_{response.status_code}"
    except Exception as e:
        logger.error(f"Request failed: {e}")
        return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text or len(text) > 150: 
        return

    # Превращаем ввод в полную ссылку, как просит новый эндпоинт
    clean_input = text.strip()
    if not clean_input.startswith("http"):
        handle = clean_input.replace("@", "")
        clean_input = f"https://t.me/{handle}"

    status_msg = await update.message.reply_text(f"📡 Запрашиваю данные (GET) для {clean_input}...")

    # Получаем данные
    raw_data = get_telemetr_data(clean_input)

    # Обработка ошибок сервера
    if str(raw_data).startswith("Error_"):
        await status_msg.edit_text(f"❌ Сервер Telemetr ответил ошибкой: {raw_data}")
        return
    elif not raw_data:
        await status_msg.edit_text("❓ Не удалось получить данные от Telemetr (пустой ответ).")
        return

    # Анализ через GPT
    if client:
        try:
            analysis_prompt = (
                "Ты аналитик Telegram-каналов. Проверь данные на признаки накрутки: "
                "аномальный рост, низкий ERR, подозрительные охваты. Сделай краткий вердикт.\n\n"
                f"ДАННЫЕ: {json.dumps(raw_data, ensure_ascii=False)[:3500]}"
            )
            
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": analysis_prompt}],
                temperature=0.2
            )
            await status_msg.edit_text(f"✅ **Анализ завершен:**\n\n{completion.choices[0].message.content}")
        except Exception as e:
            logger.error(f"GPT Error: {e}")
            await status_msg.edit_text("❌ Ошибка при генерации анализа нейросетью.")
    else:
        await status_msg.edit_text(f"📊 Данные получены, но OpenAI не настроен.\nСырой ответ: `{raw_data}`")

if __name__ == '__main__':
    if not TOKEN:
        print("Критическая ошибка: BOT_TOKEN не найден!")
    else:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        print("Бот запущен...")
        app.run_polling()
