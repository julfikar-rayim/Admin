import logging
import sqlite3
import time
import re
import os
from functools import wraps
from dotenv import load_dotenv
from telegram import Update, ChatPermissions
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DB = "bot_data.sqlite"

def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    c.execute('''
        CREATE TABLE IF NOT EXISTS warns (
            chat_id INTEGER,
            user_id INTEGER,
            warns INTEGER,
            PRIMARY KEY (chat_id, user_id)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS filters (
            chat_id INTEGER,
            word TEXT,
            PRIMARY KEY (chat_id, word)
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS recent_msgs (
            chat_id INTEGER,
            user_id INTEGER,
            last_ts REAL,
            count INTEGER,
            PRIMARY KEY (chat_id, user_id)
        )
    ''')

    conn.commit()
    conn.close()

def admin_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        chat = update.effective_chat

        if user and user.id == OWNER_ID:
            return await func(update, context, *args, **kwargs)

        if chat and user:
            member = await chat.get_member(user.id)
            if member.status in ("administrator", "creator"):
                return await func(update, context, *args, **kwargs)

        await update.message.reply_text("এই কমান্ডটি চালাতে অ্যাডমিন হতে হবে।")
    return wrapped

def add_warn(chat_id, user_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT warns FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    row = c.fetchone()

    if row:
        warns = row[0] + 1
        c.execute("UPDATE warns SET warns=? WHERE chat_id=? AND user_id=?", (warns, chat_id, user_id))
    else:
        warns = 1
        c.execute("INSERT INTO warns VALUES(?,?,?)", (chat_id, user_id, warns))

    conn.commit()
    conn.close()
    return warns

def reset_warn(chat_id, user_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    conn.commit()
    conn.close()

def get_warn(chat_id, user_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT warns FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def add_filter(chat_id, word):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO filters(chat_id, word) VALUES(?,?)", (chat_id, word))
    conn.commit()
    conn.close()

def remove_filter(chat_id, word):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("DELETE FROM filters WHERE chat_id=? AND word=?", (chat_id, word))
    conn.commit()
    conn.close()

def get_filters(chat_id):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT word FROM filters WHERE chat_id=?", (chat_id,))
    words = [w[0] for w in c.fetchall()]
    conn.close()
    return words

FLOOD_TIME = 4
FLOOD_LIMIT = 5

def flood_count(chat_id, user_id):
    now = time.time()

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT last_ts, count FROM recent_msgs WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    row = c.fetchone()

    if row:
        last, count = row
        if now - last <= FLOOD_TIME:
            count += 1
        else:
            count = 1
        c.execute("UPDATE recent_msgs SET last_ts=?, count=? WHERE chat_id=? AND user_id=?", (now, count, chat_id, user_id))
    else:
        count = 1
        c.execute("INSERT INTO recent_msgs VALUES(?,?,?,?)", (chat_id, user_id, now, 1))

    conn.commit()
    conn.close()
    return count

async def auto_warn_ban(update, context, reason=""):
    chat = update.effective_chat
    user = update.message.from_user

    warns = add_warn(chat.id, user.id)
    await context.bot.send_message(chat.id, f"{user.mention_html()} – Warn {warns}. {reason}", parse_mode="HTML")

    if warns >= 3:
        try:
            await context.bot.ban_chat_member(chat.id, user.id)
            reset_warn(chat.id, user.id)
            await context.bot.send_message(chat.id, f"{user.mention_html()} ৩ Warn পূর্ণ — ব্যান করা হলো।", parse_mode="HTML")
        except:
            pass

LINK_REGEX = r"(https?://\S+|t\.me/\S+|www\.\S+|\S+\.\S{2,})"

async def check_links(update, context):
    msg = update.message
    text = msg.text.lower()
    chat = update.effective_chat

    if not re.search(LINK_REGEX, text):
        return False

    member = await chat.get_member(msg.from_user.id)

    if member.status in ("administrator", "creator"):
        return False

    try:
        await msg.delete()
    except:
        pass

    await auto_warn_ban(update, context, "লিংক শেয়ার করেছে")
    return True

async def start(update, context):
    await update.message.reply_text("আমি গ্রুপ কন্ট্রোল বট — /help লিখে সব কমান্ড দেখুন।")

async def help(update, context):
    await update.message.reply_text(
        "কমান্ডসমূহ:\n"
        "/ban - ব্যান\n"
        "/kick - কিক\n"
        "/mute <min> - মিউট\n"
        "/warn - Warn\n"
        "/resetwarns - Warn reset\n"
        "/addfilter <word> - Filter add\n"
        "/rmfilter <word> - Filter remove\n"
        "/filters - Filter list\n"
        "Link Filter ON\n"
        "Bad word filter ON\n"
        "Anti Spam ON"
    )

@admin_only
async def ban(update, context):
    if not update.message.reply_to_message:
        return await update.message.reply_text("রেপ্লাই করে /ban ব্যবহার করুন")

    target = update.message.reply_to_message.from_user

    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id)
        await update.message.reply_text(f"{target.mention_html()} ব্যান করা হয়েছে", parse_mode="HTML")
    except:
        await update.message.reply_text("ব্যান করা যায়নি")

@admin_only
async def kick(update, context):
    if not update.message.reply_to_message:
        return await update.message.reply_text("রেপ্লাই করে /kick")

    target = update.message.reply_to_message.from_user

    try:
        await context.bot.ban_chat_member(update.effective_chat.id, target.id, until_date=int(time.time()+5))
        await context.bot.unban_chat_member(update.effective_chat.id, target.id)
        await update.message.reply_text(f"{target.mention_html()} কিক করা হয়েছে", parse_mode="HTML")
    except:
        await update.message.reply_text("কিক করা যায়নি")

@admin_only
async def mute(update, context):
    if not update.message.reply_to_message:
        return await update.message.reply_text("রেপ্লাই করে /mute <min>")

    target = update.message.reply_to_message.from_user
    mins = int(context.args[0]) if context.args else 10

    try:
        await context.bot.restrict_chat_member(
            update.effective_chat.id,
            target.id,
            ChatPermissions(can_send_messages=False),
            until_date=int(time.time() + mins*60)
        )
        await update.message.reply_text(f"{target.mention_html()} {mins} মিনিটের জন্য মিউট", parse_mode="HTML")
    except:
        await update.message.reply_text("মিউট করা যায়নি")

@admin_only
async def addfilter_cmd(update, context):
    if not context.args:
        return await update.message.reply_text("/addfilter <word>")

    word = " ".join(context.args).lower()
    add_filter(update.effective_chat.id, word)
    await update.message.reply_text(f"Filter added: {word}")

@admin_only
async def rmfilter_cmd(update, context):
    if not context.args:
        return await update.message.reply_text("/rmfilter <word>")

    word = " ".join(context.args).lower()
    remove_filter(update.effective_chat.id, word)
    await update.message.reply_text(f"Filter removed: {word}")

async def filters_cmd(update, context):
    data = get_filters(update.effective_chat.id)
    if not data:
        return await update.message.reply_text("কোনো ফিল্টার নেই")
    await update.message.reply_text("\n".join(data))

async def welcome(update, context):
    for u in update.message.new_chat_members:
        await update.message.reply_text(f"স্বাগতম {u.mention_html()}!", parse_mode="HTML")

async def handler(update, context):
    msg = update.message
    if not msg or not msg.text:
        return

    if await check_links(update, context):
        return

    text = msg.text.lower()
    for w in get_filters(update.effective_chat.id):
        if w in text:
            try:
                await msg.delete()
            except:
                pass
            await auto_warn_ban(update, context, "Filter word ব্যবহার করেছে")
            return

    c = flood_count(update.effective_chat.id, msg.from_user.id)
    if c >= FLOOD_LIMIT:
        try:
            await context.bot.restrict_chat_member(
                update.effective_chat.id,
                msg.from_user.id,
                ChatPermissions(can_send_messages=False),
                until_date=int(time.time()+300)
            )
            await msg.reply_text("স্প্যাম শনাক্ত — ৫ মিনিট মিউট")
        except:
            pass


if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("kick", kick))
    app.add_handler(CommandHandler("mute", mute))
    app.add_handler(CommandHandler("addfilter", addfilter_cmd))
    app.add_handler(CommandHandler("rmfilter", rmfilter_cmd))
    app.add_handler(CommandHandler("filters", filters_cmd))

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handler))

    print("Bot started...")
    app.run_polling()
