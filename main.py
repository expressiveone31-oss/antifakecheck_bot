import os, re, requests, json
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEMETR_TOKEN = os.environ.get("TELEMETR_TOKEN", "").strip()
BASE_URL = "https://api.telemetr.me"

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = re.search(r"(?:t\.me/|@)?([A-Za-z0-9_]{5,})", update.message.text or "")
    if not m: return
    cid = m.group(1)
    h = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}

    await update.message.reply_text(f"🚀 Запрашиваю сырые данные для `{cid}`...")

    try:
        # 1. Тянем общую статику
        st_r = requests.get(f"{BASE_URL}/channels/stat", headers=h, params={"channelId": cid}).json()
        
        # 2. Тянем последние посты
        ps_r = requests.get(f"{BASE_URL}/channels/posts", headers=h, params={"channelId": cid, "limit": 20}).json()

        # Формируем "Сырой отчет"
        st = st_r.get("response", {})
        ps = ps_r.get("response", {}).get("items", [])

        raw_report = {
            "CHANNEL": cid,
            "MAIN_STATS": {
                "subscribers": st.get("participants_count"),
                "reach_avg": st.get("avg_post_reach"),
                "mentions_total": st.get("mentions_count"),
                "forwards_total": st.get("forwards_count"),
                "growth_week": st.get("participants_count_growth_week"),
                "growth_day": st.get("participants_count_growth_day"),
                "telemetr_rating": st.get("scoring_rate")
            },
            "POSTS_DATA": [
                {
                    "id": p.get("id"),
                    "views": p.get("views_count") or (p.get("stats", {}) if isinstance(p.get("stats"), dict) else {}).get("views"),
                    "is_deleted": p.get("is_deleted"),
                    "date": p.get("date")
                } for p in ps
            ]
        }

        # Отправляем как JSON, чтобы структура не поплыла
        json_str = json.dumps(raw_report, indent=2, ensure_ascii=False)
        
        if len(json_str) > 4000:
            # Если данных слишком много, сохраняем в файл
            with open("raw_data.json", "w", encoding="utf-8") as f:
                f.write(json_str)
            await update.message.reply_document(open("raw_data.json", "rb"), caption=f"Сырые данные: {cid}")
        else:
            await update.message.reply_text(f"```json\n{json_str}\n```", parse_mode=ParseMode.MARKDOWN_V2)

    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__": main()
