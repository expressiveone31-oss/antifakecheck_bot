import os, re, math, logging, requests, time
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEMETR_TOKEN = os.environ.get("TELEMETR_TOKEN", "").strip()
BASE_URL = "https://api.telemetr.me"

# --- ШАГ 2: СЕРВЕРНАЯ ЛОГИКА (ВЕСА И АНАЛИЗ) ---

def calculate_cv(values):
    clean_v = [v for v in values if v is not None and v > 10]
    if len(clean_v) < 5: return None
    avg = sum(clean_v) / len(clean_v)
    variance = sum((x - avg) ** 2 for x in clean_v) / len(clean_v)
    return math.sqrt(variance) / avg

def run_server_analysis(raw_data):
    """
    Тот самый 'сервер', который прогоняет сырые данные по весам.
    """
    st = raw_data.get("stats", {})
    views = raw_data.get("views", [])
    del_recent = raw_data.get("deleted_count", 0)
    
    blocks = []
    
    # 1. Трафик
    s1, b1 = 0.0, []
    growth = st.get("participants_count_growth_week") or 0
    mentions = st.get("mentions_count") or 0
    reach = st.get("avg_post_reach") or 0
    
    if growth > 400 and mentions < 5:
        s1 += 4.0
        b1.append(f"🚨 Аномальный рост: +{growth} за неделю")
    elif reach > 1000 and mentions < 3:
        s1 += 1.5
        b1.append("⚠️ Охват без упоминаний")
    blocks.append({"t": "Трафик", "s": min(4.0, s1), "l": b1 or ["Ок"]})

    # 2. Контент
    s2, b2 = 0.0, []
    cv = calculate_cv(views)
    if cv is not None:
        if cv < 0.12: s2 += 3.5; b2.append(f"🚨 Забор (CV: {cv:.2f})")
        elif cv < 0.18: s2 += 1.5; b2.append(f"⚠️ Подозрительно ровно (CV: {cv:.2f})")
    if del_recent > 3: s2 += 1.0; b2.append(f"Удалено постов: {del_recent}")
    blocks.append({"t": "Контент", "s": min(4.0, s2), "l": b2 or ["Ок"]})

    # 3. База
    s3, b3 = 0.0, []
    members = st.get("participants_count") or 0
    if members > 500:
        ratio = reach / members
        if ratio < 0.05: s3 += 2.0; b3.append(f"🚨 Мертвая база: {ratio:.1%}")
    blocks.append({"t": "База", "s": s3, "l": b3 or ["Ок"]})

    total_risk = round(sum(b['s'] for b in blocks), 1)
    
    # Фильтр Telemetr
    rate = st.get("scoring_rate", 0)
    # Если рейтинг в 10-балльной шкале, переводим
    t_rate = rate * 10 if rate <= 10 else rate
    if 0 < t_rate < 48 and total_risk < 7.0:
        total_risk = 7.0
        
    return total_risk, blocks, t_rate

# --- ШАГ 1: СБОР СЫРЫХ ДАННЫХ ---

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = re.search(r"(?:t\.me/|@)?([A-Za-z0-9_]{5,})", update.message.text or "")
    if not m: return
    cid = m.group(1)
    h = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}

    status_msg = await update.message.reply_text(f"🔍 *Анализ {cid}*:\n⏳ Сбор данных...", parse_mode=ParseMode.MARKDOWN)

    raw_payload = {"stats": {}, "views": [], "deleted_count": 0}

    try:
        # 1. Запрос MAIN_STATS
        for i in range(3):
            r = requests.get(f"{BASE_URL}/channels/stat", headers=h, params={"channelId": cid}, timeout=10).json()
            if r.get("response"):
                raw_payload["stats"] = r["response"]
                break
            time.sleep(2)
        
        if not raw_payload["stats"]:
            await status_msg.edit_text(f"❌ API Telemetr не отдало статистику по @{cid}.\nПопробуй скинуть ссылку еще раз через минуту.")
            return

        await status_msg.edit_text(f"🔍 *Анализ {cid}*:\n✅ Статистика получена\n⏳ Загрузка постов...")

        # 2. Запрос POSTS
        try:
            pr = requests.get(f"{BASE_URL}/channels/posts", headers=h, params={"channelId": cid, "limit": 40}, timeout=10).json()
            items = pr.get("response", {}).get("items", [])
            for p in items:
                if p.get("is_deleted"): raw_payload["deleted_count"] += 1
                v = p.get("views_count") or (p.get("stats") if isinstance(p.get("stats"), dict) else {}).get("views")
                if v: raw_payload["views"].append(int(v))
        except:
            logger.error("Посты не получены")

        await status_msg.edit_text(f"🔍 *Анализ {cid}*:\n✅ Статистика получена\n✅ Посты получены\n⚙️ Запуск сервера аналитики...")

        # ШАГ 2: Передача данных на "сервер" (в нашу функцию анализа)
        risk, blocks, t_rate = run_server_analysis(raw_payload)

        # ФИНАЛЬНЫЙ ВЫВОД
        res = [f"📊 *Результат: @{cid}*", f"📈 Риск: `{risk}/10`", "---"]
        for b in blocks:
            icon = "🟢" if b['s'] == 0 else "🔴" if b['s'] >= 2 else "🟡"
            res.append(f"{icon} *{b['t']}*: `{b['s']:g}`")
            for line in b['l']: res.append(f"  • {line}")
        
        if risk >= 7.0 and t_rate < 48:
            res.append(f"\n❗ *Низкий траст Telemetr ({t_rate:.1f})*")

        await status_msg.edit_text("\n".join(res), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(e)
        await status_msg.edit_text(f"❌ Ошибка системы: {str(e)}")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__": main()
