import os
import sys
import fcntl
import asyncio
import sqlite3
import math
import random
import logging
import time
import aiohttp
import hmac
import hashlib
import urllib.parse
import json
import re
import html
import socket
from types import SimpleNamespace
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict, Any

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message, Dice, BufferedInputFile
)

# ==============================================================================
# SINGLE-INSTANCE LOCK (PREVENTS DUPLICATE BOT POLLING GLITCHES)
# ==============================================================================
_lock_fp = None
def acquire_single_instance_lock():
    global _lock_fp
    if _lock_fp is not None:
        return True
    lock_file_path = "/tmp/OBITOx_STORE_bot.lock"
    try:
        _lock_fp = open(lock_file_path, "w")
        fcntl.flock(_lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fp.write(f"{os.getpid()}\n")
        _lock_fp.flush()
        return True
    except (IOError, BlockingIOError):
        print(f"[FATAL] Another instance of RajuConfig Bot is already running! (Lock held by another process). Exiting PID {os.getpid()} immediately.")
        sys.exit(0)

# ==============================================================================
# 1. BOT CONFIGURATION & CONSTANTS
# ==============================================================================
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'yp_shop.db')
# Telegram credentials MUST come from the CloudVPS environment.
# Never keep a bot token hard-coded in the source file: Telegram returns
# "Unauthorized" when the token is invalid/revoked, and exposing a token
# in source is also a security risk.
BOT_TOKEN = os.getenv("BOT_TOKEN", "8960892793:AAGBOYi2Eboz4TgaUzR7-uQipKLl7avzvjo").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Add the current BotFather token to CloudVPS Environment/Variables as BOT_TOKEN.")

# Optional comma-separated ADMIN_IDS supports multiple admins. The legacy
# ADMIN_ID remains a normal integer because many existing handlers compare
# against it directly.
def _load_admin_ids() -> List[int]:
    raw = os.getenv("ADMIN_IDS", "8259869459")
    ids: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
            if value > 0 and value not in ids:
                ids.append(value)
        except ValueError:
            logger.warning("Ignoring invalid ADMIN_IDS value: %r", part) if 'logger' in globals() else None
    if not ids:
        raise RuntimeError("ADMIN_IDS is empty/invalid. Set ADMIN_IDS to one or more Telegram numeric user IDs.")
    return ids

ADMIN_IDS = _load_admin_ids()
ADMIN_ID = ADMIN_IDS[0]
ADMIN_CONTACT = os.getenv("ADMIN_CONTACT", "@OBITO_ADMIN_1").strip() or "@OBITO_ADMIN_1"
BOT_USERNAME = ""

USDT_TO_INR = 90.0
VIP_DISCOUNT_PERCENTAGE = 15.0
VIP_PRICE_INR = 299.0

WELCOME_STICKER_ID = "CAACAgIAAxkBAAEU-WZmH_..."  # Replace with your sticker ID
SPIN_DELAY_SECONDS = 2.5

# Purchase/payment click protection. These guards stop duplicate callback
# delivery from creating multiple orders or multiple fulfilments.
_purchase_locks: Dict[tuple, asyncio.Lock] = {}
_payment_verify_locks: Dict[str, asyncio.Lock] = {}
_callback_cooldowns: Dict[tuple, float] = {}
_CALLBACK_COOLDOWN_SECONDS = 3.5

def _get_purchase_lock(user_id: int, prod_id: int) -> asyncio.Lock:
    key = (int(user_id), int(prod_id))
    lock = _purchase_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _purchase_locks[key] = lock
    return lock

def _callback_is_on_cooldown(key: tuple, seconds: float = _CALLBACK_COOLDOWN_SECONDS) -> bool:
    now = time.monotonic()
    last = _callback_cooldowns.get(key, 0.0)
    if now - last < seconds:
        return True
    _callback_cooldowns[key] = now
    return False

FIXED_CATEGORIES = [
    "ANDROID NON ROOT PANEL",
    "ANDROID ROOT PANEL",
    "IPHONE PANEL",
    "PC PANEL"
]

def get_all_categories() -> List[str]:
    try:
        rows = db_query("SELECT name FROM categories ORDER BY sort_order ASC, id ASC", fetchall=True)
        if rows is not None and len(rows) > 0:
            return [r[0] for r in rows if r[0]]
    except Exception as e:
        logger.error(f"Error fetching categories: {e}")
    return []

# ==============================================================================
# YOUR PREMIUM EMOJIS – all required emoji IDs
# ==============================================================================
DEFAULT_EMOJIS = {
    'product_store': '6163205892834598715',
    'profile': '6035084557378654059',
    'add_balance': '5278467510604160626',
    'history': '6160968017304888311',
    'referral': '6032609071373226027',
    'support': '6161112036148255813',
    'ludo_spin': '6147764669361692707',
    'back': '6039539366177541657',
    'upi': '5807750375033278838',
    'binance': '5843689746538173057',
    'reseller': '6120436698695338614',
    'tutorial': '5368653135101310687',
    'download': '6161336001512874965',
    'telegram': '6161096071754818473',
    'whatsapp': '6118193823823698862',
    'welcome': '5312361253610475399',
    'vip': '6086672466132865380',
    'category_android_non_root': '6161172706856282588',
    'category_android_root': '6161449831031118974',
    'category_iphone': '6161399700172840408',
    'category_pc': '5350554349074391003',
    'grid_id': '5474625972751837256',
    'name': '5215399540814781035',
    'account_level': '6129584162992034014',
    'regular_user': '5904630315946611415',
    'wallet': '6210859306602995217',
    'current_balance': '5316711376876485361',
    'global_stats': '6161437856662298090',
    'total_orders': '6160968017304888311',
    'total_spent': '5197503331215361533',
    'total_referrals': '5938196735200333756',
    'joined_grid': '5433614043006903194',
    'info_icon': '6037421444789440735',
    'check_icon': '6161241250239356403',
    'checkbox_icon': '6161437856662298090',
    'shield_icon': '6086672466132865380',
    'money_icon': '5890848474563352982',
    'redeem_icon': '5377624166436445368',
    'wallet_left': '6210859306602995217',
    'wallet_right': '5305699699204837855',
    'point_down': '6161302621027049305',
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot_activity.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

async def notify_admins(text: str, reply_markup=None, parse_mode: str = "HTML"):
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e:
            logger.warning(f"Could not send notification to admin {aid}: {e}")

def fmt_curr(amount: float) -> str:
    return f"₹{amount:,.2f}"

def natural_sort_key(value: Any) -> List[Any]:
    """Sort names naturally: A, B, C... and 1, 2, 10 instead of 1, 10, 2."""
    text = str(value or "").strip()
    return [int(part) if part.isdigit() else part.casefold()
            for part in re.split(r"(\d+)", text)]

def normalize_validity_group(value: Any) -> str:
    """Normalize common package durations so 1 Day/1 Days, 3 Hour/3 Hours, etc.
    appear as one admin pricing group. Unknown/custom validity strings are
    normalized conservatively and remain their own group.
    """
    raw = re.sub(r"\s+", " ", str(value or "").strip())
    if not raw:
        return "custom"
    # Match a numeric duration anywhere in the label, not only at the beginning.
    # This handles names such as "Bala Movie 1 Over" and "M1 1 Over" in one group.
    m = re.search(r"(?:^|\s)(\d+(?:\.\d+)?)\s*([A-Za-z]+)\b", raw, re.IGNORECASE)
    if m:
        number, unit = m.group(1), m.group(2).lower()
        unit_map = {
            "day":"day", "days":"day",
            "hour":"hour", "hours":"hour", "hr":"hour", "hrs":"hour",
            "week":"week", "weeks":"week",
            "month":"month", "months":"month",
            "year":"year", "years":"year",
            "over":"over", "overs":"over",
            "minute":"minute", "minutes":"minute", "min":"minute", "mins":"minute",
        }
        return f"{number} {unit_map.get(unit, unit)}"
    return raw.casefold()

def display_validity_group(key: str) -> str:
    if not key:
        return "Custom"
    if key == "custom":
        return "Custom"
    parts = key.split(" ", 1)
    if len(parts) == 2 and re.match(r"^\d", parts[0]):
        n, unit = parts
        return f"{n} {unit.title()}"
    return key.title()

def get_common_validity_groups(active_only: bool = True) -> List[Tuple[str, int]]:
    where = "WHERE is_active=1" if active_only else ""
    rows = db_query(f"SELECT validity, name FROM products {where}", fetchall=True) or []
    counts: Dict[str, int] = {}
    for row in rows:
        validity = row[0] if row else ""
        name = row[1] if len(row) > 1 else ""
        # Prefer explicit validity; if it does not contain a recognized duration,
        # fall back to the package name.
        key = normalize_validity_group(validity)
        if key == "custom" or not re.search(r"\d+(?:\.\d+)?\s*[A-Za-z]+\b", str(validity or "")):
            key = normalize_validity_group(name)
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda item: natural_sort_key(display_validity_group(item[0])))


def product_matches_validity_group(validity: Any, name: Any, target_key: str) -> bool:
    """Match a common duration using both DB validity and package name.
    This fixes older products where the duration was stored in name rather than validity.
    """
    candidates = [validity, name]
    for value in candidates:
        if normalize_validity_group(value) == target_key:
            return True
    return False

# ==============================================================================
# 2. DATABASE FUNCTIONS
# ==============================================================================
def db_query(query: str, params: tuple = (), fetchone: bool = False, fetchall: bool = False, commit: bool = True) -> Any:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    try:
        c.execute(query, params)
        if fetchone:
            res = c.fetchone()
        elif fetchall:
            res = c.fetchall()
        else:
            res = None
        if commit: conn.commit()
        return res
    except Exception as e:
        logger.error(f"DB Error: {e} | Query: {query} | Params: {params}")
        if commit: conn.rollback()
        return None
    finally:
        conn.close()

def get_setting(key: str, default: str = "") -> str:
    val = db_query("SELECT value FROM settings WHERE key=?", (key,), fetchone=True)
    return val[0] if val and val[0] else default

def set_setting(key: str, value: str) -> None:
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

# Telegram inline-button styles: blue=primary, green=success, red=danger.
BUTTON_COLOR_MAP = {"blue": "primary", "green": "success", "red": "danger",
                    "primary": "primary", "success": "success", "danger": "danger"}

def button_style(setting_key: str, default: str = "primary") -> str:
    return BUTTON_COLOR_MAP.get((get_setting(setting_key, default) or default).strip().lower(), default)

def admin_menu_style() -> str:
    return button_style("admin_menu_color", "primary")

def panel_menu_style() -> str:
    return button_style("panel_menu_color", "primary")

def product_menu_style() -> str:
    return button_style("product_menu_color", "primary")

def gateway_enabled(name: str) -> bool:
    key = f"gateway_{name.lower()}_status"
    return (get_setting(key, "ON") or "ON").upper() == "ON"

def is_admin(user_id: int) -> bool:
    # ADMIN_ID is kept as an int for backwards compatibility; ADMIN_IDS is the
    # authoritative collection used by all admin checks.
    try:
        return int(user_id) in {int(a) for a in ADMIN_IDS}
    except Exception:
        return False

def is_product_in_maintenance(prod_id: int) -> bool:
    try:
        row = db_query("SELECT is_maintenance FROM products WHERE id=?", (prod_id,), fetchone=True)
        return bool(row and row[0])
    except Exception:
        return False

def set_product_maintenance(prod_id: int, is_maintenance: int) -> None:
    try:
        db_query("UPDATE products SET is_maintenance=? WHERE id=?", (1 if is_maintenance else 0, prod_id))
    except Exception as e:
        logger.error(f"Failed to set product maintenance: {e}")


def log_activity(user_id: int, action: str, details: str = "") -> None:
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db_query(
            "INSERT INTO activity_logs (user_id, action, details, timestamp) VALUES (?, ?, ?, ?)",
            (user_id, action, details, timestamp)
        )
    except Exception as e:
        logger.error(f"Failed to log activity: {e}")

def get_emoji(slot: str, default_id: str = None) -> str:
    stored = get_setting(f"emoji_{slot}", "")
    emoji_id = stored if stored and stored.isdigit() else (default_id or DEFAULT_EMOJIS.get(slot, ""))
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">✨</tg-emoji>'
    return "✨"

def get_emoji_icon(slot: str, default_id: str = None) -> str:
    stored = get_setting(f"emoji_{slot}", "")
    emoji_id = stored if stored and stored.isdigit() else (default_id or DEFAULT_EMOJIS.get(slot, ""))
    return emoji_id

# ==============================================================================
# 3. STRING RESOURCES – using placeholders for premium emojis
# ==============================================================================
UI_TEXTS = {
    "start_menu": (
        "✨ <b>WELCOME TO THE STORE</b>\n\n"
        "{product_store} Product Store : all key purchase & instantly delivery\n"
        "{profile} My Profile : check your account information\n"
        "{add_balance} Add Balance : deposit balance & secure service\n"
        "{history} All History : check all key purchase history\n"
        "{referral} Referral : invite friends & earn rewards\n"
        "{tutorial} Tutorial : view tutorial and work this bot\n"
        "{support} Support : bot problem fixed for support admin\n"
        "{ludo_spin} Ludo Spin : play game and win balance\n"
        "{download} Download Files : download latest apk for safety."
    ),
    "download_files": (
        "🗂 <b><u>DOWNLOAD PREMIUM APK & FILES 📊</u></b>\n\n"
        "🌐 All our highly secured, premium, and updated files\n"
        "are securely hosted on our private channel! ⚠️⛔️\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📱 <b>WHAT YOU GET:</b> 📌\n\n"
        "✔️ Latest APK Updates 🔔\n"
        "✔️ 100% Virus Free & Secure ‼️\n"
        "✔️ All Configs & Scripts 🌸\n"
        "✔️ Complete Installation Guides 🔺\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⌨️ Tap the button below to access the Download Channel! 📝"
    ),
    "lucky_dice_result": (
        "{ludo_spin} <b><u>LUCKY DICE RESULT 🔨 💯</u></b>\n\n"
        "🎲 <b>Dice Value:</b> {dice_value}\n\n"
        "💸 <b>You Won:</b> {won_amount}\n"
        "💰 <b>Total Balance:</b> {new_balance}\n\n"
        "Congratulations! Come back after 24 hours."
    ),
    "vip_menu": (
        "🌟 <b><u>VIP MEMBERSHIP CLUB</u></b> 🌟\n\n"
        "Unlock premium benefits and permanent discounts!\n\n"
        "💎 <b>VIP Benefits:</b>\n"
        "• Flat 15% off on ALL products (Stacks with Reseller!)\n"
        "• Priority Support\n"
        "• Exclusive VIP-only giveaways\n\n"
        "💳 <b>VIP Price:</b> ₹299.00 (Lifetime)\n"
        "👤 <b>Your Status:</b> {vip_status}"
    ),
    "add_balance_menu": (
        "{add_balance} <b>ADD BALANCE</b> {info_icon}\n\n"
        "{info_icon} Select your preferred payment method. {check_icon}\n\n"
        "┣ {upi} UPI — Fast Indian payments {checkbox_icon}\n"
        "┣ {binance} Binance — Crypto payments {checkbox_icon}\n\n"
        "{shield_icon} Payments are verified securely. {check_icon}"
    )
}

def get_ui_text(key: str, **kwargs) -> str:
    val = db_query("SELECT value FROM settings WHERE key=?", (f"ui_{key}",), fetchone=True)
    template = val[0] if val and val[0] else UI_TEXTS.get(key, "")

    emoji_map = {
        '{product_store}': get_emoji('product_store'),
        '{profile}': get_emoji('profile'),
        '{add_balance}': get_emoji('add_balance'),
        '{history}': get_emoji('history'),
        '{referral}': get_emoji('referral'),
        '{tutorial}': get_emoji('tutorial'),
        '{support}': get_emoji('support'),
        '{ludo_spin}': get_emoji('ludo_spin'),
        '{download}': get_emoji('download'),
        '{telegram}': get_emoji('telegram'),
        '{whatsapp}': get_emoji('whatsapp'),
        '{upi}': get_emoji('upi'),
        '{binance}': get_emoji('binance'),
        '{info_icon}': get_emoji('info_icon'),
        '{check_icon}': get_emoji('check_icon'),
        '{checkbox_icon}': get_emoji('checkbox_icon'),
        '{shield_icon}': get_emoji('shield_icon'),
        '{money_icon}': get_emoji('money_icon'),
        '{redeem_icon}': get_emoji('redeem_icon'),
        '{wallet_left}': get_emoji('wallet_left'),
        '{wallet_right}': get_emoji('wallet_right'),
        '{point_down}': get_emoji('point_down'),
    }
    for placeholder, emoji_tag in emoji_map.items():
        template = template.replace(placeholder, emoji_tag)

    if kwargs:
        try:
            return template.format(**kwargs)
        except KeyError as e:
            logger.warning(f"Missing formatting key for template {key}: {e}")
    return template

# ==============================================================================
# 4. DATABASE INITIALISATION & MIGRATION
# ==============================================================================
def init_db() -> None:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, 
            phone TEXT, 
            first_name TEXT, 
            username TEXT,
            balance REAL DEFAULT 0.0, 
            account_type TEXT DEFAULT 'Regular', 
            orders_count INTEGER DEFAULT 0, 
            spent REAL DEFAULT 0.0, 
            referrals_count INTEGER DEFAULT 0, 
            referral_earned REAL DEFAULT 0.0, 
            referred_by INTEGER, 
            last_spin TEXT, 
            joined_date TEXT,
            is_reseller INTEGER DEFAULT 0,
            reseller_since TEXT,
            total_saved REAL DEFAULT 0.0,
            is_banned INTEGER DEFAULT 0,
            warnings INTEGER DEFAULT 0,
            is_vip INTEGER DEFAULT 0,
            vip_since TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            category TEXT, 
            panel_name TEXT DEFAULT '',
            name TEXT, 
            price_inr REAL, 
            reseller_price REAL DEFAULT 0.0,
            stock INTEGER, 
            apk_link TEXT, 
            validity TEXT DEFAULT 'Lifetime', 
            device_limit TEXT DEFAULT '1 Device',
            is_active INTEGER DEFAULT 1
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS product_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            product_id INTEGER, 
            key_text TEXT, 
            is_used INTEGER DEFAULT 0
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            user_id INTEGER, 
            product_name TEXT, 
            price_paid REAL, 
            delivered_key TEXT, 
            purchase_date TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            user_id INTEGER, 
            message TEXT, 
            status TEXT DEFAULT 'Open',
            created_at TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, 
            value TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS coupons (
            code TEXT PRIMARY KEY, 
            amount REAL, 
            uses_left INTEGER
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS redeemed (
            user_id INTEGER, 
            code TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            order_id TEXT PRIMARY KEY, 
            user_id INTEGER, 
            amount_inr REAL, 
            status TEXT, 
            timestamp INTEGER
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS crypto_txns (
            txid TEXT PRIMARY KEY, 
            user_id INTEGER, 
            amount_usdt REAL,
            amount_inr REAL,
            photo_id TEXT,
            status TEXT DEFAULT 'pending',
            timestamp INTEGER
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS heroic_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            price REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            delivered_id TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            delivered_at TEXT DEFAULT ''
        )
    ''')

    
    c.execute('''
        CREATE TABLE IF NOT EXISTS spin_rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            amount REAL
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            details TEXT,
            timestamp TEXT
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            emoji_id TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0
        )
    ''')
    c.execute("SELECT COUNT(*) FROM categories")
    if c.fetchone()[0] == 0:
        for idx, cat in enumerate(FIXED_CATEGORIES, 1):
            c.execute("INSERT OR IGNORE INTO categories (name, sort_order) VALUES (?, ?)", (cat, idx))

    # ------------------------------------------------------------------
    # SELF-HEALING SCHEMA MIGRATION
    # ------------------------------------------------------------------
    # IMPORTANT: CREATE TABLE IF NOT EXISTS does NOT add columns to an
    # existing database. Older deployments had a products table without
    # `category`, which caused: `table products has no column named category`.
    # Always inspect PRAGMA table_info and add every column required by the
    # current bot. This makes old Railway/Render .db files forward-compatible.
    required_columns = {
        "users": {
            "is_vip": "INTEGER DEFAULT 0", "vip_since": "TEXT",
            "is_banned": "INTEGER DEFAULT 0", "warnings": "INTEGER DEFAULT 0",
        },
        "products": {
            "category": "TEXT DEFAULT ''",
            "panel_name": "TEXT DEFAULT ''",
            "name": "TEXT DEFAULT ''",
            "price_inr": "REAL DEFAULT 0",
            "reseller_price": "REAL DEFAULT 0",
            "stock": "INTEGER DEFAULT 0",
            "apk_link": "TEXT DEFAULT ''",
            "validity": "TEXT DEFAULT 'Lifetime'",
            "device_limit": "TEXT DEFAULT '1 Device'",
            "is_active": "INTEGER DEFAULT 1",
            "external_enabled": "INTEGER DEFAULT 0",
            "external_product_id": "TEXT DEFAULT ''",
            "requires_android_id": "INTEGER DEFAULT 0",
            "external_duration": "TEXT DEFAULT ''",
            "api_provider": "TEXT DEFAULT 'bunty'",
            "is_maintenance": "INTEGER DEFAULT 0",
            "sort_order": "INTEGER DEFAULT 0",
            "panel_sort_order": "INTEGER DEFAULT 0",
        },
        "tickets": {"created_at": "TEXT"},
        "transactions": {
            "purpose": "TEXT DEFAULT 'wallet_topup'",
            "product_id": "INTEGER",
            "quantity": "INTEGER DEFAULT 1",
            "description": "TEXT DEFAULT ''",
            "gateway": "TEXT DEFAULT 'zapupi'",
        },
    }

    for table, columns in required_columns.items():
        try:
            existing = {row[1] for row in c.execute(f"PRAGMA table_info({table})").fetchall()}
            for column, definition in columns.items():
                if column not in existing:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                    logger.info("DB migration: added %s.%s", table, column)
        except sqlite3.Error as exc:
            logger.exception("DB migration failed for table %s: %s", table, exc)
            raise RuntimeError(f"Database migration failed for {table}: {exc}") from exc

    # Normalize NULLs left by older databases.
    c.execute("UPDATE products SET category='' WHERE category IS NULL")
    c.execute("UPDATE products SET panel_name='' WHERE panel_name IS NULL")
    c.execute("UPDATE products SET name='' WHERE name IS NULL")
    c.execute("UPDATE products SET stock=0 WHERE stock IS NULL")
    c.execute("UPDATE products SET external_enabled=0 WHERE external_enabled IS NULL")
    c.execute("UPDATE products SET api_provider='keypanel' WHERE LOWER(COALESCE(api_provider,'')) IN ('panel_store','panelstore')")
    c.execute("UPDATE products SET external_duration=validity WHERE COALESCE(external_duration, '')='' AND validity IS NOT NULL")
    c.execute("UPDATE transactions SET quantity=1 WHERE quantity IS NULL OR quantity < 1")
    c.execute("UPDATE transactions SET purpose='wallet_topup' WHERE purpose IS NULL OR purpose=''" )
    conn.commit()

    # Backfill the API duration for existing products. The purchase code still
    # has additional fallbacks, so old databases remain compatible.
    try:
        c.execute("UPDATE products SET external_duration = validity WHERE COALESCE(external_duration, '') = ''")
    except sqlite3.OperationalError:
        pass
    
    c.execute("SELECT COUNT(*) FROM spin_rewards")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT INTO spin_rewards (amount) VALUES (?)", [(0.0,), (0.2,), (0.5,), (0.0,), (1.0,)])

    default_settings = [
        ('spin_status', 'ON'),
        ('daily_spin_limit', '0.1'),
        ('daily_spin_pool', '0.5'),
        ('daily_spin_spent', '0.0'),
        ('daily_spin_spent_date', ''),
        ('reseller_system_status', 'ON'),
        ('bot_status', 'ON'),
        ('how_to_video', 'None'),
        ('all_files_link', 'None'),
        ('payment_proof_channel', ''),
        ('zapupi_api', ''),
        ('gateway_zapupi_status', 'ON'),
        ('gateway_famgateway_status', 'ON'),
        ('famgateway_api', ''),
        ('admin_menu_color', 'blue'),
        ('panel_menu_color', 'blue'),
        ('product_menu_color', 'blue'),
        ('famgateway_url', 'https://famgateway.in'),
        ('binance_api', ''),
        ('binance_secret', ''),
        ('binance_address', ''),
        ('vip_status', 'OFF'),
        ('reseller_setup_fee', '200.0'),
        ('reseller_min_balance', '500.0'),
        ('migration_done', '0'),
        ('support_telegram', 'https://t.me/rajuconfigyt'),
        ('support_whatsapp', 'https://wa.me/YOUR_NUMBER'),
        ('ui_start_menu', UI_TEXTS['start_menu']),
        ('ui_download_files', UI_TEXTS['download_files']),
        ('ui_lucky_dice_result', UI_TEXTS['lucky_dice_result']),
        ('ui_vip_menu', UI_TEXTS['vip_menu']),
        ('ui_add_balance_menu', UI_TEXTS['add_balance_menu']),
        ('external_api_url', 'https://adminpanels.shop/api/reseller_v1.php'),
        ('external_api_key', ''),
        ('external_master_key', ''),
        ('keypanel_api_url', 'https://keypanel.shop/reseller_gateway.php'),
        ('keypanel_master_key', ''),
        ('bunty_balance_alert_threshold', '0'),
        ('bunty_balance_last_alert', '0'),
        ('heroic_slot_1_name', 'One ID'), ('heroic_slot_1_price', '0'),
        ('heroic_slot_2_name', 'Two ID'), ('heroic_slot_2_price', '0'),
        ('heroic_slot_3_name', 'Three ID'), ('heroic_slot_3_price', '0'),
        ('heroic_slot_4_name', 'Four ID'), ('heroic_slot_4_price', '0'),
        ('heroic_slot_1_emoji', '🆔'), ('heroic_slot_2_emoji', '🆔'),
        ('heroic_slot_3_emoji', '🆔'), ('heroic_slot_4_emoji', '🆔'),
    ]
    for slot, emoji_id in DEFAULT_EMOJIS.items():
        default_settings.append((f"emoji_{slot}", emoji_id))
    
    for key, val in default_settings:
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
    c.execute("DELETE FROM settings WHERE key IN ('panel_store_api_url','panel_store_api_key')")
    c.execute("UPDATE settings SET value=? WHERE key='keypanel_api_url' AND value IN (?, ?)",
             ('https://keypanel.shop/reseller_gateway.php', 'https://www.keypanel.shop/api/v1/index.php', 'https://www.panelstore.shop/api/v1/index.php'))

    conn.commit()
    conn.close()

def migrate_categories() -> None:
    done = get_setting("migration_done", "0")
    
    # ALWAYS force update emojis and UI texts regardless of migration status
    logger.info("Forcing emoji and UI text updates...")
    
    # Update all emoji settings
    for slot, emoji_id in DEFAULT_EMOJIS.items():
        set_setting(f"emoji_{slot}", emoji_id)
    
    # Force update UI texts
    set_setting("ui_start_menu", UI_TEXTS['start_menu'])
    set_setting("ui_add_balance_menu", UI_TEXTS['add_balance_menu'])
    set_setting("ui_download_files", UI_TEXTS['download_files'])
    set_setting("ui_lucky_dice_result", UI_TEXTS['lucky_dice_result'])
    set_setting("ui_vip_menu", UI_TEXTS['vip_menu'])
    logger.info("UI texts and emojis updated with new placeholders and IDs.")
    
    set_setting("migration_done", "1")
    logger.info("Category migration complete.")

# ==============================================================================
# 5. MIDDLEWARES & SECURITY
# ==============================================================================
async def hacker_loading(message: Message, text: str = "Decrypting Data") -> Message:
    msg = await message.answer(f"⚡ {text}\n[□□□] 0%")
    await asyncio.sleep(0.3)
    await msg.edit_text(f"⚡ {text}\n[■□□] 33%", parse_mode='HTML')
    await asyncio.sleep(0.3)
    await msg.edit_text(f"⚡ {text}\n[■■□] 66%", parse_mode='HTML')
    await asyncio.sleep(0.3)
    await msg.edit_text(f"⚡ {text}\n[■■■] 100%", parse_mode='HTML')
    return msg

class GlobalSecurityMiddleware(BaseMiddleware):
    def __init__(self):
        super().__init__()
        self.last_action_times = {}

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id
        now = time.time()
        if user_id in self.last_action_times:
            if now - self.last_action_times[user_id] < 0.3:
                return
        self.last_action_times[user_id] = now

        if user_id != ADMIN_ID:
            user_info = db_query("SELECT is_banned FROM users WHERE user_id=?", (user_id,), fetchone=True)
            if user_info and user_info[0] == 1:
                msg = "🚫 <b>ACCESS DENIED</b>\nYou have been banned from using this bot.\nContact support if you think this is a mistake."
                if isinstance(event, Message): await event.answer(msg)
                elif isinstance(event, CallbackQuery): await event.answer(msg, show_alert=True)
                return
                
            status_check = db_query("SELECT value FROM settings WHERE key='bot_status'", fetchone=True)
            status = status_check[0] if status_check else 'ON'
            if status == 'OFF':
                msg = "⚠️ <b>Store Maintenance</b>\n\nThe store is currently offline for updates. Please check back later!"
                if isinstance(event, Message): await event.answer(msg)
                elif isinstance(event, CallbackQuery): await event.answer("⚠️ Bot is currently OFF for Maintenance.", show_alert=True)
                return
                
        return await handler(event, data)

dp.message.middleware(GlobalSecurityMiddleware())
dp.callback_query.middleware(GlobalSecurityMiddleware())

# ==============================================================================
# 6. FSM STATES
# ==============================================================================
class UserStates(StatesGroup):
    wait_for_ticket = State()
    wait_for_redeem = State()
    wait_for_crypto_txid = State()
    wait_for_crypto_amount = State()   # User enters USDT amount
    wait_for_crypto_ss = State()       # User sends payment screenshot
    custom_amount_input = State()

class AdminStates(StatesGroup):
    add_prod_category = State()
    add_prod_panel_name = State()
    add_prod_name = State()
    add_prod_validity = State()
    add_prod_device_limit = State()
    add_prod_price = State()
    add_prod_reseller_price = State()
    add_prod_apk = State()
    add_prod_keys = State()
    
    edit_prod_field = State()
    wait_for_new_value = State()
    wait_for_add_keys = State()
    wait_for_delete_key = State()
    
    broadcast_msg = State()
    add_coupon_code = State()
    add_coupon_amount = State()
    add_coupon_uses = State()
    
    wait_for_zapupi_api = State()
    wait_for_binance_api = State()
    wait_for_binance_secret = State()
    wait_for_binance_address = State()
    
    ticket_reply_msg = State()
    reseller_manage_id = State()
    manage_target_user = State()
    wait_for_add_money = State()
    wait_for_minus_money = State()
    wait_for_warning = State()
    
    spin_add_reward = State()
    spin_set_limit = State()
    wait_for_howto_video = State()
    wait_for_all_files_link = State()
    wait_for_payment_proof_channel = State()
    
    edit_ui_text = State()
    edit_reseller_price = State()
    wait_for_reseller_setup_fee = State()
    wait_for_reseller_min_balance = State()
    confirm_ban = State()
    
    wait_for_support_telegram = State()
    wait_for_support_whatsapp = State()
    wait_for_category_emoji = State()
    wait_for_panel_emoji_id = State()
    wait_for_emoji_slot = State()
    wait_for_ext_url = State()
    wait_for_ext_key = State()
    wait_for_ext_master = State()
    wait_for_keypanel_url = State()
    wait_for_keypanel_master = State()
    wait_for_heroic_slot_name = State()
    wait_for_heroic_slot_price = State()
    wait_for_heroic_slot_emoji = State()
    wait_for_heroic_delivery = State()
    add_prod_external = State()
    add_prod_external_product_id = State()
    add_prod_external_duration = State()
    
    # Category Management States
    add_category_name = State()
    edit_category_name = State()
    edit_category_pos = State()
    edit_category_emoji = State()
    
    # Panel (Main Title) Position & Title States
    edit_panel_pos = State()
    edit_panel_title = State()
    edit_prod_pos = State()
    wait_for_duration_emoji = State()
    wait_for_search_product = State()
    quick_stock_qty = State()  # For quick stock add from main admin panel
    quick_add_product = State()
    bulk_update_link = State()

# ==============================================================================
# 7. KEYBOARDS & EMOJI RESOLVERS
# ==============================================================================
DEFAULT_DURATION_EMOJIS = {
    "1 hour": "⏱️", "2 hour": "⏱️", "3 hour": "⏱️", "4 hour": "⏱️",
    "6 hour": "⏳", "8 hour": "⏳", "12 hour": "⏳", "24 hour": "⚡",
    "1 day": "⚡", "2 day": "⚡", "3 day": "🌟", "4 day": "🌟",
    "5 day": "🌟", "7 day": "🔥", "10 day": "🔥", "14 day": "💎",
    "15 day": "💎", "20 day": "👑", "21 day": "👑", "28 day": "👑",
    "30 day": "👑", "45 day": "🏆", "60 day": "🏆", "90 day": "🏆",
}

def extract_emoji_or_id(m: Message) -> tuple:
    """Extracts (emoji_char, custom_emoji_id) from message."""
    raw_text = (m.text or "").strip()
    custom_id = ""
    emoji_char = ""
    if m.entities:
        for ent in m.entities:
            if ent.type == "custom_emoji" and ent.custom_emoji_id:
                custom_id = str(ent.custom_emoji_id)
                emoji_char = raw_text[ent.offset : ent.offset + ent.length]
                break
    if not custom_id and raw_text.isdigit():
        custom_id = raw_text
    elif not emoji_char:
        emoji_char = raw_text
    return emoji_char, custom_id

def get_duration_emoji(package_name: str) -> str:
    cleaned = str(package_name or "").strip()
    stored = get_setting(f"dur_emoji_{cleaned.lower()}", "")
    if stored:
        return stored
    lower = cleaned.lower()
    for key, emo in DEFAULT_DURATION_EMOJIS.items():
        if key in lower:
            return emo
    if "hour" in lower or "hr" in lower:
        return "⏱️"
    if "day" in lower or "d" in lower:
        return "⚡"
    if "vip" in lower or "month" in lower:
        return "👑"
    return "⚡"

def get_category_emoji(category: str) -> str:
    cid = get_setting(f"cat_custom_id_{category}", "")
    if cid and cid.isdigit():
        return cid
    stored = get_setting(f"cat_emoji_{category}", "")
    if stored and stored.isdigit():
        return stored
    try:
        cat_row = db_query("SELECT emoji_id FROM categories WHERE name=?", (category,), fetchone=True)
        if cat_row and cat_row[0] and str(cat_row[0]).isdigit():
            return str(cat_row[0])
    except Exception:
        pass
    slot_map = {
        "ANDROID NON ROOT PANEL": "category_android_non_root",
        "ANDROID ROOT PANEL": "category_android_root",
        "IPHONE PANEL": "category_iphone",
        "PC PANEL": "category_pc",
    }
    slot = slot_map.get(category)
    if slot:
        return get_emoji_icon(slot, DEFAULT_EMOJIS.get(slot, ""))
    return get_emoji_icon("product_store", "")

def get_category_display_icon(category: str) -> str:
    """Returns a unicode emoji or default icon for category button label."""
    stored = get_setting(f"cat_emoji_{category}", "")
    if stored and not stored.isdigit():
        return stored
    cat_upper = category.upper()
    if "NON ROOT" in cat_upper: return "⚡"
    if "ROOT" in cat_upper: return "🛡️"
    if "IPHONE" in cat_upper or "IOS" in cat_upper: return "🍎"
    if "PC" in cat_upper: return "💻"
    if "CARROM" in cat_upper: return "🎯"
    if "BGMI" in cat_upper or "PUBG" in cat_upper: return "🔫"
    if "GUILD" in cat_upper or "BOT" in cat_upper: return "🤖"
    return "📦"

def get_panel_display_icon(panel_name: str) -> str:
    """Returns a unicode emoji for panel button label."""
    stored = get_setting(f"panel_emoji_{panel_name}", "")
    if stored and not stored.isdigit():
        return stored
    return "🎮"

def get_panel_emoji(panel_name: str) -> str:
    cid = get_setting(f"panel_custom_id_{panel_name}", "")
    if cid and cid.isdigit():
        return cid
    stored = get_setting(f"panel_emoji_{panel_name}", "")
    if stored and stored.isdigit():
        return stored
    return get_emoji_icon("product_store")

def contact_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Verify Contact", request_contact=True)]], 
        resize_keyboard=True, 
        one_time_keyboard=True
    )

def main_menu_kb(user_id: Optional[int] = None) -> InlineKeyboardMarkup:
    status_check = db_query("SELECT value FROM settings WHERE key='reseller_system_status'", fetchone=True)
    sys_status = status_check[0] if status_check else 'ON'
    
    is_reseller = False
    if user_id:
        user_check = db_query("SELECT is_reseller FROM users WHERE user_id=?", (user_id,), fetchone=True)
        if user_check:
            is_reseller = bool(user_check[0])

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="Product Store", callback_data="menu_shop",
            icon_custom_emoji_id=get_emoji_icon("product_store"),
            style="success"
        )
    ])
    # Public Top Buyers leaderboard is intentionally the second menu row.
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="Top Buyers Leaderboard", callback_data="menu_top_buyers",
            icon_custom_emoji_id=get_emoji_icon("global_stats"),
            style="success"
        )
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="My Profile", callback_data="menu_profile",
            icon_custom_emoji_id=get_emoji_icon("profile"),
            style="success"
        ),
        InlineKeyboardButton(
            text="Add Balance", callback_data="menu_add_balance",
            icon_custom_emoji_id=get_emoji_icon("add_balance"),
            style="success"
        )
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="All History", callback_data="menu_orders",
            icon_custom_emoji_id=get_emoji_icon("history"),
            style="success"
        ),
        InlineKeyboardButton(
            text="Referral", callback_data="menu_referral",
            icon_custom_emoji_id=get_emoji_icon("referral"),
            style="success"
        )
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="Tutorials", callback_data="menu_how_to",
            icon_custom_emoji_id=get_emoji_icon("tutorial"),
            style="success"
        ),
        InlineKeyboardButton(
            text="Support", callback_data="menu_support",
            icon_custom_emoji_id=get_emoji_icon("support"),
            style="success"
        )
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="Ludo Spin", callback_data="menu_spin_landing",
            icon_custom_emoji_id=get_emoji_icon("ludo_spin"),
            style="success"
        ),
        InlineKeyboardButton(
            text="Download Files", callback_data="menu_all_files",
            icon_custom_emoji_id=get_emoji_icon("download"),
            style="success"
        )
    ])
    # Keep reseller controls in the menu, but never show the VIP Club button.
    if sys_status == 'ON' or is_reseller:
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text="Reseller Panel", callback_data="menu_reseller_dash",
                icon_custom_emoji_id=get_emoji_icon("reseller"),
                style="success"
            )
        ])

    # Payment Proof is deliberately the LAST button in the main menu.
    proof_channel = (get_setting("payment_proof_channel", "") or "").strip()
    if proof_channel and proof_channel.lower() not in ("none", "off", "disabled"):
        proof_link = proof_channel
        if proof_link.startswith("@"):
            proof_link = "https://t.me/" + proof_link[1:]
        elif not proof_link.startswith(("http://", "https://")):
            proof_link = "https://t.me/" + proof_link.lstrip("@")
        proof_button = InlineKeyboardButton(
            text="Payment Proof", url=proof_link,
            icon_custom_emoji_id=get_emoji_icon("check_icon"),
            style="success"
        )
    else:
        proof_button = InlineKeyboardButton(
            text="Payment Proof", callback_data="menu_payment_proof",
            icon_custom_emoji_id=get_emoji_icon("check_icon"),
            style="success"
        )
    kb.inline_keyboard.append([proof_button])

    return kb

def back_kb(callback: str = "back_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="BACK", callback_data=callback,
                icon_custom_emoji_id=get_emoji_icon("back"),
                style="danger"
            )
        ]]
    )

def admin_kb() -> InlineKeyboardMarkup:
    status = db_query("SELECT value FROM settings WHERE key='bot_status'", fetchone=True)
    status_val = status[0] if status else 'ON'
    vip_status = db_query("SELECT value FROM settings WHERE key='vip_status'", fetchone=True)
    vip_val = vip_status[0] if vip_status else 'OFF'
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Bot Statistics", callback_data="admin_view_stats", icon_custom_emoji_id=get_emoji_icon("global_stats"), style=admin_menu_style())],
        [InlineKeyboardButton(text="User Control Panel", callback_data="admin_user_control_start", icon_custom_emoji_id=get_emoji_icon("profile"), style=admin_menu_style())],
        [
            InlineKeyboardButton(text="Add Product", callback_data="admin_add_prod", icon_custom_emoji_id=get_emoji_icon("product_store"), style=admin_menu_style()),
            InlineKeyboardButton(text="⚡ Quick Add", callback_data="admin_quick_add_product", icon_custom_emoji_id=get_emoji_icon("product_store"), style=admin_menu_style()),
            InlineKeyboardButton(text="Manage Products", callback_data="admin_manage_prods", icon_custom_emoji_id=get_emoji_icon("product_store"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="Add Stock", callback_data="admin_quick_stock", icon_custom_emoji_id=get_emoji_icon("add_balance"), style=admin_menu_style()),
            InlineKeyboardButton(text="Quick Add Key", callback_data="admin_quick_add_key", icon_custom_emoji_id=get_emoji_icon("add_balance"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="Product Maintenance", callback_data="admin_prod_maint", icon_custom_emoji_id=get_emoji_icon("shield_icon"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="Reseller Mgmt", callback_data="admin_reseller_menu", icon_custom_emoji_id=get_emoji_icon("reseller"), style=admin_menu_style()),
            InlineKeyboardButton(text="Spin Settings", callback_data="admin_spin_menu", icon_custom_emoji_id=get_emoji_icon("ludo_spin"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="Create Coupon", callback_data="admin_create_coupon", icon_custom_emoji_id=get_emoji_icon("redeem_icon"), style=admin_menu_style()),
            InlineKeyboardButton(text="Broadcast", callback_data="admin_broadcast_btn", icon_custom_emoji_id=get_emoji_icon("telegram"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="View Tickets", callback_data="admin_view_tickets", icon_custom_emoji_id=get_emoji_icon("support"), style=admin_menu_style()),
            InlineKeyboardButton(text="Tutorial Video", callback_data="admin_set_video", icon_custom_emoji_id=get_emoji_icon("tutorial"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="All Files Link", callback_data="admin_set_all_files", icon_custom_emoji_id=get_emoji_icon("download"), style=admin_menu_style()),
            InlineKeyboardButton(text="Payment Proof Channel", callback_data="admin_set_payment_proof", icon_custom_emoji_id=get_emoji_icon("check_icon"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="Edit All Emojis", callback_data="admin_edit_emojis", icon_custom_emoji_id=get_emoji_icon("info_icon"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="ZapUPI Setup", callback_data="admin_setup_zapupi", icon_custom_emoji_id=get_emoji_icon("upi"), style=admin_menu_style()),
            InlineKeyboardButton(text="Binance Setup", callback_data="admin_setup_binance", icon_custom_emoji_id=get_emoji_icon("binance"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="Bunty API", callback_data="admin_setup_external_api", icon_custom_emoji_id=get_emoji_icon("info_icon"), style=admin_menu_style()),
            InlineKeyboardButton(text="FFPanel Key API", callback_data="admin_setup_keypanel", icon_custom_emoji_id=get_emoji_icon("info_icon"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="🔧 API Control / Bulk Update", callback_data="admin_api_control", icon_custom_emoji_id=get_emoji_icon("info_icon"), style=admin_menu_style()),
            InlineKeyboardButton(text="💜 FamGateway", callback_data="admin_setup_famgateway", icon_custom_emoji_id=get_emoji_icon("money_icon"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="🧪 Full System/API Check", callback_data="admin_full_system_check", icon_custom_emoji_id=get_emoji_icon("shield_icon"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="🎨 Menu Colors", callback_data="admin_menu_colors", icon_custom_emoji_id=get_emoji_icon("info_icon"), style=admin_menu_style()),
            InlineKeyboardButton(text="💳 Gateway Control", callback_data="admin_gateway_control", icon_custom_emoji_id=get_emoji_icon("upi"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="💸 Bulk Price Adjust", callback_data="admin_bulk_price_menu", icon_custom_emoji_id=get_emoji_icon("money_icon"), style=admin_menu_style()),
            InlineKeyboardButton(text="🧹 Zero All User Balances", callback_data="admin_zero_all_balances", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="danger")
        ],
        [
            InlineKeyboardButton(text="🏆 Reset Top Sellers", callback_data="admin_reset_top_sellers", icon_custom_emoji_id=get_emoji_icon("global_stats"), style="danger")
        ],
        [
            InlineKeyboardButton(text="Edit UI Texts", callback_data="admin_edit_ui_menu", icon_custom_emoji_id=get_emoji_icon("info_icon"), style=admin_menu_style()),
            InlineKeyboardButton(text="Edit Reseller Price", callback_data="admin_edit_reseller_price", icon_custom_emoji_id=get_emoji_icon("money_icon"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="Reseller Fee", callback_data="admin_set_reseller_fee", icon_custom_emoji_id=get_emoji_icon("money_icon"), style=admin_menu_style()),
            InlineKeyboardButton(text="Min Balance", callback_data="admin_set_reseller_min", icon_custom_emoji_id=get_emoji_icon("money_icon"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="Manage Categories", callback_data="admin_manage_categories", icon_custom_emoji_id=get_emoji_icon("info_icon"), style=admin_menu_style()),
            InlineKeyboardButton(text="Product Titles & Positions", callback_data="admin_panel_positions_menu", icon_custom_emoji_id=get_emoji_icon("grid_id"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="Set Support Links", callback_data="admin_set_support_links", icon_custom_emoji_id=get_emoji_icon("support"), style=admin_menu_style()),
            InlineKeyboardButton(text="Set Category Emojis", callback_data="admin_set_category_emojis", icon_custom_emoji_id=get_emoji_icon("info_icon"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(text="Set Panel Emojis", callback_data="admin_set_panel_emojis", icon_custom_emoji_id=get_emoji_icon("info_icon"), style=admin_menu_style()),
            InlineKeyboardButton(text="Set Duration Emojis", callback_data="admin_set_duration_emojis", icon_custom_emoji_id=get_emoji_icon("info_icon"), style=admin_menu_style())
        ],
        [
            InlineKeyboardButton(
                text=f"Bot Status: {status_val} {'🟢' if status_val == 'ON' else '🔴'}",
                callback_data="admin_toggle_bot",
                icon_custom_emoji_id=get_emoji_icon("check_icon"),
                style=admin_menu_style() if status_val == 'ON' else "danger"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"VIP System: {vip_val} {'🟢' if vip_val == 'ON' else '🔴'}",
                callback_data="admin_toggle_vip_sys",
                icon_custom_emoji_id=get_emoji_icon("vip"),
                style=admin_menu_style() if vip_val == 'ON' else "danger"
            )
        ]
    ])
    return kb

def admin_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Back to Admin", callback_data="admin_panel_back",
            icon_custom_emoji_id=get_emoji_icon("back"),
            style="danger"
        )
    ]])

# ==============================================================================
# 8. NOTIFICATIONS
# ==============================================================================
async def send_advanced_notification(user_id: int, notif_type: str, amount: float, product: str = None, key: str = None, gateway: str = "ZapUPI") -> None:
    user_info = db_query("SELECT first_name, phone, username, is_reseller, is_vip FROM users WHERE user_id=?", (user_id,), fetchone=True)
    
    name = user_info[0] if user_info else "Unknown"
    phone = user_info[1] if user_info and user_info[1] else "Not Provided"
    username = f"@{user_info[2]}" if user_info and user_info[2] else "None"
    
    tags = []
    if user_info and user_info[3]: tags.append("👑 Reseller")
    if user_info and user_info[4]: tags.append("🌟 VIP")
    tag_str = " | ".join(tags) if tags else "👤 Regular"
        
    time_now = datetime.now().strftime("%d-%m-%Y %I:%M %p")
    
    if notif_type == "ORDER":
        title = "🛒 <b>NEW ORDER PROCESSED!</b> 🛒"
        details = (f"📦 <b>Product:</b> {product}\n🔑 <b>Key:</b> <code>{key}</code>\n💰 <b>Amount Paid:</b> ₹{amount:.2f}\n📅 <b>Time:</b> {time_now}")
    else:
        title = "💰 <b>NEW WALLET DEPOSIT!</b> 💰"
        details = (f"💵 <b>Amount Added:</b> ₹{amount:.2f}\n🧾 <b>Gateway:</b> {gateway}\n🆔 <b>Reference:</b> <code>{product}</code>\n📅 <b>Time:</b> {time_now}")

    msg = f"{title}\n━━━━━━━━━━━━━━━━━━\n👤 <b>Name:</b> {name}\n🆔 <b>User ID:</b> <code>{user_id}</code>\n📱 <b>Phone:</b> {phone}\n🔗 <b>Username:</b> {username}\n🏷 <b>Status:</b> {tag_str}\n━━━━━━━━━━━━━━━━━━\n{details}"
    for aid in ADMIN_IDS:
        try: 
            await bot.send_message(aid, msg, parse_mode='HTML')
        except Exception as e: 
            logger.error(f"Failed to send admin notification to {aid}: {e}")

async def send_payment_proof_channel(user_id: int, product: str, amount: float, order_id: str = "", api_response: Optional[dict] = None) -> None:
    """Post a public purchase proof without ever exposing the delivered key."""
    channel = (get_setting("payment_proof_channel", "") or "").strip()
    if not channel or channel.lower() in ("none", "off", "disabled"):
        return

    user = db_query("SELECT first_name FROM users WHERE user_id=?", (user_id,), fetchone=True)
    # Public payment proof intentionally shows the customer's display name only.
    user_label = (user[0] if user and user[0] else "Customer").replace("<", "&lt;").replace(">", "&gt;")

    # Public proof deliberately contains no customer secret or API credential.
    verified_badge = get_emoji("check_icon")
    premium_badge = get_emoji("vip")
    text = (
        f"{premium_badge} <b>PREMIUM PAYMENT PROOF</b> {verified_badge}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Product:</b> {product}\n"
        f"💰 <b>Amount Paid:</b> {fmt_curr(amount)}\n"
        f"👤 <b>Customer:</b> {user_label}\n"
    )
    if order_id:
        text += f"🧾 <b>Order ID:</b> <code>{order_id}</code>\n"
    if api_response:
        if api_response.get("duration"):
            text += f"⏳ <b>Duration:</b> {api_response['duration']}\n"
        if api_response.get("expires_at"):
            text += f"⏰ <b>Expires:</b> {api_response['expires_at']}\n"
    text += (
        "━━━━━━━━━━━━━━━━━━\n"
        f"{verified_badge} <b>Payment Verified • Order Delivered</b> {premium_badge}\n"
        "\n"
        "🔑 <b>Key:</b> ____ <i>(Hide)</i>\n"
        "✓ <b>Bought via:-</b> @" + (BOT_USERNAME or "").lstrip("@") + "\n"
        "🛒 <b>Buy:-</b> @" + (BOT_USERNAME or "").lstrip("@")
    )

    # Public channels can be addressed as @username or https://t.me/username.
    target = channel
    if target.startswith("https://t.me/") or target.startswith("http://t.me/"):
        tail = target.rstrip("/").split("/")[-1]
        if tail and not tail.startswith("+"):
            target = "@" + tail.lstrip("@")
        else:
            logger.warning("Payment proof channel must be a public channel username, not a private invite link.")
            return
    elif target.startswith("t.me/"):
        tail = target.rstrip("/").split("/")[-1]
        if tail and not tail.startswith("+"):
            target = "@" + tail.lstrip("@")
        else:
            return
    elif not target.startswith("@"):
        target = "@" + target

    try:
        await bot.send_message(target, text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as exc:
        logger.warning("Payment proof channel post failed for %s: %s", target, exc)

def _clean_famgateway_api_key(raw: str) -> str:
    """Normalize a FamGateway API key pasted from a dashboard/message."""
    value = str(raw or "").strip().strip('"\'`')
    prefixes = ("Bearer ", "bearer ", "X-Api-Key:", "x-api-key:", "X-API-KEY:", "x-api-key=", "api_key=", "API_KEY=")
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if value.startswith(prefix):
                value = value[len(prefix):].strip().strip('"\'`')
                changed = True
                break
    return value


def _famgateway_api_key() -> str:
    """Read DB key first, then FAMGATEWAY_API_KEY environment variable."""
    stored = get_setting("famgateway_api", "")
    return _clean_famgateway_api_key(stored or os.getenv("FAMGATEWAY_API_KEY", ""))


def _famgateway_base_url() -> str:
    stored = get_setting("famgateway_url", "")
    return (stored or os.getenv("FAMGATEWAY_URL", "https://famgateway.in") or "https://famgateway.in").strip().rstrip("/")


def _famgateway_normalize_status(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    raw = data.get("status") or nested.get("status") or data.get("payment_status") or nested.get("payment_status")
    return str(raw or "").strip().lower()


def _famgateway_error_message(data: Any, http_status: int) -> str:
    if isinstance(data, dict):
        nested = data.get("data") if isinstance(data.get("data"), dict) else {}
        msg = data.get("message") or data.get("error") or nested.get("message")
        if msg:
            return str(msg)
    if http_status == 401:
        return "FamGateway rejected the API key (HTTP 401). Replace it with the active API Key from your FamGateway dashboard."
    if http_status == 403:
        return "FamGateway account/API access is suspended or forbidden (HTTP 403)."
    if http_status == 404:
        return "FamGateway endpoint or order was not found (HTTP 404)."
    if http_status == 408:
        return "FamGateway payment session has expired (HTTP 408)."
    if http_status == 429:
        return "FamGateway rate limit reached (HTTP 429). Please wait a few seconds."
    return f"FamGateway HTTP {http_status}."


async def _famgateway_request(method: str, url: str, api_key: str, *, json_payload: Optional[dict] = None, params: Optional[dict] = None, timeout: int = 20) -> Tuple[int, Any]:
    """Use documented FamGateway authentication; alternate auth is tried only after HTTP 401."""
    api_key = _clean_famgateway_api_key(api_key)
    base_headers = {"Accept": "application/json"}
    if json_payload is not None:
        base_headers["Content-Type"] = "application/json"
    attempts = [
        ("header", {**base_headers, "X-Api-Key": api_key}, dict(json_payload) if json_payload is not None else None, dict(params or {})),
        ("bearer", {**base_headers, "Authorization": f"Bearer {api_key}"}, dict(json_payload) if json_payload is not None else None, dict(params or {})),
    ]
    if json_payload is not None:
        body_with_key = dict(json_payload)
        body_with_key["api_key"] = api_key
        attempts.append(("body", base_headers.copy(), body_with_key, dict(params or {})))
    query_params = dict(params or {})
    query_params["api_key"] = api_key
    attempts.append(("query", base_headers.copy(), dict(json_payload) if json_payload is not None else None, query_params))

    async with aiohttp.ClientSession() as session:
        last_status, last_data = 0, {}
        for index, (_mode, headers, body, request_params) in enumerate(attempts):
            try:
                async with session.request(method.upper(), url, json=body if body is not None else None, params=request_params or None, headers=headers, timeout=timeout) as resp:
                    raw_text = await resp.text()
                    try:
                        data = json.loads(raw_text) if raw_text else {}
                    except Exception:
                        data = {"raw_text": raw_text[:1000]}
                    last_status, last_data = resp.status, data
                    if resp.status != 401 or index == len(attempts) - 1:
                        return resp.status, data
            except asyncio.TimeoutError:
                return 408, {"status": "error", "message": "FamGateway request timed out."}
            except aiohttp.ClientError as exc:
                return 0, {"status": "error", "message": f"FamGateway connection error: {exc}"}
            except Exception as exc:
                logger.exception("FamGateway request failed")
                return 0, {"status": "error", "message": f"FamGateway connection error: {exc}"}
        return last_status, last_data


async def famgateway_create_order(user_id: int, amount: float, description: str = "Wallet Topup") -> dict:
    api_key = _famgateway_api_key()
    base_url = _famgateway_base_url()
    if not api_key:
        return {"status": "error", "message": "FamGateway API key is not configured. Set FAMGATEWAY_API_KEY or configure it in Admin → FamGateway."}
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return {"status": "error", "message": "Invalid payment amount."}
    if amount <= 0:
        return {"status": "error", "message": "Payment amount must be greater than zero."}
    user = db_query("SELECT first_name, phone FROM users WHERE user_id=?", (user_id,), fetchone=True)
    customer_name = (user[0] if user and user[0] else "Customer")
    customer_phone = (user[1] if user and user[1] else "")
    payload = {"amount": amount, "customer_name": str(customer_name)[:100], "customer_phone": str(customer_phone)[:20], "description": str(description or "Wallet Topup")[:120]}
    try:
        http_status, data = await _famgateway_request("POST", f"{base_url}/api/create-order", api_key, json_payload=payload, timeout=20)
        if http_status < 200 or http_status >= 300:
            return {"status": "error", "message": _famgateway_error_message(data, http_status), "http_status": http_status, "raw": data}
        if not isinstance(data, dict):
            return {"status": "error", "message": "FamGateway returned an invalid response."}
        result = data.get("data") if isinstance(data.get("data"), dict) else data
        status = _famgateway_normalize_status(data)
        if status not in {"success", "created", "pending"}:
            return {"status": "error", "message": _famgateway_error_message(data, http_status), "raw": data}
        gateway_order_id = result.get("order_id") or data.get("order_id")
        payment_url = result.get("checkout_url") or result.get("payment_url") or result.get("qr_url")
        if not gateway_order_id:
            return {"status": "error", "message": "FamGateway did not return an order_id.", "raw": data}
        if not payment_url:
            return {"status": "error", "message": "FamGateway did not return a checkout/QR URL.", "raw": data}
        return {**result, "status": "success", "order_id": str(gateway_order_id),
                "checkout_url": result.get("checkout_url") or payment_url,
                "payment_url": result.get("payment_url") or payment_url,
                "qr_url": result.get("qr_url") or "",
                "upi_intent": result.get("upi_intent") or "",
                "payable_amount": result.get("payable_amount") or result.get("amount") or amount}
    except Exception as exc:
        logger.exception("FamGateway create-order failed")
        return {"status": "error", "message": f"FamGateway connection error: {exc}"}


async def famgateway_check_order(order_id: str) -> dict:
    """Authoritative FamGateway server-side status check."""
    api_key = _famgateway_api_key()
    base_url = _famgateway_base_url()
    order_id = str(order_id or "").strip()
    if not api_key:
        return {"status": "error", "message": "FamGateway API key is not configured."}
    if not order_id:
        return {"status": "error", "message": "FamGateway order_id is missing."}
    try:
        http_status, data = await _famgateway_request("GET", f"{base_url}/api/verify-order.php", api_key, params={"order_id": order_id}, timeout=15)
        if http_status < 200 or http_status >= 300:
            return {"status": "error", "message": _famgateway_error_message(data, http_status), "http_status": http_status, "raw": data}
        if not isinstance(data, dict):
            return {"status": "error", "message": "Invalid FamGateway verification response."}
        status = _famgateway_normalize_status(data)
        nested = data.get("data") if isinstance(data.get("data"), dict) else data
        if status in {"success", "paid", "completed", "confirmed"}:
            return {"status": "success", "data": nested, "raw": data}
        if status in {"pending", "processing", "initiated", "created", "awaiting_payment"}:
            return {"status": "pending", "data": nested, "raw": data}
        if status in {"expired", "failed", "cancelled", "canceled", "error", "not_found"}:
            return {"status": "expired" if status == "expired" else "failed", "message": data.get("message", status), "raw": data}
        return {"status": "pending", "message": data.get("message", "Payment status is not confirmed yet."), "raw": data}
    except Exception as exc:
        logger.warning("FamGateway verify failed for %s: %s", order_id, exc)
        return {"status": "error", "message": str(exc)}

# ==============================================================================
# 9. PAYMENT VERIFIER
# ==============================================================================
async def run_payment_verification(user_id: int, order_id: str, reply_target: Any) -> None:
    lock = _payment_verify_locks.get(str(order_id))
    if lock is None:
        lock = asyncio.Lock()
        _payment_verify_locks[str(order_id)] = lock
    if lock.locked():
        if isinstance(reply_target, CallbackQuery):
            return await reply_target.answer("⏳ This payment is already being checked. Please wait 3 seconds.", show_alert=True)
        return
    async with lock:
        return await _run_payment_verification_locked(user_id, order_id, reply_target)

async def _run_payment_verification_locked(user_id: int, order_id: str, reply_target: Any) -> None:
    txn = db_query(
        "SELECT amount_inr, status, timestamp, purpose, product_id, quantity, description, gateway "
        "FROM transactions WHERE order_id=?",
        (order_id,), fetchone=True
    )
    if not txn:
        err = "❌ Invalid or Fake Order ID detected in system!"
        if isinstance(reply_target, CallbackQuery): await reply_target.answer(err, show_alert=True)
        else: await reply_target.answer(err)
        return
        
    txn_gateway = (txn[7] or "zapupi").strip().lower()
    expiry_seconds = 300 if txn_gateway == "famgateway" else 900
    if time.time() - txn[2] > expiry_seconds and txn[1] == 'pending':
        db_query("UPDATE transactions SET status='expired' WHERE order_id=? AND status='pending'", (order_id,))
        window_text = "5-minute" if txn_gateway == "famgateway" else "15-minute"
        err_msg = f"⏳ <b>Payment Timed Out!</b>\nThe {window_text} verification window has expired."
        if isinstance(reply_target, CallbackQuery): await reply_target.message.edit_text(err_msg, reply_markup=back_kb(), parse_mode='HTML')
        else: await reply_target.answer(err_msg, reply_markup=back_kb())
        return

    if txn[1] == 'paid':
        msg = "✅ This payment has already been securely credited to your wallet."
        if isinstance(reply_target, CallbackQuery): await reply_target.answer(msg, show_alert=True)
        else: await reply_target.answer(msg)
        return
    elif txn[1] == 'expired':
        msg = "❌ This order has expired. Please create a new deposit request."
        if isinstance(reply_target, CallbackQuery): await reply_target.answer(msg, show_alert=True)
        else: await reply_target.answer(msg)
        return
        
    gateway = (txn[7] or "zapupi").lower()
    if gateway == "famgateway":
        res_json = await famgateway_check_order(order_id)
        if res_json.get("status") == "success":
            db_query("UPDATE transactions SET status='paid' WHERE order_id=? AND status='pending'", (order_id,))
            claimed = db_query("SELECT status FROM transactions WHERE order_id=?", (order_id,), fetchone=True)
            if not claimed or str(claimed[0]).lower() != "paid":
                return
            db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (txn[0], user_id))
            if txn[3] == "product_purchase" and txn[4]:
                target_message = reply_target.message if isinstance(reply_target, CallbackQuery) else reply_target
                user_row = db_query("SELECT user_id, username, first_name FROM users WHERE user_id=?", (user_id,), fetchone=True)
                proxy = _PurchaseCallProxy(SimpleNamespace(id=user_id, username=(user_row[1] if user_row else None), full_name=(user_row[2] if user_row else "User")), target_message)
                for _ in range(max(1, int(txn[5] or 1))):
                    await _process_single_buy(proxy, int(txn[4]))
            else:
                success_msg = f"🎉 <b>VERIFICATION SUCCESSFUL!</b>\n\n✅ {fmt_curr(txn[0])} has been added to your wallet securely via FamGateway."
                if isinstance(reply_target, CallbackQuery): await reply_target.message.edit_text(success_msg, reply_markup=back_kb(), parse_mode='HTML')
                else: await reply_target.answer(success_msg, reply_markup=back_kb())
                await send_advanced_notification(user_id, "DEPOSIT", txn[0], product=order_id, gateway="FamGateway")
            return
        if res_json.get("status") == "pending":
            msg = "⏳ FamGateway payment is still pending. Please wait a few seconds and try again."
        elif res_json.get("status") == "expired":
            db_query("UPDATE transactions SET status='expired' WHERE order_id=?", (order_id,))
            msg = "❌ This FamGateway payment session has expired. Please create a new payment."
        else:
            msg = f"⚠️ FamGateway error: {res_json.get('message', 'Unable to verify payment')}"
        if isinstance(reply_target, CallbackQuery): await reply_target.answer(msg, show_alert=True)
        else: await reply_target.answer(msg)
        return

    api_key_check = db_query("SELECT value FROM settings WHERE key='zapupi_api'", fetchone=True)
    if not api_key_check:
        msg = "⚠️ Gateway API key missing. Administrator needs to configure it."
        if isinstance(reply_target, CallbackQuery): await reply_target.answer(msg, show_alert=True)
        else: await reply_target.answer(msg)
        return
        
    api_key = api_key_check[0]
    url = "https://pay.zapupi.com/api/order-status"
    payload = {"zap_key": api_key, "order_id": order_id}
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    try: res_json = await resp.json(content_type=None)
                    except Exception: res_json = {}
                        
                    if res_json.get("status") == "success":
                        txn_data = res_json.get("data", {})
                        real_status = txn_data.get("status", "Pending")
                        
                        if real_status == "Success":
                            db_query("UPDATE transactions SET status='paid' WHERE order_id=?", (order_id,))
                            db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (txn[0], user_id))

                            if txn[3] == "product_purchase" and txn[4]:
                                target_message = reply_target.message if isinstance(reply_target, CallbackQuery) else reply_target
                                user_row = db_query("SELECT user_id, username, first_name FROM users WHERE user_id=?", (user_id,), fetchone=True)
                                proxy_user = SimpleNamespace(
                                    id=user_id,
                                    username=(user_row[1] if user_row else None),
                                    full_name=(user_row[2] if user_row else "User")
                                )
                                proxy = _PurchaseCallProxy(proxy_user, target_message)
                                for _ in range(max(1, int(txn[5] or 1))):
                                    await _process_single_buy(proxy, int(txn[4]))
                                log_activity(
                                    user_id, "DIRECT_UPI_PURCHASE_PAID",
                                    f"Amount: {txn[0]}, Product: {txn[4]}, Qty: {txn[5]}, Order: {order_id}"
                                )
                            else:
                                success_msg = f"🎉 <b>VERIFICATION SUCCESSFUL!</b>\n\n✅ {fmt_curr(txn[0])} has been added to your wallet securely."
                                if isinstance(reply_target, CallbackQuery):
                                    await reply_target.message.edit_text(success_msg, reply_markup=back_kb(), parse_mode='HTML')
                                else:
                                    await reply_target.answer(success_msg, reply_markup=back_kb())
                                await send_advanced_notification(user_id, "DEPOSIT", txn[0], product=order_id, gateway="ZapUPI")
                                log_activity(user_id, "DEPOSIT_SUCCESS", f"Amount: {txn[0]}, Gateway: ZapUPI, Order: {order_id}")

                        elif real_status == "Pending":
                            fail_msg = "⏳ Payment is still Pending at the gateway. Please wait a minute and try clicking manual verify again."
                            if isinstance(reply_target, CallbackQuery): await reply_target.answer(fail_msg, show_alert=True)
                            else: await reply_target.answer(fail_msg)
                        else:
                            fail_msg = f"❌ Payment Failed or Cancelled (Gateway Status: {real_status})."
                            if isinstance(reply_target, CallbackQuery): await reply_target.answer(fail_msg, show_alert=True)
                            else: await reply_target.answer(fail_msg)
                    else:
                        err = f"⚠️ Gateway Error: {res_json.get('message', 'Unknown Error')}"
                        if isinstance(reply_target, CallbackQuery): await reply_target.answer(err, show_alert=True)
                        else: await reply_target.answer(err)
                else:
                    err = f"⚠️ Gateway HTTP Error: {resp.status}. Gateway might be down."
                    if isinstance(reply_target, CallbackQuery): await reply_target.answer(err, show_alert=True)
                    else: await reply_target.answer(err)
        except Exception as e:
            logger.error(f"ZapUPI API Error: {str(e)}")
            err = f"⚠️ Unable to connect to Gateway API: Network Issue."
            if isinstance(reply_target, CallbackQuery): await reply_target.answer(err, show_alert=True)
            else: await reply_target.answer(err)

async def auto_verify_task() -> None:
    """Background payment verifier. Never dies because of one bad transaction/API response."""
    while True:
        try:
            await asyncio.sleep(5)
            pending_txns = db_query(
                "SELECT order_id, user_id, amount_inr, timestamp, purpose, product_id, quantity, gateway "
                "FROM transactions WHERE status='pending'",
                fetchall=True
            ) or []

            for txn in pending_txns:
                try:
                    order_id, user_id, amount, ts, purpose, product_id, quantity, gateway = txn
                    gw = (gateway or "zapupi").strip().lower()
                    expiry_seconds = 300 if gw == "famgateway" else 900
                    if time.time() - float(ts or 0) > expiry_seconds:
                        db_query("UPDATE transactions SET status='expired' WHERE order_id=? AND status='pending'", (order_id,))
                        try:
                            await bot.send_message(user_id, f"⏳ <b>Order Expired!</b>\nYour payment window for order <code>{order_id}</code> has timed out.", parse_mode='HTML')
                        except Exception:
                            pass
                        continue

                    paid = False
                    gateway_name = "ZapUPI"

                    if gw == "famgateway":
                        res_json = await famgateway_check_order(order_id)
                        paid = str(res_json.get("status", "")).lower() == "success"
                        gateway_name = "FamGateway"
                        if res_json.get("status") == "expired":
                            db_query("UPDATE transactions SET status='expired' WHERE order_id=? AND status='pending'", (order_id,))
                            continue
                        if res_json.get("status") in {"error", "failed"}:
                            logger.warning("FamGateway pending order %s: %s", order_id, res_json.get("message", "unknown"))
                            continue
                    else:
                        api_key_check = db_query("SELECT value FROM settings WHERE key='zapupi_api'", fetchone=True)
                        if not api_key_check or not api_key_check[0]:
                            continue
                        api_key = api_key_check[0]
                        try:
                            async with aiohttp.ClientSession() as session:
                                async with session.post(
                                    "https://pay.zapupi.com/api/order-status",
                                    json={"zap_key": api_key, "order_id": order_id},
                                    timeout=15
                                ) as resp:
                                    res_json = await resp.json(content_type=None)
                            if resp.status == 200 and isinstance(res_json, dict):
                                top = str(res_json.get("status", "")).strip().lower()
                                data = res_json.get("data") if isinstance(res_json.get("data"), dict) else res_json
                                real_status = str(data.get("status", "")).strip().lower()
                                paid = top in {"success", "ok", "paid"} and real_status in {"success", "paid", "completed", "complete"}
                                if real_status in {"expired", "failed", "cancelled", "canceled"}:
                                    db_query("UPDATE transactions SET status='expired' WHERE order_id=? AND status='pending'", (order_id,))
                                    continue
                            else:
                                logger.warning("ZapUPI status HTTP %s for %s", getattr(resp, "status", "?"), order_id)
                        except Exception as exc:
                            logger.warning("ZapUPI auto-verify request failed for %s: %s", order_id, exc)
                            continue

                    if not paid:
                        continue

                    # Atomic claim: only the worker that changes pending -> paid may fulfill.
                    db_query("UPDATE transactions SET status='paid' WHERE order_id=? AND status='pending'", (order_id,))
                    claimed = db_query("SELECT status FROM transactions WHERE order_id=?", (order_id,), fetchone=True)
                    if not claimed or str(claimed[0]).lower() != "paid":
                        continue

                    # For direct product purchases, do not permanently credit the wallet first.
                    # _process_single_buy charges the wallet, so temporarily credit exactly once.
                    db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))

                    if purpose == "product_purchase" and product_id:
                        target_message = await bot.send_message(
                            user_id,
                            f"✨ <b>{gateway_name.upper()} PAYMENT VERIFIED</b>\n\n"
                            f"🛒 Completing your purchase automatically...\n"
                            f"💰 Paid: <b>{fmt_curr(amount)}</b>",
                            parse_mode='HTML'
                        )
                        user_row = db_query("SELECT username, first_name FROM users WHERE user_id=?", (user_id,), fetchone=True)
                        proxy_user = SimpleNamespace(
                            id=user_id,
                            username=(user_row[0] if user_row else None),
                            full_name=(user_row[1] if user_row else "User")
                        )
                        proxy = _PurchaseCallProxy(proxy_user, target_message)
                        for _ in range(max(1, int(quantity or 1))):
                            try:
                                await _process_single_buy(proxy, int(product_id))
                            except Exception:
                                logger.exception("Auto product fulfillment failed: order=%s product=%s", order_id, product_id)
                                break
                        log_activity(user_id, "DIRECT_PAYMENT_AUTO_VERIFIED", f"Amount: {amount}, Gateway: {gateway_name}, Product: {product_id}, Qty: {quantity}, Order: {order_id}")
                    else:
                        try:
                            await bot.send_message(
                                user_id,
                                f"✨ <b>AUTO-VERIFIED!</b>\n\n✅ Your payment was detected successfully. {fmt_curr(amount)} has been added to your balance!",
                                parse_mode='HTML'
                            )
                        except Exception:
                            pass
                        await send_advanced_notification(user_id, "DEPOSIT", amount, product=order_id, gateway=f"{gateway_name} Auto")
                        log_activity(user_id, "DEPOSIT_AUTO_SUCCESS", f"Amount: {amount}, Gateway: {gateway_name} Auto, Order: {order_id}")

                except Exception:
                    logger.exception("Auto-verifier transaction cycle failed for order=%s", txn[0] if txn else "?")
                    continue

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Auto-verifier daemon recovered from an unexpected error")
            await asyncio.sleep(3)

# ==============================================================================
# 10. ONBOARDING & START
# ==============================================================================
def bot_username_clean() -> str:
    return str(BOT_USERNAME or "").lstrip("@").strip()

def product_deep_link(prod_id: int) -> str:
    username = bot_username_clean()
    return f"https://t.me/{username}?start=product_{int(prod_id)}"

def product_share_url(prod_id: int, title: str = "") -> str:
    deep = product_deep_link(prod_id)
    share_text = f"🛒 {title}" if title else "🛒 Check this product"
    return "https://t.me/share/url?" + urllib.parse.urlencode({"url": deep, "text": share_text})

async def send_product_deep_link_menu(message: Message, prod_id: int) -> bool:
    row = db_query("""
        SELECT id, name, price_inr, stock, reseller_price, validity, device_limit,
               external_enabled, COALESCE(is_maintenance,0), category, panel_name
        FROM products WHERE id=? AND is_active=1
    """, (prod_id,), fetchone=True)
    if not row:
        await message.answer("❌ This product is no longer available.", reply_markup=main_menu_kb(message.from_user.id), parse_mode="HTML")
        return False
    user = db_query("SELECT is_reseller, is_vip FROM users WHERE user_id=?", (message.from_user.id,), fetchone=True)
    is_reseller = bool(user[0]) if user else False
    is_vip = bool(user[1]) if user else False
    _, name, price, stock, reseller_price, validity, device, external_enabled, maintenance, category, panel = row
    base = float(reseller_price or 0) if is_reseller else float(price or 0)
    display = base - (base * VIP_DISCOUNT_PERCENTAGE / 100) if is_vip else base
    if maintenance:
        status = "🟠 Under Maintenance"
    elif external_enabled:
        status = "♾️ API Available"
    elif int(stock or 0) > 0:
        status = f"✅ In Stock: {int(stock)}"
    else:
        status = "❌ Out of Stock"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🛒 Buy {validity} — {fmt_curr(display)}", callback_data=f"buy_{prod_id}", style=product_menu_style())],
        [InlineKeyboardButton(text="🔗 Share Product", url=product_share_url(prod_id, name), style="primary")],
        [InlineKeyboardButton(text="🏪 Open Product Store", callback_data="menu_shop", style="primary")],
    ])
    await message.answer(
        f"🛍️ <b>PRODUCT</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>{name}</b>\n"
        f"🏷️ Panel: <b>{panel or 'Main Store'}</b>\n"
        f"📁 Category: <b>{category or 'Product'}</b>\n"
        f"⏳ Validity: <b>{validity}</b>\n"
        f"📱 Device Limit: <b>{device}</b>\n"
        f"📦 Status: <b>{status}</b>\n"
        f"💰 Price: <b>{fmt_curr(display)}</b>\n\n"
        f"👇 <b>Tap Buy to open the complete purchase menu.</b>",
        reply_markup=kb, parse_mode="HTML"
    )
    return True

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if WELCOME_STICKER_ID and "..." not in WELCOME_STICKER_ID and len(WELCOME_STICKER_ID) > 20:
        try: await message.answer_sticker(WELCOME_STICKER_ID)
        except Exception: pass 
    
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("v_"):
        order_id = args[1].split("v_")[1]
        msg = await message.answer("🔄 <b>Verifying your payment securely...</b>\n<i>Connecting to gateway...</i>", parse_mode='HTML')
        await run_payment_verification(message.from_user.id, order_id, msg)
        return

    # Product share/deep-link: /start product_<id> opens that exact product menu.
    if len(args) > 1 and args[1].startswith("product_"):
        try:
            shared_prod_id = int(args[1].split("product_", 1)[1])
        except (TypeError, ValueError):
            shared_prod_id = 0
        if shared_prod_id > 0:
            user_exists = db_query("SELECT user_id FROM users WHERE user_id=?", (message.from_user.id,), fetchone=True)
            if not user_exists:
                db_query("INSERT OR IGNORE INTO users (user_id, first_name, username, joined_date) VALUES (?, ?, ?, ?)",
                         (message.from_user.id, message.from_user.first_name, message.from_user.username or "", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            else:
                db_query("UPDATE users SET username=? WHERE user_id=?", (message.from_user.username or "", message.from_user.id))
            await send_product_deep_link_menu(message, shared_prod_id)
            return

    referred_by = None
    if len(args) > 1 and args[1].startswith("ref_"):
        try: referred_by = int(args[1].split("_")[1])
        except: pass

    user = db_query("SELECT user_id FROM users WHERE user_id=?", (message.from_user.id,), fetchone=True)
    current_username = message.from_user.username or ""

    if not user:
        db_query("INSERT OR IGNORE INTO users (user_id, first_name, username, referred_by, joined_date) VALUES (?, ?, ?, ?, ?)", 
                 (message.from_user.id, message.from_user.first_name, current_username, referred_by, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        log_activity(message.from_user.id, "ACCOUNT_CREATED")
        if referred_by:
            db_query("UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id=?", (referred_by,))
            try:
                await bot.send_message(referred_by, f"🎉 <b>Referral Success!</b>\nUser <b>{message.from_user.first_name}</b> joined using your link!", parse_mode='HTML')
            except Exception:
                pass
    else:
        db_query("UPDATE users SET username=? WHERE user_id=?", (current_username, message.from_user.id))
        log_activity(message.from_user.id, "CMD_START")

    text = get_ui_text("start_menu")
    kb = main_menu_kb(message.from_user.id)
    await message.answer(text, reply_markup=kb, parse_mode='HTML')

@dp.message(F.contact)
async def handle_contact(message: Message):
    if message.contact.user_id == message.from_user.id:
        db_query("UPDATE users SET phone=? WHERE user_id=?", (message.contact.phone_number, message.from_user.id))
        referrer = db_query("SELECT referred_by FROM users WHERE user_id=?", (message.from_user.id,), fetchone=True)
        if referrer and referrer[0]:
            db_query("UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id=?", (referrer[0],))
            try: await bot.send_message(referrer[0], f"🎉 <b>Referral Success!</b>\nUser <b>{message.from_user.first_name}</b> joined using your link!", parse_mode='HTML')
            except: pass
        log_activity(message.from_user.id, "CONTACT_VERIFIED")
        await message.answer("✅ Verification successful! Welcome to the system.", reply_markup=ReplyKeyboardRemove())
        await send_main_menu(message)
    else:
        await message.answer("❌ Security Alert: Please share your OWN contact using the provided button.")

async def send_main_menu(ctx: Any):
    text = get_ui_text("start_menu")
    kb = main_menu_kb(ctx.from_user.id)
    if isinstance(ctx, Message): 
        await ctx.answer(text, reply_markup=kb, parse_mode='HTML')
    else: 
        await ctx.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    log_activity(call.from_user.id, "RETURN_MAIN_MENU")
    await send_main_menu(call)

# ==============================================================================
# 11. ADD BALANCE
# ==============================================================================
@dp.callback_query(F.data == "menu_add_balance")
async def select_gateway_menu(call: CallbackQuery):
    log_activity(call.from_user.id, "VIEW_ADD_BALANCE")
    text = get_ui_text("add_balance_menu")
    rows = []
    if gateway_enabled("zapupi"):
        rows.append([InlineKeyboardButton(text="UPI PAY", callback_data="gateway_inr", icon_custom_emoji_id=get_emoji_icon("upi"), style="primary")])
    if gateway_enabled("famgateway") and _famgateway_api_key():
        rows.append([InlineKeyboardButton(text="FAMGATEWAY", callback_data="gateway_famgateway", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary")])
    rows.append([InlineKeyboardButton(text="BINANCE PAY", callback_data="gateway_crypto", icon_custom_emoji_id=get_emoji_icon("binance"), style="primary")])
    rows.append([InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

# ==============================================================================
# 12. UPI & CRYPTO PAYMENT FLOWS
# ==============================================================================
@dp.callback_query(F.data == "gateway_inr")
async def add_balance_inr(call: CallbackQuery):
    if not gateway_enabled("zapupi"):
        return await call.answer("⚠️ ZapUPI is currently OFF by Admin.", show_alert=True)
    text = f"💵 <b>— ZAPUPI DEPOSIT —</b> 💵\n\nSelect amount to deposit:"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="50", callback_data="pay_50", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary"), 
         InlineKeyboardButton(text="100", callback_data="pay_100", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary")],
        [InlineKeyboardButton(text="200", callback_data="pay_200", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary"), 
         InlineKeyboardButton(text="500", callback_data="pay_500", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary")],
        [InlineKeyboardButton(text="Custom Amount", callback_data="custom_deposit_keypad", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
        [InlineKeyboardButton(text="Back", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "gateway_famgateway")
async def add_balance_famgateway(call: CallbackQuery):
    if not gateway_enabled("famgateway"):
        return await call.answer("⚠️ FamGateway is currently OFF by Admin.", show_alert=True)
    if not _famgateway_api_key():
        return await call.answer("⚠️ FamGateway is not configured by admin yet.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="₹50", callback_data="fam_pay_50", style="primary"), InlineKeyboardButton(text="₹100", callback_data="fam_pay_100", style="primary")],
        [InlineKeyboardButton(text="₹200", callback_data="fam_pay_200", style="primary"), InlineKeyboardButton(text="₹500", callback_data="fam_pay_500", style="primary")],
        [InlineKeyboardButton(text="Custom Amount", callback_data="fam_custom_deposit", style="primary")],
        [InlineKeyboardButton(text="Back", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text("💜 <b>FAMGATEWAY UPI</b>\n\nSelect amount to deposit:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("fam_pay_"))
async def famgateway_fixed_payment(call: CallbackQuery):
    if _callback_is_on_cooldown((call.from_user.id, "fam_pay")):
        return await call.answer("⏳ Please wait 3 seconds before creating another payment.", show_alert=True)
    amount = float(call.data.split("_")[-1])
    await create_famgateway_payment(call.from_user.id, amount, call.message)

@dp.callback_query(F.data == "fam_custom_deposit")
async def famgateway_custom_payment(call: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.custom_amount_input)
    await state.update_data(amount_str="0", payment_gateway="famgateway")
    await show_keypad(call.message, "0")

@dp.callback_query(F.data == "custom_deposit_keypad")
async def show_custom_keypad(call: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.custom_amount_input)
    await state.update_data(amount_str="0")
    await show_keypad(call.message)

async def show_keypad(message: Message, amount_str: str = "0"):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="      1      ", callback_data="kp_1", style="primary"), InlineKeyboardButton(text="      2      ", callback_data="kp_2", style="primary"), InlineKeyboardButton(text="      3      ", callback_data="kp_3", style="primary")],
        [InlineKeyboardButton(text="      4      ", callback_data="kp_4", style="primary"), InlineKeyboardButton(text="      5      ", callback_data="kp_5", style="primary"), InlineKeyboardButton(text="      6      ", callback_data="kp_6", style="primary")],
        [InlineKeyboardButton(text="      7      ", callback_data="kp_7", style="primary"), InlineKeyboardButton(text="      8      ", callback_data="kp_8", style="primary"), InlineKeyboardButton(text="      9      ", callback_data="kp_9", style="primary")],
        [InlineKeyboardButton(text="    ⌫    ", callback_data="kp_backspace", style="danger"), InlineKeyboardButton(text="      0      ", callback_data="kp_0", style="primary"), InlineKeyboardButton(text="    C    ", callback_data="kp_clear", style="danger")],
        [InlineKeyboardButton(text=f"✅ Confirm (₹{amount_str})", callback_data="kp_confirm", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="success")],
        [InlineKeyboardButton(text="Cancel", callback_data="gateway_inr", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    text = f"💵 <b>Enter Amount (₹):</b>\n\nCurrent: ₹{amount_str}"
    await message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("kp_"), UserStates.custom_amount_input)
async def keypad_handler(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount_str = data.get("amount_str", "0")
    action = call.data.split("_")[1]
    if action == "confirm":
        if amount_str == "0":
            await call.answer("Amount cannot be zero.", show_alert=True)
            return
        try:
            amount = float(amount_str)
            if amount < 10:
                await call.answer("Minimum deposit is ₹1.", show_alert=True)
                return
            gateway = data.get("payment_gateway", "zapupi")
            await state.clear()
            await call.message.edit_text("⏳ <b>Generating Secure Link...</b>", parse_mode='HTML')
            if gateway == "famgateway":
                await create_famgateway_payment(call.from_user.id, amount, call.message)
            else:
                await generate_zapupi_order(call.from_user.id, amount, call.message)
        except ValueError:
            await call.answer("Invalid amount.", show_alert=True)
        return
    if action == "backspace":
        if len(amount_str) > 1: amount_str = amount_str[:-1]
        else: amount_str = "0"
    elif action == "clear":
        amount_str = "0"
    else:
        if amount_str == "0": amount_str = action
        else: amount_str += action
        if len(amount_str) > 6: amount_str = amount_str[:6]
    await state.update_data(amount_str=amount_str)
    await show_keypad(call.message, amount_str)
    await call.answer()

@dp.callback_query(F.data.startswith("pay_"))
async def process_zapupi_payment_callback(call: CallbackQuery):
    inr_amount = float(call.data.split("_")[1])
    await call.message.edit_text("⏳ <b>Generating Secure Link via ZapUPI...</b>", parse_mode='HTML')
    await generate_zapupi_order(call.from_user.id, inr_amount, call.message)

async def create_famgateway_payment(user_id: int, amount: float, message_obj: Message,
                                   purpose: str = "wallet_topup", product_id: Optional[int] = None,
                                   quantity: int = 1, description: str = "") -> None:
    if amount < 10:
        return await message_obj.edit_text("❌ Minimum deposit is ₹1.", reply_markup=back_kb("menu_add_balance"), parse_mode='HTML')
    result = await famgateway_create_order(user_id, amount, description or "Wallet Topup")
    if result.get("status") != "success":
        return await message_obj.edit_text(f"❌ <b>FamGateway Error:</b> {html.escape(str(result.get('message','Unknown error')))}", reply_markup=back_kb("menu_add_balance"), parse_mode='HTML')
    order_id = result.get("order_id")
    payment_url = result.get("checkout_url") or result.get("payment_url") or result.get("qr_url")
    if not order_id or not payment_url:
        return await message_obj.edit_text("❌ FamGateway returned an incomplete payment response.", reply_markup=back_kb("menu_add_balance"), parse_mode='HTML')
    current_time = int(time.time())
    db_query(
        "INSERT INTO transactions (order_id, user_id, amount_inr, status, timestamp, purpose, product_id, quantity, description, gateway) "
        "VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, 'famgateway')",
        (order_id, user_id, amount, current_time, purpose, product_id, quantity, description)
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Pay via FamGateway", url=payment_url, style="success")],
        [InlineKeyboardButton(text="✅ Verify Payment", callback_data=f"verify_{order_id}", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="primary")],
        [InlineKeyboardButton(text="Cancel", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    invoice_label = f"🛒 <b>Direct Purchase:</b> {description}\n" if purpose == "product_purchase" and description else ""
    text = (f"💜 <b>FAMGATEWAY SECURE UPI</b>\n\n{invoice_label}"
            f"Amount: <b>₹{amount:.2f}</b>\nOrder ID: <code>{order_id}</code>\n⏳ <b>Timer:</b> 5 Minutes\n\n"
            "1️⃣ Open Pay via FamGateway.\n2️⃣ Complete the UPI payment.\n3️⃣ Payment will be verified automatically / by Verify Payment.")
    await message_obj.edit_text(text, reply_markup=kb, parse_mode='HTML')

async def generate_zapupi_order(user_id: int, inr_amount: float, message_obj: Message,
                                purpose: str = "wallet_topup", product_id: Optional[int] = None,
                                quantity: int = 1, description: str = "") -> None:
    api_key_check = db_query("SELECT value FROM settings WHERE key='zapupi_api'", fetchone=True)
    if not api_key_check or not api_key_check[0]:
        return await message_obj.edit_text("⚠️ ZapUPI Gateway is currently offline. Admin needs to set API Key.", reply_markup=back_kb("gateway_inr"), parse_mode='HTML')
        
    api_key = api_key_check[0]
    current_time = int(time.time())
    order_id = f"NXT{user_id}{current_time}"
    user_phone = db_query("SELECT phone FROM users WHERE user_id=?", (user_id,), fetchone=True)
    mobile = user_phone[0] if user_phone and user_phone[0] else "9999999999"
    bot_deep_link = f"https://t.me/{bot_username_clean()}?start=v_{order_id}"
    
    db_query(
        "INSERT INTO transactions (order_id, user_id, amount_inr, status, timestamp, purpose, product_id, quantity, description, gateway) "
        "VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, 'zapupi')",
        (order_id, user_id, inr_amount, current_time, purpose, product_id, quantity, description)
    )
    payment_url = ""
    
    async with aiohttp.ClientSession() as session:
        try:
            url = "https://pay.zapupi.com/api/create-order"
            payload = {"zap_key": api_key, "order_id": order_id, "amount": str(inr_amount), "customer_mobile": mobile, "remark": "Wallet Topup", "success_url": bot_deep_link, "failed_url": f"https://t.me/{bot_username_clean()}", "timeout_url": f"https://t.me/{bot_username_clean()}", "webhook_url": f"https://t.me/{bot_username_clean()}"}
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    try: res_data = await resp.json(content_type=None)
                    except: res_data = {}
                    if res_data.get("status") == "success": payment_url = res_data.get("payment_url")
                    else: return await message_obj.edit_text(f"❌ <b>Gateway Data Error:</b> {res_data.get('message', 'Unknown structure.')}", reply_markup=back_kb("gateway_inr"), parse_mode='HTML')
                else: return await message_obj.edit_text(f"❌ <b>Gateway Server Error:</b> HTTP {resp.status}", reply_markup=back_kb("gateway_inr"), parse_mode='HTML')
        except Exception as e:
            return await message_obj.edit_text(f"❌ <b>API Connection Error:</b> {str(e)}", reply_markup=back_kb("gateway_inr"), parse_mode='HTML')

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Pay Now (Auto Redirects)", url=payment_url, style="success")],
        [InlineKeyboardButton(text="Manual Verify", callback_data=f"verify_{order_id}", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="primary")],
        [InlineKeyboardButton(text="Cancel Transaction", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    invoice_label = f"🛒 <b>Direct Purchase:</b> {description}\n" if purpose == "product_purchase" and description else ""
    text = (f"🧾 <b>SECURE UPI INVOICE</b>\n\n{invoice_label}"
            f"Amount: <b>₹{inr_amount:.2f}</b>\nOrder ID: <code>{order_id}</code>\n⏳ <b>Timer:</b> 15:00 Minutes\n\n"
            "1️⃣ Click <b>Pay Now</b> to open UPI Gateway.\n"
            "2️⃣ Complete the payment in your UPI app.\n"
            "3️⃣ Payment is verified automatically and the order is completed. ✨")
    log_activity(user_id, "GENERATE_INVOICE", f"Amount: {inr_amount}, Order ID: {order_id}")
    await message_obj.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("verify_"))
async def manual_verify_callback(call: CallbackQuery):
    order_id = call.data.split("_", 1)[1]
    await run_payment_verification(call.from_user.id, order_id, call)

@dp.callback_query(F.data == "gateway_crypto")
async def add_balance_crypto(call: CallbackQuery, state: FSMContext):
    address_check = db_query("SELECT value FROM settings WHERE key='binance_address'", fetchone=True)
    if not address_check or not address_check[0]:
        return await call.message.edit_text("⚠️ Binance Gateway is currently offline. Admin has not set a deposit address.", reply_markup=back_kb("menu_add_balance"), parse_mode='HTML')
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]])
    await call.message.edit_text(
        f"🪙 <b>— BINANCE USDT DEPOSIT —</b> 🪙\n\n"
        f"💵 <b>Rate:</b> 1 USDT = ₹{USDT_TO_INR}\n"
        f"⚠️ <b>Network:</b> TRC20 / BEP20\n\n"
        f"👇 First, enter the <b>USDT amount</b> you are sending:\n"
        f"<i>(e.g. 5 or 10.5)</i>",
        reply_markup=kb, parse_mode='HTML'
    )
    await state.set_state(UserStates.wait_for_crypto_amount)

@dp.message(UserStates.wait_for_crypto_amount)
async def process_crypto_amount(m: Message, state: FSMContext):
    try:
        usdt = float(m.text.strip())
        if usdt <= 0: raise ValueError
    except ValueError:
        return await m.answer("❌ Invalid amount. Enter a number like <code>5</code> or <code>10.5</code>", parse_mode='HTML')
    inr = usdt * USDT_TO_INR
    deposit_address = db_query("SELECT value FROM settings WHERE key='binance_address'", fetchone=True)
    if not deposit_address or not deposit_address[0]:
        return await m.answer("⚠️ Deposit address removed by admin. Try again later.")
    await state.update_data(crypto_usdt=usdt, crypto_inr=inr)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Cancel", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]])
    await m.answer(
        f"🪙 <b>BINANCE USDT DEPOSIT</b>\n\n"
        f"💵 Amount: <b>{usdt} USDT ≈ {fmt_curr(inr)}</b>\n\n"
        f"👇 Send USDT to this address:\n<code>{deposit_address[0]}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✅ After sending, <b>send a screenshot</b> of your payment as proof.\n"
        f"Admin will verify and credit your balance.",
        reply_markup=kb, parse_mode='HTML'
    )
    await state.set_state(UserStates.wait_for_crypto_ss)

@dp.message(UserStates.wait_for_crypto_ss)
async def process_crypto_ss(m: Message, state: FSMContext):
    # Accept photo only
    if not m.photo:
        return await m.answer("❌ Please send a <b>screenshot/photo</b> of your Binance payment.", parse_mode='HTML')
    data = await state.get_data()
    usdt = data.get("crypto_usdt", 0)
    inr = data.get("crypto_inr", 0)
    user_id = m.from_user.id
    photo_id = m.photo[-1].file_id
    import time as _time
    txid = f"SS_{user_id}_{int(_time.time())}"
    # Save as pending
    db_query(
        "INSERT OR IGNORE INTO crypto_txns (txid, user_id, amount_usdt, amount_inr, photo_id, status, timestamp) VALUES (?,?,?,?,?,'pending',?)",
        (txid, user_id, usdt, inr, photo_id, int(_time.time()))
    )
    uname = f"@{m.from_user.username}" if m.from_user.username else "No username"
    admin_text = (
        f"🪙 <b>BINANCE DEPOSIT REQUEST</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"👤 User: <b>{m.from_user.full_name}</b>\n"
        f"🆔 ID: <code>{user_id}</code> | {uname}\n"
        f"💵 Amount: <b>{usdt} USDT ≈ {fmt_curr(inr)}</b>\n"
        f"🔖 Ref: <code>{txid}</code>\n"
        f"━━━━━━━━━━━━━━\n"
        f"👇 Review screenshot and approve/reject:"
    )
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Approve & Credit", callback_data=f"crypto_approve_{txid}", style="success")],
        [InlineKeyboardButton(text="❌ Reject", callback_data=f"crypto_reject_{txid}", style="danger")]
    ])
    await notify_admins(admin_text, reply_markup=admin_kb, parse_mode='HTML')
    for aid in ADMIN_IDS:
        try: await bot.send_photo(aid, photo=photo_id, caption=f"📸 Payment SS for {txid}")
        except: pass
    await m.answer(
        "⏳ <b>Screenshot received!</b>\n\n"
        f"💵 {usdt} USDT ≈ {fmt_curr(inr)}\n\n"
        "✅ Admin will review and credit your balance shortly.",
        reply_markup=back_kb(), parse_mode='HTML'
    )
    await state.clear()

@dp.callback_query(F.data.startswith("crypto_approve_"))
async def crypto_approve(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    txid = call.data.split("crypto_approve_", 1)[1]
    row = db_query("SELECT user_id, amount_usdt, amount_inr, status FROM crypto_txns WHERE txid=?", (txid,), fetchone=True)
    if not row: return await call.answer("❌ Request not found!", show_alert=True)
    if row[3] == 'approved': return await call.answer("⚠️ Already approved!", show_alert=True)
    user_id, usdt, inr = row[0], row[1], row[2]
    db_query("UPDATE crypto_txns SET status='approved' WHERE txid=?", (txid,))
    db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (inr, user_id))
    log_activity(user_id, "CRYPTO_DEPOSIT", f"SS Approved: {txid}, USDT:{usdt}, INR:{inr}")
    try:
        await bot.send_message(user_id,
            f"🎉 <b>BINANCE PAYMENT APPROVED!</b>\n\n"
            f"✅ {usdt} USDT = <b>{fmt_curr(inr)}</b> added to your wallet!\n"
            f"💰 Check your balance in My Profile.",
            parse_mode='HTML'
        )
    except: pass
    await call.message.edit_caption(caption=f"✅ APPROVED — {usdt} USDT → {fmt_curr(inr)} credited to user {user_id}")
    await call.answer(f"✅ Approved! {fmt_curr(inr)} credited.", show_alert=True)

@dp.callback_query(F.data.startswith("crypto_reject_"))
async def crypto_reject(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    txid = call.data.split("crypto_reject_", 1)[1]
    row = db_query("SELECT user_id, amount_usdt, status FROM crypto_txns WHERE txid=?", (txid,), fetchone=True)
    if not row: return await call.answer("❌ Request not found!", show_alert=True)
    if row[2] == 'approved': return await call.answer("⚠️ Already approved, can't reject!", show_alert=True)
    db_query("UPDATE crypto_txns SET status='rejected' WHERE txid=?", (txid,))
    try:
        await bot.send_message(row[0],
            "❌ <b>BINANCE PAYMENT REJECTED</b>\n\n"
            "Your screenshot was rejected by admin.\n"
            "Contact support if you think this is a mistake.",
            parse_mode='HTML'
        )
    except: pass
    await call.message.edit_caption(caption=f"❌ REJECTED — {txid}")
    await call.answer("❌ Rejected.", show_alert=True)



# ==============================================================================
# 13. SHOP – with uppercase categories and new point_down emoji
# ==============================================================================
@dp.callback_query(F.data == "menu_shop")
async def view_shop_panels(call: CallbackQuery):
    log_activity(call.from_user.id, "VIEW_SHOP")
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    text = f"{get_emoji('product_store')} <b><u>SELECT PRODUCT PANEL</u></b>\n━━━━━━━━━━━━━━━━━━\n\n{get_emoji('point_down')} <b>Choose a panel to view its packages:</b>"
    for cat in get_all_categories():
        count = db_query("SELECT COUNT(*) FROM products WHERE category LIKE ? AND is_active=1", (cat + '%',), fetchone=True)[0]
        custom_id = get_setting(f"cat_custom_id_{cat}", "").strip()
        stored_emoji = get_setting(f"cat_emoji_{cat}", "").strip()
        
        if custom_id and custom_id.isdigit():
            btn_text = cat
            btn_icon = custom_id
        elif stored_emoji and stored_emoji.isdigit():
            btn_text = cat
            btn_icon = stored_emoji
        elif stored_emoji:
            btn_text = f"{stored_emoji} {cat}"
            btn_icon = None
        else:
            default_icon_id = get_category_emoji(cat)
            if default_icon_id and str(default_icon_id).isdigit():
                btn_text = cat
                btn_icon = default_icon_id
            else:
                cat_icon = get_category_display_icon(cat)
                btn_text = f"{cat_icon} {cat}"
                btn_icon = None
        kb.inline_keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"shop_cat_{cat}", icon_custom_emoji_id=btn_icon, style=panel_menu_style())])
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("shop_cat_"))
async def view_panel_names(call: CallbackQuery):
    category = call.data.split("shop_cat_", 1)[1]
    panel_rows = db_query("""
        SELECT MIN(id) AS sample_id, panel_name, COALESCE(MIN(panel_sort_order), 0) AS p_order
        FROM products 
        WHERE category LIKE ? AND is_active=1 AND panel_name != ''
        GROUP BY panel_name
        ORDER BY p_order ASC, panel_name ASC
    """, (category + '%',), fetchall=True)
    if not panel_rows:
        prods = db_query("""
            SELECT id, name, price_inr, stock, reseller_price, validity, device_limit, external_enabled, COALESCE(is_maintenance, 0), COALESCE(sort_order, 0)
            FROM products WHERE category LIKE ? AND is_active=1
            ORDER BY COALESCE(sort_order, 0) ASC, id ASC
        """, (category + '%',), fetchall=True)
        if not prods: return await call.answer("❌ No products available in this category yet.", show_alert=True)
        await show_products_for_panel(call, prods, category, category)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    text = f"{get_emoji('product_store')} <b><u>{category.upper()} PANELS</u></b>\n━━━━━━━━━━━━━━━━━━\n\n{get_emoji('point_down')} <b>Choose a panel name:</b>"
    for pn in panel_rows:
        sample_id, panel, p_order = pn[0], pn[1], pn[2]
        custom_id = get_setting(f"panel_custom_id_{panel}", "").strip()
        stored_emoji = get_setting(f"panel_emoji_{panel}", "").strip()
        
        if custom_id and custom_id.isdigit():
            btn_text = panel
            btn_icon = custom_id
        elif stored_emoji and stored_emoji.isdigit():
            btn_text = panel
            btn_icon = stored_emoji
        elif stored_emoji:
            btn_text = f"{stored_emoji} {panel}"
            btn_icon = None
        else:
            btn_text = panel
            btn_icon = get_emoji_icon("product_store")
            
        kb.inline_keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"shop_pnl_{sample_id}", icon_custom_emoji_id=btn_icon if (btn_icon and str(btn_icon).isdigit()) else None, style=panel_menu_style())])
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK TO PANELS", callback_data="menu_shop", icon_custom_emoji_id=get_emoji_icon("back"), style="primary")])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("shop_pnl_"))
async def view_products_for_panel(call: CallbackQuery):
    sample_id = int(call.data.split("shop_pnl_")[1])
    prod = db_query("SELECT category, panel_name FROM products WHERE id=?", (sample_id,), fetchone=True)
    if not prod: return await call.answer("No products found for this panel.", show_alert=True)
    category, panel_name = prod
    prods = db_query("""
        SELECT id, name, price_inr, stock, reseller_price, validity, device_limit, external_enabled, COALESCE(is_maintenance, 0), COALESCE(sort_order, 0)
        FROM products 
        WHERE category=? AND panel_name=? AND is_active=1
        ORDER BY COALESCE(sort_order, 0) ASC, id ASC
    """, (category, panel_name), fetchall=True)
    if not prods: return await call.answer("No products found for this panel.", show_alert=True)
    await show_products_for_panel(call, prods, f"{category} - {panel_name}", category)

async def show_products_for_panel(call: CallbackQuery, prods: List[Tuple], header: str, category: str = ""):
    user = db_query("SELECT is_reseller, is_vip FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    is_reseller = bool(user[0]) if user else False
    is_vip = bool(user[1]) if user else False
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    text = f"{get_emoji('product_store')} <b><u>{header.upper()} PACKAGES</u></b>\n━━━━━━━━━━━━━━━━━━\n\n"
    has_custom_sort = any(len(p) > 9 and p[9] != 0 for p in prods)
    if has_custom_sort:
        prods = sorted(prods or [], key=lambda row: (row[9] if len(row) > 9 else 0, natural_sort_key(row[1])))
    else:
        prods = sorted(prods or [], key=lambda row: natural_sort_key(row[1]))
    for p in prods:
        prod_id, package_name, normal_price, stock, reseller_price, validity, device, external_enabled = p[:8]
        is_maint = bool(p[8]) if len(p) > 8 else is_product_in_maintenance(prod_id)
        normal_price = float(normal_price) if normal_price is not None else 0.0
        reseller_price = float(reseller_price) if reseller_price is not None else 0.0
        base_price = reseller_price if is_reseller else normal_price
        if is_vip: display_price = base_price - (base_price * (VIP_DISCOUNT_PERCENTAGE / 100))
        else: display_price = base_price
        if is_maint:
            stock_status = "🟠 Under Maintenance"
        else:
            stock_status = "♾️ API Available" if external_enabled else ("✅ In Stock" if stock > 0 else "❌ Out of Stock")

        dur_custom_id = get_setting(f"dur_custom_id_{package_name.lower().strip()}", "").strip()
        dur_stored = get_setting(f"dur_emoji_{package_name.lower().strip()}", "").strip()
        if not dur_custom_id and not dur_stored:
            lower = package_name.lower().strip()
            for k in DEFAULT_DURATION_EMOJIS:
                if k in lower:
                    dur_custom_id = get_setting(f"dur_custom_id_{k}", "").strip()
                    dur_stored = get_setting(f"dur_emoji_{k}", "").strip()
                    break

        if dur_custom_id and dur_custom_id.isdigit():
            btn_icon_id = dur_custom_id
            btn_text = f"Buy {package_name} - {fmt_curr(display_price)}"
            dur_disp = f'<tg-emoji emoji-id="{dur_custom_id}">⚡</tg-emoji>'
        elif dur_stored and dur_stored.isdigit():
            btn_icon_id = dur_stored
            btn_text = f"Buy {package_name} - {fmt_curr(display_price)}"
            dur_disp = f'<tg-emoji emoji-id="{dur_stored}">⚡</tg-emoji>'
        elif dur_stored:
            btn_icon_id = None
            btn_text = f"{dur_stored} Buy {package_name} - {fmt_curr(display_price)}"
            dur_disp = dur_stored
        else:
            fallback = get_duration_emoji(package_name)
            btn_icon_id = None
            btn_text = f"{fallback} Buy {package_name} - {fmt_curr(display_price)}"
            dur_disp = fallback

        text += f"{dur_disp} <b>Validity: {package_name}</b>\n"
        if is_maint:
            text += "⚠️ <b>Status: Under Maintenance / यह प्रोडक्ट मेंटेनेंस में है</b>\n"
        if is_reseller or is_vip:
            text += f"💰 Regular Price: <s>{fmt_curr(normal_price)}</s>\n"
            if is_reseller and not is_vip: text += f"👑 <b>Reseller Price: {fmt_curr(display_price)}</b>\n"
            elif is_vip and not is_reseller: text += f"🌟 <b>VIP Price: {fmt_curr(display_price)}</b>\n"
            else: text += f"👑🌟 <b>Super Price: {fmt_curr(display_price)}</b>\n"
        else: text += f"💰 Price: {fmt_curr(normal_price)}\n"
        text += f"📱 Limit: {device} | 📦 {stock_status}\n\n"
        if is_maint:
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"🟠 {package_name} (Under Maintenance)", callback_data=f"buy_{prod_id}", icon_custom_emoji_id=get_emoji_icon("shield_icon"), style=product_menu_style())])
        elif external_enabled or stock > 0:
            kb.inline_keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"buy_{prod_id}", icon_custom_emoji_id=btn_icon_id, style=product_menu_style())])
        else:
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"❌ {package_name} (Out of Stock)", callback_data="ignore_stock_click", style="primary")])

    # One Share button for the whole product/panel list — never repeated beside every duration.
    if prods:
        share_prod_id = prods[0][0]
        share_title = header.split(" - ")[0].strip() if header else "Product"
        kb.inline_keyboard.append([InlineKeyboardButton(text="🔗 Share Product", url=product_share_url(share_prod_id, share_title), style="primary")])

    # Keep exactly ONE reseller-price button for the whole panel, placed above the package list.
    # This avoids repeating the same reseller control under each duration/package.
    reseller_btn = None
    if prods:
        reseller_btn = InlineKeyboardButton(
            text="👑 View All Reseller Prices",
            callback_data=f"reseller_prices_{prods[0][0]}",
            style="success"
        )
    text += f"{get_emoji('point_down')} <b>Select package below to instantly purchase:</b>"
    back_cb = f"shop_cat_{category}" if category else "menu_shop"
    # Move the single reseller button to the top of the keyboard, not between packages.
    if reseller_btn:
        kb.inline_keyboard.insert(0, [reseller_btn])
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK TO PANELS", callback_data=back_cb, icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("reseller_price_one_"))
async def show_one_reseller_price(call: CallbackQuery):
    try:
        prod_id = int(call.data.split("reseller_price_one_", 1)[1])
    except Exception:
        return await call.answer("Invalid reseller price request.", show_alert=True)
    row = db_query("SELECT name, price_inr, reseller_price, validity, panel_name FROM products WHERE id=? AND is_active=1", (prod_id,), fetchone=True)
    if not row:
        return await call.answer("Product not found.", show_alert=True)
    name, regular, reseller, validity, panel = row
    await call.message.edit_text(
        f"👑 <b>RESELLER PRICE</b>\n\n📦 <b>{html.escape(str(name or validity or 'Package'))}</b>\n🏷 Panel: <b>{html.escape(str(panel or '-'))}</b>\n💰 Regular: <s>{fmt_curr(float(regular or 0))}</s>\n👑 <b>Reseller: {fmt_curr(float(reseller or 0))}</b>\n\nℹ️ This price is for accounts with Reseller status. Your account status does not change by viewing this price.",
        reply_markup=back_kb(f"shop_pnl_{prod_id}"), parse_mode="HTML")

@dp.callback_query(F.data.startswith("reseller_prices_"))
async def show_reseller_prices(call: CallbackQuery):
    try:
        sample_id = int(call.data.split("_", 2)[2])
    except Exception:
        return await call.answer("Invalid reseller price request.", show_alert=True)
    row = db_query("SELECT category, panel_name FROM products WHERE id=?", (sample_id,), fetchone=True)
    if not row:
        return await call.answer("Product panel not found.", show_alert=True)
    category, panel_name = row
    prods = db_query("SELECT name, price_inr, reseller_price, validity FROM products WHERE category=? AND panel_name=? AND is_active=1 ORDER BY id ASC", (category, panel_name), fetchall=True) or []
    lines = [f"👑 <b>RESELLER PRICE LIST</b>\n<b>{html.escape(panel_name or category or 'Panel')}</b>", "━━━━━━━━━━━━━━━━━━"]
    for name, regular, rprice, validity in prods:
        lines.append(f"📦 <b>{html.escape(str(name or validity or 'Package'))}</b>\n💰 Regular: {fmt_curr(float(regular or 0))}\n👑 Reseller: <b>{fmt_curr(float(rprice or 0))}</b>")
    lines.append("━━━━━━━━━━━━━━━━━━\nℹ️ Reseller prices apply at checkout only to users whose account is marked as Reseller.")
    await call.message.edit_text("\n".join(lines), reply_markup=back_kb("shop_pnl_" + str(sample_id)), parse_mode='HTML')

@dp.callback_query(F.data == "ignore_stock_click")
async def ignore_stock_click(call: CallbackQuery):
    await call.answer("⚠️ This duration is completely Out of Stock! Admins have been notified to refill.", show_alert=True)

@dp.callback_query(F.data.startswith("buy_"))
async def process_buy(call: CallbackQuery):
    if _callback_is_on_cooldown((call.from_user.id, "buy_menu")):
        return await call.answer("⏳ Please wait 3 seconds before opening purchase again.", show_alert=True)
    try:
        prod_id = int(call.data.split("_")[1])
    except (ValueError, IndexError):
        return await call.answer("Invalid product.", show_alert=True)
    prod = db_query("SELECT name, price_inr, reseller_price, stock, is_active, is_maintenance, external_enabled FROM products WHERE id=?", (prod_id,), fetchone=True)
    user = db_query("SELECT balance, is_reseller, is_vip FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    if not prod or not user:
        return await call.answer("❌ Product or account not found.", show_alert=True)
    if prod[5]:
        return await call.answer("⚠️ This product is under maintenance.", show_alert=True)
    base = float(prod[2] or 0) if user[1] else float(prod[1] or 0)
    if user[2]:
        base -= base * VIP_DISCOUNT_PERCENTAGE / 100
    max_qty = 10 if prod[6] else min(10, int(prod[3] or 0))
    if max_qty < 1:
        return await call.answer("❌ This product is out of stock.", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for row in ((1,2,3),(5,10)):
        buttons=[]
        for qty in row:
            if qty <= max_qty:
                buttons.append(InlineKeyboardButton(text=f"{qty} × {fmt_curr(base*qty)}", callback_data=f"buyqty_{prod_id}_{qty}", style="primary"))
        if buttons:
            kb.inline_keyboard.append(buttons)
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK", callback_data="menu_shop", icon_custom_emoji_id=get_emoji_icon("back"), style="primary")])
    await call.message.edit_text(
        f"🛒 <b>{prod[0]}</b>\n\n💰 <b>Unit Price:</b> {fmt_curr(base)}\n"
        f"📦 Select quantity (1–{max_qty})\n💳 Wallet or direct UPI payment is available.",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("buyqty_"))
async def choose_purchase_payment(call: CallbackQuery):
    try:
        _, prod_id_s, qty_s = call.data.split("_")
        prod_id, qty = int(prod_id_s), int(qty_s)
    except (ValueError, IndexError):
        return await call.answer("Invalid purchase request.", show_alert=True)
    prod = db_query("SELECT name, price_inr, reseller_price, stock, is_maintenance, external_enabled FROM products WHERE id=?", (prod_id,), fetchone=True)
    user = db_query("SELECT balance, is_reseller, is_vip FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    if not prod or not user or prod[4]:
        return await call.answer("Product is unavailable.", show_alert=True)
    base = float(prod[2] or 0) if user[1] else float(prod[1] or 0)
    if user[2]:
        base -= base * VIP_DISCOUNT_PERCENTAGE / 100
    total = base * qty
    if not prod[5] and int(prod[3] or 0) < qty:
        return await call.answer(f"Only {prod[3] or 0} key(s) available.", show_alert=True)
    rows = []
    if float(user[0] or 0) >= total:
        rows.append([InlineKeyboardButton(text=f"💰 Pay Wallet {fmt_curr(total)}", callback_data=f"walletbuy_{prod_id}_{qty}", style="primary")])
    else:
        rows.append([InlineKeyboardButton(text=f"👛 Wallet {fmt_curr(float(user[0] or 0))}", callback_data="ignore_stock_click", style="danger")])
    if gateway_enabled("zapupi"):
        rows.append([InlineKeyboardButton(text=f"📲 Pay via ZapUPI {fmt_curr(total)}", callback_data=f"upibuy_{prod_id}_{qty}", icon_custom_emoji_id=get_emoji_icon("upi"), style="primary")])
    if gateway_enabled("famgateway") and _famgateway_api_key():
        rows.append([InlineKeyboardButton(text=f"💜 Pay via FamGateway {fmt_curr(total)}", callback_data=f"famupibuy_{prod_id}_{qty}", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary")])
    rows.append([InlineKeyboardButton(text="⬅️ Change Quantity", callback_data=f"buy_{prod_id}", icon_custom_emoji_id=get_emoji_icon("back"), style="primary")])
    await call.message.edit_text(
        f"🧾 <b>ORDER SUMMARY</b>\n\n📦 {prod[0]}\n🔢 Quantity: <b>{qty}</b>\n"
        f"💵 Unit: <b>{fmt_curr(base)}</b>\n💰 Total: <b>{fmt_curr(total)}</b>\n"
        f"👛 Wallet: <b>{fmt_curr(float(user[0] or 0))}</b>\n\nSelect payment method:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("walletbuy_"))
async def wallet_purchase_quantity(call: CallbackQuery):
    if _callback_is_on_cooldown((call.from_user.id, "walletbuy")):
        return await call.answer("⏳ Purchase already started. Please wait 3 seconds.", show_alert=True)
    try:
        _, prod_id_s, qty_s = call.data.split("_")
        prod_id, qty = int(prod_id_s), int(qty_s)
    except (ValueError, IndexError):
        return await call.answer("Invalid purchase request.", show_alert=True)
    for _ in range(qty):
        await _process_single_buy(call, prod_id)

@dp.callback_query(F.data.startswith("famupibuy_"))
async def famgateway_direct_purchase(call: CallbackQuery):
    if not gateway_enabled("famgateway"):
        return await call.answer("⚠️ FamGateway is currently OFF by Admin.", show_alert=True)
    if _callback_is_on_cooldown((call.from_user.id, "famupibuy")):
        return await call.answer("⏳ Please wait 3 seconds before creating another payment.", show_alert=True)
    try:
        _, prod_id_s, qty_s = call.data.split("_")
        prod_id, qty = int(prod_id_s), int(qty_s)
    except (ValueError, IndexError):
        return await call.answer("Invalid purchase request.", show_alert=True)
    prod = db_query("SELECT name, price_inr, reseller_price, is_maintenance FROM products WHERE id=?", (prod_id,), fetchone=True)
    user = db_query("SELECT balance, is_reseller, is_vip FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    if not prod or not user or prod[3]:
        return await call.answer("Product is unavailable.", show_alert=True)
    unit = float(prod[2] or 0) if user[1] else float(prod[1] or 0)
    if user[2]: unit -= unit * VIP_DISCOUNT_PERCENTAGE / 100
    total = unit * qty
    await call.message.edit_text("⏳ <b>Generating FamGateway payment...</b>", parse_mode="HTML")
    await create_famgateway_payment(call.from_user.id, total, call.message, purpose="product_purchase", product_id=prod_id, quantity=qty, description=f"{prod[0]} × {qty}")

@dp.callback_query(F.data.startswith("upibuy_"))
async def direct_upi_purchase(call: CallbackQuery):
    if not gateway_enabled("zapupi"):
        return await call.answer("⚠️ ZapUPI is currently OFF by Admin.", show_alert=True)
    if _callback_is_on_cooldown((call.from_user.id, "upibuy")):
        return await call.answer("⏳ Please wait 3 seconds before creating another payment.", show_alert=True)
    try:
        _, prod_id_s, qty_s = call.data.split("_")
        prod_id, qty = int(prod_id_s), int(qty_s)
    except (ValueError, IndexError):
        return await call.answer("Invalid purchase request.", show_alert=True)
    prod = db_query("SELECT name, price_inr, reseller_price, is_maintenance FROM products WHERE id=?", (prod_id,), fetchone=True)
    user = db_query("SELECT balance, is_reseller, is_vip FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    if not prod or not user or prod[3]:
        return await call.answer("Product is unavailable.", show_alert=True)
    unit = float(prod[2] or 0) if user[1] else float(prod[1] or 0)
    if user[2]:
        unit -= unit * VIP_DISCOUNT_PERCENTAGE / 100
    total = unit * qty
    await call.message.edit_text("⏳ <b>Generating direct UPI payment...</b>", parse_mode="HTML")
    await generate_zapupi_order(
        call.from_user.id, total, call.message,
        purpose="product_purchase", product_id=prod_id, quantity=qty,
        description=f"{prod[0]} × {qty}"
    )

class _PurchaseCallProxy:
    def __init__(self, user, message):
        self.from_user = user
        self.message = message
    async def answer(self, *args, **kwargs):
        return None

async def _show_purchase_timer(call: CallbackQuery, seconds: int = 3):
    """Show a visible 3-second delivery countdown in the same chat message."""
    for remaining in range(seconds, 0, -1):
        try:
            await call.message.edit_text(
                f"⏳ <b>KEY GENERATION IN PROGRESS</b>\n\n"
                f"🔐 Your key is being fetched securely...\n"
                f"⏱ <b>{remaining} seconds</b> remaining\n\n"
                "⚠️ Please do not press the purchase button again.",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await asyncio.sleep(1)


async def _process_single_buy(call: CallbackQuery, prod_id: int):
    user_id = int(call.from_user.id)
    lock = _get_purchase_lock(user_id, prod_id)
    if lock.locked():
        return await call.answer("⏳ This purchase is already being processed. Please wait.", show_alert=True)
    async with lock:
        return await _process_single_buy_locked(call, prod_id)

async def _process_single_buy_locked(call, prod_id: int):
    if is_product_in_maintenance(prod_id):
        return await call.answer("⚠️ Yeh product abhi maintenance me hai!\nThis product is currently under maintenance. Please check back later!", show_alert=True)
    prod = db_query("""
        SELECT name, price_inr, stock, apk_link, validity, device_limit,
               category, reseller_price, panel_name, external_enabled,
               external_product_id, requires_android_id, external_duration, api_provider
        FROM products WHERE id=?
    """, (prod_id,), fetchone=True)
    user = db_query("SELECT balance, referred_by, is_reseller, total_saved, is_vip FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    if not prod:
        return await call.answer("❌ Critical Error: Item not found in DB!", show_alert=True)
    if not user:
        return await call.answer("❌ User account not found!", show_alert=True)

    normal_price = float(prod[1] or 0.0)
    reseller_price = float(prod[7] or 0.0)
    is_reseller = bool(user[2]); is_vip = bool(user[4])
    base_price = reseller_price if is_reseller else normal_price
    final_price = base_price - (base_price * (VIP_DISCOUNT_PERCENTAGE / 100)) if is_vip else base_price
    savings = normal_price - final_price

    # External/API products use the remote API as the real inventory source.
    # Their local stock number is only a display buffer and must not block API purchases.
    external_enabled = bool(prod[9])
    external_product_id = (prod[10] or "").strip()
    api_provider = (prod[13] if len(prod) > 13 and prod[13] else 'bunty').lower()
    if not external_enabled and (prod[2] is None or prod[2] <= 0):
        return await call.answer("❌ This product is out of stock!", show_alert=True)
    if user[0] < final_price:
        needed = final_price - user[0]
        low_bal_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Add Balance Now",
                callback_data="menu_add_balance",
                icon_custom_emoji_id=get_emoji_icon("add_balance"),
                style="success"
            )],
            [InlineKeyboardButton(
                text="Back",
                callback_data=f"buy_{prod_id}",
                icon_custom_emoji_id=get_emoji_icon("back"),
                style="danger"
            )]
        ])
        return await call.message.edit_text(
            f"❌ <b>Insufficient Balance!</b>\n\n"
            f"💰 Your Balance: <b>{fmt_curr(user[0])}</b>\n"
            f"🛒 Required: <b>{fmt_curr(final_price)}</b>\n"
            f"➕ You need: <b>{fmt_curr(needed)} more</b>\n\n"
            "👇 Add balance to continue your purchase:",
            reply_markup=low_bal_kb,
            parse_mode='HTML'
        )

    # Prevent double-click purchases while processing the external API.
    await call.answer("⏳ Processing your purchase...", show_alert=False)
    delivered_key = None
    api_response = None

    # Charge first; external API failures are refunded immediately.
    db_query("UPDATE users SET balance=?, spent=spent+?, orders_count=orders_count+1, total_saved=total_saved+? WHERE user_id=?",
             (user[0] - final_price, final_price, savings, call.from_user.id))

    # Give the user a visible 3-second secure-delivery window before the key is shown.
    # The per-user/product asyncio lock plus wallet cooldown blocks duplicate taps.
    await _show_purchase_timer(call, 3)

    # Check if a manual key is already loaded in the vault first
    key_data = db_query("SELECT id, key_text FROM product_keys WHERE product_id=? AND is_used=0 LIMIT 1", (prod_id,), fetchone=True)
    if key_data:
        delivered_key = str(key_data[1])
        db_query("UPDATE product_keys SET is_used=1 WHERE id=?", (key_data[0],))
        db_query("UPDATE products SET stock=CASE WHEN stock>0 THEN stock-1 ELSE 0 END WHERE id=?", (prod_id,))
    elif external_enabled:
        if not external_product_id:
            db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (final_price, call.from_user.id))
            return await call.message.edit_text("❌ API product ID is not configured for this product.", reply_markup=back_kb("menu_shop"), parse_mode='HTML')

        if api_provider == "keypanel":
            try:
                logger.info("Key Panel API BUY pid=%r user=%r", external_product_id, call.from_user.id)
                api_response = await keypanel_buy_key(external_product_id, quantity=1, customer_tag=str(call.from_user.id))
            except Exception as api_exc:
                logger.exception("Key Panel API call crashed")
                api_response = {"status": "error", "message": f"API call failed: {api_exc}"}

            status_val = str(api_response.get("status", "")).lower()
            if api_response.get("success") is not True and status_val not in ["success", "true", "200"]:
                db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (final_price, call.from_user.id))
                error_msg = api_response.get("message") or api_response.get("msg") or "Key Panel returned an error"
                error_text = f"❌ <b>Key Panel API Error:</b> {html.escape(str(error_msg))}\n\n💰 Your balance has been refunded."
                try:
                    await call.message.edit_text(error_text, reply_markup=back_kb("menu_shop"), parse_mode='HTML')
                except Exception:
                    await bot.send_message(call.from_user.id, error_text, reply_markup=back_kb("menu_shop"), parse_mode='HTML')
                return

            api_data = api_response.get("data") or {}
            delivered_key = api_data.get("key") or api_data.get("keys") if isinstance(api_data, dict) else None
            if not delivered_key:
                delivered_key = api_response.get("key") or api_response.get("keys")
            if isinstance(delivered_key, list):
                delivered_key = "\n".join(str(x) for x in delivered_key)
            if delivered_key is None or str(delivered_key).strip() in ("", "KEY_NOT_FOUND"):
                db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (final_price, call.from_user.id))
                return await call.message.edit_text("❌ Key Panel returned no key.\n\n💰 Your balance has been refunded.", reply_markup=back_kb("menu_shop"), parse_mode='HTML')
            delivered_key = str(delivered_key)
        else:
            # Bunty (adminpanels.shop / bantibhaiya.to) API flow
            api_duration_saved = (prod[12] or "").strip()
            validity_saved = (prod[4] or "").strip()
            name_saved = (prod[0] or "").strip()

            raw_candidates = [c for c in (api_duration_saved, validity_saved, name_saved) if c]
            duration_candidates = []

            def add_candidate(val):
                val = str(val or "").strip()
                if val and val not in duration_candidates:
                    duration_candidates.append(val)

            for c in raw_candidates:
                add_candidate(c)
                add_candidate(normalize_api_duration(c))
                c_days = re.sub(r'\b(day|days)\b', 'DaYs', c, flags=re.IGNORECASE)
                add_candidate(c_days)
                c_upper = c.upper()
                add_candidate(c_upper)

            api_response = {"status": "error", "msg": "No API duration configured"}
            api_duration_used = ""
            for api_duration in duration_candidates:
                try:
                    logger.info("Trying Bunty API duration=%r for product_id=%r", api_duration, external_product_id)
                    api_response = await fetch_external_key(external_product_id, api_duration, "")
                except Exception as api_exc:
                    logger.exception("Bunty API purchase call crashed")
                    api_response = {"status": "error", "msg": f"API call failed: {api_exc}"}

                if api_response.get("status") == "success":
                    api_duration_used = api_duration
                    break

                error_blob = json.dumps(api_response, ensure_ascii=False).lower()
                retry_keywords = ["out of stock", "price not found", "price_not_found", "duration", "not found", "invalid"]
                if not any(k in error_blob for k in retry_keywords):
                    break

            if api_response.get("status") != "success":
                db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (final_price, call.from_user.id))
                error_msg = api_response.get("msg", "Unknown API error")
                error_text = f"❌ <b>Bunty API Error:</b> {error_msg}\n\n💰 Your balance has been refunded."
                try:
                    await call.message.edit_text(
                        error_text,
                        reply_markup=back_kb("menu_shop"),
                        parse_mode='HTML'
                    )
                except Exception as tg_error:
                    logger.exception("Could not edit purchase message after API failure: %s", tg_error)
                    try:
                        await bot.send_message(
                            call.from_user.id,
                            error_text,
                            reply_markup=back_kb("menu_shop"),
                            parse_mode='HTML'
                        )
                    except Exception:
                        pass
                return

            delivered_key = api_response.get("key")
            if isinstance(delivered_key, list):
                delivered_key = "\n".join(str(x) for x in delivered_key)
            if delivered_key is None or str(delivered_key).strip() in ("", "KEY_NOT_FOUND"):
                db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (final_price, call.from_user.id))
                return await call.message.edit_text("❌ Bunty API returned no key.\n\n💰 Your balance has been refunded.", reply_markup=back_kb("menu_shop"), parse_mode='HTML')
            delivered_key = str(delivered_key)
        # External API products are not limited by local key-vault stock.
        # Keep the admin-entered display stock unchanged so the product never
        # becomes "Out of Stock" after a successful API purchase.
    else:
        db_query("UPDATE users SET balance=balance+? WHERE user_id=?", (final_price, call.from_user.id))
        return await call.message.edit_text("❌ No manual key is available.\n\n💰 Your balance has been refunded.", reply_markup=back_kb("menu_shop"), parse_mode='HTML')

    if user[1]:
        commission = final_price * 0.15
        db_query("UPDATE users SET balance=balance+?, referral_earned=referral_earned+? WHERE user_id=?", (commission, commission, user[1]))
        try:
            await bot.send_message(user[1], f"🎁 <b>Referral Bonus Added!</b>\nYou earned {fmt_curr(commission)} from a successful purchase.", parse_mode='HTML')
        except Exception:
            pass

    product_full_name = f"{prod[6]} - {prod[8]} ({prod[0]})"
    db_query("INSERT INTO orders (user_id, product_name, price_paid, delivered_key, purchase_date) VALUES (?, ?, ?, ?, ?)",
             (call.from_user.id, product_full_name, final_price, delivered_key, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    log_activity(call.from_user.id, "PURCHASE_SUCCESS", f"Product: {product_full_name}, Paid: {final_price}, External API: {external_enabled}")
    await send_advanced_notification(call.from_user.id, "ORDER", final_price, product=product_full_name, key=delivered_key)
    try:
        await send_payment_proof_channel(
            call.from_user.id, product_full_name, final_price,
            order_id=str(db_query("SELECT id FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1", (call.from_user.id,), fetchone=True)[0])
                if db_query("SELECT id FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1", (call.from_user.id,), fetchone=True) else "",
            api_response=api_response if external_enabled else None
        )
    except Exception:
        logger.exception("Payment proof channel notification failed")

    msg = (f"✅ <b>PURCHASE SUCCESSFUL!</b>\n━━━━━━━━━━━━━━━━━━\n"
           f"📦 <b>Panel:</b> {prod[6]}\n📁 <b>Panel Name:</b> {prod[8]}\n"
           f"⏱ <b>Package:</b> {prod[0]}\n💰 <b>Amount Deducted:</b> {fmt_curr(final_price)}\n"
           f"📱 <b>Device Limit:</b> {prod[5]}\n━━━━━━━━━━━━━━━━━━\n")
    if prod[3] and prod[3].startswith("http"):
        msg += f"📥 <b>APK Link:</b> <a href='{prod[3]}'>Click Here to Download</a>\n\n"
    msg += f"🔑 <b>Your Exclusive Key:</b>\n<code>{delivered_key}</code>\n\n"
    if external_enabled and api_response:
        if api_response.get("product"):
            msg += f"📌 <b>Product:</b> {api_response['product']}\n"
        if api_response.get("duration"):
            msg += f"⏳ <b>Duration:</b> {api_response['duration']}\n"
        if api_response.get("order_id"):
            msg += f"🧾 <b>Order ID:</b> <code>{api_response['order_id']}</code>\n"
        if api_response.get("expires_at"):
            msg += f"⏰ <b>Expires:</b> <code>{api_response['expires_at']}</code>\n"
    msg += f"\n<i>For any issues, tap Support or contact: {ADMIN_CONTACT}</i>"
    await call.message.edit_text(msg, reply_markup=back_kb("menu_shop"), disable_web_page_preview=True, parse_mode='HTML')

# ==============================================================================
# 14. USER DASHBOARD, FILES, VIP, RESELLER, ORDERS, PROFILE, REFERRAL
# ==============================================================================
@dp.callback_query(F.data == "menu_top_buyers")
async def top_buyers_leaderboard(call: CallbackQuery):
    """Show the five users with the highest value of successfully completed purchases."""
    reset_at = get_setting("top_sellers_reset_at", "1970-01-01 00:00:00") or "1970-01-01 00:00:00"
    rows = db_query(
        """
        SELECT u.user_id, u.first_name, u.username,
               COUNT(o.id) AS buy_count,
               COALESCE(SUM(o.price_paid), 0) AS total_bought
        FROM orders o
        JOIN users u ON u.user_id = o.user_id
        WHERE COALESCE(o.purchase_date, '') > ?
        GROUP BY u.user_id, u.first_name, u.username
        ORDER BY total_bought DESC, buy_count DESC, u.user_id ASC
        LIMIT 5
        """,
        (reset_at,),
        fetchall=True
    ) or []

    medals = ["🥇", "🥈", "🥉", "🏅", "🎖️"]
    lines = [
        "🏆 <b><u>TOP 5 BUYERS LEADERBOARD</u></b> 🏆",
        "━━━━━━━━━━━━━━━━━━",
        "<i>Ranked by total successful purchase value.</i>",
        ""
    ]

    if not rows:
        lines.append("📭 <b>No completed purchases yet.</b>")
    else:
        for idx, row in enumerate(rows, 1):
            user_id, first_name, username, buy_count, total_bought = row
            safe_name = html.escape(str(first_name or "User"))
            lines.extend([
                f"{medals[idx - 1]} <b>TOP {idx}</b> — {safe_name}",
                f"👤 <b>Name:</b> {safe_name}",
                f"🛒 <b>Total Bought:</b> {int(buy_count)} order(s)",
                f"💰 <b>Total Invested:</b> {fmt_curr(float(total_bought or 0))}",
                "━━━━━━━━━━━━━━━━━━"
            ])

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔄 Refresh Leaderboard", callback_data="menu_top_buyers",
            icon_custom_emoji_id=get_emoji_icon("global_stats"), style="primary"
        )],
        [InlineKeyboardButton(
            text="BACK", callback_data="back_main",
            icon_custom_emoji_id=get_emoji_icon("back"), style="danger"
        )]
    ])
    await call.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data == "admin_reset_top_sellers")
async def admin_reset_top_sellers(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    reset_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_setting("top_sellers_reset_at", reset_at)
    log_activity(call.from_user.id, "ADMIN_RESET_TOP_SELLERS", f"Reset leaderboard at {reset_at}")
    await call.answer("🏆 Top Sellers reset. New purchases will start a fresh leaderboard.", show_alert=True)
    await call.message.edit_text(
        f"🏆 <b>TOP SELLERS RESET</b>\n\n"
        f"Leaderboard reset time: <code>{reset_at}</code>\n"
        "Previous purchase history is preserved; only the leaderboard window was reset.",
        reply_markup=admin_kb(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "menu_payment_proof")
async def payment_proof_menu(call: CallbackQuery):
    channel = (get_setting("payment_proof_channel", "") or "").strip()
    if not channel or channel.lower() in ("none", "off", "disabled"):
        return await call.answer("⚠️ Payment Proof channel is not configured yet.", show_alert=True)
    link = channel
    if link.startswith("@"):
        link = "https://t.me/" + link[1:]
    elif not link.startswith(("http://", "https://")):
        link = "https://t.me/" + link.lstrip("@")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧾 Open Payment Proof Channel ↗️", url=link, style="success")]
    ])
    await call.message.edit_text(
        "🧾 <b>PAYMENT PROOF</b>\n\n"
        "Here you can view successful purchase proofs.\n"
        "🔐 Delivered keys are never posted in the public channel.",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query(F.data == "menu_all_files")
async def all_files_handler(call: CallbackQuery):
    link_q = db_query("SELECT value FROM settings WHERE key='all_files_link'", fetchone=True)
    link = link_q[0] if link_q and link_q[0] != 'None' else None
    if link:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Access Download Channel ↗️", url=link, icon_custom_emoji_id=get_emoji_icon("download"), style="success")],
            [InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
        ])
        text = get_ui_text("download_files")
        await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')
    else:
        await call.answer("⚠️ Admin has not configured the private download channel link yet.", show_alert=True)

@dp.callback_query(F.data == "menu_vip_dash")
async def vip_dashboard(call: CallbackQuery):
    u = db_query("SELECT balance, is_vip, vip_since FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    is_vip = bool(u[1])
    status_str = "🟢 Active (Lifetime)" if is_vip else "🔴 Not Subscribed"
    text = get_ui_text("vip_menu", vip_status=status_str)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    if is_vip:
        text += f"\n📅 <b>Member Since:</b> {u[2]}\n\nEnjoy your permanent 15% discount!"
    else:
        text += f"\n\n💳 <b>Your Current Balance:</b> {fmt_curr(u[0])}\n"
        if u[0] >= VIP_PRICE_INR: 
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"✅ Purchase VIP for {fmt_curr(VIP_PRICE_INR)}", callback_data="execute_vip_upgrade", icon_custom_emoji_id=get_emoji_icon("vip"), style="success")])
        else:
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"❌ Need {fmt_curr(VIP_PRICE_INR)} to Upgrade", callback_data="ignore_stock_click", style="danger")])
            kb.inline_keyboard.append([InlineKeyboardButton(text="Add Balance Now", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "execute_vip_upgrade")
async def execute_vip_upgrade(call: CallbackQuery):
    u = db_query("SELECT balance, is_vip FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    if u[1]: return await call.answer("⚠️ You are already a VIP Member!", show_alert=True)
    if u[0] < VIP_PRICE_INR: return await call.answer(f"❌ Your balance dropped below {VIP_PRICE_INR}.", show_alert=True)
    new_balance = u[0] - VIP_PRICE_INR
    now_date = datetime.now().strftime("%Y-%m-%d")
    db_query("UPDATE users SET balance=?, is_vip=1, vip_since=? WHERE user_id=?", (new_balance, now_date, call.from_user.id))
    log_activity(call.from_user.id, "UPGRADED_VIP")
    await notify_admins(f"🌟 <b>NEW VIP UPGRADE</b>\n👤 User ID: <code>{call.from_user.id}</code>")
    await call.answer("🎉 Upgrade Successful! You are now a VIP Member.", show_alert=True)
    await vip_dashboard(call)

@dp.callback_query(F.data == "menu_reseller_dash")
async def reseller_dashboard(call: CallbackQuery):
    u = db_query("SELECT balance, is_reseller, reseller_since, total_saved FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    status_check = db_query("SELECT value FROM settings WHERE key='reseller_system_status'", fetchone=True)
    system_status = status_check[0] if status_check else "ON"
    setup_fee = float(get_setting("reseller_setup_fee", "200.0"))
    min_balance = float(get_setting("reseller_min_balance", "500.0"))
    if u[1]: 
        text = (f"{get_emoji('shield_icon')} <b><u>— RESELLER DASHBOARD —</u></b> {get_emoji('shield_icon')}\n\n🟢 <b>Status:</b> Active\n📅 <b>Since:</b> {u[2]}\n{get_emoji('money_icon')} <b>Total Saved:</b> {fmt_curr(u[3])}\n\n🎉 You are enjoying exclusive wholesale prices on all products!")
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]])
        await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')
        return
    if system_status == "OFF": return await call.answer("⚠️ Wholesale / Reseller registrations are currently closed by Admin.", show_alert=True)
    text = (f"⚡ <b><u>— BECOME A RESELLER —</u></b> ⚡\n\nUpgrade your account to access wholesale <b>Reseller Prices</b>!\n\n📋 <b>Requirements to Upgrade:</b>\n1️⃣ Must have a minimum balance of <b>{fmt_curr(min_balance)}</b>.\n2️⃣ A one-time setup fee of <b>{fmt_curr(setup_fee)}</b> will be deducted.\n\n💳 <b>Your Current Balance:</b> {fmt_curr(u[0])}\n")
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    if u[0] >= min_balance: 
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"✅ Pay {fmt_curr(setup_fee)} & Become Reseller", callback_data="execute_reseller_upgrade", icon_custom_emoji_id=get_emoji_icon("reseller"), style="success")])
    else:
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"❌ Insufficient Balance (Need {fmt_curr(min_balance)})", callback_data="ignore_stock_click", style="danger")])
        kb.inline_keyboard.append([InlineKeyboardButton(text="Add Balance", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "execute_reseller_upgrade")
async def execute_reseller_upgrade(call: CallbackQuery):
    setup_fee = float(get_setting("reseller_setup_fee", "200.0"))
    min_balance = float(get_setting("reseller_min_balance", "500.0"))
    u = db_query("SELECT balance, is_reseller FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    if u[1]: return await call.answer("⚠️ You are already a Reseller!", show_alert=True)
    if u[0] < min_balance: return await call.answer(f"❌ Your balance dropped below {fmt_curr(min_balance)}. Please top up.", show_alert=True)
    new_balance = u[0] - setup_fee
    db_query("UPDATE users SET balance=?, is_reseller=1, reseller_since=?, account_type='Reseller' WHERE user_id=?", (new_balance, datetime.now().strftime("%Y-%m-%d"), call.from_user.id))
    log_activity(call.from_user.id, "UPGRADED_RESELLER")
    await notify_admins(f"👑 <b>NEW RESELLER UPGRADE</b>\n👤 User ID: <code>{call.from_user.id}</code>")
    await call.answer("🎉 Upgrade Successful! Welcome to the Reseller tier.", show_alert=True)
    await reseller_dashboard(call)

@dp.callback_query(F.data == "menu_orders")
async def my_orders(call: CallbackQuery):
    orders = db_query("SELECT product_name, delivered_key, purchase_date, price_paid FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (call.from_user.id,), fetchall=True)
    if not orders: return await call.message.edit_text("🧾 You haven't made any purchases yet. Your vault is empty.", reply_markup=back_kb(), parse_mode='HTML')
    text = "🧾 <b><u>— YOUR RECENT ORDERS (LAST 10) —</u></b> 🧾\n\n"
    for o in orders: text += f"📦 <b>{o[0]}</b> ({fmt_curr(o[3])})\n🔑 <code>{o[1]}</code>\n📅 <i>{o[2]}</i>\n━━━━━━━━━━━━━━━━\n"
    await call.message.edit_text(text, reply_markup=back_kb(), parse_mode='HTML')

@dp.callback_query(F.data == "menu_profile")
async def show_profile(call: CallbackQuery):
    u = db_query("SELECT user_id, first_name, account_type, balance, orders_count, spent, referrals_count, joined_date, is_reseller, reseller_since, total_saved, is_vip FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    acc_type_display = []
    if u[8]: acc_type_display.append(f"{get_emoji('reseller')} Reseller")
    if u[11]: acc_type_display.append(f"{get_emoji('vip')} VIP")
    type_str = " | ".join(acc_type_display) if acc_type_display else f"{get_emoji('regular_user')} Regular User"
    text = (
        f"{get_emoji('grid_id')} <b><u>— YOUR SECURE PROFILE —</u></b> {get_emoji('grid_id')}\n\n"
        f"{get_emoji('grid_id')} <b>Grid ID:</b> <code>{u[0]}</code>\n"
        f"{get_emoji('name')} <b>Name:</b> {u[1]}\n"
        f"{get_emoji('account_level')} <b>Account Level:</b> {type_str}\n\n"
        f"{get_emoji('wallet_left')} <b>— Wallet —</b> {get_emoji('wallet_right')}\n"
        f"{get_emoji('wallet_left')} <b>Current Balance:</b> {fmt_curr(u[3])} {get_emoji('wallet_right')}\n\n"
        f"{get_emoji('global_stats')} <b>— Global Statistics —</b>\n"
        f"{get_emoji('total_orders')} <b>Total Orders:</b> {u[4]}\n"
        f"{get_emoji('total_spent')} <b>Total Spent:</b> {fmt_curr(u[5])}\n"
        f"{get_emoji('total_referrals')} <b>Total Referrals:</b> {u[6]}\n\n"
    )
    if u[8]:
        text += f"{get_emoji('shield_icon')} <b>— RESELLER METRICS —</b> {get_emoji('shield_icon')}\n{get_emoji('money_icon')} <b>Total Saved via Reseller:</b> {fmt_curr(u[10])}\n\n"
    text += f"{get_emoji('joined_grid')} <b>Joined Grid:</b> {u[7]}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Redeem Promo Code",
            callback_data="redeem_coupon",
            icon_custom_emoji_id=get_emoji_icon('redeem_icon'),
            style="success"
        )],
        [InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "redeem_coupon")
async def redeem_coupon_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("🎟 <b>Please enter your VIP / Promo redeem code below:</b>", reply_markup=back_kb("menu_profile"), parse_mode='HTML')
    await state.set_state(UserStates.wait_for_redeem)

@dp.message(UserStates.wait_for_redeem)
async def process_redeem(m: Message, state: FSMContext):
    code = m.text.strip().upper()
    user_id = m.from_user.id
    if db_query("SELECT * FROM redeemed WHERE user_id=? AND code=?", (user_id, code), fetchone=True):
        await m.answer("❌ Anti-Fraud Alert: You already redeemed this unique code!", reply_markup=main_menu_kb(m.from_user.id), parse_mode='HTML')
        await state.clear()
        return
    coupon = db_query("SELECT amount, uses_left FROM coupons WHERE code=?", (code,), fetchone=True)
    if not coupon: await m.answer("❌ Invalid or Expired Code!", reply_markup=main_menu_kb(m.from_user.id), parse_mode='HTML')
    elif coupon[1] <= 0: await m.answer("❌ This code's usage limit has been fully claimed by other users.", reply_markup=main_menu_kb(m.from_user.id), parse_mode='HTML')
    else:
        db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (coupon[0], user_id))
        db_query("UPDATE coupons SET uses_left = uses_left - 1 WHERE code=?", (code,))
        db_query("INSERT INTO redeemed (user_id, code) VALUES (?, ?)", (user_id, code))
        log_activity(user_id, "PROMO_REDEEMED", f"Code: {code}, Amount: {coupon[0]}")
        await m.answer(f"🎉 <b>Success!</b>\nSafely added {fmt_curr(coupon[0])} to your balance!", reply_markup=main_menu_kb(m.from_user.id), parse_mode='HTML')
        user_info = db_query("SELECT first_name FROM users WHERE user_id=?", (user_id,), fetchone=True)
        uname = user_info[0] if user_info else "Unknown User"
        await notify_admins(f"🎟 <b>PROMO CODE REDEEMED!</b>\n👤 User: {uname} (<code>{user_id}</code>)\n🔖 Code: <b>{code}</b>\n💵 Amount: {fmt_curr(coupon[0])}")
    await state.clear()

@dp.callback_query(F.data == "menu_referral")
async def show_referral(call: CallbackQuery):
    u = db_query("SELECT referrals_count, referral_earned FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    ref_link = f"https://t.me/{bot_username_clean()}?start=ref_{call.from_user.id}"
    text = (f"{get_emoji('referral')} <b><u>AFFILIATE PROGRAM</u></b> {get_emoji('referral')}\n\n✅ <b>Status:</b> ACTIVE\n💰 Earn <b>2% flat commission</b> on every successful purchase made by your referred friends!\n\n📊 <b>YOUR STATS:</b>\n👥 Total Invited: {u[0]}\n💵 Life-time Earned: {fmt_curr(u[1])}\n\n🔗 <b>Your Invite Link:</b>\n<code>{ref_link}</code>\n\n<i>Simply copy and share this link to start earning!</i>")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

# ==============================================================================
# 15. LUDO / DICE SPIN
# ==============================================================================
@dp.callback_query(F.data == "menu_spin_landing")
async def lucky_spin_landing(call: CallbackQuery):
    status_check = db_query("SELECT value FROM settings WHERE key='spin_status'", fetchone=True)
    spin_status = status_check[0] if status_check else "ON"
    if spin_status == "OFF": return await call.answer("⚠️ Lucky Ludo Spin is currently disabled by Admin.", show_alert=True)
    await call.message.edit_text(f"{get_emoji('ludo_spin')} <b><u>— LUDO SPIN —</u></b> {get_emoji('ludo_spin')}\n\nTest your luck! You can spin once every 24 hours.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Spin Dice Now!", callback_data="execute_spin", icon_custom_emoji_id=get_emoji_icon("ludo_spin"), style="success")], 
        [InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ]), parse_mode='HTML')

@dp.callback_query(F.data == "execute_spin")
async def execute_spin(call: CallbackQuery):
    status_check = db_query("SELECT value FROM settings WHERE key='spin_status'", fetchone=True)
    if status_check and status_check[0] == "OFF": return await call.answer("⚠️ Lucky Spin is disabled.", show_alert=True)
    u = db_query("SELECT last_spin, balance, is_vip FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    if not u:
        return await call.answer("❌ Account not found. Please use /start first.", show_alert=True)
    now = datetime.now()
    last_spin = u[0]
    balance = float(u[1]) if u[1] is not None else 0.0
    is_vip = bool(u[2])
    if last_spin:
        try:
            last_spin_dt = datetime.strptime(last_spin, "%Y-%m-%d %H:%M:%S")
            if now < last_spin_dt + timedelta(hours=24):
                remaining = last_spin_dt + timedelta(hours=24) - now
                hrs = int(remaining.total_seconds() // 3600)
                mins = int((remaining.total_seconds() % 3600) // 60)
                return await call.message.edit_text(
                    f"❌ <b>Cooldown Active!</b>\nYou already played today.\n\n"
                    f"⏳ Come back in <b>{hrs}h {mins}m</b>!",
                    reply_markup=back_kb(), parse_mode='HTML'
                )
        except Exception:
            pass
    await call.message.delete()
    dice_msg = await bot.send_dice(chat_id=call.message.chat.id, emoji="🎲")
    await asyncio.sleep(SPIN_DELAY_SECONDS)
    dice_val = dice_msg.dice.value
    today = now.strftime("%Y-%m-%d")
    pool_row = db_query("SELECT value FROM settings WHERE key='daily_spin_pool'", fetchone=True)
    spent_row = db_query("SELECT value FROM settings WHERE key='daily_spin_spent'", fetchone=True)
    spent_date_row = db_query("SELECT value FROM settings WHERE key='daily_spin_spent_date'", fetchone=True)
    daily_pool = max(0.0, float(pool_row[0]) if pool_row and pool_row[0] is not None else 5.0)
    spent_today = float(spent_row[0]) if spent_row and spent_row[0] is not None else 0.0
    saved_date = spent_date_row[0] if spent_date_row else ""
    if saved_date != today:
        spent_today = 0.0
        set_setting("daily_spin_spent", "0.0")
        set_setting("daily_spin_spent_date", today)
    remaining_pool = max(0.0, round(daily_pool - spent_today, 2))

    # Pick only rewards that can actually fit inside the remaining global pool.
    # VIP 2x is also capped by the same pool, so total payouts can never exceed it.
    max_base_reward = remaining_pool / 2.0 if is_vip else remaining_pool
    rewards_db = db_query("SELECT amount FROM spin_rewards WHERE amount >= 0 AND amount <= ?", (max_base_reward,), fetchall=True)
    rewards_list = [float(r[0]) for r in rewards_db] if rewards_db else [0.0]
    reward = float(random.choice(rewards_list))
    if is_vip and reward > 0:
        reward *= 2.0
    reward = min(round(reward, 2), remaining_pool)
    spent_today = round(spent_today + reward, 2)
    set_setting("daily_spin_spent", f"{spent_today:.2f}")
    set_setting("daily_spin_spent_date", today)
    new_bal = balance + reward
    db_query("UPDATE users SET balance=?, last_spin=? WHERE user_id=?", (new_bal, now.strftime("%Y-%m-%d %H:%M:%S"), call.from_user.id))
    log_activity(call.from_user.id, "PLAYED_SPIN", f"Reward: {reward}, Dice: {dice_val}")
    msg = get_ui_text("lucky_dice_result", dice_value=dice_val, won_amount=fmt_curr(reward), new_balance=fmt_curr(new_bal))
    if is_vip and reward > 0: msg += "\n\n<i>🌟 VIP Bonus: 2x Multiplier Applied!</i>"
    await dice_msg.reply(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="BACK TO MENU", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="success")]]), parse_mode='HTML')

# ==============================================================================
# 16. TUTORIALS & SUPPORT
# ==============================================================================
@dp.callback_query(F.data == "menu_how_to")
async def tutorial_system(call: CallbackQuery):
    video_link_query = db_query("SELECT value FROM settings WHERE key='how_to_video'", fetchone=True)
    video_link = video_link_query[0] if video_link_query and video_link_query[0] != 'None' else None
    text = (f"{get_emoji('tutorial')} <b><u>— TUTORIALS & GUIDE —</u></b> {get_emoji('tutorial')}\n\n1️⃣ Add funds via <b>Add Balance</b>\n2️⃣ Navigate to <b>Product Store</b>\n3️⃣ Choose your desired Panel and Package validity.\n4️⃣ The Key and Installation APK link will be instantly provided.")
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    if video_link: kb.inline_keyboard.append([InlineKeyboardButton(text="Watch Full Video Tutorial", url=video_link, icon_custom_emoji_id=get_emoji_icon("tutorial"), style="success")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "menu_support")
async def support_center(call: CallbackQuery):
    telegram_link = get_setting("support_telegram", "https://t.me/rajuconfigyt")
    whatsapp_link = get_setting("support_whatsapp", "https://wa.me/YOUR_NUMBER")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Contact on Telegram", url=telegram_link, icon_custom_emoji_id=get_emoji_icon("telegram"), style="primary")],
        [InlineKeyboardButton(text="Contact on WhatsApp", url=whatsapp_link, icon_custom_emoji_id=get_emoji_icon("whatsapp"), style="primary")],
        [InlineKeyboardButton(text="Open New Ticket", callback_data="open_ticket", icon_custom_emoji_id=get_emoji_icon("support"), style="success"), 
         InlineKeyboardButton(text="My Open Tickets", callback_data="my_tickets", icon_custom_emoji_id=get_emoji_icon("history"), style="success")], 
        [InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text(f"{get_emoji('telegram')}{get_emoji('whatsapp')} <b><u>— PREMIUM SUPPORT CENTER —</u></b>\n\nContact us via Telegram or WhatsApp for instant help, or open a support ticket for admin assistance.", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "my_tickets")
async def view_my_tickets(call: CallbackQuery):
    tickets = db_query("SELECT id, message, status, created_at FROM tickets WHERE user_id=? ORDER BY id DESC LIMIT 5", (call.from_user.id,), fetchall=True)
    if not tickets: return await call.message.edit_text("📋 You do not have any active or previous support tickets.", reply_markup=back_kb("menu_support"), parse_mode='HTML')
    text = "📋 <b><u>— Your Recent Tickets —</u></b> 📋\n\n"
    for t in tickets:
        status_icon = "🟢" if t[2] == 'Open' else "🔴"
        text += f"🎫 <b>Ticket #{t[0]}</b> | Status: {status_icon} <b>{t[2]}</b>\n📅 <i>{t[3]}</i>\n📝 <i>{t[1][:80]}...</i>\n\n"
    await call.message.edit_text(text, reply_markup=back_kb("menu_support"), parse_mode='HTML')

@dp.callback_query(F.data == "open_ticket")
async def open_ticket_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("📝 <b>Please type your issue/message below in detail:</b>", reply_markup=back_kb("menu_support"), parse_mode='HTML')
    await state.set_state(UserStates.wait_for_ticket)

@dp.message(UserStates.wait_for_ticket)
async def process_ticket(m: Message, state: FSMContext):
    db_query("INSERT INTO tickets (user_id, message, created_at) VALUES (?, ?, ?)", (m.from_user.id, m.text, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    await m.answer("✅ <b>Ticket Submitted Successfully!</b> Admins will reply soon.", reply_markup=main_menu_kb(m.from_user.id), parse_mode='HTML')
    await notify_admins(f"🚨 <b>NEW SUPPORT TICKET</b>\nFrom: <code>{m.from_user.id}</code>\nMsg: {m.text}")
    log_activity(m.from_user.id, "OPENED_TICKET")
    await state.clear()

# ==============================================================================
# 17. ADMIN PANEL
# ==============================================================================
@dp.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.clear()
    await message.answer("⚙️ <b>Advanced Admin Terminal</b>\n<i>Authorized Access Granted. Use the buttons below.</i>", reply_markup=admin_kb(), parse_mode='HTML')

@dp.callback_query(F.data == "admin_panel_back")
async def back_to_admin(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("⚙️ <b>Advanced Admin Terminal</b>\n<i>Authorized Access Granted. Use the buttons below.</i>", reply_markup=admin_kb(), parse_mode='HTML')

@dp.callback_query(F.data == "admin_toggle_vip_sys")
async def toggle_vip_sys(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    res = db_query("SELECT value FROM settings WHERE key='vip_status'", fetchone=True)
    current = res[0] if res else 'OFF'
    new_status = 'ON' if current == 'OFF' else 'OFF'
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('vip_status', ?)", (new_status,))
    await call.message.edit_reply_markup(reply_markup=admin_kb())

@dp.callback_query(F.data == "admin_user_control_start")
async def admin_user_control_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Download Full User List", callback_data="admin_download_userlist", icon_custom_emoji_id=get_emoji_icon("download"), style="success")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text("💻 <b>User Control Terminal</b>\n\n✏️ Enter the <b>User ID</b> or <b>@Username</b> you want to investigate or manage:\n\n👇 <b>OR</b> download the full user CSV format list:", reply_markup=kb, parse_mode='HTML')
    await state.set_state(AdminStates.manage_target_user)

@dp.callback_query(F.data == "admin_download_userlist")
async def admin_download_userlist(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    users = db_query("SELECT username, user_id, phone, balance, orders_count, is_vip, is_reseller FROM users", fetchall=True)
    if not users: return await call.answer("❌ No users found in the database.", show_alert=True)
    file_content = "FULL DATABASE DUMP\n" + "="*100 + "\n"
    for u in users:
        uname = u[0] if u[0] else "No_Username"
        uid = u[1]
        phone = u[2] if u[2] else "No_Phone"
        bal = u[3]
        orders = u[4]
        vip_status = "YES" if u[5] else "NO"
        res_status = "YES" if u[6] else "NO"
        file_content += f"UID: {uid} | UNAME: {uname} | PHONE: {phone} | BAL: ₹{bal:.2f} | BUY: {orders} | VIP: {vip_status} | RES: {res_status}\n"
    doc = BufferedInputFile(file_content.encode('utf-8'), filename=f"DB_{datetime.now().strftime('%Y%m%d')}.txt")
    await call.message.answer_document(document=doc, caption="📋 <b>Database export complete.</b>", parse_mode='HTML')
    await call.answer()

@dp.message(AdminStates.manage_target_user)
async def process_user_lookup(m: Message, state: FSMContext):
    target = m.text.strip()
    if target.startswith('@'): target = target[1:]
    loader_msg = await hacker_loading(m, "Querying User Database")
    user_q = db_query("SELECT user_id, first_name, username, balance, is_reseller, orders_count, spent, joined_date, is_banned, warnings, is_vip FROM users WHERE user_id=? OR username=? COLLATE NOCASE", (target, target), fetchone=True)
    if not user_q: return await loader_msg.edit_text("❌ Target not found in the grid. Check ID/Username syntax.", reply_markup=admin_back_kb(), parse_mode='HTML')
    u_id, u_name, u_user, bal, is_res, orders, spent, joined, is_banned, warnings, is_vip = user_q
    await state.update_data(target_u_id=u_id)
    status_emoji = "🔴 BANNED" if is_banned else "🟢 ACTIVE"
    tags = []
    if is_res: tags.append("👑 Reseller")
    if is_vip: tags.append("🌟 VIP")
    type_str = " | ".join(tags) if tags else "👤 Regular"
    text = (f"🛡 <b><u>USER CONTROL TERMINAL</u></b> 🛡\n━━━━━━━━━━━━━━━━━━\n📛 <b>Name:</b> {u_name} (@{u_user})\n🆔 <b>ID:</b> <code>{u_id}</code>\n📊 <b>Status:</b> {status_emoji}\n🔰 <b>Type:</b> {type_str}\n⚠️ <b>Warnings Issued:</b> {warnings}\n━━━━━━━━━━━━━━━━━━\n💰 <b>Wallet Balance:</b> {fmt_curr(bal)}\n📦 <b>Orders:</b> {orders} | 💸 <b>Total Spent:</b> {fmt_curr(spent)}\n📅 <b>Joined:</b> {joined}")
    ban_btn_text = "Unban ✅" if is_banned else "Ban 🚫"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Add Funds ➕", callback_data=f"usrctrl_add_{u_id}", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success"), 
         InlineKeyboardButton(text="Minus Funds ➖", callback_data=f"usrctrl_min_{u_id}", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="danger")],
        [InlineKeyboardButton(text=ban_btn_text, callback_data=f"usrctrl_ban_{u_id}", icon_custom_emoji_id=get_emoji_icon("shield_icon"), style="danger"), 
         InlineKeyboardButton(text="Warn User ⚠️", callback_data=f"usrctrl_warn_{u_id}", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="danger")],
        [InlineKeyboardButton(text="Give VIP 🌟" if not is_vip else "Remove VIP 🚫", callback_data=f"usrctrl_vip_{u_id}", icon_custom_emoji_id=get_emoji_icon("vip"), style="success")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await loader_msg.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("usrctrl_"))
async def handle_user_actions(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    action = call.data.split("_")[1]
    u_id = int(call.data.split("_")[2])
    await state.update_data(target_u_id=u_id)
    if action == "ban":
        current_status = db_query("SELECT is_banned FROM users WHERE user_id=?", (u_id,), fetchone=True)[0]
        if current_status == 0:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Yes, Ban", callback_data=f"confirm_ban_{u_id}", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="danger"), 
                 InlineKeyboardButton(text="Cancel", callback_data="admin_user_control_start", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
            ])
            await call.message.edit_text(f"⚠️ Are you sure you want to <b>BAN</b> user <code>{u_id}</code>?", reply_markup=kb, parse_mode='HTML')
            await state.set_state(AdminStates.confirm_ban)
        else:
            db_query("UPDATE users SET is_banned=0 WHERE user_id=?", (u_id,))
            await call.answer("✅ User unbanned successfully!", show_alert=True)
            m = call.message; m.text = str(u_id); await process_user_lookup(m, state)
    elif action == "vip":
        current_status = db_query("SELECT is_vip FROM users WHERE user_id=?", (u_id,), fetchone=True)[0]
        if current_status == 1:
            db_query("UPDATE users SET is_vip=0 WHERE user_id=?", (u_id,))
            await call.answer("✅ VIP Removed!", show_alert=True)
        else:
            db_query("UPDATE users SET is_vip=1, vip_since=? WHERE user_id=?", (datetime.now().strftime("%Y-%m-%d"), u_id))
            await call.answer("✅ VIP Granted!", show_alert=True)
        m = call.message; m.text = str(u_id); await process_user_lookup(m, state)
    elif action == "add":
        await call.message.edit_text("💰 Enter the amount to <b>ADD</b> to this user's wallet:", reply_markup=admin_back_kb(), parse_mode='HTML')
        await state.set_state(AdminStates.wait_for_add_money)
    elif action == "min":
        await call.message.edit_text("💸 Enter the amount to <b>DEDUCT</b> from this user's wallet:", reply_markup=admin_back_kb(), parse_mode='HTML')
        await state.set_state(AdminStates.wait_for_minus_money)
    elif action == "warn":
        await call.message.edit_text("⚠️ Type the strict warning message you want to send directly to this user:", reply_markup=admin_back_kb(), parse_mode='HTML')
        await state.set_state(AdminStates.wait_for_warning)

@dp.callback_query(F.data.startswith("confirm_ban_"))
async def confirm_ban(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    u_id = int(call.data.split("_")[2])
    db_query("UPDATE users SET is_banned=1 WHERE user_id=?", (u_id,))
    await call.answer("🔴 User has been banned!", show_alert=True)
    await state.clear()
    m = call.message; m.text = str(u_id); await process_user_lookup(m, state)

@dp.message(AdminStates.wait_for_add_money)
async def exec_add_money(m: Message, state: FSMContext):
    try:
        amt = float(m.text)
        data = await state.get_data()
        u_id = data['target_u_id']
        db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (amt, u_id))
        await m.answer(f"✅ Successfully added {fmt_curr(amt)} to target <code>{u_id}</code>.", reply_markup=admin_kb(), parse_mode='HTML')
        try: await bot.send_message(u_id, f"💰 <b>Wallet Top-up!</b>\nAdmin has manually added {fmt_curr(amt)} to your wallet.", parse_mode='HTML')
        except: pass
        await state.clear()
    except ValueError: await m.answer("❌ Critical Error: Input must be a valid number.")

@dp.message(AdminStates.wait_for_minus_money)
async def exec_minus_money(m: Message, state: FSMContext):
    try:
        amt = float(m.text)
        data = await state.get_data()
        u_id = data['target_u_id']
        db_query("UPDATE users SET balance = balance - ? WHERE user_id=?", (amt, u_id))
        await m.answer(f"✅ Successfully deducted {fmt_curr(amt)} from target <code>{u_id}</code>.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except ValueError: await m.answer("❌ Critical Error: Input must be a valid number.")

@dp.message(AdminStates.wait_for_warning)
async def exec_warn_user(m: Message, state: FSMContext):
    data = await state.get_data()
    u_id = data['target_u_id']
    warn_text = m.text
    db_query("UPDATE users SET warnings = warnings + 1 WHERE user_id=?", (u_id,))
    await m.answer(f"✅ Official warning dispatched to <code>{u_id}</code>.", reply_markup=admin_kb(), parse_mode='HTML')
    try: await bot.send_message(u_id, f"⚠️ <b>OFFICIAL WARNING FROM SYSTEM ADMIN:</b>\n\n{warn_text}\n\n<i>Subsequent infractions may lead to an automated grid ban.</i>", parse_mode='HTML')
    except: pass
    await state.clear()

# ==============================================================================
# 18. ADMIN STATISTICS
# ==============================================================================
@dp.callback_query(F.data == "admin_view_stats")
async def admin_dashboard_stats(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    t_users = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
    t_resellers = db_query("SELECT COUNT(*) FROM users WHERE is_reseller=1", fetchone=True)[0]
    t_vip = db_query("SELECT COUNT(*) FROM users WHERE is_vip=1", fetchone=True)[0]
    t_prods = db_query("SELECT COUNT(*) FROM products", fetchone=True)[0]
    t_keys = db_query("SELECT COUNT(*) FROM product_keys WHERE is_used=0", fetchone=True)[0]
    t_rev = db_query("SELECT SUM(spent) FROM users", fetchone=True)[0] or 0.0
    today_str = datetime.now().strftime("%Y-%m-%d")
    t_spins = db_query("SELECT COUNT(*) FROM users WHERE last_spin LIKE ?", (f"{today_str}%",), fetchone=True)[0]
    msg = (f"📊 <b><u>GRID INTELLIGENCE DASHBOARD</u></b> 📊\n━━━━━━━━━━━━━━━━━━\n👥 <b>Total Grid Users:</b> {t_users}\n👑 <b>Wholesale Resellers:</b> {t_resellers}\n🌟 <b>Elite VIP Members:</b> {t_vip}\n━━━━━━━━━━━━━━━━━━\n📦 <b>Active Products:</b> {t_prods}\n🔑 <b>Unused Keys in Vault:</b> {t_keys}\n💰 <b>Total Gross Revenue:</b> {fmt_curr(t_rev)}\n🎰 <b>Ludo Spins Today:</b> {t_spins}\n━━━━━━━━━━━━━━━━━━")
    await call.message.edit_text(msg, reply_markup=admin_back_kb(), parse_mode='HTML')

# ==============================================================================
# 19. ADMIN PRODUCT MANAGEMENT
# ==============================================================================
@dp.callback_query(F.data == "admin_quick_add_product")
async def admin_quick_add_product_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text(
        "⚡ <b>QUICK ADD PRODUCT</b>\n\n"
        "Send ONE line in this format:\n"
        "<code>Category | Panel | Package | Validity | Device | UserPrice | ResellerPrice | Provider | PID</code>\n\n"
        "Example:\n<code>ANDROID ROOT PANEL | BUNTY | 1 Hour | 1 Hour | 1 Device | 49 | 35 | bunty | 1234</code>\n\n"
        "For manual stock use <code>manual</code> as Provider and leave PID as <code>-</code>. API duration automatically uses Validity, so no extra duration step is required.",
        reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.quick_add_product)

@dp.message(AdminStates.quick_add_product)
async def admin_quick_add_product_save(m: Message, state: FSMContext):
    parts = [x.strip() for x in (m.text or "").split("|")]
    if len(parts) < 9:
        return await m.answer("❌ Format invalid. Use 9 fields separated by |. Example is shown above.")
    cat, panel, name, validity, device, price_s, rprice_s, provider, pid = parts[:9]
    provider = provider.lower()
    if provider in ("keypanel", "key panel", "kp"):
        provider = "keypanel"
    elif provider in ("bunty", "adminpanels", "bunti", "buntybhaiya"):
        provider = "bunty"
    elif provider not in ("manual", ""):
        return await m.answer("❌ Provider must be <code>bunty</code>, <code>keypanel</code>, or <code>manual</code>.", parse_mode='HTML')
    try:
        price, rprice = float(price_s), float(rprice_s)
        if price < 0 or rprice < 0: raise ValueError
    except ValueError:
        return await m.answer("❌ User Price and Reseller Price must be numbers.")
    external = 1 if provider in ("bunty", "keypanel") else 0
    ext_pid = "" if pid in ("-", "none", "") else pid
    api_duration = normalize_api_duration(validity) if external else ""
    db_query("""INSERT INTO products (category, panel_name, name, price_inr, reseller_price, stock, apk_link, validity, device_limit, external_enabled, external_product_id, requires_android_id, external_duration, api_provider) VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, 0, ?, ?)""",
             (cat, panel, name, price, rprice, 1 if external else 0, validity, device, external, ext_pid, api_duration, provider))
    await state.clear()
    await m.answer(f"✅ <b>Quick Product Added</b>\n\n📦 {html.escape(name)}\n🌐 Provider: <b>{provider}</b>\n🆔 PID: <code>{html.escape(ext_pid or '-')}</code>\n⏱ API Duration: <code>{html.escape(api_duration or '-')}</code>", reply_markup=admin_kb(), parse_mode='HTML')

@dp.callback_query(F.data == "admin_add_prod")
async def add_prod_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for cat in get_all_categories():
        emoji_id = get_category_emoji(cat)
        kb.inline_keyboard.append([InlineKeyboardButton(text=cat, callback_data=f"addprod_cat_{cat}", icon_custom_emoji_id=emoji_id, style="danger")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Cancel", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text("<b>Step 1:</b> Choose the <b>Category</b> for this product:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("addprod_cat_"))
async def add_prod_category_selected(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    category = call.data.split("addprod_cat_", 1)[1]
    await state.update_data(cat=category)
    await call.message.edit_text(f"<b>Step 2:</b> Enter <b>PANEL NAME</b>\n(e.g., 'MST PANEL', 'DRIP PANEL'):", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_panel_name)

@dp.message(AdminStates.add_prod_panel_name)
async def add_prod_panel_name(m: Message, state: FSMContext):
    await state.update_data(panel_name=(m.text or '').strip())
    await m.answer("<b>Step 3:</b> Enter <b>PACKAGE DURATION/DATE NAME</b>\n(e.g., '7 Days', '1 Month'):", parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_name)

@dp.message(AdminStates.add_prod_name)
async def add_prod_name(m: Message, state: FSMContext):
    await state.update_data(name=(m.text or '').strip())
    await m.answer("⏳ Enter Time Validity String (e.g., '24 Hours'):", parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_validity)

@dp.message(AdminStates.add_prod_validity)
async def add_prod_validity(m: Message, state: FSMContext):
    await state.update_data(validity=(m.text or '').strip())
    await m.answer("📱 Enter strict Device Enforcement Limit (e.g., '1 Device HWID'):", parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_device_limit)

@dp.message(AdminStates.add_prod_device_limit)
async def add_prod_device_limit(m: Message, state: FSMContext):
    await state.update_data(device_limit=(m.text or '').strip())
    await m.answer("💰 Enter standard **User Price** in Rupees (₹) (e.g., 500):", parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_price)

@dp.message(AdminStates.add_prod_price)
async def add_prod_price(m: Message, state: FSMContext):
    try:
        await state.update_data(price=float(m.text))
        await m.answer("👑 Enter wholesale **Reseller Price** in Rupees (₹) (e.g., 300):", parse_mode='HTML')
        await state.set_state(AdminStates.add_prod_reseller_price)
    except ValueError: await m.answer("❌ Invalid input datatype! Must be numerical.")

@dp.message(AdminStates.add_prod_reseller_price)
async def add_prod_reseller_price(m: Message, state: FSMContext):
    try:
        await state.update_data(reseller_price=float(m.text))
        await m.answer("🔗 Enter direct APK/Payload Download Link (or type 'none' to omit):", parse_mode='HTML')
        await state.set_state(AdminStates.add_prod_apk)
    except ValueError: await m.answer("❌ Invalid input datatype! Must be numerical.")

@dp.message(AdminStates.add_prod_apk)
async def add_prod_apk(m: Message, state: FSMContext):
    await state.update_data(apk="" if m.text.lower() == 'none' else m.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Bunty API (AdminPanels)", callback_data="addprod_api_bunty", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="success")],
        [InlineKeyboardButton(text="FFPanel Key API", callback_data="addprod_api_keypanel", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="success")],
        [InlineKeyboardButton(text="Manual Stock (Keys)", callback_data="addprod_ext_no", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await m.answer(
        "🔗 <b>Choose Key Source / API Provider:</b>\n\n"
        "⚡ <b>Bunty API</b>: Uses AdminPanels Reseller API\n"
        "🛒 <b>FFPanel Key API</b>: Uses reseller-v2 plans and license-key API\n"
        "📦 <b>Manual Stock</b>: Pre-loaded manual keys from vault",
        reply_markup=kb, parse_mode='HTML'
    )
    await state.set_state(AdminStates.add_prod_external)

@dp.callback_query(F.data.in_(["addprod_api_bunty", "addprod_ext_yes"]), AdminStates.add_prod_external)
async def add_prod_api_bunty(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.update_data(external_enabled=1, api_provider="bunty")
    await call.message.edit_text("⚡ <b>Bunty API Selected (AdminPanels)</b>\n\nEnter the <b>Bunty Product ID (PID)</b>:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_external_product_id)

@dp.callback_query(F.data == "addprod_api_keypanel", AdminStates.add_prod_external)
async def add_prod_api_keypanel(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.update_data(external_enabled=1, api_provider="keypanel")
    await call.message.edit_text("🛒 <b>FFPanel Reseller API Selected</b>\n\nEnter the API <b>Plan ID</b> (shown by Get Plans):", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_external_product_id)

@dp.message(AdminStates.add_prod_external_product_id)
async def add_prod_external_pid(m: Message, state: FSMContext):
    pid = (m.text or "").strip()
    if not pid:
        return await m.answer("❌ Product PID / Variant ID cannot be empty.")
    data = await state.get_data()
    provider = data.get("api_provider", "bunty")
    provider_name = "Bunty/AdminPanels" if provider == "bunty" else "Key Panel"
    await state.update_data(external_product_id=pid, requires_android_id=0)

    # FFPanel reseller-v2 purchases use plan_id + quantity. Shop validity is display-only.
    if provider == "keypanel":
        api_duration = str(data.get("validity") or "").strip()
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        c = conn.cursor()
        try:
            c.execute("""INSERT INTO products
                (category, panel_name, name, price_inr, reseller_price, stock, apk_link, validity, device_limit,
                 external_enabled, external_product_id, requires_android_id, external_duration, api_provider)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (data['cat'], data['panel_name'], data['name'], data['price'], data['reseller_price'], 1,
                 data['apk'], data['validity'], data['device_limit'], 1, pid, 0, api_duration, 'keypanel'))
            prod_id = c.lastrowid
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            logger.exception("Key Panel product creation failed: %s", exc)
            return await m.answer(
                f"❌ <b>Key Panel Product Save Failed</b>\n<code>{type(exc).__name__}: {exc}</code>",
                parse_mode='HTML')
        finally:
            conn.close()

        await state.clear()
        await m.answer(
            "✅ <b>KEY PANEL PRODUCT CREATED</b>\n\n"
            f"📦 Product ID: <code>{prod_id}</code>\n"
            f"🆔 API Plan ID: <code>{pid}</code>\n"
            f"🌐 Provider: <b>Key Panel API</b>\n"
            f"⏱ Shop Duration: <code>{data.get('validity','')}</code>\n\n"
            "The FFPanel API will receive JSON <code>action=buy</code>, this plan_id, and quantity=1 at purchase time.",
            reply_markup=admin_kb(), parse_mode='HTML')
        return

    await m.answer(
        f"⏱ <b>Enter the {provider_name} API duration/tier.</b>\n\n"
        "Examples: <code>1 Day</code>, <code>7 Days</code>, <code>30 Days</code>\n"
        f"Your shop validity is: <code>{data.get('validity', '')}</code>\n\n"
        "If the API uses the same duration, send the same value.",
        reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_external_duration)

@dp.message(AdminStates.add_prod_external_duration)
async def add_prod_external_duration(m: Message, state: FSMContext):
    data = await state.get_data()
    raw_duration = (m.text or "").strip()
    api_duration = normalize_api_duration(raw_duration or data.get("validity", ""))
    if not api_duration:
        return await m.answer("❌ API duration cannot be empty.")
    data = await state.get_data()
    provider = data.get("api_provider", "bunty")
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    try:
        c.execute("""INSERT INTO products
            (category, panel_name, name, price_inr, reseller_price, stock, apk_link, validity, device_limit,
             external_enabled, external_product_id, requires_android_id, external_duration, api_provider)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data['cat'], data['panel_name'], data['name'], data['price'], data['reseller_price'], 1,
             data['apk'], data['validity'], data['device_limit'], 1, data['external_product_id'], 0, api_duration, provider))
        prod_id = c.lastrowid
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        logger.exception("API product creation failed: %s", exc)
        return await m.answer(f"❌ <b>Product save failed</b>\n<code>{type(exc).__name__}: {exc}</code>\n\nPlease restart the bot once so database migration can run, then try again.", parse_mode='HTML')
    finally:
        conn.close()
    p_label = "⚡ Bunty API" if provider == "bunty" else "🛒 Key Panel API"
    await m.answer(
        f"✅ <b>API Product Created!</b>\n\n"
        f"📦 Product ID: <code>{prod_id}</code>\n"
        f"🌐 Provider: <b>{p_label}</b>\n"
        f"🆔 API Product ID: <code>{data['external_product_id']}</code>\n"
        f"⏱ API Duration: <code>{api_duration}</code>\n\n"
        f"The {p_label} will auto-generate and deliver keys when purchased.",
        reply_markup=admin_kb(), parse_mode='HTML'
    )
    await state.clear()

@dp.callback_query(F.data == "addprod_ext_no", AdminStates.add_prod_external)
async def add_prod_external_no(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.update_data(external_enabled=0, external_product_id="", requires_android_id=0, api_provider="manual")
    await call.message.edit_text("📥 <b>Manual Key Product</b>\n\nNow send the keys, one key per line:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_keys)

@dp.message(AdminStates.add_prod_keys)
async def add_prod_keys(m: Message, state: FSMContext):
    keys = [k.strip() for k in m.text.strip().split('\n') if k.strip()]
    if not keys:
        return await m.answer("❌ No valid keys found. Send at least one key.")
    data = await state.get_data()
    stock = len(keys)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    try:
        c.execute("""INSERT INTO products
            (category, panel_name, name, price_inr, reseller_price, stock, apk_link, validity, device_limit,
             external_enabled, external_product_id, requires_android_id, api_provider)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data['cat'], data['panel_name'], data['name'], data['price'], data['reseller_price'], stock,
             data['apk'], data['validity'], data['device_limit'], 0, '', 0, 'manual'))
        prod_id = c.lastrowid
        for k in keys:
            c.execute("INSERT INTO product_keys (product_id, key_text) VALUES (?, ?)", (prod_id, k))
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        logger.exception("Manual product creation failed: %s", exc)
        return await m.answer(f"❌ <b>Product save failed</b>\n<code>{type(exc).__name__}: {exc}</code>\n\nPlease restart the bot once so database migration can run, then try again.", parse_mode='HTML')
    finally:
        conn.close()
    await m.answer(f"✅ <b>Manual Product Created!</b>\n\n📦 Product ID: <code>{prod_id}</code>\n🔒 Stock: {stock} keys", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

PAGE_SIZE = 10

@dp.callback_query(F.data.startswith("admin_quick_add_key"))
async def admin_quick_add_key(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    data = call.data
    page = 1
    if data.startswith("admin_quick_add_key_p_"):
        try:
            page = max(1, int(data.rsplit("_", 1)[1]))
        except ValueError:
            page = 1
    products = db_query(
        "SELECT id, category, panel_name, name, validity, stock FROM products WHERE is_active=1",
        fetchall=True
    ) or []
    products = sorted(
        products,
        key=lambda r: (natural_sort_key(r[1]), natural_sort_key(r[2]), natural_sort_key(r[3]), natural_sort_key(r[4]))
    )
    if not products:
        return await call.message.edit_text("📦 No active products found.", reply_markup=admin_back_kb(), parse_mode="HTML")
    page_size = 10
    total_pages = max(1, math.ceil(len(products) / page_size))
    page = min(page, total_pages)
    chunk = products[(page-1)*page_size:page*page_size]
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for pid, cat, panel, name, validity, stock in chunk:
        label = f"➕ {panel or cat} • {name} • {validity} | Stock {stock or 0}"
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=label[:60], callback_data=f"quick_add_key_{pid}", style="primary")
        ])
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"admin_quick_add_key_p_{page-1}"))
    nav.append(InlineKeyboardButton(text=f"📄 {page}/{total_pages}", callback_data="ignore_stock_click"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"admin_quick_add_key_p_{page+1}"))
    kb.inline_keyboard.append(nav)
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")
    ])
    await call.message.edit_text(
        "🔑 <b>QUICK ADD KEY</b>\n\nSelect product/package directly.\n"
        "No need to open Manage Products.",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("quick_add_key_"))
async def quick_add_key_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    try:
        p_id = int(call.data.rsplit("_", 1)[1])
    except ValueError:
        return await call.answer("Invalid product.", show_alert=True)
    prod = db_query("SELECT panel_name, name, validity, stock FROM products WHERE id=?", (p_id,), fetchone=True)
    if not prod:
        return await call.answer("Product not found.", show_alert=True)
    await state.update_data(edit_p_id=p_id)
    await call.message.edit_text(
        f"🔑 <b>ADD KEY</b>\n\n📦 {prod[0] or 'Panel'}\n"
        f"🕒 {prod[1]} • {prod[2]}\n📦 Current Stock: {prod[3] or 0}\n\n"
        "Send one or multiple keys, <b>one key per line</b>:",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    await state.set_state(AdminStates.wait_for_add_keys)

@dp.callback_query(F.data.startswith("admin_manage_prods"))
async def admin_manage_prods(call: CallbackQuery, state: FSMContext = None):
    if call.from_user.id != ADMIN_ID: return
    data = call.data
    page = 1
    cat_filter = None
    
    if data == "admin_manage_prods":
        page = 1
    elif data.startswith("admin_manage_prods_p_"):
        parts = data.split("_p_")[1].split("_c_")
        page = int(parts[0]) if parts[0].isdigit() else 1
        if len(parts) > 1 and parts[1]:
            cat_filter = parts[1]
    elif data.startswith("admin_manage_prods_cat_"):
        cat_filter = data.split("admin_manage_prods_cat_")[1]
        page = 1
    elif data == "admin_manage_prods_filter_menu":
        cats = get_all_categories()
        kb = InlineKeyboardMarkup(inline_keyboard=[])
        for c in cats:
            count = db_query("SELECT COUNT(*) FROM products WHERE category LIKE ?", (c + '%',), fetchone=True)[0]
            cat_ico = get_category_display_icon(c)
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"{cat_ico} {c} ({count} items)", callback_data=f"admin_manage_prods_cat_{c}")])
        kb.inline_keyboard.append([InlineKeyboardButton(text="🌐 Show All Products", callback_data="admin_manage_prods_p_1")])
        kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Back to Products", callback_data="admin_manage_prods_p_1", icon_custom_emoji_icon="back" if False else None, style="danger")])
        return await call.message.edit_text("📂 <b>Filter Products by Category:</b>\nSelect a category below to view only its products:", reply_markup=kb, parse_mode='HTML')
    elif data == "admin_manage_prods_search":
        if state:
            await state.set_state(AdminStates.wait_for_search_product)
        return await call.message.edit_text("🔍 <b>Search Products</b>\n\nSend Product ID, Panel Name, or Package Name to search:", reply_markup=admin_back_kb(), parse_mode='HTML')

    if cat_filter:
        prods = db_query("SELECT id, name, category, panel_name, stock, is_active FROM products WHERE category LIKE ?", (cat_filter + '%',), fetchall=True)
    else:
        prods = db_query("SELECT id, name, category, panel_name, stock, is_active FROM products", fetchall=True)
        
    prods = sorted(prods or [], key=lambda row: (natural_sort_key(row[2]), natural_sort_key(row[3]), natural_sort_key(row[1])))
    if not prods:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="admin_manage_prods_p_1")]])
        return await call.message.edit_text("📦 Store Database is empty or no products match filter.", reply_markup=kb, parse_mode='HTML')
        
    total_items = len(prods)
    total_pages = max(1, math.ceil(total_items / PAGE_SIZE))
    page = max(1, min(page, total_pages))
    
    start_idx = (page - 1) * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    page_prods = prods[start_idx:end_idx]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for p in page_prods:
        status_dot = "🟢" if p[5] else "🔴"
        p_panel = p[3] if p[3] is not None else ""
        btn_text = f"{status_dot} [{p[0]}] {p_panel} - {p[1]} (Stock: {p[4]})"
        kb.inline_keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"admin_view_p_{p[0]}", style="primary")])
        
    # Navigation row
    nav_row = []
    cat_suffix = f"_c_{cat_filter}" if cat_filter else ""
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"admin_manage_prods_p_{page - 1}{cat_suffix}"))
    nav_row.append(InlineKeyboardButton(text=f"📄 {page}/{total_pages} ({total_items})", callback_data="ignore_stock_click"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"admin_manage_prods_p_{page + 1}{cat_suffix}"))
    kb.inline_keyboard.append(nav_row)
    
    # Filter & Search row
    filter_btn_text = f"📂 Category: {cat_filter[:12]}..." if cat_filter else "📂 Filter by Category"
    kb.inline_keyboard.append([
        InlineKeyboardButton(text=filter_btn_text, callback_data="admin_manage_prods_filter_menu"),
        InlineKeyboardButton(text="🔍 Search Product", callback_data="admin_manage_prods_search")
    ])
    if cat_filter:
        kb.inline_keyboard.append([InlineKeyboardButton(text="🌐 Clear Filter (Show All)", callback_data="admin_manage_prods_p_1")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    
    filter_msg = f" (Category: <b>{cat_filter}</b>)" if cat_filter else ""
    await call.message.edit_text(f"📦 <b>Database Editor: Select Node to modify</b>{filter_msg}\nShowing {len(page_prods)} of {total_items} products:", reply_markup=kb, parse_mode='HTML')

@dp.message(AdminStates.wait_for_search_product)
async def process_product_search(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    query = m.text.strip()
    if not query:
        return await m.answer("❌ Search query cannot be empty.", reply_markup=admin_kb())
        
    if query.isdigit():
        prods = db_query("SELECT id, name, category, panel_name, stock, is_active FROM products WHERE id=? OR panel_name LIKE ? OR name LIKE ?", (int(query), f"%{query}%", f"%{query}%"), fetchall=True)
    else:
        prods = db_query("SELECT id, name, category, panel_name, stock, is_active FROM products WHERE panel_name LIKE ? OR name LIKE ? OR category LIKE ?", (f"%{query}%", f"%{query}%", f"%{query}%"), fetchall=True)
        
    if not prods:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back to Products", callback_data="admin_manage_prods_p_1")]])
        await m.answer(f"❌ No products matched '<code>{query}</code>'.", reply_markup=kb, parse_mode='HTML')
        return await state.clear()
        
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for p in prods[:15]:
        status_dot = "🟢" if p[5] else "🔴"
        p_name = p[3] if p[3] is not None else ""
        btn_text = f"{status_dot} [{p[0]}] {p_name} - {p[1]} (Stock: {p[4]})"
        kb.inline_keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"admin_view_p_{p[0]}", style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="📦 Back to Product Manager", callback_data="admin_manage_prods_p_1")])
    await m.answer(f"🔍 <b>Found {len(prods)} products for '<code>{query}</code>':</b>\nSelect product to modify stock, keys, API duration:", reply_markup=kb, parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data.startswith("admin_view_p_"))
async def admin_view_product(call: CallbackQuery, p_id: Optional[int] = None):
    if call.from_user.id != ADMIN_ID: return
    try:
        if p_id is None:
            p_id = int(call.data.split("_")[3])
        prod = db_query("SELECT * FROM products WHERE id=?", (p_id,), fetchone=True)
        if not prod: return await call.answer("❌ Architecture fault: Node lost!", show_alert=True)
        panel_name = prod[2] if prod[2] is not None else ""
        price_inr = float(prod[4]) if prod[4] is not None and prod[4] != "" else 0.0
        reseller_price = float(prod[5]) if prod[5] is not None and prod[5] != "" else 0.0
        provider_val = str(prod[15] if len(prod) > 15 and prod[15] else 'bunty').lower()
        if prod[11]:
            if provider_val == 'keypanel':
                api_stock_mode = f"🛒 Key Panel API (PID: {prod[12]})"
            else:
                api_stock_mode = f"⚡ Bunty API (PID: {prod[12]})"
        else:
            api_stock_mode = "📦 Manual Stock (Vault)"
        payload_link = prod[7] if prod[7] else "None"
        visibility = "Active" if prod[10] else "Hidden"
        is_maint = is_product_in_maintenance(p_id)
        maint_mode = '🟠 In Maintenance (Blocked)' if is_maint else '🟢 Normal (Purchasable)'
        text = (
            f"📦 <b><u>NODE DEEP DIVE DETAILS</u></b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<b>ID:</b> <code>{prod[0]}</code>\n"
            f"<b>Panel Group:</b> {prod[1]}\n"
            f"<b>Panel Name:</b> {panel_name}\n"
            f"<b>Package Date/Time:</b> {prod[3]}\n"
            f"<b>Standard Price:</b> ₹{price_inr:.2f}\n"
            f"👑 <b>Wholesale Price:</b> ₹{reseller_price:.2f}\n"
            f"<b>Vault Stock:</b> {prod[6]}\n"
            f"<b>API Mode:</b> {api_stock_mode}\n"
            f"<b>Payload Link:</b> {payload_link}\n"
            f"<b>Time Config:</b> {prod[8]}\n"
            f"<b>HWID Limit:</b> {prod[9]}\n"
            f"<b>Visibility:</b> {visibility}\n"
            f"<b>Maintenance:</b> {maint_mode}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        toggle_btn_text = "Hide Product 👁‍🗨" if prod[10] else "Unhide Product 👁"
        toggle_maint_btn_text = "Maint OFF 🟢" if is_maint else "Maint ON 🟠"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Switch API Provider 🔄", callback_data=f"switch_provider_{p_id}", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary"),
             InlineKeyboardButton(text="Edit API PID 🆔", callback_data=f"edit_p_{p_id}_pid", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
            [InlineKeyboardButton(text="Edit Panel Group 🏷️", callback_data=f"edit_p_{p_id}_cat", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary"), 
             InlineKeyboardButton(text="Edit Panel Name 🏷️", callback_data=f"edit_p_{p_id}_panel_name", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
            [InlineKeyboardButton(text="Edit Package Name ✏️", callback_data=f"edit_p_{p_id}_name", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary"),
             InlineKeyboardButton(text="Set Position", callback_data=f"pkg_pos_{p_id}", icon_custom_emoji_id=get_emoji_icon("grid_id"), style="primary")],
            [InlineKeyboardButton(text="Edit Price 💰", callback_data=f"edit_p_{p_id}_price", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary"), 
             InlineKeyboardButton(text="Edit R-Price 👑", callback_data=f"edit_p_{p_id}_rprice", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary")],
            [InlineKeyboardButton(text="Edit Validity ⏳", callback_data=f"edit_p_{p_id}_validity", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary"), 
             InlineKeyboardButton(text="Edit Device 📱", callback_data=f"edit_p_{p_id}_device", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
            [InlineKeyboardButton(text="Edit API Duration ⏱️", callback_data=f"edit_p_{p_id}_api_duration", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
            [InlineKeyboardButton(text="Edit APK Link 🔗", callback_data=f"edit_p_{p_id}_apk", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary"), 
             InlineKeyboardButton(text="Add Keys ➕", callback_data=f"edit_p_{p_id}_keys", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success")],
            [InlineKeyboardButton(text="Add to Stock 📦➕", callback_data=f"edit_p_{p_id}_stock_add", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success")],
            [InlineKeyboardButton(text="Delete Key 🗑", callback_data=f"delkey_p_{p_id}", icon_custom_emoji_id=get_emoji_icon("back"), style="danger"), 
             InlineKeyboardButton(text=toggle_btn_text, callback_data=f"toggle_p_{p_id}", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="primary")],
            [InlineKeyboardButton(text=f"🛠️ {toggle_maint_btn_text}", callback_data=f"adm_pmaint_single_{p_id}", icon_custom_emoji_id=get_emoji_icon("shield_icon"), style="primary"),
             InlineKeyboardButton(text="Nuke Full Node 🗑", callback_data=f"delete_p_{p_id}", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")],
            [InlineKeyboardButton(text="BACK", callback_data="admin_manage_prods", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
        ])
        await call.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Error in admin_view_product: {e}")
        await call.message.edit_text(f"❌ Error loading product: {str(e)}", reply_markup=admin_back_kb(), parse_mode='HTML')

@dp.callback_query(F.data.startswith("switch_provider_"))
async def switch_product_provider_menu(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    p_id = int(call.data.split("_")[2])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Switch to Bunty API (AdminPanels)", callback_data=f"setapi_bunty_{p_id}", style="primary")],
        [InlineKeyboardButton(text="🛒 Switch to Key Panel API", callback_data=f"setapi_keypanel_{p_id}", style="primary")],
        [InlineKeyboardButton(text="📦 Switch to Manual Keys", callback_data=f"setapi_manual_{p_id}", style="primary")],
        [InlineKeyboardButton(text="🔙 Back", callback_data=f"admin_view_p_{p_id}", style="danger")]
    ])
    await call.message.edit_text(
        f"🔄 <b>Select Key Source for Product ID #{p_id}:</b>\n\n"
        "⚡ <b>Bunty API</b>: Uses AdminPanels Reseller API\n"
        "🛒 <b>FFPanel Key API</b>: Uses reseller-v2 plans and license-key API\n"
        "📦 <b>Manual Stock</b>: Pre-loaded keys from vault",
        reply_markup=kb, parse_mode='HTML'
    )

@dp.callback_query(F.data.startswith("setapi_"))
async def set_product_api_choice(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    parts = call.data.split("_")
    choice = parts[1]
    p_id = int(parts[2])
    if choice == "bunty":
        db_query("UPDATE products SET external_enabled=1, api_provider='bunty' WHERE id=?", (p_id,))
        await call.answer("Switched to Bunty API!", show_alert=True)
    elif choice == "keypanel":
        db_query("UPDATE products SET external_enabled=1, api_provider='keypanel' WHERE id=?", (p_id,))
        await call.answer("Switched to Key Panel API!", show_alert=True)
    elif choice == "manual":
        db_query("UPDATE products SET external_enabled=0, api_provider='manual' WHERE id=?", (p_id,))
        await call.answer("Switched to Manual Keys!", show_alert=True)
    await admin_view_product(call, p_id=p_id)

@dp.callback_query(F.data.startswith("toggle_p_"))
async def admin_toggle_product(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    p_id = int(call.data.split("_")[2])
    current = db_query("SELECT is_active FROM products WHERE id=?", (p_id,), fetchone=True)[0]
    new_val = 0 if current == 1 else 1
    db_query("UPDATE products SET is_active=? WHERE id=?", (new_val, p_id))
    await call.answer("Visibility updated successfully!", show_alert=True)
    await admin_view_product(call, p_id=p_id)

@dp.callback_query(F.data.startswith("adm_pmaint_single_"))
async def admin_toggle_single_product_maint(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    try:
        p_id = int(call.data.split("_")[3])
    except Exception:
        return await call.answer("Invalid product ID", show_alert=True)
    cur = is_product_in_maintenance(p_id)
    new_val = 0 if cur else 1
    db_query("UPDATE products SET is_maintenance=? WHERE id=?", (new_val, p_id))
    status_msg = "🟠 Maintenance ON (Users blocked)" if new_val else "🟢 Maintenance OFF (Active)"
    await call.answer(f"Status: {status_msg}", show_alert=True)
    await admin_view_product(call, p_id=p_id)

# ==============================================================================
# PRODUCT MAINTENANCE SYSTEM (ADMIN CONTROL PANEL - MERGED BY PRODUCT)
# ==============================================================================
async def _render_admin_product_maintenance(call: CallbackQuery, page: int = 0):
    """Render Product Maintenance control panel grouped by product name (merged durations)."""
    if not is_admin(call.from_user.id):
        return
    try:
        # Group packages by product (panel_name if set, otherwise name)
        rows = db_query("""
            SELECT 
                CASE 
                    WHEN panel_name IS NOT NULL AND TRIM(panel_name) != '' THEN TRIM(panel_name) 
                    ELSE TRIM(name) 
                END AS prod_name,
                MIN(category) AS category,
                MIN(id) AS sample_id,
                COUNT(*) AS total_pkgs,
                SUM(CASE WHEN COALESCE(is_maintenance, 0) = 1 THEN 1 ELSE 0 END) AS maint_pkgs
            FROM products
            GROUP BY CASE 
                WHEN panel_name IS NOT NULL AND TRIM(panel_name) != '' THEN TRIM(panel_name) 
                ELSE TRIM(name) 
            END
            ORDER BY prod_name ASC
        """, fetchall=True) or []

        prods = []
        for row in rows:
            if not row or len(row) < 5:
                continue
            prod_name = row[0] or "Unknown"
            category = row[1] or ""
            sample_id = int(row[2])
            total_pkgs = int(row[3] or 0)
            maint_pkgs = int(row[4] or 0)
            is_maint = (maint_pkgs > 0)
            prods.append((prod_name, category, sample_id, total_pkgs, is_maint, maint_pkgs))

        if not prods:
            text = "🛠️ <b>Product Maintenance Panel</b>\n\nNo products found in the store database."
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Back to Admin", callback_data="admin_panel_back", style="danger")]
            ])
            if call.message:
                try: await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')
                except Exception: await call.message.answer(text, reply_markup=kb, parse_mode='HTML')
            return

        per_page = 10
        total_pages = max(1, (len(prods) + per_page - 1) // per_page)
        page = max(0, min(int(page), total_pages - 1))
        start = page * per_page
        current = prods[start:start + per_page]

        maint_count = sum(1 for p in prods if p[4])
        active_count = len(prods) - maint_count

        keyboard = []
        for prod_name, category, sample_id, total_pkgs, is_maint, maint_pkgs in current:
            status_icon = "🟠 [MAINT]" if is_maint else "🟢 [ACTIVE]"
            label = f"{status_icon} {prod_name}"
            label = label[:60]
            keyboard.append([
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"adm_pmaint_t_{sample_id}_{page}",
                    style="primary"
                )
            ])

        # Batch actions: All ON / All OFF
        keyboard.append([
            InlineKeyboardButton(text="🟠 All Maint ON", callback_data=f"adm_pmaint_all_on_{page}", style="danger"),
            InlineKeyboardButton(text="🟢 All Maint OFF", callback_data=f"adm_pmaint_all_off_{page}", style="success"),
        ])

        # Pagination
        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"adm_pmaint_p_{page-1}", style="success"))
            nav.append(InlineKeyboardButton(text=f"📄 {page+1}/{total_pages}", callback_data="adm_pmaint_noop", style="primary"))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"adm_pmaint_p_{page+1}", style="success"))
            keyboard.append(nav)

        keyboard.append([InlineKeyboardButton(text="🔙 Back to Admin", callback_data="admin_panel_back", style="danger")])

        text = (
            "🛠️ <b><u>PRODUCT MAINTENANCE CONTROL PANEL</u></b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Click on any product below to toggle maintenance for <b>all its packages/durations</b> ON or OFF.\n\n"
            "• 🟠 <b>MAINT</b>: Users attempting to buy any duration will see an instant maintenance alert and order will be blocked.\n"
            "• 🟢 <b>ACTIVE</b>: Normal purchase flow is open for all durations.\n\n"
            f"📊 <b>Total Products:</b> {len(prods)}\n"
            f"🟠 <b>Under Maintenance:</b> {maint_count}\n"
            f"🟢 <b>Normal / Purchasable:</b> {active_count}\n"
            f"📄 <b>Page:</b> {page+1}/{total_pages}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Click any product button below to instantly flip its state:</i>"
        )
        if call.message:
            await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode='HTML')
    except Exception as e:
        logger.exception("Product Maintenance UI error")
        if call.message:
            try:
                await call.message.edit_text(
                    f"❌ <b>Maintenance UI error:</b> {e}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔄 Retry", callback_data="admin_prod_maint", style="success")],
                        [InlineKeyboardButton(text="🔙 Back to Admin", callback_data="admin_panel_back", style="danger")]
                    ]),
                    parse_mode='HTML'
                )
            except Exception:
                pass

@dp.callback_query(F.data == "admin_prod_maint")
async def admin_prod_maint_entry(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    try: await call.answer()
    except Exception: pass
    await _render_admin_product_maintenance(call, 0)

@dp.callback_query(F.data.regexp(r"^adm_pmaint_p_\d+$"))
async def admin_prod_maint_page(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    try: page = int(call.data.rsplit("_", 1)[1])
    except Exception: return await call.answer("Invalid page", show_alert=True)
    try: await call.answer()
    except Exception: pass
    await _render_admin_product_maintenance(call, page)

@dp.callback_query(F.data == "adm_pmaint_noop")
async def admin_prod_maint_noop(call: CallbackQuery):
    if is_admin(call.from_user.id):
        await call.answer("Current page", show_alert=False)

@dp.callback_query(F.data.regexp(r"^adm_pmaint_t_\d+_\d+$"))
async def admin_prod_maint_toggle(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    try:
        parts = call.data.split("_")
        pid = int(parts[3])
        page = int(parts[4])
    except Exception:
        return await call.answer("Invalid product/page", show_alert=True)
    
    row = db_query("SELECT name, panel_name FROM products WHERE id=?", (pid,), fetchone=True)
    if not row:
        return await call.answer("❌ Product not found", show_alert=True)
    
    panel_val = (row[1] or "").strip()
    name_val = (row[0] or "").strip()
    prod_name = panel_val if panel_val else name_val

    # Check current maintenance count for all packages of this product
    # Resolve the exact product group from the selected sample row.  Older code
    # matched only panel_name globally, so identical panel names in different
    # categories could toggle the wrong rows.  We now scope the toggle to the
    # selected category + panel (or category + package name fallback).
    exact = db_query("SELECT category, panel_name, name FROM products WHERE id=?", (pid,), fetchone=True)
    if not exact:
        return await call.answer("❌ Product group not found", show_alert=True)
    category_val = (exact[0] or '').strip()
    panel_exact = (exact[1] or '').strip()
    name_exact = (exact[2] or '').strip()
    if panel_exact:
        group_where = "category=? AND TRIM(panel_name)=?"
        group_params = (category_val, panel_exact)
        prod_name = panel_exact
    else:
        group_where = "category=? AND (panel_name IS NULL OR TRIM(panel_name)='') AND TRIM(name)=?"
        group_params = (category_val, name_exact)
        prod_name = name_exact

    stats = db_query(
        f"SELECT COUNT(*), SUM(CASE WHEN COALESCE(is_maintenance,0)=1 THEN 1 ELSE 0 END) "
        f"FROM products WHERE {group_where}",
        group_params, fetchone=True
    )
    total_pkgs = int(stats[0] or 0) if stats else 0
    maint_pkgs = int(stats[1] or 0) if stats and stats[1] is not None else 0
    new_val = 0 if maint_pkgs > 0 else 1
    db_query(f"UPDATE products SET is_maintenance=? WHERE {group_where}", (new_val, *group_params))

    status_msg = "🟠 MAINTENANCE (Users Blocked)" if new_val else "🟢 ACTIVE (Purchasable)"
    await call.answer(f"✅ {prod_name}\n({total_pkgs} durations updated)\nStatus: {status_msg}", show_alert=True)
    await _render_admin_product_maintenance(call, page)

@dp.callback_query(F.data.regexp(r"^adm_pmaint_all_(on|off)_\d+$"))
async def admin_prod_maint_toggle_all(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    try:
        parts = call.data.split("_")
        mode = parts[3]
        page = int(parts[4])
    except Exception:
        return await call.answer("Invalid parameters", show_alert=True)
    val = 1 if mode == "on" else 0
    db_query("UPDATE products SET is_maintenance=?", (val,))
    msg = "🟠 All products put into Maintenance Mode!" if val else "🟢 All products restored to Active Mode!"
    await call.answer(msg, show_alert=True)
    await _render_admin_product_maintenance(call, page)


# ==============================================================================
# QUICK STOCK ADD — Admin Main Panel shortcut
# ==============================================================================
QUICK_STOCK_PAGE_SIZE = 10

@dp.callback_query(F.data == "admin_quick_stock")
async def admin_quick_stock_menu(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    await _show_quick_stock_list(call, page=1)

@dp.callback_query(F.data.startswith("qs_page_"))
async def admin_quick_stock_page(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    page = int(call.data.split("_")[2])
    await _show_quick_stock_list(call, page=page)

async def _show_quick_stock_list(call: CallbackQuery, page: int = 1):
    """Show paginated list of all active products for quick stock add."""
    products = db_query(
        "SELECT id, category, panel_name, name, stock, external_enabled FROM products WHERE is_active=1 ORDER BY category, panel_name, name",
        fetchall=True
    )
    if not products:
        return await call.message.edit_text(
            "❌ <b>No active products found.</b>\n\nAdd products first via 'Add Product'.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")
            ]]),
            parse_mode='HTML'
        )

    total = len(products)
    total_pages = max(1, (total + QUICK_STOCK_PAGE_SIZE - 1) // QUICK_STOCK_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * QUICK_STOCK_PAGE_SIZE
    page_items = products[start:start + QUICK_STOCK_PAGE_SIZE]

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for prod in page_items:
        p_id, category, panel_name, pkg_name, stock, ext_enabled = prod
        stock_val = stock or 0
        stock_lbl = "♾️ API" if ext_enabled else f"📦 {stock_val}"
        btn_label = f"{panel_name} › {pkg_name}  [{stock_lbl}]"
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=btn_label,
                callback_data=f"qs_select_{p_id}",
                icon_custom_emoji_id=get_emoji_icon("add_balance"),
                style="success"
            )
        ])

    # Pagination row
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"qs_page_{page-1}", style="primary"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"qs_page_{page+1}", style="primary"))
    if nav:
        kb.inline_keyboard.append(nav)

    kb.inline_keyboard.append([
        InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")
    ])

    await call.message.edit_text(
        f"📦 <b>Quick Add Stock</b>\n"
        f"<i>Page {page}/{total_pages} — {total} products</i>\n\n"
        "👇 Select the product to add stock:\n"
        "<i>(Format: Panel › Package  [Current Stock])</i>",
        reply_markup=kb,
        parse_mode='HTML'
    )

@dp.callback_query(F.data.startswith("qs_select_"))
async def admin_quick_stock_select(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    p_id = int(call.data.split("_")[2])
    prod = db_query(
        "SELECT id, panel_name, name, stock, external_enabled FROM products WHERE id=?",
        (p_id,), fetchone=True
    )
    if not prod:
        return await call.answer("❌ Product not found!", show_alert=True)
    _, panel_name, pkg_name, current_stock, ext_enabled = prod
    stock_val = current_stock or 0

    await state.update_data(qs_prod_id=p_id, qs_prod_name=f"{panel_name} › {pkg_name}")
    await state.set_state(AdminStates.quick_stock_qty)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="10", callback_data="qs_qty_10", style="primary"),
         InlineKeyboardButton(text="25", callback_data="qs_qty_25", style="primary"),
         InlineKeyboardButton(text="50", callback_data="qs_qty_50", style="primary")],
        [InlineKeyboardButton(text="100", callback_data="qs_qty_100", style="primary"),
         InlineKeyboardButton(text="200", callback_data="qs_qty_200", style="primary"),
         InlineKeyboardButton(text="500", callback_data="qs_qty_500", style="primary")],
        [InlineKeyboardButton(text="Cancel", callback_data="admin_quick_stock", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text(
        f"📦 <b>Add Stock</b>\n\n"
        f"Product: <b>{panel_name} › {pkg_name}</b>\n"
        f"Current Stock: <code>{stock_val}</code>\n\n"
        "👇 Tap a quick amount or type a custom number:",
        reply_markup=kb,
        parse_mode='HTML'
    )

@dp.callback_query(F.data.startswith("qs_qty_"))
async def admin_quick_stock_qty_btn(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    qty = int(call.data.split("_")[2])
    data = await state.get_data()
    p_id = data.get("qs_prod_id")
    prod_name = data.get("qs_prod_name", "Unknown")
    if not p_id:
        return await call.answer("❌ Session expired. Please try again.", show_alert=True)
    await _do_quick_stock_add(call.message, state, p_id, prod_name, qty)

@dp.message(AdminStates.quick_stock_qty)
async def admin_quick_stock_qty_msg(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    p_id = data.get("qs_prod_id")
    prod_name = data.get("qs_prod_name", "Unknown")
    if not p_id:
        return await m.answer("❌ Session expired. Please go back and try again.")
    try:
        qty = int(m.text.strip())
        if qty <= 0 or qty > 100000:
            raise ValueError
    except ValueError:
        return await m.answer("❌ Enter a valid whole number between 1 and 100000.\nExample: <code>50</code>", parse_mode='HTML')
    await _do_quick_stock_add(m, state, p_id, prod_name, qty)

async def _do_quick_stock_add(msg_or_call, state: FSMContext, p_id: int, prod_name: str, qty: int):
    """Actually add stock to the product and show result."""
    db_query("UPDATE products SET stock=COALESCE(stock,0)+? WHERE id=?", (qty, p_id))
    new_stock = db_query("SELECT stock FROM products WHERE id=?", (p_id,), fetchone=True)
    new_stock_val = new_stock[0] if new_stock else qty
    await state.clear()
    result_text = (
        f"✅ <b>Stock Added Successfully!</b>\n\n"
        f"📦 Product: <b>{prod_name}</b>\n"
        f"➕ Added: <code>{qty}</code> units\n"
        f"📊 New Total Stock: <code>{new_stock_val}</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Add More Stock", callback_data="admin_quick_stock", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    if hasattr(msg_or_call, 'edit_text'):
        await msg_or_call.edit_text(result_text, reply_markup=kb, parse_mode='HTML')
    else:
        await msg_or_call.answer(result_text, reply_markup=kb, parse_mode='HTML')

# ==============================================================================
# FIX: Edit product field – correctly handle different data types
# ==============================================================================
@dp.callback_query(F.data.startswith("edit_p_"))
async def start_edit_product(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    parts = call.data.split("_")
    p_id = int(parts[2]); field = "_".join(parts[3:])
    await state.update_data(edit_p_id=p_id, edit_field=field)
    if field == 'keys':
        await call.message.edit_text("📥 <b>Vault Injection</b>\nPaste the <b>NEW KEYS</b> to append to the stock (1 key per line):", reply_markup=admin_back_kb(), parse_mode='HTML')
        await state.set_state(AdminStates.wait_for_add_keys)
    elif field == 'stock_add':
        await call.message.edit_text(
            "📦 <b>Add to Stock</b>\n\nEnter how many stock units to add.\nExample: <code>100</code> or <code>200</code>",
            reply_markup=admin_back_kb(), parse_mode='HTML'
        )
        await state.set_state(AdminStates.wait_for_new_value)
    else:
        field_name_map = {'cat': 'New Panel Group/Category Name', 'panel_name': 'New Panel Name', 'name': 'New Package/Date Name', 'price': 'New Standard Price in ₹', 'rprice': 'New Reseller Price in ₹', 'validity': 'New Time Validity String', 'device': 'New HWID Limit String', 'apk': 'New Payload Link (or type "none")', 'api_duration': 'Exact XYZ API Duration (e.g. 1 Day)', 'pid': 'New API Product ID (PID)', 'stock_add': 'Stock units to add'}
        await call.message.edit_text(f"✏️ Input the required data for: <b>{field_name_map.get(field, field)}</b>", reply_markup=admin_back_kb(), parse_mode='HTML')
        await state.set_state(AdminStates.wait_for_new_value)

@dp.message(AdminStates.wait_for_new_value)
async def process_edit_value(m: Message, state: FSMContext):
    data = await state.get_data()
    p_id = data['edit_p_id']; field = data['edit_field']; new_val = m.text.strip()
    
    # If field is price or reseller price, convert to float
    if field in ['price', 'rprice']:
        try:
            new_val = float(new_val)
        except ValueError:
            return await m.answer("❌ Invalid number format. Please enter a valid price (e.g., 500).")
    # If field is apk, store as string (don't convert to float!)
    elif field == 'apk':
        new_val = "" if new_val.lower() == 'none' else new_val
    elif field == 'stock_add':
        try:
            add_qty = int(new_val)
            if add_qty <= 0 or add_qty > 100000:
                raise ValueError
        except ValueError:
            return await m.answer("❌ Enter a positive whole number up to 100000, e.g. 100 or 200.")
        db_query("UPDATE products SET stock=COALESCE(stock,0)+? WHERE id=?", (add_qty, p_id))
        new_stock = db_query("SELECT stock FROM products WHERE id=?", (p_id,), fetchone=True)[0]
        await m.answer(f"✅ <b>Stock Added!</b>\n\n➕ Added: <code>{add_qty}</code>\n📦 New Stock: <code>{new_stock}</code>", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
        return
    elif field == 'cat':
        new_val = new_val.upper()
        existing_cat = db_query("SELECT id FROM categories WHERE UPPER(name)=?", (new_val,), fetchone=True)
        if not existing_cat:
            max_order = db_query("SELECT COALESCE(MAX(sort_order), 0) FROM categories", fetchone=True)[0]
            db_query("INSERT INTO categories (name, sort_order) VALUES (?, ?)", (new_val, max_order + 1))
    # For all other fields (panel_name, name, validity, device), keep as string
    
    db_col_map = {'cat': 'category', 'panel_name': 'panel_name', 'name': 'name', 'price': 'price_inr', 'rprice': 'reseller_price', 'validity': 'validity', 'device': 'device_limit', 'apk': 'apk_link', 'api_duration': 'external_duration', 'pid': 'external_product_id'}
    db_query(f"UPDATE products SET {db_col_map.get(field, field)}=? WHERE id=?", (new_val, p_id))
    await m.answer("✅ <b>Node updated gracefully!</b>", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.message(AdminStates.wait_for_add_keys)
async def process_add_keys(m: Message, state: FSMContext):
    data = await state.get_data()
    p_id = data['edit_p_id']
    keys = [k.strip() for k in m.text.strip().split('\n') if k.strip()]
    if len(keys) == 0: return await m.answer("❌ Protocol breach: Zero valid keys found.", reply_markup=admin_kb(), parse_mode='HTML')
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    c = conn.cursor()
    for k in keys: c.execute("INSERT INTO product_keys (product_id, key_text) VALUES (?, ?)", (p_id, k))
    c.execute("UPDATE products SET stock = stock + ? WHERE id=?", (len(keys), p_id))
    conn.commit(); conn.close()
    await m.answer(f"✅ <b>Vault Secure!</b> {len(keys)} new keys appended and encrypted.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data.startswith("delete_p_"))
async def admin_delete_product(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    p_id = int(call.data.split("_")[2])
    prod = db_query("SELECT panel_name, name, category FROM products WHERE id=?", (p_id,), fetchone=True)
    if not prod:
        return await call.answer("❌ Product not found.", show_alert=True)
    panel_name, package_name, category = prod
    package_count = db_query(
        "SELECT COUNT(*) FROM products WHERE category=? AND panel_name=?",
        (category, panel_name), fetchone=True
    )
    count = int(package_count[0] or 0) if package_count else 1
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"☢️ YES — DELETE ALL {count} PACKAGES", callback_data=f"confirm_delete_product_{p_id}", style="danger")],
        [InlineKeyboardButton(text="🔙 Cancel", callback_data=f"admin_view_p_{p_id}", style="primary")]
    ])
    await call.message.edit_text(
        "⚠️ <b>DELETE PRODUCT — FINAL CONFIRMATION</b>\n\n"
        f"📂 Category: <b>{html.escape(str(category or '-'))}</b>\n"
        f"📦 Product/Panel: <b>{html.escape(str(panel_name or package_name or '-'))}</b>\n"
        f"🧩 Packages found: <b>{count}</b>\n\n"
        "This will permanently remove the complete product group and its unused vault keys.\n"
        "Purchase/order history will remain intact.",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("confirm_delete_product_"))
async def confirm_delete_product(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    p_id = int(call.data.split("_")[-1])
    prod = db_query("SELECT panel_name, name, category FROM products WHERE id=?", (p_id,), fetchone=True)
    if not prod:
        return await call.answer("❌ Product already deleted.", show_alert=True)
    panel_name, package_name, category = prod
    rows = db_query("SELECT id FROM products WHERE category=? AND panel_name=?", (category, panel_name), fetchall=True) or []
    ids = [int(r[0]) for r in rows]
    for pid in ids:
        db_query("DELETE FROM product_keys WHERE product_id=?", (pid,))
        db_query("DELETE FROM products WHERE id=?", (pid,))
    log_activity(call.from_user.id, "ADMIN_DELETE_PRODUCT_GROUP", f"Category={category}, Panel={panel_name}, Packages={len(ids)}")
    await call.answer(f"☢️ Deleted complete product group ({len(ids)} packages).", show_alert=True)
    await admin_manage_prods(call)

@dp.callback_query(F.data.startswith("delkey_p_"))
async def admin_delete_key_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    p_id = int(call.data.split("_")[2])
    await state.update_data(del_p_id=p_id)
    await call.message.edit_text("🗑 Send the <b>exact string match</b> of the key you wish to purge from the vault:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_delete_key)

@dp.message(AdminStates.wait_for_delete_key)
async def process_delete_key(m: Message, state: FSMContext):
    data = await state.get_data()
    p_id = data['del_p_id']
    key_to_delete = m.text.strip()
    key_data = db_query("SELECT id, is_used FROM product_keys WHERE product_id=? AND key_text=?", (p_id, key_to_delete), fetchone=True)
    if not key_data: return await m.answer("❌ Key not found. Check logs and try again.", reply_markup=admin_back_kb(), parse_mode='HTML')
    if key_data[1] == 1: return await m.answer("⚠️ Action Blocked: This key has already been dispatched to a user.", reply_markup=admin_back_kb(), parse_mode='HTML')
    db_query("DELETE FROM product_keys WHERE id=?", (key_data[0],))
    db_query("UPDATE products SET stock = stock - 1 WHERE id=?", (p_id,))
    await m.answer(f"✅ Key <code>{key_to_delete}</code> securely purged from vault.\n📦 Database indices updated.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

# ==============================================================================
# 20. ADMIN TICKETS, BROADCAST, COUPONS
# ==============================================================================
@dp.callback_query(F.data == "admin_view_tickets")
async def admin_view_tickets(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    tickets = db_query("SELECT id, user_id, message, created_at FROM tickets WHERE status='Open' LIMIT 1", fetchall=True)
    if not tickets: return await call.answer("✅ Zero pending issues. Grid is clean!", show_alert=True)
    t = tickets[0]
    text = (f"🎫 <b><u>ACTIVE TICKET #{t[0]}</u></b>\n👤 <b>Origin UID:</b> <code>{t[1]}</code>\n📅 <b>Timestamp:</b> {t[3]}\n\n📝 <b>Payload:</b>\n{t[2]}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Formulate Reply", callback_data=f"reply_ticket_{t[0]}_{t[1]}", icon_custom_emoji_id=get_emoji_icon("telegram"), style="primary")],
        [InlineKeyboardButton(text="Force Close Ticket", callback_data=f"close_ticket_{t[0]}", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("close_ticket_"))
async def close_ticket(call: CallbackQuery):
    ticket_id = call.data.split("_")[2]
    db_query("UPDATE tickets SET status='Closed' WHERE id=?", (ticket_id,))
    await call.answer("✅ Status set to Closed.", show_alert=True)
    await admin_view_tickets(call) 

@dp.callback_query(F.data.startswith("reply_ticket_"))
async def reply_ticket_start(call: CallbackQuery, state: FSMContext):
    data = call.data.split("_")
    ticket_id, user_id = data[2], data[3]
    await state.update_data(ticket_id=ticket_id, user_id=user_id)
    await call.message.edit_text(f"💬 Formulating reply for node <code>{user_id}</code>.\n\nType your message payload:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.ticket_reply_msg)

@dp.message(AdminStates.ticket_reply_msg)
async def send_ticket_reply(m: Message, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.send_message(data['user_id'], f"📞 <b>Admin Reply (Ref #{data['ticket_id']}):</b>\n\n{m.text}", parse_mode='HTML')
        db_query("UPDATE tickets SET status='Closed' WHERE id=?", (data['ticket_id'],))
        await m.answer("✅ Payload delivered and connection closed successfully.", reply_markup=admin_kb(), parse_mode='HTML')
    except Exception as e: await m.answer(f"❌ Transmission Error: {e}", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_broadcast_btn")
async def admin_broadcast_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("📢 <b>Mass Broadcast Protocol</b>\n\nSend the rich message payload you wish to transmit globally across the grid:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.broadcast_msg)

@dp.message(AdminStates.broadcast_msg)
async def admin_broadcast_send(message: Message, state: FSMContext):
    users = db_query("SELECT user_id FROM users", fetchall=True)
    sent, failed = 0, 0
    m = await message.answer("⏳ Broadcast protocol initiated... Do not interrupt.", parse_mode='HTML')
    for u in users:
        try:
            await message.send_copy(chat_id=u[0])
            sent += 1
        except Exception: failed += 1
        await asyncio.sleep(0.06) 
    await m.edit_text(f"✅ <b>Global Broadcast Complete!</b>\n\n🟢 Nodes reached: {sent}\n🔴 Nodes failed/blocked: {failed}", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_create_coupon")
async def admin_create_coupon_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("🎟 Enter a highly secure alphanumeric sequence for the Promo Code:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.add_coupon_code)

@dp.message(AdminStates.add_coupon_code)
async def admin_coupon_code(m: Message, state: FSMContext):
    await state.update_data(code=m.text.strip().upper())
    await m.answer("💰 Enter the monetary reward payload in <b>RUPEES (₹)</b>:", parse_mode='HTML')
    await state.set_state(AdminStates.add_coupon_amount)

@dp.message(AdminStates.add_coupon_amount)
async def admin_coupon_amount(m: Message, state: FSMContext):
    try:
        await state.update_data(amount=float(m.text)) 
        await m.answer("👥 Enter the exact maximum threshold uses for this code:", parse_mode='HTML')
        await state.set_state(AdminStates.add_coupon_uses)
    except ValueError: await m.answer("❌ Non-numerical data detected. Aborting.")

@dp.message(AdminStates.add_coupon_uses)
async def admin_coupon_uses(m: Message, state: FSMContext):
    try:
        uses = int(m.text)
        data = await state.get_data()
        db_query("INSERT OR REPLACE INTO coupons (code, amount, uses_left) VALUES (?, ?, ?)", (data['code'], data['amount'], uses))
        await m.answer(f"✅ Protocol <b>{data['code']}</b> encoded!\nReward Vector: {fmt_curr(data['amount'])}\nThreshold Limit: {uses} executions.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except ValueError: await m.answer("❌ Non-numerical data detected. Aborting.")

# ==============================================================================
# 21A. ADMIN BULK MONEY / PRICE / GATEWAY / COLOR CONTROLS
# ==============================================================================
@dp.callback_query(F.data == "admin_zero_all_balances")
async def admin_zero_all_balances(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    total = db_query("SELECT COUNT(*) FROM users WHERE COALESCE(balance,0) != 0", fetchone=True)
    affected = int(total[0] or 0) if total else 0
    db_query("UPDATE users SET balance=0")
    log_activity(call.from_user.id, "ADMIN_ZERO_ALL_BALANCES", f"Affected users: {affected}")
    await call.answer(f"✅ {affected} user balances reset to ₹0.", show_alert=True)
    await call.message.edit_text("🧹 <b>ALL USER BALANCES RESET</b>\n\nAffected users: <b>%d</b>\nCurrent balance for every user: <b>₹0</b>" % affected, reply_markup=admin_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_bulk_price_menu")
async def admin_bulk_price_menu(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    count = db_query("SELECT COUNT(*) FROM products WHERE is_active=1", fetchone=True)
    n = int(count[0] or 0) if count else 0
    groups = get_common_validity_groups(active_only=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for idx, (key, group_count) in enumerate(groups[:30]):
        label = display_validity_group(key)
        kb.inline_keyboard.append([InlineKeyboardButton(
            text=f"⏱ {label}  •  {group_count} Products",
            callback_data=f"bulk_validity_group_{idx}",
            style="primary"
        )])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="🌐 ALL ACTIVE PRODUCTS", callback_data="bulk_validity_group_all", style="success")
    ])
    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel_back", style="danger")])
    await call.message.edit_text(
        "💸 <b>PREMIUM PRICE CONTROL</b>\n\n"
        f"Active packages: <b>{n}</b>\n\n"
        "Select a <b>common validity</b> to change all matching products together.\n"
        "Example: <b>1 Over</b> will update every product whose validity is 1 Over, regardless of panel name.\n\n"
        "You can also choose ALL for a global price adjustment.",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("bulk_validity_group_"))
async def admin_bulk_validity_group(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    token = call.data.split("bulk_validity_group_", 1)[1]
    if token == "all":
        title = "ALL ACTIVE PRODUCTS"
        callback_prefix = "bulk_validity_apply_all"
        count = db_query("SELECT COUNT(*) FROM products WHERE is_active=1", fetchone=True)
    else:
        try:
            idx = int(token)
            groups = get_common_validity_groups(active_only=True)
            key, _ = groups[idx]
        except (ValueError, IndexError):
            return await call.answer("❌ Validity group expired. Open the menu again.", show_alert=True)
        title = display_validity_group(key)
        callback_prefix = f"bulk_validity_apply_{idx}"
        rows = db_query("SELECT validity, name FROM products WHERE is_active=1", fetchall=True) or []
        count = sum(1 for r in rows if product_matches_validity_group(r[0] if r else "", r[1] if len(r) > 1 else "", key))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔻 -5%", callback_data=f"{callback_prefix}_dec_5", style="danger"), InlineKeyboardButton(text="🔺 +5%", callback_data=f"{callback_prefix}_inc_5", style="success")],
        [InlineKeyboardButton(text="🔻 -10%", callback_data=f"{callback_prefix}_dec_10", style="danger"), InlineKeyboardButton(text="🔺 +10%", callback_data=f"{callback_prefix}_inc_10", style="success")],
        [InlineKeyboardButton(text="🔻 -20%", callback_data=f"{callback_prefix}_dec_20", style="danger"), InlineKeyboardButton(text="🔺 +20%", callback_data=f"{callback_prefix}_inc_20", style="success")],
        [InlineKeyboardButton(text="🔻 -50%", callback_data=f"{callback_prefix}_dec_50", style="danger"), InlineKeyboardButton(text="🔺 +50%", callback_data=f"{callback_prefix}_inc_50", style="success")],
        [InlineKeyboardButton(text="🔙 Back to Validity Groups", callback_data="admin_bulk_price_menu", style="primary")]
    ])
    await call.message.edit_text(
        f"💰 <b>{html.escape(title)} PRICE CONTROL</b>\n\n"
        f"Matching active packages: <b>{int(count[0] or 0) if not isinstance(count, int) else count}</b>\n\n"
        "This change updates <b>Regular + Reseller</b> prices together.\n"
        "Choose the percentage below.",
        reply_markup=kb, parse_mode="HTML"
    )

@dp.callback_query(F.data.regexp(r"^bulk_validity_apply_(?:all|\d+)_(inc|dec)_(5|10|20|50)$"))
async def admin_bulk_validity_apply(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    parts = call.data.split("_")
    # all => [bulk, validity, apply, all, dec, 5]; indexed => [bulk, validity, apply, 3, dec, 5]
    target = parts[3]
    direction = parts[4]
    pct = float(parts[5])
    factor = 1 + (pct / 100 if direction == "inc" else -pct / 100)
    if target == "all":
        rows = db_query("SELECT id FROM products WHERE is_active=1", fetchall=True) or []
        ids = [int(r[0]) for r in rows]
        label = "ALL ACTIVE PRODUCTS"
    else:
        try:
            idx = int(target)
            groups = get_common_validity_groups(active_only=True)
            key, _ = groups[idx]
        except (ValueError, IndexError):
            return await call.answer("❌ Validity group expired. Open the menu again.", show_alert=True)
        rows = db_query("SELECT id, validity, name FROM products WHERE is_active=1", fetchall=True) or []
        ids = [int(r[0]) for r in rows if product_matches_validity_group(r[1] if len(r) > 1 else "", r[2] if len(r) > 2 else "", key)]
        label = display_validity_group(key)
    for pid in ids:
        db_query(
            "UPDATE products SET price_inr=ROUND(MAX(0, COALESCE(price_inr,0)*?),2), reseller_price=ROUND(MAX(0, COALESCE(reseller_price,0)*?),2) WHERE id=?",
            (factor, factor, pid)
        )
    sign = "+" if direction == "inc" else "-"
    log_activity(call.from_user.id, "ADMIN_VALIDITY_PRICE_ADJUST", f"{label}: {sign}{pct:g}% on {len(ids)} products")
    await call.answer(f"✅ {label}: {sign}{pct:g}% applied to {len(ids)} products.", show_alert=True)
    await admin_bulk_price_menu(call)

@dp.callback_query(F.data == "admin_gateway_control")
async def admin_gateway_control(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    z = "ON" if gateway_enabled("zapupi") else "OFF"
    f = "ON" if gateway_enabled("famgateway") else "OFF"
    zstyle = "success" if z == "ON" else "danger"
    fstyle = "success" if f == "ON" else "danger"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"ZapUPI: {'🟢' if z=='ON' else '🔴'} {z}", callback_data="admin_toggle_gateway_zapupi", style=zstyle)],
        [InlineKeyboardButton(text=f"FamGateway: {'🟢' if f=='ON' else '🔴'} {f}", callback_data="admin_toggle_gateway_famgateway", style=fstyle)],
        [InlineKeyboardButton(text="🔙 Back to Admin", callback_data="admin_panel_back", style="danger")]
    ])
    await call.message.edit_text("💳 <b>PAYMENT GATEWAY CONTROL</b>\n\nON gateway buttons are shown to users and are available for wallet top-up + direct product payment.\nOFF gateways are hidden/blocked everywhere.", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.in_(["admin_toggle_gateway_zapupi", "admin_toggle_gateway_famgateway"]))
async def admin_toggle_gateway(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    name = "zapupi" if call.data.endswith("zapupi") else "famgateway"
    key = f"gateway_{name}_status"
    new = "OFF" if gateway_enabled(name) else "ON"
    set_setting(key, new)
    log_activity(call.from_user.id, "ADMIN_GATEWAY_TOGGLE", f"{name}={new}")
    await admin_gateway_control(call)

@dp.callback_query(F.data == "admin_menu_colors")
async def admin_menu_colors(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    def label(key):
        return (get_setting(key, "blue") or "blue").upper()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🛠 Admin Menu: {label('admin_menu_color')}", callback_data="noop_color", style=admin_menu_style())],
        [InlineKeyboardButton(text="🔴", callback_data="admin_color_admin_red", style="danger"), InlineKeyboardButton(text="🔵", callback_data="admin_color_admin_blue", style="primary"), InlineKeyboardButton(text="🟢", callback_data="admin_color_admin_green", style="success")],
        [InlineKeyboardButton(text=f"📂 Plans/Panels: {label('panel_menu_color')}", callback_data="noop_color", style=panel_menu_style())],
        [InlineKeyboardButton(text="🔴", callback_data="admin_color_panel_red", style="danger"), InlineKeyboardButton(text="🔵", callback_data="admin_color_panel_blue", style="primary"), InlineKeyboardButton(text="🟢", callback_data="admin_color_panel_green", style="success")],
        [InlineKeyboardButton(text=f"📦 Products: {label('product_menu_color')}", callback_data="noop_color", style=product_menu_style())],
        [InlineKeyboardButton(text="🔴", callback_data="admin_color_product_red", style="danger"), InlineKeyboardButton(text="🔵", callback_data="admin_color_product_blue", style="primary"), InlineKeyboardButton(text="🟢", callback_data="admin_color_product_green", style="success")],
        [InlineKeyboardButton(text="🔙 Back to Admin", callback_data="admin_panel_back", style="danger")]
    ])
    await call.message.edit_text("🎨 <b>MENU COLOR CONTROL</b>\n\nSet colors independently:\n• Admin menu\n• Plans / Panels\n• Product buttons\n\nAvailable: 🔴 Red • 🔵 Blue • 🟢 Green", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.regexp(r"^admin_color_(admin|panel|product)_(red|blue|green)$"))
async def admin_set_menu_color(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    _, _, section, color = call.data.split("_")
    key = {"admin":"admin_menu_color", "panel":"panel_menu_color", "product":"product_menu_color"}[section]
    set_setting(key, color)
    log_activity(call.from_user.id, "ADMIN_MENU_COLOR", f"{section}={color}")
    await call.answer(f"✅ {section.title()} color set to {color}.", show_alert=True)
    await admin_menu_colors(call)

@dp.callback_query(F.data == "noop_color")
async def noop_color(call: CallbackQuery):
    await call.answer("Choose a color below.")

# ==============================================================================
# 21. ADMIN RESELLER & SPIN SETTINGS
# ==============================================================================
@dp.callback_query(F.data == "admin_reseller_menu")
async def admin_reseller_menu(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    status_check = db_query("SELECT value FROM settings WHERE key='reseller_system_status'", fetchone=True)
    sys_status = status_check[0] if status_check else "ON"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Grant Reseller Rights", callback_data="reseller_make", icon_custom_emoji_id=get_emoji_icon("reseller"), style="success"), 
         InlineKeyboardButton(text="Revoke Reseller", callback_data="reseller_remove", icon_custom_emoji_id=get_emoji_icon("reseller"), style="danger")],
        [InlineKeyboardButton(text="Audit Active Resellers", callback_data="reseller_view", icon_custom_emoji_id=get_emoji_icon("history"), style="primary")],
        [InlineKeyboardButton(text=f"{'🟢' if sys_status == 'ON' else '🔴'} Auto-Upgrade System: {sys_status}", callback_data="admin_toggle_reseller_sys", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="success" if sys_status == 'ON' else "danger")], 
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text("👑 <b>Wholesale Reseller Protocols</b>\nSelect administrative action:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "admin_toggle_reseller_sys")
async def toggle_reseller_sys(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    res = db_query("SELECT value FROM settings WHERE key='reseller_system_status'", fetchone=True)
    current = res[0] if res else 'ON'
    new_status = 'OFF' if current == 'ON' else 'ON'
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('reseller_system_status', ?)", (new_status,))
    await admin_reseller_menu(call)

@dp.callback_query(F.data.in_(["reseller_make", "reseller_remove"]))
async def reseller_prompt_id(call: CallbackQuery, state: FSMContext):
    action = call.data
    await state.update_data(reseller_action=action)
    await call.message.edit_text("👤 Identify target node. Input <b>User ID</b> or <b>@username</b>:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.reseller_manage_id)

@dp.message(AdminStates.reseller_manage_id)
async def process_reseller_manage(m: Message, state: FSMContext):
    data = await state.get_data()
    target = m.text.strip()
    if target.startswith('@'): target = target[1:]
    user_q = db_query("SELECT user_id, first_name FROM users WHERE user_id=? OR username=? COLLATE NOCASE", (target, target), fetchone=True)
    if not user_q: return await m.answer("❌ Target completely ghosted. Not in database.", reply_markup=admin_back_kb(), parse_mode='HTML')
    u_id, u_name = user_q[0], user_q[1]
    if data['reseller_action'] == "reseller_make":
        db_query("UPDATE users SET is_reseller=1, reseller_since=?, account_type='Reseller' WHERE user_id=?", (datetime.now().strftime("%Y-%m-%d"), u_id))
        await m.answer(f"✅ Credentials upgraded. <b>{u_name}</b> (<code>{u_id}</code>) has reseller rights.", reply_markup=admin_kb(), parse_mode='HTML')
    else:
        db_query("UPDATE users SET is_reseller=0, account_type='Regular' WHERE user_id=?", (u_id,))
        await m.answer(f"✅ Credentials revoked. <b>{u_name}</b> (<code>{u_id}</code>) is back to regular user.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "reseller_view")
async def reseller_view(call: CallbackQuery):
    resellers = db_query("SELECT user_id, first_name, username FROM users WHERE is_reseller=1", fetchall=True)
    if not resellers: return await call.message.edit_text("📋 Zero active resellers found.", reply_markup=admin_back_kb(), parse_mode='HTML')
    text = "👑 <b><u>ACTIVE RESELLER AUDIT LOG</u></b> 👑\n━━━━━━━━━━━━━━━━━━\n"
    for r in resellers:
        uname = f"(@{r[2]})" if r[2] else ""
        text += f"👤 {r[1]} {uname}\n🆔 <code>{r[0]}</code>\n\n"
    await call.message.edit_text(text, reply_markup=admin_back_kb(), parse_mode='HTML')

@dp.callback_query(F.data == "admin_spin_menu")
async def admin_spin_menu(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    status = db_query("SELECT value FROM settings WHERE key='spin_status'", fetchone=True)
    pool = db_query("SELECT value FROM settings WHERE key='daily_spin_pool'", fetchone=True)
    spent = db_query("SELECT value FROM settings WHERE key='daily_spin_spent'", fetchone=True)
    spent_date = db_query("SELECT value FROM settings WHERE key='daily_spin_spent_date'", fetchone=True)
    status_val = status[0] if status else 'ON'
    pool_val = float(pool[0]) if pool else 5.0
    spent_val = float(spent[0]) if spent else 0.0
    if (spent_date[0] if spent_date else '') != datetime.now().strftime('%Y-%m-%d'):
        spent_val = 0.0
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Append Reward Logic", callback_data="spin_add", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success"), 
         InlineKeyboardButton(text="Drop Reward Logic", callback_data="spin_del", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")],
        [InlineKeyboardButton(text="Audit Configs", callback_data="spin_view", icon_custom_emoji_id=get_emoji_icon("history"), style="primary"), 
         InlineKeyboardButton(text="Set Daily ₹5 Pool", callback_data="spin_limit", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary")],
        [InlineKeyboardButton(text=f"{'🟢' if status_val == 'ON' else '🔴'} Master Toggle: {status_val}", callback_data="spin_toggle", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="success" if status_val == 'ON' else "danger")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    rewards_count = db_query("SELECT COUNT(*) FROM spin_rewards", fetchone=True)[0] or 0
    await call.message.edit_text(
        f"🎰 <b>Advanced Ludo / Spin Control</b>\n\n"
        f"🟢 Status: <b>{status_val}</b>\n"
        f"🎁 Reward Rules: <b>{rewards_count}</b> configured\n"
        f"💰 <b>Daily Global Pool:</b> ₹{pool_val:.2f}\n"
        f"📊 <b>Used Today:</b> ₹{spent_val:.2f} / ₹{pool_val:.2f}\n"
        f"⏱ User cooldown: <b>24 hours</b>\n"
        f"🌟 VIP multiplier is capped by the same global pool.\n\n"
        "All users combined can receive at most the configured daily pool. Default is <b>₹5/day total</b>.",
        reply_markup=kb, parse_mode='HTML'
    )

@dp.callback_query(F.data == "spin_toggle")
async def spin_toggle(call: CallbackQuery):
    res = db_query("SELECT value FROM settings WHERE key='spin_status'", fetchone=True)
    current = res[0] if res else 'ON'
    new_status = 'OFF' if current == 'ON' else 'ON'
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('spin_status', ?)", (new_status,))
    await admin_spin_menu(call)

@dp.callback_query(F.data == "admin_toggle_bot")
async def toggle_bot(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    res = db_query("SELECT value FROM settings WHERE key='bot_status'", fetchone=True)
    current = res[0] if res else 'ON'
    new_status = 'OFF' if current == 'ON' else 'ON'
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('bot_status', ?)", (new_status,))
    await call.message.edit_reply_markup(reply_markup=admin_kb())

@dp.callback_query(F.data == "spin_add")
async def spin_add_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.update_data(spin_action="add")
    await call.message.edit_text(
        "🎰 <b>Inject New Reward Logic</b>\n\nEnter reward amount (e.g. <code>15.50</code> or <code>0</code> for zero reward):",
        reply_markup=admin_back_kb(), parse_mode='HTML'
    )
    await state.set_state(AdminStates.spin_add_reward)

@dp.message(AdminStates.spin_add_reward)
async def spin_add_exec(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    spin_action = data.get("spin_action", "add")
    raw = m.text.strip()
    if spin_action == "delete":
        try:
            rid = int(raw)
            row = db_query("SELECT id, amount FROM spin_rewards WHERE id=?", (rid,), fetchone=True)
            if not row:
                return await m.answer(f"❌ No reward found with ID {rid}. Please check the list and try again.")
            db_query("DELETE FROM spin_rewards WHERE id=?", (rid,))
            await m.answer(f"✅ Reward [ID: {rid}] → {fmt_curr(row[1])} has been removed from the prize pool.", reply_markup=admin_kb(), parse_mode='HTML')
            await state.clear()
        except ValueError:
            await m.answer("❌ Please send a valid reward <b>ID number</b> (e.g. <code>2</code>).", parse_mode='HTML')
    else:
        try:
            amt = float(raw)
            db_query("INSERT INTO spin_rewards (amount) VALUES (?)", (amt,))
            await m.answer(f"✅ New reward {fmt_curr(amt)} added to the prize pool!", reply_markup=admin_kb(), parse_mode='HTML')
            await state.clear()
        except ValueError:
            await m.answer("❌ Invalid amount. Please send a number (e.g. <code>15.50</code>).", parse_mode='HTML')

@dp.callback_query(F.data == "spin_view")
async def spin_view(call: CallbackQuery):
    rewards = db_query("SELECT id, amount FROM spin_rewards ORDER BY amount ASC", fetchall=True)
    text = "🎰 <b>Live Ludo Reward Constants</b>\n\n"
    for r in rewards:
        text += f"🎁 [ID: {r[0]}] → {fmt_curr(r[1])}\n"
    text += "\n<i>Use '❌ Drop Reward Logic' to delete a reward by ID.</i>"
    await call.message.edit_text(text, reply_markup=admin_back_kb(), parse_mode='HTML')

@dp.callback_query(F.data == "spin_del")
async def spin_del_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    rewards = db_query("SELECT id, amount FROM spin_rewards ORDER BY amount ASC", fetchall=True)
    if not rewards:
        return await call.answer("⚠️ No rewards configured yet.", show_alert=True)
    text = "🗑 <b>Drop Reward Logic</b>\n\n"
    for r in rewards:
        text += f"🎁 [ID: {r[0]}] → {fmt_curr(r[1])}\n"
    text += "\n👉 Reply with the <b>ID number</b> of the reward to delete (e.g. <code>2</code>):"
    await call.message.edit_text(text, reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.update_data(spin_action="delete")
    await state.set_state(AdminStates.spin_add_reward)

@dp.callback_query(F.data == "spin_limit")
async def spin_limit_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    pool_check = db_query("SELECT value FROM settings WHERE key='daily_spin_pool'", fetchone=True)
    current_pool = pool_check[0] if pool_check else "5.0"
    await call.message.edit_text(
        f"⚙️ <b>Set Daily Global Spin Pool</b>\n\n"
        f"Current Pool: <b>₹{current_pool}</b>\n\n"
        "This is the maximum combined payout for ALL users in one calendar day.\n"
        "Default is ₹5 total/day.\n\n"
        "👉 Send new daily pool amount (e.g. <code>5</code> or <code>10</code>):",
        reply_markup=admin_back_kb(),
        parse_mode='HTML'
    )
    await state.set_state(AdminStates.spin_set_limit)

@dp.message(AdminStates.spin_set_limit)
async def spin_limit_exec(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    try:
        val = float(m.text.strip())
        if val < 0:
            return await m.answer("❌ Limit must be a positive number.")
        db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('daily_spin_pool', ?)", (str(val),))
        await m.answer(f"✅ Global daily spin pool updated to <b>{fmt_curr(val)}</b>", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except ValueError:
        await m.answer("❌ Invalid number. Please send e.g. <code>25.0</code> or <code>50</code>.")

@dp.callback_query(F.data == "admin_set_video")
async def admin_set_video_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("📹 Input direct streaming / YouTube Link for Tutorial system:\n<i>(Or type 'None' to clear registry):</i>", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_howto_video)

@dp.message(AdminStates.wait_for_howto_video)
async def exec_set_video(m: Message, state: FSMContext):
    link = m.text.strip()
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('how_to_video', ?)", (link,))
    await m.answer("✅ Routing complete. Video linked.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_payment_proof")
async def admin_set_payment_proof_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    current = get_setting("payment_proof_channel", "")
    await call.message.edit_text(
        "🧾 <b>Payment Proof Channel</b>\n\n"
        f"Current: <code>{current or 'Not configured'}</code>\n\n"
        "Send the <b>public channel username</b> (example: <code>@yourchannel</code>) "
        "or its public <code>https://t.me/yourchannel</code> link.\n\n"
        "⚠️ Add this bot as an admin in that channel with permission to post messages.\n"
        "🔐 Purchase keys will NEVER be posted there.",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    await state.set_state(AdminStates.wait_for_payment_proof_channel)

@dp.message(AdminStates.wait_for_payment_proof_channel)
async def save_payment_proof_channel(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    value = (m.text or "").strip()
    if value.lower() in ("none", "off", "disable", "disabled"):
        set_setting("payment_proof_channel", "")
        await state.clear()
        return await m.answer("✅ Payment Proof channel disabled.", reply_markup=admin_kb(), parse_mode="HTML")

    if value.startswith(("https://t.me/+", "http://t.me/+", "t.me/+")):
        return await m.answer("❌ Private invite link नहीं चलेगा. Public channel username जैसे <code>@yourchannel</code> भेजें.", parse_mode="HTML")
    if not (value.startswith("@") or "t.me/" in value):
        return await m.answer("❌ Public channel username भेजें, जैसे <code>@yourchannel</code>.", parse_mode="HTML")

    set_setting("payment_proof_channel", value)
    await state.clear()
    await m.answer(
        "✅ <b>Payment Proof Channel saved.</b>\n\n"
        "Successful purchases will now be posted there automatically.\n"
        "🔐 Customer keys will remain hidden.",
        reply_markup=admin_kb(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "admin_set_all_files")
async def admin_set_all_files_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("🔗 Input the direct Channel / Cloud URL for 'Download Files' button:\n<i>(Or type 'None' to format data):</i>", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_all_files_link)

@dp.message(AdminStates.wait_for_all_files_link)
async def exec_set_all_files(m: Message, state: FSMContext):
    link = m.text.strip()
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('all_files_link', ?)", (link,))
    await m.answer("✅ Global resource variable updated.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_edit_emojis")
async def admin_edit_emojis(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    rows = db_query("SELECT key, value FROM settings WHERE key LIKE 'emoji_%' ORDER BY key", fetchall=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for row in rows:
        key = row[0]
        slot = key.replace("emoji_", "")
        current_id = row[1] if row[1] else "Not set"
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{slot} (ID: {current_id})", callback_data=f"edit_emoji_{slot}", style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text("🎨 <b>Edit All Emojis</b>\nChoose an emoji slot to change its ID:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("edit_emoji_"))
async def admin_edit_emoji_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    slot = call.data.split("edit_emoji_", 1)[1]
    await state.update_data(emoji_slot=slot)
    current = get_setting(f"emoji_{slot}", "Not set")
    await call.message.edit_text(f"✏️ Enter new emoji ID for <b>{slot}</b>:\nCurrent: {current}\n(Leave empty to reset to default)", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_emoji_slot)

@dp.message(AdminStates.wait_for_emoji_slot)
async def save_emoji_slot(m: Message, state: FSMContext):
    data = await state.get_data()
    slot = data['emoji_slot']
    new_id = m.text.strip()
    if new_id == "":
        db_query("DELETE FROM settings WHERE key=?", (f"emoji_{slot}",))
        await m.answer(f"✅ Reset emoji for '{slot}' to default.", reply_markup=admin_kb(), parse_mode='HTML')
    else:
        if not new_id.isdigit():
            await m.answer("❌ Invalid ID! Must be numeric.", reply_markup=admin_kb(), parse_mode='HTML')
            return
        set_setting(f"emoji_{slot}", new_id)
        await m.answer(f"✅ Emoji for '{slot}' updated to ID {new_id}.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_edit_ui_menu")
async def admin_edit_ui_menu(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Edit Start Menu Text", callback_data="edit_ui_start", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
        [InlineKeyboardButton(text="Edit Download Files Text", callback_data="edit_ui_download", icon_custom_emoji_id=get_emoji_icon("download"), style="primary")],
        [InlineKeyboardButton(text="Edit VIP Menu Text", callback_data="edit_ui_vip", icon_custom_emoji_id=get_emoji_icon("vip"), style="primary")],
        [InlineKeyboardButton(text="Edit Lucky Dice Text", callback_data="edit_ui_dice", icon_custom_emoji_id=get_emoji_icon("ludo_spin"), style="primary")],
        [InlineKeyboardButton(text="Edit Add Balance Text", callback_data="edit_ui_add_balance", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="primary")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text("✏️ <b>Edit User Interface Texts</b>\nSelect which text you want to modify:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("edit_ui_"))
async def admin_edit_ui_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    ui_key = call.data.split("_")[2]
    await state.update_data(ui_key=ui_key)
    current_text = get_ui_text(ui_key)
    await call.message.edit_text(f"📝 Send the new text for <b>{ui_key.upper()}</b> menu.\n\nCurrent text:\n{current_text}", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.edit_ui_text)

@dp.message(AdminStates.edit_ui_text)
async def admin_save_ui_text(m: Message, state: FSMContext):
    data = await state.get_data()
    ui_key = data['ui_key']
    new_text = m.text
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (f"ui_{ui_key}", new_text))
    await m.answer(f"✅ UI text <b>{ui_key}</b> updated successfully!", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_edit_reseller_price")
async def admin_edit_reseller_price_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    prods = db_query("SELECT id, name, category, panel_name, reseller_price FROM products", fetchall=True)
    prods = sorted(prods or [], key=lambda row: (natural_sort_key(row[2]), natural_sort_key(row[3]), natural_sort_key(row[1])))
    if not prods: return await call.message.edit_text("No products to edit.", reply_markup=admin_back_kb(), parse_mode='HTML')
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for p in prods:
        panel_name = p[3] if p[3] is not None else ""
        r_price = float(p[4]) if p[4] is not None else 0.0
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{p[2]} - {panel_name} - {p[1]} (₹{r_price:.2f})", callback_data=f"edit_reseller_{p[0]}", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text("👑 <b>Edit Reseller Price per Product</b>\nSelect a product to change its wholesale price:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("edit_reseller_"))
async def admin_edit_reseller_price_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    prod_id = int(call.data.split("_")[2])
    await state.update_data(edit_reseller_prod_id=prod_id)
    await call.message.edit_text("💰 Enter the new <b>Reseller Price</b> in Rupees (₹) for this product:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.edit_reseller_price)

@dp.message(AdminStates.edit_reseller_price)
async def admin_save_reseller_price(m: Message, state: FSMContext):
    try:
        new_price = float(m.text)
        data = await state.get_data()
        prod_id = data['edit_reseller_prod_id']
        db_query("UPDATE products SET reseller_price=? WHERE id=?", (new_price, prod_id))
        await m.answer(f"✅ Reseller price updated to {fmt_curr(new_price)} for product ID {prod_id}.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except ValueError: await m.answer("❌ Invalid number. Please enter a valid price.")

@dp.callback_query(F.data == "admin_set_reseller_fee")
async def admin_set_reseller_fee(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("💰 Enter the new <b>Reseller Setup Fee</b> in Rupees (₹):\nCurrent: " + get_setting("reseller_setup_fee", "200.0"), reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_reseller_setup_fee)

@dp.message(AdminStates.wait_for_reseller_setup_fee)
async def admin_save_reseller_fee(m: Message, state: FSMContext):
    try:
        fee = float(m.text)
        set_setting("reseller_setup_fee", str(fee))
        await m.answer(f"✅ Reseller setup fee updated to {fmt_curr(fee)}.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except ValueError: await m.answer("❌ Invalid number. Please enter a valid amount.")

@dp.callback_query(F.data == "admin_set_reseller_min")
async def admin_set_reseller_min(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("💳 Enter the new <b>Minimum Balance</b> required to become reseller (₹):\nCurrent: " + get_setting("reseller_min_balance", "500.0"), reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_reseller_min_balance)

@dp.message(AdminStates.wait_for_reseller_min_balance)
async def admin_save_reseller_min(m: Message, state: FSMContext):
    try:
        min_bal = float(m.text)
        set_setting("reseller_min_balance", str(min_bal))
        await m.answer(f"✅ Minimum reseller balance updated to {fmt_curr(min_bal)}.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except ValueError: await m.answer("❌ Invalid number. Please enter a valid amount.")

@dp.callback_query(F.data == "admin_set_support_links")
async def admin_set_support_links(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Set Telegram Link", callback_data="admin_set_telegram", icon_custom_emoji_id=get_emoji_icon("telegram"), style="primary")],
        [InlineKeyboardButton(text="Set WhatsApp Link", callback_data="admin_set_whatsapp", icon_custom_emoji_id=get_emoji_icon("whatsapp"), style="primary")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text("📌 <b>Support Contact Links</b>\nSet the URLs for Telegram and WhatsApp support:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "admin_set_telegram")
async def admin_set_telegram(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("✈️ Enter the Telegram contact URL (e.g., https://t.me/YOUR_SUPPORT):", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_support_telegram)

@dp.message(AdminStates.wait_for_support_telegram)
async def save_telegram_link(m: Message, state: FSMContext):
    link = m.text.strip()
    set_setting("support_telegram", link)
    await m.answer("✅ Telegram support link updated!", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_whatsapp")
async def admin_set_whatsapp(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("📱 Enter the WhatsApp contact URL (e.g., https://wa.me/1234567890):", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_support_whatsapp)

@dp.message(AdminStates.wait_for_support_whatsapp)
async def save_whatsapp_link(m: Message, state: FSMContext):
    link = m.text.strip()
    set_setting("support_whatsapp", link)
    await m.answer("✅ WhatsApp support link updated!", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_category_emojis")
async def admin_set_category_emojis(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for cat in get_all_categories():
        custom_id = get_setting(f"cat_custom_id_{cat}", "").strip()
        stored = get_setting(f"cat_emoji_{cat}", "").strip()
        if custom_id and custom_id.isdigit():
            btn_text = f"{cat} (Custom Emoji)"
            btn_icon = custom_id
        elif stored and stored.isdigit():
            btn_text = f"{cat} (Custom Emoji)"
            btn_icon = stored
        elif stored:
            btn_text = f"{stored} {cat}"
            btn_icon = None
        else:
            ico = get_category_display_icon(cat)
            btn_text = f"{ico} {cat} (Default)"
            btn_icon = None
        kb.inline_keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"set_cat_emoji_{cat}", icon_custom_emoji_id=btn_icon, style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text("🎨 <b>Set Category Emojis</b>\n\nChoose a category to set its emoji (send any emoji like 🔥, ⚡, 🍎, 💻 or custom emoji):", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("set_cat_emoji_"))
async def admin_set_category_emoji_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    category = call.data.split("set_cat_emoji_", 1)[1]
    await state.update_data(cat_emoji_category=category)
    current = get_setting(f"cat_emoji_{category}", "Default")
    await call.message.edit_text(
        f"🎨 <b>Set Emoji for Category:</b> <code>{category}</code>\n"
        f"<b>Current:</b> {current}\n\n"
        "👉 Send an emoji (e.g. ⚡, 🛡️, 🍎, 💻, 🎯, 🔥) or Telegram custom emoji.\n"
        "<i>(Send 'reset' or leave empty to restore default)</i>",
        reply_markup=admin_back_kb(),
        parse_mode='HTML'
    )
    await state.set_state(AdminStates.wait_for_category_emoji)

@dp.message(AdminStates.wait_for_category_emoji)
async def save_category_emoji(m: Message, state: FSMContext):
    data = await state.get_data()
    category = data['cat_emoji_category']
    raw = (m.text or "").strip()
    if raw.lower() in ("", "reset", "default"):
        db_query("DELETE FROM settings WHERE key=?", (f"cat_emoji_{category}",))
        db_query("DELETE FROM settings WHERE key=?", (f"cat_custom_id_{category}",))
        await m.answer(f"✅ Reset emoji for {category} to default.", reply_markup=admin_kb(), parse_mode='HTML')
    else:
        emoji_char, custom_id = extract_emoji_or_id(m)
        if custom_id:
            set_setting(f"cat_custom_id_{category}", custom_id)
            set_setting(f"cat_emoji_{category}", custom_id)
            disp = f'<tg-emoji emoji-id="{custom_id}">⭐</tg-emoji>'
            await m.answer(f"✅ Premium Custom Emoji set for <b>{category}</b>: {disp}", reply_markup=admin_kb(), parse_mode='HTML')
        else:
            to_store = emoji_char or raw
            db_query("DELETE FROM settings WHERE key=?", (f"cat_custom_id_{category}",))
            set_setting(f"cat_emoji_{category}", to_store)
            await m.answer(f"✅ Emoji set for <b>{category}</b>: {to_store}", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_panel_emojis")
async def admin_set_panel_emojis(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    panels = db_query("SELECT DISTINCT panel_name FROM products WHERE panel_name != '' ORDER BY panel_name", fetchall=True)
    if not panels:
        await call.message.edit_text("No panel names found in products.", reply_markup=admin_back_kb(), parse_mode='HTML')
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for p in panels:
        panel = p[0]
        custom_id = get_setting(f"panel_custom_id_{panel}", "").strip()
        stored = get_setting(f"panel_emoji_{panel}", "").strip()
        if custom_id and custom_id.isdigit():
            btn_text = f"{panel} (Custom Emoji)"
            btn_icon = custom_id
        elif stored and stored.isdigit():
            btn_text = f"{panel} (Custom Emoji)"
            btn_icon = stored
        elif stored:
            btn_text = f"{stored} {panel}"
            btn_icon = None
        else:
            pnl_ico = get_panel_display_icon(panel)
            btn_text = f"{pnl_ico} {panel} (Default)"
            btn_icon = None
        kb.inline_keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"set_panel_emoji_{panel}", icon_custom_emoji_id=btn_icon, style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text("🖼 <b>Set Panel Emojis</b>\n\nChoose a panel name to set its emoji (send any emoji like 🔥, ⚡, 🛡️, 💎, 👑 or custom emoji):", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("set_panel_emoji_"))
async def admin_set_panel_emoji_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    panel_name = call.data.split("set_panel_emoji_", 1)[1]
    await state.update_data(panel_emoji_name=panel_name)
    current = get_setting(f"panel_emoji_{panel_name}", "Default")
    await call.message.edit_text(
        f"🎨 <b>Set Emoji for Panel:</b> <code>{panel_name}</code>\n"
        f"<b>Current:</b> {current}\n\n"
        "👉 Send an emoji (e.g. 🔥, ⚡, 👑, 🛡️, 💎) or Telegram custom emoji.\n"
        "<i>(Send 'reset' or leave empty to reset to default)</i>",
        reply_markup=admin_back_kb(),
        parse_mode='HTML'
    )
    await state.set_state(AdminStates.wait_for_panel_emoji_id)

@dp.message(AdminStates.wait_for_panel_emoji_id)
async def save_panel_emoji(m: Message, state: FSMContext):
    data = await state.get_data()
    panel_name = data['panel_emoji_name']
    raw = (m.text or "").strip()
    if raw.lower() in ("", "reset", "default"):
        db_query("DELETE FROM settings WHERE key=?", (f"panel_emoji_{panel_name}",))
        db_query("DELETE FROM settings WHERE key=?", (f"panel_custom_id_{panel_name}",))
        await m.answer(f"✅ Reset emoji for panel '{panel_name}'.", reply_markup=admin_kb(), parse_mode='HTML')
    else:
        emoji_char, custom_id = extract_emoji_or_id(m)
        if custom_id:
            set_setting(f"panel_custom_id_{panel_name}", custom_id)
            set_setting(f"panel_emoji_{panel_name}", custom_id)
            disp = f'<tg-emoji emoji-id="{custom_id}">⭐</tg-emoji>'
            await m.answer(f"✅ Premium Custom Emoji set for panel '<b>{panel_name}</b>': {disp}", reply_markup=admin_kb(), parse_mode='HTML')
        else:
            to_store = emoji_char or raw
            db_query("DELETE FROM settings WHERE key=?", (f"panel_custom_id_{panel_name}",))
            set_setting(f"panel_emoji_{panel_name}", to_store)
            await m.answer(f"✅ Emoji for panel '<b>{panel_name}</b>' set to: {to_store}", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

# ==============================================================================
# DURATION EMOJI MANAGEMENT (ADMIN)
# ==============================================================================
DURATION_PRESETS = [
    ("1 Hour", "1 hour"), ("2 Hours", "2 hour"), ("3 Hours", "3 hour"),
    ("4 Hours", "4 hour"), ("6 Hours", "6 hour"), ("8 Hours", "8 hour"),
    ("12 Hours", "12 hour"), ("24 Hours", "24 hour"),
    ("1 Day", "1 day"), ("2 Days", "2 day"), ("3 Days", "3 day"),
    ("4 Days", "4 day"), ("5 Days", "5 day"), ("7 Days", "7 day"),
    ("10 Days", "10 day"), ("14 Days", "14 day"), ("15 Days", "15 day"),
    ("20 Days", "20 day"), ("21 Days", "21 day"), ("28 Days", "28 day"),
    ("30 Days", "30 day"), ("45 Days", "45 day"), ("60 Days", "60 day"),
    ("90 Days", "90 day"),
]

@dp.callback_query(F.data == "admin_set_duration_emojis")
async def admin_set_duration_emojis_menu(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for label, key in DURATION_PRESETS:
        dur_custom_id = get_setting(f"dur_custom_id_{key}", "").strip()
        dur_stored = get_setting(f"dur_emoji_{key}", "").strip()
        if dur_custom_id and dur_custom_id.isdigit():
            btn_text = f"{label} (Custom Emoji)"
            btn_icon = dur_custom_id
        elif dur_stored and dur_stored.isdigit():
            btn_text = f"{label} (Custom Emoji)"
            btn_icon = dur_stored
        elif dur_stored:
            btn_text = f"{dur_stored} {label}"
            btn_icon = None
        else:
            cur_emoji = get_duration_emoji(key)
            if cur_emoji and cur_emoji.isdigit():
                btn_text = f"{label} (Custom Emoji)"
                btn_icon = cur_emoji
            else:
                btn_text = f"{cur_emoji} {label}"
                btn_icon = None
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=btn_text, callback_data=f"set_dur_emoji_{key}", icon_custom_emoji_id=btn_icon, style="primary")
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")
    ])
    await call.message.edit_text(
        "⏱ <b>Set Duration Emojis</b>\n\n"
        "Choose a package/duration below to customize the emoji that appears on buttons and product descriptions in your shop:",
        reply_markup=kb,
        parse_mode='HTML'
    )

@dp.callback_query(F.data.startswith("set_dur_emoji_"))
async def admin_set_dur_emoji_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    dur_key = call.data.split("set_dur_emoji_", 1)[1]
    await state.update_data(dur_key=dur_key)
    cur = get_duration_emoji(dur_key)
    await call.message.edit_text(
        f"🎨 <b>Set Emoji for Duration:</b> <code>{dur_key.title()}</code>\n"
        f"<b>Current Emoji:</b> {cur}\n\n"
        "👉 Send an emoji (e.g. ⚡, 🔥, 💎, 👑, ⏱️, 🚀) or Telegram custom emoji.\n"
        "<i>(Send 'reset' or leave empty to restore default)</i>",
        reply_markup=admin_back_kb(),
        parse_mode='HTML'
    )
    await state.set_state(AdminStates.wait_for_duration_emoji)

@dp.message(AdminStates.wait_for_duration_emoji)
async def save_duration_emoji(m: Message, state: FSMContext):
    data = await state.get_data()
    dur_key = data['dur_key'].lower().strip()
    raw = (m.text or "").strip()
    if raw.lower() in ("", "reset", "default"):
        db_query("DELETE FROM settings WHERE key=?", (f"dur_emoji_{dur_key}",))
        db_query("DELETE FROM settings WHERE key=?", (f"dur_custom_id_{dur_key}",))
        await m.answer(f"✅ Reset emoji for {dur_key.title()} to default.", reply_markup=admin_kb(), parse_mode='HTML')
    else:
        emoji_char, custom_id = extract_emoji_or_id(m)
        if custom_id:
            set_setting(f"dur_custom_id_{dur_key}", custom_id)
            set_setting(f"dur_emoji_{dur_key}", custom_id)
            disp = f'<tg-emoji emoji-id="{custom_id}">⚡</tg-emoji>'
            await m.answer(f"✅ Premium Custom Emoji for duration '<b>{dur_key.title()}</b>' set to: {disp}", reply_markup=admin_kb(), parse_mode='HTML')
        else:
            to_store = emoji_char or raw
            db_query("DELETE FROM settings WHERE key=?", (f"dur_custom_id_{dur_key}",))
            set_setting(f"dur_emoji_{dur_key}", to_store)
            await m.answer(f"✅ Duration emoji for <b>{dur_key.title()}</b> set to: {to_store}", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()


# ==============================================================================
# CATEGORY MANAGEMENT (ADMIN)
# ==============================================================================
@dp.callback_query(F.data == "admin_manage_categories")
async def admin_manage_categories(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    cats = db_query("SELECT id, name, emoji_id, sort_order FROM categories ORDER BY sort_order ASC, id ASC", fetchall=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    text = (
        "🗂 <b><u>CATEGORY MANAGEMENT</u></b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Click any category to <b>Rename</b>, <b>Change Position</b>, <b>Set Emoji</b>, or <b>Delete</b>.\n\n"
    )
    if not cats:
        text += "<i>No categories found. Click 'Add Category' below.</i>"
    else:
        for c in cats:
            cid, cname, cemj, corder = c
            prod_cnt = db_query("SELECT COUNT(*) FROM products WHERE category LIKE ?", (cname + '%',), fetchone=True)[0]
            emj_icon = get_category_emoji(cname)
            kb.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"[#{corder}] {cname} ({prod_cnt} prods)", 
                    callback_data=f"adm_cat_v_{cid}", 
                    icon_custom_emoji_id=emj_icon or get_emoji_icon("info_icon"),
                    style="primary"
                )
            ])
    
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="Add New Category", callback_data="admin_add_cat_start", icon_custom_emoji_id=get_emoji_icon("product_store"), style="success")
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "admin_add_cat_start")
async def admin_add_cat_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text(
        "➕ <b>Add New Category</b>\n\nEnter the name for the new category (e.g. <code>EMULATOR PANEL</code> or <code>BGMI PANEL</code>):",
        reply_markup=admin_back_kb(),
        parse_mode='HTML'
    )
    await state.set_state(AdminStates.add_category_name)

@dp.message(AdminStates.add_category_name)
async def admin_save_new_cat(m: Message, state: FSMContext):
    new_name = m.text.strip().upper()
    if not new_name:
        return await m.answer("❌ Category name cannot be empty.", reply_markup=admin_kb())
    existing = db_query("SELECT id FROM categories WHERE UPPER(name)=?", (new_name,), fetchone=True)
    if existing:
        return await m.answer(f"❌ Category <b>{new_name}</b> already exists!", reply_markup=admin_kb(), parse_mode='HTML')
    max_order = db_query("SELECT COALESCE(MAX(sort_order), 0) FROM categories", fetchone=True)[0]
    db_query("INSERT INTO categories (name, sort_order) VALUES (?, ?)", (new_name, max_order + 1))
    await m.answer(f"✅ <b>Category Added!</b>\n\nCategory <b>{new_name}</b> is now active at position #{max_order + 1}.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data.startswith("adm_cat_v_"))
async def admin_view_cat(call: CallbackQuery, cid: Optional[int] = None):
    if call.from_user.id != ADMIN_ID: return
    if cid is None:
        cid = int(call.data.split("adm_cat_v_")[1])
    cat = db_query("SELECT id, name, emoji_id, sort_order FROM categories WHERE id=?", (cid,), fetchone=True)
    if not cat: return await call.answer("Category not found.", show_alert=True)
    cid, cname, cemj, corder = cat
    prod_cnt = db_query("SELECT COUNT(*) FROM products WHERE category LIKE ?", (cname + '%',), fetchone=True)[0]
    
    text = (
        f"🏷️ <b><u>CATEGORY DETAILS</u></b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>Name:</b> <code>{cname}</code>\n"
        f"<b>Position / Sort Order:</b> #{corder}\n"
        f"<b>Emoji ID:</b> <code>{cemj or 'Default'}</code>\n"
        f"<b>Associated Products:</b> {prod_cnt} items\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>Select an action below to modify this category:</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Rename Category", callback_data=f"adm_cat_ren_{cid}", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary"),
            InlineKeyboardButton(text="Set Position #", callback_data=f"adm_cat_pos_{cid}", icon_custom_emoji_id=get_emoji_icon("grid_id"), style="primary")
        ],
        [
            InlineKeyboardButton(text="⬆️ Move Up", callback_data=f"adm_cat_up_{cid}", style="primary"),
            InlineKeyboardButton(text="⬇️ Move Down", callback_data=f"adm_cat_dn_{cid}", style="primary")
        ],
        [
            InlineKeyboardButton(text="Set Emoji ID", callback_data=f"adm_cat_emj_{cid}", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary"),
            InlineKeyboardButton(text="Delete Category", callback_data=f"adm_cat_del_{cid}", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")
        ],
        [
            InlineKeyboardButton(text="Back to Categories", callback_data="admin_manage_categories", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")
        ]
    ])
    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')
    except Exception:
        pass

@dp.callback_query(F.data.startswith("adm_cat_ren_"))
async def admin_rename_cat_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    cid = int(call.data.split("adm_cat_ren_")[1])
    cat = db_query("SELECT name FROM categories WHERE id=?", (cid,), fetchone=True)
    if not cat: return await call.answer("Category not found.", show_alert=True)
    await state.update_data(rename_cat_id=cid, old_cat_name=cat[0])
    await call.message.edit_text(
        f"✏️ <b>Rename Category</b>\n\nCurrent Name: <b>{cat[0]}</b>\n\nEnter the new name for this category:\n<i>(All products under this category will automatically update!)</i>",
        reply_markup=admin_back_kb(),
        parse_mode='HTML'
    )
    await state.set_state(AdminStates.edit_category_name)

@dp.message(AdminStates.edit_category_name)
async def admin_save_rename_cat(m: Message, state: FSMContext):
    data = await state.get_data()
    cid = data['rename_cat_id']
    old_name = data['old_cat_name']
    new_name = m.text.strip().upper()
    if not new_name:
        return await m.answer("❌ Name cannot be empty.", reply_markup=admin_kb())
    
    db_query("UPDATE categories SET name=? WHERE id=?", (new_name, cid))
    db_query("UPDATE products SET category=? WHERE category=?", (new_name, old_name))
    await m.answer(
        f"✅ <b>Category Renamed!</b>\n\nOld Name: <b>{old_name}</b>\nNew Name: <b>{new_name}</b>\nAll associated products have been updated.",
        reply_markup=admin_kb(),
        parse_mode='HTML'
    )
    await state.clear()

@dp.callback_query(F.data.startswith("adm_cat_pos_"))
async def admin_cat_pos_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    cid = int(call.data.split("adm_cat_pos_")[1])
    cat = db_query("SELECT name, sort_order FROM categories WHERE id=?", (cid,), fetchone=True)
    if not cat: return await call.answer("Category not found.", show_alert=True)
    await state.update_data(pos_cat_id=cid)
    await call.message.edit_text(
        f"🔢 <b>Set Position for {cat[0]}</b>\n\nCurrent Position: #{cat[1]}\n\nEnter new position number (e.g. <code>1</code> for top, <code>2</code>, <code>3</code>...):",
        reply_markup=admin_back_kb(),
        parse_mode='HTML'
    )
    await state.set_state(AdminStates.edit_category_pos)

@dp.message(AdminStates.edit_category_pos)
async def admin_save_cat_pos(m: Message, state: FSMContext):
    data = await state.get_data()
    cid = data['pos_cat_id']
    try:
        new_pos = int(m.text.strip())
    except ValueError:
        return await m.answer("❌ Please enter a valid number (e.g. 1, 2, 3).")
    cats = db_query("SELECT id FROM categories ORDER BY sort_order ASC, id ASC", fetchall=True)
    cat_ids = [c[0] for c in cats if c[0] != cid]
    target_pos = max(1, min(new_pos, len(cat_ids) + 1))
    cat_ids.insert(target_pos - 1, cid)
    for pos, c_id in enumerate(cat_ids, start=1):
        db_query("UPDATE categories SET sort_order=? WHERE id=?", (pos, c_id))
    cat = db_query("SELECT name FROM categories WHERE id=?", (cid,), fetchone=True)
    cname = cat[0] if cat else "Category"
    await m.answer(f"✅ Position for <b>{cname}</b> set to #{target_pos} successfully!", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data.startswith("adm_cat_up_"))
async def admin_cat_up(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    cid = int(call.data.split("adm_cat_up_")[1])
    cats = db_query("SELECT id FROM categories ORDER BY sort_order ASC, id ASC", fetchall=True)
    cat_ids = [c[0] for c in cats]
    if cid not in cat_ids:
        return await call.answer("Category not found", show_alert=True)
    idx = cat_ids.index(cid)
    if idx == 0:
        await call.answer("Already at top!", show_alert=False)
        return await admin_view_cat(call, cid=cid)
    cat_ids[idx], cat_ids[idx - 1] = cat_ids[idx - 1], cat_ids[idx]
    for pos, c_id in enumerate(cat_ids, start=1):
        db_query("UPDATE categories SET sort_order=? WHERE id=?", (pos, c_id))
    await call.answer("⬆️ Moved Up!")
    await admin_view_cat(call, cid=cid)

@dp.callback_query(F.data.startswith("adm_cat_dn_"))
async def admin_cat_dn(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    cid = int(call.data.split("adm_cat_dn_")[1])
    cats = db_query("SELECT id FROM categories ORDER BY sort_order ASC, id ASC", fetchall=True)
    cat_ids = [c[0] for c in cats]
    if cid not in cat_ids:
        return await call.answer("Category not found", show_alert=True)
    idx = cat_ids.index(cid)
    if idx >= len(cat_ids) - 1:
        await call.answer("Already at bottom!", show_alert=False)
        return await admin_view_cat(call, cid=cid)
    cat_ids[idx], cat_ids[idx + 1] = cat_ids[idx + 1], cat_ids[idx]
    for pos, c_id in enumerate(cat_ids, start=1):
        db_query("UPDATE categories SET sort_order=? WHERE id=?", (pos, c_id))
    await call.answer("⬇️ Moved Down!")
    await admin_view_cat(call, cid=cid)

@dp.callback_query(F.data.startswith("adm_cat_emj_"))
async def admin_cat_emoji_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    cid = int(call.data.split("adm_cat_emj_")[1])
    cat = db_query("SELECT name, emoji_id FROM categories WHERE id=?", (cid,), fetchone=True)
    if not cat: return await call.answer("Category not found.", show_alert=True)
    await state.update_data(emj_cat_id=cid, cat_name=cat[0])
    await call.message.edit_text(
        f"🎨 <b>Set Emoji for {cat[0]}</b>\nCurrent Emoji ID: <code>{cat[1] or 'None'}</code>\n\nEnter numeric Telegram custom emoji ID (or send <code>none</code> to reset):",
        reply_markup=admin_back_kb(),
        parse_mode='HTML'
    )
    await state.set_state(AdminStates.edit_category_emoji)

@dp.message(AdminStates.edit_category_emoji)
async def admin_save_cat_emoji(m: Message, state: FSMContext):
    data = await state.get_data()
    cid = data['emj_cat_id']
    emoji_char, custom_id = extract_emoji_or_id(m)
    val = (m.text or "").strip()
    if val.lower() in ('none', 'default', '', 'reset'):
        val = ""
    elif custom_id:
        val = custom_id
    elif not val.isdigit():
        return await m.answer("❌ Please send a valid Telegram custom emoji or numeric ID (e.g. <code>6161172706856282588</code>).")
    db_query("UPDATE categories SET emoji_id=? WHERE id=?", (val, cid))
    await m.answer("✅ Category emoji updated successfully!", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data.startswith("adm_cat_del_"))
async def admin_cat_delete(call: CallbackQuery):
    """Immediately delete a category and everything belonging to it.

    Admin explicitly requested a one-click destructive category delete with no
    confirmation prompt. Purchase/transaction history is intentionally kept.
    """
    if call.from_user.id != ADMIN_ID:
        return
    cid = int(call.data.split("adm_cat_del_", 1)[1])
    cat = db_query("SELECT name FROM categories WHERE id=?", (cid,), fetchone=True)
    if not cat:
        return await call.answer("Category not found.", show_alert=True)
    cname = cat[0]

    # Match the category logic used throughout the shop (supports legacy
    # category labels with a suffix). Remove keys first to avoid FK issues.
    prods = db_query("SELECT id FROM products WHERE category LIKE ?", (cname + '%',), fetchall=True) or []
    product_ids = [int(row[0]) for row in prods]
    for pid in product_ids:
        db_query("DELETE FROM product_keys WHERE product_id=?", (pid,))
    db_query("DELETE FROM products WHERE category LIKE ?", (cname + '%',))
    db_query("DELETE FROM categories WHERE id=?", (cid,))

    # Clean category-specific UI settings; this does not touch transaction history.
    try:
        db_query("DELETE FROM settings WHERE key LIKE ?", (f"cat_emoji_{cname}%",))
        db_query("DELETE FROM settings WHERE key LIKE ?", (f"cat_custom_id_{cname}%",))
    except Exception:
        pass

    await call.message.edit_text(
        f"☢️ <b>Category Deleted</b>\n\n"
        f"🏷 <b>{html.escape(str(cname))}</b>\n"
        f"📦 Products removed: <b>{len(product_ids)}</b>\n"
        f"🔑 Product keys removed with them.\n\n"
        f"🧾 Purchase/transaction history was kept safely.",
        reply_markup=admin_back_kb(),
        parse_mode='HTML'
    )


@dp.callback_query(F.data.startswith("adm_cat_delcf_keep_"))
async def admin_cat_delete_confirm_keep(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    cid = int(call.data.split("adm_cat_delcf_keep_")[1])
    cat = db_query("SELECT name FROM categories WHERE id=?", (cid,), fetchone=True)
    if not cat: return await call.answer("Category not found.", show_alert=True)
    cname = cat[0]
    prod_cnt = db_query("SELECT COUNT(*) FROM products WHERE category LIKE ?", (cname + '%',), fetchone=True)[0]
    db_query("DELETE FROM categories WHERE id=?", (cid,))
    msg = f"✅ Category <b>{cname}</b> deleted."
    if prod_cnt > 0:
        msg += f"\n<i>Note: {prod_cnt} existing products still keep their category label unless re-assigned.</i>"
    await call.message.edit_text(msg, reply_markup=admin_back_kb(), parse_mode='HTML')

@dp.callback_query(F.data.startswith("adm_cat_delcf_all_"))
async def admin_cat_delete_confirm_all(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    cid = int(call.data.split("adm_cat_delcf_all_")[1])
    cat = db_query("SELECT name FROM categories WHERE id=?", (cid,), fetchone=True)
    if not cat: return await call.answer("Category not found.", show_alert=True)
    cname = cat[0]
    prods = db_query("SELECT id FROM products WHERE category LIKE ?", (cname + '%',), fetchall=True)
    for p in (prods or []):
        db_query("DELETE FROM product_keys WHERE product_id=?", (p[0],))
    db_query("DELETE FROM products WHERE category LIKE ?", (cname + '%',))
    db_query("DELETE FROM categories WHERE id=?", (cid,))
    msg = f"☢️ Category <b>{cname}</b> and all its associated products/keys were permanently deleted."
    await call.message.edit_text(msg, reply_markup=admin_back_kb(), parse_mode='HTML')


# ==============================================================================
# PRODUCT MAIN TITLES & POSITIONS (ADMIN)
# ==============================================================================
@dp.callback_query(F.data == "admin_panel_positions_menu")
async def admin_panel_positions_menu(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    cats = db_query("SELECT id, name, emoji_id, sort_order FROM categories ORDER BY sort_order ASC, id ASC", fetchall=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    text = (
        "🎯 <b><u>PRODUCT MAIN TITLES & POSITIONS</u></b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Select a Category to view its Main Titles (Panels) and customize their display order / position:\n"
    )
    for cid, cat, emj, corder in cats:
        emj_icon = get_category_emoji(cat)
        panel_count = db_query("SELECT COUNT(DISTINCT panel_name) FROM products WHERE category=? AND panel_name != ''", (cat,), fetchone=True)[0]
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{cat} ({panel_count} Main Titles)", 
                callback_data=f"pnlpos_c_{cid}", 
                icon_custom_emoji_id=emj_icon or get_emoji_icon("info_icon"),
                style="primary"
            )
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("pnlpos_c_"))
async def admin_pnlpos_list_panels(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    cid = int(call.data.split("pnlpos_c_")[1])
    cat_row = db_query("SELECT id, name FROM categories WHERE id=?", (cid,), fetchone=True)
    if not cat_row: return await call.answer("Category not found", show_alert=True)
    cat = cat_row[1]
    
    rows = db_query("""
        SELECT MIN(id) AS sample_id, panel_name, COALESCE(MIN(panel_sort_order), 0) AS p_order, COUNT(*) AS pkg_count
        FROM products
        WHERE category=? AND panel_name != ''
        GROUP BY panel_name
        ORDER BY p_order ASC, panel_name ASC
    """, (cat,), fetchall=True)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    text = (
        f"🎯 <b><u>MAIN TITLES: {cat.upper()}</u></b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Click any title below to <b>Change its Position (Display Order)</b> or <b>Rename the Title</b>:\n\n"
    )
    if not rows:
        text += "<i>No products / panels found in this category yet.</i>"
    else:
        for r in rows:
            sample_id, pname, porder, pkg_count = r
            emj = get_panel_emoji(pname)
            kb.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"[#{porder}] {pname} ({pkg_count} pkgs)", 
                    callback_data=f"adm_pnl_v_{sample_id}",
                    icon_custom_emoji_id=emj or get_emoji_icon("product_store"),
                    style="primary"
                )
            ])
    
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="Back to Categories", callback_data="admin_panel_positions_menu", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("adm_pnl_v_"))
async def admin_pnlpos_view_panel(call: CallbackQuery, sample_id: Optional[int] = None):
    if call.from_user.id != ADMIN_ID: return
    if sample_id is None:
        sample_id = int(call.data.split("adm_pnl_v_")[1])
    prod = db_query("SELECT category, panel_name, COALESCE(panel_sort_order, 0) FROM products WHERE id=?", (sample_id,), fetchone=True)
    if not prod: return await call.answer("Panel not found.", show_alert=True)
    cat, pname, porder = prod
    pkg_count = db_query("SELECT COUNT(*) FROM products WHERE category=? AND panel_name=?", (cat, pname), fetchone=True)[0]
    cat_row = db_query("SELECT id FROM categories WHERE name=?", (cat,), fetchone=True)
    back_cb = f"pnlpos_c_{cat_row[0]}" if cat_row else "admin_panel_positions_menu"
    
    text = (
        f"📦 <b><u>MAIN TITLE DETAILS</u></b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>Main Title:</b> <b>{pname}</b>\n"
        f"<b>Category:</b> {cat}\n"
        f"<b>Current Position (Sort Order):</b> #{porder}\n"
        f"<b>Packages under this Title:</b> {pkg_count}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>Choose an action to reorganize this product title:</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Set Position #", callback_data=f"adm_pnl_pos_{sample_id}", icon_custom_emoji_id=get_emoji_icon("grid_id"), style="primary"),
            InlineKeyboardButton(text="Rename Main Title", callback_data=f"adm_pnl_ren_{sample_id}", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")
        ],
        [
            InlineKeyboardButton(text="⬆️ Move Up", callback_data=f"adm_pnl_up_{sample_id}", style="primary"),
            InlineKeyboardButton(text="⬇️ Move Down", callback_data=f"adm_pnl_dn_{sample_id}", style="primary")
        ],
        [
            InlineKeyboardButton(text="Organize Packages", callback_data=f"adm_pnl_pkgs_{sample_id}", icon_custom_emoji_id=get_emoji_icon("product_store"), style="primary"),
            InlineKeyboardButton(text="Delete Main Title", callback_data=f"adm_pnl_del_{sample_id}", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")
        ],
        [
            InlineKeyboardButton(text="Back", callback_data=back_cb, icon_custom_emoji_id=get_emoji_icon("back"), style="danger")
        ]
    ])
    try:
        await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')
    except Exception:
        pass

@dp.callback_query(F.data.startswith("adm_pnl_pos_"))
async def admin_pnlpos_set_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    sample_id = int(call.data.split("adm_pnl_pos_")[1])
    prod = db_query("SELECT category, panel_name, COALESCE(panel_sort_order, 0) FROM products WHERE id=?", (sample_id,), fetchone=True)
    if not prod: return await call.answer("Panel not found.", show_alert=True)
    cat, pname, porder = prod
    await state.update_data(pnl_sample_id=sample_id, pnl_cat=cat, pnl_name=pname)
    await call.message.edit_text(
        f"🔢 <b>Set Position for: {pname}</b>\n\n"
        f"Current Position: #{porder}\n\n"
        f"Enter the desired position number (e.g. <code>1</code> for 1st place, <code>2</code> for 2nd, etc.):",
        reply_markup=admin_back_kb(),
        parse_mode='HTML'
    )
    await state.set_state(AdminStates.edit_panel_pos)

@dp.message(AdminStates.edit_panel_pos)
async def admin_pnlpos_save_pos(m: Message, state: FSMContext):
    data = await state.get_data()
    cat = data['pnl_cat']
    pname = data['pnl_name']
    try:
        new_pos = int(m.text.strip())
    except ValueError:
        return await m.answer("❌ Please enter a valid number (e.g. 1, 2, 3).")
    
    rows = db_query("""
        SELECT panel_name
        FROM products
        WHERE category=? AND panel_name != ''
        GROUP BY panel_name
        ORDER BY COALESCE(MIN(panel_sort_order), 0) ASC, panel_name ASC
    """, (cat,), fetchall=True)
    panel_list = [r[0] for r in rows if r[0] != pname]
    target_pos = max(1, min(new_pos, len(panel_list) + 1))
    panel_list.insert(target_pos - 1, pname)
    for pos, name in enumerate(panel_list, start=1):
        db_query("UPDATE products SET panel_sort_order=? WHERE category=? AND panel_name=?", (pos, cat, name))
    
    await m.answer(f"✅ <b>Position Updated!</b>\n\nMain Title <b>{pname}</b> is now set to position <b>#{target_pos}</b> in <b>{cat}</b>.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data.startswith("adm_pnl_up_"))
async def admin_pnlpos_move_up(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    sample_id = int(call.data.split("adm_pnl_up_")[1])
    prod = db_query("SELECT category, panel_name FROM products WHERE id=?", (sample_id,), fetchone=True)
    if not prod: return await call.answer("Not found", show_alert=True)
    cat, pname = prod
    
    rows = db_query("""
        SELECT panel_name
        FROM products
        WHERE category=? AND panel_name != ''
        GROUP BY panel_name
        ORDER BY COALESCE(MIN(panel_sort_order), 0) ASC, panel_name ASC
    """, (cat,), fetchall=True)
    panel_list = [r[0] for r in rows]
    if pname not in panel_list:
        return await call.answer("Title not found", show_alert=True)
    
    idx = panel_list.index(pname)
    if idx == 0:
        await call.answer("Already at top!", show_alert=False)
        return await admin_pnlpos_view_panel(call, sample_id=sample_id)
    
    panel_list[idx], panel_list[idx - 1] = panel_list[idx - 1], panel_list[idx]
    for pos, name in enumerate(panel_list, start=1):
        db_query("UPDATE products SET panel_sort_order=? WHERE category=? AND panel_name=?", (pos, cat, name))
    
    await call.answer("⬆️ Moved Up!")
    await admin_pnlpos_view_panel(call, sample_id=sample_id)

@dp.callback_query(F.data.startswith("adm_pnl_dn_"))
async def admin_pnlpos_move_dn(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    sample_id = int(call.data.split("adm_pnl_dn_")[1])
    prod = db_query("SELECT category, panel_name FROM products WHERE id=?", (sample_id,), fetchone=True)
    if not prod: return await call.answer("Not found", show_alert=True)
    cat, pname = prod
    
    rows = db_query("""
        SELECT panel_name
        FROM products
        WHERE category=? AND panel_name != ''
        GROUP BY panel_name
        ORDER BY COALESCE(MIN(panel_sort_order), 0) ASC, panel_name ASC
    """, (cat,), fetchall=True)
    panel_list = [r[0] for r in rows]
    if pname not in panel_list:
        return await call.answer("Title not found", show_alert=True)
    
    idx = panel_list.index(pname)
    if idx >= len(panel_list) - 1:
        await call.answer("Already at bottom!", show_alert=False)
        return await admin_pnlpos_view_panel(call, sample_id=sample_id)
    
    panel_list[idx], panel_list[idx + 1] = panel_list[idx + 1], panel_list[idx]
    for pos, name in enumerate(panel_list, start=1):
        db_query("UPDATE products SET panel_sort_order=? WHERE category=? AND panel_name=?", (pos, cat, name))
    
    await call.answer("⬇️ Moved Down!")
    await admin_pnlpos_view_panel(call, sample_id=sample_id)

@dp.callback_query(F.data.startswith("adm_pnl_ren_"))
async def admin_pnlpos_rename_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    sample_id = int(call.data.split("adm_pnl_ren_")[1])
    prod = db_query("SELECT category, panel_name FROM products WHERE id=?", (sample_id,), fetchone=True)
    if not prod: return await call.answer("Not found", show_alert=True)
    cat, pname = prod
    await state.update_data(pnl_sample_id=sample_id, pnl_cat=cat, pnl_old_name=pname)
    await call.message.edit_text(
        f"✏️ <b>Rename Main Title</b>\n\n"
        f"Current Title: <b>{pname}</b>\n\n"
        f"Enter the new Main Title / Panel Name:\n<i>(This will update all duration packages under this title!)</i>",
        reply_markup=admin_back_kb(),
        parse_mode='HTML'
    )
    await state.set_state(AdminStates.edit_panel_title)

@dp.message(AdminStates.edit_panel_title)
async def admin_pnlpos_save_rename(m: Message, state: FSMContext):
    data = await state.get_data()
    cat = data['pnl_cat']
    old_name = data['pnl_old_name']
    new_name = m.text.strip()
    if not new_name:
        return await m.answer("❌ Title cannot be empty.", reply_markup=admin_kb())
    
    db_query("UPDATE products SET panel_name=? WHERE category=? AND panel_name=?", (new_name, cat, old_name))
    await m.answer(
        f"✅ <b>Main Title Renamed!</b>\n\nOld: <b>{old_name}</b>\nNew: <b>{new_name}</b>\nAll packages have been updated.",
        reply_markup=admin_kb(),
        parse_mode='HTML'
    )
    await state.clear()

@dp.callback_query(F.data.startswith("adm_pnl_pkgs_"))
async def admin_pnlpos_packages(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    sample_id = int(call.data.split("adm_pnl_pkgs_")[1])
    prod = db_query("SELECT category, panel_name FROM products WHERE id=?", (sample_id,), fetchone=True)
    if not prod: return await call.answer("Not found", show_alert=True)
    cat, pname = prod
    
    pkgs = db_query("""
        SELECT id, name, price_inr, COALESCE(sort_order, 0) 
        FROM products 
        WHERE category=? AND panel_name=?
        ORDER BY COALESCE(sort_order, 0) ASC, id ASC
    """, (cat, pname), fetchall=True)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    text = (
        f"📦 <b><u>PACKAGES: {pname}</u></b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Click any package below to set its position/order within this panel:\n\n"
    )
    for p in pkgs:
        pid, pname_pkg, price, sorder = p
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=f"[#{sorder}] {pname_pkg} - ₹{price:.0f}", callback_data=f"adm_pkg_pos_{pid}", style="primary")
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="Back to Title", callback_data=f"adm_pnl_v_{sample_id}", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("adm_pkg_pos_"))
async def admin_pkg_pos_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    pid = int(call.data.split("pkg_pos_")[1])
    prod = db_query("SELECT name, panel_name, COALESCE(sort_order, 0) FROM products WHERE id=?", (pid,), fetchone=True)
    if not prod: return await call.answer("Product not found", show_alert=True)
    name, panel_name, sorder = prod
    await state.update_data(pkg_pid=pid)
    await call.message.edit_text(
        f"🔢 <b>Set Position for: {panel_name} - {name}</b>\n\nCurrent Position: #{sorder}\n\nEnter new position number (e.g. 1 for first package, 2 for second, etc.):",
        reply_markup=admin_back_kb(),
        parse_mode='HTML'
    )
    await state.set_state(AdminStates.edit_prod_pos)

@dp.message(AdminStates.edit_prod_pos)
async def admin_pkg_pos_save(m: Message, state: FSMContext):
    data = await state.get_data()
    pid = data['pkg_pid']
    try:
        new_pos = int(m.text.strip())
    except ValueError:
        return await m.answer("❌ Please enter a valid number (e.g. 1, 2, 3).")
    db_query("UPDATE products SET sort_order=? WHERE id=?", (new_pos, pid))
    await m.answer(f"✅ Package position updated to #{new_pos} successfully!", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data.startswith("adm_pnl_del_"))
async def admin_pnlpos_delete_prompt(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    sample_id = int(call.data.split("adm_pnl_del_")[1])
    prod = db_query("SELECT category, panel_name FROM products WHERE id=?", (sample_id,), fetchone=True)
    if not prod: return await call.answer("Title not found.", show_alert=True)
    cat, pname = prod
    pkg_count = db_query("SELECT COUNT(*) FROM products WHERE category=? AND panel_name=?", (cat, pname), fetchone=True)[0]
    text = (
        f"⚠️ <b><u>DELETE MAIN TITLE: {pname}</u></b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📁 <b>Category:</b> {cat}\n"
        f"📦 <b>Packages under this title:</b> {pkg_count}\n\n"
        f"Are you sure you want to permanently delete this Main Title and all its {pkg_count} packages and vault keys?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="☢️ Yes, Delete Title & Packages", callback_data=f"adm_pnl_delcf_{sample_id}", style="danger")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data=f"adm_pnl_v_{sample_id}", style="primary")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("adm_pnl_delcf_"))
async def admin_pnlpos_delete_confirm(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    sample_id = int(call.data.split("adm_pnl_delcf_")[1])
    prod = db_query("SELECT category, panel_name FROM products WHERE id=?", (sample_id,), fetchone=True)
    if not prod: return await call.answer("Title not found or already deleted.", show_alert=True)
    cat, pname = prod
    p_ids = db_query("SELECT id FROM products WHERE category=? AND panel_name=?", (cat, pname), fetchall=True)
    for p in (p_ids or []):
        db_query("DELETE FROM product_keys WHERE product_id=?", (p[0],))
    db_query("DELETE FROM products WHERE category=? AND panel_name=?", (cat, pname))
    cat_row = db_query("SELECT id FROM categories WHERE name=?", (cat,), fetchone=True)
    back_cb = f"pnlpos_c_{cat_row[0]}" if cat_row else "admin_panel_positions_menu"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Back to Titles", callback_data=back_cb, icon_custom_emoji_id=get_emoji_icon("back"), style="primary")]
    ])
    await call.message.edit_text(
        f"✅ <b>Main Title Deleted!</b>\n\nTitle <b>{pname}</b> and all associated packages have been permanently wiped from the database.",
        reply_markup=kb,
        parse_mode='HTML'
    )


def heroic_slot_config(slot: int):
    name = get_setting(f"heroic_slot_{slot}_name", f"{['One','Two','Three','Four'][slot-1]} ID")
    try: price = float(get_setting(f"heroic_slot_{slot}_price", "0") or 0)
    except ValueError: price = 0.0
    return name, price

def heroic_slot_emoji(slot: int) -> tuple:
    raw = get_setting(f"heroic_slot_{slot}_emoji", "🆔").strip()
    if raw.isdigit():
        return "", raw
    return raw or "🆔", None

@dp.callback_query(F.data.startswith("heroic_buy_"))
async def heroic_buy(call: CallbackQuery):
    try: slot = int(call.data.rsplit("_", 1)[1])
    except (ValueError, IndexError): return await call.answer("Invalid slot", show_alert=True)
    if slot not in range(1,5): return await call.answer("Invalid slot", show_alert=True)
    name, price = heroic_slot_config(slot)
    if price <= 0:
        return await call.answer("This ID slot is not configured yet. Contact owner.", show_alert=True)
    user = db_query("SELECT balance, first_name, username FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    if not user: return await call.answer("User account not found", show_alert=True)
    balance = float(user[0] or 0)
    if balance < price:
        needed = price - balance
        low_bal_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="Add Balance Now",
                callback_data="menu_add_balance",
                icon_custom_emoji_id=get_emoji_icon("add_balance"),
                style="success"
            )],
            [InlineKeyboardButton(
                text="Back",
                callback_data="menu_heroic_ids",
                icon_custom_emoji_id=get_emoji_icon("back"),
                style="danger"
            )]
        ])
        return await call.message.edit_text(
            f"❌ <b>Insufficient Balance!</b>\n\n"
            f"🆔 Package: <b>{name}</b>\n"
            f"💰 Your Balance: <b>{fmt_curr(balance)}</b>\n"
            f"🛒 Required: <b>{fmt_curr(price)}</b>\n"
            f"➕ You need: <b>{fmt_curr(needed)} more</b>\n\n"
            "👇 Add balance to continue your purchase:",
            reply_markup=low_bal_kb,
            parse_mode="HTML"
        )
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Atomic enough for SQLite single-process bot: deduct and create the pending row in one transaction.
    conn = sqlite3.connect(DB_PATH, timeout=30.0); cur = conn.cursor()
    try:
        cur.execute("UPDATE users SET balance=balance-?, spent=spent+?, orders_count=orders_count+1 WHERE user_id=? AND balance>=?", (price, price, call.from_user.id, price))
        if cur.rowcount != 1:
            conn.rollback(); return await call.answer("Balance changed; please try again.", show_alert=True)
        cur.execute("INSERT INTO heroic_orders (user_id, slot, product_name, price, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)", (call.from_user.id, slot, name, price, now))
        order_id = cur.lastrowid
        conn.commit()
    except Exception:
        conn.rollback(); logger.exception("Heroic order creation failed")
        return await call.answer("Could not create order. Please try again.", show_alert=True)
    finally: conn.close()
    uname = f"@{call.from_user.username}" if call.from_user.username else "No username"
    owner_text = (f"🆕 <b>HEROIC ID ORDER</b>\n━━━━━━━━━━━━━━\n"
                  f"Order: <code>HID-{order_id}</code>\nPackage: <b>{name}</b>\nAmount: <b>{fmt_curr(price)}</b>\n"
                  f"User: <b>{call.from_user.full_name}</b>\nUsername: {uname}\nUser ID: <code>{call.from_user.id}</code>\n"
                  f"Status: <b>PENDING</b>\n\nDeliver the ID to the user or refund the order.")
    owner_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Deliver ID", callback_data=f"heroic_deliver_{order_id}", style="success")],
        [InlineKeyboardButton(text="↩️ Refund", callback_data=f"heroic_refund_{order_id}", style="danger")]
    ])
    await notify_admins(owner_text, reply_markup=owner_kb, parse_mode="HTML")
    await call.message.edit_text(
        f"⏳ <b>PAYMENT RECEIVED</b>\n\nOrder: <code>HID-{order_id}</code>\nPackage: <b>{name}</b>\nPaid: <b>{fmt_curr(price)}</b>\n\n"
        "Your order is <b>Pending</b>. Owner has been notified in DM and will deliver your ID here.",
        reply_markup=back_kb("menu_heroic_ids"), parse_mode="HTML")

@dp.callback_query(F.data.startswith("heroic_deliver_"))
async def heroic_deliver_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    try: order_id = int(call.data.rsplit("_",1)[1])
    except (ValueError, IndexError): return await call.answer("Invalid order", show_alert=True)
    order = db_query("SELECT user_id, product_name, price, status FROM heroic_orders WHERE id=?", (order_id,), fetchone=True)
    if not order or order[3] != 'pending': return await call.answer("Order is not pending.", show_alert=True)
    await state.update_data(heroic_order_id=order_id)
    await call.message.edit_text(f"📤 <b>Deliver HID-{order_id}</b>\n\nSend the Heroic ID exactly as it should be delivered to the customer.", reply_markup=admin_back_kb(), parse_mode="HTML")
    await state.set_state(AdminStates.wait_for_heroic_delivery)

@dp.message(AdminStates.wait_for_heroic_delivery)
async def heroic_deliver_id(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    data = await state.get_data(); order_id = int(data.get('heroic_order_id',0)); delivered = (m.text or '').strip()
    if not order_id or not delivered: return await m.answer("❌ ID cannot be empty.")
    order = db_query("SELECT user_id, product_name, price, status FROM heroic_orders WHERE id=?", (order_id,), fetchone=True)
    if not order or order[3] != 'pending':
        await state.clear(); return await m.answer("❌ Order is no longer pending.", reply_markup=admin_kb())
    updated = db_query("UPDATE heroic_orders SET status='delivered', delivered_id=?, delivered_at=? WHERE id=? AND status='pending'", (delivered, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), order_id), commit=True)
    try:
        await bot.send_message(order[0], f"✅ <b>HEROIC ID DELIVERED</b>\n\nOrder: <code>HID-{order_id}</code>\nPackage: <b>{order[1]}</b>\nYour ID:\n<code>{delivered}</code>", parse_mode="HTML")
    except Exception: logger.exception("Failed to deliver Heroic ID to user %s", order[0])
    await state.clear(); await m.answer(f"✅ HID-{order_id} marked delivered and sent to user.", reply_markup=admin_kb(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("heroic_refund_"))
async def heroic_refund(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    try: order_id = int(call.data.rsplit("_",1)[1])
    except (ValueError, IndexError): return await call.answer("Invalid order", show_alert=True)
    conn=sqlite3.connect(DB_PATH, timeout=30.0); cur=conn.cursor()
    try:
        cur.execute("SELECT user_id, product_name, price, status FROM heroic_orders WHERE id=?", (order_id,)); order=cur.fetchone()
        if not order or order[3] != 'pending': conn.rollback(); return await call.answer("Order is not pending.", show_alert=True)
        cur.execute("UPDATE heroic_orders SET status='refunded', delivered_at=? WHERE id=? AND status='pending'", (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), order_id))
        if cur.rowcount != 1: conn.rollback(); return await call.answer("Order was already handled.", show_alert=True)
        cur.execute("UPDATE users SET balance=balance+?, orders_count=CASE WHEN orders_count>0 THEN orders_count-1 ELSE 0 END, spent=CASE WHEN spent>=? THEN spent-? ELSE 0 END WHERE user_id=?", (order[2], order[2], order[2], order[0]))
        conn.commit()
    except Exception:
        conn.rollback(); logger.exception("Heroic refund failed"); return await call.answer("Refund failed", show_alert=True)
    finally: conn.close()
    try: await bot.send_message(order[0], f"↩️ <b>HEROIC ID ORDER REFUNDED</b>\n\nOrder: <code>HID-{order_id}</code>\nAmount refunded: <b>{fmt_curr(order[2])}</b>", parse_mode="HTML")
    except Exception: logger.exception("Failed refund notification")
    await call.message.edit_text(f"↩️ HID-{order_id} refunded successfully.", reply_markup=admin_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_heroic_slots")
async def admin_heroic_slots(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    kb=InlineKeyboardMarkup(inline_keyboard=[])
    lines=[]
    for slot in range(1,5):
        name,price=heroic_slot_config(slot)
        emo_text, emo_id = heroic_slot_emoji(slot)
        lines.append(f"{slot}. {emo_text} <b>{name}</b> — {fmt_curr(price)}")
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=f"Edit Slot {slot}", callback_data=f"admin_heroic_edit_{slot}", style="primary"),
            InlineKeyboardButton(text="🎨 Emoji", callback_data=f"admin_heroic_emoji_{slot}", icon_custom_emoji_id=emo_id, style="primary")
        ])
    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel_back", style="danger")])
    await call.message.edit_text("🆔 <b>HEROIC ID SLOTS</b>\n\n"+"\n".join(lines)+"\n\nEach slot has an editable name and price.", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("admin_heroic_edit_"))
async def admin_heroic_edit(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    slot=int(call.data.rsplit('_',1)[1]);
    if slot not in range(1,5): return
    await state.update_data(heroic_slot=slot)
    await call.message.edit_text(f"✏️ Slot {slot}: enter the new ID name (e.g. Heroic ID / Diamond ID / Gold ID):", reply_markup=admin_back_kb(), parse_mode="HTML")
    await state.set_state(AdminStates.wait_for_heroic_slot_name)

@dp.callback_query(F.data.startswith("admin_heroic_emoji_"))
async def admin_heroic_emoji_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    try:
        slot = int(call.data.rsplit("_", 1)[1])
    except ValueError:
        return
    if slot not in range(1,5):
        return
    current = get_setting(f"heroic_slot_{slot}_emoji", "🆔")
    await state.update_data(heroic_slot=slot)
    await call.message.edit_text(
        f"🎨 <b>Heroic Slot {slot} Emoji</b>\n\nCurrent: {current}\n\n"
        "Send a normal emoji (🔥/💎/👑) or Telegram custom emoji.\n"
        "Send <code>reset</code> to use 🆔.",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    await state.set_state(AdminStates.wait_for_heroic_slot_emoji)

@dp.message(AdminStates.wait_for_heroic_slot_emoji)
async def save_heroic_slot_emoji(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    slot = int(data.get("heroic_slot", 0))
    raw = (m.text or "").strip()
    if raw.lower() in ("", "reset", "default"):
        set_setting(f"heroic_slot_{slot}_emoji", "🆔")
        await state.clear()
        return await m.answer(f"✅ Heroic Slot {slot} emoji reset.", reply_markup=admin_kb(), parse_mode="HTML")
    emoji_char, custom_id = extract_emoji_or_id(m)
    if custom_id:
        set_setting(f"heroic_slot_{slot}_emoji", custom_id)
        msg = f'<tg-emoji emoji-id="{custom_id}">🆔</tg-emoji>'
    else:
        set_setting(f"heroic_slot_{slot}_emoji", emoji_char or raw)
        msg = emoji_char or raw
    await state.clear()
    await m.answer(f"✅ Heroic Slot {slot} emoji updated to {msg}", reply_markup=admin_kb(), parse_mode="HTML")

@dp.message(AdminStates.wait_for_heroic_slot_name)
async def save_heroic_slot_name(m: Message, state: FSMContext):
    name=(m.text or '').strip()
    if not name or len(name)>40: return await m.answer("❌ Name must be 1-40 characters.")
    data=await state.get_data(); slot=int(data['heroic_slot']); set_setting(f"heroic_slot_{slot}_name", name)
    await m.answer(f"💰 Now enter price for <b>{name}</b> in INR (e.g. 299):", parse_mode="HTML")
    await state.set_state(AdminStates.wait_for_heroic_slot_price)

@dp.message(AdminStates.wait_for_heroic_slot_price)
async def save_heroic_slot_price(m: Message, state: FSMContext):
    try: price=float((m.text or '').strip())
    except ValueError: return await m.answer("❌ Price must be numeric.")
    if price < 0 or price > 1000000: return await m.answer("❌ Enter a price between 0 and 1000000.")
    data=await state.get_data(); slot=int(data['heroic_slot']); set_setting(f"heroic_slot_{slot}_price", f"{price:.2f}")
    await state.clear(); await m.answer(f"✅ Slot {slot} saved: {get_setting(f'heroic_slot_{slot}_name')} — {fmt_curr(price)}", reply_markup=admin_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_full_system_check")
async def admin_full_system_check(call: CallbackQuery):
    """Safe diagnostics: checks DB/schema and API configuration without creating purchases."""
    if not is_admin(call.from_user.id):
        return
    await call.answer("🧪 Running safe system checks...", show_alert=False)
    checks = []
    try:
        required = {
            "products": ["id","category","panel_name","name","price_inr","reseller_price","stock","is_active","external_enabled","external_product_id","external_duration","api_provider","is_maintenance"],
            "transactions": ["order_id","user_id","amount_inr","status","purpose","product_id","quantity","gateway"],
            "users": ["user_id","balance","is_reseller","is_vip"],
        }
        conn = sqlite3.connect(DB_PATH, timeout=10)
        cur = conn.cursor()
        for table, cols in required.items():
            existing = {r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}
            missing = [c for c in cols if c not in existing]
            checks.append(("🟢" if not missing else "🔴", f"DB {table}: OK" if not missing else f"DB {table}: missing {', '.join(missing)}"))
        conn.close()
    except Exception as exc:
        checks.append(("🔴", f"DB check failed: {exc}"))

    bunty_url = get_setting("external_api_url", "").strip()
    bunty_key = get_setting("external_api_key", "").strip()
    checks.append(("🟢" if bunty_url and bunty_key else "🟠", "Bunty config: configured" if bunty_url and bunty_key else "Bunty config: URL/API key incomplete"))

    kp_url = get_setting("keypanel_api_url", os.getenv("KEYPANEL_API_URL", "")).strip()
    kp_key = get_setting("keypanel_master_key", os.getenv("KEYPANEL_MASTER_KEY", "")).strip()
    checks.append(("🟢" if kp_url and kp_key else "🟠", "KeyPanel config: configured" if kp_url and kp_key else "KeyPanel config: URL/master key incomplete"))

    zap = get_setting("zapupi_api", "").strip()
    checks.append(("🟢" if zap else "🟠", "ZapUPI: API key configured" if zap else "ZapUPI: API key not configured"))

    fam = _famgateway_api_key()
    fam_url = get_setting("famgateway_url", "https://famgateway.in").strip()
    checks.append(("🟢" if fam and fam_url else "🟠", "FamGateway: configured" if fam and fam_url else "FamGateway: API key/URL incomplete"))

    maint_total = db_query("SELECT COUNT(*) FROM products WHERE COALESCE(is_maintenance,0)=1", fetchone=True)
    checks.append(("🟢", f"Maintenance DB: {int(maint_total[0] or 0) if maint_total else 0} package(s) currently blocked"))

    text = "🧪 <b>FULL SYSTEM / API CHECK</b>\n━━━━━━━━━━━━━━━━━━\n" + "\n".join(f"{icon} {html.escape(msg)}" for icon, msg in checks)
    text += "\n━━━━━━━━━━━━━━━━━━\nℹ️ No purchase/order was created by this check.\nℹ️ Live gateway APIs that require a real order are not called here."
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Bunty Live Balance", callback_data="admin_check_bunty_balance", style="success"), InlineKeyboardButton(text="🩺 Bunty Health", callback_data="admin_bunty_health", style="primary")],
        [InlineKeyboardButton(text="🧪 KeyPanel Validate", callback_data="admin_test_keypanel", style="primary")],
        [InlineKeyboardButton(text="🔄 Re-run Check", callback_data="admin_full_system_check", style="success")],
        [InlineKeyboardButton(text="🔙 Back to Admin", callback_data="admin_panel_back", style="danger")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "admin_api_control")
async def admin_api_control(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    rows = db_query("SELECT api_provider, COUNT(*) FROM products WHERE external_enabled=1 GROUP BY api_provider", fetchall=True) or []
    counts = {str(r[0] or 'manual').lower(): int(r[1]) for r in rows}
    bunty_key = get_setting("external_api_key", "")
    bunty_master = get_setting("external_master_key", "")
    kp_master = get_setting("keypanel_master_key", "")
    mask = lambda x: (x[:4] + "••••" + x[-4:]) if len(x) > 8 else ("Configured" if x else "Not set")
    products = db_query("SELECT name, external_product_id, api_provider FROM products WHERE external_enabled=1 ORDER BY api_provider, name", fetchall=True) or []
    lines = ["🔧 <b>API CONTROL / BULK UPDATE</b>", "━━━━━━━━━━━━━━━━━━",
             f"⚡ <b>Bunty/AdminPanels:</b> {counts.get('bunty',0)} products",
             f"🛒 <b>Key Panel:</b> {counts.get('keypanel',0)} products",
             f"⚡ Bunty API Key: <code>{mask(bunty_key)}</code>",
             f"⚡ Bunty Master: <code>{mask(bunty_master)}</code>",
             f"🛒 Key Panel Master: <code>{mask(kp_master)}</code>", "", "<b>Products using API:</b>"]
    for name, pid, provider in products[:30]:
        lines.append(f"• {html.escape(str(name or 'Unnamed'))} — <b>{str(provider).upper()}</b> — PID <code>{html.escape(str(pid or '-'))}</code>")
    if len(products) > 30: lines.append(f"… and {len(products)-30} more")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 ⚡ Check Bunty Balance", callback_data="admin_check_bunty_balance", style="success")],
        [InlineKeyboardButton(text="🩺 ⚡ Bunty API Health Check", callback_data="admin_bunty_health", style="primary")],
        [InlineKeyboardButton(text="📋 All API Products / PIDs", callback_data="admin_api_products_1", style="primary")],
        [InlineKeyboardButton(text="⚡ Set Bunty API Key (ALL Bunty Products)", callback_data="admin_bulk_bunty_key", style="success")],
        [InlineKeyboardButton(text="⚡ Set Bunty Master (ALL Bunty Products)", callback_data="admin_bulk_bunty_master", style="success")],
        [InlineKeyboardButton(text="🛒 Set Key Panel Master (ALL KeyPanel Products)", callback_data="admin_bulk_keypanel_master", style="success")],
        [InlineKeyboardButton(text="🌐 Set Update/APK Link for ALL Products", callback_data="admin_bulk_update_link", style="primary")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode='HTML')

async def _bunty_balance_request() -> dict:
    """Read Bunty/AdminPanels wallet balance without placing an order."""
    url = get_setting("external_api_url", "https://adminpanels.shop/api/reseller_v1.php").strip()
    api_key = get_setting("external_api_key", "").strip()
    master_key = get_setting("external_master_key", "").strip()
    if not url:
        return {"status": "error", "message": "Bunty API URL is not configured."}
    if not api_key:
        return {"status": "error", "message": "Bunty API Key is not configured."}

    payload = {"api_key": api_key, "action": "balance"}
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json, text/plain, */*"}
    if master_key:
        headers["x-master-key"] = master_key
    timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_connect=5, sock_read=10)
    started = time.perf_counter()
    try:
        connector = aiohttp.TCPConnector(family=socket.AF_INET, force_close=True, ssl=True)
        try:
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                async with session.post(url, data=payload, headers=headers, allow_redirects=True) as resp:
                    raw = await resp.text()
                    elapsed_ms = round((time.perf_counter() - started) * 1000)
                    result = None
                    try:
                        result = json.loads(raw) if raw.strip() else None
                    except json.JSONDecodeError:
                        result = None

                    if resp.status < 200 or resp.status >= 300:
                        return {"status":"error", "http":resp.status, "latency":elapsed_ms, "raw":raw[:1200], "message":f"Bunty HTTP {resp.status}"}
                    if result is None:
                        return {"status":"error", "http":resp.status, "latency":elapsed_ms, "raw":raw[:1200], "message":"Bunty returned non-JSON response."}

                    data = result.get("data") if isinstance(result, dict) else None
                    if not isinstance(data, dict):
                        data = result if isinstance(result, dict) else {}
                    balance = data.get("balance", result.get("balance") if isinstance(result, dict) else None)
                    currency = data.get("currency", result.get("currency", "INR") if isinstance(result, dict) else "INR")
                    ok = balance is not None and not (isinstance(result, dict) and result.get("error"))
                    if isinstance(result, dict) and str(result.get("status", "")).lower() in ("error", "failed", "failure"):
                        ok = False
                    msg = result.get("message") or result.get("msg") or result.get("error") if isinstance(result, dict) else ""
                    return {
                        "status":"success" if ok else "error",
                        "http":resp.status, "latency":elapsed_ms,
                        "balance":balance, "currency":currency,
                        "message":str(msg or ("Balance received" if ok else "Bunty did not return a balance field.")),
                        "json":result, "raw":raw[:1200]
                    }
        finally:
            if not connector.closed:
                await connector.close()
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        logger.warning("Bunty balance check failed: %s", exc)
        return {"status":"error", "latency":elapsed_ms, "message":f"Connection failed: {type(exc).__name__}: {exc}"}
    except Exception as exc:
        logger.exception("Unexpected Bunty balance check error")
        return {"status":"error", "message":f"Unexpected error: {exc}"}


@dp.callback_query(F.data == "admin_check_bunty_balance")
async def admin_check_bunty_balance(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    await call.answer("⏳ Checking Bunty balance...", show_alert=False)
    result = await _bunty_balance_request()
    if result.get("status") == "success":
        balance = result.get("balance")
        currency = html.escape(str(result.get("currency") or "INR"))
        threshold = get_setting("bunty_balance_alert_threshold", "0")
        try:
            low = float(threshold or 0) > 0 and float(balance) <= float(threshold)
        except (TypeError, ValueError):
            low = False
        low_line = "\n⚠️ <b>LOW BALANCE ALERT:</b> threshold reached." if low else ""
        text = (f"💰 <b>BUNTY / ADMINPANELS BALANCE</b>\n\n"
                f"💵 <b>Balance:</b> <code>{html.escape(str(balance))}</code> {currency}\n"
                f"📡 <b>HTTP:</b> {result.get('http','-')}\n"
                f"⚡ <b>Response:</b> {result.get('latency','-')} ms\n"
                f"🎯 <b>Action:</b> balance\n"
                f"🔐 <b>Key:</b> configured\n"
                f"{low_line}")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Check Again", callback_data="admin_check_bunty_balance", style="success")],
            [InlineKeyboardButton(text="🩺 Full Health Check", callback_data="admin_bunty_health", style="primary")],
            [InlineKeyboardButton(text="🔙 API Control", callback_data="admin_api_control", style="danger")]
        ])
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        preview = html.escape(str(result.get("raw") or result.get("message") or "Unknown error"))[:1800]
        text = (f"❌ <b>BUNTY BALANCE CHECK FAILED</b>\n\n"
                f"📡 HTTP: <code>{result.get('http','-')}</code>\n"
                f"⚡ Response: <code>{result.get('latency','-')} ms</code>\n"
                f"💬 {html.escape(str(result.get('message','Unknown error')))}\n\n"
                f"<b>Response Preview:</b>\n<code>{preview}</code>")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Retry", callback_data="admin_check_bunty_balance", style="success")],
            [InlineKeyboardButton(text="⚙️ Bunty API Setup", callback_data="admin_setup_external_api", style="primary")],
            [InlineKeyboardButton(text="🔙 API Control", callback_data="admin_api_control", style="danger")]
        ])
        await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "admin_bunty_health")
async def admin_bunty_health(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    await call.answer("🩺 Running Bunty API health check...", show_alert=False)
    result = await _bunty_balance_request()
    if result.get("status") == "success":
        text = ("🩺 <b>BUNTY API HEALTH</b>\n━━━━━━━━━━━━━━━━━━\n"
                f"🟢 <b>Connection:</b> OK\n"
                f"🟢 <b>HTTP:</b> {result.get('http','-')}\n"
                f"⚡ <b>Latency:</b> {result.get('latency','-')} ms\n"
                f"💰 <b>Balance:</b> <code>{html.escape(str(result.get('balance')))}</code> {html.escape(str(result.get('currency') or 'INR'))}\n"
                "🧪 <b>Test:</b> balance action only (NO purchase made)\n━━━━━━━━━━━━━━━━━━")
    else:
        text = ("🩺 <b>BUNTY API HEALTH</b>\n━━━━━━━━━━━━━━━━━━\n"
                "🔴 <b>Status:</b> FAILED\n"
                f"📡 <b>HTTP:</b> {result.get('http','-')}\n"
                f"⚡ <b>Latency:</b> {result.get('latency','-')} ms\n"
                f"💬 <b>Error:</b> {html.escape(str(result.get('message','Unknown error')))}\n━━━━━━━━━━━━━━━━━━")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Check Balance", callback_data="admin_check_bunty_balance", style="success")],
        [InlineKeyboardButton(text="⚙️ Bunty API Setup", callback_data="admin_setup_external_api", style="primary")],
        [InlineKeyboardButton(text="🔙 API Control", callback_data="admin_api_control", style="danger")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


def _api_products_keyboard(page: int, total: int):
    per_page = 10
    pages = max(1, (total + per_page - 1) // per_page)
    row = []
    if page > 1:
        row.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"admin_api_products_{page-1}"))
    if page < pages:
        row.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"admin_api_products_{page+1}"))
    rows = [row] if row else []
    rows.append([InlineKeyboardButton(text="🔙 API Control", callback_data="admin_api_control", style="danger")])
    return rows


@dp.callback_query(F.data.regexp(r"^admin_api_products_\d+$"))
async def admin_api_products(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    page = max(1, int(call.data.rsplit("_",1)[1]))
    per_page = 10
    total_row = db_query("SELECT COUNT(*) FROM products WHERE external_enabled=1", fetchone=True)
    total = int(total_row[0] or 0) if total_row else 0
    offset = (page - 1) * per_page
    rows = db_query("SELECT name, panel_name, external_product_id, api_provider, external_duration, is_active FROM products WHERE external_enabled=1 ORDER BY api_provider, panel_name, name LIMIT ? OFFSET ?", (per_page, offset), fetchall=True) or []
    pages = max(1, (total + per_page - 1) // per_page)
    lines = [f"📋 <b>API PRODUCT MAP</b> — Page {page}/{pages}", "━━━━━━━━━━━━━━━━━━"]
    if not rows:
        lines.append("No API products found.")
    for idx, (name, panel, pid, provider, duration, active) in enumerate(rows, offset+1):
        status = "🟢" if int(active or 0) else "🔴"
        lines.append(f"{idx}. {status} <b>{html.escape(str(name or 'Unnamed'))}</b>\n   🏷 Panel: {html.escape(str(panel or '-'))}\n   🌐 API: <b>{html.escape(str(provider or 'manual').upper())}</b> | PID: <code>{html.escape(str(pid or '-'))}</code>\n   ⏱ Duration: <code>{html.escape(str(duration or '-'))}</code>")
    kb = InlineKeyboardMarkup(inline_keyboard=_api_products_keyboard(page, total))
    await call.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "admin_bulk_update_link")
async def admin_bulk_update_link(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text(
        "🌐 <b>BULK UPDATE/APK LINK</b>\n\nEnter the new update/download channel or APK link.\nThis will replace the <code>apk_link</code> for <b>ALL active products</b> at once.",
        reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.bulk_update_link)

@dp.message(AdminStates.bulk_update_link)
async def save_bulk_update_link(m: Message, state: FSMContext):
    value=(m.text or '').strip()
    if value.lower() in ('none','off','-'):
        value=''
    elif value and not value.startswith(('http://','https://','tg://')):
        return await m.answer("❌ Send a valid http(s):// or tg:// link, or type <code>none</code> to clear it.", parse_mode='HTML')
    db_query("UPDATE products SET apk_link=? WHERE is_active=1", (value,))
    count = db_query("SELECT COUNT(*) FROM products WHERE is_active=1", fetchone=True)
    await state.clear()
    await m.answer(f"✅ Update/APK link synchronized across <b>{int(count[0]) if count else 0}</b> active products.", reply_markup=admin_kb(), parse_mode='HTML')

@dp.callback_query(F.data.in_(["admin_bulk_bunty_key", "admin_bulk_bunty_master", "admin_bulk_keypanel_master"]))
async def admin_bulk_api_prompt(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    action = call.data.replace("admin_bulk_", "")
    await state.update_data(bulk_api_action=action)
    labels = {
        "bunty_key": "Bunty/AdminPanels API Key",
        "bunty_master": "Bunty/AdminPanels Master Key",
        "keypanel_master": "Key Panel Master Key",
    }
    await call.message.edit_text(f"🔐 Enter new <b>{labels[action]}</b>.\n\nThis is a global credential: every product assigned to that provider will use the new value automatically.", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_ext_key)

@dp.message(AdminStates.wait_for_ext_key)
async def save_ext_key_or_bulk(m: Message, state: FSMContext):
    data = await state.get_data()
    action = data.get("bulk_api_action")
    value = (m.text or "").strip()
    if not value:
        return await m.answer("❌ Credential cannot be empty.")
    if action == "bunty_key":
        set_setting("external_api_key", value)
        msg = "⚡ Bunty API Key updated globally. All Bunty products now use the new key."
    elif action == "bunty_master":
        set_setting("external_master_key", value)
        msg = "⚡ Bunty Master Key updated globally. All Bunty products now use the new master."
    elif action == "keypanel_master":
        set_setting("keypanel_master_key", value)
        msg = "🛒 Key Panel Master Key updated globally. All Key Panel products now use the new master."
    else:
        # Existing normal External API setup flow.
        set_setting("external_api_key", value)
        msg = "✅ External API Key saved."
    await state.clear()
    await m.answer("✅ <b>" + html.escape(msg) + "</b>", reply_markup=admin_kb(), parse_mode='HTML')

@dp.callback_query(F.data == "admin_setup_famgateway")
async def admin_setup_famgateway(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    key = _famgateway_api_key()
    url = _famgateway_base_url()
    mask = (key[:4] + "••••" + key[-4:]) if len(key) > 8 else ("Configured" if key else "Not set")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Set FamGateway API Key", callback_data="admin_set_famgateway_key", style="primary")],
        [InlineKeyboardButton(text="Set Gateway URL", callback_data="admin_set_famgateway_url", style="primary")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel_back", style="danger")]
    ])
    await call.message.edit_text(f"💜 <b>FAMGATEWAY</b>\n\nURL: <code>{html.escape(url)}</code>\nAPI Key: <code>{mask}</code>\n\nUses the official FamGateway create-order and verify-order API.", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "admin_set_famgateway_key")
async def admin_set_famgateway_key(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("Enter FamGateway API Key:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_ext_master)
    await state.update_data(famgateway_field="key")

@dp.callback_query(F.data == "admin_set_famgateway_url")
async def admin_set_famgateway_url(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("Enter FamGateway base URL (default https://famgateway.in):", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_ext_master)
    await state.update_data(famgateway_field="url")

@dp.message(AdminStates.wait_for_ext_master)
async def save_ext_master_or_famgateway(m: Message, state: FSMContext):
    data = await state.get_data()
    value = (m.text or "").strip()
    if not value:
        return await m.answer("❌ Value cannot be empty.")
    if data.get("famgateway_field") == "key":
        set_setting("famgateway_api", value)
        msg = "FamGateway API Key saved."
    elif data.get("famgateway_field") == "url":
        if not value.startswith(("http://", "https://")):
            return await m.answer("❌ URL must start with http:// or https://")
        set_setting("famgateway_url", value.rstrip("/"))
        msg = "FamGateway URL saved."
    else:
        set_setting("external_master_key", value)
        msg = "External API Master Key saved."
    await state.clear()
    await m.answer("✅ " + msg, reply_markup=admin_kb(), parse_mode='HTML')

@dp.callback_query(F.data == "admin_setup_zapupi")
async def setup_zapupi_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("⚙️ <b>ZAPUPI SECURITY DEPLOYMENT</b>\nInput master <b>API Key (zap_key)</b>:\n<i>(Type /cancel to abort sequence)</i>", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_zapupi_api)

@dp.message(AdminStates.wait_for_zapupi_api)
async def zapupi_api(m: Message, state: FSMContext):
    if m.text == '/cancel':
        await state.clear()
        return await m.answer("Sequence killed.", reply_markup=admin_kb(), parse_mode='HTML')
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('zapupi_api', ?)", (m.text.strip(),))
    await m.answer("✅ <b>Keys synchronized with ZapUPI backbone.</b>", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_setup_binance")
async def setup_binance_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("🪙 <b>CRYPTO NODE INIT: Step 1/3</b>\nInput Master <b>Binance API Key</b>:\n<i>(Type /cancel to halt protocol)</i>", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_binance_api)

@dp.message(AdminStates.wait_for_binance_api)
async def setup_binance_api(m: Message, state: FSMContext):
    if m.text == '/cancel':
        await state.clear()
        return await m.answer("Sequence aborted.", reply_markup=admin_kb(), parse_mode='HTML')
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('binance_api', ?)", (m.text.strip(),))
    await m.answer("🪙 <b>CRYPTO NODE INIT: Step 2/3</b>\nNow inject the highly secure <b>Binance Secret Key</b>:", parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_binance_secret)

@dp.message(AdminStates.wait_for_binance_secret)
async def setup_binance_secret(m: Message, state: FSMContext):
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('binance_secret', ?)", (m.text.strip(),))
    await m.answer("🪙 <b>CRYPTO NODE INIT: Step 3/3</b>\nFinal variable: Set the public <b>USDT Deposit Address (TRC20/BEP20)</b>\nUsers will broadcast to this ledger:", parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_binance_address)

@dp.message(AdminStates.wait_for_binance_address)
async def setup_binance_address(m: Message, state: FSMContext):
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('binance_address', ?)", (m.text.strip(),))
    await m.answer("✅ <b>Blockchain node synchronized.</b> Crypto gateway is fully armed.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

# ==============================================================================
# 22. BOOTSTRAPPING & MAIN
# ==============================================================================
async def main() -> None:
    acquire_single_instance_lock()

    # Fail fast with a useful message. The screenshot's
    # "Telegram server says - Unauthorized" is a Telegram authentication
    # failure, not a database/FamGateway failure. Validate the token before
    # starting background workers or polling.
    global BOT_USERNAME
    try:
        me = await bot.get_me()
        BOT_USERNAME = f"@{me.username}" if me.username else ""
        logger.info("Telegram token verified successfully for %s (id=%s).", me.username or "unnamed-bot", me.id)
    except TelegramUnauthorizedError as exc:
        logger.critical(
            "BOT_TOKEN REJECTED BY TELEGRAM (Unauthorized). "
            "Set BOT_TOKEN to the CURRENT token from BotFather, then restart CloudVPS. %s", exc
        )
        return
    except Exception as exc:
        logger.critical("Telegram startup check failed: %s", exc)
        return

    init_db()
    logger.info("Initializing DB structure...")
    migrate_categories()
    asyncio.create_task(auto_verify_task(), name="payment-auto-verifier")
    logger.info("Payment Auto-Verifier Daemon Running in Background (ZapUPI + FamGateway).")
    logger.info("🚀 CORE SYSTEM IS FULLY OPERATIONAL...")
    try:
        await dp.start_polling(bot)
    except TelegramUnauthorizedError as err:
        logger.critical("Polling stopped: Telegram rejected BOT_TOKEN as Unauthorized. Update BOT_TOKEN and restart. %s", err)
    except Exception as err:
        logger.exception("Critical System Failure in Polling: %s", err)
    finally:
        await bot.session.close()

# ==============================================================================
# EXTERNAL KEY GENERATION API
# ==============================================================================
def normalize_api_duration(duration: str) -> str:
    """Normalize the package name into the duration format expected by the API."""
    value = str(duration or "").strip()
    if not value:
        return value

    # Normalize whitespace
    value = re.sub(r"\s+", " ", value)
    
    # Define exact mapping for your API format
    # API expects: "X Hours" or "X DaYs" (with capital D and Y)
    duration_map = {
        # Hours - API expects "X Hours"
        "1 hour": "1 Hours",
        "1 hours": "1 Hours",
        "1hr": "1 Hours",
        "1h": "1 Hours",
        "2 hour": "2 Hours",
        "2 hours": "2 Hours",
        "2hr": "2 Hours",
        "3 hour": "3 Hours",
        "3 hours": "3 Hours",
        "3hr": "3 Hours",
        "6 hour": "6 Hours",
        "6 hours": "6 Hours",
        "6hr": "6 Hours",
        "12 hour": "12 Hours",
        "12 hours": "12 Hours",
        "12hr": "12 Hours",
        
        "24 hour": "24 Hours",
        "24 hours": "24 Hours",
        "24hr": "24 Hours",
        
        # Days - API expects "X DaYs" (capital D and Y)
        "1 day": "1 DaYs",
        "1 days": "1 DaYs",
        "1d": "1 DaYs",
        "2 day": "2 DaYs",
        "2 days": "2 DaYs",
        "2d": "2 DaYs",
        "3 day": "3 DaYs",
        "3 days": "3 DaYs",
        "3d": "3 DaYs",
        "5 day": "5 DaYs",
        "5 days": "5 DaYs",
        "5d": "5 DaYs",
        "7 day": "7 DaYs",
        "7 days": "7 DaYs",
        "7d": "7 DaYs",
        "10 day": "10 DaYs",
        "10 days": "10 DaYs",
        "10d": "10 DaYs",
        "14 day": "14 DaYs",
        "14 days": "14 DaYs",
        "14d": "14 DaYs",
        "15 day": "15 DaYs",
        "15 days": "15 DaYs",
        "15d": "15 DaYs",
        "20 day": "20 DaYs",
        "20 days": "20 DaYs",
        "20d": "20 DaYs",
        "28 day": "28 DaYs",
        "28 days": "28 DaYs",
        "28d": "28 DaYs",
        "30 day": "30 DaYs",
        "30 days": "30 DaYs",
        "30d": "30 DaYs",
        "60 day": "60 DaYs",
        "60 days": "60 DaYs",
        "60d": "60 DaYs",
    }
    
    # Check exact matches first (case insensitive)
    lower_val = value.lower()
    for pattern, result in duration_map.items():
        if lower_val == pattern.lower():
            return result
    
    # Check if it's already in correct format
    if value in ["1 Hours", "2 Hours", "3 Hours", "6 Hours", "12 Hours", "24 Hours",
                 "1 DaYs", "2 DaYs", "3 DaYs", "5 DaYs", "7 DaYs", "10 DaYs",
                 "14 DaYs", "15 DaYs", "20 DaYs", "28 DaYs", "30 DaYs", "60 DaYs"]:
        return value
    
    # Try to extract number and determine unit ONLY IF string is purely duration
    match = re.match(r"^(\d+)\s*(hour|hours|hr|hrs|h|day|days|d)$", value, re.IGNORECASE)
    if match:
        number = match.group(1)
        unit = match.group(2).lower()
        if unit in ["hour", "hours", "hr", "hrs", "h"]:
            return f"{number} Hours"
        if unit in ["day", "days", "d"]:
            return f"{number} DaYs"
    
    # If it's just a number, treat as hours
    if value.isdigit():
        return f"{value} Hours"
    
    # Return as-is if compound string (e.g. "1 DaYS NONROOT", "1 Days Nonroot")
    return value

async def fetch_external_key(product_id: str, duration: str, android_id: str = "") -> dict:
    """Buy a key using a fresh aiohttp session for every API attempt."""
    url = get_setting("external_api_url", "https://adminpanels.shop/api/reseller_v1.php").strip()
    api_key = get_setting("external_api_key", "").strip()
    master_key = get_setting("external_master_key", "").strip()

    if not url:
        return {"status": "error", "msg": "External API URL is not configured"}
    if not api_key:
        return {"status": "error", "msg": "External API key is not configured"}

    product_id = str(product_id or "").strip()
    duration = str(duration or "").strip()
    if not product_id:
        return {"status": "error", "msg": "External API Product ID is empty"}
    if not duration:
        return {"status": "error", "msg": "Product duration is empty"}

    data = {"api_key": api_key, "action": "buy", "product_id": product_id, "duration": duration}
    if android_id:
        data["android_id"] = str(android_id).strip()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json, text/plain, */*",
    }
    if master_key:
        headers["x-master-key"] = master_key

    timeout = aiohttp.ClientTimeout(total=15, connect=5, sock_connect=5, sock_read=10)

    for attempt in range(1, 3):
        try:
            logger.info("External API BUY attempt=%s product_id=%r duration=%r", attempt, product_id, duration)

            # Never reuse a ClientSession/connector from a previous attempt.
            connector = aiohttp.TCPConnector(family=socket.AF_INET, force_close=True, ssl=True)
            try:
                async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                    async with session.post(url, data=data, headers=headers, allow_redirects=True) as resp:
                        raw = await resp.text()
                        logger.info("External API BUY response: HTTP %s body=%s", resp.status, raw[:1000])

                        if resp.status != 200:
                            return {"status": "error", "msg": f"HTTP {resp.status}: {raw[:500]}"}
                        if not raw.strip():
                            return {"status": "error", "msg": "API returned an empty response"}
                        try:
                            result = json.loads(raw)
                        except json.JSONDecodeError:
                            return {"status": "error", "msg": f"API returned invalid JSON: {raw[:300]}"}
                        if not isinstance(result, dict):
                            return {"status": "error", "msg": "API returned an invalid response object"}
                        return result
            finally:
                if not connector.closed:
                    await connector.close()

        except (aiohttp.ClientConnectorError, aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError, asyncio.TimeoutError) as exc:
            logger.warning("External API connection attempt %s failed: %s", attempt, exc)
            if attempt == 1:
                await asyncio.sleep(1)
                continue
            return {"status": "error", "msg": f"API connection failed: {type(exc).__name__}: {exc}"}
        except aiohttp.ClientError as exc:
            logger.exception("External API client error")
            return {"status": "error", "msg": f"API client error: {exc}"}
        except Exception as exc:
            logger.exception("API unexpected error")
            return {"status": "error", "msg": f"API unexpected error: {exc}"}

    return {"status": "error", "msg": "API request failed after retries"}


async def keypanel_request(action: str, **kwargs) -> dict:
    """Call the FFPanel reseller-v2 API using JSON. Secrets stay in server env."""
    url = (
        os.getenv("KEYPANEL_API_URL", "").strip()
        or os.getenv("FFPANEL_API_URL", "").strip()
        or "https://ffpanelstore.kesug.com/api/reseller-v2.php"
    )
    api_key = os.getenv("KEYPANEL_API_KEY", "").strip() or os.getenv("FFPANEL_API_KEY", "").strip() or os.getenv("API_KEY", "").strip()
    master_key = os.getenv("KEYPANEL_MASTER_KEY", "").strip() or os.getenv("FFPANEL_MASTER_KEY", "").strip() or os.getenv("MASTER_KEY", "").strip()

    if not url.startswith(("https://", "http://")):
        return {"status": "error", "success": False, "message": "Key Panel API URL must start with http:// or https://"}
    if not api_key:
        return {"status": "error", "success": False, "message": "Missing API key. Set KEYPANEL_API_KEY in server environment variables."}
    if not master_key:
        return {"status": "error", "success": False, "message": "Missing master key. Set KEYPANEL_MASTER_KEY in server environment variables."}
    if action not in {"plans", "balance", "profile", "buy"}:
        return {"status": "error", "success": False, "message": f"Unsupported Key Panel action: {action}"}

    payload = {"action": action, "api_key": api_key, "master_key": master_key, **kwargs}
    timeout = aiohttp.ClientTimeout(total=30, connect=7, sock_connect=7, sock_read=20)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url, json=payload,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            ) as resp:
                raw = await resp.text()
                try:
                    result = json.loads(raw)
                except json.JSONDecodeError:
                    return {"status": "error", "success": False, "message": "Invalid JSON response from Key Panel API", "http_code": resp.status}
                if not isinstance(result, dict):
                    return {"status": "error", "success": False, "message": "Unexpected API response", "http_code": resp.status}
                result["http_code"] = resp.status
                ok = result.get("success") is True
                result["status"] = "success" if ok else "error"
                if not ok and not result.get("message"):
                    result["message"] = f"Key Panel API request failed (HTTP {resp.status})"
                return result
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("Key Panel reseller-v2 request failed for action=%s: %s", action, exc)
        return {"status": "error", "success": False, "message": f"API network error: {type(exc).__name__}: {exc}"}
    except Exception as exc:
        logger.exception("Unexpected Key Panel reseller-v2 error")
        return {"status": "error", "success": False, "message": f"Unexpected API error: {type(exc).__name__}"}


async def keypanel_buy_key(plan_id: str, quantity: int = 1, customer_tag: str = "") -> dict:
    """Purchase license key(s) from a reseller-v2 plan."""
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return {"status": "error", "success": False, "message": "Quantity must be a number"}
    plan_id = str(plan_id or "").strip()
    if not plan_id:
        return {"status": "error", "success": False, "message": "Key Panel plan_id is empty"}
    if quantity < 1 or quantity > 10:
        return {"status": "error", "success": False, "message": "Quantity must be between 1 and 10"}
    return await keypanel_request("buy", plan_id=plan_id, quantity=quantity)


async def keypanel_check_config() -> dict:
    """Validate credentials by requesting the authenticated reseller profile."""
    return await keypanel_request("profile")


def _api_result_text(result: dict, heading: str) -> str:
    body = result.get("data")
    if body is None:
        body = {k: v for k, v in result.items() if k not in {"http_code", "status", "success"}}
    rendered = json.dumps(body, ensure_ascii=False, indent=2, default=str)
    if len(rendered) > 3000:
        rendered = rendered[:3000] + "\n…"
    return (
        f"<b>{html.escape(heading)}</b>\n\n"
        f"HTTP: <code>{html.escape(str(result.get('http_code', '-')))}</code>\n"
        f"<pre>{html.escape(rendered)}</pre>"
    )


@dp.callback_query(F.data == "admin_setup_keypanel")
async def admin_setup_keypanel(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    url = (
        os.getenv("KEYPANEL_API_URL", "").strip()
        or os.getenv("FFPANEL_API_URL", "").strip()
        or "https://ffpanelstore.kesug.com/api/reseller-v2.php"
    )
    api_key = os.getenv("KEYPANEL_API_KEY", "").strip() or os.getenv("FFPANEL_API_KEY", "").strip() or os.getenv("API_KEY", "").strip()
    master = os.getenv("KEYPANEL_MASTER_KEY", "").strip() or os.getenv("FFPANEL_MASTER_KEY", "").strip() or os.getenv("MASTER_KEY", "").strip()
    mask = lambda value: (value[:4] + "••••••" + value[-4:]) if len(value) > 8 else ("Configured" if value else "Not set")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Get Plans", callback_data="admin_keypanel_plans", style="primary")],
        [InlineKeyboardButton(text="💰 Check Balance", callback_data="admin_keypanel_balance", style="success")],
        [InlineKeyboardButton(text="👤 Check Profile", callback_data="admin_keypanel_profile", style="primary")],
        [InlineKeyboardButton(text="🧪 Validate Config", callback_data="admin_test_keypanel", style="success")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel_back", style="danger")]
    ])
    await call.message.edit_text(
        "🔐 <b>FFPANEL RESELLER-V2 API</b>\n\n"
        f"Endpoint: <code>{html.escape(url)}</code>\n"
        f"API Key: <code>{html.escape(mask(api_key))}</code>\n"
        f"Master Key: <code>{html.escape(mask(master))}</code>\n\n"
        "Set credentials in VPS environment variables (not in chat): "
        "<code>KEYPANEL_API_KEY</code> and <code>KEYPANEL_MASTER_KEY</code>.\n"
        "Use the plan_id returned by Get Plans when adding a product.",
        reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.in_({"admin_keypanel_plans", "admin_keypanel_balance", "admin_keypanel_profile"}))
async def keypanel_admin_read_action(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    action_map = {
        "admin_keypanel_plans": ("plans", "FFPanel Active Plans"),
        "admin_keypanel_balance": ("balance", "FFPanel Reseller Balance"),
        "admin_keypanel_profile": ("profile", "FFPanel Reseller Profile"),
    }
    action, heading = action_map[call.data]
    await call.answer("Checking API…")
    result = await keypanel_request(action)
    if result.get("success") is True:
        await call.message.edit_text(_api_result_text(result, heading), reply_markup=admin_back_kb(), parse_mode="HTML")
    else:
        msg = result.get("message") or result.get("msg") or "API request failed"
        await call.message.edit_text(
            f"❌ <b>{html.escape(heading)} failed</b>\n\n"
            f"HTTP: <code>{html.escape(str(result.get('http_code', '-')))}</code>\n"
            f"{html.escape(str(msg))}",
            reply_markup=admin_back_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "admin_set_keypanel_url")
async def admin_set_keypanel_url(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("Enter Key Panel API endpoint URL:\n\nDefault: <code>https://keypanel.shop/reseller_gateway.php</code>", reply_markup=admin_back_kb(), parse_mode="HTML")
    await state.set_state(AdminStates.wait_for_keypanel_url)

@dp.message(AdminStates.wait_for_keypanel_url)
async def save_keypanel_url(m: Message, state: FSMContext):
    value = (m.text or "").strip()
    if not value.startswith(("http://", "https://")):
        return await m.answer("❌ URL must start with http:// or https://")
    set_setting("keypanel_api_url", value)
    await state.clear()
    await m.answer("✅ Key Panel API URL saved.", reply_markup=admin_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_set_keypanel_master")
async def admin_set_keypanel_master(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("Enter Key Panel <b>Master Key</b> (secret):\n\nIt will be stored masked and never displayed in full.", reply_markup=admin_back_kb(), parse_mode="HTML")
    await state.set_state(AdminStates.wait_for_keypanel_master)

@dp.message(AdminStates.wait_for_keypanel_master)
async def save_keypanel_master(m: Message, state: FSMContext):
    value = (m.text or "").strip()
    if not value:
        return await m.answer("❌ Master Key cannot be empty.")
    set_setting("keypanel_master_key", value)
    await state.clear()
    await m.answer("✅ Key Panel Master Key saved securely.", reply_markup=admin_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_test_keypanel")
async def admin_test_keypanel(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    result = await keypanel_check_config()
    if result.get("success") is True:
        await call.answer("✅ Key Panel configuration OK", show_alert=True)
        await call.message.edit_text("🔐 <b>Key Panel Ready</b>\n\nCredentials accepted. Profile request succeeded; no purchase was made.", reply_markup=admin_back_kb(), parse_mode="HTML")
    else:
        await call.answer("❌ Key Panel configuration error", show_alert=True)
        message = html.escape(str(result.get("message", "Unknown error")))
        await call.message.edit_text(f"❌ <b>Key Panel Configuration Error</b>\n\n{message}", reply_markup=admin_back_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_setup_external_api")
async def admin_setup_external_api(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID:
        return
    url = get_setting("external_api_url", "")
    key = get_setting("external_api_key", "")
    master = get_setting("external_master_key", "")
    mask = lambda x: (x[:4] + "••••" + x[-4:]) if len(x) > 8 else ("Configured" if x else "Not set")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Set API URL", callback_data="admin_set_ext_url", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
        [InlineKeyboardButton(text="Set API Key", callback_data="admin_set_ext_key", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
        [InlineKeyboardButton(text="Set Master Key", callback_data="admin_set_ext_master", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
        [InlineKeyboardButton(text="💰 Check Bunty Balance", callback_data="admin_check_bunty_balance", style="success")],
        [InlineKeyboardButton(text="🩺 API Health Check", callback_data="admin_bunty_health", style="primary")],
        [InlineKeyboardButton(text="Back", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    text = ("🔗 <b>External Key API Configuration</b>\n\n"
            f"URL: <code>{url or 'Not set'}</code>\n"
            f"API Key: <code>{mask(key)}</code>\n"
            f"Master Key: <code>{mask(master)}</code>\n\n"
            "Products can be switched to API generation from the Add Product flow.")
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "admin_set_ext_url")
async def set_ext_url(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("Enter External API URL:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_ext_url)

@dp.message(AdminStates.wait_for_ext_url)
async def save_ext_url(m: Message, state: FSMContext):
    value = m.text.strip()
    if not value.startswith(("http://", "https://")):
        return await m.answer("❌ URL must start with http:// or https://")
    set_setting("external_api_url", value)
    await m.answer("✅ External API URL saved.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_ext_key")
async def set_ext_key(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("Enter External API Key:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_ext_key)

@dp.message(AdminStates.wait_for_ext_key)
async def save_ext_key(m: Message, state: FSMContext):
    set_setting("external_api_key", m.text.strip())
    await m.answer("✅ External API Key saved.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_ext_master")
async def set_ext_master(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("Enter External API Master Key:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_ext_master)

@dp.message(AdminStates.wait_for_ext_master)
async def save_ext_master(m: Message, state: FSMContext):
    set_setting("external_master_key", m.text.strip())
    await m.answer("✅ External API Master Key saved.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

if __name__ == "__main__":
    acquire_single_instance_lock()
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("System shutting down gracefully. Goodbye.")
