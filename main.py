import os
import logging
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return
    
    # Максимально чистая обработка: только юзернейм
    clean_id = text.strip().replace("@", "").split('/')[-1]
    status_msg = await update.message.reply_text(f"📡 Запрос к Telemetr для {clean_id}...")

    # Используем только GET, как того требует сервер
    url = "https://api.telemetr.me/channels/stat"
    
    headers = {
        "Authorization": f"Bearer {TELEMETR_TOKEN}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        # Пробуем передать параметр прямо в URL, иногда это критично для API
        final_url = f"{url}?id={clean_id}"
        response = requests.get(final_url, headers=headers, timeout=15)
        
        logger.info(f"Telemetr Status: {response.status_code} | Body: {response.text}")

        if response.status_code != 200:
            error_raw = response.text
            await status_msg.edit_text(f"❌ Ошибка {response.status_code}\nОтвет: `{error_raw}`")
            return

        data = response.json()
        # Извлекаем данные (у Telemetr они обычно в response или data)
        info = data.get('response', data.get('data', data))
        
        subs = info.get('participants_count', info.get('subscribers_count', 0))
        err = info.get('err', 0)

        if not subs and not err:
            await status_msg.edit_text(f"⚠️ Данные по {clean_id} не найдены.")
            return

        prompt = (
            f"Ты антифрод-эксперт. Анализ канала @{clean_id}.\n"
            f"Метрики: {subs} сабов, ERR {err}%.\n"
            "Дай краткий вердикт: ЧИСТ или НАКРУЧЕН. Объясни одной короткой фразой."
        )

        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        
        await status_msg.edit_text(f"🏁 **Результат:**\n\n{res.choices[0].message.content}")

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        await status_msg.edit_text(f"📛 Сбой: {str(e)[:100]}")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    # Очищаем очередь, чтобы не обрабатывать старые ошибки
    app.run_polling(drop_pending_updates=True)
