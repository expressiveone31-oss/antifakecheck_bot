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

# ======================
# ENV
# ======================
BOT_TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
TELEMETR_TOKEN = (os.environ.get("TELEMETR_TOKEN") or "").strip()
TELEMETR_BASE_URL = (os.environ.get("TELEMETR_BASE_URL") or "https://api.telemetr.me").rstrip("/")

HEADERS = {
    "Authorization": f"Bearer {TELEMETR_TOKEN}",
    "Accept": "application/json",
}

TIMEOUT = 25

# ======================
# UTILS
# ======================
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


# ======================
# BASIC SCORE (soft layer)
# ======================
def base_fake_score(err_percent: float, avg_reach: int, members: int) -> int:
    """
    Базовый (soft) скоринг, ДО event-based.
    """
    score = 1

    if err_percent < 5:
        score += 4
    elif err_percent < 8:
        score += 2

    if members > 0 and avg_reach > 0:
        ratio = avg_reach / members
        if ratio < 0.04:
            score += 4
        elif ratio < 0.08:
            score += 2

    return min(score, 10)


# ======================
# EVENT-BASED DETECTION
# ======================
SUSPECT_KEYWORDS = [
    "накрут", "подозр", "fake", "bot", "fraud", "scam",
    "artificial", "manipulat", "view", "просмотр", "подписчик"
]

def _contains_suspect_text(s: str) -> bool:
    s = (s or "").lower()
    return any(k in s for k in SUSPECT_KEYWORDS)


def detect_telemetr_suspect_flag(resp: dict) -> tuple[bool, str]:
    """
    Ищем любой признак того, что Telemetr пометил канал как подозрительный.
    Возвращаем (True/False, объяснение).
    """
    if not isinstance(resp, dict):
        return False, ""

    # 1. Явные булевые флаги
    for key in ("is_badlisted", "badlisted", "is_suspicious", "suspected", "is_scam"):
        if resp.get(key) is True:
            return True, f"Telemetr flag: {key}=true"

    # 2. Текстовые предупреждения
    for key in ("restrictions", "warnings", "alerts", "notes", "flags"):
        v = resp.get(key)
        if isinstance(v, str) and _contains_suspect_text(v):
            return True, f"Telemetr {key}: {v}"
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str) and _contains_suspect_text(item):
                    return True, f"Telemetr {key}: {item}"
                if isinstance(item, dict):
                    for k2 in ("type", "message", "text", "reason", "description"):
                        t = item.get(k2)
                        if isinstance(t, str) and _contains_suspect_text(t):
                            return True, f"Telemetr {key}.{k2}: {t}"

    # 3. Последний шанс — пройтись по всем строкам
    def walk(x):
        if isinstance(x, str):
            return _contains_suspect_text(x)
        if isinstance(x, dict):
            return any(walk(v) for v in x.values())
        if isinstance(x, list):
            return any(walk(v) for v in x)
        return False

    if walk(resp):
        return True, "Telemetr flag: suspect keywords found"

    return False, ""


# ======================
# HANDLERS
# ======================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Кинь ссылку или @username Telegram-канала — я оценю вероятность накрутки (1–10)."
    )


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "OK ✅\n"
        f"BASE_URL: {TELEMETR_BASE_URL}\n"
        f"BOT_TOKEN: {'YES' if BOT_TOKEN else 'NO'}\n"
        f"TELEMETR_TOKEN: {'YES' if TELEMETR_TOKEN else 'NO'}"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not BOT_TOKEN:
        await update.message.reply_text("Ошибка: BOT_TOKEN не задан.")
        return
    if not TELEMETR_TOKEN:
        await update.message.reply_text("Ошибка: TELEMETR_TOKEN не задан.")
        return

    channel_id = extract_channel_id(update.message.text or "")
    if not channel_id:
        await update.message.reply_text("Не распознала канал 😕")
        return

    try:
        data = telemetr_get("/channels/stat", {"channelId": channel_id})
        resp = data.get("response", {}) or {}

        title = resp.get("title") or channel_id
        username = resp.get("username") or channel_id
        members = int(resp.get("participants_count") or 0)
        avg_reach = int(resp.get("avg_post_reach") or 0)
        er_percent = float(resp.get("err_percent") or 0.0)
        ci_index = resp.get("ci_index", 0)
        scoring_rate = resp.get("scoring_rate", 0)

        # 1) базовый скор
        risk = base_fake_score(er_percent, avg_reach, members)

        # 2) event-based floor
        flagged, flag_reason = detect_telemetr_suspect_flag(resp)
        if flagged:
            risk = max(risk, 8)

        flag_text = ""
        if flagged:
            flag_text = (
                "\n🚨 Telemetr: канал помечен как подозрительный\n"
                "→ риск минимум 8/10\n"
            )

        await update.message.reply_text(
            f"📊 {title}\n"
            f"@{username}\n\n"
            f"👥 Подписчики: {members}\n"
            f"👀 Средний охват поста: {avg_reach}\n"
            f"📈 ER: {er_percent:.2f}%\n"
            f"🔗 CI: {ci_index}\n"
            f"⭐️ Telemetr rating: {scoring_rate}\n"
            f"{flag_text}"
            f"⚠️ Вероятность накрутки: {risk}/10"
        )

    except requests.HTTPError as e:
        await update.message.reply_text(f"Ошибка Telemetr API:\n{e}")
    except Exception as e:
        await update.message.reply_text(f"Неожиданная ошибка:\n{e}")


# ======================
# MAIN
# ======================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling()


if __name__ == "__main__":
    main()
