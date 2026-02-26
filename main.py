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

# 2. Переменные окружения (подтягиваются из Railway)
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY)

def get_telemetr_data(channel_id):
    """Получение статистики канала напрямую из Telemetr API"""
    url = f"https://api.telemetr.me/v1/channels/stat-full/{channel_id}/"
    headers = {"Authorization": f"Token {TELEMETR_TOKEN}"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"Telemetr API error: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Telemetr request failed: {e}")
        return None

async def ask_gpt_expert(data_payload):
    """Отправка очищенных данных в GPT для анализа"""
    if not data_payload:
        return "Недостаточно данных для точного вердикта."

    try:
        # Обрезаем лишнее, чтобы бот не падал на больших каналах (Мэш и др.)
        safe_prompt = str(data_payload)[:3500] 

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — топовый аналитик Telegram-каналов. Твоя задача: отличить живой канал от накрученного ботами.\n\n"
                        "ПРАВИЛА АНАЛИЗА:\n"
                        "1. ERR (охват) 10–30% — это НОРМА. ERR выше 30% — это ОТЛИЧНО. Никогда не называй высокий ERR накруткой.\n"
                        "2. Считай математически верно: 33% — это БОЛЬШЕ, чем 5%. Если охват высокий, это признак качества.\n"
                        "3. Признаки накрутки: резкие скачки подписчиков без упоминаний в других каналах, ERR ниже 2%.\n"
                        "4. Если данных в отчете мало, пиши: 'Недостаточно данных для точного вердикта'.\n"
                        "5. Формат ответа: РЕЗЮМЕ (1 предл.), РИСКИ (есть/нет), ОБОСНОВАНИЕ (цифры), ОЦЕНКА (от 1 до 10)."
                    )
                },
                {"role": "user", "content": safe_prompt}
            ],
            temperature=0
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"GPT Error: {e}")
        return f"Ошибка нейросети: {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    await update.message.reply_text("Пришли мне @username канала, и я проверю его статистику через Telemetr + GPT.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ссылки на канал"""
    text = update.message.text
    if not text: return

    # Очищаем юзернейм
    cid = text.replace("https://t.me/", "").replace("@", "").strip().split('/')[0]
    
    status_msg = await update.message.reply_text(f"🧠 GPT-аналитик проверяет @{cid}...")

    try:
        # Получаем реальные данные
        raw_payload = get_telemetr_data(cid)
        
        # Анализируем через GPT
        verdict = await ask_gpt_expert(raw_payload)

        # Отправляем финальный текст БЕЗ parse_mode (чтобы не было ошибок разметки)
        await status_msg.edit_text(f"✅ Анализ завершен для @{cid}:\n\n{verdict}")

    except Exception as e:
        logger.error(f"Handle error: {e}")
        await status_msg.edit_text("❌ Произошла ошибка. Проверь токен Telemetr или доступность API.")

if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    application.run_polling()
