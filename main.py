import os
import re
import math
import logging
import json
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

# Токены из Railway
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEMETR_TOKEN = os.environ.get("TELEMETR_TOKEN", "").strip()
TELEMETR_BASE_URL = "https://api.telemetr.me"

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

def compute_risk(members, avg_reach, er_percent, mentions, forwards, views, scoring_rate) -> ScoreResult:
    blocks = []
    
    # 1. Внешний трафик
    s1, b1 = 0.0, []
    if avg_reach > 1000 and mentions < 3:
        s1 = 3.0
        b1.append("⚠️ Критически мало упоминаний")
    else: b1.append("Цитируемость в норме")
    blocks.append(ScoreBlock("Внешний трафик", s1, 3.0, b1))

    # 2. Ровность (CV)
    s2, b2 = 0.0, []
    if views and len(views) >= 3:
        cv = calculate_cv(views)
        b2.append(f"CV: {cv:.2f} (по {len(views)} постам)")
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
    ratio = (avg_reach / members) if members else 0
    if ratio < 0.04 and members > 100:
        s3 = 2.0
        b3.append(f"Охват {ratio:.1%} — много ботов")
    else: b3.append("Отношение охвата к базе в норме")
    blocks.append(ScoreBlock("Качество базы", s3, 2.0, b3))

    # 4. Реакции
    s4, b4 = 0.0, []
    if avg_reach > 5000 and forwards < 2:
        s4 = 1.0
        b4.append("Подозрительно мало репостов")
    else: b4.append("Активность репостов в норме")
    blocks.append(ScoreBlock("Реакции", s4, 1.0, b4))

    raw = sum(b.score for b in blocks)
    risk = int(min(10, max(1, round(raw + 1))))
    
    floor, reason = False, ""
    # ИСПРАВЛЕНИЕ: фильтр срабатывает только если рейтинг реально существует (не 0 и не None)
    if scoring_rate and 0 < scoring_rate < 35:
        if risk < 7:
            risk = 7
            floor = True
            reason = f"Низкий рейтинг Telemetr ({scoring_rate})"

    return ScoreResult(risk, blocks, floor, reason)

def telemetr_get(path, params):
    try:
        headers = {"Authorization": f"Bearer {TELEMETR_TOKEN}", "Accept": "application/json"}
        r = requests.get(f"{TELEMETR_BASE_URL}{path}", headers=headers, params=params, timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text or ""
    m = re.search(r"(?:t\.me/|@)?([A-Za-z0-9_]{5,})", raw)
    if not m: return
    cid = m.group(1)

    try:
        # ЗАПРОС 1: Статистика
        st_resp = telemetr_get("/channels/stat", {"channelId": cid})
        st = st_resp.get("response", {})
        
        # ЗАПРОС 2: Посты
        ps_resp = telemetr_get("/channels/posts", {"channelId": cid, "limit": 5})
        ps_items = ps_resp.get("response", {}).get("items", [])
        views = [p.get("views_count") for p in ps_items if p.get("views_count")]

        # ДЕБАГ-СООБЩЕНИЕ (отправляем сырой JSON)
        debug_json = {
            "channel_id_requested": cid,
            "stat_response_keys": list(st.keys()),
            "scoring_rate_received": st.get("scoring_rate"),
            "posts_count_received": len(ps_items),
            "views_array": views
        }
        await update.message.reply_text(
            f"🛠 **DEBUG INFO:**\n```json\n{json.dumps(debug_json, indent=2)}\n```",
            parse_mode=ParseMode.MARKDOWN
        )

        sr = compute_risk(
            int(st.get("participants_count") or 0),
            int(st.get("avg_post_reach") or 0),
            float(st.get("err_percent") or 0),
            int(st.get("mentions_count") or 0),
            int(st.get("forwards_count") or 0),
            views,
            st.get("scoring_rate") # Передаем как есть
        )

        msg = [f"📊 *Анализ: {cid}*", f"📈 Риск: `{sr.risk}/10`", "---"]
        for b in sr.blocks:
            icon = "🔴" if b.score >= 1.5 else "🟡" if b.score > 0 else "🟢"
            msg.append(f"{icon} *{b.title}*: {b.score:g}/{b.max_score:g}")
            for bul in b.bullets: msg.append(f"  • {bul}")
        
        if sr.telemetr_floor_applied:
            msg.append(f"\n❗ *Фильтр:* {sr.telemetr_reason}")

        await update.message.reply_text("\n".join(msg), parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"❌ Ошибка в обработчике: {e}")

def main():
    if not BOT_TOKEN: return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
