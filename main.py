import os
import re
import json
import logging
import requests
from datetime import datetime, timedelta

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

# Если вдруг в value положили строку вида "TELEMETR_TOKEN=xxxx"
TELEMETR_TOKEN = _RAW_TELEMETR_TOKEN.split("=", 1)[1].strip() if _RAW_TELEMETR_TOKEN.startswith("TELEMETR_TOKEN=") else _RAW_TELEMETR_TOKEN

TELEMETR_BASE_URL = (os.getenv("TELEMETR_BASE_URL", "https://api.telemetr.me") or "").strip().rstrip("/")
TIMEOUT = 25

HEADERS = {
    "Authorization": f"Bearer {TELEMETR_TOKEN}",
    "Accept": "application/json",
}

session = requests.Session()

# ----------------------------
# Helpers
# ----------------------------
def extract_channel_id(text: str) -> str | None:
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
    r = session.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)

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


def fetch_channel_get(channel_id: str) -> dict:
    # ВАЖНО: флаги/ограничения обычно здесь
    return telemetr_get("/channels/get", {"channelId": channel_id})


def fetch_channel_stat(channel_id: str) -> dict:
    return telemetr_get("/channels/stat", {"channelId": channel_id})


def detect_telemetr_flag(get_resp: dict) -> tuple[bool, str]:
    """
    Смотрим строго на "служебные" поля, а не on about.
    """
    resp = (get_resp or {}).get("response") or {}
    is_badlisted = resp.get("is_badlisted")
    restrictions = resp.get("restrictions")

    if is_badlisted is True:
        return True, "get.response.is_badlisted=true"

    # restrictions может быть list/dict/строка — любое непустое считаем флагом
    if restrictions:
        return True, f"get.response.restrictions={restrictions}"

    return False, ""


def score_fake_simple(err_percent: float, avg_reach: int, members: int) -> int:
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
        "Кинь ссылку или @username канала — пришлю метрики и риск накрутки (1–10)."
    )


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "OK ✅\n"
        f"BASE_URL: {TELEMETR_BASE_URL}\n"
        f"BOT_TOKEN set: {'YES' if bool(BOT_TOKEN) else 'NO'}\n"
        f"TELEMETR_TOKEN set: {'YES' if bool(TELEMETR_TOKEN) else 'NO'}\n"
        f"TELEMETR_TOKEN raw style: {'TELEMETR_TOKEN=...' if _RAW_TELEMETR_TOKEN.startswith('TELEMETR_TOKEN=') else 'raw token'}"
    )


async def cmd_telemetr_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not TELEMETR_TOKEN:
        await update.message.reply_text("TELEMETR_TOKEN пустой ❌")
        return
    try:
        g = fetch_channel_get("telemetr_me")
        s = fetch_channel_stat("telemetr_me")
        await update.message.reply_text(
            "Telemetr API test ✅\n"
            f"/channels/get status: {(g.get('status') or 'ok')}\n"
            f"/channels/stat status: {(s.get('status') or 'ok')}"
        )
    except Exception as e:
        await update.message.reply_text(f"Telemetr API test ❌\n{e}")


async def cmd_debug_raw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /debug_raw @channel — покажет какие поля реально пришли с /channels/get (коротко).
    """
    raw = (update.message.text or "").strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Используй: /debug_raw @username")
        return

    channel_id = extract_channel_id(parts[1])
    if not channel_id:
        await update.message.reply_text("Не распознала канал.")
        return

    try:
        g = fetch_channel_get(channel_id)
        resp = (g.get("response") or {})
        mini = {
            "username": resp.get("username"),
            "title": resp.get("title"),
            "is_badlisted": resp.get("is_badlisted"),
            "restrictions": resp.get("restrictions"),
        }
        await update.message.reply_text("channels/get:\n" + json.dumps(mini, ensure_ascii=False, indent=2))
    except Exception as e:
        await update.message.reply_text(f"debug_raw ❌\n{e}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not BOT_TOKEN:
        await update.message.reply_text("Ошибка: BOT_TOKEN не задан.")
        return
    if not TELEMETR_TOKEN:
        await update.message.reply_text("Ошибка: TELEMETR_TOKEN не задан.")
        return

    raw = update.message.text or ""
    channel_id = extract_channel_id(raw)

    if not channel_id:
        await update.message.reply_text("Не распознала канал 😕 Пришли https://t.me/username или @username.")
        return

    try:
        # 1) Берём служебные флаги
        get_data = fetch_channel_get(channel_id)
        flagged, flag_reason = detect_telemetr_flag(get_data)

        # 2) Берём метрики
        stat_data = fetch_channel_stat(channel_id)
        resp = (stat_data.get("response") or {})

        title = resp.get("title") or channel_id
        username = resp.get("username") or channel_id

        members = int(resp.get("participants_count") or 0)
        avg_reach = int(resp.get("avg_post_reach") or 0)
        err_percent = float(resp.get("err_percent") or 0.0)
        ci_index = resp.get("mentions_count") or resp.get("ci_index") or 0  # на всякий
        scoring_rate = resp.get("scoring_rate", 0)

        risk = score_fake_simple(err_percent, avg_reach, members)

        note = ""
        if flagged:
            risk = max(risk, 8)
            note = f"\n\n🚨 Telemetr: канал помечен как подозрительный → риск минимум 8/10\nПричина: {flag_reason}"

        await update.message.reply_text(
            f"📊 {title}\n"
            f"@{username}\n\n"
            f"👥 Подписчики: {members}\n"
            f"👀 Средний охват поста: {avg_reach}\n"
            f"📈 ER: {err_percent:.2f}%\n"
            f"🔗 CI: {ci_index}\n"
            f"⭐️ Telemetr rating: {scoring_rate}\n\n"
            f"⚠️ Вероятность накрутки: {risk}/10"
            f"{note}"
        )

    except requests.HTTPError as e:
        await update.message.reply_text(f"Ошибка Telemetr API:\n{e}")
    except Exception as e:
        await update.message.reply_text(f"Неожиданная ошибка:\n{e}")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.exception("Unhandled exception", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    log.info("BOOT: base_url=%s", TELEMETR_BASE_URL)
    log.info("BOOT: BOT_TOKEN set=%s", bool(BOT_TOKEN))
    log.info("BOOT: TELEMETR_TOKEN set=%s", bool(TELEMETR_TOKEN))

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_error_handler(on_error)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("telemetr_test", cmd_telemetr_test))
    app.add_handler(CommandHandler("debug_raw", cmd_debug_raw))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # важно: чистим очередь апдейтов и не даём старым апдейтам мешать
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
