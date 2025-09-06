import logging
import os
import asyncio
import time
import sys
import platform
import psutil # pip install psutil
import datetime
import html
import random
import textwrap
from io import StringIO
import aiohttp # pip install aiohttp for shorten URL (placeholder for now)
import json # for notes and lists persistence (simple in-memory with file save)

from pyrogram import Client, filters, enums
from pyrogram.types import Message, ChatPermissions
from pyrogram.errors import (
    UserNotParticipant, ChatAdminRequired, FloodWait, MessageTooLong,
    Forbidden, UserRestricted, ChannelPrivate, PeerIdInvalid,
    RPCError, UserIsBlocked, RPCError, UserNotBlocked, BadRequest
)

# --- تنظیمات و کانفیگ ---
# مقادیر API_ID و API_HASH را از متغیرهای محیطی بخوانید.
# در صورت عدم وجود، از مقادیر پیش‌فرض (که باید با اطلاعات خودتان جایگزین کنید) استفاده می‌کند.
# استفاده از متغیرهای محیطی امن‌تر است.

API_ID = int(os.environ.get("API_ID", "21481524")) # شناسه API خود را اینجا وارد کنید (عدد)
API_HASH = os.environ.get("API_HASH", "269244c75e7a9baba3e91f8915a369f3") # هش API خود را اینجا وارد کنید (رشته)
SESSION_NAME = os.environ.get("SESSION_NAME", "my_epic_userbot") # نام دلخواه برای فایل نشست ربات
PREFIX = os.environ.get("COMMAND_PREFIX", ".") # پیشوند دستورات، مثلاً .
OWNER_ID = os.environ.get("OWNER_ID", None) # شناسه عددی شما. برای دستورات حساس مانند exec/eval

if OWNER_ID:
    OWNER_ID = int(OWNER_ID)
    OWNER_IDS = [OWNER_ID]
else:
    OWNER_IDS = [] # اگر تنظیم نشود، فقط filters.me کار می کند.

# تنظیمات لاگینگ برای نمایش رویدادها و خطاها در کنسول و فایل
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[logging.FileHandler("userbot.log"), logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

# --- اینیشیالایز کردن کلاینت Pyrogram ---
user_bot = Client(
    name=SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
    parse_mode=enums.ParseMode.MARKDOWN, # تنظیم حالت پیش‌فرض برای Markdown
    no_updates=False # برای userbot باید False باشد تا آپدیت‌ها را دریافت کند
)

# --- متغیرهای گلوبال برای قابلیت‌ها ---
START_TIME = time.time()
AFK_MODE = False
AFK_MESSAGE = "من در حال حاضر نیستم. بعداً پاسخ خواهم داد."
AFK_LAST_SEEN = None
AFK_COUNT = {} # {chat_id: {user_id: count}}

# لیست‌های دشمن، دوستان، عشاق
# اینها در حافظه ذخیره می‌شوند. برای پایداری واقعی، نیاز به دیتابیس (SQLite) است.
# اما برای سادگی در یک فایل، ما آنها را به یک فایل JSON ذخیره و بارگذاری می‌کنیم.
ENEMIES_FILE = "enemies.json"
FRIENDS_FILE = "friends.json"
LOVERS_FILE = "lovers.json"
NOTES_FILE = "notes.json"
BANNED_WORDS_FILE = "banned_words.json"

ENEMIES = set()
FRIENDS = set()
LOVERS = set()
NOTES = {} # {note_name: note_content}
BANNED_WORDS = set()

# --- توابع کمکی ---

async def load_data(filename: str, default_data):
    """بارگذاری داده‌ها از فایل JSON."""
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                if filename.endswith("json"):
                    return json.load(f)
                else:
                    return {line.strip() for line in f if line.strip()}
        return default_data
    except Exception as e:
        logger.error(f"Error loading data from {filename}: {e}")
        return default_data

async def save_data(filename: str, data):
    """ذخیره داده‌ها در فایل JSON."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            if filename.endswith("json"):
                json.dump(data, f, ensure_ascii=False, indent=4)
            else:
                for item in data:
                    f.write(f"{item}\n")
        return True
    except Exception as e:
        logger.error(f"Error saving data to {filename}: {e}")
        return False

async def get_target_message(message: Message):
    """
    بررسی می کند که آیا پیام پاسخ به یک پیام دیگر است.
    اگر بله، آن پیام را برمی گرداند، در غیر این صورت پیام اصلی را.
    """
    if message.reply_to_message:
        return message.reply_to_message
    return message

async def get_target_user(client: Client, message: Message):
    """
    کاربر هدف را از پیام پاسخ داده شده یا از آرگومان (یوزرنیم/ID) برمی گرداند.
    """
    if message.reply_to_message:
        return message.reply_to_message.from_user
    elif len(message.command) > 1:
        try:
            user_id_or_username = message.command[1]
            return await client.get_users(user_id_or_username)
        except (ValueError, PeerIdInvalid, BadRequest):
            await message.edit_text("`کاربر مورد نظر یافت نشد یا شناسه/یوزرنیم نامعتبر است.`")
            await asyncio.sleep(2)
            await message.delete()
            return None
    return None

async def get_admin_rights(client: Client, chat_id: int):
    """
    بررسی می کند که آیا userbot در چت داده شده ادمین است و چه حقوقی دارد.
    """
    try:
        member = await client.get_chat_member(chat_id, "me")
        return member.privileges
    except (ChatAdminRequired, UserNotParticipant, PeerIdInvalid, ChannelPrivate):
        return None
    except Exception as e:
        logger.error(f"Error getting admin rights: {e}")
        return None

# --- قبل از شروع ربات، داده‌ها را بارگذاری کنید ---
async def load_initial_data():
    global ENEMIES, FRIENDS, LOVERS, NOTES, BANNED_WORDS
    ENEMIES = await load_data(ENEMIES_FILE, [])
    FRIENDS = await load_data(FRIENDS_FILE, [])
    LOVERS = await load_data(LOVERS_FILE, [])
    NOTES = await load_data(NOTES_FILE, {})
    BANNED_WORDS = await load_data(BANNED_WORDS_FILE, [])
    # تبدیل لیست ها به set برای جستجوی سریعتر
    ENEMIES = set(ENEMIES)
    FRIENDS = set(FRIENDS)
    LOVERS = set(LOVERS)
    BANNED_WORDS = set(BANNED_WORDS)
    logger.info("Initial data loaded.")

# --- هندلر برای پاسخ خودکار AFK ---
@user_bot.on_message(~filters.me & (filters.private | filters.mentioned), group=0)
async def afk_responder(client: Client, message: Message):
    global AFK_COUNT
    if AFK_MODE:
        user = message.from_user
        if user and user.id == user_bot.me.id: # اگر پیام از خود ربات است
            return
        
        # اگر کاربر در لیست دوستان باشد، پاسخ نده
        if user and str(user.id) in FRIENDS:
            return

        chat_id = message.chat.id
        user_id = str(user.id) if user else "anonymous"

        if chat_id not in AFK_COUNT:
            AFK_COUNT[chat_id] = {}
        if user_id not in AFK_COUNT[chat_id]:
            AFK_COUNT[chat_id][user_id] = 0
        AFK_COUNT[chat_id][user_id] += 1
        
        afk_duration = str(timedelta(seconds=int(time.time() - AFK_LAST_SEEN))) if AFK_LAST_SEEN else "نا معلوم"
        
        try:
            await message.reply_text(
                f"**{user_bot.me.first_name}** در حال حاضر آفلاین است.\n\n"
                f"`{AFK_MESSAGE}`\n\n"
                f"**آخرین بازدید:** `{afk_duration}` قبل\n"
                f"این {AFK_COUNT[chat_id][user_id]}امین باری است که شما پیام می دهید.",
                quote=True
            )
            logger.info(f"AFK response sent to user {user.id} in chat {chat_id}.")
            await asyncio.sleep(2) # تأخیر برای جلوگیری از Flood
        except FloodWait as e:
            logger.warning(f"FloodWait for AFK response: {e.value} seconds.")
            await asyncio.sleep(e.value)
        except Exception as e:
            logger.error(f"Error sending AFK response: {e}")

# --- هندلر برای حذف خودکار کلمات ممنوعه ---
@user_bot.on_message(~filters.me & filters.text & filters.group, group=1)
async def banned_words_deleter(client: Client, message: Message):
    if message.text:
        chat_id = message.chat.id
        admin_rights = await get_admin_rights(client, chat_id)
        if admin_rights and admin_rights.can_delete_messages:
            for word in BANNED_WORDS:
                if word.lower() in message.text.lower():
                    try:
                        await message.delete()
                        logger.info(f"Deleted message containing banned word '{word}' from user {message.from_user.id} in chat {chat_id}.")
                        # (اختیاری) می توانید یک پیام هشدار به ادمین یا کاربر بفرستید
                        # await client.send_message(chat_id, f"پیام کاربر {message.from_user.first_name} به دلیل استفاده از کلمه ممنوعه حذف شد.")
                        break # فقط یک بار حذف شود
                    except Exception as e:
                        logger.error(f"Error deleting banned word message: {e}")
        else:
            logger.debug(f"Userbot is not admin or doesn't have delete rights in chat {chat_id} to enforce banned words.")


# --- هندلرها و دستورات ربات (حدود 50+ قابلیت) ---

# 1. Ping: بررسی وضعیت ربات
@user_bot.on_message(filters.me & filters.command("ping", prefixes=PREFIX))
async def ping_command(client: Client, message: Message):
    """`.ping`: نمایش وضعیت ربات و زمان پاسخ (latency)."""
    start_time = time.time()
    try:
        await message.edit_text("`Pong!`")
        end_time = time.time()
        latency = round((end_time - start_time) * 1000)
        await message.edit_text(f"`Pong! {latency}ms`")
        logger.info(f"Command '{PREFIX}ping' executed. Latency: {latency}ms")
    except Exception as e:
        logger.error(f"Error in ping command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 2. ID: نمایش شناسه ها
@user_bot.on_message(filters.me & filters.command("id", prefixes=PREFIX))
async def id_command(client: Client, message: Message):
    """`.id`: نمایش ID چت فعلی یا ID کاربر/کانال/گروهی که به پیامش پاسخ داده شده."""
    target_message = await get_target_message(message)
    text = ""
    if target_message.from_user:
        user_id = target_message.from_user.id
        user_name = target_message.from_user.first_name
        text += f"**👤 شناسه کاربر:** `{user_id}`\n" \
                f"**نام:** `{user_name}`\n"
        if target_message.from_user.username:
            text += f"**یوزرنیم:** `@{target_message.from_user.username}`\n"
    
    if target_message.sender_chat:
        chat_id_sender = target_message.sender_chat.id
        chat_title_sender = target_message.sender_chat.title
        text += f"**🆔 شناسه فرستنده (کانال/گروه):** `{chat_id_sender}`\n" \
                f"**عنوان:** `{chat_title_sender}`\n"

    chat_id = target_message.chat.id
    chat_type = target_message.chat.type.name
    chat_title = target_message.chat.title if target_message.chat.title else "گپ خصوصی"
    
    text += f"\n**ℹ️ اطلاعات چت:**\n" \
            f"**شناسه چت:** `{chat_id}`\n" \
            f"**نوع چت:** `{chat_type}`\n" \
            f"**عنوان چت:** `{chat_title}`"
    if target_message.id != message.id:
        text += f"\n**شناسه پیام پاسخ داده شده:** `{target_message.id}`"

    try:
        await message.edit_text(text)
        logger.info(f"Command '{PREFIX}id' executed. Chat ID: {chat_id}.")
    except Exception as e:
        logger.error(f"Error in id command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(5)
    await message.delete()

# 3. UserInfo / Whois: نمایش اطلاعات کاربر
@user_bot.on_message(filters.me & filters.command(["userinfo", "whois"], prefixes=PREFIX))
async def userinfo_command(client: Client, message: Message):
    """`.userinfo [یوزرنیم|ID] / .whois [یوزرنیم|ID]`: نمایش اطلاعات کامل در مورد یک کاربر."""
    target_user = await get_target_user(client, message)
    if not target_user:
        await message.edit_text("`لطفاً یک یوزرنیم یا ID معتبر ارائه دهید یا به یک پیام پاسخ دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return

    text = (
        f"**اطلاعات کاربر:**\n"
        f"**🆔 ID:** `{target_user.id}`\n"
        f"**نام:** `{html.escape(target_user.first_name)}`\n"
    )
    if target_user.last_name:
        text += f"**نام خانوادگی:** `{html.escape(target_user.last_name)}`\n"
    if target_user.username:
        text += f"**یوزرنیم:** `@{target_user.username}`\n"
    text += (
        f"**آیا ربات است؟** `{target_user.is_bot}`\n"
        f"**زبان:** `{target_user.language_code or 'نامشخص'}`\n"
        f"**پروفایل لینک:** [Link](tg://user?id={target_user.id})\n"
    )
    if target_user.is_contact: text += "**مخاطب شماست.**\n"
    if target_user.is_verified: text += "**تایید شده.**\n"
    if target_user.is_scam: text += "**کلاهبردار.**\n"
    if target_user.is_support: text += "**پشتیبانی تلگرام.**\n"
    if target_user.is_fake: text += "**حساب فیک.**\n"
    
    if str(target_user.id) in ENEMIES: text += "**در لیست دشمنان است!**\n"
    if str(target_user.id) in FRIENDS: text += "**در لیست دوستان است!**\n"
    if str(target_user.id) in LOVERS: text += "**در لیست عشاق است!**\n"

    try:
        await message.edit_text(text, disable_web_page_preview=True)
        logger.info(f"Command '{PREFIX}userinfo' executed for user {target_user.id}.")
    except Exception as e:
        logger.error(f"Error in userinfo command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(5)
    await message.delete()

# 4. ChatInfo: نمایش اطلاعات چت
@user_bot.on_message(filters.me & filters.command("chatinfo", prefixes=PREFIX))
async def chatinfo_command(client: Client, message: Message):
    """`.chatinfo`: نمایش اطلاعات کامل در مورد چت فعلی."""
    chat = message.chat
    text = (
        f"**اطلاعات چت:**\n"
        f"**🆔 ID:** `{chat.id}`\n"
        f"**نوع چت:** `{chat.type.name}`\n"
    )
    if chat.title:
        text += f"**عنوان:** `{html.escape(chat.title)}`\n"
    if chat.username:
        text += f"**یوزرنیم (لینک):** `@{chat.username}`\n"
    if chat.members_count:
        text += f"**تعداد اعضا:** `{chat.members_count}`\n"
    if chat.description:
        text += f"**توضیحات:** `{html.escape(chat.description[:200])}{'...' if len(chat.description) > 200 else ''}`\n"
    if chat.invite_link:
        text += f"**لینک دعوت:** `{chat.invite_link}`\n"
    if chat.linked_chat:
        text += f"**چت لینک شده (فوروم/کامنت):** `{chat.linked_chat.title} ({chat.linked_chat.id})`\n"

    try:
        await message.edit_text(text, disable_web_page_preview=True)
        logger.info(f"Command '{PREFIX}chatinfo' executed for chat {chat.id}.")
    except MessageTooLong:
        await message.edit_text("`اطلاعات چت خیلی طولانی است و نمی‌تواند نمایش داده شود.`")
    except Exception as e:
        logger.error(f"Error in chatinfo command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(5)
    await message.delete()

# 5. Me: ارسال متن شما و حذف دستور
@user_bot.on_message(filters.me & filters.command("me", prefixes=PREFIX))
async def me_command(client: Client, message: Message):
    """`.me <متن>`: متن شما را ارسال می‌کند و پیام دستور را حذف می‌کند."""
    text_to_send = " ".join(message.command[1:])
    if not text_to_send:
        await message.edit_text("`لطفاً متنی برای ارسال ارائه دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    try:
        await client.send_message(message.chat.id, f"**{user_bot.me.first_name}:** _{text_to_send}_")
        await message.delete()
        logger.info(f"Command '{PREFIX}me' executed with text: '{text_to_send}'")
    except Exception as e:
        logger.error(f"Error in me command: {e}")
        await message.edit_text(f"`خطا: {e}`")
        await asyncio.sleep(3)
        await message.delete()

# 6. Echo: تکرار پیام
@user_bot.on_message(filters.me & filters.command("echo", prefixes=PREFIX))
async def echo_command(client: Client, message: Message):
    """`.echo <متن>` / (پاسخ به پیام) `.echo`: هر متنی را تکرار می‌کند."""
    text_to_echo = ""
    if message.reply_to_message:
        text_to_echo = message.reply_to_message.text or message.reply_to_message.caption or ""
    
    if len(message.command) > 1:
        text_to_echo = " ".join(message.command[1:])
    
    if text_to_echo:
        try:
            await message.edit_text(text_to_echo)
            logger.info(f"Command '{PREFIX}echo' executed with text: '{text_to_echo}'")
        except Exception as e:
            logger.error(f"Error in echo command: {e}")
            await message.edit_text(f"`خطا: {e}`")
    else:
        await message.edit_text("`متنی برای تکرار یافت نشد.`")
    await asyncio.sleep(3)
    await message.delete()

# 7. Type: شبیه‌سازی تایپ
@user_bot.on_message(filters.me & filters.command("type", prefixes=PREFIX))
async def type_command(client: Client, message: Message):
    """`.type`: شبیه‌سازی تایپ کردن برای چند ثانیه."""
    try:
        await client.send_chat_action(message.chat.id, enums.ChatAction.TYPING)
        await asyncio.sleep(5)
        await message.delete()
        logger.info(f"Command '{PREFIX}type' executed in chat {message.chat.id}.")
    except Exception as e:
        logger.error(f"Error in type command: {e}")
        await message.edit_text(f"`خطا در نمایش وضعیت تایپ: {e}`")
        await asyncio.sleep(3)
        await message.delete()

# 8. Del: حذف پیام پاسخ داده شده
@user_bot.on_message(filters.me & filters.command("del", prefixes=PREFIX))
async def delete_command(client: Client, message: Message):
    """(پاسخ به پیام) `.del`: حذف پیام پاسخ داده شده و دستور."""
    if not message.reply_to_message:
        await message.edit_text("`لطفاً برای حذف، به یک پیام پاسخ دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return

    try:
        await client.delete_messages(
            chat_id=message.chat.id,
            message_ids=[message.reply_to_message.id, message.id]
        )
        logger.info(f"Command '{PREFIX}del' executed. Deleted messages in chat {message.chat.id}.")
    except Exception as e:
        logger.error(f"Error deleting messages: {e}")
        await message.edit_text(f"`خطا در حذف پیام: {e}`")
        await asyncio.sleep(3)
        await message.delete()

# 9. Purge: حذف یک بازه از پیام‌ها
@user_bot.on_message(filters.me & filters.command("purge", prefixes=PREFIX))
async def purge_command(client: Client, message: Message):
    """(پاسخ به پیام A) ... `.purge`: حذف پیام‌ها از پیام A تا پیام دستور."""
    if not message.reply_to_message:
        await message.edit_text("`لطفاً برای پاکسازی، به اولین پیام در بازه مورد نظر پاسخ دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return

    try:
        start_message_id = message.reply_to_message.id
        end_message_id = message.id
        message_ids_to_delete = list(range(start_message_id, end_message_id + 1))
        
        await client.delete_messages(
            chat_id=message.chat.id,
            message_ids=message_ids_to_delete
        )
        logger.info(f"Command '{PREFIX}purge' executed. Purged messages from {start_message_id} to {end_message_id} in chat {message.chat.id}.")
    except Exception as e:
        logger.error(f"Error purging messages: {e}")
        await message.edit_text(f"`خطا در پاکسازی پیام‌ها: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 10. Edit: ویرایش پیام پاسخ داده شده
@user_bot.on_message(filters.me & filters.command("edit", prefixes=PREFIX))
async def edit_command(client: Client, message: Message):
    """`.edit <متن جدید>` / (پاسخ به پیام) `.edit <متن جدید>`: ویرایش پیام."""
    new_text = " ".join(message.command[1:])
    if not new_text:
        await message.edit_text("`لطفاً متنی برای ویرایش ارائه دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return

    if message.reply_to_message:
        try:
            await client.edit_message_text(
                chat_id=message.chat.id,
                message_id=message.reply_to_message.id,
                text=new_text
            )
            await message.delete()
            logger.info(f"Command '{PREFIX}edit' executed. Edited replied message to: '{new_text}'")
        except Exception as e:
            logger.error(f"Error editing replied message: {e}")
            await message.edit_text(f"`خطا در ویرایش پیام: {e}`")
            await asyncio.sleep(3)
            await message.delete()
    else:
        try:
            await message.edit_text(new_text)
            logger.info(f"Command '{PREFIX}edit' executed. Edited own message to: '{new_text}'")
        except Exception as e:
            logger.error(f"Error editing own message: {e}")
            await message.edit_text(f"`خطا در ویرایش پیام خود: {e}`")
            await asyncio.sleep(3)
            await message.delete()

# 11. Uptime: مدت زمان فعالیت ربات
@user_bot.on_message(filters.me & filters.command("uptime", prefixes=PREFIX))
async def uptime_command(client: Client, message: Message):
    """`.uptime`: نمایش مدت زمان فعال بودن ربات."""
    current_time = time.time()
    up_time = str(timedelta(seconds=int(current_time - START_TIME)))
    try:
        await message.edit_text(f"`ربات به مدت: {up_time} فعال است.`")
        logger.info(f"Command '{PREFIX}uptime' executed. Uptime: {up_time}")
    except Exception as e:
        logger.error(f"Error in uptime command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(5)
    await message.delete()

# 12. SysInfo: اطلاعات سیستم
@user_bot.on_message(filters.me & filters.command("sysinfo", prefixes=PREFIX))
async def sysinfo_command(client: Client, message: Message):
    """`.sysinfo`: نمایش اطلاعات سیستم عامل و سخت‌افزار سرور."""
    cpu_percent = psutil.cpu_percent(interval=1)
    ram_info = psutil.virtual_memory()
    disk_info = psutil.disk_usage('/')
    
    text = (
        f"**اطلاعات سیستم:**\n"
        f"**سیستم عامل:** `{platform.system()} {platform.release()}`\n"
        f"**معماری:** `{platform.machine()}`\n"
        f"**پایتون:** `{platform.python_version()}`\n"
        f"**CPU usage:** `{cpu_percent}%`\n"
        f"**RAM:** `{ram_info.percent}% ({ram_info.used / (1024**3):.2f}GB / {ram_info.total / (1024**3):.2f}GB)`\n"
        f"**Disk usage:** `{disk_info.percent}% ({disk_info.used / (1024**3):.2f}GB / {disk_info.total / (1024**3):.2f}GB)`"
    )
    try:
        await message.edit_text(text)
        logger.info(f"Command '{PREFIX}sysinfo' executed.")
    except Exception as e:
        logger.error(f"Error in sysinfo command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(5)
    await message.delete()

# 13. Pin / Unpin: پین/آنپین پیام
@user_bot.on_message(filters.me & filters.command(["pin", "unpin"], prefixes=PREFIX))
async def pin_unpin_command(client: Client, message: Message):
    """(پاسخ به پیام) `.pin` / `.unpin`: پین/آنپین پیام پاسخ داده شده."""
    if message.chat.type == enums.ChatType.PRIVATE:
        await message.edit_text("`این دستور فقط در گروه‌ها و کانال‌ها قابل استفاده است.`")
        await asyncio.sleep(2)
        await message.delete()
        return

    admin_rights = await get_admin_rights(client, message.chat.id)
    if not admin_rights or not admin_rights.can_pin_messages:
        await message.edit_text("`من (ربات) حقوق کافی برای پین/آنپین کردن پیام‌ها را ندارم.`")
        await asyncio.sleep(3)
        await message.delete()
        return
    
    cmd = message.command[0]
    if cmd == "pin":
        if not message.reply_to_message:
            await message.edit_text("`لطفاً برای پین کردن، به یک پیام پاسخ دهید.`")
            await asyncio.sleep(2)
            await message.delete()
            return
        try:
            await client.pin_chat_message(
                chat_id=message.chat.id,
                message_id=message.reply_to_message.id,
                disable_notification=True
            )
            await message.edit_text("`پیام با موفقیت پین شد.`")
            logger.info(f"Message {message.reply_to_message.id} pinned in chat {message.chat.id}.")
        except Exception as e:
            logger.error(f"Error pinning message: {e}")
            await message.edit_text(f"`خطا در پین کردن پیام: {e}`")
    elif cmd == "unpin":
        try:
            await client.unpin_chat_message(
                chat_id=message.chat.id,
                message_id=message.reply_to_message.id if message.reply_to_message else None
            )
            await message.edit_text("`پیام با موفقیت آنپین شد.`")
            logger.info(f"Message unpinned in chat {message.chat.id}.")
        except Exception as e:
            logger.error(f"Error unpinning message: {e}")
            await message.edit_text(f"`خطا در آنپین کردن پیام: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 14. Leave: ترک چت فعلی
@user_bot.on_message(filters.me & filters.command("leave", prefixes=PREFIX))
async def leave_command(client: Client, message: Message):
    """`.leave`: ترک چت فعلی."""
    if message.chat.type == enums.ChatType.PRIVATE:
        await message.edit_text("`نمی‌توان از چت خصوصی خارج شد.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    chat_title = message.chat.title or message.chat.id
    try:
        await message.edit_text(f"`در حال ترک چت` `{chat_title}` `...`")
        await client.leave_chat(message.chat.id)
        logger.info(f"Left chat {chat_title} ({message.chat.id}).")
    except Exception as e:
        logger.error(f"Error leaving chat {chat_title}: {e}")
        await message.edit_text(f"`خطا در ترک چت: {e}`")
        await asyncio.sleep(3)
        await message.delete()

# 15. Block / Unblock: بلاک/آنبلاک کاربر
@user_bot.on_message(filters.me & filters.command(["block", "unblock"], prefixes=PREFIX))
async def block_unblock_command(client: Client, message: Message):
    """`.block [یوزرنیم|ID]` / (پاسخ به پیام) `.block`: بلاک/آنبلاک کردن کاربر."""
    target_user = await get_target_user(client, message)
    if not target_user:
        return
    
    cmd = message.command[0]
    if cmd == "block":
        try:
            await client.block_user(target_user.id)
            await message.edit_text(f"`کاربر {target_user.first_name} ({target_user.id}) با موفقیت بلاک شد.`")
            logger.info(f"User {target_user.id} blocked by {PREFIX}block.")
        except UserIsBlocked:
            await message.edit_text(f"`کاربر {target_user.first_name} ({target_user.id}) از قبل بلاک شده بود.`")
        except Exception as e:
            logger.error(f"Error blocking user {target_user.id}: {e}")
            await message.edit_text(f"`خطا در بلاک کردن کاربر: {e}`")
    elif cmd == "unblock":
        try:
            await client.unblock_user(target_user.id)
            await message.edit_text(f"`کاربر {target_user.first_name} ({target_user.id}) با موفقیت آنبلاک شد.`")
            logger.info(f"User {target_user.id} unblocked by {PREFIX}unblock.")
        except UserNotBlocked:
            await message.edit_text(f"`کاربر {target_user.first_name} ({target_user.id}) از قبل بلاک نبود.`")
        except Exception as e:
            logger.error(f"Error unblocking user {target_user.id}: {e}")
            await message.edit_text(f"`خطا در آنبلاک کردن کاربر: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 16. Profile Set Pic: تغییر عکس پروفایل
@user_bot.on_message(filters.me & filters.command("profile", prefixes=PREFIX) & filters.text)
async def profile_command_text(client: Client, message: Message):
    """`.profile setpic [URL]` / (پاسخ به عکس) `.profile setpic`: تغییر عکس پروفایل."""
    cmd_parts = message.command
    if len(cmd_parts) > 1 and cmd_parts[1].lower() == "setpic":
        await message.edit_text("`در حال تغییر عکس پروفایل...`")
        try:
            if message.reply_to_message and message.reply_to_message.photo:
                photo_file_id = message.reply_to_message.photo.file_id
                downloaded_file = await client.download_media(photo_file_id, file_name="temp_profile_pic.jpg")
                await client.set_profile_photo(photo=downloaded_file)
                os.remove(downloaded_file)
                await message.edit_text("`عکس پروفایل با موفقیت تغییر یافت.`")
                logger.info("Profile picture updated from replied photo.")
            elif len(cmd_parts) > 2 and (cmd_parts[2].startswith("http") or cmd_parts[2].startswith("https")):
                photo_url = cmd_parts[2]
                async with aiohttp.ClientSession() as session:
                    async with session.get(photo_url) as resp:
                        if resp.status == 200:
                            with open("temp_profile_pic_url.jpg", "wb") as f:
                                f.write(await resp.read())
                            await client.set_profile_photo(photo="temp_profile_pic_url.jpg")
                            os.remove("temp_profile_pic_url.jpg")
                            await message.edit_text("`عکس پروفایل با موفقیت از URL تغییر یافت.`")
                            logger.info(f"Profile picture updated from URL: {photo_url}.")
                        else:
                            await message.edit_text(f"`خطا: قادر به دانلود عکس از URL نیستم (کد وضعیت: {resp.status}).`")
                            logger.error(f"Failed to download profile picture from URL: {photo_url}, Status: {resp.status}")
            else:
                await message.edit_text("`لطفاً یک عکس را ریپلای کنید یا لینک مستقیم عکس را ارائه دهید.`")
                await asyncio.sleep(3)
                await message.delete()
                return
        except Exception as e:
            logger.error(f"Error setting profile picture: {e}")
            await message.edit_text(f"`خطا در تغییر عکس پروفایل: {e}`")
        await asyncio.sleep(3)
        await message.delete()

# 17. Profile Set Name: تغییر نام پروفایل
@user_bot.on_message(filters.me & filters.command("profile", prefixes=PREFIX) & filters.text)
async def profile_setname_command(client: Client, message: Message):
    """`.profile setname <نام اول> [نام خانوادگی]`: تغییر نام پروفایل."""
    cmd_parts = message.command
    if len(cmd_parts) > 1 and cmd_parts[1].lower() == "setname":
        if len(cmd_parts) < 3:
            await message.edit_text("`لطفاً نام اول را ارائه دهید. استفاده: .profile setname <نام اول> [نام خانوادگی]`")
            await asyncio.sleep(2)
            await message.delete()
            return
        
        first_name = cmd_parts[2]
        last_name = " ".join(cmd_parts[3:]) if len(cmd_parts) > 3 else ""
        
        try:
            await client.update_profile(first_name=first_name, last_name=last_name)
            full_name = f"{first_name} {last_name}".strip()
            await message.edit_text(f"`نام شما به:` `{full_name}` `تغییر یافت.`")
            logger.info(f"Name updated to: '{full_name}'")
        except Exception as e:
            logger.error(f"Error updating name: {e}")
            await message.edit_text(f"`خطا در تغییر نام: {e}`")
        await asyncio.sleep(3)
        await message.delete()

# 18. Profile Set Bio: تغییر بایو پروفایل
@user_bot.on_message(filters.me & filters.command("profile", prefixes=PREFIX) & filters.text)
async def profile_setbio_command(client: Client, message: Message):
    """`.profile setbio <متن جدید بایو>`: تغییر بایو پروفایل."""
    cmd_parts = message.command
    if len(cmd_parts) > 1 and cmd_parts[1].lower() == "setbio":
        new_bio = " ".join(cmd_parts[2:])
        if not new_bio:
            await message.edit_text("`لطفاً متنی برای بایو ارائه دهید.`")
            await asyncio.sleep(2)
            await message.delete()
            return
        
        try:
            await client.update_profile(bio=new_bio)
            await message.edit_text(f"`بایو شما به:` `{new_bio}` `تغییر یافت.`")
            logger.info(f"Bio updated to: '{new_bio}'")
        except Exception as e:
            logger.error(f"Error updating bio: {e}")
            await message.edit_text(f"`خطا در تغییر بایو: {e}`")
        await asyncio.sleep(3)
        await message.delete()

# 19. Join: پیوستن به چت با لینک/یوزرنیم
@user_bot.on_message(filters.me & filters.command("join", prefixes=PREFIX))
async def join_chat_command(client: Client, message: Message):
    """`.join <لینک_دعوت|یوزرنیم>`: پیوستن به چت."""
    if len(message.command) < 2:
        await message.edit_text("`لطفاً لینک دعوت یا یوزرنیم چت را ارائه دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    chat_id_or_link = message.command[1]
    try:
        await client.join_chat(chat_id_or_link)
        await message.edit_text(f"`با موفقیت به چت` `{chat_id_or_link}` `پیوستید.`")
        logger.info(f"Joined chat: {chat_id_or_link}.")
    except Exception as e:
        logger.error(f"Error joining chat {chat_id_or_link}: {e}")
        await message.edit_text(f"`خطا در پیوستن به چت: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 20. LeaveAll: ترک تمامی چت‌ها
@user_bot.on_message(filters.me & filters.command("leaveall", prefixes=PREFIX))
async def leave_all_chats_command(client: Client, message: Message):
    """`.leaveall`: ترک تمامی گروه‌ها و کانال‌ها (بسیار خطرناک!)."""
    confirm_msg = await message.edit_text("`هشدار! این دستور شما را از تمامی گروه‌ها و کانال‌ها خارج می‌کند. برای تأیید، 'yes' را بنویسید.`")
    
    try:
        response_msg = await client.listen(message.chat.id, filters.me & filters.text, timeout=30)
        if response_msg and response_msg.text.lower() == "yes":
            await response_msg.delete()
            await confirm_msg.edit_text("`در حال ترک تمامی چت‌ها...`")
            count = 0
            async for dialog in client.get_dialogs():
                if dialog.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP, enums.ChatType.CHANNEL]:
                    try:
                        await client.leave_chat(dialog.chat.id)
                        logger.info(f"Left chat: {dialog.chat.title} ({dialog.chat.id}).")
                        count += 1
                        await asyncio.sleep(0.5) # برای جلوگیری از FloodWait
                    except FloodWait as e:
                        logger.warning(f"FloodWait while leaving chats: {e.value}s.")
                        await asyncio.sleep(e.value + 1)
                    except Exception as e:
                        logger.error(f"Error leaving chat {dialog.chat.title} ({dialog.chat.id}): {e}")
            await confirm_msg.edit_text(f"`با موفقیت از {count} چت خارج شدید.`")
            logger.info(f"Command '{PREFIX}leaveall' executed. Left {count} chats.")
        else:
            await confirm_msg.edit_text("`عملیات لغو شد.`")
            logger.info(f"Command '{PREFIX}leaveall' cancelled.")
    except asyncio.TimeoutError:
        await confirm_msg.edit_text("`زمان تأیید به پایان رسید. عملیات لغو شد.`")
        logger.info(f"Command '{PREFIX}leaveall' timed out and cancelled.")
    except Exception as e:
        logger.error(f"Error during leaveall confirmation: {e}")
        await confirm_msg.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 21. Check_bots: شمارش بات‌ها در گروه
@user_bot.on_message(filters.me & filters.command("check_bots", prefixes=PREFIX))
async def check_bots_command(client: Client, message: Message):
    """`.check_bots`: شمارش تعداد بات‌ها در گروه فعلی."""
    if message.chat.type not in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        await message.edit_text("`این دستور فقط در گروه‌ها قابل استفاده است.`")
        await asyncio.sleep(2)
        await message.delete()
        return

    await message.edit_text("`در حال شمارش بات‌ها...`")
    bot_count = 0
    non_bot_count = 0
    try:
        async for member in client.get_chat_members(message.chat.id):
            if member.user.is_bot:
                bot_count += 1
            else:
                non_bot_count += 1
        total_members = bot_count + non_bot_count
        await message.edit_text(f"`تعداد کل اعضا: {total_members}\nبات‌ها: {bot_count}\nکاربران: {non_bot_count}`")
        logger.info(f"Command '{PREFIX}check_bots' executed in chat {message.chat.id}. Bots: {bot_count}.")
    except Exception as e:
        logger.error(f"Error checking bots: {e}")
        await message.edit_text(f"`خطا در شمارش بات‌ها: {e}`")
    await asyncio.sleep(5)
    await message.delete()

# --- تبچی و AFK ---

# 22. Spam: ارسال متن به تعداد مشخص
@user_bot.on_message(filters.me & filters.command("spam", prefixes=PREFIX))
async def spam_command(client: Client, message: Message):
    """`.spam <تعداد> <متن>`: ارسال متن به تعداد مشخص (با احتیاط!)."""
    if len(message.command) < 3:
        await message.edit_text("`استفاده: .spam <تعداد> <متن>`")
        await asyncio.sleep(2)
        await message.delete()
        return

    try:
        count = int(message.command[1])
        if count > 50: # محدودیت برای جلوگیری از سوء استفاده شدید
            await message.edit_text("`حداکثر تعداد اسپم 50 است.`")
            await asyncio.sleep(2)
            await message.delete()
            return
        
        spam_text = " ".join(message.command[2:])
        await message.delete()
        
        for _ in range(count):
            try:
                await client.send_message(message.chat.id, spam_text)
                await asyncio.sleep(0.2) # تأخیر کوچک بین پیام ها
            except FloodWait as e:
                logger.warning(f"FloodWait during spam: {e.value}s.")
                await asyncio.sleep(e.value + 1)
            except Exception as e:
                logger.error(f"Error sending spam message: {e}")
                await client.send_message(message.chat.id, f"`خطا در ارسال اسپم: {e}`")
                break
        logger.warning(f"Command '{PREFIX}spam' executed {count} times with text: '{spam_text}' in chat {message.chat.id}")
    except ValueError:
        await message.edit_text("`تعداد باید یک عدد باشد.`")
        await asyncio.sleep(2)
        await message.delete()
    except Exception as e:
        await message.edit_text(f"`خطا در ارسال اسپم: {e}`")
        logger.error(f"Error in spam command: {e}")
        await asyncio.sleep(3)
        await message.delete()

# 23. Fspam: فوروارد پیام پاسخ داده شده به تعداد مشخص
@user_bot.on_message(filters.me & filters.command("fspam", prefixes=PREFIX))
async def fspam_command(client: Client, message: Message):
    """(پاسخ به پیام) `.fspam <تعداد>`: فوروارد پیام پاسخ داده شده به تعداد مشخص (با احتیاط!)."""
    if not message.reply_to_message or len(message.command) < 2:
        await message.edit_text("`لطفاً به یک پیام پاسخ دهید و تعداد فوروارد را وارد کنید. مثال: .fspam 5`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    try:
        count = int(message.command[1])
        if count > 30: # محدودیت برای جلوگیری از سوء استفاده شدید
            await message.edit_text("`حداکثر تعداد fspam 30 است.`")
            await asyncio.sleep(2)
            await message.delete()
            return
        
        await message.delete()
        
        for _ in range(count):
            try:
                await client.forward_messages(message.chat.id, message.chat.id, message.reply_to_message.id)
                await asyncio.sleep(0.3) # تأخیر کوچک
            except FloodWait as e:
                logger.warning(f"FloodWait during fspam: {e.value}s.")
                await asyncio.sleep(e.value + 1)
            except Exception as e:
                logger.error(f"Error forwarding spam message: {e}")
                await client.send_message(message.chat.id, f"`خطا در فوروارد اسپم: {e}`")
                break
        logger.warning(f"Command '{PREFIX}fspam' executed {count} times in chat {message.chat.id}.")
    except ValueError:
        await message.edit_text("`تعداد باید یک عدد باشد.`")
        await asyncio.sleep(2)
        await message.delete()
    except Exception as e:
        await message.edit_text(f"`خطا در fspam: {e}`")
        logger.error(f"Error in fspam command: {e}")
        await asyncio.sleep(3)
        await message.delete()

# 24. FastSpam: اسپم سریعتر (ممکن است زودتر به FloodWait بخورد)
@user_bot.on_message(filters.me & filters.command("fastspam", prefixes=PREFIX))
async def fastspam_command(client: Client, message: Message):
    """`.fastspam <تعداد> <متن>`: اسپم سریعتر (ریسک FloodWait بالاتر)."""
    if len(message.command) < 3:
        await message.edit_text("`استفاده: .fastspam <تعداد> <متن>`")
        await asyncio.sleep(2)
        await message.delete()
        return

    try:
        count = int(message.command[1])
        if count > 20: # محدودیت شدیدتر
            await message.edit_text("`حداکثر تعداد fastspam 20 است.`")
            await asyncio.sleep(2)
            await message.delete()
            return
        
        spam_text = " ".join(message.command[2:])
        await message.delete()
        
        for _ in range(count):
            try:
                await client.send_message(message.chat.id, spam_text)
                # بدون تأخیر یا تأخیر خیلی کم
            except FloodWait as e:
                logger.warning(f"Fastspam hit FloodWait: {e.value}s.")
                await client.send_message(message.chat.id, f"`FloodWait: {e.value} ثانیه صبر کنید.`")
                await asyncio.sleep(e.value + 1)
            except Exception as e:
                logger.error(f"Error sending fastspam message: {e}")
                await client.send_message(message.chat.id, f"`خطا در ارسال fastspam: {e}`")
                break
        logger.warning(f"Command '{PREFIX}fastspam' executed {count} times with text: '{spam_text}' in chat {message.chat.id}")
    except ValueError:
        await message.edit_text("`تعداد باید یک عدد باشد.`")
        await asyncio.sleep(2)
        await message.delete()
    except Exception as e:
        await message.edit_text(f"`خطا در fastspam: {e}`")
        logger.error(f"Error in fastspam command: {e}")
        await asyncio.sleep(3)
        await message.delete()

# 25. DelSpam: حذف N پیام آخر شما
@user_bot.on_message(filters.me & filters.command("delspam", prefixes=PREFIX))
async def delspam_command(client: Client, message: Message):
    """`.delspam <تعداد>`: حذف N پیام آخر شما در چت فعلی."""
    if len(message.command) < 2:
        await message.edit_text("`استفاده: .delspam <تعداد_پیام>`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    try:
        count_to_delete = int(message.command[1])
        if count_to_delete <= 0 or count_to_delete > 100:
            await message.edit_text("`تعداد پیام برای حذف باید بین 1 تا 100 باشد.`")
            await asyncio.sleep(2)
            await message.delete()
            return
        
        await message.delete() # حذف پیام دستور
        
        deleted_count = 0
        async for msg in client.get_chat_history(message.chat.id, limit=count_to_delete + 10): # چند پیام بیشتر برای اطمینان
            if msg.from_user and msg.from_user.id == client.me.id and msg.id != message.id:
                try:
                    await msg.delete()
                    deleted_count += 1
                    if deleted_count >= count_to_delete:
                        break
                    await asyncio.sleep(0.1) # تأخیر کوچک
                except Exception as e:
                    logger.error(f"Error deleting message {msg.id} in delspam: {e}")
            await asyncio.sleep(0.05) # تأخیر برای جلوگیری از FloodWait
        
        confirmation_msg = await client.send_message(message.chat.id, f"`{deleted_count} پیام آخر شما حذف شد.`")
        logger.info(f"Command '{PREFIX}delspam' executed. Deleted {deleted_count} messages in chat {message.chat.id}.")
        await asyncio.sleep(3)
        await confirmation_msg.delete()

    except ValueError:
        await message.edit_text("`تعداد باید یک عدد صحیح باشد.`")
        await asyncio.sleep(2)
        await message.delete()
    except Exception as e:
        logger.error(f"Error in delspam command: {e}")
        await message.edit_text(f"`خطا در حذف اسپم: {e}`")
        await asyncio.sleep(3)
        await message.delete()

# 26. AFK [پیام]: فعال کردن حالت AFK
@user_bot.on_message(filters.me & filters.command("afk", prefixes=PREFIX))
async def afk_on_command(client: Client, message: Message):
    """`.afk [پیام]`: فعال کردن حالت AFK با پیام اختیاری."""
    global AFK_MODE, AFK_MESSAGE, AFK_LAST_SEEN, AFK_COUNT
    AFK_MODE = True
    AFK_LAST_SEEN = time.time()
    AFK_COUNT = {} # ریست کردن شمارنده AFK
    
    new_afk_message = " ".join(message.command[1:])
    if new_afk_message:
        AFK_MESSAGE = new_afk_message
    else:
        AFK_MESSAGE = "من در حال حاضر نیستم. بعداً پاسخ خواهم داد." # پیام پیش‌فرض اگر خالی باشد

    try:
        await message.edit_text(f"`حالت AFK فعال شد: {AFK_MESSAGE}`")
        logger.info(f"Command '{PREFIX}afk' executed. AFK mode enabled with message: '{AFK_MESSAGE}'")
    except Exception as e:
        logger.error(f"Error in afk_on command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 27. UnAFK: غیرفعال کردن AFK
@user_bot.on_message(filters.me & filters.command("unafk", prefixes=PREFIX))
async def afk_off_command(client: Client, message: Message):
    """`.unafk`: غیرفعال کردن حالت AFK."""
    global AFK_MODE, AFK_LAST_SEEN, AFK_COUNT
    if AFK_MODE:
        AFK_MODE = False
        afk_duration = str(timedelta(seconds=int(time.time() - AFK_LAST_SEEN))) if AFK_LAST_SEEN else "نا معلوم"
        
        reply_counts = sum(sum(chat.values()) for chat in AFK_COUNT.values())
        
        try:
            await message.edit_text(f"`حالت AFK غیرفعال شد.\nمدت زمان AFK: {afk_duration}\nتعداد پاسخ‌ها: {reply_counts}`")
            logger.info(f"Command '{PREFIX}unafk' executed. AFK mode disabled. Replied {reply_counts} times.")
        except Exception as e:
            logger.error(f"Error in unafk command: {e}")
            await message.edit_text(f"`خطا: {e}`")
    else:
        try:
            await message.edit_text("`حالت AFK از قبل غیرفعال بود.`")
        except Exception as e:
            logger.error(f"Error in unafk command (already off): {e}")
    
    AFK_COUNT = {} # ریست کردن شمارنده AFK
    AFK_LAST_SEEN = None # ریست کردن زمان AFK
    await asyncio.sleep(5)
    await message.delete()

# 28. SetAFKMsg: تنظیم پیام AFK پیش‌فرض
@user_bot.on_message(filters.me & filters.command("setafkmsg", prefixes=PREFIX))
async def setafkmsg_command(client: Client, message: Message):
    """`.setafkmsg <پیام>`: تنظیم پیام AFK پیش‌فرض."""
    global AFK_MESSAGE
    new_msg = " ".join(message.command[1:])
    if not new_msg:
        await message.edit_text("`لطفاً متنی برای پیام AFK ارائه دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    AFK_MESSAGE = new_msg
    try:
        await message.edit_text(f"`پیام AFK پیش‌فرض به:` `{AFK_MESSAGE}` `تغییر یافت.`")
        logger.info(f"Command '{PREFIX}setafkmsg' executed. AFK message set to: '{AFK_MESSAGE}'")
    except Exception as e:
        logger.error(f"Error in setafkmsg command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# --- مدیریت لیست (دشمن، مشتی، عشق) ---

# توابع کمکی برای مدیریت لیست‌ها
async def manage_list(client: Client, message: Message, target_list: set, filename: str, list_name: str, action: str):
    target_user = await get_target_user(client, message)
    if not target_user:
        return

    user_id_str = str(target_user.id)
    user_name = html.escape(target_user.first_name)

    response_text = ""
    try:
        if action == "add":
            if user_id_str in target_list:
                response_text = f"`کاربر {user_name} از قبل در لیست {list_name} بود.`"
            else:
                target_list.add(user_id_str)
                await save_data(filename, list(target_list))
                response_text = f"`کاربر {user_name} به لیست {list_name} اضافه شد.`"
            logger.info(f"Added user {user_id_str} to {list_name}.")
        elif action == "remove":
            if user_id_str not in target_list:
                response_text = f"`کاربر {user_name} در لیست {list_name} یافت نشد.`"
            else:
                target_list.remove(user_id_str)
                await save_data(filename, list(target_list))
                response_text = f"`کاربر {user_name} از لیست {list_name} حذف شد.`"
            logger.info(f"Removed user {user_id_str} from {list_name}.")
        else: # list
            if not target_list:
                response_text = f"`لیست {list_name} خالی است.`"
            else:
                users_info = []
                for uid_str in list(target_list):
                    try:
                        user_obj = await client.get_users(int(uid_str))
                        users_info.append(f"- {html.escape(user_obj.first_name)} (`{uid_str}`) @{user_obj.username or 'ندارد'}")
                    except Exception:
                        users_info.append(f"- `Unknown User` (`{uid_str}`) - **حساب حذف شده؟**")
                response_text = f"**لیست {list_name}:**\n" + "\n".join(users_info)
            logger.info(f"Listed {list_name}.")

        await message.edit_text(response_text, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Error managing {list_name} list ({action}): {e}")
        await message.edit_text(f"`خطا در مدیریت لیست {list_name}: {e}`")
    await asyncio.sleep(5)
    await message.delete()

# 29-31. Enemy List
@user_bot.on_message(filters.me & filters.command(["addenemy", "rmenemy", "enemies"], prefixes=PREFIX))
async def enemy_list_command(client: Client, message: Message):
    cmd = message.command[0]
    action = "add" if cmd == "addenemy" else ("remove" if cmd == "rmenemy" else "list")
    await manage_list(client, message, ENEMIES, ENEMIES_FILE, "دشمنان", action)

# 32. Load Enemies from file
@user_bot.on_message(filters.me & filters.command("loadenemies", prefixes=PREFIX))
async def load_enemies_command(client: Client, message: Message):
    """`.loadenemies <مسیر_فایل>`: بارگذاری لیست دشمنان از یک فایل متنی."""
    if len(message.command) < 2:
        await message.edit_text("`لطفاً مسیر فایل را ارائه دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    file_path = message.command[1]
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            new_enemies = {line.strip() for line in f if line.strip()}
        global ENEMIES
        ENEMIES.update(new_enemies)
        await save_data(ENEMIES_FILE, list(ENEMIES))
        await message.edit_text(f"`با موفقیت {len(new_enemies)} دشمن جدید از فایل بارگذاری شد.`")
        logger.info(f"Loaded {len(new_enemies)} enemies from {file_path}.")
    except FileNotFoundError:
        await message.edit_text("`فایل یافت نشد.`")
    except Exception as e:
        await message.edit_text(f"`خطا در بارگذاری دشمنان: {e}`")
        logger.error(f"Error loading enemies from file: {e}")
    await asyncio.sleep(3)
    await message.delete()

# 33-35. Friend List
@user_bot.on_message(filters.me & filters.command(["addfriend", "rmfriend", "friends"], prefixes=PREFIX))
async def friend_list_command(client: Client, message: Message):
    cmd = message.command[0]
    action = "add" if cmd == "addfriend" else ("remove" if cmd == "rmfriend" else "list")
    await manage_list(client, message, FRIENDS, FRIENDS_FILE, "دوستان (مشتی‌ها)", action)

# 36. Load Friends from file
@user_bot.on_message(filters.me & filters.command("loadfriends", prefixes=PREFIX))
async def load_friends_command(client: Client, message: Message):
    """`.loadfriends <مسیر_فایل>`: بارگذاری لیست دوستان از یک فایل متنی."""
    if len(message.command) < 2:
        await message.edit_text("`لطفاً مسیر فایل را ارائه دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    file_path = message.command[1]
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            new_friends = {line.strip() for line in f if line.strip()}
        global FRIENDS
        FRIENDS.update(new_friends)
        await save_data(FRIENDS_FILE, list(FRIENDS))
        await message.edit_text(f"`با موفقیت {len(new_friends)} دوست جدید از فایل بارگذاری شد.`")
        logger.info(f"Loaded {len(new_friends)} friends from {file_path}.")
    except FileNotFoundError:
        await message.edit_text("`فایل یافت نشد.`")
    except Exception as e:
        await message.edit_text(f"`خطا در بارگذاری دوستان: {e}`")
        logger.error(f"Error loading friends from file: {e}")
    await asyncio.sleep(3)
    await message.delete()

# 37-39. Lover List
@user_bot.on_message(filters.me & filters.command(["addlove", "rmlove", "lovers"], prefixes=PREFIX))
async def lover_list_command(client: Client, message: Message):
    cmd = message.command[0]
    action = "add" if cmd == "addlove" else ("remove" if cmd == "rmlove" else "list")
    await manage_list(client, message, LOVERS, LOVERS_FILE, "عشاق", action)

# 40. Load Lovers from file
@user_bot.on_message(filters.me & filters.command("loadlovers", prefixes=PREFIX))
async def load_lovers_command(client: Client, message: Message):
    """`.loadlovers <مسیر_فایل>`: بارگذاری لیست عشاق از یک فایل متنی."""
    if len(message.command) < 2:
        await message.edit_text("`لطفاً مسیر فایل را ارائه دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    file_path = message.command[1]
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            new_lovers = {line.strip() for line in f if line.strip()}
        global LOVERS
        LOVERS.update(new_lovers)
        await save_data(LOVERS_FILE, list(LOVERS))
        await message.edit_text(f"`با موفقیت {len(new_lovers)} عاشق جدید از فایل بارگذاری شد.`")
        logger.info(f"Loaded {len(new_lovers)} lovers from {file_path}.")
    except FileNotFoundError:
        await message.edit_text("`فایل یافت نشد.`")
    except Exception as e:
        await message.edit_text(f"`خطا در بارگذاری عشاق: {e}`")
        logger.error(f"Error loading lovers from file: {e}")
    await asyncio.sleep(3)
    await message.delete()

# 41. Clear List
@user_bot.on_message(filters.me & filters.command("clearlist", prefixes=PREFIX))
async def clear_list_command(client: Client, message: Message):
    """`.clearlist <enemies|friends|lovers|bannedwords|notes>`: پاک کردن یک لیست خاص."""
    if len(message.command) < 2:
        await message.edit_text("`لطفاً نوع لیست را برای پاک کردن مشخص کنید (enemies, friends, lovers, bannedwords, notes).`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    list_type = message.command[1].lower()
    response_text = ""
    success = False
    
    if list_type == "enemies":
        global ENEMIES
        ENEMIES.clear()
        success = await save_data(ENEMIES_FILE, list(ENEMIES))
        response_text = "`لیست دشمنان پاک شد.`"
    elif list_type == "friends":
        global FRIENDS
        FRIENDS.clear()
        success = await save_data(FRIENDS_FILE, list(FRIENDS))
        response_text = "`لیست دوستان پاک شد.`"
    elif list_type == "lovers":
        global LOVERS
        LOVERS.clear()
        success = await save_data(LOVERS_FILE, list(LOVERS))
        response_text = "`لیست عشاق پاک شد.`"
    elif list_type == "bannedwords":
        global BANNED_WORDS
        BANNED_WORDS.clear()
        success = await save_data(BANNED_WORDS_FILE, list(BANNED_WORDS))
        response_text = "`لیست کلمات ممنوعه پاک شد.`"
    elif list_type == "notes":
        global NOTES
        NOTES.clear()
        success = await save_data(NOTES_FILE, NOTES)
        response_text = "`لیست یادداشت‌ها پاک شد.`"
    else:
        response_text = "`نوع لیست نامعتبر است.`"

    if success:
        await message.edit_text(response_text)
        logger.info(f"Command '{PREFIX}clearlist' executed. Cleared '{list_type}'.")
    else:
        await message.edit_text(f"`خطا در پاک کردن لیست {list_type}.`")
    await asyncio.sleep(3)
    await message.delete()

# 42. Is <کاربر>: بررسی وضعیت کاربر در لیست‌ها
@user_bot.on_message(filters.me & filters.command("is", prefixes=PREFIX))
async def is_command(client: Client, message: Message):
    """`.is [یوزرنیم|ID]` / (پاسخ به پیام) `.is`: بررسی وضعیت کاربر در لیست‌ها."""
    target_user = await get_target_user(client, message)
    if not target_user:
        return
    
    user_id_str = str(target_user.id)
    user_name = html.escape(target_user.first_name)
    
    status_text = f"**وضعیت {user_name} ({user_id_str}):**\n"
    if user_id_str in ENEMIES:
        status_text += "- در لیست دشمنان ✅\n"
    else:
        status_text += "- در لیست دشمنان ❌\n"
    if user_id_str in FRIENDS:
        status_text += "- در لیست دوستان ✅\n"
    else:
        status_text += "- در لیست دوستان ❌\n"
    if user_id_str in LOVERS:
        status_text += "- در لیست عشاق ✅\n"
    else:
        status_text += "- در لیست عشاق ❌\n"
    
    try:
        await message.edit_text(status_text)
        logger.info(f"Command '{PREFIX}is' executed for user {user_id_str}.")
    except Exception as e:
        logger.error(f"Error in is command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(5)
    await message.delete()

# --- سرگرمی ---

# 43. Roll: پرتاب تاس
@user_bot.on_message(filters.me & filters.command("roll", prefixes=PREFIX))
async def roll_command(client: Client, message: Message):
    """`.roll`: پرتاب یک تاس."""
    result = random.randint(1, 6)
    try:
        await message.edit_text(f"`🎲 تاس: {result}`")
        logger.info(f"Command '{PREFIX}roll' executed. Result: {result}")
    except Exception as e:
        logger.error(f"Error in roll command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 44. Coin: پرتاب سکه
@user_bot.on_message(filters.me & filters.command("coin", prefixes=PREFIX))
async def coin_command(client: Client, message: Message):
    """`.coin`: پرتاب سکه (شیر یا خط)."""
    result = random.choice(["شیر", "خط"])
    try:
        await message.edit_text(f"`🪙 سکه: {result}`")
        logger.info(f"Command '{PREFIX}coin' executed. Result: {result}")
    except Exception as e:
        logger.error(f"Error in coin command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 45. 8Ball: توپ جادویی ۸
@user_bot.on_message(filters.me & filters.command("8ball", prefixes=PREFIX))
async def eightball_command(client: Client, message: Message):
    """`.8ball <سوال>`: توپ جادویی ۸ به سوال شما پاسخ می‌دهد."""
    if len(message.command) < 2:
        await message.edit_text("`لطفاً سوال خود را بپرسید.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    answers = [
        "مطمئناً", "بله", "قطعاً بله", "بدون شک", "به نظر خوب می‌رسد",
        "احتمالاً", "شاید", "سعی کن دوباره", "بهتر است نگویم", "نمی‌توانم پیش‌بینی کنم",
        "روی آن حساب نکن", "جواب منفی است", "نه", "قطعاً نه", "پاسخ بسیار منفی است"
    ]
    question = " ".join(message.command[1:])
    answer = random.choice(answers)
    try:
        await message.edit_text(f"`سوال: {question}\n8Ball: {answer}`")
        logger.info(f"Command '{PREFIX}8ball' executed. Question: '{question}', Answer: '{answer}'")
    except Exception as e:
        logger.error(f"Error in 8ball command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(5)
    await message.delete()

# 46. React <اموجی>: ارسال ری‌اکشن
@user_bot.on_message(filters.me & filters.command("react", prefixes=PREFIX))
async def react_command(client: Client, message: Message):
    """(پاسخ به پیام) `.react <اموجی>`: ارسال ری‌اکشن به پیام."""
    if not message.reply_to_message or len(message.command) < 2:
        await message.edit_text("`لطفاً به پیامی پاسخ دهید و اموجی ری‌اکشن را ارائه دهید. مثال: .react 👍`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    emoji = message.command[1]
    try:
        await client.send_reaction(
            chat_id=message.chat.id,
            message_id=message.reply_to_message.id,
            emoji=emoji,
            is_big=False
        )
        await message.delete()
        logger.info(f"Command '{PREFIX}react' executed. Reacted with '{emoji}' to message {message.reply_to_message.id}.")
    except Exception as e:
        logger.error(f"Error reacting to message: {e}")
        await message.edit_text(f"`خطا در ارسال ری‌اکشن: {e}`")
        await asyncio.sleep(3)
        await message.delete()

# 47. Mock <متن>: متن تقلیدی
@user_bot.on_message(filters.me & filters.command("mock", prefixes=PREFIX))
async def mock_command(client: Client, message: Message):
    """`.mock <متن>` / (پاسخ به پیام) `.mock`: متن را به صورت تقلیدی ارسال می‌کند."""
    text_to_mock = ""
    if message.reply_to_message:
        text_to_mock = message.reply_to_message.text or message.reply_to_message.caption or ""
    
    if len(message.command) > 1:
        text_to_mock = " ".join(message.command[1:])
    
    if not text_to_mock:
        await message.edit_text("`لطفاً متنی برای تقلید ارائه دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    mocked_text = "".join([c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(text_to_mock)])
    try:
        await message.edit_text(mocked_text)
        logger.info(f"Command '{PREFIX}mock' executed with text: '{mocked_text}'")
    except Exception as e:
        logger.error(f"Error in mock command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 48. Reverse <متن>: برعکس کردن متن
@user_bot.on_message(filters.me & filters.command("reverse", prefixes=PREFIX))
async def reverse_command(client: Client, message: Message):
    """`.reverse <متن>` / (پاسخ به پیام) `.reverse`: متن را برعکس می‌کند."""
    text_to_reverse = ""
    if message.reply_to_message:
        text_to_reverse = message.reply_to_message.text or message.reply_to_message.caption or ""
    
    if len(message.command) > 1:
        text_to_reverse = " ".join(message.command[1:])
    
    if text_to_reverse:
        try:
            await message.edit_text(text_to_reverse[::-1])
            logger.info(f"Command '{PREFIX}reverse' executed with text: '{text_to_reverse}'")
        except Exception as e:
            logger.error(f"Error in reverse command: {e}")
            await message.edit_text(f"`خطا: {e}`")
    else:
        await message.edit_text("`متنی برای معکوس کردن یافت نشد.`")
    await asyncio.sleep(3)
    await message.delete()

# 49. Shrug: ارسال ¯\_(ツ)_/¯
@user_bot.on_message(filters.me & filters.command("shrug", prefixes=PREFIX))
async def shrug_command(client: Client, message: Message):
    """`.shrug`: ارسال ¯\_(ツ)_/¯."""
    try:
        await message.edit_text("¯\_(ツ)_/¯")
        logger.info(f"Command '{PREFIX}shrug' executed.")
    except Exception as e:
        logger.error(f"Error in shrug command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 50. Lenny: ارسال ( ͡° ͜ʖ ͡°)
@user_bot.on_message(filters.me & filters.command("lenny", prefixes=PREFIX))
async def lenny_command(client: Client, message: Message):
    """`.lenny`: ارسال ( ͡° ͜ʖ ͡°)."""
    try:
        await message.edit_text("( ͡° ͜ʖ ͡°)")
        logger.info(f"Command '{PREFIX}lenny' executed.")
    except Exception as e:
        logger.error(f"Error in lenny command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 51. Quote: نقل قول تصادفی (به جای API خارجی، از لیست داخلی استفاده می کنیم)
@user_bot.on_message(filters.me & filters.command("quote", prefixes=PREFIX))
async def quote_command(client: Client, message: Message):
    """`.quote`: نمایش یک نقل قول تصادفی."""
    quotes = [
        "تنها راه انجام کارهای بزرگ، دوست داشتن کاری است که می‌کنید. - استیو جابز",
        "زندگی ۱۰ درصد آن چیزی است که برای شما اتفاق می‌افتد و ۹۰ درصد آنکه چگونه به آن واکنش نشان می‌دهید. - چارلز آر. سوئیندال",
        "باور کنید که می‌توانید و در نیمه راه قرار دارید. - تئودور روزولت",
        "آینده متعلق به کسانی است که زیبایی رویاهای خود را باور دارند. - الینور روزولت",
        "تنها محدودیت ما در زندگی، دیدگاه‌های ماست. - ناپلئون هیل",
        "موفقیت به معنای پایان نیست، شکست کشنده نیست: این شجاعت ادامه دادن است که اهمیت دارد. - وینستون چرچیل"
    ]
    random_quote = random.choice(quotes)
    try:
        await message.edit_text(f"`\"`{random_quote}`\"`")
        logger.info(f"Command '{PREFIX}quote' executed.")
    except Exception as e:
        logger.error(f"Error in quote command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(7)
    await message.delete()

# 52. Choose <گزینه1> <گزینه2> ...: انتخاب تصادفی
@user_bot.on_message(filters.me & filters.command("choose", prefixes=PREFIX))
async def choose_command(client: Client, message: Message):
    """`.choose <گزینه1> <گزینه2> ...`: از بین گزینه‌ها به صورت تصادفی انتخاب می‌کند."""
    options = message.command[1:]
    if not options:
        await message.edit_text("`لطفاً گزینه‌هایی برای انتخاب ارائه دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    choice = random.choice(options)
    try:
        await message.edit_text(f"`من انتخاب کردم: {choice}`")
        logger.info(f"Command '{PREFIX}choose' executed. Options: {options}, Choice: {choice}")
    except Exception as e:
        logger.error(f"Error in choose command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 53. Slap <کاربر>: سیلی زدن مجازی
@user_bot.on_message(filters.me & filters.command("slap", prefixes=PREFIX))
async def slap_command(client: Client, message: Message):
    """`.slap [یوزرنیم|ID]` / (پاسخ به پیام) `.slap`: سیلی زدن مجازی به کاربر."""
    target_user = await get_target_user(client, message)
    if not target_user:
        return
    
    slap_actions = [
        "یک ماهی بزرگ به صورت", "یک کفش خیس به", "یک سیلی محکم به",
        "یک دمپایی به کله", "یک دستکش بوکس به صورت"
    ]
    action = random.choice(slap_actions)
    
    try:
        await message.edit_text(f"`{user_bot.me.first_name} {action} {html.escape(target_user.first_name)} زد!`")
        logger.info(f"Command '{PREFIX}slap' executed on user {target_user.id}.")
    except Exception as e:
        logger.error(f"Error in slap command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(5)
    await message.delete()

# 54-58. Dice / Dart / Football / Basketball / Slot: ارسال اموجی‌های بازی تلگرام
@user_bot.on_message(filters.me & filters.command(["dice", "dart", "football", "basketball", "slot"], prefixes=PREFIX))
async def game_emoji_command(client: Client, message: Message):
    """`.dice` / `.dart` / `.football` / `.basketball` / `.slot`: ارسال اموجی‌های بازی تلگرام."""
    emoji_map = {
        "dice": "🎲",
        "dart": "🎯",
        "football": "⚽",
        "basketball": "🏀",
        "slot": "🎰"
    }
    cmd = message.command[0]
    emoji = emoji_map.get(cmd)
    if not emoji:
        await message.edit_text("`دستور بازی نامعتبر.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    try:
        await message.delete() # حذف دستور اصلی
        await client.send_dice(message.chat.id, emoji=emoji)
        logger.info(f"Command '{PREFIX}{cmd}' executed. Sent '{emoji}'.")
    except Exception as e:
        logger.error(f"Error sending game emoji ({cmd}): {e}")
        await client.send_message(message.chat.id, f"`خطا در ارسال بازی: {e}`")
        await asyncio.sleep(3)

# --- ابزارها ---

# 59. Markdown <متن>: پیش‌نمایش Markdown
@user_bot.on_message(filters.me & filters.command("markdown", prefixes=PREFIX))
async def markdown_preview_command(client: Client, message: Message):
    """`.markdown <متن>`: پیش‌نمایش متن Markdown."""
    text_to_preview = " ".join(message.command[1:])
    if not text_to_preview:
        await message.edit_text("`لطفاً متنی با فرمت Markdown ارائه دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    try:
        await message.edit_text(text_to_preview, parse_mode=enums.ParseMode.MARKDOWN)
        logger.info(f"Command '{PREFIX}markdown' executed.")
    except Exception as e:
        logger.error(f"Error in markdown command: {e}")
        await message.edit_text(f"`خطا در پیش‌نمایش Markdown: {e}`")
    await asyncio.sleep(5)
    await message.delete()

# 60. HTML <متن>: پیش‌نمایش HTML
@user_bot.on_message(filters.me & filters.command("html", prefixes=PREFIX))
async def html_preview_command(client: Client, message: Message):
    """`.html <متن>`: پیش‌نمایش متن HTML."""
    text_to_preview = " ".join(message.command[1:])
    if not text_to_preview:
        await message.edit_text("`لطفاً متنی با فرمت HTML ارائه دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    try:
        await message.edit_text(text_to_preview, parse_mode=enums.ParseMode.HTML)
        logger.info(f"Command '{PREFIX}html' executed.")
    except Exception as e:
        logger.error(f"Error in html command: {e}")
        await message.edit_text(f"`خطا در پیش‌نمایش HTML: {e}`")
    await asyncio.sleep(5)
    await message.delete()

# 61. Calc <عبارت_ریاضی>: ماشین حساب
@user_bot.on_message(filters.me & filters.command("calc", prefixes=PREFIX))
async def calc_command(client: Client, message: Message):
    """`.calc <عبارت_ریاضی>`: یک عبارت ریاضی را محاسبه می‌کند."""
    expression = " ".join(message.command[1:])
    if not expression:
        await message.edit_text("`لطفاً یک عبارت ریاضی ارائه دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return

    # محیط ایمن برای eval
    safe_dict = {
        '__builtins__': None, # Disable built-in functions
        'abs': abs, 'all': all, 'any': any, 'bool': bool, 'complex': complex,
        'dict': dict, 'divmod': divmod, 'float': float, 'int': int, 'len': len,
        'list': list, 'max': max, 'min': min, 'pow': pow, 'range': range,
        'round': round, 'set': set, 'slice': slice, 'str': str, 'sum': sum,
        'tuple': tuple, 'type': type, 'zip': zip
    }
    
    try:
        result = str(eval(expression, {"__builtins__": None}, safe_dict))
        await message.edit_text(f"`نتیجه: {expression} = {result}`")
        logger.info(f"Command '{PREFIX}calc' executed. Expression: '{expression}', Result: '{result}'")
    except SyntaxError:
        await message.edit_text("`خطای نوشتاری در عبارت ریاضی.`")
    except TypeError:
        await message.edit_text("`عملگر/نوع نامعتبر در عبارت ریاضی.`")
    except ZeroDivisionError:
        await message.edit_text("`تقسیم بر صفر! `")
    except Exception as e:
        await message.edit_text(f"`خطا در محاسبه: {e}`")
        logger.error(f"Error in calc command for '{expression}': {e}")
    await asyncio.sleep(5)
    await message.delete()

# 62. Shorten <URL>: کوتاه کننده URL (Placeholder)
@user_bot.on_message(filters.me & filters.command("shorten", prefixes=PREFIX))
async def shorten_url_command(client: Client, message: Message):
    """`.shorten <URL>`: کوتاه کننده URL (در حال حاضر یک Placeholder است)."""
    if len(message.command) < 2:
        await message.edit_text("`لطفاً یک URL برای کوتاه کردن ارائه دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    original_url = message.command[1]
    # این بخش نیاز به یک API واقعی برای کوتاه کردن URL دارد.
    # برای مثال، می توانید از bit.ly API یا tinyurl.com API استفاده کنید.
    # به دلیل محدودیت API Key و سرویس‌های رایگان، فعلاً یک Placeholder است.
    
    try:
        await message.edit_text(f"`قابلیت کوتاه کننده URL در حال حاضر فعال نیست یا نیاز به پیکربندی API دارد.\nURL اصلی: {original_url}`")
        logger.info(f"Command '{PREFIX}shorten' executed. Placeholder for URL: {original_url}")
    except Exception as e:
        logger.error(f"Error in shorten command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(5)
    await message.delete()

# 63-66. سیستم نوت‌برداری ساده (addnote, getnote, delnote, notes)
@user_bot.on_message(filters.me & filters.command(["addnote", "getnote", "delnote", "notes"], prefixes=PREFIX))
async def notes_command(client: Client, message: Message):
    """`.addnote <نام_نوت> <متن>` / `.getnote <نام_نوت>` / `.delnote <نام_نوت>` / `.notes`: مدیریت یادداشت‌ها."""
    cmd = message.command[0]
    
    if cmd == "addnote":
        if len(message.command) < 3:
            await message.edit_text("`استفاده: .addnote <نام_نوت> <متن_نوت>`")
            await asyncio.sleep(2)
            await message.delete()
            return
        note_name = message.command[1].lower()
        note_content = " ".join(message.command[2:])
        NOTES[note_name] = note_content
        await save_data(NOTES_FILE, NOTES)
        await message.edit_text(f"`یادداشت '{note_name}' با موفقیت ذخیره شد.`")
        logger.info(f"Note '{note_name}' added/updated.")
    elif cmd == "getnote":
        if len(message.command) < 2:
            await message.edit_text("`استفاده: .getnote <نام_نوت>`")
            await asyncio.sleep(2)
            await message.delete()
            return
        note_name = message.command[1].lower()
        if note_name in NOTES:
            await message.edit_text(f"**{note_name}:**\n`{NOTES[note_name]}`")
            logger.info(f"Note '{note_name}' retrieved.")
        else:
            await message.edit_text(f"`یادداشت '{note_name}' یافت نشد.`")
    elif cmd == "delnote":
        if len(message.command) < 2:
            await message.edit_text("`استفاده: .delnote <نام_نوت>`")
            await asyncio.sleep(2)
            await message.delete()
            return
        note_name = message.command[1].lower()
        if note_name in NOTES:
            del NOTES[note_name]
            await save_data(NOTES_FILE, NOTES)
            await message.edit_text(f"`یادداشت '{note_name}' حذف شد.`")
            logger.info(f"Note '{note_name}' deleted.")
        else:
            await message.edit_text(f"`یادداشت '{note_name}' یافت نشد.`")
    elif cmd == "notes":
        if NOTES:
            notes_list = "\n".join([f"- `{name}`" for name in NOTES.keys()])
            await message.edit_text(f"**یادداشت‌های شما:**\n{notes_list}")
            logger.info("Notes listed.")
        else:
            await message.edit_text("`هیچ یادداشتی ذخیره نشده است.`")
    
    await asyncio.sleep(5)
    await message.delete()

# 67-69. مدیریت کلمات ممنوعه (addbannedword, rmbannedword, bannedwords)
@user_bot.on_message(filters.me & filters.command(["addbannedword", "rmbannedword", "bannedwords"], prefixes=PREFIX))
async def banned_words_command(client: Client, message: Message):
    """`.addbannedword <کلمه>` / `.rmbannedword <کلمه>` / `.bannedwords`: مدیریت کلمات ممنوعه برای حذف خودکار."""
    cmd = message.command[0]
    
    if cmd == "addbannedword":
        if len(message.command) < 2:
            await message.edit_text("`لطفاً کلمه‌ای برای ممنوع کردن ارائه دهید.`")
            await asyncio.sleep(2)
            await message.delete()
            return
        word = message.command[1].lower()
        if word in BANNED_WORDS:
            await message.edit_text(f"`کلمه '{word}' از قبل در لیست کلمات ممنوعه بود.`")
        else:
            BANNED_WORDS.add(word)
            await save_data(BANNED_WORDS_FILE, list(BANNED_WORDS))
            await message.edit_text(f"`کلمه '{word}' به لیست کلمات ممنوعه اضافه شد.`")
            logger.info(f"Banned word '{word}' added.")
    elif cmd == "rmbannedword":
        if len(message.command) < 2:
            await message.edit_text("`لطفاً کلمه‌ای برای حذف از لیست ممنوعه ارائه دهید.`")
            await asyncio.sleep(2)
            await message.delete()
            return
        word = message.command[1].lower()
        if word in BANNED_WORDS:
            BANNED_WORDS.remove(word)
            await save_data(BANNED_WORDS_FILE, list(BANNED_WORDS))
            await message.edit_text(f"`کلمه '{word}' از لیست کلمات ممنوعه حذف شد.`")
            logger.info(f"Banned word '{word}' removed.")
        else:
            await message.edit_text(f"`کلمه '{word}' در لیست کلمات ممنوعه یافت نشد.`")
    elif cmd == "bannedwords":
        if BANNED_WORDS:
            words_list = "\n".join([f"- `{w}`" for w in BANNED_WORDS])
            await message.edit_text(f"**کلمات ممنوعه:**\n{words_list}")
            logger.info("Banned words listed.")
        else:
            await message.edit_text("`هیچ کلمه ممنوعه‌ای تنظیم نشده است.`")
    
    await asyncio.sleep(5)
    await message.delete()

# --- مدیریت گروه (نیاز به دسترسی ادمین) ---

# 70. Kick <کاربر>: کیک کردن کاربر
@user_bot.on_message(filters.me & filters.command("kick", prefixes=PREFIX))
async def kick_command(client: Client, message: Message):
    """`.kick [یوزرنیم|ID]` / (پاسخ به پیام) `.kick`: کیک کردن کاربر از گروه."""
    if message.chat.type == enums.ChatType.PRIVATE:
        await message.edit_text("`این دستور فقط در گروه‌ها قابل استفاده است.`")
        await asyncio.sleep(2)
        await message.delete()
        return

    target_user = await get_target_user(client, message)
    if not target_user:
        return
    
    admin_rights = await get_admin_rights(client, message.chat.id)
    if not admin_rights or not admin_rights.can_restrict_members:
        await message.edit_text("`من (ربات) حقوق کافی برای اخراج اعضا را ندارم.`")
        await asyncio.sleep(3)
        await message.delete()
        return

    try:
        await client.kick_chat_member(message.chat.id, target_user.id)
        await message.edit_text(f"`کاربر {target_user.first_name} با موفقیت اخراج شد.`")
        logger.info(f"User {target_user.id} kicked from chat {message.chat.id}.")
    except Exception as e:
        logger.error(f"Error kicking user {target_user.id}: {e}")
        await message.edit_text(f"`خطا در اخراج کاربر: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 71. Ban <کاربر>: بن کردن کاربر
@user_bot.on_message(filters.me & filters.command("ban", prefixes=PREFIX))
async def ban_command(client: Client, message: Message):
    """`.ban [یوزرنیم|ID]` / (پاسخ به پیام) `.ban`: بن کردن کاربر از گروه."""
    if message.chat.type == enums.ChatType.PRIVATE:
        await message.edit_text("`این دستور فقط در گروه‌ها قابل استفاده است.`")
        await asyncio.sleep(2)
        await message.delete()
        return

    target_user = await get_target_user(client, message)
    if not target_user:
        return
    
    admin_rights = await get_admin_rights(client, message.chat.id)
    if not admin_rights or not admin_rights.can_restrict_members:
        await message.edit_text("`من (ربات) حقوق کافی برای بن کردن اعضا را ندارم.`")
        await asyncio.sleep(3)
        await message.delete()
        return

    try:
        await client.ban_chat_member(message.chat.id, target_user.id)
        await message.edit_text(f"`کاربر {target_user.first_name} با موفقیت بن شد.`")
        logger.info(f"User {target_user.id} banned from chat {message.chat.id}.")
    except Exception as e:
        logger.error(f"Error banning user {target_user.id}: {e}")
        await message.edit_text(f"`خطا در بن کردن کاربر: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 72. Unban <کاربر>: آنبن کردن کاربر
@user_bot.on_message(filters.me & filters.command("unban", prefixes=PREFIX))
async def unban_command(client: Client, message: Message):
    """`.unban [یوزرنیم|ID]` / (پاسخ به پیام) `.unban`: آنبن کردن کاربر از گروه."""
    if message.chat.type == enums.ChatType.PRIVATE:
        await message.edit_text("`این دستور فقط در گروه‌ها قابل استفاده است.`")
        await asyncio.sleep(2)
        await message.delete()
        return

    target_user = await get_target_user(client, message)
    if not target_user:
        return
    
    admin_rights = await get_admin_rights(client, message.chat.id)
    if not admin_rights or not admin_rights.can_restrict_members:
        await message.edit_text("`من (ربات) حقوق کافی برای آنبن کردن اعضا را ندارم.`")
        await asyncio.sleep(3)
        await message.delete()
        return

    try:
        await client.unban_chat_member(message.chat.id, target_user.id)
        await message.edit_text(f"`کاربر {target_user.first_name} با موفقیت آنبن شد.`")
        logger.info(f"User {target_user.id} unbanned from chat {message.chat.id}.")
    except Exception as e:
        logger.error(f"Error unbanning user {target_user.id}: {e}")
        await message.edit_text(f"`خطا در آنبن کردن کاربر: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 73. Mute <کاربر>: میوت کردن کاربر
@user_bot.on_message(filters.me & filters.command("mute", prefixes=PREFIX))
async def mute_command(client: Client, message: Message):
    """`.mute [یوزرنیم|ID] [زمان] [دلیل]`: میوت کردن کاربر. زمان: 1m, 1h, 1d, 1w."""
    if message.chat.type == enums.ChatType.PRIVATE:
        await message.edit_text("`این دستور فقط در گروه‌ها قابل استفاده است.`")
        await asyncio.sleep(2)
        await message.delete()
        return

    target_user = await get_target_user(client, message)
    if not target_user:
        return
    
    admin_rights = await get_admin_rights(client, message.chat.id)
    if not admin_rights or not admin_rights.can_restrict_members:
        await message.edit_text("`من (ربات) حقوق کافی برای میوت کردن اعضا را ندارم.`")
        await asyncio.sleep(3)
        await message.delete()
        return

    until_date = 0
    reason = None
    if len(message.command) > 1:
        time_str = message.command[1]
        if time_str.endswith('m'): until_date = time.time() + int(time_str[:-1]) * 60
        elif time_str.endswith('h'): until_date = time.time() + int(time_str[:-1]) * 3600
        elif time_str.endswith('d'): until_date = time.time() + int(time_str[:-1]) * 86400
        elif time_str.endswith('w'): until_date = time.time() + int(time_str[:-1]) * 604800
        else:
            try: until_date = time.time() + int(time_str) * 60 # فرض بر دقیقه
            except ValueError: reason = " ".join(message.command[1:]) # اگر عدد نبود، دلیل است
        
        if not reason and len(message.command) > 2:
            reason = " ".join(message.command[2:])
        elif not reason and not until_date and len(message.command) > 1:
            reason = " ".join(message.command[1:])

    try:
        await client.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target_user.id,
            permissions=ChatPermissions(), # بدون هیچ پرمیشنی (سکوت کامل)
            until_date=int(until_date) # 0 به معنای نامحدود است
        )
        mute_duration = ""
        if until_date:
            duration_td = timedelta(seconds=int(until_date - time.time()))
            mute_duration = f" برای {duration_td}"
        status_msg = f"`کاربر {target_user.first_name} با موفقیت ساکت شد{mute_duration}.`"
        if reason: status_msg += f"\n`دلیل: {reason}`"
        await message.edit_text(status_msg)
        logger.info(f"User {target_user.id} muted in chat {message.chat.id}. Duration: {mute_duration}.")
    except Exception as e:
        logger.error(f"Error muting user {target_user.id}: {e}")
        await message.edit_text(f"`خطا در میوت کردن کاربر: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 74. Unmute <کاربر>: آنمیوت کردن کاربر
@user_bot.on_message(filters.me & filters.command("unmute", prefixes=PREFIX))
async def unmute_command(client: Client, message: Message):
    """`.unmute [یوزرنیم|ID]` / (پاسخ به پیام) `.unmute`: آنمیوت کردن کاربر از گروه."""
    if message.chat.type == enums.ChatType.PRIVATE:
        await message.edit_text("`این دستور فقط در گروه‌ها قابل استفاده است.`")
        await asyncio.sleep(2)
        await message.delete()
        return

    target_user = await get_target_user(client, message)
    if not target_user:
        return
    
    admin_rights = await get_admin_rights(client, message.chat.id)
    if not admin_rights or not admin_rights.can_restrict_members:
        await message.edit_text("`من (ربات) حقوق کافی برای آنمیوت کردن اعضا را ندارم.`")
        await asyncio.sleep(3)
        await message.delete()
        return

    try:
        default_permissions = ChatPermissions(
            can_send_messages=True, can_send_media_messages=True, can_send_stickers=True,
            can_send_animations=True, can_send_games=True, can_use_inline_bots=True,
            can_add_web_page_previews=True, can_send_polls=True, can_change_info=False,
            can_invite_users=True, can_pin_messages=False, can_manage_topics=False
        )
        await client.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target_user.id,
            permissions=default_permissions,
            until_date=0
        )
        await message.edit_text(f"`کاربر {target_user.first_name} با موفقیت از حالت سکوت خارج شد.`")
        logger.info(f"User {target_user.id} unmuted in chat {message.chat.id}.")
    except Exception as e:
        logger.error(f"Error unmuting user {target_user.id}: {e}")
        await message.edit_text(f"`خطا در آنمیوت کردن کاربر: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 75. Set Title: تغییر عنوان چت
@user_bot.on_message(filters.me & filters.command("settitle", prefixes=PREFIX))
async def set_chat_title_command(client: Client, message: Message):
    """`.settitle <عنوان_جدید>`: تغییر عنوان چت فعلی."""
    if message.chat.type == enums.ChatType.PRIVATE:
        await message.edit_text("`این دستور فقط در گروه‌ها و کانال‌ها قابل استفاده است.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    new_title = " ".join(message.command[1:])
    if not new_title:
        await message.edit_text("`لطفاً عنوان جدید را ارائه دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    admin_rights = await get_admin_rights(client, message.chat.id)
    if not admin_rights or not admin_rights.can_change_info:
        await message.edit_text("`من (ربات) حقوق کافی برای تغییر عنوان چت را ندارم.`")
        await asyncio.sleep(3)
        await message.delete()
        return

    try:
        await client.set_chat_title(message.chat.id, new_title)
        await message.edit_text(f"`عنوان چت به:` `{new_title}` `تغییر یافت.`")
        logger.info(f"Chat {message.chat.id} title set to: '{new_title}'.")
    except Exception as e:
        logger.error(f"Error setting chat title: {e}")
        await message.edit_text(f"`خطا در تغییر عنوان چت: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 76. Set Description: تغییر توضیحات چت
@user_bot.on_message(filters.me & filters.command("setdesc", prefixes=PREFIX))
async def set_chat_description_command(client: Client, message: Message):
    """`.setdesc <توضیحات_جدید>`: تغییر توضیحات چت فعلی."""
    if message.chat.type == enums.ChatType.PRIVATE:
        await message.edit_text("`این دستور فقط در گروه‌ها و کانال‌ها قابل استفاده است.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    new_description = " ".join(message.command[1:])
    
    admin_rights = await get_admin_rights(client, message.chat.id)
    if not admin_rights or not admin_rights.can_change_info:
        await message.edit_text("`من (ربات) حقوق کافی برای تغییر توضیحات چت را ندارم.`")
        await asyncio.sleep(3)
        await message.delete()
        return

    try:
        await client.set_chat_description(message.chat.id, new_description)
        await message.edit_text(f"`توضیحات چت به:` `{new_description[:100]}...` `تغییر یافت.`")
        logger.info(f"Chat {message.chat.id} description set to: '{new_description}'.")
    except Exception as e:
        logger.error(f"Error setting chat description: {e}")
        await message.edit_text(f"`خطا در تغییر توضیحات چت: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 77. Exec: اجرای کد پایتون (فقط برای صاحب ربات)
@user_bot.on_message(filters.user(OWNER_IDS) & filters.command("exec", prefixes=PREFIX))
async def exec_command(client: Client, message: Message):
    """`.exec <code>`: اجرای کد پایتون روی سرور (فقط برای OWNER_ID)."""
    code = " ".join(message.command[1:])
    if not code:
        await message.edit_text("`لطفاً کدی برای اجرا ارائه دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return

    reply_to_id = message.id
    if message.reply_to_message:
        reply_to_id = message.reply_to_message.id

    try:
        local_vars = {
            "client": client,
            "message": message,
            "os": os, "sys": sys, "asyncio": asyncio,
            "__builtins__": {}, # محدود کردن دسترسی
            "eval": None, "input": None, "open": None,
        }
        
        old_stdout = sys.stdout
        redirected_output = StringIO()
        sys.stdout = redirected_output
        
        exec_code = f"async def __ex_code():\n{textwrap.indent(code, '    ')}"
        exec(exec_code, globals(), local_vars)
        result = await local_vars["__ex_code"]()

        output = redirected_output.getvalue()
        sys.stdout = old_stdout

        if result:
            output = f"{output}\n`{result}`"

        if not output:
            output = "`کدی اجرا نشد یا خروجی نداشت.`"
        elif len(output) > 4096:
            with StringIO(output) as file_buffer:
                await client.send_document(
                    chat_id=message.chat.id,
                    document=file_buffer.read().encode("utf-8"),
                    file_name="exec_output.txt",
                    caption="`خروجی exec بسیار طولانی بود و به صورت فایل ارسال شد.`",
                    reply_to_message_id=reply_to_id
                )
            await message.delete()
            return
        
        await message.edit_text(f"`خروجی exec:`\n`{output}`", reply_to_message_id=reply_to_id)
        logger.warning(f"Exec command executed by OWNER_ID in chat {message.chat.id}")
    except Exception as e:
        output_error = f"`خطا در اجرای کد:`\n`{e}`"
        await message.edit_text(output_error, reply_to_message_id=reply_to_id)
        logger.error(f"Error in exec command: {e}", exc_info=True)
    finally:
        if sys.stdout != old_stdout:
            sys.stdout = old_stdout # Reset stdout
    await asyncio.sleep(5)
    await message.delete()

# 78. Eval: ارزیابی یک عبارت پایتون (فقط برای صاحب ربات)
@user_bot.on_message(filters.user(OWNER_IDS) & filters.command("eval", prefixes=PREFIX))
async def eval_command(client: Client, message: Message):
    """`.eval <عبارت>`: ارزیابی یک عبارت پایتون روی سرور (فقط برای OWNER_ID)."""
    expression = " ".join(message.command[1:])
    if not expression:
        await message.edit_text("`لطفاً عبارتی برای ارزیابی ارائه دهید.`")
        await asyncio.sleep(2)
        await message.delete()
        return
    
    reply_to_id = message.id
    if message.reply_to_message:
        reply_to_id = message.reply_to_message.id

    try:
        local_vars = {
            "client": client, "message": message, "asyncio": asyncio,
            "__builtins__": {}, # محدود کردن دسترسی
            "eval": None, "exec": None, "input": None, "open": None, "os": None, "sys": None
        }
        result = eval(expression, {"__builtins__": {}}, local_vars)
        if asyncio.iscoroutine(result):
            result = await result
        await message.edit_text(f"`نتیجه eval:`\n`{result}`", reply_to_message_id=reply_to_id)
        logger.warning(f"Eval command executed by OWNER_ID in chat {message.chat.id}")
    except Exception as e:
        output_error = f"`خطا در ارزیابی عبارت:`\n`{e}`"
        await message.edit_text(output_error, reply_to_message_id=reply_to_id)
        logger.error(f"Error in eval command: {e}", exc_info=True)
    await asyncio.sleep(5)
    await message.delete()

# 79. Restart: ری‌استارت ربات (فقط برای صاحب ربات)
@user_bot.on_message(filters.user(OWNER_IDS) & filters.command("restart", prefixes=PREFIX))
async def restart_command(client: Client, message: Message):
    """`.restart`: ری‌استارت ربات (فقط برای OWNER_ID)."""
    try:
        await message.edit_text("`در حال ری‌استارت...`")
        logger.warning("Bot is restarting...")
        await client.stop() 
        os._exit(0) # این دستور باعث خروج از برنامه می شود. نیاز به ابزار خارجی برای ری‌استارت واقعی.
    except Exception as e:
        logger.error(f"Error during restart command: {e}")
        await message.edit_text(f"`خطا در ری‌استارت: {e}`")
    await asyncio.sleep(3)
    await message.delete()

# 80. Help: نمایش لیست دستورات
@user_bot.on_message(filters.me & filters.command("help", prefixes=PREFIX))
async def help_command(client: Client, message: Message):
    """`.help`: نمایش لیست دستورات موجود."""
    # لیست دستورات باید به صورت دستی یا پویا از داک‌استرینگ‌ها جمع‌آوری شود.
    # برای حفظ خطوط، یک لیست جامع و دستی اینجا قرار داده می‌شود.
    help_text = "**لیست دستورات Userbot:**\n\n"
    help_text += f"**🤖 عمومی:**\n"
    help_text += f"`{PREFIX}ping` | `{PREFIX}id` | `{PREFIX}userinfo` | `{PREFIX}chatinfo` | `{PREFIX}me <text>`\n"
    help_text += f"`{PREFIX}echo <text>` | `{PREFIX}type` | `{PREFIX}del` | `{PREFIX}purge` | `{PREFIX}edit <text>`\n"
    help_text += f"`{PREFIX}uptime` | `{PREFIX}sysinfo` | `{PREFIX}pin` | `{PREFIX}unpin` | `{PREFIX}leave`\n"
    help_text += f"`{PREFIX}block` | `{PREFIX}unblock` | `{PREFIX}profile setpic` | `{PREFIX}profile setname` | `{PREFIX}profile setbio`\n"
    help_text += f"`{PREFIX}join <link>` | `{PREFIX}leaveall` | `{PREFIX}check_bots`\n\n"

    help_text += f"**👻 تبچی و AFK:**\n"
    help_text += f"`{PREFIX}spam <count> <text>` | `{PREFIX}fspam <count>` | `{PREFIX}fastspam <count> <text>`\n"
    help_text += f"`{PREFIX}delspam <count>` | `{PREFIX}afk [msg]` | `{PREFIX}unafk` | `{PREFIX}setafkmsg <msg>`\n"
    help_text += "`AFK پاسخ خودکار به پیام‌های خصوصی و منشن‌ها.`\n\n"

    help_text += f"**👥 مدیریت لیست (دشمن، مشتی، عشق):**\n"
    help_text += f"`{PREFIX}addenemy` | `{PREFIX}rmenemy` | `{PREFIX}enemies` | `{PREFIX}loadenemies <file>`\n"
    help_text += f"`{PREFIX}addfriend` | `{PREFIX}rmfriend` | `{PREFIX}friends` | `{PREFIX}loadfriends <file>`\n"
    help_text += f"`{PREFIX}addlove` | `{PREFIX}rmlove` | `{PREFIX}lovers` | `{PREFIX}loadlovers <file>`\n"
    help_text += f"`{PREFIX}clearlist <type>` | `{PREFIX}is <user>`\n"
    help_text += "`واکنش خودکار به پیام‌های دشمنان.`\n\n"

    help_text += f"**😄 سرگرمی:**\n"
    help_text += f"`{PREFIX}roll` | `{PREFIX}coin` | `{PREFIX}8ball <سوال>` | `{PREFIX}react <emoji>` | `{PREFIX}mock <text>`\n"
    help_text += f"`{PREFIX}reverse <text>` | `{PREFIX}shrug` | `{PREFIX}lenny` | `{PREFIX}quote` | `{PREFIX}choose <opt1> <opt2>...`\n"
    help_text += f"`{PREFIX}slap <user>` | `{PREFIX}dice` | `{PREFIX}dart` | `{PREFIX}football` | `{PREFIX}basketball` | `{PREFIX}slot`\n\n"

    help_text += f"**🛠️ ابزارها:**\n"
    help_text += f"`{PREFIX}markdown <text>` | `{PREFIX}html <text>` | `{PREFIX}calc <expr>` | `{PREFIX}shorten <URL>` (Placeholder)\n"
    help_text += f"`{PREFIX}addnote <name> <text>` | `{PREFIX}getnote <name>` | `{PREFIX}delnote <name>` | `{PREFIX}notes`\n"
    help_text += f"`{PREFIX}addbannedword <word>` | `{PREFIX}rmbannedword <word>` | `{PREFIX}bannedwords`\n"
    help_text += "`حذف خودکار پیام‌های حاوی کلمات ممنوعه در گروه‌ها.`\n\n"

    help_text += f"**⚙️ مدیریت گروه (نیاز به دسترسی ادمین):**\n"
    help_text += f"`{PREFIX}kick <user>` | `{PREFIX}ban <user>` | `{PREFIX}unban <user>`\n"
    help_text += f"`{PREFIX}mute <user> [time] [reason]` | `{PREFIX}unmute <user>`\n"
    help_text += f"`{PREFIX}settitle <title>` | `{PREFIX}setdesc <description>`\n\n"

    if OWNER_IDS:
        help_text += f"**⚠️ دستورات توسعه‌دهنده (فقط برای مالک):**\n"
        help_text += f"`{PREFIX}exec <code>` | `{PREFIX}eval <expr>` | `{PREFIX}restart`\n\n"
    
    help_text += "برای جزئیات بیشتر هر دستور، کد را مطالعه کنید یا از `.<command> --help` (اگر پیاده سازی شده باشد) استفاده کنید.\n"
    help_text += f"پیشوند دستورات: `{PREFIX}`"

    try:
        await message.edit_text(help_text, disable_web_page_preview=True)
        logger.info(f"Command '{PREFIX}help' executed.")
    except MessageTooLong:
        # اگر پیام راهنما خیلی طولانی بود، به صورت فایل ارسال شود.
        with StringIO(help_text) as file_buffer:
            await client.send_document(
                chat_id=message.chat.id,
                document=file_buffer.read().encode("utf-8"),
                file_name="userbot_commands_help.txt",
                caption="`لیست دستورات Userbot (خیلی طولانی بود!)`",
                reply_to_message_id=message.id
            )
        logger.warning(f"Help command output too long for message in chat {message.chat.id}. Sent as file.")
    except Exception as e:
        logger.error(f"Error in help command: {e}")
        await message.edit_text(f"`خطا: {e}`")
    await asyncio.sleep(10)
    await message.delete()


# --- شروع به کار ربات ---
async def main():
    logger.info("Starting Userbot...")
    await load_initial_data() # بارگذاری داده‌ها در زمان شروع
    await user_bot.start()
    me = await user_bot.get_me()
    logger.info(f"Userbot started as @{me.username} ({me.first_name}).")
    logger.info(f"Commands prefix: '{PREFIX}'")
    if OWNER_IDS:
        logger.info(f"Owner IDs configured: {OWNER_IDS}")
        # ارسال پیام شروع به مالک
        try:
            await user_bot.send_message(OWNER_IDS[0], f"**✅ Userbot فعال شد!**\n`@{me.username}` در حال گوش دادن به دستورات است.\n`{PREFIX}help` برای مشاهده دستورات.")
        except Exception as e:
            logger.error(f"Failed to send startup message to owner: {e}")
    else:
        logger.warning("OWNER_ID is not configured. Sensitive commands like exec/eval/restart will not work.")
    
    logger.info("Userbot is ready and listening for commands!")
    await user_bot.idle() # ربات را در حالت اجرا نگه می دارد تا زمانی که Ctrl+C فشرده شود
    logger.info("Userbot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Userbot stopped by user (Ctrl+C).")
    except Exception as e:
        logger.exception("An unhandled error occurred during Userbot execution:")
