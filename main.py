import json
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
MASTER_ADMIN_ID = 534078278

# הגדרת אזור זמן ישראל
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')
HEBREW_DAYS = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
scheduler = AsyncIOScheduler(timezone=ISRAEL_TZ)

def get_time_remaining_str(target_time):
    now = datetime.now(ISRAEL_TZ)
    diff = target_time - now
    days = diff.days
    hours = diff.seconds // 3600

    if days > 1: return f"בעוד {days} ימים"
    elif days == 1: return "מחר"
    elif days == 0 and hours > 0: return f"היום, בעוד כ-{hours} שעות"
    else: return "ממש בקרוב (פחות משעה)"

def load_data(filename, default_value):
    if not os.path.exists(filename):
        with open(filename, 'w') as f: json.dump(default_value, f)
        return default_value
    with open(filename, 'r') as f: return json.load(f)

def save_data(filename, data):
    with open(filename, 'w') as f: json.dump(data, f)

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

# --- שדרוג: מערכת תאריכים מתקדמת (מחליף את get_upcoming_schedule הישן) ---
def get_schedule_by_range(user_id, start_dt, end_dt, title_prefix="הלו\"ז שלך"):
    events = load_data(EVENTS_FILE, [])
    registrations = load_data(REGISTRATIONS_FILE, {})

    filtered = []
    for e in events:
        # בדיקת יעד (Target) - סינון לפי קבוצות
        target = e.get('target', 'all')
        if target != 'all':
            parts = target.split('|')
            if len(parts) == 2:
                reg_id, group_name = parts
                user_reg = registrations.get(reg_id, {}).get("users", {}).get(str(user_id))
                if not user_reg or user_reg['group'] != group_name:
                    continue # הסטודנט לא בקבוצה הזו, דלג על האירוע

        event_dt = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
        if start_dt <= event_dt <= end_dt:
            filtered.append(e)

    if not filtered: return f"📭 <b>{title_prefix}:</b>\nאין אירועים מתוכננים בטווח הזה."

    filtered.sort(key=lambda x: x['time'])
    text = f"📅 <b>{title_prefix}:</b>\n\n"
    for e in filtered:
        dt = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
        day_name = HEBREW_DAYS[dt.weekday()]
        text += f"• <b>{e['course']}</b>\n  {e['type']} | יום {day_name}, {dt.strftime('%d/%m')} ב-{dt.strftime('%H:%M')}\n\n"
    return text

def add_event_to_db(course, event_type, event_time_str, target="all"):
    events = load_data(EVENTS_FILE, [])
    now = datetime.now(ISRAEL_TZ)
    valid_events = [e for e in events if ISRAEL_TZ.localize(datetime.fromisoformat(e['time'])) > now]
    valid_events.append({'course': course, 'type': event_type, 'time': event_time_str, 'target': target})
    save_data(EVENTS_FILE, valid_events)

# ================= פונקציות הבוט המרכזיות =================
# --- שדרוג: סיכום שבועי חכם ---
async def send_weekly_summary(bot):
    events = load_data(EVENTS_FILE, [])
    registrations = load_data(REGISTRATIONS_FILE, {})
    users = load_data(USERS_FILE, [])
    now = datetime.now(ISRAEL_TZ)

    print(f"--- מתחיל שליחת סיכום שבועי ל-{len(users)} משתמשים ---")

    start_of_summary = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_summary = (start_of_summary + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=0)

    for user_id in users:
        weekly_mandatory = []
        for e in events:
            if e['type'] == "🔴 נוכחות חובה":
                target = e.get('target', 'all')
                if target != 'all':
                    reg_id, group_name = target.split('|')
                    user_reg = registrations.get(reg_id, {}).get("users", {}).get(str(user_id))
                    if not user_reg or user_reg['group'] != group_name:
                        continue

                dt = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
                if start_of_summary <= dt <= end_of_summary:
                    weekly_mandatory.append({'course': e['course'], 'time': dt})

        if not weekly_mandatory:
            try:
                await bot.send_message(chat_id=user_id, text="🎉 <b>סיכום שבועי:</b>\nאין לך אירועי נוכחות חובה בשבוע הקרוב. שבוע רגוע!", parse_mode='HTML')
                print(f"נשלח סיכום ריק בהצלחה למשתמש {user_id}")
            except Exception as err:
                print(f"❌ שגיאה בשליחת סיכום ריק למשתמש {user_id}: {err}")
            continue

        summary_text = f"📅 <b>לו\"ז חובה לשבוע הקרוב ({start_of_summary.strftime('%d/%m')}-{end_of_summary.strftime('%d/%m')}):</b>\n\n"
        for ev in sorted(weekly_mandatory, key=lambda x: x['time']):
            day_name = HEBREW_DAYS[ev['time'].weekday()]
            summary_text += f"• <b>{ev['course']}</b>\n  יום {day_name}, {ev['time'].strftime('%d/%m')} בשעה {ev['time'].strftime('%H:%M')}\n\n"

        summary_text += "<i>שיהיה שבוע מוצלח לכולם!</i>"
        try:
            await bot.send_message(chat_id=user_id, text=summary_text, parse_mode='HTML')
            print(f"נשלח סיכום מלא בהצלחה למשתמש {user_id}")
        except Exception as err:
            print(f"❌ שגיאה בשליחת סיכום מלא למשתמש {user_id}: {err}")

    print("--- סיום שליחת סיכום שבועי ---")

async def delete_old_messages(bot, course_id):
    history = load_data(MESSAGES_FILE, {})
    if course_id in history:
        for msg in history[course_id]:
            try: await bot.delete_message(chat_id=msg['chat_id'], message_id=msg['message_id'])
            except: pass
        del history[course_id]
        save_data(MESSAGES_FILE, history)

# --- שדרוג: שליחה לקבוצה בלבד ---
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
        while try_count < 3: # נותנים לבוט 3 הזדמנויות לשלוח לכל סטודנט
            try:
                msg = await bot.send_message(chat_id=user_id, text=text, parse_mode='HTML', reply_markup=reply_markup)
                success_count += 1
                if course_id: sent_details.append({'chat_id': user_id, 'message_id': msg.message_id})
                break # ההודעה נשלחה בהצלחה! יוצאים ממעגל הניסיונות של הסטודנט הזה

            except RetryAfter as e:
                # טלגרם ביקשה להמתין בגלל עומס
                await asyncio.sleep(e.retry_after + 1)
                try_count += 1

            except Exception as e:
                # שגיאה אמיתית (למשל: הסטודנט חסם את הבוט) - מדלגים
                break

        # המתנה מזערית בין כל הודעה כדי לא להקפיץ את מנגנון ההגנה של טלגרם
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

    # חישוב יום ראשון הקרוב (תחילת שבוע נוכחי)
    # ב-weekday של פייתון: 0=שני, 6=ראשון. אנחנו רוצים שראשון יהיה 0.
    curr_weekday = (now.weekday() + 1) % 7
    start_of_this_week = (now - timedelta(days=curr_weekday)).replace(hour=0, minute=0, second=0)
    end_of_this_week = (start_of_this_week + timedelta(days=6)).replace(hour=23, minute=59, second=59)

    if text == "📅 מה יש היום?":
        start_today = now.replace(hour=0, minute=0, second=0)
        end_today = now.replace(hour=23, minute=59, second=59)
        response = get_schedule_by_range(user_id, start_today, end_today, "לו\"ז להיום")
        await update.message.reply_text(response, parse_mode='HTML')

    elif text == "🗓️ לו\"ז שבועי":
        response = get_schedule_by_range(user_id, start_of_this_week, end_of_this_week, "לו\"ז לשבוע הנוכחי (א'-ש')")
        await update.message.reply_text(response, parse_mode='HTML')

    elif text == "⏭️ שבוע הבא":
        start_next_week = start_of_this_week + timedelta(days=7)
        end_next_week = start_next_week + timedelta(days=6)
        response = get_schedule_by_range(user_id, start_next_week, end_next_week, "לו\"ז לשבוע הבא")
        await update.message.reply_text(response, parse_mode='HTML')

    elif text == "🔍 לו\"ז לפי תאריך":
        await update.message.reply_text("שלח לי תאריך בפורמט DD/MM (לדוגמה: 15/05) ואבדוק מה מתוכנן.")

    elif text == "📝 הרשמות פתוחות":
        registrations = load_data(REGISTRATIONS_FILE, {})
        if not registrations:
            await update.message.reply_text("📭 כרגע אין הרשמות פתוחות לקבוצות.")
            return

        await update.message.reply_text("📋 <b>הנה ההרשמות שפתוחות כרגע:</b>\n(ניתן ללחוץ כדי להירשם או לשנות שיבוץ)", parse_mode='HTML')

        # עובר על כל ההרשמות הקיימות ומייצר להן את הכפתורים מחדש
        for reg_id, data in registrations.items():
            keyboard = []
            for idx, opt in enumerate(data["options"]):
                callback_data = f"reg|{reg_id}|{idx}"
                keyboard.append([InlineKeyboardButton(opt, callback_data=callback_data)])

            keyboard.append([InlineKeyboardButton("❌ ביטול רישום", callback_data=f"reg|{reg_id}|cancel")])
            reply_markup = InlineKeyboardMarkup(keyboard)

            msg_text = f"📌 <b>{data['title']}</b>"
            await update.message.reply_text(msg_text, parse_mode='HTML', reply_markup=reply_markup)

    # זיהוי תאריך שהמשתמש הקליד (למשל 12/04)
    elif "/" in text and len(text) == 5:
        try:
            day, month = map(int, text.split("/"))
            search_date = now.replace(month=month, day=day, hour=0, minute=0, second=0)
            end_search = search_date.replace(hour=23, minute=59, second=59)
            response = get_schedule_by_range(user_id, search_date, end_search, f"לו\"ז לתאריך {text}")
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
        
        # --- השינוי כאן: כיווץ הנתונים (ZLIB + Base64) ---
        payload_str = json.dumps({"events": valid_events, "admins": admins_list, "registrations": registrations}, ensure_ascii=False)
        compressed_data = base64.urlsafe_b64encode(zlib.compress(payload_str.encode('utf-8'))).decode('ascii')
        
        # שולחים בכתובת את המשתנה החדש cdata (Compressed Data)
        button = KeyboardButton(text="⚙️ כניסה לפאנל הניהול", web_app=WebAppInfo(url=f"{WEB_APP_URL}?cdata={compressed_data}"))
        reply_markup = ReplyKeyboardMarkup([[button], [KeyboardButton("🔙 חזרה לתפריט הראשי")]], resize_keyboard=True)
        await update.message.reply_text("הנתונים סונכרנו! לחץ למטה 👇", reply_markup=reply_markup)
    elif text == "🔙 חזרה לתפריט הראשי":
        reply_markup = ReplyKeyboardMarkup([
        ["📅 מה יש היום?", "🗓️ לו\"ז שבועי"],
        ["⏭️ שבוע הבא", "🔍 לו\"ז לפי תאריך"],
        ["📝 הרשמות פתוחות", "🔗 קישורים חשובים"],
        ["👑 ניהול מערכת"]
        ], resize_keyboard=True)
        await update.message.reply_text("חזרנו למסך הראשי.", reply_markup=reply_markup)

# ================= פקודות טקסט =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_id_str = str(user_id)
    users = load_data(USERS_FILE, [])
    if user_id not in users:
        users.append(user_id); save_data(USERS_FILE, users)

    keyboard = [
        [KeyboardButton("📅 מה יש היום?"), KeyboardButton("🗓️ לו\"ז שבועי")],
        [KeyboardButton("⏭️ שבוע הבא"), KeyboardButton("🔍 לו\"ז לפי תאריך")],
        [KeyboardButton("📝 הרשמות פתוחות"), KeyboardButton("🔗 קישורים חשובים")],
    ]

    admins = get_admins_dict()
    if user_id_str in admins:
        keyboard.append([KeyboardButton("👑 ניהול מערכת")])

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

    # --- הנה התיקון: המקלדת המעודכנת ---
    main_keyboard = [
        [KeyboardButton("📅 מה יש היום?"), KeyboardButton("🗓️ לו\"ז שבועי")],
        [KeyboardButton("⏭️ שבוע הבא"), KeyboardButton("🔍 לו\"ז לפי תאריך")],
        [KeyboardButton("📝 הרשמות פתוחות"), KeyboardButton("🔗 קישורים חשובים")],
        [KeyboardButton("👑 ניהול מערכת")] # (רק למנהלים כמובן)
    ]
    main_markup = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True)

    if user_id_str not in admins:
        await update.message.reply_text("⛔ גישה חסומה.", reply_markup=ReplyKeyboardRemove())
        return

    data = json.loads(update.message.web_app_data.data)
    action = data.get('action')

    # --- יצירת הרשמה חדשה עם אפשרויות טקסט חופשי ---
    if action == 'create_registration':
        course = data.get('course', '')
        options_text = data.get('options', '')
        options = [opt.strip() for opt in options_text.split(',') if opt.strip()]

        if course and options:
            reg_id = str(uuid.uuid4())[:8] # יצירת מזהה קצר
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

            # הוספת כפתור "ביטול רישום" קבוע בתחתית
            keyboard.append([InlineKeyboardButton("❌ ביטול רישום", callback_data=f"reg|{reg_id}|cancel")])

            reply_markup = InlineKeyboardMarkup(keyboard)
            msg_text = f"📋 <b>{course}</b>\n\nלחצו על הכפתור המתאים:"

            success = await send_formatted_broadcast(context.bot, msg_text, reply_markup=reply_markup)
            await update.message.reply_text(f"✅ הודעת הרישום נשלחה ל-{success} סטודנטים.", reply_markup=main_markup)
        return

    # --- מחיקת הרשמה ---
    if action == 'delete_registration':
        reg_id = data.get('reg_id')
        registrations = load_data(REGISTRATIONS_FILE, {})
        if reg_id in registrations:
            del registrations[reg_id]
            save_data(REGISTRATIONS_FILE, registrations)
            await update.message.reply_text("🗑️ ההרשמה נמחקה בהצלחה מהמערכת.", reply_markup=main_markup)
        return

    # --- נעילת הרשמה (הקפאה בלי למחוק את הנתונים) ---
    if action == 'toggle_lock':
        reg_id = data.get('reg_id')
        registrations = load_data(REGISTRATIONS_FILE, {})
        if reg_id in registrations:
            current_status = registrations[reg_id].get("status", "open")
            # הופך את המצב (מסגור לפתוח או מפתוח לסגור)
            registrations[reg_id]["status"] = "closed" if current_status == "open" else "open"
            save_data(REGISTRATIONS_FILE, registrations)

            state_msg = "ננעלה (סטודנטים לא יוכלו יותר להירשם)" if registrations[reg_id]["status"] == "closed" else "נפתחה מחדש"
            await update.message.reply_text(f"🔒 ההרשמה {state_msg}.", reply_markup=main_markup)
        return

    # --- הסרת סטודנט ספציפי מקבוצה ---
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

    # --- הוספת סטודנט ידנית ---
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

    # --- שליחת הודעה ממוקדת לקבוצה (דרך דאשבורד הרישומים) ---
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
            await update.message.reply_text(f"אין משתמשים עם אפליקציה שמחוברים לקבוצה זו.", reply_markup=main_markup)
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

    # --- הפצת הודעה כללית (משודרג: תומך בקהל יעד!) ---
    if action == 'general_broadcast':
        text = data.get('text', '')
        target = data.get('target', 'all') # <--- כאן המוח! מושך את קהל היעד שבחרת במסך
        if text:
            msg_text = f"📣 <b>הודעת תפוצה:</b>\n\n{text}"
            success = await send_formatted_broadcast(context.bot, msg_text, target=target)
            await update.message.reply_text(f"✅ ההודעה נשלחה בהצלחה ל-{success} סטודנטים.", reply_markup=main_markup)
        return

    # --- ניהול נציגים ---
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

    # --- יצירת אירועים ותזכורות (משודרג: תומך בקהל יעד!) ---
    elif action in ['broadcast', 'edit_event', 'cancel_event']:
        course = data.get('course', '')
        target = data.get('target', 'all') # <--- מושך את קהל היעד לאירועים
        safe_id = course.replace(" ", "_")

        if action == 'cancel_event':
            c_id = data.get('course_id')
            await delete_old_messages(context.bot, c_id)
            events = load_data(EVENTS_FILE, [])
            save_data(EVENTS_FILE, [e for e in events if e['course'].replace(" ", "_") != c_id])
            try:
                scheduler.remove_job(f"{c_id}_24h"); scheduler.remove_job(f"{c_id}_1h")
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
                    scheduler.remove_job(f"{old_id}_24h"); scheduler.remove_job(f"{old_id}_1h")
                except: pass
                prefix = "🔄 <b>עדכון לו\"ז:</b>"
            else:
                prefix = "📢 <b>עדכון חדש:</b>"

            add_event_to_db(course, data['type'], data['time'], target)

            # --- הבלוק החדש שמייצר את הטקסט המשודרג ---
            day_name = HEBREW_DAYS[event_time.weekday()]
            time_left = get_time_remaining_str(event_time)

            msg_text = f"{prefix} {course}\n"
            msg_text += f"📌 <b>סוג:</b> {data['type']}\n"
            msg_text += f"⏰ <b>מועד:</b> יום {day_name}, {event_time.strftime('%d/%m ב-%H:%M')}\n"
            msg_text += f"⏳ <b>מתי?</b> {time_left}"
            # ----------------------------------------

            success = await send_formatted_broadcast(context.bot, msg_text, safe_id, target=target)
            await update.message.reply_text(f"✅ האירוע נשמר ונשלח ל-{success} סטודנטים.", reply_markup=main_markup)

            for hours in [24, 1]:
                run_time = event_time - timedelta(hours=hours)
                if run_time > datetime.now(ISRAEL_TZ):
                    scheduler.add_job(send_formatted_broadcast, 'date', run_date=run_time,
                                      args=[context.bot, f"⏰ תזכורת: {course} בעוד {hours} שעות", safe_id, None, target], id=f"{safe_id}_{hours}h")

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

            # --- חסימת נעילה ---
            if registrations[reg_id].get("status") == "closed":
                await query.answer("ההרשמה הזו ננעלה וסגורה לשינויים כרגע 🔒", show_alert=True)
                return

            # --- טיפול בלחיצה על "ביטול רישום" ---
            if opt_idx == 'cancel':
                if user_id in registrations[reg_id]["users"]:
                    del registrations[reg_id]["users"][user_id]
                    save_data(REGISTRATIONS_FILE, registrations)
                    await query.answer("❌ הרישום שלך בוטל בהצלחה והוסרת מהרשימה.", show_alert=True)
                else:
                    await query.answer("לא היית רשום לאף קבוצה, הכל בסדר! 👍", show_alert=False)
                return

            # --- טיפול בהרשמה לקבוצה ---
            opt_idx = int(opt_idx)
            options = registrations[reg_id]["options"]
            if opt_idx < len(options):
                group_name = options[opt_idx]

                # בדיקה אם הסטודנט כבר רשום בדיוק לאותה קבוצה
                existing_user = registrations[reg_id]["users"].get(user_id)
                if existing_user and existing_user["group"] == group_name:
                    await query.answer(f"אתה כבר רשום ל: {group_name} 😅", show_alert=False)
                    return

                # רישום חדש (או עדכון קבוצה קיימת אם הוא בחר משהו אחר)
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
    scheduler.add_job(send_weekly_summary, 'cron', day_of_week='sat', hour=22, minute=7, args=[application.bot])

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", my_id_command))
    app.add_handler(CommandHandler("admins", list_admins_command))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
    app.add_handler(CallbackQueryHandler(handle_registration_click, pattern="^reg\|"))
    app.run_polling()