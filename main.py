import os
import re
import math
import asyncio
import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
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

# Telemetr.me API (НЕ telemetr.io)
TELEMETR_BASE_URL = os.getenv("TELEMETR_BASE_URL", "https://api.telemetr.me").rstrip("/")

HEADERS = {
    "Authorization": f"Bearer {TELEMETR_TOKEN}",
    "Accept": "application/json",
}

TIMEOUT = 25

# =========================
# Helpers
# =========================
def extract_channel_id(text: str) -> Optional[str]:
    """
    Accepts:
      - https://t.me/username
      - t.me/username
      - @username
      - username
      - joinchat / +AAAA... (optional; not used now)
    Returns channelId for Telemetr.me.
    """
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
    url = f"{TELEMETR_BASE_URL}{path}"
    r = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)

    if not r.ok:
        try:
            body = r.json()
        except Exception:
            body = r.text
        raise requests.HTTPError(
            f"{r.status_code} {r.reason} for url: {r.url}\nResponse: {body}",
            response=r
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
            response=r
        )
    return r.json()


def _as_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _as_int(x, default=0) -> int:
    try:
        return int(float(x))
    except Exception:
        return default


def _fmt_signed(n: int) -> str:
    if n > 0:
        return f"+{n}"
    return str(n)


def _pct(num: float) -> float:
    return num * 100.0


def now_utc() -> dt.datetime:
    return dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)


def fmt_telemetr_date(d: dt.datetime) -> str:
    # API docs show: "YYYY-MM-DD HH:MM:SS"
    return d.strftime("%Y-%m-%d %H:%M:%S")


# =========================
# Event-based suspect flag
# =========================
# ВАЖНО: избегаем ложных срабатываний по "about" (там часто реклама/боты/РКН и т.д.)
# Ищем только "служебные" поля и явные булевы флаги.
BOOL_FLAGS = (
    "is_badlisted", "badlisted",
    "is_suspicious", "suspected",
    "is_scam", "scam",
    "is_fraud", "fraud",
)

SAFE_TEXT_KEYS = (
    # только те поля, которые с высокой вероятностью реально про модерацию/предупреждение
    "warning", "warnings",
    "moderation", "moderation_message",
    "restriction", "restrictions",
    "status", "code", "reason",
)

SUSPECT_KEYWORDS = (
    "накрут", "подозр", "фейк", "мошен", "скам", "fraud", "scam", "fake", "manipulat",
    "просмотр", "view", "подписчик", "subscriber",
)


def _contains_suspect_text(s: str) -> bool:
    s = (s or "").lower()
    return any(k in s for k in SUSPECT_KEYWORDS)


def detect_telemetr_suspect_flag(resp: dict) -> Tuple[bool, str]:
    """
    Returns (flag, reason).
    We only look at:
      - explicit boolean flags in resp or nested dict/list items
      - moderation/restriction-like fields (safe keys)
    """
    if not isinstance(resp, dict):
        return (False, "")

    # 1) explicit boolean flags on top-level
    for k in BOOL_FLAGS:
        if resp.get(k) is True:
            return (True, f"Telemetr flag: {k}=true")

    # 2) scan nested values but carefully (dict/list), still prefer explicit flags
    for key, v in resp.items():
        # explicit nested dict
        if isinstance(v, dict):
            for k in BOOL_FLAGS:
                if v.get(k) is True:
                    return (True, f"Telemetr flag: {key}.{k}=true")

            # safe text keys inside nested dict
            for tk in SAFE_TEXT_KEYS:
                tv = v.get(tk)
                if isinstance(tv, str) and _contains_suspect_text(tv):
                    return (True, f"Telemetr note: {key}.{tk}: {tv}")

        # list of items
        if isinstance(v, list):
            for i, item in enumerate(v[:50]):
                if isinstance(item, dict):
                    for k in BOOL_FLAGS:
                        if item.get(k) is True:
                            return (True, f"Telemetr flag: {key}[{i}].{k}=true")
                    for tk in SAFE_TEXT_KEYS:
                        tv = item.get(tk)
                        if isinstance(tv, str) and _contains_suspect_text(tv):
                            return (True, f"Telemetr note: {key}[{i}].{tk}: {tv}")

        # safe text keys at top level
        if key in SAFE_TEXT_KEYS and isinstance(v, str) and _contains_suspect_text(v):
            return (True, f"Telemetr note: {key}: {v}")

    return (False, "")


# =========================
# Scoring configuration
# =========================
@dataclass
class ScorePart:
    name: str
    max_points: float
    got: float
    details: List[str]


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def compute_cv(values: List[float]) -> Optional[float]:
    if not values or len(values) < 3:
        return None
    m = sum(values) / len(values)
    if m <= 0:
        return None
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    sd = math.sqrt(var)
    return sd / m


def score_from_subs_dynamics(
    members: int,
    day_delta: int,
    week_delta: int,
    month_delta: int,
) -> ScorePart:
    """
    Главный блок (0–4): динамика подписчиков.
    Считаем проценты + минимальный абсолют, чтобы не ловить шум.
    """
    max_points = 4.0
    got = 0.0
    details = []

    def add(points: float, line: str):
        nonlocal got
        got += points
        details.append(f"• {line} → +{points:g}")

    def check_delta(period: str, delta: int, abs_min: int, pct_min: float, points: float):
        if members <= 0:
            return
        pct = abs(delta) / members
        if abs(delta) >= abs_min and pct >= pct_min:
            add(points, f"{period}: Δ={_fmt_signed(delta)} ({_pct(pct):.2f}%), порог {abs_min}+ и { _pct(pct_min):.2f}%+")

    # DAY thresholds (как ты предложила)
    # если Δday% ≤ −0.35% и abs(Δday) ≥ 300 → +2
    # если Δday% ≤ −0.60% и abs(Δday) ≥ 500 → +3
    # (берём abs по модулю, т.к. накрутка может быть и ростом, и списаниями)
    check_delta("День", day_delta, 300, 0.0035, 2.0)
    check_delta("День", day_delta, 500, 0.0060, 3.0)

    # WEEK (рабочие пороги, можно потом покрутить)
    check_delta("Неделя", week_delta, 3000, 0.0150, 2.0)
    check_delta("Неделя", week_delta, 5000, 0.0250, 3.0)

    # MONTH
    check_delta("Месяц", month_delta, 12000, 0.0500, 2.0)
    check_delta("Месяц", month_delta, 20000, 0.0800, 3.0)

    got = clamp(got, 0.0, max_points)

    if not details:
        details.append("• нет сильных сигналов (порогов не достигли)")

    summary = f"(день {_fmt_signed(day_delta)}, неделя {_fmt_signed(week_delta)}, месяц {_fmt_signed(month_delta)})"
    details.insert(0, summary)

    return ScorePart(
        name="1) Динамика подписчиков (главный)",
        max_points=max_points,
        got=got,
        details=details,
    )


def score_reach_vs_subs(members: int, avg_reach: int) -> ScorePart:
    """
    0–3: охват/подписчики
    """
    max_points = 3.0
    got = 0.0
    details = []

    if members <= 0 or avg_reach <= 0:
        return ScorePart(
            name="2) Подписчики vs охват",
            max_points=max_points,
            got=0.0,
            details=["• нет данных для сравнения (members/avg_reach пустые)"],
        )

    ratio = avg_reach / members
    details.append(f"• охват/подписчики = {ratio:.3f} ({_pct(ratio):.1f}%)")

    # мягкие эвристики
    if ratio < 0.03:
        got += 3.0
        details.append("• очень низкий охват для такой базы → +3")
    elif ratio < 0.05:
        got += 2.0
        details.append("• низковатый охват → +2")
    elif ratio < 0.08:
        got += 1.0
        details.append("• немного низкий охват → +1")
    else:
        details.append("• выглядит ок")

    got = clamp(got, 0.0, max_points)
    return ScorePart(
        name="2) Подписчики vs охват",
        max_points=max_points,
        got=got,
        details=details,
    )


def score_er(err_percent: float) -> ScorePart:
    """
    0–1: ER как слабый сигнал (сам по себе не доказательство)
    """
    max_points = 1.0
    got = 0.0
    details = [f"• ER = {err_percent:.2f}%"]

    # Здесь специально слабые веса: ER легко “нарисовать”.
    if err_percent < 3.0:
        got += 1.0
        details.append("• слишком низкий ER → +1")
    else:
        details.append("• по ER явных проблем нет")

    got = clamp(got, 0.0, max_points)
    return ScorePart(
        name="3) ER (слабый сигнал)",
        max_points=max_points,
        got=got,
        details=details,
    )


def score_forward_mentions(forwards: int, mentions: int, members: int) -> ScorePart:
    """
    0–2: пересылки/упоминания — косвенный сигнал “живости”
    """
    max_points = 2.0
    got = 0.0
    details = [
        f"• пересылки: {forwards}",
        f"• упоминания: {mentions}",
    ]

    if members <= 0:
        details.append("• нет данных по подписчикам → пропуск")
        return ScorePart(
            name="4) Упоминания/пересылки",
            max_points=max_points,
            got=0.0,
            details=details,
        )

    # Очень грубо: если канал большой, а пересылок/упоминаний почти нет — подозрительно.
    if members >= 100_000 and (forwards + mentions) < 50:
        got += 2.0
        details.append("• для большого канала слишком мало сигналов распространения → +2")
    elif members >= 50_000 and (forwards + mentions) < 20:
        got += 1.5
        details.append("• мало сигналов распространения → +1.5")
    else:
        details.append("• выглядит ок")

    got = clamp(got, 0.0, max_points)
    return ScorePart(
        name="4) Упоминания/пересылки",
        max_points=max_points,
        got=got,
        details=details,
    )


def total_score_to_risk(total_points: float) -> int:
    """
    Переводим суммарные баллы в шкалу 1–10.
    """
    # максимум примерно 10, но у нас сейчас около 10
    risk = int(round(clamp(1 + total_points, 1, 10)))
    return risk


def build_explanation(parts: List[ScorePart]) -> str:
    lines: List[str] = []
    for p in parts:
        lines.append(f"{p.name} ({p.got:.1f}/{p.max_points:.1f})")
        for d in p.details:
            lines.append(f"   {d}")
        lines.append("")
    return "\n".join(lines).strip()


def weights_text() -> str:
    return (
        "⚙️ Пороги и веса (текущая версия)\n\n"
        "1) Динамика подписчиков (0–4)\n"
        "   День: abs>=300 и >=0.35% → +2; abs>=500 и >=0.60% → +3\n"
        "   Неделя: abs>=3000 и >=1.50% → +2; abs>=5000 и >=2.50% → +3\n"
        "   Месяц: abs>=12000 и >=5.00% → +2; abs>=20000 и >=8.00% → +3\n\n"
        "2) Подписчики vs охват (0–3)\n"
        "   reach/subs <3% → +3; <5% → +2; <8% → +1\n\n"
        "3) ER (слабый сигнал, 0–1)\n"
        "   ER <3% → +1\n\n"
        "4) Упоминания/пересылки (0–2)\n"
        "   >100k subs и (mentions+forwards)<50 → +2\n"
        "   >50k subs и (mentions+forwards)<20 → +1.5\n\n"
        "🚨 Event-based правило\n"
        "   Если Telemetr пометил канал (is_badlisted/is_suspicious/etc) → риск минимум 8/10\n"
    )


# =========================
# Data fetchers
# =========================
@dataclass
class ChannelStat:
    title: str
    username: str
    members: int
    avg_reach: int
    err_percent: float
    ci_index: int
    scoring_rate: float
    mentions_count: int
    forwards_count: int
    raw_resp: dict


def fetch_channel_stat(channel_id: str) -> ChannelStat:
    data = telemetr_get("/channels/stat", {"channelId": channel_id})
    resp = data.get("response", {}) or {}

    title = resp.get("title") or channel_id
    username = resp.get("username") or channel_id

    members = _as_int(resp.get("participants_count"), 0)
    avg_reach = _as_int(resp.get("avg_post_reach"), 0)
    err_percent = _as_float(resp.get("err_percent"), 0.0)
    ci_index = _as_int(resp.get("ci_index"), 0)
    scoring_rate = _as_float(resp.get("scoring_rate"), 0.0)

    mentions_count = _as_int(resp.get("mentions_count"), 0)
    forwards_count = _as_int(resp.get("forwards_count"), 0)

    return ChannelStat(
        title=title,
        username=username,
        members=members,
        avg_reach=avg_reach,
        err_percent=err_percent,
        ci_index=ci_index,
        scoring_rate=scoring_rate,
        mentions_count=mentions_count,
        forwards_count=forwards_count,
        raw_resp=resp,
    )


def fetch_subscribers_series(channel_id: str, days: int = 30) -> List[dict]:
    """
    GET /channels/subscribers
    Returns list in "response": [ {date:..., participants_count:...}, ... ]
    We ask group=day for last `days` days.
    """
    end = now_utc()
    start = end - dt.timedelta(days=days)
    data = telemetr_get("/channels/subscribers", {
        "channelId": channel_id,
        "group": "day",
        "startDate": fmt_telemetr_date(start),
        "endDate": fmt_telemetr_date(end),
    })
    resp = data.get("response", [])
    if isinstance(resp, list):
        return resp
    return []


def compute_deltas_from_series(series: List[dict]) -> Tuple[int, int, int]:
    """
    Returns (day_delta, week_delta, month_delta) based on end-of-day counts.
    If not enough points — returns 0 for missing periods.
    """
    # extract counts in chronological order if already so; if not, sort by date
    def get_date(x):
        return x.get("date") or ""
    s = [x for x in series if isinstance(x, dict)]
    s.sort(key=get_date)

    counts = [_as_int(x.get("participants_count"), None) for x in s]
    counts = [c for c in counts if c is not None]
    if len(counts) < 2:
        return (0, 0, 0)

    last = counts[-1]

    day_delta = last - counts[-2] if len(counts) >= 2 else 0
    week_delta = last - counts[-8] if len(counts) >= 8 else 0   # ~7 days back
    month_delta = last - counts[0] if len(counts) >= 30 else (last - counts[0])

    return (day_delta, week_delta, month_delta)


# =========================
# Telegram handlers
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Кинь ссылку или @username Telegram-канала — я пришлю оценку накрутки (1–10) и объясню, почему."
    )


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "OK ✅\n"
        f"BASE_URL: {TELEMETR_BASE_URL}\n"
        f"TELEMETR_TOKEN set: {'YES' if bool(TELEMETR_TOKEN) else 'NO'}\n"
        f"BOT_TOKEN set: {'YES' if bool(BOT_TOKEN) else 'NO'}"
    )


async def cmd_weights(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(weights_text())


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
        # Network calls in a thread so we don't block asyncio loop
        stat: ChannelStat = await asyncio.to_thread(fetch_channel_stat, channel_id)
        subs_series: List[dict] = await asyncio.to_thread(fetch_subscribers_series, channel_id, 30)

        day_delta, week_delta, month_delta = compute_deltas_from_series(subs_series)

        # Event-based rule (Telemetr moderation flags)
        suspect_flag, suspect_reason = detect_telemetr_suspect_flag(stat.raw_resp)

        # Build scoring parts
        parts: List[ScorePart] = []
        parts.append(score_from_subs_dynamics(stat.members, day_delta, week_delta, month_delta))
        parts.append(score_reach_vs_subs(stat.members, stat.avg_reach))
        parts.append(score_er(stat.err_percent))
        parts.append(score_forward_mentions(stat.forwards_count, stat.mentions_count, stat.members))

        total_points = sum(p.got for p in parts)
        risk = total_score_to_risk(total_points)

        # event-based floor
        event_line = ""
        if suspect_flag:
            risk = max(risk, 8)
            event_line = (
                "\n🚨 Telemetr: канал помечен как подозрительный → риск минимум 8/10\n"
                f"Причина: {suspect_reason}\n"
            )

        explanation = build_explanation(parts)

        msg = (
            f"📊 {stat.title}\n"
            f"@{stat.username}\n\n"
            f"👥 Подписчики: {stat.members}\n"
            f"👀 Средний охват поста: {stat.avg_reach}\n"
            f"📈 ER: {stat.err_percent:.2f}%\n"
            f"🔗 CI: {stat.ci_index}\n"
            f"⭐️ Telemetr rating: {stat.scoring_rate}\n"
            f"📣 Упоминания: {stat.mentions_count}\n"
            f"↪️ Пересылки: {stat.forwards_count}\n"
            f"{event_line}\n"
            f"⚠️ Вероятность накрутки: {risk}/10\n\n"
            f"🧠 Разбор (почему такой риск):\n{explanation}\n\n"
            f"Команда: /weights — пороги и веса"
        )

        await update.message.reply_text(msg)

    except requests.HTTPError as e:
        await update.message.reply_text(f"Ошибка Telemetr API:\n{e}")
    except Exception as e:
        await update.message.reply_text(f"Неожиданная ошибка:\n{e}")


# =========================
# Main
# =========================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("weights", cmd_weights))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Important for stability when load grows:
    # - drop_pending_updates avoids huge backlog after restarts
    # (ptb v20 supports it via run_polling argument)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
