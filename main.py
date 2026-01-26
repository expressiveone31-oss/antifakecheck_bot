import os
import re
import math
import statistics
import requests
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN", "").strip()

# Telemetr.me API
TELEMETR_BASE_URL = os.getenv("TELEMETR_BASE_URL", "https://api.telemetr.me").rstrip("/")

HEADERS = {
    "Authorization": f"Bearer {TELEMETR_TOKEN}",
    "Accept": "application/json",
}

TIMEOUT = 25


# -------------------------
# Helpers: parsing channel id
# -------------------------
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


# -------------------------
# Telemetr API calls
# -------------------------
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


def safe_get(path: str, params: dict) -> dict | None:
    """Не валим бота, если доп.эндпоинт не отдался — просто вернём None."""
    try:
        return telemetr_get(path, params)
    except Exception:
        return None


# -------------------------
# Data fetchers
# -------------------------
def fetch_stat(channel_id: str) -> dict:
    data = telemetr_get("/channels/stat", {"channelId": channel_id})
    return (data or {}).get("response", {}) or {}


def fetch_subscribers_series(channel_id: str, group: str = "day", days: int = 31) -> list[dict]:
    """
    /channels/subscribers возвращает массив точек.
    group: hour/day/week/month (в доках default "day")
    """
    # Даты: API любит строки, но формат может отличаться — делаем без них (как минимум group работает).
    params = {"channelId": channel_id, "group": group}

    data = safe_get("/channels/subscribers", params)
    if not data:
        return []

    series = (data.get("response") if isinstance(data, dict) else None) or []
    if not isinstance(series, list):
        return []
    # Оставим последние N точек (если вдруг прилетает много)
    return series[-days:]


def fetch_posts_views(channel_id: str, limit: int = 20) -> list[int]:
    """
    В доках: GET /channels/posts
    Параметры: channelId, offset, hideForwards, hideDeleted
    """
    params = {
        "channelId": channel_id,
        "offset": 0,
        "hideForwards": False,
        "hideDeleted": True,
    }
    data = safe_get("/channels/posts", params)
    if not data:
        return []

    resp = data.get("response", {})
    items = resp.get("items", []) if isinstance(resp, dict) else []
    if not isinstance(items, list):
        return []

    views = []
    for it in items[:limit]:
        stats = it.get("stats") if isinstance(it, dict) else None
        if isinstance(stats, dict):
            v = stats.get("views")
            if isinstance(v, (int, float)) and v >= 0:
                views.append(int(v))
    return views


# -------------------------
# Scoring: "human formula"
# -------------------------
def compute_deltas_from_series(series: list[dict]) -> dict:
    """
    Серия может быть разного формата. Ищем число подписчиков в каждом элементе.
    Возможные ключи: participants_count / subscribers / count / value
    """
    def pick_count(p: dict) -> int | None:
        for k in ("participants_count", "subscribers", "count", "value"):
            v = p.get(k)
            if isinstance(v, (int, float)):
                return int(v)
        return None

    counts = [pick_count(p) for p in series if isinstance(p, dict)]
    counts = [c for c in counts if isinstance(c, int)]

    if len(counts) < 2:
        return {
            "counts": counts,
            "delta_day": 0,
            "delta_week": 0,
            "delta_month": 0,
            "pct_month": 0.0,
            "sawtooth": False,
            "daily_changes": [],
        }

    # daily changes
    daily_changes = []
    for i in range(1, len(counts)):
        daily_changes.append(counts[i] - counts[i - 1])

    last = counts[-1]

    # day: last - prev
    delta_day = counts[-1] - counts[-2]

    # week: last - 7 points back (if possible)
    if len(counts) >= 8:
        delta_week = counts[-1] - counts[-8]
    else:
        delta_week = counts[-1] - counts[0]

    # month: last - first in window
    delta_month = counts[-1] - counts[0]

    pct_month = (delta_month / last) if last else 0.0

    # "sawtooth": есть заметный положительный всплеск, за которым идёт серия отрицательных дней
    # (примитивная, но работающая эвристика)
    sawtooth = False
    if len(daily_changes) >= 10:
        max_up = max(daily_changes)
        min_down = min(daily_changes)
        # если был приличный рост и приличное падение в одном окне — это подозрительно
        if max_up >= 500 and min_down <= -500:
            sawtooth = True

    return {
        "counts": counts,
        "delta_day": delta_day,
        "delta_week": delta_week,
        "delta_month": delta_month,
        "pct_month": pct_month,
        "sawtooth": sawtooth,
        "daily_changes": daily_changes,
    }


def score_subscriber_dynamics(delta_day: int, delta_week: int, pct_month: float, sawtooth: bool) -> tuple[float, list[str]]:
    """
    Блок 1 (0–4)
    """
    s = 0.0
    why = []

    if delta_day <= -500:
        s += 2.0
        why.append(f"−{abs(delta_day)} за день (<=500)")

    if delta_week <= -3000:
        s += 2.0
        why.append(f"−{abs(delta_week)} за неделю (<=3000)")

    if pct_month <= -0.10:
        s += 2.0
        why.append(f"падение {abs(pct_month)*100:.1f}% за месяц (>=10%)")

    if sawtooth:
        s += 1.0
        why.append("«пила»: есть и крупный рост, и крупное падение")

    s = min(s, 4.0)
    return s, why


def score_reach_vs_dynamics(delta_week: int, delta_month: int, er_percent: float, avg_reach: int, members: int) -> tuple[float, list[str]]:
    """
    Блок 2 (0–3)
    Идея: если подписчики падают, а ER/охваты не ведут себя естественно — подозрительно.
    """
    s = 0.0
    why = []

    if members > 0 and avg_reach > 0:
        reach_ratio = avg_reach / members
    else:
        reach_ratio = 0.0

    if delta_month < 0 and er_percent >= 20:
        s += 3.0
        why.append("подписчики падают, ER высокий (>=20%)")
    elif delta_month < 0 and er_percent >= 15:
        s += 2.0
        why.append("подписчики падают, ER высокий (>=15%)")
    elif delta_week < 0 and er_percent >= 15:
        s += 1.0
        why.append("падение за неделю при высоком ER")

    # дополнительный сигнал: очень высокий reach/subs на фоне падения
    if delta_month < 0 and reach_ratio >= 0.20:
        s = max(s, 2.0)
        why.append("падение подписчиков при очень высоком reach/subs (>=20%)")

    s = min(s, 3.0)
    return s, why


def score_stability_anomalies(post_views: list[int]) -> tuple[float, list[str]]:
    """
    Блок 3 (0–1.5)
    Смотрим CV = std/mean по просмотрам последних постов.
    """
    if len(post_views) < 10:
        return 0.0, ["мало постов для оценки ровности (нужно >=10)"]

    mean_v = statistics.mean(post_views)
    if mean_v <= 0:
        return 0.0, ["некорректные просмотры для оценки ровности"]

    std_v = statistics.pstdev(post_views)
    cv = std_v / mean_v  # coefficient of variation

    s = 0.0
    why = [f"ровность просмотров: CV={cv:.2f} (ниже = ровнее)"]

    if cv < 0.08:
        s = 1.5
        why.append("подозрительно ровные просмотры (CV<0.08)")
    elif cv < 0.15:
        s = 1.0
        why.append("очень ровные просмотры (CV<0.15)")
    elif cv < 0.22:
        s = 0.5
        why.append("скорее ровные просмотры (CV<0.22)")

    return s, why


def score_repost_behavior(delta_month: int, forwards_count: int, members: int) -> tuple[float, list[str]]:
    """
    Блок 4 (0–1)
    """
    s = 0.0
    why = []

    if members > 0:
        f_ratio = forwards_count / members
    else:
        f_ratio = 0.0

    if delta_month < 0 and f_ratio > 0.01 and forwards_count > 50:
        s = 1.0
        why.append("много пересылок на фоне падения подписчиков")
    elif f_ratio > 0.01 and forwards_count > 50:
        s = 0.5
        why.append("много пересылок относительно базы")

    return s, why if why else ["нет явных аномалий по пересылкам"]


def score_weak_engagement(er_percent: float, avg_reach: int, members: int) -> tuple[float, list[str]]:
    """
    Блок 5 (0–0.5)
    """
    s = 0.0
    why = []

    if members > 0 and avg_reach > 0:
        reach_ratio = avg_reach / members
    else:
        reach_ratio = 0.0

    if er_percent > 0 and er_percent < 4:
        s = 0.5
        why.append("низкий ER (<4%)")
    elif reach_ratio > 0 and reach_ratio < 0.05:
        s = 0.5
        why.append("низкий reach/subs (<5%)")

    return s, why if why else ["нет явных проблем по ER/reach"]


def fake_score_human(
    *,
    members: int,
    avg_reach: int,
    er_percent: float,
    forwards_count: int,
    subs_series: list[dict],
    post_views: list[int],
) -> tuple[int, dict]:
    """
    Возвращает:
      - итоговый скор 1..10
      - breakdown (по блокам + объяснения)
    """
    deltas = compute_deltas_from_series(subs_series)

    b1, why1 = score_subscriber_dynamics(
        deltas["delta_day"], deltas["delta_week"], deltas["pct_month"], deltas["sawtooth"]
    )
    b2, why2 = score_reach_vs_dynamics(
        deltas["delta_week"], deltas["delta_month"], er_percent, avg_reach, members
    )
    b3, why3 = score_stability_anomalies(post_views)
    b4, why4 = score_repost_behavior(deltas["delta_month"], forwards_count, members)
    b5, why5 = score_weak_engagement(er_percent, avg_reach, members)

    total = b1 + b2 + b3 + b4 + b5
    total = min(total, 10.0)

    # чтобы было похоже на "риск": минимум 1
    total_int = max(1, int(round(total)))

    breakdown = {
        "total": total,
        "blocks": {
            "subs_dynamics": {"score": b1, "max": 4.0, "why": why1, "deltas": deltas},
            "reach_vs_dynamics": {"score": b2, "max": 3.0, "why": why2},
            "stability": {"score": b3, "max": 1.5, "why": why3, "posts_used": len(post_views)},
            "reposts": {"score": b4, "max": 1.0, "why": why4},
            "weak_engagement": {"score": b5, "max": 0.5, "why": why5},
        },
    }
    return total_int, breakdown


# -------------------------
# Telegram handlers
# -------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Кинь ссылку или @username Telegram-канала — пришлю ER/охват + риск накрутки (1–10) и разбор по факторам."
    )


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "OK ✅\n"
        f"BASE_URL: {TELEMETR_BASE_URL}\n"
        f"TELEMETR_TOKEN set: {'YES' if bool(TELEMETR_TOKEN) else 'NO'}"
    )


async def cmd_weights(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚙️ Логика скоринга (1–10)\n\n"
        "Блок 1 — Динамика подписчиков (0–4):\n"
        " • −500+ за день → +2\n"
        " • −3000+ за неделю → +2\n"
        " • −10%+ за месяц → +2\n"
        " • «пила» (крупный рост и падение) → +1 (cap 4)\n\n"
        "Блок 2 — Подписчики vs охваты/ER (0–3):\n"
        " • падение за месяц + ER>=20% → +3\n"
        " • падение за месяц + ER>=15% → +2\n"
        " • падение за неделю + ER>=15% → +1\n"
        " • падение + reach/subs>=20% → минимум +2\n\n"
        "Блок 3 — Ровность просмотров постов (0–1.5):\n"
        " • CV<0.08 → +1.5\n"
        " • CV<0.15 → +1.0\n"
        " • CV<0.22 → +0.5\n\n"
        "Блок 4 — Пересылки (0–1):\n"
        " • много пересылок на фоне падения → +1\n"
        " • просто много пересылок относительно базы → +0.5\n\n"
        "Блок 5 — слабая вовлечённость (0–0.5):\n"
        " • ER<4% → +0.5\n"
        " • reach/subs<5% → +0.5\n"
    )


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
        await update.message.reply_text("Не распознала канал 😕 Пришли https://t.me/username или @username.")
        return

    try:
        stat = fetch_stat(channel_id)

        title = stat.get("title") or channel_id
        username = stat.get("username") or channel_id

        members = int(stat.get("participants_count") or 0)
        avg_reach = int(stat.get("avg_post_reach") or 0)
        er_percent = float(stat.get("err_percent") or 0.0)

        mentions_count = int(stat.get("mentions_count") or 0)
        forwards_count = int(stat.get("forwards_count") or 0)
        ci_index = stat.get("ci_index", 0)
        scoring_rate = stat.get("scoring_rate", 0)

        # Доп.данные для скоринга
        subs_series = fetch_subscribers_series(channel_id, group="day", days=31)
        post_views = fetch_posts_views(channel_id, limit=20)

        risk, breakdown = fake_score_human(
            members=members,
            avg_reach=avg_reach,
            er_percent=er_percent,
            forwards_count=forwards_count,
            subs_series=subs_series,
            post_views=post_views,
        )

        # Красивый разбор
        b = breakdown["blocks"]
        d = b["subs_dynamics"]["deltas"]
        delta_day = d.get("delta_day", 0)
        delta_week = d.get("delta_week", 0)
        delta_month = d.get("delta_month", 0)
        pct_month = d.get("pct_month", 0.0)

        def fmt_signed(x: int) -> str:
            return f"{x:+d}"

        details = (
            "🧠 Разбор (почему такой риск):\n"
            f"1) Подписчики (0–4): {b['subs_dynamics']['score']}/{b['subs_dynamics']['max']}  "
            f"(день {fmt_signed(delta_day)}, неделя {fmt_signed(delta_week)}, месяц {fmt_signed(delta_month)} / {pct_month*100:.1f}%)\n"
            f"   • " + ("; ".join(b["subs_dynamics"]["why"]) if b["subs_dynamics"]["why"] else "нет сильных сигналов") + "\n\n"
            f"2) Подписчики vs ER/охват (0–3): {b['reach_vs_dynamics']['score']}/{b['reach_vs_dynamics']['max']}\n"
            f"   • " + ("; ".join(b["reach_vs_dynamics"]["why"]) if b["reach_vs_dynamics"]["why"] else "нет сильных сигналов") + "\n\n"
            f"3) Ровность просмотров (0–1.5): {b['stability']['score']}/{b['stability']['max']} (постов: {b['stability']['posts_used']})\n"
            f"   • " + ("; ".join(b["stability"]["why"]) if b["stability"]["why"] else "нет сильных сигналов") + "\n\n"
            f"4) Пересылки (0–1): {b['reposts']['score']}/{b['reposts']['max']}\n"
            f"   • " + ("; ".join(b["reposts"]["why"]) if b["reposts"]["why"] else "нет сильных сигналов") + "\n\n"
            f"5) ER/reach (0–0.5): {b['weak_engagement']['score']}/{b['weak_engagement']['max']}\n"
            f"   • " + ("; ".join(b["weak_engagement"]["why"]) if b["weak_engagement"]["why"] else "нет сильных сигналов") + "\n"
        )

        await update.message.reply_text(
            f"📊 {title}\n"
            f"@{username}\n\n"
            f"👥 Подписчики: {members}\n"
            f"👀 Средний охват поста: {avg_reach}\n"
            f"📈 ER: {er_percent:.2f}%\n"
            f"🔗 CI: {ci_index}\n"
            f"⭐️ Telemetr rating: {scoring_rate}\n"
            f"📣 Упоминания: {mentions_count}\n"
            f"↪️ Пересылки: {forwards_count}\n\n"
            f"⚠️ Вероятность накрутки: {risk}/10\n\n"
            f"{details}\n"
            f"Команда: /weights — пороги и веса"
        )

    except requests.HTTPError as e:
        await update.message.reply_text(f"Ошибка Telemetr API:\n{e}")
    except Exception as e:
        await update.message.reply_text(f"Неожиданная ошибка:\n{e}")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("weights", cmd_weights))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling()


if __name__ == "__main__":
    main()
