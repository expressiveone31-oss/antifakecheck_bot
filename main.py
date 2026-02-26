import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from openai import OpenAI

# Настройка логирования
logging.basicConfig(
    format='%(asctime) - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения (в Railway они подтянутся сами)
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY)

async def ask_gpt_expert(prompt):
    """Отправка данных в GPT-4o-mini для анализа"""
    try:
        # Обрезаем входные данные, чтобы не "уронить" бота на больших каналах
        safe_prompt = str(prompt)[:3500] 

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
                        "3. Признаки накрутки: резкие скачки подписчиков без упоминаний, ERR ниже 2%.\n"
                        "4. Если данных мало, пиши: 'Недостаточно данных для точного вердикта'.\n"
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
        return f"Ошибка при обращении к нейросети: {str(e)}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start"""
    await update.message.reply_text("Привет! Пришли мне ссылку на канал (например, @username), и я проверю его на накрутки.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Основная логика обработки сообщений"""
    text = update.message.text
    if not text:
        return

    # Извлекаем username/ID канала
    cid = text.replace("https://t.me/", "").replace("@", "").strip()
    
    status_msg = await update.message.reply_text(f"🧠 GPT-аналитик проверяет @{cid} на вшивость...")

    try:
        # Здесь должна быть твоя функция получения данных из Telemetr
        # Для примера представим, что мы получили raw_payload
        # raw_payload = get_telemetr_data(cid) 
        
        # ВАЖНО: Убедись, что твоя функция get_telemetr_data определена выше или импортирована!
        # Если ее нет, бот выдаст ошибку.
        
        # Передаем данные в GPT
        # verdict = await ask_gpt_expert(raw_payload)
        
        # ВРЕМЕННАЯ ЗАГЛУШКА (замени на реальный вызов Telemetr, когда будешь готова)
        verdict = await ask_gpt_expert(f"Данные для канала {cid} из Telemetr...")

        # Отправляем финальный ответ (без parse_mode во избежание ошибок разметки)
        await status_msg.edit_text(f"✅ Анализ завершен для @{cid}:\n\n{verdict}")

    except Exception as e:
        logger.error(f"Handle Message Error: {e}")
        await status_msg.edit_text("❌ Ошибка при запросе к API. Проверь правильность ссылки или токены.")

if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).build()
    
    start_handler = CommandHandler('start', start)
    msg_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    
    application.add_handler(start_handler)
    application.add_handler(msg_handler)
    
    application.run_polling()
