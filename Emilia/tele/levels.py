import datetime as dt
import time
import asyncio
import aiohttp
import aiofiles
import os
from io import BytesIO
from typing import Optional, Dict, List, Tuple
import math

from PIL import Image, ImageDraw, ImageFont, ImageFilter
from telethon import Button, events
from telethon.tl.types import InputMediaUploadedPhoto

import Emilia.strings as strings
from Emilia import LOGGER, db, telethn
from Emilia.custom_filter import callbackquery, register
from Emilia.functions.admins import get_time, is_admin
from Emilia.utils.decorators import *
from Emilia.utils.cache import SimpleCache

# Database collections
users_collection = db.chatlevels
first_name = db.first_name
level = db.onofflevel
user_profiles = db.user_profiles

# Enhanced rank system with more detailed progression
ranks = [
    {"name": "Newbie", "min_points": 0, "color": "#808080", "emoji": "🥉"},
    {"name": "Rookie", "min_points": 100, "color": "#CD853F", "emoji": "🏃"},
    {"name": "Explorer", "min_points": 300, "color": "#32CD32", "emoji": "🗺️"},
    {"name": "Adventurer", "min_points": 600, "color": "#1E90FF", "emoji": "⚔️"},
    {"name": "Warrior", "min_points": 1000, "color": "#FF6347", "emoji": "🛡️"},
    {"name": "Champion", "min_points": 2500, "color": "#FF4500", "emoji": "🏆"},
    {"name": "Legend", "min_points": 5000, "color": "#8A2BE2", "emoji": "⭐"},
    {"name": "Mythic", "min_points": 10000, "color": "#FFD700", "emoji": "👑"},
    {"name": "Divine", "min_points": 25000, "color": "#FF1493", "emoji": "💎"},
    {"name": "Immortal", "min_points": 50000, "color": "#00FFFF", "emoji": "🌟"},
    {"name": "Transcendent", "min_points": 100000, "color": "#9932CC", "emoji": "🔮"},
]

# XP multipliers for different activities
XP_MULTIPLIERS = {
    "text_message": 1,
    "photo": 2,
    "video": 3,
    "document": 2,
    "sticker": 1,
    "voice": 4,
    "video_note": 3,
    "poll": 5,
    "game": 10,
}

# Streak bonuses
STREAK_BONUSES = {
    3: 10,   # 3 days = +10 XP bonus
    7: 25,   # 1 week = +25 XP bonus
    14: 50,  # 2 weeks = +50 XP bonus
    30: 100, # 1 month = +100 XP bonus
}

levels = 10000  # Max level increased

# Enhanced caching system
_level_cache = SimpleCache(default_ttl=300)
_name_cache = SimpleCache(default_ttl=600)
_profile_cache = SimpleCache(default_ttl=180)
_points_buffer = {}
_lastmsg_buffer = {}
_activity_buffer = {}
_streak_buffer = {}
_buffer_flush_inflight = False
_last_flush = 0.0
_MIN_FLUSH_INTERVAL = 2.0
_MAX_BUFFER_OPS = 500
_periodic_flush_started = False

# Banner generation settings
BANNER_WIDTH = 1200
BANNER_HEIGHT = 400
FONT_PATHS = {
    "title": "assets/fonts/Roboto-Bold.ttf",
    "subtitle": "assets/fonts/Roboto-Medium.ttf",
    "text": "assets/fonts/Roboto-Regular.ttf",
}

# Create fonts directory if not exists
os.makedirs("assets/fonts", exist_ok=True)
os.makedirs("assets/backgrounds", exist_ok=True)
os.makedirs("temp/banners", exist_ok=True)

async def download_default_fonts():
    """Download default fonts if not present"""
    fonts = {
        "Roboto-Bold.ttf": "https://github.com/google/fonts/raw/main/apache/roboto/Roboto-Bold.ttf",
        "Roboto-Medium.ttf": "https://github.com/google/fonts/raw/main/apache/roboto/Roboto-Medium.ttf",
        "Roboto-Regular.ttf": "https://github.com/google/fonts/raw/main/apache/roboto/Roboto-Regular.ttf",
    }
    
    async with aiohttp.ClientSession() as session:
        for font_name, url in fonts.items():
            font_path = f"assets/fonts/{font_name}"
            if not os.path.exists(font_path):
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            async with aiofiles.open(font_path, 'wb') as f:
                                await f.write(await resp.read())
                except Exception as e:
                    LOGGER.error(f"Failed to download font {font_name}: {e}")

def get_font(font_type: str, size: int) -> ImageFont.FreeTypeFont:
    """Get font with fallback to default"""
    try:
        return ImageFont.truetype(FONT_PATHS.get(font_type, FONT_PATHS["text"]), size)
    except Exception:
        try:
            return ImageFont.truetype("arial.ttf", size)
        except Exception:
            return ImageFont.load_default()

async def get_user_avatar(user_id: int) -> Optional[Image.Image]:
    """Download and process user avatar"""
    try:
        # Get user profile photo
        photos = await telethn.get_profile_photos(user_id, limit=1)
        if not photos:
            return None
            
        # Download photo
        photo_bytes = await telethn.download_media(photos[0], file=BytesIO())
        avatar = Image.open(photo_bytes)
        
        # Resize and make circular
        avatar = avatar.resize((150, 150), Image.Resampling.LANCZOS)
        mask = Image.new('L', (150, 150), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, 150, 150), fill=255)
        
        # Apply circular mask
        output = Image.new('RGBA', (150, 150), (0, 0, 0, 0))
        output.paste(avatar, (0, 0))
        output.putalpha(mask)
        
        return output
    except Exception as e:
        LOGGER.error(f"Failed to get avatar for user {user_id}: {e}")
        return None

def create_gradient_background(width: int, height: int, color1: str, color2: str) -> Image.Image:
    """Create a gradient background"""
    base = Image.new('RGB', (width, height), color1)
    overlay = Image.new('RGB', (width, height), color2)
    
    # Create gradient mask
    mask = Image.new('L', (width, height))
    mask_draw = ImageDraw.Draw(mask)
    
    for y in range(height):
        alpha = int(255 * (y / height))
        mask_draw.rectangle([(0, y), (width, y + 1)], fill=alpha)
    
    # Apply gradient
    base.paste(overlay, mask=mask)
    return base

def draw_progress_bar(draw: ImageDraw.Draw, x: int, y: int, width: int, height: int, 
                     progress: float, bg_color: str = "#333333", fill_color: str = "#00FF00"):
    """Draw a progress bar"""
    # Background
    draw.rounded_rectangle([x, y, x + width, y + height], radius=height//2, fill=bg_color)
    
    # Progress fill
    if progress > 0:
        fill_width = int(width * min(progress, 1.0))
        if fill_width > 0:
            draw.rounded_rectangle([x, y, x + fill_width, y + height], 
                                 radius=height//2, fill=fill_color)

async def generate_rank_banner(user_id: int, chat_id: int, stats: Dict) -> BytesIO:
    """Generate a beautiful rank banner for the user"""
    
    # Get rank info
    rank_info = await get_rank_info(stats['points'])
    next_rank = await get_next_rank_info(stats['points'])
    
    # Create base image
    img = Image.new('RGBA', (BANNER_WIDTH, BANNER_HEIGHT), (0, 0, 0, 0))
    
    # Create gradient background
    bg = create_gradient_background(BANNER_WIDTH, BANNER_HEIGHT, 
                                  rank_info['color'], "#1a1a1a")
    
    # Add subtle pattern overlay
    overlay = Image.new('RGBA', (BANNER_WIDTH, BANNER_HEIGHT), (255, 255, 255, 10))
    pattern_draw = ImageDraw.Draw(overlay)
    for i in range(0, BANNER_WIDTH + 100, 100):
        pattern_draw.line([(i, 0), (i - 100, BANNER_HEIGHT)], fill=(255, 255, 255, 5), width=2)
    
    bg = Image.alpha_composite(bg.convert('RGBA'), overlay)
    
    # Add blur effect to background
    bg = bg.filter(ImageFilter.GaussianBlur(radius=1))
    
    draw = ImageDraw.Draw(bg)
    
    # Get user avatar
    avatar = await get_user_avatar(user_id)
    if avatar:
        # Add glow effect to avatar
        glow = Image.new('RGBA', (170, 170), (255, 255, 255, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.ellipse((0, 0, 170, 170), fill=(255, 255, 255, 30))
        glow = glow.filter(ImageFilter.GaussianBlur(radius=10))
        bg.paste(glow, (40, 125), glow)
        
        # Paste avatar
        bg.paste(avatar, (50, 125), avatar)
    
    # Fonts
    title_font = get_font("title", 48)
    subtitle_font = get_font("subtitle", 32)
    text_font = get_font("text", 24)
    small_font = get_font("text", 20)
    
    # User name and title
    name = stats['first_name'][:20]  # Limit name length
    draw.text((250, 50), name, font=title_font, fill="white")
    
    # Rank with emoji
    rank_text = f"{rank_info['emoji']} {rank_info['name']}"
    draw.text((250, 110), rank_text, font=subtitle_font, fill=rank_info['color'])
    
    # Level and XP
    level_text = f"Level {stats['level']}"
    draw.text((250, 160), level_text, font=text_font, fill="white")
    
    # Current points
    points_text = f"{stats['points']:,} XP"
    draw.text((250, 190), points_text, font=text_font, fill="#FFD700")
    
    # Progress bar for next rank
    if next_rank:
        current_progress = stats['points'] - rank_info['min_points']
        needed_progress = next_rank['min_points'] - rank_info['min_points']
        progress = current_progress / needed_progress if needed_progress > 0 else 1.0
        
        # Progress bar
        bar_y = 240
        draw_progress_bar(draw, 250, bar_y, 400, 25, progress, 
                         bg_color="#333333", fill_color=rank_info['color'])
        
        # Progress text
        progress_text = f"{current_progress:,} / {needed_progress:,} XP to {next_rank['name']}"
        draw.text((250, bar_y + 35), progress_text, font=small_font, fill="#CCCCCC")
    
    # Additional stats in right section
    stats_x = 700
    
    # Chat rank
    chat_rank = await get_user_chat_rank(user_id, chat_id)
    draw.text((stats_x, 120), f"Chat Rank: #{chat_rank}", font=text_font, fill="#00FFFF")
    
    # Activity streak
    streak = await get_user_streak(user_id, chat_id)
    draw.text((stats_x, 150), f"Streak: {streak} days 🔥", font=text_font, fill="#FF4500")
    
    # Total messages
    total_msgs = await get_user_total_messages(user_id, chat_id)
    draw.text((stats_x, 180), f"Messages: {total_msgs:,}", font=text_font, fill="#90EE90")
    
    # Join date
    join_date = await get_user_join_date(user_id, chat_id)
    if join_date:
        days_since = (dt.datetime.now() - join_date).days
        draw.text((stats_x, 210), f"Member for: {days_since} days", font=text_font, fill="#DDA0DD")
    
    # Add decorative elements
    # Corner decorations
    draw.ellipse([BANNER_WIDTH-80, 20, BANNER_WIDTH-20, 80], fill=(255, 255, 255, 20))
    draw.ellipse([20, BANNER_HEIGHT-80, 80, BANNER_HEIGHT-20], fill=(255, 255, 255, 20))
    
    # Convert to bytes
    buffer = BytesIO()
    bg.save(buffer, format='PNG', optimize=True, quality=95)
    buffer.seek(0)
    return buffer

async def generate_leaderboard_banner(chat_id: int, leaderboard_data: List) -> BytesIO:
    """Generate leaderboard banner"""
    img_height = min(800, 200 + len(leaderboard_data) * 60)
    img = Image.new('RGB', (BANNER_WIDTH, img_height), '#1a1a2e')
    draw = ImageDraw.Draw(img)
    
    # Title
    title_font = get_font("title", 60)
    subtitle_font = get_font("subtitle", 32)
    text_font = get_font("text", 28)
    
    # Header gradient
    header = create_gradient_background(BANNER_WIDTH, 120, '#16213e', '#0f4c75')
    img.paste(header, (0, 0))
    
    draw.text((60, 30), "🏆 LEADERBOARD 🏆", font=title_font, fill="#FFD700")
    
    # Leaderboard entries
    y_pos = 150
    for idx, user_data in enumerate(leaderboard_data[:10]):
        # Position indicators
        if idx == 0:
            medal = "🥇"
            color = "#FFD700"
        elif idx == 1:
            medal = "🥈"
            color = "#C0C0C0"
        elif idx == 2:
            medal = "🥉"
            color = "#CD7F32"
        else:
            medal = f"{idx + 1}."
            color = "#FFFFFF"
        
        # User info
        name = user_data.get('first_name', 'Unknown')[:25]
        points = user_data.get('points', 0)
        
        # Background for each entry
        entry_bg = Image.new('RGBA', (BANNER_WIDTH - 40, 50), (255, 255, 255, 10))
        img.paste(entry_bg, (20, y_pos - 5), entry_bg)
        
        # Draw entry
        draw.text((40, y_pos), f"{medal}", font=text_font, fill=color)
        draw.text((120, y_pos), name, font=text_font, fill=color)
        draw.text((BANNER_WIDTH - 200, y_pos), f"{points:,} XP", font=text_font, fill=color)
        
        y_pos += 60
    
    buffer = BytesIO()
    img.save(buffer, format='PNG', optimize=True, quality=95)
    buffer.seek(0)
    return buffer

async def _get_level_on(chat_id: int) -> bool:
    """Check if level system is enabled for chat"""
    key = f"lvl:{chat_id}"
    val = _level_cache.get(key)
    if val is not None:
        return val
    exists = await level.find_one({"chat_id": chat_id}) is not None
    _level_cache.set(key, exists, ttl=300)
    return exists

async def _get_first_name(user_id: int) -> str:
    """Get user's registered first name with caching"""
    key = f"name:{user_id}"
    val = _name_cache.get(key)
    if val is not None:
        return val
    doc = await first_name.find_one({"user_id": user_id})
    name = (doc or {}).get("first_name", "Unknown")
    _name_cache.set(key, name, ttl=600)
    return name

async def get_rank_info(points: int) -> Dict:
    """Get current rank information"""
    for rank in ranks[::-1]:
        if points >= rank["min_points"]:
            return rank
    return ranks[0]

async def get_next_rank_info(points: int) -> Optional[Dict]:
    """Get next rank information"""
    for rank in ranks:
        if points < rank["min_points"]:
            return rank
    return None

async def get_user_chat_rank(user_id: int, chat_id: int) -> int:
    """Get user's rank position in chat"""
    try:
        pipeline = [
            {"$match": {"chat_id": chat_id}},
            {"$sort": {"points": -1}},
            {"$group": {"_id": None, "users": {"$push": "$$ROOT"}}},
            {"$unwind": {"path": "$users", "includeArrayIndex": "rank"}},
            {"$match": {"users.user_id": user_id}},
            {"$project": {"rank": {"$add": ["$rank", 1]}}}
        ]
        result = await users_collection.aggregate(pipeline).to_list(1)
        return result[0]["rank"] if result else 999
    except Exception:
        return 999

async def get_user_streak(user_id: int, chat_id: int) -> int:
    """Calculate user's activity streak"""
    try:
        user_data = await users_collection.find_one(
            {"user_id": user_id, "chat_id": chat_id},
            {"streak_count": 1, "last_activity_date": 1}
        )
        if not user_data:
            return 0
        return user_data.get("streak_count", 0)
    except Exception:
        return 0

async def get_user_total_messages(user_id: int, chat_id: int) -> int:
    """Get total message count for user"""
    try:
        user_data = await users_collection.find_one(
            {"user_id": user_id, "chat_id": chat_id},
            {"total_messages": 1}
        )
        if not user_data:
            return 0
        return user_data.get("total_messages", 0)
    except Exception:
        return 0

async def get_user_join_date(user_id: int, chat_id: int) -> Optional[dt.datetime]:
    """Get when user first joined/registered"""
    try:
        user_data = await users_collection.find_one(
            {"user_id": user_id, "chat_id": chat_id},
            {"join_date": 1}
        )
        if user_data and "join_date" in user_data:
            return dt.datetime.fromtimestamp(user_data["join_date"])
        return None
    except Exception:
        return None

async def _flush_enhanced_buffers():
    """Enhanced buffer flushing with activity tracking"""
    global _points_buffer, _lastmsg_buffer, _activity_buffer, _streak_buffer, _buffer_flush_inflight, _last_flush
    
    if _buffer_flush_inflight:
        return
        
    _buffer_flush_inflight = True
    try:
        # Merge all updates
        merged = {}
        current_time = time.time()
        today = dt.date.today()
        
        for (user_id, chat_id), inc in list(_points_buffer.items()):
            if not inc:
                continue
            key = (user_id, chat_id)
            entry = merged.setdefault(key, {"inc": {}, "set": {}, "addToSet": {}})
            entry["inc"]["points"] = entry["inc"].get("points", 0) + inc
            entry["inc"]["total_messages"] = entry["inc"].get("total_messages", 0) + 1
            
        for (user_id, chat_id), timestamp in list(_lastmsg_buffer.items()):
            key = (user_id, chat_id)
            entry = merged.setdefault(key, {"inc": {}, "set": {}, "addToSet": {}})
            entry["set"]["last_message_time"] = timestamp
            entry["set"]["last_activity_date"] = today.toordinal()
            
        for (user_id, chat_id), activity_type in list(_activity_buffer.items()):
            key = (user_id, chat_id)
            entry = merged.setdefault(key, {"inc": {}, "set": {}, "addToSet": {}})
            entry["addToSet"]["activity_types"] = activity_type

        if merged:
            updates = []
            for (user_id, chat_id), spec in merged.items():
                update_doc = {}
                if spec["inc"]:
                    update_doc["$inc"] = spec["inc"]
                if spec["set"]:
                    update_doc["$set"] = spec["set"]
                if spec["addToSet"]:
                    update_doc["$addToSet"] = spec["addToSet"]
                
                # Set join date if new user
                update_doc["$setOnInsert"] = {
                    "join_date": current_time,
                    "streak_count": 0
                }
                
                updates.append({
                    "q": {"user_id": user_id, "chat_id": chat_id},
                    "u": update_doc,
                    "upsert": True,
                    "multi": False,
                })
                
            try:
                await db.command("update", users_collection.name, updates=updates, ordered=False)
            except Exception as e:
                LOGGER.error(f"Enhanced levels flush error: {e}")
                
        # Clear buffers
        _points_buffer.clear()
        _lastmsg_buffer.clear()
        _activity_buffer.clear()
        _streak_buffer.clear()
        _last_flush = time.time()
        
    except Exception as e:
        LOGGER.error(f"Enhanced flush error: {e}")
    finally:
        _buffer_flush_inflight = False

async def _schedule_enhanced_flush(force: bool = False):
    """Enhanced flush scheduling"""
    try:
        total_ops = len(_points_buffer) + len(_lastmsg_buffer) + len(_activity_buffer)
        if not force:
            if total_ops < _MAX_BUFFER_OPS and (time.time() - _last_flush) < _MIN_FLUSH_INTERVAL:
                return
        asyncio.create_task(_flush_enhanced_buffers())
    except Exception:
        pass

async def calculate_xp_gain(message_type: str = "text_message", streak_days: int = 0) -> int:
    """Calculate XP gain based on message type and streak"""
    base_xp = XP_MULTIPLIERS.get(message_type, 1)
    
    # Apply streak bonus
    streak_bonus = 0
    for days, bonus in STREAK_BONUSES.items():
        if streak_days >= days:
            streak_bonus = bonus
    
    # Random bonus (1-3 extra XP)
    import random
    random_bonus = random.randint(1, 3)
    
    return base_xp + streak_bonus + random_bonus

# Enhanced command handlers

@register(pattern="rank")
async def enhanced_rank(event):
    """Enhanced rank command with beautiful banner"""
    if not event.is_group:
        return await event.reply("You can only see your rank inside a specific group chat.")
    
    if not await _get_level_on(event.chat_id):
        return await event.reply("Levelling system is not active in this chat. Use `/level on`")
    
    user_id = event.sender_id
    chat_id = event.chat_id
    
    try:
        stats = await get_enhanced_user_stats(user_id, chat_id)
        if not stats:
            return await event.reply("Use /register to register yourself in bot first!")
        
        # Generate banner
        banner_buffer = await generate_rank_banner(user_id, chat_id, stats)
        
        # Send banner
        await event.reply(
            f"**{stats['first_name']}'s Profile**\n\n"
            f"🎯 **Rank**: {stats['rank_info']['emoji']} {stats['rank_info']['name']}\n"
            f"⭐ **Level**: {stats['level']}\n"
            f"💎 **XP**: {stats['points']:,}\n"
            f"🔥 **Streak**: {stats['streak']} days\n"
            f"📊 **Chat Rank**: #{stats['chat_rank']}\n"
            f"💬 **Messages**: {stats['total_messages']:,}",
            file=banner_buffer
        )
        
    except Exception as e:
        LOGGER.error(f"Enhanced rank error: {e}")
        await event.reply("Error generating rank card. Please try again later.")

@register(pattern="leaderboard")
async def enhanced_leaderboard(event):
    """Enhanced leaderboard with banner"""
    if not event.is_group:
        return await event.reply("Leaderboard is only for group chats.")
    
    if not await _get_level_on(event.chat_id):
        return await event.reply("Levelling system is not active in this chat. Use `/level on`")
    
    chat_id = event.chat_id
    leaderboard = await get_leaderboard(chat_id)
    
    if not leaderboard:
        return await event.reply("No data for this chat. Try /register to register yourself first!")
    
    try:
        # Add first names to leaderboard data
        for user in leaderboard:
            user['first_name'] = await _get_first_name(user['user_id'])
        
        # Generate banner
        banner_buffer = await generate_leaderboard_banner(chat_id, leaderboard)
        
        # Text summary
        text_summary = "🏆 **TOP PLAYERS** 🏆\n\n"
        for idx, user in enumerate(leaderboard[:5], 1):
            medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][idx-1]
            name = user.get('first_name', 'Unknown')
            points = user.get('points', 0)
            text_summary += f"{medal} {name}: {points:,} XP\n"
        
        await event.reply(
            text_summary,
            file=banner_buffer,
            buttons=[
                [Button.inline("Global Leaderboard 🌐", data="gleaderboard_")],
                [Button.inline("My Rank 📊", data=f"myrank_{event.sender_id}")]
            ]
        )
        
    except Exception as e:
        LOGGER.error(f"Enhanced leaderboard error: {e}")
        await event.reply("Error generating leaderboard. Please try again later.")

async def get_enhanced_user_stats(user_id: int, chat_id: int) -> Optional[Dict]:
    """Get enhanced user statistics"""
    try:
        user_data = await users_collection.find_one({"user_id": user_id, "chat_id": chat_id})
        if not user_data:
            return None
            
        points = user_data.get("points", 0)
        first_name = await _get_first_name(user_id)
        level = min(points // 100, levels)  # 100 XP per level
        rank_info = await get_rank_info(points)
        chat_rank = await get_user_chat_rank(user_id, chat_id)
        streak = user_data.get("streak_count", 0)
        total_messages = user_data.get("total_messages", 0)
        
        return {
            "points": points,
            "first_name": first_name,
            "level": level,
            "rank_info": rank_info,
            "chat_rank": chat_rank,
            "streak": streak,
            "total_messages": total_messages
        }
    except Exception as e:
        LOGGER.error(f"Enhanced stats error: {e}")
        return None

@register(pattern="daily")
async def enhanced_daily(event):
    """Enhanced daily reward with streak bonuses"""
    if not event.is_group:
        return await event.reply("You can only claim your daily bonus inside a group chat!")
    
    if not await _get_level_on(event.chat_id):
        return await event.reply("Levelling system is not active in this chat. Use `/level on`")
    
    user_id = event.sender_id
    chat_id = event.chat_id
    
    stats = await get_enhanced_user_stats(user_id, chat_id)
    if not stats:
        return await event.reply("Use /register to register yourself first!")
    
    can_collect, time_left = await can_collect_daily_enhanced(user_id, chat_id)
    if not can_collect:
        time_str = await get_time(time_left)
        return await event.reply(f"You can claim your daily reward in {time_str}")
    
    # Calculate reward based on streak and level
    base_reward = 100
    level_bonus = stats['level'] * 2
    streak_bonus = min(stats['streak'] * 5, 200)  # Max 200 bonus from streak
    total_reward = base_reward + level_bonus + streak_bonus
    
    # Update user data
    current_time = dt.datetime.now()
    await users_collection.update_one(
        {"user_id": user_id, "chat_id": chat_id},
        {
            "$inc": {"points": total_reward},
            "$set": {"last_daily_claim": current_time.timestamp()},
            "$inc": {"daily_streak": 1} if stats['streak'] > 0 else {"$set": {"daily_streak": 1}}
        },
        upsert=True
    )
    
    new_points = stats['points'] + total_reward
    await event.reply(
        f"🎁 **Daily Reward Claimed!**\n\n"
        f"💰 Base Reward: {base_reward} XP\n"
        f"⭐ Level Bonus: {level_bonus} XP\n"
        f"🔥 Streak Bonus: {streak_bonus} XP\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"✨ **Total**: +{total_reward} XP\n"
        f"💎 **New Balance**: {new_points:,} XP"
    )

async def can_collect_daily_enhanced(user_id: int, chat_id: int) -> Tuple[bool, float]:
    """Enhanced daily collection check with streak tracking"""
    try:
        user_data = await users_collection.find_one({"user_id": user_id, "chat_id": chat_id})
        if not user_data or "last_daily_claim" not in user_data:
            return True, 0
        
        last_claim = dt.datetime.fromtimestamp(user_data["last_daily_claim"])
        current_time = dt.datetime.now()
        time_diff = current_time - last_claim
        
        if time_diff.total_seconds() >= 86400:  # 24 hours
            return True, 0
        else:
            return False, 86400 - time_diff.total_seconds()
    except Exception:
        return True, 0

@register(pattern="weekly")
async def enhanced_weekly(event):
    """Enhanced weekly reward system"""
    if not event.is_group:
        return await event.reply("You can only claim your weekly bonus inside a group chat!")
    
    if not await _get_level_on(event.chat_id):
        return await event.reply("Levelling system is not active in this chat. Use `/level on`")
    
    user_id = event.sender_id
    chat_id = event.chat_id
    
    stats = await get_enhanced_user_stats(user_id, chat_id)
    if not stats:
        return await event.reply("Use /register to register yourself first!")
    
    can_collect, time_left = await can_collect_weekly_enhanced(user_id, chat_id)
    if not can_collect:
        time_str = await get_time(time_left)
        return await event.reply(f"You can claim your weekly reward in {time_str}")
    
    # Calculate enhanced weekly reward
    base_reward = 500
    level_bonus = stats['level'] * 10
    activity_bonus = min(stats['total_messages'] // 50, 1000)  # Max 1000 from activity
    rank_bonus = get_rank_bonus(stats['rank_info']['name'])
    
    total_reward = base_reward + level_bonus + activity_bonus + rank_bonus
    
    # Update user data
    current_time = dt.datetime.now()
    await users_collection.update_one(
        {"user_id": user_id, "chat_id": chat_id},
        {
            "$inc": {"points": total_reward},
            "$set": {"last_weekly_claim": current_time.timestamp()}
        },
        upsert=True
    )
    
    new_points = stats['points'] + total_reward
    await event.reply(
        f"🏆 **Weekly Reward Claimed!**\n\n"
        f"💰 Base Reward: {base_reward} XP\n"
        f"⭐ Level Bonus: {level_bonus} XP\n"
        f"📈 Activity Bonus: {activity_bonus} XP\n"
        f"👑 Rank Bonus: {rank_bonus} XP\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"✨ **Total**: +{total_reward} XP\n"
        f"💎 **New Balance**: {new_points:,} XP"
    )

def get_rank_bonus(rank_name: str) -> int:
    """Get bonus XP based on rank"""
    rank_bonuses = {
        "Newbie": 0, "Rookie": 50, "Explorer": 100, "Adventurer": 200,
        "Warrior": 300, "Champion": 500, "Legend": 750, "Mythic": 1000,
        "Divine": 1500, "Immortal": 2000, "Transcendent": 3000
    }
    return rank_bonuses.get(rank_name, 0)

async def can_collect_weekly_enhanced(user_id: int, chat_id: int) -> Tuple[bool, float]:
    """Enhanced weekly collection check"""
    try:
        user_data = await users_collection.find_one({"user_id": user_id, "chat_id": chat_id})
        if not user_data or "last_weekly_claim" not in user_data:
            return True, 0
        
        last_claim = dt.datetime.fromtimestamp(user_data["last_weekly_claim"])
        current_time = dt.datetime.now()
        time_diff = current_time - last_claim
        
        if time_diff.total_seconds() >= 604800:  # 7 days
            return True, 0
        else:
            return False, 604800 - time_diff.total_seconds()
    except Exception:
        return True, 0

@register(pattern="register")
async def enhanced_register(event):
    """Enhanced registration with profile setup"""
    if not event.is_group:
        return await event.reply("Please register inside a group, each group will have separate rankings.")
    
    if not await _get_level_on(event.chat_id):
        return await event.reply("Levelling system is not active in this chat. Use `/level on`")
    
    try:
        args = event.text.split(None, 1)[1].strip()
    except IndexError:
        return await event.reply(
            "🎯 **Registration Help**\n\n"
            "Use: `/register YourName`\n"
            "Example: `/register CoolGamer123`\n\n"
            "⚠️ **Note**: You cannot change your name once registered!"
        )
    
    if len(args) > 25:
        return await event.reply("❌ Name too long! Please use maximum 25 characters.")
    
    if len(args) < 2:
        return await event.reply("❌ Name too short! Please use at least 2 characters.")
    
    # Check if user already registered
    present = await first_name.find_one({"user_id": event.sender_id})
    if present and "first_name" in present:
        return await event.reply(
            "✅ You are already registered!\n"
            f"🏷️ **Your Name**: {present['first_name']}\n\n"
            "Use /rank to see your stats."
        )
    
    # Check if name is taken
    name_taken = await first_name.find_one({"first_name": args})
    if name_taken:
        return await event.reply(
            f"❌ Name `{args}` is already taken!\n"
            "Please choose a different name."
        )
    
    # Register user
    await first_name.update_one(
        {"user_id": event.sender_id},
        {"$set": {"first_name": args, "registration_date": dt.datetime.now().timestamp()}},
        upsert=True
    )
    
    # Initialize user in chat
    await users_collection.update_one(
        {"user_id": event.sender_id, "chat_id": event.chat_id},
        {
            "$setOnInsert": {
                "points": 0,
                "total_messages": 0,
                "streak_count": 0,
                "join_date": dt.datetime.now().timestamp()
            }
        },
        upsert=True
    )
    
    await event.reply(
        f"🎉 **Registration Successful!**\n\n"
        f"🏷️ **Name**: {args}\n"
        f"🆔 **User ID**: {event.sender_id}\n"
        f"📅 **Registered**: {dt.datetime.now().strftime('%Y-%m-%d')}\n\n"
        f"🎮 Welcome to the leveling system!\n"
        f"💬 Start chatting to gain XP and level up!\n\n"
        f"📊 Use /rank to check your stats\n"
        f"🏆 Use /leaderboard to see rankings"
    )

@register(pattern="profile")
async def user_profile(event):
    """Show detailed user profile"""
    if not event.is_group:
        return await event.reply("Profile command is only available in groups.")
    
    if not await _get_level_on(event.chat_id):
        return await event.reply("Levelling system is not active in this chat.")
    
    # Check if user wants to see someone else's profile
    try:
        if event.reply_to_msg_id:
            replied_msg = await event.get_reply_message()
            target_user_id = replied_msg.sender_id
        else:
            target_user_id = event.sender_id
    except Exception:
        target_user_id = event.sender_id
    
    stats = await get_enhanced_user_stats(target_user_id, event.chat_id)
    if not stats:
        return await event.reply("User not found in the leveling system!")
    
    # Get additional profile data
    user_data = await users_collection.find_one({"user_id": target_user_id, "chat_id": event.chat_id})
    
    join_date = None
    if user_data and "join_date" in user_data:
        join_date = dt.datetime.fromtimestamp(user_data["join_date"])
        days_active = (dt.datetime.now() - join_date).days
    else:
        days_active = 0
    
    # Calculate next rank progress
    next_rank = await get_next_rank_info(stats['points'])
    if next_rank:
        progress = ((stats['points'] - stats['rank_info']['min_points']) / 
                   (next_rank['min_points'] - stats['rank_info']['min_points']) * 100)
        progress_bar = "▓" * int(progress // 10) + "░" * (10 - int(progress // 10))
        next_rank_text = f"\n🎯 **Next Rank**: {next_rank['emoji']} {next_rank['name']}\n📊 **Progress**: {progress:.1f}% {progress_bar}"
    else:
        next_rank_text = "\n👑 **Max Rank Achieved!**"
    
    profile_text = (
        f"👤 **{stats['first_name']}'s Profile**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🏷️ **Rank**: {stats['rank_info']['emoji']} {stats['rank_info']['name']}\n"
        f"⭐ **Level**: {stats['level']}\n"
        f"💎 **XP**: {stats['points']:,}\n"
        f"🏆 **Chat Rank**: #{stats['chat_rank']}\n"
        f"🔥 **Streak**: {stats['streak']} days\n"
        f"💬 **Total Messages**: {stats['total_messages']:,}\n"
        f"📅 **Days Active**: {days_active}\n"
        f"{next_rank_text}"
    )
    
    await event.reply(profile_text)

@register(pattern="gift")
async def gift_xp(event):
    """Gift XP to another user (premium feature simulation)"""
    if not event.is_group:
        return await event.reply("You can only gift XP in group chats.")
    
    if not await _get_level_on(event.chat_id):
        return await event.reply("Levelling system is not active in this chat.")
    
    if not event.reply_to_msg_id:
        return await event.reply("Reply to a user's message to gift them XP!")
    
    try:
        args = event.text.split()
        amount = int(args[1]) if len(args) > 1 else 0
    except (ValueError, IndexError):
        return await event.reply(
            "🎁 **XP Gift Usage**\n\n"
            "Reply to someone and use:\n"
            "`/gift <amount>`\n\n"
            "Example: `/gift 100`\n"
            "Minimum: 10 XP, Maximum: 1000 XP"
        )
    
    if amount < 10 or amount > 1000:
        return await event.reply("❌ Gift amount must be between 10 and 1000 XP!")
    
    replied_msg = await event.get_reply_message()
    target_user_id = replied_msg.sender_id
    sender_id = event.sender_id
    
    if target_user_id == sender_id:
        return await event.reply("❌ You cannot gift XP to yourself!")
    
    # Check if sender has enough XP
    sender_stats = await get_enhanced_user_stats(sender_id, event.chat_id)
    if not sender_stats or sender_stats['points'] < amount:
        return await event.reply("❌ You don't have enough XP to gift!")
    
    # Transfer XP
    await users_collection.update_one(
        {"user_id": sender_id, "chat_id": event.chat_id},
        {"$inc": {"points": -amount}}
    )
    
    await users_collection.update_one(
        {"user_id": target_user_id, "chat_id": event.chat_id},
        {"$inc": {"points": amount}},
        upsert=True
    )
    
    target_name = await _get_first_name(target_user_id)
    sender_name = await _get_first_name(sender_id)
    
    await event.reply(
        f"🎁 **XP Gift Successful!**\n\n"
        f"👤 **From**: {sender_name}\n"
        f"👤 **To**: {target_name}\n"
        f"💎 **Amount**: {amount} XP\n\n"
        f"Thank you for being generous! 💝"
    )

@register(pattern="achievements")
async def show_achievements(event):
    """Show user achievements and milestones"""
    if not event.is_group:
        return await event.reply("Achievements are only available in group chats.")
    
    if not await _get_level_on(event.chat_id):
        return await event.reply("Levelling system is not active in this chat.")
    
    user_id = event.sender_id
    chat_id = event.chat_id
    
    stats = await get_enhanced_user_stats(user_id, chat_id)
    if not stats:
        return await event.reply("Use /register to register yourself first!")
    
    achievements = []
    
    # XP milestones
    xp_milestones = [100, 500, 1000, 5000, 10000, 25000, 50000, 100000]
    for milestone in xp_milestones:
        if stats['points'] >= milestone:
            achievements.append(f"💎 {milestone:,} XP Milestone")
    
    # Message milestones
    msg_milestones = [100, 500, 1000, 2500, 5000, 10000]
    for milestone in msg_milestones:
        if stats['total_messages'] >= milestone:
            achievements.append(f"💬 {milestone:,} Messages")
    
    # Streak achievements
    if stats['streak'] >= 7:
        achievements.append("🔥 Weekly Warrior (7+ day streak)")
    if stats['streak'] >= 30:
        achievements.append("🔥 Monthly Master (30+ day streak)")
    if stats['streak'] >= 100:
        achievements.append("🔥 Century Streak (100+ days)")
    
    # Rank achievements
    if stats['chat_rank'] <= 10:
        achievements.append("🏆 Top 10 Player")
    if stats['chat_rank'] <= 3:
        achievements.append("🥉 Top 3 Elite")
    if stats['chat_rank'] == 1:
        achievements.append("👑 Chat Champion")
    
    if not achievements:
        achievements.append("🌱 Just Getting Started!")
    
    achievement_text = (
        f"🏅 **{stats['first_name']}'s Achievements**\n"
        f"━━━━━━━━━━━━━━━━━━━\n" +
        "\n".join(f"✅ {achievement}" for achievement in achievements[-10:])  # Show last 10
    )
    
    if len(achievements) > 10:
        achievement_text += f"\n\n📊 **Total Achievements**: {len(achievements)}"
    
    await event.reply(achievement_text)

@callbackquery(pattern="gleaderboard_")
async def enhanced_global_leaderboard(event):
    """Enhanced global leaderboard with better formatting"""
    try:
        # Get global top players
        cursor = users_collection.aggregate([
            {"$group": {
                "_id": "$user_id",
                "total_points": {"$sum": "$points"},
                "total_messages": {"$sum": "$total_messages"},
                "chats_active": {"$sum": 1}
            }},
            {"$sort": {"total_points": -1}},
            {"$limit": 15}
        ])
        
        global_players = await cursor.to_list(length=15)
        
        # Get names for all players
        for player in global_players:
            player['first_name'] = await _get_first_name(player['_id'])
            player['rank_info'] = await get_rank_info(player['total_points'])
        
        if not global_players:
            return await event.edit("No global data available yet!")
        
        leaderboard_text = "🌍 **GLOBAL LEADERBOARD** 🌍\n\n"
        
        for idx, player in enumerate(global_players, 1):
            if idx <= 3:
                medals = ["🥇", "🥈", "🥉"]
                medal = medals[idx-1]
            else:
                medal = f"{idx}."
            
            name = player.get('first_name', 'Unknown')[:15]
            points = player.get('total_points', 0)
            rank_emoji = player.get('rank_info', {}).get('emoji', '⭐')
            chats = player.get('chats_active', 0)
            
            leaderboard_text += (
                f"{medal} {rank_emoji} **{name}**\n"
                f"    💎 {points:,} XP • 🏠 {chats} chats\n\n"
            )
        
        await event.edit(
            leaderboard_text,
            buttons=[[Button.inline("« Back to Chat Leaderboard", data="chat_leaderboard")]]
        )
        
    except Exception as e:
        LOGGER.error(f"Global leaderboard error: {e}")
        await event.edit("Error loading global leaderboard.")

# Enhanced message handler with activity detection
@telethn.on(events.NewMessage)
async def enhanced_message_handler(event):
    """Enhanced message handler with activity tracking and XP calculation"""
    if not event.is_group or not event.from_id:
        return
    
    user_id = event.sender_id
    chat_id = event.chat_id
    
    # Skip bot messages
    if user_id == 5737513498:
        return
    
    if not await _get_level_on(chat_id):
        return
    
    # Detect message type for XP calculation
    message_type = "text_message"
    if event.photo:
        message_type = "photo"
    elif event.video:
        message_type = "video"
    elif event.document:
        message_type = "document"
    elif event.sticker:
        message_type = "sticker"
    elif event.voice:
        message_type = "voice"
    elif event.video_note:
        message_type = "video_note"
    elif event.poll:
        message_type = "poll"
    elif event.game:
        message_type = "game"
    
    # Anti-flood check
    if await is_flooding_enhanced(user_id, chat_id):
        return
    
    # Get user's streak for XP calculation
    user_data = await users_collection.find_one(
        {"user_id": user_id, "chat_id": chat_id},
        {"streak_count": 1}
    )
    streak_days = user_data.get("streak_count", 0) if user_data else 0
    
    # Calculate XP gain
    xp_gain = await calculate_xp_gain(message_type, streak_days)
    
    # Buffer updates
    _points_buffer[(user_id, chat_id)] = _points_buffer.get((user_id, chat_id), 0) + xp_gain
    _lastmsg_buffer[(user_id, chat_id)] = time.time()
    _activity_buffer[(user_id, chat_id)] = message_type
    
    await _schedule_enhanced_flush()
    
    # Check for rank up (requires fresh data)
    current_points = await get_user_current_points(user_id, chat_id)
    if current_points:
        for rank in ranks[::-1]:
            # Check if user just reached this rank threshold
            if (current_points >= rank["min_points"] and 
                current_points - xp_gain < rank["min_points"]):
                
                rank_up_msg = (
                    f"🎉 **RANK UP!** 🎉\n\n"
                    f"👤 **Player**: {await _get_first_name(user_id)}\n"
                    f"🏷️ **New Rank**: {rank['emoji']} {rank['name']}\n"
                    f"💎 **XP**: {current_points:,}\n\n"
                    f"Congratulations on your achievement! 🌟"
                )
                await event.reply(rank_up_msg)
                break

async def get_user_current_points(user_id: int, chat_id: int) -> Optional[int]:
    """Get current points including buffer"""
    try:
        user_data = await users_collection.find_one(
            {"user_id": user_id, "chat_id": chat_id},
            {"points": 1}
        )
        db_points = user_data.get("points", 0) if user_data else 0
        buffer_points = _points_buffer.get((user_id, chat_id), 0)
        return db_points + buffer_points
    except Exception:
        return None

async def is_flooding_enhanced(user_id: int, chat_id: int) -> bool:
    """Enhanced flood detection with different cooldowns for different content"""
    try:
        current_time = time.time()
        
        # Check buffer first
        if (user_id, chat_id) in _lastmsg_buffer:
            last_time = _lastmsg_buffer[(user_id, chat_id)]
            if current_time - last_time < 3:  # 3 second cooldown
                return True
        
        # Check database
        user_data = await users_collection.find_one(
            {"user_id": user_id, "chat_id": chat_id},
            {"last_message_time": 1}
        )
        
        if user_data and "last_message_time" in user_data:
            return (current_time - user_data["last_message_time"]) < 3
        
        return False
    except Exception:
        return False

async def get_leaderboard(chat_id: int) -> List[Dict]:
    """Enhanced leaderboard with better sorting"""
    try:
        cursor = users_collection.find(
            {"chat_id": chat_id},
            {"_id": 0, "user_id": 1, "points": 1, "total_messages": 1}
        ).sort([("points", -1), ("total_messages", -1)]).limit(15)
        
        return await cursor.to_list(length=15)
    except Exception as e:
        LOGGER.error(f"Leaderboard error: {e}")
        return []

# Initialize fonts on startup
async def initialize_level_system():
    """Initialize the enhanced level system"""
    try:
        await download_default_fonts()
        await start_levels_flush_task(3.0)  # Flush every 3 seconds
        LOGGER.info("Enhanced level system initialized successfully!")
    except Exception as e:
        LOGGER.error(f"Failed to initialize level system: {e}")

# Command to manually flush buffers (admin only)
@register(pattern="flushxp")
async def manual_flush(event):
    """Manually flush XP buffers (admin only)"""
    if not await is_admin(event, event.sender_id):
        return
    
    await _flush_enhanced_buffers()
    await event.reply("✅ XP buffers flushed successfully!")

# Stats command for admins
@register(pattern="levelstats")
async def level_system_stats(event):
    """Show level system statistics (admin only)"""
    if not await is_admin(event, event.sender_id):
        return
    
    try:
        # Get various statistics
        total_users = await users_collection.count_documents({})
        active_chats = await level.count_documents({})
        buffered_ops = len(_points_buffer) + len(_lastmsg_buffer) + len(_activity_buffer)
        
        # Top global player
        top_player_cursor = users_collection.aggregate([
            {"$group": {"_id": "$user_id", "total_points": {"$sum": "$points"}}},
            {"$sort": {"total_points": -1}},
            {"$limit": 1}
        ])
        top_player = await top_player_cursor.to_list(1)
        
        if top_player:
            top_name = await _get_first_name(top_player[0]['_id'])
            top_points = top_player[0]['total_points']
        else:
            top_name, top_points = "None", 0
        
        stats_text = (
            f"📊 **Level System Statistics**\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"👥 **Total Users**: {total_users:,}\n"
            f"💬 **Active Chats**: {active_chats:,}\n"
            f"⏳ **Buffered Operations**: {buffered_ops:,}\n"
            f"🏆 **Top Player**: {top_name} ({top_points:,} XP)\n"
            f"🔧 **Last Buffer Flush**: {int(time.time() - _last_flush)}s ago"
        )
        
        await event.reply(stats_text)
        
    except Exception as e:
        LOGGER.error(f"Level stats error: {e}")
        await event.reply("Error retrieving level system statistics.")

# Level system toggle (enhanced)
@usage("/level [on/off]")
@example("/level on")
@description("Enhanced levelling system with XP, ranks, streaks, and achievements")
@register(pattern="level")
@logging
@exception
async def enhanced_level_toggle(event):
    """Enhanced level system toggle with better feedback"""
    if not event.is_group:
        return await event.reply("❌ This command only works in group chats.")
    
    if not await is_admin(event, event.sender_id):
        return await event.reply("❌ Only admins can toggle the level system.")
    
    try:
        args = event.text.split()
        if len(args) < 2:
            # Show current status
            is_enabled = await _get_level_on(event.chat_id)
            status = "🟢 **Enabled**" if is_enabled else "🔴 **Disabled**"
            await event.reply(
                f"⚙️ **Level System Status**: {status}\n\n"
                f"Use `/level on` to enable\n"
                f"Use `/level off` to disable"
            )
            return
        
        command = args[1].lower()
        
        if command in ["on", "enable", "yes", "true", "1"]:
            if await level.find_one({"chat_id": event.chat_id}):
                return await event.reply("✅ Level system is already enabled!")
            
            await level.update_one(
                {"chat_id": event.chat_id},
                {"$set": {"chat_id": event.chat_id, "enabled_date": dt.datetime.now().timestamp()}},
                upsert=True
            )
            
            await event.reply(
                f"🎉 **Level System Enabled!**\n\n"
                f"✨ Features unlocked:\n"
                f"• 📊 XP and leveling system\n"
                f"• 🏆 Rankings and leaderboards\n"
                f"• 🎁 Daily and weekly rewards\n"
                f"• 🔥 Activity streaks\n"
                f"• 🏅 Achievements system\n"
                f"• 💎 XP gifting\n\n"
                f"Use /register to get started!"
            )
            
        elif command in ["off", "disable", "no", "false", "0"]:
            if not await level.find_one({"chat_id": event.chat_id}):
                return await event.reply("❌ Level system is already disabled!")
            
            await level.delete_one({"chat_id": event.chat_id})
            await event.reply(
                f"🔴 **Level System Disabled**\n\n"
                f"All level data has been preserved and will be restored if you re-enable the system."
            )
            
        else:
            await event.reply(
                f"❌ Invalid option. Use:\n"
                f"• `/level on` to enable\n"
                f"• `/level off` to disable"
            )
            
    except Exception as e:
        LOGGER.error(f"Level toggle error: {e}")
        await event.reply("❌ Error toggling level system.")

# Help command for the level system
@register(pattern="levelhelp")
async def level_help(event):
    """Show comprehensive help for the level system"""
    help_text = (
        f"📚 **Level System Help**\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        
        f"🎮 **Getting Started:**\n"
        f"• `/register <name>` - Register with a unique name\n"
        f"• `/rank` - View your rank card with banner\n"
        f"• `/profile` - Detailed profile information\n\n"
        
        f"🏆 **Leaderboards:**\n"
        f"• `/leaderboard` - Chat leaderboard with banner\n"
        f"• Click 'Global Leaderboard' for worldwide ranks\n\n"
        
        f"💰 **Rewards:**\n"
        f"• `/daily` - Claim daily XP (increases with level/streak)\n"
        f"• `/weekly` - Claim weekly XP (bonus based on activity)\n"
        f"• `/gift <amount>` - Gift XP to others (reply to message)\n\n"
        
        f"🎯 **Features:**\n"
        f"• `/achievements` - View your milestone achievements\n"
        f"• Different XP for different message types\n"
        f"• Activity streaks with bonus XP\n"
        f"• Beautiful rank banners with user avatars\n"
        f"• 11 unique ranks from Newbie to Transcendent\n\n"
        
        f"📊 **XP System:**\n"
        f"• Text messages: +1-6 XP (base + bonuses)\n"
        f"• Photos/Documents: +2-8 XP\n"
        f"• Voice messages: +4-12 XP\n"
        f"• Videos: +3-9 XP\n"
        f"• Polls/Games: +5-15 XP\n"
        f"• Streak bonuses up to +200 XP\n"
        f"• Anti-flood protection (3s cooldown)\n\n"
        
        f"⚙️ **Admin Commands:**\n"
        f"• `/level on/off` - Toggle system for chat\n"
        f"• `/levelstats` - View system statistics\n"
        f"• `/flushxp` - Manually flush XP buffers\n\n"
        
        f"🎨 **Visual Features:**\n"
        f"• Custom rank banners with gradients\n"
        f"• User avatars in rank cards\n"
        f"• Progress bars for next rank\n"
        f"• Beautiful leaderboard designs\n"
        f"• Rank-specific colors and emojis"
    )
    
    await event.reply(help_text)

# Enhanced rankings info
@register(pattern="rankings")
@exception
async def enhanced_rankings_info(event):
    """Show detailed ranking system information"""
    rankings_text = (
        f"👑 **Enhanced Ranking System**\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    for i, rank in enumerate(ranks):
        # Add spacing for better readability
        if i > 0:
            rankings_text += "\n"
            
        next_rank = ranks[i + 1] if i + 1 < len(ranks) else None
        if next_rank:
            xp_needed = next_rank['min_points'] - rank['min_points']
            rankings_text += (
                f"{rank['emoji']} **{rank['name']}**\n"
                f"   💎 {rank['min_points']:,}+ XP\n"
                f"   📈 {xp_needed:,} XP to next rank\n"
            )
        else:
            rankings_text += (
                f"{rank['emoji']} **{rank['name']}** (Max Rank)\n"
                f"   💎 {rank['min_points']:,}+ XP\n"
                f"   👑 Ultimate Achievement!\n"
            )
    
    rankings_text += (
        f"\n━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 **Progression Tips:**\n"
        f"• Stay active daily for streak bonuses\n"
        f"• Share different content types for bonus XP\n"
        f"• Claim daily/weekly rewards consistently\n"
        f"• Help others by gifting XP\n"
        f"• Participate in community activities\n\n"
        f"🏆 Each rank unlocks special perks and recognition!"
    )
    
    await event.reply(rankings_text)

# Streak management functions
async def update_user_streak(user_id: int, chat_id: int):
    """Update user's activity streak"""
    try:
        current_date = dt.date.today()
        yesterday = current_date - dt.timedelta(days=1)
        
        user_data = await users_collection.find_one(
            {"user_id": user_id, "chat_id": chat_id},
            {"last_activity_date": 1, "streak_count": 1}
        )
        
        if not user_data:
            # New user - start streak
            await users_collection.update_one(
                {"user_id": user_id, "chat_id": chat_id},
                {
                    "$set": {
                        "last_activity_date": current_date.toordinal(),
                        "streak_count": 1
                    }
                },
                upsert=True
            )
            return 1
        
        last_activity = user_data.get("last_activity_date")
        current_streak = user_data.get("streak_count", 0)
        
        if not last_activity:
            # First activity
            await users_collection.update_one(
                {"user_id": user_id, "chat_id": chat_id},
                {
                    "$set": {
                        "last_activity_date": current_date.toordinal(),
                        "streak_count": 1
                    }
                }
            )
            return 1
        
        last_date = dt.date.fromordinal(last_activity)
        
        if last_date == current_date:
            # Already active today
            return current_streak
        elif last_date == yesterday:
            # Consecutive day - increment streak
            new_streak = current_streak + 1
            await users_collection.update_one(
                {"user_id": user_id, "chat_id": chat_id},
                {
                    "$set": {
                        "last_activity_date": current_date.toordinal(),
                        "streak_count": new_streak
                    }
                }
            )
            return new_streak
        else:
            # Streak broken - reset to 1
            await users_collection.update_one(
                {"user_id": user_id, "chat_id": chat_id},
                {
                    "$set": {
                        "last_activity_date": current_date.toordinal(),
                        "streak_count": 1
                    }
                }
            )
            return 1
            
    except Exception as e:
        LOGGER.error(f"Streak update error: {e}")
        return 0

# Auto-initialization when module loads
async def start_levels_flush_task(interval_seconds: float = 3.0):
    """Start periodic buffer flushing task"""
    global _periodic_flush_started
    if _periodic_flush_started:
        return
    _periodic_flush_started = True

    async def _periodic_flush():
        while True:
            try:
                await asyncio.sleep(max(1.0, interval_seconds))
                await _flush_enhanced_buffers()
            except Exception as e:
                LOGGER.error(f"Periodic flush error: {e}")

    asyncio.create_task(_periodic_flush())

# Initialize the system
asyncio.create_task(initialize_level_system())
