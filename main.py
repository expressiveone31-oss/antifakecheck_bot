import os, re, math, logging, requests, time
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEMETR_TOKEN = os.environ.get("TELEMETR_TOKEN", "").strip()
BASE_URL = "https://api.telemetr.me"

def calculate_cv(values):
    """Считает коэффициент вариации. Если просмотров мало или они null - возвращает None."""
    # Фильтруем нулевые и пустые просмотры для точности
    clean_v = [v for v in values if v is not None and v > 10]
    if len(clean_v) < 5: return None
    avg = sum(clean_v) / len(clean_v)
    variance = sum((x - avg) ** 2 for x in clean_v) / len(clean_v)
    return math.sqrt(variance) / avg

def compute_risk(st, views, del_recent):
    """
    Финальная логика скоринга (точность 0.1).
    """
    blocks = []
    
    # Данные из статистики
    members = st.get("participants_count") or 0
    reach = st.get("avg_post_reach") or 0
    mentions = st.get("mentions_count") or 0
    growth = st.get("participants_count_growth_week") or 0
    rate = st.get("scoring_rate") or 0 # Обычно 0.0 - 10.0

    # 1. ТРАФИК И РОСТ (Макс 4.0)
    s1, b1 = 0.0, []
    # Аномальный рост без рекламы (главный фактор)
    if growth and growth > 400 and mentions < 5:
        s1 += 4.0
        b1.append(f"🚨 АНОМАЛИЯ: Рост +{growth} при {mentions} упом.")
    elif reach > 1000 and mentions < 3:
        s1 += 1.5
        b1.append("⚠️ Высокий охват при почти нулевом цитировании")
    
    # Виральность (Эффективность)
    if mentions > 0:
        eff = reach / mentions
        if eff > 5000 and reach > 2000:
            s1 += 1.0
            b1.append(f"Подозрительный выхлоп: {eff:.0f} охвата/упом.")
            
    blocks.append({"t": "Трафик и Рост", "s": min(4.0, s1), "l": b1 or ["Источники выглядят ок"]})

    # 2. АНАЛИЗ КОНТЕНТА (CV + Удаления, Макс 4.0)
    s2, b2 = 0.0, []
    cv = calculate_cv(views)
    if cv is not None:
        if cv < 0.12: # "Забор" как у sergeeva_official
            s2 += 3.5
            b2.append(f"🚨 Железобетонный забор (CV: {cv:.2f})")
        elif cv < 0.18: # "Докрутка" как у sncmag
            s2 += 1.5
            b2.append(f"⚠️ Подозрительная ровность (CV: {cv:.2f})")
        else:
            b2.append(f"Естественный разброс (CV: {cv:.2f})")
    
    if del_recent > 3:
        s2 += 1.0
        b2.append(f"Удалено {del_recent} постов из последних")
        
    blocks.append({"t": "Контент", "s": min(4.0, s2), "l": b2 or ["Нет данных по постам"]})

    # 3. КАЧЕСТВО БАЗЫ (Твой порог 5%, Макс 2.0)
    s3, b3 = 0.0, []
    if members > 500:
        ratio = reach / members
        if ratio < 0.05:
            s3 += 2.0
            b3.append(f"🚨 Мертвая база: охват {ratio:.1%} (порог 5%)")
        else:
            b3.append(f"Активность базы: {ratio:.1%}")
            
    blocks.append({"t": "База", "s": s3, "l": b3 or ["Мало данных"]})

    # ИТОГО
    total_risk = round(sum(b['s'] for b in blocks), 1)
    
    # Фильтр Telemetr (Принудительный)
    floor_active = False
    if 0 < rate < 4.8 and total_risk < 7.0:
        total_risk = 7.0
        floor_active = True

    return total_risk, blocks, floor_active, rate

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = re.search(r"(?:t\.me/|@)?([A-Za-z0-9_]{5,})", update.message.text or "")
    if not m: return
    cid = m.group(1)
    h = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}

    try:
        # Упрямый запрос статы
        st = {}
        for _ in range(3):
            r = requests.get(f"{BASE_URL}/channels/stat", headers=h, params={"channelId": cid}).json()
            st = r.get("response", {})
            if st and st.get("participants_count") is not None: break
            time.sleep(2)

        if not st or st.get("participants_count") is None:
            await update.message.reply_text(f"⚠️ API не отдало данные по {cid}. Попробуй позже.")
            return

        # Посты
        ps_r = requests.get(f"{BASE_URL}/channels/posts", headers=h, params={"channelId": cid, "limit": 40}).json()
        ps = ps_r.get("response", {}).get("items", [])
        
        views, del_cnt = [], 0
        for p in ps:
            if p.get("is_deleted"): del_cnt += 1
            v = p.get("views_count") or (p.get("stats") if isinstance(p.get("stats"), dict) else {}).get("views")
            if v is not None: views.append(int(v))

        # Считаем
        risk, blocks, floor, t_rate = compute_risk(st, views, del_cnt)

        # Рендерим ответ
        res = [f"📊 *Анализ канала: @{cid}*", f"📈 Риск накрутки: `{risk}/10`", "---"]
        for b in blocks:
            icon = "🟢" if b['s'] == 0 else "🟡" if b['s'] < 2 else "🔴"
            res.append(f"{icon} *{b['t']}*: {b['s']:g}")
            for line in b['l']: res.append(f"  • {line}")
            
        if floor:
            res.append(f"\n❗ *Внимание:* Низкий траст Telemetr ({t_rate}), риск поднят до 7.0")

        await update.message.reply_text("\n".join(res), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(e)
        await update.message.reply_text("❌ Ошибка при анализе.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__": main()
