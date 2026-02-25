import os, re, math, logging, json, requests, time
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные окружения
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEMETR_TOKEN = os.environ.get("TELEMETR_TOKEN", "").strip()
BASE_URL = "https://api.telemetr.me"

def calculate_cv(values):
    """Считает коэффициент вариации для определения 'ровности' просмотров."""
    if not values or len(values) < 5: return None
    avg = sum(values) / len(values)
    if avg == 0: return 0
    variance = sum((x - avg) ** 2 for x in values) / len(values)
    return math.sqrt(variance) / avg

def compute_risk(members, reach, mentions, views, raw_rate, del_recent):
    """Логика оценки рисков на основе полученных данных."""
    blocks = []
    
    # Приведение рейтинга к 100-балльной шкале
    rate = raw_rate * 10 if (raw_rate and raw_rate <= 10) else (raw_rate or 0)
    
    # 1. Внешний трафик
    s1, b1 = 0.0, []
    if reach > 1000 and mentions < 2:
        s1 = 3.0
        b1.append("⚠️ Подозрительно мало упоминаний")
    else:
        b1.append("Цитируемость соответствует охвату")
    blocks.append({"t": "Внешний трафик", "s": s1, "m": 3.0, "l": b1})

    # 2. Анализ контента (CV + Удаленные посты)
    s2, b2 = 0.0, []
    cv = calculate_cv(views)
    if cv is not None:
        b2.append(f"CV: {cv:.2f} (анализ {len(views)} постов)")
        if cv < 0.18:
            s2 += 2.5
            b2.append("🚨 Критически ровные просмотры (накрутка)")
        elif cv < 0.25:
            s2 += 1.0
            b2.append("Подозрительно стабильный охват")
    else:
        b2.append("❌ Нет данных по просмотрам постов")
    
    if del_recent > 3:
        s2 += 1.5
        b2.append(f"Удалено {del_recent} постов из последних 50")
    
    blocks.append({"t": "Анализ контента", "s": min(3.0, s2), "m": 3.0, "l": b2})

    # 3. Качество базы
    s3, b3 = 0.0, []
    ratio = (reach / members) if members else 0
    if ratio < 0.03 and members > 500:
        s3 = 2.0
        b3.append(f"Охват {ratio:.1%} — база неактивна")
    else:
        b3.append("Отношение охвата к базе в норме")
    blocks.append({"t": "Качество базы", "s": s3, "m": 2.0, "l": b3})

    # Итоговый расчет риска
    raw_sum = sum(b['s'] for b in blocks)
    risk_score = int(min(10, max(1, round(raw_sum + 1))))
    
    # Фильтр по официальному рейтингу Telemetr
    floor, reason = False, ""
    if 0 < rate < 48:
        if risk_score < 7:
            risk_score = 7
            floor = True
            reason = f"Низкий траст Telemetr ({rate:.1f})"

    return risk_score, blocks, floor, reason

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = re.search(r"(?:t\.me/|@)?([A-Za-z0-9_]{5,})", update.message.text or "")
    if not m: return
    cid = m.group(1)
    h = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}

    try:
        # УПРЯМЫЙ ЗАПРОС СТАТИСТИКИ (3 попытки)
        st = {}
        for attempt in range(3):
            resp = requests.get(f"{BASE_URL}/channels/stat", headers=h, params={"channelId": cid}).json()
            st = resp.get("response", {})
            if st and st.get("participants_count") is not None:
                break
            logger.info(f"Попытка {attempt+1} для {cid} пустая, ждем...")
            time.sleep(2 * (attempt + 1))

        if not st or st.get("participants_count") is None:
            await update.message.reply_text(f"⚠️ API Telemetr перегружено. Не удалось получить базу данных по **{cid}**. Попробуйте позже.")
            return

        # ЗАПРОС ПОСТОВ
        ps_resp = requests.get(f"{BASE_URL}/channels/posts", headers=h, params={"channelId": cid, "limit": 50}).json()
        ps = ps_resp.get("response", {}).get("items", [])
        
        views, del_cnt = [], 0
        for p in ps:
            if p.get("is_deleted"): del_cnt += 1
            v = None
            if "stats" in p and isinstance(p["stats"], dict):
                v = p["stats"].get("views")
            if v is None:
                v = p.get("views_count") or p.get("views")
            if v is not None:
                views.append(int(v))

        # РАСЧЕТ
        risk, blocks, floor, reason = compute_risk(
            st.get("participants_count"), 
            st.get("avg_post_reach"),
            st.get("mentions_count", 0), 
            views, 
            st.get("scoring_rate"), 
            del_cnt
        )

        # ФОРМИРОВАНИЕ ОТВЕТА
        report = [f"📊 *Анализ канала: {cid}*", f"📈 Риск накрутки: `{risk}/10`", "---"]
        for b in blocks:
            # Иконка в зависимости от штрафных баллов
            icon = "🟢" if b['s'] == 0 else "🟡" if b['s'] < 2 else "🔴"
            if "Нет данных" in str(b['l']): icon = "⚪"
            
            report.append(f"{icon} *{b['t']}*: {b['s']:g}/{b['m']:g}")
            for line in b['l']:
                report.append(f"  • {line}")
        
        if floor:
            report.append(f"\n❗ *Фильтр:* {reason}")

        await update.message.reply_text("\n".join(report), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("❌ Ошибка при обработке данных. Попробуйте другой канал.")

def main():
    if not BOT_TOKEN: return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
