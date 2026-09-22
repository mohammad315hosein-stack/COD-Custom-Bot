import telebot
from telebot import types
import sqlite3
import threading
import time
import re
import html
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import quote

# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_ID = 8997986166

CHANNEL_USERNAME = "@CODCusttom"
CHANNEL_URL = "https://t.me/CODCusttom"

DB_PATH = "cod_custom.db"

TZ = ZoneInfo("Asia/Tehran")

MAX_TEAMS = 25
TEAM_CAPACITY = 4
MAX_PLAYERS = 100
LOCK_MINUTES = 5
FREE_PRIZE_PER_MEMBER = 20
COIN_ENTRY_FEE = 25

bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode="HTML"
)

state_lock = threading.RLock()
states = {}


# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate_db(conn):
    """Add columns introduced by newer bot versions to an existing SQLite DB."""
    migrations = {
        "users": {
            "joined_at": "TEXT",
            "last_name_change": "TEXT",
            "cash_balance": "INTEGER NOT NULL DEFAULT 0",
            "banned": "INTEGER NOT NULL DEFAULT 0",
            "ban_until": "TEXT",
            "ban_reason": "TEXT DEFAULT ''",
            "welcome_bonus_claimed": "INTEGER NOT NULL DEFAULT 0",
            "rank_points": "INTEGER NOT NULL DEFAULT 0",
            "rank": "TEXT NOT NULL DEFAULT 'برنز'",
            "daily_last_claim": "TEXT",
            "daily_streak": "INTEGER NOT NULL DEFAULT 0",
        },
        "customs": {
            "date": "TEXT",
            "time": "TEXT",
            "entry_fee": "INTEGER NOT NULL DEFAULT 0",
            "min_players": "INTEGER NOT NULL DEFAULT 1",
            "winners_count": "INTEGER NOT NULL DEFAULT 1",
            "prize1": "INTEGER NOT NULL DEFAULT 0",
            "prize2": "INTEGER NOT NULL DEFAULT 0",
            "prize3": "INTEGER NOT NULL DEFAULT 0",
            "lock_minutes": "INTEGER NOT NULL DEFAULT 5",
            "room_id": "TEXT DEFAULT ''",
            "room_password": "TEXT DEFAULT ''",
            "results_submitted": "INTEGER NOT NULL DEFAULT 0",
        },
        "participants": {
            "entry_paid": "INTEGER NOT NULL DEFAULT 0",
            "left_at": "TEXT",
        },
        "promo_codes": {
            "code": "TEXT",
            "amount": "INTEGER NOT NULL DEFAULT 0",
            "max_uses": "INTEGER NOT NULL DEFAULT 0",
            "used_count": "INTEGER NOT NULL DEFAULT 0",
            "expires_at": "TEXT",
            "active": "INTEGER NOT NULL DEFAULT 1",
            "created_by": "INTEGER",
            "created_at": "TEXT",
        },
        "transactions": {
            "balance_type": "TEXT NOT NULL DEFAULT 'coins'",
            "description": "TEXT DEFAULT ''",
            "custom_id": "INTEGER",
            "admin_id": "INTEGER",
        },
    }
    for table, columns in migrations.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if not existing:
            continue
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    # Repair legacy databases created by older bot versions.
    # In particular, old users tables did not have joined_at.
    # SQLite allows adding this nullable column without rebuilding the table.
    users_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if users_cols and "joined_at" not in users_cols:
        conn.execute("ALTER TABLE users ADD COLUMN joined_at TEXT")

    # All open/locked customs use the new 5-minute registration lock.
    conn.execute("UPDATE customs SET lock_minutes=? WHERE status IN ('open','locked')", (LOCK_MINUTES,))


def init_db():

    conn = db()

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        first_name TEXT DEFAULT '',
        username TEXT DEFAULT '',
        cod_name TEXT DEFAULT '',
        coins INTEGER NOT NULL DEFAULT 0,
        cash_balance INTEGER NOT NULL DEFAULT 0,
        joined_at TEXT NOT NULL,
        last_name_change TEXT,
        banned INTEGER NOT NULL DEFAULT 0,
        ban_until TEXT,
        ban_reason TEXT DEFAULT '',
        welcome_bonus_claimed INTEGER NOT NULL DEFAULT 0,
        rank_points INTEGER NOT NULL DEFAULT 0,
        rank TEXT NOT NULL DEFAULT 'برنز',
        daily_last_claim TEXT,
        daily_streak INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS referrals(
        inviter_id INTEGER NOT NULL,
        invitee_id INTEGER PRIMARY KEY,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS customs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        title TEXT NOT NULL,
        date TEXT NOT NULL,
        time TEXT NOT NULL,

        custom_type TEXT NOT NULL,

        entry_fee INTEGER NOT NULL DEFAULT 0,

        min_players INTEGER NOT NULL DEFAULT 1,

        winners_count INTEGER NOT NULL DEFAULT 1,

        prize1 INTEGER NOT NULL DEFAULT 0,
        prize2 INTEGER NOT NULL DEFAULT 0,
        prize3 INTEGER NOT NULL DEFAULT 0,

        lock_minutes INTEGER NOT NULL DEFAULT 5,

        status TEXT NOT NULL DEFAULT 'open',

        room_id TEXT DEFAULT '',
        room_password TEXT DEFAULT '',

        created_by INTEGER,
        created_at TEXT NOT NULL,

        results_submitted INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS teams(
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        custom_id INTEGER NOT NULL,
        team_no INTEGER NOT NULL,

        UNIQUE(custom_id, team_no),

        FOREIGN KEY(custom_id)
            REFERENCES customs(id)
            ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS participants(
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        custom_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,

        team_no INTEGER NOT NULL,

        joined_at TEXT NOT NULL,

        entry_paid INTEGER NOT NULL DEFAULT 0,

        left_at TEXT,

        UNIQUE(custom_id, user_id),

        FOREIGN KEY(custom_id)
            REFERENCES customs(id)
            ON DELETE CASCADE,

        FOREIGN KEY(user_id)
            REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS transactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        user_id INTEGER NOT NULL,

        amount INTEGER NOT NULL,

        balance_type TEXT NOT NULL DEFAULT 'coins',

        kind TEXT NOT NULL,

        description TEXT DEFAULT '',

        custom_id INTEGER,

        admin_id INTEGER,

        created_at TEXT NOT NULL,

        FOREIGN KEY(user_id)
            REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS winners(
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        custom_id INTEGER NOT NULL,

        team_no INTEGER NOT NULL,

        place INTEGER NOT NULL,

        prize INTEGER NOT NULL,

        created_at TEXT NOT NULL,

        UNIQUE(custom_id, place),

        UNIQUE(custom_id, team_no)
    );

    CREATE TABLE IF NOT EXISTS result_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        custom_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        team_no INTEGER NOT NULL,
        place INTEGER NOT NULL,
        prize INTEGER NOT NULL DEFAULT 0,
        rank_points_awarded INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        UNIQUE(custom_id, user_id)
    );

    CREATE INDEX IF NOT EXISTS idx_result_history_user
    ON result_history(user_id);

    CREATE TABLE IF NOT EXISTS achievements(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        achievement_key TEXT NOT NULL,
        title TEXT NOT NULL,
        unlocked_at TEXT NOT NULL,
        UNIQUE(user_id, achievement_key)
    );

    CREATE INDEX IF NOT EXISTS idx_achievements_user
    ON achievements(user_id);

    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS custom_notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        custom_id INTEGER NOT NULL,
        event_key TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(custom_id, event_key),
        FOREIGN KEY(custom_id) REFERENCES customs(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS promo_codes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        amount INTEGER NOT NULL,
        max_uses INTEGER NOT NULL DEFAULT 0,
        used_count INTEGER NOT NULL DEFAULT 0,
        expires_at TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        created_by INTEGER,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS promo_redemptions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        promo_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        redeemed_at TEXT NOT NULL,
        UNIQUE(promo_id, user_id),
        FOREIGN KEY(promo_id) REFERENCES promo_codes(id) ON DELETE CASCADE,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE INDEX IF NOT EXISTS idx_participants_custom
    ON participants(custom_id);

    CREATE INDEX IF NOT EXISTS idx_transactions_user
    ON transactions(user_id);

    CREATE TABLE IF NOT EXISTS friends(
        user_id INTEGER NOT NULL,
        friend_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(user_id, friend_id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(friend_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        kind TEXT DEFAULT 'general',
        is_read INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        UNIQUE(user_id, kind, title, body, created_at),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS daily_missions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        mission_type TEXT NOT NULL,
        target INTEGER NOT NULL,
        reward INTEGER NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS mission_progress(
        user_id INTEGER NOT NULL,
        mission_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        progress INTEGER NOT NULL DEFAULT 0,
        claimed INTEGER NOT NULL DEFAULT 0,
        UNIQUE(user_id, mission_id, day),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(mission_id) REFERENCES daily_missions(id)
    );

    CREATE TABLE IF NOT EXISTS campaigns(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        reward INTEGER NOT NULL DEFAULT 0,
        start_at TEXT NOT NULL,
        end_at TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_by INTEGER,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS campaign_claims(
        campaign_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        claimed_at TEXT NOT NULL,
        UNIQUE(campaign_id, user_id),
        FOREIGN KEY(campaign_id) REFERENCES campaigns(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS admin_confirmations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER NOT NULL,
        action_key TEXT NOT NULL,
        payload TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        UNIQUE(admin_id, action_key)
    );

    CREATE TABLE IF NOT EXISTS security_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_id INTEGER,
        target_user_id INTEGER,
        action TEXT NOT NULL,
        details TEXT DEFAULT '',
        custom_id INTEGER,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_security_logs_created
    ON security_logs(created_at);

    CREATE INDEX IF NOT EXISTS idx_security_logs_target
    ON security_logs(target_user_id);
    """)

    migrate_db(conn)
    conn.commit()
    conn.close()


# =========================================================
# HELPERS
# =========================================================

def security_log(action, actor_id=None, target_user_id=None, details="", custom_id=None, conn=None):
    own = conn is None
    connection = conn or db()
    connection.execute(
        """INSERT INTO security_logs(actor_id,target_user_id,action,details,custom_id,created_at) VALUES(?,?,?,?,?,?)""",
        (actor_id, target_user_id, action, details, custom_id, now_str())
    )
    if own:
        connection.commit()
        connection.close()


def security_dashboard(chat_id):
    conn = db()
    total = conn.execute("SELECT COUNT(*) FROM security_logs").fetchone()[0]
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    today_count = conn.execute("SELECT COUNT(*) FROM security_logs WHERE created_at LIKE ?", (today+"%",)).fetchone()[0]
    banned = conn.execute("SELECT COUNT(*) FROM users WHERE banned=1").fetchone()[0]
    suspicious = conn.execute("SELECT COUNT(*) FROM security_logs WHERE action IN ('duplicate_result','duplicate_registration','balance_adjust','ban','unban') ORDER BY id DESC LIMIT 100").fetchall()
    rows = conn.execute("SELECT action,details,actor_id,target_user_id,created_at FROM security_logs ORDER BY id DESC LIMIT 12").fetchall()
    conn.close()
    labels = {'ban':'مسدودسازی','unban':'رفع مسدودی','balance_adjust':'تغییر موجودی','duplicate_result':'نتیجه تکراری','duplicate_registration':'ثبت‌نام تکراری','payout_blocked':'پرداخت تکراری','admin_action':'عملیات ادمین'}
    text = ("🛡️ <b>مرکز امنیت</b>\n\n" f"📋 کل لاگ‌ها: <b>{total}</b>\n" f"📅 امروز: <b>{today_count}</b>\n" f"🚫 کاربران مسدود: <b>{banned}</b>\n\n" "🧾 <b>آخرین رویدادها</b>\n")
    for r in rows:
        text += f"\n• {labels.get(r['action'], r['action'])} | {r['details'] or '-'} | {r['created_at']}"
    kb=types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🔄 بروزرسانی", callback_data="admin:security"), types.InlineKeyboardButton("🚫 کاربران مسدود", callback_data="admin:banned"))
    kb.add(types.InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin"))
    bot.send_message(chat_id,text,reply_markup=kb)


def admin_banned_users(chat_id):
    conn=db(); rows=conn.execute("SELECT id,cod_name,coins FROM users WHERE banned=1 ORDER BY id DESC LIMIT 50").fetchall(); conn.close()
    text="🚫 <b>کاربران مسدود</b>\n\n" + ("\n".join(f"• {r['cod_name'] or 'بدون نام'} | ID: <code>{r['id']}</code> | {r['coins']} 🪙" for r in rows) if rows else "موردی وجود ندارد.")
    kb=types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("🛡️ امنیت", callback_data="admin:security"),types.InlineKeyboardButton("🔙 پنل",callback_data="admin")); bot.send_message(chat_id,text,reply_markup=kb)


def now_str():
    return datetime.now(TZ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def parse_custom_datetime(row):

    return datetime.strptime(
        f"{row['date']} {row['time']}",
        "%Y-%m-%d %H:%M"
    ).replace(tzinfo=TZ)


def is_admin(user_id):
    return user_id == ADMIN_ID


def set_state(user_id, data):

    with state_lock:
        states[user_id] = data


def get_state(user_id):

    with state_lock:
        return states.get(user_id)


def clear_state(user_id):

    with state_lock:
        states.pop(user_id, None)


def upsert_user(user):

    conn = db()

    conn.execute("""
        INSERT INTO users(
            id,
            first_name,
            username,
            joined_at
        )
        VALUES(
            ?,
            ?,
            ?,
            ?
        )

        ON CONFLICT(id)
        DO UPDATE SET
            first_name=excluded.first_name,
            username=excluded.username
    """, (
        user.id,
        user.first_name or "",
        user.username or "",
        now_str()
    ))

    conn.commit()
    conn.close()


def get_user(user_id):

    conn = db()

    row = conn.execute(
        "SELECT * FROM users WHERE id=?",
        (user_id,)
    ).fetchone()

    conn.close()

    return row


def is_banned(user_id):
    user = get_user(user_id)
    if not user:
        return False
    if not user["banned"]:
        return False
    until = user["ban_until"] if "ban_until" in user.keys() else None
    if until:
        try:
            dt = datetime.strptime(until, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
            if datetime.now(TZ) >= dt:
                conn=db(); conn.execute("UPDATE users SET banned=0, ban_until=NULL, ban_reason='' WHERE id=?",(user_id,)); conn.commit(); conn.close()
                return False
        except Exception:
            pass
    return True


def valid_int(text, minimum=0):

    text = text.strip()

    text = text.replace(",", "")
    text = text.replace("٬", "")

    if not re.fullmatch(r"\d+", text):
        return None

    number = int(text)

    if number < minimum:
        return None

    return number


def add_coins(
    user_id,
    amount,
    kind,
    description="",
    custom_id=None,
    admin_id=None,
    conn=None
):

    own_connection = conn is None

    connection = conn or db()

    connection.execute(
        """
        UPDATE users
        SET coins = coins + ?
        WHERE id=?
        """,
        (
            int(amount),
            user_id
        )
    )

    connection.execute(
        """
        INSERT INTO transactions(
            user_id,
            amount,
            balance_type,
            kind,
            description,
            custom_id,
            admin_id,
            created_at
        )
        VALUES(
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?
        )
        """,
        (
            user_id,
            int(amount),
            "coins",
            kind,
            description,
            custom_id,
            admin_id,
            now_str()
        )
    )

    if own_connection:
        connection.commit()
        connection.close()


# =========================================================
# PROMO CODES
# =========================================================

def normalize_promo_code(value):
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]{3,32}", value):
        return None
    return value


def redeem_promo_code(user_id, raw_code):
    code = normalize_promo_code(raw_code)
    if not code:
        return False, "❌ کد نامعتبر است. فقط حروف انگلیسی، عدد، _ و - و بین 3 تا 32 کاراکتر مجاز است."

    conn = db()
    try:
        with conn:
            row = conn.execute("SELECT * FROM promo_codes WHERE code=?", (code,)).fetchone()
            if not row or not row["active"]:
                return False, "❌ این کد وجود ندارد یا غیرفعال شده است."

            if row["expires_at"]:
                try:
                    exp = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
                    if datetime.now(TZ) >= exp:
                        conn.execute("UPDATE promo_codes SET active=0 WHERE id=?", (row["id"],))
                        return False, "⏰ مهلت استفاده از این کد تمام شده است."
                except Exception:
                    pass

            if row["max_uses"] > 0 and row["used_count"] >= row["max_uses"]:
                conn.execute("UPDATE promo_codes SET active=0 WHERE id=?", (row["id"],))
                return False, "❌ ظرفیت استفاده از این کد تکمیل شده است."

            already = conn.execute("SELECT 1 FROM promo_redemptions WHERE promo_id=? AND user_id=?", (row["id"], user_id)).fetchone()
            if already:
                return False, "⚠️ شما قبلاً از این کد استفاده کرده‌اید."

            add_coins(user_id, row["amount"], "promo_code", f"فعال‌سازی کد جایزه {code}", None, None, conn)
            conn.execute("INSERT INTO promo_redemptions(promo_id,user_id,amount,redeemed_at) VALUES(?,?,?,?)", (row["id"], user_id, row["amount"], now_str()))
            conn.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE id=?", (row["id"],))
            return True, f"🎉 <b>کد با موفقیت فعال شد!</b>\n\n🎟️ کد: <code>{code}</code>\n🪙 جایزه: <b>+{row['amount']} کوین</b>"
    except sqlite3.IntegrityError:
        return False, "⚠️ این کد قبلاً برای حساب شما ثبت شده است."
    finally:
        conn.close()


def show_promo_redeem(chat_id, user_id):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 منوی اصلی", callback_data="home"))
    set_state(user_id, {"action": "promo_redeem"})
    bot.send_message(chat_id, "🎟️ <b>کد جایزه</b>\n\nکد جایزه را وارد کنید:\nمثال: <code>COD100</code>", reply_markup=kb)


def admin_promo_menu(chat_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ ساخت کد", callback_data="admin:promo:create"),
        types.InlineKeyboardButton("📋 لیست کدها", callback_data="admin:promo:list")
    )
    kb.add(types.InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin"))
    bot.send_message(chat_id, "🎟️ <b>مدیریت کدهای جایزه</b>", reply_markup=kb)


def admin_promo_list(chat_id):
    conn = db()
    rows = conn.execute("SELECT * FROM promo_codes ORDER BY id DESC LIMIT 30").fetchall()
    conn.close()
    kb = types.InlineKeyboardMarkup()
    lines = ["🎟️ <b>کدهای جایزه</b>\n"]
    if not rows:
        lines.append("هنوز کدی ساخته نشده است.")
    for row in rows:
        status = "فعال" if row["active"] else "غیرفعال"
        limit = "نامحدود" if row["max_uses"] == 0 else str(row["max_uses"])
        exp = row["expires_at"] or "بدون انقضا"
        lines.append(f"<code>{row['code']}</code> — {row['amount']} 🪙 | {row['used_count']}/{limit} | {status} | {exp}")
        if row["active"]:
            kb.add(types.InlineKeyboardButton(f"⛔ غیرفعال کردن {row['code']}", callback_data=f"admin:promo:disable:{row['id']}"))
    kb.add(types.InlineKeyboardButton("🔙 مدیریت کدها", callback_data="admin:promo"))
    bot.send_message(chat_id, "\n".join(lines), reply_markup=kb)


def handle_promo_admin_text(user_id, text):
    state = get_state(user_id)
    if not state or state.get("action") != "promo_create":
        return
    step = state.get("step")
    data = state.setdefault("data", {})
    if step == "code":
        code = normalize_promo_code(text)
        if not code:
            bot.send_message(user_id, "❌ کد نامعتبر است. مثال: <code>COD100</code>")
            return
        conn = db(); exists = conn.execute("SELECT 1 FROM promo_codes WHERE code=?", (code,)).fetchone(); conn.close()
        if exists:
            bot.send_message(user_id, "❌ این کد قبلاً ساخته شده است. یک کد دیگر وارد کنید.")
            return
        data["code"] = code; state["step"] = "amount"
        bot.send_message(user_id, "🪙 مقدار جایزه را به کوین وارد کنید:")
    elif step == "amount":
        amount = valid_int(text, 1)
        if amount is None:
            bot.send_message(user_id, "❌ یک عدد صحیح بزرگ‌تر از صفر وارد کنید.")
            return
        data["amount"] = amount; state["step"] = "uses"
        bot.send_message(user_id, "👥 حداکثر تعداد استفاده را وارد کنید.\n0 = نامحدود")
    elif step == "uses":
        uses = valid_int(text, 0)
        if uses is None:
            bot.send_message(user_id, "❌ عدد نامعتبر است.")
            return
        data["max_uses"] = uses; state["step"] = "expiry"
        bot.send_message(user_id, "⏰ چند روز اعتبار داشته باشد؟\n0 = بدون انقضا")
    elif step == "expiry":
        days = valid_int(text, 0)
        if days is None:
            bot.send_message(user_id, "❌ عدد نامعتبر است.")
            return
        data["days"] = days
        expires = (datetime.now(TZ) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S") if days > 0 else None
        conn = db()
        try:
            with conn:
                conn.execute("INSERT INTO promo_codes(code,amount,max_uses,used_count,expires_at,active,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)", (data["code"],data["amount"],data["max_uses"],0,expires,1,user_id,now_str()))
        except sqlite3.IntegrityError:
            bot.send_message(user_id, "❌ این کد قبلاً ساخته شده است.")
            conn.close(); return
        conn.close(); clear_state(user_id)
        exp = expires or "بدون انقضا"
        limit = "نامحدود" if data["max_uses"] == 0 else str(data["max_uses"])
        bot.send_message(user_id, f"✅ <b>کد ساخته شد</b>\n\n🎟️ <code>{data['code']}</code>\n🪙 جایزه: {data['amount']} کوین\n👥 استفاده: {limit}\n⏰ انقضا: {exp}")
        admin_promo_menu(user_id)

# =========================================================
# CHANNEL
# =========================================================

def channel_keyboard():

    keyboard = types.InlineKeyboardMarkup()

    keyboard.add(
        types.InlineKeyboardButton(
            "📢 عضویت در کانال",
            url=CHANNEL_URL
        )
    )

    keyboard.add(
        types.InlineKeyboardButton(
            "✅ بررسی عضویت",
            callback_data="check_channel"
        )
    )

    return keyboard


def member_status(user_id):

    try:

        member = bot.get_chat_member(
            CHANNEL_USERNAME,
            user_id
        )

        if member.status in (
            "member",
            "administrator",
            "creator"
        ):
            return True

        if (
            member.status == "restricted"
            and getattr(member, "is_member", False)
        ):
            return True

        return False

    except Exception:

        return False


def require_channel(chat_id, user_id):

    if member_status(user_id):
        return True

    bot.send_message(
        chat_id,

        "🔒 <b>دسترسی بسته است</b>\n\n"
        "برای استفاده از ربات ابتدا عضو کانال شوید.\n\n"
        "بعد از عضویت روی «بررسی عضویت» بزنید.",

        reply_markup=channel_keyboard()
    )

    return False


# =========================================================
# MESSAGE NAVIGATION
# =========================================================

def render_message(chat_id, text, reply_markup=None, message_id=None):
    """Edit inline-menu messages in place; only send a new message as fallback."""
    if message_id:
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=reply_markup)


# =========================================================
# MAIN MENU
# =========================================================

def main_menu(user_id):

    keyboard = types.InlineKeyboardMarkup(
        row_width=2
    )

    keyboard.add(

        types.InlineKeyboardButton(
            "🎮 کاستوم‌ها",
            callback_data="customs"
        ),

        types.InlineKeyboardButton(
            "💰 کیف پول",
            callback_data="wallet"
        ),

        types.InlineKeyboardButton(
            "👤 پروفایل",
            callback_data="profile"
        ),

        types.InlineKeyboardButton(
            "🏆 لیدربورد",
            callback_data="leaderboard"
        ),

        types.InlineKeyboardButton(
            "🎁 پاداش روزانه",
            callback_data="daily"
        ),

        types.InlineKeyboardButton(
            "🎟️ کد جایزه",
            callback_data="promo"
        ),

        types.InlineKeyboardButton(
            "🎁 دعوت دوستان",
            callback_data="referral"
        ),

        types.InlineKeyboardButton(
            "👥 دوستان",
            callback_data="friends"
        ),

        types.InlineKeyboardButton(
            "🎯 مأموریت‌های روزانه",
            callback_data="missions"
        ),

        types.InlineKeyboardButton(
            "🎉 کمپین‌ها",
            callback_data="campaigns"
        ),

        types.InlineKeyboardButton(
            "🔔 اعلان‌ها",
            callback_data="notifications"
        ),

        types.InlineKeyboardButton(
            "📜 قوانین",
            callback_data="rules"
        ),

        types.InlineKeyboardButton(
            "📖 راهنما",
            callback_data="help"
        ),

        types.InlineKeyboardButton(
            "🆘 پشتیبانی",
            callback_data="support"
        )
    )

    if is_admin(user_id):

        keyboard.add(
            types.InlineKeyboardButton(
                "👑 پنل مدیریت",
                callback_data="admin"
            )
        )

    return keyboard


def send_home(
    chat_id,
    user_id,
    text=None
):

    if text is None:

        text = (
            "🎮 <b>کاستوم کالاف</b>\n\n"
            "به ربات کاستوم‌های بتل‌رویال خوش آمدید."
        )

    bot.send_message(
        chat_id,
        text,
        reply_markup=main_menu(user_id)
    )


# =========================================================
# CUSTOM HELPERS
# =========================================================

def custom_type_text(custom_type):

    if custom_type == "free":
        return "🆓 رایگان"

    return "🪙 سکه‌ای"


def status_text(status):

    return {

        "open":
            "🟢 ثبت‌نام باز",

        "locked":
            "🔒 ثبت‌نام بسته",

        "started":
            "🎮 در حال برگزاری",

        "finished":
            "🏁 پایان یافته",

        "cancelled":
            "❌ لغو شده"

    }.get(
        status,
        status
    )


def participant_count(custom_id):

    conn = db()

    result = conn.execute(
        """
        SELECT COUNT(*)
        FROM participants

        WHERE custom_id=?
        AND left_at IS NULL
        """,
        (custom_id,)
    ).fetchone()[0]

    conn.close()

    return result


def team_count(
    custom_id,
    team_no
):

    conn = db()

    result = conn.execute(
        """
        SELECT COUNT(*)
        FROM participants

        WHERE custom_id=?
        AND team_no=?
        AND left_at IS NULL
        """,
        (
            custom_id,
            team_no
        )
    ).fetchone()[0]

    conn.close()

    return result


def user_participation(
    custom_id,
    user_id
):

    conn = db()

    result = conn.execute(
        """
        SELECT *
        FROM participants

        WHERE custom_id=?
        AND user_id=?
        """,
        (
            custom_id,
            user_id
        )
    ).fetchone()

    conn.close()

    return result


# =========================================================
# CUSTOM CARD
# =========================================================

def custom_card(row, user_id):

    count = participant_count(
        row["id"]
    )

    joined = user_participation(
        row["id"],
        user_id
    )

    lines = [

        f"🎮 <b>{row['title']}</b>",

        "",

        f"📅 تاریخ: <code>{row['date']}</code>",

        f"⏰ ساعت: <code>{row['time']}</code>",

        f"🎯 نوع: {custom_type_text(row['custom_type'])}",

        f"👥 بازیکنان: <b>{count}/{MAX_PLAYERS}</b>",

        f"🏆 تعداد تیم برنده: "
        f"<b>{row['winners_count']}</b>",

        f"🔒 بسته شدن ثبت‌نام: "
        f"<b>{row['lock_minutes']} دقیقه قبل</b>",

        f"📌 وضعیت: "
        f"<b>{status_text(row['status'])}</b>"
    ]

    if row["custom_type"] == "coin":

        lines.append(
            f"🪙 ورودیه: "
            f"<b>{row['entry_fee']}</b> سکه"
        )

        if row["winners_count"] >= 1:

            lines.append(
                f"🥇 جایزه رتبه ۱: "
                f"<b>{row['prize1']}</b> سکه"
            )

        if row["winners_count"] >= 2:

            lines.append(
                f"🥈 جایزه رتبه ۲: "
                f"<b>{row['prize2']}</b> سکه"
            )

        if row["winners_count"] >= 3:

            lines.append(
                f"🥉 جایزه رتبه ۳: "
                f"<b>{row['prize3']}</b> سکه"
            )

    else:

        lines.append(
            f"🎁 جایزه هر عضو تیم اول: "
            f"<b>{row['prize1']}</b> سکه"
        )

    if joined and joined["left_at"] is None:

        lines.append(
            f"\n✅ شما در <b>تیم "
            f"{joined['team_no']}</b> هستید."
        )

    return "\n".join(lines)


# =========================================================
# CUSTOM LIST
# =========================================================

def show_customs(
    chat_id,
    user_id,
    message_id=None
):

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM customs

        WHERE status IN(
            'open',
            'locked',
            'started'
        )

        ORDER BY date,time,id
        """
    ).fetchall()

    conn.close()

    keyboard = types.InlineKeyboardMarkup(
        row_width=1
    )

    if not rows:

        text = (
            "🎮 <b>کاستوم‌ها</b>\n\n"
            "فعلاً کاستوم فعالی وجود ندارد."
        )

    else:

        text = (
            "🎮 <b>کاستوم‌های فعال</b>\n\n"
            "یک کاستوم را انتخاب کنید:"
        )

        for row in rows:

            count = participant_count(
                row["id"]
            )

            keyboard.add(
                types.InlineKeyboardButton(
                    f"🎮 {row['title']} | "
                    f"{row['date']} "
                    f"{row['time']} | "
                    f"{count}/100",

                    callback_data=
                    f"custom:{row['id']}"
                )
            )

    keyboard.add(
        types.InlineKeyboardButton(
            "🔙 منوی اصلی",
            callback_data="home"
        )
    )

    if message_id:

        try:

            bot.edit_message_text(
                text,
                chat_id,
                message_id,
                reply_markup=keyboard
            )

        except Exception:

            bot.send_message(
                chat_id,
                text,
                reply_markup=keyboard
            )

    else:

        bot.send_message(
            chat_id,
            text,
            reply_markup=keyboard
        )


# =========================================================
# CUSTOM DETAIL
# =========================================================

def custom_detail(
    chat_id,
    user_id,
    custom_id,
    message_id=None
):

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM customs
        WHERE id=?
        """,
        (custom_id,)
    ).fetchone()

    conn.close()

    if not row:

        text = "❌ کاستوم پیدا نشد."

        keyboard = types.InlineKeyboardMarkup()

        keyboard.add(
            types.InlineKeyboardButton(
                "🔙 کاستوم‌ها",
                callback_data="customs"
            )
        )

    else:

        text = custom_card(
            row,
            user_id
        )

        keyboard = types.InlineKeyboardMarkup(
            row_width=2
        )

        joined = user_participation(
            custom_id,
            user_id
        )

        if (
            joined
            and joined["left_at"] is None
        ):

            if row["status"] == "open":

                keyboard.add(

                    types.InlineKeyboardButton(
                        "🔄 تغییر تیم",
                        callback_data=
                        f"teams:{custom_id}:change"
                    ),

                    types.InlineKeyboardButton(
                        "🚪 خروج",
                        callback_data=
                        f"leave:{custom_id}"
                    )
                )

            if row["status"] in (
                "locked",
                "started"
            ):

                if (
                    row["room_id"]
                    or row["room_password"]
                ):

                    keyboard.add(
                        types.InlineKeyboardButton(
                            "🔑 اطلاعات روم",
                            callback_data=
                            f"room:{custom_id}"
                        )
                    )

            keyboard.add(
                types.InlineKeyboardButton(
                    "👥 مشاهده تیم من",
                    callback_data=
                    f"teamview:{custom_id}:"
                    f"{joined['team_no']}"
                )
            )

            keyboard.add(
                types.InlineKeyboardButton(
                    "👥 دعوت دوستان به تیم",
                    callback_data=f"friend:invite:{custom_id}"
                )
            )

        elif row["status"] == "open":

            keyboard.add(
                types.InlineKeyboardButton(
                    "🎮 شرکت در کاستوم",
                    callback_data=
                    f"teams:{custom_id}:join"
                )
            )

        keyboard.add(
            types.InlineKeyboardButton(
                "👥 تیم‌ها",
                callback_data=
                f"teamsview:{custom_id}"
            )
        )

        keyboard.add(
            types.InlineKeyboardButton(
                "🔙 کاستوم‌ها",
                callback_data="customs"
            )
        )

    if message_id:

        try:

            bot.edit_message_text(
                text,
                chat_id,
                message_id,
                reply_markup=keyboard
            )

        except Exception:

            bot.send_message(
                chat_id,
                text,
                reply_markup=keyboard
            )

    else:

        bot.send_message(
            chat_id,
            text,
            reply_markup=keyboard
        )


# =========================================================
# TEAM KEYBOARD
# =========================================================

def teams_keyboard(
    custom_id,
    action="join"
):

    keyboard = types.InlineKeyboardMarkup(
        row_width=5
    )

    buttons = []

    for team_no in range(
        1,
        MAX_TEAMS + 1
    ):

        count = team_count(
            custom_id,
            team_no
        )

        if action in (
            "join",
            "change"
        ) and count >= TEAM_CAPACITY:

            continue

        buttons.append(
            types.InlineKeyboardButton(
                f"تیم {team_no} "
                f"({count}/4)",

                callback_data=
                f"teamselect:"
                f"{custom_id}:"
                f"{team_no}:"
                f"{action}"
            )
        )

    for i in range(
        0,
        len(buttons),
        5
    ):

        keyboard.row(
            *buttons[i:i + 5]
        )

    keyboard.add(
        types.InlineKeyboardButton(
            "🔙 بازگشت",
            callback_data=
            f"custom:{custom_id}"
        )
    )

    return keyboard


# =========================================================
# TEAM VIEW
# =========================================================

def teams_view(
    chat_id,
    user_id,
    custom_id,
    message_id=None
):

    conn = db()

    row = conn.execute(
        """
        SELECT title
        FROM customs
        WHERE id=?
        """,
        (custom_id,)
    ).fetchone()

    conn.close()

    if not row:
        return

    keyboard = types.InlineKeyboardMarkup(
        row_width=2
    )

    for team_no in range(
        1,
        MAX_TEAMS + 1
    ):

        count = team_count(
            custom_id,
            team_no
        )

        if count > 0:

            keyboard.add(
                types.InlineKeyboardButton(
                    f"تیم {team_no} ({count}/4)",

                    callback_data=
                    f"teamview:"
                    f"{custom_id}:"
                    f"{team_no}"
                )
            )

    keyboard.add(
        types.InlineKeyboardButton(
            "🔙 بازگشت",
            callback_data=
            f"custom:{custom_id}"
        )
    )

    text = (
        f"👥 <b>تیم‌های {row['title']}</b>\n\n"
        "برای مشاهده اعضای هر تیم انتخاب کنید:"
    )

    if message_id:

        try:

            bot.edit_message_text(
                text,
                chat_id,
                message_id,
                reply_markup=keyboard
            )

        except Exception:

            bot.send_message(
                chat_id,
                text,
                reply_markup=keyboard
            )

    else:

        bot.send_message(
            chat_id,
            text,
            reply_markup=keyboard
        )


def show_team(
    chat_id,
    custom_id,
    team_no,
    message_id=None
):

    conn = db()

    members = conn.execute(
        """
        SELECT
            u.cod_name,
            u.first_name

        FROM participants p

        JOIN users u
            ON u.id=p.user_id

        WHERE p.custom_id=?
        AND p.team_no=?
        AND p.left_at IS NULL

        ORDER BY p.id
        """,
        (
            custom_id,
            team_no
        )
    ).fetchall()

    custom = conn.execute(
        """
        SELECT title
        FROM customs
        WHERE id=?
        """,
        (custom_id,)
    ).fetchone()

    conn.close()

    names = "\n".join(
        f"• {member['cod_name'] or member['first_name'] or 'بدون نام'}"
        for member in members
    )

    text = (
        f"👥 <b>تیم {team_no}</b>\n"
        f"🎮 {custom['title'] if custom else ''}\n\n"
        f"{names or 'این تیم خالی است.'}"
    )

    keyboard = types.InlineKeyboardMarkup()

    keyboard.add(
        types.InlineKeyboardButton(
            "🔙 بازگشت",
            callback_data=
            f"teamsview:{custom_id}"
        )
    )

    render_message(chat_id, text, keyboard, message_id)


# =========================================================
# WALLET
# =========================================================

def show_wallet(
    chat_id,
    user_id,
    message_id=None
):

    user = get_user(user_id)

    conn = db()

    transactions = conn.execute(
        """
        SELECT
            amount,
            kind,
            description,
            created_at

        FROM transactions

        WHERE user_id=?

        ORDER BY id DESC

        LIMIT 8
        """,
        (user_id,)
    ).fetchall()

    conn.close()

    text = (
        "💰 <b>کیف پول</b>\n\n"
        f"🪙 موجودی سکه: "
        f"<b>{user['coins']}</b>\n\n"
        f"💵 موجودی نقدی: "
        f"<b>{user['cash_balance']}</b>\n\n"
    )

    if transactions:

        text += "📜 <b>آخرین تراکنش‌ها</b>\n\n"

        for transaction in transactions:

            sign = (
                "+"
                if transaction["amount"] >= 0
                else ""
            )

            text += (
                f"{sign}"
                f"{transaction['amount']} 🪙 — "
                f"{transaction['description'] or transaction['kind']}\n"
            )

    else:

        text += (
            "هنوز تراکنشی ثبت نشده است."
        )

    keyboard = types.InlineKeyboardMarkup()

    keyboard.add(
        types.InlineKeyboardButton(
            "🔙 منوی اصلی",
            callback_data="home"
        )
    )

    render_message(chat_id, text, keyboard, message_id)


# =========================================================
# PROFILE
# =========================================================

def show_leaderboard(chat_id, user_id, period="all", message_id=None):

    conn = db()
    cutoff = (datetime.now(TZ) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    if period == "weekly":
        params = (cutoff, cutoff, cutoff)
        query = """
        WITH played AS (
            SELECT p.user_id, COUNT(DISTINCT p.custom_id) AS played
            FROM participants p
            JOIN customs c ON c.id=p.custom_id
            WHERE p.left_at IS NULL
              AND c.status='finished'
              AND p.joined_at >= ?
            GROUP BY p.user_id
        ),
        results AS (
            SELECT p.user_id,
                   COUNT(DISTINCT CASE WHEN w.place=1 THEN w.custom_id END) AS wins,
                   COUNT(DISTINCT CASE WHEN w.place BETWEEN 1 AND 3 THEN w.custom_id END) AS top3
            FROM winners w
            JOIN participants p ON p.custom_id=w.custom_id AND p.team_no=w.team_no
            WHERE w.created_at >= ?
            GROUP BY p.user_id
        ),
        prizes AS (
            SELECT user_id, COALESCE(SUM(amount),0) AS prizes
            FROM transactions
            WHERE kind='prize' AND amount>0 AND created_at >= ?
            GROUP BY user_id
        ),
        stats AS (
            SELECT u.id, u.cod_name, u.rank, u.rank_points,
                   COALESCE(r.wins,0) AS wins,
                   COALESCE(r.top3,0) AS top3,
                   COALESCE(pr.prizes,0) AS prizes,
                   COALESCE(pl.played,0) AS played
            FROM users u
            LEFT JOIN played pl ON pl.user_id=u.id
            LEFT JOIN results r ON r.user_id=u.id
            LEFT JOIN prizes pr ON pr.user_id=u.id
            WHERE u.banned=0
        )
        SELECT * FROM stats
        WHERE played > 0 OR wins > 0 OR prizes > 0
        ORDER BY wins DESC, prizes DESC, played DESC, id ASC
        LIMIT 10
        """
        rank_query = """
        WITH played AS (
            SELECT p.user_id, COUNT(DISTINCT p.custom_id) AS played
            FROM participants p JOIN customs c ON c.id=p.custom_id
            WHERE p.left_at IS NULL AND c.status='finished' AND p.joined_at >= ?
            GROUP BY p.user_id
        ), results AS (
            SELECT p.user_id,
                   COUNT(DISTINCT CASE WHEN w.place=1 THEN w.custom_id END) AS wins
            FROM winners w JOIN participants p ON p.custom_id=w.custom_id AND p.team_no=w.team_no
            WHERE w.created_at >= ?
            GROUP BY p.user_id
        ), prizes AS (
            SELECT user_id, COALESCE(SUM(amount),0) AS prizes
            FROM transactions WHERE kind='prize' AND amount>0 AND created_at >= ?
            GROUP BY user_id
        ), stats AS (
            SELECT u.id, COALESCE(r.wins,0) wins, COALESCE(pr.prizes,0) prizes, COALESCE(pl.played,0) played
            FROM users u LEFT JOIN played pl ON pl.user_id=u.id LEFT JOIN results r ON r.user_id=u.id LEFT JOIN prizes pr ON pr.user_id=u.id
            WHERE u.banned=0
        ), ranked AS (
            SELECT id, ROW_NUMBER() OVER (ORDER BY wins DESC, prizes DESC, played DESC, id ASC) AS rank
            FROM stats
            WHERE played > 0 OR wins > 0 OR prizes > 0
        )
        SELECT rank FROM ranked WHERE id=?
        """
        title = "🏆 <b>لیدربورد هفتگی</b>\n<i>۷ روز اخیر</i>"
    else:
        params = ()
        query = """
        WITH played AS (
            SELECT p.user_id, COUNT(DISTINCT p.custom_id) AS played
            FROM participants p JOIN customs c ON c.id=p.custom_id
            WHERE p.left_at IS NULL AND c.status='finished'
            GROUP BY p.user_id
        ), results AS (
            SELECT p.user_id,
                   COUNT(DISTINCT CASE WHEN w.place=1 THEN w.custom_id END) AS wins,
                   COUNT(DISTINCT CASE WHEN w.place BETWEEN 1 AND 3 THEN w.custom_id END) AS top3
            FROM winners w JOIN participants p ON p.custom_id=w.custom_id AND p.team_no=w.team_no
            GROUP BY p.user_id
        ), prizes AS (
            SELECT user_id, COALESCE(SUM(amount),0) AS prizes
            FROM transactions WHERE kind='prize' AND amount>0
            GROUP BY user_id
        ), stats AS (
            SELECT u.id, u.cod_name, u.rank, u.rank_points,
                   COALESCE(r.wins,0) AS wins,
                   COALESCE(r.top3,0) AS top3,
                   COALESCE(pr.prizes,0) AS prizes,
                   COALESCE(pl.played,0) AS played
            FROM users u
            LEFT JOIN played pl ON pl.user_id=u.id
            LEFT JOIN results r ON r.user_id=u.id
            LEFT JOIN prizes pr ON pr.user_id=u.id
            WHERE u.banned=0
        )
        SELECT * FROM stats
        WHERE played > 0 OR wins > 0 OR prizes > 0
        ORDER BY wins DESC, prizes DESC, played DESC, id ASC
        LIMIT 10
        """
        rank_query = """
        WITH played AS (
            SELECT p.user_id, COUNT(DISTINCT p.custom_id) AS played
            FROM participants p JOIN customs c ON c.id=p.custom_id
            WHERE p.left_at IS NULL AND c.status='finished'
            GROUP BY p.user_id
        ), results AS (
            SELECT p.user_id, COUNT(DISTINCT CASE WHEN w.place=1 THEN w.custom_id END) AS wins
            FROM winners w JOIN participants p ON p.custom_id=w.custom_id AND p.team_no=w.team_no
            GROUP BY p.user_id
        ), prizes AS (
            SELECT user_id, COALESCE(SUM(amount),0) AS prizes
            FROM transactions WHERE kind='prize' AND amount>0
            GROUP BY user_id
        ), stats AS (
            SELECT u.id, COALESCE(r.wins,0) wins, COALESCE(pr.prizes,0) prizes, COALESCE(pl.played,0) played
            FROM users u LEFT JOIN played pl ON pl.user_id=u.id LEFT JOIN results r ON r.user_id=u.id LEFT JOIN prizes pr ON pr.user_id=u.id
            WHERE u.banned=0
        ), ranked AS (
            SELECT id, ROW_NUMBER() OVER (ORDER BY wins DESC, prizes DESC, played DESC, id ASC) AS rank
            FROM stats
            WHERE played > 0 OR wins > 0 OR prizes > 0
        )
        SELECT rank FROM ranked WHERE id=?
        """
        title = "🏆 <b>لیدربورد کلی</b>\n<i>از ابتدای فعالیت ربات</i>"

    rows = conn.execute(query, params).fetchall()
    rank_params = params + (user_id,)
    my_rank = conn.execute(rank_query, rank_params).fetchone()
    conn.close()

    text = title + "\n\n"
    if not rows:
        text += "هنوز آماری برای نمایش وجود ندارد.\n"
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, row in enumerate(rows, start=1):
            name = row["cod_name"] or "بدون نام"
            medal = medals[i - 1] if i <= 3 else f"{i}."
            text += (
                f"{medal} {row['rank']} <b>{name}</b>\n"
                f"   ⭐ {row['rank_points']} امتیاز | 🏆 برد: {row['wins']} | 🥉 تاپ ۳: {row['top3']} | "
                f"🎮 بازی: {row['played']} | 🪙 جایزه: {row['prizes']}\n"
            )

    if my_rank:
        text += f"\n📍 رتبه شما: <b>{my_rank['rank']}</b>"
    else:
        text += "\n📍 رتبه شما: <b>هنوز ثبت نشده</b>"

    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("📅 هفتگی" + (" ✓" if period == "weekly" else ""), callback_data="leaderboard:weekly"),
        types.InlineKeyboardButton("🏆 کلی" + (" ✓" if period == "all" else ""), callback_data="leaderboard:all")
    )
    keyboard.add(types.InlineKeyboardButton("🔙 منوی اصلی", callback_data="home"))

    if message_id:
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=keyboard)
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=keyboard)


def profile_stats(chat_id, user_id, message_id=None):
    user = get_user(user_id)
    if not user:
        return
    conn = db()
    played = conn.execute("""SELECT COUNT(DISTINCT p.custom_id) FROM participants p JOIN customs c ON c.id=p.custom_id WHERE p.user_id=? AND p.left_at IS NULL AND c.status='finished'""", (user_id,)).fetchone()[0]
    active = conn.execute("""SELECT COUNT(DISTINCT p.custom_id) FROM participants p JOIN customs c ON c.id=p.custom_id WHERE p.user_id=? AND p.left_at IS NULL AND c.status IN ('open','locked','started')""", (user_id,)).fetchone()[0]
    wins = conn.execute("""SELECT COUNT(DISTINCT w.custom_id) FROM winners w JOIN participants p ON p.custom_id=w.custom_id AND p.team_no=w.team_no WHERE p.user_id=? AND w.place=1""", (user_id,)).fetchone()[0]
    top3 = conn.execute("""SELECT COUNT(DISTINCT w.custom_id) FROM winners w JOIN participants p ON p.custom_id=w.custom_id AND p.team_no=w.team_no WHERE p.user_id=? AND w.place BETWEEN 1 AND 3""", (user_id,)).fetchone()[0]
    prizes = conn.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE user_id=? AND kind='prize' AND amount>0", (user_id,)).fetchone()[0]
    invited = conn.execute("SELECT COUNT(*) FROM referrals WHERE inviter_id=?", (user_id,)).fetchone()[0]
    rank_row = conn.execute("SELECT rank_points, rank, daily_streak FROM users WHERE id=?", (user_id,)).fetchone()
    rank_points = int(rank_row['rank_points'] or 0) if rank_row else 0
    rank_name = rank_row['rank'] if rank_row else rank_from_points(rank_points)
    streak = int(rank_row['daily_streak'] or 0) if rank_row else 0
    cash = int(user['cash_balance'] or 0)
    joined = user['joined_at'] or '-'
    conn.close()
    win_rate = (wins / played * 100) if played else 0
    status = '🚫 مسدود' if int(user['banned'] or 0) else '✅ فعال'
    text = ("📊 <b>آمار کامل بازیکن</b>\n\n"
            f"🔥 رنک: <b>{rank_name}</b> | ⭐ امتیاز: <b>{rank_points}</b>\n"
            f"🎮 بازی‌های تمام‌شده: <b>{played}</b>\n"
            f"🏆 برد: <b>{wins}</b>\n"
            f"🥉 تاپ ۳: <b>{top3}</b>\n"
            f"📈 درصد برد: <b>{win_rate:.1f}%</b>\n"
            f"🟢 بازی فعال: <b>{active}</b>\n"
            f"🪙 جوایز دریافتی: <b>{prizes}</b>\n"
            f"🎁 دعوت موفق: <b>{invited}</b>\n"
            f"🔥 استریک روزانه: <b>{streak}</b> روز\n\n"
            f"💰 موجودی سکه: <b>{user['coins']}</b>\n"
            f"💵 موجودی نقدی: <b>{cash}</b>\n"
            f"🛡️ وضعیت حساب: <b>{status}</b>\n"
            f"📅 عضویت: <b>{joined}</b>")
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("🏅 دستاوردها", callback_data="profile:achievements"), types.InlineKeyboardButton("🎮 آخرین بازی‌ها", callback_data="profile:recent"))
    kb.add(types.InlineKeyboardButton("🔙 پروفایل", callback_data="profile"), types.InlineKeyboardButton("🏆 لیدربورد", callback_data="leaderboard"))
    if message_id:
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=kb)
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=kb)


def profile_achievements(chat_id, user_id, message_id=None):
    conn = db()
    played = conn.execute("SELECT COUNT(DISTINCT custom_id) FROM participants WHERE user_id=? AND left_at IS NULL", (user_id,)).fetchone()[0]
    wins = conn.execute("SELECT COUNT(DISTINCT w.custom_id) FROM winners w JOIN participants p ON p.custom_id=w.custom_id AND p.team_no=w.team_no WHERE p.user_id=? AND w.place=1", (user_id,)).fetchone()[0]
    top3 = conn.execute("SELECT COUNT(DISTINCT w.custom_id) FROM winners w JOIN participants p ON p.custom_id=w.custom_id AND p.team_no=w.team_no WHERE p.user_id=? AND w.place BETWEEN 1 AND 3", (user_id,)).fetchone()[0]
    invited = conn.execute("SELECT COUNT(*) FROM referrals WHERE inviter_id=?", (user_id,)).fetchone()[0]
    streak = conn.execute("SELECT daily_streak FROM users WHERE id=?", (user_id,)).fetchone()[0]
    conn.close()
    checks = [
        ("🎮 اولین ورود", played >= 1),
        ("🔥 ۱۰ بازی", played >= 10),
        ("🏆 اولین برد", wins >= 1),
        ("👑 ۵ برد", wins >= 5),
        ("🥉 ۱۰ تاپ ۳", top3 >= 10),
        ("🎁 اولین دعوت", invited >= 1),
        ("👥 ۵ دعوت موفق", invited >= 5),
        ("🔥 استریک ۷ روزه", int(streak or 0) >= 7),
    ]
    unlocked = sum(ok for _, ok in checks)
    text = f"🏅 <b>دستاوردها</b>\n\nآنلاک‌شده: <b>{unlocked}/{len(checks)}</b>\n\n"
    for name, ok in checks:
        text += f"{'✅' if ok else '🔒'} {name}\n"
    kb = types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("📊 آمار کامل", callback_data="profile:stats"), types.InlineKeyboardButton("🔙 پروفایل", callback_data="profile"))
    if message_id:
        try: bot.edit_message_text(text, chat_id, message_id, reply_markup=kb); return
        except Exception: pass
    bot.send_message(chat_id, text, reply_markup=kb)


def profile_recent(chat_id, user_id, message_id=None):
    conn = db()
    rows = conn.execute("""SELECT c.title,c.date,c.time,c.status,p.team_no,COALESCE(rh.place,0) place FROM participants p JOIN customs c ON c.id=p.custom_id LEFT JOIN result_history rh ON rh.custom_id=p.custom_id AND rh.user_id=p.user_id WHERE p.user_id=? AND p.left_at IS NULL ORDER BY p.id DESC LIMIT 8""", (user_id,)).fetchall()
    conn.close()
    text = "🎮 <b>آخرین بازی‌ها</b>\n\n"
    if not rows: text += "هنوز بازی‌ای ثبت نشده است."
    else:
        for r in rows:
            result = f"🏆 رتبه {r['place']}" if r['place'] else ('🟢 فعال' if r['status'] in ('open','locked','started') else '➖ بدون رتبه')
            text += f"• <b>{r['title']}</b> | تیم {r['team_no']} | {result}\n  📅 {r['date']} {r['time']}\n"
    kb = types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("📊 آمار کامل", callback_data="profile:stats"), types.InlineKeyboardButton("🔙 پروفایل", callback_data="profile"))
    if message_id:
        try: bot.edit_message_text(text, chat_id, message_id, reply_markup=kb); return
        except Exception: pass
    bot.send_message(chat_id, text, reply_markup=kb)


def show_profile(chat_id, user_id, message_id=None):
    user = get_user(user_id)
    if not user:
        bot.send_message(chat_id, "❌ اطلاعات کاربر پیدا نشد.")
        return
    conn = db()
    played = conn.execute("SELECT COUNT(DISTINCT p.custom_id) FROM participants p JOIN customs c ON c.id=p.custom_id WHERE p.user_id=? AND p.left_at IS NULL AND c.status='finished'", (user_id,)).fetchone()[0]
    active = conn.execute("SELECT COUNT(DISTINCT p.custom_id) FROM participants p JOIN customs c ON c.id=p.custom_id WHERE p.user_id=? AND p.left_at IS NULL AND c.status IN ('open','locked','started')", (user_id,)).fetchone()[0]
    wins = conn.execute("SELECT COUNT(DISTINCT w.custom_id) FROM winners w JOIN participants p ON p.custom_id=w.custom_id AND p.team_no=w.team_no WHERE p.user_id=? AND w.place=1", (user_id,)).fetchone()[0]
    top3 = conn.execute("SELECT COUNT(DISTINCT w.custom_id) FROM winners w JOIN participants p ON p.custom_id=w.custom_id AND p.team_no=w.team_no WHERE p.user_id=? AND w.place BETWEEN 1 AND 3", (user_id,)).fetchone()[0]
    prizes = conn.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE user_id=? AND kind='prize' AND amount>0", (user_id,)).fetchone()[0]
    invited = conn.execute("SELECT COUNT(*) FROM referrals WHERE inviter_id=?", (user_id,)).fetchone()[0]
    rank_row = conn.execute("SELECT rank_points, rank, daily_streak FROM users WHERE id=?", (user_id,)).fetchone()
    rank_points = int(rank_row['rank_points'] or 0) if rank_row else 0
    rank_name = rank_row['rank'] if rank_row else rank_from_points(rank_points)
    streak = int(rank_row['daily_streak'] or 0) if rank_row else 0
    conn.close()
    win_rate = (wins / played * 100) if played else 0
    text = ("👤 <b>پروفایل بازیکن</b>\n\n"
            f"🎮 نام کالاف: <b>{user['cod_name'] or 'ثبت نشده'}</b>\n"
            f"🔥 رنک: <b>{rank_name}</b> | ⭐ {rank_points}\n"
            f"🪙 موجودی: <b>{user['coins']}</b>\n\n"
            f"🎮 بازی: <b>{played}</b> | 🟢 فعال: <b>{active}</b>\n"
            f"🏆 برد: <b>{wins}</b> | 🥉 تاپ ۳: <b>{top3}</b>\n"
            f"📈 درصد برد: <b>{win_rate:.1f}%</b>\n"
            f"🪙 مجموع جوایز: <b>{prizes}</b>\n"
            f"🎁 دعوت موفق: <b>{invited}</b>\n"
            f"🔥 استریک: <b>{streak}</b> روز")
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("📊 آمار کامل", callback_data="profile:stats"), types.InlineKeyboardButton("🏅 دستاوردها", callback_data="profile:achievements"))
    kb.add(types.InlineKeyboardButton("🎮 آخرین بازی‌ها", callback_data="profile:recent"))
    kb.add(types.InlineKeyboardButton("🏆 لیدربورد", callback_data="leaderboard"), types.InlineKeyboardButton("✏️ تغییر نام کالاف", callback_data="change_cod"))
    kb.add(types.InlineKeyboardButton("🔙 منوی اصلی", callback_data="home"))
    if message_id:
        try: bot.edit_message_text(text, chat_id, message_id, reply_markup=kb); return
        except Exception: pass
    bot.send_message(chat_id, text, reply_markup=kb)

# =========================================================
# DAILY REWARD + STREAK
# =========================================================

DAILY_REWARD_SCHEDULE = {1: 25, 2: 30, 3: 40, 4: 50, 5: 65, 6: 80, 7: 120}

def daily_reward_for_streak(streak):
    return DAILY_REWARD_SCHEDULE[((int(streak) - 1) % 7) + 1]

def daily_status(user_id):
    conn = db()
    row = conn.execute("SELECT daily_last_claim, daily_streak FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if not row: return None, 0, True
    last, streak = row["daily_last_claim"], int(row["daily_streak"] or 0)
    today = datetime.now(TZ).date()
    if last == today.strftime("%Y-%m-%d"): return last, streak, False
    return last, streak, True

def show_daily_reward(chat_id, user_id, message_id=None):
    last, streak, can_claim = daily_status(user_id)
    user = get_user(user_id); coins = int(user["coins"] or 0)
    kb = types.InlineKeyboardMarkup(row_width=1)
    if can_claim:
        today = datetime.now(TZ).date(); next_streak = 1
        if last:
            try:
                if datetime.strptime(str(last), "%Y-%m-%d").date() == today - timedelta(days=1): next_streak = streak + 1
            except ValueError: pass
        reward = daily_reward_for_streak(next_streak); day_no = ((next_streak-1)%7)+1
        text = (f"🎁 <b>پاداش روزانه</b>\n\n🔥 استریک فعلی: <b>{streak}</b> روز\n"
                f"📅 روز بعدی: <b>{day_no}/7</b>\n🪙 جایزه: <b>+{reward} کوین</b>\n"
                f"💰 موجودی: <b>{coins} کوین</b>\n\nبا دریافت امروز، استریک شما ثبت می‌شود.")
        kb.add(types.InlineKeyboardButton("🎁 دریافت پاداش امروز", callback_data="daily:claim"))
    else:
        text = (f"✅ <b>پاداش امروز را دریافت کرده‌اید</b>\n\n🔥 استریک: <b>{streak}</b> روز\n"
                f"💰 موجودی: <b>{coins} کوین</b>\n\nفردا دوباره برگردید.")
    kb.add(types.InlineKeyboardButton("🔙 منوی اصلی", callback_data="home"))
    if message_id:
        try: bot.edit_message_text(text, chat_id, message_id, reply_markup=kb); return
        except Exception: pass
    bot.send_message(chat_id, text, reply_markup=kb)

def claim_daily_reward(user_id):
    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT daily_last_claim, daily_streak FROM users WHERE id=?", (user_id,)).fetchone()
        if not row: conn.rollback(); return None, "❌ کاربر پیدا نشد."
        today = datetime.now(TZ).date(); today_str = today.strftime("%Y-%m-%d"); last=row["daily_last_claim"]; old=int(row["daily_streak"] or 0)
        if last == today_str: conn.rollback(); return None, "⚠️ پاداش امروز را قبلاً دریافت کرده‌اید."
        streak=old+1
        if last:
            try:
                if datetime.strptime(str(last), "%Y-%m-%d").date() != today-timedelta(days=1): streak=1
            except ValueError: streak=1
        reward=daily_reward_for_streak(streak); day_no=((streak-1)%7)+1
        conn.execute("UPDATE users SET coins=coins+?, daily_last_claim=?, daily_streak=? WHERE id=?", (reward,today_str,streak,user_id))
        conn.execute("INSERT INTO transactions(user_id,amount,balance_type,kind,description,custom_id,admin_id,created_at) VALUES(?,?,?,?,?,?,?,?)", (user_id,reward,"coins","daily_reward",f"پاداش روزانه - روز {day_no}/7 - استریک {streak}",None,None,now_str()))
        conn.commit()
        return reward, (f"🎉 <b>پاداش دریافت شد!</b>\n\n🔥 استریک: <b>{streak} روز</b>\n🪙 جایزه: <b>+{reward} کوین</b>" + ("\n\n🎊 جایزه ویژه روز هفتم بود!" if day_no==7 else "") + "\n\nفردا برای حفظ استریک برگرد.")
    finally: conn.close()

# =========================================================
# REFERRAL
# =========================================================

def show_referral(
    chat_id,
    user_id,
    message_id=None
):

    me = bot.get_me()

    link = (
        f"https://t.me/"
        f"{me.username}"
        f"?start=ref_{user_id}"
    )

    conn = db()

    count = conn.execute(
        """
        SELECT COUNT(*)
        FROM referrals

        WHERE inviter_id=?
        """,
        (user_id,)
    ).fetchone()[0]

    conn.close()

    text = (
        "🎁 <b>دعوت دوستان</b>\n\n"

        "🎁 ورود مستقیم: "
        "<b>50</b> سکه\n"

        "🎁 ورود با دعوت: "
        "<b>100</b> سکه\n"

        "💰 پاداش دعوت‌کننده: "
        "<b>50</b> سکه\n\n"

        f"👥 تعداد دعوت‌ها: "
        f"<b>{count}</b>\n\n"

        f"🔗 <code>{link}</code>"
    )

    keyboard = types.InlineKeyboardMarkup()

    share_url = (
        "https://t.me/share/url?"
        f"url={quote(link)}&"
        f"text={quote('🎮 بیا کاستوم کالاف بازی کنیم!')}"
    )

    keyboard.add(
        types.InlineKeyboardButton(
            "📤 ارسال لینک دعوت",
            url=share_url
        )
    )

    keyboard.add(
        types.InlineKeyboardButton(
            "🔙 منوی اصلی",
            callback_data="home"
        )
    )

    render_message(chat_id, text, keyboard, message_id)


# =========================================================
# RULES / HELP / SUPPORT
# =========================================================

def show_rules(chat_id, message_id=None):

    text = (
        "📜 <b>قوانین</b>\n\n"

        "1️⃣ ثبت‌نام فقط با نام واقعی کالاف انجام می‌شود.\n\n"

        "2️⃣ هر تیم حداکثر 4 نفر دارد.\n\n"

        "3️⃣ ثبت‌نام 5 دقیقه قبل از شروع قفل می‌شود.\n\n"

        "4️⃣ در کاستوم سکه‌ای، خروج داوطلبانه "
        "باعث برگشت ورودیه نمی‌شود.\n\n"

        "5️⃣ اگر حداقل بازیکنان تکمیل نشود، "
        "کاستوم لغو و ورودیه‌ها برگشت داده می‌شود.\n\n"

        "6️⃣ نتیجه نهایی توسط مدیریت ثبت می‌شود.\n\n"

        "7️⃣ تقلب و سوءاستفاده می‌تواند باعث "
        "محرومیت از ربات شود."
    )

    keyboard = types.InlineKeyboardMarkup()

    keyboard.add(
        types.InlineKeyboardButton(
            "🔙 منوی اصلی",
            callback_data="home"
        )
    )

    render_message(chat_id, text, keyboard, message_id)


def show_help(chat_id, message_id=None):

    text = (
        "📖 <b>راهنما</b>\n\n"

        "🎮 از بخش «کاستوم‌ها» مسابقه موردنظر را انتخاب کن.\n\n"

        "👥 یکی از 25 تیم را انتخاب کن.\n\n"

        "4 نفر ظرفیت هر تیم است.\n\n"

        "🔒 پنج دقیقه قبل از شروع، ثبت‌نام قفل می‌شود.\n\n"

        "🔑 بعد از قفل شدن، اطلاعات روم در اختیار "
        "شرکت‌کنندگان قرار می‌گیرد.\n\n"

        "🏆 بعد از مسابقه، مدیریت نتیجه را ثبت می‌کند "
        "و جایزه به صورت خودکار واریز می‌شود.\n\n"

        "✏️ نام کالاف هر 7 روز یک‌بار قابل تغییر است."
    )

    keyboard = types.InlineKeyboardMarkup()

    keyboard.add(
        types.InlineKeyboardButton(
            "🔙 منوی اصلی",
            callback_data="home"
        )
    )

    render_message(chat_id, text, keyboard, message_id)


def show_support(chat_id, message_id=None):

    set_state(
        chat_id,
        {"action": "support_user"}
    )

    keyboard = types.InlineKeyboardMarkup()

    keyboard.add(
        types.InlineKeyboardButton(
            "🔙 منوی اصلی",
            callback_data="home"
        )
    )

    render_message(
        chat_id,
        "🆘 <b>پشتیبانی</b>\n\n"
        "پیام خود را همین‌جا بنویسید تا برای مدیریت ارسال شود.\n\n"
        "هر تعداد پیام که لازم دارید می‌توانید ارسال کنید.",
        keyboard,
        message_id
    )


def send_support_to_admin(message):
    """Send a user's support message to the admin with a direct reply button."""
    user = get_user(message.from_user.id)
    cod_name = (user["cod_name"] if user else "") or "بدون نام"
    first_name = message.from_user.first_name or "-"
    username = message.from_user.username
    username_text = f"@{username}" if username else "ندارد"

    header = (
        "🆘 <b>پیام جدید پشتیبانی</b>\n\n"
        f"👤 نام: <b>{html.escape(first_name)}</b>\n"
        f"🎮 نام کالاف: <b>{html.escape(cod_name)}</b>\n"
        f"🔗 یوزرنیم: {html.escape(username_text)}\n"
        f"🆔 آیدی تلگرام: <code>{message.from_user.id}</code>\n\n"
        "💬 <b>متن پیام:</b>\n"
        f"{html.escape(message.text or '')}"
    )

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(
            "↩️ پاسخ به کاربر",
            callback_data=f"support:reply:{message.from_user.id}"
        )
    )

    try:
        bot.send_message(
            ADMIN_ID,
            header,
            reply_markup=keyboard
        )
        bot.send_message(
            message.chat.id,
            "✅ پیام شما برای پشتیبانی ارسال شد.\n\n"
            "می‌توانید پیام بعدی خود را هم همین‌جا ارسال کنید.",
        )
    except Exception as error:
        print("Support send error:", repr(error))
        bot.send_message(
            message.chat.id,
            "❌ ارسال پیام به پشتیبانی انجام نشد. لطفاً دوباره تلاش کنید."
        )


def start_support_reply(admin_id, target_user_id):
    if not is_admin(admin_id):
        return

    target = get_user(target_user_id)
    if not target:
        bot.send_message(admin_id, "❌ کاربر پیدا نشد.")
        return

    set_state(
        admin_id,
        {
            "action": "support_reply",
            "target": target_user_id
        }
    )

    cod_name = target["cod_name"] or "بدون نام"
    bot.send_message(
        admin_id,
        "↩️ <b>پاسخ به کاربر</b>\n\n"
        f"🎮 نام کالاف: <b>{html.escape(cod_name)}</b>\n"
        f"🆔 آیدی: <code>{target_user_id}</code>\n\n"
        "متن پاسخ را ارسال کنید.\n"
        "برای لغو بنویسید: <code>لغو</code>"
    )


# =========================================================
# ADMIN MENU
# =========================================================

def admin_menu(chat_id, message_id=None):

    keyboard = types.InlineKeyboardMarkup(
        row_width=2
    )

    buttons = [

        (
            "➕ ساخت کاستوم",
            "admin:create"
        ),

        (
            "🎮 مدیریت کاستوم‌ها",
            "admin:customs"
        ),

        (
            "🏆 اعلام نتایج",
            "admin:results"
        ),

        (
            "👥 شرکت‌کنندگان",
            "admin:participants"
        ),

        (
            "🔑 Room ID/Password",
            "admin:room"
        ),

        (
            "💰 مدیریت موجودی",
            "admin:balance"
        ),

        (
            "👤 مدیریت کاربران",
            "admin:users"
        ),

        (
            "📢 پیام همگانی",
            "admin:broadcast"
        ),

        (
            "📊 آمار",
            "admin:stats"
        ),

        (
            "🧪 تست اعلان‌ها",
            "admin:notif_test"
        ),

        (
            "🎟️ کدهای جایزه",
            "admin:promo"
        ),

        (
            "🛡️ امنیت و لاگ‌ها",
            "admin:security"
        ),

        (
            "⚙️ تنظیمات",
            "admin:settings"
        ),

        (
            "🆕 قابلیت‌های جدید",
            "admin:newfeatures"
        )
    ]

    for label, data in buttons:

        keyboard.add(
            types.InlineKeyboardButton(
                label,
                callback_data=data
            )
        )

    keyboard.add(
        types.InlineKeyboardButton(
            "🔙 منوی اصلی",
            callback_data="home"
        )
    )

    render_message(chat_id, "👑 <b>پنل مدیریت</b>\n\nیک بخش را انتخاب کنید:", keyboard, message_id)


# =========================================================
# ADMIN CUSTOM LIST
# =========================================================

def admin_customs(chat_id, filter_status="active", message_id=None):

    conn = db()

    if filter_status == "active":
        where = "status IN ('open','locked','started')"
        title = "🟢 فعال"
    elif filter_status == "finished":
        where = "status='finished'"
        title = "🏁 تمام‌شده"
    elif filter_status == "cancelled":
        where = "status='cancelled'"
        title = "❌ لغوشده"
    else:
        where = "1=1"
        title = "📋 همه"

    rows = conn.execute(
        f"""
        SELECT c.*,
               (SELECT COUNT(*) FROM participants p
                WHERE p.custom_id=c.id AND p.left_at IS NULL) AS player_count
        FROM customs c
        WHERE {where}
        ORDER BY c.id DESC
        LIMIT 30
        """
    ).fetchall()
    conn.close()

    keyboard = types.InlineKeyboardMarkup(row_width=2)

    keyboard.add(
        types.InlineKeyboardButton("🟢 فعال", callback_data="admin:customs:active"),
        types.InlineKeyboardButton("📋 همه", callback_data="admin:customs:all")
    )
    keyboard.add(
        types.InlineKeyboardButton("🏁 تمام‌شده", callback_data="admin:customs:finished"),
        types.InlineKeyboardButton("❌ لغوشده", callback_data="admin:customs:cancelled")
    )

    if not rows:
        text = f"🎮 <b>مدیریت کاستوم‌ها</b>\n\n{title}\n\nهیچ کاستومی در این بخش وجود ندارد."
    else:
        for row in rows:
            count = row["player_count"]
            keyboard.add(types.InlineKeyboardButton(
                f"#{row['id']} {row['title']} | {count}/{MAX_PLAYERS} | {status_text(row['status'])}",
                callback_data=f"admin:custom:{row['id']}"
            ))
        text = (f"🎮 <b>مدیریت کاستوم‌ها</b>\n\nفیلتر: <b>{title}</b>\n"
                f"تعداد نمایش: <b>{len(rows)}</b>\n\nبرای کنترل دقیق، یک کاستوم را انتخاب کنید.")
    keyboard.add(types.InlineKeyboardButton("🔄 بروزرسانی", callback_data=f"admin:customs:{filter_status}"))
    keyboard.add(types.InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin"))
    render_message(chat_id, text, keyboard, message_id)


def admin_custom_detail(
    chat_id,
    custom_id,
    message_id=None
):

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM customs
        WHERE id=?
        """,
        (custom_id,)
    ).fetchone()

    conn.close()

    if not row:

        bot.send_message(
            chat_id,
            "❌ کاستوم پیدا نشد."
        )

        return

    text = custom_card(
        row,
        ADMIN_ID
    )

    keyboard = types.InlineKeyboardMarkup(
        row_width=2
    )

    if row["status"] == "open":

        keyboard.add(

            types.InlineKeyboardButton(
                "🔒 قفل ثبت‌نام",
                callback_data=
                f"admin:lock:{custom_id}"
            ),

            types.InlineKeyboardButton(
                "▶️ شروع",
                callback_data=
                f"admin:start:{custom_id}"
            )
        )

    if row["status"] in (
        "open",
        "locked"
    ):

        keyboard.add(
            types.InlineKeyboardButton(
                "❌ لغو و بازپرداخت",
                callback_data=
                f"admin:cancel:{custom_id}"
            )
        )

    keyboard.add(

        types.InlineKeyboardButton(
            "👥 شرکت‌کنندگان",
            callback_data=
            f"admin:plist:{custom_id}"
        ),

        types.InlineKeyboardButton(
            "📊 وضعیت تیم‌ها",
            callback_data=
            f"admin:teams:{custom_id}"
        )
    )

    keyboard.add(
        types.InlineKeyboardButton(
            "🔑 تنظیم Room",
            callback_data=
            f"admin:roomset:{custom_id}"
        )
    )

    if (
        row["status"] in (
            "started",
            "locked"
        )
        and not row["results_submitted"]
    ):

        keyboard.add(
            types.InlineKeyboardButton(
                "🏆 ثبت نتیجه",
                callback_data=
                f"admin:resultstart:{custom_id}"
            )
        )

    if row["status"] == "finished" and row["results_submitted"]:
        keyboard.add(
            types.InlineKeyboardButton(
                "✏️ اصلاح نتیجه",
                callback_data=f"admin:resultedit:{custom_id}"
            )
        )
        keyboard.add(
            types.InlineKeyboardButton(
                "📋 جدول نتیجه",
                callback_data=f"admin:resulttable:{custom_id}"
            )
        )

    keyboard.add(
        types.InlineKeyboardButton(
            "🔙 مدیریت کاستوم‌ها",
            callback_data="admin:customs"
        )
    )

    render_message(chat_id, text, keyboard, message_id)


# =========================================================
# ADMIN CUSTOM TEAM STATUS
# =========================================================

def admin_custom_teams(chat_id, custom_id):
    conn = db()
    custom = conn.execute("SELECT * FROM customs WHERE id=?", (custom_id,)).fetchone()
    rows = conn.execute(
        """
        SELECT team_no, COUNT(*) AS cnt
        FROM participants
        WHERE custom_id=? AND left_at IS NULL
        GROUP BY team_no
        ORDER BY team_no
        """,
        (custom_id,)
    ).fetchall()
    conn.close()

    if not custom:
        bot.send_message(chat_id, "❌ کاستوم پیدا نشد.")
        return

    counts = {r["team_no"]: r["cnt"] for r in rows}
    total = sum(counts.values())
    used_teams = len(counts)
    available_teams = MAX_TEAMS - used_teams

    lines = [
        "📊 <b>وضعیت تیم‌ها</b>",
        "",
        f"🎮 {custom['title']} | #{custom_id}",
        f"📌 وضعیت: <b>{status_text(custom['status'])}</b>",
        f"👥 بازیکنان: <b>{total}/{MAX_PLAYERS}</b>",
        f"🧩 تیم‌های دارای بازیکن: <b>{used_teams}/{MAX_TEAMS}</b>",
        f"🟢 تیم‌های آزاد: <b>{available_teams}</b>",
        "",
    ]

    if counts:
        for team_no in sorted(counts):
            cnt = counts[team_no]
            bar = "🟩" * cnt + "⬜" * (4 - cnt)
            lines.append(f"تیم {team_no:02d}  {bar}  <b>{cnt}/4</b>")
    else:
        lines.append("هنوز هیچ تیمی بازیکن ندارد.")

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔄 بروزرسانی", callback_data=f"admin:teams:{custom_id}"))
    kb.add(types.InlineKeyboardButton("🔙 کاستوم", callback_data=f"admin:custom:{custom_id}"))
    bot.send_message(chat_id, "\n".join(lines), reply_markup=kb)


# =========================================================
# CREATE CUSTOM
# =========================================================

def create_custom_start(user_id):
    set_state(
        user_id,
        {
            "action": "create_custom",
            "step": "title",
            "data": {}
        }
    )
    bot.send_message(
        user_id,
        "➕ <b>ساخت کاستوم امروز</b>\n\n"
        "نام کاستوم را وارد کنید.\n\n"
        "برای لغو بنویسید: <code>لغو</code>"
    )


def ask_next_create(user_id):
    state = get_state(user_id)
    if not state:
        return
    step = state["step"]
    prompts = {
        "time": "⏰ ساعت شروع کاستوم امروز را وارد کنید.\nمثال: <code>22:00</code>",
        "min_players": "👥 حداقل تعداد بازیکن برای شروع را وارد کنید.\nحداکثر 100 نفر.",
    }
    if step in prompts:
        bot.send_message(user_id, prompts[step])


def start_create_type(user_id):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("🆓 رایگان", callback_data="create:type:free"))
    keyboard.add(types.InlineKeyboardButton("🪙 کوینی — 25 سکه", callback_data="create:type:coin"))
    bot.send_message(user_id, "🎯 نوع کاستوم را انتخاب کنید:", reply_markup=keyboard)


def handle_create_text(user_id, text):
    state = get_state(user_id)
    if not state or state["action"] != "create_custom":
        return False

    if text.strip() == "لغو":
        clear_state(user_id)
        bot.send_message(user_id, "❌ ساخت کاستوم لغو شد.")
        admin_menu(user_id)
        return True

    step = state["step"]
    data = state["data"]

    if step == "title":
        if not text.strip():
            bot.send_message(user_id, "❌ نام نمی‌تواند خالی باشد.")
            return True
        data["title"] = text.strip()[:100]
        state["step"] = "time"
        ask_next_create(user_id)
        return True

    if step == "time":
        value = text.strip()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            bot.send_message(user_id, "❌ ساعت نامعتبر است.\nمثال: <code>22:00</code>")
            return True
        now = datetime.now(TZ)
        try:
            start = datetime.strptime(value, "%H:%M").replace(
                year=now.year, month=now.month, day=now.day, tzinfo=TZ
            )
        except Exception:
            bot.send_message(user_id, "❌ ساعت نامعتبر است.")
            return True
        if start <= now:
            bot.send_message(user_id, "❌ این ساعت گذشته است. فقط برای ساعت‌های باقی‌مانده امروز می‌توانید کاستوم بسازید.")
            return True
        data["date"] = now.strftime("%Y-%m-%d")
        data["time"] = value
        state["step"] = "type"
        start_create_type(user_id)
        return True

    if step == "min_players":
        number = valid_int(text, 1)
        if number is None or number > MAX_PLAYERS:
            bot.send_message(user_id, "❌ عدد باید بین 1 تا 100 باشد.")
            return True
        data["min_players"] = number
        state["step"] = "confirm"
        preview = build_create_preview(data)
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton("✅ ساخت", callback_data="create:confirm"))
        keyboard.add(types.InlineKeyboardButton("❌ لغو", callback_data="create:cancel"))
        bot.send_message(user_id, preview, reply_markup=keyboard)
        return True

    return True


def build_create_preview(data):
    if data["custom_type"] == "free":
        rules = (
            f"🆓 ورودیه: رایگان\n"
            f"🏆 برنده: 1 تیم\n"
            f"🎁 جایزه: {FREE_PRIZE_PER_MEMBER} سکه برای هر عضو تیم اول"
        )
    else:
        rules = (
            f"🪙 ورودیه: {COIN_ENTRY_FEE} سکه\n"
            f"🏆 برنده: 3 تیم\n"
            "💰 جوایز: به‌صورت خودکار از مجموع ورودیه‌ها بین رتبه‌های 1 تا 3 تقسیم می‌شود."
        )
    return (
        "📋 <b>تأیید ساخت کاستوم امروز</b>\n\n"
        f"🎮 {data['title']}\n"
        f"📅 امروز — {data['date']}\n"
        f"⏰ {data['time']}\n"
        f"👥 حداقل بازیکن: {data['min_players']}\n"
        f"🔒 قفل ثبت‌نام: {LOCK_MINUTES} دقیقه قبل\n\n"
        f"{rules}\n\n"
        "🔑 Room ID و Password: بعداً از بخش مدیریت ثبت می‌شود."
    )


def finalize_custom(user_id):
    state = get_state(user_id)
    if not state:
        return
    data = state["data"]
    now = datetime.now(TZ)
    try:
        start = datetime.strptime(
            f"{data['date']} {data['time']}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=TZ)
    except Exception:
        bot.send_message(user_id, "❌ ساعت کاستوم نامعتبر است.")
        return

    # Safety: creation is always for today, never another date.
    if data["date"] != now.strftime("%Y-%m-%d"):
        bot.send_message(user_id, "❌ فقط امکان ساخت کاستوم برای امروز وجود دارد.")
        clear_state(user_id)
        return
    if start <= now:
        bot.send_message(user_id, "❌ زمان کاستوم گذشته است.")
        return

    custom_type = data["custom_type"]
    if custom_type == "free":
        entry_fee, winners_count = 0, 1
        prize1, prize2, prize3 = FREE_PRIZE_PER_MEMBER, 0, 0
    else:
        entry_fee, winners_count = COIN_ENTRY_FEE, 3
        # Actual coin prize pools are calculated automatically at start.
        prize1 = prize2 = prize3 = 0

    conn = db()
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO customs(
                    title,date,time,custom_type,entry_fee,min_players,
                    winners_count,prize1,prize2,prize3,lock_minutes,
                    status,room_id,room_password,created_by,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    data["title"], data["date"], data["time"], custom_type,
                    entry_fee, data["min_players"], winners_count,
                    prize1, prize2, prize3, LOCK_MINUTES, "open", "", "",
                    user_id, now_str()
                )
            )
            custom_id = cursor.lastrowid
            conn.executemany(
                "INSERT INTO teams(custom_id,team_no) VALUES(?,?)",
                [(custom_id, n) for n in range(1, MAX_TEAMS + 1)]
            )
    finally:
        conn.close()

    clear_state(user_id)
    bot.send_message(
        user_id,
        "✅ <b>کاستوم ساخته شد</b>\n\n"
        f"🆔 شناسه: <code>{custom_id}</code>\n"
        f"🎮 {data['title']}\n"
        f"📅 امروز — ⏰ {data['time']}\n"
        f"🎯 {custom_type_text(custom_type)}\n"
        f"🔒 قفل ثبت‌نام: {LOCK_MINUTES} دقیقه قبل\n\n"
        "🔑 Room ID و Password هنوز ثبت نشده و می‌توانید بعداً از «مدیریت کاستوم‌ها» وارد کنید."
    )
    admin_menu(user_id)

# =========================================================
# ROOM
# =========================================================

def room_text(row):

    return (
        "🔑 <b>اطلاعات روم</b>\n\n"

        f"🆔 Room ID:\n"
        f"<code>{row['room_id']}</code>\n\n"

        f"🔐 Password:\n"
        f"<code>{row['room_password']}</code>"
    )


# =========================================================
# NOTIFICATIONS
# =========================================================

def notify_participants(
    custom_id,
    text
):

    conn = db()

    users = conn.execute(
        """
        SELECT DISTINCT user_id
        FROM participants

        WHERE custom_id=?
        AND left_at IS NULL
        """,
        (custom_id,)
    ).fetchall()

    conn.close()

    for user in users:
        try:
            clean = html.unescape(re.sub(r"<[^>]+>", "", text))
            conn2=db()
            conn2.execute("INSERT INTO notifications(user_id,title,body,kind,is_read,created_at) VALUES(?,?,?,?,0,?)",(user["user_id"],f"کاستوم #{custom_id}",clean,"custom",now_str()))
            conn2.commit(); conn2.close()
            bot.send_message(user["user_id"], text)
        except Exception:
            pass


def notify_once(custom_id, event_key, text):
    """Send a custom notification once per event, preventing duplicates."""
    conn = db()
    try:
        with conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO custom_notifications(custom_id,event_key,created_at) VALUES(?,?,?)",
                (custom_id, event_key, now_str())
            )
            if cur.rowcount != 1:
                return False
    finally:
        conn.close()
    notify_participants(custom_id, text)
    return True


# =========================================================
# LOCK CUSTOM
# =========================================================

def lock_custom(
    custom_id,
    automatic=False
):

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM customs
        WHERE id=?
        """,
        (custom_id,)
    ).fetchone()

    if (
        not row
        or row["status"] != "open"
    ):

        conn.close()

        return False

    conn.execute(
        """
        UPDATE customs
        SET status='locked'
        WHERE id=?
        """,
        (custom_id,)
    )

    conn.commit()
    conn.close()

    notify_once(
        custom_id,
        "lock",
        "🔒 <b>ثبت‌نام بسته شد</b>\n\n"
        "دیگر امکان ثبت‌نام یا تغییر تیم وجود ندارد."
    )

    return True


# =========================================================
# CANCEL CUSTOM
# =========================================================

def cancel_custom(
    custom_id,
    reason="لغو توسط مدیریت"
):

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM customs
        WHERE id=?
        """,
        (custom_id,)
    ).fetchone()

    if (
        not row
        or row["status"] in (
            "finished",
            "cancelled"
        )
    ):

        conn.close()

        return False

    try:

        with conn:

            participants = conn.execute(
                """
                SELECT *
                FROM participants

                WHERE custom_id=?
                AND left_at IS NULL
                """,
                (custom_id,)
            ).fetchall()

            for participant in participants:

                if participant["entry_paid"] > 0:

                    add_coins(

                        participant["user_id"],

                        participant["entry_paid"],

                        "refund",

                        "بازپرداخت ورودیه کاستوم",

                        custom_id,

                        ADMIN_ID,

                        conn
                    )

            conn.execute(
                """
                UPDATE customs
                SET status='cancelled'
                WHERE id=?
                """,
                (custom_id,)
            )

    finally:

        conn.close()

    notify_once(
        custom_id,
        "cancelled",
        "❌ <b>کاستوم لغو شد.</b>\n\n"
        f"دلیل: {reason}\n\n"
        "ورودیه‌های مشمول بازپرداخت، برگشت داده شدند."
    )

    return True


def calculate_coin_prizes(conn, custom_id, entry_fee, player_count):
    total = int(entry_fee) * int(player_count)
    # 50% / 30% / 20%, with every coin assigned and no rounding loss.
    p1 = total * 50 // 100
    p2 = total * 30 // 100
    p3 = total - p1 - p2
    conn.execute(
        "UPDATE customs SET prize1=?, prize2=?, prize3=? WHERE id=?",
        (p1, p2, p3, custom_id)
    )
    return p1, p2, p3


# =========================================================
# START CUSTOM
# =========================================================

def start_custom(custom_id):

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM customs
        WHERE id=?
        """,
        (custom_id,)
    ).fetchone()

    if (
        not row
        or row["status"] not in (
            "open",
            "locked"
        )
    ):

        conn.close()

        return False

    count = conn.execute(
        """
        SELECT COUNT(*)
        FROM participants

        WHERE custom_id=?
        AND left_at IS NULL
        """,
        (custom_id,)
    ).fetchone()[0]

    conn.close()

    if count < row["min_players"]:

        cancel_custom(
            custom_id,
            f"حداقل {row['min_players']} بازیکن تکمیل نشد."
        )

        return False

    # For coin customs, build the three prize pools automatically from the collected entry fees.
    if row["custom_type"] == "coin":
        conn = db()
        try:
            calculate_coin_prizes(conn, custom_id, row["entry_fee"], count)
            conn.commit()
            row = conn.execute("SELECT * FROM customs WHERE id=?", (custom_id,)).fetchone()
        finally:
            conn.close()

    conn = db()

    conn.execute(
        """
        UPDATE customs
        SET status='started'
        WHERE id=?
        """,
        (custom_id,)
    )

    conn.commit()
    conn.close()

    if row["room_id"] and row["room_password"]:
        message = "▶️ <b>کاستوم شروع شد!</b>\n\n" + room_text(row)
    else:
        message = (
            "▶️ <b>کاستوم شروع شد!</b>\n\n"
            "⚠️ اطلاعات روم هنوز توسط مدیریت ثبت نشده است."
        )
    notify_once(custom_id, "started", message)

    return True


# =========================================================
# JOIN CUSTOM
# =========================================================

def join_custom(
    user_id,
    custom_id,
    team_no
):

    conn = db()

    try:

        with conn:

            row = conn.execute(
                """
                SELECT *
                FROM customs
                WHERE id=?
                """,
                (custom_id,)
            ).fetchone()

            user = conn.execute(
                """
                SELECT *
                FROM users
                WHERE id=?
                """,
                (user_id,)
            ).fetchone()

            if not row or not user:
                return "notfound"

            if user["banned"]:
                return "banned"

            if row["status"] != "open":
                return "locked"

            start = parse_custom_datetime(row)

            lock_time = (
                start
                - timedelta(
                    minutes=row["lock_minutes"]
                )
            )

            if datetime.now(TZ) >= lock_time:
                return "locked"

            old = conn.execute(
                """
                SELECT *
                FROM participants

                WHERE custom_id=?
                AND user_id=?
                """,
                (
                    custom_id,
                    user_id
                )
            ).fetchone()

            if (
                old
                and old["left_at"] is None
            ):
                security_log("duplicate_registration", actor_id=user_id, target_user_id=user_id, custom_id=custom_id, details="تلاش برای ثبت‌نام تکراری", conn=conn)
                return "duplicate"

            team_members = conn.execute(
                """
                SELECT COUNT(*)
                FROM participants

                WHERE custom_id=?
                AND team_no=?
                AND left_at IS NULL
                """,
                (
                    custom_id,
                    team_no
                )
            ).fetchone()[0]

            if team_members >= TEAM_CAPACITY:
                return "full"

            total = conn.execute(
                """
                SELECT COUNT(*)
                FROM participants

                WHERE custom_id=?
                AND left_at IS NULL
                """,
                (custom_id,)
            ).fetchone()[0]

            if total >= MAX_PLAYERS:
                return "max"

            fee = (
                row["entry_fee"]
                if row["custom_type"] == "coin"
                else 0
            )

            if user["coins"] < fee:
                return "balance"

            if old:

                conn.execute(
                    """
                    UPDATE participants

                    SET
                        team_no=?,
                        joined_at=?,
                        left_at=NULL,
                        entry_paid=?

                    WHERE id=?
                    """,
                    (
                        team_no,
                        now_str(),
                        fee,
                        old["id"]
                    )
                )

            else:

                conn.execute(
                    """
                    INSERT INTO participants(
                        custom_id,
                        user_id,
                        team_no,
                        joined_at,
                        entry_paid
                    )
                    VALUES(
                        ?,
                        ?,
                        ?,
                        ?,
                        ?
                    )
                    """,
                    (
                        custom_id,
                        user_id,
                        team_no,
                        now_str(),
                        fee
                    )
                )

            if fee:

                conn.execute(
                    """
                    UPDATE users

                    SET coins=coins-?

                    WHERE id=?
                    """,
                    (
                        fee,
                        user_id
                    )
                )

                conn.execute(
                    """
                    INSERT INTO transactions(
                        user_id,
                        amount,
                        balance_type,
                        kind,
                        description,
                        custom_id,
                        created_at
                    )
                    VALUES(
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        ?,
                        ?
                    )
                    """,
                    (
                        user_id,
                        -fee,
                        "coins",
                        "entry",
                        "ورود به کاستوم",
                        custom_id,
                        now_str()
                    )
                )

            return "ok"

    finally:

        conn.close()


# =========================================================
# LEAVE CUSTOM
# =========================================================

def leave_custom(
    user_id,
    custom_id
):

    conn = db()

    participant = conn.execute(
        """
        SELECT *
        FROM participants

        WHERE custom_id=?
        AND user_id=?
        AND left_at IS NULL
        """,
        (
            custom_id,
            user_id
        )
    ).fetchone()

    custom = conn.execute(
        """
        SELECT *
        FROM customs
        WHERE id=?
        """,
        (custom_id,)
    ).fetchone()

    if not participant or not custom:

        conn.close()

        return "not"

    if custom["status"] != "open":

        conn.close()

        return "locked"

    conn.execute(
        """
        UPDATE participants

        SET left_at=?

        WHERE id=?
        """,
        (
            now_str(),
            participant["id"]
        )
    )

    conn.commit()
    conn.close()

    return "ok"


# =========================================================
# CHANGE TEAM
# =========================================================

def change_team(
    user_id,
    custom_id,
    new_team
):

    conn = db()

    try:

        with conn:

            custom = conn.execute(
                """
                SELECT *
                FROM customs
                WHERE id=?
                """,
                (custom_id,)
            ).fetchone()

            participant = conn.execute(
                """
                SELECT *
                FROM participants

                WHERE custom_id=?
                AND user_id=?
                AND left_at IS NULL
                """,
                (
                    custom_id,
                    user_id
                )
            ).fetchone()

            if (
                not custom
                or not participant
                or custom["status"] != "open"
            ):

                return False

            members = conn.execute(
                """
                SELECT COUNT(*)
                FROM participants

                WHERE custom_id=?
                AND team_no=?
                AND left_at IS NULL
                """,
                (
                    custom_id,
                    new_team
                )
            ).fetchone()[0]

            if members >= TEAM_CAPACITY:
                return False

            conn.execute(
                """
                UPDATE participants

                SET team_no=?

                WHERE id=?
                """,
                (
                    new_team,
                    participant["id"]
                )
            )

            return True

    finally:

        conn.close()


# =========================================================
# RANK SYSTEM
# =========================================================

RANKS = [
    (0, "🥉 برنز"),
    (100, "🥈 نقره"),
    (250, "🥇 طلا"),
    (500, "💎 پلاتینیوم"),
    (900, "💠 الماس"),
    (1500, "👑 مستر"),
]

PLACE_RANK_POINTS = {
    1: 35,
    2: 25,
    3: 18,
}
PARTICIPATION_RANK_POINTS = 5

def rank_from_points(points):
    current = RANKS[0][1]
    for threshold, name in RANKS:
        if points >= threshold:
            current = name
        else:
            break
    return current

def add_rank_points(user_id, points, conn):
    row = conn.execute(
        "SELECT rank_points FROM users WHERE id=?",
        (user_id,)
    ).fetchone()
    if not row:
        return
    new_points = max(0, int(row["rank_points"] or 0) + int(points))
    new_rank = rank_from_points(new_points)
    conn.execute(
        "UPDATE users SET rank_points=?, rank=? WHERE id=?",
        (new_points, new_rank, user_id)
    )

# =========================================================
# RESULTS
# =========================================================

def submit_results(
    custom_id,
    selections,
    allow_correction=False
):

    conn = db()

    try:

        with conn:

            custom = conn.execute(
                """
                SELECT *
                FROM customs
                WHERE id=?
                """,
                (custom_id,)
            ).fetchone()

            if not custom:

                return (
                    False,
                    "کاستوم پیدا نشد."
                )

            if custom["results_submitted"] and not allow_correction:

                security_log("duplicate_result", actor_id=ADMIN_ID, custom_id=custom_id, details="تلاش برای ثبت نتیجه تکراری", conn=conn)
                return (
                    False,
                    "نتیجه قبلاً ثبت شده است."
                )

            # In correction mode, fully reverse the previous result inside the
            # same transaction before applying the new placement. This keeps
            # the wallet, rank points, winners table and player history in sync.
            if custom["results_submitted"] and allow_correction:
                previous = conn.execute(
                    "SELECT * FROM result_history WHERE custom_id=? ORDER BY id",
                    (custom_id,)
                ).fetchall()
                for old in previous:
                    if int(old["prize"] or 0):
                        add_coins(old["user_id"], -int(old["prize"]), "result_correction",
                                  "اصلاح نتیجه قبلی کاستوم", custom_id, ADMIN_ID, conn)
                    old_points = int(old["rank_points_awarded"] or 0)
                    if old_points:
                        add_rank_points(old["user_id"], -old_points, conn)
                conn.execute("DELETE FROM winners WHERE custom_id=?", (custom_id,))
                conn.execute("DELETE FROM result_history WHERE custom_id=?", (custom_id,))
                conn.execute("UPDATE customs SET results_submitted=0 WHERE id=?", (custom_id,))
                security_log("result_correction", actor_id=ADMIN_ID, custom_id=custom_id,
                             details="نتیجه قبلی معکوس شد و نتیجه جدید در حال ثبت است", conn=conn)

            if custom["status"] not in (
                "started",
                "locked",
                "finished" if allow_correction else "__never__"
            ):

                return (
                    False,
                    "این کاستوم آماده ثبت نتیجه نیست."
                )

            if len(selections) != custom["winners_count"]:

                return (
                    False,
                    "تعداد برنده‌ها صحیح نیست."
                )

            if len(set(selections)) != len(selections):

                return (
                    False,
                    "یک تیم دوبار انتخاب شده است."
                )

            prizes = [
                custom["prize1"],
                custom["prize2"],
                custom["prize3"]
            ]

            all_participants = conn.execute(
                "SELECT user_id FROM participants WHERE custom_id=? AND left_at IS NULL",
                (custom_id,)
            ).fetchall()
            for member in all_participants:
                add_rank_points(member["user_id"], PARTICIPATION_RANK_POINTS, conn)

            for index, team_no in enumerate(
                selections,
                start=1
            ):

                members = conn.execute(
                    """
                    SELECT user_id
                    FROM participants

                    WHERE custom_id=?
                    AND team_no=?
                    AND left_at IS NULL

                    ORDER BY id
                    """,
                    (
                        custom_id,
                        team_no
                    )
                ).fetchall()

                if not members:

                    return (
                        False,
                        f"تیم {team_no} عضو ندارد."
                    )

                if custom["custom_type"] == "free":

                    prize = custom["prize1"]

                    for member in members:

                        add_coins(

                            member["user_id"],

                            prize,

                            "prize",

                            "جایزه کاستوم رایگان",

                            custom_id,

                            ADMIN_ID,

                            conn
                        )

                else:

                    prize = prizes[index - 1]

                    count = len(members)

                    base, remainder = divmod(
                        prize,
                        count
                    )

                    for position, member in enumerate(
                        members
                    ):

                        amount = (
                            base
                            + (
                                1
                                if position < remainder
                                else 0
                            )
                        )

                        if amount > 0:

                            add_coins(

                                member["user_id"],

                                amount,

                                "prize",

                                f"جایزه رتبه {index}",

                                custom_id,

                                ADMIN_ID,

                                conn
                            )

                # Rank points are awarded from the actual final placement.
                placement_points = PLACE_RANK_POINTS.get(index, 0)
                if placement_points:
                    for member in members:
                        add_rank_points(member["user_id"], placement_points, conn)

                conn.execute(
                    """
                    INSERT INTO winners(
                        custom_id,
                        team_no,
                        place,
                        prize,
                        created_at
                    )
                    VALUES(
                        ?,
                        ?,
                        ?,
                        ?,
                        ?
                    )
                    """,
                    (
                        custom_id,
                        team_no,
                        index,
                        prize,
                        now_str()
                    )
                )

                # Store player-level performance so profile/history data is
                # based on the actual final result, not only the winning team.
                for member in members:
                    conn.execute(
                        """
                        INSERT INTO result_history(
                            custom_id, user_id, team_no, place, prize,
                            rank_points_awarded, created_at
                        ) VALUES(?,?,?,?,?,?,?)
                        """,
                        (
                            custom_id, member["user_id"], team_no, index,
                            (prize // len(members)) if custom["custom_type"] == "free" else
                                (prize // len(members)) + (1 if members.index(member) < (prize % len(members)) else 0),
                            placement_points + PARTICIPATION_RANK_POINTS,
                            now_str()
                        )
                    )

            # Store every participant in the final performance history.
            # Winners already have a row with their exact prize/placement;
            # remaining players get place=0 and participation points only.
            all_final = conn.execute(
                "SELECT user_id, team_no FROM participants WHERE custom_id=? AND left_at IS NULL",
                (custom_id,)
            ).fetchall()
            for member in all_final:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO result_history(
                        custom_id, user_id, team_no, place, prize,
                        rank_points_awarded, created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (custom_id, member["user_id"], member["team_no"], 0, 0,
                     PARTICIPATION_RANK_POINTS, now_str())
                )

            conn.execute(
                """
                UPDATE customs

                SET
                    status='finished',
                    results_submitted=1

                WHERE id=?
                """,
                (custom_id,)
            )

            affected_users = [r["user_id"] for r in conn.execute(
                "SELECT user_id FROM result_history WHERE custom_id=?", (custom_id,)
            ).fetchall()]
            update_result_achievements(conn, affected_users)

        notify_once(
            custom_id,
            "results",
            "🏆 <b>نتیجه کاستوم اعلام شد!</b>\n\n"
            "نتایج ثبت شد و جوایز بر اساس رتبه پرداخت شدند. 🪙"
        )

        return (
            True,
            "ok"
        )

    except sqlite3.IntegrityError:

        return (
            False,
            "نتیجه قبلاً ثبت شده یا تیم تکراری انتخاب شده."
        )

    finally:

        conn.close()


# =========================================================
# RESULT ACHIEVEMENTS
# =========================================================

def update_result_achievements(conn, user_ids):
    """Persist achievements unlocked by the latest completed results."""
    for user_id in set(user_ids):
        played = conn.execute(
            "SELECT COUNT(DISTINCT custom_id) FROM result_history WHERE user_id=?",
            (user_id,)
        ).fetchone()[0]
        wins = conn.execute(
            "SELECT COUNT(DISTINCT custom_id) FROM result_history WHERE user_id=? AND place=1",
            (user_id,)
        ).fetchone()[0]
        top3 = conn.execute(
            "SELECT COUNT(DISTINCT custom_id) FROM result_history WHERE user_id=? AND place BETWEEN 1 AND 3",
            (user_id,)
        ).fetchone()[0]
        invited = conn.execute(
            "SELECT COUNT(*) FROM referrals WHERE inviter_id=?",
            (user_id,)
        ).fetchone()[0]
        streak_row = conn.execute(
            "SELECT daily_streak FROM users WHERE id=?", (user_id,)
        ).fetchone()
        streak = int(streak_row[0] or 0) if streak_row else 0
        checks = [
            ("first_game", "🎮 اولین ورود", played >= 1),
            ("games_10", "🔥 ۱۰ بازی", played >= 10),
            ("first_win", "🏆 اولین برد", wins >= 1),
            ("wins_5", "👑 ۵ برد", wins >= 5),
            ("top3_10", "🥉 ۱۰ تاپ ۳", top3 >= 10),
            ("first_referral", "🎁 اولین دعوت", invited >= 1),
            ("referrals_5", "👥 ۵ دعوت موفق", invited >= 5),
            ("streak_7", "🔥 استریک ۷ روزه", streak >= 7),
        ]
        for key, title, unlocked in checks:
            if unlocked:
                conn.execute(
                    "INSERT OR IGNORE INTO achievements(user_id,achievement_key,title,unlocked_at) VALUES(?,?,?,?)",
                    (user_id, key, title, now_str())
                )


def admin_result_table(chat_id, custom_id, message_id=None):
    conn = db()
    custom = conn.execute("SELECT title FROM customs WHERE id=?", (custom_id,)).fetchone()
    rows = conn.execute(
        """
        SELECT rh.place, rh.team_no, u.cod_name, rh.prize
        FROM result_history rh
        JOIN users u ON u.id=rh.user_id
        WHERE rh.custom_id=?
        ORDER BY CASE WHEN rh.place=0 THEN 99 ELSE rh.place END, rh.team_no, rh.id
        """, (custom_id,)
    ).fetchall()
    conn.close()
    if not custom:
        text = "❌ کاستوم پیدا نشد."
    else:
        text = f"📋 <b>جدول نتیجه</b>\n\n🎮 {custom['title']} | #{custom_id}\n\n"
        current_team = None
        for r in rows:
            if r["place"] and r["place"] != current_team:
                text += f"\n🏅 رتبه {r['place']}\n"
                current_team = r["place"]
            label = f"تیم {r['team_no']}"
            prize = f" | 🪙 {r['prize']}" if r["prize"] else ""
            text += f"• {label} — <b>{r['cod_name']}</b>{prize}\n"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✏️ اصلاح نتیجه", callback_data=f"admin:resultedit:{custom_id}"))
    kb.add(types.InlineKeyboardButton("🔙 کاستوم", callback_data=f"admin:custom:{custom_id}"))
    render_message(chat_id, text, kb, message_id)


# =========================================================
# ADMIN RESULTS VIEW
# =========================================================

def admin_result_view(
    chat_id,
    custom_id,
    message_id=None
):

    conn = db()

    custom = conn.execute(
        """
        SELECT *
        FROM customs
        WHERE id=?
        """,
        (custom_id,)
    ).fetchone()

    conn.close()

    if not custom:

        bot.send_message(
            chat_id,
            "❌ کاستوم پیدا نشد."
        )

        return

    state = get_state(
        ADMIN_ID
    )

    selected = []

    if (
        state
        and state.get("action") in ("results", "results_edit")
        and state.get("cid") == custom_id
    ):

        selected = state.get(
            "selected",
            []
        )

    keyboard = types.InlineKeyboardMarkup(
        row_width=2
    )

    for team_no in range(
        1,
        MAX_TEAMS + 1
    ):

        count = team_count(
            custom_id,
            team_no
        )

        if count <= 0:
            continue

        mark = (
            "✅ "
            if team_no in selected
            else ""
        )

        keyboard.add(
            types.InlineKeyboardButton(
                f"{mark}تیم {team_no} ({count})",

                callback_data=
                f"resultteam:"
                f"{custom_id}:"
                f"{team_no}"
            )
        )

    keyboard.add(
        types.InlineKeyboardButton(
            f"🏆 ثبت نتیجه "
            f"({len(selected)}/"
            f"{custom['winners_count']})",

            callback_data=
            f"resultsubmit:{custom_id}"
        )
    )

    keyboard.add(
        types.InlineKeyboardButton(
            "❌ لغو",
            callback_data=
            f"admin:custom:{custom_id}"
        )
    )

    text = (
        "✏️ <b>اصلاح / ثبت نتیجه</b>\n\n" if custom["results_submitted"] else
        "🏆 <b>انتخاب تیم‌های برنده</b>\n\n"
    ) + (
        f"تعداد لازم: <b>{custom['winners_count']}</b>\n\n"
        f"انتخاب فعلی: <b>{selected}</b>"
    )

    if message_id:
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=keyboard)
            return
        except Exception:
            pass

    bot.send_message(
        chat_id,
        text,

        f"تعداد لازم: "
        f"<b>{custom['winners_count']}</b>\n\n"

        f"انتخاب فعلی: "
        f"<b>{selected}</b>",

        reply_markup=keyboard
    )


# =========================================================
# ADMIN PARTICIPANTS
# =========================================================

def admin_participants(
    chat_id,
    custom_id
):

    conn = db()

    rows = conn.execute(
        """
        SELECT
            p.team_no,
            u.cod_name,
            u.first_name,
            u.id

        FROM participants p

        JOIN users u
            ON u.id=p.user_id

        WHERE p.custom_id=?
        AND p.left_at IS NULL

        ORDER BY p.team_no,p.id
        """,
        (custom_id,)
    ).fetchall()

    conn.close()

    if not rows:

        text = (
            "👥 هنوز شرکت‌کننده‌ای "
            "ثبت نشده است."
        )

    else:

        text = (
            "👥 <b>شرکت‌کنندگان</b>\n"
        )

        current_team = None

        for row in rows:

            if current_team != row["team_no"]:

                current_team = row["team_no"]

                text += (
                    f"\n<b>تیم "
                    f"{current_team}</b>\n"
                )

            text += (
                f"• "
                f"{row['cod_name'] or row['first_name']}"
                f"\n"
            )

    keyboard = types.InlineKeyboardMarkup()

    keyboard.add(
        types.InlineKeyboardButton(
            "🔙 کاستوم",
            callback_data=
            f"admin:custom:{custom_id}"
        )
    )

    bot.send_message(
        chat_id,
        text,
        reply_markup=keyboard
    )


# =========================================================
# ADMIN STATS
# =========================================================

def admin_stats(chat_id, message_id=None):

    conn = db()
    today = datetime.now(TZ).date()
    today_str = today.strftime("%Y-%m-%d")
    cutoff = (datetime.now(TZ) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active_users = conn.execute("SELECT COUNT(*) FROM users WHERE banned=0").fetchone()[0]
    new_users_today = conn.execute(
        "SELECT COUNT(*) FROM users WHERE joined_at LIKE ?", (today_str + "%",)
    ).fetchone()[0]

    customs = conn.execute("SELECT COUNT(*) FROM customs").fetchone()[0]
    customs_today = conn.execute(
        "SELECT COUNT(*) FROM customs WHERE date=?", (today_str,)
    ).fetchone()[0]
    running = conn.execute(
        "SELECT COUNT(*) FROM customs WHERE status IN ('open','locked','started')"
    ).fetchone()[0]
    finished = conn.execute(
        "SELECT COUNT(*) FROM customs WHERE status='finished'"
    ).fetchone()[0]
    cancelled = conn.execute(
        "SELECT COUNT(*) FROM customs WHERE status='cancelled'"
    ).fetchone()[0]

    participants = conn.execute(
        "SELECT COUNT(*) FROM participants WHERE left_at IS NULL"
    ).fetchone()[0]
    participants_today = conn.execute(
        "SELECT COUNT(*) FROM participants WHERE joined_at LIKE ? AND left_at IS NULL",
        (today_str + "%",)
    ).fetchone()[0]

    total_coins = conn.execute(
        "SELECT COALESCE(SUM(coins),0) FROM users"
    ).fetchone()[0]
    prizes_paid = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE kind='prize' AND amount>0"
    ).fetchone()[0]
    prizes_7d = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE kind='prize' AND amount>0 AND created_at>=?",
        (cutoff,)
    ).fetchone()[0]
    coin_inflow = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE amount>0"
    ).fetchone()[0]
    coin_outflow = conn.execute(
        "SELECT COALESCE(SUM(-amount),0) FROM transactions WHERE amount<0"
    ).fetchone()[0]
    transaction_count = conn.execute(
        "SELECT COUNT(*) FROM transactions"
    ).fetchone()[0]
    promo_count = conn.execute("SELECT COUNT(*) FROM promo_codes").fetchone()[0]
    promo_used = conn.execute(
        "SELECT COALESCE(SUM(used_count),0) FROM promo_codes"
    ).fetchone()[0]
    daily_rewards = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE kind='daily_reward'"
    ).fetchone()[0]

    top = conn.execute(
        """
        SELECT u.cod_name, u.rank, u.rank_points,
               COUNT(DISTINCT CASE WHEN c.status='finished' THEN p.custom_id END) AS played,
               COUNT(DISTINCT CASE WHEN w.place=1 THEN w.custom_id END) AS wins,
               COUNT(DISTINCT CASE WHEN w.place BETWEEN 1 AND 3 THEN w.custom_id END) AS top3,
               COALESCE(SUM(CASE WHEN t.kind='prize' AND t.amount>0 THEN t.amount ELSE 0 END),0) AS prizes
        FROM users u
        LEFT JOIN participants p ON p.user_id=u.id AND p.left_at IS NULL
        LEFT JOIN customs c ON c.id=p.custom_id
        LEFT JOIN winners w ON w.custom_id=p.custom_id AND w.team_no=p.team_no
        LEFT JOIN transactions t ON t.user_id=u.id
        WHERE u.banned=0
        GROUP BY u.id
        HAVING played>0 OR wins>0 OR prizes>0
        ORDER BY wins DESC, prizes DESC, played DESC, u.rank_points DESC, u.id ASC
        LIMIT 5
        """
    ).fetchall()

    week_customs = conn.execute(
        """
        SELECT date, COUNT(*) AS count
        FROM customs
        WHERE created_at>=?
        GROUP BY date
        ORDER BY date DESC
        LIMIT 7
        """, (cutoff,)
    ).fetchall()

    week_tx = conn.execute(
        """
        SELECT substr(created_at,1,10) AS day,
               COALESCE(SUM(CASE WHEN amount>0 THEN amount ELSE 0 END),0) AS income,
               COALESCE(SUM(CASE WHEN amount<0 THEN -amount ELSE 0 END),0) AS expense
        FROM transactions
        WHERE created_at>=?
        GROUP BY day
        ORDER BY day DESC
        LIMIT 7
        """, (cutoff,)
    ).fetchall()
    conn.close()

    text = (
        "📊 <b>داشبورد مدیریت</b>\n\n"
        "👥 <b>کاربران</b>\n"
        f"• کل کاربران: <b>{users}</b>\n"
        f"• کاربران فعال: <b>{active_users}</b>\n"
        f"• ثبت‌نام امروز: <b>{new_users_today}</b>\n\n"
        "🎮 <b>کاستوم‌ها</b>\n"
        f"• کل: <b>{customs}</b>\n"
        f"• امروز: <b>{customs_today}</b>\n"
        f"• فعال: <b>{running}</b>\n"
        f"• تمام‌شده: <b>{finished}</b>\n"
        f"• لغوشده: <b>{cancelled}</b>\n\n"
        "👤 <b>شرکت‌کنندگان</b>\n"
        f"• در کاستوم‌های باز: <b>{participants}</b>\n"
        f"• امروز: <b>{participants_today}</b>\n\n"
        "🪙 <b>اقتصاد کوین</b>\n"
        f"• موجودی فعلی کاربران: <b>{total_coins}</b> 🪙\n"
        f"• جوایز پرداخت‌شده: <b>{prizes_paid}</b> 🪙\n"
        f"• جوایز ۷ روز اخیر: <b>{prizes_7d}</b> 🪙\n"
        f"• ورود کوین: <b>{coin_inflow}</b> 🪙\n"
        f"• خروج کوین: <b>{coin_outflow}</b> 🪙\n"
        f"• کل تراکنش‌ها: <b>{transaction_count}</b>\n\n"
        "🎁 <b>پاداش‌ها</b>\n"
        f"• کدهای جایزه: <b>{promo_count}</b>\n"
        f"• دفعات استفاده از کدها: <b>{promo_used}</b>\n"
        f"• پاداش‌های روزانه دریافت‌شده: <b>{daily_rewards}</b>\n"
    )

    if top:
        text += "\n🏆 <b>۵ بازیکن برتر</b>\n"
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        for i, row in enumerate(top):
            name = row["cod_name"] or "بدون نام"
            text += (
                f"{medals[i]} <b>{name}</b> — {row['rank']} "
                f"({row['rank_points']} امتیاز)\n"
                f"   🎮 {row['played']} | 🏆 {row['wins']} | 🥉 {row['top3']} | 🪙 {row['prizes']}\n"
            )
    else:
        text += "\n🏆 هنوز آمار بازیکنان برای رتبه‌بندی ثبت نشده است.\n"

    if week_customs:
        text += "\n📈 <b>کاستوم‌های ۷ روز اخیر</b>\n"
        for row in week_customs:
            text += f"• {row['date']}: <b>{row['count']}</b> کاستوم\n"

    if week_tx:
        text += "\n💹 <b>گردش کوین در ۷ روز اخیر</b>\n"
        for row in week_tx:
            text += f"• {row['day']}: +{row['income']} / -{row['expense']} 🪙\n"

    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("🔄 بروزرسانی", callback_data="admin:stats"),
        types.InlineKeyboardButton("👥 کاربران", callback_data="admin:users")
    )
    keyboard.add(
        types.InlineKeyboardButton("🎮 کاستوم‌ها", callback_data="admin:customs"),
        types.InlineKeyboardButton("🎟️ کدها", callback_data="admin:promo")
    )
    keyboard.add(types.InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin"))

    bot.send_message(chat_id, text, reply_markup=keyboard)


# =========================================================
# ADMIN USERS
# =========================================================

def admin_users(chat_id, message_id=None):

    conn = db()

    rows = conn.execute(
        """
        SELECT
            id,
            cod_name,
            coins,
            banned

        FROM users

        ORDER BY id DESC

        LIMIT 30
        """
    ).fetchall()

    conn.close()

    keyboard = types.InlineKeyboardMarkup(
        row_width=1
    )

    for row in rows:

        icon = (
            "🚫"
            if row["banned"]
            else "👤"
        )

        keyboard.add(
            types.InlineKeyboardButton(

                f"{icon} "
                f"{row['cod_name'] or 'بدون نام'} | "
                f"{row['coins']} 🪙",

                callback_data=
                f"admin:user:{row['id']}"
            )
        )

    keyboard.add(
        types.InlineKeyboardButton(
            "🔙 پنل",
            callback_data="admin"
        )
    )

    bot.send_message(
        chat_id,
        "👤 <b>کاربران</b>",
        reply_markup=keyboard
    )


def admin_user_detail(
    chat_id,
    target_id,
    message_id=None
):

    user = get_user(
        target_id
    )

    if not user:

        bot.send_message(
            chat_id,
            "❌ کاربر پیدا نشد."
        )

        return

    keyboard = types.InlineKeyboardMarkup(
        row_width=2
    )

    keyboard.add(
        types.InlineKeyboardButton(
            "💰 تغییر موجودی",
            callback_data=
            f"admin:bal:{target_id}"
        )
    )

    keyboard.add(
        types.InlineKeyboardButton(

            (
                "✅ رفع مسدودی"
                if user["banned"]
                else "🚫 مسدود کردن"
            ),

            callback_data=
            f"admin:ban:{target_id}"
        )
    )

    keyboard.add(
        types.InlineKeyboardButton(
            "🔙 کاربران",
            callback_data="admin:users"
        )
    )

    bot.send_message(

        chat_id,

        "👤 <b>اطلاعات کاربر</b>\n\n"

        f"🆔 ID: "
        f"<code>{target_id}</code>\n"

        f"🎮 نام کالاف: "
        f"<b>{user['cod_name'] or 'ثبت نشده'}</b>\n"

        f"🪙 موجودی: "
        f"<b>{user['coins']}</b>\n"

        f"📌 وضعیت: "
        f"<b>{'مسدود' if user['banned'] else 'فعال'}</b>",

        reply_markup=keyboard
    )


# =========================================================
# AUTOMATIC MANAGER
# =========================================================

def manager_loop():

    while True:

        try:

            conn = db()

            rows = conn.execute(
                """
                SELECT *
                FROM customs

                WHERE status IN(
                    'open',
                    'locked'
                )
                """
            ).fetchall()

            conn.close()

            current = datetime.now(TZ)

            for row in rows:

                start = parse_custom_datetime(row)
                lock_at = start - timedelta(minutes=row["lock_minutes"])
                seconds_left = (start - current).total_seconds()

                # Smart reminders: only participants of this custom receive them,
                # and each event is sent once even though the manager runs repeatedly.
                if row["status"] == "open":
                    if 0 < seconds_left <= 30 * 60:
                        notify_once(
                            row["id"],
                            "reminder_30",
                            "⏰ <b>یادآوری کاستوم</b>\n\n"
                            f"🎮 {row['title']}\n"
                            f"⏱ تا شروع کمتر از ۳۰ دقیقه باقی مانده است.\n"
                            f"🕐 ساعت شروع: <b>{row['time']}</b>"
                        )

                    if 0 < seconds_left <= 10 * 60:
                        notify_once(
                            row["id"],
                            "reminder_10",
                            "⚠️ <b>کاستوم نزدیک است!</b>\n\n"
                            f"🎮 {row['title']}\n"
                            "⏳ کمتر از ۱۰ دقیقه تا شروع باقی مانده. آماده باشید!"
                        )

                    if current >= lock_at and current < start:
                        lock_custom(row["id"], True)

                # Capacity alerts are based on actual active participants.
                count = participant_count(row["id"])
                capacity = MAX_PLAYERS
                if count >= capacity:
                    notify_once(
                        row["id"],
                        "capacity_full",
                        "🚫 <b>ظرفیت کاستوم تکمیل شد!</b>\n\n"
                        f"🎮 {row['title']}\n👥 {count}/{capacity} بازیکن"
                    )
                elif count >= int(capacity * 0.90):
                    notify_once(
                        row["id"],
                        "capacity_90",
                        "🔥 <b>ظرفیت کاستوم در حال تکمیل است!</b>\n\n"
                        f"🎮 {row['title']}\n👥 {count}/{capacity} بازیکن\n"
                        f"⚠️ فقط {capacity-count} جای خالی باقی مانده."
                    )
                elif count >= int(capacity * 0.75):
                    notify_once(
                        row["id"],
                        "capacity_75",
                        "📈 <b>کاستوم شلوغ شد!</b>\n\n"
                        f"🎮 {row['title']}\n👥 {count}/{capacity} بازیکن"
                    )

                if current >= start and row["status"] in ("open", "locked"):
                    start_custom(row["id"])

        except Exception as error:

            print(
                "Manager error:",
                repr(error)
            )

        time.sleep(10)


# =========================================================
# /START
# =========================================================

@bot.message_handler(
    commands=["start"]
)
def start_command(message):

    upsert_user(
        message.from_user
    )

    user_id = message.from_user.id

    if is_banned(user_id):

        bot.send_message(
            message.chat.id,
            "🚫 حساب شما مسدود است."
        )

        return

    referral_id = None

    parts = message.text.split(
        maxsplit=1
    )

    if (
        len(parts) > 1
        and parts[1].startswith("ref_")
    ):

        try:

            referral_id = int(
                parts[1][4:]
            )

        except Exception:

            referral_id = None

    conn = db()

    user = conn.execute(
        """
        SELECT *
        FROM users
        WHERE id=?
        """,
        (user_id,)
    ).fetchone()

    if not user["welcome_bonus_claimed"]:

        try:

            with conn:

                valid_referral = (

                    referral_id is not None

                    and referral_id != user_id

                    and conn.execute(
                        """
                        SELECT id
                        FROM users
                        WHERE id=?
                        """,
                        (referral_id,)
                    ).fetchone()

                    and not conn.execute(
                        """
                        SELECT 1
                        FROM referrals
                        WHERE invitee_id=?
                        """,
                        (user_id,)
                    ).fetchone()
                )

                if valid_referral:

                    conn.execute(
                        """
                        INSERT INTO referrals(
                            inviter_id,
                            invitee_id,
                            created_at
                        )
                        VALUES(
                            ?,
                            ?,
                            ?
                        )
                        """,
                        (
                            referral_id,
                            user_id,
                            now_str()
                        )
                    )

                    add_coins(
                        user_id,
                        100,
                        "welcome_ref",
                        "پاداش ورود با دعوت",
                        None,
                        None,
                        conn
                    )

                    add_coins(
                        referral_id,
                        50,
                        "referral",
                        "پاداش دعوت دوست",
                        None,
                        None,
                        conn
                    )

                else:

                    add_coins(
                        user_id,
                        50,
                        "welcome",
                        "پاداش ورود",
                        None,
                        None,
                        conn
                    )

                conn.execute(
                    """
                    UPDATE users

                    SET welcome_bonus_claimed=1

                    WHERE id=?
                    """,
                    (user_id,)
                )

        finally:

            conn.close()

    else:

        conn.close()

    if not require_channel(
        message.chat.id,
        user_id
    ):
        return

    user = get_user(
        user_id
    )

    if not user["cod_name"]:

        set_state(
            user_id,
            {
                "action": "cod_name"
            }
        )

        bot.send_message(
            message.chat.id,

            "🎮 <b>نام داخل Call of Duty</b>\n\n"

            "لطفاً دقیقاً نامی که داخل کالاف "
            "استفاده می‌کنی را وارد کن."
        )

        return

    send_home(
        message.chat.id,
        user_id
    )


# =========================================================
# /CANCEL
# =========================================================

@bot.message_handler(
    commands=["cancel"]
)
def cancel_command(message):

    clear_state(
        message.from_user.id
    )

    bot.send_message(
        message.chat.id,
        "❌ عملیات لغو شد."
    )

    send_home(
        message.chat.id,
        message.from_user.id
    )


# =========================================================
# TEXT HANDLER
# =========================================================

@bot.message_handler(
    func=lambda message: True,
    content_types=["text"]
)
def text_handler(message):

    user_id = message.from_user.id

    upsert_user(
        message.from_user
    )

    if is_banned(user_id):

        bot.send_message(
            message.chat.id,
            "🚫 حساب شما مسدود است."
        )

        return

    if not require_channel(
        message.chat.id,
        user_id
    ):
        return

    state = get_state(
        user_id
    )

    text = message.text.strip()

    if new_feature_text_handler(message):
        return

    if state:

        # -------------------------
        # USER SUPPORT MESSAGE
        # -------------------------

        if state["action"] == "support_user":

            if text == "لغو":
                clear_state(user_id)
                bot.send_message(message.chat.id, "❌ پشتیبانی لغو شد.")
                send_home(message.chat.id, user_id)
                return

            if not text:
                bot.send_message(message.chat.id, "❌ پیام نمی‌تواند خالی باشد.")
                return

            send_support_to_admin(message)
            return

        # -------------------------
        # ADMIN SUPPORT REPLY
        # -------------------------

        if state["action"] == "support_reply":

            if not is_admin(user_id):
                clear_state(user_id)
                return

            if text == "لغو":
                clear_state(user_id)
                bot.send_message(message.chat.id, "❌ پاسخ لغو شد.")
                return

            target_user_id = state.get("target")
            target = get_user(target_user_id) if target_user_id else None

            if not target:
                clear_state(user_id)
                bot.send_message(message.chat.id, "❌ کاربر پیدا نشد.")
                return

            try:
                bot.send_message(
                    target_user_id,
                    "📩 <b>پاسخ پشتیبانی</b>\n\n"
                    f"{html.escape(text)}"
                )
                bot.send_message(
                    message.chat.id,
                    "✅ پاسخ برای کاربر ارسال شد.\n\n"
                    "اگر لازم است پیام دیگری بفرستید، دوباره روی «↩️ پاسخ به کاربر» بزنید."
                )
            except Exception as error:
                print("Support reply error:", repr(error))
                bot.send_message(
                    message.chat.id,
                    "❌ ارسال پاسخ انجام نشد. ممکن است کاربر ربات را مسدود کرده باشد."
                )

            clear_state(user_id)
            return

        # -------------------------
        # COD NAME
        # -------------------------

        if state["action"] == "cod_name":

            if (
                len(text) < 2
                or len(text) > 50
            ):

                bot.send_message(
                    message.chat.id,
                    "❌ نام باید بین "
                    "2 تا 50 کاراکتر باشد."
                )

                return

            conn = db()

            conn.execute(
                """
                UPDATE users

                SET cod_name=?

                WHERE id=?
                """,
                (
                    text,
                    user_id
                )
            )

            conn.commit()
            conn.close()

            clear_state(
                user_id
            )

            bot.send_message(
                message.chat.id,
                "✅ نام کالاف ثبت شد."
            )

            send_home(
                message.chat.id,
                user_id
            )

            return

        # -------------------------
        # CHANGE COD NAME
        # -------------------------

        if state["action"] == "change_cod":

            user = get_user(
                user_id
            )

            if user["last_name_change"]:

                last_change = datetime.strptime(
                    user["last_name_change"],
                    "%Y-%m-%d %H:%M:%S"
                ).replace(
                    tzinfo=TZ
                )

                elapsed = (
                    datetime.now(TZ)
                    - last_change
                ).total_seconds()

                if elapsed < 7 * 86400:

                    remaining = (
                        7 * 86400
                        - elapsed
                    )

                    days = int(
                        remaining
                        // 86400
                    )

                    bot.send_message(
                        message.chat.id,

                        "⏳ تغییر نام هر "
                        "7 روز یک‌بار است.\n\n"

                        f"حدود {days} روز دیگر."
                    )

                    return

            if (
                len(text) < 2
                or len(text) > 50
            ):

                bot.send_message(
                    message.chat.id,
                    "❌ نام نامعتبر است."
                )

                return

            conn = db()

            conn.execute(
                """
                UPDATE users

                SET
                    cod_name=?,
                    last_name_change=?

                WHERE id=?
                """,
                (
                    text,
                    now_str(),
                    user_id
                )
            )

            conn.commit()
            conn.close()

            clear_state(
                user_id
            )

            bot.send_message(
                message.chat.id,
                "✅ نام کالاف تغییر کرد."
            )

            send_home(
                message.chat.id,
                user_id
            )

            return

        # -------------------------
        # CREATE CUSTOM
        # -------------------------

        if state["action"] == "create_custom":

            if is_admin(user_id):

                handle_create_text(
                    user_id,
                    text
                )

            return

        # -------------------------
        # PROMO REDEEM
        # -------------------------

        if state["action"] == "promo_redeem":

            clear_state(user_id)
            _, result = redeem_promo_code(user_id, text)
            bot.send_message(message.chat.id, result)
            send_home(message.chat.id, user_id)
            return

        # -------------------------
        # PROMO ADMIN CREATE
        # -------------------------

        if state["action"] == "promo_create":

            if is_admin(user_id):
                handle_promo_admin_text(user_id, text)
            return

        # -------------------------
        # ADMIN BALANCE
        # -------------------------

        if state["action"] == "admin_balance":

            target_id = state["target"]
            value_text = text.strip()
            negative = value_text.startswith("-")
            if negative: value_text = value_text[1:]
            number = valid_int(value_text, 0)
            if number is None:
                bot.send_message(user_id, "❌ عدد صحیح وارد کنید.")
                return
            if negative: number = -number
            set_state(user_id,{"action":"admin_balance_confirm","target":target_id,"amount":number})
            kb=types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("✅ تأیید تغییر",callback_data="admin:confirm:balance"),types.InlineKeyboardButton("❌ لغو",callback_data="admin:confirm:cancel"))
            bot.send_message(user_id,f"🔐 <b>تأیید عملیات حساس</b>\n\nکاربر: <code>{target_id}</code>\nتغییر: <b>{number:+d} 🪙</b>\n\nآیا اجرا شود؟",reply_markup=kb)
            return

        if state["action"] == "admin_balance_confirm":
            bot.send_message(user_id,"🔐 ابتدا از دکمه تأیید یا لغو استفاده کنید.")
            return

        # -------------------------
        # BROADCAST
        # -------------------------

        if state["action"] == "broadcast":

            clear_state(
                user_id
            )

            conn = db()

            users = conn.execute(
                """
                SELECT id
                FROM users

                WHERE banned=0
                """
            ).fetchall()

            conn.close()

            success = 0

            for user in users:

                try:

                    bot.send_message(
                        user["id"],
                        text
                    )

                    success += 1

                except Exception:
                    pass

            bot.send_message(
                user_id,
                f"📢 پیام برای "
                f"{success} کاربر ارسال شد."
            )

            admin_menu(
                user_id
            )

            return

        # -------------------------
        # ROOM SET
        # -------------------------

        if state["action"] == "roomset":

            custom_id = state["cid"]

            conn = db()

            if state["step"] == "room_id":

                conn.execute(
                    """
                    UPDATE customs

                    SET room_id=?

                    WHERE id=?
                    """,
                    (
                        text,
                        custom_id
                    )
                )

                conn.commit()
                conn.close()

                state["step"] = "password"

                bot.send_message(
                    user_id,
                    "🔐 Password را وارد کنید."
                )

                return

            conn.execute(
                """
                UPDATE customs

                SET room_password=?

                WHERE id=?
                """,
                (
                    text,
                    custom_id
                )
            )

            conn.commit()
            conn.close()

            clear_state(
                user_id
            )

            bot.send_message(
                user_id,
                "✅ اطلاعات روم ذخیره شد."
            )

            room_conn = db()
            room_row = room_conn.execute("SELECT * FROM customs WHERE id=?", (custom_id,)).fetchone()
            room_conn.close()
            if room_row and room_row["status"] in ("locked", "started"):
                notify_once(
                    custom_id,
                    "room_ready",
                    "🔑 <b>اطلاعات روم آماده شد!</b>\n\n" + room_text(room_row)
                )

            admin_custom_detail(
                user_id,
                custom_id
            )

            return

    # =====================================================
    # TEXT MENU FALLBACK
    # =====================================================

    mapping = {

        "🎮 کاستوم‌ها":
            "customs",

        "💰 کیف پول":
            "wallet",

        "👤 پروفایل":
            "profile",

        "🏆 لیدربورد":
            "leaderboard",

        "🎁 پاداش روزانه":
            "daily",

        "🎟️ کد جایزه":
            "promo",

        "🎁 دعوت دوستان":
            "referral",

        "👥 دوستان":
            "friends",

        "🎯 مأموریت‌های روزانه":
            "missions",

        "🎉 کمپین‌ها":
            "campaigns",

        "🔔 اعلان‌ها":
            "notifications",

        "📜 قوانین":
            "rules",

        "📖 راهنما":
            "help",

        "🆘 پشتیبانی":
            "support",

        "👑 پنل مدیریت":
            "admin"
    }

    if text in mapping:

        process_callback(
            user_id,
            mapping[text],
            message.chat.id
        )

    else:

        send_home(
            message.chat.id,
            user_id
        )


# =========================================================
# NEW FEATURES - FRIENDS / NOTIFICATIONS / MISSIONS / CAMPAIGNS
# =========================================================

def notify_user(user_id, title, body, kind="general"):
    conn=db()
    conn.execute("INSERT INTO notifications(user_id,title,body,kind,is_read,created_at) VALUES(?,?,?,?,0,?)",(user_id,title,body,kind,now_str()))
    conn.commit(); conn.close()
    try: bot.send_message(user_id, f"🔔 <b>{html.escape(title)}</b>\n\n{html.escape(body)}")
    except Exception: pass

def show_notifications(chat_id,user_id,message_id=None):
    conn=db(); rows=conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 30",(user_id,)).fetchall(); unread=conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",(user_id,)).fetchone()[0]; conn.close()
    text=f"🔔 <b>اعلان‌ها</b>\n\nخوانده‌نشده: <b>{unread}</b>\n\n"
    if rows:
        for r in rows: text += f"{'🔵' if not r['is_read'] else '⚪'} <b>{html.escape(r['title'])}</b>\n{html.escape(r['body'])}\n<i>{r['created_at']}</i>\n\n"
    else: text += "اعلانی وجود ندارد."
    kb=types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("✅ خواندن همه",callback_data="notifications:readall")); kb.add(types.InlineKeyboardButton("🔙 منوی اصلی",callback_data="home"))
    render_message(chat_id,text,kb,message_id)

def mark_notifications_read(user_id):
    conn=db(); conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?",(user_id,)); conn.commit(); conn.close()

def show_friends(chat_id,user_id,message_id=None):
    conn=db(); rows=conn.execute("SELECT u.id,u.cod_name FROM friends f JOIN users u ON u.id=f.friend_id WHERE f.user_id=? ORDER BY u.cod_name",(user_id,)).fetchall(); conn.close()
    text="👥 <b>دوستان من</b>\n\n" + ("\n".join(f"• {html.escape(r['cod_name'] or 'بدون نام')}" for r in rows) if rows else "هنوز دوستی اضافه نکرده‌ای.")
    kb=types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("➕ افزودن دوست",callback_data="friends:add"))
    for r in rows: kb.add(types.InlineKeyboardButton(f"❌ حذف {r['cod_name'][:20]}",callback_data=f"friend:remove:{r['id']}"))
    kb.add(types.InlineKeyboardButton("🔙 منوی اصلی",callback_data="home")); render_message(chat_id,text,kb,message_id)

def add_friend(user_id,friend_id):
    if user_id==friend_id: return
    conn=db(); exists=conn.execute("SELECT 1 FROM users WHERE id=?",(friend_id,)).fetchone()
    if exists: conn.execute("INSERT OR IGNORE INTO friends(user_id,friend_id,created_at) VALUES(?,?,?)",(user_id,friend_id,now_str())); conn.commit()
    conn.close()

def remove_friend(user_id,friend_id):
    conn=db(); conn.execute("DELETE FROM friends WHERE user_id=? AND friend_id=?",(user_id,friend_id)); conn.commit(); conn.close()

def search_user_by_cod(name):
    conn=db(); rows=conn.execute("SELECT id,cod_name FROM users WHERE cod_name LIKE ? LIMIT 10",("%"+name+"%",)).fetchall(); conn.close(); return rows

def mission_today(): return datetime.now(TZ).strftime("%Y-%m-%d")

def mission_progress_value(user_id,mission_id):
    conn=db(); r=conn.execute("SELECT progress,claimed FROM mission_progress WHERE user_id=? AND mission_id=? AND day=?",(user_id,mission_id,mission_today())).fetchone(); conn.close(); return (r[0],r[1]) if r else (0,0)

def mission_type_value(user_id,typ):
    conn=db()
    if typ=="games": v=conn.execute("SELECT COUNT(DISTINCT custom_id) FROM participants WHERE user_id=? AND left_at IS NULL AND joined_at LIKE ?",(user_id,mission_today()+"%" )).fetchone()[0]
    elif typ=="wins": v=conn.execute("SELECT COUNT(DISTINCT custom_id) FROM result_history WHERE user_id=? AND place=1 AND created_at LIKE ?",(user_id,mission_today()+"%" )).fetchone()[0]
    elif typ=="referrals": v=conn.execute("SELECT COUNT(*) FROM referrals WHERE inviter_id=? AND created_at LIKE ?",(user_id,mission_today()+"%" )).fetchone()[0]
    else: v=0
    conn.close(); return v

def show_missions(chat_id,user_id,message_id=None):
    conn=db(); rows=conn.execute("SELECT * FROM daily_missions WHERE active=1 ORDER BY id DESC").fetchall(); conn.close()
    text="🎯 <b>مأموریت‌های روزانه</b>\n\n"; kb=types.InlineKeyboardMarkup()
    if not rows: text += "امروز مأموریتی تنظیم نشده است."
    for r in rows:
        current=max(mission_progress_value(user_id,r['id'])[0],mission_type_value(user_id,r['mission_type']))
        text += f"🎯 {html.escape(r['title'])}\nپیشرفت: <b>{min(current,r['target'])}/{r['target']}</b> | جایزه: <b>{r['reward']} 🪙</b>\n\n"
        if current>=r['target'] and not mission_progress_value(user_id,r['id'])[1]: kb.add(types.InlineKeyboardButton(f"🎁 دریافت {r['reward']} 🪙",callback_data=f"mission:claim:{r['id']}"))
    kb.add(types.InlineKeyboardButton("🔙 منوی اصلی",callback_data="home")); render_message(chat_id,text,kb,message_id)

def claim_mission(chat_id,user_id,mid):
    conn=db()
    try:
        conn.execute("BEGIN IMMEDIATE"); r=conn.execute("SELECT * FROM daily_missions WHERE id=? AND active=1",(mid,)).fetchone()
        if not r: raise ValueError
        current=max(mission_progress_value(user_id,mid)[0],mission_type_value(user_id,r['mission_type']))
        pr=conn.execute("SELECT progress,claimed FROM mission_progress WHERE user_id=? AND mission_id=? AND day=?",(user_id,mid,mission_today())).fetchone()
        if current<r['target'] or (pr and pr['claimed']): conn.rollback(); bot.send_message(chat_id,"❌ مأموریت هنوز کامل نشده یا قبلاً دریافت شده است."); return
        conn.execute("INSERT INTO mission_progress(user_id,mission_id,day,progress,claimed) VALUES(?,?,?,?,1) ON CONFLICT(user_id,mission_id,day) DO UPDATE SET progress=?,claimed=1",(user_id,mid,mission_today(),current,current))
        add_coins(user_id,r['reward'],"daily_mission",r['title'],None,None,conn); conn.commit(); bot.send_message(chat_id,f"🎉 مأموریت انجام شد و <b>{r['reward']} کوین</b> گرفتی!")
    except Exception: conn.rollback(); bot.send_message(chat_id,"❌ دریافت پاداش انجام نشد.")
    finally: conn.close()
    show_missions(chat_id,user_id)

def show_campaigns(chat_id,user_id,message_id=None):
    now=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"); conn=db(); rows=conn.execute("SELECT * FROM campaigns WHERE active=1 AND start_at<=? AND end_at>=? ORDER BY id DESC",(now,now)).fetchall(); conn.close()
    text="🎉 <b>کمپین‌های فعال</b>\n\n"; kb=types.InlineKeyboardMarkup()
    if not rows: text+="کمپین فعالی وجود ندارد."
    for r in rows:
        conn=db(); claimed=conn.execute("SELECT 1 FROM campaign_claims WHERE campaign_id=? AND user_id=?",(r['id'],user_id)).fetchone(); conn.close()
        text+=f"🎉 <b>{html.escape(r['title'])}</b>\n{html.escape(r['description'])}\n🎁 پاداش: {r['reward']} 🪙\n⏰ تا: {r['end_at']}\n\n"
        if not claimed: kb.add(types.InlineKeyboardButton(f"🎁 دریافت {r['reward']} 🪙",callback_data=f"campaign:claim:{r['id']}"))
    kb.add(types.InlineKeyboardButton("🔙 منوی اصلی",callback_data="home")); render_message(chat_id,text,kb,message_id)

def claim_campaign(chat_id,user_id,cid):
    conn=db()
    try:
        conn.execute("BEGIN IMMEDIATE"); now=now_str(); r=conn.execute("SELECT * FROM campaigns WHERE id=? AND active=1 AND start_at<=? AND end_at>=?",(cid,now,now)).fetchone()
        if not r: raise ValueError
        if conn.execute("SELECT 1 FROM campaign_claims WHERE campaign_id=? AND user_id=?",(cid,user_id)).fetchone(): raise ValueError
        conn.execute("INSERT INTO campaign_claims(campaign_id,user_id,claimed_at) VALUES(?,?,?)",(cid,user_id,now)); add_coins(user_id,r['reward'],"campaign",r['title'],None,None,conn); conn.commit(); bot.send_message(chat_id,f"🎉 پاداش کمپین <b>{r['reward']} کوین</b> به موجودی اضافه شد.")
    except Exception: conn.rollback(); bot.send_message(chat_id,"❌ دریافت پاداش ممکن نیست.")
    finally: conn.close()
    show_campaigns(chat_id,user_id)

def show_friend_invite_list(chat_id,user_id,custom_id):
    part=user_participation(custom_id,user_id)
    if not part or part["left_at"] is not None:
        bot.send_message(chat_id,"❌ ابتدا باید در کاستوم عضو باشی."); return
    conn=db(); rows=conn.execute("SELECT u.id,u.cod_name FROM friends f JOIN users u ON u.id=f.friend_id WHERE f.user_id=?",(user_id,)).fetchall(); conn.close()
    kb=types.InlineKeyboardMarkup();
    for r in rows: kb.add(types.InlineKeyboardButton(f"📨 {r['cod_name'][:25]}",callback_data=f"friend:send:{custom_id}:{part['team_no']}:{r['id']}"))
    kb.add(types.InlineKeyboardButton("🔙 کاستوم",callback_data=f"custom:{custom_id}")); bot.send_message(chat_id,"👥 <b>دعوت دوستان به تیم</b>\n\nیک دوست را انتخاب کن:",reply_markup=kb)

# =========================================================
# NEW ADMIN FEATURES
# =========================================================

def admin_newfeatures(chat_id):
    kb=types.InlineKeyboardMarkup(row_width=2)
    for label,data in [("👥 محرومیت زمانی","admin:banpanel"),("🔎 جستجوی پیشرفته","admin:search"),("📢 اطلاع‌رسانی هدفمند","admin:broadcast_target"),("🎯 مأموریت‌ها","admin:missions"),("🎉 کمپین‌ها","admin:campaigns"),("📈 آمار پیشرفته","admin:advanced_stats")]: kb.add(types.InlineKeyboardButton(label,callback_data=data))
    kb.add(types.InlineKeyboardButton("🔐 تأیید عملیات حساس","admin:confirm:help")); kb.add(types.InlineKeyboardButton("🛡️ امنیت","admin:security"),types.InlineKeyboardButton("🔙 پنل",callback_data="admin")); bot.send_message(chat_id,"🆕 <b>قابلیت‌های جدید</b>",reply_markup=kb)

def admin_ban_start(user_id):
    set_state(user_id,{"action":"admin_search_ban"}); bot.send_message(user_id,"🚫 ID کاربر یا COD Name را وارد کنید:")

def unban_user(target,actor):
    conn=db(); conn.execute("UPDATE users SET banned=0,ban_until=NULL,ban_reason='' WHERE id=?",(target,)); security_log("unban",actor_id=actor,target_user_id=target,details="رفع محرومیت",conn=conn); conn.commit(); conn.close()

def admin_targeted_broadcast_menu(chat_id):
    kb=types.InlineKeyboardMarkup(row_width=2); kb.add(types.InlineKeyboardButton("👥 همه کاربران فعال","admin:broadcast:all"),types.InlineKeyboardButton("🔥 کاربران فعال امروز","admin:broadcast:active")); kb.add(types.InlineKeyboardButton("😴 کاربران غیرفعال ۷ روز","admin:broadcast:inactive"),types.InlineKeyboardButton("🎮 شرکت‌کنندگان کاستوم","admin:broadcast:participants")); kb.add(types.InlineKeyboardButton("🔙 پنل",callback_data="admin:newfeatures")); bot.send_message(chat_id,"📢 <b>انتخاب گروه گیرندگان</b>",reply_markup=kb)

def send_targeted_broadcast(actor,text,target):
    conn=db(); params=[]
    if target=="all": rows=conn.execute("SELECT id FROM users WHERE banned=0").fetchall()
    elif target=="active": rows=conn.execute("SELECT id FROM users WHERE banned=0 AND joined_at LIKE ?",(mission_today()+"%",)).fetchall()
    elif target=="inactive": rows=conn.execute("SELECT id FROM users WHERE banned=0 AND datetime(joined_at)<datetime(?)",((datetime.now(TZ)-timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S"),)).fetchall()
    else: rows=conn.execute("SELECT DISTINCT u.id FROM users u JOIN participants p ON p.user_id=u.id WHERE u.banned=0 AND p.joined_at LIKE ?",(mission_today()+"%",)).fetchall()
    conn.close(); ok=0
    for r in rows:
        try: bot.send_message(r['id'],text); ok+=1
        except Exception: pass
    return ok

def admin_missions(chat_id):
    conn=db(); rows=conn.execute("SELECT * FROM daily_missions ORDER BY id DESC").fetchall(); conn.close(); text="🎯 <b>مدیریت مأموریت‌ها</b>\n\n"+("\n".join(f"#{r['id']} | {html.escape(r['title'])} | {r['mission_type']} {r['target']} | {r['reward']} 🪙 | {'فعال' if r['active'] else 'خاموش'}" for r in rows) if rows else "مأموریتی وجود ندارد."); kb=types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("➕ مأموریت جدید",callback_data="admin:mission:create")); kb.add(types.InlineKeyboardButton("🔙 قابلیت‌ها",callback_data="admin:newfeatures")); bot.send_message(chat_id,text,reply_markup=kb)

def admin_campaigns(chat_id):
    conn=db(); rows=conn.execute("SELECT * FROM campaigns ORDER BY id DESC LIMIT 20").fetchall(); conn.close(); text="🎉 <b>مدیریت کمپین‌ها</b>\n\n"+("\n".join(f"#{r['id']} | {html.escape(r['title'])} | {r['reward']} 🪙 | {r['start_at']} تا {r['end_at']}" for r in rows) if rows else "کمپینی وجود ندارد."); kb=types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("➕ کمپین جدید",callback_data="admin:campaign:create")); kb.add(types.InlineKeyboardButton("🔙 قابلیت‌ها",callback_data="admin:newfeatures")); bot.send_message(chat_id,text,reply_markup=kb)

def admin_advanced_stats(chat_id):
    conn=db(); today=mission_today(); vals={
      'users':conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
      'today_users':conn.execute("SELECT COUNT(*) FROM users WHERE joined_at LIKE ?",(today+'%',)).fetchone()[0],
      'coins':conn.execute("SELECT COALESCE(SUM(coins),0) FROM users").fetchone()[0],
      'customs':conn.execute("SELECT COUNT(*) FROM customs").fetchone()[0],
      'active_customs':conn.execute("SELECT COUNT(*) FROM customs WHERE status IN ('open','locked','started')").fetchone()[0],
      'entries':conn.execute("SELECT COALESCE(SUM(entry_paid),0) FROM participants WHERE entry_paid>0").fetchone()[0],
      'prizes':conn.execute("SELECT COALESCE(SUM(prize),0) FROM result_history").fetchone()[0],
      'missions':conn.execute("SELECT COUNT(*) FROM daily_missions WHERE active=1").fetchone()[0],
      'campaigns':conn.execute("SELECT COUNT(*) FROM campaigns WHERE active=1").fetchone()[0]}; conn.close()
    text=("📈 <b>آمار پیشرفته</b>\n\n"+f"👥 کل کاربران: <b>{vals['users']}</b>\n📅 کاربران امروز: <b>{vals['today_users']}</b>\n🪙 موجودی کل کوین: <b>{vals['coins']}</b>\n🎮 کل کاستوم‌ها: <b>{vals['customs']}</b>\n🟢 کاستوم فعال: <b>{vals['active_customs']}</b>\n💰 مجموع ورودیه‌های ثبت‌شده: <b>{vals['entries']}</b>\n🏆 مجموع جوایز: <b>{vals['prizes']}</b>\n🎯 مأموریت فعال: <b>{vals['missions']}</b>\n🎉 کمپین فعال: <b>{vals['campaigns']}</b>")
    kb=types.InlineKeyboardMarkup(); kb.add(types.InlineKeyboardButton("🔄 بروزرسانی",callback_data="admin:advanced_stats"),types.InlineKeyboardButton("🔙 قابلیت‌ها",callback_data="admin:newfeatures")); bot.send_message(chat_id,text,reply_markup=kb)

def parse_ban_duration(v):
    v=v.strip().lower()
    if v in ('permanent','دائمی'): return None
    m=re.fullmatch(r'(\d+)([hd])',v)
    if not m:return False
    return datetime.now(TZ)+timedelta(hours=int(m.group(1)) if m.group(2)=='h' else 24*int(m.group(1)))

def perform_ban(target,actor,duration,reason=''):
    until=duration.strftime('%Y-%m-%d %H:%M:%S') if duration else None
    conn=db(); conn.execute("UPDATE users SET banned=1,ban_until=?,ban_reason=? WHERE id=?",(until,reason,target)); security_log('ban',actor_id=actor,target_user_id=target,details=(until or 'دائمی')+' | '+reason,conn=conn); conn.commit(); conn.close()
    try: bot.send_message(target,"🚫 حساب شما موقتاً/دائماً مسدود شد." + (f"\n⏰ تا: {until}" if until else ""))
    except Exception: pass

def confirm_sensitive_action(admin_id,key):
    if key=='help': bot.send_message(admin_id,"🔐 عملیات حساس در بخش‌های مالی و مدیریتی با تأیید نهایی اجرا می‌شوند.")

def admin_search(chat_id,query):
    conn=db(); rows=conn.execute("SELECT id,cod_name,coins,banned,ban_until FROM users WHERE id=? OR cod_name LIKE ? LIMIT 20",(int(query) if query.isdigit() else -1,'%'+query+'%')).fetchall(); conn.close(); text="🔎 <b>نتایج جستجو</b>\n\n"; kb=types.InlineKeyboardMarkup()
    customs=[]
    if query.isdigit(): customs=conn.execute("SELECT id,title,status,date,time FROM customs WHERE id=?",(int(query),)).fetchall()
    conn.close()
    if not rows and not customs:text+='موردی پیدا نشد.'
    for r in rows:
        action="admin:unban" if r['banned'] else "admin:ban"
        text+=f"👤 {html.escape(r['cod_name'] or 'بدون نام')} | ID <code>{r['id']}</code> | {r['coins']} 🪙 | {'🚫' if r['banned'] else '✅'}\n"; kb.add(types.InlineKeyboardButton(f"{'✅ رفع' if r['banned'] else '🚫 محروم'} {r['cod_name'][:18]}",callback_data=f"{action}:{r['id']}"))
    for c in customs:text+=f"\n🎮 کاستوم #{c['id']} | {html.escape(c['title'])} | {c['date']} {c['time']} | {c['status']}"
    kb.add(types.InlineKeyboardButton("🔙 قابلیت‌ها",callback_data="admin:newfeatures")); bot.send_message(chat_id,text,reply_markup=kb)

def new_feature_text_handler(message):
    uid=message.from_user.id; state=get_state(uid); text=message.text.strip() if message.text else ''
    if not state:return False
    action=state.get('action')
    if action=='friend_add':
        rows=search_user_by_cod(text); clear_state(uid); kb=types.InlineKeyboardMarkup()
        for r in rows: kb.add(types.InlineKeyboardButton(f"➕ {r['cod_name'][:25]}",callback_data=f"friend:add:{r['id']}"))
        kb.add(types.InlineKeyboardButton("🔙 دوستان",callback_data="friends")); bot.send_message(uid,"🔎 کاربران پیدا شده:",reply_markup=kb); return True
    if action=='admin_search':
        clear_state(uid); admin_search(uid,text); return True
    if action=='admin_search_ban':
        rows=search_user_by_cod(text) if not text.isdigit() else [get_user(int(text))]
        rows=[r for r in rows if r]
        if not rows: bot.send_message(uid,"❌ کاربر پیدا نشد."); return True
        target=rows[0]['id']; state['action']='ban_user'; state['target']=target; state['step']='duration'; bot.send_message(uid,"🚫 مدت محرومیت: 1h / 1d / 7d / permanent"); return True
    if action=='ban_user' and state.get('step')=='duration':
        dur=parse_ban_duration(text)
        if dur is False: bot.send_message(uid,"❌ فرمت نامعتبر است."); return True
        state['step']='reason'; state['duration']=dur; bot.send_message(uid,"📝 دلیل محرومیت را وارد کن:"); return True
    if action=='ban_user' and state.get('step')=='reason':
        perform_ban(state['target'],uid,state['duration'],text); clear_state(uid); bot.send_message(uid,"✅ محرومیت ثبت شد."); return True
    if action=='target_broadcast':
        target=state['target']; clear_state(uid); ok=send_targeted_broadcast(uid,text,target); bot.send_message(uid,f"📢 پیام برای <b>{ok}</b> کاربر ارسال شد."); return True
    if action=='mission_create':
        d=state.setdefault('data',{}); step=state.get('step')
        if step=='title': d['title']=text; state['step']='type'; bot.send_message(uid,"نوع را وارد کن: games / wins / referrals"); return True
        if step=='type' and text in ('games','wins','referrals'): d['type']=text; state['step']='target'; bot.send_message(uid,"هدف عددی:"); return True
        if step=='target':
            n=valid_int(text,1); 
            if n is None: bot.send_message(uid,"❌ عدد صحیح وارد کن."); return True
            d['target']=n; state['step']='reward'; bot.send_message(uid,"پاداش کوینی:"); return True
        if step=='reward':
            n=valid_int(text,1)
            if n is None: bot.send_message(uid,"❌ عدد صحیح وارد کن."); return True
            d['reward']=n; conn=db(); conn.execute("INSERT INTO daily_missions(title,mission_type,target,reward,active,created_at) VALUES(?,?,?,?,1,?)",(d['title'],d['type'],d['target'],d['reward'],now_str())); conn.commit(); conn.close(); clear_state(uid); bot.send_message(uid,"✅ مأموریت ساخته شد."); admin_missions(uid); return True
    if action=='campaign_create':
        d=state.setdefault('data',{}); step=state.get('step')
        if step=='title': d['title']=text; state['step']='desc'; bot.send_message(uid,"توضیحات کمپین:"); return True
        if step=='desc': d['desc']=text; state['step']='reward'; bot.send_message(uid,"پاداش کوینی:"); return True
        if step=='reward':
            n=valid_int(text,1)
            if n is None: bot.send_message(uid,"❌ عدد صحیح."); return True
            d['reward']=n; state['step']='start'; bot.send_message(uid,"شروع: YYYY-MM-DD HH:MM"); return True
        if step=='start':
            try: datetime.strptime(text,'%Y-%m-%d %H:%M'); d['start']=text+':00'; state['step']='end'; bot.send_message(uid,"پایان: YYYY-MM-DD HH:MM");
            except: bot.send_message(uid,"❌ فرمت تاریخ نامعتبر.")
            return True
        if step=='end':
            try:
                datetime.strptime(text,'%Y-%m-%d %H:%M'); d['end']=text+':00'; conn=db(); conn.execute("INSERT INTO campaigns(title,description,reward,start_at,end_at,active,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",(d['title'],d['desc'],d['reward'],d['start'],d['end'],1,uid,now_str())); conn.commit(); conn.close(); clear_state(uid); bot.send_message(uid,"✅ کمپین ساخته شد."); admin_campaigns(uid)
            except: bot.send_message(uid,"❌ فرمت تاریخ نامعتبر.")
            return True
    return False

# =========================================================
# CALLBACK PROCESSOR
# =========================================================

def process_callback(
    user_id,
    data,
    chat_id,
    message_id=None
):

    # -----------------------------------------------------
    # NEW FEATURES
    # -----------------------------------------------------
    if data == "friends":
        show_friends(chat_id, user_id, message_id)
        return
    if data == "friends:add":
        set_state(user_id, {"action":"friend_add"})
        bot.send_message(chat_id, "👤 نام کالاف دوستت را دقیق وارد کن:")
        return
    if data.startswith("friend:add:"):
        fid=int(data.split(":")[2]); add_friend(user_id,fid); show_friends(chat_id,user_id)
        return
    if data.startswith("friend:remove:"):
        fid=int(data.split(":")[2]); remove_friend(user_id,fid); show_friends(chat_id,user_id)
        return
    if data.startswith("friend:invite:"):
        cid=int(data.split(":")[2]); show_friend_invite_list(chat_id,user_id,cid); return
    if data.startswith("friendinvite:"):
        _,cid,team,inviter=data.split(":"); result=join_custom(user_id,int(cid),int(team));
        bot.send_message(chat_id, "✅ به تیم دوستت پیوستی." if result=="ok" else "❌ دعوت دیگر معتبر نیست یا ظرفیت تیم تکمیل شده."); return
    if data.startswith("friend:send:"):
        _,_,cid,team,fid=data.split(":");
        try: kb_inv=types.InlineKeyboardMarkup(); kb_inv.add(types.InlineKeyboardButton("✅ پیوستن به تیم",callback_data=f"friendinvite:{cid}:{team}:{user_id}")); bot.send_message(int(fid),f"📨 دوستت تو را به تیم {team} کاستوم #{cid} دعوت کرده است.",reply_markup=kb_inv)
        except Exception: pass
        bot.send_message(chat_id,"✅ دعوت ارسال شد."); return
    if data == "missions":
        show_missions(chat_id,user_id,message_id); return
    if data.startswith("mission:claim:"):
        claim_mission(chat_id,user_id,int(data.split(":")[2])); return
    if data == "campaigns":
        show_campaigns(chat_id,user_id,message_id); return
    if data.startswith("campaign:claim:"):
        claim_campaign(chat_id,user_id,int(data.split(":")[2])); return
    if data == "notifications":
        show_notifications(chat_id,user_id,message_id); return
    if data == "notifications:readall":
        mark_notifications_read(user_id); show_notifications(chat_id,user_id,message_id); return
    if data == "admin:newfeatures" and is_admin(user_id):
        admin_newfeatures(chat_id); return
    if data == "admin:banpanel" and is_admin(user_id):
        admin_ban_start(user_id); return
    if data == "admin:search" and is_admin(user_id):
        set_state(user_id,{"action":"admin_search"}); bot.send_message(chat_id,"🔎 شناسه تلگرام، نام کالاف یا شماره کاستوم/پرداخت را وارد کنید:"); return
    if data == "admin:broadcast_target" and is_admin(user_id):
        admin_targeted_broadcast_menu(chat_id); return
    if data.startswith("admin:broadcast:") and is_admin(user_id):
        target=data.split(":",2)[2]; set_state(user_id,{"action":"target_broadcast","target":target}); bot.send_message(chat_id,"📢 متن پیام را ارسال کنید:"); return
    if data == "admin:missions" and is_admin(user_id):
        admin_missions(chat_id); return
    if data == "admin:mission:create" and is_admin(user_id):
        set_state(user_id,{"action":"mission_create","step":"title","data":{}}); bot.send_message(chat_id,"🎯 عنوان مأموریت را وارد کنید:"); return
    if data == "admin:campaigns" and is_admin(user_id):
        admin_campaigns(chat_id); return
    if data == "admin:campaign:create" and is_admin(user_id):
        set_state(user_id,{"action":"campaign_create","step":"title","data":{}}); bot.send_message(chat_id,"🎉 عنوان کمپین را وارد کنید:"); return
    if data.startswith("admin:ban:") and is_admin(user_id):
        target=int(data.split(":")[2]); set_state(user_id,{"action":"ban_user","target":target,"step":"duration"}); bot.send_message(chat_id,"🚫 مدت محرومیت را وارد کن: 1h / 1d / 7d / permanent"); return
    if data.startswith("admin:unban:") and is_admin(user_id):
        target=int(data.split(":")[2]); unban_user(target,user_id); bot.send_message(chat_id,"✅ محرومیت برداشته شد."); return
    if data == "admin:confirm:cancel" and is_admin(user_id):
        clear_state(user_id); bot.send_message(chat_id,"❌ عملیات لغو شد."); admin_menu(chat_id); return
    if data == "admin:confirm:balance" and is_admin(user_id):
        st=get_state(user_id)
        if not st or st.get("action")!="admin_balance_confirm": bot.send_message(chat_id,"❌ عملیات منقضی شده است."); return
        target=st["target"]; amount=st["amount"]; conn=db()
        with conn:
            add_coins(target,amount,"admin_adjust","تغییر موجودی توسط مدیریت",None,user_id,conn)
            security_log("balance_adjust",actor_id=user_id,target_user_id=target,details=f"تغییر {amount:+d} سکه با تأیید دومرحله‌ای",conn=conn)
        conn.close(); clear_state(user_id); bot.send_message(chat_id,"✅ تغییر موجودی با تأیید دومرحله‌ای انجام شد."); admin_user_detail(chat_id,target); return
    if data.startswith("admin:confirm:results:") and is_admin(user_id):
        cid=int(data.split(":")[3]); st=get_state(user_id)
        if not st or st.get("cid")!=cid or st.get("step")!="confirm": bot.send_message(chat_id,"❌ عملیات منقضی شده است."); return
        success,result=submit_results(cid,st["selected"],allow_correction=(st.get("action")=="results_edit")); clear_state(user_id); bot.send_message(chat_id,"✅ نتیجه با موفقیت ثبت/اصلاح شد و جوایز پردازش شدند." if success else f"❌ {result}"); return
    if data.startswith("admin:confirm:") and is_admin(user_id):
        confirm_sensitive_action(user_id, data.split(":",2)[2]); return

    # -----------------------------------------------------
    # HOME
    # -----------------------------------------------------

    if data == "home":

        try:

            bot.edit_message_text(
                "🎮 <b>کاستوم کالاف</b>\n\n"
                "منوی اصلی:",

                chat_id,
                message_id,

                reply_markup=
                main_menu(user_id)
            )

        except Exception:

            send_home(
                chat_id,
                user_id
            )

    # -----------------------------------------------------
    # CHANNEL
    # -----------------------------------------------------

    elif data == "check_channel":

        if member_status(user_id):

            clear_state(
                user_id
            )

            user = get_user(
                user_id
            )

            if not user["cod_name"]:

                set_state(
                    user_id,
                    {
                        "action":
                        "cod_name"
                    }
                )

                bot.send_message(
                    chat_id,
                    "🎮 نام داخل کالاف را وارد کنید:"
                )

            else:

                send_home(
                    chat_id,
                    user_id
                )

        else:

            bot.send_message(
                chat_id,
                "❌ هنوز عضویت شما تأیید نشده.",
                reply_markup=
                channel_keyboard()
            )

    # -----------------------------------------------------
    # USER MENU
    # -----------------------------------------------------

    elif data == "customs":

        show_customs(
            chat_id,
            user_id,
            message_id
        )

    elif data.startswith("custom:"):

        custom_id = int(
            data.split(":")[1]
        )

        custom_detail(
            chat_id,
            user_id,
            custom_id,
            message_id
        )

    elif data == "wallet":

        show_wallet(
            chat_id,
            user_id,
            message_id
        )

    elif data == "profile":

        show_profile(chat_id, user_id, message_id)

    elif data == "profile:stats":

        profile_stats(chat_id, user_id, message_id)

    elif data == "profile:achievements":

        profile_achievements(chat_id, user_id, message_id)

    elif data == "profile:recent":

        profile_recent(chat_id, user_id, message_id)

    elif data == "leaderboard":

        show_leaderboard(
            chat_id,
            user_id,
            "all",
            message_id
        )

    elif data == "leaderboard:weekly":

        show_leaderboard(
            chat_id,
            user_id,
            "weekly",
            message_id
        )

    elif data == "leaderboard:all":

        show_leaderboard(
            chat_id,
            user_id,
            "all",
            message_id
        )

    elif data == "daily":

        show_daily_reward(chat_id, user_id, message_id)

    elif data == "daily:claim":

        _, result = claim_daily_reward(user_id)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 منوی اصلی", callback_data="home"))
        try:
            bot.edit_message_text(result, chat_id, message_id, reply_markup=kb)
        except Exception:
            bot.send_message(chat_id, result, reply_markup=kb)

    elif data == "promo":

        show_promo_redeem(chat_id, user_id)

    elif data == "referral":

        show_referral(
            chat_id,
            user_id,
            message_id
        )

    elif data == "rules":

        show_rules(
            chat_id,
            message_id
        )

    elif data == "help":

        show_help(
            chat_id,
            message_id
        )

    elif data == "support":

        show_support(
            chat_id,
            message_id
        )

    elif data.startswith("support:reply:"):

        if not is_admin(user_id):
            return

        try:
            target_user_id = int(data.split(":")[2])
        except (ValueError, IndexError):
            bot.send_message(chat_id, "❌ کاربر نامعتبر است.")
            return

        start_support_reply(user_id, target_user_id)

    # -----------------------------------------------------
    # CHANGE COD NAME
    # -----------------------------------------------------

    elif data == "change_cod":

        set_state(
            user_id,
            {
                "action":
                "change_cod"
            }
        )

        bot.send_message(
            chat_id,
            "✏️ نام جدید کالاف را وارد کنید:"
        )

    # -----------------------------------------------------
    # TEAM SELECTOR
    # -----------------------------------------------------

    elif data.startswith("teams:"):

        parts = data.split(":")

        custom_id = int(
            parts[1]
        )

        action = parts[2]

        render_message(
            chat_id,
            "👥 <b>انتخاب تیم</b>\n\nیک تیم با ظرفیت خالی انتخاب کنید:",
            teams_keyboard(custom_id, action),
            message_id
        )

    # -----------------------------------------------------
    # TEAM JOIN / CHANGE
    # -----------------------------------------------------

    elif data.startswith("teamselect:"):

        parts = data.split(":")

        custom_id = int(
            parts[1]
        )

        team_no = int(
            parts[2]
        )

        action = parts[3]

        if action == "join":

            result = join_custom(
                user_id,
                custom_id,
                team_no
            )

        else:

            result = change_team(
                user_id,
                custom_id,
                team_no
            )

            result = (
                "ok"
                if result
                else "locked"
            )

        messages = {

            "ok":
                "✅ ثبت شد.",

            "duplicate":
                "⚠️ شما قبلاً در این کاستوم هستید.",

            "full":
                "❌ این تیم پر است.",

            "max":
                "❌ ظرفیت 100 نفر تکمیل شده.",

            "balance":
                "❌ موجودی سکه کافی نیست.",

            "locked":
                "🔒 ثبت‌نام بسته شده است.",

            "banned":
                "🚫 حساب شما مسدود است.",

            "notfound":
                "❌ کاستوم پیدا نشد."
        }

        bot.send_message(
            chat_id,
            messages.get(
                result,
                "❌ عملیات انجام نشد."
            )
        )

        custom_detail(
            chat_id,
            user_id,
            custom_id
        )

    # -----------------------------------------------------
    # TEAMS VIEW
    # -----------------------------------------------------

    elif data.startswith("teamsview:"):

        custom_id = int(
            data.split(":")[1]
        )

        teams_view(
            chat_id,
            user_id,
            custom_id,
            message_id
        )

    elif data.startswith("teamview:"):

        parts = data.split(":")

        custom_id = int(
            parts[1]
        )

        team_no = int(
            parts[2]
        )

        show_team(
            chat_id,
            custom_id,
            team_no,
            message_id
        )

    # -----------------------------------------------------
    # LEAVE
    # -----------------------------------------------------

    elif data.startswith("leave:"):

        custom_id = int(
            data.split(":")[1]
        )

        conn = db()

        custom = conn.execute(
            """
            SELECT custom_type
            FROM customs

            WHERE id=?
            """,
            (custom_id,)
        ).fetchone()

        conn.close()

        keyboard = types.InlineKeyboardMarkup()

        keyboard.add(
            types.InlineKeyboardButton(
                "⚠️ بله، خارج می‌شوم",
                callback_data=
                f"leave_do:{custom_id}"
            )
        )

        keyboard.add(
            types.InlineKeyboardButton(
                "❌ انصراف",
                callback_data=
                f"custom:{custom_id}"
            )
        )

        if (
            custom
            and custom["custom_type"] == "coin"
        ):

            warning = (
                "⚠️ در کاستوم سکه‌ای، "
                "ورودیه بعد از خروج برگشت داده نمی‌شود."
            )

        else:

            warning = (
                "خروج از کاستوم رایگان "
                "بدون جریمه است."
            )

        bot.send_message(

            chat_id,

            "🚪 <b>تأیید خروج</b>\n\n"

            f"{warning}\n\n"

            "آیا مطمئنی؟",

            reply_markup=keyboard
        )

    elif data.startswith("leave_do:"):

        custom_id = int(
            data.split(":")[1]
        )

        result = leave_custom(
            user_id,
            custom_id
        )

        if result == "ok":

            bot.send_message(
                chat_id,
                "✅ از کاستوم خارج شدی."
            )

        elif result == "locked":

            bot.send_message(
                chat_id,
                "🔒 دیگر امکان خروج وجود ندارد."
            )

        else:

            bot.send_message(
                chat_id,
                "❌ شما در این کاستوم نیستید."
            )

        custom_detail(
            chat_id,
            user_id,
            custom_id
        )

    # -----------------------------------------------------
    # ROOM
    # -----------------------------------------------------

    elif data.startswith("room:"):

        custom_id = int(
            data.split(":")[1]
        )

        conn = db()

        row = conn.execute(
            """
            SELECT *
            FROM customs

            WHERE id=?
            """,
            (custom_id,)
        ).fetchone()

        conn.close()

        participant = user_participation(
            custom_id,
            user_id
        )

        if (
            row
            and participant
            and participant["left_at"] is None
            and row["status"] in (
                "locked",
                "started"
            )
        ):

            bot.send_message(
                chat_id,
                room_text(row)
            )

        else:

            bot.send_message(
                chat_id,
                "🔒 اطلاعات روم هنوز قابل نمایش نیست."
            )

    # =====================================================
    # ADMIN
    # =====================================================

    elif data == "admin":

        if is_admin(user_id):

            admin_menu(
                chat_id,
                message_id
            )

    elif data == "admin:notif_test":

        if is_admin(user_id):
            admin_notification_test_menu(chat_id)

    elif data.startswith("admin:notif_test:"):

        if is_admin(user_id):
            try:
                custom_id = int(data.split(":")[2])
            except (ValueError, IndexError):
                return
            run_notification_test(custom_id, chat_id)

    elif data == "admin:create":

        if is_admin(user_id):

            create_custom_start(
                user_id
            )

    # -----------------------------------------------------
    # CREATE TYPE
    # -----------------------------------------------------

    elif data.startswith(
        "create:type:"
    ):

        if not is_admin(user_id):
            return

        custom_type = data.split(":")[2]

        state = get_state(
            user_id
        )

        if (
            not state
            or state["action"] != "create_custom"
        ):
            return

        state["data"]["custom_type"] = (
            custom_type
        )

        if custom_type == "free":
            state["data"]["entry_fee"] = 0
            state["data"]["winners_count"] = 1
            state["data"]["prize1"] = FREE_PRIZE_PER_MEMBER
            state["data"]["prize2"] = 0
            state["data"]["prize3"] = 0
        else:
            state["data"]["entry_fee"] = COIN_ENTRY_FEE
            state["data"]["winners_count"] = 3
            state["data"]["prize1"] = 0
            state["data"]["prize2"] = 0
            state["data"]["prize3"] = 0

        state["step"] = "min_players"
        ask_next_create(user_id)

    elif data == "create:confirm":

        if is_admin(user_id):

            finalize_custom(
                user_id
            )

    elif data == "create:cancel":

        if is_admin(user_id):

            clear_state(
                user_id
            )

            bot.send_message(
                user_id,
                "❌ ساخت کاستوم لغو شد."
            )

            admin_menu(
                user_id
            )

    # -----------------------------------------------------
    # ADMIN CUSTOMS
    # -----------------------------------------------------

    elif data == "admin:promo":

        if is_admin(user_id):
            admin_promo_menu(chat_id)

    elif data == "admin:promo:create":

        if is_admin(user_id):
            set_state(user_id, {"action": "promo_create", "step": "code", "data": {}})
            bot.send_message(chat_id, "🎟️ کد جدید را وارد کنید:\nمثال: <code>COD100</code>")

    elif data == "admin:promo:list":

        if is_admin(user_id):
            admin_promo_list(chat_id)

    elif data.startswith("admin:promo:disable:"):

        if is_admin(user_id):
            promo_id = int(data.split(":")[3])
            conn = db()
            conn.execute("UPDATE promo_codes SET active=0 WHERE id=?", (promo_id,))
            conn.commit(); conn.close()
            bot.send_message(chat_id, "⛔ کد غیرفعال شد.")
            admin_promo_list(chat_id)

    elif data == "admin:customs":

        if is_admin(user_id):
            admin_customs(chat_id, "active")

    elif data.startswith("admin:customs:"):

        if is_admin(user_id):
            filter_status = data.split(":", 2)[2]
            if filter_status not in ("active", "all", "finished", "cancelled"):
                filter_status = "active"
            admin_customs(chat_id, filter_status)

    elif data.startswith(
        "admin:custom:"
    ):

        if is_admin(user_id):

            custom_id = int(
                data.split(":")[2]
            )

            admin_custom_detail(
                chat_id,
                custom_id,
                message_id
            )

    # -----------------------------------------------------
    # ADMIN LOCK
    # -----------------------------------------------------

    elif data.startswith(
        "admin:lock:"
    ):

        if is_admin(user_id):

            custom_id = int(
                data.split(":")[2]
            )

            lock_custom(
                custom_id
            )

            admin_custom_detail(
                chat_id,
                custom_id
            )

    # -----------------------------------------------------
    # ADMIN START
    # -----------------------------------------------------

    elif data.startswith(
        "admin:start:"
    ):

        if is_admin(user_id):

            custom_id = int(
                data.split(":")[2]
            )

            start_custom(
                custom_id
            )

            admin_custom_detail(
                chat_id,
                custom_id
            )

    # -----------------------------------------------------
    # ADMIN CANCEL
    # -----------------------------------------------------

    elif data.startswith(
        "admin:cancel:"
    ):

        if is_admin(user_id):

            custom_id = int(
                data.split(":")[2]
            )

            cancel_custom(
                custom_id,
                "لغو توسط مدیریت"
            )

            admin_custom_detail(
                chat_id,
                custom_id
            )

    # -----------------------------------------------------
    # ADMIN PARTICIPANTS
    # -----------------------------------------------------

    elif data == "admin:participants":

        if is_admin(user_id):

            admin_customs(
                chat_id,
                "active"
            )

    elif data.startswith("admin:teams:"):

        if is_admin(user_id):
            custom_id = int(data.split(":")[2])
            admin_custom_teams(chat_id, custom_id)

    elif data.startswith(
        "admin:plist:"
    ):

        if is_admin(user_id):

            custom_id = int(
                data.split(":")[2]
            )

            admin_participants(
                chat_id,
                custom_id
            )

    # -----------------------------------------------------
    # ADMIN ROOM
    # -----------------------------------------------------

    elif data == "admin:room":

        if is_admin(user_id):

            admin_customs(
                chat_id,
                "active"
            )

    elif data.startswith(
        "admin:roomset:"
    ):

        if is_admin(user_id):

            custom_id = int(
                data.split(":")[2]
            )

            set_state(

                user_id,

                {
                    "action": "roomset",
                    "cid": custom_id,
                    "step": "room_id"
                }
            )

            bot.send_message(
                chat_id,
                "🔑 Room ID را وارد کنید:"
            )

    # -----------------------------------------------------
    # ADMIN RESULTS
    # -----------------------------------------------------

    elif data == "admin:results":

        if is_admin(user_id):

            admin_customs(
                chat_id,
                "active"
            )

    elif data.startswith("admin:resulttable:"):

        if is_admin(user_id):
            custom_id = int(data.split(":")[2])
            admin_result_table(chat_id, custom_id, message_id)

    elif data.startswith(
        "admin:resultstart:"
    ):

        if is_admin(user_id):

            custom_id = int(
                data.split(":")[2]
            )

            set_state(

                user_id,

                {
                    "action": "results",
                    "cid": custom_id,
                    "selected": []
                }
            )

            admin_result_view(
                chat_id,
                custom_id,
                message_id
            )

    elif data.startswith("admin:resultedit:"):

        if not is_admin(user_id):
            return

        custom_id = int(data.split(":")[2])
        conn_tmp = db()
        row = conn_tmp.execute("SELECT status, results_submitted FROM customs WHERE id=?", (custom_id,)).fetchone()
        conn_tmp.close()
        if not row or not row["results_submitted"]:
            bot.answer_callback_query("نتیجه‌ای برای اصلاح وجود ندارد.", show_alert=True)
            return
        set_state(user_id, {"action": "results_edit", "cid": custom_id, "selected": []})
        admin_result_view(chat_id, custom_id, message_id)

    elif data.startswith(
        "resultteam:"
    ):

        if not is_admin(user_id):
            return

        parts = data.split(":")

        custom_id = int(
            parts[1]
        )

        team_no = int(
            parts[2]
        )

        state = get_state(
            user_id
        )

        if (
            not state
            or state.get("action") not in ("results", "results_edit")
            or state.get("cid") != custom_id
        ):
            return

        selected = state["selected"]

        if team_no in selected:

            selected.remove(
                team_no
            )

        else:

            conn_tmp = db()
            try:
                custom = conn_tmp.execute(
                    "SELECT winners_count FROM customs WHERE id=?",
                    (custom_id,)
                ).fetchone()
            finally:
                conn_tmp.close()

            if (
                custom
                and len(selected)
                < custom["winners_count"]
            ):

                selected.append(
                    team_no
                )

        admin_result_view(
            chat_id,
            custom_id,
            message_id
        )

    elif data.startswith(
        "resultsubmit:"
    ):

        if not is_admin(user_id):
            return

        custom_id = int(
            data.split(":")[1]
        )

        state = get_state(
            user_id
        )

        if not state:
            return

        conn = db()

        custom = conn.execute(
            """
            SELECT winners_count
            FROM customs

            WHERE id=?
            """,
            (custom_id,)
        ).fetchone()

        conn.close()

        if not custom:
            return

        if len(state["selected"]) != custom["winners_count"]:

            bot.send_message(
                chat_id,

                "❌ دقیقاً به تعداد "
                "تعیین‌شده تیم انتخاب کنید."
            )

            return

        state["step"] = "confirm"
        kb=types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("✅ تأیید و ثبت نتایج",callback_data=f"admin:confirm:results:{custom_id}"),types.InlineKeyboardButton("❌ لغو",callback_data=f"admin:resultstart:{custom_id}"))
        bot.send_message(chat_id,"🔐 <b>تأیید عملیات حساس</b>\n\nپس از تأیید، نتیجه ثبت و جوایز طبق رتبه واریز می‌شوند.\n\nتأیید می‌کنی؟",reply_markup=kb)
        return

        if success:

            was_edit = state.get("action") == "results_edit"
            bot.send_message(
                chat_id,
                ("✅ نتیجه با موفقیت اصلاح شد و "
                 "جوایز/امتیازها بر اساس نتیجه جدید محاسبه شدند."
                 if was_edit else
                 "✅ نتایج ثبت شد و جوایز واریز شدند.")
            )

            notify_participants(
                custom_id,

                ("✏️ <b>نتیجه کاستوم اصلاح شد.</b>\n\n"
                 "رتبه‌ها، جوایز و امتیازهای رنک بر اساس نتیجه جدید به‌روزرسانی شدند."
                 if was_edit else
                 "🏆 <b>نتیجه کاستوم ثبت شد.</b>\n\n"
                 "جوایز برندگان به صورت خودکار به کیف پول آنها واریز شد.")
            )

        else:

            bot.send_message(
                chat_id,
                f"❌ {result}"
            )

        admin_custom_detail(
            chat_id,
            custom_id
        )

    # -----------------------------------------------------
    # ADMIN STATS
    # -----------------------------------------------------

    elif data == "admin:advanced_stats":

        if is_admin(user_id):
            admin_advanced_stats(chat_id)

    elif data == "admin:stats":

        if is_admin(user_id):

            admin_stats(
                chat_id,
                message_id
            )

    # -----------------------------------------------------
    # ADMIN USERS
    # -----------------------------------------------------

    elif data == "admin:users":

        if is_admin(user_id):

            admin_users(
                chat_id,
                message_id
            )

    elif data.startswith(
        "admin:user:"
    ):

        if is_admin(user_id):

            target_id = int(
                data.split(":")[2]
            )

            admin_user_detail(
                chat_id,
                target_id,
                message_id
            )

    # -----------------------------------------------------
    # BAN
    # -----------------------------------------------------

    elif data.startswith(
        "admin:ban:"
    ):

        if is_admin(user_id):

            target_id = int(
                data.split(":")[2]
            )

            if target_id == ADMIN_ID:
                return

            conn = db()

            user = conn.execute(
                """
                SELECT banned
                FROM users

                WHERE id=?
                """,
                (target_id,)
            ).fetchone()

            if user:

                new_status = (
                    0
                    if user["banned"]
                    else 1
                )

                conn.execute(
                    """
                    UPDATE users

                    SET banned=?

                    WHERE id=?
                    """,
                    (
                        new_status,
                        target_id
                    )
                )
                security_log("ban" if new_status else "unban", actor_id=user_id, target_user_id=target_id, details="تغییر وضعیت توسط مدیریت", conn=conn)

                conn.commit()

            conn.close()

            admin_user_detail(
                chat_id,
                target_id
            )

    # -----------------------------------------------------
    # BALANCE
    # -----------------------------------------------------

    elif data == "admin:balance":

        if is_admin(user_id):

            admin_users(
                chat_id
            )

    elif data.startswith(
        "admin:bal:"
    ):

        if is_admin(user_id):

            target_id = int(
                data.split(":")[2]
            )

            set_state(

                user_id,

                {
                    "action":
                    "admin_balance",

                    "target":
                    target_id
                }
            )

            bot.send_message(

                chat_id,

                "💰 مقدار تغییر موجودی "
                "را وارد کنید.\n\n"

                "مثال:\n"
                "<code>500</code>\n\n"

                "یا:\n"
                "<code>-200</code>"
            )

    # -----------------------------------------------------
    # BROADCAST
    # -----------------------------------------------------

    elif data == "admin:broadcast":

        if is_admin(user_id):

            set_state(

                user_id,

                {
                    "action":
                    "broadcast"
                }
            )

            bot.send_message(
                chat_id,
                "📢 متن پیام همگانی "
                "را ارسال کنید."
            )

    # -----------------------------------------------------
    # SECURITY
    # -----------------------------------------------------

    elif data == "admin:security":

        if is_admin(user_id):
            security_dashboard(chat_id)

    elif data == "admin:banned":

        if is_admin(user_id):
            admin_banned_users(chat_id)

    # -----------------------------------------------------
    # SETTINGS
    # -----------------------------------------------------

    elif data == "admin:settings":

        if is_admin(user_id):

            bot.send_message(

                chat_id,

                "⚙️ <b>تنظیمات فعلی</b>\n\n"

                f"🔒 زمان قفل: "
                f"{LOCK_MINUTES} دقیقه\n"

                f"👥 ظرفیت هر تیم: "
                f"{TEAM_CAPACITY}\n"

                f"🎮 تعداد تیم‌ها: "
                f"{MAX_TEAMS}\n"

                f"👤 ظرفیت کل: "
                f"{MAX_PLAYERS}"
            )


# =========================================================
# ADMIN NOTIFICATION TEST
# =========================================================

def admin_notification_test_menu(chat_id):
    """Choose a custom whose participants will receive test notifications.
    This never writes notification event keys, so real notifications remain intact.
    """
    conn = db()
    rows = conn.execute(
        """
        SELECT * FROM customs
        WHERE status IN ('open','locked','started')
        ORDER BY id DESC
        LIMIT 20
        """
    ).fetchall()
    conn.close()

    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for row in rows:
        keyboard.add(types.InlineKeyboardButton(
            f"🧪 #{row['id']} {row['title']} | {status_text(row['status'])}",
            callback_data=f"admin:notif_test:{row['id']}"
        ))
    keyboard.add(types.InlineKeyboardButton("🔙 پنل مدیریت", callback_data="admin"))

    bot.send_message(
        chat_id,
        "🧪 <b>تست اعلان‌های هوشمند</b>\n\n"
        "یک کاستوم فعال را انتخاب کنید.\n"
        "تمام اعلان‌های آزمایشی برای شرکت‌کنندگان همان کاستوم ارسال می‌شود.\n\n"
        "⚠️ این تست هیچ اعلان واقعی را ثبت نمی‌کند و زمان‌بندی اصلی را تغییر نمی‌دهد.",
        reply_markup=keyboard
    )


def run_notification_test(custom_id, admin_chat_id):
    conn = db()
    row = conn.execute(
        "SELECT * FROM customs WHERE id=?",
        (custom_id,)
    ).fetchone()
    conn.close()

    if not row:
        bot.send_message(admin_chat_id, "❌ کاستوم پیدا نشد.")
        return

    conn = db()
    users = conn.execute(
        """
        SELECT DISTINCT user_id FROM participants
        WHERE custom_id=? AND left_at IS NULL
        """,
        (custom_id,)
    ).fetchall()
    conn.close()

    if not users:
        bot.send_message(
            admin_chat_id,
            "⚠️ برای این کاستوم هنوز شرکت‌کننده فعالی وجود ندارد.\n\n"
            "حداقل یک کاربر وارد کاستوم کنید و دوباره تست را بزنید."
        )
        return

    title = row["title"]
    room_id = row["room_id"] or "TEST-ROOM"
    password = row["room_password"] or "TEST-PASS"

    messages = [
        "🧪 <b>تست اعلان ۳۰ دقیقه‌ای</b>\n\n"
        f"🎮 {title}\n⏰ یادآوری: کمتر از ۳۰ دقیقه تا شروع باقی مانده است.",
        "🧪 <b>تست اعلان ۱۰ دقیقه‌ای</b>\n\n"
        f"🎮 {title}\n⚠️ کمتر از ۱۰ دقیقه تا شروع باقی مانده است.",
        "🧪 <b>تست اعلان قفل ثبت‌نام</b>\n\n"
        f"🎮 {title}\n🔒 ثبت‌نام بسته شد.",
        "🧪 <b>تست اعلان ظرفیت</b>\n\n"
        f"🎮 {title}\n🔥 ظرفیت کاستوم در حال تکمیل است.",
        "🧪 <b>تست اعلان تکمیل ظرفیت</b>\n\n"
        f"🎮 {title}\n🚫 ظرفیت تکمیل شد.",
        "🧪 <b>تست اعلان شروع</b>\n\n"
        f"🎮 {title}\n🎮 کاستوم شروع شد.",
        "🧪 <b>تست اعلان Room</b>\n\n"
        f"🎮 {title}\n🔑 اطلاعات روم آماده است.\n"
        f"Room ID: <code>{room_id}</code>\n"
        f"Password: <code>{password}</code>",
        "🧪 <b>تست اعلان نتیجه</b>\n\n"
        f"🎮 {title}\n🏆 نتیجه کاستوم ثبت شد.",
        "🧪 <b>تست اعلان لغو</b>\n\n"
        f"🎮 {title}\n❌ کاستوم لغو شد."
    ]

    sent = 0
    failed = 0
    for user in users:
        user_ok = True
        for message in messages:
            try:
                bot.send_message(user["user_id"], message)
            except Exception:
                user_ok = False
        if user_ok:
            sent += 1
        else:
            failed += 1

    bot.send_message(
        admin_chat_id,
        "✅ <b>تست اعلان‌ها انجام شد.</b>\n\n"
        f"👥 شرکت‌کنندگان: {len(users)}\n"
        f"✅ دریافت موفق همه تست‌ها: {sent}\n"
        f"⚠️ دارای خطا: {failed}\n\n"
        "این تست روی زمان‌بندی واقعی اعلان‌ها اثری نگذاشت."
    )


# =========================================================
# CALLBACK ROUTER
# =========================================================

@bot.callback_query_handler(
    func=lambda call: True
)
def callback_router(call):

    user_id = call.from_user.id

    try:

        upsert_user(
            call.from_user
        )

        if is_banned(user_id):

            bot.answer_callback_query(
                call.id,
                "حساب شما مسدود است.",
                show_alert=True
            )

            return

        # Channel check button must work
        if call.data == "check_channel":

            process_callback(

                user_id,

                call.data,

                call.message.chat.id,

                call.message.message_id
            )

            bot.answer_callback_query(
                call.id
            )

            return

        # Admin callbacks must not be blocked by the public channel check.
        # This is especially important for ban/unban and management actions.
        if not is_admin(user_id) and not member_status(user_id):

            bot.answer_callback_query(

                call.id,

                "ابتدا عضو کانال شوید.",

                show_alert=True
            )

            bot.send_message(
                call.message.chat.id,
                "🔒 ابتدا عضو کانال شوید.",
                reply_markup=
                channel_keyboard()
            )

            return

        process_callback(

            user_id,

            call.data,

            call.message.chat.id,

            call.message.message_id
        )

        bot.answer_callback_query(
            call.id
        )

    except Exception as error:

        print(
            "Callback error:",
            repr(error)
        )

        try:

            bot.answer_callback_query(
                call.id,
                "خطایی رخ داد.",
                show_alert=True
            )

        except Exception:
            pass


# =========================================================
# STARTUP
# =========================================================

def startup():

    init_db()

    print(
        "COD Custom Bot Starting..."
    )

    print(
        "Timezone: Asia/Tehran"
    )

    print(
        "Database:",
        DB_PATH
    )

    if not BOT_TOKEN:
        print("Telegram connection failed:")
        print("BOT_TOKEN environment variable is empty.")
        print("Termux: export BOT_TOKEN='YOUR_BOT_TOKEN'")
        return

    try:

        me = bot.get_me()

        print(
            f"Connected: @{me.username}"
        )

    except Exception as error:

        print(
            "Telegram connection failed:"
        )

        print(
            repr(error)
        )

        print(
            "توکن ربات معتبر نیست، "
            "منقضی شده یا توسط BotFather لغو شده است."
        )

        return

    manager = threading.Thread(
        target=manager_loop,
        daemon=True
    )

    manager.start()

    print(
        "Automatic custom manager started."
    )

    print(
        "Bot is running..."
    )

    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    startup()
