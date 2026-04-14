import json
import os
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

TOKEN = '8595177968:AAEwImqSp432W2GD3YkNpvkzjjQqiwvmhOI'
WEB_APP_URL = 'https://eliyahugamliel.github.io/malmALarmBOT/'
USERS_FILE = 'users.json'

ADMINS_FILE = 'admins.json'
MASTER_ADMIN_ID = 534078278 # <--- החלף את זה ב-ID האמיתי שלך
# --- רשימת מורשים ---
ADMIN_IDS = [534078278]

# --- ניהול מסד הנתונים (JSON) ---
def load_users():
    if not os.path.exists(USERS_FILE):
        return []
    with open(USERS_FILE, 'r') as f:
        return json.load(f)

def save_user(chat_id):
    users = load_users()
    if chat_id not in users:
        users.append(chat_id)
        with open(USERS_FILE, 'w') as f:
            json.dump(users, f)

def load_admins():
    if not os.path.exists(ADMINS_FILE):
        # אם הקובץ לא קיים, ניצור אותו עם מנהל-העל בפנים כברירת מחדל
        with open(ADMINS_FILE, 'w') as f:
            json.dump([MASTER_ADMIN_ID], f)
        return [MASTER_ADMIN_ID]
    with open(ADMINS_FILE, 'r') as f:
        return json.load(f)

def save_admin(new_admin_id):
    admins = load_admins()
    if new_admin_id not in admins:
        admins.append(new_admin_id)
        with open(ADMINS_FILE, 'w') as f:
            json.dump(admins, f)
        return True # נוסף בהצלחה
    return False # כבר קיים

# --- פקודות הבוט ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_name = update.effective_user.first_name
    
    save_user(chat_id) # שומרים את הסטודנט החדש
    
    await update.message.reply_text(
        f"אהלן {user_name}! נרשמת בהצלחה למערכת העדכונים של שנה ג'.\n"
        f"מעכשיו תקבל לכאן תזכורות אישיות. 🩺"
    )

async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    admins = load_admins()
    
    # 1. מוודאים שמי שמנסה להוסיף מנהל הוא מנהל בעצמו
    if user_id not in admins:
        await update.message.reply_text("⛔ פעולה זו מורשית למנהלי המערכת בלבד.")
        return

    # 2. מושכים את המספר שהוקלד אחרי הפקודה
    try:
        # context.args מכיל את מה שנכתב אחרי הפקודה. למשל: /addadmin 12345
        new_id = int(context.args[0]) 
        
        if save_admin(new_id):
            await update.message.reply_text(f"✅ המשתמש {new_id} הוגדר כמנהל בהצלחה!")
        else:
            await update.message.reply_text("⚠️ המשתמש הזה כבר מוגדר כמנהל.")
            
    except (IndexError, ValueError):
        # אם האדמין כתב רק /addadmin בלי מספר, או כתב טקסט במקום מספר
        await update.message.reply_text("❌ שגיאה: יש להקליד את הפקודה יחד עם ה-ID של המנהל החדש.\nלדוגמה: `/addadmin 123456789`", parse_mode='Markdown')

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    
    # בדיקת הרשאות (The Gatekeeper)
    if user_id not in load_admins():
        await update.message.reply_text("⛔ סליחה, הפקודה הזו מיועדת לנציגי המחזור בלבד.")
        return
    
    # יצירת כפתור מיוחד שפותח את ה-Mini App (רק למי שעבר את הבדיקה)
    button = KeyboardButton(
        text="⚙️ פתח פאנל ניהול הפצות", 
        web_app=WebAppInfo(url=WEB_APP_URL)
    )
    markup = ReplyKeyboardMarkup([[button]], resize_keyboard=True)
    
    await update.message.reply_text("היי נציג! לחץ על הכפתור למטה כדי לשלוח עדכון למחזור:", reply_markup=markup)    # כאן אפשר להוסיף תנאי שבודק אם ה-chat_id שייך לאדמין מורשה
    
    # יצירת כפתור מיוחד שפותח את ה-Mini App
    button = KeyboardButton(
        text="⚙️ פתח פאנל ניהול הפצות", 
        web_app=WebAppInfo(url=WEB_APP_URL)
    )
    markup = ReplyKeyboardMarkup([[button]], resize_keyboard=True)
    
    await update.message.reply_text("היי נציג! לחץ על הכפתור למטה כדי לשלוח עדכון למחזור:", reply_markup=markup)

async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = json.loads(update.message.web_app_data.data)
    action = data.get('action')

    if action == 'add_admin':
        new_id = data.get('new_id')
        if save_admin(new_id):
            await update.message.reply_text(f"✅ מנהל {new_id} נוסף מהממשק!")
        else:
            await update.message.reply_text("⚠️ המנהל כבר קיים.")

    elif action == 'broadcast':
        raw_data = update.message.web_app_data.data
        data = json.loads(raw_data)
    
        # עיצוב ההודעה שתישלח לסטודנטים
        broadcast_msg = (
            f"📢 *עדכון לו\"ז חדש!*\n\n"
            f"📚 *קורס:* {data['course']}\n"
            f"📌 *סוג:* {data['type']}\n"
            f"🕒 *מתי:* {data['time']}\n"
        )
    
    # שליחת אישור לאדמין שההפצה התחילה
        await update.message.reply_text("מתחיל בהפצת העדכון לסטודנטים... ⏳")
    
    # לולאת ההפצה
        users = load_users()
        success_count = 0
    
        for chat_id in users:
            try:
                await context.bot.send_message(chat_id=chat_id, text=broadcast_msg, parse_mode='Markdown')
                success_count += 1
            except Exception as e:
                print(f"Failed to send to {chat_id}: {e}")
            # כאן אפשר בעתיד להסיר משתמשים שחסמו את הבוט
            
        await update.message.reply_text(f"✅ ההפצה הסתיימה! נשלח ל-{success_count} סטודנטים.")
        pass

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()

    # רישום ההנדלרים
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("addadmin", add_admin_command))

    # הנדלר מיוחד שתופס את הנתונים שחוזרים מה-Mini App
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))

    print("הבוט רץ ומוכן...")
    app.run_polling()