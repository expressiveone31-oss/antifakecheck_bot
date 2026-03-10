import os
import logging
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

# Настройка логирования для Railway
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загрузка ключей
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return
    
    # Жесткая очистка ID: убираем @, ссылки, пробелы
    clean_id = text.strip().replace("@", "").split('/')[-1]
    status_msg = await update.message.reply_text(f"📡 Анализ {clean_id} через Telemetr.me...")

    url = "https://api.telemetr.me/channels/stat"
    headers = {
        "Authorization": f"Bearer {TELEMETR_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    try:
        # Попытка №1: Стандартный GET (как в документации)
        response = requests.get(url, headers=headers, params={"id": clean_id}, timeout=15)
        
        # Попытка №2: Если 400, пробуем POST с JSON-телом
        if response.status_code == 400:
            logger.info("GET 400, пробуем POST...")
            response = requests.post(url, headers=headers, json={"id": clean_id}, timeout=15)

        logger.info(f"Telemetr Status: {response.status_code} | Body: {response.text}")

        if response.status_code != 200:
            # Выводим подробности ошибки прямо в чат
            error_msg = response.text[:200]
            await status_msg.edit_text(f"❌ Ошибка Telemetr {response.status_code}\nОтвет сервера: `{error_msg}`", parse_mode='Markdown')
            return

        data = response.json()
        info = data.get('data', data)
        
        subs = info.get('subscribers_count', 0)
        err = info.get('err', 0)

        if subs == 0:
            await status_msg.edit_text(f"⚠️ Канал {clean_id} не найден в базе Telemetr.")
            return

        # Вердикт через GPT-4o-mini
        prompt = (
            f"Ты антифрод-эксперт. Канал @{clean_id}.\n"
            f"Статистика: {subs} подписчиков, ERR {err}%.\n"
            "Вынеси краткий вердикт: ЧИСТ (высокий ERR при малых сабах) или НАКРУЧЕН (ERR < 1% или странные цифры)."
        )

        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        
        await status_msg.edit_text(f"🏁 **Результат для @{clean_id}:**\n\n{res.choices[0].message.content}")

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        await status_msg.edit_text(f"📛 Ошибка в коде: {str(e)[:100]}")

if __name__ == '__main__':
    if not TOKEN:
        logger.error("BOT_TOKEN не найден!")
    else:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        logger.info("Бот запущен. Ожидание сообщений...")
        app.run_polling(drop_pending_updates=True) # Очищает очередь старых сообщений
