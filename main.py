import os
import re
import logging
import requests

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

# ----------------------------
# Logging
# ----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("antifakecheck_bot")

# ----------------------------
# Env
# ----------------------------
BOT_TOKEN = (os.getenv("BOT_TOKEN", "") or "").strip()
_RAW_TELEMETR_TOKEN = (os.getenv("TELEMETR_TOKEN", "") or "").strip()

# На случай если кто-то положил в value строку "TELEMETR_TOKEN=xxxxx"
if _RAW_TELEMETR_TOKEN.startswith("TELEMETR_TOKEN="):
    TELEMETR_TOKEN = _RAW_TELEMETR_TOKEN.split("=", 1)[1].strip()
else:
    TELEMETR_TOKEN = _RAW_TELEMETR_TOKEN

TELEMETR_BASE_URL = (os.getenv("TELEMETR_BASE_URL", "https://api.telemetr.me") or "").strip().rstrip("/")

TIMEOUT = 25

HEADERS = {
    "Authorization": f"Bearer {TELEMETR_TOKEN}",
    "Accept": "application/json",
}

# ----------------------------
# Helpers
# ----------------------------
def extract_channel_id(text: str) -> str | None:
    """
    Принимает:
      - https://t.me/username
      - t.me/username
      - @username
      - username
    Возвращает channelId для Telemetr.me.
    """
    if not text:
        return None

    t = text.strip().strip(" \n\t<>[](){}.,;")

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
    url = f"{TELEMETR_BASE_URL}{path}"
    r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)

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


def score_fake_simple(err_percent: float, avg_reach: int, members: int) -> int:
    """
    Базовый скоринг (пока простой).
    """
    score = 1

    # ER подозрительно низкий
    if err_percent < 5:
        score += 4
    elif err_percent < 8:
        score += 2

    # охват/подписчики подозрительно низкий
    if members and avg_reach:
        ratio = avg_reach / members
        if ratio < 0.04:
            score += 4
        elif ratio < 0.08:
            score += 2

    return min(score, 10)


# ----------------------------
# Telegram handlers
# ----------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Кинь ссылку или @username Telegram-канала — я пришлю базовые метрики и скоринг (1–10)."
    )


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "OK ✅\n"
        f"BASE_URL: {TELEMETR_BASE_URL}\n"
        f"BOT_TOKEN set: {'YES' if bool(BOT_TOKEN) else 'NO'}\n"
        f"TELEMETR_TOKEN set: {'YES' if bool(TELEMETR_TOKEN) else 'NO'}\n"
        f"TELEMETR_TOKEN looks like: {'TELEMETR_TOKEN=...' if _RAW_TELEMETR_TOKEN.startswith('TELEMETR_TOKEN=') else 'raw token'}"
    )


async def cmd_telemetr_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Тестим Telemetr API на публичном канале telemetr_me.
    Если тут 401/403/400 — значит проблема точно в токене/плане/домене.
    """
    if not TELEMETR_TOKEN:
        await update.message.reply_text("TELEMETR_TOKEN пустой ❌ Проверь Railway Variables.")
        return

    try:
        data = telemetr_get("/channels/stat", {"channelId": "telemetr_me"})
        resp = data.get("response", {})
        title = resp.get("title") or "telemetr_me"
        members = resp.get("participants_count")
        await update.message.reply_text(
            "Telemetr API test ✅\n"
            f"Channel: {title}\n"
            f"participants_count: {members}"
        )
    except Exception as e:
        await update.message.reply_text(f"Telemetr API test ❌\n{e}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not BOT_TOKEN:
        await update.message.reply_text("Ошибка: BOT_TOKEN не задан в переменных окружения.")
        return
    if not TELEMETR_TOKEN:
        await update.message.reply_text("Ошибка: TELEMETR_TOKEN не задан в переменных окружения.")
        return

    raw = update.message.text or ""
    channel_id = extract_channel_id(raw)

    if not channel_id:
        await update.message.reply_text("Не распознала канал 😕 Пришли ссылку вида https://t.me/username или @username.")
        return

    try:
        data = telemetr_get("/channels/stat", {"channelId": channel_id})
        resp = data.get("response", {})

        title = resp.get("title") or channel_id
        username = resp.get("username") or channel_id

        members = int(resp.get("participants_count") or 0)
        avg_reach = int(resp.get("avg_post_reach") or 0)
        err_percent = float(resp.get("err_percent") or 0.0)
        ci_index = resp.get("ci_index", 0)
        scoring_rate = resp.get("scoring_rate", 0)

        risk = score_fake_simple(err_percent, avg_reach, members)

        await update.message.reply_text(
            f"📊 {title}\n"
            f"@{username}\n\n"
            f"👥 Подписчики: {members}\n"
            f"👀 Средний охват поста: {avg_reach}\n"
            f"📈 ER: {err_percent:.2f}%\n"
            f"🔗 CI: {ci_index}\n"
            f"⭐️ Telemetr rating: {scoring_rate}\n\n"
            f"⚠️ Вероятность накрутки: {risk}/10"
        )

    except requests.HTTPError as e:
        await update.message.reply_text(f"Ошибка Telemetr API:\n{e}")
    except Exception as e:
        await update.message.reply_text(f"Неожиданная ошибка:\n{e}")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    # это попадёт в Railway logs + не будет "No error handlers are registered"
    log.exception("Unhandled exception", exc_info=context.error)


# ----------------------------
# Main
# ----------------------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    log.info("BOOT: base_url=%s", TELEMETR_BASE_URL)
    log.info("BOOT: BOT_TOKEN set=%s", bool(BOT_TOKEN))
    log.info("BOOT: TELEMETR_TOKEN set=%s", bool(TELEMETR_TOKEN))
    log.info("BOOT: TELEMETR_TOKEN raw startswith 'TELEMETR_TOKEN=' = %s", _RAW_TELEMETR_TOKEN.startswith("TELEMETR_TOKEN="))

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_error_handler(on_error)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("telemetr_test", cmd_telemetr_test))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # КЛЮЧЕВОЕ: прибиваем webhook и чистим очередь апдейтов перед polling
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()


