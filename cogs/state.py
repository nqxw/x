# cogs/state.py | shared state for all cogs
# every mutable the cogs touch lives here so imports stay clean and one source of truth

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
import discord
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from collections import defaultdict

# ── filled in at boot by selfbot.py ──
CLIENT: discord.Client | None = None
MAIN_CLIENT: discord.Client | None = None
TOKEN: str = ""
PREFIX: str = "."
USER_AGENT: str = ""
VERSION: str = "2.2.7"
HAS_HCAPTCHA: bool = False

# ── UI palette ──
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

# ── config hooks (set by selfbot.py at boot to avoid circular import) ──
load_config = None       # () -> dict
save_config = None       # (dict) -> None
log_msg = None           # (tag, content) -> None
db_inc_stat = None       # (cmd) -> None
log_enabled = None       # () -> bool

# ── cog-owned function pointers (set by selfbot.py at boot) ──
HOSTED_DISPATCH = None            # async (client, message) -> None — for hosted account on_message
async_hosted_token_add = None     # async (token, username=None) -> None
async_hosted_token_remove = None  # async (token) -> None
neko_gif = None                   # async (action) -> url | None
translate_text = None             # async (text, lang) -> str
send_temp = None                  # async (channel, content) -> Message
set_hypesquad = None              # async (house_id) -> (bool, str)
clear_hypesquad = None            # async () -> bool
encrypt_file = None               # (path) -> bool
decrypt_file = None               # (path) -> bool
_perm_check_ref = None            # selfbot._perm_check (guard sync)

# ── permission + guard state ──
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

# ── hosted accounts (host cog owns these) ──
HOSTED_TOKENS: list = []
_host_sessions: list = []
_hosted_clients: list = []
_hosted_spawned: bool = False
_host_lock = None   # asyncio.Lock, set lazily

# ── quest state ──
_hcaptcha_agent = None

# ── voice / mass / nuke / scrape / webhooks: no persistent state ──

# ── automod / monitor state ──
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

# ── backup / nuke state ──
_server_backups: dict = {}
_nuke_backups: dict = {}

# ── scheduler state ──
_scheduler: list = []
_scheduler_task = None

# ── task manager (moved from selfbot.py) ──
_managed_tasks: dict = {}
_TASK_STORE = "database/tasks.json"
_TRIGGER_STORE = "database/triggers.json"

# ── triggers ──
_triggers = {"message": [], "reaction": [], "voice": [], "member": []}
_trigger_fired_counts: dict = {}

# ── db state ──
_db_path = "database/selfbot.db"
_db: sqlite3.Connection | None = None

def db_open():
    global _db
    _db = sqlite3.connect(_db_path, check_same_thread=False)
    c = _db.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS notes (user_id TEXT, note TEXT, ts REAL);
    CREATE TABLE IF NOT EXISTS history (user_id TEXT, entry TEXT, ts REAL);
    CREATE TABLE IF NOT EXISTS stats (cmd TEXT PRIMARY KEY, count INTEGER);
    CREATE TABLE IF NOT EXISTS sched (id TEXT, when_ts REAL, action TEXT, payload TEXT);
    """)
    _db.commit()

def db_note_add(uid, note):
    _db.execute("INSERT INTO notes VALUES (?,?,?)", (str(uid), note, time.time())); _db.commit()

def db_note_list(uid):
    return _db.execute("SELECT note, ts FROM notes WHERE user_id=? ORDER BY ts DESC", (str(uid),)).fetchall()

def db_note_clear(uid):
    _db.execute("DELETE FROM notes WHERE user_id=?", (str(uid),)); _db.commit()

def db_hist_add(uid, entry):
    _db.execute("INSERT INTO history VALUES (?,?,?)", (str(uid), entry, time.time())); _db.commit()

def db_hist_list(uid, limit=50):
    return _db.execute("SELECT entry, ts FROM history WHERE user_id=? ORDER BY ts DESC LIMIT ?", (str(uid), limit)).fetchall()

def db_stats_inc(cmd):
    _db.execute("INSERT INTO stats VALUES (?,1) ON CONFLICT(cmd) DO UPDATE SET count=count+1", (cmd,))
    _db.commit()

def db_stats_all():
    return _db.execute("SELECT cmd, count FROM stats ORDER BY count DESC").fetchall()

def db_stats_clear():
    _db.execute("DELETE FROM stats"); _db.commit()

# ── lastfm state ──
LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"
_lfm: dict = {}
_PERIOD = {"w":"7day","week":"7day","m":"1month","month":"1month",
           "3m":"3month","6m":"6month","y":"12month","year":"12month","all":"overall","overall":"overall"}
_PLABEL = {"7day":"this week","1month":"this month","3month":"3 months",
           "6month":"6 months","12month":"this year","overall":"all time"}

# ── social state ──
_autoaddback = False

# ── status state ──
_speak_lang = None

# ── agc ──
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
    with open("config/agc_whitelist.json", "w") as f:
        json.dump(list(_agc_whitelist), f)

# ── admin helpers (host cog owns) ──
def _load_admins() -> list:
    cfg = load_config() or {}
    admins = cfg.get("admins", [])
    if not isinstance(admins, list): return []
    return [str(x) for x in admins]

def _save_admins(admins: list):
    cfg = load_config() or {}
    cfg["admins"] = [str(x) for x in admins]
    save_config(cfg)

def _owner_id() -> str:
    cfg = load_config() or {}
    return str(cfg.get("owner_id", "") or "")

def _set_owner(uid: str):
    cfg = load_config() or {}
    cfg["owner_id"] = str(uid)
    save_config(cfg)

def _is_owner(user_id) -> bool:
    o = _owner_id()
    return bool(o) and str(user_id) == o

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

# ── scheduler helpers ──
def sched_save():
    try:
        with open("database/scheduler.json", "w") as f:
            json.dump(_scheduler, f, indent=2)
    except Exception: pass

def sched_load():
    global _scheduler
    if os.path.exists("database/scheduler.json"):
        try:
            with open("database/scheduler.json") as f: _scheduler = json.load(f)
        except Exception: _scheduler = []

# ── settings state ──
_server_prefixes: dict = {}
_aliases: dict = {}
_cooldowns: dict = {}
_cooldown_last: dict = {}
_profiles: dict = {}
_encrypt_enabled = False
_has_crypto = False
_HAS_CRYPTO = False    # mirror name; keep both if some code references either
_key_path = "config/.key"

# ── resilience state ──
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

# caches owned by resilience / general
_snipe_cache: dict = {}
_editsnipe_cache: dict = {}
_typing_tasks: dict = {}
_tracking: dict = {}
_cache_auto = True
_tracked_users: set = set()

# ── general cog state ──
SNIPE_LIMIT = 20
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

# ── fun / auto / utility state ──
_autoreact_emoji = None
_multireact_pool: list = []
_multireact_enabled = False
_giveaway_enabled = False
_nitrosniper_enabled = True
_vsniper_list: list = []
_vsniper_task = None
_afk_enabled = False
_afk_msg = None
_autodelete_secs = 0

# ── developer state ──
_proxy = None
_plugins: dict = {}
_sessions: list = []
_session_idx = 0
