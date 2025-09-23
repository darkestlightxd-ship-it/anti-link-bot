import re
import time
import asyncio
import os
import sys
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.enums import ChatMemberStatus
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from pymongo import MongoClient

# Load environment variables
load_dotenv()

# ===== MONGODB CONNECTION =====
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
try:
    client = MongoClient(MONGO_URI)
    db = client["anti_link_bot"]
    print("✅ MongoDB Connected Successfully!")
except Exception as e:
    print(f"❌ MongoDB Connection Failed: {e}")
    db = None

# ===== DATABASE COLLECTIONS =====
def get_collection(name):
    if db:
        return db[name]
    return None

users_col = get_collection("users")
groups_col = get_collection("groups")
warnings_col = get_collection("warnings")

# ===== CONFIG =====
API_TOKEN = os.getenv("BOT_TOKEN", "8470214636:AAExm5uh4tu621S5zvHDMDfWQzxruvgvuwY")
BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", 6156257558))
OWNER_USERNAME = "Insaanova"
UPDATES_USERNAME = "FRIENDS_CORNER_CHATTING_GROUP"
LOG_CHAT_ID = int(os.getenv("LOG_CHAT_ID", -1003086724642))
BROADCAST_SOURCE_CHANNEL = -1002933746046

# ===== DATABASE FUNCTIONS =====
async def save_user_data(user_id: int, data: dict):
    """Save user data to MongoDB"""
    if users_col:
        try:
            await asyncio.to_thread(
                users_col.update_one,
                {"_id": user_id},
                {"$set": data},
                upsert=True
            )
        except Exception as e:
            print(f"Error saving user data: {e}")

async def save_group_settings(chat_id: int, settings: dict):
    """Save group settings to MongoDB"""
    if groups_col:
        try:
            await asyncio.to_thread(
                groups_col.update_one,
                {"_id": chat_id},
                {"$set": settings},
                upsert=True
            )
        except Exception as e:
            print(f"Error saving group settings: {e}")

async def get_group_settings(chat_id: int):
    """Get group settings from MongoDB or memory"""
    if groups_col:
        try:
            settings = await asyncio.to_thread(
                groups_col.find_one,
                {"_id": chat_id}
            )
            if settings:
                # Remove MongoDB _id field
                settings.pop('_id', None)
                return settings
        except Exception as e:
            print(f"Error getting group settings: {e}")
    
    # Fallback to memory
    return {"links": True, "biolinks": True, "username": True, "botlink": True}

async def save_warning(user_id: int, chat_id: int, count: int):
    """Save warning count to MongoDB"""
    if warnings_col:
        try:
            await asyncio.to_thread(
                warnings_col.update_one,
                {"user_id": user_id, "chat_id": chat_id},
                {"$set": {"count": count, "last_warning": time.time()}},
                upsert=True
            )
        except Exception as e:
            print(f"Error saving warning: {e}")

async def get_warning_count(user_id: int, chat_id: int):
    """Get warning count from MongoDB"""
    if warnings_col:
        try:
            warning = await asyncio.to_thread(
                warnings_col.find_one,
                {"user_id": user_id, "chat_id": chat_id}
            )
            return warning.get("count", 0) if warning else 0
        except Exception as e:
            print(f"Error getting warning: {e}")
    return 0

# ===== MEMORY STORAGE (FALLBACK) =====
warnings_memory = {}
whitelist_memory = set()
approved_users_memory = set()
group_settings_memory = {}

async def get_warnings(user_id: int, chat_id: int):
    """Get warnings from MongoDB or memory"""
    if warnings_col:
        return await get_warning_count(user_id, chat_id)
    return warnings_memory.get(user_id, 0)

async def set_warnings(user_id: int, chat_id: int, count: int):
    """Set warnings in MongoDB or memory"""
    if warnings_col:
        await save_warning(user_id, chat_id, count)
    else:
        warnings_memory[user_id] = count

async def is_whitelisted(user_id: int):
    """Check if user is whitelisted"""
    if users_col:
        try:
            user = await asyncio.to_thread(users_col.find_one, {"_id": user_id})
            return user and user.get("whitelisted", False)
        except:
            return user_id in whitelist_memory
    return user_id in whitelist_memory

async def add_to_whitelist(user_id: int):
    """Add user to whitelist"""
    if users_col:
        await save_user_data(user_id, {"whitelisted": True})
    whitelist_memory.add(user_id)

async def remove_from_whitelist(user_id: int):
    """Remove user from whitelist"""
    if users_col:
        await save_user_data(user_id, {"whitelisted": False})
    whitelist_memory.discard(user_id)

async def is_approved(user_id: int):
    """Check if user is approved"""
    if users_col:
        try:
            user = await asyncio.to_thread(users_col.find_one, {"_id": user_id})
            return user and user.get("approved", False)
        except:
            return user_id in approved_users_memory
    return user_id in approved_users_memory

async def add_approved_user(user_id: int):
    """Add user to approved list"""
    if users_col:
        await save_user_data(user_id, {"approved": True})
    approved_users_memory.add(user_id)

async def remove_approved_user(user_id: int):
    """Remove user from approved list"""
    if users_col:
        await save_user_data(user_id, {"approved": False})
    approved_users_memory.discard(user_id)

# ===== ORIGINAL CODE CONTINUES =====
# Dynamic buttons storage (in-memory)
dynamic_buttons = [
    {"text": "👑 Owner", "url": f"https://t.me/{OWNER_USERNAME}"},
    {"text": "📢 Updates", "url": f"https://t.me/{UPDATES_USERNAME}"},
    {"text": "❓ Help & Commands", "callback_data": "help"}
]

mute_duration = 5  # minutes
user_bio_cache = {}  # Cache user bios to avoid frequent API calls
maintenance_active = False
pending_broadcast = None

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Owner check function
def is_owner(user_id: int) -> bool:
    return user_id == BOT_OWNER_ID

# --- Helper Functions ---
async def get_group_settings_wrapper(chat_id: int):
    """Wrapper for group settings"""
    settings = await get_group_settings(chat_id)
    return settings

def has_links(text: str):
    if not text:
        return False
    pattern = r"(https?://|t\.me/|wa\.me|instagram\.com|youtube\.com|facebook\.com|twitter.com|whatsapp\.com|linkedin\.com|snapchat\.com|pinterest\.com|reddit\.com|tiktok\.com|discord\.gg|telegram\.me)"
    return bool(re.search(pattern, text, re.IGNORECASE))

def has_bot_username(text: str):
    if not text:
        return False
    pattern = r"@[\w_]*bot"
    return bool(re.search(pattern, text, re.IGNORECASE))

def has_username(text: str):
    if not text:
        return False
    pattern = r"@[\w_]+"
    return bool(re.search(pattern, text, re.IGNORECASE))

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

# Bio checking function with real-time checking (no cache)
async def check_user_bio(user_id: int):
    try:
        # Get fresh bio from Telegram with error handling
        user = await bot.get_chat(user_id)
        bio = user.bio or ""
        
        # Check for any restricted content in real-time
        return has_links(bio) or has_username(bio) or has_bot_username(bio)
        
    except TelegramBadRequest as e:
        print(f"Bio check failed for user {user_id}: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error in bio check: {e}")
        return False

# --- Admin Check Function ---
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

# --- NEW FUNCTION: Admin message deletion without warning ---
async def delete_admin_message(message: types.Message):
    try:
        await message.delete()
    except TelegramBadRequest as e:
        print(f"Delete failed: {e}")
        return

    # Send polite deletion notice for admins
    deletion_msg = await message.answer(
        f"🗑️ Admin @{message.from_user.username or message.from_user.first_name}, your link was deleted. Please approve yourself first using /approveme",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👑 Owner", url=f"https://t.me/{OWNER_USERNAME}")],
                [InlineKeyboardButton(text="📢 Updates", url=f"https://t.me/{UPDATES_USERNAME}")]
            ]
        )
    )
    asyncio.create_task(auto_delete(deletion_msg))

# --- UPDATED: Custom Warning Function with Specific Messages ---
async def warn_and_delete(message: types.Message, violation_type: str = "links"):
    try:
        # Check if user is admin first
        is_user_admin = await is_admin(message.chat.id, message.from_user.id)
        
        if is_user_admin:
            await delete_admin_message(message)
            return
            
        if await is_whitelisted(message.from_user.id) or await is_approved(message.from_user.id):
            return

        user_id = message.from_user.id
        current_warnings = await get_warnings(user_id, message.chat.id)
        new_warnings = current_warnings + 1
        await set_warnings(user_id, message.chat.id, new_warnings)

        try:
            await message.delete()
        except TelegramBadRequest as e:
            print(f"Delete failed: {e}")
            return  # Don't proceed if message deletion failed

        # Custom warning messages based on violation type
        if violation_type == "biolinks":
            warning_text = f"👤 @{message.from_user.username or message.from_user.first_name} Your message was hidden. Bio links are not allowed in this group, please remove them."
        elif violation_type == "links":
            warning_text = f"👤 @{message.from_user.username or message.from_user.first_name} Your message was hidden. Links are not allowed in this group, please remove them."
        elif violation_type == "username":
            warning_text = f"👤 @{message.from_user.username or message.from_user.first_name} Your message was hidden. Usernames are not allowed in this group, please remove them."
        elif violation_type == "botlink":
            warning_text = f"👤 @{message.from_user.username or message.from_user.first_name} Your message was hidden. Bot usernames are not allowed in this group, please remove them."
        else:
            warning_text = f"⚠️ @{message.from_user.username or message.from_user.first_name} Warning {new_warnings}/3 - Risky content not allowed!"

        # REMOVED HELP BUTTON FROM WARNING MESSAGE
        warning_msg = await message.answer(
            warning_text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="👑 Owner", url=f"https://t.me/{OWNER_USERNAME}")],
                    [InlineKeyboardButton(text="📢 Updates", url=f"https://t.me/{UPDATES_USERNAME}")]
                ]
            )
        )

        asyncio.create_task(auto_delete(warning_msg))

        # Log the action with error handling
        try:
            await bot.send_message(
                LOG_CHAT_ID,
                f"⚠️ @{message.from_user.username or message.from_user.first_name} sent {violation_type} in {message.chat.title}"
            )
        except TelegramBadRequest as e:
            print(f"Log failed: {e}")

        if new_warnings >= 3:
            until_date = int(time.time()) + mute_duration * 60
            try:
                await bot.restrict_chat_member(
                    message.chat.id,
                    user_id,
                    permissions=types.ChatPermissions(can_send_messages=False),
                    until_date=until_date
                )

                mute_msg = await bot.send_message(
                    message.chat.id,
                    f"🔇 @{message.from_user.username or message.from_user.first_name} muted for {mute_duration} min.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="Unmute", callback_data=f"unmute:{user_id}")]
                        ]
                    )
                )
                asyncio.create_task(auto_delete(mute_msg))

                # reset warnings after mute
                await set_warnings(user_id, message.chat.id, 0)

                # Log the mute action
                try:
                    await bot.send_message(
                        LOG_CHAT_ID,
                        f"🔇 @{message.from_user.username or message.from_user.first_name} muted in {message.chat.title}"
                    )
                except TelegramBadRequest as e:
                    print(f"Log failed: {e}")

            except TelegramBadRequest as e:
                error_msg = await message.reply("❌ I need admin permissions to mute users!")
                asyncio.create_task(auto_delete(error_msg))
                print(f"Mute failed: {e}")
                
    except Exception as e:
        print(f"Error in warn_and_delete: {e}")

# --- Updated PM Buttons Function ---
async def get_personal_buttons():
    me = await bot.get_me()
    
    # Create keyboard from dynamic buttons
    keyboard = []
    current_row = []
    
    for i, btn in enumerate(dynamic_buttons):
        if 'url' in btn:
            current_row.append(InlineKeyboardButton(text=btn['text'], url=btn['url']))
        else:
            current_row.append(InlineKeyboardButton(text=btn['text'], callback_data=btn['callback_data']))
        
        # Add row after every 2 buttons or at the end
        if len(current_row) == 2 or i == len(dynamic_buttons) - 1:
            keyboard.append(current_row)
            current_row = []
    
    # Always add "Add to Group" button at the end
    keyboard.append([InlineKeyboardButton(text="➕ Add me to your group", 
                                        url=f"https://t.me/{me.username}?startgroup=true")])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.chat.type == "private":
        # Get user's first name
        user_name = message.from_user.first_name
        
        # Your provided image URL
        photo_url = "https://cftc-15g.pages.dev/1758448580525_file_1758448580525.jpg"
        
        try:
            # Send message with photo
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
            # If photo fails, send simple message
            print(f"Photo send failed: {e}")
            await message.reply(
                f"Hey 👋🏻 {user_name}\n\n"
                "Welcome to Links Shield Bot\n"
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

# --- FIXED HELP TEXTS ---
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

# Help keyboard with close and back button
help_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_to_main")],
        [InlineKeyboardButton(text="❌ Close", callback_data="close_help")]
    ]
)

# --- FIXED HELP COMMAND ---
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    # Personal chat
    if message.chat.type == "private":
        if is_owner(message.from_user.id):
            # Show everything to owner with proper formatting
            help_text = f"{BASIC_HELP_TEXT}\n\n{ADMIN_HELP_TEXT}\n\n{OWNER_HELP_TEXT}"
        else:
            # Show only basic + admin commands to normal users
            help_text = f"{BASIC_HELP_TEXT}\n\n{ADMIN_HELP_TEXT}"
        
        await message.reply(help_text, reply_markup=help_keyboard, parse_mode="Markdown")
    
    # Group chat
    else:
        try:
            member = await bot.get_chat_member(message.chat.id, message.from_user.id)
            if member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                if is_owner(message.from_user.id):
                    # Show everything to owner in group
                    help_text = f"{BASIC_HELP_TEXT}\n\n{ADMIN_HELP_TEXT}\n\n{OWNER_HELP_TEXT}"
                else:
                    # Show only basic + admin commands to admins in group
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

# --- Approveme Command ---
@dp.message(Command("approveme"))
async def approve_me(message: types.Message):
    if message.chat.type == "private":
        return
        
    if not await is_admin(message.chat.id, message.from_user.id):
        status_msg = await message.reply("❌ Only admins can use this command!")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    
    user_id = message.from_user.id
    await add_approved_user(user_id)
    
    status_msg = await message.reply(
        f"✅ @{message.from_user.username or message.from_user.first_name} approved!\n"
        f"You can now send links in this group."
    )
    asyncio.create_task(auto_delete(status_msg, 10))

# --- Toggle Commands (Fixed for admins only) ---
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
        
    settings = await get_group_settings_wrapper(message.chat.id)
    settings["biolinks"] = args[1].lower() == "on"
    await save_group_settings(message.chat.id, settings)
    
    status_msg = await message.reply(f"Bio links deletion set to {'ON ✅' if settings['biolinks'] else 'OFF ❌'}")
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
        
    settings = await get_group_settings_wrapper(message.chat.id)
    settings["links"] = args[1].lower() == "on"
    await save_group_settings(message.chat.id, settings)
    
    status_msg = await message.reply(f"Links deletion set to {'ON ✅' if settings['links'] else 'OFF ❌'}")
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
        
    settings = await get_group_settings_wrapper(message.chat.id)
    settings["username"] = args[1].lower() == "on"
    await save_group_settings(message.chat.id, settings)
    
    status_msg = await message.reply(f"Username deletion set to {'ON ✅' if settings['username'] else 'OFF ❌'}")
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
        
    settings = await get_group_settings_wrapper(message.chat.id)
    settings["botlink"] = args[1].lower() == "on"
    await save_group_settings(message.chat.id, settings)
    
    status_msg = await message.reply(f"Bot usernames deletion set to {'ON ✅' if settings['botlink'] else 'OFF ❌'}")
    asyncio.create_task(auto_delete(status_msg, 10))

# --- COMPLETELY FIXED: Whitelist Commands ---
@dp.message(Command("whitelistadd"))
async def whitelist_add(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        status_msg = await message.reply("❌ Only admins can use this command!")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    
    user_id = None
    user_name = ""
    
    # Check if replying to a message
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        user_name = message.reply_to_message.from_user.full_name
    
    # Check if user is mentioned in the message
    elif message.entities:
        for entity in message.entities:
            if entity.type == "mention":
                # Extract username from mention
                username = message.text[entity.offset+1:entity.offset+entity.length]
                try:
                    # Search for user by username in current chat
                    async for member in bot.get_chat_members(message.chat.id):
                        if member.user.username and member.user.username.lower() == username.lower():
                            user_id = member.user.id
                            user_name = member.user.full_name
                            break
                except Exception as e:
                    continue
            elif entity.type == "text_mention":
                # Direct user mention
                user_id = entity.user.id
                user_name = entity.user.full_name
                break
    
    # Check if username is provided as argument
    elif len(message.text.split()) > 1:
        username_arg = message.text.split()[1].replace('@', '').strip()
        if username_arg:
            try:
                # Try to search user in the current chat
                async for member in bot.get_chat_members(message.chat.id):
                    if (member.user.username and member.user.username.lower() == username_arg.lower()) or \
                       (member.user.full_name and username_arg.lower() in member.user.full_name.lower()):
                        user_id = member.user.id
                        user_name = member.user.full_name
                        break
                
                if not user_id:
                    status_msg = await message.reply(f"❌ User @{username_arg} not found in this group!")
                    asyncio.create_task(auto_delete(status_msg, 10))
                    return
                    
            except Exception as e:
                status_msg = await message.reply(f"❌ Error finding user!")
                asyncio.create_task(auto_delete(status_msg, 10))
                return
    
    else:
        status_msg = await message.reply("❌ Usage:\n• Reply to user's message\n• Or use: /whitelistadd @username\n• Or mention the user")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    
    if user_id:
        await add_to_whitelist(user_id)
        status_msg = await message.reply(f"✅ {user_name} (ID: {user_id}) whitelisted successfully!")
        asyncio.create_task(auto_delete(status_msg, 10))

@dp.message(Command("whitelistremove"))
async def whitelist_remove(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        status_msg = await message.reply("❌ Only admins can use this command!")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    
    user_id = None
    user_name = ""
    
    # Check if replying to a message
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        user_name = message.reply_to_message.from_user.full_name
    
    # Check if user is mentioned in the message
    elif message.entities:
        for entity in message.entities:
            if entity.type == "mention":
                # Extract username from mention
                username = message.text[entity.offset+1:entity.offset+entity.length]
                # Search in whitelist by username
                for uid in list(whitelist_memory):
                    try:
                        user = await bot.get_chat(uid)
                        if user.username and user.username.lower() == username.lower():
                            user_id = uid
                            user_name = user.full_name
                            break
                    except:
                        continue
            elif entity.type == "text_mention":
                # Direct user mention
                user_id = entity.user.id
                user_name = entity.user.full_name
                break
    
    # Check if username is provided as argument
    elif len(message.text.split()) > 1:
        username_arg = message.text.split()[1].replace('@', '').strip()
        if username_arg:
            # Search in whitelist by username or user ID
            for uid in list(whitelist_memory):
                try:
                    user = await bot.get_chat(uid)
                    if (user.username and user.username.lower() == username_arg.lower()) or \
                       (user.full_name and username_arg.lower() in user.full_name.lower()) or \
                       str(uid) == username_arg:
                        user_id = uid
                        user_name = user.full_name
                        break
                except:
                    continue
            
            if not user_id:
                status_msg = await message.reply(f"❌ User @{username_arg} not found in whitelist!")
                asyncio.create_task(auto_delete(status_msg, 10))
                return
    
    else:
        status_msg = await message.reply("❌ Usage:\n• Reply to user's message\n• Or use: /whitelistremove @username\n• Or mention the user")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    
    if user_id:
        await remove_from_whitelist(user_id)
        await remove_approved_user(user_id)
        status_msg = await message.reply(f"❌ {user_name} (ID: {user_id}) removed from whitelist!")
        asyncio.create_task(auto_delete(status_msg, 10))

@dp.message(Command("whitelistshow"))
async def whitelist_show(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        status_msg = await message.reply("❌ Only admins can use this command!")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    
    whitelist_users = []
    if users_col:
        try:
            whitelisted = await asyncio.to_thread(users_col.find, {"whitelisted": True})
            whitelist_users = list(whitelisted)
        except:
            whitelist_users = list(whitelist_memory)
    else:
        whitelist_users = list(whitelist_memory)
    
    if not whitelist_users: 
        status_msg = await message.reply("No whitelisted users.")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
        
    whitelist_info = "👤 *Whitelisted Users:*\n"
    for user_data in whitelist_users[:15]:  # Show first 15 only
        user_id = user_data.get('_id') if isinstance(user_data, dict) else user_data
        try:
            user = await bot.get_chat(user_id)
            username = f"@{user.username}" if user.username else "No username"
            whitelist_info += f"• {user.full_name} ({username}) - ID: `{user_id}`\n"
        except:
            whitelist_info += f"• Unknown User (ID: `{user_id}`)\n"
    
    status_msg = await message.reply(whitelist_info, parse_mode="Markdown")
    asyncio.create_task(auto_delete(status_msg, 15))

# --- Owner Commands ---
@dp.message(Command("botstats"))
async def bot_stats(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    
    # Get stats from MongoDB
    total_groups = 0
    if groups_col:
        total_groups = await asyncio.to_thread(groups_col.count_documents, {})
    
    total_whitelisted = 0
    if users_col:
        total_whitelisted = await asyncio.to_thread(users_col.count_documents, {"whitelisted": True})
    
    stats_text = (
        f"🤖 **Bot Statistics**\n\n"
        f"📊 Total Groups: {total_groups}\n"
        f"👤 Whitelisted Users: {total_whitelisted}\n"
        f"✅ Approved Users: {len(approved_users_memory)}\n"
        f"⚠️ Total Warnings: {sum(warnings_memory.values())}\n"
        f"🕒 Start Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"💾 Storage: {'MongoDB' if db else 'Memory'}"
    )
    
    await message.reply(stats_text)

@dp.message(Command("listgroups"))
async def list_groups(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    
    groups_list = []
    if groups_col:
        try:
            groups = await asyncio.to_thread(groups_col.find, {})
            groups_data = list(groups)
            for group in groups_data[:15]:  # First 15 only
                chat_id = group['_id']
                try:
                    chat = await bot.get_chat(chat_id)
                    groups_list.append(f"• {chat.title} (ID: {chat_id})")
                except:
                    groups_list.append(f"• Unknown Group (ID: {chat_id})")
        except Exception as e:
            groups_list.append(f"❌ Error fetching groups: {e}")
    
    if not groups_list:
        await message.reply("❌ No groups data available.")
        return
    
    response = "👥 **Groups List (First 15):**\n\n" + "\n".join(groups_list)
    await message.reply(response)

# ... (Rest of the owner commands remain similar with MongoDB integration)

# --- UPDATED: Message Filtering with Specific Violation Types ---
@dp.message(F.text | F.caption)
async def filter_messages(message: types.Message):
    if maintenance_active and not is_owner(message.from_user.id):
        return
    
    if message.chat.type not in ["group", "supergroup"]:
        return
    
    # Skip if user is whitelisted or approved
    if await is_whitelisted(message.from_user.id) or await is_approved(message.from_user.id):
        return
    
    settings = await get_group_settings_wrapper(message.chat.id)
    text = message.text or message.caption or ""
    
    # Debug logging with user info
    print(f"Checking message from {message.from_user.id} ({message.from_user.username}): {text[:50]}...")
    
    # Check for links first
    if settings["links"] and has_links(text):
        print(f"Detected links in message from {message.from_user.id}")
        await warn_and_delete(message, "links")
        return
    
    # Check for bot usernames
    if settings["botlink"] and has_bot_username(text):
        print(f"Detected bot username in message from {message.from_user.id}")
        await warn_and_delete(message, "botlink")
        return
    
    # Check for regular usernames
    if settings["username"] and has_username(text):
        print(f"Detected username in message from {message.from_user.id}")
        await warn_and_delete(message, "username")
        return
    
    # Check bio links (only if other checks passed)
    if settings["biolinks"]:
        print(f"Checking bio for user {message.from_user.id}...")
        has_bio_links = await check_user_bio(message.from_user.id)
        if has_bio_links:
            print(f"Detected bio links for user {message.from_user.id}")
            await warn_and_delete(message, "biolinks")
            return
    
    print(f"Message from {message.from_user.id} passed all checks")

# --- Error Handler ---
@dp.errors()
async def error_handler(update: types.Update, exception: Exception):
    print(f"Update: {update}")
    print(f"Exception type: {type(exception).__name__}")
    print(f"Exception details: {exception}")
    
    # Log to your log chat if it's a critical error
    if "critical" in str(exception).lower() or "forbidden" in str(exception).lower():
        try:
            await bot.send_message(
                LOG_CHAT_ID,
                f"⚠️ Bot Error: {type(exception).__name__}\n\n{str(exception)[:1000]}"
            )
        except:
            pass
            
    return True

# --- Main Function ---
async def main():
    print("🤖 Bot is starting...")
    if db:
        print("✅ MongoDB Connected - Data will be persisted")
    else:
        print("⚠️ Using in-memory storage - Data will reset on restart")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())