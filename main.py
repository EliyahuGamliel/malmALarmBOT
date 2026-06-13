import json
import time
import os
import logging
import asyncio
import urllib.parse
import pytz
import uuid
import base64
import zlib
from datetime import datetime, timedelta
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.error import RetryAfter

# ================= הגדרות מערכת =================
TOKEN = '8595177968:AAEwImqSp432W2GD3YkNpvkzjjQqiwvmhOI'
WEB_APP_URL = 'https://eliyahugamliel.github.io/malmALarmBOT/index.html'
USERS_FILE = 'users.json'
ADMINS_FILE = 'admins.json'
MESSAGES_FILE = 'sent_messages.json'
EVENTS_FILE = 'events.json'
REGISTRATIONS_FILE = 'registrations.json'
PERSONAL_EVENTS_FILE = 'personal_events.json' # קובץ חדש לאירועים אישיים
MASTER_ADMIN_ID = 534078278

# הגדרת אזור זמן ישראל
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')
HEBREW_DAYS = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
scheduler = AsyncIOScheduler(timezone=ISRAEL_TZ)

def get_time_remaining_str(target_time):
    now = datetime.now(ISRAEL_TZ)
    diff_days = (target_time.date() - now.date()).days

    if diff_days > 1:
        return f"בעוד {diff_days} ימים"
    elif diff_days == 1:
        return "מחר"
    elif diff_days == 0:
        diff_seconds = (target_time - now).total_seconds()
        hours = int(diff_seconds // 3600)
        if hours > 0:
            return f"היום, בעוד כ-{hours} שעות"
        elif diff_seconds > 0:
            return "ממש בקרוב (פחות משעה)"
        else:
            return "כבר התחיל / עבר"
    else:
        return "כבר עבר"

def load_data(filename, default_value):
    if not os.path.exists(filename):
        with open(filename, 'w') as f: json.dump(default_value, f)
        return default_value
    with open(filename, 'r') as f: return json.load(f)

def save_data(filename, data):
    with open(filename, 'w') as f: json.dump(data, f)

# ================= ניהול אירועים אישיים =================
def get_personal_events(user_id):
    data = load_data(PERSONAL_EVENTS_FILE, {})
    return data.get(str(user_id), [])

def add_personal_event(user_id, course, event_time_str):
    data = load_data(PERSONAL_EVENTS_FILE, {})
    if str(user_id) not in data:
        data[str(user_id)] = []
    data[str(user_id)].append({'course': course, 'time': event_time_str})
    save_data(PERSONAL_EVENTS_FILE, data)

def get_admins_dict():
    data = load_data(ADMINS_FILE, {str(MASTER_ADMIN_ID): "מנהל ראשי"})
    if isinstance(data, list):
        new_dict = {str(MASTER_ADMIN_ID): "מנהל ראשי"}
        for admin_id in data:
            if str(admin_id) != str(MASTER_ADMIN_ID): new_dict[str(admin_id)] = "נציג/ה"
        save_data(ADMINS_FILE, new_dict)
        return new_dict
    if str(MASTER_ADMIN_ID) not in data:
        data[str(MASTER_ADMIN_ID)] = "מנהל ראשי"
        save_data(ADMINS_FILE, data)
    return data

# --- מערכת תאריכים מתקדמת של המערכת ---
def get_schedule_by_range(user_id, start_dt, end_dt, title_prefix="הלו\"ז שלך"):
    events = load_data(EVENTS_FILE, [])
    registrations = load_data(REGISTRATIONS_FILE, {})

    filtered = []
    for e in events:
        target = e.get('target', 'all')
        if target != 'all':
            parts = target.split('|')
            if len(parts) == 2:
                reg_id, group_name = parts
                user_reg = registrations.get(reg_id, {}).get("users", {}).get(str(user_id))
                if not user_reg or user_reg['group'] != group_name:
                    continue

        event_dt = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
        if start_dt <= event_dt <= end_dt:
            filtered.append(e)

    if not filtered:
        return f"📭 <b>{title_prefix}:</b>\nאין אירועי מערכת מתוכננים בטווח הזה."

    filtered.sort(key=lambda x: x['time'])
    text = f"📅 <b>{title_prefix}:</b>\n\n"
    for e in filtered:
        dt = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
        day_name = HEBREW_DAYS[dt.weekday()]
        text += f"• <b>{e['course']}</b>\n  {e['type']} | יום {day_name}, {dt.strftime('%d/%m')} ב-{dt.strftime('%H:%M')}\n\n"
    return text

# --- שילוב אירועי מערכת עם אירועים אישיים של המשתמש הספציפי ---
def get_combined_schedule(user_id, start_dt, end_dt, title_prefix="הלו\"ז שלך"):
    system_text = get_schedule_by_range(user_id, start_dt, end_dt, title_prefix)

    personal_events = get_personal_events(user_id)
    personal_filtered = []

    for e in personal_events:
        event_dt = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
        if start_dt <= event_dt <= end_dt:
            personal_filtered.append(e)

    if personal_filtered:
        personal_filtered.sort(key=lambda x: x['time'])
        system_text += "\n👤 <b>אירועים אישיים שלך:</b>\n\n"
        for e in personal_filtered:
            dt = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
            day_name = HEBREW_DAYS[dt.weekday()]
            system_text += f"• <b>{e['course']}</b>\n  יום {day_name}, {dt.strftime('%d/%m')} ב-{dt.strftime('%H:%M')}\n\n"

    return system_text

def add_event_to_db(course, event_type, event_time_str, target="all"):
    events = load_data(EVENTS_FILE, [])
    now = datetime.now(ISRAEL_TZ)
    valid_events = [e for e in events if ISRAEL_TZ.localize(datetime.fromisoformat(e['time'])) > now]
    valid_events.append({'course': course, 'type': event_type, 'time': event_time_str, 'target': target})
    save_data(EVENTS_FILE, valid_events)

# ================= פונקציות הבוט המרכזיות =================
async def send_weekly_summary(bot):
    events = load_data(EVENTS_FILE, [])
    registrations = load_data(REGISTRATIONS_FILE, {})
    users = load_data(USERS_FILE, [])
    now = datetime.now(ISRAEL_TZ)

    start_of_summary = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_summary = (start_of_summary + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=0)

    for user_id in users:
        # --- 1. איסוף אירועי מערכת (חובה + דדליינים) ---
        weekly_mandatory = []
        for e in events:
            # מזהה אירועי נוכחות חובה ואירועי "דד-ליין"
            if e['type'] == "🔴 נוכחות חובה" or "דד" in e['type']:
                target = e.get('target', 'all')
                if target != 'all':
                    parts = target.split('|')
                    if len(parts) == 2:
                        reg_id, group_name = parts
                        user_reg = registrations.get(reg_id, {}).get("users", {}).get(str(user_id))
                        if not user_reg or user_reg['group'] != group_name:
                            continue

                dt = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
                if start_of_summary <= dt <= end_of_summary:
                    weekly_mandatory.append({'course': e['course'], 'time': dt, 'type': e['type']})

        # --- 2. איסוף אירועים אישיים של המשתמש ---
        personal_events = get_personal_events(user_id)
        weekly_personal = []
        for pe in personal_events:
            p_dt = ISRAEL_TZ.localize(datetime.fromisoformat(pe['time']))
            if start_of_summary <= p_dt <= end_of_summary:
                weekly_personal.append({'course': pe['course'], 'time': p_dt})

        # --- 3. בניית ההודעה ---
        if not weekly_mandatory and not weekly_personal:
            empty_msg = (
                "✨ <b>סיכום שבועי:</b> ✨\n\n"
                "בדקתי במערכת ו... <b>אין לך שום אירועים מתוכננים לשבוע הקרוב!</b> 🎉\n"
                "<i>שיהיה שבוע רגוע ומלא בחוויות טובות!</i> ☕"
            )
            try:
                await bot.send_message(chat_id=user_id, text=empty_msg, parse_mode='HTML')
            except: pass
            continue

        summary_text = (
            "🌟 <b>הלו\"ז השבועי שלך מוכן!</b> 🌟\n"
            f"🗓️ <b>שבוע:</b> <code>{start_of_summary.strftime('%d/%m')}</code> ➖ <code>{end_of_summary.strftime('%d/%m')}</code>\n"
        )

        if weekly_mandatory:
            summary_text += "\n➖➖➖➖➖➖➖➖➖➖\n🎓 <b>אירועי מערכת (חובה ודד-ליינים):</b>\n"
            for ev in sorted(weekly_mandatory, key=lambda x: x['time']):
                day_name = HEBREW_DAYS[ev['time'].weekday()]
                time_str = ev['time'].strftime('%H:%M')
                date_str = ev['time'].strftime('%d/%m')
                # שמים אמוג'י שונה אם זה דד-ליין
                emoji = "🚨" if "דד" in ev['type'] else "🔹"
                summary_text += f"{emoji} <b>{ev['course']}</b> | יום {day_name}, {date_str} ב-{time_str}\n"

        if weekly_personal:
            summary_text += "\n➖➖➖➖➖➖➖➖➖➖\n👤 <b>האירועים האישיים שלך:</b>\n"
            for ev in sorted(weekly_personal, key=lambda x: x['time']):
                day_name = HEBREW_DAYS[ev['time'].weekday()]
                time_str = ev['time'].strftime('%H:%M')
                date_str = ev['time'].strftime('%d/%m')
                summary_text += f"🔸 <b>{ev['course']}</b> | יום {day_name}, {date_str} ב-{time_str}\n"

        summary_text += (
            "\n➖➖➖➖➖➖➖➖➖➖\n"
            "💪 <b>שיהיה שבוע אש ובהצלחה!</b> 🚀"
        )

        try:
            await bot.send_message(chat_id=user_id, text=summary_text, parse_mode='HTML')
        except: pass

async def delete_old_messages(bot, course_id):
    history = load_data(MESSAGES_FILE, {})
    if course_id in history:
        for msg in history[course_id]:
            try: await bot.delete_message(chat_id=msg['chat_id'], message_id=msg['message_id'])
            except: pass
        del history[course_id]
        save_data(MESSAGES_FILE, history)

async def send_formatted_broadcast(bot, text, course_id=None, reply_markup=None, target='all'):
    users = load_data(USERS_FILE, [])
    registrations = load_data(REGISTRATIONS_FILE, {})

    target_users = []
    if target == 'all':
        target_users = users
    else:
        parts = target.split('|')
        if len(parts) == 2:
            reg_id, group_name = parts
            for uid, info in registrations.get(reg_id, {}).get("users", {}).items():
                if info['group'] == group_name and not uid.startswith("manual_"):
                    target_users.append(uid)

    sent_details = []
    success_count = 0

    for user_id in target_users:
        try_count = 0
        while try_count < 3:
            try:
                msg = await bot.send_message(chat_id=user_id, text=text, parse_mode='HTML', reply_markup=reply_markup)
                success_count += 1
                if course_id: sent_details.append({'chat_id': user_id, 'message_id': msg.message_id})
                break

            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                try_count += 1

            except Exception as e:
                break

        await asyncio.sleep(0.05)

    if course_id and sent_details:
        history = load_data(MESSAGES_FILE, {})
        if course_id not in history: history[course_id] = []
        history[course_id].extend(sent_details)
        save_data(MESSAGES_FILE, history)

    return success_count

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    user_id = update.effective_user.id
    now = datetime.now(ISRAEL_TZ)

    user_state = context.user_data.get('state')

    menu_buttons = ["📅 מה יש היום?", "🗓️ לו\"ז שבועי", "⏭️ שבוע הבא", "🔍 לו\"ז לפי תאריך", "📝 הרשמות פתוחות", "🔗 קישורים חשובים", "👑 ניהול מערכת", "🔙 חזרה לתפריט הראשי"]
    if text in menu_buttons:
        context.user_data['state'] = None
        user_state = None

    if user_state == 'AWAITING_EVENT_NAME':
        context.user_data['temp_event_name'] = text
        context.user_data['state'] = 'AWAITING_EVENT_TIME'

        await update.message.reply_text(
            f"שמרתי: <b>{text}</b>.\n"
            f"עכשיו, שלח לי את התאריך והשעה בפורמט הבא: <code>DD/MM HH:MM</code>\n\n"
            f"לדוגמה: <code>15/06 18:30</code>",
            parse_mode='HTML'
        )
        return

    elif user_state == 'AWAITING_EVENT_TIME':
        try:
            dt = datetime.strptime(text.strip(), "%d/%m %H:%M").replace(year=now.year)
            course_name = context.user_data.get('temp_event_name')

            add_personal_event(user_id, course_name, dt.isoformat())

            context.user_data['state'] = None
            context.user_data['temp_event_name'] = None

            await update.message.reply_text(f"✅ האירוע <b>{course_name}</b> נוסף בהצלחה ללו\"ז האישי שלך!", parse_mode='HTML')
        except:
            await update.message.reply_text("⚠️ הזמן לא תקין. אנא נסה שוב בפורמט המדויק: `DD/MM HH:MM` (לדוגמה: `15/05 14:00`)", parse_mode='Markdown')
        return

    curr_weekday = (now.weekday() + 1) % 7
    start_of_this_week = (now - timedelta(days=curr_weekday)).replace(hour=0, minute=0, second=0)
    end_of_this_week = (start_of_this_week + timedelta(days=6)).replace(hour=23, minute=59, second=59)

    if text == "📅 מה יש היום?":
        start_today = now.replace(hour=0, minute=0, second=0)
        end_today = now.replace(hour=23, minute=59, second=59)
        response = get_combined_schedule(user_id, start_today, end_today, "לו\"ז להיום")
        await update.message.reply_text(response, parse_mode='HTML')

    elif text == "🗓️ לו\"ז שבועי":
        response = get_combined_schedule(user_id, start_of_this_week, end_of_this_week, "לו\"ז לשבוע הנוכחי (א'-ש')")
        await update.message.reply_text(response, parse_mode='HTML')

    elif text == "⏭️ שבוע הבא":
        start_next_week = start_of_this_week + timedelta(days=7)
        end_next_week = start_next_week + timedelta(days=6)
        response = get_combined_schedule(user_id, start_next_week, end_next_week, "לו\"ז לשבוע הבא")
        await update.message.reply_text(response, parse_mode='HTML')

    elif text == "🔍 לו\"ז לפי תאריך":
        await update.message.reply_text("שלח לי תאריך בפורמט DD/MM (לדוגמה: 15/05) ואבדוק מה מתוכנן.")

    elif text == "➕ הוסף אירוע אישי":
        context.user_data['state'] = 'AWAITING_EVENT_NAME'
        await update.message.reply_text("מעולה, בוא נוסיף אירוע אישי.\nמה שם האירוע? (לדוגמה: תור לרופא, עבודת סמינר, חתונה)")

    elif text == "📝 הרשמות פתוחות":
        registrations = load_data(REGISTRATIONS_FILE, {})
        if not registrations:
            await update.message.reply_text("📭 כרגע אין הרשמות פתוחות לקבוצות.")
            return

        await update.message.reply_text("📋 <b>הנה ההרשמות שפתוחות כרגע:</b>\n(ניתן ללחוץ כדי להירשם או לשנות שיבוץ)", parse_mode='HTML')

        for reg_id, data in registrations.items():
            keyboard = []
            for idx, opt in enumerate(data["options"]):
                callback_data = f"reg|{reg_id}|{idx}"
                keyboard.append([InlineKeyboardButton(opt, callback_data=callback_data)])

            keyboard.append([InlineKeyboardButton("❌ ביטול רישום", callback_data=f"reg|{reg_id}|cancel")])
            reply_markup = InlineKeyboardMarkup(keyboard)

            msg_text = f"📌 <b>{data['title']}</b>"
            await update.message.reply_text(msg_text, parse_mode='HTML', reply_markup=reply_markup)

    elif "/" in text and len(text) == 5:
        try:
            day, month = map(int, text.split("/"))
            search_date = now.replace(month=month, day=day, hour=0, minute=0, second=0)
            end_search = search_date.replace(hour=23, minute=59, second=59)
            response = get_combined_schedule(user_id, search_date, end_search, f"לו\"ז לתאריך {text}")
            await update.message.reply_text(response, parse_mode='HTML')
        except:
            await update.message.reply_text("התאריך לא תקין. נסה שוב בפורמט DD/MM.")

    elif text == "🔗 קישורים חשובים":
        links = "🔗 <b>קישורים שימושיים:</b>\n📂 <a href='https://drive.google.com/drive/u/1/folders/1A1g_caVz-94pkEbzHwIYSnQvuOqUJX6d'>הדיסק שנה ג׳</a>\n💻 <a href='https://orbitlive.huji.ac.il/Main.aspx'>פורטל הסטודנט</a>"
        await update.message.reply_text(links, parse_mode='HTML', disable_web_page_preview=True)

    elif text == "👑 ניהול מערכת":
        admins = get_admins_dict()
        if str(user_id) not in admins: return
        now = datetime.now(ISRAEL_TZ)
        valid_events = [{'id': e['course'].replace(" ", "_"), 'course': e['course'], 'type': e['type'], 'time': e['time'], 'target': e.get('target', 'all')}
            for e in load_data(EVENTS_FILE, []) if ISRAEL_TZ.localize(datetime.fromisoformat(e['time'])) > now]

        admins_list = [{'id': k, 'name': v} for k, v in admins.items()]
        registrations = load_data(REGISTRATIONS_FILE, {})

        payload_str = json.dumps({"events": valid_events, "admins": admins_list, "registrations": registrations}, ensure_ascii=False)
        compressed_data = base64.urlsafe_b64encode(zlib.compress(payload_str.encode('utf-8'))).decode('ascii')

        button = KeyboardButton(text="⚙️ כניסה לפאנל הניהול", web_app=WebAppInfo(url=f"{WEB_APP_URL}?cdata={compressed_data}"))
        reply_markup = ReplyKeyboardMarkup([[button], [KeyboardButton("🔙 חזרה לתפריט הראשי")]], resize_keyboard=True)
        await update.message.reply_text("הנתונים סונכרנו! לחץ למטה 👇", reply_markup=reply_markup)

    elif text == "🔙 חזרה לתפריט הראשי":
        reply_markup = ReplyKeyboardMarkup([
            ["📅 מה יש היום?", "🗓️ לו\"ז שבועי"],
            ["⏭️ שבוע הבא", "🔍 לו\"ז לפי תאריך"],
            ["📝 הרשמות פתוחות", "🔗 קישורים חשובים"],
            ["➕ הוסף אירוע אישי", "👑 ניהול מערכת"]
        ], resize_keyboard=True)
        await update.message.reply_text("חזרנו למסך הראשי.", reply_markup=reply_markup)

# ================= פקודות טקסט =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_id_str = str(user_id)
    users = load_data(USERS_FILE, [])
    if user_id not in users:
        users.append(user_id)
        save_data(USERS_FILE, users)

    keyboard = [
        [KeyboardButton("📅 מה יש היום?"), KeyboardButton("🗓️ לו\"ז שבועי")],
        [KeyboardButton("⏭️ שבוע הבא"), KeyboardButton("🔍 לו\"ז לפי תאריך")],
        [KeyboardButton("📝 הרשמות פתוחות"), KeyboardButton("🔗 קישורים חשובים")],
        [KeyboardButton("➕ הוסף אירוע אישי")]
    ]

    admins = get_admins_dict()
    if user_id_str in admins:
        keyboard[-1].append(KeyboardButton("👑 ניהול מערכת"))

    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("👋 ברוך הבא למערכת malmALarm!\nהשתמש בכפתורים למטה כדי לקבל מידע מעודכן.", reply_markup=reply_markup)

async def my_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"ה-ID שלך: `{update.effective_user.id}`", parse_mode='Markdown')

async def list_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admins = get_admins_dict()
    if str(update.effective_user.id) not in admins: return
    text = "👥 <b>צוות הניהול המוגדר במערכת:</b>\n\n"
    for idx, (adm_id, name) in enumerate(admins.items(), 1):
        role = "👑" if adm_id == str(MASTER_ADMIN_ID) else "👤"
        text += f"{idx}. {role} <b>{name}</b> (<code>{adm_id}</code>)\n"
    await update.message.reply_text(text, parse_mode='HTML')

# ================= טיפול בנתונים מהממשק =================
async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id_str = str(update.effective_user.id)
    admins = get_admins_dict()

    main_keyboard = [
        [KeyboardButton("📅 מה יש היום?"), KeyboardButton("🗓️ לו\"ז שבועי")],
        [KeyboardButton("⏭️ שבוע הבא"), KeyboardButton("🔍 לו\"ז לפי תאריך")],
        [KeyboardButton("📝 הרשמות פתוחות"), KeyboardButton("🔗 קישורים חשובים")],
        [KeyboardButton("➕ הוסף אירוע אישי"), KeyboardButton("👑 ניהול מערכת")]
    ]
    main_markup = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True)

    if user_id_str not in admins:
        await update.message.reply_text("⛔ גישה חסומה.", reply_markup=ReplyKeyboardRemove())
        return

    data = json.loads(update.message.web_app_data.data)
    action = data.get('action')

    if action == 'create_registration':
        course = data.get('course', '')
        options_text = data.get('options', '')
        options = [opt.strip() for opt in options_text.split(',') if opt.strip()]

        if course and options:
            reg_id = str(uuid.uuid4())[:8]
            registrations = load_data(REGISTRATIONS_FILE, {})
            registrations[reg_id] = {
                "title": course,
                "options": options,
                "users": {}
            }
            save_data(REGISTRATIONS_FILE, registrations)

            keyboard = []
            for idx, opt in enumerate(options):
                callback_data = f"reg|{reg_id}|{idx}"
                keyboard.append([InlineKeyboardButton(opt, callback_data=callback_data)])

            keyboard.append([InlineKeyboardButton("❌ ביטול רישום", callback_data=f"reg|{reg_id}|cancel")])

            reply_markup = InlineKeyboardMarkup(keyboard)
            msg_text = f"📋 <b>{course}</b>\n\nלחצו על הכפתור המתאים:"

            success = await send_formatted_broadcast(context.bot, msg_text, reply_markup=reply_markup)
            await update.message.reply_text(f"✅ הודעת הרישום נשלחה ל-{success} סטודנטים.", reply_markup=main_markup)
        return

    if action == 'delete_registration':
        reg_id = data.get('reg_id')
        registrations = load_data(REGISTRATIONS_FILE, {})
        if reg_id in registrations:
            del registrations[reg_id]
            save_data(REGISTRATIONS_FILE, registrations)
            await update.message.reply_text("🗑️ ההרשמה נמחקה בהצלחה מהמערכת.", reply_markup=main_markup)
        return

    if action == 'toggle_lock':
        reg_id = data.get('reg_id')
        registrations = load_data(REGISTRATIONS_FILE, {})
        if reg_id in registrations:
            current_status = registrations[reg_id].get("status", "open")
            registrations[reg_id]["status"] = "closed" if current_status == "open" else "open"
            save_data(REGISTRATIONS_FILE, registrations)

            state_msg = "ננעלה (סטודנטים לא יוכלו יותר להירשם)" if registrations[reg_id]["status"] == "closed" else "נפתחה מחדש"
            await update.message.reply_text(f"🔒 ההרשמה {state_msg}.", reply_markup=main_markup)
        return

    if action == 'remove_student':
        reg_id = data.get('reg_id')
        student_id = str(data.get('student_id'))
        registrations = load_data(REGISTRATIONS_FILE, {})

        if reg_id in registrations and student_id in registrations[reg_id]["users"]:
            student_name = registrations[reg_id]["users"][student_id]["name"]
            del registrations[reg_id]["users"][student_id]
            save_data(REGISTRATIONS_FILE, registrations)
            await update.message.reply_text(f"🗑️ הסטודנט {student_name} הוסר מהרשימה.", reply_markup=main_markup)
        return

    if action == 'manual_register':
        reg_id = data.get('reg_id')
        group_name = data.get('group')
        student_name = data.get('name')
        registrations = load_data(REGISTRATIONS_FILE, {})

        if reg_id in registrations:
            fake_id = "manual_" + str(uuid.uuid4())[:6]
            registrations[reg_id]["users"][fake_id] = {
                "name": f"👤 {student_name} (ידני)",
                "group": group_name,
                "time": datetime.now(ISRAEL_TZ).strftime('%d/%m %H:%M')
            }
            save_data(REGISTRATIONS_FILE, registrations)
            await update.message.reply_text(f"✅ {student_name} נוסף ידנית לקבוצה '{group_name}'.", reply_markup=main_markup)
        return

    if action == 'targeted_broadcast':
        reg_id = data.get('reg_id')
        group = data.get('group')
        text = data.get('text')
        registrations = load_data(REGISTRATIONS_FILE, {})

        target_users = []
        if reg_id in registrations:
            for uid, info in registrations[reg_id]['users'].items():
                if info['group'] == group and not uid.startswith("manual_"):
                    target_users.append(uid)

        if not target_users:
            await update.message.reply_text("אין משתמשים עם אפליקציה שמחוברים לקבוצה זו.", reply_markup=main_markup)
            return

        msg_text = f"📣 <b>הודעה ממוקדת ל{group} ({registrations[reg_id]['title']}):</b>\n\n{text}"
        success_count = 0
        for uid in target_users:
            try:
                await context.bot.send_message(chat_id=uid, text=msg_text, parse_mode='HTML')
                success_count += 1
            except: pass
        await update.message.reply_text(f"✅ נשלח ל-{success_count} סטודנטים בקבוצה.", reply_markup=main_markup)
        return

    if action == 'general_broadcast':
        text = data.get('text', '')
        target = data.get('target', 'all')
        if text:
            msg_text = f"📣 <b>הודעת תפוצה:</b>\n\n{text}"
            success = await send_formatted_broadcast(context.bot, msg_text, target=target)
            await update.message.reply_text(f"✅ ההודעה נשלחה בהצלחה ל-{success} סטודנטים.", reply_markup=main_markup)
        return

    if action in ['add_admin', 'remove_admin']:
        if user_id_str != str(MASTER_ADMIN_ID):
            await update.message.reply_text("⛔ פעולה חסומה: רק המנהל הראשי רשאי לנהל נציגים.", reply_markup=main_markup)
            return

        if action == 'add_admin':
            new_id = str(data.get('new_id'))
            name = data.get('name', 'נציג/ה')
            if new_id not in admins:
                admins[new_id] = name
                save_data(ADMINS_FILE, admins)
                await update.message.reply_text(f"✅ הנציג <b>{name}</b> נוסף למערכת.", parse_mode='HTML', reply_markup=main_markup)

        elif action == 'remove_admin':
            remove_id = str(data.get('admin_id'))
            if remove_id == str(MASTER_ADMIN_ID):
                await update.message.reply_text("⛔ לא ניתן להסיר את המנהל הראשי!", reply_markup=main_markup)
            elif remove_id in admins:
                removed_name = admins.pop(remove_id)
                save_data(ADMINS_FILE, admins)
                await update.message.reply_text(f"🗑️ הגישה של <b>{removed_name}</b> נשללה.", parse_mode='HTML', reply_markup=main_markup)

    elif action in ['broadcast', 'edit_event', 'cancel_event']:
        course = data.get('course', '')
        target = data.get('target', 'all')
        safe_id = course.replace(" ", "_")

        if action == 'cancel_event':
            c_id = data.get('course_id')
            await delete_old_messages(context.bot, c_id)
            events = load_data(EVENTS_FILE, [])
            save_data(EVENTS_FILE, [e for e in events if e['course'].replace(" ", "_") != c_id])
            try:
                scheduler.remove_job(f"{c_id}_24h")
                scheduler.remove_job(f"{c_id}_1h")
            except: pass
            await send_formatted_broadcast(context.bot, f"❌ <b>עדכון מערכת: אירוע בוטל</b>\n\nהאירוע <b>{c_id.replace('_', ' ')}</b> בוטל.")
            await update.message.reply_text("🗑️ האירוע בוטל.", reply_markup=main_markup)

        elif action in ['broadcast', 'edit_event']:
            event_time_naive = datetime.fromisoformat(data['time'])
            event_time = ISRAEL_TZ.localize(event_time_naive)

            if action == 'edit_event':
                old_id = data.get('old_id')
                await delete_old_messages(context.bot, old_id)
                events = load_data(EVENTS_FILE, [])
                save_data(EVENTS_FILE, [e for e in events if e['course'].replace(" ", "_") != old_id])
                try:
                    scheduler.remove_job(f"{old_id}_24h")
                    scheduler.remove_job(f"{old_id}_1h")
                except: pass
                prefix = "🔄 <b>עדכון לו\"ז:</b>"
            else:
                prefix = "📢 <b>עדכון חדש:</b>"

            add_event_to_db(course, data['type'], data['time'], target)

            day_name = HEBREW_DAYS[event_time.weekday()]
            time_left = get_time_remaining_str(event_time)

            msg_text = f"{prefix} {course}\n"
            msg_text += f"📌 <b>סוג:</b> {data['type']}\n"
            msg_text += f"⏰ <b>מועד:</b> יום {day_name}, {event_time.strftime('%d/%m ב-%H:%M')}\n"
            msg_text += f"⏳ <b>מתי?</b> {time_left}"

            success = await send_formatted_broadcast(context.bot, msg_text, safe_id, target=target)
            await update.message.reply_text(f"✅ האירוע נשמר ונשלח ל-{success} סטודנטים.", reply_markup=main_markup)

            # רק אם זה דד-ליין המערכת תשלח התראות 24h ו-1h
            if "דד" in data['type']:
                for hours in [24, 1]:
                    run_time = event_time - timedelta(hours=hours)
                    if run_time > datetime.now(ISRAEL_TZ):
                        scheduler.add_job(
                            send_formatted_broadcast,
                            'date',
                            run_date=run_time,
                            args=[context.bot, f"🚨 תזכורת דד-ליין: {course} בעוד {hours} שעות", safe_id, None, target],
                            id=f"{safe_id}_{hours}h",
                            misfire_grace_time=3600
                        )

# ================= טיפול בלחיצה על כפתור רישום =================
async def handle_registration_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    user_name = query.from_user.first_name

    parts = query.data.split("|")
    if len(parts) == 3:
        _, reg_id, opt_idx = parts
        registrations = load_data(REGISTRATIONS_FILE, {})
        if reg_id in registrations:
            if registrations[reg_id].get("status") == "closed":
                await query.answer("ההרשמה הזו ננעלה וסגורה לשינויים כרגע 🔒", show_alert=True)
                return

            if opt_idx == 'cancel':
                if user_id in registrations[reg_id]["users"]:
                    del registrations[reg_id]["users"][user_id]
                    save_data(REGISTRATIONS_FILE, registrations)
                    await query.answer("❌ הרישום שלך בוטל בהצלחה והוסרת מהרשימה.", show_alert=True)
                else:
                    await query.answer("לא היית רשום לאף קבוצה, הכל בסדר! 👍", show_alert=False)
                return

            opt_idx = int(opt_idx)
            options = registrations[reg_id]["options"]
            if opt_idx < len(options):
                group_name = options[opt_idx]
                existing_user = registrations[reg_id]["users"].get(user_id)
                if existing_user and existing_user["group"] == group_name:
                    await query.answer(f"אתה כבר רשום ל: {group_name} 😅", show_alert=False)
                    return

                registrations[reg_id]["users"][user_id] = {
                    "name": user_name,
                    "group": group_name,
                    "time": datetime.now(ISRAEL_TZ).strftime('%d/%m %H:%M')
                }
                save_data(REGISTRATIONS_FILE, registrations)

                if existing_user:
                    await query.answer(f"🔄 עברת בהצלחה לקבוצה: {group_name}!", show_alert=True)
                else:
                    await query.answer(f"✅ נרשמת בהצלחה ל: {group_name}!", show_alert=True)
            else:
                await query.answer("שגיאה: כפתור לא תקין.", show_alert=True)
        else:
            await query.answer("ההרשמה הזו נסגרה או לא קיימת יותר.", show_alert=True)

async def post_init(application):
    scheduler.start()

    # --- 1. תזמון הסיכום השבועי ---
    scheduler.add_job(
        send_weekly_summary,
        'cron',
        day_of_week='sat',
        hour=22,
        minute=5,
        args=[application.bot],
        misfire_grace_time=3600
    )

    # --- 2. מנגנון שחזור: מקים מחדש תזכורות לדד-ליינים קיימים במקרה שהשרת עושה ריסטארט ---
    events = load_data(EVENTS_FILE, [])
    now = datetime.now(ISRAEL_TZ)
    for e in events:
        if "דד" in e.get('type', ''):
            event_time = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
            if event_time > now:
                safe_id = e['course'].replace(" ", "_")
                target = e.get('target', 'all')
                for hours in [24, 1]:
                    run_time = event_time - timedelta(hours=hours)
                    # מריץ רק אם התזכורת עדיין רלוונטית ולא קיימת כרגע
                    if run_time > now and not scheduler.get_job(f"{safe_id}_{hours}h"):
                        scheduler.add_job(
                            send_formatted_broadcast,
                            'date',
                            run_date=run_time,
                            args=[application.bot, f"🚨 תזכורת דד-ליין: {e['course']} בעוד {hours} שעות", safe_id, None, target],
                            id=f"{safe_id}_{hours}h",
                            misfire_grace_time=3600
                        )

if __name__ == '__main__':
    PORT = int(os.environ.get('PORT', 8443))
    
    # הלינק המעודכן ל-Render שלך
    RENDER_URL = "https://malmalarmbot.onrender.com" 

    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", my_id_command))
    app.add_handler(CommandHandler("admins", list_admins_command))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
    app.add_handler(CallbackQueryHandler(handle_registration_click, pattern=r"^reg\|"))

    print("🚀 מתחיל ריצת Webhook מול Render...")
    
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        secret_token="MALMALARM_SECRET_123",
        webhook_url=f"{RENDER_URL}/{TOKEN}",
        drop_pending_updates=True  # שורת הקסם שמוחקת את פקק התנועה!
    )
