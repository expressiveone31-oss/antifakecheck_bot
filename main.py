import os, re, math, logging, json, requests, time
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEMETR_TOKEN = os.environ.get("TELEMETR_TOKEN", "").strip()
BASE_URL = "https://api.telemetr.me"

def calculate_cv(values):
    if not values or len(values) < 5: return None
    avg = sum(values) / len(values)
    if avg == 0: return 0
    var = sum((x - avg) ** 2 for x in values) / len(values)
    return math.sqrt(var) / avg

def compute_risk(members, reach, mentions, views, raw_rate, del_recent):
    blocks = []
    # Если данных нет вообще - риск неопределен
    if not members and not raw_rate:
        return 0, [], False, "API Telemetr временно не отдал данные. Попробуйте через минуту."

    rate = raw_rate * 10 if (raw_rate and raw_rate <= 10) else (raw_rate or 0)
    
    # 1. Трафик + Аномальная виральность
    s1, b1 = 0.0, []
    if reach > 1000 and mentions < 2:
        s1 = 3.0
        b1.append("⚠️ Подозрительно мало упоминаний")
    elif reach > 100 and mentions > 0:
        # Проверка "выхлопа" (примерный расчет)
        efficiency = reach / mentions
        if efficiency > 5000: # Один пост приносит > 5к охвата? Подозрительно для мелких.
            s1 = 1.5
            b1.append("Аномальная отдача от упоминаний")
    blocks.append({"t": "Внешний трафик", "s": s1, "m": 3.0, "l": b1})

    # 2. Ровность (CV) + Удаленные посты
    s2, b2 = 0.0, []
    cv = calculate_cv(views)
    if cv is not None:
        b2.append(f"CV: {cv:.2f} (на основе {len(views)} постов)")
        # Ужесточаем пороги: идеальная ровность теперь наказывается сильнее
        if cv < 0.18:
            s2 += 2.5
            b2.append("🚨 Критически ровные просмотры")
        elif cv < 0.25:
            s2 += 1.0
            b2.append("Слишком стабильный охват")
    
    if del_recent > 3: # Если в последних 50 постах удалено больше 3
        s2 += 1.5
        b2.append(f"Удалено {del_recent} постов из последних 50")
    
    blocks.append({"t": "Анализ контента", "s": min(3.0, s2), "m": 3.0, "l": b2})

    # 3. База
    s3, b3 = 0.0, []
    ratio = (reach / members) if members else 0
    if ratio < 0.03 and members > 500:
        s3 = 2.0
        b3.append(f"Охват {ratio:.1%} — база 'мертвая'")
    blocks.append({"t": "Качество базы", "s": s3, "m": 2.0, "l": b3})

    raw_sum = sum(b['s'] for b in blocks)
    risk = int(min(10, max(1, round(raw_sum + 1))))
    
    floor, reason = False, ""
    if 0 < rate < 48: # Чуть подняли планку фильтра
        if risk < 7:
            risk = 7
            floor = True
            reason = f"Низкий траст Telemetr ({rate:.1f})"

    return risk, blocks, floor, reason

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = re.search(r"(?:t\.me/|@)?([A-Za-z0-9_]{5,})", update.message.text or "")
    if not m: return
    cid = m.group(1)
    h = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}

    try:
        # Попытка 1: Статистика
        st = {}
        for _ in range(2): # 2 попытки если пришел null
            st = requests.get(f"{BASE_URL}/channels/stat", headers=h, params={"channelId": cid}).json().get("response", {})
            if st.get("participants_count"): break
            time.sleep(1.5)

        # Попытка 2: Посты
        ps = requests.get(f"{BASE_URL}/channels/posts", headers=h, params={"channelId": cid, "limit": 50}).json().get("response", {}).get("items", [])
        
        views, del_cnt = [], 0
        for p in ps:
            if p.get("is_deleted"): del_cnt += 1
            v = None
            if "stats" in p and isinstance(p["stats"], dict): v = p["stats"].get("views")
            if v is None: v = p.get("views_count") or p.get("views")
            if v is not None: views.append(int(v))

        risk, blocks, floor, reason = compute_risk(
            st.get("participants_count"), st.get("avg_post_reach"),
            st.get("mentions_count"), views, st.get("scoring_rate"), del_cnt
        )

        if risk == 0: # Если данных так и нет
            await update.message.reply_text(f"⚠️ Канал **{cid}** не отдал данные API. Возможно, он скрыт или в кэше Telemetr пусто.")
            return

        res = [f"📊 *Анализ: {cid}*", f"📈 Риск: `{risk}/10`", "---"]
        for b in blocks:
            icon = "🟢" if b['s'] == 0 else "🟡" if b['s'] < 2 else "🔴"
            res.append(f"{icon} *{b['t']}*: {b['s']:g}/{b['m']:g}")
            for l in b['l']: res.append(f"  • {l}")
        if floor: res.append(f"\n❗ *Фильтр:* {reason}")

        await update.message.reply_text("\n".join(res), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(e)

def main():
    if not BOT_TOKEN: return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__": main()
