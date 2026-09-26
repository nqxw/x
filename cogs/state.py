# cogs/state.py | shared state for all cogs
import os
import re
import json
import time
import math
import base64
import random
import string
import sqlite3
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from collections import defaultdict

import modifyself_shim as discord

CLIENT = None
MAIN_CLIENT = None
TOKEN: str = ""
PREFIX: str = "."
USER_AGENT: str = ""
VERSION: str = "2.6.0"
HAS_HCAPTCHA: bool = False

OWNER_ID: int = 1551632054574121051
_admins: set = set()
_devs: set = set()

ADMIN_COMMANDS = frozenset({
    "admin", "setadmin", "adminremove", "adminlist",
    "blacklist", "whitelist",
    "serverblacklist", "channelblacklist", "rolerestrict",
    "guards", "perms", "perm",
    "nuke", "massban", "masskick",
})

DEVELOPER_COMMANDS = frozenset({
    "eval", "restart", "reconnect", "proxy", "plugin", "session", "logs",
    "setdev", "devremove", "devlist", "accesslist",
})

OWNER_COMMANDS = frozenset({"setowner"})

_ACCESS_FILE = "database/access.json"

def access_save():
    try:
        os.makedirs(os.path.dirname(_ACCESS_FILE), exist_ok=True)
        with open(_ACCESS_FILE, "w") as f:
            json.dump({
                "owner":  _current_owner(),
                "admins": sorted(_admins),
                "devs":   sorted(_devs),
            }, f, indent=2)
    except Exception as e:
        print(f"[access] save error: {e}")

def access_load():
    global OWNER_ID, _admins, _devs
    if not os.path.exists(_ACCESS_FILE):
        return
    try:
        with open(_ACCESS_FILE) as f:
            d = json.load(f)
        if isinstance(d.get("owner"), int):
            OWNER_ID = int(d["owner"])
        _admins = set(int(x) for x in d.get("admins", []))
        _devs   = set(int(x) for x in d.get("devs", []))
        print(f"[access] loaded owner={OWNER_ID} admins={len(_admins)} devs={len(_devs)}")
    except Exception as e:
        print(f"[access] load error: {e}")

def _current_owner():
    return OWNER_ID

def _access_level(uid: int) -> str:
    if uid == _current_owner(): return "owner"
    if uid in _admins: return "admin"
    if uid in _devs: return "dev"
    return "user"

def _access_ok(uid: int, cmd: str) -> bool:
    lvl = _access_level(uid)
    if lvl == "owner": return True
    if cmd in OWNER_COMMANDS: return False
    if cmd in ADMIN_COMMANDS and lvl != "admin": return False
    if cmd in DEVELOPER_COMMANDS and lvl != "dev": return False
    return True

ESC = "\x1b"
RESET   = f"{ESC}[0m"
GREY    = f"{ESC}[2;37m"
WHITE   = f"{ESC}[1;37m"
CYAN    = f"{ESC}[36m"
GREEN   = f"{ESC}[32m"
YELLOW  = f"{ESC}[33m"
RED     = f"{ESC}[31m"
BLUE    = f"{ESC}[34m"
MAGENTA = f"{ESC}[35m"
DIM     = f"{ESC}[2m"

def _ansi_block(lines):
    result = "> ```ansi\n"
    for line in lines:
        result += ("> \n" if line.strip() == "" else f"> {line}\n")
    result += "> ```"
    return "\n".join(l for l in result.split("\n") if not re.match(r'^>\s*$', l))

def ui_box(title, rows, footer=""):
    lines = [f"  {WHITE}{title}{RESET}"] + list(rows)
    if footer:
        lines += ["", f"  {DIM}{footer}{RESET}"]
    return _ansi_block(lines)

def ui_ok(msg):   return _ansi_block([f"  {GREEN}✓{RESET}  {msg}"])
def ui_err(msg):  return _ansi_block([f"  {RED}✗{RESET}  {msg}"])
def ui_info(msg): return _ansi_block([f"  {CYAN}•{RESET}  {msg}"])
def ui_warn(msg): return _ansi_block([f"  {YELLOW}!{RESET}  {msg}"])

def ui_progress(label, pct):
    filled = int(pct / 10)
    bar = f"{GREEN}{'█' * filled}{GREY}{'░' * (10 - filled)}{RESET}"
    return f"  {bar} {WHITE}{pct}%{RESET}  {DIM}{label}{RESET}"

PAGE_SIZE = 8

def _paginate(title, subtitle, rows, page=1):
    total = max(1, math.ceil(len(rows) / PAGE_SIZE))
    page = max(1, min(page, total))
    chunk = rows[(page-1)*PAGE_SIZE:(page-1)*PAGE_SIZE+PAGE_SIZE]
    lines = [f"  {WHITE}> {title}{RESET}  {DIM}{subtitle}{RESET}", ""] + chunk + [
        "", f"  {DIM}page {page}/{total}{RESET}"
    ]
    return _ansi_block(lines)

load_config = None
save_config = None
log_msg = None
db_inc_stat = None
log_enabled = None

HOSTED_DISPATCH = None
async_hosted_token_add = None
async_hosted_token_remove = None
neko_gif = None
translate_text = None
send_temp = None
set_hypesquad = None
clear_hypesquad = None
encrypt_file = None
decrypt_file = None
_get_session = None
_perm_check_ref = None

_pre_hooks: list = []

def register_pre_hook(fn):
    if fn not in _pre_hooks:
        _pre_hooks.append(fn)

def unregister_pre_hook(fn):
    try: _pre_hooks.remove(fn)
    except ValueError: pass

_user_blacklist: set = set()
_user_whitelist: set = set()
_cmd_blacklist_server: dict = {}
_cmd_blacklist_channel: dict = {}
_role_restrict: dict = {}
_cmd_disabled: set = set()
_perm_allow: dict = {}
_perm_block: set = set()
_perm_channel: dict = {}
_perm_server: dict = {}

def _perm_check(cmd, message) -> bool:
    if cmd in _cmd_disabled: return False
    if message.author.id in _user_blacklist: return False
    if _user_whitelist and message.author.id not in _user_whitelist: return False
    if message.guild and str(message.guild.id) in _cmd_blacklist_server:
        if cmd in _cmd_blacklist_server[str(message.guild.id)]: return False
    if str(message.channel.id) in _cmd_blacklist_channel:
        if cmd in _cmd_blacklist_channel[str(message.channel.id)]: return False
    if cmd in _role_restrict:
        if not message.guild or not hasattr(message.author, "roles"): return False
        have = {r.id for r in message.author.roles}
        if not (have & _role_restrict[cmd]): return False
    if cmd in _perm_block: return False
    if cmd in _perm_channel and message.channel.id != _perm_channel[cmd]: return False
    if cmd in _perm_server and (message.guild is None or message.guild.id != _perm_server[cmd]): return False
    if cmd in _perm_allow and _perm_allow[cmd]:
        return message.author.id in _perm_allow[cmd]
    return True

HOSTED_TOKENS: list = []
_host_sessions: list = []
_hosted_clients: list = []
_hosted_spawned: bool = False
_host_lock = None

_hcaptcha_agent = None

_monitor = {"joins": False, "leaves": False, "roles": False, "nicks": False,
            "invites": False, "log_ch": None, "keywords": []}
_invite_cache: dict = {}
_automod = {"enabled": False, "words": [], "action": "delete", "log_ch": None}
_raidmode = {"enabled": False, "threshold": 5}
_quarantine = {"enabled": False, "role_id": None, "age_days": 7}
_ticket_cfg = {"category_id": None}
_verify_cfg = {"role_id": None}
_buttons_enabled = True
_modals_enabled = True
_pending_interactions: list = []

_server_backups: dict = {}
_nuke_backups: dict = {}

_scheduler: list = []
_scheduler_task = None

_managed_tasks: dict = {}
_TASK_STORE = "database/tasks.json"
_TRIGGER_STORE = "database/triggers.json"

_triggers = {"message": [], "reaction": [], "voice": [], "member": []}
_trigger_fired_counts: dict = {}

_db_path = "database/selfbot.db"
_db = None

def db_open():
    global _db
    try:
        os.makedirs("database", exist_ok=True)
        _db = sqlite3.connect(_db_path, check_same_thread=False)
        c = _db.cursor()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS notes (user_id TEXT, note TEXT, ts REAL);
        CREATE TABLE IF NOT EXISTS history (user_id TEXT, entry TEXT, ts REAL);
        CREATE TABLE IF NOT EXISTS stats (cmd TEXT PRIMARY KEY, count INTEGER);
        CREATE TABLE IF NOT EXISTS sched (id TEXT, when_ts REAL, action TEXT, payload TEXT);
        """)
        _db.commit()
    except Exception as e:
        print(f"[state] db_open failed: {e}")
        _db = None

def db_note_add(uid, note):
    if _db is None: return
    _db.execute("INSERT INTO notes VALUES (?,?,?)", (str(uid), note, time.time())); _db.commit()

def db_note_list(uid):
    if _db is None: return []
    return _db.execute("SELECT note, ts FROM notes WHERE user_id=? ORDER BY ts DESC", (str(uid),)).fetchall()

def db_note_clear(uid):
    if _db is None: return
    _db.execute("DELETE FROM notes WHERE user_id=?", (str(uid),)); _db.commit()

def db_hist_add(uid, entry):
    if _db is None: return
    _db.execute("INSERT INTO history VALUES (?,?,?)", (str(uid), entry, time.time())); _db.commit()

def db_hist_list(uid, limit=50):
    if _db is None: return []
    return _db.execute("SELECT entry, ts FROM history WHERE user_id=? ORDER BY ts DESC LIMIT ?", (str(uid), limit)).fetchall()

def db_stats_inc(cmd):
    if _db is None: return
    try:
        _db.execute("INSERT INTO stats VALUES (?,1) ON CONFLICT(cmd) DO UPDATE SET count=count+1", (cmd,))
        _db.commit()
    except Exception: pass

def db_stats_all():
    if _db is None: return []
    return _db.execute("SELECT cmd, count FROM stats ORDER BY count DESC").fetchall()

def db_stats_clear():
    if _db is None: return
    _db.execute("DELETE FROM stats"); _db.commit()

db_open()

LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"
_lfm: dict = {}
_PERIOD = {"w":"7day","week":"7day","m":"1month","month":"1month",
           "3m":"3month","6m":"6month","y":"12month","year":"12month","all":"overall","overall":"overall"}
_PLABEL = {"7day":"this week","1month":"this month","3month":"3 months",
           "6month":"6 months","12month":"this year","overall":"all time"}

_autoaddback = False
_speak_lang = None

_agc_state = {"enabled": False, "block": False, "leave_msg": "lol nice try",
              "gc_name": "trap detected", "gc_icon_url": None, "webhook_url": None}
_agc_whitelist: set = set()

def agc_load_wl():
    global _agc_whitelist
    p = "config/agc_whitelist.json"
    if os.path.exists(p):
        try:
            with open(p) as f: _agc_whitelist = set(json.load(f))
        except Exception: _agc_whitelist = set()

def agc_save_wl():
    os.makedirs("config", exist_ok=True)
    with open("config/agc_whitelist.json", "w") as f:
        json.dump(list(_agc_whitelist), f)

def _load_admins() -> list:
    return [str(x) for x in sorted(_admins)]

def _save_admins(admins: list):
    global _admins
    _admins = set(int(x) for x in admins)
    access_save()

def _owner_id() -> str:
    return str(_current_owner())

def _set_owner(uid: str):
    global OWNER_ID
    OWNER_ID = int(uid)
    access_save()

def _is_owner(user_id) -> bool:
    try:
        return int(user_id) == _current_owner()
    except Exception:
        return False

def _parse_uid_str(user: str):
    if not user: return None
    if user.startswith("<@") and user.endswith(">"):
        return user.strip("<@!>")
    if user.isdigit(): return user
    return None

def _get_host_session(identifier: str):
    try:
        idx = int(identifier) - 1
        if 0 <= idx < len(_host_sessions):
            return _host_sessions[idx]
    except (ValueError, TypeError):
        pass
    for s in _host_sessions:
        u = s.get("user")
        uid = getattr(u, "id", None) if u is not None else None
        if uid is not None and str(uid) == str(identifier):
            return s
    return None

def _b64uid(token):
    try:
        first = token.split(".")[0]
        return base64.b64decode(first + "=" * (-len(first) % 4)).decode()
    except Exception: return None

async def hosted_username(token):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://discord.com/api/v9/users/@me",
                headers={"Authorization": token.strip(), "User-Agent": USER_AGENT}) as r:
                if r.status == 200: return (await r.json()).get("username","?")
    except Exception: pass
    return "?"

async def hosted_info(token):
    token = token.strip().strip('"').strip("'")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://discord.com/api/v9/users/@me",
                             headers={"Authorization": token, "User-Agent": USER_AGENT}) as r:
                if r.status == 200:
                    d = await r.json()
                    return {"username": d.get("username","?"), "id": d.get("id","?"), "valid": True}
                return {"username": "invalid token", "id": "?", "valid": False}
    except Exception as e:
        return {"username": f"error: {e}", "id": "?", "valid": False}

async def hosted_send_via_client(session, channel_id: int, content: str):
    cl = session.get("client")
    if cl is None: return False
    try:
        ch = cl.get_channel(channel_id)
        if ch is None: ch = await cl.fetch_channel(channel_id)
        await ch.send(content)
        return True
    except Exception as e:
        print(f"[host say] {e}")
        return False

async def hosted_send(token, channel_id, content):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f"https://discord.com/api/v9/channels/{channel_id}/messages",
                headers={"Authorization": token.strip(), "Content-Type":"application/json", "User-Agent":USER_AGENT},
                json={"content": content}) as r:
                return r.status in (200,201)
    except Exception: return False

def sched_save():
    try:
        os.makedirs("database", exist_ok=True)
        with open("database/scheduler.json", "w") as f:
            json.dump(_scheduler, f, indent=2)
    except Exception: pass

def sched_load():
    global _scheduler
    if os.path.exists("database/scheduler.json"):
        try:
            with open("database/scheduler.json") as f: _scheduler = json.load(f)
        except Exception: _scheduler = []

_server_prefixes: dict = {}
_aliases: dict = {}
_cooldowns: dict = {}
_cooldown_last: dict = {}
_profiles: dict = {}
_encrypt_enabled = False
_has_crypto = False
_HAS_CRYPTO = False
_key_path = "config/.key"

_reconnect_count = 0
_last_ready_ts = 0
_session_events: list = []
_auto_reconnect = True
_auto_restart = True
_restart_backoff = 0
_MAX_RESTART_BACKOFF = 300

_rate_limit_events: list = []
_rate_limit_tracking = True
_cmd_queue = None
_queue_enabled = False
_queue_workers = 3
_queue_worker_tasks: list = []

_snipe_cache: dict = {}
_editsnipe_cache: dict = {}
_typing_tasks: dict = {}
_tracking: dict = {}
_cache_auto = True
_tracked_users: set = set()
_latency_history: list = []

SNIPE_LIMIT = 50
LOG_FILE = "message_log.txt"
SNIPER_ENABLED = True
LOGGER_ENABLED = False
_current_platform = "desktop"

PLATFORM_MAP = {
    "desktop":  "Windows",
    "web":      "Web",
    "mobile":   "Android",
    "ios":      "iOS",
    "android":  "Android",
    "embedded": "Embedded",
}
HOUSE_IDS = {"bravery": 1, "brilliance": 2, "balance": 3}
HOUSE_NAMES = {1: "Bravery", 2: "Brilliance", 3: "Balance"}

_spam_tasks: dict = {}
AUTO_RESPONSES: dict = {}
_mimic_dict: dict = {}

_autoreact_emoji = None
_superreact_emoji = None
_multireact_pool: list = []
_multireact_enabled = False
_giveaway_enabled = False
_nitrosniper_enabled = True
_vsniper_list: list = []
_vsniper_task = None
_afk_enabled = False
_afk_msg = None
_autodelete_secs = 0

_proxy = None
_plugins: dict = {}
_sessions: list = []
_session_idx = 0

# ── AFK state ──
afk = {
    "enabled":        False,
    "message":        "I'm AFK right now — back soon.",
    "whitelist":      set(),
    "blacklist":      set(),
    "cooldown":       60,
    "last_reply":     {},
    "emergency":      False,
    "expires_at":     0.0,
    "dm_only":        False,
    "per_server":     {},
    "custom_replies": {},
    "ping_counter":   {},
}

# ── loggers ──
loggers = {
    "message":  {"enabled": False, "channel": None},
    "deleted":  {"enabled": False, "channel": None},
    "edited":   {"enabled": False, "channel": None},
    "reaction": {"enabled": False, "channel": None},
    "mention":  {"enabled": False, "channel": None},
    "dm":       {"enabled": False, "channel": None},
    "joins":    {"enabled": False, "channel": None},
    "leaves":   {"enabled": False, "channel": None},
}

# ── filters ──
filters = {
    "spam":         {"enabled": False, "threshold": 5, "window": 5.0, "action": "delete", "history": {}},
    "duplicate":    {"enabled": False, "window": 30.0, "history": {}},
    "link":         {"enabled": False, "whitelist": set()},
    "invite":       {"enabled": False},
    "attachment":   {"enabled": False},
    "nsfw":         {"enabled": False, "keywords": {"nsfw", "porn", "xxx", "nude", "lewd"}},
    "mention_spam": {"enabled": False, "threshold": 5},
    "mass_ping":    {"enabled": False, "threshold": 5},
    "scam":         {"enabled": False, "patterns": set()},
    "webhook":      {"enabled": False},
    "bot":          {"enabled": False},
    "auto_purge":   {"enabled": False, "keep": 50},
    "log_channel":  None,
    "actions_log":  [],
}

# ── autoresponder rules ──
ar_rules: list = []
ar_variables = {
    "{user}":   lambda m: str(m.author),
    "{uid}":    lambda m: str(m.author.id),
    "{channel}":lambda m: getattr(m.channel, "name", str(m.channel.id)),
    "{server}": lambda m: getattr(getattr(m, "guild", None), "name", "DM"),
    "{time}":   lambda m: datetime.now().strftime("%H:%M:%S"),
    "{date}":   lambda m: datetime.now().strftime("%Y-%m-%d"),
}

# ── nickname ──
nickname = {
    "auto":      False,
    "pattern":   "{user}",
    "history":   {},
    "interval":  0,
    "last_run":  0.0,
}

# ── reminders / timers / notifications ──
reminders: list = []
timers: list = []
notifications: list = []

# ── ping tracking ──
ping_counts: dict = {}
mention_log: list = []
ping_cfg = {"track": True, "log_channel": None, "max_store": 200}

# ── profile tracking ──
profile_history: dict = {}
personal_blocklist: set = set()
personal_whitelist: set = set()
personal_ignore: set = set()

# ── meta ──
meta = {
    "debug":         False,
    "dev_mode":      False,
    "uptime_start":  time.time(),
    "error_log":     [],
    "status_watch":  {"enabled": False, "interval": 300, "last": 0.0},
    "webhook":       None,
    "github_repo":   None,
    "github_last":   None,
    "gh_notify_ch":  None,
    "api_endpoints": {},
}

# ── scrapers / schedulers ──
scrape_cfg = {"auto_purge": False, "keep_last": 50}

# ============================================================
# multispoof satellite state — shared across cogs
# ============================================================
MULTISPOOF_GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
MULTISPOOF_CLIENT_BUILD = 331500
satellites: dict = {}   # name -> _Satellite instance


async def satellite_stop_all():
    """Stop every satellite, cancelling its task and closing the socket."""
    for name in list(satellites.keys()):
        sat = satellites.pop(name, None)
        if sat is None:
            continue
        try:
            if getattr(sat, "task", None) and not sat.task.done():
                sat.task.cancel()
        except Exception:
            pass
        try:
            await sat.close()
        except Exception:
            pass


async def reply(message, content):
    """
    Robust reply — tries edit, falls back to delete+send. Survives
    age-restricted channels where edit_message 403s.
    """
    try:
        return await message.edit(content=content)
    except Exception:
        pass
    try:
        await message.delete()
    except Exception:
        pass
    try:
        return await message.channel.send(content)
    except Exception as e:
        print(f"[state.reply] {e}")
        return None
