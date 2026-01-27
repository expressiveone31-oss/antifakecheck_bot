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

BOT_TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
TELEMETR_TOKEN = (os.environ.get("TELEMETR_TOKEN") or "").strip()
TELEMETR_BASE_URL = (os.environ.get("TELEMETR_BASE_URL") or "https://api.telemetr.me").rstrip("/")

TIMEOUT = 25

def make_headers() -> dict:
    return {
        "Authorization": f"Bearer {TELEMETR_TOKEN}",
        "Accept": "application/json",
    }

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
    r = requests.get(url, headers=make_headers(), params=params, timeout=TIMEOUT)
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

def safe_get(path: str, params: dict) -> dict | None:
    try:
        return telemetr_get(path, params)
    except Exception:
        return None

# --------------------------
# Base scoring (soft)
# --------------------------
def base_fake_score(err_percent: float, avg_reach: int, members: int) -> int:
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

# --------------------------
# Event-based flag detection
# --------------------------
# Ключевые слова — только про накрутку/фрод (без "bot")
SUSPECT_KEYWORDS = [
    "накрут", "подозр", "фейк", "fake", "fraud", "scam",
    "artificial", "manipulat", "ботовод", "боты",
    "просмотр", "view", "подписчик", "subscriber"
]

def _contains_suspect_text(s: str) -> bool:
    s = (s or "").lower()
    return any(k in s for k in SUSPECT_KEYWORDS)

def detect_telemetr_suspect_flag(resp: dict) -> tuple[bool, str]:
    """
    Возвращает (True/False, reason).

    ВАЖНО:
    - Не сканируем весь JSON подряд (это даёт фолсы на about, ссылки на рекламных ботов и т.д.)
    - Смотрим только на явные флаги и на "служебные" поля модерации/предупреждений.
    """
    if not isinstance(resp, dict):
        return False, ""

    # 1) Явные булевые флаги (самое надёжное)
    BOOL_FLAGS = (
        "is_badlisted", "badlisted",
        "is_suspicious", "suspected",
        "is_scam", "scam",
        "is_fraud", "fraud",
    )
    for key in BOOL_FLAGS:
        if resp.get(key) is True:
            return True, f"Telemetr flag: {key}=true"

    # 2) Служебные поля, где обычно живут предупреждения/модерация
    # (важно: сюда НЕ включаем about/title/description/username)
    MOD_FIELDS = (
        "warnings", "alerts", "flags",
        "restrictions", "moderation",
        "notes", "labels", "status",
        "risk", "fraud_status", "scam_status",
    )

    # Проверяем значения в этих полях
    for key in MOD_FIELDS:
        if key not in resp:
            continue

        v = resp.get(key)

        # строка
        if isinstance(v, str) and _contains_suspect_text(v):
            return True, f"Telemetr {key}: {v}"

        # список строк/объектов
        if isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, str) and _contains_suspect_text(item):
                    return True, f"Telemetr {key}[{i}]: {item}"
                if isinstance(item, dict):
                    for k2 in ("type", "message", "text", "reason", "title", "description", "code"):
                        t = item.get(k2)
                        if isinstance(t, str) and _contains_suspect_text(t):
                            return True, f"Telemetr {key}[{i}].{k2}: {t}"
                    # Также если внутри item есть явный булевый флаг
                    for bf in BOOL_FLAGS:
                        if item.get(bf) is True:
                            return True, f"Telemetr {key}[{i}]: {bf}=true"

        # вложенный объект
        if isinstance(v, dict):
            # сначала ищем явные булевые
            for bf in BOOL_FLAGS:
                if v.get(bf) is True:
                    return True, f"Telemetr {key}: {bf}=true"
            # потом пробуем типовые текстовые ключи
            for k2 in ("type", "message", "text", "reason", "description", "code"):
                t = v.get(k2)
                if isinstance(t, str) and _contains_suspect_text(t):
                    return True, f"Telemetr {key}.{k2}: {t}"

    # 3) НИЧЕГО НЕ НАШЛИ → флага нет
    return False, ""


# --------------------------
# Telegram handlers
# --------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Кинь ссылку или @username канала — я оценю накрутку (1–10).")

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
        await update.message.reply_text("Не распознала канал 😕 Пришли https://t.me/username или @username")
        return

    try:
        # 1) Статы
        stat_raw = telemetr_get("/channels/stat", {"channelId": channel_id})
        stat = stat_raw.get("response", {}) or {}

        title = stat.get("title") or channel_id
        username = stat.get("username") or channel_id
        members = int(stat.get("participants_count") or 0)
        avg_reach = int(stat.get("avg_post_reach") or 0)
        er_percent = float(stat.get("err_percent") or 0.0)
        ci_index = stat.get("ci_index", 0)
        scoring_rate = stat.get("scoring_rate", 0)

        # 2) Доп.мета (там часто живут флаги модерации/подозрения)
        meta_raw = safe_get("/channels/get", {"channelId": channel_id})
        meta = (meta_raw or {}).get("response", {}) if isinstance(meta_raw, dict) else {}

        # 3) Базовый риск
        risk = base_fake_score(er_percent, avg_reach, members)

        # 4) Event-based floor=8 по флагу (ищем в stat и meta)
        flagged1, reason1 = detect_telemetr_suspect_flag(stat, "stat")
        flagged2, reason2 = detect_telemetr_suspect_flag(meta, "get")

        flagged = flagged1 or flagged2
        reason = reason1 if flagged1 else reason2

        flag_text = ""
        if flagged:
            risk = max(risk, 8)
            flag_text = (
                "\n🚨 Telemetr: канал помечен как подозрительный → риск минимум 8/10\n"
                f"Источник: {reason}\n"
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

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
