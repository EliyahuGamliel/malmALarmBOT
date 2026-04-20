import json
import os
import logging
import asyncio
import urllib.parse
import pytz
from datetime import datetime, timedelta
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes # <-- נוסף CallbackQueryHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================= הגדרות מערכת =================
TOKEN = '8595177968:AAEwImqSp432W2GD3YkNpvkzjjQqiwvmhOI'
WEB_APP_URL = 'https://eliyahugamliel.github.io/malmALarmBOT/' 
USERS_FILE = 'users.json'
ADMINS_FILE = 'admins.json'
MESSAGES_FILE = 'sent_messages.json'
EVENTS_FILE = 'events.json'
REGISTRATIONS_FILE = 'registrations.json' # <-- קובץ רישומים חדש
MASTER_ADMIN_ID = 534078278

# הגדרת אזור זמן ישראל
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
scheduler = AsyncIOScheduler(timezone=ISRAEL_TZ)

# ================= פונקציות מסד נתונים =================
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

def get_upcoming_schedule(days=1):
    events = load_data(EVENTS_FILE, [])
    now = datetime.now(ISRAEL_TZ)
    end_time = now + timedelta(days=days)
    
    filtered = []
    for e in events:
        event_dt = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
        if now <= event_dt <= end_time:
            filtered.append(e)
            
    if not filtered:
        return "נקי! אין אירועים מתוכננים לטווח הזמן הזה. 🎉"

    filtered.sort(key=lambda x: x['time'])
    text = "📅 <b>הלו\"ז המעודכן:</b>\n\n"
    for e in filtered:
        dt = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
        time_str = dt.strftime('%H:%M')
        date_str = dt.strftime('%d/%m')
        text += f"• <b>{e['course']}</b>\n  {e['type']} | {date_str} ב-{time_str}\n\n"
    return text

def add_event_to_db(course, event_type, event_time_str):
    events = load_data(EVENTS_FILE, [])
    now = datetime.now(ISRAEL_TZ)
    valid_events = []
    for e in events:
        dt = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
        if dt > now: valid_events.append(e)
        
    valid_events.append({'course': course, 'type': event_type, 'time': event_time_str})
    save_data(EVENTS_FILE, valid_events)

# ================= פונקציות הבוט המרכזיות =================
async def send_weekly_summary(bot):
    events = load_data(EVENTS_FILE, [])
    now = datetime.now(ISRAEL_TZ)
    next_week = now + timedelta(days=7)
    
    weekly_mandatory = []
    for e in events:
        if e['type'] == "🔴 נוכחות חובה":
            dt = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
            if now < dt < next_week: weekly_mandatory.append({'course': e['course'], 'time': dt})
            
    if not weekly_mandatory: 
        empty_msg = "🎉 <b>סיכום שבועי:</b>\nאין אירועי נוכחות חובה מתוכננים לשבוע הקרוב! שבוע רגוע ומוצלח לכולם."
        users = load_data(USERS_FILE, [])
        for user_id in users:
            try: await bot.send_message(chat_id=user_id, text=empty_msg, parse_mode='HTML')
            except: continue
        return
    
    summary_text = "📅 <b>סיכום שבועי: אירועי חובה</b>\n\n"
    for ev in sorted(weekly_mandatory, key=lambda x: x['time']):
        summary_text += f"• <b>{ev['course']}</b>\n  יום {ev['time'].strftime('%d/%m')} בשעה {ev['time'].strftime('%H:%M')}\n\n"
    summary_text += "<i>שיהיה שבוע מוצלח לכולם!</i>"
    
    users = load_data(USERS_FILE, [])
    for user_id in users:
        try: await bot.send_message(chat_id=user_id, text=summary_text, parse_mode='HTML')
        except: continue

async def delete_old_messages(bot, course_id):
    history = load_data(MESSAGES_FILE, {})
    if course_id in history:
        for msg in history[course_id]:
            try: await bot.delete_message(chat_id=msg['chat_id'], message_id=msg['message_id'])
            except: pass
        del history[course_id]
        save_data(MESSAGES_FILE, history)

# נוספה האפשרות לקבל reply_markup כדי שנוכל לשלוח כפתורים בתפוצה
async def send_formatted_broadcast(bot, text, course_id=None, reply_markup=None):
    users = load_data(USERS_FILE, [])
    sent_details = []
    success_count = 0 
    
    for user_id in users:
        try:
            msg = await bot.send_message(chat_id=user_id, text=text, parse_mode='HTML', reply_markup=reply_markup)
            success_count += 1
            if course_id: 
                sent_details.append({'chat_id': user_id, 'message_id': msg.message_id})
        except Exception as e: 
            print(f"❌ לא ניתן לשלוח הודעה למשתמש {user_id}: {e}")
            continue
            
    if course_id and sent_details:
        history = load_data(MESSAGES_FILE, {})
        if course_id not in history: history[course_id] = []
        history[course_id].extend(sent_details)
        save_data(MESSAGES_FILE, history)
        
    return success_count

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    
    if text == "📅 מה יש היום?":
        response = get_upcoming_schedule(days=1)
        await update.message.reply_text(response, parse_mode='HTML')
        
    elif text == "🗓️ לו\"ז שבועי":
        response = get_upcoming_schedule(days=7)
        await update.message.reply_text(response, parse_mode='HTML')
        
    elif text == "🔗 קישורים חשובים":
        links_text = (
            "🔗 <b>קישורים שימושיים למחזור:</b>\n\n"
            "📂 <a href='https://drive.google.com/drive/u/1/folders/1A1g_caVz-94pkEbzHwIYSnQvuOqUJX6d'>הדיסק שנה ג׳</a>\n"
            "💻 <a href='https://orbitlive.huji.ac.il/Main.aspx'>פורטל הסטודנט</a>"
        )
        await update.message.reply_text(links_text, parse_mode='HTML', disable_web_page_preview=True)

    elif text == "👑 ניהול מערכת":
        user_id_str = str(update.effective_user.id)
        admins = get_admins_dict()
        if user_id_str not in admins: return

        now = datetime.now(ISRAEL_TZ)
        raw_events = load_data(EVENTS_FILE, [])
        valid_events = [
            {'id': e['course'].replace(" ", "_"), 'course': e['course'], 'type': e['type'], 'time': e['time']} 
            for e in raw_events if ISRAEL_TZ.localize(datetime.fromisoformat(e['time'])) > now
        ]
        
        admins_list = [{'id': k, 'name': v} for k, v in admins.items()]
        payload = json.dumps({"events": valid_events, "admins": admins_list})
        safe_payload = urllib.parse.quote(payload)
        
        button = InlineKeyboardButton(text="⚙️ פתח פאנל ניהול", web_app=WebAppInfo(url=f"{WEB_APP_URL}?data={safe_payload}"))
        reply_markup = InlineKeyboardMarkup([[button]])
        await update.message.reply_text("מערכת הניהול מסונכרנת ומוכנה:", reply_markup=reply_markup)

# ================= פקודות טקסט =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_id_str = str(user_id)
    users = load_data(USERS_FILE, [])
    if user_id not in users:
        users.append(user_id); save_data(USERS_FILE, users)
    
    keyboard = [
        [KeyboardButton("📅 מה יש היום?"), KeyboardButton("🗓️ לו\"ז שבועי")],
        [KeyboardButton("🔗 קישורים חשובים")]
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
    if user_id_str not in admins:
        await update.message.reply_text("⛔ גישה חסומה.", reply_markup=ReplyKeyboardRemove())
        return 

    data = json.loads(update.message.web_app_data.data)
    action = data.get('action')

    # --- פתיחת הרשמה חדשה ---
    if action == 'create_registration':
        course = data.get('course', '')
        groups_count = int(data.get('groups', 0))

        if course and groups_count > 0:
            keyboard = []
            row = []
            for i in range(1, groups_count + 1):
                # הקוד שמועבר בכפתור: reg|שם הקורס|מספר הקבוצה
                callback_data = f"reg|{course}|{i}"
                row.append(InlineKeyboardButton(f"קבוצה {i}", callback_data=callback_data))
                # מסדרים 2 כפתורים בשורה
                if len(row) == 2 or i == groups_count:
                    keyboard.append(row)
                    row = []

            reply_markup = InlineKeyboardMarkup(keyboard)
            msg_text = f"📋 <b>הרשמה לקבוצות: {course}</b>\n\nלחצו על הכפתור המתאים כדי להירשם:"
            
            success = await send_formatted_broadcast(context.bot, msg_text, reply_markup=reply_markup)
            await update.message.reply_text(f"✅ הודעת הרישום נשלחה ל-{success} סטודנטים.")
        return 

    if action == 'general_broadcast':
        text = data.get('text', '')
        if text:
            msg_text = f"📣 <b>הודעת תפוצה:</b>\n\n{text}"
            success = await send_formatted_broadcast(context.bot, msg_text)
            await update.message.reply_text(f"✅ הודעת התפוצה נשלחה בהצלחה ל-{success} סטודנטים.")
        return 

    if action in ['add_admin', 'remove_admin']:
        if user_id_str != str(MASTER_ADMIN_ID):
            await update.message.reply_text("⛔ פעולה חסומה: רק המנהל הראשי רשאי לנהל נציגים.")
            return

        if action == 'add_admin':
            new_id = str(data.get('new_id'))
            name = data.get('name', 'נציג/ה')
            if new_id not in admins:
                admins[new_id] = name
                save_data(ADMINS_FILE, admins)
                await update.message.reply_text(f"✅ הנציג <b>{name}</b> נוסף למערכת.", parse_mode='HTML')

        elif action == 'remove_admin':
            remove_id = str(data.get('admin_id'))
            if remove_id == str(MASTER_ADMIN_ID):
                await update.message.reply_text("⛔ לא ניתן להסיר את המנהל הראשי!")
            elif remove_id in admins:
                removed_name = admins.pop(remove_id)
                save_data(ADMINS_FILE, admins)
                await update.message.reply_text(f"🗑️ הגישה של <b>{removed_name}</b> נשללה.", parse_mode='HTML')

    elif action in ['broadcast', 'edit_event', 'cancel_event']:
        course = data.get('course', '')
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
            await update.message.reply_text("🗑️ האירוע בוטל.")

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

            add_event_to_db(course, data['type'], data['time'])
            msg_text = f"{prefix} {course}\n📌 <b>סוג:</b> {data['type']}\n⏰ <b>מועד:</b> {event_time.strftime('%d/%m ב-%H:%M')}"
            success = await send_formatted_broadcast(context.bot, msg_text, safe_id)
            await update.message.reply_text(f"✅ נשלח ל-{success} סטודנטים.")

            for hours in [24, 1]:
                run_time = event_time - timedelta(hours=hours)
                if run_time > datetime.now(ISRAEL_TZ):
                    scheduler.add_job(send_formatted_broadcast, 'date', run_date=run_time, 
                                      args=[context.bot, f"⏰ תזכורת: {course} בעוד {hours} שעות", safe_id], id=f"{safe_id}_{hours}h")

# ================= טיפול בלחיצה על כפתור רישום =================
async def handle_registration_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    user_name = query.from_user.first_name

    # חילוץ הנתונים מהכפתור (reg|Course|Group)
    parts = query.data.split("|")
    if len(parts) == 3:
        _, course, group_num = parts
        
        registrations = load_data(REGISTRATIONS_FILE, {})
        if course not in registrations:
            registrations[course] = {}
            
        # שמירת הסטודנט בקבוצה המבוקשת
        registrations[course][str(user_id)] = {
            "name": user_name,
            "group": group_num,
            "time": datetime.now(ISRAEL_TZ).strftime('%d/%m %H:%M')
        }
        save_data(REGISTRATIONS_FILE, registrations)
        
        # הקפצת חלונית אישור לסטודנט
        await query.answer(f"✅ נרשמת בהצלחה לקבוצה {group_num} בקורס {course}!", show_alert=True)

async def post_init(application):
    scheduler.start()
    scheduler.add_job(send_weekly_summary, 'cron', day_of_week='sun', hour=11, minute=0, args=[application.bot])

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", my_id_command))
    app.add_handler(CommandHandler("admins", list_admins_command))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
    # מאזין חדש לכל הלחיצות על כפתורי הרישום
    app.add_handler(CallbackQueryHandler(handle_registration_click, pattern="^reg\|"))
    app.run_polling()