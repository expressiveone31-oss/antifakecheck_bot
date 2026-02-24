import os
import re
import math
import logging
import json
import requests
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# Настройка логов для Railway
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные окружения
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEMETR_TOKEN = os.environ.get("TELEMETR_TOKEN", "").strip()
BASE_URL = "https://api.telemetr.me"

def calculate_cv(values):
    if not values or len(values) < 3: return 0.5
    avg = sum(values) / len(values)
    if avg == 0: return 0
    var = sum((x - avg) ** 2 for x in values) / len(values)
    return math.sqrt(var) / avg

def compute_risk(members, reach, er, mentions, forwards, views, raw_rate):
    blocks = []
    # Коррекция рейтинга: 6.8 -> 68, 4 -> 40
    rate = raw_rate * 10 if (raw_rate and raw_rate <= 10) else (raw_rate or 0)
    
    # 1. Внешний трафик
    s1, b1 = 0.0, []
    if reach > 1000 and mentions < 3:
        s1 = 3.0
        b1.append("⚠️ Критически мало упоминаний")
    else: b1.append("Цитируемость в норме")
    blocks.append({"t": "Внешний трафик", "s": s1, "m": 3.0, "l": b1})

    # 2. Ровность (CV)
    s2, b2 = 0.0, []
    if views and len(views) >= 3:
        cv = calculate_cv(views)
        b2.append(f"CV: {cv:.2f} (анализ {len(views)} постов)")
        if cv < 0.15:
            s2 = 3.0
            b2.append("🚨 Аномальная стабильность (накрутка)")
        elif cv < 0.22:
            s2 = 1.5
            b2.append("Подозрительно ровный охват")
    else:
        b2.append("❌ Нет данных по просмотрам постов")
    blocks.append({"t": "Ровность (CV)", "s": s2, "m": 3.0, "l": b2})

    # 3. База
    s3, b3 = 0.0, []
    ratio = (reach / members) if members else 0
    if ratio < 0.04 and members > 100:
        s3 = 2.0
        b3.append(f"Охват {ratio:.1%} — база неактивна")
    else: b3.append("Отношение охвата к базе в норме")
    blocks.append({"t": "Качество базы", "s": s3, "m": 2.0, "l": b3})

    # 4. Реакции
    s4, b4 = 0.0, []
    if reach > 5000 and forwards < 2:
        s4 = 1.0
        b4.append("Мало репостов")
    else: b4.append("Активность репостов в норме")
    blocks.append({"t": "Реакции", "s": s4, "m": 1.0, "l": b4})

    raw_sum = sum(b['s'] for b in blocks)
    risk = int(min(10, max(1, round(raw_sum + 1))))
    
    floor, reason = False, ""
    if 0 < rate < 35:
        if risk < 7:
            risk = 7
            floor = True
            reason = f"Низкий рейтинг Telemetr ({rate:.1f})"

    return risk, blocks, floor, reason

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = re.search(r"(?:t\.me/|@)?([A-Za-z0-9_]{5,})", update.message.text or "")
    if not m: return
    cid = m.group(1)

    try:
        h = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}
        
        # Запрос статы
        st_req = requests.get(f"{BASE_URL}/channels/stat", headers=h, params={"channelId": cid})
        st = st_req.json().get("response", {})
        
        # Запрос постов
        ps_req = requests.get(f"{BASE_URL}/channels/posts", headers=h, params={"channelId": cid, "limit": 15})
        ps_data = ps_req.json().get("response", {})
        ps_items = ps_data.get("items", [])
        
        # Сбор просмотров + Детектор ключей
        views = []
        sample_keys = []
        if ps_items:
            sample_keys = list(ps_items[0].keys())
            for p in ps_items:
                # Пробуем все варианты из доков
                v = p.get("views_count") or p.get("views") or p.get("views_per_post")
                if v is not None: views.append(int(v))

        # DEBUG ПАТЧ
        debug_info = {
            "views_found": len(views),
            "rate_raw": st.get("scoring_rate"),
            "keys_in_post": sample_keys[:10] # Первые 10 ключей для проверки
        }
        await update.message.reply_text(f"🛠 DEBUG: `{json.dumps(debug_info)}`")

        risk, blocks, floor, reason = compute_risk(
            int(st.get("participants_count") or 0),
            int(st.get("avg_post_reach") or 0),
            float(st.get("err_percent") or 0),
            int(st.get("mentions_count") or 0),
            int(st.get("forwards_count") or 0),
            views,
            st.get("scoring_rate")
        )

        res = [f"📊 *Анализ: {cid}*", f"📈 Риск: `{risk}/10`", "---"]
        for b in blocks:
            icon = "🔴" if b['s'] >= 1.5 else "🟡" if b['s'] > 0 else "🟢"
            res.append(f"{icon} *{b['t']}*: {b['s']:g}/{b['m']:g}")
            for l in b['l']: res.append(f"  • {l}")
        
        if floor:
            res.append(f"\n❗ *Фильтр:* {reason}")

        await update.message.reply_text("\n".join(res), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(e)
        await update.message.reply_text(f"❌ Ошибка: {e}")

def main():
    if not BOT_TOKEN: return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
