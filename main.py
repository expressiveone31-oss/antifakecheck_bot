import os
import logging
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from openai import OpenAI

# 1. Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 2. Переменные окружения
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY)

def get_telemetr_data(channel_id):
    """Получение статистики канала через метод поиска по юзернейму"""
    # МЫ ЗАМЕНИЛИ ЭТУ СТРОКУ: теперь стучимся в правильную "дверь" API
    url = f"https://api.telemetr.me/v1/channels/by_username/{channel_id}/"
    headers = {"Authorization": f"Token {TELEMETR_TOKEN}"}
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        logger.info(f"Запрос к Telemetr для {channel_id}. Статус: {response.status_code}")
        
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            logger.warning(f"Канал {channel_id} не найден в базе Telemetr.")
            return {"error": "Channel not found"}
        else:
            # Логируем текст ошибки, чтобы видеть причину (как те кракозябры в 17:47)
            logger.error(f"Telemetr API error {response.status_code}: {response.text}")
            return None
    except Exception as e:
        logger.error(f"Ошибка при запросе к Telemetr: {e}")
        return None

async def ask_gpt_expert(data_payload):
    """Анализ данных через GPT-4o-mini"""
    if not data_payload or (isinstance(data_payload, dict) and "error" in data_payload):
        return "Недостаточно данных для точного вердикта. Канал может быть новым или отсутствовать в базе Telemetr."

    try:
        # Защита от перегрузки (обрезаем до 3800 символов)
        safe_prompt = str(data_payload)[:3800] 

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — эксперт по выявлению накруток в Telegram. Твоя задача: проанализировать цифры.\n\n"
                        "ПРАВИЛА:\n"
                        "1. ERR выше 10% — это ХОРОШО. 30%+ — ОТЛИЧНО. Не называй высокий охват накруткой.\n"
                        "2. Сравнивай числа верно: 33 > 5. Если охват высокий (например, 33%), это НЕ боты, а хороший контент.\n"
                        "3. Признаки ботов: ERR < 2%, резкие скачки подписчиков без внешних упоминаний.\n"
                        "4. Формат ответа: РЕЗЮМЕ, РИСКИ, ОБОСНОВАНИЕ (по цифрам), ОЦЕНКА (1-10)."
                    )
                },
                {"role": "user", "content": f"Проанализируй данные канала: {safe_prompt}"}
            ],
            temperature=0
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"GPT Error: {e}")
        return f"Ошибка нейросети: {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Пришли мне @username канала для проверки через Telemetr.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return

    # Очищаем юзернейм от ссылок и лишних знаков
    clean_id = text.replace("https://t.me/", "").replace("@", "").strip().split('/')[0].split('?')[0]
    
    status_msg = await update.message.reply_text(f"🧠 GPT анализирует @{clean_id}...")

    try:
        # 1. Получаем данные по юзернейму
        raw_payload = get_telemetr_data(clean_id)
        
        # 2. Передаем их в GPT
        verdict = await ask_gpt_expert(raw_payload)

        # 3. Отправляем финальный ответ
        await status_msg.edit_text(f"✅ Анализ завершен для @{clean_id}:\n\n{verdict}")

    except Exception as e:
        logger.error(f"Ошибка в handle_message: {e}")
        await status_msg.edit_text("❌ Произошла ошибка. Попробуй позже.")

if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    application.run_polling()
