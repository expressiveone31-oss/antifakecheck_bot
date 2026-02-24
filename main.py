import os
import re
import math
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TELEMETR_TOKEN = os.getenv("TELEMETR_TOKEN", "").strip()
TELEMETR_BASE_URL = os.getenv("TELEMETR_BASE_URL", "https://api.telemetr.me").rstrip("/")

_session = requests.Session()
HEADERS = {"Authorization": f"Bearer {TELEMETR_TOKEN}", "Accept": "application/json"}

@dataclass
class ScoreBlock:
    title: str
    score: float
    max_score: float
    bullets: List[str]

@dataclass
class ScoreResult:
    risk: int
    blocks: List[ScoreBlock]
    telemetr_floor_applied: bool = False
    telemetr_reason: str = ""

def calculate_cv(values: List[int]) -> float:
    if not values or len(values) < 3: return 0.5 
    avg = sum(values) / len(values)
    if avg == 0: return 0
    variance = sum((x - avg) ** 2 for x in values) / len(values)
    return math.sqrt(variance) / avg

def compute_risk(
    members: int,
    avg_reach: int,
    er_percent: float,
    mentions_count: int,
    forwards_count: int,
    recent_views: List[int] = None,
    scoring_rate: int = 100
) -> ScoreResult:
    blocks = []
    
    # 1. Внешний трафик
    s1, b1 = 0.0, []
    if avg_reach > 1000 and mentions_count < 5:
        s1 = 3.0
        b1.append("⚠️ Критически мало упоминаний")
    elif avg_reach > 5000 and mentions_count < 20:
        s1 = 1.5
        b1.append("Низкая цитируемость")
    else: b1.append("Цитируемость в норме")
    blocks.append(ScoreBlock("Внешний трафик", s1, 3.0, b1))

    # 2. Ровность CV
    s2, b2 = 0.0, []
    if recent_views and len(recent_views) >= 3: # Снизил порог до 3 для гибкости
        cv = calculate_cv(recent_views)
        b2.append(f"CV: {cv:.2f} (по {len(recent_views)} постам)")
        if cv < 0.15:
            s2 = 3.0
            b2.append("🚨 Аномальная стабильность (накрутка)")
        elif cv < 0.25:
            s2 = 1.5
            b2.append("Подозрительно ровный охват")
    else:
        b2.append("❌ Нет данных по просмотрам постов")
    blocks.append(ScoreBlock("Ровность (CV)", s2, 3.0, b2))

    # 3. Качество базы
    s3, b3 = 0.0, []
    reach_ratio = (avg_reach / members) if members else 0
    if members > 500:
        if reach_ratio < 0.04:
            s3 = 2.0
            b3.append(f"Охват {reach_ratio:.1%} — база неактивна")
        elif er_percent < 6:
            s3 = 1.0
            b3.append(f"Низкий ER ({er_percent}%)")
    blocks.append(ScoreBlock("Качество базы", s3, 2.0, b3))

    # 4. Реакции
    s4, b4 = 0.0, []
    if avg_reach > 5000 and forwards_count < 2:
        s4 = 1.0
        b4.append("Подозрительно мало репостов")
    blocks.append(ScoreBlock("Реакции", s4, 1.0, b4))

    raw_sum = sum(b.score for b in blocks)
    risk = int(min(10, max(1, round(raw_sum + 1))))
    
    telemetr_floor, reason = False, ""
    if scoring_rate < 35:
        if risk < 7:
            risk = 7
            telemetr_floor = True
            reason = f"Низкий рейтинг Telemetr ({scoring_rate})"

    return ScoreResult(risk=risk, blocks=blocks, telemetr_floor_applied=telemetr_floor, telemetr_reason=reason)

def telemetr_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    try:
        r = _session.get(f"{TELEMETR_BASE_URL}{path}", headers=HEADERS, params=params, timeout=20)
        return r.json() if r.ok else {}
    except: return {}

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text or ""
    cid_match = re.search(r"(?:t\.me/|@)?([A-Za-z0-9_]{5,})", raw_text)
    if not cid_match: return
    channel_id = cid_match.group(1)

    try:
        # ЗАПРОС 1: Статистика
        st_data = telemetr_get("/channels/stat", {"channelId": channel_id}).get("response", {})
        
        # ЗАПРОС 2: Поиск постов (основной метод)
        posts_search = telemetr_get("/posts/search", {"channel_id": channel_id, "limit": 15})
        items = posts_search.get("response", {}).get("items", [])
        
        # Собираем просмотры
        views_history = [p.get("views_count") for p in items if p.get("views_count")]

        # ЗАПРОС 3: Если поиск постов пуст, пробуем вытянуть посты через эндпоинт самого канала
        # (Некоторые тарифы лучше отдают данные через /channels/posts)
        if not views_history:
            ch_posts = telemetr_get("/channels/posts", {"channelId": channel_id, "limit": 10})
            items = ch_posts.get("response", {}).get("items", [])
            views_history = [p.get("views_count") for p in items if p.get("views_count")]

        sr = compute_risk(
            members=int(st_data.get("participants_count") or 0),
            avg_reach=int(st_data.get("avg_post_reach") or 0),
            er_percent=float(st_data.get("err_percent") or 0),
            mentions_count=int(st_data.get("mentions_count") or 0),
            forwards_count=int(st_data.get("forwards_count") or 0),
            recent_views=views_history,
            scoring_rate=int(st_data.get("scoring_rate") or 100)
        )

        res = [f"📊 *Анализ: {channel_id}*", f"📈 Риск: `{sr.risk}/10`", "---"]
        for b in sr.blocks:
            icon = "🔴" if b.score >= 1.5 else "🟡" if b.score > 0 else "🟢"
            res.append(f"{icon} *{b.title}*: {b.score:g}/{b.max_score:g}")
            for bullet in b.bullets: res.append(f"  • {bullet}")
        
        if sr.telemetr_floor_applied:
            res.append(f"\n❗ *Фильтр:* {sr.telemetr_reason}")

        await update.message.reply_text("\n".join(res), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Error: {e}")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
