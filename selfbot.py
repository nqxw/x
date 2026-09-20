# selfbot.py | Python 3.10+ | discord.py-self + aiohttp
# sy's selfbot — v2.2.6

import discord
import asyncio
import aiohttp
import json
import os
import sys
import time
import re
import base64
import io
import math
import random
import string
import hashlib
import sqlite3
import csv
import threading
import queue
import traceback
import signal
import configparser
import datetime as dt
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

# ─────────────────────────────────────────────
# OPTIONAL INTEGRATIONS
# ─────────────────────────────────────────────

HAS_IPC = False
HAS_DB = False

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

print(f"[selfbot] token: {TOKEN[:10]}...{TOKEN[-5:] if len(TOKEN) > 15 else ''}")

if not TOKEN or TOKEN in ("YOUR_TOKEN_HERE", "", "None"):
    print("[FATAL] No token. Set TOKEN env var or config.json")
    sys.exit(1)

PREFIX = os.environ.get("PREFIX") or _cfg.get("prefix", ".")
VERSION = "2.2.6"
LOG_FILE = "message_log.txt"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) discord/1.0.9044 Chrome/120.0.6099.291 "
              "Electron/28.2.10 Safari/537.36")

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
        ("autoclaim on/off","auto-claim completed quests"),("orbbadge","claim orb badge"),
        ("captcha set <key>","set 2captcha api key"),
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
        ("vcjoin [ch_id]","join a voice channel"),("vcleave","leave voice channel"),
        ("vcmute <user_id>","server mute user"),("vcunmute <user_id>","server unmute user"),
        ("vcdeafen <user_id>","server deafen user"),("vcundeafen <user_id>","server undeafen user"),
        ("vckick <user_id>","kick user from vc"),("vcmove <user> <ch_id>","move user to channel"),
        ("vcmoveall <ch1> <ch2>","move all users ch1 → ch2"),
        ("selfmute","toggle your own server mute"),
        ("selfdeaf","toggle your own server deafen"),
        ("selfstream","toggle your stream (go live)"),
        ("selfcamera","toggle your camera/video"),
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
        ("host add <token>","add account to host list"),
        ("host remove <token_or_index>","remove by index or token"),
        ("host list","list hosted accounts"),
        ("host info <idx>","show account details + token preview"),
        ("host say <idx> <msg>","force hosted account to say something"),
        ("host broadcast <msg>","send msg from all hosted accounts"),
        ("host clear","remove all hosted accounts"),
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
        ("version","show selfbot version"),("reload","reload config from disk"),
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
        ("host say <idx> <msg>","force hosted account to say"),
        ("host broadcast <msg>","broadcast from all accounts"),
        ("logs [n]","tail selfbot console"),("eval <code>","evaluate python code"),
        ("restart","restart the selfbot process"),("reconnect","force gateway reconnect"),
        ("proxy set <url>","set HTTP/SOCKS proxy"),("proxy clear","clear proxy"),
        ("plugin load <path>","load a plugin from /plugins"),("plugin unload <name>","unload a plugin"),
        ("plugin list","list loaded plugins"),
        ("session switch <idx>","switch active session token"),("session list","list saved sessions"),
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
        ("multireact add <emoji>","add emoji to multi-react pool"),
        ("multireact remove <emoji>","remove emoji from pool"),
        ("multireact list","list pool"),("multireact on/off","toggle multi-react"),
        ("autoaddback on/off","auto-accept friend requests"),
        ("vsniper add <code> <gid>","add vanity url to watch list"),
        ("vsniper start/stop/list","vanity sniper control"),
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
    "rpc": [
        ("rpc <1-6> <field> <value>","set a rich presence slot"),
        ("rpc <slot> name <text>","activity name"),
        ("rpc <slot> details <text>","details line"),
        ("rpc <slot> state <text>","state line"),
        ("rpc <slot> type <type>","playing/streaming/listening/watching/competing/purplestream"),
        ("rpc <slot> platform <preset>","xbox/ps/ps4/ps5/crunchyroll/youtube/twitch/vrchat/meta"),
        ("rpc <slot> large_image <url>","large image"),
        ("rpc <slot> small_image <url>","small image"),
        ("rpc <slot> large_text <text>","large image hover text"),
        ("rpc <slot> small_text <text>","small image hover text"),
        ("rpc <slot> timestamp <val>","3600 | 1:00:00 | clear"),
        ("rpc <slot> btn1 <label> <url>","first button"),
        ("rpc <slot> btn2 <label> <url>","second button"),
        ("rpc <slot> clear","wipe that slot"),
        ("rpc status","show all 6 slots"),
        ("rpc clearall","wipe every slot"),
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
        ("stopactivity","clear current activity"),
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
        "fun":"fun & roleplay","tools":"tools & generators","host":"multi-account hosting",
        "lastfm":"last.fm integration","settings":"prefix, aliases, cooldowns, profiles",
        "guards":"blacklists, whitelists, restrictions, per-cmd toggles",
        "resilience":"auto-reconnect, session log, rate limits, cache, queue",
        "tasks":"background task manager","triggers":"message / reaction / voice / member triggers",
        "developer":"dev tools, plugins, proxy, sessions","server":"server management",
        "information":"user & server lookup","groupchat":"group dm & anti-gc",
        "utility":"text, afk, translate","tracking":"message & profile tracking",
        "downloads":"media downloader","social":"friends & social","auto":"automation & snipers",
        "profile":"account profile","status":"custom status",
        "mass":"mass action tools","nuke":"destructive ops + backup","scrape":"scrape & export",
        "webhooks":"webhooks & emoji tools","automod":"automod, raid, quarantine, tickets, verify",
        "monitor":"event monitoring & alerts","backup":"server backup & restore",
        "perms":"per-command permissions","scheduler":"scheduled actions",
        "db":"local database & stats","interactions":"button & modal handling",
        "rpc":"rich presence — 6 slots, spotify, xbox, ps, vrchat, meta",
    }
    lines = [f"  {WHITE}> sy's selfbot{RESET}  {DIM}v{VERSION}{RESET}", "", f"  {GREY}categories{RESET}", ""]
    for c in chunk:
        lines.append(f"  {CYAN}{c:<14}{RESET}  {DIM}{desc.get(c,'commands')}{RESET}")
    lines += ["", f"  {DIM}{PREFIX}help <category> [page]  •  {PREFIX}help <page> to flip{RESET}",
              f"  {DIM}page {page}/{total}  •  sy | ver {VERSION}{RESET}"]
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

client = discord.Client(chunk_guilds_at_startup=False, request_guilds=True)
_MAIN_CLIENT = client

# RPC cog — plain class, no commands.Bot. Loaded in on_ready.
RPC_COG = None

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
_multireact_pool = []
_multireact_enabled = False
_spam_tasks = {}
_snipe_cache = {}
_editsnipe_cache = {}
SNIPE_LIMIT = 20

_autoaddback = False
_giveaway_enabled = False
_nitrosniper_enabled = True
_captcha_key = ""
_autoclaim_enabled = False
_speak_lang = None
_vsniper_list = []
_vsniper_task = None
_aliases = {}
_cooldowns = {}
_cooldown_last = {}
_autodelete_secs = 0
_profiles = {}
_encrypt_enabled = False
_proxy = None
_plugins = {}
_sessions = []
_session_idx = 0
_perm_allow = {}
_perm_block = set()
_perm_channel = {}
_perm_server = {}
_mass_tasks = {}
_scheduler = []
_scheduler_task = None
_db_path = "database/selfbot.db"
_db = None
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
_nuke_backups = {}
_server_backups = {}
_stats = defaultdict(int)

_server_prefixes = {}
_cmd_blacklist_server = {}
_cmd_blacklist_channel = {}
_user_blacklist = set()
_user_whitelist = set()
_role_restrict = {}
_cmd_disabled = set()

_reconnect_count = 0
_last_ready_ts = 0
_session_events = []
_auto_reconnect = True
_auto_restart = True
_restart_backoff = 0
_MAX_RESTART_BACKOFF = 300

_rate_limit_events = []
_rate_limit_tracking = True
_cmd_queue = None
_queue_enabled = False
_queue_workers = 3
_queue_worker_tasks = []

_managed_tasks = {}
_cache_auto = True

_triggers = {"message": [], "reaction": [], "voice": [], "member": []}
_trigger_fired_counts = defaultdict(int)

_TASK_STORE = "database/tasks.json"
_TRIGGER_STORE = "database/triggers.json"

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

async def set_hypesquad(house_id: int):
    h = {"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post("https://discord.com/api/v9/hypesquad/online",
                              headers=h, json={"house_id": house_id}) as r:
                return r.status in (200, 204), await r.text()
    except Exception as e:
        return False, str(e)

async def clear_hypesquad():
    h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.delete("https://discord.com/api/v9/hypesquad/online", headers=h) as r:
                return r.status in (200, 204)
    except Exception:
        return False

# ─────────────────────────────────────────────
# AGC STATE
# ─────────────────────────────────────────────

_agc_state = {"enabled": False, "block": False, "leave_msg": "lol nice try",
              "gc_name": "trap detected", "gc_icon_url": None, "webhook_url": None}
_agc_whitelist = set()

def _agc_load_wl():
    global _agc_whitelist
    p = "config/agc_whitelist.json"
    if os.path.exists(p):
        try:
            with open(p) as f: _agc_whitelist = set(json.load(f))
        except Exception: _agc_whitelist = set()

def _agc_save_wl():
    with open("config/agc_whitelist.json", "w") as f:
        json.dump(list(_agc_whitelist), f)

_agc_load_wl()

# ─────────────────────────────────────────────
# HOSTED ACCOUNTS
# ─────────────────────────────────────────────

async def load_hosted_tokens_async():
    global HOSTED_TOKENS
    try:
        HOSTED_TOKENS = await async_hosted_tokens_get()
        print(f"[host] loaded {len(HOSTED_TOKENS)} tokens")
    except Exception as e:
        print(f"[host] load failed: {e} — falling back to empty")
        HOSTED_TOKENS = []

HOSTED_TOKENS: list[str] = []

# ─────────────────────────────────────────────
# HOSTED GATEWAY CLIENTS
# ─────────────────────────────────────────────

_hosted_clients: list[discord.Client] = []
_hosted_spawned = False

async def _run_hosted_client(hc, tok):
    try:
        await hc.start(tok)
    except Exception as e:
        print(f"[hosted:{hc._bot_index}] CONNECT ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()

async def _spawn_hosted_clients():
    global _hosted_spawned
    if _hosted_spawned:
        return
    _hosted_spawned = True

    try:
        tokens = await async_hosted_tokens_get()
    except Exception as e:
        print(f"[hosted] reload failed: {e}")
        tokens = list(HOSTED_TOKENS)

    print(f"[hosted] launcher called, {len(tokens)} tokens in list")
    if not tokens:
        return

    for i, tok in enumerate(tokens):
        print(f"[hosted:{i+1}] attempting login...")
        try:
            hc = discord.Client(chunk_guilds_at_startup=False, request_guilds=True)
            hc._bot_index = i + 1

            @hc.event
            async def _hc_on_ready(_hc=hc):
                print(f"[hosted:{_hc._bot_index}] \u2713 {_hc.user} online")

            @hc.event
            async def _hc_on_message(_m, _hc=hc):
                try:
                    await _dispatch_message(_hc, _m)
                except Exception as e:
                    print(f"[hosted:{_hc._bot_index}] dispatch error: {e}")
                    traceback.print_exc()

            _hosted_clients.append(hc)
            asyncio.create_task(_run_hosted_client(hc, tok))
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[hosted:{i+1}] spawn failed: {type(e).__name__}: {e}")
            traceback.print_exc()

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

def log_msg(tag, content):
    if not LOGGER_ENABLED: return
    line = f"[{datetime.now().strftime('%H:%M:%S')}] [{tag}] {content}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception: pass

# ─────────────────────────────────────────────
# PERSISTENT TASK + TRIGGER STORAGE
# ─────────────────────────────────────────────

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

# ─────────────────────────────────────────────
# CACHE CLEANUP
# ─────────────────────────────────────────────

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
            await asyncio.sleep(600)
        except Exception as e:
            print(f"[cache] {e}")
            await asyncio.sleep(60)

# ─────────────────────────────────────────────
# COMMAND QUEUE
# ─────────────────────────────────────────────

async def _queue_worker(name):
    while True:
        try:
            item = await _cmd_queue.get()
            if item is None:
                await asyncio.sleep(0.05); continue
            coro = item
            try: await coro
            except Exception as e: print(f"[queue:{name}] {e}")
            await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[queue worker] {e}")
            await asyncio.sleep(1)

# ─────────────────────────────────────────────
# TASK MANAGER
# ─────────────────────────────────────────────

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
# LOCAL DB
# ─────────────────────────────────────────────

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

_db_open()

# ─────────────────────────────────────────────
# ENCRYPTION
# ─────────────────────────────────────────────

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
# PLUGINS
# ─────────────────────────────────────────────

def _load_plugin(name):
    import importlib.util
    path = f"plugins/{name}.py"
    if not os.path.exists(path): return False
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _plugins[name] = mod
    if hasattr(mod, "setup"):
        try: mod.setup(client, globals())
        except Exception as e: print(f"[plugin] setup error: {e}")
    return True

def _unload_plugin(name):
    if name in _plugins:
        mod = _plugins.pop(name)
        if hasattr(mod, "teardown"):
            try: mod.teardown()
            except Exception: pass
        return True
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
        "os":"Windows","browser":"Chrome","device":"","system_locale":"en",
        "has_client_mods":False,"browser_user_agent":USER_AGENT,"browser_version":"142.0.0.0",
        "os_version":"10","release_channel":"stable","client_launch_id":str(uuid4()),
        "client_build_number":971383,"client_event_source":None,
    }).encode()).decode()
    return {"authorization": token.strip().strip('"').strip("'"),
            "accept":"*/*","content-type":"application/json","user-agent":USER_AGENT,
            "x-super-properties":sp,"origin":"https://discord.com",
            "referer":"https://discord.com/quest-home"}

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
                if _rate_limit_tracking:
                    _rate_limit_events.append({"ts": time.time(), "url": url, "wait": ra})
                await asyncio.sleep(ra); continue
            if e.status >= 500:
                await asyncio.sleep(0.5 * (attempt + 1)); continue
            raise
    if last is not None:
        raise last
    return {}

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
        if rw: return rw[0].get("messages", {}).get("name") or "Reward"
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
        except Exception: return []
    async def enroll(self, session, quest):
        d = await _api(session, "POST", f"https://discord.com/api/v9/quests/{quest.id}/enroll",
                       headers=self.headers,
                       json_body={"location":11,"is_targeted":False,"metadata_raw":None})
        if d: quest.data["user_status"] = d
    async def run(self, session, quest):
        if not quest.is_enrolled(): await self.enroll(session, quest)
        if not quest.is_supported(): return "unsupported"
        task = quest.selected_task
        if task in ("WATCH_VIDEO","WATCH_VIDEO_ON_MOBILE"): return await self._video(session, quest)
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
            ts = min(float(quest.target), float(int(datetime.now(timezone.utc).timestamp()) - started))
            try:
                d = await _api(session, "POST", f"https://discord.com/api/v9/quests/{quest.id}/video-progress",
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
                    d = await _api(session, "POST", f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                                   headers=self.headers, json_body=p, retries=2)
                    active = p; break
                except APIError: continue
            if d: quest.data["user_status"] = d
            if quest.is_completed() or quest.progress_value() >= quest.target: break
            await asyncio.sleep(interval)
        try:
            t = dict(active); t["terminal"] = True
            await _api(session, "POST", f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                       headers=self.headers, json_body=t, retries=1)
        except Exception: pass
        return "completed" if quest.is_completed() else "recovering"

async def autoquest_run(token):
    svc = QuestService(token)
    async with aiohttp.ClientSession() as session:
        quests = await svc.fetch(session)
        active = [q for q in quests if not q.is_completed() and q.is_supported()]
        for q in active:
            print(f"[AutoQuest] {q.name}")
            await svc.run(session, q)
            print(f"[AutoQuest] ✓ {q.name}")

# ─────────────────────────────────────────────
# ORB / NITRO / SPAM / LASTFM
# ─────────────────────────────────────────────

ORB_SKU = "1342211853484429445"

async def claim_orb(token):
    h = {"authorization": token, "content-type": "application/json", "user-agent": USER_AGENT,
         "origin": "https://discord.com", "referer": "https://discord.com/shop?tab=orbs"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(f"https://discord.com/api/v9/virtual-currency/skus/{ORB_SKU}/redeem",
                                    headers=h, json={}) as r:
                return r.status in (200,201,204), await r.text()
        except Exception as e: return False, str(e)

GIFT_RE = re.compile(r"(discord\.gift|discord\.com/gifts)/([a-zA-Z0-9]+)")

async def snipe_nitro(code, channel_id):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"https://discord.com/api/v9/entitlements/gift-codes/{code}/redeem",
                headers={"Authorization":TOKEN,"Content-Type":"application/json","User-Agent":USER_AGENT},
                json={"channel_id": str(channel_id)}) as r:
                log_msg("SNIPER", f"{'✓ SNIPED' if r.status==200 else '✗ miss'} {code} [{r.status}]")
    except Exception as e: log_msg("SNIPER", f"error: {e}")

async def _spam_worker(channel, count, text):
    try:
        for _ in range(count): await channel.send(text)
    except asyncio.CancelledError: raise
    except Exception as e: print(f"[spam] {e}")
    finally: _spam_tasks.pop(channel.id, None)

LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"
_lfm = {}
def _load_lfm():
    global _lfm
    _lfm = load_config().get("lastfm", {})
def _save_lfm():
    cfg = load_config(); cfg["lastfm"] = _lfm; save_config(cfg)
_load_lfm()

async def lfm_get(method, params):
    p = {"method": method, "api_key": _lfm.get("api_key",""), "format":"json", **params}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(LASTFM_BASE, params=p, timeout=aiohttp.ClientTimeout(total=8)) as r:
                return await r.json() if r.status == 200 else {}
    except Exception: return {}

_PERIOD = {"w":"7day","week":"7day","m":"1month","month":"1month",
           "3m":"3month","6m":"6month","y":"12month","year":"12month","all":"overall","overall":"overall"}
_PLABEL = {"7day":"this week","1month":"this month","3month":"3 months",
           "6month":"6 months","12month":"this year","overall":"all time"}

async def lfm_np(username):
    d = await lfm_get("user.getRecentTracks", {"user": username, "limit": 1, "extended": 1})
    tracks = d.get("recenttracks", {}).get("track", [])
    if not tracks: return None
    t = tracks[0] if isinstance(tracks, list) else tracks
    artist = t.get("artist", {})
    return {"title": t.get("name","?"),
            "artist": artist.get("name","?") if isinstance(artist,dict) else str(artist),
            "album": (t.get("album",{}) or {}).get("#text",""),
            "playing": t.get("@attr",{}).get("nowplaying") == "true",
            "loved": t.get("loved","0") == "1",
            "total": d.get("recenttracks",{}).get("@attr",{}).get("total","?")}

# ─────────────────────────────────────────────
# HOSTED / UTIL HELPERS
# ─────────────────────────────────────────────

async def hosted_send(token, channel_id, content):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f"https://discord.com/api/v9/channels/{channel_id}/messages",
                headers={"Authorization": token.strip(), "Content-Type":"application/json", "User-Agent":USER_AGENT},
                json={"content": content}) as r:
                return r.status in (200,201)
    except Exception: return False

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

def uwuify(text):
    text = re.sub(r'[rRlL]', 'w', text)
    text = re.sub(r'n([aeiou])', r'ny\1', text)
    text = re.sub(r'N([aeiou])', r'Ny\1', text)
    faces = [" >w<", " uwu", " owo", " >.<", " ^w^"]
    for p in ".!?": text = text.replace(p, p + random.choice(faces))
    return text

def owoify(text):
    for a,b in [("r","w"),("l","w"),("R","W"),("L","W"),("n","ny"),("N","NY")]:
        text = text.replace(a,b)
    return f"owo {text} owo"

def mock_text(text): return "".join(c.upper() if i % 2 else c.lower() for i,c in enumerate(text))

def aesthetic(text):
    n = "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    w = "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ　ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ０１２３４５６７８９"
    return text.translate(str.maketrans(n, w))

def clap_text(text): return " 👏 ".join(text.split())

NEKO_ACTIONS = {"feed":"feed","tickle":"tickle","slap":"slap","hug":"hug","cuddle":"cuddle",
                "pat":"pat","kiss":"kiss","poke":"poke","wink":"wink","smug":"smug","boop":"boop",
                "nom":"nom","wave":"wave","highfive":"highfive","bite":"bite","blush":"blush",
                "dance":"dance","happy":"happy","cringe":"cringe"}

async def neko_gif(action):
    endpoint = NEKO_ACTIONS.get(action, action)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://nekos.life/api/v2/img/{endpoint}",
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200: return (await r.json()).get("url")
    except Exception: pass
    return None

async def translate_text(text, target_lang):
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {"client":"gtx","sl":"auto","tl":target_lang,"dt":"t","q":text}
        async with aiohttp.ClientSession() as s:
            async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=8)) as r:
                d = await r.json()
                return "".join(p[0] for p in d[0] if p[0])
    except Exception as e: return f"error: {e}"

async def vsniper_loop():
    while _vsniper_task and not _vsniper_task.cancelled():
        for entry in list(_vsniper_list):
            code = entry["code"]; guild_id = entry["guild_id"]
            try:
                async with aiohttp.ClientSession() as s:
                    h = {"Authorization":TOKEN,"Content-Type":"application/json","User-Agent":USER_AGENT}
                    async with s.get(f"https://discord.com/api/v9/invites/{code}", headers=h) as r:
                        if r.status == 404:
                            async with s.patch(f"https://discord.com/api/v9/guilds/{guild_id}/vanity-url",
                                headers=h, json={"code": code}) as r2:
                                if r2.status in (200,204):
                                    log_msg("VSNIPER", f"CLAIMED {code} for guild {guild_id}")
            except Exception: pass
        await asyncio.sleep(0.5)

async def typing_loop(channel):
    while True:
        try:
            async with channel.typing(): await asyncio.sleep(9)
        except Exception: await asyncio.sleep(5)

# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────

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

async def _run_scheduled(job):
    action = job["action"]; payload = job.get("payload", {})
    if action == "message":
        ch = client.get_channel(int(payload["ch_id"]))
        if ch: await ch.send(payload["msg"])
    elif action == "status":
        await client.change_presence(activity=discord.CustomActivity(name=payload["text"]))

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

# ─────────────────────────────────────────────
# MASS ACTION WORKERS
# ─────────────────────────────────────────────

async def mass_dm(guild, msg, ctx=None):
    done = 0
    for m in list(guild.members):
        if m.bot or m.id == client.user.id: continue
        try: await m.send(msg); done += 1
        except Exception: pass
        await asyncio.sleep(1.2)
    return done

async def mass_friend_ids(ids, ctx=None):
    done = 0
    h = {"Authorization": TOKEN, "Content-Type":"application/json", "User-Agent": USER_AGENT}
    async with aiohttp.ClientSession() as s:
        for uid in ids:
            try:
                async with s.put(f"https://discord.com/api/v9/users/@me/relationships/{uid}",
                                 headers=h, json={"type": 1}) as r:
                    if r.status in (200,201,204): done += 1
            except Exception: pass
            await asyncio.sleep(1.5)
    return done

async def mass_role(guild, role_id, ids):
    done = 0; role = guild.get_role(int(role_id))
    if not role: return 0
    for uid in ids:
        m = guild.get_member(int(uid))
        if not m: continue
        try: await m.add_roles(role); done += 1
        except Exception: pass
        await asyncio.sleep(0.6)
    return done

async def mass_unrole(guild, role_id, ids):
    done = 0; role = guild.get_role(int(role_id))
    if not role: return 0
    for uid in ids:
        m = guild.get_member(int(uid))
        if not m: continue
        try: await m.remove_roles(role); done += 1
        except Exception: pass
        await asyncio.sleep(0.6)
    return done

async def mass_ban(guild, ids, reason="mass ban"):
    done = 0
    for uid in ids:
        try:
            user = await client.fetch_user(int(uid))
            await guild.ban(user, reason=reason); done += 1
        except Exception: pass
        await asyncio.sleep(0.8)
    return done

async def mass_kick(guild, ids, reason="mass kick"):
    done = 0
    for uid in ids:
        m = guild.get_member(int(uid))
        if not m: continue
        try: await m.kick(reason=reason); done += 1
        except Exception: pass
        await asyncio.sleep(0.8)
    return done

async def mass_join(invite, tokens, ctx=None):
    done = 0
    h_tmpl = {"Content-Type":"application/json","User-Agent":USER_AGENT}
    async with aiohttp.ClientSession() as s:
        for tk in tokens:
            try:
                async with s.post(f"https://discord.com/api/v9/invites/{invite}",
                    headers={**h_tmpl, "Authorization": tk.strip()},
                    json={"session_id": str(uuid4())[:12]}) as r:
                    if r.status in (200,204): done += 1
            except Exception: pass
            await asyncio.sleep(1.5)
    return done

async def mass_leave(guild_id, tokens):
    done = 0
    h_tmpl = {"User-Agent":USER_AGENT}
    async with aiohttp.ClientSession() as s:
        for tk in tokens:
            try:
                async with s.delete(f"https://discord.com/api/v9/users/@me/guilds/{guild_id}",
                                    headers={**h_tmpl, "Authorization": tk.strip()}) as r:
                    if r.status in (200,204): done += 1
            except Exception: pass
            await asyncio.sleep(1.0)
    return done

# ─────────────────────────────────────────────
# MONITOR / PERMISSIONS
# ─────────────────────────────────────────────

async def _monitor_log(guild, title, body):
    if not _monitor["log_ch"]: return
    ch = client.get_channel(int(_monitor["log_ch"]))
    if not ch: return
    try: await ch.send(ui_box(title, [body]))
    except Exception: pass

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

# ─────────────────────────────────────────────
# AUTO-DELETE
# ─────────────────────────────────────────────

async def _send_temp(channel, content):
    msg = await channel.send(content)
    if _autodelete_secs > 0:
        async def _d():
            await asyncio.sleep(_autodelete_secs)
            try: await msg.delete()
            except Exception: pass
        asyncio.create_task(_d())
    return msg

# ─────────────────────────────────────────────
# SERVER BACKUP HELPERS
# ─────────────────────────────────────────────

async def _backup_server(guild):
    data = {"id": str(guild.id), "name": guild.name,
            "icon": str(guild.icon) if guild.icon else None,
            "roles": [{"id": str(r.id), "name": r.name, "color": r.color.value, "perms": r.permissions.value,
                       "position": r.position, "hoist": r.hoist, "mentionable": r.mentionable}
                      for r in guild.roles],
            "categories": [{"id": str(c.id), "name": c.name, "position": c.position} for c in guild.categories],
            "channels": [{"id": str(ch.id), "name": ch.name, "type": str(ch.type),
                          "position": ch.position,
                          "category": str(ch.category_id) if getattr(ch, "category_id", None) else None}
                         for ch in guild.channels]}
    p = f"backups/server_{guild.id}_{int(time.time())}.json"
    with open(p, "w") as f: json.dump(data, f, indent=2)
    return p

async def _restore_server(guild, path):
    with open(path) as f: data = json.load(f)
    for r in data.get("roles", []):
        if r["name"] == "@everyone": continue
        try:
            await guild.create_role(name=r["name"], color=discord.Color(r["color"]),
                                    permissions=discord.Permissions(r["perms"]),
                                    hoist=r["hoist"], mentionable=r["mentionable"])
        except Exception: pass
    for c in data.get("categories", []):
        try: await guild.create_category(c["name"])
        except Exception: pass
    for ch in data.get("channels", []):
        try:
            if "text" in ch["type"]: await guild.create_text_channel(ch["name"])
            elif "voice" in ch["type"]: await guild.create_voice_channel(ch["name"])
        except Exception: pass

async def _dump_server(guild):
    p = f"exports/server_{guild.id}_{int(time.time())}.json"
    data = await _backup_server(guild)
    with open(p, "w") as f: json.dump(data, f, indent=2)
    return p

async def _sync_servers(src, dst):
    await _restore_server(dst, await _backup_server(src))

# ─────────────────────────────────────────────
# INTERACTIONS
# ─────────────────────────────────────────────

@client.event
async def on_interaction(interaction):
    try:
        tname = getattr(interaction.type, "name", str(interaction.type))
        if tname == "component" and _buttons_enabled:
            cid = interaction.data.get("custom_id", "") if interaction.data else ""
            if cid.startswith("verify_") and _verify_cfg["role_id"]:
                member = interaction.user
                guild = interaction.guild
                role = guild.get_role(int(_verify_cfg["role_id"])) if guild else None
                if role and member:
                    try: await member.add_roles(role)
                    except Exception: pass
                    try: await interaction.response.send_message("verified.", ephemeral=True)
                    except Exception: pass
        elif tname == "modal_submit" and _modals_enabled:
            _pending_interactions.append(interaction)
    except Exception as e:
        print(f"[interaction] {e}")

# ─────────────────────────────────────────────
# RPC COG LOADER
# ─────────────────────────────────────────────

async def _load_rpc_cog():
    global RPC_COG
    try:
        import importlib.util
        if not os.path.exists("cogs/rpc.py"):
            print("[RPC] cogs/rpc.py MISSING — push the file to the repo.")
            return
        spec = importlib.util.spec_from_file_location("cogs.rpc", "cogs/rpc.py")
        if spec is None or spec.loader is None:
            print("[RPC] cogs/rpc.py could not be loaded")
            return
        mod = importlib.util.module_from_spec(spec)
        sys.modules["cogs.rpc"] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception as e:
            print(f"[RPC] cogs/rpc.py failed to exec: {e}")
            traceback.print_exc()
            return
        cog = mod.RPCCog(_MAIN_CLIENT)
        RPC_COG = cog
        try:
            await cog.on_ready()
        except Exception as e:
            print(f"[RPC] on_ready failed: {e}")
            traceback.print_exc()
        print("[RPC] cog loaded")
    except Exception as e:
        print(f"[RPC] load failed: {e}")
        traceback.print_exc()

# ─────────────────────────────────────────────
# EVENTS
# ─────────────────────────────────────────────

@client.event
async def on_ready():
    global _autoaddback, _autoreact_emoji, _last_ready_ts, _cmd_queue, _queue_worker_tasks, HOSTED_TOKENS
    _last_ready_ts = time.time()
    _session_events.append({"ts": _last_ready_ts, "event": "ready", "user": str(client.user)})

    is_main = not hasattr(client, "_bot_index")
    idx = getattr(client, "_bot_index", "main")
    print(f"[{idx}] ✓ {client.user} ({client.user.id}) | prefix: {PREFIX} | servers: {len(client.guilds)}")

    await load_hosted_tokens_async()

    if is_main and not _hosted_spawned:
        asyncio.create_task(_spawn_hosted_clients())

    cfg = load_config()
    if cfg.get("autoquest_enabled"):
        asyncio.create_task(autoquest_run(TOKEN))
    if cfg.get("autoaddback"):
        _autoaddback = True

    sched_load()
    triggers_load()
    tasks_load()

    if is_main and not globals().get("_ipc_initialized"):
        print("[ipc] initializing global state...")
        globals()["_ipc_initialized"] = True
        globals()["_uptime_start"] = time.time()
        globals()["_latency"] = 0
        globals()["_platform"] = "desktop"
        globals()["_rpc_state"] = {
            "enabled":     False,
            "type":        "playing",
            "name":        "selfbot",
            "details":     "",
            "state":       "",
            "url":         "",
            "large_image": "",
            "large_text":  "",
            "small_image": "",
            "small_text":  "",
        }
        globals()["_modules"] = {
            "afk": False,
            "spotify_sync": False,
            "lyrics": False,
            "autoresponse": False,
            "stealthy": False,
            "gift_sniper": True,
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

    if _cmd_queue is None:
        _cmd_queue = asyncio.Queue()
        for i in range(_queue_workers):
            _queue_worker_tasks.append(asyncio.create_task(_queue_worker(f"w{i}")))

    try:
        for g in client.guilds:
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

    if is_main and RPC_COG is None:
        await _load_rpc_cog()

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

async def _dispatch_message(_client, message):
    client = _client

    global PREFIX, _cfg
    global SNIPER_ENABLED, LOGGER_ENABLED, _afk_enabled, _afk_msg
    global _autoreact_emoji, _autoaddback, _current_platform
    global _autoclaim_enabled, _captcha_key, _speak_lang
    global _giveaway_enabled, _nitrosniper_enabled
    global _vsniper_task
    global _multireact_enabled, _multireact_pool
    global _autodelete_secs, _encrypt_enabled, _proxy, _session_idx
    global _rate_limit_tracking, _cache_auto
    global _queue_enabled, _queue_workers
    global _scheduler
    global _reconnect_count, _auto_reconnect, _auto_restart
    global _buttons_enabled, _modals_enabled
    global _triggers, _trigger_fired_counts
    global _server_prefixes, _cmd_blacklist_server, _cmd_blacklist_channel
    global _user_blacklist, _user_whitelist, _role_restrict, _cmd_disabled
    global _managed_tasks, _multireact_enabled

    # ── RPC COG DISPATCH (main client only) ──
    if RPC_COG is not None and _client is _MAIN_CLIENT:
        if message.content and message.content.startswith(PREFIX):
            _raw = message.content[len(PREFIX):].strip()
            _args = _raw.split()
            _cmd = _args[0].lower() if _args else ""
            if _cmd in RPC_COG.COMMANDS:
                try:
                    await RPC_COG.handle(message, _cmd, _args[1:])
                except Exception as e:
                    print(f"[rpc dispatch] {e}")
                    traceback.print_exc()
                return

    if LOGGER_ENABLED and message.guild:
        try: log_msg("MSG", f"{message.guild.name}/#{message.channel.name} | {message.author}: {message.content[:100]}")
        except Exception: pass

    for t in _triggers.get("message", []):
        try:
            ch_filter = t.get("channels", [])
            if ch_filter and str(message.channel.id) not in ch_filter: continue
            if t.get("contains") and t["contains"].lower() in (message.content or "").lower():
                _trigger_fired_counts[t["name"]] += 1
                await message.channel.send(t.get("reply") or "")
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
        _tracking.setdefault(message.author.id, []).append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "content": message.content,
            "channel": getattr(message.channel, "name", str(message.channel.id)),
        })
        if len(_tracking[message.author.id]) > 200: _tracking[message.author.id] = _tracking[message.author.id][-200:]

    if _automod["enabled"] and message.guild and message.author.id != client.user.id:
        body = (message.content or "").lower()
        for w in _automod["words"]:
            if w.lower() in body:
                try: await message.delete()
                except Exception: pass
                try:
                    if _automod["action"] == "kick": await message.author.kick(reason="automod")
                    elif _automod["action"] == "ban": await message.guild.ban(message.author, reason="automod")
                    elif _automod["action"] == "timeout":
                        until = discord.utils.utcnow() + timedelta(minutes=10)
                        await message.author.timeout(until)
                except Exception: pass
                await _monitor_log(message.guild, "automod", f"{message.author} matched `{w}`")
                break

    if _monitor["keywords"] and message.guild and message.author.id != client.user.id:
        body = (message.content or "").lower()
        for kw in _monitor["keywords"]:
            if kw.lower() in body:
                await _monitor_log(message.guild, "keyword", f"{message.author} said `{kw}` in {message.channel.mention}")
                break

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
                try: await message.add_reaction(emoji)
                except Exception: pass
                await asyncio.sleep(0.15)

    if message.author.id != client.user.id: return

    if not message.content:
        return
    effective_prefix = PREFIX
    if message.guild and str(message.guild.id) in _server_prefixes:
        effective_prefix = _server_prefixes[str(message.guild.id)]

    if not message.content.startswith(effective_prefix): return

    raw = message.content[len(effective_prefix):]
    args = raw.split()
    cmd = args[0].lower() if args else ""

    if RPC_COG is not None and cmd in RPC_COG.COMMANDS:
        return

    if cmd in _aliases: cmd = _aliases[cmd]

    if cmd in _cooldowns:
        key = (message.author.id, cmd)
        last = _cooldown_last.get(key, 0)
        if time.time() - last < _cooldowns[cmd]: return
        _cooldown_last[key] = time.time()

    if not _perm_check(cmd, message): return

    db_stats_inc(cmd)

    # ─────────────────────────────────────────────
    # HELP
    # ─────────────────────────────────────────────
    if cmd in ("help", "h"):
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub.isdigit():
            await message.channel.send(build_help_root(int(sub))); return
        if sub:
            page = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1
            await message.channel.send(build_help_section(sub, page)); return
        await message.channel.send(build_help_root(1))

    # ─────────────────────────────────────────────
    # SETTINGS
    # ─────────────────────────────────────────────
    elif cmd == "prefix":
        if len(args) < 2: return await message.edit(content=ui_info(f"current prefix: {PREFIX}"))
        PREFIX = args[1]; cfg = load_config(); cfg["prefix"] = PREFIX; save_config(cfg)
        await message.edit(content=ui_ok(f"prefix changed to `{PREFIX}`"))
    elif cmd == "version":
        await message.edit(content=ui_info(f"sy's selfbot v{VERSION}"))
    elif cmd == "reload":
        _cfg = load_config(); await message.edit(content=ui_ok("config reloaded"))
    elif cmd == "serverprefix":
        try: await message.delete()
        except Exception: pass
        if not message.guild: return await message.channel.send(ui_err("server only"), delete_after=5)
        if len(args) < 2:
            cur = _server_prefixes.get(str(message.guild.id), PREFIX)
            return await message.channel.send(ui_info(f"server prefix: `{cur}`"), delete_after=6)
        _server_prefixes[str(message.guild.id)] = args[1]
        cfg = load_config(); cfg.setdefault("server_prefixes", {})[str(message.guild.id)] = args[1]
        save_config(cfg)
        await message.channel.send(ui_ok(f"server prefix → `{args[1]}`"))
    elif cmd == "serverprefixclear":
        try: await message.delete()
        except Exception: pass
        if not message.guild: return await message.channel.send(ui_err("server only"), delete_after=5)
        _server_prefixes.pop(str(message.guild.id), None)
        cfg = load_config()
        if "server_prefixes" in cfg: cfg["server_prefixes"].pop(str(message.guild.id), None)
        save_config(cfg)
        await message.channel.send(ui_ok("cleared"))
    elif cmd == "alias":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add" and len(args) >= 4:
            _aliases[args[3].lower()] = args[2].lower()
            await message.edit(content=ui_ok(f"alias `{args[3]}` → `{args[2]}`"))
        elif sub == "remove" and len(args) >= 3:
            _aliases.pop(args[2].lower(), None); await message.edit(content=ui_ok("removed"))
        elif sub == "list":
            rows = [f"  {GREY}•{RESET} {k} → {v}" for k, v in _aliases.items()]
            await message.edit(content=_paginate("aliases", "", rows) if rows else ui_info("no aliases"))
        else: await message.edit(content=ui_err("usage: alias add/remove/list"))
    elif cmd == "cooldown":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "set" and len(args) >= 4 and args[3].isdigit():
            _cooldowns[args[2].lower()] = int(args[3]); await message.edit(content=ui_ok("set"))
        elif sub == "clear" and len(args) >= 3:
            _cooldowns.pop(args[2].lower(), None); await message.edit(content=ui_ok("cleared"))
        elif sub == "list":
            rows = [f"  {GREY}•{RESET} {k}: {v}s" for k, v in _cooldowns.items()]
            await message.edit(content=_paginate("cooldowns", "", rows) if rows else ui_info("none"))
        else: await message.edit(content=ui_err("usage: cooldown set/clear/list"))
    elif cmd == "profile":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "save" and len(args) >= 3:
            os.makedirs("database/profiles", exist_ok=True)
            with open(f"database/profiles/{args[2]}.json", "w") as f: json.dump(load_config(), f, indent=2)
            await message.edit(content=ui_ok("saved"))
        elif sub == "load" and len(args) >= 3:
            p = f"database/profiles/{args[2]}.json"
            if os.path.exists(p):
                with open(p) as f: save_config(json.load(f))
                await message.edit(content=ui_ok("loaded (restart to fully apply)"))
            else: await message.edit(content=ui_err("not found"))
        elif sub == "list":
            if not os.path.isdir("database/profiles"): return await message.edit(content=ui_info("no profiles"))
            rows = [f"  {GREY}•{RESET} {n[:-5]}" for n in os.listdir("database/profiles") if n.endswith(".json")]
            await message.edit(content=_paginate("profiles", "", rows) if rows else ui_info("none"))
        elif sub == "delete" and len(args) >= 3:
            p = f"database/profiles/{args[2]}.json"
            if os.path.exists(p): os.remove(p); await message.edit(content=ui_ok("deleted"))
            else: await message.edit(content=ui_err("not found"))
        else: await message.edit(content=ui_err("usage: profile save/load/list/delete"))
    elif cmd == "config":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "export":
            p = f"exports/config_{int(time.time())}.json"
            with open(p, "w") as f: json.dump(load_config(), f, indent=2)
            await message.edit(content=ui_ok(f"→ {p}"))
        elif sub == "import" and len(args) >= 3 and os.path.exists(args[2]):
            with open(args[2]) as f: save_config(json.load(f))
            await message.edit(content=ui_ok("imported"))
        else: await message.edit(content=ui_err("usage: config export | import <path>"))
    elif cmd == "encrypt":
        if not _HAS_CRYPTO: return await message.edit(content=ui_err("cryptography not installed"))
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "on":
            try: encrypt_file("config.json"); await message.edit(content=ui_ok("encrypted"))
            except Exception as e: await message.edit(content=ui_err(str(e)))
        elif sub == "off":
            try: decrypt_file("config.json"); await message.edit(content=ui_ok("decrypted"))
            except Exception as e: await message.edit(content=ui_err(str(e)))
        else: await message.edit(content=ui_err("usage: encrypt on/off"))
    elif cmd == "enable":
        if len(args) < 2: return await message.edit(content=ui_err("usage: enable <cmd>"))
        _cmd_disabled.discard(args[1].lower()); await message.edit(content=ui_ok(f"enabled `{args[1]}`"))
    elif cmd == "disable":
        if len(args) < 2: return await message.edit(content=ui_err("usage: disable <cmd>"))
        _cmd_disabled.add(args[1].lower()); await message.edit(content=ui_ok(f"disabled `{args[1]}`"))
    elif cmd == "disabled":
        rows = [f"  {GREY}•{RESET} {c}" for c in sorted(_cmd_disabled)]
        await message.edit(content=_paginate("disabled commands", "", rows) if rows else ui_info("none"))

    # ─────────────────────────────────────────────
    # GUARDS
    # ─────────────────────────────────────────────
    elif cmd == "blacklist":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add" and len(args) >= 3:
            _user_blacklist.add(int(args[2])); await message.channel.send(ui_ok("added"))
        elif sub == "remove" and len(args) >= 3:
            _user_blacklist.discard(int(args[2])); await message.channel.send(ui_ok("removed"))
        elif sub == "list":
            rows = [f"  {GREY}•{RESET} <@{u}>" for u in _user_blacklist]
            await message.channel.send(_paginate("user blacklist", "", rows) if rows else ui_info("empty"))
        elif sub == "clear":
            _user_blacklist.clear(); await message.channel.send(ui_ok("cleared"))
        else: await message.channel.send(build_help_section("guards"))
    elif cmd == "whitelist":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add" and len(args) >= 3:
            _user_whitelist.add(int(args[2]))
            await message.channel.send(ui_ok(f"whitelisted {args[2]} (whitelist ON)"))
        elif sub == "remove" and len(args) >= 3:
            _user_whitelist.discard(int(args[2])); await message.channel.send(ui_ok("removed"))
        elif sub == "list":
            rows = [f"  {GREY}•{RESET} <@{u}>" for u in _user_whitelist]
            await message.channel.send(_paginate("user whitelist", "", rows) if rows else ui_info("empty"))
        elif sub == "clear":
            _user_whitelist.clear(); await message.channel.send(ui_ok("cleared"))
        else: await message.channel.send(build_help_section("guards"))
    elif cmd == "serverblacklist":
        try: await message.delete()
        except Exception: pass
        if not message.guild: return await message.channel.send(ui_err("server only"), delete_after=5)
        sub = args[1].lower() if len(args) > 1 else ""; gid = str(message.guild.id)
        if sub == "add" and len(args) >= 3:
            _cmd_blacklist_server.setdefault(gid, set()).add(args[2])
            await message.channel.send(ui_ok(f"blocked `{args[2]}`"))
        elif sub == "remove" and len(args) >= 3:
            _cmd_blacklist_server.get(gid, set()).discard(args[2]); await message.channel.send(ui_ok("removed"))
        elif sub == "list":
            rows = [f"  {GREY}•{RESET} {c}" for c in _cmd_blacklist_server.get(gid, set())]
            await message.channel.send(_paginate("server blacklist", message.guild.name, rows) if rows else ui_info("empty"))
        elif sub == "clear":
            _cmd_blacklist_server.pop(gid, None); await message.channel.send(ui_ok("cleared"))
        else: await message.channel.send(build_help_section("guards"))
    elif cmd == "channelblacklist":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""; cid = str(message.channel.id)
        if sub == "add" and len(args) >= 3:
            _cmd_blacklist_channel.setdefault(cid, set()).add(args[2])
            await message.channel.send(ui_ok(f"blocked `{args[2]}`"))
        elif sub == "remove" and len(args) >= 3:
            _cmd_blacklist_channel.get(cid, set()).discard(args[2]); await message.channel.send(ui_ok("removed"))
        elif sub == "list":
            rows = [f"  {GREY}•{RESET} {c}" for c in _cmd_blacklist_channel.get(cid, set())]
            await message.channel.send(_paginate("channel blacklist", "", rows) if rows else ui_info("empty"))
        elif sub == "clear":
            _cmd_blacklist_channel.pop(cid, None); await message.channel.send(ui_ok("cleared"))
        else: await message.channel.send(build_help_section("guards"))
    elif cmd == "rolerestrict":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add" and len(args) >= 4:
            _role_restrict.setdefault(args[2], set()).add(int(args[3]))
            await message.channel.send(ui_ok(f"`{args[2]}` → role {args[3]}"))
        elif sub == "remove" and len(args) >= 4:
            _role_restrict.get(args[2], set()).discard(int(args[3])); await message.channel.send(ui_ok("removed"))
        elif sub == "list":
            rows = []
            for c, ids in _role_restrict.items():
                for rid in ids: rows.append(f"  {GREY}•{RESET} {c} → role {rid}")
            await message.channel.send(_paginate("role restrictions", "", rows) if rows else ui_info("empty"))
        elif sub == "clear" and len(args) >= 3:
            _role_restrict.pop(args[2], None); await message.channel.send(ui_ok("cleared"))
        else: await message.channel.send(build_help_section("guards"))
    elif cmd == "guards":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "status":
            await message.edit(content=ui_box("guards", [
                f"  {DIM}user blacklist{RESET}   {len(_user_blacklist)}",
                f"  {DIM}user whitelist{RESET}   {len(_user_whitelist)}",
                f"  {DIM}server blk sets{RESET} {len(_cmd_blacklist_server)}",
                f"  {DIM}channel blk sets{RESET} {len(_cmd_blacklist_channel)}",
                f"  {DIM}role restrictions{RESET} {len(_role_restrict)}",
                f"  {DIM}disabled cmds{RESET}    {len(_cmd_disabled)}",
            ]))
        elif sub == "reset":
            _user_blacklist.clear(); _user_whitelist.clear()
            _cmd_blacklist_server.clear(); _cmd_blacklist_channel.clear()
            _role_restrict.clear(); _cmd_disabled.clear()
            await message.edit(content=ui_ok("guards reset"))
        else: await message.edit(content=build_help_section("guards"))

    # ─────────────────────────────────────────────
    # RESILIENCE
    # ─────────────────────────────────────────────
    elif cmd == "autoreconnect":
        _auto_reconnect = (args[1].lower() in ("on","enable")) if len(args) > 1 else not _auto_reconnect
        await message.edit(content=ui_ok(f"autoreconnect → {'on' if _auto_reconnect else 'off'}"))
    elif cmd == "autorestart":
        _auto_restart = (args[1].lower() in ("on","enable")) if len(args) > 1 else not _auto_restart
        await message.edit(content=ui_ok(f"autorestart → {'on' if _auto_restart else 'off'}"))
    elif cmd == "sessionmon":
        enabled = (args[1].lower() in ("on","enable")) if len(args) > 1 else True
        await message.edit(content=ui_ok(f"session monitor → {'on' if enabled else 'off'}"))
    elif cmd == "sessions":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "clear":
            _session_events.clear(); await message.edit(content=ui_ok("cleared"))
        else:
            rows = [f"  {DIM}{datetime.fromtimestamp(e['ts']).strftime('%H:%M:%S')}{RESET}  {e['event']}  {DIM}{e.get('user','') or ''}{e.get('count','') or ''}{RESET}"
                    for e in _session_events[-30:]]
            await message.edit(content=_paginate("sessions", "recent events", rows) if rows else ui_info("none"))
    elif cmd == "ratelimit":
        _rate_limit_tracking = (args[1].lower() in ("on","enable")) if len(args) > 1 else not _rate_limit_tracking
        await message.edit(content=ui_ok(f"ratelimit tracking → {'on' if _rate_limit_tracking else 'off'}"))
    elif cmd == "ratelimits":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "clear":
            _rate_limit_events.clear(); await message.edit(content=ui_ok("cleared"))
        else:
            rows = [f"  {DIM}{datetime.fromtimestamp(e['ts']).strftime('%H:%M:%S')}{RESET}  wait={e.get('wait')}s  {e.get('url','')[:40]}"
                    for e in _rate_limit_events[-30:]]
            await message.edit(content=_paginate("rate-limits", "recent", rows) if rows else ui_info("none"))
    elif cmd == "cache":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "stats":
            await message.edit(content=ui_box("cache", [
                f"  {DIM}snipe{RESET}     {sum(len(v) for v in _snipe_cache.values())}",
                f"  {DIM}editsnipe{RESET} {sum(len(v) for v in _editsnipe_cache.values())}",
                f"  {DIM}typing{RESET}    {len(_typing_tasks)}",
                f"  {DIM}tracking{RESET}  {sum(len(v) for v in _tracking.values())}",
                f"  {DIM}sessions{RESET}  {len(_session_events)}",
                f"  {DIM}ratelimits{RESET} {len(_rate_limit_events)}",
                f"  {DIM}auto-clean{RESET} {_cache_auto}",
            ]))
        elif sub == "clean":
            _snipe_cache.clear(); _editsnipe_cache.clear()
            for cid in list(_typing_tasks.keys()):
                try: _typing_tasks[cid].cancel()
                except Exception: pass
            _typing_tasks.clear()
            _tracking.clear(); _session_events.clear(); _rate_limit_events.clear()
            await message.edit(content=ui_ok("caches cleared"))
        elif sub == "auto":
            _cache_auto = (args[2].lower() in ("on","enable")) if len(args) > 2 else not _cache_auto
            await message.edit(content=ui_ok(f"cache auto → {'on' if _cache_auto else 'off'}"))
        else: await message.edit(content=build_help_section("resilience"))
    elif cmd == "queue":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "on": _queue_enabled = True; await message.edit(content=ui_ok("queue on"))
        elif sub == "off": _queue_enabled = False; await message.edit(content=ui_ok("queue off"))
        elif sub == "workers" and len(args) >= 3 and args[2].isdigit():
            _queue_workers = max(1, int(args[2])); await message.edit(content=ui_ok(f"workers → {_queue_workers}"))
        elif sub == "status":
            await message.edit(content=ui_box("queue", [
                f"  {DIM}enabled{RESET}  {_queue_enabled}",
                f"  {DIM}workers{RESET}  {_queue_workers}",
                f"  {DIM}pending{RESET}  {_cmd_queue.qsize() if _cmd_queue else 0}",
            ]))
        else: await message.edit(content=build_help_section("resilience"))

    # ─────────────────────────────────────────────
    # TASKS
    # ─────────────────────────────────────────────
    elif cmd == "task":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "list":
            rows = [f"  {GREY}•{RESET} {n}  {DIM}{'running' if not t.done() else 'done'}{RESET}" for n, t in _managed_tasks.items()]
            await message.edit(content=_paginate("tasks", "background", rows) if rows else ui_info("none"))
        elif sub == "cancel" and len(args) >= 3:
            ok = task_cancel(args[2]); await message.edit(content=ui_ok("cancelled") if ok else ui_err("not found"))
        elif sub == "register" and len(args) >= 3:
            name = args[2]
            async def _placeholder():
                while True: await asyncio.sleep(3600)
            ok = task_register(name, _placeholder())
            await message.edit(content=ui_ok("registered") if ok else ui_err("already exists"))
        elif sub == "save":
            tasks_save(); await message.edit(content=ui_ok("saved"))
        elif sub == "load":
            data = tasks_load()
            await message.edit(content=ui_ok(f"loaded {len(data)} task records"))
        elif sub == "clear":
            try: os.remove(_TASK_STORE)
            except Exception: pass
            await message.edit(content=ui_ok("cleared"))
        else: await message.edit(content=build_help_section("tasks"))

    # ─────────────────────────────────────────────
    # TRIGGERS
    # ─────────────────────────────────────────────
    elif cmd == "trigger":
        try: await message.delete()
        except Exception: pass
        if len(args) < 3: return await message.channel.send(build_help_section("triggers"))
        kind = args[1].lower(); action = args[2].lower()
        if kind == "message":
            if action == "add" and len(args) >= 5:
                rest = " ".join(args[3:])
                if "|" not in rest:
                    return await message.channel.send(ui_err("format: trigger message add <name> <contains> | <reply>"), delete_after=6)
                pre, reply = rest.split("|", 1)
                parts = pre.strip().split(" ", 1)
                if len(parts) < 2: return await message.channel.send(ui_err("missing contains"), delete_after=6)
                name, contains = parts[0], parts[1].strip()
                _triggers["message"].append({"name": name, "contains": contains, "reply": reply.strip()})
                triggers_save()
                await message.channel.send(ui_ok(f"message trigger `{name}` added"))
            elif action == "remove" and len(args) >= 4:
                name = args[3]; _triggers["message"] = [t for t in _triggers["message"] if t.get("name") != name]
                triggers_save(); await message.channel.send(ui_ok("removed"))
            elif action == "list":
                rows = [f"  {GREY}•{RESET} {t['name']}  {DIM}contains:{t.get('contains','')}{RESET}" for t in _triggers["message"]]
                await message.channel.send(_paginate("message triggers", "", rows) if rows else ui_info("none"))
            else: await message.channel.send(build_help_section("triggers"))
        elif kind == "reaction":
            if action == "add" and len(args) >= 6:
                rest = " ".join(args[3:])
                if "|" not in rest:
                    return await message.channel.send(ui_err("format: trigger reaction add <name> <emoji> <mode> | <reply>"), delete_after=6)
                pre, reply = rest.split("|", 1)
                parts = pre.strip().split()
                if len(parts) < 3: return await message.channel.send(ui_err("missing args"), delete_after=6)
                name, emoji, mode = parts[0], parts[1], parts[2]
                _triggers["reaction"].append({"name": name, "emoji": emoji, "mode": mode, "reply": reply.strip()})
                triggers_save()
                await message.channel.send(ui_ok(f"reaction trigger `{name}` added"))
            elif action == "remove" and len(args) >= 4:
                name = args[3]; _triggers["reaction"] = [t for t in _triggers["reaction"] if t.get("name") != name]
                triggers_save(); await message.channel.send(ui_ok("removed"))
            elif action == "list":
                rows = [f"  {GREY}•{RESET} {t['name']}  {DIM}{t.get('emoji')} → {t.get('mode')}{RESET}" for t in _triggers["reaction"]]
                await message.channel.send(_paginate("reaction triggers", "", rows) if rows else ui_info("none"))
            else: await message.channel.send(build_help_section("triggers"))
        elif kind == "voice":
            if action == "add" and len(args) >= 6:
                name = args[3]; event = args[4]; ch_id = args[5]
                _triggers["voice"].append({"name": name, "event": event, "channel_id": ch_id})
                triggers_save(); await message.channel.send(ui_ok(f"voice trigger `{name}` added"))
            elif action == "remove" and len(args) >= 4:
                name = args[3]; _triggers["voice"] = [t for t in _triggers["voice"] if t.get("name") != name]
                triggers_save(); await message.channel.send(ui_ok("removed"))
            elif action == "list":
                rows = [f"  {GREY}•{RESET} {t['name']}  {DIM}{t.get('event')} → {t.get('channel_id')}{RESET}" for t in _triggers["voice"]]
                await message.channel.send(_paginate("voice triggers", "", rows) if rows else ui_info("none"))
            else: await message.channel.send(build_help_section("triggers"))
        elif kind == "member":
            if action == "add" and len(args) >= 5:
                name = args[3]; event = args[4]
                _triggers["member"].append({"name": name, "event": event})
                triggers_save(); await message.channel.send(ui_ok(f"member trigger `{name}` added"))
            elif action == "remove" and len(args) >= 4:
                name = args[3]; _triggers["member"] = [t for t in _triggers["member"] if t.get("name") != name]
                triggers_save(); await message.channel.send(ui_ok("removed"))
            elif action == "list":
                rows = [f"  {GREY}•{RESET} {t['name']}  {DIM}{t.get('event')}{RESET}" for t in _triggers["member"]]
                await message.channel.send(_paginate("member triggers", "", rows) if rows else ui_info("none"))
            else: await message.channel.send(build_help_section("triggers"))
        else: await message.channel.send(build_help_section("triggers"))
    elif cmd == "triggers":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "status":
            rows = [f"  {GREY}•{RESET} {n}: {c}" for n, c in _trigger_fired_counts.items()]
            await message.edit(content=_paginate("trigger counts", "", rows) if rows else ui_info("nothing fired yet"))
        elif sub == "clear":
            _triggers = {"message": [], "reaction": [], "voice": [], "member": []}
            _trigger_fired_counts.clear(); triggers_save()
            await message.edit(content=ui_ok("cleared"))
        else: await message.edit(content=build_help_section("triggers"))

    # ─────────────────────────────────────────────
    # GENERAL
    # ─────────────────────────────────────────────
    elif cmd == "ping":
        await message.edit(content=ui_ok(f"pong — `{round(client.latency*1000)}ms`"))
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
        if len(args) < 3: return await message.edit(content=ui_err("usage: spam <n> <text>"))
        try: count = int(args[1])
        except ValueError: return await message.edit(content=ui_err("n must be a number"))
        count = min(count, 200); text = " ".join(args[2:]); cid = message.channel.id
        ex = _spam_tasks.get(cid)
        if ex and not ex.done():
            ex.cancel()
            try: await ex
            except Exception: pass
        try: await message.delete()
        except Exception: pass
        _spam_tasks[cid] = asyncio.create_task(_spam_worker(message.channel, count, text))
    elif cmd == "spamstop":
        cid = message.channel.id; t = _spam_tasks.get(cid)
        if not t or t.done():
            killed = 0
            for ch_id, tt in list(_spam_tasks.items()):
                if tt and not tt.done(): tt.cancel(); killed += 1
            _spam_tasks.clear()
            await message.edit(content=ui_ok(f"stopped {killed}") if killed else ui_info("no active spam"))
            return
        t.cancel()
        try: await t
        except Exception: pass
        _spam_tasks.pop(cid, None)
        await message.edit(content=ui_ok("spam stopped"))
    elif cmd == "purge":
        limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
        try: await message.delete()
        except Exception: pass
        d = 0
        async for msg in message.channel.history(limit=500):
            if msg.author.id == client.user.id:
                try: await msg.delete()
                except Exception: pass
                d += 1; await asyncio.sleep(0.3)
                if d >= limit: break
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
        sub = args[1].lower() if len(args) > 1 else ""; cid = message.channel.id
        if sub == "clear": _snipe_cache.pop(cid, None); return await message.channel.send(ui_ok("snipe cache cleared"), delete_after=4)
        entries = _snipe_cache.get(cid, [])
        if not entries: return await message.channel.send(ui_info("nothing to snipe"), delete_after=5)
        try: idx = int(sub) if sub else 1
        except ValueError: idx = 1
        if idx < 1 or idx > len(entries): return await message.channel.send(ui_err(f"range 1–{len(entries)}"), delete_after=5)
        e = entries[-idx]; atts = "\n".join(e.get("attachments", [])) or "none"
        await message.channel.send(ui_box(f"sniped #{idx}/{len(entries)}", [
            f"  {DIM}author{RESET}      {WHITE}{e['author']}{RESET}",
            f"  {DIM}deleted{RESET}     {e['time']}",
            f"  {DIM}attachments{RESET} {atts}", "",
            f"  {WHITE}{e['content'] or '(no content)'}{RESET}",
        ]))
    elif cmd in ("editsnipe", "esnipe"):
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""; cid = message.channel.id
        if sub == "clear": _editsnipe_cache.pop(cid, None); return await message.channel.send(ui_ok("editsnipe cache cleared"), delete_after=4)
        entries = _editsnipe_cache.get(cid, [])
        if not entries: return await message.channel.send(ui_info("no edits"), delete_after=5)
        try: idx = int(sub) if sub else 1
        except ValueError: idx = 1
        if idx < 1 or idx > len(entries): return await message.channel.send(ui_err(f"range 1–{len(entries)}"), delete_after=5)
        e = entries[-idx]
        await message.channel.send(ui_box(f"sniped edit #{idx}/{len(entries)}", [
            f"  {DIM}author{RESET}  {WHITE}{e['author']}{RESET}", f"  {DIM}edited{RESET}  {e['time']}", "",
            f"  {DIM}before:{RESET}", f"  {WHITE}{e['before'] or '(empty)'}{RESET}", "",
            f"  {DIM}after:{RESET}", f"  {WHITE}{e['after'] or '(empty)'}{RESET}",
        ]))
    elif cmd == "copycat":
        if len(args) < 2: return await message.edit(content=ui_err("usage: copycat <user_id>"))
        try: uid = int(args[1])
        except ValueError: return await message.edit(content=ui_err("invalid user id"))
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
        if len(args) < 2: return await message.edit(content=ui_info(f"platform: {_current_platform}\ntypes: {' '.join(PLATFORM_MAP)}"))
        plat = "desktop" if args[1].lower() == "off" else args[1].lower()
        if plat not in PLATFORM_MAP: return await message.edit(content=ui_err(f"unknown platform: {plat}"))
        _current_platform = plat
        await message.edit(content=ui_ok(f"platform → {plat}"))
        try: await _MAIN_CLIENT.ws.close(code=4000)
        except Exception: pass
    elif cmd == "hypesquad":
        if len(args) < 2: return await message.edit(content=ui_err("usage: hypesquad bravery/brilliance/balance/off"))
        sub = args[1].lower()
        if sub == "off":
            ok = await clear_hypesquad(); await message.edit(content=ui_ok("removed") if ok else ui_err("failed"))
        elif sub in HOUSE_IDS:
            ok, _ = await set_hypesquad(HOUSE_IDS[sub])
            await message.edit(content=ui_ok(f"house {HOUSE_NAMES[HOUSE_IDS[sub]]}") if ok else ui_err("failed"))
        else: await message.edit(content=ui_err("unknown house"))

    # SNIPER / LOGGER
    elif cmd == "sniper":
        SNIPER_ENABLED = len(args) < 2 or args[1].lower() == "on"
        await message.edit(content=ui_ok(f"sniper → {'ON' if SNIPER_ENABLED else 'OFF'}"))
    elif cmd == "logger":
        LOGGER_ENABLED = len(args) < 2 or args[1].lower() == "on"
        await message.edit(content=ui_ok(f"logger → {'ON' if LOGGER_ENABLED else 'OFF'}"))
    elif cmd in ("readlog", "logs"):
        n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
        if not os.path.exists(LOG_FILE): return await message.edit(content=ui_err("no log"))
        with open(LOG_FILE, "r", encoding="utf-8") as f: lines = f.readlines()
        tail = "".join(lines[-n:])
        if len(tail) > 1900: tail = tail[-1900:]
        await message.edit(content=f"```\n{tail}\n```")

    # AR
    elif cmd == "ar":
        sub = args[1].lower() if len(args) > 1 else ""; rest = " ".join(args[2:])
        if sub == "add":
            if "|" not in rest: return await message.edit(content=ui_err("format: ar add trigger | response"))
            trig, resp = rest.split("|", 1); AUTO_RESPONSES[trig.strip()] = resp.strip()
            await message.edit(content=ui_ok(f"added: `{trig.strip()}`"))
        elif sub == "remove":
            AUTO_RESPONSES.pop(rest.strip(), None); await message.edit(content=ui_ok(f"removed: `{rest.strip()}`"))
        elif sub == "list":
            rows = [f"  {GREY}├{RESET} {k}  {DIM}→ {v}{RESET}" for k, v in list(AUTO_RESPONSES.items())]
            await message.edit(content=_paginate("ar", "auto-responder", rows) if rows else ui_info("none set"))
        else: await message.edit(content=ui_err("usage: ar add/remove/list"))

    # QUESTS
    elif cmd == "quest":
        try: await message.delete()
        except Exception: pass
        svc = QuestService(TOKEN)
        async with aiohttp.ClientSession() as session: quests = await svc.fetch(session)
        if not quests: return await message.channel.send(ui_err("no quests"), delete_after=8)
        rows = []
        for i, q in enumerate(quests):
            tag = f"{GREEN}done{RESET}" if q.is_completed() else (f"{CYAN}ok{RESET}" if q.is_supported() else f"{RED}unsupported{RESET}")
            rows.append(f"  {GREY}[{i}]{RESET} {WHITE}{q.name}{RESET}  {tag}")
            rows.append(f"       {ui_progress(q.reward, q.progress_pct())}")
        await message.channel.send(_paginate("quests", "active", rows))
    elif cmd == "questrun":
        try: await message.delete()
        except Exception: pass
        idx = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
        svc = QuestService(TOKEN)
        async with aiohttp.ClientSession() as session:
            quests = await svc.fetch(session)
            if not quests or idx >= len(quests): return await message.channel.send(ui_err("index out of range"), delete_after=6)
            q = quests[idx]
            if q.is_completed(): return await message.channel.send(ui_ok("already done"), delete_after=6)
            await message.channel.send(ui_info(f"started {q.name}"), delete_after=5)
            res = await svc.run(session, q)
            if res == "completed": await message.channel.send(ui_ok(f"{q.name} complete"), delete_after=10)
    elif cmd == "questall":
        try: await message.delete()
        except Exception: pass
        svc = QuestService(TOKEN)
        async with aiohttp.ClientSession() as session:
            quests = await svc.fetch(session)
            active = [q for q in quests if not q.is_completed() and q.is_supported()]
            if not active: return await message.channel.send(ui_err("no active quests"), delete_after=6)
            await message.channel.send(ui_info(f"{len(active)} queued"), delete_after=5)
            async def _run(q):
                res = await svc.run(session, q)
                if res == "completed": await message.channel.send(ui_ok(f"{q.name} ✓"), delete_after=10)
            await asyncio.gather(*[_run(q) for q in active])
    elif cmd == "autoquest":
        try: await message.delete()
        except Exception: pass
        cfg = load_config(); on = len(args) < 2 or args[1].lower() in ("on","enable")
        cfg["autoquest_enabled"] = on; save_config(cfg)
        if on: asyncio.create_task(autoquest_run(TOKEN))
        await message.channel.send(ui_ok(f"autoquest → {'on' if on else 'off'}"), delete_after=5)
    elif cmd == "autoclaim":
        _autoclaim_enabled = len(args) < 2 or args[1].lower() in ("on","enable")
        await message.edit(content=ui_ok(f"autoclaim → {'on' if _autoclaim_enabled else 'off'}"))
    elif cmd == "captcha":
        if len(args) < 3 or args[1].lower() != "set": return await message.edit(content=ui_err("usage: captcha set <key>"))
        _captcha_key = args[2].strip(); cfg = load_config(); cfg["captcha_key"] = _captcha_key; save_config(cfg)
        await message.edit(content=ui_ok("2captcha key saved"))
    elif cmd == "orbbadge":
        try: await message.delete()
        except Exception: pass
        ok, text = await claim_orb(TOKEN)
        await message.channel.send(ui_ok("claimed") if ok else ui_err(f"failed: {text[:80]}"), delete_after=8)

    # VOICE
    elif cmd == "vcjoin":
        try: await message.delete()
        except Exception: pass
        if len(args) < 2: return await message.channel.send(ui_err("usage: vcjoin <ch_id>"), delete_after=5)
        try:
            ch = client.get_channel(int(args[1]))
            if not ch: return await message.channel.send(ui_err("channel not found"), delete_after=5)
            if message.guild and message.guild.voice_client: await message.guild.voice_client.disconnect(force=True)
            await ch.connect(self_deaf=True)
            await message.channel.send(ui_ok(f"joined {ch.name}"), delete_after=5)
        except Exception as e: await message.channel.send(ui_err(str(e)), delete_after=5)
    elif cmd == "vcleave":
        try: await message.delete()
        except Exception: pass
        if message.guild and message.guild.voice_client:
            name = message.guild.voice_client.channel.name
            await message.guild.voice_client.disconnect(force=True)
            await message.channel.send(ui_ok(f"left {name}"), delete_after=5)
        else: await message.channel.send(ui_err("not in a vc"), delete_after=5)
    elif cmd in ("vcmute","vcunmute","vcdeafen","vcundeafen","vckick"):
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 2: return await message.channel.send(ui_err(f"usage: {cmd} <user_id>"), delete_after=5)
        try:
            member = message.guild.get_member(int(args[1]))
            if not member or not member.voice: return await message.channel.send(ui_err("user not in vc"), delete_after=5)
            if cmd == "vcmute": await member.edit(mute=True)
            elif cmd == "vcunmute": await member.edit(mute=False)
            elif cmd == "vcdeafen": await member.edit(deafen=True)
            elif cmd == "vcundeafen": await member.edit(deafen=False)
            elif cmd == "vckick": await member.move_to(None)
            await message.channel.send(ui_ok(f"{cmd} → {member.name}"), delete_after=5)
        except Exception as e: await message.channel.send(ui_err(str(e)), delete_after=5)
    elif cmd == "vcmove":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 3: return await message.channel.send(ui_err("usage: vcmove <user_id> <ch_id>"), delete_after=5)
        try:
            member = message.guild.get_member(int(args[1])); ch = client.get_channel(int(args[2]))
            await member.move_to(ch); await message.channel.send(ui_ok(f"moved {member.name}"), delete_after=5)
        except Exception as e: await message.channel.send(ui_err(str(e)), delete_after=5)
    elif cmd == "vcmoveall":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 3: return await message.channel.send(ui_err("usage: vcmoveall <ch1> <ch2>"), delete_after=5)
        try:
            ch1 = client.get_channel(int(args[1])); ch2 = client.get_channel(int(args[2]))
            for m in list(ch1.members):
                await m.move_to(ch2); await asyncio.sleep(0.2)
            await message.channel.send(ui_ok(f"moved all"), delete_after=5)
        except Exception as e: await message.channel.send(ui_err(str(e)), delete_after=5)
    elif cmd in ("selfmute","selfdeaf","selfstream","selfcamera"):
        try: await message.delete()
        except Exception: pass
        if not message.guild: return await message.channel.send(ui_err("must be in a server"), delete_after=5)
        me = message.guild.me
        vs = me.voice if me else None
        if not vs or not vs.channel: return await message.channel.send(ui_err("not in a vc"), delete_after=5)
        cur_mute = vs.self_mute; cur_deaf = vs.self_deaf; cur_stream = vs.self_stream; cur_video = vs.self_video
        new_mute = (not cur_mute) if cmd == "selfmute" else cur_mute
        new_deaf = (not cur_deaf) if cmd == "selfdeaf" else cur_deaf
        new_stream = (not cur_stream) if cmd == "selfstream" else cur_stream
        new_video = (not cur_video) if cmd == "selfcamera" else cur_video
        try:
            await client.ws.send_as_json({"op": 4, "d": {
                "guild_id": str(message.guild.id), "channel_id": str(vs.channel.id),
                "self_mute": new_mute, "self_deaf": new_deaf,
                "self_stream": new_stream, "self_video": new_video,
            }})
            state_map = {"selfmute":"muted" if new_mute else "unmuted",
                         "selfdeaf":"deafened" if new_deaf else "undeafened",
                         "selfstream":"streaming" if new_stream else "stopped",
                         "selfcamera":"camera on" if new_video else "camera off"}
            await message.channel.send(ui_ok(f"self {state_map[cmd]}"), delete_after=5)
        except Exception as e: await message.channel.send(ui_err(f"failed: {e}"), delete_after=6)

    # FUN
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
        else: await message.channel.send(ui_err("could not fetch image"), delete_after=5)
    elif cmd == "meme":
        try: await message.delete()
        except Exception: pass
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://meme-api.com/gimme") as r:
                    if r.status == 200: await message.channel.send((await r.json()).get("url","no meme"))
        except Exception as e: await message.channel.send(ui_err(str(e)), delete_after=5)
    elif cmd == "joke":
        try: await message.delete()
        except Exception: pass
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://official-joke-api.appspot.com/random_joke") as r:
                    if r.status == 200:
                        j = await r.json()
                        await message.channel.send(f"**{j['setup']}**\n||{j['punchline']}||")
        except Exception as e: await message.channel.send(ui_err(str(e)), delete_after=5)
    elif cmd == "mimic":
        try: await message.delete()
        except Exception: pass
        if len(args) < 2: return await message.channel.send(ui_err("usage: mimic <user_id>"), delete_after=5)
        uid = int(args[1]); cid = message.channel.id
        _mimic_dict.setdefault(cid, [])
        if uid not in _mimic_dict[cid]: _mimic_dict[cid].append(uid)
        await message.channel.send(ui_ok(f"mimicking <@{uid}>"), delete_after=5)
    elif cmd == "unmimic":
        try: await message.delete()
        except Exception: pass
        if len(args) < 2: return await message.channel.send(ui_err("usage: unmimic <user_id>"), delete_after=5)
        uid = int(args[1]); cid = message.channel.id
        if cid in _mimic_dict and uid in _mimic_dict[cid]:
            _mimic_dict[cid].remove(uid)
            if not _mimic_dict[cid]: del _mimic_dict[cid]
        await message.channel.send(ui_ok("stopped"), delete_after=5)
    elif cmd == "stopmimic":
        try: await message.delete()
        except Exception: pass
        _mimic_dict.clear()
        await message.channel.send(ui_ok("all stopped"), delete_after=5)

    # TOOLS
    elif cmd == "nitro":
        try: await message.delete()
        except Exception: pass
        code = "".join(random.choices(string.ascii_letters + string.digits, k=16))
        await message.channel.send(f"```\nhttps://discord.gift/{code}\n```")
    elif cmd == "applybypass":
        try: await message.delete()
        except Exception: pass
        if len(args) < 2: return await message.channel.send(ui_err("usage: applybypass <invite>"), delete_after=5)
        invite = args[1].replace("https://discord.gg/","").replace("discord.gg/","")
        h = {"Authorization": TOKEN, "Content-Type":"application/json", "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://discord.com/api/v9/invites/{invite}", headers=h) as r:
                    if r.status != 200: return await message.channel.send(ui_err("invalid invite"), delete_after=6)
                    inv = await r.json()
                guild_id = inv.get("guild", {}).get("id")
                async with s.post(f"https://discord.com/api/v9/invites/{invite}",
                                  headers=h, json={"session_id": str(uuid4())[:8]}) as r2:
                    if r2.status in (200,204): await message.channel.send(ui_ok("joined"), delete_after=8)
                    elif r2.status == 403 and guild_id:
                        async with s.put(f"https://discord.com/api/v9/guilds/{guild_id}/requests/@me",
                                         headers=h, json={"form_fields":[]}) as r3:
                            await message.channel.send(ui_ok("applied") if r3.status in (200,201,204) else ui_err(f"failed {r3.status}"), delete_after=8)
                    else: await message.channel.send(ui_err(f"failed {r2.status}"), delete_after=6)
        except Exception as e: await message.channel.send(ui_err(str(e)), delete_after=6)
    elif cmd == "tokeninfo":
        token = args[1] if len(args) > 1 else ""
        if not token: return await message.edit(content=ui_err("usage: tokeninfo <token>"))
        try:
            parts = token.split("."); uid_b64 = parts[0]
            uid = base64.b64decode(uid_b64 + "=" * (-len(uid_b64) % 4)).decode()
            ts_b64 = parts[1]; pad = ts_b64 + "=" * (-len(ts_b64) % 4)
            ts_bytes = base64.b64decode(pad); epoch = int.from_bytes(ts_bytes[:4], "big")
            created = datetime.utcfromtimestamp(epoch + 1293840000).strftime("%Y-%m-%d %H:%M:%S")
            await message.edit(content=ui_box("token info", [
                f"  {DIM}user_id{RESET}  {uid}", f"  {DIM}created{RESET}  {created} UTC",
            ]))
        except Exception as e: await message.edit(content=ui_err(f"decode failed: {e}"))
    elif cmd == "calculate":
        if len(args) < 2: return await message.edit(content=ui_err("usage: calculate <expr>"))
        expr = " ".join(args[1:])
        try:
            result = eval(re.sub(r"[^0-9+\-*/(). ]", "", expr))
            await message.edit(content=ui_ok(f"{expr} = {result}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "fact":
        try: await message.delete()
        except Exception: pass
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://uselessfacts.jsph.pl/api/v2/facts/random?language=en") as r:
                    d = await r.json()
                    await message.channel.send(f"💡 {d.get('text','no fact')}")
        except Exception as e: await message.channel.send(ui_err(str(e)), delete_after=5)
    elif cmd == "fetchlyrics":
        if len(args) < 2: return await message.edit(content=ui_err("usage: fetchlyrics <artist - title>"))
        query = " ".join(args[1:])
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://lyrist.vercel.app/api/{query.replace(' - ','/')}") as r:
                    if r.status == 200:
                        lyrics = (await r.json()).get("lyrics","")[:1800]
                        await message.edit(content=f"```\n{lyrics}\n```")
                    else: await message.edit(content=ui_err("not found"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "robuxtax":
        if len(args) < 2: return await message.edit(content=ui_err("usage: robuxtax <amount>"))
        try:
            amount = int(args[1]); after = int(amount * 0.7); fee = amount - after
            await message.edit(content=ui_box("roblox fee", [
                f"  {DIM}listed{RESET}  {amount:,} R$", f"  {DIM}fee{RESET}     {fee:,} R$",
                f"  {DIM}you get{RESET} {after:,} R$",
            ]))
        except ValueError: await message.edit(content=ui_err("invalid amount"))
    elif cmd == "archivechannel":
        try: await message.delete()
        except Exception: pass
        ch = client.get_channel(int(args[1])) if len(args) > 1 and args[1].isdigit() else message.channel
        if not ch: return await message.channel.send(ui_err("not found"), delete_after=5)
        count = 0; out = []
        async for msg in ch.history(limit=2000):
            out.append(f"[{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {msg.author}: {msg.content}"); count += 1
        fname = f"exports/archive_{ch.id}.txt"
        with open(fname, "w", encoding="utf-8") as f: f.write("\n".join(reversed(out)))
        await message.channel.send(ui_ok(f"archived {count} → {fname}"), delete_after=8)

    # HOST
    elif cmd == "host":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add":
            if len(args) < 3: return await message.edit(content=ui_err("usage: host add <token>"))
            t = args[2].strip().strip('"').strip("'")
            if t in HOSTED_TOKENS:
                return await message.edit(content=ui_err("already in hosted list"))
            uname = await hosted_username(t)
            if not uname or uname == "?":
                return await message.edit(content=ui_err("invalid or dead token"))
            try:
                await async_hosted_token_add(t, username=uname)
                HOSTED_TOKENS.append(t)
                await message.edit(content=ui_ok(f"✓ hosted {uname}"))
                print(f"[host] added {uname} ({t[:20]}...)")
            except Exception as e:
                await message.edit(content=ui_err(f"write failed: {e}"))
                print(f"[host] error adding token: {e}")

        elif sub == "remove":
            if len(args) < 3: return await message.edit(content=ui_err("usage: host remove <token_or_index>"))
            ref = args[2].strip().strip('"').strip("'")
            target = None
            if ref.isdigit():
                idx = int(ref)
                if 0 <= idx < len(HOSTED_TOKENS): target = HOSTED_TOKENS[idx]
            elif ref in HOSTED_TOKENS:
                target = ref
            if not target:
                return await message.edit(content=ui_err("not in hosted list"))
            try:
                await async_hosted_token_remove(target)
                HOSTED_TOKENS.remove(target)
                await message.edit(content=ui_ok("✓ removed"))
                print("[host] removed token")
            except Exception as e:
                await message.edit(content=ui_err(f"delete failed: {e}"))
                print(f"[host] error removing token: {e}")

        elif sub == "list":
            if not HOSTED_TOKENS:
                return await message.edit(content=ui_err("no hosted accounts"))
            rows = []
            for i, t in enumerate(HOSTED_TOKENS):
                uname = await hosted_username(t)
                rows.append(f"  {GREY}[{i}]{RESET} {WHITE}{uname}{RESET}  {DIM}{t[:20]}...{RESET}")
            await message.edit(content=_paginate("host", "hosted accounts", rows))

        elif sub == "info":
            if len(args) < 3 or not args[2].isdigit():
                return await message.edit(content=ui_err("usage: host info <index>"))
            idx = int(args[2])
            if idx >= len(HOSTED_TOKENS):
                return await message.edit(content=ui_err("index out of range"))
            t = HOSTED_TOKENS[idx]
            info = await hosted_info(t)
            await message.edit(content=ui_box("hosted account", [
                f"  {DIM}index{RESET}     [{idx}]",
                f"  {DIM}username{RESET}  {info['username']}",
                f"  {DIM}id{RESET}        {info['id']}",
                f"  {DIM}valid{RESET}     {'yes' if info['valid'] else 'no'}",
                f"  {DIM}token{RESET}     {t[:12]}...{t[-6:]}",
            ]))

        elif sub == "broadcast":
            if len(args) < 3: return await message.edit(content=ui_err("usage: host broadcast <msg>"))
            text = " ".join(args[2:]); ok = 0; failed = 0
            if not HOSTED_TOKENS:
                return await message.edit(content=ui_err("no hosted tokens"))
            for t in HOSTED_TOKENS:
                try:
                    if await hosted_send(t, message.channel.id, text):
                        ok += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
                await asyncio.sleep(0.5)
            await message.edit(content=ui_ok(f"sent from {ok}/{len(HOSTED_TOKENS)}" + (f" ({failed} failed)" if failed else "")))

        elif sub == "say":
            if len(args) < 4: return await message.edit(content=ui_err("usage: host say <idx> <msg>"))
            try:
                idx = int(args[2])
                if idx < 0 or idx >= len(HOSTED_TOKENS):
                    return await message.edit(content=ui_err(f"index 0–{len(HOSTED_TOKENS)-1}"))
                t = HOSTED_TOKENS[idx]
            except ValueError:
                return await message.edit(content=ui_err("invalid index"))
            ok = await hosted_send(t, message.channel.id, " ".join(args[3:]))
            await message.edit(content=ui_ok("sent") if ok else ui_err("failed"))

        elif sub == "clear":
            count = len(HOSTED_TOKENS)
            for t in list(HOSTED_TOKENS):
                try: await async_hosted_token_remove(t)
                except Exception: pass
            HOSTED_TOKENS.clear()
            await message.edit(content=ui_ok(f"cleared {count} hosted account(s)"))

        else:
            await message.edit(content=build_help_section("host"))

    # LASTFM
    elif cmd == "lastfm":
        sub = args[1].lower() if len(args) > 1 else ""
        if not sub or sub == "help":
            try: await message.delete()
            except Exception: pass
            await message.channel.send(build_help_section("lastfm"))
        elif sub == "set":
            if len(args) < 3: return await message.edit(content=ui_err("usage: lastfm set <username> [api_key]"))
            _lfm["username"] = args[2].strip()
            if len(args) > 3: _lfm["api_key"] = args[3].strip()
            _save_lfm(); await message.edit(content=ui_ok(f"linked {_lfm['username']}"))
        elif sub in ("np","nowplaying"):
            try: await message.delete()
            except Exception: pass
            u = _lfm.get("username","")
            if not u: return await message.channel.send(ui_err("set username first"), delete_after=8)
            t = await lfm_np(u)
            if not t: return await message.channel.send(ui_info("nothing"), delete_after=6)
            rows = [f"  {DIM}{'▶ now playing' if t['playing'] else '⏸ last played'}{RESET}", "",
                    f"  {WHITE}{t['title']}{RESET}", f"  {DIM}by{RESET} {t['artist']}"]
            if t["album"]: rows.append(f"  {DIM}album{RESET} {t['album']}")
            rows.append(f"  {DIM}scrobbles{RESET} {t['total']}")
            await message.channel.send(_ansi_block(rows))
        elif sub == "recent":
            try: await message.delete()
            except Exception: pass
            u = _lfm.get("username","")
            if not u: return await message.channel.send(ui_err("set username first"), delete_after=6)
            n = int(args[2]) if len(args) > 2 and args[2].isdigit() else 5
            d = await lfm_get("user.getRecentTracks", {"user": u, "limit": min(n,15)})
            tracks = d.get("recenttracks", {}).get("track", [])
            if not tracks: return await message.channel.send(ui_info("no recent"), delete_after=6)
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
            if not u: return await message.channel.send(ui_err("set username first"), delete_after=6)
            period_raw = args[2].lower() if len(args) > 2 else "overall"
            period = _PERIOD.get(period_raw, "overall"); label = _PLABEL.get(period, "all time")
            mm = {"topartists":("user.getTopArtists","topartists","artist"),
                  "artists":("user.getTopArtists","topartists","artist"),
                  "toptracks":("user.getTopTracks","toptracks","track"),
                  "tracks":("user.getTopTracks","toptracks","track"),
                  "topalbums":("user.getTopAlbums","topalbums","album"),
                  "albums":("user.getTopAlbums","topalbums","album")}
            method, key, ik = mm[sub]
            d = await lfm_get(method, {"user": u, "period": period, "limit": 10})
            items = d.get(key, {}).get(ik, [])
            if not items: return await message.channel.send(ui_info("no data"), delete_after=6)
            maxp = int(items[0].get("playcount", 1)) or 1
            rows = []
            for i, it in enumerate(items[:10], 1):
                name = it.get("name","?"); plays = int(it.get("playcount", 0)); pct = int(plays / maxp * 100)
                bar = f"{GREEN}{'▓' * (pct // 10)}{GREY}{'░' * (10 - pct // 10)}{RESET}"
                extra = ""
                if "artist" in it and isinstance(it["artist"], dict):
                    extra = f"  {DIM}— {it['artist'].get('name','')}{RESET}"
                rows.append(f"  {GREY}{i:2}.{RESET} {bar} {DIM}{plays:>5}{RESET}  {WHITE}{name}{RESET}{extra}")
            await message.channel.send(_paginate(sub, f"{u} — {label}", rows))
        elif sub == "stats":
            try: await message.delete()
            except Exception: pass
            u = _lfm.get("username","")
            if not u: return await message.channel.send(ui_err("set username first"), delete_after=6)
            d = await lfm_get("user.getInfo", {"user": u}); ud = d.get("user",{})
            if not ud: return await message.channel.send(ui_err("not found"), delete_after=6)
            await message.channel.send(_ansi_block([f"  {WHITE}{u}{RESET}", "",
                f"  {DIM}scrobbles{RESET}  {ud.get('playcount','?')}",
                f"  {DIM}artists{RESET}    {ud.get('artist_count','?')}",
                f"  {DIM}albums{RESET}     {ud.get('album_count','?')}",
                f"  {DIM}tracks{RESET}     {ud.get('track_count','?')}",
                f"  {DIM}country{RESET}    {ud.get('country','?')}"]))
        elif sub == "compare":
            try: await message.delete()
            except Exception: pass
            u = _lfm.get("username","")
            if not u or len(args) < 3: return await message.channel.send(ui_err("usage: lastfm compare <user>"), delete_after=6)
            other = args[2]
            d = await lfm_get("tasteometer.compare", {"type1":"user","type2":"user","value1":u,"value2":other,"limit":5})
            r = d.get("comparison",{}).get("result",{}); score = float(r.get("score",0)) * 100
            artists = r.get("artists",{}).get("artist",[])
            if isinstance(artists, dict): artists = [artists]
            bar = f"{GREEN}{'▓'*int(score/10)}{GREY}{'░'*(10-int(score/10))}{RESET}"
            rows = [f"  {WHITE}{u}{RESET} vs {WHITE}{other}{RESET}", f"  {bar}  {DIM}{score:.1f}% compatible{RESET}"]
            if artists:
                rows += ["", f"  {DIM}shared:{RESET}"] + [f"    {GREY}•{RESET} {a.get('name','?') if isinstance(a,dict) else a}" for a in artists[:5]]
            await message.channel.send(_ansi_block(rows))
        else:
            try: await message.delete()
            except Exception: pass
            await message.channel.send(build_help_section("lastfm"))

    # MASS
    elif cmd == "massdm":
        try: await message.delete()
        except Exception: pass
        if not message.guild: return await message.channel.send(ui_err("server only"), delete_after=5)
        if len(args) < 2: return await message.channel.send(ui_err("usage: massdm <msg>"), delete_after=5)
        txt = " ".join(args[1:])
        await message.channel.send(ui_info("mass dm started"))
        done = await mass_dm(message.guild, txt)
        await message.channel.send(ui_ok(f"sent to {done} members"))
    elif cmd == "massdmfile":
        try: await message.delete()
        except Exception: pass
        if len(args) < 3 or not os.path.exists(args[1]):
            return await message.channel.send(ui_err("usage: massdmfile <path> <msg>"), delete_after=5)
        txt = " ".join(args[2:])
        with open(args[1]) as f: ids = [l.strip() for l in f if l.strip()]
        done = 0
        for uid in ids:
            try:
                u = await client.fetch_user(int(uid))
                await u.send(txt); done += 1
            except Exception: pass
            await asyncio.sleep(1.2)
        await message.channel.send(ui_ok(f"sent to {done}/{len(ids)}"))
    elif cmd == "massfriend":
        try: await message.delete()
        except Exception: pass
        if len(args) < 2 or not os.path.exists(args[1]):
            return await message.channel.send(ui_err("usage: massfriend <file>"), delete_after=5)
        with open(args[1]) as f: ids = [l.strip() for l in f if l.strip()]
        done = await mass_friend_ids(ids)
        await message.channel.send(ui_ok(f"sent {done}/{len(ids)}"))
    elif cmd == "massjoin":
        try: await message.delete()
        except Exception: pass
        if len(args) < 3: return await message.channel.send(ui_err("usage: massjoin <invite> <count>"), delete_after=5)
        invite = args[1].replace("https://discord.gg/","").replace("discord.gg/","")
        count = int(args[2]) if args[2].isdigit() else 1
        tokens = HOSTED_TOKENS[:count]
        done = await mass_join(invite, tokens)
        await message.channel.send(ui_ok(f"joined {done}/{len(tokens)}"))
    elif cmd == "massleave":
        try: await message.delete()
        except Exception: pass
        if len(args) < 2: return await message.channel.send(ui_err("usage: massleave <guild_id>"), delete_after=5)
        done = await mass_leave(args[1], HOSTED_TOKENS)
        await message.channel.send(ui_ok(f"left {done}/{len(HOSTED_TOKENS)}"))
    elif cmd == "massrole":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 3:
            return await message.channel.send(ui_err("usage: massrole <role_id> <uids...>"), delete_after=5)
        done = await mass_role(message.guild, args[1], args[2:])
        await message.channel.send(ui_ok(f"role assigned to {done}"))
    elif cmd == "massunrole":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 3:
            return await message.channel.send(ui_err("usage: massunrole <role_id> <uids...>"), delete_after=5)
        done = await mass_unrole(message.guild, args[1], args[2:])
        await message.channel.send(ui_ok(f"removed from {done}"))
    elif cmd == "massban":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 2:
            return await message.channel.send(ui_err("usage: massban <uids...>"), delete_after=5)
        done = await mass_ban(message.guild, args[1:])
        await message.channel.send(ui_ok(f"banned {done}"))
    elif cmd == "masskick":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 2:
            return await message.channel.send(ui_err("usage: masskick <uids...>"), delete_after=5)
        done = await mass_kick(message.guild, args[1:])
        await message.channel.send(ui_ok(f"kicked {done}"))
    elif cmd == "massch":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 3:
            return await message.channel.send(ui_err("usage: massch <name> <n>"), delete_after=5)
        n = int(args[2]) if args[2].isdigit() else 5
        for _ in range(min(n, 50)):
            try: await message.guild.create_text_channel(args[1])
            except Exception: pass
        await message.channel.send(ui_ok(f"created {min(n,50)}"))
    elif cmd == "massvc":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 3:
            return await message.channel.send(ui_err("usage: massvc <name> <n>"), delete_after=5)
        n = int(args[2]) if args[2].isdigit() else 5
        for _ in range(min(n, 50)):
            try: await message.guild.create_voice_channel(args[1])
            except Exception: pass
        await message.channel.send(ui_ok(f"created {min(n,50)}"))
    elif cmd == "masscat":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 3:
            return await message.channel.send(ui_err("usage: masscat <name> <n>"), delete_after=5)
        n = int(args[2]) if args[2].isdigit() else 5
        for _ in range(min(n, 20)):
            try: await message.guild.create_category(args[1])
            except Exception: pass
        await message.channel.send(ui_ok(f"created {min(n,20)}"))
    elif cmd == "massrolecreate":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 3:
            return await message.channel.send(ui_err("usage: massrolecreate <name> <n>"), delete_after=5)
        n = int(args[2]) if args[2].isdigit() else 5
        for _ in range(min(n, 50)):
            try: await message.guild.create_role(name=args[1])
            except Exception: pass
        await message.channel.send(ui_ok(f"created {min(n,50)}"))
    elif cmd == "massreact":
        try: await message.delete()
        except Exception: pass
        if len(args) < 2: return await message.channel.send(ui_err("usage: massreact <emoji>"), delete_after=5)
        emoji = args[1]; done = 0
        async for msg in message.channel.history(limit=10):
            try: await msg.add_reaction(emoji); done += 1
            except Exception: pass
            await asyncio.sleep(0.4)
        await message.channel.send(ui_ok(f"reacted to {done}"))
    elif cmd == "massdelete":
        try: await message.delete()
        except Exception: pass
        n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
        d = 0
        async for msg in message.channel.history(limit=n*2):
            if msg.author.id == client.user.id:
                try: await msg.delete()
                except Exception: pass
                d += 1; await asyncio.sleep(0.3)
                if d >= n: break
        await message.channel.send(ui_ok(f"deleted {d}"), delete_after=5)

    # NUKE
    elif cmd == "nuke":
        try: await message.delete()
        except Exception: pass
        if not message.guild: return await message.channel.send(ui_err("server only"), delete_after=5)
        sub = args[1].lower() if len(args) > 1 else ""
        g = message.guild
        if sub == "status":
            await message.channel.send(ui_info(f"nuke ready — ch:{len(g.channels)} r:{len(g.roles)} e:{len(g.emojis)}"))
        elif sub == "channels":
            for ch in list(g.channels):
                try: await ch.delete()
                except Exception: pass
        elif sub == "roles":
            for r in list(g.roles):
                if r.is_default(): continue
                try: await r.delete()
                except Exception: pass
        elif sub == "emojis":
            for e in list(g.emojis):
                try: await e.delete()
                except Exception: pass
        elif sub == "webhooks":
            for ch in list(g.text_channels):
                try:
                    for wh in await ch.webhooks():
                        try: await wh.delete()
                        except Exception: pass
                except Exception: pass
        elif sub == "everything":
            for ch in list(g.channels):
                try: await ch.delete()
                except Exception: pass
            for r in list(g.roles):
                if r.is_default(): continue
                try: await r.delete()
                except Exception: pass
            for e in list(g.emojis):
                try: await e.delete()
                except Exception: pass
        elif sub == "restore":
            if len(args) < 3: return await message.channel.send(ui_err("usage: nuke restore <file>"), delete_after=5)
            p = f"backups/{args[2]}" if not os.path.exists(args[2]) else args[2]
            if not os.path.exists(p): return await message.channel.send(ui_err("not found"), delete_after=5)
            await _restore_server(g, p)
            await message.channel.send(ui_ok("restored"))
        else:
            await message.channel.send(build_help_section("nuke"))
    elif cmd == "nukebackup":
        try: await message.delete()
        except Exception: pass
        if not message.guild: return await message.channel.send(ui_err("server only"), delete_after=5)
        p = await _backup_server(message.guild)
        await message.channel.send(ui_ok(f"backup saved: {p}"))

    # SCRAPE
    elif cmd == "scrape":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "members" and message.guild:
            rows = [f"  {GREY}•{RESET} {m}  {DIM}({m.id}){RESET}" for m in list(message.guild.members)[:100]]
            await message.channel.send(_paginate("members", message.guild.name, rows))
        elif sub == "invites" and message.guild:
            try:
                invs = await message.guild.invites()
                rows = [f"  {GREY}•{RESET} {i.code}  {DIM}uses={i.uses} by {i.inviter}{RESET}" for i in invs]
                await message.channel.send(_paginate("invites", message.guild.name, rows) if rows else ui_info("none"))
            except Exception as e: await message.channel.send(ui_err(str(e)), delete_after=5)
        elif sub == "channel" and len(args) >= 3:
            ch = client.get_channel(int(args[2]))
            if not ch: return await message.channel.send(ui_err("not found"), delete_after=5)
            n = int(args[3]) if len(args) > 3 and args[3].isdigit() else 500
            out = []
            async for m in ch.history(limit=n):
                out.append({"author": str(m.author), "id": m.id, "content": m.content,
                            "ts": m.created_at.isoformat()})
            p = f"exports/channel_{ch.id}_{int(time.time())}.json"
            with open(p, "w") as f: json.dump(out, f, indent=2)
            await message.channel.send(ui_ok(f"exported {len(out)} → {p}"))
        elif sub == "server" and message.guild:
            p = await _dump_server(message.guild)
            await message.channel.send(ui_ok(f"server dump: {p}"))
        else:
            await message.channel.send(build_help_section("scrape"))
    elif cmd == "export":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "members" and len(args) >= 3:
            g = client.get_guild(int(args[2]))
            if not g: return await message.channel.send(ui_err("guild not found"), delete_after=5)
            p = f"exports/members_{g.id}_{int(time.time())}.csv"
            with open(p, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f); w.writerow(["id", "name", "nick", "joined", "bot"])
                for m in g.members:
                    w.writerow([m.id, str(m), m.nick or "", m.joined_at.isoformat() if m.joined_at else "", m.bot])
            await message.channel.send(ui_ok(f"exported {len(g.members)} → {p}"))
        elif sub == "messages" and len(args) >= 3:
            ch = client.get_channel(int(args[2]))
            if not ch: return await message.channel.send(ui_err("not found"), delete_after=5)
            n = int(args[3]) if len(args) > 3 and args[3].isdigit() else 1000
            out = []
            async for m in ch.history(limit=n):
                out.append({"author": str(m.author), "content": m.content, "ts": m.created_at.isoformat()})
            p = f"exports/messages_{ch.id}_{int(time.time())}.json"
            with open(p, "w") as f: json.dump(out, f, indent=2)
            await message.channel.send(ui_ok(f"exported {len(out)} → {p}"))
        elif sub == "invites" and len(args) >= 3:
            g = client.get_guild(int(args[2]))
            if not g: return await message.channel.send(ui_err("guild not found"), delete_after=5)
            invs = await g.invites()
            p = f"exports/invites_{g.id}_{int(time.time())}.json"
            with open(p, "w") as f: json.dump([{"code": i.code, "uses": i.uses, "inviter": str(i.inviter)} for i in invs], f, indent=2)
            await message.channel.send(ui_ok(f"exported {len(invs)} → {p}"))
        else: await message.channel.send(build_help_section("scrape"))
    elif cmd == "import":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "members" and len(args) >= 3 and os.path.exists(args[2]):
            with open(args[2]) as f: data = json.load(f) if args[2].endswith(".json") else [l.strip() for l in f]
            await message.channel.send(ui_ok(f"loaded {len(data)} ids from {args[2]}"))
        else: await message.channel.send(build_help_section("scrape"))

    # WEBHOOKS
    elif cmd == "webhook":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "create":
            name = args[2] if len(args) > 2 else "voltrix"
            try:
                wh = await message.channel.create_webhook(name=name)
                await message.channel.send(ui_ok(f"webhook: {wh.url}"))
            except Exception as e: await message.channel.send(ui_err(str(e)), delete_after=5)
        elif sub == "delete" and len(args) >= 3:
            try:
                wh = await client.fetch_webhook(int(args[2])); await wh.delete()
                await message.channel.send(ui_ok("deleted"))
            except Exception as e: await message.channel.send(ui_err(str(e)), delete_after=5)
        elif sub == "list":
            try:
                whs = await message.channel.webhooks()
                rows = [f"  {GREY}•{RESET} {w.name}  {DIM}({w.id}){RESET}" for w in whs]
                await message.channel.send(_paginate("webhooks", "", rows) if rows else ui_info("none"))
            except Exception as e: await message.channel.send(ui_err(str(e)), delete_after=5)
        elif sub == "spam" and len(args) >= 5:
            url = args[2]; n = int(args[3]) if args[3].isdigit() else 10
            txt = " ".join(args[4:]); done = 0
            async with aiohttp.ClientSession() as s:
                for _ in range(n):
                    try:
                        async with s.post(url, json={"content": txt}) as r:
                            if r.status in (200,204): done += 1
                    except Exception: pass
                    await asyncio.sleep(0.5)
            await message.channel.send(ui_ok(f"spammed {done}/{n}"))
        elif sub == "rename" and len(args) >= 4:
            try:
                wh = await client.fetch_webhook(int(args[2])); await wh.edit(name=" ".join(args[3:]))
                await message.channel.send(ui_ok("renamed"))
            except Exception as e: await message.channel.send(ui_err(str(e)), delete_after=5)
        elif sub == "emoji":
            if not message.guild: return await message.channel.send(ui_err("server only"), delete_after=5)
            rows = [f"  {GREY}•{RESET} {e}  {DIM}{e.name} ({e.id}){RESET}" for e in list(message.guild.emojis)[:80]]
            await message.channel.send(_paginate("emojis", message.guild.name, rows) if rows else ui_info("none"))
        elif sub == "steal" and len(args) >= 3:
            e = args[2]
            m = re.match(r"<a?:([a-zA-Z0-9_]+):(\d+)>", e)
            if not m: return await message.channel.send(ui_err("provide a custom emoji"), delete_after=5)
            name, eid = m.group(1), m.group(2)
            url = f"https://cdn.discordapp.com/emojis/{eid}." + ("gif" if e.startswith("<a:") else "png")
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(url) as r: img = await r.read()
                new = await message.guild.create_custom_emoji(name=name, image=img)
                await message.channel.send(ui_ok(f"stolen: {new}"))
            except Exception as e: await message.channel.send(ui_err(str(e)), delete_after=5)
        elif sub == "clear":
            n = 0
            for ch in message.guild.text_channels:
                try:
                    for wh in await ch.webhooks():
                        try: await wh.delete(); n += 1
                        except Exception: pass
                except Exception: pass
            await message.channel.send(ui_ok(f"deleted {n} webhooks"))
        else: await message.channel.send(build_help_section("webhooks"))

    # AUTOMOD / RAID / QUARANTINE / TICKETS / VERIFY
    elif cmd == "automod":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "on": _automod["enabled"] = True; await message.channel.send(ui_ok("automod on"))
        elif sub == "off": _automod["enabled"] = False; await message.channel.send(ui_ok("automod off"))
        elif sub == "add" and len(args) >= 3:
            _automod["words"].append(args[2]); await message.channel.send(ui_ok(f"added `{args[2]}`"))
        elif sub == "remove" and len(args) >= 3:
            _automod["words"] = [w for w in _automod["words"] if w.lower() != args[2].lower()]
            await message.channel.send(ui_ok(f"removed `{args[2]}`"))
        elif sub == "list":
            rows = [f"  {GREY}•{RESET} {w}" for w in _automod["words"]]
            await message.channel.send(_paginate("automod words", "", rows) if rows else ui_info("none"))
        elif sub == "action" and len(args) >= 3:
            if args[2] not in ("delete","kick","ban","timeout"):
                return await message.channel.send(ui_err("action must be delete/kick/ban/timeout"), delete_after=5)
            _automod["action"] = args[2]; await message.channel.send(ui_ok(f"action → {args[2]}"))
        elif sub == "logs" and len(args) >= 3:
            _automod["log_ch"] = args[2]; await message.channel.send(ui_ok("logs set"))
        else: await message.channel.send(build_help_section("automod"))
    elif cmd == "raidmode":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "on": _raidmode["enabled"] = True; await message.channel.send(ui_ok("raidmode on"))
        elif sub == "off": _raidmode["enabled"] = False; await message.channel.send(ui_ok("raidmode off"))
        elif sub == "threshold" and len(args) >= 3 and args[2].isdigit():
            _raidmode["threshold"] = int(args[2]); await message.channel.send(ui_ok(f"threshold → {args[2]}/sec"))
        else: await message.channel.send(build_help_section("automod"))
    elif cmd == "quarantine":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "on": _quarantine["enabled"] = True; await message.channel.send(ui_ok("quarantine on"))
        elif sub == "off": _quarantine["enabled"] = False; await message.channel.send(ui_ok("quarantine off"))
        elif sub == "role" and len(args) >= 3:
            _quarantine["role_id"] = args[2]; await message.channel.send(ui_ok("role set"))
        elif sub == "age" and len(args) >= 3 and args[2].isdigit():
            _quarantine["age_days"] = int(args[2]); await message.channel.send(ui_ok(f"age → {args[2]}d"))
        else: await message.channel.send(build_help_section("automod"))
    elif cmd == "ticket":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "setup" and len(args) >= 3:
            _ticket_cfg["category_id"] = args[2]; await message.channel.send(ui_ok("ticket category set"))
        elif sub == "close":
            if isinstance(message.channel, discord.TextChannel):
                try: await message.channel.delete()
                except Exception as e: await message.channel.send(ui_err(str(e)))
        else: await message.channel.send(build_help_section("automod"))
    elif cmd == "verify":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "setup" and len(args) >= 3:
            _verify_cfg["role_id"] = args[2]; await message.channel.send(ui_ok("verified role set"))
        elif sub == "button":
            label = " ".join(args[2:]) if len(args) > 2 else "Verify"
            try:
                view = discord.ui.View()
                view.add_item(discord.ui.Button(label=label, custom_id="verify_button", style=discord.ButtonStyle.success))
                await message.channel.send("click to verify", view=view)
            except Exception as e:
                await message.channel.send(ui_err(f"button send failed: {e}"), delete_after=6)
        else: await message.channel.send(build_help_section("automod"))

    # MONITOR
    elif cmd == "monitor":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "joins": _monitor["joins"] = args[2].lower() in ("on","enable") if len(args) > 2 else not _monitor["joins"]; await message.channel.send(ui_ok(f"joins → {_monitor['joins']}"))
        elif sub == "leaves": _monitor["leaves"] = args[2].lower() in ("on","enable") if len(args) > 2 else not _monitor["leaves"]; await message.channel.send(ui_ok(f"leaves → {_monitor['leaves']}"))
        elif sub == "roles": _monitor["roles"] = args[2].lower() in ("on","enable") if len(args) > 2 else not _monitor["roles"]; await message.channel.send(ui_ok(f"roles → {_monitor['roles']}"))
        elif sub == "nicks": _monitor["nicks"] = args[2].lower() in ("on","enable") if len(args) > 2 else not _monitor["nicks"]; await message.channel.send(ui_ok(f"nicks → {_monitor['nicks']}"))
        elif sub == "invites": _monitor["invites"] = args[2].lower() in ("on","enable") if len(args) > 2 else not _monitor["invites"]; await message.channel.send(ui_ok(f"invites → {_monitor['invites']}"))
        elif sub == "keywords" and len(args) >= 3:
            _monitor["keywords"] = args[2:]; await message.channel.send(ui_ok(f"keywords: {len(args)-2}"))
        elif sub == "keywordstop": _monitor["keywords"] = []; await message.channel.send(ui_ok("keywords cleared"))
        elif sub == "logch" and len(args) >= 3: _monitor["log_ch"] = args[2]; await message.channel.send(ui_ok("logch set"))
        elif sub == "status":
            await message.channel.send(ui_box("monitor", [
                f"  {DIM}joins{RESET}    {_monitor['joins']}",
                f"  {DIM}leaves{RESET}   {_monitor['leaves']}",
                f"  {DIM}roles{RESET}    {_monitor['roles']}",
                f"  {DIM}nicks{RESET}    {_monitor['nicks']}",
                f"  {DIM}invites{RESET}  {_monitor['invites']}",
                f"  {DIM}logch{RESET}    {_monitor['log_ch'] or '-'}",
                f"  {DIM}keywords{RESET} {len(_monitor['keywords'])}",
            ]))
        else: await message.channel.send(build_help_section("monitor"))

    # BACKUP
    elif cmd == "backup":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "server" and message.guild:
            p = await _backup_server(message.guild)
            await message.channel.send(ui_ok(f"backed up: {p}"))
        elif sub == "restore" and len(args) >= 3 and message.guild:
            p = f"backups/{args[2]}" if not os.path.exists(args[2]) else args[2]
            if not os.path.exists(p): return await message.channel.send(ui_err("backup not found"), delete_after=5)
            await _restore_server(message.guild, p); await message.channel.send(ui_ok("restored"))
        elif sub == "list":
            files = sorted(os.listdir("backups")) if os.path.isdir("backups") else []
            rows = [f"  {GREY}•{RESET} {f}" for f in files]
            await message.channel.send(_paginate("backups", "", rows) if rows else ui_info("no backups"))
        elif sub == "sync" and len(args) >= 4:
            src = client.get_guild(int(args[2])); dst = client.get_guild(int(args[3]))
            if not src or not dst: return await message.channel.send(ui_err("guild not found"), delete_after=5)
            await _sync_servers(src, dst); await message.channel.send(ui_ok("synced"))
        elif sub == "autosave":
            cfg = load_config()
            cfg["autosave"] = (args[2].lower() in ("on","enable")) if len(args) > 2 else not cfg.get("autosave", False)
            save_config(cfg); await message.channel.send(ui_ok(f"autosave → {cfg['autosave']}"))
        elif sub == "config":
            p = f"backups/config_{int(time.time())}.json"
            with open(p, "w") as f: json.dump(load_config(), f, indent=2)
            await message.channel.send(ui_ok(f"config backed up → {p}"))
        else: await message.channel.send(build_help_section("backup"))

    # PERMS
    elif cmd == "perm":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add" and len(args) >= 4:
            _perm_allow.setdefault(args[2], set()).add(int(args[3]))
            await message.channel.send(ui_ok(f"allowed {args[3]} on `{args[2]}`"))
        elif sub == "remove" and len(args) >= 4 and args[2] in _perm_allow:
            _perm_allow[args[2]].discard(int(args[3]))
            await message.channel.send(ui_ok("removed"))
        elif sub == "block" and len(args) >= 3:
            _perm_block.add(args[2]); await message.channel.send(ui_ok(f"blocked `{args[2]}`"))
        elif sub == "unblock" and len(args) >= 3:
            _perm_block.discard(args[2]); await message.channel.send(ui_ok("unblocked"))
        elif sub == "list":
            rows = [f"  {GREY}•{RESET} {k} → {v}" for k, v in _perm_allow.items()]
            rows += [f"  {GREY}•{RESET} BLOCKED: {c}" for c in _perm_block]
            await message.channel.send(_paginate("perms", "", rows) if rows else ui_info("no overrides"))
        elif sub == "channel" and len(args) >= 4:
            _perm_channel[args[3]] = int(args[2]); await message.channel.send(ui_ok(f"`{args[3]}` → channel {args[2]}"))
        elif sub == "server" and len(args) >= 4:
            _perm_server[args[3]] = int(args[2]); await message.channel.send(ui_ok(f"`{args[3]}` → server {args[2]}"))
        elif sub == "reset":
            _perm_allow.clear(); _perm_block.clear(); _perm_channel.clear(); _perm_server.clear()
            await message.channel.send(ui_ok("perms wiped"))
        else: await message.channel.send(build_help_section("perms"))

    # SCHEDULER
    elif cmd == "schedule":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add" and len(args) >= 4 and args[2].isdigit():
            when = int(args[2]); msg = " ".join(args[3:])
            jid = str(uuid4())[:8]
            _scheduler.append({"id": jid, "when": when, "action": "message",
                               "payload": {"ch_id": message.channel.id, "msg": msg}})
            sched_save(); await message.channel.send(ui_ok(f"scheduled id {jid}"))
        elif sub == "addrel" and len(args) >= 4 and args[2].isdigit():
            when = int(time.time()) + int(args[2]); msg = " ".join(args[3:])
            jid = str(uuid4())[:8]
            _scheduler.append({"id": jid, "when": when, "action": "message",
                               "payload": {"ch_id": message.channel.id, "msg": msg}})
            sched_save(); await message.channel.send(ui_ok(f"scheduled id {jid}"))
        elif sub == "addchan" and len(args) >= 5 and args[2].isdigit() and args[3].isdigit():
            jid = str(uuid4())[:8]
            _scheduler.append({"id": jid, "when": int(args[3]), "action": "message",
                               "payload": {"ch_id": args[2], "msg": " ".join(args[4:])}})
            sched_save(); await message.channel.send(ui_ok(f"scheduled id {jid}"))
        elif sub == "list":
            rows = [f"  {GREY}•{RESET} {j['id']} @ {j['when']}" for j in _scheduler]
            await message.channel.send(_paginate("scheduled", "", rows) if rows else ui_info("none"))
        elif sub == "remove" and len(args) >= 3:
            _scheduler = [j for j in _scheduler if j["id"] != args[2]]
            sched_save(); await message.channel.send(ui_ok("removed"))
        elif sub == "clear":
            _scheduler.clear(); sched_save(); await message.channel.send(ui_ok("cleared"))
        elif sub == "status" and len(args) >= 4 and args[2].isdigit():
            jid = str(uuid4())[:8]
            _scheduler.append({"id": jid, "when": int(args[2]), "action": "status",
                               "payload": {"text": " ".join(args[3:])}})
            sched_save(); await message.channel.send(ui_ok(f"scheduled status id {jid}"))
        else: await message.channel.send(build_help_section("scheduler"))

    # DB
    elif cmd == "note":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add" and len(args) >= 4:
            db_note_add(args[2], " ".join(args[3:])); await message.channel.send(ui_ok("saved"))
        elif sub == "list" and len(args) >= 3:
            rows = [f"  {GREY}•{RESET} {datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')}  {n}" for n, ts in db_note_list(args[2])]
            await message.channel.send(_paginate("notes", args[2], rows) if rows else ui_info("none"))
        elif sub == "clear" and len(args) >= 3:
            db_note_clear(args[2]); await message.channel.send(ui_ok("cleared"))
        else: await message.channel.send(build_help_section("db"))
    elif cmd == "history":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add" and len(args) >= 4:
            db_hist_add(args[2], " ".join(args[3:])); await message.channel.send(ui_ok("saved"))
        elif sub == "list" and len(args) >= 3:
            rows = [f"  {GREY}•{RESET} {datetime.fromtimestamp(ts).strftime('%H:%M')}  {e}" for e, ts in db_hist_list(args[2])]
            await message.channel.send(_paginate("history", args[2], rows) if rows else ui_info("none"))
        else: await message.channel.send(build_help_section("db"))
    elif cmd == "stats":
        try: await message.delete()
        except Exception: pass
        if len(args) > 1 and args[1].lower() == "clear":
            db_stats_clear(); return await message.channel.send(ui_ok("cleared"))
        rows = [f"  {GREY}•{RESET} {c}  {DIM}{n}{RESET}" for c, n in db_stats_all()[:100]]
        await message.channel.send(_paginate("stats", "top commands", rows) if rows else ui_info("no stats yet"))
    elif cmd == "db":
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "info":
            size = os.path.getsize(_db_path) if os.path.exists(_db_path) else 0
            await message.channel.send(ui_box("db", [
                f"  {DIM}path{RESET}  {_db_path}", f"  {DIM}size{RESET}  {size} bytes"]))
        elif sub == "vacuum":
            _db.execute("VACUUM"); _db.commit(); await message.channel.send(ui_ok("vacuumed"))
        else: await message.channel.send(build_help_section("db"))

    # INTERACTIONS
    elif cmd == "buttons":
        _buttons_enabled = (args[1].lower() in ("on","enable")) if len(args) > 1 else not _buttons_enabled
        await message.edit(content=ui_ok(f"buttons → {'on' if _buttons_enabled else 'off'}"))
    elif cmd == "modals":
        _modals_enabled = (args[1].lower() in ("on","enable")) if len(args) > 1 else not _modals_enabled
        await message.edit(content=ui_ok(f"modals → {'on' if _modals_enabled else 'off'}"))
    elif cmd == "interact":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "list":
            rows = [f"  {GREY}•{RESET} {i}" for i in _pending_interactions[-20:]]
            await message.edit(content=_paginate("pending interactions", "", rows) if rows else ui_info("none"))
        elif sub == "clear":
            _pending_interactions.clear(); await message.edit(content=ui_ok("cleared"))
        else: await message.edit(content=build_help_section("interactions"))

    # DEVELOPER
    elif cmd == "eval":
        if len(args) < 2: return await message.edit(content=ui_err("usage: eval <code>"))
        code = " ".join(args[1:])
        try:
            result = eval(code, globals(), {"client": client, "message": message, "discord": discord, "asyncio": asyncio})
            if asyncio.iscoroutine(result): result = await result
            await message.edit(content=f"```py\n{str(result)[:1900]}\n```")
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "restart":
        await message.edit(content=ui_warn("restarting...")); os.execv(sys.executable, [sys.executable] + sys.argv)
    elif cmd == "reconnect":
        try: await client.ws.close(code=4000); await message.edit(content=ui_ok("reconnect requested"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "proxy":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "set" and len(args) >= 3:
            _proxy = args[2]; await message.edit(content=ui_ok(f"proxy → {args[2]}"))
        elif sub == "clear":
            _proxy = None; await message.edit(content=ui_ok("proxy cleared"))
        else: await message.edit(content=ui_err("usage: proxy set <url> | clear"))
    elif cmd == "plugin":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "load" and len(args) >= 3:
            ok = _load_plugin(args[2])
            await message.edit(content=ui_ok(f"loaded {args[2]}") if ok else ui_err("failed"))
        elif sub == "unload" and len(args) >= 3:
            ok = _unload_plugin(args[2]); await message.edit(content=ui_ok("unloaded") if ok else ui_err("not loaded"))
        elif sub == "list":
            rows = [f"  {GREY}•{RESET} {n}" for n in _plugins]
            await message.edit(content=_paginate("plugins", "", rows) if rows else ui_info("none"))
        else: await message.edit(content=ui_err("usage: plugin load/unload/list"))
    elif cmd == "session":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "list":
            rows = [f"  {GREY}[{i}]{RESET} {s[:12]}..." for i, s in enumerate(_sessions)]
            await message.edit(content=_paginate("sessions", "", rows) if rows else ui_info("no saved sessions"))
        elif sub == "switch" and len(args) >= 3 and args[2].isdigit():
            idx = int(args[2])
            if 0 <= idx < len(_sessions):
                _session_idx = idx; await message.edit(content=ui_ok(f"active → {_sessions[idx][:12]}..."))
            else: await message.edit(content=ui_err("bad index"))
        else: await message.edit(content=ui_err("usage: session list | switch <idx>"))

    # SERVER
    elif cmd == "serverinfo":
        g = message.guild
        if not g: return await message.edit(content=ui_err("not in a server"))
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
        if not g: return await message.edit(content=ui_err("not in a server"))
        n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 20
        rows = [f"  {GREY}•{RESET} {m.display_name}  {DIM}({m.id}){RESET}" for m in list(g.members)[:n]]
        await message.edit(content=_paginate("members", g.name, rows))
    elif cmd == "channels":
        g = message.guild
        if not g: return await message.edit(content=ui_err("not in a server"))
        rows = [f"  {GREY}•{RESET} #{ch.name}  {DIM}({ch.id}){RESET}" for ch in g.channels]
        await message.edit(content=_paginate("channels", g.name, rows))
    elif cmd == "roles":
        g = message.guild
        if not g: return await message.edit(content=ui_err("not in a server"))
        rows = [f"  {GREY}•{RESET} {r.name}  {DIM}({r.id}){RESET}" for r in g.roles]
        await message.edit(content=_paginate("roles", g.name, rows))
    elif cmd in ("ban","kick","mute","unmute"):
        g = message.guild
        if not g or len(args) < 2: return await message.edit(content=ui_err(f"usage: {cmd} <user_id>"))
        try:
            member = g.get_member(int(args[1]))
            if cmd == "ban": await g.ban(member, reason=" ".join(args[2:]) or "no reason")
            elif cmd == "kick": await g.kick(member, reason=" ".join(args[2:]) or "no reason")
            elif cmd == "mute":
                until = discord.utils.utcnow() + timedelta(minutes=10); await member.timeout(until)
            elif cmd == "unmute": await member.timeout(None)
            await message.edit(content=ui_ok(f"{cmd} → {member}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "setnick":
        g = message.guild
        if not g or len(args) < 3: return await message.edit(content=ui_err("usage: setnick <user_id> <nick>"))
        try:
            m = g.get_member(int(args[1])); await m.edit(nick=" ".join(args[2:]))
            await message.edit(content=ui_ok("nick set"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "topic":
        if not message.guild or len(args) < 2: return await message.edit(content=ui_err("usage: topic <text>"))
        try: await message.channel.edit(topic=" ".join(args[1:])); await message.edit(content=ui_ok("topic set"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "slowmode":
        if not message.guild or len(args) < 2: return await message.edit(content=ui_err("usage: slowmode <seconds>"))
        try: await message.channel.edit(slowmode_delay=int(args[1])); await message.edit(content=ui_ok("slowmode set"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "servericon":
        if not message.guild or len(args) < 2: return await message.edit(content=ui_err("usage: servericon <url>"))
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(args[1]) as r: img = await r.read()
            await message.guild.edit(icon=img); await message.edit(content=ui_ok("icon set"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "serverbanner":
        if not message.guild or len(args) < 2: return await message.edit(content=ui_err("usage: serverbanner <url>"))
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(args[1]) as r: img = await r.read()
            await message.guild.edit(banner=img); await message.edit(content=ui_ok("banner set"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "servername":
        if not message.guild or len(args) < 2: return await message.edit(content=ui_err("usage: servername <name>"))
        try: await message.guild.edit(name=" ".join(args[1:])); await message.edit(content=ui_ok("name set"))
        except Exception as e: await message.edit(content=ui_err(str(e)))

    # INFORMATION
    elif cmd == "userinfo":
        uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
        try:
            async with aiohttp.ClientSession() as s:
                h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
                async with s.get(f"https://discord.com/api/v9/users/{uid}", headers=h) as r:
                    if r.status != 200: return await message.edit(content=ui_err("not found"))
                    u = await r.json()
            pfp = f"https://cdn.discordapp.com/avatars/{uid}/{u.get('avatar')}.webp?size=256" if u.get("avatar") else "none"
            await message.edit(content=ui_box("user info", [
                f"  {DIM}username{RESET}   {u.get('username','?')}",
                f"  {DIM}id{RESET}         {uid}",
                f"  {DIM}avatar{RESET}     {pfp}",
                f"  {DIM}bot{RESET}        {'yes' if u.get('bot') else 'no'}",
                f"  {DIM}flags{RESET}      {u.get('public_flags',0)}",
            ]))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "avatar":
        uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
        try:
            async with aiohttp.ClientSession() as s:
                h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
                async with s.get(f"https://discord.com/api/v9/users/{uid}", headers=h) as r:
                    u = await r.json()
            if u.get("avatar"):
                await message.edit(content=f"https://cdn.discordapp.com/avatars/{uid}/{u['avatar']}.webp?size=2048")
            else: await message.edit(content=ui_info("no avatar"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "checkname":
        if len(args) < 2: return await message.edit(content=ui_err("usage: checkname <username>"))
        username = args[1].lower().strip()
        try:
            async with aiohttp.ClientSession() as s:
                h = {"Authorization": TOKEN, "Content-Type":"application/json", "User-Agent": USER_AGENT}
                async with s.post("https://discord.com/api/v9/users/@me/pomelo-attempt",
                                  headers=h, json={"username": username}) as r:
                    if r.status == 200:
                        taken = (await r.json()).get("taken", True)
                        await message.edit(content=ui_ok(f"`{username}` available") if not taken else ui_err(f"`{username}` taken"))
                    else: await message.edit(content=ui_err(f"check failed {r.status}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "whois":
        uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
        try:
            async with aiohttp.ClientSession() as s:
                h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
                async with s.get(f"https://discord.com/api/v9/users/{uid}/profile", headers=h) as r:
                    if r.status != 200: return await message.edit(content=ui_err("not found"))
                    p = await r.json()
            u = p.get("user", {}); badges = [b.get("id","") for b in p.get("badges", [])]
            await message.edit(content=ui_box("whois", [
                f"  {DIM}username{RESET}       {u.get('username','?')}",
                f"  {DIM}id{RESET}             {uid}",
                f"  {DIM}bio{RESET}            {u.get('bio','') or '-'}",
                f"  {DIM}badges{RESET}         {', '.join(badges) or 'none'}",
                f"  {DIM}nitro{RESET}          {'yes' if p.get('premium_since') else 'no'}",
                f"  {DIM}mutual servers{RESET} {len(p.get('mutual_guilds', []))}",
                f"  {DIM}mutual friends{RESET} {len(p.get('mutual_friends', []))}",
            ]))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "channelinfo":
        cid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.channel.id
        ch = client.get_channel(cid)
        if not ch: return await message.edit(content=ui_err("not found"))
        rows = [f"  {DIM}name{RESET}    #{getattr(ch, 'name', cid)}",
                f"  {DIM}id{RESET}      {ch.id}",
                f"  {DIM}type{RESET}    {str(ch.type)}"]
        try: rows.append(f"  {DIM}created{RESET} {ch.created_at.strftime('%Y-%m-%d')}")
        except Exception: pass
        rows.append(f"  {DIM}guild{RESET}   {ch.guild.name if getattr(ch,'guild',None) else 'DM'}")
        await message.edit(content=ui_box("channel info", rows))
    elif cmd == "roleinfo":
        if not message.guild or len(args) < 2: return await message.edit(content=ui_err("usage: roleinfo <role_id>"))
        role = message.guild.get_role(int(args[1]))
        if not role: return await message.edit(content=ui_err("not found"))
        await message.edit(content=ui_box("role info", [
            f"  {DIM}name{RESET}        {role.name}",
            f"  {DIM}id{RESET}          {role.id}",
            f"  {DIM}color{RESET}       #{role.color.value:06x}",
            f"  {DIM}members{RESET}     {len(role.members)}",
            f"  {DIM}position{RESET}    {role.position}",
            f"  {DIM}mentionable{RESET} {'yes' if role.mentionable else 'no'}",
            f"  {DIM}hoisted{RESET}     {'yes' if role.hoist else 'no'}",
        ]))

    # GROUPCHAT
    elif cmd == "gclist":
        try: await message.delete()
        except Exception: pass
        dms = [c for c in client.private_channels if isinstance(c, discord.GroupChannel)]
        if not dms: return await message.channel.send(ui_info("no group DMs"), delete_after=5)
        rows = [f"  {GREY}[{i}]{RESET} {WHITE}{gc.name or 'Unnamed'}{RESET}  {DIM}({gc.id}){RESET}" for i, gc in enumerate(dms)]
        await message.channel.send(_paginate("groupchats", "your group DMs", rows))
    elif cmd == "gcrename":
        if len(args) < 2 or not isinstance(message.channel, discord.GroupChannel):
            return await message.edit(content=ui_err("run in a group DM: gcrename <name>"))
        try: await message.channel.edit(name=" ".join(args[1:])); await message.edit(content=ui_ok("gc renamed"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "gcleave":
        if not isinstance(message.channel, discord.GroupChannel): return await message.edit(content=ui_err("run in a group DM"))
        try: await message.channel.leave()
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "gccreate":
        if len(args) < 2: return await message.edit(content=ui_err("usage: gccreate <user_id> [user_id2...]"))
        try:
            users = []
            for uid_str in args[1:]:
                u = await client.fetch_user(int(uid_str))
                if u: users.append(u)
            if not users: return await message.edit(content=ui_err("no valid users"))
            gc = await client.user.create_group(*users); await message.edit(content=ui_ok(f"created {gc.id}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "gcadd":
        if not isinstance(message.channel, discord.GroupChannel) or len(args) < 2:
            return await message.edit(content=ui_err("run in group DM: gcadd <user_id>"))
        try:
            u = await client.fetch_user(int(args[1])); await message.channel.add_recipients(u)
            await message.edit(content=ui_ok(f"added {u}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "gcremove":
        if not isinstance(message.channel, discord.GroupChannel) or len(args) < 2:
            return await message.edit(content=ui_err("run in group DM: gcremove <user_id>"))
        try:
            u = await client.fetch_user(int(args[1])); await message.channel.remove_recipients(u)
            await message.edit(content=ui_ok(f"removed {u}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "gcicon":
        if not isinstance(message.channel, discord.GroupChannel) or len(args) < 2:
            return await message.edit(content=ui_err("run in group DM: gcicon <url>"))
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(args[1]) as r: img = await r.read()
            ext = args[1].split(".")[-1].split("?")[0].lower()
            mime = {"gif":"gif","png":"png","jpg":"jpeg","jpeg":"jpeg","webp":"webp"}.get(ext,"png")
            b64 = base64.b64encode(img).decode()
            h = {"Authorization": TOKEN, "Content-Type":"application/json", "User-Agent": USER_AGENT}
            async with aiohttp.ClientSession() as s:
                async with s.patch(f"https://discord.com/api/v9/channels/{message.channel.id}",
                                   headers=h, json={"icon": f"data:image/{mime};base64,{b64}"}) as resp:
                    await message.edit(content=ui_ok("gc icon set") if resp.status == 200 else ui_err(f"failed {resp.status}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "agc":
        sub = args[1].lower() if len(args) > 1 else ""
        if not sub:
            state = "ON" if _agc_state["enabled"] else "OFF"
            return await message.edit(content=ui_info(f"agc is {state}"))
        elif sub in ("on","enable"): _agc_state["enabled"] = True; await message.edit(content=ui_ok("agc on"))
        elif sub in ("off","disable"): _agc_state["enabled"] = False; await message.edit(content=ui_ok("agc off"))
        elif sub == "block":
            opt = args[2].lower() if len(args) > 2 else ""
            _agc_state["block"] = opt in ("on","enable"); await message.edit(content=ui_ok(f"block → {opt}"))
        elif sub == "msg": _agc_state["leave_msg"] = " ".join(args[2:]); await message.edit(content=ui_ok("leave msg set"))
        elif sub == "name": _agc_state["gc_name"] = " ".join(args[2:]); await message.edit(content=ui_ok("gc name set"))
        elif sub == "icon": _agc_state["gc_icon_url"] = args[2] if len(args) > 2 else None; await message.edit(content=ui_ok("icon set"))
        elif sub == "webhook": _agc_state["webhook_url"] = args[2] if len(args) > 2 else None; await message.edit(content=ui_ok("webhook set"))
        elif sub == "whitelist" and len(args) >= 3:
            _agc_whitelist.add(args[2].strip("<@!>")); _agc_save_wl(); await message.edit(content=ui_ok("whitelisted"))
        elif sub == "unwhitelist" and len(args) >= 3:
            _agc_whitelist.discard(args[2].strip("<@!>")); _agc_save_wl(); await message.edit(content=ui_ok("removed"))
        elif sub == "wllist":
            rows = [f"  {GREY}•{RESET} {u}" for u in _agc_whitelist]
            await message.edit(content=_paginate("agc whitelist", "", rows) if rows else ui_info("empty"))
        else: await message.edit(content=build_help_section("groupchat"))

    # UTILITY
    elif cmd == "uwuify":
        if len(args) < 2: return await message.edit(content=ui_err("usage: uwuify <text>"))
        await message.edit(content=uwuify(" ".join(args[1:])))
    elif cmd == "owoify":
        if len(args) < 2: return await message.edit(content=ui_err("usage: owoify <text>"))
        await message.edit(content=owoify(" ".join(args[1:])))
    elif cmd == "mock":
        if len(args) < 2: return await message.edit(content=ui_err("usage: mock <text>"))
        await message.edit(content=mock_text(" ".join(args[1:])))
    elif cmd == "reverse":
        if len(args) < 2: return await message.edit(content=ui_err("usage: reverse <text>"))
        await message.edit(content=" ".join(args[1:])[::-1])
    elif cmd == "aesthetic":
        if len(args) < 2: return await message.edit(content=ui_err("usage: aesthetic <text>"))
        await message.edit(content=aesthetic(" ".join(args[1:])))
    elif cmd == "clap":
        if len(args) < 2: return await message.edit(content=ui_err("usage: clap <text>"))
        await message.edit(content=clap_text(" ".join(args[1:])))
    elif cmd == "animatetype":
        if len(args) < 2: return await message.edit(content=ui_err("usage: animatetype <text>"))
        text = " ".join(args[1:]); built = ""
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
        if len(args) < 3: return await message.edit(content=ui_err("usage: translate <lang> <text>"))
        await message.edit(content=f"```\n{await translate_text(' '.join(args[2:]), args[1])}\n```")
    elif cmd == "speaklanguage":
        if len(args) < 2: return await message.edit(content=ui_err("usage: speaklanguage <lang>"))
        _speak_lang = args[1]; await message.edit(content=ui_ok(f"auto → {_speak_lang}"))
    elif cmd == "speaklanguagestop":
        _speak_lang = None; await message.edit(content=ui_ok("stopped"))
    elif cmd == "ghostping":
        if len(args) < 2: return await message.edit(content=ui_err("usage: ghostping <user_id>"))
        try: await message.delete()
        except Exception: pass
        m = await message.channel.send(f"<@{args[1]}>")
        await asyncio.sleep(0.3); await m.delete()
    elif cmd == "pin":
        if len(args) < 2: return await message.edit(content=ui_err("usage: pin <msg_id>"))
        try:
            msg = await message.channel.fetch_message(int(args[1])); await msg.pin()
            await message.edit(content=ui_ok("pinned"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "unpin":
        if len(args) < 2: return await message.edit(content=ui_err("usage: unpin <msg_id>"))
        try:
            msg = await message.channel.fetch_message(int(args[1])); await msg.unpin()
            await message.edit(content=ui_ok("unpinned"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "therapy":
        try: await message.delete()
        except Exception: pass
        await message.channel.send(random.choice([
            "I hear you. That sounds really difficult.",
            "Your feelings are valid. Take things one step at a time.",
            "It's okay to not have everything figured out.",
            "You're doing better than you think.",
            "Remember to be kind to yourself today.",
        ]))
    elif cmd == "ragebait":
        try: await message.delete()
        except Exception: pass
        await message.channel.send(random.choice([
            "pineapple on pizza is literally the best topping change my mind",
            "anime is just cartoons for people who couldn't make friends in high school",
            "dogs are overrated. cats are objectively superior",
            "morning people are just people who go to bed early. you're not special",
        ]))
    elif cmd == "firstmessage":
        try: await message.delete()
        except Exception: pass
        async for msg in message.channel.history(limit=1, oldest_first=True):
            await message.channel.send(ui_box("first message", [
                f"  {DIM}author{RESET}  {msg.author}",
                f"  {DIM}date{RESET}    {msg.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
                f"  {DIM}content{RESET} {msg.content[:200] or '(empty)'}",
                f"  {DIM}url{RESET}     {msg.jump_url}",
            ]))
    elif cmd == "autodelete":
        if len(args) > 1 and args[1].lower() == "off":
            _autodelete_secs = 0; await message.edit(content=ui_ok("autodelete off"))
        elif len(args) > 1 and args[1].isdigit():
            _autodelete_secs = int(args[1]); await message.edit(content=ui_ok(f"autodelete {args[1]}s"))
        else: await message.edit(content=ui_err("usage: autodelete <secs> | off"))

    # TRACKING
    elif cmd == "track":
        if len(args) < 2: return await message.edit(content=ui_err("usage: track <user_id>"))
        _tracked_users.add(int(args[1])); await message.edit(content=ui_ok(f"tracking <@{args[1]}>"))
    elif cmd == "untrack":
        if len(args) < 2: return await message.edit(content=ui_err("usage: untrack <user_id>"))
        uid = int(args[1]); _tracked_users.discard(uid); _tracking.pop(uid, None)
        await message.edit(content=ui_ok("stopped"))
    elif cmd == "tracklist":
        if not _tracked_users: return await message.edit(content=ui_info("not tracking anyone"))
        rows = [f"  {GREY}•{RESET} <@{uid}>  {DIM}({len(_tracking.get(uid,[]))} msgs){RESET}" for uid in _tracked_users]
        await message.edit(content=_paginate("tracking", "", rows))

    # DOWNLOADS
    elif cmd in ("yt","youtube","ytaudio","tiktok","tt","instagram","ig"):
        try: await message.delete()
        except Exception: pass
        if len(args) < 2: return await message.channel.send(ui_err(f"usage: {cmd} <url>"), delete_after=5)
        url = args[1]; audio_only = cmd in ("ytaudio",)
        flags = ["--extract-audio", "--audio-format", "mp3"] if audio_only else ["-f", "best[filesize<25M]"]
        try:
            await message.channel.send(ui_info(f"downloading {url}..."), delete_after=5)
            fname = f"/tmp/dl_{uuid4().hex[:8]}.%(ext)s"
            proc = await asyncio.create_subprocess_exec("yt-dlp", url, "-o", fname, *flags,
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(proc.communicate(), timeout=60)
            import glob
            files = glob.glob("/tmp/dl_*")
            if files:
                latest = max(files, key=os.path.getctime)
                if os.path.getsize(latest) < 25 * 1024 * 1024:
                    await message.channel.send(file=discord.File(latest)); os.remove(latest)
                else:
                    await message.channel.send(ui_err("too large >25MB"), delete_after=8); os.remove(latest)
            else:
                await message.channel.send(ui_err(f"failed: {err.decode()[:200]}"), delete_after=8)
        except asyncio.TimeoutError: await message.channel.send(ui_err("timed out"), delete_after=8)
        except FileNotFoundError: await message.channel.send(ui_err("yt-dlp not installed"), delete_after=8)
        except Exception as e: await message.channel.send(ui_err(str(e)[:200]), delete_after=8)

    # SOCIAL
    elif cmd == "addfriend":
        if len(args) < 2: return await message.edit(content=ui_err("usage: addfriend <user_id>"))
        h = {"Authorization": TOKEN, "Content-Type":"application/json", "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.put(f"https://discord.com/api/v9/users/@me/relationships/{args[1]}",
                                 headers=h, json={"type": 1}) as r:
                    await message.edit(content=ui_ok("sent") if r.status in (200,201,204) else ui_err(f"failed {r.status}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "removefriend":
        if len(args) < 2: return await message.edit(content=ui_err("usage: removefriend <user_id>"))
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.delete(f"https://discord.com/api/v9/users/@me/relationships/{args[1]}", headers=h) as r:
                    await message.edit(content=ui_ok("removed") if r.status in (200,204) else ui_err(f"failed {r.status}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "block":
        if len(args) < 2: return await message.edit(content=ui_err("usage: block <user_id>"))
        h = {"Authorization": TOKEN, "Content-Type":"application/json", "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.put(f"https://discord.com/api/v9/users/@me/relationships/{args[1]}",
                                 headers=h, json={"type": 2}) as r:
                    await message.edit(content=ui_ok("blocked") if r.status in (200,204) else ui_err(f"failed {r.status}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "unblock":
        if len(args) < 2: return await message.edit(content=ui_err("usage: unblock <user_id>"))
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.delete(f"https://discord.com/api/v9/users/@me/relationships/{args[1]}", headers=h) as r:
                    await message.edit(content=ui_ok("unblocked") if r.status in (200,204) else ui_err(f"failed {r.status}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd in ("friends","blocked","pending"):
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://discord.com/api/v9/users/@me/relationships", headers=h) as r:
                    if r.status != 200: return await message.edit(content=ui_err(f"failed {r.status}"))
                    rels = await r.json()
            tf = {"friends": 1, "blocked": 2, "pending": 3}; t = tf[cmd]
            filtered = [x for x in rels if x.get("type") == t]
            rows = [f"  {GREY}•{RESET} {x.get('user',{}).get('username','?')}  {DIM}({x.get('user',{}).get('id','?')}){RESET}" for x in filtered]
            await message.edit(content=_paginate(cmd, f"{len(filtered)} results", rows) if rows else ui_info(f"no {cmd}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
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
        except Exception as e: await message.edit(content=ui_err(str(e)))
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
            await message.edit(content=ui_ok(f"declined {len(incoming)}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
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
            await message.edit(content=ui_ok(f"cancelled {len(outgoing)}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "closedms":
        count = 0; h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                for ch in list(client.private_channels):
                    if isinstance(ch, discord.DMChannel):
                        await s.delete(f"https://discord.com/api/v9/channels/{ch.id}", headers=h)
                        count += 1; await asyncio.sleep(0.3)
            await message.edit(content=ui_ok(f"closed {count}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "readdms":
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}; count = 0
        try:
            async with aiohttp.ClientSession() as s:
                for ch in list(client.private_channels):
                    await s.post(f"https://discord.com/api/v9/channels/{ch.id}/ack",
                                 headers={**h, "Content-Type":"application/json"}, json={})
                    count += 1; await asyncio.sleep(0.2)
            await message.edit(content=ui_ok(f"read {count}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "autoaddback":
        _autoaddback = len(args) < 2 or args[1].lower() in ("on","enable")
        cfg = load_config(); cfg["autoaddback"] = _autoaddback; save_config(cfg)
        await message.edit(content=ui_ok(f"autoaddback → {'on' if _autoaddback else 'off'}"))

    # AUTO
    elif cmd == "giveaway":
        _giveaway_enabled = len(args) < 2 or args[1].lower() in ("on","enable")
        await message.edit(content=ui_ok(f"giveaway → {'on' if _giveaway_enabled else 'off'}"))
    elif cmd == "nitrosniper":
        _nitrosniper_enabled = len(args) < 2 or args[1].lower() in ("on","enable")
        await message.edit(content=ui_ok(f"nitrosniper → {'on' if _nitrosniper_enabled else 'off'}"))
    elif cmd == "autoreact":
        if len(args) < 2: return await message.edit(content=ui_err("usage: autoreact <emoji>"))
        _autoreact_emoji = args[1]; await message.edit(content=ui_ok(f"reacting with {_autoreact_emoji}"))
    elif cmd == "autoreactstop":
        _autoreact_emoji = None; await message.edit(content=ui_ok("stopped"))
    elif cmd in ("multireact","multiautoreact"):
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add" and len(args) >= 3:
            if args[2] in _multireact_pool: return await message.edit(content=ui_info("already in pool"))
            _multireact_pool.append(args[2]); await message.edit(content=ui_ok(f"added ({len(_multireact_pool)})"))
        elif sub in ("remove","rem","del") and len(args) >= 3:
            if args[2] not in _multireact_pool: return await message.edit(content=ui_err("not in pool"))
            _multireact_pool.remove(args[2]); await message.edit(content=ui_ok(f"removed ({len(_multireact_pool)})"))
        elif sub == "list":
            rows = [f"  {GREY}{i:2}.{RESET}  {e}" for i, e in enumerate(_multireact_pool, 1)]
            await message.edit(content=ui_box(f"multi pool — {'ON' if _multireact_enabled else 'OFF'}", rows) if rows else ui_info("empty"))
        elif sub in ("on","enable"):
            if not _multireact_pool: return await message.edit(content=ui_err("pool is empty"))
            _multireact_enabled = True; await message.edit(content=ui_ok("enabled"))
        elif sub in ("off","disable"):
            _multireact_enabled = False; await message.edit(content=ui_ok("disabled"))
        elif sub == "clear":
            _multireact_pool.clear(); _multireact_enabled = False; await message.edit(content=ui_ok("cleared"))
        else: await message.edit(content=ui_info("usage: multireact add/remove/list/on/off/clear"))
    elif cmd == "vsniper":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add" and len(args) >= 4:
            _vsniper_list.append({"code": args[2], "guild_id": args[3]})
            await message.edit(content=ui_ok(f"watching {args[2]}"))
        elif sub == "start":
            if _vsniper_task and not _vsniper_task.done(): return await message.edit(content=ui_info("already running"))
            _vsniper_task = asyncio.create_task(vsniper_loop()); await message.edit(content=ui_ok("started"))
        elif sub == "stop":
            if _vsniper_task: _vsniper_task.cancel(); _vsniper_task = None
            await message.edit(content=ui_ok("stopped"))
        elif sub == "list":
            rows = [f"  {GREY}•{RESET} {e['code']}  {DIM}guild {e['guild_id']}{RESET}" for e in _vsniper_list]
            await message.edit(content=_paginate("vsniper", "watch list", rows) if rows else ui_info("empty"))
        else: await message.edit(content=ui_err("usage: vsniper add/start/stop/list"))

    # PROFILE / STATUS
    elif cmd == "setpfp":
        if len(args) < 2: return await message.edit(content=ui_err("usage: setpfp <url>"))
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(args[1]) as r: img = await r.read()
            ext = args[1].split(".")[-1].split("?")[0].lower()
            mime = {"jpg":"jpeg","jpeg":"jpeg","png":"png","gif":"gif","webp":"webp"}.get(ext,"png")
            b64 = base64.b64encode(img).decode()
            h = {"Authorization": TOKEN, "Content-Type":"application/json", "User-Agent": USER_AGENT}
            async with aiohttp.ClientSession() as s:
                async with s.patch("https://discord.com/api/v9/users/@me",
                                   headers=h, json={"avatar": f"data:image/{mime};base64,{b64}"}) as r2:
                    await message.edit(content=ui_ok("pfp updated") if r2.status == 200 else ui_err(f"failed {r2.status}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "setbio":
        bio = " ".join(args[1:]) if len(args) > 1 else ""
        h = {"Authorization": TOKEN, "Content-Type":"application/json", "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.patch("https://discord.com/api/v9/users/@me/profile", headers=h, json={"bio": bio}) as r:
                    await message.edit(content=ui_ok("bio updated") if r.status == 200 else ui_err(f"failed {r.status}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "setbanner":
        if len(args) < 2: return await message.edit(content=ui_err("usage: setbanner <url>"))
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(args[1]) as r: img = await r.read()
            ext = args[1].split(".")[-1].split("?")[0].lower()
            mime = {"jpg":"jpeg","jpeg":"jpeg","png":"png","gif":"gif","webp":"webp"}.get(ext,"png")
            b64 = base64.b64encode(img).decode()
            h = {"Authorization": TOKEN, "Content-Type":"application/json", "User-Agent": USER_AGENT}
            async with aiohttp.ClientSession() as s:
                async with s.patch("https://discord.com/api/v9/users/@me",
                                   headers=h, json={"banner": f"data:image/{mime};base64,{b64}"}) as r2:
                    await message.edit(content=ui_ok("banner updated") if r2.status == 200 else ui_err(f"failed {r2.status}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "myprofile":
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://discord.com/api/v9/users/@me", headers=h) as r:
                    if r.status != 200: return await message.edit(content=ui_err("failed"))
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
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "accountbackup":
        try: await message.delete()
        except Exception: pass
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        async with aiohttp.ClientSession() as s:
            async with s.get("https://discord.com/api/v9/users/@me", headers=h) as r:
                profile = await r.json() if r.status == 200 else {}
            async with s.get("https://discord.com/api/v9/users/@me/relationships", headers=h) as r:
                rels = await r.json() if r.status == 200 else []
        backup = {"profile": profile, "relationships": rels,
                  "guilds": [{"id": str(g.id), "name": g.name} for g in client.guilds],
                  "timestamp": datetime.now().isoformat()}
        fname = f"backups/account_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(fname, "w") as f: json.dump(backup, f, indent=2)
        await message.channel.send(ui_ok(f"backed up → {fname}"), delete_after=8)
    elif cmd in ("setstatus", "customstatus"):
        if len(args) < 2:
            return await message.edit(content=ui_box("setstatus", [
                f"  {DIM}usage:{RESET}",
                f"  {PREFIX}setstatus <text>",
                f"  {PREFIX}setstatus <emoji>, <text>",
                f"  {PREFIX}setstatus <:name:id>, <text>",
                "", f"  {DIM}examples:{RESET}",
                f"  {PREFIX}setstatus Gaming now",
                f"  {PREFIX}setstatus 🎮, Gaming now",
                f"  {PREFIX}setstatus <:pepe:123456789>, vibing",
            ]))
        full_text = " ".join(args[1:]); emoji_name = None; emoji_id = None; text = full_text.strip()
        if "," in text:
            parts = text.split(",", 1); emoji_part = parts[0].strip(); text_part = parts[1].strip() if len(parts) > 1 else ""
            if not text_part: return await message.edit(content=ui_err("provide status text after the comma"))
            m = re.match(r"<:([a-zA-Z0-9_]+):([0-9]+)>", emoji_part)
            if m: emoji_name = m.group(1); emoji_id = m.group(2)
            elif len(emoji_part) >= 1 and (len(emoji_part) == 1 or any(ord(c) > 127 for c in emoji_part)):
                emoji_name = emoji_part
            else: return await message.edit(content=ui_err("invalid emoji"))
            text = text_part
        if not text: return await message.edit(content=ui_err("provide status text"))
        payload = {"custom_status": {"text": text, "emoji_name": emoji_name, "emoji_id": emoji_id}}
        h = {"Authorization": TOKEN, "Content-Type":"application/json", "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.patch("https://discord.com/api/v9/users/@me/settings", headers=h, json=payload) as r:
                    if r.status == 200:
                        cfg = load_config(); hist = cfg.get("status_history", [])
                        hist.insert(0, {"text": text, "emoji": emoji_name, "time": datetime.now().strftime("%H:%M %d/%m")})
                        cfg["status_history"] = hist[:20]; save_config(cfg)
                        await message.edit(content=ui_ok(f"status set: {emoji_name or ''} {text}".strip()))
                    elif r.status == 429:
                        retry = (await r.json()).get("retry_after", 1)
                        await message.edit(content=ui_warn(f"rate limited — retry in {retry}s"))
                    else: await message.edit(content=ui_err(f"failed {r.status}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "clearstatus":
        payload = {"custom_status": {"text": "", "emoji_name": None, "emoji_id": None}}
        h = {"Authorization": TOKEN, "Content-Type":"application/json", "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.patch("https://discord.com/api/v9/users/@me/settings", headers=h, json=payload) as r:
                    await message.edit(content=ui_ok("cleared") if r.status == 200 else ui_err(f"failed {r.status}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd in ("stealstatus", "copystatus"):
        if len(args) < 2: return await message.edit(content=ui_err("usage: stealstatus <user_id>"))
        uid = args[1].strip("<@!>")
        h = {"Authorization": TOKEN, "User-Agent": USER_AGENT}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://discord.com/api/v9/users/{uid}/profile", headers=h) as r:
                    if r.status != 200: return await message.edit(content=ui_err("cannot fetch profile"))
                    profile = await r.json()
                username = profile.get("user", {}).get("username", "?")
                custom_text = profile.get("user_profile", {}).get("bio", "") or ""
                if not custom_text: return await message.edit(content=ui_err(f"{username} has no visible status"))
                payload = {"custom_status": {"text": custom_text, "emoji_name": None, "emoji_id": None}}
                async with s.patch("https://discord.com/api/v9/users/@me/settings",
                                   headers={**h, "Content-Type":"application/json"}, json=payload) as r2:
                    if r2.status == 200:
                        cfg = load_config(); hist = cfg.get("status_history", [])
                        hist.insert(0, {"text": custom_text, "emoji": None, "time": datetime.now().strftime("%H:%M %d/%m"), "stolen_from": username})
                        cfg["status_history"] = hist[:20]; save_config(cfg)
                        await message.edit(content=ui_ok(f"stole from {username}"))
                    else: await message.edit(content=ui_err(f"failed {r2.status}"))
        except Exception as e: await message.edit(content=ui_err(str(e)))
    elif cmd == "statushistory":
        cfg = load_config(); hist = cfg.get("status_history", [])
        if not hist: return await message.edit(content=ui_info("no status history yet"))
        rows = []
        for i, entry in enumerate(hist[:20], 1):
            emoji = f"{entry['emoji']} " if entry.get("emoji") else ""
            stolen = f"  {DIM}(from {entry['stolen_from']}){RESET}" if entry.get("stolen_from") else ""
            rows.append(f"  {GREY}{i:2}.{RESET} {WHITE}{emoji}{entry['text']}{RESET}  {DIM}{entry['time']}{RESET}{stolen}")
        await message.edit(content=_paginate("status history", "recent", rows))

    else:
        pass


@client.event
async def on_message(message):
    await _dispatch_message(client, message)

# ─────────────────────────────────────────────
# REACTION / VOICE TRIGGER EVENTS
# ─────────────────────────────────────────────

@client.event
async def on_reaction_add(reaction, user):
    if user.id == client.user.id: return
    for t in _triggers.get("reaction", []):
        try:
            if str(reaction.emoji) == str(t.get("emoji")) or (t.get("emoji") and t["emoji"] in str(reaction.emoji)):
                _trigger_fired_counts[t["name"]] += 1
                mode = t.get("mode", "dm")
                if mode == "reply":
                    try: await reaction.message.reply(t.get("reply") or "")
                    except Exception: pass
                elif mode == "channel":
                    try: await reaction.message.channel.send(t.get("reply") or "")
                    except Exception: pass
                elif mode == "dm":
                    try: await user.send(t.get("reply") or "")
                    except Exception: pass
        except Exception: pass

@client.event
async def on_voice_state_update(member, before, after):
    for t in _triggers.get("voice", []):
        try:
            ev = t.get("event", ""); ch_filter = t.get("channel_id")
            fire = False
            if ev == "join" and before.channel is None and after.channel is not None: fire = True
            elif ev == "leave" and before.channel is not None and after.channel is None: fire = True
            elif ev == "move" and before.channel and after.channel and before.channel != after.channel: fire = True
            if fire:
                if ch_filter and str((after.channel or before.channel).id) != str(ch_filter): continue
                _trigger_fired_counts[t["name"]] += 1
                await _monitor_log(member.guild, f"voice trigger: {t['name']}", f"{member} {ev}")
        except Exception: pass

# ─────────────────────────────────────────────
# OTHER EVENTS
# ─────────────────────────────────────────────

@client.event
async def on_message_delete(message):
    if message.author.id == client.user.id: return
    cid = message.channel.id
    _snipe_cache.setdefault(cid, [])
    _snipe_cache[cid].append({
        "author": str(message.author), "author_id": message.author.id,
        "content": message.content or "",
        "attachments": [a.url for a in message.attachments] if message.attachments else [],
        "time": datetime.now().strftime("%H:%M:%S"), "ts": time.time(),
    })
    if len(_snipe_cache[cid]) > SNIPE_LIMIT: _snipe_cache[cid] = _snipe_cache[cid][-SNIPE_LIMIT:]
    if LOGGER_ENABLED:
        log_msg("DEL", f"{message.author} in #{getattr(message.channel,'name','DM')}: {message.content[:100]}")

@client.event
async def on_message_edit(before, after):
    if before.author.id == client.user.id: return
    if before.content == after.content: return
    cid = before.channel.id
    _editsnipe_cache.setdefault(cid, [])
    _editsnipe_cache[cid].append({
        "author": str(before.author), "author_id": before.author.id,
        "before": before.content or "", "after": after.content or "",
        "time": datetime.now().strftime("%H:%M:%S"), "ts": time.time(),
    })
    if len(_editsnipe_cache[cid]) > SNIPE_LIMIT: _editsnipe_cache[cid] = _editsnipe_cache[cid][-SNIPE_LIMIT:]
    if LOGGER_ENABLED:
        log_msg("EDIT", f"{before.author}: '{before.content[:60]}' → '{after.content[:60]}'")

@client.event
async def on_member_join(member):
    if _quarantine["enabled"] and _quarantine["role_id"]:
        created = member.created_at
        age_days = (datetime.now(timezone.utc) - created).days
        if age_days < _quarantine["age_days"]:
            role = member.guild.get_role(int(_quarantine["role_id"]))
            if role:
                try: await member.add_roles(role, reason="quarantine")
                except Exception: pass
    if _monitor["joins"]:
        await _monitor_log(member.guild, "join", f"{member} ({member.id}) joined")
    for t in _triggers.get("member", []):
        try:
            if t.get("event") == "join":
                _trigger_fired_counts[t["name"]] += 1
                await _monitor_log(member.guild, f"member trigger: {t['name']}", f"{member} joined")
        except Exception: pass

@client.event
async def on_member_remove(member):
    if _monitor["leaves"]:
        await _monitor_log(member.guild, "leave", f"{member} ({member.id}) left")
    for t in _triggers.get("member", []):
        try:
            if t.get("event") == "leave":
                _trigger_fired_counts[t["name"]] += 1
                await _monitor_log(member.guild, f"member trigger: {t['name']}", f"{member} left")
        except Exception: pass

@client.event
async def on_member_update(before, after):
    if _monitor["roles"] and before.roles != after.roles:
        await _monitor_log(after.guild, "roles", f"{after} roles updated")
    if _monitor["nicks"] and before.nick != after.nick:
        await _monitor_log(after.guild, "nick", f"{after} nick: {before.nick} → {after.nick}")

@client.event
async def on_group_channel_create(channel):
    if not _agc_state["enabled"]: return
    channel_id = str(channel.id)
    owner_id = str(channel.owner_id) if hasattr(channel, "owner_id") and channel.owner_id else ""
    if owner_id == str(client.user.id): return
    if owner_id in _agc_whitelist: return
    log_msg("AGC", f"trap detected — ch {channel_id}, owner {owner_id}")
    h = {"Authorization": TOKEN, "Content-Type":"application/json", "User-Agent": USER_AGENT}
    async with aiohttp.ClientSession() as s:
        if _agc_state["gc_name"]:
            try: await s.patch(f"https://discord.com/api/v9/channels/{channel_id}", headers=h, json={"name": _agc_state["gc_name"]})
            except Exception: pass
        if _agc_state["gc_icon_url"]:
            try:
                async with s.get(_agc_state["gc_icon_url"]) as r: img = await r.read()
                mime = "image/gif" if img[:6] in (b"GIF87a",b"GIF89a") else "image/png"
                b64 = base64.b64encode(img).decode()
                await s.patch(f"https://discord.com/api/v9/channels/{channel_id}",
                              headers=h, json={"icon": f"data:{mime};base64,{b64}"})
            except Exception: pass
        if _agc_state["leave_msg"]:
            try: await s.post(f"https://discord.com/api/v9/channels/{channel_id}/messages", headers=h, json={"content": _agc_state["leave_msg"]})
            except Exception: pass
        if _agc_state["block"] and owner_id:
            try: await s.put(f"https://discord.com/api/v9/users/@me/relationships/{owner_id}", headers=h, json={"type": 2})
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
                await s.post(_agc_state["webhook_url"], json={"content": f"**AGC** owner `{owner_id}` ch `{channel_id}` members `{', '.join(members)}`"})
            except Exception: pass

# ─────────────────────────────────────────────
# SIGNAL HANDLING + RUN WITH AUTO-RESTART
# ─────────────────────────────────────────────

def _install_signal_handlers():
    def _sig(sig, frame):
        print(f"[signal] {sig} received — graceful exit")
        try: asyncio.get_event_loop().stop()
        except Exception: pass
    try:
        signal.signal(signal.SIGINT, _sig)
        signal.signal(signal.SIGTERM, _sig)
    except Exception:
        pass

_install_signal_handlers()

print(f"[selfbot] starting — prefix: '{PREFIX}' — v{VERSION}")
_attempt = 0
while True:
    try:
        client.run(TOKEN)
        break
    except discord.LoginFailure as e:
        print(f"[FATAL] login failed: {e}"); sys.exit(1)
    except KeyboardInterrupt:
        print("[shutdown]"); break
    except Exception as e:
        _attempt += 1
        if not _auto_restart:
            print(f"[FATAL] {e}"); raise
        backoff = min(2 ** _attempt, _MAX_RESTART_BACKOFF)
        print(f"[crash] attempt {_attempt} — restarting in {backoff}s — {e}")
        traceback.print_exc()
        time.sleep(backoff)
        continue
