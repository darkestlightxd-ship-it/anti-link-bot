# anti_link_bot_mongo_full.py
import os
import re
import time
import asyncio
from typing import Optional, List

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.enums import ChatMemberStatus
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

from motor.motor_asyncio import AsyncIOMotorClient

# === DIRECT CREDENTIALS (as requested) ===
API_TOKEN = "8470214636:AAExm5uh4tu621S5zvHDMDfWQzxruvgvuwY"
MONGO_URI = "mongodb+srv://darkestlightxd_db_user:dbuserinsaan085122@cluster0.cwamdsd.mongodb.net/anti_link_bot"
LOG_CHAT_ID = -1003086724642

# === Init bot/dispatcher ===
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# === MongoDB connection ===
if not MONGO_URI:
    raise RuntimeError("MONGO_URI not provided")

mongo = AsyncIOMotorClient(MONGO_URI)
# If your URI includes the DB name (it does: anti_link_bot), get_default_database() will return it.
db = mongo.get_default_database()

# Collections
col_groups = db["group_settings"]
col_whitelist = db["whitelist"]
col_approved = db["approved_users"]
col_warnings = db["warnings"]
col_buttons = db["dynamic_buttons"]
col_meta = db["meta"]

# Defaults / Helpers ===
DEFAULT_GROUP_SETTINGS = {"links": True, "biolinks": True, "username": True, "botlink": True}
MUTE_DURATION_MIN = 5

LINK_PATTERN = re.compile(r"(https?://|t\.me/|wa\.me|instagram\.com|youtube\.com|facebook\.com|twitter.com|whatsapp\.com|linkedin\.com|snapchat\.com|pinterest\.com|reddit\.com|tiktok\.com|discord\.gg|telegram\.me)", re.IGNORECASE)
BOTNAME_PATTERN = re.compile(r"@[\w_]*bot", re.IGNORECASE)
USERNAME_PATTERN = re.compile(r"@[\w_]+", re.IGNORECASE)

BOT_OWNER_ID = 6156257558
OWNER_USERNAME = "Insaanova"
UPDATES_USERNAME = "FRIENDS_CORNER_CHATTING_GROUP"

def is_owner(user_id: int) -> bool:
    return user_id == BOT_OWNER_ID

# --- DB accessors ---
async def get_group_settings(chat_id: int) -> dict:
    doc = await col_groups.find_one({"_id": chat_id})
    if not doc:
        doc = DEFAULT_GROUP_SETTINGS.copy()
        doc["_id"] = chat_id
        await col_groups.insert_one(doc)
    return {k: doc.get(k, DEFAULT_GROUP_SETTINGS[k]) for k in DEFAULT_GROUP_SETTINGS}

async def set_group_setting(chat_id: int, key: str, value: bool):
    await col_groups.update_one({"_id": chat_id}, {"$set": {key: value}}, upsert=True)

async def is_whitelisted(user_id: int) -> bool:
    doc = await col_whitelist.find_one({"_id": user_id})
    return doc is not None

async def whitelist_add(user_id: int, full_name: str = "", username: Optional[str] = None):
    await col_whitelist.update_one({"_id": user_id}, {"$set": {"full_name": full_name, "username": username}}, upsert=True)

async def whitelist_remove(user_id: int):
    await col_whitelist.delete_one({"_id": user_id})

async def get_whitelist_list(limit: int = 100) -> List[dict]:
    docs = col_whitelist.find().limit(limit)
    return [d async for d in docs]

async def is_approved(user_id: int) -> bool:
    doc = await col_approved.find_one({"_id": user_id})
    return doc is not None

async def approve_user(user_id: int):
    await col_approved.update_one({"_id": user_id}, {"$set": {"approved": True}}, upsert=True)

async def unapprove_user(user_id: int):
    await col_approved.delete_one({"_id": user_id})

async def get_warning_count(user_id: int) -> int:
    doc = await col_warnings.find_one({"_id": user_id})
    return doc.get("count", 0) if doc else 0

async def inc_warning(user_id: int) -> int:
    await col_warnings.update_one({"_id": user_id}, {"$inc": {"count": 1}}, upsert=True)
    doc = await col_warnings.find_one({"_id": user_id})
    return doc.get("count", 0)

async def reset_warnings(user_id: int):
    await col_warnings.delete_one({"_id": user_id})

async def get_dynamic_buttons() -> List[dict]:
    doc = await col_buttons.find_one({"_id": "buttons"})
    if not doc:
        default = [
            {"text": "👑 Owner", "url": f"https://t.me/{OWNER_USERNAME}"},
            {"text": "📢 Updates", "url": f"https://t.me/{UPDATES_USERNAME}"},
            {"text": "❓ Help & Commands", "callback_data": "help"}
        ]
        await col_buttons.insert_one({"_id": "buttons", "buttons": default})
        return default
    return doc.get("buttons", [])

async def set_dynamic_buttons(new_buttons: List[dict]):
    await col_buttons.update_one({"_id": "buttons"}, {"$set": {"buttons": new_buttons}}, upsert=True)

async def get_meta(key: str, default=None):
    doc = await col_meta.find_one({"_id": key})
    return doc.get("value") if doc else default

async def set_meta(key: str, value):
    await col_meta.update_one({"_id": key}, {"$set": {"value": value}}, upsert=True)

# utility
def has_links(text: str) -> bool:
    if not text:
        return False
    return bool(LINK_PATTERN.search(text))

def has_bot_username(text: str) -> bool:
    if not text:
        return False
    return bool(BOTNAME_PATTERN.search(text))

def has_username(text: str) -> bool:
    if not text:
        return False
    return bool(USERNAME_PATTERN.search(text))

async def auto_delete(msg: types.Message, delay: int = 5):
    try:
        await asyncio.sleep(delay)
        try:
            await msg.delete()
        except TelegramBadRequest as e:
            if "message to delete not found" not in str(e).lower():
                print(f"Auto delete failed: {e}")
    except asyncio.CancelledError:
        print("Auto-delete task cancelled")
    except Exception as e:
        print(f"Error in auto_delete: {e}")

async def check_user_bio(user_id: int) -> bool:
    try:
        user = await bot.get_chat(user_id)
        bio = getattr(user, "bio", "") or ""
        return has_links(bio) or has_username(bio) or has_bot_username(bio)
    except TelegramBadRequest as e:
        print(f"Bio check failed for user {user_id}: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error in bio check: {e}")
        return False

async def get_personal_buttons() -> InlineKeyboardMarkup:
    me = await bot.get_me()
    btns = await get_dynamic_buttons()
    keyboard = []
    current_row = []
    for i, btn in enumerate(btns):
        if 'url' in btn:
            current_row.append(InlineKeyboardButton(text=btn['text'], url=btn['url']))
        else:
            current_row.append(InlineKeyboardButton(text=btn['text'], callback_data=btn['callback_data']))
        if len(current_row) == 2 or i == len(btns) - 1:
            keyboard.append(current_row)
            current_row = []
    keyboard.append([InlineKeyboardButton(text="➕ Add me to your group", url=f"https://t.me/{me.username}?startgroup=true")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

BASIC_HELP_TEXT = """🤖 *Anti-Link Bot Help*

I automatically detect and remove risky content including:
• Links (http/https, t.me, wa.me, etc.)
• Social media links  
• Usernames with "bot"
• Regular usernames (@username)
• Bio links"""
ADMIN_HELP_TEXT = """🔧 *Admin Commands:*
• `/links on|off` - Toggle link detection
• `/username on|off` - Toggle username detection  
• `/biolinks on|off` - Toggle bio link detection
• `/botlink on|off` - Toggle bot username detection
• `/whitelistadd` - Reply to user to whitelist them
• `/whitelistremove` - Reply to remove from whitelist
• `/whitelistshow` - Show whitelisted users
• `/approveme` - Approve yourself to send links"""
OWNER_HELP_TEXT = """👑 *Owner Commands:*
• `/botstats` - Bot statistics
• `/listgroups` - Groups list
• `/whitelist_info` - Whitelisted users info
• `/groupinfo <id>` - Group information
• `/broadcast <msg>` - Broadcast message
• `/restart` - Restart bot
• `/maintenance on|off` - Maintenance mode
• `/setbuttons` - Change buttons
• `/previewbuttons` - Preview buttons
• `/resetbuttons` - Reset buttons"""
help_keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")],[InlineKeyboardButton(text="❌ Close", callback_data="close_help")]])


# === Handlers ===

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.chat.type != "private":
        return
    user_name = message.from_user.first_name
    photo_url = "https://cftc-15g.pages.dev/1758448580525_file_1758448580525.jpg"
    try:
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=photo_url,
            caption=f"Hey 👋🏻 {user_name}\n\n"
                    "Welcome to Links Shield Bot\n\n"
                    "I protect your group from:\n"
                    "➤ All spam Links & URLs\n"
                    "➤ Username (@example)\n"
                    "➤ Bot Usernames (@bot)\n"
                    "➤ Bio Links also\n"
                    "➤ Admin Links too\n\n"
                    "Admins: Use /approveme to send links\n\n"
                    "Add me to your group & make me admin!",
            parse_mode="Markdown",
            reply_markup=await get_personal_buttons()
        )
    except Exception as e:
        print(f"Photo send failed: {e}")
        await message.reply(
            f"Hey 👋🏻 {user_name}\n\n"
            "Welcome to Links Shield Bot\n"
            "I protect your group from spam & risky links.\n\n"
            "Admins: Use /approveme to send links\n\n"
            "Add me to your group & make me admin!",
            parse_mode="Markdown",
            reply_markup=await get_personal_buttons()
        )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    if message.chat.type == "private":
        if is_owner(message.from_user.id):
            help_text = f"{BASIC_HELP_TEXT}\n\n{ADMIN_HELP_TEXT}\n\n{OWNER_HELP_TEXT}"
        else:
            help_text = f"{BASIC_HELP_TEXT}\n\n{ADMIN_HELP_TEXT}"
        await message.reply(help_text, reply_markup=help_keyboard, parse_mode="Markdown")
    else:
        try:
            member = await bot.get_chat_member(message.chat.id, message.from_user.id)
            if member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                if is_owner(message.from_user.id):
                    help_text = f"{BASIC_HELP_TEXT}\n\n{ADMIN_HELP_TEXT}\n\n{OWNER_HELP_TEXT}"
                else:
                    help_text = f"{BASIC_HELP_TEXT}\n\n{ADMIN_HELP_TEXT}"
                help_msg = await message.reply(help_text, parse_mode="Markdown")
                await asyncio.sleep(10)
                await help_msg.delete()
            else:
                status_msg = await message.reply("❌ Only admins can use this command!")
                await asyncio.sleep(5)
                await status_msg.delete()
        except:
            status_msg = await message.reply("❌ Only admins can use this command!")
            await asyncio.sleep(5)
            await status_msg.delete()


@dp.message(Command("approveme"))
async def approve_me(message: types.Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        status_msg = await message.reply("❌ Only admins can use this command!")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    await approve_user(message.from_user.id)
    status_msg = await message.reply(
        f"✅ @{message.from_user.username or message.from_user.first_name} approved!\nYou can now send links in this group."
    )
    asyncio.create_task(auto_delete(status_msg, 10))


# --- admin check wrapper
async def is_admin(chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]
    except TelegramBadRequest as e:
        print(f"Admin check failed for user {user_id} in chat {chat_id}: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error in admin check: {e}")
        return False


# Toggle commands
@dp.message(Command("biolinks"))
async def toggle_biolinks(message: types.Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        status_msg = await message.reply("❌ Only admins can use this command!")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    args = message.text.split()
    if len(args) != 2 or args[1].lower() not in ["on", "off"]:
        status_msg = await message.reply("Usage: /biolinks on|off")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    await set_group_setting(message.chat.id, "biolinks", args[1].lower() == "on")
    status_msg = await message.reply(f"Bio links deletion set to {'ON ✅' if args[1].lower() == 'on' else 'OFF ❌'}")
    asyncio.create_task(auto_delete(status_msg, 10))

@dp.message(Command("links"))
async def toggle_links(message: types.Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        status_msg = await message.reply("❌ Only admins can use this command!")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    args = message.text.split()
    if len(args) != 2 or args[1].lower() not in ["on", "off"]:
        status_msg = await message.reply("Usage: /links on|off")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    await set_group_setting(message.chat.id, "links", args[1].lower() == "on")
    status_msg = await message.reply(f"Links deletion set to {'ON ✅' if args[1].lower() == 'on' else 'OFF ❌'}")
    asyncio.create_task(auto_delete(status_msg, 10))

@dp.message(Command("username"))
async def toggle_username(message: types.Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        status_msg = await message.reply("❌ Only admins can use this command!")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    args = message.text.split()
    if len(args) != 2 or args[1].lower() not in ["on", "off"]:
        status_msg = await message.reply("Usage: /username on|off")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    await set_group_setting(message.chat.id, "username", args[1].lower() == "on")
    status_msg = await message.reply(f"Username deletion set to {'ON ✅' if args[1].lower() == 'on' else 'OFF ❌'}")
    asyncio.create_task(auto_delete(status_msg, 10))

@dp.message(Command("botlink"))
async def toggle_botlink(message: types.Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        status_msg = await message.reply("❌ Only admins can use this command!")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    args = message.text.split()
    if len(args) != 2 or args[1].lower() not in ["on", "off"]:
        status_msg = await message.reply("Usage: /botlink on|off")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    await set_group_setting(message.chat.id, "botlink", args[1].lower() == "on")
    status_msg = await message.reply(f"Bot usernames deletion set to {'ON ✅' if args[1].lower() == 'on' else 'OFF ❌'}")
    asyncio.create_task(auto_delete(status_msg, 10))


# Whitelist commands
@dp.message(Command("whitelistadd"))
async def whitelist_add_cmd(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        status_msg = await message.reply("❌ Only admins can use this command!")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    user_id = None
    user_name = ""
    username = None

    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        user_name = message.reply_to_message.from_user.full_name
        username = message.reply_to_message.from_user.username
    else:
        parts = message.text.split()
        if len(parts) > 1:
            maybe = parts[1].strip().lstrip('@')
            # try to resolve chat member by username in chat (may fail)
            try:
                chat_member = await bot.get_chat_member(message.chat.id, maybe)
                user_id = chat_member.user.id
                user_name = chat_member.user.full_name
                username = chat_member.user.username
            except:
                # fallback: try convert to int ID
                try:
                    user_id = int(maybe)
                    u = await bot.get_chat(user_id)
                    user_name = u.full_name
                    username = u.username
                except Exception as e:
                    pass

    if not user_id:
        status_msg = await message.reply("❌ Usage: Reply to user or /whitelistadd @username or /whitelistadd <id>")
        asyncio.create_task(auto_delete(status_msg, 10))
        return

    await whitelist_add(user_id, full_name=user_name, username=username)
    status_msg = await message.reply(f"✅ {user_name} (ID: {user_id}) whitelisted successfully!")
    asyncio.create_task(auto_delete(status_msg, 10))

@dp.message(Command("whitelistremove"))
async def whitelist_remove_cmd(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        status_msg = await message.reply("❌ Only admins can use this command!")
        asyncio.create_task(auto_delete(status_msg, 10))
        return

    user_id = None
    user_name = ""
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        user_name = message.reply_to_message.from_user.full_name
    else:
        parts = message.text.split()
        if len(parts) > 1:
            arg = parts[1].strip().lstrip('@')
            # try id
            try:
                user_id = int(arg)
                u = await bot.get_chat(user_id)
                user_name = u.full_name
            except:
                doc = await col_whitelist.find_one({"username": arg})
                if doc:
                    user_id = doc["_id"]
                    user_name = doc.get("full_name", "")
    if not user_id:
        status_msg = await message.reply("❌ Usage: Reply to user or /whitelistremove @username or /whitelistremove <id>")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    await whitelist_remove(user_id)
    await unapprove_user(user_id)
    status_msg = await message.reply(f"❌ {user_name} (ID: {user_id}) removed from whitelist!")
    asyncio.create_task(auto_delete(status_msg, 10))

@dp.message(Command("whitelistshow"))
async def whitelist_show_cmd(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        status_msg = await message.reply("❌ Only admins can use this command!")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    docs = await get_whitelist_list(limit=50)
    if not docs:
        status_msg = await message.reply("No whitelisted users.")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    txt = "👤 *Whitelisted Users:*\n"
    for d in docs:
        uname = f"@{d.get('username')}" if d.get('username') else "No username"
        fname = d.get("full_name", "Unknown")
        txt += f"• {fname} ({uname}) - ID: `{d['_id']}`\n"
    status_msg = await message.reply(txt, parse_mode="Markdown")
    asyncio.create_task(auto_delete(status_msg, 15))


# Owner commands
@dp.message(Command("botstats"))
async def bot_stats(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    groups_count = await col_groups.count_documents({})
    whitelist_count = await col_whitelist.count_documents({})
    approved_count = await col_approved.count_documents({})
    warnings_total_cursor = col_warnings.aggregate([{"$group": {"_id": None, "sum": {"$sum": "$count"}}}])
    warnings_total = 0
    async for doc in warnings_total_cursor:
        warnings_total = doc.get("sum", 0)
    stats_text = (
        f"🤖 **Bot Statistics**\n\n"
        f"📊 Total Groups: {groups_count}\n"
        f"👤 Whitelisted Users: {whitelist_count}\n"
        f"✅ Approved Users: {approved_count}\n"
        f"⚠️ Total Warnings: {warnings_total}\n"
        f"🕒 Start Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await message.reply(stats_text)


@dp.message(Command("listgroups"))
async def list_groups(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    cursor = col_groups.find().limit(15)
    groups_list = []
    async for doc in cursor:
        chat_id = doc["_id"]
        try:
            chat = await bot.get_chat(chat_id)
            group_name = chat.title
            groups_list.append(f"• {group_name} (ID: {chat_id})")
        except:
            groups_list.append(f"• Unknown Group (ID: {chat_id})")
    if not groups_list:
        await message.reply("❌ No groups data available.")
        return
    response = "👥 **Groups List (First 15):**\n\n" + "\n".join(groups_list)
    await message.reply(response)


@dp.message(Command("whitelist_info"))
async def whitelist_info(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    cursor = col_whitelist.find().limit(20)
    rows = []
    async for d in cursor:
        uname = f"@{d.get('username')}" if d.get('username') else "No username"
        rows.append(f"• {d.get('full_name','Unknown')} ({uname}) - ID: {d['_id']}")
    if not rows:
        await message.reply("❌ No whitelisted users.")
        return
    await message.reply("👤 **Whitelisted Users (First 20):**\n\n" + "\n".join(rows))


@dp.message(Command("groupinfo"))
async def group_info_owner(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.reply("❌ Usage: /groupinfo <group_id>")
        return
    try:
        chat_id = int(args[1])
        chat = await bot.get_chat(chat_id)
        settings = await get_group_settings(chat_id)
        members = "Unknown"
        info_text = (
            f"👥 **Group Info:**\n\n"
            f"• **Name:** {getattr(chat, 'title', 'Unknown')}\n"
            f"• **ID:** {chat_id}\n"
            f"• **Type:** {getattr(chat, 'type', 'Unknown')}\n"
            f"• **Members:** {members}\n\n"
            f"⚙️ **Settings:**\n"
            f"• Links: {'✅ ON' if settings.get('links', True) else '❌ OFF'}\n"
            f"• Bio Links: {'✅ ON' if settings.get('biolinks', True) else '❌ OFF'}\n"
            f"• Usernames: {'✅ ON' if settings.get('username', True) else '❌ OFF'}\n"
            f"• Bot Links: {'✅ ON' if settings.get('botlink', True) else '❌ OFF'}\n"
        )
        await message.reply(info_text)
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")


# Broadcast flow
@dp.message(Command("broadcast"))
async def broadcast_message(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    if not message.reply_to_message:
        await message.reply("❌ Reply to a message to broadcast it.")
        return
    pending = {
        "chat_id": message.reply_to_message.chat.id,
        "message_id": message.reply_to_message.message_id
    }
    await set_meta("pending_broadcast", pending)
    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm", callback_data="broadcast_confirm"),
         InlineKeyboardButton(text="❌ Cancel", callback_data="broadcast_cancel")]
    ])
    await message.reply(f"⚠️ **Broadcast Confirmation**\n\nThis will send the message to all {await col_groups.count_documents({})} groups. Continue?", reply_markup=confirm_keyboard)


@dp.callback_query(F.data == "broadcast_confirm")
async def confirm_broadcast(callback: types.CallbackQuery):
    if not is_owner(callback.from_user.id):
        return
    pending = await get_meta("pending_broadcast", None)
    if not pending:
        await callback.answer("No pending broadcast.")
        return
    await callback.message.edit_text("📤 Broadcasting started...")
    success = 0
    failed = 0
    async for doc in col_groups.find():
        chat_id = doc["_id"]
        try:
            await bot.copy_message(chat_id=chat_id, from_chat_id=pending["chat_id"], message_id=pending["message_id"])
            success += 1
            await asyncio.sleep(0.4)
        except Exception as e:
            print(f"Broadcast failed to {chat_id}: {e}")
            failed += 1
    await callback.message.edit_text(f"📊 **Broadcast Complete**\n\n✅ Success: {success}\n❌ Failed: {failed}\n📋 Total: {success+failed}")
    await set_meta("pending_broadcast", None)

@dp.callback_query(F.data == "broadcast_cancel")
async def cancel_broadcast(callback: types.CallbackQuery):
    if not is_owner(callback.from_user.id):
        return
    await set_meta("pending_broadcast", None)
    await callback.message.edit_text("❌ Broadcast cancelled.")


@dp.message(Command("restart"))
async def restart_bot(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    await message.reply("🔄 Restarting bot...")
    os.execv(sys.executable, [sys.executable] + sys.argv)


@dp.message(Command("maintenance"))
async def maintenance_mode(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    args = message.text.split()
    if len(args) != 2 or args[1].lower() not in ["on", "off"]:
        await message.reply("Usage: /maintenance on|off")
        return
    active = args[1].lower() == "on"
    await set_meta("maintenance_active", active)
    status = "🟢 ACTIVATED" if active else "🔴 DEACTIVATED"
    await message.reply(f"🔧 Maintenance mode: {status}")


# Button management
@dp.message(Command("setbuttons"))
async def set_buttons(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.reply("Usage: /setbuttons text - url | text - callback_data")
            return
        buttons_config = args[1].split('|')
        new_buttons = []
        for btn_config in buttons_config:
            btn_config = btn_config.strip()
            if '-' in btn_config:
                text, data = btn_config.split('-', 1)
                text = text.strip()
                data = data.strip()
                if data.startswith(('http://', 'https://', 't.me/')):
                    new_buttons.append({"text": text, "url": data})
                else:
                    new_buttons.append({"text": text, "callback_data": data})
        if new_buttons:
            await set_dynamic_buttons(new_buttons)
            await message.reply("✅ Buttons updated successfully!")
        else:
            await message.reply("❌ No valid buttons found.")
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")

@dp.message(Command("previewbuttons"))
async def preview_buttons(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    buttons = await get_dynamic_buttons()
    buttons_info = "\n".join([f"• {btn['text']} -> {btn.get('url', btn.get('callback_data', 'No data'))}" for btn in buttons])
    await message.reply(f"🔘 **Current Buttons:**\n\n{buttons_info}\n\nTotal: {len(buttons)} buttons", reply_markup=await get_personal_buttons())

@dp.message(Command("resetbuttons"))
async def reset_buttons(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    default = [
        {"text": "👑 Owner", "url": f"https://t.me/{OWNER_USERNAME}"},
        {"text": "📢 Updates", "url": f"https://t.me/{UPDATES_USERNAME}"},
        {"text": "❓ Help & Commands", "callback_data": "help"}
    ]
    await set_dynamic_buttons(default)
    await message.reply("✅ Buttons reset to default!")


@dp.callback_query(F.data == "help")
async def help_callback(callback: types.CallbackQuery):
    try:
        if is_owner(callback.from_user.id):
            help_text = f"{BASIC_HELP_TEXT}\n\n{ADMIN_HELP_TEXT}\n\n{OWNER_HELP_TEXT}"
        else:
            help_text = f"{BASIC_HELP_TEXT}\n\n{ADMIN_HELP_TEXT}"
        try:
            await callback.message.delete()
        except:
            pass
        await bot.send_message(chat_id=callback.from_user.id, text=help_text, reply_markup=help_keyboard, parse_mode="Markdown")
        await callback.answer("Help menu opened!")
    except Exception as e:
        print(f"Error in help callback: {e}")
        await callback.answer("❌ Error!")

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery):
    try:
        user_name = callback.from_user.first_name
        try:
            await callback.message.delete()
        except:
            pass
        photo_url = "https://cftc-15g.pages.dev/1758448580525_file_1758448580525.jpg"
        try:
            await bot.send_photo(chat_id=callback.from_user.id, photo=photo_url,
                                 caption=f"Hey 👋🏻 {user_name}\n\nWelcome to Links Shield Bot\n\nI protect your group from spam & links.\n\nAdmins: Use /approveme to send links\n\nAdd me to your group & make me admin!",
                                 parse_mode="Markdown", reply_markup=await get_personal_buttons())
        except Exception:
            await bot.send_message(chat_id=callback.from_user.id, text=f"Hey 👋🏻 {user_name}\n\nWelcome to Links Shield Bot",
                                   reply_markup=await get_personal_buttons())
        await callback.answer("Back to main menu!")
    except Exception as e:
        print(f"Error in back_to_main: {e}")
        await callback.answer("❌ Error!")

@dp.callback_query(F.data == "close_help")
async def close_help(callback: types.CallbackQuery):
    try:
        await callback.message.delete()
        await callback.answer("Help menu closed!")
    except Exception as e:
        await callback.answer("✅ Closed")


@dp.callback_query(F.data.startswith("unmute:"))
async def unmute_user(callback: types.CallbackQuery):
    if not await is_admin(callback.message.chat.id, callback.from_user.id):
        await callback.answer("❌ Only admins can unmute!")
        return
    user_id = int(callback.data.split(":")[1])
    try:
        await bot.restrict_chat_member(callback.message.chat.id, user_id, permissions=types.ChatPermissions(can_send_messages=True))
        await callback.message.edit_text(f"✅ User unmuted successfully!")
        await asyncio.sleep(3)
        await callback.message.delete()
    except Exception as e:
        await callback.answer(f"❌ Unmute failed: {e}")


async def delete_admin_message(message: types.Message):
    try:
        await message.delete()
    except TelegramBadRequest as e:
        print(f"Delete failed: {e}")
        return
    buttons = await get_dynamic_buttons()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=b["text"], url=b["url"])] for b in buttons if "url" in b][:2])
    deletion_msg = await message.answer(
        f"🗑️ Admin @{message.from_user.username or message.from_user.first_name}, your link was deleted. Please approve yourself first using /approveme",
        reply_markup=kb
    )
    asyncio.create_task(auto_delete(deletion_msg))


async def warn_and_delete(message: types.Message, violation_type: str = "links"):
    try:
        is_user_admin = await is_admin(message.chat.id, message.from_user.id)
        if is_user_admin:
            await delete_admin_message(message)
            return
        if await is_whitelisted(message.from_user.id) or await is_approved(message.from_user.id):
            return
        user_id = message.from_user.id
        warnings_count = await inc_warning(user_id)
        try:
            await message.delete()
        except TelegramBadRequest as e:
            print(f"Delete failed: {e}")
            return
        name = message.from_user.username or message.from_user.first_name
        if violation_type == "biolinks":
            warning_text = f"👤 @{name} Your message was hidden. Bio links are not allowed in this group, please remove them."
        elif violation_type == "links":
            warning_text = f"👤 @{name} Your message was hidden. Links are not allowed in this group, please remove them."
        elif violation_type == "username":
            warning_text = f"👤 @{name} Your message was hidden. Usernames are not allowed in this group, please remove them."
        elif violation_type == "botlink":
            warning_text = f"👤 @{name} Your message was hidden. Bot usernames are not allowed in this group, please remove them."
        else:
            warning_text = f"⚠️ @{name} Warning {warnings_count}/3 - Risky content not allowed!"
        buttons = await get_dynamic_buttons()
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=b["text"], url=b["url"])] for b in buttons if "url" in b][:2])
        warning_msg = await message.answer(warning_text, reply_markup=kb)
        asyncio.create_task(auto_delete(warning_msg))
        try:
            await bot.send_message(LOG_CHAT_ID, f"⚠️ @{name} sent {violation_type} in {message.chat.title}")
        except Exception as e:
            print(f"Log failed: {e}")
        if warnings_count >= 3:
            until_date = int(time.time()) + MUTE_DURATION_MIN * 60
            try:
                await bot.restrict_chat_member(message.chat.id, user_id, permissions=types.ChatPermissions(can_send_messages=False), until_date=until_date)
                mute_msg = await bot.send_message(message.chat.id, f"🔇 @{name} muted for {MUTE_DURATION_MIN} min.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Unmute", callback_data=f"unmute:{user_id}")]]))
                asyncio.create_task(auto_delete(mute_msg))
                await reset_warnings(user_id)
                try:
                    await bot.send_message(LOG_CHAT_ID, f"🔇 @{name} muted in {message.chat.title}")
                except:
                    pass
            except TelegramBadRequest as e:
                error_msg = await message.reply("❌ I need admin permissions to mute users!")
                asyncio.create_task(auto_delete(error_msg))
                print(f"Mute failed: {e}")
    except Exception as e:
        print(f"Error in warn_and_delete: {e}")


@dp.message(F.text | F.caption)
async def filter_messages(message: types.Message):
    try:
        maintenance_active = await get_meta("maintenance_active", False)
        if maintenance_active and not is_owner(message.from_user.id):
            return
        if message.chat.type not in ["group", "supergroup"]:
            return
        if await is_whitelisted(message.from_user.id) or await is_approved(message.from_user.id):
            return
        settings = await get_group_settings(message.chat.id)
        text = message.text or message.caption or ""
        print(f"Checking message from {message.from_user.id} ({message.from_user.username}): {text[:50]}...")
        if settings.get("links", True) and has_links(text):
            await warn_and_delete(message, "links")
            return
        if settings.get("botlink", True) and has_bot_username(text):
            await warn_and_delete(message, "botlink")
            return
        if settings.get("username", True) and has_username(text):
            await warn_and_delete(message, "username")
            return
        if settings.get("biolinks", True):
            has_bio_links = await check_user_bio(message.from_user.id)
            if has_bio_links:
                await warn_and_delete(message, "biolinks")
                return
        print(f"Message from {message.from_user.id} passed all checks")
    except Exception as e:
        print(f"Error in filter_messages: {e}")


@dp.errors()
async def error_handler(update: types.Update, exception: Exception):
    print(f"Update: {update}")
    print(f"Exception type: {type(exception).__name__}")
    print(f"Exception details: {exception}")
    if "critical" in str(exception).lower() or "forbidden" in str(exception).lower():
        try:
            await bot.send_message(LOG_CHAT_ID, f"⚠️ Bot Error: {type(exception).__name__}\n\n{str(exception)[:1000]}")
        except:
            pass
    return True


# === Main run ===
async def main():
    print("🤖 Bot starting (MongoDB integrated)...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import sys
    asyncio.run(main())
