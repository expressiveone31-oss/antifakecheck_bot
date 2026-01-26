import os
import re
import json
import requests
from typing import Any, Dict, Optional, Tuple

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("TELEMETR_API_KEY")

BASE_URL = "https://api.telemetr.io"


# ---------- helpers ----------

def normalize_handle(text: str) -> Optional[str]:
    """Accepts @name or https://t.me/name and returns @name"""
    text = (text or "").strip()
    m = re.search(r"(?:@|t\.me/)([A-Za-z0-9_]{4,})", text)
    if not m:
        return None
    return "@" + m.group(1)


def telemetr_get(path: str, params: Optional[dict] = None) -> Any:
    if not API_KEY:
        raise RuntimeError("TELEMETR_API_KEY is missing")

    headers = {
        "accept": "application/json",
        "x-api-key": API_KEY,
    }
    url = f"{BASE_URL}{path}"
    r = requests.get(url, headers=headers, params=params or {}, timeout=25)

    # If error, include response body (Telemetr often returns useful JSON)
    if r.status_code >= 400:
        body = r.text
        raise requests.HTTPError(f"{r.status_code} {r.reason} for url: {r.url}\nResponse: {body}", response=r)

    # Try json, else raw text
    try:
        return r.json()
    except Exception:
        return r.text


def first_dict(x: Any) -> Dict[str, Any]:
    """Telemetr often returns list[dict]; normalize to dict."""
    if isinstance(x, list):
        return x[0] if x else {}
    if isinstance(x, dict):
        return x
    return {}


def to_int(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(float(x))
    except Exception:
        return None


def to_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def score_fake(er_percent: Optional[float], views: Optional[int], members: Optional[int]) -> int:
    """
    1..10 = вероятность накрутки (10 = очень вероятно).
    MVP-логика:
    - низкий ER -> выше риск
    - очень низкие просмотры относительно подписчиков -> выше риск
    """
    score = 1

    if members and views is not None:
        ratio = views / max(members, 1)
        if ratio < 0.05:
            score += 4
        elif ratio < 0.08:
            score += 3
        elif ratio < 0.12:
            score += 2
        elif ratio < 0.18:
            score += 1

    if er_percent is not None:
        if er_percent < 2:
            score += 4
        elif er_percent < 4:
            score += 3
        elif er_percent < 6:
            score += 2
        elif er_percent < 8:
            score += 1

    return max(1, min(10, score))


# ---------- telemetr API wrappers ----------

def search_channel(handle: str) -> Dict[str, Any]:
    """
    Telemetr search. We send term without @ (часто так стабильнее),
    но если не найдено — пробуем с @.
    """
    term_variants = [handle.lstrip("@"), handle]
    last = None
    for term in term_variants:
        res = telemetr_get("/v1/channels/search", {"term": term, "limit": 1, "skip": 0})
        last = res
        if isinstance(res, list) and res:
            return res[0]
    return first_dict(last)


def fetch_channel_stats(internal_id: str) -> Dict[str, Any]:
    """
    Tries to fetch stats with different parameter names because
    Telemetr API sometimes expects different keys depending on plan/version.
    """
    attempts: Tuple[Tuple[str, dict], ...] = (
        ("/v1/channel/stats", {"internal_id": internal_id}),
        ("/v1/channel/stats", {"channel_internal_id": internal_id}),
        ("/v1/channel/stats", {"id": internal_id}),
    )

    last_err: Optional[Exception] = None
    for path, params in attempts:
        try:
            res = telemetr_get(path, params)
            return first_dict(res)
        except requests.HTTPError as e:
            last_err = e
            # If it's 400/404, try next variant; if 401/403, stop immediately
            status = getattr(e.response, "status_code", None)
            if status in (401, 403):
                raise
            continue

    if last_err:
        raise last_err
    return {}


# ---------- telegram handlers ----------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Пришли мне @username канала или ссылку https://t.me/username — я посчитаю риск накрутки (1–10)."
    )


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not BOT_TOKEN or not API_KEY:
        await update.message.reply_text("Не заданы BOT_TOKEN или TELEMETR_API_KEY в Variables Railway.")
        return

    handle = normalize_handle(update.message.text)
    if not handle:
        await update.message.reply_text("Пришли @username канала или ссылку https://t.me/username")
        return

    try:
        ch = search_channel(handle)
        if not ch or not ch.get("internal_id"):
            await update.message.reply_text(
                f"Не нашла канал {handle} в Telemetr. Попробуй ещё раз или проверь, что канал публичный."
            )
            return

        internal_id = ch.get("internal_id")
        title = ch.get("title") or handle

        # members in search response
        members = to_int(ch.get("members_count") or ch.get("members"))

        # fetch stats
        stats = fetch_channel_stats(internal_id)

        # fields (with fallbacks)
        views = to_int(stats.get("views_avg") or stats.get("avg_views") or stats.get("views"))
        er = to_float(stats.get("err_percent") or stats.get("er_percent") or stats.get("er"))

        risk = score_fake(er, views, members)

        verdict = "🟢 скорее живой" if risk <= 3 else ("🟡 есть риски" if risk <= 6 else "🔴 высокая вероятность накрутки")

        msg = (
            f"**{title}** ({handle})\n"
            f"Telemetr internal_id: `{internal_id}`\n\n"
            f"Подписчики: {members if members is not None else '—'}\n"
            f"Средние просмотры: {views if views is not None else '—'}\n"
            f"ER (%): {er if er is not None else '—'}\n\n"
            f"Вероятность накрутки: **{risk}/10** — {verdict}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    except requests.HTTPError as e:
        # Return a compact error + hint (without leaking secrets)
        await update.message.reply_text(f"Ошибка Telemetr API:\n{e}")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    app.run_polling()


if __name__ == "__main__":
    main()
