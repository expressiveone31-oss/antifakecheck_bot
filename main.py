import os
import re
import requests
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("TELEMETR_API_KEY")

BASE_URL = "https://api.telemetr.io"

def normalize_handle(text: str):
    text = (text or "").strip()
    m = re.search(r"(?:@|t\.me/)([A-Za-z0-9_]{4,})", text)
    if not m:
        return None
    return "@" + m.group(1)

def telemetr_get(path: str, params: dict):
    headers = {
        "accept": "application/json",
        "x-api-key": API_KEY
    }
    r = requests.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def score_fake(er_percent, views, members):
    score = 1

    if members and views is not None:
        ratio = views / max(members, 1)
        if ratio < 0.05: score += 4
        elif ratio < 0.08: score += 3
        elif ratio < 0.12: score += 2
        elif ratio < 0.18: score += 1

    if er_percent is not None:
        if er_percent < 2: score += 4
        elif er_percent < 4: score += 3
        elif er_percent < 6: score += 2
        elif er_percent < 8: score += 1

    return max(1, min(10, score))

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not BOT_TOKEN or not API_KEY:
        await update.message.reply_text("Не заданы BOT_TOKEN или TELEMETR_API_KEY.")
        return

    handle = normalize_handle(update.message.text)
    if not handle:
        await update.message.reply_text("Пришли @username канала или ссылку https://t.me/username")
        return

    try:
        res = telemetr_get("/v1/channels/search", {
            "term": handle,
            "limit": 1,
            "skip": 0
        })

        if not res:
            await update.message.reply_text(f"Канал {handle} не найден в Telemetr.")
            return

        ch = res[0]
        import json
        await update.message.reply_text(
            "SEARCH RESPONSE:\n" + json.dumps(ch, ensure_ascii=False, indent=2)
        )
        return

        internal_id = ch.get("internal_id") or ch.get("id")
        title = ch.get("title") or handle

        stats = telemetr_get("/v1/channel/stats", {
            "internal_id": internal_id
        })

        s = stats[0] if isinstance(stats, list) and stats else {}

        members = s.get("members")
        views = s.get("views_avg")
        er = s.get("err_percent")

        members = int(members) if members is not None else None
        views = int(views) if views is not None else None
        er = float(er) if er is not None else None

        risk = score_fake(er, views, members)

        msg = (
            f"**{title}** ({handle})\n\n"
            f"Подписчики: {members if members else '—'}\n"
            f"Средние просмотры: {views if views else '—'}\n"
            f"ER (%): {er if er else '—'}\n\n"
            f"Вероятность накрутки: **{risk}/10**"
        )

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.run_polling()

if __name__ == "__main__":
    main()
