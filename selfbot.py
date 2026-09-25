# selfbot.py | Python 3.10+ | modifyself + aiohttp + hcaptcha-challenger
# mist — v2.6.0-turbo

import modifyself_shim as discord

import asyncio
import aiohttp
import json
import os
import sys
import time
import re
import base64
import math
import random
import string
import sqlite3
import traceback
import signal
import importlib
import socket
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from collections import defaultdict

# ─────────────────────────────────────────────
# OPTIONAL INTEGRATIONS
# ─────────────────────────────────────────────

HAS_IPC = False
HAS_DB = False
HAS_HCAPTCHA = False

try:
    from selfbot_ipc import start_ipc_server
    HAS_IPC = True
    print("[boot] selfbot_ipc loaded")
except ImportError as _e:
    print(f"[boot] selfbot_ipc NOT found ({_e}) — IPC server disabled")
    def start_ipc_server(_globals):
        print("[ipc] start_ipc_server called but IPC is unavailable")

try:
    from db_helper import (
        config_get, config_set, config_get_all,
        hosted_tokens_get, hosted_token_add, hosted_token_remove,
        hosted_token_update_username, sync_local_to_supabase, health_check,
        async_hosted_tokens_get, async_hosted_token_add, async_hosted_token_remove,
    )
    HAS_DB = True
    print("[boot] db_helper loaded — Supabase backing active")
except ImportError as _e:
    print(f"[boot] db_helper NOT found ({_e}) — falling back to local JSON")

    _LOCAL_CFG_PATH = "config.json"

    def _local_load():
        if os.path.exists(_LOCAL_CFG_PATH):
            try:
                with open(_LOCAL_CFG_PATH) as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _local_save(cfg):
        try:
            with open(_LOCAL_CFG_PATH, "w") as f:
                json.dump(cfg, f, indent=4)
        except Exception as e:
            print(f"[config] local save error: {e}")

    def config_get(key, default=None):
        return _local_load().get(key, default)

    def config_set(key, value):
        cfg = _local_load()
        cfg[key] = value
        _local_save(cfg)

    def config_get_all():
        return _local_load()

    def hosted_tokens_get():
        return list(_local_load().get("hosted_tokens", []))

    def hosted_token_add(token, username=None):
        cfg = _local_load()
        toks = cfg.setdefault("hosted_tokens", [])
        if token not in toks:
            toks.append(token)
        _local_save(cfg)

    def hosted_token_remove(token):
        cfg = _local_load()
        toks = cfg.get("hosted_tokens", [])
        if token in toks:
            toks.remove(token)
        _local_save(cfg)

    def hosted_token_update_username(token, username):
        return None

    def sync_local_to_supabase():
        return None

    def health_check():
        return False

    async def async_hosted_tokens_get():
        return list(_local_load().get("hosted_tokens", []))

    async def async_hosted_token_add(token, username=None):
        hosted_token_add(token, username)

    async def async_hosted_token_remove(token):
        hosted_token_remove(token)

try:
    from hcaptcha_challenger.agent import AgentV, AgentConfig
    HAS_HCAPTCHA = True
    print("[boot] hcaptcha-challenger loaded — autoclaim captcha solver active")
except ImportError as _e:
    print(f"[boot] hcaptcha-challenger NOT found ({_e}) — autoclaim will fail on captcha")
    AgentV = None
    AgentConfig = None

# ─────────────────────────────────────────────
# BOOTSTRAP
# ─────────────────────────────────────────────

os.makedirs("config", exist_ok=True)
os.makedirs("database", exist_ok=True)
os.makedirs("exports", exist_ok=True)
os.makedirs("plugins", exist_ok=True)
os.makedirs("backups", exist_ok=True)
os.makedirs("cogs", exist_ok=True)
os.makedirs("data", exist_ok=True)

def load_config():
    return config_get_all()

def save_config(cfg: dict):
    for key, value in cfg.items():
        config_set(key, value)

_cfg = load_config()

TOKEN = (
    os.environ.get("TOKEN", "").strip()
    or os.environ.get("DISCORD_TOKEN", "").strip()
    or str(_cfg.get("token", "")).strip()
).strip('"').strip("'")

print(f"[mist] token: {TOKEN[:10]}...{TOKEN[-5:] if len(TOKEN) > 15 else ''}")

if not TOKEN or TOKEN in ("YOUR_TOKEN_HERE", "", "None"):
    print("[FATAL] No token. Set TOKEN env var or config.json")
    sys.exit(1)

PREFIX = os.environ.get("PREFIX") or _cfg.get("prefix", ".")
VERSION = "2.6.0-turbo"
OWNER_ID = 1551632054574121051
LOG_FILE = "message_log.txt"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) discord/1.0.9044 Chrome/120.0.6099.291 "
              "Electron/28.2.10 Safari/537.36")

# ─────────────────────────────────────────────
# LATENCY TUNING — global aiohttp session
# one shared connection pool, keepalive, no Nagle, no DNS re-resolution
# ─────────────────────────────────────────────

_LATENCY_HEADERS = {
    "User-Agent": USER_AGENT,
    "Authorization": TOKEN,
}

def _build_connector():
    try:
        return aiohttp.TCPConnector(
            limit=32,
            limit_per_host=16,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
            force_close=False,
            use_dns_cache=True,
        )
    except TypeError:
        return aiohttp.TCPConnector(limit=32, limit_per_host=16)

_aio_timeout = aiohttp.ClientTimeout(total=30, connect=5, sock_read=20)
_GLOBAL_SESSION = None

def _get_session():
    """Return the shared ClientSession. Created lazily because the loop
    must exist first."""
    global _GLOBAL_SESSION
    if _GLOBAL_SESSION is None or _GLOBAL_SESSION.closed:
        _GLOBAL_SESSION = aiohttp.ClientSession(
            headers=_LATENCY_HEADERS,
            timeout=_aio_timeout,
            connector=_build_connector(),
        )
    return _GLOBAL_SESSION

async def _close_session():
    global _GLOBAL_SESSION
    if _GLOBAL_SESSION and not _GLOBAL_SESSION.closed:
        try:
            await _GLOBAL_SESSION.close()
        except Exception:
            pass
        _GLOBAL_SESSION = None

def _tcp_nodelay(sock):
    """Turn off Nagle on a raw socket. Small gateway frames go out
    immediately instead of coalescing for 40-200ms."""
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass

# ─────────────────────────────────────────────
# ACCESS CONTROL
# ─────────────────────────────────────────────

_admins: set = set()
_devs: set = set()

ADMIN_COMMANDS = frozenset({
    "admin",
    "setadmin", "adminremove", "adminlist",
    "blacklist", "whitelist",
    "serverblacklist", "channelblacklist", "rolerestrict",
    "guards", "perms", "perm",
    "nuke", "massban", "masskick",
})

DEVELOPER_COMMANDS = frozenset({
    "eval", "restart", "reconnect", "proxy", "plugin", "session",
    "logs",
    "setdev", "devremove", "devlist",
    "accesslist",
})

OWNER_COMMANDS = frozenset({
    "setowner",
})

_ACCESS_FILE = "database/access.json"

def _current_owner():
    try:
        from cogs import state as cstate
        v = getattr(cstate, "OWNER_ID", None)
        if isinstance(v, int):
            return v
    except Exception:
        pass
    return OWNER_ID

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
        try:
            from cogs import state as cstate
            cstate.OWNER_ID = OWNER_ID
            cstate._admins  = _admins
            cstate._devs    = _devs
        except Exception:
            pass
        print(f"[access] loaded owner={OWNER_ID} admins={len(_admins)} devs={len(_devs)}")
    except Exception as e:
        print(f"[access] load error: {e}")

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

access_load()

# ─────────────────────────────────────────────
# UI HELPERS
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

# ─────────────────────────────────────────────
# PAGINATED HELP
# ─────────────────────────────────────────────

PAGE_SIZE = 8

def _paginate(title, subtitle, rows, page=1):
    total = max(1, math.ceil(len(rows) / PAGE_SIZE))
    page = max(1, min(page, total))
    chunk = rows[(page-1)*PAGE_SIZE:(page-1)*PAGE_SIZE+PAGE_SIZE]
    lines = [f"  {WHITE}> {title}{RESET}  {DIM}{subtitle}{RESET}", ""] + chunk + [
        "", f"  {DIM}page {page}/{total}  •  {PREFIX}h {title.lower()} {page+1 if page<total else 1} to flip{RESET}"
    ]
    return _ansi_block(lines)

HELP_DATA = {
    "general": [
        ("ping","latency check"),("info","account snapshot"),("say <text>","replace command with text"),
        ("spam <n> <text>","blast n messages fast"),("spamstop","kill active spam loop"),
        ("purge [n]","delete your last n messages"),("clear","delete command message"),
        ("snipe [n]","snipe last deleted message"),("snipe clear","wipe snipe cache"),
        ("editsnipe [n]","snipe last edited message"),("editsnipe clear","wipe edit-snipe cache"),
        ("copycat <id>","mirror next 10 msgs from user"),("status <text>","set custom status"),
        ("status clear","clear status"),("platform <type>","spoof gateway platform"),
        ("platform off","reset platform to desktop"),("hypesquad <house>","set hypesquad house"),
        ("hypesquad off","remove hypesquad badge"),
    ],
    "quests": [
        ("quest","list active quests + progress"),("questrun <index>","solve specific quest"),
        ("questall","solve all quests at once"),("autoquest on/off","auto-run quests on startup"),
        ("autoclaim on/off","auto-claim completed quests"),("autoclaim run","sweep and claim now"),
        ("orbbadge","claim orb badge"),("questdump [idx]","dump raw quest config"),
        ("questdiag","show quest diagnostics"),
    ],
    "sniper": [
        ("sniper on/off","toggle nitro gift sniper"),("logger on/off","toggle message logger"),
        ("readlog [n]","read last n log lines"),
    ],
    "ar": [
        ("ar add <trigger> | <response>","add auto-response"),
        ("ar remove <trigger>","remove auto-response"),("ar list","list all auto-responses"),
    ],
    "voice": [
        ("vcjoin <ch_id>","join a voice channel"),("vcleave","leave voice channel"),
        ("vcmute <user_id>","server mute user"),("vcunmute <user_id>","server unmute user"),
        ("vcdeafen <user_id>","server deafen user"),("vcundeafen <user_id>","server undeafen user"),
        ("vckick <user_id>","kick user from vc"),("vcmove <user> <ch_id>","move user to channel"),
        ("vcmoveall <ch1> <ch2>","move all users ch1 → ch2"),
        ("vcreconnect on/off/status","auto-rejoin vc on disconnect"),
        ("selfmute","toggle your own server mute"),
        ("selfdeaf","toggle your own server deafen"),
        ("selfstream","toggle your stream (go live)"),
        ("selfcamera","toggle your camera/video"),
        ("vcdiag","voice diagnostics dump"),
    ],
    "rpc": [
        ("rpc <1-6> <field> <value>","set a rich presence slot"),
        ("rpc <slot> name <text>","activity name"),
        ("rpc <slot> details <text>","details line"),
        ("rpc <slot> state <text>","state line"),
        ("rpc <slot> type <type>","playing/streaming/listening/watching/competing/purplestream"),
        ("rpc <slot> platform <preset>","xbox/ps/ps4/ps5/crunchyroll/youtube/twitch/vrchat/meta/roblox"),
        ("rpc <slot> large_image <url>","large image"),
        ("rpc <slot> small_image <url>","small image"),
        ("rpc <slot> timestamp <val>","3600 | 1:00:00 | clear"),
        ("rpc <slot> btn1 <label> <url>","first button"),
        ("rpc <slot> btn2 <label> <url>","second button"),
        ("rpc <slot> clear","wipe that slot"),
        ("rpc status","show all 6 slots"),
        ("rpc clearall","wipe every slot"),
        ("rpc1 <field> <value>","slot 1 shorthand"),
        ("rpc2 <field> <value>","slot 2 shorthand"),
        ("rpc3 <field> <value>","slot 3 shorthand"),
        ("rpc4 <field> <value>","slot 4 shorthand"),
        ("rpc5 <field> <value>","slot 5 shorthand"),
        ("rpc6 <field> <value>","slot 6 shorthand"),
        ("roblox <game> [- details] [slot]","quick roblox presence"),
        ("spotify <song - artist> [slot]","quick spotify presence"),
        ("youtube <video - channel> [slot]","quick youtube presence"),
        ("xbox <game - details> [slot]","quick xbox presence"),
        ("ps <game - details> [slot]","quick playstation presence"),
        ("ps4 <game - details> [slot]","quick ps4 presence"),
        ("crunchy <anime - ep> [slot]","quick crunchyroll presence"),
        ("vrchat <state - world> [slot]","quick vrchat presence"),
        ("meta <state - world> [slot] [image]","quick meta quest presence"),
        ("playing <text>","simple playing activity"),
        ("listening <text>","simple listening activity"),
        ("watching <text>","simple watching activity"),
        ("competing <text>","simple competing activity"),
        ("rpc_status","list every slot"),
        ("clear_multi_rpc","wipe all six slots"),
        ("aoff","clear current activity"),
        ("rstatus <a, b, c>","rotate custom status text"),
        ("remoji <a, b, c>","rotate custom status emoji"),
        ("stopstatus","stop status rotation"),
        ("stopemoji","stop emoji rotation"),
        ("rpcwatchdog","rpc watchdog status/stop/start"),
    ],
    "fun": [
        ("gayrate [user_id]","gay percentage"),("feed <user_id>","feed a user"),
        ("tickle <user_id>","tickle a user"),("slap <user_id>","slap a user"),
        ("hug <user_id>","hug a user"),("cuddle <user_id>","cuddle a user"),
        ("pat <user_id>","pat a user"),("kiss <user_id>","kiss a user"),
        ("poke <user_id>","poke a user"),("wink <user_id>","wink at a user"),
        ("smug <user_id>","smug at a user"),("boop <user_id>","boop a user"),
        ("nom <user_id>","nom a user"),("mimic <user_id>","mirror user's messages"),
        ("unmimic <user_id>","stop mimicking user"),("stopmimic","stop all mimics"),
        ("meme","random meme"),("joke","random joke"),
    ],
    "tools": [
        ("nitro","generate random nitro url"),("applybypass <invite>","bypass apply-to-join"),
        ("tokeninfo <token>","decode a discord token"),("calculate <expr>","evaluate math expression"),
        ("fact","random useless fact"),("fetchlyrics <artist - title>","fetch song lyrics"),
        ("robuxtax <amount>","roblox marketplace fee calc"),
        ("archivechannel [ch_id]","save channel messages to txt"),
    ],
    "host": [
        ("host add <token>","add a token to hosted list"),
        ("host list","list hosted tokens from DB"),
        ("host sessions","list live sessions with uptime"),
        ("host start <idx_or_token>","spawn a hosted client"),
        ("host stop <idx_or_uid>","close one hosted client"),
        ("host stopall","close every hosted client"),
        ("host all","spawn every token in the list"),
        ("host info <idx>","token preview + user info"),
        ("host say <idx> <msg>","send as a hosted account"),
        ("host broadcast <msg>","send as all hosted accounts"),
        ("host remove <idx_or_token>","remove from hosted list"),
        ("host clear","wipe hosted list + stop sessions"),
    ],
    "admin": [
        ("admin add <uid>","owner only — grant admin tier"),
        ("admin remove <uid>","owner only — revoke admin tier"),
        ("admin devadd <uid>","owner only — grant dev tier"),
        ("admin devremove <uid>","owner only — revoke dev tier"),
        ("admin list","list owner + admins + devs"),
        ("admin setowner <uid>","owner only — transfer ownership"),
    ],
    "lastfm": [
        ("lastfm set <user> [key]","link your last.fm account"),("lastfm np","now playing track"),
        ("lastfm recent [n]","last n scrobbles"),("lastfm topartists [w/m/y/all]","top artists"),
        ("lastfm toptracks [w/m/y/all]","top tracks"),("lastfm topalbums [w/m/y/all]","top albums"),
        ("lastfm stats","scrobble count & stats"),("lastfm compare <user>","taste compatibility"),
    ],
    "settings": [
        ("prefix <new>","change global command prefix"),
        ("serverprefix <p>","set a per-server prefix"),
        ("serverprefixclear","clear per-server prefix"),
        ("version","show mist version"),("reload","reload config from disk"),
        ("alias add <cmd> <alias>","add a custom alias"),("alias remove <alias>","remove an alias"),
        ("alias list","list all aliases"),
        ("cooldown set <cmd> <secs>","set command cooldown"),
        ("cooldown clear <cmd>","remove cooldown"),("cooldown list","list cooldowns"),
        ("profile save <name>","save current config as profile"),
        ("profile load <name>","load a saved profile"),("profile list","list saved profiles"),
        ("profile delete <name>","delete a profile"),
        ("config export","export full config to JSON"),("config import <path>","import config from JSON"),
        ("encrypt on/off","encrypt config at rest"),
        ("enable <cmd>","enable a disabled command"),("disable <cmd>","disable a command"),
        ("disabled","list disabled commands"),
    ],
    "guards": [
        ("blacklist add <uid>","add a user to the blacklist"),
        ("blacklist remove <uid>","remove from blacklist"),
        ("blacklist list","show blacklisted users"),
        ("blacklist clear","wipe the user blacklist"),
        ("whitelist add <uid>","allow a user (whitelist mode)"),
        ("whitelist remove <uid>","remove from whitelist"),
        ("whitelist list","show whitelisted users"),
        ("whitelist clear","disable whitelist mode"),
        ("serverblacklist add <cmd>","block a command server-wide"),
        ("serverblacklist remove <cmd>","unblock it"),
        ("serverblacklist list","list server-blocks"),
        ("serverblacklist clear","clear server-blocks"),
        ("channelblacklist add <cmd>","block a command in this channel"),
        ("channelblacklist remove <cmd>","unblock it"),
        ("channelblacklist list","list channel-blocks"),
        ("channelblacklist clear","clear channel-blocks"),
        ("rolerestrict add <cmd> <role_id>","restrict a cmd to roles"),
        ("rolerestrict remove <cmd> <role_id>","remove a restriction"),
        ("rolerestrict list","list role restrictions"),
        ("rolerestrict clear <cmd>","clear restrictions for a command"),
        ("guards status","show all guard state"),
        ("guards reset","wipe every guard"),
    ],
    "resilience": [
        ("autoreconnect on/off","auto-reconnect on disconnect"),
        ("autorestart on/off","auto-restart on crash"),
        ("sessionmon on/off","log gateway session events"),
        ("sessions","show recent session events"),
        ("sessions clear","clear session event log"),
        ("ratelimit on/off","track rate-limit hits"),
        ("ratelimits","show recent rate-limit events"),
        ("ratelimits clear","clear rate-limit log"),
        ("cache stats","show cache sizes"),
        ("cache clean","force a cache wipe"),
        ("cache auto on/off","toggle automatic cache cleanup"),
        ("queue on/off","toggle command queue mode"),
        ("queue workers <n>","set the number of queue workers"),
        ("queue status","show queue state"),
        ("health","one-line alive + latency check"),
        ("uptime","bot uptime summary"),
        ("latency [history [n]]","heartbeat latency samples"),
        ("incidents","disconnect / reconnect log"),
    ],
    "tasks": [
        ("task list","list background tasks"),
        ("task cancel <name>","cancel a task"),
        ("task register <name>","register a task by name"),
        ("task save","persist tasks to disk"),
        ("task load","load persisted tasks"),
        ("task clear","clear the persisted task file"),
    ],
    "triggers": [
        ("trigger message add <name> <contains> | <reply>","add message trigger"),
        ("trigger message remove <name>","remove a message trigger"),
        ("trigger message list","list message triggers"),
        ("trigger reaction add <name> <emoji> <mode> | <reply>","add reaction trigger"),
        ("trigger reaction remove <name>","remove a reaction trigger"),
        ("trigger reaction list","list reaction triggers"),
        ("trigger voice add <name> <join/leave/move> | <channel_id>","add voice trigger"),
        ("trigger voice remove <name>","remove a voice trigger"),
        ("trigger voice list","list voice triggers"),
        ("trigger member add <name> <join/leave>","add member trigger"),
        ("trigger member remove <name>","remove a member trigger"),
        ("trigger member list","list member triggers"),
        ("triggers status","show trigger fired counts"),
        ("triggers clear","wipe every trigger"),
    ],
    "developer": [
        ("eval <code>","owner+dev only — evaluate python code"),
        ("restart","restart the mist process"),
        ("reconnect","force gateway reconnect"),
        ("proxy set <url>","set HTTP/SOCKS proxy"),("proxy clear","clear proxy"),
        ("plugin load <path>","load a plugin from /plugins"),
        ("plugin unload <name>","unload a plugin"),
        ("plugin list","list loaded plugins"),
        ("session switch <idx>","switch active session token"),
        ("session list","list saved sessions"),
        ("setdev <uid>","owner only — grant developer tier"),
        ("devremove <uid>","owner only — revoke developer tier"),
        ("devlist","owner only — list developers"),
        ("setadmin <uid>","owner only — grant admin tier"),
        ("adminremove <uid>","owner only — revoke admin tier"),
        ("adminlist","owner only — list admins"),
        ("setowner <uid>","owner only — transfer ownership"),
        ("accesslist","owner only — show owner/admins/devs"),
    ],
    "server": [
        ("serverinfo","current server info"),("members [n]","list server members"),
        ("channels","list server channels"),("roles","list server roles"),
        ("ban <user_id> [reason]","ban a user"),("kick <user_id> [reason]","kick a user"),
        ("mute <user_id>","timeout a user (10 min)"),("unmute <user_id>","remove timeout"),
        ("createrole <name>","create a role"),("delrole <role_id>","delete a role"),
        ("createchannel <name>","create a text channel"),("deletechannel <ch_id>","delete a channel"),
        ("setnick <user> <nick>","set a member's nickname"),("topic <text>","set channel topic"),
        ("slowmode <seconds>","set channel slowmode"),
        ("servericon <url>","set server icon"),("serverbanner <url>","set server banner"),
        ("servername <name>","change server name"),
    ],
    "information": [
        ("userinfo [user_id]","discord user lookup"),("avatar [user_id]","get user avatar"),
        ("serverinfo","server details"),("channelinfo [ch_id]","channel details"),
        ("roleinfo <role_id>","role details"),
        ("checkname <username>","check if discord username is taken"),
        ("whois <user_id>","full user profile dump"),
    ],
    "groupchat": [
        ("gclist","list your group DMs"),("gccreate <user1> [user2...]","create a group DM"),
        ("gcrename <name>","rename current group DM"),("gcicon <url>","set group DM icon"),
        ("gcleave","leave current group DM"),("gcadd <user_id>","add user to group DM"),
        ("gcremove <user_id>","remove user from group DM"),
        ("agc on/off","anti gc-trap toggle"),("agc block on/off","auto-block gc-trap owner"),
        ("agc msg <text>","set leave message"),("agc name <text>","set gc rename on trap"),
        ("agc icon <url>","set gc icon on trap"),("agc webhook <url>","set webhook for trap alerts"),
        ("agc whitelist <user_id>","whitelist a user from agc"),
        ("agc unwhitelist <user_id>","remove from agc whitelist"),("agc wllist","show agc whitelist"),
    ],
    "utility": [
        ("uwuify <text>","uwuify text"),("owoify <text>","owoify text"),
        ("mock <text>","spongebob mock case"),("reverse <text>","reverse text"),
        ("aesthetic <text>","full-width text"),("clap <text>","👏 add 👏 claps"),
        ("animatetype <text>","type message character-by-character"),
        ("checkname <username>","check if username is available"),
        ("typing","start continuous typing indicator"),("typingstop","stop typing indicator"),
        ("afk [msg]","set AFK auto-reply"),("afkstop","disable AFK"),
        ("translate <lang> <text>","translate text"),("ghostping <user_id>","ghost ping a user"),
        ("pin <msg_id>","pin a message"),("unpin <msg_id>","unpin a message"),
        ("therapy","random therapy response"),("ragebait","random ragebait"),
        ("purgeall","delete all your msgs in channel"),
        ("firstmessage","get first message in channel"),
        ("autodelete <secs>","auto-delete next command"),("autodelete off","disable auto-delete"),
    ],
    "tracking": [
        ("track <user_id>","track a user's messages in channel"),
        ("untrack <user_id>","stop tracking user"),("tracklist","list tracked users"),
        ("history <user_id>","show tracked message history"),
    ],
    "downloads": [
        ("yt <url>","download youtube video"),("ytaudio <url>","download youtube audio"),
        ("tiktok <url>","download tiktok video"),("instagram <url>","download instagram post"),
    ],
    "social": [
        ("addfriend <user_id>","send friend request"),("removefriend <user_id>","remove friend"),
        ("block <user_id>","block user"),("unblock <user_id>","unblock user"),
        ("friends","list all friends"),("blocked","list blocked users"),
        ("pending","show pending friend requests"),("clearincoming","decline all incoming requests"),
        ("clearoutgoing","cancel all outgoing requests"),
        ("friendcount","friend / block / pending counts"),
        ("closedms","close all DM channels"),("readdms","mark all DMs as read"),
        ("note <user_id> <text>","set note on user"),("autoaddback on/off","auto-accept friend requests"),
    ],
    "auto": [
        ("giveaway on/off","auto-enter giveaways"),("nitrosniper on/off","auto-redeem nitro gift codes"),
        ("autoreact <emoji>","auto-react to your own messages"),
        ("autoreactstop","stop auto-react"),
        ("superreact <emoji>","continuous react to your own messages"),
        ("superreactstop","stop superreact"),
        ("multireact add <emoji>","add emoji to multi-react pool"),
        ("multireact remove <emoji>","remove emoji from pool"),
        ("multireact list","list pool"),("multireact on/off","toggle multi-react"),
        ("autoaddback on/off","auto-accept friend requests"),
        ("vsniper add <code> <gid>","add vanity url to watch list"),
        ("vsniper start/stop/list","vanity sniper control"),
        ("reactdiag","show autoreact/superreact state"),
    ],
    "spoofer": [
        ("platform [name]","show or set spoofed platform"),
        ("spoof <platform>","rewrite IDENTIFY payload + reconnect"),
        ("spoofer <platform>","alias for spoof"),
        ("spoof status","show spoofer state"),
        ("spoof reset","reset to desktop"),
        ("spoofstatus","show spoofer state"),
        ("spoofreset","reset to desktop"),
        ("vr","spoof as VR headset"),("console","spoof as console"),
        ("spooferdiag","spoofer diagnostics dump"),
    ],
    "profile": [
        ("setpfp <url>","set profile picture from url"),("setbio <text>","set profile bio"),
        ("setbanner <url>","set profile banner"),("myprofile","show your own profile info"),
        ("accountbackup","backup account to JSON"),
    ],
    "status": [
        ("setstatus <text>","set custom status text"),
        ("setstatus <emoji>, <text>","set status with emoji"),
        ("setstatus <:name:id>, <text>","set status with custom emoji"),
        ("clearstatus","clear your custom status"),
        ("stealstatus <user_id>","copy a user's custom status"),
        ("statushistory","show your recent status history"),
        ("schedule status <unix> <text>","schedule a status change"),
        ("schedule list","list scheduled statuses"),("schedule clear","clear scheduled statuses"),
    ],
    "mass": [
        ("massdm <msg>","DM everyone in a server"),("massdmfile <path> <msg>","DM a list of user IDs from a file"),
        ("massfriend <file>","send friend requests to user IDs"),
        ("massjoin <invite> <count>","join a server with alt tokens"),
        ("massleave <guild_id>","leave a server with alt tokens"),
        ("massrole <role_id> <user_ids...>","assign role to users"),
        ("massunrole <role_id> <user_ids...>","remove role from users"),
        ("massban <user_ids...>","ban a list of users"),
        ("masskick <user_ids...>","kick a list of users"),
        ("massch <name> <n>","create n text channels"),
        ("massvc <name> <n>","create n voice channels"),
        ("masscat <name> <n>","create n categories"),
        ("massrolecreate <name> <n>","create n roles"),
        ("massreact <emoji>","react to the last 10 messages"),
        ("massdelete <n>","delete your own last n messages"),
    ],
    "nuke": [
        ("nuke status","show nuke module state"),("nuke channels","delete every channel"),
        ("nuke roles","delete every deletable role"),("nuke emojis","delete every emoji"),
        ("nuke webhooks","delete every webhook"),
        ("nuke everything","delete channels + roles + emojis"),
        ("nuke restore <backup_file>","restore from a nuke backup"),("nukebackup","backup the current server structure"),
    ],
    "scrape": [
        ("scrape members","export every member of the current server"),
        ("scrape invites","list every active invite in the current server"),
        ("scrape channel <ch_id> [n]","export last n messages from a channel"),
        ("scrape server","full server structure dump (JSON)"),
        ("export members <guild_id>","export members of a server to CSV"),
        ("export messages <ch_id> [n]","export messages to JSON"),
        ("export invites <guild_id>","export invites for a server"),
        ("import members <path>","import member IDs from a file"),
    ],
    "webhooks": [
        ("webhook create <name>","create a webhook in this channel"),
        ("webhook delete <wh_id>","delete a webhook"),("webhook list","list webhooks in this channel"),
        ("webhook spam <url> <n> <msg>","spam a webhook n times"),
        ("webhook rename <wh_id> <name>","rename a webhook"),
        ("webhook emoji","list every emoji in the server"),
        ("webhook steal <emoji>","steal an emoji into this server"),
        ("webhook clear","delete every webhook in the server"),
    ],
    "automod": [
        ("automod on/off","toggle the automod watchdog"),("automod add <word>","add a banned word"),
        ("automod remove <word>","remove a banned word"),("automod list","list banned words"),
        ("automod action <action>","delete/kick/ban/timeout"),("automod logs <ch_id>","set automod log channel"),
        ("raidmode on/off","toggle raid-mode"),("raidmode threshold <n>","members/sec to trigger raidmode"),
        ("quarantine on/off","auto-quarantine new accounts"),("quarantine role <role_id>","quarantine role id"),
        ("quarantine age <days>","account age threshold (days)"),
        ("ticket setup <category_id>","set the ticket category id"),
        ("ticket close","close the current ticket channel"),
        ("verify setup <role_id>","set the verified role id"),
        ("verify button <label>","send a verify button in this channel"),
    ],
    "monitor": [
        ("monitor joins on/off","log member joins"),("monitor leaves on/off","log member leaves"),
        ("monitor roles on/off","log role changes"),("monitor nicks on/off","log nickname changes"),
        ("monitor invites on/off","log new invites"),
        ("monitor keywords <words...>","alert on keyword matches"),
        ("monitor keywordstop","clear keyword alerts"),("monitor logch <ch_id>","set monitor log channel"),
        ("monitor status","show monitor state"),
    ],
    "backup": [
        ("backup server","backup server structure"),("backup restore <file>","restore server from backup"),
        ("backup list","list local backups"),("backup sync <src> <dst>","sync two servers"),
        ("backup autosave on/off","auto backup every 30 min"),("backup config","backup the full config"),
    ],
    "perms": [
        ("perm add <cmd> <user_id>","allow a user to run a command"),
        ("perm remove <cmd> <user_id>","revoke user access"),
        ("perm block <cmd>","block a command server-wide"),
        ("perm unblock <cmd>","unblock a command"),("perm list","list permission overrides"),
        ("perm channel <ch_id> <cmd>","restrict a command to a channel"),
        ("perm server <guild_id> <cmd>","restrict a command to a server"),
        ("perm reset","wipe all permissions"),
    ],
    "scheduler": [
        ("schedule add <unix> <msg>","schedule a message"),
        ("schedule addrel <secs> <msg>","schedule after n seconds"),
        ("schedule addchan <ch_id> <unix> <msg>","schedule to a channel"),
        ("schedule list","list scheduled messages"),
        ("schedule remove <id>","remove a scheduled message"),
        ("schedule clear","clear all scheduled messages"),
        ("schedule status <unix> <text>","schedule a status change"),
    ],
    "db": [
        ("note add <user_id> <text>","add a note to the local DB"),
        ("note list <user_id>","list notes on a user"),
        ("note clear <user_id>","clear notes on a user"),
        ("history add <user_id> <text>","append to user history"),
        ("history list <user_id>","read user history"),
        ("stats","command usage statistics"),("stats clear","reset command stats"),
        ("db info","database stats"),("db vacuum","compact the database"),
    ],
    "interactions": [
        ("buttons on/off","toggle button interaction handler"),
        ("modals on/off","toggle modal interaction handler"),
        ("interact list","list pending interactions"),
        ("interact clear","clear pending interactions"),
    ],
}

def build_help_root(page=1):
    cats = list(HELP_DATA.keys())
    total = max(1, math.ceil(len(cats)/10))
    page = max(1, min(page, total))
    chunk = cats[(page-1)*10:(page-1)*10+10]
    desc = {
        "general":"utilities, platform & status","quests":"quest completer & orb badge",
        "sniper":"nitro sniper & logger","ar":"auto-responder","voice":"voice channel controls",
        "rpc":"rich presence — 6 slots, spotify, xbox, ps, vrchat, meta",
        "fun":"fun & roleplay","tools":"tools & generators","host":"multi-account hosting",
        "admin":"owner / admin management",
        "lastfm":"last.fm integration","settings":"prefix, aliases, cooldowns, profiles",
        "guards":"blacklists, whitelists, restrictions, per-cmd toggles",
        "resilience":"auto-reconnect, session log, rate limits, cache, queue",
        "tasks":"background task manager","triggers":"message / reaction / voice / member triggers",
        "developer":"dev tools, plugins, proxy, sessions, access",
        "server":"server management",
        "information":"user & server lookup","groupchat":"group dm & anti-gc",
        "utility":"text, afk, translate","tracking":"message & profile tracking",
        "downloads":"media downloader","social":"friends & social",
        "auto":"automation, snipers, superreact",
        "spoofer":"platform / device spoofing",
        "profile":"account profile","status":"custom status",
        "mass":"mass action tools","nuke":"destructive ops + backup","scrape":"scrape & export",
        "webhooks":"webhooks & emoji tools","automod":"automod, raid, quarantine, tickets, verify",
        "monitor":"event monitoring & alerts","backup":"server backup & restore",
        "perms":"per-command permissions","scheduler":"scheduled actions",
        "db":"local database & stats","interactions":"button & modal handling",
    }
    lines = [f"  {WHITE}> mist{RESET}  {DIM}v{VERSION}{RESET}", "", f"  {GREY}categories{RESET}", ""]
    for c in chunk:
        lines.append(f"  {CYAN}{c:<14}{RESET}  {DIM}{desc.get(c,'commands')}{RESET}")
    lines += ["", f"  {DIM}{PREFIX}help <category> [page]  •  {PREFIX}help <page> to flip{RESET}",
              f"  {DIM}page {page}/{total}  •  mist | ver {VERSION}{RESET}"]
    return _ansi_block(lines)

def build_help_section(cat, page=1):
    if cat not in HELP_DATA:
        return ui_err(f"unknown category: {cat}")
    rows = HELP_DATA[cat]
    total = max(1, math.ceil(len(rows)/PAGE_SIZE))
    page = max(1, min(page, total))
    chunk = rows[(page-1)*PAGE_SIZE:(page-1)*PAGE_SIZE+PAGE_SIZE]
    lines = [f"  {WHITE}> {cat}{RESET}", ""]
    for c, d in chunk:
        lines.append(f"  {GREY}├{RESET} {WHITE}{PREFIX}{c}{RESET}  {DIM}{d}{RESET}")
    lines += ["", f"  {DIM}page {page}/{total}  •  {PREFIX}h {cat} {(page%total)+1}{RESET}"]
    return _ansi_block(lines)

# ─────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────

# latency-optimised client — no guild chunking, socket-level tweaks
client = discord.Client(token=TOKEN, chunk_guilds_at_startup=False)
_MAIN_CLIENT = client

# ── inline state ──
AUTO_RESPONSES = {}
SNIPER_ENABLED = True
LOGGER_ENABLED = False
_mimic_dict = {}
_tracking = {}
_tracked_users = set()
_afk_msg = None
_afk_enabled = False
_typing_tasks = {}
_autoreact_emoji = None
_superreact_emoji = None
_multireact_pool = []
_multireact_enabled = False
_spam_tasks = {}
_snipe_cache = {}
_editsnipe_cache = {}
SNIPE_LIMIT = 20

_autoaddback = False
_giveaway_enabled = False
_nitrosniper_enabled = True
_autoclaim_enabled = False
_speak_lang = None
_vsniper_list = []
_vsniper_task = None
_aliases = {}
_cooldowns = {}
_cooldown_last = {}
_autodelete_secs = 0
_proxy = None
_plugins = {}
_sessions = []
_session_idx = 0

_server_prefixes = {}
_cmd_blacklist_server = {}
_cmd_blacklist_channel = {}
_user_blacklist = set()
_user_whitelist = set()
_role_restrict = {}
_cmd_disabled = set()
_perm_allow = {}
_perm_block = set()
_perm_channel = {}
_perm_server = {}

_reconnect_count = 0
_last_ready_ts = 0
_session_events = []
_auto_reconnect = True
_auto_restart = True
_rate_limit_events = []
_rate_limit_tracking = True
_cmd_queue = None
_queue_workers = 3
_queue_worker_tasks = []
_cache_auto = True

_managed_tasks = {}
_TASK_STORE = "database/tasks.json"
_TRIGGER_STORE = "database/triggers.json"
_triggers = {"message": [], "reaction": [], "voice": [], "member": []}
_trigger_fired_counts = defaultdict(int)

# latency samples
_latency_history = []

# ── host state ──
_host_sessions: list = []
_host_lock = None

HOSTED_TOKENS: list = []
_hosted_clients: list = []
_hosted_spawned = False

# ── automod / monitor / verify / quarantine ──
_monitor = {"joins": False, "leaves": False, "roles": False, "nicks": False,
            "invites": False, "log_ch": None, "keywords": []}
_invite_cache = {}
_automod = {"enabled": False, "words": [], "action": "delete", "log_ch": None}
_raidmode = {"enabled": False, "threshold": 5}
_quarantine = {"enabled": False, "role_id": None, "age_days": 7}
_ticket_cfg = {"category_id": None}
_verify_cfg = {"role_id": None}
_buttons_enabled = True
_modals_enabled = True
_pending_interactions = []

# ── scheduler / db ──
_scheduler = []
_db_path = "database/selfbot.db"
_db = None

# ── platform / hypesquad ──
PLATFORM_MAP = {
    "desktop":  "Windows",
    "web":      "Web",
    "mobile":   "Android",
    "ios":      "iOS",
    "android":  "Android",
    "embedded": "Embedded",
}
_current_platform = "desktop"

HOUSE_IDS = {"bravery": 1, "brilliance": 2, "balance": 3}
HOUSE_NAMES = {1: "Bravery", 2: "Brilliance", 3: "Balance"}

# ── crypto ──
try:
    from cryptography.fernet import Fernet
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

def _key_path(): return "config/.key"

def _get_key():
    if not _HAS_CRYPTO: return None
    if not os.path.exists(_key_path()):
        with open(_key_path(), "wb") as f: f.write(Fernet.generate_key())
    with open(_key_path(), "rb") as f: return f.read()

def encrypt_file(path):
    if not _HAS_CRYPTO: return False
    k = _get_key()
    with open(path, "rb") as f: data = f.read()
    with open(path, "wb") as f: f.write(Fernet(k).encrypt(data))
    return True

def decrypt_file(path):
    if not _HAS_CRYPTO: return False
    k = _get_key()
    with open(path, "rb") as f: data = f.read()
    with open(path, "wb") as f: f.write(Fernet(k).decrypt(data))
    return True

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

GIFT_RE = re.compile(r"(discord\.gift|discord\.com/gifts)/([a-zA-Z0-9]+)")

async def snipe_nitro(code, channel_id):
    try:
        s = _get_session()
        async with s.post(
            f"https://discord.com/api/v9/entitlements/gift-codes/{code}/redeem",
            headers={"Content-Type": "application/json"},
            json={"channel_id": str(channel_id)},
        ) as r:
            log_msg("SNIPER", f"{'✓ SNIPED' if r.status == 200 else '✗ miss'} {code} [{r.status}]")
    except Exception as e:
        log_msg("SNIPER", f"error: {e}")

def log_msg(tag, content):
    if not LOGGER_ENABLED: return
    line = f"[{datetime.now().strftime('%H:%M:%S')}] [{tag}] {content}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

async def translate_text(text, target_lang):
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client": "gtx", "sl": "auto", "tl": target_lang, "dt": "t", "q": text}
        s = _get_session()
        async with s.get(url, params=params) as r:
            d = await r.json()
            return "".join(p[0] for p in d[0] if p[0])
    except Exception as e:
        return f"error: {e}"

NEKO_ACTIONS = {"feed", "tickle", "slap", "hug", "cuddle", "pat", "kiss",
                "poke", "wink", "smug", "boop", "nom", "wave", "highfive",
                "bite", "blush", "dance", "happy", "cringe"}

async def neko_gif(action):
    endpoint = action if action in NEKO_ACTIONS else "hug"
    try:
        s = _get_session()
        async with s.get(f"https://nekos.life/api/v2/img/{endpoint}") as r:
            if r.status == 200:
                return (await r.json()).get("url")
    except Exception:
        pass
    return None

async def set_hypesquad(house_id: int):
    try:
        s = _get_session()
        async with s.post("https://discord.com/api/v9/hypesquad/online",
                          headers={"Content-Type": "application/json"},
                          json={"house_id": house_id}) as r:
            return r.status in (200, 204), await r.text()
    except Exception as e:
        return False, str(e)

async def clear_hypesquad():
    try:
        s = _get_session()
        async with s.delete("https://discord.com/api/v9/hypesquad/online") as r:
            return r.status in (200, 204)
    except Exception:
        return False

def _perm_check(cmd, message):
    for hc in _hosted_clients:
        try:
            if hc.user and hc.user.id == message.author.id:
                return True
        except Exception:
            pass
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

def db_stats_inc(cmd):
    try:
        _db.execute("INSERT INTO stats VALUES (?,1) ON CONFLICT(cmd) DO UPDATE SET count=count+1", (cmd,))
        _db.commit()
    except Exception:
        pass

def _db_open():
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

_db_open()

def tasks_save():
    data = {name: {"created": str(t)} for name, t in _managed_tasks.items() if not t.done()}
    try:
        with open(_TASK_STORE, "w") as f: json.dump(data, f, indent=2)
    except Exception: pass

def tasks_load():
    if not os.path.exists(_TASK_STORE): return {}
    try:
        with open(_TASK_STORE) as f: return json.load(f)
    except Exception: return {}

def triggers_save():
    try:
        with open(_TRIGGER_STORE, "w") as f: json.dump(_triggers, f, indent=2)
    except Exception: pass

def triggers_load():
    global _triggers
    if os.path.exists(_TRIGGER_STORE):
        try:
            with open(_TRIGGER_STORE) as f: _triggers = json.load(f)
        except Exception: pass

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

async def _run_scheduled(job):
    action = job["action"]; payload = job.get("payload", {})
    if action == "message":
        ch = client.get_channel(int(payload["ch_id"]))
        if ch: await ch.send(payload["msg"])
    elif action == "status":
        await client.change_presence(activity=discord.CustomActivity(name=payload["text"]))

async def _scheduler_loop():
    while True:
        try:
            now = time.time()
            for job in list(_scheduler):
                if job["when"] <= now:
                    try: await _run_scheduled(job)
                    except Exception as e: print(f"[scheduler] {e}")
                    _scheduler.remove(job)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[scheduler loop] {e}")
            await asyncio.sleep(5)

async def _cache_cleanup_loop():
    while True:
        try:
            if _cache_auto:
                now = time.time()
                for cache in (_snipe_cache, _editsnipe_cache):
                    for cid in list(cache.keys()):
                        cache[cid] = [e for e in cache[cid] if now - float(e.get("ts", now)) < 3600]
                        if not cache[cid]: cache.pop(cid, None)
                for cid in list(_typing_tasks.keys()):
                    if _typing_tasks[cid].done(): _typing_tasks.pop(cid, None)
                for uid in list(_tracking.keys()):
                    if len(_tracking[uid]) > 200: _tracking[uid] = _tracking[uid][-200:]
                if len(_session_events) > 200: del _session_events[:-200]
                if len(_rate_limit_events) > 200: del _rate_limit_events[:-200]
                if len(_latency_history) > 500: del _latency_history[:-500]
            await asyncio.sleep(600)
        except Exception as e:
            print(f"[cache] {e}")
            await asyncio.sleep(60)

async def _heartbeat_probe_loop():
    """Sample client.latency every 60s so $latency history has data even when idle."""
    while True:
        try:
            await asyncio.sleep(60)
            lat = getattr(client, "latency", None)
            if lat is not None:
                _latency_history.append({"ts": time.time(), "ms": round(lat * 1000, 1)})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[heartbeat probe] {e}")

async def _queue_worker(name):
    while True:
        try:
            item = await _cmd_queue.get()
            if item is None:
                await asyncio.sleep(0.05); continue
            try: await item
            except Exception as e: print(f"[queue:{name}] {e}")
            await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[queue worker] {e}")
            await asyncio.sleep(1)

def task_register(name, coro):
    if name in _managed_tasks and not _managed_tasks[name].done():
        return False
    _managed_tasks[name] = asyncio.create_task(coro)
    tasks_save()
    return True

def task_cancel(name):
    t = _managed_tasks.get(name)
    if t and not t.done():
        t.cancel()
        _managed_tasks.pop(name, None)
        tasks_save()
        return True
    return False

# ─────────────────────────────────────────────
# COG BOOT
# ─────────────────────────────────────────────

COG_MODULES = [
    ("cogs.quests", "QuestsCog"),
    ("cogs.host", "HostCog"),
    ("cogs.voice", "VoiceCog"),
    ("cogs.mass", "MassCog"),
    ("cogs.nuke", "NukeCog"),
    ("cogs.scrape", "ScrapeCog"),
    ("cogs.webhooks", "WebhooksCog"),
    ("cogs.automod", "AutomodCog"),
    ("cogs.monitor", "MonitorCog"),
    ("cogs.backup", "BackupCog"),
    ("cogs.perms", "PermsCog"),
    ("cogs.scheduler", "SchedulerCog"),
    ("cogs.db", "DbCog"),
    ("cogs.lastfm", "LastfmCog"),
    ("cogs.social", "SocialCog"),
    ("cogs.status", "StatusCog"),
    ("cogs.agc", "AgcCog"),
    ("cogs.gc", "GroupChatCog"),
    ("cogs.triggers", "TriggersCog"),
    ("cogs.tasks", "TasksCog"),
    ("cogs.guards", "GuardsCog"),
    ("cogs.resilience", "ResilienceCog"),
    ("cogs.settings", "SettingsCog"),
    ("cogs.general", "GeneralCog"),
    ("cogs.fun", "FunCog"),
    ("cogs.tools", "ToolsCog"),
    ("cogs.utility", "UtilityCog"),
    ("cogs.tracking", "TrackingCog"),
    ("cogs.downloads", "DownloadsCog"),
    ("cogs.auto", "AutoCog"),
    ("cogs.profile", "ProfileCog"),
    ("cogs.developer", "DeveloperCog"),
    ("cogs.server", "ServerCog"),
    ("cogs.information", "InformationCog"),
    ("cogs.interactions", "InteractionsCog"),
    ("cogs.spoofer", "SpooferCog"),
    ("cogs.rpc_adapter", "RpcAdapterCog"),
]

_COG_REGISTRY = {}
_COG_INSTANCES = []
_COGS_BOOTED = False

async def _boot_cogs():
    global _COG_REGISTRY, _COG_INSTANCES, _COGS_BOOTED
    if _COGS_BOOTED:
        return
    _COGS_BOOTED = True
    try:
        from cogs import state as cstate

        cstate.CLIENT = client
        cstate.MAIN_CLIENT = _MAIN_CLIENT
        cstate.TOKEN = TOKEN
        cstate.PREFIX = PREFIX
        cstate.USER_AGENT = USER_AGENT
        cstate.VERSION = VERSION
        cstate.HAS_HCAPTCHA = HAS_HCAPTCHA
        cstate.load_config = load_config
        cstate.save_config = save_config
        cstate.log_msg = log_msg
        cstate.db_inc_stat = db_stats_inc
        cstate.HOSTED_DISPATCH = _dispatch_message
        cstate.async_hosted_token_add = async_hosted_token_add
        cstate.async_hosted_token_remove = async_hosted_token_remove
        cstate.neko_gif = neko_gif
        cstate.translate_text = translate_text
        cstate.set_hypesquad = set_hypesquad
        cstate.clear_hypesquad = clear_hypesquad
        cstate.encrypt_file = encrypt_file
        cstate.decrypt_file = decrypt_file
        cstate._has_crypto = _HAS_CRYPTO
        cstate._HAS_CRYPTO = _HAS_CRYPTO

        # NEW — expose the shared session builder so cogs stop opening
        # fresh ClientSessions on every request
        cstate._get_session = _get_session

        cstate.HOSTED_TOKENS = HOSTED_TOKENS
        cstate._host_sessions = _host_sessions
        cstate._hosted_clients = _hosted_clients
        cstate._monitor = _monitor
        cstate._automod = _automod
        cstate._raidmode = _raidmode
        cstate._quarantine = _quarantine
        cstate._ticket_cfg = _ticket_cfg
        cstate._verify_cfg = _verify_cfg
        cstate._buttons_enabled = _buttons_enabled
        cstate._modals_enabled = _modals_enabled
        cstate._pending_interactions = _pending_interactions
        cstate._scheduler = _scheduler
        cstate._db = _db
        cstate._db_path = _db_path
        cstate._managed_tasks = _managed_tasks
        cstate._TASK_STORE = _TASK_STORE
        cstate._TRIGGER_STORE = _TRIGGER_STORE
        cstate._triggers = _triggers
        cstate._trigger_fired_counts = _trigger_fired_counts
        cstate._server_prefixes = _server_prefixes
        cstate._aliases = _aliases
        cstate._cooldowns = _cooldowns
        cstate._cooldown_last = _cooldown_last
        cstate._reconnect_count = _reconnect_count
        cstate._session_events = _session_events
        cstate._rate_limit_events = _rate_limit_events
        cstate._snipe_cache = _snipe_cache
        cstate._editsnipe_cache = _editsnipe_cache
        cstate._typing_tasks = _typing_tasks
        cstate._tracking = _tracking
        cstate._tracked_users = _tracked_users
        cstate._cache_auto = _cache_auto
        cstate._cmd_queue = _cmd_queue
        cstate._queue_workers = _queue_workers
        cstate._spam_tasks = _spam_tasks
        cstate.AUTO_RESPONSES = AUTO_RESPONSES
        cstate._mimic_dict = _mimic_dict
        cstate._afk_enabled = _afk_enabled
        cstate._afk_msg = _afk_msg
        cstate._autodelete_secs = _autodelete_secs
        cstate._autoreact_emoji = _autoreact_emoji
        cstate._superreact_emoji = _superreact_emoji
        cstate._multireact_pool = _multireact_pool
        cstate._multireact_enabled = _multireact_enabled
        cstate._giveaway_enabled = _giveaway_enabled
        cstate._nitrosniper_enabled = _nitrosniper_enabled
        cstate._vsniper_list = _vsniper_list
        cstate._vsniper_task = _vsniper_task
        cstate._speak_lang = _speak_lang
        cstate._proxy = _proxy
        cstate._plugins = _plugins
        cstate._sessions = _sessions
        cstate._session_idx = _session_idx
        cstate.SNIPER_ENABLED = SNIPER_ENABLED
        cstate.LOGGER_ENABLED = LOGGER_ENABLED
        cstate._current_platform = _current_platform
        cstate._latency_history = _latency_history
        cstate._last_ready_ts = _last_ready_ts

        # access control (shared by reference)
        cstate.OWNER_ID           = OWNER_ID
        cstate._admins            = _admins
        cstate._devs              = _devs
        cstate.ADMIN_COMMANDS     = ADMIN_COMMANDS
        cstate.DEVELOPER_COMMANDS = DEVELOPER_COMMANDS
        cstate.OWNER_COMMANDS     = OWNER_COMMANDS
        cstate.access_save        = access_save
        cstate.access_load        = access_load
        cstate._access_level      = _access_level
        cstate._access_ok         = _access_ok
        cstate._current_owner     = _current_owner

        for mod_name, cls_name in COG_MODULES:
            try:
                mod = importlib.import_module(mod_name)
            except Exception as e:
                print(f"[cogs] SKIP {mod_name} — import failed: {type(e).__name__}: {e}")
                continue
            cls = getattr(mod, cls_name, None)
            if cls is None:
                print(f"[cogs] SKIP {mod_name} — class {cls_name} not found")
                continue
            try:
                inst = cls()
            except Exception as e:
                print(f"[cogs] SKIP {mod_name}.{cls_name} — init failed: {e}")
                continue
            _COG_INSTANCES.append(inst)
            for c in getattr(inst, "COMMANDS", set()):
                _COG_REGISTRY[c] = (inst, c)
            if hasattr(inst, "register"):
                try:
                    inst.register(client)
                except Exception as e:
                    print(f"[cogs] {cls_name} event register failed: {e}")

        print(f"[cogs] loaded {len(_COG_INSTANCES)} cogs, {len(_COG_REGISTRY)} commands registered")
        if not _COG_REGISTRY:
            print("[cogs] WARNING: no commands registered — check cogs/ folder is present and modules import cleanly")
    except Exception as e:
        print(f"[cogs] fatal boot error: {e}")
        traceback.print_exc()
        _COG_REGISTRY = {}
        _COG_INSTANCES = []

# ─────────────────────────────────────────────
# EVENTS
# ─────────────────────────────────────────────

@client.event
async def on_ready():
    global _last_ready_ts, _cmd_queue, _queue_worker_tasks, _host_lock, _COGS_BOOTED
    if _host_lock is None:
        _host_lock = asyncio.Lock()
    _last_ready_ts = time.time()
    _session_events.append({"ts": _last_ready_ts, "event": "ready", "user": str(client.user)})

    # NEW — shared session + TCP_NODELAY on the gateway socket
    try:
        _get_session()
        print("[turbo] shared aiohttp session warm")
    except Exception as e:
        print(f"[turbo] session init failed: {e}")

    try:
        gw = getattr(client, "_gateway", None)
        ws = getattr(gw, "_ws", None) if gw else None
        sock = getattr(ws, "_sock", None) or getattr(ws, "sock", None)
        if sock is not None:
            _tcp_nodelay(sock)
            print("[turbo] TCP_NODELAY applied to gateway socket")
    except Exception as e:
        print(f"[turbo] tcp_nodelay skipped: {e}")

    is_main = True
    idx = "main"
    n_guilds = len(getattr(client._state, "_guilds", {}) or {})
    print(f"[{idx}] ✓ {client.user} ({client.user.id}) | prefix: {PREFIX} | servers: {n_guilds}")

    global HOSTED_TOKENS
    try:
        HOSTED_TOKENS = await async_hosted_tokens_get()
        print(f"[host] loaded {len(HOSTED_TOKENS)} tokens")
    except Exception as e:
        print(f"[host] load failed: {e} — falling back to empty")
        HOSTED_TOKENS = []

    cfg = load_config()
    if cfg.get("autoquest_enabled"):
        try:
            from cogs.quests import autoquest_run
            asyncio.create_task(autoquest_run(TOKEN))
        except Exception as e:
            print(f"[autoquest boot] {e}")

    sched_load()
    triggers_load()
    tasks_load()

    if is_main and not _COGS_BOOTED:
        await _boot_cogs()

    if is_main and not globals().get("_ipc_initialized"):
        print("[ipc] initializing global state...")
        globals()["_ipc_initialized"] = True
        globals()["_uptime_start"] = time.time()
        globals()["_latency"] = 0
        globals()["_platform"] = "desktop"
        globals()["_modules"] = {
            "afk": False, "spotify_sync": False, "lyrics": False,
            "autoresponse": False, "stealthy": False, "gift_sniper": True,
        }
        globals()["_ar_responses"] = {}
        globals()["_quests"] = []
        globals()["_sniper_enabled"] = True
        globals()["_logger_enabled"] = False
        globals()["_autoquest_enabled"] = cfg.get("autoquest_enabled", False)
        globals()["_custom_status"] = None

    if is_main and not globals().get("_ipc_server_started"):
        globals()["_ipc_server_started"] = True
        try:
            start_ipc_server(globals())
            if HAS_IPC:
                print("[ipc] ✓ server started at http://127.0.0.1:6969")
            else:
                print("[ipc] ✗ selfbot_ipc.py not found — dashboard will be offline")
        except Exception as e:
            print(f"[ipc] ✗ failed to start: {e}")

    if not any("scheduler" in str(t) for t in asyncio.all_tasks()):
        task_register("scheduler", _scheduler_loop())
    if not any("cache_cleanup" in str(t) for t in asyncio.all_tasks()):
        task_register("cache_cleanup", _cache_cleanup_loop())
    if not any("heartbeat_probe" in str(t) for t in asyncio.all_tasks()):
        task_register("heartbeat_probe", _heartbeat_probe_loop())
    if cfg.get("autoclaim_enabled") and not any("autoclaim" in str(t) for t in asyncio.all_tasks()):
        try:
            from cogs.quests import autoclaim_loop
            task_register("autoclaim_loop", autoclaim_loop())
        except Exception as e:
            print(f"[autoclaim boot] {e}")

    if _cmd_queue is None:
        _cmd_queue = asyncio.Queue()
        for i in range(_queue_workers):
            _queue_worker_tasks.append(asyncio.create_task(_queue_worker(f"w{i}")))

    try:
        for g in (client._state._guilds.values() if hasattr(client, "_state") else []):
            if not _monitor["invites"]:
                continue
            try:
                _invite_cache[g.id] = await g.invites()
            except Exception:
                pass
    except Exception:
        pass

    try:
        sync_local_to_supabase()
    except Exception as e:
        print(f"[db] sync error: {e}")

@client.event
async def on_disconnect():
    global _reconnect_count
    _reconnect_count += 1
    _session_events.append({"ts": time.time(), "event": "disconnect", "count": _reconnect_count})
    if _auto_reconnect:
        print(f"[ws] disconnected — auto-reconnect attempt #{_reconnect_count}")

@client.event
async def on_resumed():
    _session_events.append({"ts": time.time(), "event": "resumed"})

# ─────────────────────────────────────────────
# DISPATCHER
# ─────────────────────────────────────────────

async def _dispatch_message(_client, message):
    client = _client

    global PREFIX, _cfg
    global SNIPER_ENABLED, LOGGER_ENABLED, _afk_enabled, _afk_msg
    global _autoreact_emoji, _superreact_emoji, _autoaddback, _current_platform
    global _autoclaim_enabled, _speak_lang
    global _giveaway_enabled, _nitrosniper_enabled
    global _vsniper_task
    global _multireact_enabled, _multireact_pool
    global _autodelete_secs, _proxy, _session_idx
    global _rate_limit_tracking, _cache_auto
    global _queue_enabled, _queue_workers
    global _scheduler
    global _reconnect_count, _auto_reconnect, _auto_restart
    global _buttons_enabled, _modals_enabled
    global _triggers, _trigger_fired_counts
    global _server_prefixes, _cmd_blacklist_server, _cmd_blacklist_channel
    global _user_blacklist, _user_whitelist, _role_restrict, _cmd_disabled
    global _managed_tasks
    global _latency_history

    # ── PRE-HOOKS ──

    if LOGGER_ENABLED and message.guild:
        try:
            log_msg("MSG", f"{message.guild.name}/#{getattr(message.channel,'name','?')} | "
                            f"{message.author}: {message.content[:100]}")
        except Exception:
            pass

    for t in _triggers.get("message", []):
        try:
            ch_filter = t.get("channels", [])
            if ch_filter and str(message.channel.id) not in ch_filter:
                continue
            if t.get("contains") and t["contains"].lower() in (message.content or "").lower():
                _trigger_fired_counts[t["name"]] += 1
                await message.channel.send(t.get("reply") or "")
        except Exception:
            pass

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
        try:
            await message.reply(_afk_msg or "I'm AFK right now.", mention_author=False)
        except Exception:
            pass

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
        _tracking.setdefault(message.author.id, []).append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "content": message.content,
            "channel": getattr(message.channel, "name", str(message.channel.id)),
            "ts": time.time(),
        })
        if len(_tracking[message.author.id]) > 200:
            _tracking[message.author.id] = _tracking[message.author.id][-200:]

    if _automod["enabled"] and message.guild and message.author.id != client.user.id:
        body = (message.content or "").lower()
        for w in _automod["words"]:
            if w.lower() in body:
                try: await message.delete()
                except Exception: pass
                try:
                    if _automod["action"] == "kick":
                        await message.author.kick(reason="automod")
                    elif _automod["action"] == "ban":
                        await message.guild.ban(message.author, reason="automod")
                    elif _automod["action"] == "timeout":
                        until = discord.utils.utcnow() + timedelta(minutes=10)
                        await message.author.timeout(until)
                except Exception:
                    pass
                break

    if _monitor["keywords"] and message.guild and message.author.id != client.user.id:
        body = (message.content or "").lower()
        for kw in _monitor["keywords"]:
            if kw.lower() in body:
                if _monitor["log_ch"]:
                    ch = client.get_channel(int(_monitor["log_ch"]))
                    if ch:
                        try:
                            await ch.send(ui_box("keyword", [
                                f"{message.author} said `{kw}` in {message.channel.mention if hasattr(message.channel,'mention') else message.channel.id}"]))
                        except Exception:
                            pass
                break

    if message.author.id == client.user.id and _speak_lang and not message.content.startswith(PREFIX):
        try:
            translated = await translate_text(message.content, _speak_lang)
            if translated and translated != message.content:
                await asyncio.sleep(0.3)
                await message.edit(content=translated)
        except Exception:
            pass

    # ── AUTO-REACT (concurrent) ──
    if message.author.id == client.user.id and not message.content.startswith(PREFIX):
        try:
            react_tasks = []
            if _autoreact_emoji:
                react_tasks.append(message.add_reaction(_autoreact_emoji))
            if _multireact_enabled and _multireact_pool:
                react_tasks.extend(message.add_reaction(e) for e in _multireact_pool)
            if react_tasks:
                await asyncio.gather(*react_tasks, return_exceptions=True)
        except Exception:
            pass

    # ── SUPERREACT (own messages) ──
    if (_superreact_emoji
            and message.author.id == client.user.id
            and message.content
            and not message.content.startswith(PREFIX)):
        try:
            await message.add_reaction(_superreact_emoji)
        except Exception:
            pass

    # ── COMMAND GATE ──

    if message.author.id != client.user.id:
        return
    if not message.content:
        return

    effective_prefix = PREFIX
    if message.guild and str(message.guild.id) in _server_prefixes:
        effective_prefix = _server_prefixes[str(message.guild.id)]

    if not message.content.startswith(effective_prefix):
        return

    raw = message.content[len(effective_prefix):]
    args = raw.split()
    cmd = args[0].lower() if args else ""
    if cmd in _aliases:
        cmd = _aliases[cmd]

    if cmd in _cooldowns:
        key = (message.author.id, cmd)
        last = _cooldown_last.get(key, 0)
        if time.time() - last < _cooldowns[cmd]:
            return
        _cooldown_last[key] = time.time()

    # sample latency on every command
    try:
        lat = getattr(client, "latency", None)
        if lat is not None:
            _latency_history.append({"ts": time.time(), "ms": round(lat * 1000, 1)})
            if len(_latency_history) > 500: del _latency_history[:-500]
    except Exception:
        pass

    if not _perm_check(cmd, message):
        return

    # ── ACCESS GATE ──
    if not _access_ok(message.author.id, cmd):
        lvl = _access_level(message.author.id)
        need = "owner"
        if cmd in ADMIN_COMMANDS:      need = "admin"
        elif cmd in DEVELOPER_COMMANDS: need = "dev"
        try:
            await message.edit(content=ui_err(
                f"`{cmd}` requires {need} access — your tier: {lvl}"))
        except Exception:
            pass
        return

    db_stats_inc(cmd)

    # ── INLINE LATENCY / HEALTH / UPTIME — no cog hop, fastest reply ──
    if cmd == "health":
        cogs_n = len(_COG_INSTANCES)
        live_tasks = sum(1 for t in asyncio.all_tasks() if not t.done())
        gw = getattr(client, "_gateway", None)
        gw_ok = bool(getattr(gw, "is_connected", False)) if gw else False
        lat = getattr(client, "latency", 0) or 0
        await message.edit(content=ui_box("health", [
            f"  {DIM}user{S.RESET}      {client.user}",
            f"  {DIM}gateway{S.RESET}   {'ok' if gw_ok else 'DOWN'}",
            f"  {DIM}latency{S.RESET}   {round(lat*1000,1)}ms",
            f"  {DIM}cogs{S.RESET}      {cogs_n}",
            f"  {DIM}tasks{S.RESET}     {live_tasks}",
            f"  {DIM}disconnects{S.RESET} {_reconnect_count}",
            f"  {DIM}uptime{S.RESET}    {int(time.time() - _last_ready_ts)}s",
        ]))
        return

    if cmd == "uptime":
        up = int(time.time() - _last_ready_ts)
        h, rem = divmod(up, 3600); m, s = divmod(rem, 60)
        await message.edit(content=ui_box("uptime", [
            f"  {DIM}up{S.RESET}          {h}h {m}m {s}s",
            f"  {DIM}disconnects{S.RESET} {_reconnect_count}",
            f"  {DIM}since ready{S.RESET} {datetime.fromtimestamp(_last_ready_ts).strftime('%Y-%m-%d %H:%M:%S')}",
        ]))
        return

    if cmd == "latency":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "history":
            n = int(args[2]) if len(args) > 2 and args[2].isdigit() else 20
            samples = _latency_history[-n:]
            if not samples:
                return await message.edit(content=ui_info("no samples yet"))
            vals = [s["ms"] for s in samples]
            avg = sum(vals) / len(vals)
            lo, hi = min(vals), max(vals)
            await message.edit(content=ui_box("latency history", [
                f"  {DIM}samples{S.RESET}  {len(samples)}",
                f"  {DIM}avg{S.RESET}      {avg:.1f}ms",
                f"  {DIM}min{S.RESET}      {lo:.1f}ms",
                f"  {DIM}max{S.RESET}      {hi:.1f}ms",
                f"  {DIM}current{S.RESET}  {round(getattr(client, 'latency', 0)*1000, 1)}ms",
            ]))
            return
        cur = round(getattr(client, "latency", 0) * 1000, 1)
        return await message.edit(content=ui_ok(f"latency: {cur}ms"))

    if cmd == "incidents":
        rows = []
        for e in _session_events[-40:]:
            if e.get("event") in ("disconnect", "resumed", "ready"):
                ts = datetime.fromtimestamp(e["ts"]).strftime("%H:%M:%S")
                extra = f" #{e.get('count')}" if "count" in e else ""
                rows.append(f"  {GREY}{ts}{RESET}  {e['event']}{extra}")
        await message.edit(content=_paginate("incidents", "session events", rows)
                           if rows else ui_info("none"))
        return

    # ── COG ROUTE ──
    if cmd in _COG_REGISTRY:
        cog, _ = _COG_REGISTRY[cmd]
        try:
            await cog.handle(message, cmd, args)
        except Exception as e:
            print(f"[cog:{cmd}] handler error: {e}")
            traceback.print_exc()
            try:
                await message.channel.send(ui_err(f"`{cmd}` errored — check console"))
            except Exception:
                pass
        return

    # ── INLINE FALLTHROUGH (help only) ──
    if cmd in ("help", "h"):
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub.isdigit():
            await message.channel.send(build_help_root(int(sub)))
            return
        if sub:
            page = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1
            await message.channel.send(build_help_section(sub, page))
            return
        await message.channel.send(build_help_root(1))
        return


@client.event
async def on_message(message):
    await _dispatch_message(client, message)

# ─────────────────────────────────────────────
# OTHER EVENTS
# ─────────────────────────────────────────────

@client.event
async def on_message_delete(message):
    if message.author.id == client.user.id: return
    cid = message.channel_id
    _snipe_cache.setdefault(cid, [])
    _snipe_cache[cid].append({
        "author": str(message.author), "author_id": message.author.id,
        "content": message.content or "",
        "attachments": [a.get("url") for a in (message.attachments or [])],
        "time": datetime.now().strftime("%H:%M:%S"), "ts": time.time(),
    })
    if len(_snipe_cache[cid]) > SNIPE_LIMIT:
        _snipe_cache[cid] = _snipe_cache[cid][-SNIPE_LIMIT:]
    if LOGGER_ENABLED:
        log_msg("DEL", f"{message.author}: {message.content[:100]}")

@client.event
async def on_message_edit(before, after):
    if before.author.id == client.user.id: return
    if before.content == after.content: return
    cid = before.channel_id
    _editsnipe_cache.setdefault(cid, [])
    _editsnipe_cache[cid].append({
        "author": str(before.author), "author_id": before.author.id,
        "before": before.content or "", "after": after.content or "",
        "time": datetime.now().strftime("%H:%M:%S"), "ts": time.time(),
    })
    if len(_editsnipe_cache[cid]) > SNIPE_LIMIT:
        _editsnipe_cache[cid] = _editsnipe_cache[cid][-SNIPE_LIMIT:]
    if LOGGER_ENABLED:
        log_msg("EDIT", f"{before.author}: '{before.content[:60]}' → '{after.content[:60]}'")

@client.event
async def on_member_update(before, after):
    if _monitor["roles"] and _monitor["log_ch"]:
        ch = client.get_channel(int(_monitor["log_ch"]))
        if ch:
            try: await ch.send(ui_box("roles", [f"{after} updated"]))
            except Exception: pass
    if _monitor["nicks"] and _monitor["log_ch"]:
        ch = client.get_channel(int(_monitor["log_ch"]))
        if ch:
            try: await ch.send(ui_box("nick", [f"{after} updated"]))
            except Exception: pass

# ─────────────────────────────────────────────
# SIGNAL HANDLING + RUN
# ─────────────────────────────────────────────

def _install_signal_handlers():
    def _sig(sig, frame):
        print(f"[signal] {sig} received — exiting for platform restart")
        os._exit(0)
    try:
        signal.signal(signal.SIGINT, _sig)
        signal.signal(signal.SIGTERM, _sig)
    except Exception:
        pass

_install_signal_handlers()

print(f"[Mist] starting — prefix: '{PREFIX}' — v{VERSION}")
try:
    client.run()
except Exception as e:
    print(f"[FATAL] run failed: {type(e).__name__}: {e}")
    sys.exit(1)
