import json
import os
import logging
import asyncio
import urllib.parse
import pytz  # <-- ייבוא ספריית אזורי הזמן
from datetime import datetime, timedelta
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================= הגדרות מערכת =================
TOKEN = '8595177968:AAEwImqSp432W2GD3YkNpvkzjjQqiwvmhOI'
WEB_APP_URL = 'https://eliyahugamliel.github.io/malmALarmBOT/' 
USERS_FILE = 'users.json'
ADMINS_FILE = 'admins.json'
MESSAGES_FILE = 'sent_messages.json'
EVENTS_FILE = 'events.json'
MASTER_ADMIN_ID = 534078278 # <--- החלף את זה ב-ID האמיתי שלך

# הגדרת אזור זמן ישראל
ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
# עדכון המתזמן שיעבוד לפי שעון ישראל
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

def add_event_to_db(course, event_type, event_time_str):
    events = load_data(EVENTS_FILE, [])
    now = datetime.now(ISRAEL_TZ)
    valid_events = []
    for e in events:
        # המרת הזמן מהקובץ לזמן ישראל כדי לבדוק אם עבר
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
            try: 
                await bot.send_message(chat_id=user_id, text=empty_msg, parse_mode='HTML')
            except: 
                continue
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

async def send_formatted_broadcast(bot, text, course_id=None):
    users = load_data(USERS_FILE, [])
    sent_details = []
    for user_id in users:
        try:
            msg = await bot.send_message(chat_id=user_id, text=text, parse_mode='HTML')
            if course_id: sent_details.append({'chat_id': user_id, 'message_id': msg.message_id})
        except: continue
    if course_id and sent_details:
        history = load_data(MESSAGES_FILE, {})
        if course_id not in history: history[course_id] = []
        history[course_id].extend(sent_details)
        save_data(MESSAGES_FILE, history)
    return len(sent_details)

# ================= פקודות טקסט =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    users = load_data(USERS_FILE, [])
    if user_id not in users:
        users.append(user_id); save_data(USERS_FILE, users)
    await update.message.reply_text("👋 ברוך הבא למערכת malmALarm!")

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

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if str(update.effective_user.id) not in get_admins_dict(): return

    now = datetime.now(ISRAEL_TZ)
    raw_events = load_data(EVENTS_FILE, [])
    valid_events = [
        {'id': e['course'].replace(" ", "_"), 'course': e['course'], 'type': e['type'], 'time': e['time']} 
        for e in raw_events if ISRAEL_TZ.localize(datetime.fromisoformat(e['time'])) > now
    ]
    
    admins_list = [{'id': k, 'name': v} for k, v in get_admins_dict().items()]
    payload = json.dumps({"events": valid_events, "admins": admins_list})
    safe_payload = urllib.parse.quote(payload)
    
    button = KeyboardButton(text="⚙️ פתח פאנל ניהול", web_app=WebAppInfo(url=f"{WEB_APP_URL}?data={safe_payload}"))
    await update.message.reply_text("מערכת הניהול מסונכרנת ומוכנה:", reply_markup=ReplyKeyboardMarkup([[button]], resize_keyboard=True))

# ================= טיפול בנתונים מהממשק =================
async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id_str = str(update.effective_user.id)
    admins = get_admins_dict()
    
    if user_id_str not in admins:
        await update.message.reply_text("⛔ גישה חסומה.", reply_markup=ReplyKeyboardRemove())
        return 

    data = json.loads(update.message.web_app_data.data)
    action = data.get('action')

    # --- הבלוק החדש: הודעה חופשית ---
    if action == 'general_broadcast':
        text = data.get('text', '')
        if text:
            msg_text = f"📣 <b>הודעת תפוצה:</b>\n\n{text}"
            # אנחנו קוראים לפונקציית ההפצה הקיימת שלנו, אבל בלי לתת לה course_id כי זה לא אירוע שצריך למחוק אח"כ
            success = await send_formatted_broadcast(context.bot, msg_text)
            await update.message.reply_text(f"✅ הודעת התפוצה נשלחה בהצלחה ל-{success} סטודנטים.")
        return # עוצרים כאן כדי שלא ימשיך לבדוק שאר תנאים
    # ---------------------------------

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
            # המרת הזמן מהממשק לזמן ישראל
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
                # בדיקה מול השעה הנוכחית בישראל
                if run_time > datetime.now(ISRAEL_TZ):
                    scheduler.add_job(send_formatted_broadcast, 'date', run_date=run_time, 
                                      args=[context.bot, f"⏰ תזכורת: {course} בעוד {hours} שעות", safe_id], id=f"{safe_id}_{hours}h")

async def post_init(application):
    scheduler.start()
    scheduler.add_job(send_weekly_summary, 'cron', day_of_week='sun', hour=11, minute=0, args=[application.bot])

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", my_id_command))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("admins", list_admins_command))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
    app.run_polling()