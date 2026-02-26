import os, re, logging, requests, time, openai
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токены (подтягиваются из Railway)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEMETR_TOKEN = os.environ.get("TELEMETR_TOKEN", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()

BASE_URL = "https://api.telemetr.me"

# Инициализируем клиента OpenAI
client = openai.OpenAI(api_key=OPENAI_API_KEY)

async def ask_gpt_expert(raw_payload):
    """
    Тот самый 'мозг', который анализирует данные за копейки.
    """
    st = raw_payload["stats"]
    views = raw_payload["views"]
    
    # Формируем инструкцию для GPT
    prompt = f"""
    Проанализируй данные Telegram канала и вынеси вердикт о накрутке.
    ДАННЫЕ:
    - Подписчики: {st.get('participants_count')}
    - Средний охват: {st.get('avg_post_reach')}
    - Упоминания (ИЦ): {st.get('mentions_count')}
    - Рост за неделю: {st.get('participants_count_growth_week')}
    - Просмотры последних постов: {views[:15]}
    - Удалено постов недавно: {raw_payload.get('deleted_count')}

    ТВОИ КРИТЕРИИ:
    1. 'Забор': Если разница между просмотрами постов < 5%, это 100% боты.
    2. 'Кладбище': Если охват < 5% от числа подписчиков — это накрученные боты.
    3. 'Фантом': Рост более 300 сабов при почти 0 упоминаний — это залив ботов.

    ОТВЕТЬ СТРОГО ПО ФОРМАТУ:
    📊 Вердикт: (чистый / накрутка / подозрительный)
    📈 Риск: (число от 0.0 до 10.0)
    🧐 Почему: (3 кратких причины)
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini", # <--- САМАЯ ДЕШЕВАЯ МОДЕЛЬ
            messages=[
                {"role": "system", "content": "Ты эксперт по выявлению накруток в Telegram. Говоришь только правду, основываясь на аномалиях в цифрах."},
                {"role": "user", "content": prompt}
            ],
            temperature=0 # Чтобы не фантазировал, а считал точно
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"❌ Ошибка GPT: {str(e)}"

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = re.search(r"(?:t\.me/|@)?([A-Za-z0-9_]{5,})", update.message.text or "")
    if not m: return
    cid = m.group(1)
    
    status_msg = await update.message.reply_text(f"🔍 Собираю данные по @{cid}...")
    h = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}

    try:
        # 1. Сбор статы
        r = requests.get(f"{BASE_URL}/channels/stat", headers=h, params={"channelId": cid}).json()
        st = r.get("response", {})
        if not st:
            await status_msg.edit_text("❌ Telemetr не отдал данные.")
            return

        # 2. Сбор постов
        pr = requests.get(f"{BASE_URL}/channels/posts", headers=h, params={"channelId": cid, "limit": 20}).json()
        items = pr.get("response", {}).get("items", [])
        
        raw_payload = {
            "stats": st,
            "views": [p.get("views_count") or 0 for p in items],
            "deleted_count": sum(1 for p in items if p.get("is_deleted"))
        }

        await status_msg.edit_text(f"🧠 GPT-аналитик проверяет @{cid} на вшивость...")

        # 3. Анализ GPT
        verdict = await ask_gpt_expert(raw_payload)
        
        await status_msg.edit_text(f"✅ Анализ завершен для @{cid}:\n\n{verdict}")

    except Exception as e:
        logger.error(e)
        await status_msg.edit_text("❌ Ошибка при запросе к API.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__": main()
