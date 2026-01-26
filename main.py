import os
import re
import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

TIMEOUT = 25


def get_bot_token() -> str:
    return (os.environ.get("BOT_TOKEN") or "").strip()


def get_base_url() -> str:
    return (os.environ.get("TELEMETR_BASE_URL") or "https://api.telemetr.me").rstrip("/")


def get_telemetr_token() -> str:
    # ВАЖНО: читаем каждый раз из окружения, не кешируем
    return (os.environ.get("TELEMETR_TOKEN") or "").strip()


def get_headers() -> dict:
    token = get_telemetr_token()
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def extract_channel_id(text: str) -> str | None:
    if not text:
        return None

    t = text.strip()
    t = t.strip(" \n\t<>[](){}.,;")

    m = re.search(r"(?:https?://)?t\.me/([A-Za-z0-9_]{5,})", t)
    if m:
        return m.group(1)

    if t.startswith("@"):
        u = t[1:]
        if re.fullmatch(r"[A-Za-z0-9_]{5,}", u):
            return u

    if re.fullmatch(r"[A-Za-z0-9_]{5,}", t):
        return t

    return None


def telemetr_get(path: str, params: dict):
    base_url = get_base_url()
    url = f"{base_url}{path}"
    r = requests.get(url, headers=get_headers(), params=params, timeout=TIMEOUT)

    if not r.ok:
        try:
            body = r.json()
        except Exception:
            body = r.text
        raise requests.HTTPError(
            f"{r.status_code} {r.reason} for url: {r.url}\nResponse: {body}",
            response=r,
        )

    return r.json()


def score_fake(err_percent: float, avg_reach: int, members: int) -> int:
    score = 1

    if err_percent < 5:
        score += 4
    elif err_percent < 8:
        score += 2

    if members and avg_reach:
        ratio = avg_reach / members
        if ratio < 0.04:
            score += 4
        elif ratio < 0.08:
            score += 2

    return min(score, 10)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Кинь ссылку или @username Telegram-канала — я пришлю ER, CI и скоринг накрутки (1–10)."
    )


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверяем РЕАЛЬНОЕ окружение, а не закешированную переменную
    base_url = get_base_url()
    t = get_telemetr_token()
    b = get_bot_token()

    await update.message.reply_text(
        "OK ✅\n"
        f"BASE_URL: {base_url}\n"
        f"BOT_TOKEN present: {'YES' if bool(b) else 'NO'}\n"
        f"TELEMETR_TOKEN present: {'YES' if bool(t) else 'NO'}\n"
        f"TELEMETR_TOKEN len: {len(t) if t else 0}"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not get_bot_token():
        await update.message.reply_text("Ошибка: BOT_TOKEN не задан в переменных окружения.")
        return
    if not get_telemetr_token():
        await update.message.reply_text("Ошибка: TELEMETR_TOKEN не задан в переменных окружения.")
        return

    raw = update.message.text or ""
    channel_id = extract_channel_id(raw)

    if not channel_id:
        await update.message.reply_text("Не распознала канал 😕 Пришли ссылку вида https://t.me/username или @username.")
        return

    try:
        # Telemetr.me: GET /channels/stat?channelId=...
        data = telemetr_get("/channels/stat", {"channelId": channel_id})

        resp = data.get("response", {})
        title = resp.get("title") or channel_id
        username = resp.get("username") or channel_id

        members = int(resp.get("participants_count") or 0)
        avg_reach = int(resp.get("avg_post_reach") or 0)
        err_percent = float(resp.get("err_percent") or 0.0)
        ci_index = resp.get("ci_index", 0)
        scoring_rate = resp.get("scoring_rate", 0)

        risk = score_fake(err_percent, avg_reach, members)

        await update.message.reply_text(
            f"📊 {title}\n"
            f"@{username}\n\n"
            f"👥 Подписчики: {members}\n"
            f"👀 Средний охват поста: {avg_reach}\n"
            f"📈 ER: {err_percent:.2f}%\n"
            f"🔗 CI (индекс цитирования): {ci_index}\n"
            f"⭐️ Telemetr rating: {scoring_rate}\n\n"
            f"⚠️ Вероятность накрутки: {risk}/10"
        )

    except requests.HTTPError as e:
        await update.message.reply_text(f"Ошибка Telemetr API:\n{e}")
    except Exception as e:
        await update.message.reply_text(f"Неожиданная ошибка:\n{e}")


def main():
    bot_token = get_bot_token()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is missing")

    app = ApplicationBuilder().token(bot_token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling()


if __name__ == "__main__":
    main()
