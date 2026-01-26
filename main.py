import os
import re
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN")

BASE_URL = "https://api.telemetr.me"

HEADERS = {
    "Authorization": f"Bearer {TELEMETR_TOKEN}",
    "Content-Type": "application/json"
}


# ---------- utils ----------

def extract_channel_id(text: str) -> str | None:
    """
    Принимает:
    - https://t.me/username
    - @username
    - username
    """
    text = text.strip()

    if "t.me/" in text:
        return text.split("t.me/")[-1].split("/")[0]

    if text.startswith("@"):
        return text[1:]

    if re.match(r"^[a-zA-Z0-9_]{5,}$", text):
        return text

    return None


def telemetr_get(path: str, params: dict):
    url = f"{BASE_URL}{path}"
    r = requests.get(url, headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def score_fake(er: float, avg_reach: int, members: int) -> int:
    """
    Примитивный скоринг накрутки 1–10
    (можем усложнить позже)
    """
    score = 1

    if er < 5:
        score += 3
    elif er < 8:
        score += 2

    if avg_reach and members:
        reach_ratio = avg_reach / members
        if reach_ratio < 0.05:
            score += 3
        elif reach_ratio < 0.1:
            score += 2

    return min(score, 10)


# ---------- handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Кинь ссылку или @username Telegram-канала — я оценю накрутку 📊"
    )


async def handle_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    channel_id = extract_channel_id(text)

    if not channel_id:
        await update.message.reply_text("Не смогла распознать канал 😕")
        return

    try:
        data = telemetr_get(
            "/channels/stat",
            {"channelId": channel_id}
        )

        ch = data["response"]

        title = ch.get("title", channel_id)
        members = ch.get("participants_count", 0)
        avg_reach = ch.get("avg_post_reach", 0)
        er = ch.get("err_percent", 0.0)
        ci = ch.get("ci_index", 0)
        scoring_rate = ch.get("scoring_rate", 0)

        risk = score_fake(er, avg_reach, members)

        await update.message.reply_text(
            f"📊 *{title}*\n\n"
            f"👥 Подписчики: {members}\n"
            f"👀 Средний охват поста: {avg_reach}\n"
            f"📈 ER: {er:.2f}%\n"
            f"🔗 Индекс цитирования (CI): {ci}\n"
            f"⭐️ Рейтинг Telemetr: {scoring_rate}\n\n"
            f"⚠️ *Вероятность накрутки:* **{risk}/10**",
            parse_mode="Markdown"
        )

    except requests.HTTPError as e:
        await update.message.reply_text(
            f"Ошибка Telemetr API:\n{e.response.status_code} {e.response.text}"
        )
    except Exception as e:
        await update.message.reply_text(f"Неожиданная ошибка: {e}")


# ---------- main ----------

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_channel))

    app.run_polling()


if __name__ == "__main__":
    main()
