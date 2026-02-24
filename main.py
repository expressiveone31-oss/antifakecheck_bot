import os
import re
import math
import statistics
import requests
from typing import Any, Dict, List, Tuple, Optional

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN", "").strip()

# Telemetr.me (НЕ telemetr.io)
TELEMETR_BASE_URL = os.getenv("TELEMETR_BASE_URL", "https://api.telemetr.me").rstrip("/")

TIMEOUT = int(os.getenv("TELEMETR_TIMEOUT", "25"))
# если Telemetr часто тупит — поставь 40–60
# TIMEOUT = 45

HEADERS = {
    "Authorization": f"Bearer {TELEMETR_TOKEN}",
    "Accept": "application/json",
}

# =========================
# Helpers: parse channel id
# =========================
def extract_channel_id(text: str) -> Optional[str]:
    """
    Принимает:
      - https://t.me/username
      - t.me/username
      - @username
      - username
    Возвращает channelId для Telemetr.me (username / joinchat если надо будет).
    """
    if not text:
        return None

    t = text.strip()
    t = t.strip(" \n\t<>[](){}.,;\"'")

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


# =========================
# Telemetr API
# =========================
def telemetr_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
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


def telemetr_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{TELEMETR_BASE_URL}{path}"
    r = requests.post(url, headers=HEADERS, json=payload, timeout=TIMEOUT)

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


# =========================
# Event-based flag detection (Telemetr suspect)
# =========================
SUSPECT_KEYWORDS = [
    "накрут", "подозр", "фейк", "fake", "fraud", "scam",
    "artificial", "manipulat", "ботовод", "боты",
    "просмотр", "view", "подписчик", "subscriber",
    "suspicious", "suspect", "badlist", "blacklist"
]

def _contains_suspect_text(s: str) -> bool:
    s = (s or "").lower()
    return any(k in s for k in SUSPECT_KEYWORDS)

def detect_telemetr_suspect_flag(resp: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Возвращает (True/False, reason)
    Ищем только "служебные" признаки, а не описание канала.
    """
    if not isinstance(resp, dict):
        return False, ""

    # 1) явные булевы флаги — самое надежное
    BOOL_FLAGS = (
        "is_badlisted", "badlisted",
        "is_suspicious", "suspected",
        "is_scam", "scam",
        "is_fraud", "fraud",
        "is_blacklisted", "blacklisted",
    )
    for key in BOOL_FLAGS:
        if resp.get(key) is True:
            return True, f"Telemetr flag: {key}=true"

    # 2) служебные поля: moderation/warnings/flags (НЕ about)
    SERVICE_KEYS = ("warnings", "warning", "moderation", "flags", "status", "meta")
    for sk in SERVICE_KEYS:
        v = resp.get(sk)
        if isinstance(v, str) and _contains_suspect_text(v):
            return True, f"Telemetr {sk}: {v}"
        if isinstance(v, dict):
            for bf in BOOL_FLAGS:
                if v.get(bf) is True:
                    return True, f"Telemetr {sk}: {bf}=true"
            for k2 in ("type", "message", "text", "reason", "description", "code"):
                t = v.get(k2)
                if isinstance(t, str) and _contains_suspect_text(t):
                    return True, f"Telemetr {sk}.{k2}: {t}"
        if isinstance(v, list):
            for i, item in enumerate(v[:10]):
                if isinstance(item, dict):
                    for bf in BOOL_FLAGS:
                        if item.get(bf) is True:
                            return True, f"Telemetr {sk}[{i}]: {bf}=true"
                    for k2 in ("type", "message", "text", "reason", "description", "code"):
                        t = item.get(k2)
                        if isinstance(t, str) and _contains_suspect_text(t):
                            return True, f"Telemetr {sk}[{i}].{k2}: {t}"
                elif isinstance(item, str) and _contains_suspect_text(item):
                    return True, f"Telemetr {sk}[{i}]: {item}"

    return False, ""


# =========================
# Scoring (human-readable)
# =========================
def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def safe_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default

def safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def cv(values: List[float]) -> float:
    """Coefficient of variation: std/mean. If mean==0 -> large."""
    if not values:
        return 0.0
    m = statistics.mean(values)
    if m == 0:
        return 999.0
    s = statistics.pstdev(values)
    return float(s / m)

def score_block_a_subscriber_dynamics(members: int, day_delta: int, week_delta: int, month_delta: int) -> Tuple[float, List[str]]:
    """
    Главный блок.
    Считаем проценты и минимальный абсолют, чтобы не ловить шум.
    Возвращает (баллы 0–4, причины).
    """
    pts = 0.0
    reasons = []

    def add(rule: str, add_pts: float):
        nonlocal pts
        pts += add_pts
        reasons.append(rule)

    def pct(delta: int) -> float:
        if members <= 0:
            return 0.0
        return (delta / members) * 100.0

    # День
    p_day = pct(day_delta)
    if (p_day <= -0.60) and (abs(day_delta) >= 500):
        add(f"день: Δ={day_delta} ({p_day:.2f}%) → +3", 3.0)
    elif (p_day <= -0.35) and (abs(day_delta) >= 300):
        add(f"день: Δ={day_delta} ({p_day:.2f}%) → +2", 2.0)

    # Неделя (примерная логика, можешь править)
    p_week = pct(week_delta)
    if (p_week <= -2.0) and (abs(week_delta) >= 2000):
        add(f"неделя: Δ={week_delta} ({p_week:.2f}%) → +2", 2.0)

    # Месяц (примерная логика)
    p_month = pct(month_delta)
    if (p_month <= -8.0) and (abs(month_delta) >= 8000):
        add(f"месяц: Δ={month_delta} ({p_month:.2f}%) → +2", 2.0)

    pts = clamp(pts, 0.0, 4.0)
    if not reasons:
        reasons.append("нет сильных сигналов")
    return pts, reasons

def score_block_b_members_vs_reach_er(members: int, avg_reach: int, err_percent: float) -> Tuple[float, List[str]]:
    """
    0–3 балла.
    Идея: подозрительно низкий охват/подписчики или странный ER.
    """
    pts = 0.0
    reasons = []

    if members > 0 and avg_reach > 0:
        ratio = avg_reach / members  # доля охвата
        # очень низко
        if ratio < 0.04:
            pts += 3.0
            reasons.append(f"охват/подписчики={ratio:.3f} (<0.04) → +3")
        elif ratio < 0.08:
            pts += 1.5
            reasons.append(f"охват/подписчики={ratio:.3f} (<0.08) → +1.5")
        else:
            reasons.append(f"охват/подписчики={ratio:.3f} — ок")

    # err_percent (у тебя это ER, судя по Telemetr /channels/stat)
    if err_percent < 5.0:
        pts += 1.5
        reasons.append(f"ER={err_percent:.2f}% (<5) → +1.5")
    elif err_percent < 8.0:
        pts += 0.8
        reasons.append(f"ER={err_percent:.2f}% (<8) → +0.8")
    else:
        reasons.append(f"ER={err_percent:.2f}% — ок")

    pts = clamp(pts, 0.0, 3.0)
    return pts, reasons

def score_block_c_views_smoothness(post_reaches: List[int]) -> Tuple[float, List[str]]:
    """
    0–1.5 балла.
    Слишком "ровные" охваты могут быть сигналом.
    """
    if not post_reaches:
        return 0.0, ["нет данных по постам"]

    vals = [float(x) for x in post_reaches if x is not None]
    if len(vals) < 8:
        return 0.0, [f"мало постов для оценки (n={len(vals)})"]

    c = cv(vals)
    # Чем ниже CV, тем "ровнее".
    pts = 0.0
    reasons = [f"ровность просмотров: CV={c:.2f} (ниже = ровнее)"]

    if c < 0.15:
        pts = 1.5
        reasons.append("слишком ровно → +1.5")
    elif c < 0.22:
        pts = 0.8
        reasons.append("подозрительно ровно → +0.8")

    return clamp(pts, 0.0, 1.5), reasons

def score_block_d_forwards(forwards: int, avg_reach: int) -> Tuple[float, List[str]]:
    """
    0–1 балл.
    Упрощённо: ноль пересылок при большом охвате иногда странно.
    """
    pts = 0.0
    reasons = []

    if avg_reach >= 20000 and forwards <= 1:
        pts = 1.0
        reasons.append(f"пересылки={forwards} при охвате={avg_reach} → +1")
    else:
        reasons.append("нет явных аномалий по пересылкам")

    return pts, reasons

def score_block_e_er_sanity(err_percent: float, avg_reach: int, members: int) -> Tuple[float, List[str]]:
    """
    0–0.5 балла.
    Лёгкий sanity-check: очень высокий ER при очень низком охвате/подписчиках и т.п.
    """
    pts = 0.0
    reasons = ["нет явных проблем по ER/reach"]

    if members > 0 and avg_reach > 0:
        ratio = avg_reach / members
        # если охват очень низкий, но ER высокий — может быть странно (пример)
        if ratio < 0.03 and err_percent > 20:
            pts = 0.5
            reasons = [f"охват/подписчики={ratio:.3f} и ER={err_percent:.2f}% → +0.5"]

    return pts, reasons


def build_scoring_breakdown(resp: Dict[str, Any], post_reaches: List[int]) -> Tuple[int, str]:
    """
    Возвращает (risk_int_1_10, breakdown_text)
    """
    members = safe_int(resp.get("participants_count"), 0)
    avg_reach = safe_int(resp.get("avg_post_reach"), 0)
    err_percent = safe_float(resp.get("err_percent"), 0.0)
    ci_index = resp.get("ci_index", 0)
    scoring_rate = resp.get("scoring_rate", 0)

    # Дельты подписчиков (если есть в /channels/stat)
    day_delta = safe_int(resp.get("participants_today") or resp.get("subscribers_today") or resp.get("today") or 0)
    week_delta = safe_int(resp.get("participants_week") or resp.get("subscribers_week") or resp.get("week") or 0)
    month_delta = safe_int(resp.get("participants_month") or resp.get("subscribers_month") or resp.get("month") or 0)

    # Блоки
    a_pts, a_reasons = score_block_a_subscriber_dynamics(members, day_delta, week_delta, month_delta)
    b_pts, b_reasons = score_block_b_members_vs_reach_er(members, avg_reach, err_percent)
    c_pts, c_reasons = score_block_c_views_smoothness(post_reaches)
    # forwards/mentions — могут отсутствовать в api, поэтому берём из resp если есть
    forwards = safe_int(resp.get("forwards_count") or resp.get("forwards") or 0)
    d_pts, d_reasons = score_block_d_forwards(forwards, avg_reach)
    e_pts, e_reasons = score_block_e_er_sanity(err_percent, avg_reach, members)

    total = a_pts + b_pts + c_pts + d_pts + e_pts
    # Переводим 0..10: базово 1 + total (но чтобы не было 0)
    risk = int(clamp(round(1 + total), 1, 10))

    # Event-based floor: если Telemetr пометил как подозрительный → минимум 8
    flagged, flag_reason = detect_telemetr_suspect_flag(resp)
    if flagged:
        risk = max(risk, 8)

    breakdown = []
    breakdown.append("🧠 Разбор (почему такой риск):")
    breakdown.append(f"1) Подписчики (0–4): {a_pts:.1f}/4.0  (день {day_delta:+}, неделя {week_delta:+}, месяц {month_delta:+})")
    for r in a_reasons:
        breakdown.append(f"   • {r}")

    breakdown.append(f"\n2) Подписчики vs ER/охват (0–3): {b_pts:.1f}/3.0")
    for r in b_reasons:
        breakdown.append(f"   • {r}")

    breakdown.append(f"\n3) Ровность просмотров (0–1.5): {c_pts:.1f}/1.5 (постов: {len(post_reaches)})")
    for r in c_reasons:
        breakdown.append(f"   • {r}")

    breakdown.append(f"\n4) Пересылки (0–1): {d_pts:.1f}/1.0")
    for r in d_reasons:
        breakdown.append(f"   • {r}")

    breakdown.append(f"\n5) ER/reach (0–0.5): {e_pts:.1f}/0.5")
    for r in e_reasons:
        breakdown.append(f"   • {r}")

    if flagged:
        breakdown.append(f"\n🚨 Telemetr: канал помечен как подозрительный → риск минимум 8/10")
        breakdown.append(f"Источник: {flag_reason}")

    breakdown.append("\nКоманда: /weights — пороги и веса")

    return risk, "\n".join(breakdown)


# =========================
# Telegram handlers
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Кинь ссылку или @username Telegram-канала — я пришлю ER, CI и оценку накрутки (1–10).\n"
        "Команды: /health /weights"
    )


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "OK ✅\n"
        f"BASE_URL: {TELEMETR_BASE_URL}\n"
        f"TELEMETR_TOKEN set: {'YES' if bool(TELEMETR_TOKEN) else 'NO'}\n"
        f"TIMEOUT: {TIMEOUT}s"
    )


async def cmd_weights(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚙️ Веса и пороги скоринга\n\n"
        "A) Динамика подписчиков (0–4)\n"
        "  • День: Δday% ≤ −0.35% и |Δday| ≥ 300  → +2\n"
        "  • День: Δday% ≤ −0.60% и |Δday| ≥ 500  → +3\n"
        "  • Неделя: Δweek% ≤ −2.0% и |Δweek| ≥ 2000 → +2\n"
        "  • Месяц:  Δmonth% ≤ −8.0% и |Δmonth| ≥ 8000 → +2\n\n"
        "B) Подписчики vs ER/охват (0–3)\n"
        "  • avg_reach/members < 0.04 → +3\n"
        "  • avg_reach/members < 0.08 → +1.5\n"
        "  • ER < 5% → +1.5; ER < 8% → +0.8\n\n"
        "C) Ровность просмотров (0–1.5)\n"
        "  • CV < 0.15 → +1.5; CV < 0.22 → +0.8\n\n"
        "D) Пересылки (0–1)\n"
        "  • охват ≥ 20k и пересылок ≤ 1 → +1\n\n"
        "E) ER/reach sanity (0–0.5)\n"
        "  • охват/подписчики < 0.03 и ER > 20% → +0.5\n\n"
        "Event-based floor\n"
        "  • если Telemetr пометил канал как подозрительный → риск минимум 8/10"
    )


def _try_get_posts_reaches(channel_id: str, limit: int = 20) -> List[int]:
    """
    Пытаемся взять охваты постов.
    Если API не отдаёт, просто возвращаем [] (скоринг без блока C).
    """
    # В доках есть /channels/posts (и /channels/posts/get/search),
    # но структура может отличаться. Мы максимально “не ломаемся”.
    try:
        data = telemetr_get("/channels/posts", {"channelId": channel_id, "limit": limit, "offset": 0})
        resp = data.get("response") or {}
        items = resp.get("items") or []
        reaches = []
        for it in items:
            if not isinstance(it, dict):
                continue
            stats = it.get("stats") or {}
            # stats может быть dict или строка/что-то ещё
            if isinstance(stats, dict):
                views = stats.get("views")
                if views is not None:
                    reaches.append(safe_int(views, 0))
        return [x for x in reaches if x > 0]
    except Exception:
        return []


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

    # 1) берём базовую статистику
    try:
        data = telemetr_get("/channels/stat", {"channelId": channel_id})
    except requests.exceptions.Timeout:
        await update.message.reply_text("⏳ Telemetr временно не отвечает.\nПопробуй ещё раз через минуту.")
        return
    except requests.HTTPError as e:
        await update.message.reply_text(f"Ошибка Telemetr API:\n{e}")
        return
    except Exception as e:
        await update.message.reply_text(f"Неожиданная ошибка:\n{e}")
        return

    resp = data.get("response", {}) or {}
    title = resp.get("title") or channel_id
    username = resp.get("username") or channel_id

    members = safe_int(resp.get("participants_count"), 0)
    avg_reach = safe_int(resp.get("avg_post_reach"), 0)
    err_percent = safe_float(resp.get("err_percent"), 0.0)
    ci_index = resp.get("ci_index", 0)
    scoring_rate = resp.get("scoring_rate", 0)

    # дополнительно (могут быть)
    mentions = safe_int(resp.get("mentions_count") or resp.get("mentions") or 0)
    forwards = safe_int(resp.get("forwards_count") or resp.get("forwards") or 0)

    # 2) достаём охваты постов (если получится) — для блока "ровность"
    post_reaches = _try_get_posts_reaches(channel_id, limit=20)

    # 3) скоринг + разбор
    risk, breakdown = build_scoring_breakdown(resp, post_reaches)

    # 4) ответ
    msg = (
        f"📊 {title}\n"
        f"@{username}\n\n"
        f"👥 Подписчики: {members}\n"
        f"👀 Средний охват поста: {avg_reach}\n"
        f"📈 ER: {err_percent:.2f}%\n"
        f"🔗 CI: {ci_index}\n"
        f"⭐️ Telemetr rating: {scoring_rate}\n"
    )
    if mentions:
        msg += f"📣 Упоминания: {mentions}\n"
    if forwards:
        msg += f"↪️ Пересылки: {forwards}\n"

    msg += f"\n⚠️ Вероятность накрутки: {risk}/10\n\n{breakdown}"

    await update.message.reply_text(msg)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("weights", cmd_weights))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
