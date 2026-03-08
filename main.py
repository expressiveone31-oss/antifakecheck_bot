import os
import logging
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from openai import OpenAI

# Настройка логирования для отслеживания ошибок в Railway
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация ключей из переменных окружения
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TGSTAT_TOKEN = os.getenv("TGSTAT_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text: return
    
    # Очистка ID канала (убираем @ и лишние ссылки)
    clean_id = text.strip().replace("@", "").split('/')[-1]
    status_msg = await update.message.reply_text(f"📡 Запрос данных для @{clean_id}...")

    try:
        # 1. Получаем общую статистику канала через проверенный метод
        url = f"https://api.tgstat.ru/channels/stat?token={TGSTAT_TOKEN}&channelId={clean_id}"
        response = requests.get(url, timeout=15)
        
        # Проверка, что API вообще что-то ответило
        if response.status_code != 200:
            await status_msg.edit_text(f"❌ Ошибка сервера API (Код: {response.status_code})")
            return

        data = response.json()

        if data.get('status') != 'ok':
            error_text = data.get('error', 'Канал не найден или скрыт')
            await status_msg.edit_text(f"❌ TGStat ответил ошибкой: {error_text}")
            return

        # Извлекаем основные метрики
        res = data.get('response', {})
        subs = res.get('participants_count', 0)
        err = res.get('err', 0)
        ci = res.get('ci_index', 0) # Индекс цитируемости
        red_label = res.get('red_label', False)
        
        # 2. Формируем инструкцию для нейросети
        # Мы явно задаем логику «Авторского канала», чтобы спасти @taknaglo
        prompt = (
            f"Ты — эксперт по аудиту Telegram-каналов. Проанализируй данные @{clean_id}:\n"
            f"- Подписчиков: {subs}\n"
            f"- ERR (Вовлеченность): {err}%\n"
            f"- CI (Индекс цитируемости): {ci}\n"
            f"- Метка red_label: {red_label}\n\n"
            "ТВОИ ПРАВИЛА ВЕРДИКТА:\n"
            "1. Если подписчиков < 50к и ERR > 20% — это АВТОРСКИЙ КАНАЛ (ЧИСТ). Высокие цифры здесь — признак лояльности.\n"
            "2. Если red_label = True — это НАКРУТКА (без вариантов).\n"
            "3. Если CI (цитируемость) крайне низкий при высоком охвате — это ПОДОЗРИТЕЛЬНО.\n"
            "4. Сравнивай с эталоном: @taknaglo — чист (авторский хаос), @shumim_media — накручен (стерильность).\n\n"
            "Напиши краткий вердикт: ЧИСТ или НАКРУЧЕН, и обоснуй в 2-3 предложениях."
        )

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        
        await status_msg.edit_text(f"🏁 **Результат для @{clean_id}:**\n\n{completion.choices[0].message.content}")

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        await status_msg.edit_text("📛 Ошибка соединения. Попробуй еще раз через минуту.")

if __name__ == '__main__':
    # Проверка наличия токена перед запуском
    if not TOKEN:
        logger.error("BOT_TOKEN не найден в переменных окружения!")
    else:
        app = ApplicationBuilder().token(TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
        app.run_polling()
