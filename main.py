import os, re, math, logging, requests, time
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

def compute_risk(st, views, del_recent):
    blocks = []
    
    # Извлекаем базовые цифры
    members = st.get("participants_count") or 0
    reach = st.get("avg_post_reach") or 0
    mentions = st.get("mentions_count") or 0
    growth_week = st.get("participants_count_growth_week") or 0
    raw_rate = st.get("scoring_rate") or 0
    rate = raw_rate * 10 if raw_rate <= 10 else raw_rate

    # --- БЛОК 1: ТРАФИК И АНОМАЛЬНЫЙ РОСТ ---
    s1, b1 = 0.0, []
    # Главный фактор: Рост без упоминаний
    if growth_week > 300 and mentions < 2:
        s1 += 4.0
        b1.append(f"🚨 АНОМАЛИЯ: Рост +{growth_week} за неделю при 0 рекламы")
    elif reach > 1000 and mentions < 3:
        s1 += 2.0
        b1.append("⚠️ Высокий охват без видимых источников трафика")
    
    # Проверка "выхлопа" (Виральность)
    if mentions > 0:
        efficiency = reach / mentions
        if efficiency > 7000:
            s1 += 1.5
            b1.append(f"Подозрительная отдача: {efficiency:.0f} охвата/упоминание")
    
    if not b1: b1.append("Источники трафика выглядят органично")
    blocks.append({"t": "Трафик и Рост", "s": min(4.0, s1), "m": 4.0, "l": b1})

    # --- БЛОК 2: КОНТЕНТ (CV 15% И УДАЛЕНИЯ) ---
    s2, b2 = 0.0, []
    cv = calculate_cv(views)
    if cv is not None:
        b2.append(f"CV: {cv:.2f}")
        if cv < 0.15: # Твой новый порог
            s2 += 3.0
            b2.append("🚨 Накрутка просмотров (CV < 15%)")
        elif cv < 0.22:
            s2 += 1.0
            b2.append("Просмотры подозрительно ровные")
    else:
        b2.append("⚪ Данные по постам не получены (не влияет на балл)")
    
    if del_recent > 3:
        s2 += 1.0
        b2.append(f"Удалено {del_recent} постов (заметание следов)")
    
    blocks.append({"t": "Анализ постов", "s": min(3.0, s2), "m": 3.0, "l": b2})

    # --- БЛОК 3: БАЗА (ПОРОГ 5%) ---
    s3, b3 = 0.0, []
    if members > 0:
        ratio = reach / members
        if ratio < 0.05: # Твой новый порог
            s3 = 2.0
            b3.append(f"Охват {ratio:.1%} — база мертва/боты (порог 5%)")
        else:
            b3.append(f"Активность аудитории: {ratio:.1%}")
    else:
        b3.append("Нет данных о подписчиках")

    blocks.append({"t": "Качество базы", "s": s3, "m": 2.0, "l": b3})

    # ИТОГ
    raw_sum = sum(b['s'] for b in blocks)
    risk = int(min(10, max(1, round(raw_sum + 1))))
    
    # Фильтр Telemetr
    if 0 < rate < 48 and risk < 7:
        risk = 7
        return risk, blocks, True, f"Низкий траст Telemetr ({rate:.1f})"

    return risk, blocks, False, ""

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = re.search(r"(?:t\.me/|@)?([A-Za-z0-9_]{5,})", update.message.text or "")
    if not m: return
    cid = m.group(1)
    h = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}

    try:
        # 1. Запрос статистики (с повтором)
        st = {}
        for _ in range(3):
            r = requests.get(f"{BASE_URL}/channels/stat", headers=h, params={"channelId": cid}).json()
            st = r.get("response", {})
            if st and st.get("participants_count"): break
            time.sleep(2)

        # 2. Запрос постов
        ps_r = requests.get(f"{BASE_URL}/channels/posts", headers=h, params={"channelId": cid, "limit": 50}).json()
        ps = ps_r.get("response", {}).get("items", [])
        
        views, del_cnt = [], 0
        for p in ps:
            if p.get("is_deleted"): del_cnt += 1
            v = None
            if "stats" in p and isinstance(p["stats"], dict): v = p["stats"].get("views")
            if v is None: v = p.get("views_count") or p.get("views")
            if v is not None: views.append(int(v))

        # 3. Расчет
        risk, blocks, floor, reason = compute_risk(st, views, del_cnt)

        # Вывод
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
