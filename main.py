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
from flask import Flask
from threading import Thread

# ================= הגדרות מערכת =================
TOKEN = '8595177968:AAEwImqSp432W2GD3YkNpvkzjjQqiwvmhOI'
WEB_APP_URL = 'https://eliyahugamliel.github.io/malmALarmBOT/index.html'
USERS_FILE = 'users.json'
ADMINS_FILE = 'admins.json'
MESSAGES_FILE = 'sent_messages.json'
EVENTS_FILE = 'events.json'
REGISTRATIONS_FILE = 'registrations.json'
PERSONAL_EVENTS_FILE = 'personal_events.json' 
MASTER_ADMIN_ID = 534078278

ISRAEL_TZ = pytz.timezone('Asia/Jerusalem')
HEBREW_DAYS = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
scheduler = AsyncIOScheduler(timezone=ISRAEL_TZ)

def get_time_remaining_str(target_time):
    now = datetime.now(ISRAEL_TZ)
    diff_days = (target_time.date() - now.date()).days
    if diff_days > 1: return f"בעוד {diff_days} ימים"
    elif diff_days == 1: return "מחר"
    elif diff_days == 0:
        diff_seconds = (target_time - now).total_seconds()
        hours = int(diff_seconds // 3600)
        if hours > 0: return f"היום, בעוד כ-{hours} שעות"
        elif diff_seconds > 0: return "ממש בקרוב (פחות משעה)"
        else: return "כבר התחיל / עבר"
    else: return "כבר עבר"

def load_data(filename, default_value):
    if not os.path.exists(filename):
        with open(filename, 'w') as f: json.dump(default_value, f)
        return default_value
    with open(filename, 'r') as f: return json.load(f)

def save_data(filename, data):
    with open(filename, 'w') as f: json.dump(data, f)

def get_personal_events(user_id):
    return load_data(PERSONAL_EVENTS_FILE, {}).get(str(user_id), [])

def add_personal_event(user_id, course, event_time_str):
    data = load_data(PERSONAL_EVENTS_FILE, {})
    if str(user_id) not in data: data[str(user_id)] = []
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

def get_schedule_by_range(user_id, start_dt, end_dt, title_prefix="הלו\"ז שלך"):
    events, registrations, filtered = load_data(EVENTS_FILE, []), load_data(REGISTRATIONS_FILE, {}), []
    for e in events:
        target = e.get('target', 'all')
        if target != 'all':
            parts = target.split('|')
            if len(parts) == 2 and (not registrations.get(parts[0], {}).get("users", {}).get(str(user_id)) or registrations[parts[0]]["users"][str(user_id)]['group'] != parts[1]):
                continue 
        event_dt = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
        if start_dt <= event_dt <= end_dt: filtered.append(e)
    if not filtered: return f"📭 <b>{title_prefix}:</b>\nאין אירועי מערכת מתוכננים בטווח הזה."
    filtered.sort(key=lambda x: x['time'])
    text = f"📅 <b>{title_prefix}:</b>\n\n"
    for e in filtered:
        dt = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
        text += f"• <b>{e['course']}</b>\n  {e['type']} | יום {HEBREW_DAYS[dt.weekday()]}, {dt.strftime('%d/%m')} ב-{dt.strftime('%H:%M')}\n\n"
    return text

def get_combined_schedule(user_id, start_dt, end_dt, title_prefix="הלו\"ז שלך"):
    system_text = get_schedule_by_range(user_id, start_dt, end_dt, title_prefix)
    personal_filtered = [e for e in get_personal_events(user_id) if start_dt <= ISRAEL_TZ.localize(datetime.fromisoformat(e['time'])) <= end_dt]
    if personal_filtered:
        personal_filtered.sort(key=lambda x: x['time'])
        system_text += "\n👤 <b>אירועים אישיים שלך:</b>\n\n"
        for e in personal_filtered:
            dt = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
            system_text += f"• <b>{e['course']}</b>\n  יום {HEBREW_DAYS[dt.weekday()]}, {dt.strftime('%d/%m')} ב-{dt.strftime('%H:%M')}\n\n"
    return system_text

def add_event_to_db(course, event_type, event_time_str, target="all"):
    events = load_data(EVENTS_FILE, [])
    valid_events = [e for e in events if ISRAEL_TZ.localize(datetime.fromisoformat(e['time'])) > datetime.now(ISRAEL_TZ)]
    valid_events.append({'course': course, 'type': event_type, 'time': event_time_str, 'target': target})
    save_data(EVENTS_FILE, valid_events)

async def send_weekly_summary(bot):
    events, registrations, users = load_data(EVENTS_FILE, []), load_data(REGISTRATIONS_FILE, {}), load_data(USERS_FILE, [])
    now = datetime.now(ISRAEL_TZ)
    start_of_summary = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_summary = (start_of_summary + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=0)

    for user_id in users:
        weekly_mandatory = []
        for e in events:
            if e['type'] == "🔴 נוכחות חובה" or "דד" in e['type']:
                target = e.get('target', 'all')
                if target != 'all':
                    parts = target.split('|')
                    if len(parts) == 2 and (not registrations.get(parts[0], {}).get("users", {}).get(str(user_id)) or registrations[parts[0]]["users"][str(user_id)]['group'] != parts[1]):
                        continue
                dt = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
                if start_of_summary <= dt <= end_of_summary: weekly_mandatory.append({'course': e['course'], 'time': dt, 'type': e['type']})

        weekly_personal = [pe for pe in get_personal_events(user_id) if start_of_summary <= ISRAEL_TZ.localize(datetime.fromisoformat(pe['time'])) <= end_of_summary]
        for idx, pe in enumerate(weekly_personal): weekly_personal[idx]['time'] = ISRAEL_TZ.localize(datetime.fromisoformat(pe['time']))

        if not weekly_mandatory and not weekly_personal:
            try: await bot.send_message(chat_id=user_id, text="✨ <b>סיכום שבועי:</b> ✨\n\nבדקתי במערכת ו... <b>אין לך שום אירועים מתוכננים לשבוע הקרוב!</b> 🎉\n<i>שיהיה שבוע רגוע ומלא בחוויות טובות!</i> ☕", parse_mode='HTML')
            except: pass
            continue

        summary_text = f"🌟 <b>הלו\"ז השבועי שלך מוכן!</b> 🌟\n🗓️ <b>שבוע:</b> <code>{start_of_summary.strftime('%d/%m')}</code> ➖ <code>{end_of_summary.strftime('%d/%m')}</code>\n"
        if weekly_mandatory:
            summary_text += "\n➖➖➖➖➖➖➖➖➖➖\n🎓 <b>אירועי מערכת (חובה ודד-ליינים):</b>\n"
            for ev in sorted(weekly_mandatory, key=lambda x: x['time']):
                emoji = "🚨" if "דד" in ev['type'] else "🔹"
                summary_text += f"{emoji} <b>{ev['course']}</b> | יום {HEBREW_DAYS[ev['time'].weekday()]}, {ev['time'].strftime('%d/%m')} ב-{ev['time'].strftime('%H:%M')}\n"
        if weekly_personal:
            summary_text += "\n➖➖➖➖➖➖➖➖➖➖\n👤 <b>האירועים האישיים שלך:</b>\n"
            for ev in sorted(weekly_personal, key=lambda x: x['time']):
                summary_text += f"🔸 <b>{ev['course']}</b> | יום {HEBREW_DAYS[ev['time'].weekday()]}, {ev['time'].strftime('%d/%m')} ב-{ev['time'].strftime('%H:%M')}\n"
        summary_text += "\n➖➖➖➖➖➖➖➖➖➖\n💪 <b>שיהיה שבוע אש ובהצלחה!</b> 🚀"
        try: await bot.send_message(chat_id=user_id, text=summary_text, parse_mode='HTML')
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
    users, registrations, target_users = load_data(USERS_FILE, []), load_data(REGISTRATIONS_FILE, {}), []
    if target == 'all': target_users = users
    else:
        parts = target.split('|')
        if len(parts) == 2:
            for uid, info in registrations.get(parts[0], {}).get("users", {}).items():
                if info['group'] == parts[1] and not uid.startswith("manual_"): target_users.append(uid)

    sent_details, success_count = [], 0
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
            except Exception as e: break
        await asyncio.sleep(0.05)

    if course_id and sent_details:
        history = load_data(MESSAGES_FILE, {})
        if course_id not in history: history[course_id] = []
        history[course_id].extend(sent_details)
        save_data(MESSAGES_FILE, history)
    return success_count

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, user_id, now, user_state = update.message.text, update.effective_user.id, datetime.now(ISRAEL_TZ), context.user_data.get('state')
    menu_buttons = ["📅 מה יש היום?", "🗓️ לו\"ז שבועי", "⏭️ שבוע הבא", "🔍 לו\"ז לפי תאריך", "📝 הרשמות פתוחות", "🔗 קישורים חשובים", "👑 ניהול מערכת", "🔙 חזרה לתפריט הראשי"]
    if text in menu_buttons: context.user_data['state'], user_state = None, None

    if user_state == 'AWAITING_EVENT_NAME':
        context.user_data['temp_event_name'], context.user_data['state'] = text, 'AWAITING_EVENT_TIME'
        await update.message.reply_text(f"שמרתי: <b>{text}</b>.\nעכשיו, שלח לי את התאריך והשעה בפורמט: <code>DD/MM HH:MM</code>", parse_mode='HTML')
        return
    elif user_state == 'AWAITING_EVENT_TIME':
        try:
            dt = datetime.strptime(text.strip(), "%d/%m %H:%M").replace(year=now.year)
            add_personal_event(user_id, context.user_data.get('temp_event_name'), dt.isoformat())
            context.user_data['state'], context.user_data['temp_event_name'] = None, None
            await update.message.reply_text(f"✅ האירוע נוסף בהצלחה ללו\"ז האישי שלך!", parse_mode='HTML')
        except: await update.message.reply_text("⚠️ הזמן לא תקין. נסה שוב בפורמט: `DD/MM HH:MM`")
        return

    curr_weekday = (now.weekday() + 1) % 7
    start_of_this_week = (now - timedelta(days=curr_weekday)).replace(hour=0, minute=0, second=0)
    end_of_this_week = (start_of_this_week + timedelta(days=6)).replace(hour=23, minute=59, second=59)

    if text == "📅 מה יש היום?": await update.message.reply_text(get_combined_schedule(user_id, now.replace(hour=0, minute=0, second=0), now.replace(hour=23, minute=59, second=59), "לו\"ז להיום"), parse_mode='HTML')
    elif text == "🗓️ לו\"ז שבועי": await update.message.reply_text(get_combined_schedule(user_id, start_of_this_week, end_of_this_week, "לו\"ז לשבוע הנוכחי"), parse_mode='HTML')
    elif text == "⏭️ שבוע הבא": await update.message.reply_text(get_combined_schedule(user_id, start_of_this_week + timedelta(days=7), start_of_this_week + timedelta(days=13), "לו\"ז לשבוע הבא"), parse_mode='HTML')
    elif text == "🔍 לו\"ז לפי תאריך": await update.message.reply_text("שלח לי תאריך בפורמט DD/MM ואבדוק מה מתוכנן.")
    elif text == "➕ הוסף אירוע אישי":
        context.user_data['state'] = 'AWAITING_EVENT_NAME'
        await update.message.reply_text("מה שם האירוע?")
    elif text == "📝 הרשמות פתוחות":
        registrations = load_data(REGISTRATIONS_FILE, {})
        if not registrations: await update.message.reply_text("📭 כרגע אין הרשמות פתוחות.")
        else:
            await update.message.reply_text("📋 <b>הרשמות פתוחות:</b>", parse_mode='HTML')
            for reg_id, data in registrations.items():
                keyboard = [[InlineKeyboardButton(opt, callback_data=f"reg|{reg_id}|{idx}")] for idx, opt in enumerate(data["options"])] + [[InlineKeyboardButton("❌ ביטול רישום", callback_data=f"reg|{reg_id}|cancel")]]
                await update.message.reply_text(f"📌 <b>{data['title']}</b>", parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
    elif "/" in text and len(text) == 5:
        try:
            day, month = map(int, text.split("/"))
            search_date = now.replace(month=month, day=day, hour=0, minute=0, second=0)
            await update.message.reply_text(get_combined_schedule(user_id, search_date, search_date.replace(hour=23, minute=59, second=59), f"לו\"ז לתאריך {text}"), parse_mode='HTML')
        except: await update.message.reply_text("התאריך לא תקין. נסה בפורמט DD/MM.")
    elif text == "🔗 קישורים חשובים": await update.message.reply_text("🔗 <b>קישורים:</b>\n📂 <a href='https://drive.google.com/drive/u/1/folders/1A1g_caVz-94pkEbzHwIYSnQvuOqUJX6d'>הדיסק שנה ג׳</a>\n💻 <a href='https://orbitlive.huji.ac.il/Main.aspx'>פורטל הסטודנט</a>", parse_mode='HTML', disable_web_page_preview=True)
    elif text == "👑 ניהול מערכת":
        admins = get_admins_dict()
        if str(user_id) not in admins: return
        valid_events = [{'id': e['course'].replace(" ", "_"), 'course': e['course'], 'type': e['type'], 'time': e['time'], 'target': e.get('target', 'all')} for e in load_data(EVENTS_FILE, []) if ISRAEL_TZ.localize(datetime.fromisoformat(e['time'])) > now]
        payload_str = json.dumps({"events": valid_events, "admins": [{'id': k, 'name': v} for k, v in admins.items()], "registrations": load_data(REGISTRATIONS_FILE, {})}, ensure_ascii=False)
        button = KeyboardButton(text="⚙️ כניסה לפאנל הניהול", web_app=WebAppInfo(url=f"{WEB_APP_URL}?cdata={base64.urlsafe_b64encode(zlib.compress(payload_str.encode('utf-8'))).decode('ascii')}"))
        await update.message.reply_text("לחץ למטה 👇", reply_markup=ReplyKeyboardMarkup([[button], [KeyboardButton("🔙 חזרה לתפריט הראשי")]], resize_keyboard=True))
    elif text == "🔙 חזרה לתפריט הראשי": await update.message.reply_text("חזרנו למסך הראשי.", reply_markup=ReplyKeyboardMarkup([["📅 מה יש היום?", "🗓️ לו\"ז שבועי"], ["⏭️ שבוע הבא", "🔍 לו\"ז לפי תאריך"], ["📝 הרשמות פתוחות", "🔗 קישורים חשובים"], ["➕ הוסף אירוע אישי", "👑 ניהול מערכת"]], resize_keyboard=True))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    users = load_data(USERS_FILE, [])
    if user_id not in users:
        users.append(user_id)
        save_data(USERS_FILE, users)
    keyboard = [["📅 מה יש היום?", "🗓️ לו\"ז שבועי"], ["⏭️ שבוע הבא", "🔍 לו\"ז לפי תאריך"], ["📝 הרשמות פתוחות", "🔗 קישורים חשובים"], ["➕ הוסף אירוע אישי"]]
    if str(user_id) in get_admins_dict(): keyboard[-1].append("👑 ניהול מערכת")
    await update.message.reply_text("👋 ברוך הבא למערכת malmALarm!\nהשתמש בכפתורים למטה.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(k)] for row in keyboard for k in row] if False else [[KeyboardButton(k) for k in row] for row in keyboard], resize_keyboard=True))

async def my_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"ה-ID שלך: `{update.effective_user.id}`", parse_mode='Markdown')

async def list_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admins = get_admins_dict()
    if str(update.effective_user.id) not in admins: return
    text = "👥 <b>צוות ניהול:</b>\n" + "".join([f"{idx}. {'👑' if adm_id == str(MASTER_ADMIN_ID) else '👤'} <b>{name}</b> (<code>{adm_id}</code>)\n" for idx, (adm_id, name) in enumerate(admins.items(), 1)])
    await update.message.reply_text(text, parse_mode='HTML')

async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id_str, admins = str(update.effective_user.id), get_admins_dict()
    main_markup = ReplyKeyboardMarkup([["📅 מה יש היום?", "🗓️ לו\"ז שבועי"], ["⏭️ שבוע הבא", "🔍 לו\"ז לפי תאריך"], ["📝 הרשמות פתוחות", "🔗 קישורים חשובים"], ["➕ הוסף אירוע אישי", "👑 ניהול מערכת"]], resize_keyboard=True)
    if user_id_str not in admins:
        await update.message.reply_text("⛔ גישה חסומה.", reply_markup=ReplyKeyboardRemove())
        return
    data = json.loads(update.message.web_app_data.data)
    action = data.get('action')

    if action == 'create_registration':
        options = [opt.strip() for opt in data.get('options', '').split(',') if opt.strip()]
        if data.get('course', '') and options:
            reg_id, registrations = str(uuid.uuid4())[:8], load_data(REGISTRATIONS_FILE, {})
            registrations[reg_id] = {"title": data['course'], "options": options, "users": {}}
            save_data(REGISTRATIONS_FILE, registrations)
            success = await send_formatted_broadcast(context.bot, f"📋 <b>{data['course']}</b>\n\nלחצו על הכפתור המתאים:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(opt, callback_data=f"reg|{reg_id}|{idx}")] for idx, opt in enumerate(options)] + [[InlineKeyboardButton("❌ ביטול רישום", callback_data=f"reg|{reg_id}|cancel")]]))
            await update.message.reply_text(f"✅ נשלח ל-{success} סטודנטים.", reply_markup=main_markup)
    elif action == 'delete_registration':
        registrations = load_data(REGISTRATIONS_FILE, {})
        if data.get('reg_id') in registrations:
            del registrations[data['reg_id']]
            save_data(REGISTRATIONS_FILE, registrations)
            await update.message.reply_text("🗑️ ההרשמה נמחקה.", reply_markup=main_markup)
    elif action == 'toggle_lock':
        registrations = load_data(REGISTRATIONS_FILE, {})
        if data.get('reg_id') in registrations:
            registrations[data['reg_id']]["status"] = "closed" if registrations[data['reg_id']].get("status", "open") == "open" else "open"
            save_data(REGISTRATIONS_FILE, registrations)
            await update.message.reply_text(f"🔒 ההרשמה {'ננעלה' if registrations[data['reg_id']]['status'] == 'closed' else 'נפתחה'}.", reply_markup=main_markup)
    elif action == 'remove_student':
        registrations = load_data(REGISTRATIONS_FILE, {})
        if data.get('reg_id') in registrations and str(data.get('student_id')) in registrations[data['reg_id']]["users"]:
            del registrations[data['reg_id']]["users"][str(data['student_id'])]
            save_data(REGISTRATIONS_FILE, registrations)
            await update.message.reply_text(f"🗑️ הוסר מהרשימה.", reply_markup=main_markup)
    elif action == 'manual_register':
        registrations = load_data(REGISTRATIONS_FILE, {})
        if data.get('reg_id') in registrations:
            registrations[data['reg_id']]["users"]["manual_" + str(uuid.uuid4())[:6]] = {"name": f"👤 {data.get('name')} (ידני)", "group": data.get('group'), "time": datetime.now(ISRAEL_TZ).strftime('%d/%m %H:%M')}
            save_data(REGISTRATIONS_FILE, registrations)
            await update.message.reply_text(f"✅ נוסף ידנית.", reply_markup=main_markup)
    elif action == 'targeted_broadcast':
        registrations, target_users = load_data(REGISTRATIONS_FILE, {}), []
        if data.get('reg_id') in registrations:
            target_users = [uid for uid, info in registrations[data['reg_id']]['users'].items() if info['group'] == data.get('group') and not uid.startswith("manual_")]
        success_count = 0
        for uid in target_users:
            try:
                await context.bot.send_message(chat_id=uid, text=f"📣 <b>הודעה ל{data['group']}:</b>\n\n{data['text']}", parse_mode='HTML')
                success_count += 1
            except: pass
        await update.message.reply_text(f"✅ נשלח ל-{success_count} סטודנטים.", reply_markup=main_markup)
    elif action == 'general_broadcast':
        success = await send_formatted_broadcast(context.bot, f"📣 <b>הודעת תפוצה:</b>\n\n{data.get('text')}", target=data.get('target', 'all'))
        await update.message.reply_text(f"✅ נשלח ל-{success} סטודנטים.", reply_markup=main_markup)
    elif action in ['add_admin', 'remove_admin']:
        if user_id_str != str(MASTER_ADMIN_ID): return await update.message.reply_text("⛔ גישה חסומה.", reply_markup=main_markup)
        if action == 'add_admin': admins[str(data.get('new_id'))] = data.get('name', 'נציג/ה')
        else:
            if str(data.get('admin_id')) != str(MASTER_ADMIN_ID) and str(data.get('admin_id')) in admins: admins.pop(str(data['admin_id']))
        save_data(ADMINS_FILE, admins)
        await update.message.reply_text("✅ נשמר.", reply_markup=main_markup)
    elif action in ['broadcast', 'edit_event', 'cancel_event']:
        course, target, safe_id = data.get('course', ''), data.get('target', 'all'), data.get('course', '').replace(" ", "_")
        if action == 'cancel_event':
            await delete_old_messages(context.bot, data.get('course_id'))
            save_data(EVENTS_FILE, [e for e in load_data(EVENTS_FILE, []) if e['course'].replace(" ", "_") != data.get('course_id')])
            try:
                scheduler.remove_job(f"{data.get('course_id')}_24h")
                scheduler.remove_job(f"{data.get('course_id')}_1h")
            except: pass
            await send_formatted_broadcast(context.bot, f"❌ <b>אירוע בוטל</b>\n\nהאירוע <b>{data.get('course_id').replace('_', ' ')}</b> בוטל.")
            await update.message.reply_text("🗑️ בוטל.", reply_markup=main_markup)
        else:
            event_time = ISRAEL_TZ.localize(datetime.fromisoformat(data['time']))
            if action == 'edit_event':
                await delete_old_messages(context.bot, data.get('old_id'))
                save_data(EVENTS_FILE, [e for e in load_data(EVENTS_FILE, []) if e['course'].replace(" ", "_") != data.get('old_id')])
                try:
                    scheduler.remove_job(f"{data.get('old_id')}_24h")
                    scheduler.remove_job(f"{data.get('old_id')}_1h")
                except: pass
            add_event_to_db(course, data['type'], data['time'], target)
            success = await send_formatted_broadcast(context.bot, f"{'🔄' if action == 'edit_event' else '📢'} <b>{course}</b>\n📌 {data['type']}\n⏰ יום {HEBREW_DAYS[event_time.weekday()]}, {event_time.strftime('%d/%m ב-%H:%M')}\n⏳ {get_time_remaining_str(event_time)}", safe_id, target=target)
            await update.message.reply_text(f"✅ נשמר ונשלח ל-{success}.", reply_markup=main_markup)
            if "דד" in data['type']:
                for hours in [24, 1]:
                    if event_time - timedelta(hours=hours) > datetime.now(ISRAEL_TZ):
                        scheduler.add_job(send_formatted_broadcast, 'date', run_date=event_time - timedelta(hours=hours), args=[context.bot, f"🚨 דד-ליין: {course} בעוד {hours} שעות", safe_id, None, target], id=f"{safe_id}_{hours}h", misfire_grace_time=3600)

async def handle_registration_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split("|")
    if len(parts) == 3:
        _, reg_id, opt_idx = parts
        registrations = load_data(REGISTRATIONS_FILE, {})
        if reg_id in registrations:
            if registrations[reg_id].get("status") == "closed": return await query.answer("ההרשמה ננעלה 🔒", show_alert=True)
            if opt_idx == 'cancel':
                if str(query.from_user.id) in registrations[reg_id]["users"]:
                    del registrations[reg_id]["users"][str(query.from_user.id)]
                    save_data(REGISTRATIONS_FILE, registrations)
                    await query.answer("❌ הרישום בוטל.", show_alert=True)
                else: await query.answer("לא היית רשום.", show_alert=False)
                return
            group_name = registrations[reg_id]["options"][int(opt_idx)]
            existing_user = registrations[reg_id]["users"].get(str(query.from_user.id))
            if existing_user and existing_user["group"] == group_name: return await query.answer(f"אתה כבר רשום ל: {group_name}", show_alert=False)
            registrations[reg_id]["users"][str(query.from_user.id)] = {"name": query.from_user.first_name, "group": group_name, "time": datetime.now(ISRAEL_TZ).strftime('%d/%m %H:%M')}
            save_data(REGISTRATIONS_FILE, registrations)
            await query.answer(f"🔄 עברת ל: {group_name}" if existing_user else f"✅ נרשמת ל: {group_name}", show_alert=True)
        else: await query.answer("ההרשמה נסגרה.", show_alert=True)

async def post_init(application):
    scheduler.start()
    scheduler.add_job(send_weekly_summary, 'cron', day_of_week='sat', hour=23, minute=59, args=[application.bot], misfire_grace_time=3600)
    for e in load_data(EVENTS_FILE, []):
        if "דד" in e.get('type', ''):
            event_time = ISRAEL_TZ.localize(datetime.fromisoformat(e['time']))
            for hours in [24, 1]:
                if event_time - timedelta(hours=hours) > datetime.now(ISRAEL_TZ) and not scheduler.get_job(f"{e['course'].replace(' ', '_')}_{hours}h"):
                    scheduler.add_job(send_formatted_broadcast, 'date', run_date=event_time - timedelta(hours=hours), args=[application.bot, f"🚨 דד-ליין: {e['course']} בעוד {hours} שעות", e['course'].replace(" ", "_"), None, e.get('target', 'all')], id=f"{e['course'].replace(' ', '_')}_{hours}h", misfire_grace_time=3600)

# ================= שרת בובה כדי ש-Render לא יירדם =================
app_web = Flask(__name__)

@app_web.route('/')
def home():
    return "Bot is running 24/7!"

def run_web():
    # פותח את פורט הרשת של Render
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    # 1. מפעילים את שרת הבובה ברקע
    Thread(target=run_web).start()

    # 2. מפעילים את הבוט המקורי שלך!
    while True:
        try:
            print("🚀 מפעיל את הבוט בשיטת Polling...")
            app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("myid", my_id_command))
            app.add_handler(CommandHandler("admins", list_admins_command))
            app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
            app.add_handler(CallbackQueryHandler(handle_registration_click, pattern=r"^reg\|"))
            
            # Polling רגיל שמוחק את הפקק (drop_pending_updates)
            app.run_polling(drop_pending_updates=True, poll_interval=1.0)
        except Exception as e:
            logging.error(f"Error: {e}")
            time.sleep(15)
