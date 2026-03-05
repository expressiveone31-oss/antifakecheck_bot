import os
import logging
import requests
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

# Настройка логирования для Railway
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные окружения
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

# Инициализация OpenAI с защитой от пустых переменных
if not OPENAI_API_KEY:
    logger.error("КРИТИЧЕСКАЯ ОШИБКА: OPENAI_API_KEY не найден!")
    client = None
else:
    client = OpenAI(api_key=OPENAI_API_KEY)

def get_telemetr_data(identifier):
    """
    Версия согласно последним данным поддержки:
    Endpoint: /channels/get?channelId=
    """
    url = "https://api.telemetr.me/channels/get"
    
    # Отправляем токен всеми возможными способами одновременно, 
    # чтобы исключить проблему авторизации (403)
    headers = {
        "X-Api-Token": TELEMETR_TOKEN,
        "Authorization": f"Token {TELEMETR_TOKEN}",
        "Api-Token": TELEMETR_TOKEN
    }
    
    # Поддержка указала использовать параметр channelId
    params = {"channelId": identifier}
    
    try:
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
    if not text or len(text) > 150: return

    # Очищаем ввод: извлекаем username или ID для параметра channelId
    clean_id = text.strip().replace("https://t.me/", "").replace("@", "").split('/')[0]

    status_msg = await update.message.reply_text(f"📡 Попытка №3: Запрос данных для {clean_id}...")

    # Получаем данные
    raw_data = get_telemetr_data(clean_id)

    # Если всё еще 403 или другая ошибка
    if str(raw_data).startswith("Error_"):
        await status_msg.edit_text(
            f"❌ Ошибка API: {raw_data}.\n\n"
            "Если это 403, значит поддержка дала неверный эндпоинт для твоего тарифа."
        )
        return
    elif not raw_data:
        await status_msg.edit_text("❓ Пустой ответ от сервера.")
        return

    # Анализ через GPT
    if client:
        try:
            analysis_prompt = (
                "Ты эксперт по Telegram. Проанализируй данные канала на предмет накрутки. "
                "Оцени ERR, охваты и динамику. Сделай краткий и четкий вердикт.\n\n"
                f"ДАННЫЕ: {json.dumps(raw_data, ensure_ascii=False)[:3500]}"
            )
            
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": analysis_prompt}],
                temperature=0.2
            )
            await status_msg.edit_text(f"✅ **Результат анализа @{clean_id}:**\n\n{completion.choices[0].message.content}")
        except Exception as e:
            logger.error(f"GPT Error: {e}")
            await status_msg.edit_text("❌ Данные получены, но нейросеть не смогла их обработать.")
    else:
        await status_msg.edit_text(f"📊 Ответ API получен, но GPT не настроен:\n`{raw_data}`")

if __name__ == '__main__':
    if not TOKEN:
        print("ОШИБКА: BOT_TOKEN не найден!")
    else:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        print("Бот запущен...")
        app.run_polling()
