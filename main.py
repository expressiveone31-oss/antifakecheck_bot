import os
import re
import time
import math
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
# Математика и Логика
# =========================

def calculate_cv(values: List[int]) -> float:
    """Считает коэффициент вариации. Низкий CV = подозрительно ровные просмотры."""
    if not values or len(values) < 3: return 0.5 # Недостаточно данных для подозрений
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
    posts_count: int,
    recent_views: List[int] = None, # Нужно для анализа "ровности"
    scoring_rate: int = 100
) -> ScoreResult:
    blocks: List[ScoreBlock] = []
    
    # 1. Индекс Цитируемости vs Охват (0-3 балла)
    # Если охват большой, а упоминаний (mentions) нет — это "налив" просмотров
    s1 = 0.0
    bullets1 = []
    if avg_reach > 1000 and mentions_count < 3:
        s1 = 3.0
        bullets1.append("⚠️ Фантомный охват: просмотры есть, а упоминаний в других каналах нет")
    elif avg_reach > 5000 and mentions_count < 15:
        s1 = 1.5
        bullets1.append("Низкая цитируемость для такого охвата")
    else:
        bullets1.append("Ок: цитируемость соответствует охвату")
    blocks.append(ScoreBlock("Внешний трафик", s1, 3.0, bullets1))

    # 2. Аномальная ровность просмотров (0-2.5 балла)
    s2 = 0.0
    bullets2 = []
    if recent_views and len(recent_views) >= 5:
        cv = calculate_cv(recent_views)
        bullets2.append(f"Вариативность просмотров (CV): {cv:.2f}")
        if cv < 0.12: # Просмотры почти идентичны
            s2 = 2.5
            bullets2.append("🚨 Слишком ровные просмотры: признак автонакрутки")
        elif cv < 0.20:
            s2 = 1.0
            bullets2.append("Подозрительно стабильные просмотры")
    else:
        bullets2.append("Недостаточно данных по постам для анализа CV")
    blocks.append(ScoreBlock("Ровность (CV)", s2, 2.5, bullets2))

    # 3. Баланс ER и Охвата (0-2.5 балла)
    s3 = 0.0
    bullets3 = []
    reach_ratio = (avg_reach / members) if members else 0
    if members > 1000:
        if reach_ratio < 0.03: # Охват меньше 3% от базы
            s3 = 2.5
            bullets3.append(f"Reach/Subs ({reach_ratio:.2%}) критически мал — в канале 'мертвые души'")
        elif er_percent < 5:
            s3 = 1.0
            bullets3.append(f"Низкая вовлеченность (ER {er_percent}%)")
    blocks.append(ScoreBlock("Качество базы", s3, 2.5, bullets3))

    # 4. Пересылки (0-2 балла)
    s4 = 0.0
    bullets4 = []
    if avg_reach > 10000 and forwards_count < 5:
        s4 = 2.0
        bullets4.append("🚨 Огромный охват при почти нулевых репостах")
    blocks.append(ScoreBlock("Реакции", s4, 2.0, bullets4))

    # Итоговый расчет
    raw_sum = sum(b.score for b in blocks)
    risk = int(min(10, max(1, round(raw_sum + 1)))) # База 1, макс 10
    
    # "Пол" по внутреннему рейтингу Telemetr
    telemetr_floor = False
    reason = ""
    if scoring_rate < 30:
        if risk < 7:
            risk = 7
            telemetr_floor = True
            reason = f"Низкий рейтинг Telemetr ({scoring_rate})"

    return ScoreResult(risk=risk, blocks=blocks, telemetr_floor_applied=telemetr_floor, telemetr_reason=reason)

# =========================
# API и Обработка
# =========================

def extract_channel_id(text: str) -> Optional[str]:
    m = re.search(r"(?:https?://)?t\.me/([A-Za-z0-9_]{5,})", text)
    if m: return m.group(1)
    t = text.strip().replace("@", "")
    if re.fullmatch(r"[A-Za-z0-9_]{5,}", t): return t
    return None

def telemetr_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    r = _session.get(f"{TELEMETR_BASE_URL}{path}", headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = extract_channel_id(update.message.text or "")
    if not cid:
        await update.message.reply_text("Пришлите ссылку на канал")
        return

    try:
        # Получаем общую статику
        st_data = telemetr_get("/channels/stat", {"channelId": cid}).get("response", {})
        
        # Для анализа ровности вытягиваем последние посты (если API позволяет)
        # В некоторых тарифах это /posts/search с фильтром по channel_id
        # Если данных нет, CV будет пропущен.
        views_history = st_data.get("recent_posts_views", []) 

        sr = compute_risk(
            members=int(st_data.get("participants_count") or 0),
            avg_reach=int(st_data.get("avg_post_reach") or 0),
            er_percent=float(st_data.get("err_percent") or 0),
            mentions_count=int(st_data.get("mentions_count") or 0),
            forwards_count=int(st_data.get("forwards_count") or 0),
            posts_count=int(st_data.get("posts_count") or 0),
            recent_views=views_history,
            scoring_rate=int(st_data.get("scoring_rate") or 100)
        )

        # Сборка сообщения
        res_msg = [
            f"📊 *Анализ канала: {cid}*",
            f"📈 Риск накрутки: `{sr.risk}/10`",
            "---"
        ]
        
        for b in sr.blocks:
            icon = "🔴" if b.score > 1.5 else "🟡" if b.score > 0 else "🟢"
            res_msg.append(f"{icon} *{b.title}*: {b.score}/{b.max_score}")
            for bullet in b.bullets:
                res_msg.append(f"  • {bullet}")
        
        if sr.telemetr_floor_applied:
            res_msg.append(f"\n❗ *Применен фильтр:* {sr.telemetr_reason}")

        await update.message.reply_text("\n".join(res_msg), parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

# =========================
# Запуск
# =========================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Пришли username канала")))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
