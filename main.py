import os, re, math, logging, requests, time
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Данные из окружения
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEMETR_TOKEN = os.environ.get("TELEMETR_TOKEN", "").strip()
BASE_URL = "https://api.telemetr.me"

def calculate_cv(values):
    """Считает коэффициент вариации (разброс просмотров)."""
    # Убираем аномально низкие значения (1-5 просмотров), чтобы не портить статистику
    clean_v = [v for v in values if v is not None and v > 10]
    if len(clean_v) < 5: return None
    
    avg = sum(clean_v) / len(clean_v)
    if avg == 0: return 0
    variance = sum((x - avg) ** 2 for x in clean_v) / len(clean_v)
    return math.sqrt(variance) / avg

def compute_risk(st, views, del_recent):
    """Основная логика оценки по твоим параметрам."""
    blocks = []
    
    # Извлекаем переменные
    members = st.get("participants_count") or 0
    reach = st.get("avg_post_reach") or 0
    mentions = st.get("mentions_count") or 0
    growth = st.get("participants_count_growth_week") or 0
    # Telemetr scoring_rate обычно 0.0 - 10.0, приводим к 100-балльной для фильтра
    raw_rate = st.get("scoring_rate") or 0
    rate = raw_rate * 10 if raw_rate <= 10 else raw_rate

    # 1. ТРАФИК И РОСТ (Макс 4.0)
    s1, b1 = 0.0, []
    if growth and growth > 400 and mentions < 5:
        s1 += 4.0
        b1.append(f"🚨 Аномальный рост: +{growth} саб/нед при {mentions} упом.")
    elif reach > 1000 and mentions < 3:
        s1 += 1.5
        b1.append("⚠️ Высокий охват без внешних упоминаний")
    
    # Виральность
    if mentions > 0:
        eff = reach / mentions
        if eff > 5000 and reach > 2000:
            s1 += 1.0
            b1.append(f"Подозрительный выхлоп: {eff:.0f} охвата на 1 упом.")
    
    blocks.append({"t": "Трафик", "s": min(4.0, s1), "l": b1 or ["Источники выглядят ок"]})

    # 2. КОНТЕНТ (CV + УДАЛЕНИЯ, Макс 4.0)
    s2, b2 = 0.0, []
    cv = calculate_cv(views)
    if cv is not None:
        if cv < 0.12: # "Забор"
            s2 += 3.5
            b2.append(f"🚨 Критическая ровность (CV: {cv:.2f})")
        elif cv < 0.18: # "Докрутка"
            s2 += 1.5
            b2.append(f"⚠️ Подозрительная ровность (CV: {cv:.2f})")
        else:
            b2.append(f"Органика (CV: {cv:.2f})")
    
    if del_recent > 3:
        s2 += 1.0
        b2.append(f"Удалено {del_recent} постов (заметание следов)")
        
    blocks.append({"t": "Контент", "s": min(4.0, s2), "l": b2 or ["Нет данных по постам"]})

    # 3. БАЗА (Порог 5%, Макс 2.0)
    s3, b3 = 0.0, []
    if members > 500:
        ratio = reach / members
        if ratio < 0.05:
            s3 += 2.0
            b3.append(f"🚨 Мертвая база: охват {ratio:.1%} (порог 5%)")
        else:
            b3.append(f"Активность аудитории: {ratio:.1%}")
            
    blocks.append({"t": "База", "s": s3, "l": b3 or ["Мало данных для оценки базы"]})

    # ИТОГОВЫЙ БАЛЛ (Дробный, без округления до целого)
    total_risk = round(sum(b['s'] for b in blocks), 1)
    
    # Фильтр Telemetr (Принудительный риск 7.0 если траст низкий)
    floor_active = False
    if 0 < rate < 48 and total_risk < 7.0:
        total_risk = 7.0
        floor_active = True

    return total_risk, blocks, floor_active, rate

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = re.search(r"(?:t\.me/|@)?([A-Za-z0-9_]{5,})", update.message.text or "")
    if not m: return
    cid = m.group(1)
    h = {"Authorization": f"Bearer {TELEMETR_TOKEN}"}

    try:
        # ШАГ 1: Статистика (3 попытки с прогревом)
        st = {}
        for attempt in range(3):
            try:
                r = requests.get(f"{BASE_URL}/channels/stat", headers=h, params={"channelId": cid}, timeout=10).json()
                st = r.get("response", {})
                if st and st.get("participants_count") is not None:
                    break
            except: pass
            time.sleep(3 + attempt)

        if not st or st.get("participants_count") is None:
            await update.message.reply_text(f"⚠️ Telemetr не отдал базовую стат по @{cid}. Канал может быть не проиндексирован.")
            return

        # ШАГ 2: Посты (не падаем, если их нет)
        ps = []
        try:
            ps_r = requests.get(f"{BASE_URL}/channels/posts", headers=h, params={"channelId": cid, "limit": 40}, timeout=10).json()
            ps = ps_r.get("response", {}).get("items", [])
        except:
            logger.warning(f"Не удалось получить посты для {cid}")
        
        views, del_cnt = [], 0
        for p in ps:
            if p.get("is_deleted"): del_cnt += 1
            # Ищем просмотры во всех возможных полях
            v = p.get("views_count")
            if v is None and isinstance(p.get("stats"), dict):
                v = p["stats"].get("views")
            if v is not None:
                views.append(int(v))

        # ШАГ 3: Расчет
        risk, blocks, floor, t_rate = compute_risk(st, views, del_cnt)

        # ШАГ 4: Красивый вывод
        res = [f"📊 *Анализ канала: @{cid}*", f"📈 Итоговый риск: `{risk}/10`", "---"]
        for b in blocks:
            # Иконки для визуальной наглядности
            icon = "🟢" if b['s'] == 0 else "🟡" if b['s'] < 2 else "🔴"
            res.append(f"{icon} *{b['t']}*: `{b['s']:g}`")
            for line in b['l']:
                res.append(f"  • {line}")
            
        if floor:
            res.append(f"\n❗ *Внимание:* Траст Telemetr критически низкий ({t_rate:.1f}). Риск поднят до 7.0 автоматически.")

        await update.message.reply_text("\n".join(res), parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"Ошибка хендлера: {e}")
        await update.message.reply_text("❌ Произошла ошибка при обработке данных. Попробуйте еще раз.")

def main():
    if not BOT_TOKEN:
        print("BOT_TOKEN не найден!")
        return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
