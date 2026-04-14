import json
import os
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

TOKEN = 'הטוקן_שלך_כאן'
WEB_APP_URL = 'הכתובת_שקיבלת_מגיטהאב_כאן'
USERS_FILE = 'users.json'

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

# --- פקודות הבוט ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_name = update.effective_user.first_name
    
    save_user(chat_id) # שומרים את הסטודנט החדש
    
    await update.message.reply_text(
        f"אהלן {user_name}! נרשמת בהצלחה למערכת העדכונים של שנה ג'.\n"
        f"מעכשיו תקבל לכאן תזכורות אישיות. 🩺"
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # כאן אפשר להוסיף תנאי שבודק אם ה-chat_id שייך לאדמין מורשה
    
    # יצירת כפתור מיוחד שפותח את ה-Mini App
    button = KeyboardButton(
        text="⚙️ פתח פאנל ניהול הפצות", 
        web_app=WebAppInfo(url=WEB_APP_URL)
    )
    markup = ReplyKeyboardMarkup([[button]], resize_keyboard=True)
    
    await update.message.reply_text("היי נציג! לחץ על הכפתור למטה כדי לשלוח עדכון למחזור:", reply_markup=markup)

# --- לוגיקת ההפצה (קבלת נתונים מה-Mini App) ---
async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # טלגרם מעבירה את הנתונים מה-Mini App כ-String
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

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()

    # רישום ההנדלרים
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    
    # הנדלר מיוחד שתופס את הנתונים שחוזרים מה-Mini App
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_web_app_data))

    print("הבוט רץ ומוכן...")
    app.run_polling()