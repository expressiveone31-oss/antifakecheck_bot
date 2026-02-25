import os, re, math, logging, json, requests
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEMETR_TOKEN = os.environ.get("TELEMETR_TOKEN", "").strip()
BASE_URL = "https://api.telemetr.me"

def calculate_cv(values):
    if not values or len(values) < 3: return 0.5
    avg = sum(values) / len(values)
    if avg == 0: return 0
    var = sum((x - avg) ** 2 for x in values) / len(values)
    return math.sqrt(var) / avg

def compute_risk(members, reach, mentions, views, raw_rate):
    blocks = []
    # Шкала Telemetr: 6.8 -> 68, 4.3 -> 43
    rate = raw_rate * 10 if (raw_rate and raw_rate <= 10) else (raw_rate or 0)
    
    # 1. Трафик (Здесь потом внедрим "Рекламную отдачу")
    s1, b1 = 0.0, []
    if reach > 1000 and mentions < 3:
        s1 = 3.0
        b1.append("⚠️ Критически мало упоминаний")
    else: b1.append("Цитируемость в норме")
    blocks.append({"t": "Внешний трафик", "s": s1, "m": 3.0, "l": b1})

    # 2. Ровность (CV) - Пока старые пороги, будем ужесточать после теста
    s2, b2 = 0.0, []
    if views and len(views) >= 3:
        cv = calculate_cv(views)
        b2.append(f"CV: {cv:.2f} (анализ {len(views)} постов)")
        if cv < 0.20: # Немного подняли планку с 0.15
            s2 = 3.0
            b2.append("🚨 Аномальная стабильность (накрутка)")
    else: b2.append("❌ Нет данных по просмотрам")
    blocks.append({"t": "Ровность (CV)", "s": s2, "m": 3.0, "l": b2})

    # 3. База
    s3, b3 = 0.0, []
    ratio = (reach / members) if members else 0
    if ratio < 0.04 and members > 100:
        s3 = 2.0
        b3.append(f"Охват {ratio:.1%} — база неактивна")
    else: b3.append("Соотношение в норме")
    blocks.append({"t": "Качество базы", "s": s3, "m": 2.0, "l": b3})

    raw_sum = sum(b['s'] for b in blocks)
    risk = int(min(10, max(1, round(raw_sum + 1))))
    
    floor, reason = False, ""
    if 0 < rate < 45:
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
        
        # 1. Статистика канала
        st_resp = requests.get(f"{BASE_URL}/channels/stat", headers=h, params={"channelId": cid}).json()
        st = st_resp.get("response", {})
        
        # 2. Посты (50 шт)
        ps_resp = requests.get(f"{BASE_URL}/channels/posts", headers=h, params={"channelId": cid, "limit": 50}).json()
        ps = ps_resp.get("response", {}).get("items", [])
        
        # Сбор данных для CV и проверка на удаленные посты внутри массива
        views = []
        deleted_in_last_50 = 0
        for p in ps:
            if p.get("is_deleted"): deleted_in_last_50 += 1
            
            v = None
            if "stats" in p and isinstance(p["stats"], dict):
                v = p["stats"].get("views")
            if v is None: v = p.get("views_count") or p.get("views")
            if v is not None: views.append(int(v))

        # --- БЛОК ПРОВЕРКИ НОВЫХ ДАННЫХ ---
        debug_features = {
            "scoring_rate": st.get("scoring_rate"),
            "deleted_total_stat": st.get("deleted_posts_count"), 
            "deleted_in_recent_50": deleted_in_last_50,
            "adv_posts_found": st.get("adv_posts_count") or st.get("advertising_posts_count")
        }
        await update.message.reply_text(f"🔍 ПРОВЕРКА ДАННЫХ: `{json.dumps(debug_features)}`")
        # ---------------------------------

        risk, blocks, floor, reason = compute_risk(
            int(st.get("participants_count") or 0),
            int(st.get("avg_post_reach") or 0),
            int(st.get("mentions_count") or 0),
            views,
            st.get("scoring_rate")
        )

        res = [f"📊 *Анализ канала: {cid}*", f"📈 Риск накрутки: `{risk}/10`", "---"]
        for b in blocks:
            icon = "🟢" if b['s'] == 0 else "🟡" if b['s'] < 2 else "🔴"
            res.append(f"{icon} *{b['t']}*: {b['s']:g}/{b['m']:g}")
            for l in b['l']: res.append(f"  • {l}")
        if floor: res.append(f"\n❗ *Фильтр:* {reason}")

        await update.message.reply_text("\n".join(res), parse_mode=ParseMode.MARKDOWN)
        
    except Exception as e:
        logger.error(e)
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

def main():
    if not BOT_TOKEN: return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__": main()
