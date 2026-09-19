# selfbot.py | Python 3.10+ | discord.py-self + aiohttp
# sy's selfbot — full rewrite (Railway-fixed)

import discord
import asyncio
import aiohttp
import json
import os
import sys
import re
import base64
import io
import math
import random
import string
import hashlib
import configparser
import datetime as dt
from datetime import datetime, timezone, timedelta
from uuid import uuid4

# ─────────────────────────────────────────────
# BOOTSTRAP
# ─────────────────────────────────────────────

os.makedirs("config", exist_ok=True)
os.makedirs("database", exist_ok=True)

DEFAULT_APP_ID = "1550836202091843684"

def load_config():
    if os.path.exists("config.json"):
        try:
            with open("config.json", "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(cfg):
    try:
        with open("config.json", "w") as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        print(f"[Config] save error: {e}")

_cfg = load_config()

TOKEN = (
    os.environ.get("TOKEN", "").strip()
    or os.environ.get("DISCORD_TOKEN", "").strip()
    or str(_cfg.get("token", "")).strip()
).strip('"').strip("'")

print(f"[selfbot] token: {TOKEN[:10]}...{TOKEN[-5:] if len(TOKEN) > 15 else ''}")

if not TOKEN or TOKEN in ("YOUR_TOKEN_HERE", "", "None"):
    print("[FATAL] No token. Set TOKEN env var or config.json")
    sys.exit(1)

PREFIX = os.environ.get("PREFIX") or _cfg.get("prefix", ".")
VERSION = "1.3.0"
LOG_FILE = "message_log.txt"

# ─────────────────────────────────────────────
# UI HELPER — ansi block formatting (no bars)
# ─────────────────────────────────────────────

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

def _ansi_block(lines: list[str]) -> str:
    result = "> ```ansi\n"
    for line in lines:
        if line.strip() == "":
            result += "> \n"
        else:
            result += f"> {line}\n"
    result += "> ```"
    cleaned = []
    for l in result.split("\n"):
        if re.match(r'^>\s*$', l):
            continue
        cleaned.append(l)
    return "\n".join(cleaned)

def ui_box(title: str, rows: list[str], footer: str = "") -> str:
    lines = [f"  {WHITE}{title}{RESET}"]
    for row in rows:
        lines.append(row)
    if footer:
        lines.append("")
        lines.append(f"  {DIM}{footer}{RESET}")
    return _ansi_block(lines)

def ui_row(cmd: str, desc: str) -> str:
    return f"  {GREY}├{RESET} {WHITE}{cmd}{RESET}  {DIM}{desc}{RESET}"

def ui_section(name: str) -> str:
    return f"\n  {YELLOW}[{name}]{RESET}"

def ui_ok(msg: str) -> str:
    return _ansi_block([f"  {GREEN}✓{RESET}  {msg}"])

def ui_err(msg: str) -> str:
    return _ansi_block([f"  {RED}✗{RESET}  {msg}"])

def ui_info(msg: str) -> str:
    return _ansi_block([f"  {CYAN}•{RESET}  {msg}"])

def ui_warn(msg: str) -> str:
    return _ansi_block([f"  {YELLOW}!{RESET}  {msg}"])

def ui_progress(label: str, pct: int) -> str:
    filled = int(pct / 10)
    bar = f"{GREEN}{'█' * filled}{GREY}{'░' * (10 - filled)}{RESET}"
    return f"  {bar} {WHITE}{pct}%{RESET}  {DIM}{label}{RESET}"

# ─────────────────────────────────────────────
# PAGINATED HELP SYSTEM
# ─────────────────────────────────────────────

PAGE_SIZE = 8

def _paginate(title: str, subtitle: str, rows: list[str], page: int = 1) -> str:
    total_pages = max(1, math.ceil(len(rows) / PAGE_SIZE))
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    chunk = rows[start:start + PAGE_SIZE]
    lines = [f"  {WHITE}> {title}{RESET}  {DIM}{subtitle}{RESET}", ""]
    lines.extend(chunk)
    lines.append("")
    lines.append(f"  {DIM}page {page}/{total_pages}  •  {PREFIX}h {title.lower()} {page + 1 if page < total_pages else 1} to flip{RESET}")
    return _ansi_block(lines)

HELP_DATA: dict[str, list[tuple]] = {
    "general": [
        ("ping",              "latency check"),
        ("info",              "account snapshot"),
        ("say <text>",        "replace command with text"),
        ("spam <n> <text>",   "blast n messages fast"),
        ("spamstop",          "kill active spam loop"),
        ("purge [n]",         "delete your last n messages"),
        ("clear",             "delete command message"),
        ("snipe [n]",         "snipe last deleted message"),
        ("snipe clear",       "wipe snipe cache"),
        ("editsnipe [n]",     "snipe last edited message"),
        ("editsnipe clear",   "wipe edit-snipe cache"),
        ("copycat <id>",      "mirror next 10 msgs from user"),
        ("status <text>",     "set custom status"),
        ("status clear",      "clear status"),
        ("platform <type>",   "spoof gateway platform"),
        ("platform off",      "reset platform to desktop"),
        ("hypesquad <house>", "set hypesquad house"),
        ("hypesquad off",     "remove hypesquad badge"),
    ],
    "rpc": [
        ("rpc enable",                        "turn on rich presence"),
        ("rpc disable / stop / clear",        "clear rich presence"),
        ("rpc status",                        "show current rpc config"),
        ("rpc application_id <id>",           "set the app id for asset registration"),
        ("rpc type",                          "playing/streaming/watching/listening/competing"),
        ("rpc name",                          "activity name"),
        ("rpc details",                       "details line"),
        ("rpc state",                         "state line"),
        ("rpc url",                           "streaming url"),
        ("rpc start / end",                   "timestamps (unix/MM:SS/none)"),
        ("rpc large_image <url or key>",      "large image (auto-registers url)"),
        ("rpc large_text",                    "large image hover text"),
        ("rpc small_image <url or key>",      "small image (auto-registers url)"),
        ("rpc small_text",                    "small image hover text"),
        ("rpc button1/2_name/url",            "rpc buttons"),
        ("rpc party enable/disable/current/max", "party config"),
        ("rpc spotify <title> | <artist> | <secs>", "spotify brand rpc"),
        ("rpc youtube <video> | <channel> | <secs>", "youtube brand rpc"),
        ("rpc xbox <game>",                   "xbox brand rpc"),
        ("rpc playstation <game>",            "playstation brand rpc"),
        ("rpc crunchyroll <anime> | <ep>",    "crunchyroll brand rpc"),
        ("rpc roblox <game> | <details>",     "roblox brand rpc"),
        ("rpc custom <name> | <details> | <state>", "custom rpc"),
    ],
    "quests": [
        ("quest",               "list active quests + progress"),
        ("questrun <index>",    "solve specific quest"),
        ("questall",            "solve all quests at once (fast)"),
        ("autoquest on/off",    "auto-run quests on startup"),
        ("autoclaim on/off",    "auto-claim completed quests"),
        ("orbbadge",            "claim orb badge"),
        ("captcha set <key>",   "set 2captcha api key"),
    ],
    "sniper": [
        ("sniper on/off",   "toggle nitro gift sniper"),
        ("logger on/off",   "toggle message logger"),
        ("readlog [n]",     "read last n log lines"),
    ],
    "ar": [
        ("ar add <trigger> | <response>", "add auto-response"),
        ("ar remove <trigger>",           "remove auto-response"),
        ("ar list",                       "list all auto-responses"),
    ],
    "voice": [
        ("vcjoin [ch_id]",           "join a voice channel"),
        ("vcleave",                  "leave voice channel"),
        ("vcmute <user_id>",         "server mute user"),
        ("vcunmute <user_id>",       "server unmute user"),
        ("vcdeafen <user_id>",       "server deafen user"),
        ("vcundeafen <user_id>",     "server undeafen user"),
        ("vckick <user_id>",         "kick user from vc"),
        ("vcmove <user> <ch_id>",    "move user to channel"),
        ("vcmoveall <ch1> <ch2>",    "move all users ch1 → ch2"),
    ],
    "fun": [
        ("gayrate [user_id]",     "gay percentage"),
        ("feed <user_id>",        "feed a user"),
        ("tickle <user_id>",      "tickle a user"),
        ("slap <user_id>",        "slap a user"),
        ("hug <user_id>",         "hug a user"),
        ("cuddle <user_id>",      "cuddle a user"),
        ("pat <user_id>",         "pat a user"),
        ("kiss <user_id>",        "kiss a user"),
        ("poke <user_id>",        "poke a user"),
        ("wink <user_id>",        "wink at a user"),
        ("smug <user_id>",        "smug at a user"),
        ("boop <user_id>",        "boop a user"),
        ("nom <user_id>",         "nom a user"),
        ("mimic <user_id>",       "mirror user's messages"),
        ("unmimic <user_id>",     "stop mimicking user"),
        ("stopmimic",             "stop all mimics"),
        ("meme",                  "random meme"),
        ("joke",                  "random joke"),
    ],
    "tools": [
        ("nitro",                "generate random nitro url"),
        ("applybypass <invite>", "bypass apply-to-join"),
        ("tokeninfo <token>",    "decode a discord token"),
        ("calculate <expr>",     "evaluate math expression"),
        ("fact",                 "random useless fact"),
        ("fetchlyrics <artist - title>", "fetch song lyrics"),
        ("robuxtax <amount>",    "roblox marketplace fee calc"),
        ("archivechannel [ch_id]", "save channel messages to txt"),
    ],
    "host": [
        ("host add <token>",        "add account to host list"),
        ("host remove <token>",     "remove from host list"),
        ("host list",               "list hosted accounts"),
        ("host broadcast <msg>",    "send msg from all hosted accounts"),
        ("host say <idx> <msg>",    "force hosted account to say something"),
    ],
    "lastfm": [
        ("lastfm set <user> [key]",       "link your last.fm account"),
        ("lastfm np",                     "now playing track"),
        ("lastfm recent [n]",             "last n scrobbles"),
        ("lastfm topartists [w/m/y/all]", "top artists"),
        ("lastfm toptracks [w/m/y/all]",  "top tracks"),
        ("lastfm topalbums [w/m/y/all]",  "top albums"),
        ("lastfm stats",                  "scrobble count & stats"),
        ("lastfm compare <user>",         "taste compatibility"),
        ("lastfm rpc",                    "set rpc to now playing"),
        ("lastfm autorpc on/off",         "auto-update rpc with scrobbles"),
    ],
    "settings": [
        ("prefix <new>",         "change command prefix"),
        ("version",              "show selfbot version"),
        ("reload",               "reload config from disk"),
    ],
    "developer": [
        ("host say <idx> <msg>",     "force hosted account to say"),
        ("host broadcast <msg>",     "broadcast from all accounts"),
        ("logs [n]",                 "tail railway / selfbot console"),
        ("eval <code>",              "evaluate python code"),
        ("restart",                  "restart the selfbot process"),
    ],
    "server": [
        ("serverinfo",               "current server info"),
        ("members [n]",              "list server members"),
        ("channels",                 "list server channels"),
        ("roles",                    "list server roles"),
        ("ban <user_id> [reason]",   "ban a user"),
        ("kick <user_id> [reason]",  "kick a user"),
        ("mute <user_id>",           "timeout a user (10 min)"),
        ("unmute <user_id>",         "remove timeout"),
        ("createrole <name>",        "create a role"),
        ("delrole <role_id>",        "delete a role"),
        ("createchannel <name>",     "create a text channel"),
        ("deletechannel <ch_id>",    "delete a channel"),
        ("setnick <user> <nick>",    "set a member's nickname"),
        ("topic <text>",             "set channel topic"),
        ("slowmode <seconds>",       "set channel slowmode"),
    ],
    "information": [
        ("userinfo [user_id]",      "discord user lookup"),
        ("avatar [user_id]",        "get user avatar"),
        ("serverinfo",              "server details"),
        ("channelinfo [ch_id]",     "channel details"),
        ("roleinfo <role_id>",      "role details"),
        ("checkname <username>",    "check if discord username is taken"),
        ("whois <user_id>",         "full user profile dump"),
    ],
    "groupchat": [
        ("gclist",                      "list your group DMs"),
        ("gccreate <user1> [user2...]", "create a group DM"),
        ("gcrename <name>",             "rename current group DM"),
        ("gcicon <url>",                "set group DM icon"),
        ("gcleave",                     "leave current group DM"),
        ("gcadd <user_id>",             "add user to group DM"),
        ("gcremove <user_id>",          "remove user from group DM"),
        ("agc on/off",                  "anti gc-trap toggle"),
        ("agc block on/off",            "auto-block gc-trap owner"),
        ("agc msg <text>",              "set leave message"),
        ("agc name <text>",             "set gc rename on trap"),
        ("agc icon <url>",              "set gc icon on trap"),
        ("agc webhook <url>",           "set webhook for trap alerts"),
        ("agc whitelist <user_id>",     "whitelist a user from agc"),
        ("agc unwhitelist <user_id>",   "remove from agc whitelist"),
        ("agc wllist",                  "show agc whitelist"),
    ],
    "utility": [
        ("uwuify <text>",           "uwuify text"),
        ("owoify <text>",           "owoify text"),
        ("mock <text>",             "spongebob mock case"),
        ("reverse <text>",          "reverse text"),
        ("aesthetic <text>",        "full-width text"),
        ("clap <text>",             "👏 add 👏 claps"),
        ("animatetype <text>",      "type message character-by-character"),
        ("checkname <username>",    "check if username is available"),
        ("typing",                  "start continuous typing indicator"),
        ("typingstop",              "stop typing indicator"),
        ("afk [msg]",               "set AFK auto-reply"),
        ("afkstop",                 "disable AFK"),
        ("translate <lang> <text>", "translate text"),
        ("ghostping <user_id>",     "ghost ping a user"),
        ("pin <msg_id>",            "pin a message"),
        ("unpin <msg_id>",          "unpin a message"),
        ("therapy",                 "random therapy response"),
        ("ragebait",                "random ragebait"),
        ("purgeall",                "delete all your msgs in channel"),
        ("firstmessage",            "get first message in channel"),
    ],
    "tracking": [
        ("track <user_id>",       "track a user's messages in channel"),
        ("untrack <user_id>",     "stop tracking user"),
        ("tracklist",             "list tracked users"),
        ("history <user_id>",     "show tracked message history"),
    ],
    "downloads": [
        ("yt <url>",             "download youtube video"),
        ("ytaudio <url>",        "download youtube audio"),
        ("tiktok <url>",         "download tiktok video"),
        ("instagram <url>",      "download instagram post"),
    ],
    "social": [
        ("addfriend <user_id>",      "send friend request"),
        ("removefriend <user_id>",   "remove friend"),
        ("block <user_id>",          "block user"),
        ("unblock <user_id>",        "unblock user"),
        ("friends",                  "list all friends"),
        ("blocked",                  "list blocked users"),
        ("pending",                  "show pending friend requests"),
        ("clearincoming",            "decline all incoming requests"),
        ("clearoutgoing",            "cancel all outgoing requests"),
        ("friendcount",              "friend / block / pending counts"),
        ("closedms",                 "close all DM channels"),
        ("readdms",                  "mark all DMs as read"),
        ("note <user_id> <text>",    "set note on user"),
        ("autoaddback on/off",       "auto-accept friend requests"),
    ],
    "auto": [
        ("giveaway on/off",          "auto-enter giveaways"),
        ("nitrosniper on/off",       "auto-redeem nitro gift codes"),
        ("autoreact <emoji>",        "auto-react to your own messages"),
        ("autoreactstop",            "stop auto-react"),
        ("multireact add <emoji>",   "add emoji to multi-react pool"),
        ("multireact remove <emoji>","remove emoji from pool"),
        ("multireact list",          "list pool"),
        ("multireact on/off",        "toggle multi-react"),
        ("autoaddback on/off",       "auto-accept friend requests"),
        ("vsniper add <code> <gid>", "add vanity url to watch list"),
        ("vsniper start/stop/list",  "vanity sniper control"),
    ],
    "profile": [
        ("setpfp <url>",         "set profile picture from url"),
        ("setbio <text>",        "set profile bio"),
        ("setbanner <url>",      "set profile banner"),
        ("myprofile",            "show your own profile info"),
        ("accountbackup",        "backup account to JSON"),
    ],
    "status": [
        ("setstatus <text>",                    "set custom status text"),
        ("setstatus <emoji>, <text>",           "set status with emoji"),
        ("setstatus <:name:id>, <text>",        "set status with custom emoji"),
        ("clearstatus",                         "clear your custom status"),
        ("stealstatus <user_id>",               "copy a user's custom status"),
        ("statushistory",                        "show your recent status history"),
    ],
}

def build_help_root(page: int = 1) -> str:
    categories = list(HELP_DATA.keys())
    total_pages = max(1, math.ceil(len(categories) / 10))
    page = max(1, min(page, total_pages))
    chunk = categories[(page - 1) * 10 : page * 10]
    lines = [
        f"  {WHITE}> sy's selfbot{RESET}  {DIM}v{VERSION}{RESET}",
        "",
        f"  {GREY}categories{RESET}",
        "",
    ]
    for cat in chunk:
        desc_map = {
            "general": "utilities, platform & status",
            "rpc": "rich presence & brands",
            "quests": "quest completer & orb badge",
            "sniper": "nitro sniper & logger",
            "ar": "auto-responder",
            "voice": "voice channel controls",
            "fun": "fun & roleplay commands",
            "tools": "tools & generators",
            "host": "multi-account hosting",
            "lastfm": "last.fm integration",
            "settings": "prefix & selfbot config",
            "developer": "dev tools & console",
            "server": "server management",
            "information": "user & server lookup",
            "groupchat": "group dm & anti-gc",
            "utility": "uwuify, afk, translate & misc",
            "tracking": "message & profile tracking",
            "downloads": "media downloader",
            "social": "friends & social management",
            "auto": "automation & snipers",
            "profile": "account & profile management",
            "status": "custom status management",
        }
        desc = desc_map.get(cat, "commands")
        lines.append(f"  {CYAN}{cat:<14}{RESET}  {DIM}{desc}{RESET}")
    lines.append("")
    lines.append(f"  {DIM}{PREFIX}help <category> [page]  •  {PREFIX}help <page> to flip{RESET}")
    lines.append(f"  {DIM}page {page}/{total_pages}  •  sy | ver {VERSION}{RESET}")
    return _ansi_block(lines)

def build_help_section(cat: str, page: int = 1) -> str:
    if cat not in HELP_DATA:
        return ui_err(f"unknown category: {cat}  —  use {PREFIX}help")
    rows_raw = HELP_DATA[cat]
    total_pages = max(1, math.ceil(len(rows_raw) / PAGE_SIZE))
    page = max(1, min(page, total_pages))
    chunk = rows_raw[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    lines = [f"  {WHITE}> {cat}{RESET}", ""]
    for cmd, desc in chunk:
        lines.append(f"  {GREY}├{RESET} {WHITE}{PREFIX}{cmd}{RESET}  {DIM}{desc}{RESET}")
    lines.append("")
    lines.append(f"  {DIM}page {page}/{total_pages}  •  {PREFIX}h {cat} {(page % total_pages) + 1}{RESET}")
    return _ansi_block(lines)

# ─────────────────────────────────────────────
# STATE / GLOBALS
# ─────────────────────────────────────────────

client = discord.Client(chunk_guilds_at_startup=False, request_guilds=True)

AUTO_RESPONSES:  dict[str, str] = {}
SNIPER_ENABLED  = True
LOGGER_ENABLED  = True
_mimic_dict:    dict[int, list[int]] = {}
_tracking:      dict[int, list[dict]] = {}
_tracked_users: set[int] = set()
_afk_msg:       str | None = None
_afk_enabled   = False
_typing_tasks:  dict[int, asyncio.Task] = {}
_autoreact_emoji: str | None = None

_multireact_pool: list[str] = []
_multireact_enabled = False

_spam_tasks: dict[int, asyncio.Task] = {}

_snipe_cache:      dict[int, list[dict]] = {}
_editsnipe_cache:  dict[int, list[dict]] = {}
SNIPE_LIMIT = 20

_rpc_asset_cache: dict[str, str] = {}

_autoaddback   = False
_giveaway_enabled = False
_nitrosniper_enabled = True
_autorpc_enabled = False
_autorpc_task:  asyncio.Task | None = None
_captcha_key:   str = ""
_autoclaim_enabled = False
_speak_lang:    str | None = None
_vsniper_list:  list[dict] = []
_vsniper_task:  asyncio.Task | None = None

# ─────────────────────────────────────────────
# AGC STATE
# ─────────────────────────────────────────────

_agc_state = {
    "enabled":     False,
    "block":       False,
    "leave_msg":   "lol nice try",
    "gc_name":     "trap detected",
    "gc_icon_url": None,
    "webhook_url": None,
}
_agc_whitelist: set[str] = set()

def _agc_load_wl():
    global _agc_whitelist
    path = "config/agc_whitelist.json"
    if os.path.exists(path):
        try:
            with open(path) as f:
                _agc_whitelist = set(json.load(f))
        except Exception:
            _agc_whitelist = set()

def _agc_save_wl():
    with open("config/agc_whitelist.json", "w") as f:
        json.dump(list(_agc_whitelist), f)

_agc_load_wl()

# ─────────────────────────────────────────────
# HOSTED ACCOUNTS
# ─────────────────────────────────────────────

HOSTED_TOKENS: list[str] = list(_cfg.get("hosted_tokens", []))

def save_hosted():
    cfg = load_config()
    cfg["hosted_tokens"] = HOSTED_TOKENS
    save_config(cfg)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) discord/1.0.9044 Chrome/120.0.6099.291 "
    "Electron/28.2.10 Safari/537.36"
)

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

def log_msg(tag: str, content: str):
    if not LOGGER_ENABLED:
        return
    line = f"[{datetime.now().strftime('%H:%M:%S')}] [{tag}] {content}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ─────────────────────────────────────────────
# RPC CONFIG
# ─────────────────────────────────────────────

def load_rpc_cfg():
    path = "config/rpc_config.json"
    default = {
        "enabled": False, "type": "playing", "name": "selfbot",
        "state": "", "details": "", "url": "",
        "application_id": DEFAULT_APP_ID,
        "large_image": "", "large_text": "", "small_image": "", "small_text": "",
        "start_timestamp": None, "end_timestamp": None,
        "party": {"enabled": False, "current": 1, "max": 5},
        "buttons": [{"label": "", "url": ""}, {"label": "", "url": ""}],
    }
    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump(default, f, indent=4)
        return default
    try:
        with open(path) as f:
            d = json.load(f)
        if not d.get("application_id"):
            d["application_id"] = DEFAULT_APP_ID
        for k, v in default.items():
            if k not in d:
                d[k] = v
        return d
    except Exception:
        return default

def save_rpc_cfg(cfg):
    with open("config/rpc_config.json", "w") as f:
        json.dump(cfg, f, indent=4)

# ── RPC external-asset registration (rpc.txt flow) ─────────────
async def register_external_image(url: str, application_id: str, token: str) -> str | None:
    endpoint = f"https://discord.com/api/v9/applications/{application_id}/external-assets"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    body = {"urls": [url]}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=body, headers=headers) as resp:
                if resp.status != 200:
                    txt = await resp.text()
                    print(f"[RPC asset] register failed {resp.status}: {txt[:200]}")
                    return None
                data = await resp.json()
                if data and isinstance(data, list):
                    return data[0].get("external_asset_path")
    except Exception as e:
        print(f"[RPC asset] {e}")
    return None

async def resolve_rpc_image(url: str, application_id: str, token: str) -> str | None:
    if not url:
        return None
    if not (url.startswith("http://") or url.startswith("https://")):
        return url
    if not application_id:
        return url
    cache_key = f"{application_id}:{url}"
    if cache_key in _rpc_asset_cache:
        return _rpc_asset_cache[cache_key]
    key = await register_external_image(url, str(application_id), token)
    if key:
        _rpc_asset_cache[cache_key] = key
        return key
    return None

async def update_rpc():
    try:
        cfg = load_rpc_cfg()
        if not cfg.get("enabled"):
            await client.change_presence(activity=None)
            return
        from discord.activity import ActivityAssets, ActivityTimestamps
        from discord import ActivityType, Activity, ActivityButton
        t = cfg.get("type", "playing")
        tmap = {"playing": ActivityType.playing, "streaming": ActivityType.streaming,
                "listening": ActivityType.listening, "watching": ActivityType.watching,
                "competing": ActivityType.competing}
        act_type = tmap.get(t, ActivityType.playing)
        kw: dict = {"name": cfg.get("name") or "selfbot", "type": act_type}

        app_id = cfg.get("application_id") or DEFAULT_APP_ID
        try: kw["application_id"] = int(app_id)
        except Exception: pass

        if act_type == ActivityType.streaming and cfg.get("url"):
            kw["url"] = cfg["url"]
        if cfg.get("state"):   kw["state"]   = cfg["state"]
        if cfg.get("details"): kw["details"] = cfg["details"]

        ak = {}
        for slot in ("large_image", "small_image"):
            raw = cfg.get(slot)
            if raw:
                resolved = await resolve_rpc_image(raw, str(app_id), TOKEN)
                if resolved:
                    ak[slot] = resolved
                else:
                    ak[slot] = raw
        for slot in ("large_text", "small_text"):
            if cfg.get(slot):
                ak[slot] = cfg[slot]
        if ak:
            try: kw["assets"] = ActivityAssets(**ak)
            except Exception: pass

        if cfg.get("start_timestamp"):
            try:
                ts_kw = {"start": datetime.fromtimestamp(float(cfg["start_timestamp"]), tz=timezone.utc)}
                if cfg.get("end_timestamp"):
                    ts_kw["end"] = datetime.fromtimestamp(float(cfg["end_timestamp"]), tz=timezone.utc)
                kw["timestamps"] = ActivityTimestamps(**ts_kw)
            except Exception: pass
        btns = [ActivityButton(label=b["label"], url=b["url"])
                for b in cfg.get("buttons", [])[:2] if b.get("label") and b.get("url")]
        if btns:
            try: kw["buttons"] = btns
            except Exception: pass
        await client.change_presence(activity=Activity(**kw))
    except Exception as e:
        print(f"[RPC] {e}")

async def rpc_prompt(channel, author, label: str) -> str | None:
    pm = await channel.send(_ansi_block([f"  {YELLOW}✏  {label}{RESET}", f"  {DIM}type value — 60s — 'none' to clear{RESET}"]))
    def check(m): return m.author.id == author.id and m.channel.id == channel.id
    try:
        msg = await client.wait_for("message", check=check, timeout=60)
        val = msg.content.strip()
        try: await msg.delete()
        except Exception: pass
        try: await pm.delete()
        except Exception: pass
        return None if val.lower() == "none" else val
    except asyncio.TimeoutError:
        try: await pm.delete()
        except Exception: pass
        await channel.send(ui_err("timed out"), delete_after=4)
        return "__TIMEOUT__"

# ─────────────────────────────────────────────
# BRAND RPC
# ─────────────────────────────────────────────

BRAND_ICONS = {
    "spotify":     "https://cdn.discordapp.com/app-icons/367827983903490050/c1aac7c70a2df8bf5b50b88e2de36ff2.webp?size=256",
    "youtube":     "https://cdn.discordapp.com/app-icons/880218394199220334/5c1bbad36e89e14af4a6eb2a73dc26a3.webp?size=256",
    "xbox":        "https://cdn.discordapp.com/app-icons/438122941302046720/92f4c4e1f4fb1d7c2a69ba8c3e71bf4a.webp?size=256",
    "roblox":      "https://cdn.discordapp.com/app-icons/363445589247131668/95b28c0f29d0c9d3360d38c4e76c6855.webp?size=256",
    "crunchyroll": "https://cdn.discordapp.com/app-icons/1020123345567822899/76dac659b13a77e3c79a6e4cc6ab75b2.webp?size=256",
    "playstation": "https://cdn.discordapp.com/app-icons/473226677884194826/2dd91e54b57ad2cb4949dc4b24feae0d.webp?size=256",
}

BRAND_APP_IDS = {b: DEFAULT_APP_ID for b in BRAND_ICONS}

async def apply_brand_rpc(brand: str, user_args: list[str]) -> bool:
    try:
        from discord.activity import ActivityAssets, ActivityTimestamps
        from discord import ActivityType, Activity
    except ImportError as e:
        print(f"[RPC] import error: {e}")
        return False

    now = datetime.now(timezone.utc)

    def _ts(start=None, end=None):
        try:
            kw = {}
            if start: kw["start"] = start
            if end:   kw["end"]   = end
            return ActivityTimestamps(**kw)
        except Exception: return None

    def _parts(n=3):
        raw = " ".join(user_args) if user_args else ""
        p = [x.strip() for x in raw.split("|")]
        while len(p) < n: p.append("")
        return p

    app_id = DEFAULT_APP_ID
    raw_icon = BRAND_ICONS.get(brand, "")
    icon_key = None
    if raw_icon:
        icon_key = await resolve_rpc_image(raw_icon, app_id, TOKEN)

    kw: dict = {"application_id": int(app_id)}
    ak = {}
    if icon_key:
        ak["large_image"] = icon_key
        ak["small_image"] = icon_key
    ak["large_text"] = brand.capitalize()
    ak["small_text"] = brand.capitalize()

    if brand == "spotify":
        p = _parts(3)
        title = p[0] or "Unknown"; artist = p[1] or "Unknown"
        try: dur = int(p[2]) if p[2] else 210
        except: dur = 210
        kw.update({"type": ActivityType.listening, "name": "Spotify",
                   "details": title, "state": artist})
        ak["large_text"] = "Spotify"; ak["small_text"] = artist
        ts = _ts(now, now + timedelta(seconds=dur))
        if ts: kw["timestamps"] = ts

    elif brand == "youtube":
        p = _parts(3)
        video = p[0] or "Video"; channel = p[1] or "Channel"
        try: dur = int(p[2]) if p[2] else 600
        except: dur = 600
        kw.update({"type": ActivityType.watching, "name": "YouTube",
                   "details": video, "state": channel})
        ak["large_text"] = channel
        ts = _ts(now, now + timedelta(seconds=dur))
        if ts: kw["timestamps"] = ts

    elif brand == "xbox":
        p = _parts(2)
        game = p[0] or "Game"; state = p[1] or "Playing on Xbox"
        kw.update({"type": ActivityType.playing, "name": "Xbox",
                   "details": game, "state": state})
        ak["large_text"] = game
        ts = _ts(now)
        if ts: kw["timestamps"] = ts

    elif brand == "playstation":
        p = _parts(2)
        game = p[0] or "Game"; state = p[1] or "Playing on PlayStation"
        kw.update({"type": ActivityType.playing, "name": "PlayStation",
                   "details": game, "state": state})
        ak["large_text"] = game
        ts = _ts(now)
        if ts: kw["timestamps"] = ts

    elif brand == "crunchyroll":
        p = _parts(3)
        anime = p[0] or "Anime"; ep = p[1] or ""
        try: dur = int(p[2]) if p[2] else 1440
        except: dur = 1440
        kw.update({"type": ActivityType.watching, "name": "Crunchyroll",
                   "details": anime})
        if ep: kw["state"] = ep
        ak["large_text"] = anime
        ts = _ts(now, now + timedelta(seconds=dur))
        if ts: kw["timestamps"] = ts

    elif brand == "roblox":
        p = _parts(5)
        game = p[0] or "Roblox"; det = p[1] or game
        state = p[2] or "Playing on Roblox"
        lt = p[3] or game; st = p[4] or "Roblox"
        kw.update({"type": ActivityType.playing, "name": "Roblox",
                   "details": det, "state": state})
        ak.update({"large_text": lt, "small_text": st})
        ts = _ts(now)
        if ts: kw["timestamps"] = ts

    elif brand == "custom":
        p = _parts(3)
        kw.update({"type": ActivityType.playing, "name": p[0] or "Custom"})
        if p[1]: kw["details"] = p[1]
        if p[2]: kw["state"]   = p[2]
        ts = _ts(now)
        if ts: kw["timestamps"] = ts
        ak = {}

    else:
        kw.update({"type": ActivityType.playing, "name": brand.capitalize()})
        ts = _ts(now)
        if ts: kw["timestamps"] = ts

    if ak:
        try:
            kw["assets"] = ActivityAssets(**ak)
        except Exception as e:
            print(f"[RPC] assets: {e}")

    try:
        await client.change_presence(activity=Activity(**kw))
        return True
    except Exception as e:
        print(f"[RPC] presence: {e}")
        return False

# ─────────────────────────────────────────────
# QUEST SYSTEM
# ─────────────────────────────────────────────

def _b64uid(token):
    try:
        first = token.split(".")[0]
        return base64.b64decode(first + "=" * (-len(first) % 4)).decode()
    except Exception: return None

def _quest_headers(token):
    sp = base64.b64encode(json.dumps({
        "os": "Windows", "browser": "Chrome", "device": "",
        "system_locale": "en", "has_client_mods": False,
        "browser_user_agent": USER_AGENT, "browser_version": "142.0.0.0",
        "os_version": "10", "release_channel": "stable",
        "client_launch_id": str(uuid4()),
        "client_build_number": 971383,
        "client_event_source": None,
    }).encode()).decode()
    return {
        "authorization": token.strip().strip('"').strip("'"),
        "accept": "*/*", "content-type": "application/json",
        "user-agent": USER_AGENT, "x-super-properties": sp,
        "origin": "https://discord.com", "referer": "https://discord.com/quest-home",
    }

class APIError(Exception):
    def __init__(self, status, body=None):
        super().__init__(f"Discord API {status}")
        self.status = status; self.body = body or {}

async def _api(session, method, url, headers=None, json_body=None, retries=3):
    last = None
    for attempt in range(retries):
        try:
            async with session.request(method, url, headers=headers, json=json_body) as r:
                text = await r.text()
                body = {}
                try: body = json.loads(text)
                except Exception: pass
                if r.status >= 400: raise APIError(r.status, body)
                return body
        except APIError as e:
            last = e
            if e.status == 429:
                ra = float(e.body.get("retry_after", 1.0))
                await asyncio.sleep(ra); continue
            if e.status >= 500:
                await asyncio.sleep(0.5 * (attempt + 1)); continue
            raise
    raise last

SUPPORTED_TASKS = ("WATCH_VIDEO","WATCH_VIDEO_ON_MOBILE","PLAY_ON_DESKTOP",
                   "PLAY_ON_DESKTOP_V2","PLAY_ACTIVITY","STREAM_ON_DESKTOP")

class QuestRecord:
    def __init__(self, data):
        self.data = data
        self.selected_task = self._pick()
        self.target = float(self.tasks.get(self.selected_task, {}).get("target", 0) or 0)

    @property
    def id(self): return str(self.data.get("id"))
    @property
    def cfg(self): return self.data.get("config", {})
    @property
    def msgs(self): return self.cfg.get("messages", {})
    @property
    def user_status(self): return self.data.get("user_status") or {}
    @property
    def tasks(self):
        tc = (self.cfg.get("task_config_v2") or self.cfg.get("task_config")
              or self.cfg.get("taskConfigV2") or self.cfg.get("taskConfig") or {})
        return tc.get("tasks", {})
    @property
    def name(self): return self.msgs.get("quest_name") or self.msgs.get("game_title") or "Quest"
    @property
    def reward(self):
        rw = self.cfg.get("rewards_config", {}).get("rewards", [])
        if rw: m = rw[0].get("messages", {}); return m.get("name") or "Reward"
        return "Reward"
    @property
    def app_id(self): return self.cfg.get("application", {}).get("id")
    @property
    def expires_at(self): return self.cfg.get("expires_at") or ""

    def is_completed(self): return bool(self.user_status.get("completed_at"))
    def is_enrolled(self): return bool(self.user_status.get("enrolled_at"))
    def is_supported(self): return self.selected_task in SUPPORTED_TASKS
    def progress_value(self):
        p = self.user_status.get("progress", {}).get(self.selected_task, {})
        return float(p.get("value", 0) or 0) if p else 0.0
    def progress_pct(self):
        return min(100, int(self.progress_value() / self.target * 100)) if self.target else 0
    def _pick(self):
        for t in SUPPORTED_TASKS:
            if t in self.tasks: return t
        return next(iter(self.tasks.keys()), "UNKNOWN")

class QuestService:
    def __init__(self, token, speed="fastest"):
        self.token = token.strip().strip('"').strip("'")
        self.speed = speed
        self.uid = _b64uid(token)
        self.headers = _quest_headers(self.token)

    async def fetch(self, session):
        try:
            d = await _api(session, "GET", "https://discord.com/api/v9/quests/@me", headers=self.headers)
            out = []
            for r in d.get("quests", []):
                q = QuestRecord(r)
                if q.expires_at:
                    try:
                        exp = dt.datetime.fromisoformat(q.expires_at.replace("Z", "+00:00"))
                        if dt.datetime.now(dt.timezone.utc) > exp: continue
                    except Exception: pass
                out.append(q)
            return out
        except Exception as e:
            print(f"[Quest] fetch: {e}"); return []

    async def enroll(self, session, quest):
        d = await _api(session, "POST",
            f"https://discord.com/api/v9/quests/{quest.id}/enroll",
            headers=self.headers,
            json_body={"location": 11, "is_targeted": False, "metadata_raw": None})
        if d: quest.data["user_status"] = d

    async def run(self, session, quest):
        if not quest.is_enrolled():
            await self.enroll(session, quest)
        if not quest.is_supported():
            return "unsupported"
        task = quest.selected_task
        if task in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
            return await self._video(session, quest)
        payloads = [{"stream_key": f"call:{quest.id}:1", "terminal": False}]
        if quest.app_id: payloads.append({"application_id": quest.app_id, "terminal": False})
        if task == "PLAY_ACTIVITY":
            payloads.insert(0, {"stream_key": f"call:{self.uid or quest.id}:1", "terminal": False})
        return await self._heartbeat(session, quest, payloads)

    async def _video(self, session, quest):
        interval = 2.0
        last = quest.progress_value()
        started = int(datetime.now(timezone.utc).timestamp()) - int(last)
        while last < quest.target:
            ts = min(float(quest.target),
                float(int(datetime.now(timezone.utc).timestamp()) - started))
            try:
                d = await _api(session, "POST",
                    f"https://discord.com/api/v9/quests/{quest.id}/video-progress",
                    headers=self.headers, json_body={"timestamp": ts}, retries=2)
                if d: quest.data["user_status"] = d
                last = max(last, ts, quest.progress_value())
                if d and d.get("completed_at"): break
            except APIError as e:
                if e.status == 400: await asyncio.sleep(1); continue
                break
            await asyncio.sleep(interval)
        return "completed" if quest.is_completed() or quest.progress_value() >= quest.target else "recovering"

    async def _heartbeat(self, session, quest, payloads):
        interval = 15
        active = payloads[0]
        while True:
            d = None
            for p in payloads:
                try:
                    d = await _api(session, "POST",
                        f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                        headers=self.headers, json_body=p, retries=2)
                    active = p; break
                except APIError: continue
            if d: quest.data["user_status"] = d
            if quest.is_completed() or quest.progress_value() >= quest.target:
                break
            await asyncio.sleep(interval)
        try:
            t = dict(active); t["terminal"] = True
            await _api(session, "POST",
                f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                headers=self.headers, json_body=t, retries=1)
        except Exception: pass
        return "completed" if quest.is_completed() else "recovering"

async def autoquest_run(token):
    svc = QuestService(token)
    async with aiohttp.ClientSession() as session:
        quests = await svc.fetch(session)
        active = [q for q in quests if not q.is_completed() and q.is_supported()]
        if not active: return
        for q in active:
            print(f"[AutoQuest] {q.name}")
            await svc.run(session, q)
            print(f"[AutoQuest] ✓ {q.name}")

# ─────────────────────────────────────────────
# ORB BADGE
# ─────────────────────────────────────────────

ORB_SKU = "1342211853484429445"

async def claim_orb(token):
    h = {"authorization": token, "content-type": "application/json", "user-agent": USER_AGENT,
         "origin": "https://discord.com", "referer": "https://discord.com/shop?tab=orbs"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"https://discord.com/api/v9/virtual-currency/skus/{ORB_SKU}/redeem",
                headers=h, json={}) as r:
                return r.status in (200, 201, 204), await r.text()
        except Exception as e:
            return False, str(e)

# ─────────────────────────────────────────────
# NITRO SNIPER
# ─────────────────────────────────────────────

GIFT_RE = re.compile(r"(discord\.gift|discord\.com/gifts)/([a-zA-Z0-9]+)")

async def snipe_nitro(code, channel_id):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://discord.com/api/v9/entitlements/gift-codes/{code}/redeem",
                headers={"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT},
                json={"channel_id": str(channel_id)}) as r:
                log_msg("SNIPER", f"{'✓ SNIPED' if r.status == 200 else '✗ miss'} {code} [{r.status}]")
    except Exception as e:
        log_msg("SNIPER", f"error: {e}")

# ─────────────────────────────────────────────
# SPAM WORKER (cancellable)
# ─────────────────────────────────────────────

async def _spam_worker(channel, count: int, text: str):
    try:
        for _ in range(count):
            await channel.send(text)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[spam] {e}")
    finally:
        _spam_tasks.pop(channel.id, None)

# ─────────────────────────────────────────────
# LAST.FM
# ─────────────────────────────────────────────

LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"
_lfm: dict = {}

def _load_lfm():
    global _lfm
    _lfm = load_config().get("lastfm", {})

def _save_lfm():
    cfg = load_config(); cfg["lastfm"] = _lfm; save_config(cfg)

_load_lfm()

async def lfm_get(method, params):
    p = {"method": method, "api_key": _lfm.get("api_key",""), "format": "json", **params}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(LASTFM_BASE, params=p, timeout=aiohttp.ClientTimeout(total=8)) as r:
                return await r.json() if r.status == 200 else {}
    except Exception: return {}

_PERIOD = {"w":"7day","week":"7day","m":"1month","month":"1month",
           "3m":"3month","6m":"6month","y":"12month","year":"12month",
           "all":"overall","overall":"overall"}
_PLABEL = {"7day":"this week","1month":"this month","3month":"3 months",
           "6month":"6 months","12month":"this year","overall":"all time"}

async def lfm_np(username):
    d = await lfm_get("user.getRecentTracks", {"user": username, "limit": 1, "extended": 1})
    tracks = d.get("recenttracks", {}).get("track", [])
    if not tracks: return None
    t = tracks[0] if isinstance(tracks, list) else tracks
    artist = t.get("artist", {})
    return {
        "title":   t.get("name", "?"),
        "artist":  artist.get("name","?") if isinstance(artist, dict) else str(artist),
        "album":   (t.get("album",{}) or {}).get("#text",""),
        "playing": t.get("@attr",{}).get("nowplaying") == "true",
        "loved":   t.get("loved","0") == "1",
        "total":   d.get("recenttracks",{}).get("@attr",{}).get("total","?"),
    }

async def lfm_autorpc_loop():
    last = ""
    while _autorpc_enabled:
        try:
            u = _lfm.get("username","")
            if u:
                t = await lfm_np(u)
                if t and t["playing"]:
                    key = f"{t['title']}|{t['artist']}"
                    if key != last:
                        last = key
                        await apply_brand_rpc("spotify", [f"{t['title']} | {t['artist']} | 210"])
        except Exception as e:
            print(f"[LFMRPC] {e}")
        await asyncio.sleep(30)

# ─────────────────────────────────────────────
# HOSTED ACCOUNT HELPERS
# ─────────────────────────────────────────────

async def hosted_send(token, channel_id, content):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"https://discord.com/api/v9/channels/{channel_id}/messages",
                headers={"Authorization": token.strip(), "Content-Type": "application/json", "User-Agent": USER_AGENT},
                json={"content": content}) as r:
                return r.status in (200, 201)
    except Exception: return False

async def hosted_username(token):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://discord.com/api/v9/users/@me",
                headers={"Authorization": token.strip(), "User-Agent": USER_AGENT}) as r:
                if r.status == 200:
                    d = await r.json()
                    return d.get("username","?")
    except Exception: pass
    return "?"

# ─────────────────────────────────────────────
# UTILITY HELPERS
# ─────────────────────────────────────────────

def uwuify(text):
    text = re.sub(r'[rRlL]', 'w', text)
    text = re.sub(r'n([aeiou])', r'ny\1', text)
    text = re.sub(r'N([aeiou])', r'Ny\1', text)
    faces = [" >w<", " uwu", " owo", " >.<", " ^w^"]
    for p in ".!?":
        text = text.replace(p, p + random.choice(faces))
    return text

def owoify(text):
    subs = [("r","w"),("l","w"),("R","W"),("L","W"),("n","ny"),("N","NY")]
    for a, b in subs:
        text = text.replace(a, b)
    return f"owo {text} owo"

def mock_text(text):
    return "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(text))

def aesthetic(text):
    normal = "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    wide   = "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ　ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ０１２３４５６７８９"
    table = str.maketrans(normal, wide)
    return text.translate(table)

def clap_text(text):
    return " 👏 ".join(text.split())

NEKO_ACTIONS = {
    "feed": "feed", "tickle": "tickle", "slap": "slap", "hug": "hug",
    "cuddle": "cuddle", "pat": "pat", "kiss": "kiss", "poke": "poke",
    "wink": "wink", "smug": "smug", "boop": "boop", "nom": "nom",
    "wave": "wave", "highfive": "highfive", "bite": "bite", "blush": "blush",
    "dance": "dance", "happy": "happy", "cringe": "cringe",
}

async def neko_gif(action: str) -> str | None:
    endpoint = NEKO_ACTIONS.get(action, action)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://nekos.life/api/v2/img/{endpoint}",
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    d = await r.json()
                    return d.get("url")
    except Exception as e:
        print(f"[Neko] {e}")
    return None

async def translate_text(text: str, target_lang: str) -> str:
    try:
        url = f"https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "auto", "tl": target_lang, "dt": "t", "q": text}
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as r:
                d = await r.json()
                return "".join(p[0] for p in d[0] if p[0])
    except Exception as e:
        return f"error: {e}"

async def vsniper_loop():
    while _vsniper_task and not _vsniper_task.cancelled():
        for entry in list(_vsniper_list):
            code = entry["code"]; guild_id = entry["guild_id"]
            try:
                async with aiohttp.ClientSession() as s:
                    h = {"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT}
                    async with s.get(f"https://discord.com/api/v9/invites/{code}", headers=h) as r:
                        if r.status == 404:
                            async with s.patch(
                                f"https://discord.com/api/v9/guilds/{guild_id}/vanity-url",
                                headers=h, json={"code": code}) as r2:
                                if r2.status in (200, 204):
                                    log_msg("VSNIPER", f"CLAIMED {code} for guild {guild_id}")
            except Exception: pass
        await asyncio.sleep(0.5)

async def typing_loop(channel):
    while True:
        try:
            async with channel.typing():
                await asyncio.sleep(9)
        except Exception:
            await asyncio.sleep(5)

# ─────────────────────────────────────────────
# PLATFORM SPOOFER
# ─────────────────────────────────────────────

PLATFORM_MAP = {
    "phone": {"os": "iOS", "browser": "Discord iOS"},
    "android": {"os": "Android", "browser": "Discord Android"},
    "desktop": {"os": "Windows", "browser": "Discord Client"},
    "web": {"os": "Windows", "browser": "Chrome"},
    "console": {"os": "PlayStation 4", "browser": "Discord Embedded"},
    "xbox": {"os": "Xbox One", "browser": "Discord Embedded"},
    "playstation": {"os": "PlayStation 4", "browser": "Discord Embedded"},
    "vr": {"os": "Windows", "browser": "Discord Embedded"},
}
_current_platform = "desktop"

# ─────────────────────────────────────────────
# HYPESQUAD
# ─────────────────────────────────────────────

HOUSE_IDS = {"bravery": 1, "brilliance": 2, "balance": 3}
HOUSE_NAMES = {1: "Bravery", 2: "Brilliance", 3: "Balance"}

async def set_hypesquad(house_id):
    async with aiohttp.ClientSession() as s:
        async with s.post("https://discord.com/api/v9/hypesquad/online",
            headers={"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT},
            json={"house_id": house_id}) as r:
            return r.status in (200, 201, 204), await r.text()

async def clear_hypesquad():
    async with aiohttp.ClientSession() as s:
        async with s.delete("https://discord.com/api/v9/hypesquad/online",
            headers={"Authorization": TOKEN, "User-Agent": USER_AGENT}) as r:
            return r.status in (200, 201, 204)

# ─────────────────────────────────────────────
# EVENTS
# ─────────────────────────────────────────────

@client.event
async def on_ready():
    global _autoreact_emoji, _autoaddback
    print(f"[+] {client.user} ({client.user.id}) | prefix: {PREFIX} | servers: {len(client.guilds)}")
    await update_rpc()
    cfg = load_config()
    if cfg.get("autoquest_enabled"):
        asyncio.create_task(autoquest_run(TOKEN))
    if cfg.get("autoaddback"):
        _autoaddback = True

@client.event
async def on_message(message):
    global PREFIX, _cfg
    global SNIPER_ENABLED, LOGGER_ENABLED, _afk_enabled, _afk_msg
    global _autoreact_emoji, _autoaddback, _current_platform
    global _autoclaim_enabled, _captcha_key, _speak_lang
    global _giveaway_enabled, _nitrosniper_enabled
    global _autorpc_enabled, _autorpc_task, _vsniper_task
    global _multireact_enabled, _multireact_pool

    if LOGGER_ENABLED and message.guild:
        try:
            log_msg("MSG", f"{message.guild.name}/#{message.channel.name} | {message.author}: {message.content[:100]}")
        except Exception: pass

    if _nitrosniper_enabled and message.author.id != client.user.id:
        for _, code in GIFT_RE.findall(message.content):
            asyncio.create_task(snipe_nitro(code, message.channel.id))

    if _giveaway_enabled and message.author.id != client.user.id:
        if message.components and "🎉" in message.content:
            for row in message.components:
                for btn in getattr(row, "children", []):
                    if "Enter" in getattr(btn, "label", "") or "🎉" in getattr(btn, "label", ""):
                        try: await btn.click()
                        except Exception: pass

    if _afk_enabled and client.user in message.mentions and message.author.id != client.user.id:
        try: await message.reply(_afk_msg or "I'm AFK right now.", mention_author=False)
        except Exception: pass

    if message.author.id != client.user.id:
        cl = message.content.lower()
        for trig, resp in AUTO_RESPONSES.items():
            if trig.lower() in cl:
                try: await message.channel.send(resp)
                except Exception: pass
                break

    if message.author.id != client.user.id:
        cid = message.channel.id
        if cid in _mimic_dict and message.author.id in _mimic_dict[cid]:
            if not message.content.startswith(PREFIX):
                try: await message.channel.send(message.content)
                except Exception: pass

    if message.author.id in _tracked_users and message.author.id != client.user.id:
        if message.author.id not in _tracking:
            _tracking[message.author.id] = []
        _tracking[message.author.id].append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "content": message.content,
            "channel": getattr(message.channel, "name", str(message.channel.id)),
        })
        if len(_tracking[message.author.id]) > 200:
            _tracking[message.author.id] = _tracking[message.author.id][-200:]

    if message.author.id == client.user.id and _speak_lang and not message.content.startswith(PREFIX):
        try:
            translated = await translate_text(message.content, _speak_lang)
            if translated and translated != message.content:
                await asyncio.sleep(0.3)
                await message.edit(content=translated)
        except Exception: pass

    if message.author.id == client.user.id and not message.content.startswith(PREFIX):
        if _autoreact_emoji:
            try: await message.add_reaction(_autoreact_emoji)
            except Exception: pass
        if _multireact_enabled and _multireact_pool:
            for emoji in _multireact_pool:
                try:
                    await message.add_reaction(emoji)
                except Exception: pass
                await asyncio.sleep(0.15)

    if message.author.id != client.user.id:
        return
    if not message.content.startswith(PREFIX):
        return

    raw  = message.content[len(PREFIX):]
    args = raw.split()
    cmd  = args[0].lower() if args else ""

    # ─────────────────────────────────
    # HELP
    # ─────────────────────────────────

    if cmd in ("help", "h"):
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub.isdigit():
            page = int(sub)
            await message.channel.send(build_help_root(page))
            return
        if sub:
            page = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1
            await message.channel.send(build_help_section(sub, page))
            return
        await message.channel.send(build_help_root(1))

    # ─────────────────────────────────
    # SETTINGS
    # ─────────────────────────────────

    elif cmd == "prefix":
        if len(args) < 2:
            return await message.edit(content=ui_info(f"current prefix: {PREFIX}"))
        PREFIX = args[1]
        cfg = load_config(); cfg["prefix"] = PREFIX; save_config(cfg)
        await message.edit(content=ui_ok(f"prefix changed to `{PREFIX}`"))

    elif cmd == "version":
        await message.edit(content=ui_info(f"sy's selfbot v{VERSION}"))

    elif cmd == "reload":
        _cfg = load_config()
        await message.edit(content=ui_ok("config reloaded"))

    # ─────────────────────────────────
    # GENERAL
    # ─────────────────────────────────

    elif cmd == "ping":
        await message.edit(content=ui_ok(f"pong — `{round(client.latency * 1000)}ms`"))

    elif cmd == "info":
        u = client.user
        await message.edit(content=ui_box("account", [
            f"  {DIM}user{RESET}     {WHITE}{u}{RESET}",
            f"  {DIM}id{RESET}       {u.id}",
            f"  {DIM}created{RESET}  {u.created_at.strftime('%Y-%m-%d')}",
            f"  {DIM}servers{RESET}  {len(client.guilds)}",
            f"  {DIM}prefix{RESET}   {PREFIX}",
            f"  {DIM}platform{RESET} {_current_platform}",
        ]))

    elif cmd == "say":
        await message.edit(content=" ".join(args[1:]))

    elif cmd == "spam":
        if len(args) < 3:
            return await message.edit(content=ui_err("usage: spam <n> <text>"))
        try: count = int(args[1])
        except ValueError:
            return await message.edit(content=ui_err("n must be a number"))
        count = min(count, 200)
        text = " ".join(args[2:])
        cid = message.channel.id
        existing = _spam_tasks.get(cid)
        if existing and not existing.done():
            existing.cancel()
            try: await existing
            except Exception: pass
        try: await message.delete()
        except Exception: pass
        _spam_tasks[cid] = asyncio.create_task(_spam_worker(message.channel, count, text))

    elif cmd == "spamstop":
        cid = message.channel.id
        task = _spam_tasks.get(cid)
        if not task or task.done():
            killed = 0
            for ch_id, t in list(_spam_tasks.items()):
                if t and not t.done():
                    t.cancel()
                    killed += 1
            _spam_tasks.clear()
            if killed:
                await message.edit(content=ui_ok(f"spam stopped in {killed} channel(s)"))
            else:
                await message.edit(content=ui_info("no active spam to stop"))
            return
        task.cancel()
        try: await task
        except Exception: pass
        _spam_tasks.pop(cid, None)
        await message.edit(content=ui_ok("spam stopped"))

    elif cmd == "purge":
        limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
        try: await message.delete()
        except Exception: pass
        deleted = 0
        async for msg in message.channel.history(limit=500):
            if msg.author.id == client.user.id:
                try: await msg.delete()
                except Exception: pass
                deleted += 1
                await asyncio.sleep(0.3)
                if deleted >= limit: break

    elif cmd == "purgeall":
        try: await message.delete()
        except Exception: pass
        async for msg in message.channel.history(limit=1000):
            if msg.author.id == client.user.id:
                try: await msg.delete()
                except Exception: pass
                await asyncio.sleep(0.3)

    elif cmd == "clear":
        try: await message.delete()
        except Exception: pass

    elif cmd == "snipe":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        cid = message.channel.id
        if sub == "clear":
            _snipe_cache.pop(cid, None)
            return await message.channel.send(ui_ok("snipe cache cleared"), delete_after=4)
        entries = _snipe_cache.get(cid, [])
        if not entries:
            return await message.channel.send(ui_info("nothing to snipe in this channel"), delete_after=5)
        try:
            idx = int(sub) if sub else 1
        except ValueError:
            idx = 1
        if idx < 1 or idx > len(entries):
            return await message.channel.send(ui_err(f"index out of range (1–{len(entries)})"), delete_after=5)
        e = entries[-idx]
        atts = "\n".join(e.get("attachments", [])) or "none"
        rows = [
            f"  {DIM}author{RESET}      {WHITE}{e['author']}{RESET}  {DIM}({e['author_id']}){RESET}",
            f"  {DIM}deleted{RESET}     {e['time']}",
            f"  {DIM}attachments{RESET} {atts}",
            "",
            f"  {WHITE}{e['content'] or '(no content)'}{RESET}",
        ]
        await message.channel.send(ui_box(f"sniped message  #{idx}/{len(entries)}", rows))

    elif cmd in ("editsnipe", "esnipe"):
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        cid = message.channel.id
        if sub == "clear":
            _editsnipe_cache.pop(cid, None)
            return await message.channel.send(ui_ok("editsnipe cache cleared"), delete_after=4)
        entries = _editsnipe_cache.get(cid, [])
        if not entries:
            return await message.channel.send(ui_info("no edits to snipe in this channel"), delete_after=5)
        try:
            idx = int(sub) if sub else 1
        except ValueError:
            idx = 1
        if idx < 1 or idx > len(entries):
            return await message.channel.send(ui_err(f"index out of range (1–{len(entries)})"), delete_after=5)
        e = entries[-idx]
        rows = [
            f"  {DIM}author{RESET}  {WHITE}{e['author']}{RESET}  {DIM}({e['author_id']}){RESET}",
            f"  {DIM}edited{RESET}  {e['time']}",
            "",
            f"  {DIM}before:{RESET}",
            f"  {WHITE}{e['before'] or '(empty)'}{RESET}",
            "",
            f"  {DIM}after:{RESET}",
            f"  {WHITE}{e['after'] or '(empty)'}{RESET}",
        ]
        await message.channel.send(ui_box(f"sniped edit  #{idx}/{len(entries)}", rows))

    elif cmd == "copycat":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: copycat <user_id>"))
        try: uid = int(args[1])
        except ValueError:
            return await message.edit(content=ui_err("invalid user id"))
        try: await message.delete()
        except Exception: pass
        def check(m): return m.author.id == uid and m.channel.id == message.channel.id
        for _ in range(10):
            try:
                m = await client.wait_for("message", check=check, timeout=60)
                await message.channel.send(m.content)
            except asyncio.TimeoutError: break

    elif cmd == "status":
        if len(args) < 2 or args[1].lower() == "clear":
            await client.change_presence(activity=None)
            await message.edit(content=ui_ok("status cleared"))
        else:
            text = " ".join(args[1:])
            await client.change_presence(activity=discord.CustomActivity(name=text))
            await message.edit(content=ui_ok(f"status set: {text}"))

    elif cmd == "platform":
        if len(args) < 2:
            return await message.edit(content=ui_info(f"platform: {_current_platform}\ntypes: {' '.join(PLATFORM_MAP)}"))
        plat = "desktop" if args[1].lower() == "off" else args[1].lower()
        if plat not in PLATFORM_MAP:
            return await message.edit(content=ui_err(f"unknown platform: {plat}"))
        _current_platform = plat
        await message.edit(content=ui_ok(f"platform → {plat}  (reconnect required)"))
        try: await client.ws.close(code=4000)
        except Exception: pass

    elif cmd == "hypesquad":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: hypesquad bravery/brilliance/balance/off"))
        sub = args[1].lower()
        if sub == "off":
            ok = await clear_hypesquad()
            await message.edit(content=ui_ok("hypesquad removed") if ok else ui_err("failed"))
        elif sub in HOUSE_IDS:
            ok, _ = await set_hypesquad(HOUSE_IDS[sub])
            await message.edit(content=ui_ok(f"house {HOUSE_NAMES[HOUSE_IDS[sub]]}") if ok else ui_err("failed"))
        else:
            await message.edit(content=ui_err("unknown house"))

    # ─────────────────────────────────
    # SNIPER / LOGGER
    # ─────────────────────────────────

    elif cmd == "sniper":
        SNIPER_ENABLED = len(args) < 2 or args[1].lower() == "on"
        await message.edit(content=ui_ok(f"sniper → {'ON' if SNIPER_ENABLED else 'OFF'}"))

    elif cmd == "logger":
        LOGGER_ENABLED = len(args) < 2 or args[1].lower() == "on"
        await message.edit(content=ui_ok(f"logger → {'ON' if LOGGER_ENABLED else 'OFF'}"))

    elif cmd == "readlog":
        n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
        if not os.path.exists(LOG_FILE):
            return await message.edit(content=ui_err("no log file yet"))
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        tail = "".join(lines[-n:])
        if len(tail) > 1900: tail = tail[-1900:]
        await message.edit(content=f"```\n{tail}\n```")

    elif cmd == "logs":
        n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 20
        if not os.path.exists(LOG_FILE):
            return await message.edit(content=ui_err("no log yet"))
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        tail = "".join(lines[-n:])
        if len(tail) > 1900: tail = tail[-1900:]
        await message.edit(content=f"```\n{tail}\n```")

    # ─────────────────────────────────
    # AUTO-RESPONDER
    # ─────────────────────────────────

    elif cmd == "ar":
        sub = args[1].lower() if len(args) > 1 else ""
        rest = " ".join(args[2:])
        if sub == "add":
            if "|" not in rest:
                return await message.edit(content=ui_err("format: ar add trigger | response"))
            trig, resp = rest.split("|", 1)
            AUTO_RESPONSES[trig.strip()] = resp.strip()
            await message.edit(content=ui_ok(f"added: `{trig.strip()}`"))
        elif sub == "remove":
            AUTO_RESPONSES.pop(rest.strip(), None)
            await message.edit(content=ui_ok(f"removed: `{rest.strip()}`"))
        elif sub == "list":
            if not AUTO_RESPONSES:
                return await message.edit(content=ui_info("no auto-responses set"))
            rows = [f"  {GREY}├{RESET} {k}  {DIM}→ {v}{RESET}" for k, v in list(AUTO_RESPONSES.items())]
            await message.edit(content=_paginate("ar", "auto-responder", rows))
        else:
            await message.edit(content=ui_err("usage: ar add/remove/list"))

    # ─────────────────────────────────
    # QUESTS
    # ─────────────────────────────────

    elif cmd == "quest":
        try: await message.delete()
        except Exception: pass
        svc = QuestService(TOKEN)
        async with aiohttp.ClientSession() as session:
            quests = await svc.fetch(session)
        if not quests:
            return await message.channel.send(ui_err("no quests found"), delete_after=8)
        rows = []
        for i, q in enumerate(quests):
            tag = f"{GREEN}done{RESET}" if q.is_completed() else (f"{CYAN}ok{RESET}" if q.is_supported() else f"{RED}unsupported{RESET}")
            rows.append(f"  {GREY}[{i}]{RESET} {WHITE}{q.name}{RESET}  {tag}")
            rows.append(f"       {ui_progress(q.reward, q.progress_pct())}")
        await message.channel.send(_paginate("quests", "active quests", rows))

    elif cmd == "questrun":
        try: await message.delete()
        except Exception: pass
        idx = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
        svc = QuestService(TOKEN)
        async with aiohttp.ClientSession() as session:
            quests = await svc.fetch(session)
            if not quests or idx >= len(quests):
                return await message.channel.send(ui_err("index out of range"), delete_after=6)
            q = quests[idx]
            if q.is_completed():
                return await message.channel.send(ui_ok(f"{q.name} already done"), delete_after=6)
            await message.channel.send(ui_info(f"quest completer started\n  {q.name}"), delete_after=5)
            res = await svc.run(session, q)
            if res == "completed":
                await message.channel.send(ui_ok(f"{q.name} complete — {q.reward}"), delete_after=10)

    elif cmd == "questall":
        try: await message.delete()
        except Exception: pass
        svc = QuestService(TOKEN)
        async with aiohttp.ClientSession() as session:
            quests = await svc.fetch(session)
            active = [q for q in quests if not q.is_completed() and q.is_supported()]
            if not active:
                return await message.channel.send(ui_err("no active quests"), delete_after=6)
            for q in active:
                if not q.is_enrolled():
                    try: await svc.enroll(session, q)
                    except Exception: pass
            await message.channel.send(ui_info(f"quest completer started\n  {len(active)} quest(s) queued"), delete_after=5)
            async def _run(q):
                res = await svc.run(session, q)
                if res == "completed":
                    await message.channel.send(ui_ok(f"{q.name} — {q.reward}"), delete_after=10)
            await asyncio.gather(*[_run(q) for q in active])

    elif cmd == "autoquest":
        try: await message.delete()
        except Exception: pass
        cfg = load_config()
        on = len(args) < 2 or args[1].lower() in ("on", "enable")
        cfg["autoquest_enabled"] = on
        save_config(cfg)
        if on: asyncio.create_task(autoquest_run(TOKEN))
        await message.channel.send(ui_ok(f"autoquest → {'on' if on else 'off'}"), delete_after=5)

    elif cmd == "autoclaim":
        _autoclaim_enabled = len(args) < 2 or args[1].lower() in ("on", "enable")
        await message.edit(content=ui_ok(f"autoclaim → {'on' if _autoclaim_enabled else 'off'}"))

    elif cmd == "captcha":
        if len(args) < 3 or args[1].lower() != "set":
            return await message.edit(content=ui_err("usage: captcha set <2captcha_api_key>"))
        _captcha_key = args[2].strip()
        cfg = load_config(); cfg["captcha_key"] = _captcha_key; save_config(cfg)
        await message.edit(content=ui_ok("2captcha key saved"))

    elif cmd == "orbbadge":
        try: await message.delete()
        except Exception: pass
        ok, text = await claim_orb(TOKEN)
        await message.channel.send(
            ui_ok("orb badge claimed!") if ok else ui_err(f"failed: {text[:80]}"),
            delete_after=8)

    # ─────────────────────────────────
    # RPC
    # ─────────────────────────────────

    elif cmd == "rpc":
        sub = args[1].lower() if len(args) > 1 else ""
        BRANDS = ("spotify","youtube","xbox","playstation","crunchyroll","roblox","custom")

        if not sub or sub == "help":
            try: await message.delete()
            except Exception: pass
            await message.channel.send(build_help_section("rpc"))

        elif sub in BRANDS:
            ok = await apply_brand_rpc(sub, args[2:])
            await message.edit(content=ui_ok(f"rpc → {sub}") if ok else ui_err("rpc failed"))

        elif sub in ("disable","stop","clear","off"):
            cfg = load_rpc_cfg(); cfg["enabled"] = False; save_rpc_cfg(cfg)
            await client.change_presence(activity=None)
            await message.edit(content=ui_ok("rpc cleared"))

        elif sub == "enable":
            cfg = load_rpc_cfg(); cfg["enabled"] = True; save_rpc_cfg(cfg)
            await update_rpc()
            await message.edit(content=ui_ok("rpc enabled"))

        elif sub == "status":
            cfg = load_rpc_cfg()
            await message.edit(content=ui_box("rpc", [
                f"  {DIM}enabled{RESET}        {'yes' if cfg.get('enabled') else 'no'}",
                f"  {DIM}application_id{RESET} {cfg.get('application_id') or '-'}",
                f"  {DIM}type{RESET}           {cfg.get('type')}",
                f"  {DIM}name{RESET}           {cfg.get('name') or '-'}",
                f"  {DIM}details{RESET}        {cfg.get('details') or '-'}",
                f"  {DIM}state{RESET}          {cfg.get('state') or '-'}",
                f"  {DIM}large_image{RESET}    {cfg.get('large_image') or '-'}",
                f"  {DIM}small_image{RESET}    {cfg.get('small_image') or '-'}",
            ]))

        elif sub == "application_id":
            try: await message.delete()
            except Exception: pass
            val = await rpc_prompt(message.channel, message.author, "application_id (numeric)")
            if val == "__TIMEOUT__": return
            cfg = load_rpc_cfg(); cfg["application_id"] = val or DEFAULT_APP_ID; save_rpc_cfg(cfg)
            _rpc_asset_cache.clear()
            await update_rpc()
            await message.channel.send(ui_ok(f"application_id → {cfg['application_id']}"), delete_after=4)

        elif sub == "type":
            try: await message.delete()
            except Exception: pass
            val = await rpc_prompt(message.channel, message.author, "type (playing/streaming/watching/listening/competing)")
            if val == "__TIMEOUT__": return
            cfg = load_rpc_cfg(); cfg["type"] = val; save_rpc_cfg(cfg); await update_rpc()
            await message.channel.send(ui_ok(f"type → {val}"), delete_after=4)

        elif sub in ("name","details","state","url","large_image","large_text","small_image","small_text"):
            try: await message.delete()
            except Exception: pass
            val = await rpc_prompt(message.channel, message.author, sub)
            if val == "__TIMEOUT__": return
            cfg = load_rpc_cfg(); cfg[sub] = val; save_rpc_cfg(cfg); await update_rpc()
            await message.channel.send(ui_ok(f"{sub} set"), delete_after=4)

        elif sub in ("start","end"):
            try: await message.delete()
            except Exception: pass
            key = "start_timestamp" if sub == "start" else "end_timestamp"
            val = await rpc_prompt(message.channel, message.author, f"{sub} timestamp (unix / none)")
            if val == "__TIMEOUT__": return
            cfg = load_rpc_cfg()
            cfg[key] = None if (not val or val.lower() == "none") else val
            save_rpc_cfg(cfg); await update_rpc()
            await message.channel.send(ui_ok(f"{sub} timestamp set"), delete_after=4)

        elif sub in ("button1_name","button1_url","button2_name","button2_url"):
            try: await message.delete()
            except Exception: pass
            idx = 0 if "1" in sub else 1
            field = "label" if "name" in sub else "url"
            val = await rpc_prompt(message.channel, message.author, f"button {idx+1} {field}")
            if val == "__TIMEOUT__": return
            cfg = load_rpc_cfg(); cfg["buttons"][idx][field] = val or ""; save_rpc_cfg(cfg); await update_rpc()
            await message.channel.send(ui_ok(f"button {idx+1} {field} set"), delete_after=4)

        elif sub == "party":
            opt = args[2].lower() if len(args) > 2 else ""
            cfg = load_rpc_cfg()
            if opt == "enable":
                cfg["party"]["enabled"] = True; save_rpc_cfg(cfg); await update_rpc()
                await message.edit(content=ui_ok("party enabled"))
            elif opt == "disable":
                cfg["party"]["enabled"] = False; save_rpc_cfg(cfg); await update_rpc()
                await message.edit(content=ui_ok("party disabled"))
            elif opt in ("current","max"):
                try:
                    cfg["party"][opt] = int(args[3]); save_rpc_cfg(cfg); await update_rpc()
                    await message.edit(content=ui_ok(f"party {opt} → {args[3]}"))
                except (IndexError, ValueError):
                    await message.edit(content=ui_err("usage: rpc party current/max <n>"))
        else:
            await message.edit(content=ui_err(f"unknown rpc subcommand: {sub}"))

    # ─────────────────────────────────
    # VOICE
    # ─────────────────────────────────

    elif cmd == "vcjoin":
        try: await message.delete()
        except Exception: pass
        if len(args) < 2:
            return await message.channel.send(ui_err("usage: vcjoin <channel_id>"), delete_after=5)
        try:
            ch = client.get_channel(int(args[1]))
            if not ch:
                return await message.channel.send(ui_err("channel not found"), delete_after=5)
            if message.guild and message.guild.voice_client:
                await message.guild.voice_client.disconnect(force=True)
            await ch.connect(self_deaf=True)
            await message.channel.send(ui_ok(f"joined {ch.name}"), delete_after=5)
        except Exception as e:
            await message.channel.send(ui_err(str(e)), delete_after=5)

    elif cmd == "vcleave":
        try: await message.delete()
        except Exception: pass
        if message.guild and message.guild.voice_client:
            name = message.guild.voice_client.channel.name
            await message.guild.voice_client.disconnect(force=True)
            await message.channel.send(ui_ok(f"left {name}"), delete_after=5)
        else:
            await message.channel.send(ui_err("not in a vc"), delete_after=5)

    elif cmd in ("vcmute","vcunmute","vcdeafen","vcundeafen","vckick"):
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 2:
            return await message.channel.send(ui_err(f"usage: {cmd} <user_id>"), delete_after=5)
        try:
            member = message.guild.get_member(int(args[1]))
            if not member or not member.voice:
                return await message.channel.send(ui_err("user not in vc"), delete_after=5)
            if cmd == "vcmute":    await member.edit(mute=True)
            elif cmd == "vcunmute":  await member.edit(mute=False)
            elif cmd == "vcdeafen":  await member.edit(deafen=True)
            elif cmd == "vcundeafen":await member.edit(deafen=False)
            elif cmd == "vckick":    await member.move_to(None)
            await message.channel.send(ui_ok(f"{cmd} → {member.name}"), delete_after=5)
        except Exception as e:
            await message.channel.send(ui_err(str(e)), delete_after=5)

    elif cmd == "vcmove":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 3:
            return await message.channel.send(ui_err("usage: vcmove <user_id> <ch_id>"), delete_after=5)
        try:
            member = message.guild.get_member(int(args[1]))
            ch = client.get_channel(int(args[2]))
            await member.move_to(ch)
            await message.channel.send(ui_ok(f"moved {member.name} → {ch.name}"), delete_after=5)
        except Exception as e:
            await message.channel.send(ui_err(str(e)), delete_after=5)

    elif cmd == "vcmoveall":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 3:
            return await message.channel.send(ui_err("usage: vcmoveall <ch1_id> <ch2_id>"), delete_after=5)
        try:
            ch1 = client.get_channel(int(args[1]))
            ch2 = client.get_channel(int(args[2]))
            for m in list(ch1.members): await m.move_to(ch2); await asyncio.sleep(0.2)
            await message.channel.send(ui_ok(f"moved all from {ch1.name} → {ch2.name}"), delete_after=5)
        except Exception as e:
            await message.channel.send(ui_err(str(e)), delete_after=5)

    # ─────────────────────────────────
    # FUN
    # ─────────────────────────────────

    elif cmd == "gayrate":
        try: await message.delete()
        except Exception: pass
        uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
        pct = 0 if uid == message.author.id else random.randint(0, 100)
        await message.channel.send(f"🏳️‍🌈 <@{uid}> is **{pct}%** gay")

    elif cmd in NEKO_ACTIONS:
        try: await message.delete()
        except Exception: pass
        url = await neko_gif(cmd)
        if url:
            uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
            mention = f" <@{uid}>" if uid else ""
            await message.channel.send(f"{cmd}{mention}\n{url}")
        else:
            await message.channel.send(ui_err("could not fetch image"), delete_after=5)

    elif cmd == "meme":
        try: await message.delete()
        except Exception: pass
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://meme-api.com/gimme") as r:
                    if r.status == 200:
                        d = await r.json()
                        await message.channel.send(d.get("url", "no meme"))
        except Exception as e:
            await message.channel.send(ui_err(str(e)), delete_after=5)

    elif cmd == "joke":
        try: await message.delete()
        except Exception: pass
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://official-joke-api.appspot.com/random_joke") as r:
                    if r.status == 200:
                        j = await r.json()
                        setup = j["setup"]; punch = j["punchline"]
                        await message.channel.send(f"**{setup}**\n||{punch}||")
        except Exception as e:
            await message.channel.send(ui_err(str(e)), delete_after=5)

    elif cmd == "mimic":
        try: await message.delete()
        except Exception: pass
        if len(args) < 2:
            return await message.channel.send(ui_err("usage: mimic <user_id>"), delete_after=5)
        uid = int(args[1])
        cid = message.channel.id
        if cid not in _mimic_dict: _mimic_dict[cid] = []
        if uid not in _mimic_dict[cid]: _mimic_dict[cid].append(uid)
        await message.channel.send(ui_ok(f"mimicking <@{uid}>"), delete_after=5)

    elif cmd == "unmimic":
        try: await message.delete()
        except Exception: pass
        if len(args) < 2:
            return await message.channel.send(ui_err("usage: unmimic <user_id>"), delete_after=5)
        uid = int(args[1]); cid = message.channel.id
        if cid in _mimic_dict and uid in _mimic_dict[cid]:
            _mimic_dict[cid].remove(uid)
            if not _mimic_dict[cid]: del _mimic_dict[cid]
        await message.channel.send(ui_ok("stopped mimic"), delete_after=5)

    elif cmd == "stopmimic":
        try: await message.delete()
        except Exception: pass
        _mimic_dict.clear()
        await message.channel.send(ui_ok("all mimics stopped"), delete_after=5)

    # ─────────────────────────────────
    # TOOLS
    # ─────────────────────────────────

    elif cmd == "nitro":
        try: await message.delete()
        except Exception: pass
        code = "".join(random.choices(string.ascii_letters + string.digits, k=16))
        await message.channel.send(f"```\nhttps://discord.gift/{code}\n```")

    elif cmd == "applybypass":
        try: await message.delete()
        except Exception: pass
        if len(args) < 2:
            return await message.channel.send(ui_err("usage: applybypass <invite>"), delete_after=5)
        invite = args[1].replace("https://discord.gg/","").replace("discord.gg/","")
        h = {"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://discord.com/api/v9/invites/{invite}", headers=h) as r:
                    if r.status != 200:
                        return await message.channel.send(ui_err("invalid invite"), delete_after=6)
                    inv = await r.json()
                guild_id = inv.get("guild", {}).get("id")
                async with s.post(f"https://discord.com/api/v9/invites/{invite}",
                    headers=h, json={"session_id": str(uuid4())[:8]}) as r2:
                    if r2.status in (200, 204):
                        await message.channel.send(ui_ok(f"joined {inv.get('guild',{}).get('name','server')}"), delete_after=8)
                    elif r2.status == 403 and guild_id:
                        async with s.put(f"https://discord.com/api/v9/guilds/{guild_id}/requests/@me",
                            headers=h, json={"form_fields": []}) as r3:
                            await message.channel.send(
                                ui_ok("application submitted") if r3.status in (200,201,204)
                                else ui_err(f"bypass failed {r3.status}"), delete_after=8)
                    else:
                        await message.channel.send(ui_err(f"failed {r2.status}"), delete_after=6)
        except Exception as e:
            await message.channel.send(ui_err(str(e)), delete_after=6)

    elif cmd == "tokeninfo":
        token = args[1] if len(args) > 1 else ""
        if not token:
            return await message.edit(content=ui_err("usage: tokeninfo <token>"))
        try:
            parts = token.split(".")
            uid_b64 = parts[0]
            uid = base64.b64decode(uid_b64 + "=" * (-len(uid_b64) % 4)).decode()
            ts_b64 = parts[1]
            pad = ts_b64 + "=" * (-len(ts_b64) % 4)
            ts_bytes = base64.b64decode(pad)
            epoch = int.from_bytes(ts_bytes[:4], "big")
            created = datetime.utcfromtimestamp(epoch + 1293840000).strftime("%Y-%m-%d %H:%M:%S")
            await message.edit(content=ui_box("token info", [
                f"  {DIM}user_id{RESET}  {uid}",
                f"  {DIM}created{RESET}  {created} UTC",
            ]))
        except Exception as e:
            await message.edit(content=ui_err(f"decode failed: {e}"))

    elif cmd == "calculate":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: calculate <expr>"))
        expr = " ".join(args[1:])
        try:
            result = eval(re.sub(r"[^0-9+\-*/(). ]", "", expr))
            await message.edit(content=ui_ok(f"{expr} = {result}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "fact":
        try: await message.delete()
        except Exception: pass
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://uselessfacts.jsph.pl/api/v2/facts/random?language=en") as r:
                    d = await r.json()
                    await message.channel.send(f"💡 {d.get('text','no fact')}")
        except Exception as e:
            await message.channel.send(ui_err(str(e)), delete_after=5)

    elif cmd == "fetchlyrics":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: fetchlyrics <artist - title>"))
        query = " ".join(args[1:])
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://lyrist.vercel.app/api/{query.replace(' - ','/')}") as r:
                    if r.status == 200:
                        d = await r.json()
                        lyrics = d.get("lyrics","")[:1800]
                        await message.edit(content=f"```\n{lyrics}\n```")
                    else:
                        await message.edit(content=ui_err("lyrics not found"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "robuxtax":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: robuxtax <amount>"))
        try:
            amount = int(args[1])
            after_tax = int(amount * 0.7)
            fee = amount - after_tax
            await message.edit(content=ui_box("roblox marketplace fee", [
                f"  {DIM}listed price{RESET}    {amount:,} R$",
                f"  {DIM}marketplace fee{RESET} {fee:,} R$ (30%)",
                f"  {DIM}you receive{RESET}     {after_tax:,} R$",
            ]))
        except ValueError:
            await message.edit(content=ui_err("invalid amount"))

    elif cmd == "archivechannel":
        try: await message.delete()
        except Exception: pass
        ch = client.get_channel(int(args[1])) if len(args) > 1 and args[1].isdigit() else message.channel
        if not ch:
            return await message.channel.send(ui_err("channel not found"), delete_after=5)
        count = 0
        out = []
        async for msg in ch.history(limit=2000):
            out.append(f"[{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {msg.author}: {msg.content}")
            count += 1
        fname = f"archive_{ch.id}.txt"
        with open(fname, "w", encoding="utf-8") as f:
            f.write("\n".join(reversed(out)))
        await message.channel.send(ui_ok(f"archived {count} messages to {fname}"), delete_after=8)

    # ─────────────────────────────────
    # HOST
    # ─────────────────────────────────

    elif cmd == "host":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add":
            if len(args) < 3:
                return await message.edit(content=ui_err("usage: host add <token>"))
            t = args[2].strip()
            if t in HOSTED_TOKENS:
                return await message.edit(content=ui_err("already in list"))
            HOSTED_TOKENS.append(t)
            save_hosted()
            uname = await hosted_username(t)
            await message.edit(content=ui_ok(f"added {uname}"))

        elif sub == "remove":
            if len(args) < 3:
                return await message.edit(content=ui_err("usage: host remove <token>"))
            t = args[2].strip()
            if t in HOSTED_TOKENS:
                HOSTED_TOKENS.remove(t); save_hosted()
                await message.edit(content=ui_ok("removed"))
            else:
                await message.edit(content=ui_err("not in list"))

        elif sub == "list":
            if not HOSTED_TOKENS:
                return await message.edit(content=ui_err("no hosted accounts"))
            rows = []
            for i, t in enumerate(HOSTED_TOKENS):
                uname = await hosted_username(t)
                rows.append(f"  {GREY}[{i}]{RESET} {WHITE}{uname}{RESET}  {DIM}{t[:12]}...{RESET}")
            await message.edit(content=_paginate("host", "hosted accounts", rows))

        elif sub == "broadcast":
            if len(args) < 3:
                return await message.edit(content=ui_err("usage: host broadcast <msg>"))
            text = " ".join(args[2:])
            ok = 0
            for t in HOSTED_TOKENS:
                if await hosted_send(t, message.channel.id, text): ok += 1
                await asyncio.sleep(0.5)
            await message.edit(content=ui_ok(f"sent from {ok}/{len(HOSTED_TOKENS)} accounts"))

        elif sub == "say":
            if len(args) < 4:
                return await message.edit(content=ui_err("usage: host say <index> <msg>"))
            try:
                idx = int(args[2])
                t = HOSTED_TOKENS[idx]
            except (ValueError, IndexError):
                return await message.edit(content=ui_err("invalid index"))
            text = " ".join(args[3:])
            ok = await hosted_send(t, message.channel.id, text)
            await message.edit(content=ui_ok("sent") if ok else ui_err("failed"))
        else:
            await message.edit(content=build_help_section("host"))

    # ─────────────────────────────────
    # LAST.FM
    # ─────────────────────────────────

    elif cmd == "lastfm":
        sub = args[1].lower() if len(args) > 1 else ""

        if not sub or sub == "help":
            try: await message.delete()
            except Exception: pass
            await message.channel.send(build_help_section("lastfm"))

        elif sub == "set":
            if len(args) < 3:
                return await message.edit(content=ui_err("usage: lastfm set <username> [api_key]"))
            _lfm["username"] = args[2].strip()
            if len(args) > 3: _lfm["api_key"] = args[3].strip()
            _save_lfm()
            await message.edit(content=ui_ok(f"last.fm linked: {_lfm['username']}"))

        elif sub in ("np","nowplaying"):
            try: await message.delete()
            except Exception: pass
            u = _lfm.get("username","")
            if not u:
                return await message.channel.send(ui_err("set username first: lastfm set <user>"), delete_after=8)
            t = await lfm_np(u)
            if not t:
                return await message.channel.send(ui_info("no recent tracks"), delete_after=6)
            loved = "♥ " if t["loved"] else ""
            status = "▶ now playing" if t["playing"] else "⏸ last played"
            rows = [
                f"  {DIM}{status}{RESET}",
                "",
                f"  {WHITE}{loved}{t['title']}{RESET}",
                f"  {DIM}by{RESET} {t['artist']}",
            ]
            if t["album"]: rows.append(f"  {DIM}album{RESET} {t['album']}")
            rows.append(f"  {DIM}scrobbles{RESET} {t['total']}")
            await message.channel.send(_ansi_block(rows))

        elif sub == "recent":
            try: await message.delete()
            except Exception: pass
            u = _lfm.get("username","")
            if not u:
                return await message.channel.send(ui_err("set username first"), delete_after=6)
            n = int(args[2]) if len(args) > 2 and args[2].isdigit() else 5
            d = await lfm_get("user.getRecentTracks", {"user": u, "limit": min(n, 15)})
            tracks = d.get("recenttracks", {}).get("track", [])
            if not tracks:
                return await message.channel.send(ui_info("no recent tracks"), delete_after=6)
            rows = []
            for i, t in enumerate(tracks[:n], 1):
                title = t.get("name","?")
                artist = (t.get("artist",{}) or {}).get("#text","?") if isinstance(t.get("artist"),dict) else "?"
                now = " ▶" if t.get("@attr",{}).get("nowplaying") else ""
                rows.append(f"  {GREY}{i:2}.{RESET} {WHITE}{title}{RESET}  {DIM}— {artist}{now}{RESET}")
            await message.channel.send(_paginate("recent", u, rows))

        elif sub in ("topartists","artists","toptracks","tracks","topalbums","albums"):
            try: await message.delete()
            except Exception: pass
            u = _lfm.get("username","")
            if not u:
                return await message.channel.send(ui_err("set username first"), delete_after=6)
            period_raw = args[2].lower() if len(args) > 2 else "overall"
            period = _PERIOD.get(period_raw, "overall")
            label  = _PLABEL.get(period, "all time")
            method_map = {
                "topartists": ("user.getTopArtists", "topartists", "artist"),
                "artists":    ("user.getTopArtists", "topartists", "artist"),
                "toptracks":  ("user.getTopTracks",  "toptracks",  "track"),
                "tracks":     ("user.getTopTracks",  "toptracks",  "track"),
                "topalbums":  ("user.getTopAlbums",  "topalbums",  "album"),
                "albums":     ("user.getTopAlbums",  "topalbums",  "album"),
            }
            method, key, item_key = method_map[sub]
            d = await lfm_get(method, {"user": u, "period": period, "limit": 10})
            items = d.get(key, {}).get(item_key, [])
            if not items:
                return await message.channel.send(ui_info("no data"), delete_after=6)
            max_plays = int(items[0].get("playcount", 1)) or 1
            rows = []
            for i, item in enumerate(items[:10], 1):
                name   = item.get("name","?")
                plays  = int(item.get("playcount", 0))
                pct    = int(plays / max_plays * 100)
                bar    = f"{GREEN}{'▓' * (pct // 10)}{GREY}{'░' * (10 - pct // 10)}{RESET}"
                extra  = ""
                if "artist" in item and isinstance(item["artist"], dict):
                    extra = f"  {DIM}— {item['artist'].get('name','')}{RESET}"
                rows.append(f"  {GREY}{i:2}.{RESET} {bar} {DIM}{plays:>5}{RESET}  {WHITE}{name}{RESET}{extra}")
            await message.channel.send(_paginate(sub, f"{u} — {label}", rows))

        elif sub == "stats":
            try: await message.delete()
            except Exception: pass
            u = _lfm.get("username","")
            if not u:
                return await message.channel.send(ui_err("set username first"), delete_after=6)
            d = await lfm_get("user.getInfo", {"user": u})
            ud = d.get("user",{})
            if not ud:
                return await message.channel.send(ui_err("user not found"), delete_after=6)
            rows = [
                f"  {WHITE}{u}{RESET}",
                "",
                f"  {DIM}scrobbles{RESET}  {ud.get('playcount','?')}",
                f"  {DIM}artists{RESET}    {ud.get('artist_count','?')}",
                f"  {DIM}albums{RESET}     {ud.get('album_count','?')}",
                f"  {DIM}tracks{RESET}     {ud.get('track_count','?')}",
                f"  {DIM}country{RESET}    {ud.get('country','?')}",
            ]
            await message.channel.send(_ansi_block(rows))

        elif sub == "compare":
            try: await message.delete()
            except Exception: pass
            u = _lfm.get("username","")
            if not u or len(args) < 3:
                return await message.channel.send(ui_err("usage: lastfm compare <other_user>"), delete_after=6)
            other = args[2]
            d = await lfm_get("tasteometer.compare", {"type1":"user","type2":"user","value1":u,"value2":other,"limit":5})
            result = d.get("comparison",{}).get("result",{})
            score = float(result.get("score",0)) * 100
            artists = result.get("artists",{}).get("artist",[])
            if isinstance(artists, dict): artists = [artists]
            filled = int(score / 10)
            bar = f"{GREEN}{'▓'*filled}{GREY}{'░'*(10-filled)}{RESET}"
            rows = [
                f"  {WHITE}{u}{RESET}  {DIM}vs{RESET}  {WHITE}{other}{RESET}",
                f"  {bar}  {DIM}{score:.1f}% compatible{RESET}",
            ]
            if artists:
                rows.append("")
                rows.append(f"  {DIM}shared:{RESET}")
                for a in artists[:5]:
                    rows.append(f"    {GREY}•{RESET} {a.get('name','?') if isinstance(a,dict) else a}")
            await message.channel.send(_ansi_block(rows))

        elif sub == "rpc":
            try: await message.delete()
            except Exception: pass
            u = _lfm.get("username","")
            if not u:
                return await message.channel.send(ui_err("set username first"), delete_after=6)
            t = await lfm_np(u)
            if not t or not t["playing"]:
                return await message.channel.send(ui_info("nothing playing right now"), delete_after=6)
            ok = await apply_brand_rpc("spotify", [f"{t['title']} | {t['artist']} | 210"])
            await message.channel.send(
                ui_ok(f"rpc → {t['title']} — {t['artist']}") if ok else ui_err("rpc failed"),
                delete_after=6)

        elif sub == "autorpc":
            opt = args[2].lower() if len(args) > 2 else ""
            if opt == "on":
                u = _lfm.get("username","")
                if not u:
                    return await message.edit(content=ui_err("set username first"))
                _autorpc_enabled = True
                if _autorpc_task and not _autorpc_task.done():
                    _autorpc_task.cancel()
                _autorpc_task = asyncio.create_task(lfm_autorpc_loop())
                await message.edit(content=ui_ok("lastfm autorpc enabled"))
            elif opt == "off":
                _autorpc_enabled = False
                if _autorpc_task:
                    _autorpc_task.cancel(); _autorpc_task = None
                await message.edit(content=ui_ok("lastfm autorpc disabled"))
            else:
                state = "on" if _autorpc_enabled else "off"
                await message.edit(content=ui_info(f"autorpc is {state}"))
        else:
            try: await message.delete()
            except Exception: pass
            await message.channel.send(build_help_section("lastfm"))

    # ─────────────────────────────────
    # DEVELOPER
    # ─────────────────────────────────

    elif cmd == "eval":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: eval <code>"))
        code = " ".join(args[1:])
        try:
            result = eval(code, {"client": client, "message": message, "discord": discord, "asyncio": asyncio})
            if asyncio.iscoroutine(result): result = await result
            await message.edit(content=f"```py\n{str(result)[:1900]}\n```")
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "restart":
        await message.edit(content=ui_warn("restarting..."))
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # ─────────────────────────────────
    # SERVER MANAGEMENT
    # ─────────────────────────────────

    elif cmd == "serverinfo":
        g = message.guild
        if not g:
            return await message.edit(content=ui_err("not in a server"))
        await message.edit(content=ui_box(g.name, [
            f"  {DIM}id{RESET}          {g.id}",
            f"  {DIM}owner{RESET}       {g.owner}",
            f"  {DIM}members{RESET}     {g.member_count}",
            f"  {DIM}channels{RESET}    {len(g.channels)}",
            f"  {DIM}roles{RESET}       {len(g.roles)}",
            f"  {DIM}created{RESET}     {g.created_at.strftime('%Y-%m-%d')}",
            f"  {DIM}boost level{RESET} {g.premium_tier}",
        ]))

    elif cmd == "members":
        g = message.guild
        if not g:
            return await message.edit(content=ui_err("not in a server"))
        n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 20
        rows = [f"  {GREY}•{RESET} {m.display_name}  {DIM}({m.id}){RESET}" for m in list(g.members)[:n]]
        await message.edit(content=_paginate("members", g.name, rows))

    elif cmd == "channels":
        g = message.guild
        if not g:
            return await message.edit(content=ui_err("not in a server"))
        rows = [f"  {GREY}•{RESET} #{ch.name}  {DIM}({ch.id}){RESET}" for ch in g.channels]
        await message.edit(content=_paginate("channels", g.name, rows))

    elif cmd == "roles":
        g = message.guild
        if not g:
            return await message.edit(content=ui_err("not in a server"))
        rows = [f"  {GREY}•{RESET} {r.name}  {DIM}({r.id}){RESET}" for r in g.roles]
        await message.edit(content=_paginate("roles", g.name, rows))

    elif cmd in ("ban","kick","mute","unmute"):
        g = message.guild
        if not g or len(args) < 2:
            return await message.edit(content=ui_err(f"usage: {cmd} <user_id>"))
        try:
            member = g.get_member(int(args[1]))
            if cmd == "ban":
                reason = " ".join(args[2:]) or "no reason"
                await g.ban(member, reason=reason)
            elif cmd == "kick":
                reason = " ".join(args[2:]) or "no reason"
                await g.kick(member, reason=reason)
            elif cmd == "mute":
                until = discord.utils.utcnow() + timedelta(minutes=10)
                await member.timeout(until)
            elif cmd == "unmute":
                await member.timeout(None)
            await message.edit(content=ui_ok(f"{cmd} → {member}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "setnick":
        g = message.guild
        if not g or len(args) < 3:
            return await message.edit(content=ui_err("usage: setnick <user_id> <nick>"))
        try:
            m = g.get_member(int(args[1]))
            nick = " ".join(args[2:])
            await m.edit(nick=nick)
            await message.edit(content=ui_ok(f"nick set: {nick}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "topic":
        if not message.guild or len(args) < 2:
            return await message.edit(content=ui_err("usage: topic <text>"))
        try:
            await message.channel.edit(topic=" ".join(args[1:]))
            await message.edit(content=ui_ok("topic set"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "slowmode":
        if not message.guild or len(args) < 2:
            return await message.edit(content=ui_err("usage: slowmode <seconds>"))
        try:
            await message.channel.edit(slowmode_delay=int(args[1]))
            await message.edit(content=ui_ok(f"slowmode → {args[1]}s"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    # ─────────────────────────────────
    # INFORMATION
    # ─────────────────────────────────

    elif cmd == "userinfo":
        uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
        try:
            async with aiohttp.ClientSession() as s:
                h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
                async with s.get(f"https://discord.com/api/v9/users/{uid}", headers=h) as r:
                    if r.status != 200:
                        return await message.edit(content=ui_err("user not found"))
                    u = await r.json()
            pfp = f"https://cdn.discordapp.com/avatars/{uid}/{u.get('avatar')}.webp?size=256" if u.get("avatar") else "no avatar"
            await message.edit(content=ui_box("user info", [
                f"  {DIM}username{RESET}   {u.get('username','?')}",
                f"  {DIM}id{RESET}         {uid}",
                f"  {DIM}avatar{RESET}     {pfp}",
                f"  {DIM}bot{RESET}        {'yes' if u.get('bot') else 'no'}",
                f"  {DIM}badge flags{RESET} {u.get('public_flags',0)}",
            ]))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "avatar":
        uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
        try:
            async with aiohttp.ClientSession() as s:
                h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
                async with s.get(f"https://discord.com/api/v9/users/{uid}", headers=h) as r:
                    if r.status != 200:
                        return await message.edit(content=ui_err("user not found"))
                    u = await r.json()
            if u.get("avatar"):
                url = f"https://cdn.discordapp.com/avatars/{uid}/{u['avatar']}.webp?size=2048"
                await message.edit(content=url)
            else:
                await message.edit(content=ui_info("no avatar"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "checkname":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: checkname <username>"))
        username = args[1].lower().strip()
        try:
            async with aiohttp.ClientSession() as s:
                h = {"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT}
                async with s.post("https://discord.com/api/v9/users/@me/pomelo-attempt",
                    headers=h, json={"username": username}) as r:
                    if r.status == 200:
                        d = await r.json()
                        taken = d.get("taken", True)
                        await message.edit(content=
                            ui_ok(f"`{username}` is AVAILABLE! claim it now") if not taken
                            else ui_err(f"`{username}` is taken"))
                    else:
                        await message.edit(content=ui_err(f"check failed ({r.status})"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "whois":
        uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
        try:
            async with aiohttp.ClientSession() as s:
                h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
                async with s.get(f"https://discord.com/api/v9/users/{uid}/profile", headers=h) as r:
                    if r.status != 200:
                        return await message.edit(content=ui_err("profile not found"))
                    p = await r.json()
            u = p.get("user", {})
            badges = [b.get("id","") for b in p.get("badges", [])]
            rows = [
                f"  {DIM}username{RESET}       {u.get('username','?')}",
                f"  {DIM}id{RESET}             {uid}",
                f"  {DIM}bio{RESET}            {u.get('bio','') or '-'}",
                f"  {DIM}badges{RESET}         {', '.join(badges) or 'none'}",
                f"  {DIM}nitro{RESET}          {'yes' if p.get('premium_since') else 'no'}",
                f"  {DIM}mutual servers{RESET} {len(p.get('mutual_guilds', []))}",
                f"  {DIM}mutual friends{RESET} {len(p.get('mutual_friends', []))}",
            ]
            await message.edit(content=ui_box("whois", rows))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "channelinfo":
        cid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.channel.id
        ch = client.get_channel(cid)
        if not ch:
            return await message.edit(content=ui_err("channel not found"))
        rows = [
            f"  {DIM}name{RESET}    #{getattr(ch, 'name', cid)}",
            f"  {DIM}id{RESET}      {ch.id}",
            f"  {DIM}type{RESET}    {str(ch.type)}",
        ]
        try:
            rows.append(f"  {DIM}created{RESET} {ch.created_at.strftime('%Y-%m-%d')}")
        except Exception: pass
        if getattr(ch, "guild", None):
            rows.append(f"  {DIM}guild{RESET}   {ch.guild.name}")
        else:
            rows.append(f"  {DIM}guild{RESET}   DM")
        await message.edit(content=ui_box("channel info", rows))

    elif cmd == "roleinfo":
        if not message.guild or len(args) < 2:
            return await message.edit(content=ui_err("usage: roleinfo <role_id>"))
        role = message.guild.get_role(int(args[1]))
        if not role:
            return await message.edit(content=ui_err("role not found"))
        await message.edit(content=ui_box("role info", [
            f"  {DIM}name{RESET}        {role.name}",
            f"  {DIM}id{RESET}          {role.id}",
            f"  {DIM}color{RESET}       #{role.color.value:06x}",
            f"  {DIM}members{RESET}     {len(role.members)}",
            f"  {DIM}position{RESET}    {role.position}",
            f"  {DIM}mentionable{RESET} {'yes' if role.mentionable else 'no'}",
            f"  {DIM}hoisted{RESET}     {'yes' if role.hoist else 'no'}",
        ]))

    # ─────────────────────────────────
    # GROUP CHAT
    # ─────────────────────────────────

    elif cmd == "gclist":
        try: await message.delete()
        except Exception: pass
        dms = [c for c in client.private_channels if isinstance(c, discord.GroupChannel)]
        if not dms:
            return await message.channel.send(ui_info("no group DMs"), delete_after=5)
        rows = [f"  {GREY}[{i}]{RESET} {WHITE}{gc.name or 'Unnamed GC'}{RESET}  {DIM}({gc.id}){RESET}"
                for i, gc in enumerate(dms)]
        await message.channel.send(_paginate("groupchats", "your group DMs", rows))

    elif cmd == "gcrename":
        if len(args) < 2 or not isinstance(message.channel, discord.GroupChannel):
            return await message.edit(content=ui_err("run in a group DM: gcrename <name>"))
        try:
            await message.channel.edit(name=" ".join(args[1:]))
            await message.edit(content=ui_ok("gc renamed"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "gcleave":
        if not isinstance(message.channel, discord.GroupChannel):
            return await message.edit(content=ui_err("run in a group DM"))
        try:
            await message.channel.leave()
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "gccreate":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: gccreate <user_id> [user_id2...]"))
        try:
            users = []
            for uid_str in args[1:]:
                u = await client.fetch_user(int(uid_str))
                if u: users.append(u)
            if not users:
                return await message.edit(content=ui_err("no valid users"))
            gc = await client.user.create_group(*users)
            await message.edit(content=ui_ok(f"group DM created: {gc.id}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "gcadd":
        if not isinstance(message.channel, discord.GroupChannel) or len(args) < 2:
            return await message.edit(content=ui_err("run in group DM: gcadd <user_id>"))
        try:
            u = await client.fetch_user(int(args[1]))
            await message.channel.add_recipients(u)
            await message.edit(content=ui_ok(f"added {u}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "gcremove":
        if not isinstance(message.channel, discord.GroupChannel) or len(args) < 2:
            return await message.edit(content=ui_err("run in group DM: gcremove <user_id>"))
        try:
            u = await client.fetch_user(int(args[1]))
            await message.channel.remove_recipients(u)
            await message.edit(content=ui_ok(f"removed {u}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "gcicon":
        if not isinstance(message.channel, discord.GroupChannel) or len(args) < 2:
            return await message.edit(content=ui_err("run in group DM: gcicon <url>"))
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(args[1]) as r:
                    img = await r.read()
            ext = args[1].split(".")[-1].split("?")[0].lower()
            mime = {"gif":"gif","png":"png","jpg":"jpeg","jpeg":"jpeg","webp":"webp"}.get(ext,"png")
            b64 = base64.b64encode(img).decode()
            h = {"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT}
            async with aiohttp.ClientSession() as s:
                async with s.patch(f"https://discord.com/api/v9/channels/{message.channel.id}",
                    headers=h, json={"icon": f"data:image/{mime};base64,{b64}"}) as resp:
                    await message.edit(content=ui_ok("gc icon set") if resp.status == 200 else ui_err(f"failed {resp.status}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "agc":
        sub = args[1].lower() if len(args) > 1 else ""
        if not sub:
            state = "ON" if _agc_state["enabled"] else "OFF"
            return await message.edit(content=ui_info(f"anti-gc trap is {state}"))
        elif sub in ("on","enable"):
            _agc_state["enabled"] = True
            await message.edit(content=ui_ok("anti-gc trap enabled"))
        elif sub in ("off","disable"):
            _agc_state["enabled"] = False
            await message.edit(content=ui_ok("anti-gc trap disabled"))
        elif sub == "block":
            opt = args[2].lower() if len(args) > 2 else ""
            _agc_state["block"] = opt in ("on","enable")
            await message.edit(content=ui_ok(f"agc auto-block → {opt}"))
        elif sub == "msg":
            _agc_state["leave_msg"] = " ".join(args[2:])
            await message.edit(content=ui_ok("agc leave message set"))
        elif sub == "name":
            _agc_state["gc_name"] = " ".join(args[2:])
            await message.edit(content=ui_ok("agc gc name set"))
        elif sub == "icon":
            _agc_state["gc_icon_url"] = args[2] if len(args) > 2 else None
            await message.edit(content=ui_ok("agc icon url set"))
        elif sub == "webhook":
            _agc_state["webhook_url"] = args[2] if len(args) > 2 else None
            await message.edit(content=ui_ok("agc webhook set"))
        elif sub == "whitelist":
            if len(args) < 3:
                return await message.edit(content=ui_err("usage: agc whitelist <user_id>"))
            uid = args[2].strip("<@!>")
            _agc_whitelist.add(uid); _agc_save_wl()
            await message.edit(content=ui_ok(f"whitelisted {uid}"))
        elif sub == "unwhitelist":
            if len(args) < 3:
                return await message.edit(content=ui_err("usage: agc unwhitelist <user_id>"))
            uid = args[2].strip("<@!>")
            _agc_whitelist.discard(uid); _agc_save_wl()
            await message.edit(content=ui_ok(f"removed {uid} from whitelist"))
        elif sub == "wllist":
            if not _agc_whitelist:
                return await message.edit(content=ui_info("whitelist is empty"))
            rows = [f"  {GREY}•{RESET} {uid}" for uid in _agc_whitelist]
            await message.edit(content=_paginate("agc whitelist", "", rows))
        else:
            await message.edit(content=ui_err("usage: agc on/off/block/msg/name/icon/webhook/whitelist"))

    # ─────────────────────────────────
    # UTILITY
    # ─────────────────────────────────

    elif cmd == "uwuify":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: uwuify <text>"))
        await message.edit(content=uwuify(" ".join(args[1:])))

    elif cmd == "owoify":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: owoify <text>"))
        await message.edit(content=owoify(" ".join(args[1:])))

    elif cmd == "mock":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: mock <text>"))
        await message.edit(content=mock_text(" ".join(args[1:])))

    elif cmd == "reverse":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: reverse <text>"))
        await message.edit(content=" ".join(args[1:])[::-1])

    elif cmd == "aesthetic":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: aesthetic <text>"))
        await message.edit(content=aesthetic(" ".join(args[1:])))

    elif cmd == "clap":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: clap <text>"))
        await message.edit(content=clap_text(" ".join(args[1:])))

    elif cmd == "animatetype":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: animatetype <text>"))
        text = " ".join(args[1:])
        built = ""
        for ch in text:
            built += ch
            try: await message.edit(content=built)
            except Exception: pass
            await asyncio.sleep(0.1)

    elif cmd == "typing":
        cid = message.channel.id
        if cid in _typing_tasks and not _typing_tasks[cid].done():
            return await message.edit(content=ui_info("already typing here"))
        try: await message.delete()
        except Exception: pass
        _typing_tasks[cid] = asyncio.create_task(typing_loop(message.channel))

    elif cmd == "typingstop":
        cid = message.channel.id
        if cid in _typing_tasks:
            _typing_tasks[cid].cancel(); del _typing_tasks[cid]
        try: await message.delete()
        except Exception: pass

    elif cmd == "afk":
        _afk_enabled = True
        _afk_msg = " ".join(args[1:]) if len(args) > 1 else "I'm AFK right now."
        await message.edit(content=ui_ok(f"AFK set: {_afk_msg}"))

    elif cmd == "afkstop":
        _afk_enabled = False; _afk_msg = None
        await message.edit(content=ui_ok("AFK disabled"))

    elif cmd == "translate":
        if len(args) < 3:
            return await message.edit(content=ui_err("usage: translate <lang_code> <text>"))
        lang = args[1]; text = " ".join(args[2:])
        result = await translate_text(text, lang)
        await message.edit(content=f"```\n{result}\n```")

    elif cmd == "speaklanguage":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: speaklanguage <lang_code>"))
        _speak_lang = args[1]
        await message.edit(content=ui_ok(f"auto-translate → {_speak_lang}"))

    elif cmd == "speaklanguagestop":
        _speak_lang = None
        await message.edit(content=ui_ok("auto-translate stopped"))

    elif cmd == "ghostping":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: ghostping <user_id>"))
        try: await message.delete()
        except Exception: pass
        m = await message.channel.send(f"<@{args[1]}>")
        await asyncio.sleep(0.3)
        await m.delete()

    elif cmd == "pin":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: pin <msg_id>"))
        try:
            msg = await message.channel.fetch_message(int(args[1]))
            await msg.pin()
            await message.edit(content=ui_ok("pinned"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "unpin":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: unpin <msg_id>"))
        try:
            msg = await message.channel.fetch_message(int(args[1]))
            await msg.unpin()
            await message.edit(content=ui_ok("unpinned"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "therapy":
        try: await message.delete()
        except Exception: pass
        responses = [
            "I hear you. That sounds really difficult.",
            "Your feelings are valid. Take things one step at a time.",
            "It's okay to not have everything figured out.",
            "You're doing better than you think.",
            "Remember to be kind to yourself today.",
        ]
        await message.channel.send(random.choice(responses))

    elif cmd == "ragebait":
        try: await message.delete()
        except Exception: pass
        baits = [
            "pineapple on pizza is literally the best topping change my mind",
            "anime is just cartoons for people who couldn't make friends in high school",
            "people who say 'its the vibe' without explaining anything are just not smart enough to articulate",
            "dogs are overrated. cats are objectively superior",
            "morning people are just people who go to bed early. you're not special",
        ]
        await message.channel.send(random.choice(baits))

    elif cmd == "firstmessage":
        try: await message.delete()
        except Exception: pass
        async for msg in message.channel.history(limit=1, oldest_first=True):
            await message.channel.send(
                ui_box("first message", [
                    f"  {DIM}author{RESET}  {msg.author}",
                    f"  {DIM}date{RESET}    {msg.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
                    f"  {DIM}content{RESET} {msg.content[:200] or '(empty)'}",
                    f"  {DIM}url{RESET}     {msg.jump_url}",
                ]))

    # ─────────────────────────────────
    # TRACKING
    # ─────────────────────────────────

    elif cmd == "track":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: track <user_id>"))
        uid = int(args[1])
        _tracked_users.add(uid)
        await message.edit(content=ui_ok(f"tracking <@{uid}>"))

    elif cmd == "untrack":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: untrack <user_id>"))
        uid = int(args[1])
        _tracked_users.discard(uid)
        _tracking.pop(uid, None)
        await message.edit(content=ui_ok(f"stopped tracking <@{uid}>"))

    elif cmd == "tracklist":
        if not _tracked_users:
            return await message.edit(content=ui_info("not tracking anyone"))
        rows = [f"  {GREY}•{RESET} <@{uid}>  {DIM}({len(_tracking.get(uid,[]))} msgs){RESET}"
                for uid in _tracked_users]
        await message.edit(content=_paginate("tracking", "tracked users", rows))

    elif cmd == "history":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: history <user_id>"))
        uid = int(args[1])
        msgs = _tracking.get(uid, [])
        if not msgs:
            return await message.edit(content=ui_info("no tracked messages for this user"))
        rows = [f"  {DIM}[{m['time']}] #{m['channel']}{RESET}  {WHITE}{m['content'][:60]}{RESET}"
                for m in msgs[-PAGE_SIZE * 3:]]
        await message.edit(content=_paginate("history", f"<@{uid}>", rows))

    # ─────────────────────────────────
    # DOWNLOADS
    # ─────────────────────────────────

    elif cmd in ("yt","youtube","ytaudio","tiktok","tt","instagram","ig"):
        try: await message.delete()
        except Exception: pass
        if len(args) < 2:
            return await message.channel.send(ui_err(f"usage: {cmd} <url>"), delete_after=5)
        url = args[1]
        audio_only = cmd in ("ytaudio",)
        audio_flag = ["--extract-audio", "--audio-format", "mp3"] if audio_only else ["-f", "best[filesize<25M]"]
        import subprocess
        try:
            await message.channel.send(ui_info(f"downloading {url}..."), delete_after=5)
            fname = f"/tmp/dl_{uuid4().hex[:8]}.%(ext)s"
            proc = await asyncio.create_subprocess_exec(
                "yt-dlp", url, "-o", fname, *audio_flag,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, err = await asyncio.wait_for(proc.communicate(), timeout=60)
            import glob
            files = glob.glob(f"/tmp/dl_*")
            if files:
                latest = max(files, key=os.path.getctime)
                size = os.path.getsize(latest)
                if size < 25 * 1024 * 1024:
                    await message.channel.send(file=discord.File(latest))
                    os.remove(latest)
                else:
                    await message.channel.send(ui_err("file too large for discord (>25MB)"), delete_after=8)
                    os.remove(latest)
            else:
                await message.channel.send(ui_err(f"download failed: {err.decode()[:200]}"), delete_after=8)
        except asyncio.TimeoutError:
            await message.channel.send(ui_err("download timed out"), delete_after=8)
        except FileNotFoundError:
            await message.channel.send(ui_err("yt-dlp not installed — pip install yt-dlp"), delete_after=8)
        except Exception as e:
            await message.channel.send(ui_err(str(e)[:200]), delete_after=8)

    # ─────────────────────────────────
    # SOCIAL
    # ─────────────────────────────────

    elif cmd == "addfriend":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: addfriend <user_id>"))
        h = {"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.put(f"https://discord.com/api/v9/users/@me/relationships/{args[1]}",
                    headers=h, json={"type": 1}) as r:
                    await message.edit(content=ui_ok(f"friend request sent") if r.status in (200,201,204) else ui_err(f"failed {r.status}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "removefriend":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: removefriend <user_id>"))
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.delete(f"https://discord.com/api/v9/users/@me/relationships/{args[1]}", headers=h) as r:
                    await message.edit(content=ui_ok("removed") if r.status in (200,204) else ui_err(f"failed {r.status}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "block":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: block <user_id>"))
        h = {"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.put(f"https://discord.com/api/v9/users/@me/relationships/{args[1]}",
                    headers=h, json={"type": 2}) as r:
                    await message.edit(content=ui_ok("blocked") if r.status in (200,204) else ui_err(f"failed {r.status}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "unblock":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: unblock <user_id>"))
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.delete(f"https://discord.com/api/v9/users/@me/relationships/{args[1]}", headers=h) as r:
                    await message.edit(content=ui_ok("unblocked") if r.status in (200,204) else ui_err(f"failed {r.status}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd in ("friends","blocked","pending"):
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://discord.com/api/v9/users/@me/relationships", headers=h) as r:
                    if r.status != 200:
                        return await message.edit(content=ui_err(f"failed {r.status}"))
                    rels = await r.json()
            type_filter = {"friends": 1, "blocked": 2, "pending": 3}
            t = type_filter[cmd]
            filtered = [x for x in rels if x.get("type") == t]
            rows = [f"  {GREY}•{RESET} {x.get('user',{}).get('username','?')}  {DIM}({x.get('user',{}).get('id','?')}){RESET}"
                    for x in filtered]
            await message.edit(content=_paginate(cmd, f"{len(filtered)} results", rows) if rows else ui_info(f"no {cmd}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "friendcount":
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://discord.com/api/v9/users/@me/relationships", headers=h) as r:
                    rels = await r.json() if r.status == 200 else []
            friends = sum(1 for x in rels if x.get("type") == 1)
            blocked = sum(1 for x in rels if x.get("type") == 2)
            pending = sum(1 for x in rels if x.get("type") == 3)
            await message.edit(content=ui_box("friend counts", [
                f"  {DIM}friends{RESET}  {friends}",
                f"  {DIM}blocked{RESET}  {blocked}",
                f"  {DIM}pending{RESET}  {pending}",
            ]))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "clearincoming":
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://discord.com/api/v9/users/@me/relationships", headers=h) as r:
                    rels = await r.json() if r.status == 200 else []
                incoming = [x for x in rels if x.get("type") == 3]
                for rel in incoming:
                    uid = rel.get("user", {}).get("id")
                    if uid:
                        await s.delete(f"https://discord.com/api/v9/users/@me/relationships/{uid}", headers=h)
                        await asyncio.sleep(0.3)
            await message.edit(content=ui_ok(f"declined {len(incoming)} incoming requests"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "clearoutgoing":
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://discord.com/api/v9/users/@me/relationships", headers=h) as r:
                    rels = await r.json() if r.status == 200 else []
                outgoing = [x for x in rels if x.get("type") == 4]
                for rel in outgoing:
                    uid = rel.get("user", {}).get("id")
                    if uid:
                        await s.delete(f"https://discord.com/api/v9/users/@me/relationships/{uid}", headers=h)
                        await asyncio.sleep(0.3)
            await message.edit(content=ui_ok(f"cancelled {len(outgoing)} outgoing requests"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "closedms":
        count = 0
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                for ch in list(client.private_channels):
                    if isinstance(ch, discord.DMChannel):
                        await s.delete(f"https://discord.com/api/v9/channels/{ch.id}", headers=h)
                        count += 1
                        await asyncio.sleep(0.3)
            await message.edit(content=ui_ok(f"closed {count} DMs"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "readdms":
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        count = 0
        try:
            async with aiohttp.ClientSession() as s:
                for ch in list(client.private_channels):
                    await s.post(f"https://discord.com/api/v9/channels/{ch.id}/ack",
                        headers={**h, "Content-Type": "application/json"}, json={})
                    count += 1
                    await asyncio.sleep(0.2)
            await message.edit(content=ui_ok(f"marked {count} DMs as read"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "note":
        if len(args) < 3:
            return await message.edit(content=ui_err("usage: note <user_id> <text>"))
        uid = args[1]; text = " ".join(args[2:])
        h = {"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.put(f"https://discord.com/api/v9/users/@me/notes/{uid}",
                    headers=h, json={"note": text}) as r:
                    await message.edit(content=ui_ok("note set") if r.status in (200,204) else ui_err(f"failed {r.status}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "autoaddback":
        _autoaddback = len(args) < 2 or args[1].lower() in ("on","enable")
        cfg = load_config(); cfg["autoaddback"] = _autoaddback; save_config(cfg)
        await message.edit(content=ui_ok(f"autoaddback → {'on' if _autoaddback else 'off'}"))

    # ─────────────────────────────────
    # AUTO
    # ─────────────────────────────────

    elif cmd == "giveaway":
        _giveaway_enabled = len(args) < 2 or args[1].lower() in ("on","enable")
        await message.edit(content=ui_ok(f"giveaway sniper → {'on' if _giveaway_enabled else 'off'}"))

    elif cmd == "nitrosniper":
        _nitrosniper_enabled = len(args) < 2 or args[1].lower() in ("on","enable")
        await message.edit(content=ui_ok(f"nitro sniper → {'on' if _nitrosniper_enabled else 'off'}"))

    elif cmd == "autoreact":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: autoreact <emoji>"))
        _autoreact_emoji = args[1]
        await message.edit(content=ui_ok(f"auto-reacting with {_autoreact_emoji}"))

    elif cmd == "autoreactstop":
        _autoreact_emoji = None
        await message.edit(content=ui_ok("auto-react stopped"))

    elif cmd in ("multireact", "multiautoreact"):
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add":
            if len(args) < 3:
                return await message.edit(content=ui_err("usage: multireact add <emoji>"))
            emoji = args[2]
            if emoji in _multireact_pool:
                return await message.edit(content=ui_info(f"{emoji} already in pool"))
            _multireact_pool.append(emoji)
            await message.edit(content=ui_ok(f"added {emoji} to pool ({len(_multireact_pool)} total)"))
        elif sub in ("remove","rem","del"):
            if len(args) < 3:
                return await message.edit(content=ui_err("usage: multireact remove <emoji>"))
            emoji = args[2]
            if emoji not in _multireact_pool:
                return await message.edit(content=ui_err(f"{emoji} not in pool"))
            _multireact_pool.remove(emoji)
            await message.edit(content=ui_ok(f"removed {emoji} ({len(_multireact_pool)} left)"))
        elif sub == "list":
            if not _multireact_pool:
                return await message.edit(content=ui_info("pool is empty"))
            rows = [f"  {GREY}{i:2}.{RESET}  {e}" for i, e in enumerate(_multireact_pool, 1)]
            state = "ON" if _multireact_enabled else "OFF"
            await message.edit(content=ui_box(f"multi-react pool  —  {state}", rows))
        elif sub in ("on","enable"):
            if not _multireact_pool:
                return await message.edit(content=ui_err("pool is empty — add emojis first"))
            _multireact_enabled = True
            await message.edit(content=ui_ok(f"multi-react enabled ({len(_multireact_pool)} emojis)"))
        elif sub in ("off","disable"):
            _multireact_enabled = False
            await message.edit(content=ui_ok("multi-react disabled"))
        elif sub == "clear":
            _multireact_pool.clear()
            _multireact_enabled = False
            await message.edit(content=ui_ok("multi-react pool cleared"))
        else:
            await message.edit(content=ui_info(
                "usage: multireact add/remove/list/on/off/clear"))

    elif cmd == "vsniper":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add":
            if len(args) < 4:
                return await message.edit(content=ui_err("usage: vsniper add <vanity_code> <guild_id>"))
            _vsniper_list.append({"code": args[2], "guild_id": args[3]})
            await message.edit(content=ui_ok(f"watching vanity: {args[2]}"))
        elif sub == "start":
            if _vsniper_task and not _vsniper_task.done():
                return await message.edit(content=ui_info("vsniper already running"))
            _vsniper_task = asyncio.create_task(vsniper_loop())
            await message.edit(content=ui_ok("vsniper started"))
        elif sub == "stop":
            if _vsniper_task:
                _vsniper_task.cancel(); _vsniper_task = None
            await message.edit(content=ui_ok("vsniper stopped"))
        elif sub == "list":
            if not _vsniper_list:
                return await message.edit(content=ui_info("no vanities in watch list"))
            rows = [f"  {GREY}•{RESET} {e['code']}  {DIM}guild {e['guild_id']}{RESET}" for e in _vsniper_list]
            await message.edit(content=_paginate("vsniper", "watch list", rows))
        else:
            await message.edit(content=ui_err("usage: vsniper add/start/stop/list"))

    # ─────────────────────────────────
    # PROFILE
    # ─────────────────────────────────

    elif cmd == "setpfp":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: setpfp <image_url>"))
        url = args[1]
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url) as r:
                    img_data = await r.read()
                ext = url.split(".")[-1].split("?")[0].lower()
                mime = {"jpg":"jpeg","jpeg":"jpeg","png":"png","gif":"gif","webp":"webp"}.get(ext,"png")
                b64 = base64.b64encode(img_data).decode()
                data_uri = f"data:image/{mime};base64,{b64}"
                h = {"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT}
                async with s.patch("https://discord.com/api/v9/users/@me",
                    headers=h, json={"avatar": data_uri}) as r2:
                    await message.edit(content=ui_ok("pfp updated") if r2.status == 200 else ui_err(f"failed {r2.status}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "setbio":
        bio = " ".join(args[1:]) if len(args) > 1 else ""
        h = {"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.patch("https://discord.com/api/v9/users/@me/profile",
                    headers=h, json={"bio": bio}) as r:
                    await message.edit(content=ui_ok("bio updated") if r.status == 200 else ui_err(f"failed {r.status}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "setbanner":
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: setbanner <image_url>"))
        url = args[1]
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url) as r:
                    img_data = await r.read()
                ext = url.split(".")[-1].split("?")[0].lower()
                mime = {"jpg":"jpeg","jpeg":"jpeg","png":"png","gif":"gif","webp":"webp"}.get(ext,"png")
                b64 = base64.b64encode(img_data).decode()
                data_uri = f"data:image/{mime};base64,{b64}"
                h = {"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT}
                async with s.patch("https://discord.com/api/v9/users/@me",
                    headers=h, json={"banner": data_uri}) as r2:
                    await message.edit(content=ui_ok("banner updated") if r2.status == 200 else ui_err(f"failed {r2.status}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "myprofile":
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://discord.com/api/v9/users/@me", headers=h) as r:
                    if r.status != 200:
                        return await message.edit(content=ui_err("failed"))
                    u = await r.json()
            uid = u.get("id")
            pfp = f"https://cdn.discordapp.com/avatars/{uid}/{u.get('avatar')}.webp?size=256" if u.get("avatar") else "none"
            banner = f"https://cdn.discordapp.com/banners/{uid}/{u.get('banner')}.webp?size=512" if u.get("banner") else "none"
            await message.edit(content=ui_box("my profile", [
                f"  {DIM}username{RESET}  {u.get('username')}",
                f"  {DIM}id{RESET}        {uid}",
                f"  {DIM}avatar{RESET}    {pfp}",
                f"  {DIM}banner{RESET}    {banner}",
                f"  {DIM}email{RESET}     {u.get('email','?')}",
                f"  {DIM}phone{RESET}     {u.get('phone','?')}",
                f"  {DIM}nitro{RESET}     {'yes' if u.get('premium_type') else 'no'}",
            ]))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "accountbackup":
        try: await message.delete()
        except Exception: pass
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        async with aiohttp.ClientSession() as s:
            async with s.get("https://discord.com/api/v9/users/@me", headers=h) as r:
                profile = await r.json() if r.status == 200 else {}
            async with s.get("https://discord.com/api/v9/users/@me/relationships", headers=h) as r:
                rels = await r.json() if r.status == 200 else []
        backup = {
            "profile": profile,
            "relationships": rels,
            "guilds": [{"id": str(g.id), "name": g.name} for g in client.guilds],
            "timestamp": datetime.now().isoformat(),
        }
        fname = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(fname, "w") as f:
            json.dump(backup, f, indent=2)
        await message.channel.send(ui_ok(f"account backed up to {fname}"), delete_after=8)

    # ─────────────────────────────────
    # STATUS
    # ─────────────────────────────────

    elif cmd in ("setstatus", "customstatus"):
        if len(args) < 2:
            return await message.edit(content=ui_box("setstatus", [
                f"  {DIM}usage:{RESET}",
                f"  {PREFIX}setstatus <text>",
                f"  {PREFIX}setstatus <emoji>, <text>",
                f"  {PREFIX}setstatus <:name:id>, <text>",
                "",
                f"  {DIM}examples:{RESET}",
                f"  {PREFIX}setstatus Gaming now",
                f"  {PREFIX}setstatus 🎮, Gaming now",
                f"  {PREFIX}setstatus <:pepe:123456789>, vibing",
            ]))

        full_text = " ".join(args[1:])
        emoji_name = None
        emoji_id = None
        text = full_text.strip()

        if "," in text:
            parts = text.split(",", 1)
            emoji_part = parts[0].strip()
            text_part = parts[1].strip() if len(parts) > 1 else ""

            if not text_part:
                return await message.edit(content=ui_err("provide status text after the comma"))

            ce_match = re.match(r"<:([a-zA-Z0-9_]+):([0-9]+)>", emoji_part)
            if ce_match:
                emoji_name = ce_match.group(1)
                emoji_id   = ce_match.group(2)
            elif len(emoji_part) >= 1 and (len(emoji_part) == 1 or any(ord(c) > 127 for c in emoji_part)):
                emoji_name = emoji_part
            else:
                return await message.edit(content=ui_err("invalid emoji — use standard emoji or <:name:id>"))

            text = text_part

        if not text:
            return await message.edit(content=ui_err("provide status text"))

        payload = {"custom_status": {"text": text, "emoji_name": emoji_name, "emoji_id": emoji_id}}
        h = {"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.patch("https://discord.com/api/v9/users/@me/settings",
                    headers=h, json=payload) as r:
                    if r.status == 200:
                        emoji_display = f"{emoji_name} " if emoji_name else ""
                        cfg = load_config()
                        hist = cfg.get("status_history", [])
                        hist.insert(0, {"text": text, "emoji": emoji_name, "time": datetime.now().strftime("%H:%M %d/%m")})
                        cfg["status_history"] = hist[:20]
                        save_config(cfg)
                        await message.edit(content=ui_ok(f"status set: {emoji_display}{text}"))
                    elif r.status == 429:
                        retry = (await r.json()).get("retry_after", 1)
                        await message.edit(content=ui_warn(f"rate limited — retry in {retry}s"))
                    else:
                        await message.edit(content=ui_err(f"failed: {r.status}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "clearstatus":
        payload = {"custom_status": {"text": "", "emoji_name": None, "emoji_id": None}}
        h = {"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.patch("https://discord.com/api/v9/users/@me/settings",
                    headers=h, json=payload) as r:
                    await message.edit(content=ui_ok("status cleared") if r.status == 200 else ui_err(f"failed: {r.status}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd in ("stealstatus", "copystatus"):
        if len(args) < 2:
            return await message.edit(content=ui_err("usage: stealstatus <user_id>"))
        uid = args[1].strip("<@!>")
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://discord.com/api/v9/users/{uid}/profile", headers=h) as r:
                    if r.status != 200:
                        return await message.edit(content=ui_err("could not fetch user profile"))
                    profile = await r.json()

                username = profile.get("user", {}).get("username", "?")
                user_profile = profile.get("user_profile", {})
                custom_text  = user_profile.get("bio", "") or ""
                emoji_name   = None
                emoji_id     = None

                if not custom_text:
                    return await message.edit(content=ui_err(f"{username} has no visible custom status"))

                payload = {"custom_status": {"text": custom_text, "emoji_name": emoji_name, "emoji_id": emoji_id}}
                async with s.patch("https://discord.com/api/v9/users/@me/settings",
                    headers={**h, "Content-Type": "application/json"}, json=payload) as r2:
                    if r2.status == 200:
                        cfg = load_config()
                        hist = cfg.get("status_history", [])
                        hist.insert(0, {"text": custom_text, "emoji": None, "time": datetime.now().strftime("%H:%M %d/%m"), "stolen_from": username})
                        cfg["status_history"] = hist[:20]
                        save_config(cfg)
                        await message.edit(content=ui_ok(f"stole status from {username}: {custom_text}"))
                    else:
                        await message.edit(content=ui_err(f"failed to apply status: {r2.status}"))
        except Exception as e:
            await message.edit(content=ui_err(str(e)))

    elif cmd == "statushistory":
        cfg = load_config()
        hist = cfg.get("status_history", [])
        if not hist:
            return await message.edit(content=ui_info("no status history yet"))
        rows = []
        for i, entry in enumerate(hist[:20], 1):
            emoji = f"{entry['emoji']} " if entry.get("emoji") else ""
            stolen = f"  {DIM}(from {entry['stolen_from']}){RESET}" if entry.get("stolen_from") else ""
            rows.append(f"  {GREY}{i:2}.{RESET} {WHITE}{emoji}{entry['text']}{RESET}  {DIM}{entry['time']}{RESET}{stolen}")
        await message.edit(content=_paginate("status history", "recent statuses", rows))

# ─────────────────────────────────────────────
# OTHER EVENTS
# ─────────────────────────────────────────────

@client.event
async def on_message_delete(message):
    if message.author.id == client.user.id:
        return
    cid = message.channel.id
    _snipe_cache.setdefault(cid, [])
    _snipe_cache[cid].append({
        "author":    str(message.author),
        "author_id": message.author.id,
        "content":   message.content or "",
        "attachments": [a.url for a in message.attachments] if message.attachments else [],
        "time":      datetime.now().strftime("%H:%M:%S"),
    })
    if len(_snipe_cache[cid]) > SNIPE_LIMIT:
        _snipe_cache[cid] = _snipe_cache[cid][-SNIPE_LIMIT:]
    if not LOGGER_ENABLED:
        return
    log_msg("DEL", f"{message.author} in #{getattr(message.channel,'name','DM')}: {message.content[:100]}")

@client.event
async def on_message_edit(before, after):
    if before.author.id == client.user.id:
        return
    if before.content == after.content:
        return
    cid = before.channel.id
    _editsnipe_cache.setdefault(cid, [])
    _editsnipe_cache[cid].append({
        "author":    str(before.author),
        "author_id": before.author.id,
        "before":    before.content or "",
        "after":     after.content or "",
        "time":      datetime.now().strftime("%H:%M:%S"),
    })
    if len(_editsnipe_cache[cid]) > SNIPE_LIMIT:
        _editsnipe_cache[cid] = _editsnipe_cache[cid][-SNIPE_LIMIT:]
    if not LOGGER_ENABLED:
        return
    log_msg("EDIT", f"{before.author}: '{before.content[:60]}' → '{after.content[:60]}'")

@client.event
async def on_relationship_add(relationship):
    if not _autoaddback:
        return
    if relationship.type == discord.RelationshipType.incoming_request:
        try:
            await relationship.accept()
            log_msg("SOCIAL", f"auto-accepted friend request from {relationship.user}")
        except Exception as e:
            log_msg("SOCIAL", f"autoaddback failed: {e}")

@client.event
async def on_group_channel_create(channel):
    if not _agc_state["enabled"]:
        return

    channel_id = str(channel.id)
    owner_id   = str(channel.owner_id) if hasattr(channel, "owner_id") and channel.owner_id else ""

    if owner_id == str(client.user.id):
        return
    if owner_id in _agc_whitelist:
        log_msg("AGC", f"whitelisted owner {owner_id}, skipping")
        return

    log_msg("AGC", f"trap detected — ch {channel_id}, owner {owner_id}")

    h = {"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT}

    async with aiohttp.ClientSession() as s:
        if _agc_state["gc_name"]:
            try:
                await s.patch(f"https://discord.com/api/v9/channels/{channel_id}",
                    headers=h, json={"name": _agc_state["gc_name"]})
            except Exception: pass

        if _agc_state["gc_icon_url"]:
            try:
                async with s.get(_agc_state["gc_icon_url"]) as r:
                    img = await r.read()
                mime = "image/gif" if img[:6] in (b"GIF87a",b"GIF89a") else "image/png"
                b64 = base64.b64encode(img).decode()
                await s.patch(f"https://discord.com/api/v9/channels/{channel_id}",
                    headers=h, json={"icon": f"data:{mime};base64,{b64}"})
            except Exception: pass

        if _agc_state["leave_msg"]:
            try:
                await s.post(f"https://discord.com/api/v9/channels/{channel_id}/messages",
                    headers=h, json={"content": _agc_state["leave_msg"]})
            except Exception: pass

        if _agc_state["block"] and owner_id:
            try:
                await s.put(f"https://discord.com/api/v9/users/@me/relationships/{owner_id}",
                    headers=h, json={"type": 2})
            except Exception: pass

        for _ in range(3):
            try:
                async with s.delete(f"https://discord.com/api/v9/channels/{channel_id}", headers=h) as r:
                    if r.status in (200,204): break
            except Exception: pass
            await asyncio.sleep(1)

        if _agc_state["webhook_url"]:
            try:
                members = [str(u.id) for u in (channel.recipients or [])]
                body = {"content": f"**AGC Alert**\nowner: `{owner_id}`\nchannel: `{channel_id}`\nmembers: `{', '.join(members)}`"}
                await s.post(_agc_state["webhook_url"], json=body)
            except Exception: pass

# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────

print(f"[selfbot] starting — prefix: '{PREFIX}' — v{VERSION}")
try:
    client.run(TOKEN)
except discord.LoginFailure as e:
    print(f"[FATAL] login failed: {e}")
    sys.exit(1)
except Exception as e:
    print(f"[FATAL] {e}")
    raise