import os
import re
import math
import statistics
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
# Env
# ----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN", "").strip()

# Telemetr.me API
TELEMETR_BASE_URL = os.getenv("TELEMETR_BASE_URL", "https://api.telemetr.me").rstrip("/")

HEADERS = {
    "Authorization": f"Bearer {TELEMETR_TOKEN}",
    "Accept": "application/json",
}

TIMEOUT = 25


# ----------------------------
# Helpers: parsing input
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

    t = text.strip()
    t = t.strip(" \n\t<>[](){}.,;")

    # t.me link
    m = re.search(r"(?:https?://)?t\.me/([A-Za-z0-9_]{5,})", t)
    if m:
        return m.group(1)

    # @username
    if t.startswith("@"):
        u = t[1:]
        if re.fullmatch(r"[A-Za-z0-9_]{5,}", u):
            return u

    # bare username
    if re.fullmatch(r"[A-Za-z0-9_]{5,}", t):
        return t

    return None


# ----------------------------
# HTTP wrapper
# ----------------------------
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


def telemetr_post(path: str, payload: dict):
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


# ----------------------------
# Telemetr suspect flag (event-based)
# ----------------------------

# Слова для “служебных” сообщений/полей (не about!), если API отдаст
SUSPECT_KEYWORDS = [
    "накрут", "подозр", "фейк", "fake", "fraud", "scam",
    "artificial", "manipulat", "botovod", "боты",
    "просмотр", "view", "подписчик", "subscriber",
]

def _contains_suspect_text(s: str) -> bool:
    s = (s or "").lower()
    return any(k in s for k in SUSPECT_KEYWORDS)


def detect_telemetr_suspect_flag(resp: dict) -> tuple[bool, str]:
    """
    Возвращает (True/False, reason).

    ВАЖНО:
    - НЕ сканируем resp['about'] и подобные “описания” (там реклама/РКН/контакты → фолсы)
    - Ищем только:
      1) явные boolean-флаги
      2) “служебные” поля модерации/предупреждений/статусов (если они есть)
    """
    if not isinstance(resp, dict):
        return False, ""

    # 1) Явные булевые флаги (самое надёжное)
    BOOL_FLAGS = (
        "is_badlisted", "badlisted",
        "is_suspicious", "suspected",
        "is_scam", "scam",
        "is_fraud", "fraud",
        "is_fake", "fake",
        "is_spam", "spam",
    )
    for key in BOOL_FLAGS:
        if resp.get(key) is True:
            return True, f"Telemetr flag: {key}=true"

    # 2) “служебные” поля — НЕ about/description профиля.
    # Иногда API кладёт предупреждения в status/message/reason/moderation/flags etc.
    SERVICE_KEYS = (
        "status", "message", "reason", "warning", "warnings",
        "moderation", "flags", "labels", "mark", "marks",
        "badge", "badges", "note", "notes",
    )
    for k in SERVICE_KEYS:
        v = resp.get(k)
        if isinstance(v, str) and _contains_suspect_text(v):
            return True, f"Telemetr {k}: {v}"

        if isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, str) and _contains_suspect_text(item):
                    return True, f"Telemetr {k}[{i}]: {item}"
                if isinstance(item, dict):
                    # сначала булевые
                    for bf in BOOL_FLAGS:
                        if item.get(bf) is True:
                            return True, f"Telemetr {k}[{i}]: {bf}=true"
                    # затем текстовые типовые поля
                    for tk in ("type", "message", "text", "reason", "description", "code", "name", "title"):
                        tv = item.get(tk)
                        if isinstance(tv, str) and _contains_suspect_text(tv):
                            return True, f"Telemetr {k}[{i}].{tk}: {tv}"

        if isinstance(v, dict):
            for bf in BOOL_FLAGS:
                if v.get(bf) is True:
                    return True, f"Telemetr {k}: {bf}=true"
            for tk in ("type", "message", "text", "reason", "description", "code", "name", "title"):
                tv = v.get(tk)
                if isinstance(tv, str) and _contains_suspect_text(tv):
                    return True, f"Telemetr {k}.{tk}: {tv}"

    return False, ""


# ----------------------------
# Scoring logic (human formula)
# ----------------------------
WEIGHTS = {
    "A_subscribers_dynamics": 4.0,     # главный блок
    "B_reach_vs_subs": 3.0,
    "C_views_smoothness": 1.5,
    "D_forwards": 1.0,
    "E_er_sanity": 0.5,
}
EVENT_FLOOR = 8  # если Telemetr пометил, риск минимум 8/10


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def safe_int(x, default=0) -> int:
    try:
        return int(float(x))
    except Exception:
        return default


def score_block_A_subs(deltas: dict, members: int) -> tuple[float, list[str]]:
    """
    deltas = {"day": int, "week": int, "month": int, "day_pct": float, ...}
    Логика: считаем % + минимальный абсолют.
    """
    pts = 0.0
    reasons: list[str] = []

    day = deltas.get("day", 0)
    week = deltas.get("week", 0)
    month = deltas.get("month", 0)

    day_pct = deltas.get("day_pct", 0.0)
    week_pct = deltas.get("week_pct", 0.0)
    month_pct = deltas.get("month_pct", 0.0)

    # День (как ты задала)
    if day_pct <= -0.60 and abs(day) >= 500:
        pts += 3.0
        reasons.append(f"день: {day} ({day_pct:.2f}%) ≤ -0.60% и abs≥500 → +3")
    elif day_pct <= -0.35 and abs(day) >= 300:
        pts += 2.0
        reasons.append(f"день: {day} ({day_pct:.2f}%) ≤ -0.35% и abs≥300 → +2")

    # Неделя (примерно как ты накидала, но нормализуем)
    # для больших каналов могут быть большие абсолюты, поэтому добавляем % и abs
    if week_pct <= -1.50 and abs(week) >= 3000:
        pts += 3.0
        reasons.append(f"неделя: {week} ({week_pct:.2f}%) ≤ -1.50% и abs≥3000 → +3")
    elif week_pct <= -1.00 and abs(week) >= 2000:
        pts += 2.0
        reasons.append(f"неделя: {week} ({week_pct:.2f}%) ≤ -1.00% и abs≥2000 → +2")

    # Месяц (для накрутки часто видно “пилообразность” / откаты)
    if month_pct <= -4.0 and abs(month) >= 8000:
        pts += 3.0
        reasons.append(f"месяц: {month} ({month_pct:.2f}%) ≤ -4.0% и abs≥8000 → +3")
    elif month_pct <= -2.5 and abs(month) >= 5000:
        pts += 2.0
        reasons.append(f"месяц: {month} ({month_pct:.2f}%) ≤ -2.5% и abs≥5000 → +2")

    # Нормируем в 0..4
    pts = clamp(pts, 0.0, WEIGHTS["A_subscribers_dynamics"])
    if not reasons:
        reasons.append("нет сильных сигналов")
    return pts, reasons


def score_block_B_reach_vs_subs(avg_reach: int, members: int, err_percent: float) -> tuple[float, list[str]]:
    """
    Сравнение охвата с подписчиками + sanity по ER.
    Низкий reach/members при приличных подписчиках может быть сигналом.
    """
    pts = 0.0
    reasons = []

    if members <= 0 or avg_reach <= 0:
        return 0.0, ["недостаточно данных"]

    ratio = avg_reach / members  # доля охвата на пост
    # очень низко
    if ratio < 0.03:
        pts += 3.0
        reasons.append(f"охват/подписчики={ratio:.3f} < 0.03 → +3")
    elif ratio < 0.06:
        pts += 2.0
        reasons.append(f"охват/подписчики={ratio:.3f} < 0.06 → +2")
    elif ratio < 0.08:
        pts += 1.0
        reasons.append(f"охват/подписчики={ratio:.3f} < 0.08 → +1")
    else:
        reasons.append(f"охват/подписчики={ratio:.3f} (норм)")

    # ER sanity: слишком низкий ER для размера тоже бывает сигналом (тут мягко)
    if err_percent < 4.0:
        pts += 1.0
        reasons.append(f"ER={err_percent:.2f}% < 4% → +1")
    elif err_percent < 6.0:
        pts += 0.5
        reasons.append(f"ER={err_percent:.2f}% < 6% → +0.5")

    pts = clamp(pts, 0.0, WEIGHTS["B_reach_vs_subs"])
    return pts, reasons


def score_block_C_views_smoothness(post_views: list[int]) -> tuple[float, list[str]]:
    """
    “Ровность” просмотров по последним постам: слишком ровно тоже подозрительно.
    Используем CV (коэф. вариации = stdev/mean).
    """
    if not post_views or len(post_views) < 8:
        return 0.0, ["мало постов для оценки ровности"]

    mean_v = statistics.mean(post_views)
    if mean_v <= 0:
        return 0.0, ["нет корректных значений просмотров"]

    stdev_v = statistics.pstdev(post_views)
    cv = stdev_v / mean_v

    pts = 0.0
    reasons = [f"ровность просмотров: CV={cv:.2f} (ниже = ровнее)"]

    # слишком ровно → +1.5
    if cv < 0.12:
        pts = 1.5
        reasons.append("слишком ровно (CV < 0.12) → +1.5")
    elif cv < 0.18:
        pts = 1.0
        reasons.append("подозрительно ровно (CV < 0.18) → +1.0")
    elif cv < 0.25:
        pts = 0.5
        reasons.append("слегка ровно (CV < 0.25) → +0.5")
    else:
        reasons.append("нет сигнала по ровности")

    pts = clamp(pts, 0.0, WEIGHTS["C_views_smoothness"])
    return pts, reasons


def score_block_D_forwards(forwards: int, members: int) -> tuple[float, list[str]]:
    """
    Пересылки/упоминания: очень низко при большом канале может быть странно.
    """
    pts = 0.0
    reasons = []

    if members <= 0:
        return 0.0, ["нет данных о подписчиках"]

    # простая эвристика: пересылки крайне низкие
    if forwards <= 5 and members >= 50000:
        pts = 1.0
        reasons.append(f"пересылок={forwards} при {members} подписчиках → +1")
    else:
        reasons.append("нет явных аномалий по пересылкам")

    pts = clamp(pts, 0.0, WEIGHTS["D_forwards"])
    return pts, reasons


def score_block_E_er_sanity(err_percent: float) -> tuple[float, list[str]]:
    """
    Мягкий блок: если ER совсем “неживой”.
    """
    pts = 0.0
    reasons = []

    if err_percent < 3.0:
        pts = 0.5
        reasons.append(f"ER={err_percent:.2f}% очень низкий → +0.5")
    else:
        reasons.append("нет явных проблем по ER/reach")

    pts = clamp(pts, 0.0, WEIGHTS["E_er_sanity"])
    return pts, reasons


def compute_risk_breakdown(
    resp: dict,
    members: int,
    avg_reach: int,
    err_percent: float,
    forwards: int,
    mentions: int,
    post_views: list[int],
    deltas: dict,
) -> tuple[int, str]:
    """
    Возвращает (risk_1_10, explanation_text).
    """
    # Event-based: Telemetr пометил → минимум 8
    suspect, reason = detect_telemetr_suspect_flag(resp)

    parts = []
    score = 0.0

    A_pts, A_reason = score_block_A_subs(deltas, members)
    B_pts, B_reason = score_block_B_reach_vs_subs(avg_reach, members, err_percent)
    C_pts, C_reason = score_block_C_views_smoothness(post_views)
    D_pts, D_reason = score_block_D_forwards(forwards, members)
    E_pts, E_reason = score_block_E_er_sanity(err_percent)

    score += A_pts + B_pts + C_pts + D_pts + E_pts

    # переводим score (0..10) в risk (1..10) с минимумом 1
    # score уже ограничен суммой весов (=10)
    risk = int(round(clamp(score, 0.0, 10.0)))
    risk = max(risk, 1)

    # event-floor
    if suspect:
        risk = max(risk, EVENT_FLOOR)

    # explanation
    parts.append("🧠 Разбор (почему такой риск):")

    parts.append(
        f"1) Подписчики (0–{WEIGHTS['A_subscribers_dynamics']:.0f}): {A_pts:.1f}/{WEIGHTS['A_subscribers_dynamics']:.1f}  "
        f"(день {deltas.get('day',0):+}, неделя {deltas.get('week',0):+}, месяц {deltas.get('month',0):+})"
    )
    for r in A_reason:
        parts.append(f"   • {r}")

    parts.append(
        f"\n2) Подписчики vs ER/охват (0–{WEIGHTS['B_reach_vs_subs']:.0f}): {B_pts:.1f}/{WEIGHTS['B_reach_vs_subs']:.1f}"
    )
    for r in B_reason:
        parts.append(f"   • {r}")

    parts.append(
        f"\n3) Ровность просмотров (0–{WEIGHTS['C_views_smoothness']:.1f}): {C_pts:.1f}/{WEIGHTS['C_views_smoothness']:.1f} "
        f"(постов: {len(post_views)})"
    )
    for r in C_reason:
        parts.append(f"   • {r}")

    parts.append(
        f"\n4) Пересылки (0–{WEIGHTS['D_forwards']:.0f}): {D_pts:.1f}/{WEIGHTS['D_forwards']:.1f}"
    )
    for r in D_reason:
        parts.append(f"   • {r}")

    parts.append(
        f"\n5) ER/reach (0–{WEIGHTS['E_er_sanity']:.1f}): {E_pts:.1f}/{WEIGHTS['E_er_sanity']:.1f}"
    )
    for r in E_reason:
        parts.append(f"   • {r}")

    if suspect:
        parts.append(
            f"\n🚨 Telemetr: канал помечен как подозрительный → риск минимум {EVENT_FLOOR}/10"
        )
        parts.append(f"Источник: {reason}")

    parts.append("\nКоманда: /weights — пороги и веса")

    return risk, "\n".join(parts)


# ----------------------------
# Data fetchers
# ----------------------------
def fetch_channel_stat(channel_id: str) -> dict:
    # GET /channels/stat?channelId=...
    data = telemetr_get("/channels/stat", {"channelId": channel_id})
    return data.get("response", {}) if isinstance(data, dict) else {}


def fetch_subscribers_series(channel_id: str, group: str = "day") -> list[dict]:
    """
    GET /channels/subscribers?channelId=...&group=day&startDate=...&endDate=...
    Возвращает список точек (может быть пустым).
    """
    now = datetime.utcnow()
    if group == "day":
        start = now - timedelta(days=30)
    elif group == "week":
        start = now - timedelta(days=90)
    else:
        start = now - timedelta(days=180)

    start_str = start.strftime("%Y-%m-%d 00:00:00")
    end_str = now.strftime("%Y-%m-%d 23:59:59")

    data = telemetr_get(
        "/channels/subscribers",
        {
            "channelId": channel_id,
            "group": "day",  # по доке default day, и нам проще
            "startDate": start_str,
            "endDate": end_str,
        },
    )

    resp = data.get("response", [])
    if isinstance(resp, list):
        return resp
    return []


def compute_sub_deltas(series: list[dict], members_now: int) -> dict:
    """
    На вход: series = [{'date': 'YYYY-MM-DD', 'subscribers': 123}, ...] (примерно)
    Реальный ключ может отличаться, поэтому аккуратно.
    Возвращает day/week/month дельты и проценты.
    """
    # series может быть в форматах:
    # - {'date': 'YYYY-MM-DD', 'value': 123}
    # - {'date': 'YYYY-MM-DD', 'subscribers': 123}
    # - {'date': 'YYYY-MM-DD', 'count': 123}
    points = []
    for p in series:
        if not isinstance(p, dict):
            continue
        d = p.get("date")
        v = p.get("value")
        if v is None:
            v = p.get("subscribers")
        if v is None:
            v = p.get("count")
        if d is None or v is None:
            continue
        points.append((str(d), safe_int(v, 0)))

    points.sort(key=lambda x: x[0])
    if len(points) < 2:
        return {"day": 0, "week": 0, "month": 0, "day_pct": 0.0, "week_pct": 0.0, "month_pct": 0.0}

    def value_at_days_ago(days: int) -> int | None:
        # ищем ближайшую точку <= target_date
        target = datetime.utcnow().date() - timedelta(days=days)
        best = None
        for ds, val in points:
            try:
                dd = datetime.fromisoformat(ds.split(" ")[0]).date()
            except Exception:
                continue
            if dd <= target:
                best = val
        return best

    # baseline: если members_now не совпадает с последней точкой, используем members_now как “текущее”
    current = members_now if members_now > 0 else points[-1][1]

    d1 = value_at_days_ago(1)
    d7 = value_at_days_ago(7)
    d30 = value_at_days_ago(30)

    def delta_and_pct(prev: int | None) -> tuple[int, float]:
        if prev is None or prev <= 0:
            return 0, 0.0
        delta = current - prev
        pct = (delta / prev) * 100.0
        return delta, pct

    day_delta, day_pct = delta_and_pct(d1)
    week_delta, week_pct = delta_and_pct(d7)
    month_delta, month_pct = delta_and_pct(d30)

    return {
        "day": day_delta,
        "week": week_delta,
        "month": month_delta,
        "day_pct": day_pct,
        "week_pct": week_pct,
        "month_pct": month_pct,
    }


def fetch_last_posts_views(channel_id: str, limit: int = 20) -> list[int]:
    """
    GET /channels/posts?channelId=...&limit=...&offset=0
    Достаём просмотры из items[*].stats.views (по примеру в доке).
    """
    data = telemetr_get("/channels/posts", {"channelId": channel_id, "limit": limit, "offset": 0})
    resp = data.get("response", {})
    items = []
    if isinstance(resp, dict):
        items = resp.get("items", [])
    if not isinstance(items, list):
        return []

    views = []
    for it in items:
        if not isinstance(it, dict):
            continue
        stats = it.get("stats", {})
        if isinstance(stats, dict):
            v = stats.get("views")
            if v is not None:
                views.append(safe_int(v, 0))
    # убираем нули, если они мусорные
    views = [v for v in views if v > 0]
    return views


# ----------------------------
# Telegram handlers
# ----------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Кинь ссылку или @username Telegram-канала — я пришлю метрики и скоринг накрутки (1–10).\n"
        "Команда: /weights — веса и пороги."
    )


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "OK ✅\n"
        f"BASE_URL: {TELEMETR_BASE_URL}\n"
        f"TELEMETR_TOKEN set: {'YES' if bool(TELEMETR_TOKEN) else 'NO'}"
    )


async def cmd_weights(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "⚙️ Веса и пороги (текущая версия)\n\n"
        f"Event-based floor (Telemetr флаг): минимум {EVENT_FLOOR}/10\n\n"
        "A) Динамика подписчиков (0–4):\n"
        " • день:  Δday% ≤ −0.35% и abs≥300 → +2\n"
        " • день:  Δday% ≤ −0.60% и abs≥500 → +3\n"
        " • неделя: Δweek% ≤ −1.00% и abs≥2000 → +2\n"
        " • неделя: Δweek% ≤ −1.50% и abs≥3000 → +3\n"
        " • месяц:  Δmonth% ≤ −2.5% и abs≥5000 → +2\n"
        " • месяц:  Δmonth% ≤ −4.0% и abs≥8000 → +3\n\n"
        "B) Охват/подписчики + ER (0–3):\n"
        " • reach/subs < 0.03 → +3; <0.06 → +2; <0.08 → +1\n"
        " • ER < 4% → +1; ER < 6% → +0.5\n\n"
        "C) Ровность просмотров (0–1.5):\n"
        " • CV < 0.12 → +1.5; <0.18 → +1.0; <0.25 → +0.5\n\n"
        "D) Пересылки (0–1):\n"
        " • forwards ≤ 5 при subs ≥ 50k → +1\n\n"
        "E) ER sanity (0–0.5):\n"
        " • ER < 3% → +0.5\n"
    )
    await update.message.reply_text(txt)


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
        resp = fetch_channel_stat(channel_id)

        title = resp.get("title") or channel_id
        username = resp.get("username") or channel_id

        members = safe_int(resp.get("participants_count"), 0)
        avg_reach = safe_int(resp.get("avg_post_reach"), 0)
        err_percent = safe_float(resp.get("err_percent"), 0.0)

        ci_index = resp.get("ci_index", 0)
        scoring_rate = resp.get("scoring_rate", 0)

        mentions_count = safe_int(resp.get("mentions_count"), 0)
        forwards_count = safe_int(resp.get("forwards_count"), 0)

        # Подписчики: series + дельты
        series = fetch_subscribers_series(channel_id)
        deltas = compute_sub_deltas(series, members)

        # Просмотры по последним постам
        post_views = fetch_last_posts_views(channel_id, limit=20)

        # Risk + breakdown
        risk, breakdown = compute_risk_breakdown(
            resp=resp,
            members=members,
            avg_reach=avg_reach,
            err_percent=err_percent,
            forwards=forwards_count,
            mentions=mentions_count,
            post_views=post_views,
            deltas=deltas,
        )

        await update.message.reply_text(
            f"📊 {title}\n"
            f"@{username}\n\n"
            f"👥 Подписчики: {members}\n"
            f"👀 Средний охват поста: {avg_reach}\n"
            f"📈 ER: {err_percent:.2f}%\n"
            f"🔗 CI: {ci_index}\n"
            f"⭐️ Telemetr rating: {scoring_rate}\n"
            f"📣 Упоминания: {mentions_count}\n"
            f"↪️ Пересылки: {forwards_count}\n\n"
            f"⚠️ Вероятность накрутки: {risk}/10\n\n"
            f"{breakdown}"
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
