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

# Загрузка переменных окружения из Railway
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

# Инициализация OpenAI с проверкой ключа
if not OPENAI_API_KEY:
    logger.error("ОШИБКА: OPENAI_API_KEY не установлен в переменных Railway!")
    client = None
else:
    client = OpenAI(api_key=OPENAI_API_KEY)

def get_telemetr_data(channel_link):
    """
    Используем новый эндпоинт от поддержки: https://api.telemetr.me/channels/get
    """
    url = "https://api.telemetr.me/channels/get"
    
    # Поддержка часто требует X-Api-Token или стандартный Authorization
    headers = {
        "X-Api-Token": TELEMETR_TOKEN,
        "Content-Type": "application/json"
    }
    
    # Отправляем запрос согласно новым вводным
    payload = {"link": channel_link}
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        
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

    # Очищаем ввод: если это не ссылка, превращаем в ссылку для API
    clean_input = text.strip()
    if not clean_input.startswith("http"):
        # Убираем собачку, если она есть
        handle = clean_input.replace("@", "")
        clean_input = f"https://t.me/{handle}"

    status_msg = await update.message.reply_text(f"📡 Запрашиваю данные для {clean_input}...")

    # Получаем данные от Telemetr
    raw_data = get_telemetr_data(clean_input)

    if raw_data == "Error_404":
        await status_msg.edit_text("❌ Эндпоинт не найден. Пожалуйста, проверь правильность API-ключа.")
        return
    elif raw_data == "Error_500":
        await status_msg.edit_text("⚠️ Сервер Telemetr выдал 500. Похоже, их новый эндпоинт тоже нестабилен.")
        return
    elif not raw_data:
        await status_msg.edit_text("❓ Не удалось получить данные от Telemetr.")
        return

    # Если данные пришли, отправляем их в GPT
    if client:
        try:
            analysis_prompt = (
                "Ты профессиональный аналитик Telegram-каналов. Проверь данные на признаки накрутки: "
                "аномальный рост, низкий ERR, подозрительные охваты. Сделай краткий и жесткий вердикт.\n\n"
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
