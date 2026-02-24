import os
import re
import math
import logging
import requests
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# Настройка логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Данные из Railway Variables
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEMETR_TOKEN = os.environ.get("TELEMETR_TOKEN", "").strip()
BASE_URL = "https://api.telemetr.me"

def calculate_cv(values):
    """Считает коэффициент вариации. Чем меньше, тем ровнее (подозрительнее) просмотры."""
    if not values or len(values) < 3: return 0.5
    avg = sum(values) / len(values)
    if avg == 0: return 0
    var = sum((x - avg) ** 2 for x in values) / len(values)
    return math.sqrt(var) / avg

def compute_risk(members, reach, er, mentions, forwards, views, raw_rate):
    blocks = []
    
    # 1. Коррекция рейтинга Telemetr (приводим к 100-балльной шкале)
    # Если пришло 6.8 (как у Mash), это станет 68. Если 4 (как у FatCat), станет 40.
    rate = raw_rate * 10 if (raw_rate and raw_rate <= 10) else (raw_rate or 0)
    
    # Блок 1: Внешний трафик
    s1, b1 = 0.0, []
    if reach > 1000 and mentions < 3:
        s1 = 3.0
        b1.append("⚠️ Критически мало упоминаний для такого охвата")
    else:
        b1.append("Цитируемость соответствует охвату")
    blocks.append({"t": "Внешний трафик", "s": s1, "m": 3.0, "l": b1})

    # Блок 2: Ровность (CV) - исправленный сбор данных
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

    # Блок 3: База
    s3, b3 = 0.0, []
    ratio = (reach / members) if members else 0
    if ratio < 0.04 and members > 100:
        s3 = 2.0
        b3.append(f"Охват {ratio:.1%} — база неактивна (боты)")
    else:
        b3.append("Отношение охвата к базе в норме")
    blocks.append({"t": "Качество базы", "s": s3, "m": 2.0, "l": b3})

    # Блок 4: Реакции
    s4, b4 = 0.0, []
    if reach > 5000 and forwards < 2:
        s4 = 1.0
        b4.append("Подозрительно мало репостов")
    else:
        b4.append("Активность репостов в норме")
    blocks.append({"t": "Реакции", "s": s4, "m": 1.0, "l": b4})

    # Итоговый риск
    raw_sum = sum(b['s'] for b in blocks)
    risk_score = int(min(10, max(1, round(raw_sum + 1))))
    
    # Применение фильтра по низкому рейтингу
    floor_applied, floor_reason = False, ""
    if 0 < rate < 35:
        if risk_score < 7:
            risk_score = 7
            floor_applied = True
            floor_reason = f"Низкий рейтинг Telemetr ({rate:.1f})"

    return risk_score, blocks, floor_applied, floor_reason

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Извлекаем username/id
    text = update.message.text or ""
    match = re.search(r"(?:t\.me/|@)?([A-Za-z0-9_]{5,})", text)
    if not match:
        return
    cid = match.group(1)

    try:
        headers = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}
        
        # Запрос 1: Общая статистика
        st_resp = requests.get(f"{BASE_URL}/channels/stat", headers=headers, params={"channelId": cid})
        st = st_resp.json().get("response", {})
        
        # Запрос 2: Последние посты (для анализа CV)
        ps_resp = requests.get(f"{BASE_URL}/channels/posts", headers=headers, params={"channelId": cid, "limit": 15})
        ps_items = ps_resp.json().get("response", {}).get("items", [])
        
        # Универсальный сбор просмотров по документации
        views_history = []
        for p in ps_items:
            v = p.get("views_count") or p.get("views") or p.get("views_per_post")
            if v is not None:
                views_history.append(int(v))

        # Расчет риска
        risk, blocks, floor, reason = compute_risk(
            members=int(st.get("participants_count") or 0),
            reach=int(st.get("avg_post_reach") or 0),
            er=float(st.get("err_percent") or 0),
            mentions=int(st.get("mentions_count") or 0),
            forwards=int(st.get("forwards_count") or 0),
            views=views_history,
            raw_rate=st.get("scoring_rate")
        )

        # Формирование отчета
        res = [
            f"📊 *Анализ канала: {cid}*",
            f"📈 Риск накрутки: `{risk}/10`",
            "---"
        ]
        
        for b in blocks:
            icon = "🔴" if b['s'] >= 1.5 else "🟡" if b['s'] > 0 else "🟢"
            res.append(f"{icon} *{b['t']}*: {b['s']:g}/{b['m']:g}")
            for line in b['l']:
                res.append(f"  • {line}")
        
        if floor:
            res.append(f"\n❗ *Применен фильтр:* {reason}")

        await update.message.reply_text("\n".join(res), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("❌ Ошибка при запросе к API. Проверьте токен или ID канала.")

def main():
    if not BOT_TOKEN or not TELEMETR_TOKEN:
        print("Ошибка: Токены не найдены в переменных окружения!")
        return
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
