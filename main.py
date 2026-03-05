import os
import logging
import requests
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные из Railway
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

if not OPENAI_API_KEY:
    logger.error("OPENAI_API_KEY не установлен!")
    client = None
else:
    client = OpenAI(api_key=OPENAI_API_KEY)

def get_telemetr_data(identifier):
    """
    Версия согласно последним данным поддержки:
    Endpoint: /channels/get?channelId=
    """
    url = "https://api.telemetr.me/channels/get"
    
    # Поддержка подтвердила использование этого эндпоинта
    headers = {
        "X-Api-Token": TELEMETR_TOKEN
    }
    
    # Теперь используем channelId вместо link
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

    # Очищаем ввод: для параметра channelId подходит просто username или ID
    clean_id = text.strip().replace("https://t.me/", "").replace("@", "").split('/')[0]

    status_msg = await update.message.reply_text(f"📡 Запрашиваю данные для {clean_id}...")

    raw_data = get_telemetr_data(clean_id)

    if str(raw_data).startswith("Error_"):
        await status_msg.edit_text(f"❌ Ошибка API: {raw_data}. Проверь токен или доступ к методу.")
        return
    elif not raw_data:
        await status_msg.edit_text("❓ Не удалось получить данные (пустой ответ).")
        return

    # Если данные пришли, анализируем их через GPT
    if client:
        try:
            analysis_prompt = (
                "Ты аналитик Telegram. Проверь данные на признаки накрутки (ERR, охваты, рост). "
                "Сделай краткий и профессиональный вердикт.\n\n"
                f"ДАННЫЕ: {json.dumps(raw_data, ensure_ascii=False)[:3500]}"
            )
            
            completion = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": analysis_prompt}],
                temperature=0.2
            )
            await status_msg.edit_text(f"✅ **Вердикт для @{clean_id}:**\n\n{completion.choices[0].message.content}")
        except Exception as e:
            logger.error(f"GPT Error: {e}")
            await status_msg.edit_text("❌ Ошибка нейросети при анализе.")
    else:
        await status_msg.edit_text(f"📊 Данные получены:\n`{raw_data}`")

if __name__ == '__main__':
    if not TOKEN:
        print("BOT_TOKEN не найден!")
    else:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        app.run_polling()
