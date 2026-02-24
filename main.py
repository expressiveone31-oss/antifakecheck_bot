import os
import re
import math
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

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

# Настройка логов для отладки
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================
# Настройки
# =========================
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

# =========================
# Математика
# =========================

def calculate_cv(values: List[int]) -> float:
    """Считает CV. Чем ниже число, тем 'подозрительнее' стабильность просмотров."""
    if not values or len(values) < 5: return 0.5 
    avg = sum(values) / len(values)
    if avg == 0: return 0
    variance = sum((x - avg) ** 2 for x in values) / len(values)
    return math.sqrt(variance) / avg

# =========================
# Scoring Logic
# =========================

def compute_risk(
    members: int,
    avg_reach: int,
    er_percent: float,
    mentions_count: int,
    forwards_count: int,
    recent_views: List[int] = None,
    scoring_rate: int = 100
) -> ScoreResult:
    blocks: List[ScoreBlock] = []
    
    # 1. Внешний трафик (0-3)
    s1 = 0.0
    bullets1 = []
    if avg_reach > 1000 and mentions_count < 5:
        s1 = 3.0
        bullets1.append("⚠️ Фантомный охват: упоминаний в 10 раз меньше нормы")
    elif avg_reach > 5000 and mentions_count < 20:
        s1 = 1.5
        bullets1.append("Низкая цитируемость для такого масштаба")
    else:
        bullets1.append("Цитируемость в пределах нормы")
    blocks.append(ScoreBlock("Внешний трафик", s1, 3.0, bullets1))

    # 2. Анализ Ровности CV (0-3) - ТЕПЕРЬ С ДАННЫМИ
    s2 = 0.0
    bullets2 = []
    if recent_views and len(recent_views) >= 5:
        cv = calculate_cv(recent_views)
        bullets2.append(f"Коэффициент вариации (CV): {cv:.2f}")
        if cv < 0.15:
            s2 = 3.0
            bullets2.append("🚨 Критически ровные просмотры (автонакрутка)")
        elif cv < 0.25:
            s2 = 1.5
            bullets2.append("Подозрительно стабильный охват")
    else:
        bullets2.append("❌ Недостаточно данных по постам (нужно минимум 5)")
    blocks.append(ScoreBlock("Ровность (CV)", s2, 3.0, bullets2))

    # 3. Качество базы (0-2)
    s3 = 0.0
    bullets3 = []
    reach_ratio = (avg_reach / members) if members else 0
    if members > 500:
        if reach_ratio < 0.04:
            s3 = 2.0
            bullets3.append(f"Охват всего {reach_ratio:.1%} — база 'мертвая'")
        elif er_percent < 6:
            s3 = 1.0
            bullets3.append(f"Низкий ER ({er_percent}%)")
    blocks.append(ScoreBlock("Качество базы", s3, 2.0, bullets3))

    # 4. Реакции (0-1)
    s4 = 0.0
    bullets4 = []
    if avg_reach > 5000 and forwards_count < 2:
        s4 = 1.0
        bullets4.append("Подозрительно мало репостов")
    blocks.append(ScoreBlock("Реакции", s4, 1.0, bullets4))

    # Итоговый риск
    raw_sum = sum(b.score for b in blocks)
    risk = int(min(10, max(1, round(raw_sum + 1))))
    
    # Floor по рейтингу Telemetr
    telemetr_floor = False
    reason = ""
    if scoring_rate < 35:
        if risk < 7:
            risk = 7
            telemetr_floor = True
            reason = f"Низкий рейтинг Telemetr ({scoring_rate})"

    return ScoreResult(risk=risk, blocks=blocks, telemetr_floor_applied=telemetr_floor, telemetr_reason=reason)

# =========================
# API Fetching
# =========================

def telemetr_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    try:
        r = _session.get(f"{TELEMETR_BASE_URL}{path}", headers=HEADERS, params=params, timeout=25)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"API Error at {path}: {e}")
        return {}

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = update.message.text or ""
    # Извлекаем username
    cid = re.search(r"(?:t\.me/|@)?([A-Za-z0-9_]{5,})", raw_text)
    if not cid:
        await update.message.reply_text("Пришлите ссылку или @username")
        return
    channel_id = cid.group(1)

    try:
        # 1. Запрос основной статистики
        st_data = telemetr_get("/channels/stat", {"channelId": channel_id}).get("response", {})
        
        # 2. Запрос последних постов для CV (наиболее надежный способ получить просмотры)
        # Если твой тариф позволяет, используем поиск постов канала
        posts_data = telemetr_get("/posts/search", {"channel_id": channel_id, "limit": 10})
        posts_list = posts_data.get("response", {}).get("items", [])
        
        # Вытягиваем только цифры просмотров
        views_history = [p.get("views_count", 0) for p in posts_list if p.get("views_count")]

        sr = compute_risk(
            members=int(st_data.get("participants_count") or 0),
            avg_reach=int(st_data.get("avg_post_reach") or 0),
            er_percent=float(st_data.get("err_percent") or 0),
            mentions_count=int(st_data.get("mentions_count") or 0),
            forwards_count=int(st_data.get("forwards_count") or 0),
            recent_views=views_history,
            scoring_rate=int(st_data.get("scoring_rate") or 100)
        )

        # Формируем отчет
        res = [
            f"📊 *Анализ канала: {channel_id}*",
            f"📈 Риск накрутки: `{sr.risk}/10`",
            "---"
        ]
        
        for b in sr.blocks:
            icon = "🔴" if b.score >= 1.5 else "🟡" if b.score > 0 else "🟢"
            res.append(f"{icon} *{b.title}*: {b.score:g}/{b.max_score:g}")
            for bullet in b.bullets:
                res.append(f"  • {bullet}")
        
        if sr.telemetr_floor_applied:
            res.append(f"\n❗ *Применен фильтр:* {sr.telemetr_reason}")

        await update.message.reply_text("\n".join(res), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        await update.message.reply_text(f"Ошибка при обработке: {e}")

def main():
    if not BOT_TOKEN or not TELEMETR_TOKEN:
        print("BOT_TOKEN или TELEMETR_TOKEN не найдены!")
        return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
