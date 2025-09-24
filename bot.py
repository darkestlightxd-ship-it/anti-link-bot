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

# Load environment variables
load_dotenv()

# ===== CONFIG =====
API_TOKEN = os.getenv("BOT_TOKEN", "8470214636:AAExm5uh4tu621S5zvHDMDfWQzxruvgvuwY")
BOT_OWNER_ID = int(os.getenv("OWNER_ID", "6156257558"))  # YOUR OWNER ID
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "Insaanova")
UPDATES_USERNAME = os.getenv("UPDATES_USERNAME", "FRIENDS_CORNER_CHATTING_GROUP")
LOG_CHAT_ID = int(os.getenv("LOG_CHAT_ID", "-1003086724642"))
BROADCAST_SOURCE_CHANNEL = int(os.getenv("BROADCAST_SOURCE_CHANNEL", "-1002933746046"))  # APNA SOURCE CHANNEL ID

# Dynamic buttons storage (in-memory)
dynamic_buttons = [
    {"text": "👑 Owner", "url": f"https://t.me/{OWNER_USERNAME}"},
    {"text": "📢 Updates", "url": f"https://t.me/{UPDATES_USERNAME}"},
    {"text": "❓ Help & Commands", "callback_data": "help"}
]

warnings = {}
whitelist = set()
approved_users = set()  # For approveme command
group_settings = {}  # chat_id: settings
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
def get_group_settings(chat_id: int):
    if chat_id not in group_settings:
        group_settings[chat_id] = {"links": True, "biolinks": True, "username": True, "botlink": True}
    return group_settings[chat_id]

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
            
        if message.from_user.id in whitelist or message.from_user.id in approved_users:
            return

        user_id = message.from_user.id
        warnings[user_id] = warnings.get(user_id, 0) + 1

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
            warning_text = f"⚠️ @{message.from_user.username or message.from_user.first_name} Warning {warnings[user_id]}/3 - Risky content not allowed!"

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

        if warnings[user_id] >= 3:
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
                warnings[user_id] = 0

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
    approved_users.add(user_id)
    
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
        
    settings = get_group_settings(message.chat.id)
    settings["biolinks"] = args[1].lower() == "on"
    
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
        
    settings = get_group_settings(message.chat.id)
    settings["links"] = args[1].lower() == "on"
    
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
        
    settings = get_group_settings(message.chat.id)
    settings["username"] = args[1].lower() == "on"
    
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
        
    settings = get_group_settings(message.chat.id)
    settings["botlink"] = args[1].lower() == "on"
    
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
        whitelist.add(user_id)
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
                for uid in list(whitelist):
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
            for uid in list(whitelist):
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
        whitelist.discard(user_id)
        approved_users.discard(user_id)  # Also remove from approved
        status_msg = await message.reply(f"❌ {user_name} (ID: {user_id}) removed from whitelist!")
        asyncio.create_task(auto_delete(status_msg, 10))

@dp.message(Command("whitelistshow"))
async def whitelist_show(message: types.Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        status_msg = await message.reply("❌ Only admins can use this command!")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
    
    if not whitelist: 
        status_msg = await message.reply("No whitelisted users.")
        asyncio.create_task(auto_delete(status_msg, 10))
        return
        
    whitelist_info = "👤 *Whitelisted Users:*\n"
    for user_id in list(whitelist):
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
    
    stats_text = (
        f"🤖 **Bot Statistics**\n\n"
        f"📊 Total Groups: {len(group_settings)}\n"
        f"👤 Whitelisted Users: {len(whitelist)}\n"
        f"✅ Approved Users: {len(approved_users)}\n"
        f"⚠️ Total Warnings: {sum(warnings.values())}\n"
        f"🕒 Start Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    
    await message.reply(stats_text)

@dp.message(Command("listgroups"))
async def list_groups(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    
    if not group_settings:
        await message.reply("❌ No groups data available.")
        return
    
    groups_list = []
    for chat_id, settings in list(group_settings.items())[:15]:
        try:
            chat = await bot.get_chat(chat_id)
            group_name = chat.title
            groups_list.append(f"• {group_name} (ID: {chat_id})")
        except:
            groups_list.append(f"• Unknown Group (ID: {chat_id})")
    
    response = "👥 **Groups List (First 15):**\n\n" + "\n".join(groups_list)
    await message.reply(response)

@dp.message(Command("whitelist_info"))
async def whitelist_info(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    
    if not whitelist:
        await message.reply("❌ No whitelisted users.")
        return
    
    whitelist_info = []
    for user_id in list(whitelist)[:20]:
        try:
            user = await bot.get_chat(user_id)
            username = f"@{user.username}" if user.username else "No username"
            user_info = f"• {user.full_name} ({username}) - ID: {user_id}"
            whitelist_info.append(user_info)
        except:
            whitelist_info.append(f"• Unknown User (ID: {user_id}")
    response = "👤 **Whitelisted Users (First 20):**\n\n" + "\n".join(whitelist_info)
    await message.reply(response)

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
        
        settings = group_settings.get(chat_id, {})
        
        info_text = (
            f"👥 **Group Info:**\n\n"
            f"• **Name:** {chat.title}\n"
            f"• **ID:** {chat.id}\n"
            f"• **Type:** {chat.type}\n"
            f"• **Members:** {await chat.get_member_count() if hasattr(chat, 'get_member_count') else 'Unknown'}\n\n"
            f"⚙️ **Settings:**\n"
            f"• Links: {'✅ ON' if settings.get('links', True) else '❌ OFF'}\n"
            f"• Bio Links: {'✅ ON' if settings.get('biolinks', True) else '❌ OFF'}\n"
            f"• Usernames: {'✅ ON' if settings.get('username', True) else '❌ OFF'}\n"
            f"• Bot Links: {'✅ ON' if settings.get('botlink', True) else '❌ OFF'}\n"
        )
        
        await message.reply(info_text)
        
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")

@dp.message(Command("broadcast"))
async def broadcast_message(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    
    if not message.reply_to_message:
        await message.reply("❌ Reply to a message to broadcast it.")
        return
    
    global pending_broadcast
    pending_broadcast = message.reply_to_message
    
    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Confirm", callback_data="broadcast_confirm"),
                InlineKeyboardButton(text="❌ Cancel", callback_data="broadcast_cancel")
            ]
        ]
    )
    
    await message.reply(
        "⚠️ **Broadcast Confirmation**\n\n"
        f"This will send the message to all {len(group_settings)} groups. Continue?",
        reply_markup=confirm_keyboard
    )

@dp.callback_query(F.data == "broadcast_confirm")
async def confirm_broadcast(callback: types.CallbackQuery):
    if not is_owner(callback.from_user.id):
        return
    
    global pending_broadcast
    if not pending_broadcast:
        await callback.answer("No pending broadcast.")
        return
    
    await callback.message.edit_text("📤 Broadcasting started...")
    
    success = 0
    failed = 0
    
    for chat_id in group_settings.keys():
        try:
            await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=pending_broadcast.chat.id,
                message_id=pending_broadcast.message_id
            )
            success += 1
            await asyncio.sleep(0.5)  # Rate limiting
        except Exception as e:
            print(f"Broadcast failed to {chat_id}: {e}")
            failed += 1
    
    await callback.message.edit_text(
        f"📊 **Broadcast Complete**\n\n"
        f"✅ Success: {success}\n"
        f"❌ Failed: {failed}\n"
        f"📋 Total: {success + failed}"
    )
    
    pending_broadcast = None

@dp.callback_query(F.data == "broadcast_cancel")
async def cancel_broadcast(callback: types.CallbackQuery):
    if not is_owner(callback.from_user.id):
        return
    
    global pending_broadcast
    pending_broadcast = None
    await callback.message.edit_text("❌ Broadcast cancelled.")

@dp.message(Command("restart"))
async def restart_bot(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    
    await message.reply("🔄 Restarting bot...")
    os.execl(sys.executable, sys.executable, *sys.argv)

@dp.message(Command("maintenance"))
async def maintenance_mode(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    
    args = message.text.split()
    if len(args) != 2 or args[1].lower() not in ["on", "off"]:
        await message.reply("Usage: /maintenance on|off")
        return
    
    global maintenance_active
    maintenance_active = args[1].lower() == "on"
    
    status = "🟢 ACTIVATED" if maintenance_active else "🔴 DEACTIVATED"
    await message.reply(f"🔧 Maintenance mode: {status}")

# --- Button Management Commands ---
@dp.message(Command("setbuttons"))
async def set_buttons(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    
    try:
        # Parse button configuration from message
        # Format: "text1 - url1 | text2 - url2 | text3 - callback_data3"
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
            global dynamic_buttons
            dynamic_buttons = new_buttons
            await message.reply("✅ Buttons updated successfully!")
        else:
            await message.reply("❌ No valid buttons found.")
            
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")

@dp.message(Command("previewbuttons"))
async def preview_buttons(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    
    buttons_info = "\n".join([
        f"• {btn['text']} -> {btn.get('url', btn.get('callback_data', 'No data'))}"
        for btn in dynamic_buttons
    ])
    
    await message.reply(
        f"🔘 **Current Buttons:**\n\n{buttons_info}\n\n"
        f"Total: {len(dynamic_buttons)} buttons",
        reply_markup=await get_personal_buttons()
    )

@dp.message(Command("resetbuttons"))
async def reset_buttons(message: types.Message):
    if not is_owner(message.from_user.id):
        return
    
    global dynamic_buttons
    dynamic_buttons = [
        {"text": "👑 Owner", "url": f"https://t.me/{OWNER_USERNAME}"},
        {"text": "📢 Updates", "url": f"https://t.me/{UPDATES_USERNAME}"},
        {"text": "❓ Help & Commands", "callback_data": "help"}
    ]
    
    await message.reply("✅ Buttons reset to default!")

# --- FIXED HELP CALLBACK WITH PHOTO SUPPORT ---
@dp.callback_query(F.data == "help")
async def help_callback(callback: types.CallbackQuery):
    try:
        if is_owner(callback.from_user.id):
            help_text = f"{BASIC_HELP_TEXT}\n\n{ADMIN_HELP_TEXT}\n\n{OWNER_HELP_TEXT}"
        else:
            help_text = f"{BASIC_HELP_TEXT}\n\n{ADMIN_HELP_TEXT}"
        
        # Always delete current message and send new text message
        try:
            await callback.message.delete()
        except:
            pass
        
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=help_text,
            reply_markup=help_keyboard,
            parse_mode="Markdown"
        )
        
        await callback.answer("Help menu opened!")
        
    except Exception as e:
        print(f"Error in help callback: {e}")
        await callback.answer("❌ Error!")

# --- FIXED BACK TO MAIN WITH PHOTO SUPPORT ---
@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery):
    try:
        user_name = callback.from_user.first_name
        
        # Delete current message
        try:
            await callback.message.delete()
        except:
            pass
        
        # Send photo message with main menu (same as /start)
        photo_url = "https://cftc-15g.pages.dev/1758448580525_file_1758448580525.jpg"
        
        try:
            await bot.send_photo(
                chat_id=callback.from_user.id,
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
            # If photo fails, send text message
            await bot.send_message(
                chat_id=callback.from_user.id,
                text=f"Hey 👋🏻 {user_name}\n\n"
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
        
        await callback.answer("Back to main menu!")
            
    except Exception as e:
        print(f"Error in back_to_main: {e}")
        await callback.answer("❌ Error!")

# --- FIXED CLOSE HELP ---  
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
        await bot.restrict_chat_member(
            callback.message.chat.id,
            user_id,
            permissions=types.ChatPermissions(can_send_messages=True)
        )
        
        await callback.message.edit_text(f"✅ User unmuted successfully!")
        await asyncio.sleep(3)
        await callback.message.delete()
        
    except Exception as e:
        await callback.answer(f"❌ Unmute failed: {e}")

# --- UPDATED: Message Filtering with Specific Violation Types ---
@dp.message(F.text | F.caption)
async def filter_messages(message: types.Message):
    if maintenance_active and not is_owner(message.from_user.id):
        return
    
    if message.chat.type not in ["group", "supergroup"]:
        return
    
    # Skip if user is whitelisted or approved
    if message.from_user.id in whitelist or message.from_user.id in approved_users:
        return
    
    settings = get_group_settings(message.chat.id)
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
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
