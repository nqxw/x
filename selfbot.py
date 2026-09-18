# selfbot.py | Python 3.10+ | discord.py-self + aiohttp

import discord
import asyncio
import aiohttp
import json
import os
import sys
import re
import base64
import datetime as dt
import configparser
from datetime import datetime, timezone, timedelta
from uuid import uuid4

# ─────────────────────────────────────────────
# BOOTSTRAP
# ─────────────────────────────────────────────

os.makedirs("config", exist_ok=True)
os.makedirs("database", exist_ok=True)

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

print(f"[selfbot] token loaded: {TOKEN[:10]}...{TOKEN[-5:] if len(TOKEN) > 15 else '(short)'}")

if not TOKEN or TOKEN in ("YOUR_TOKEN_HERE", "", "None"):
    print("[FATAL] No token found. Set TOKEN env var or put it in config.json")
    sys.exit(1)

PREFIX         = os.environ.get("PREFIX") or _cfg.get("prefix", ".")
AUTOQUEST_ENABLED = (
    os.environ.get("AUTOQUEST", "").lower() in ("1", "true", "yes")
    or _cfg.get("autoquest_enabled", False)
)

LOG_FILE       = "message_log.txt"
AUTO_RESPONSES = {}
SNIPER_ENABLED = True
LOGGER_ENABLED = False

# ─────────────────────────────────────────────
# UI HELPERS — discord markdown (mobile + desktop safe)
# ─────────────────────────────────────────────

# Discord markdown tokens used in responses
R  = ""; B  = "**"; DIM= ""
CY = ""; GR = ""; YE = ""
RD = ""; BL = ""; MG = ""; WH = ""

def ansi(text):
    """For command responses — just return text stripped of any escape codes."""
    import re as _re
    clean = _re.sub(r'\x1b\[[0-9;]*m', '', text)
    # strip leftover empty bold markers
    clean = clean.replace("****", "")
    return clean.strip()

def _box(lines):
    return "```\n" + "\n".join(lines) + "\n```"

def help_header(title, subtitle=""):
    bar = "─" * 36
    t = f"> {title.lower()}"
    if subtitle:
        t += f"  {subtitle}"
    return f"{bar}\n{t}\n{bar}"

def help_row(cmd, desc, indent=0):
    pad = "  " * indent
    return f"{pad}├ {cmd}  —  {desc}"

def help_section(name):
    return f"\n  [{name}]"

# ─────────────────────────────────────────────
# PLATFORM SPOOFER
# ─────────────────────────────────────────────

PLATFORM_MAP = {
    "phone":     {"os": "iOS", "browser": "Discord iOS"},
    "android":   {"os": "Android", "browser": "Discord Android"},
    "desktop":   {"os": "Windows", "browser": "Discord Client"},
    "web":       {"os": "Windows", "browser": "Chrome"},
    "console":   {"os": "PlayStation 4", "browser": "Discord Embedded"},
    "xbox":      {"os": "Xbox One", "browser": "Discord Embedded"},
    "playstation": {"os": "PlayStation 4", "browser": "Discord Embedded"},
    "vr":        {"os": "Windows", "browser": "Discord Embedded"},
}

_current_platform = "desktop"

# ─────────────────────────────────────────────
# HYPESQUAD
# ─────────────────────────────────────────────

HOUSE_NAMES = {1: "Bravery", 2: "Brilliance", 3: "Balance"}
HOUSE_IDS   = {"bravery": 1, "brilliance": 2, "balance": 3}

async def change_hypesquad(session, token, house_id):
    url = "https://discord.com/api/v9/hypesquad/online"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    try:
        async with session.post(url, headers=headers, json={"house_id": house_id}) as resp:
            if resp.status in (200, 201, 204):
                return True, HOUSE_NAMES[house_id]
            text = await resp.text()
            try:
                data = json.loads(text)
                return False, data.get("message", f"HTTP {resp.status}")
            except Exception:
                return False, f"HTTP {resp.status}"
    except Exception as e:
        return False, str(e)

async def remove_hypesquad(session, token):
    url = "https://discord.com/api/v9/hypesquad/online"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    try:
        async with session.delete(url, headers=headers) as resp:
            if resp.status in (200, 201, 204):
                return True, "removed"
            text = await resp.text()
            try:
                data = json.loads(text)
                return False, data.get("message", f"HTTP {resp.status}")
            except Exception:
                return False, f"HTTP {resp.status}"
    except Exception as e:
        return False, str(e)


async def set_platform(platform: str):
    """Change the gateway identify properties to spoof platform."""
    global _current_platform
    props = PLATFORM_MAP.get(platform.lower())
    if not props:
        return False

    _current_platform = platform.lower()

    # discord.py-self exposes the websocket; patch the identify properties
    try:
        ws = client.ws
        if ws and hasattr(ws, '_identify'):
            # Force a re-identify by closing and reconnecting
            # The properties are baked at IDENTIFY time so we need reconnect
            pass
        # Patch the internal identify data on the connection
        conn = client._connection
        if hasattr(conn, '_identify'):
            conn._identify['properties']['$os'] = props['os']
            conn._identify['properties']['$browser'] = props['browser']
            conn._identify['properties']['$device'] = props['browser']
    except Exception as e:
        print(f"[Platform] patch error: {e}")

    # Easiest reliable method: change presence which re-sends gateway data
    # The platform indicator is set in the IDENTIFY payload on reconnect
    # We store it and reconnect the ws
    try:
        if client.ws:
            await client.ws.close(code=4000)  # triggers auto-reconnect with new identify
    except Exception as e:
        print(f"[Platform] reconnect error: {e}")

    return True

# Patch the identify payload before connection
_original_identify = None

async def patched_identify(self, *, resume=False, reconnect=True):
    global _current_platform
    props = PLATFORM_MAP.get(_current_platform, PLATFORM_MAP["desktop"])
    try:
        data = self._identify if hasattr(self, '_identify') else {}
        if 'properties' in data:
            data['properties']['$os'] = props['os']
            data['properties']['$browser'] = props['browser']
            data['properties']['$device'] = props['browser']
    except Exception:
        pass
    if _original_identify:
        return await _original_identify(self, resume=resume, reconnect=reconnect)

# ─────────────────────────────────────────────
# CLIENT
# ─────────────────────────────────────────────

client = discord.Client(
    chunk_guilds_at_startup=False,
    request_guilds=True,
)

# ─────────────────────────────────────────────
# RPC CONFIG
# ─────────────────────────────────────────────

def load_rpc_config():
    path = "config/rpc_config.json"
    default = {
        "enabled": False, "type": "playing", "name": "selfbot",
        "state": "running", "details": "", "url": "https://twitch.tv/discord",
        "application_id": None, "large_image": "", "large_text": "",
        "small_image": "", "small_text": "", "start_timestamp": None,
        "end_timestamp": None,
        "party": {"enabled": False, "current": 1, "max": 5},
        "buttons": [{"label": "", "url": ""}, {"label": "", "url": ""}],
    }
    if not os.path.exists(path):
        try:
            with open(path, "w") as f:
                json.dump(default, f, indent=4)
        except Exception:
            pass
        return default
    try:
        with open(path, "r") as f:
            data = json.load(f)
        for k, v in default.items():
            if k not in data:
                data[k] = v
        return data
    except Exception:
        return default

def save_rpc_config(cfg):
    try:
        with open("config/rpc_config.json", "w") as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        print(f"[RPC] save error: {e}")

# ─────────────────────────────────────────────
# BRAND RPC PRESETS
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# BRAND RPC — complete rewrite
# Images use Discord's CDN-proxied URLs via the streaming gateway.
# The trick: pass image URLs directly as large_image strings —
# discord.py-self forwards them as-is in the PRESENCE_UPDATE payload.
# Discord's client renders any https:// URL set in large_image/small_image
# when the activity has no application_id (or one that supports external assets).
# ─────────────────────────────────────────────

# CDN image URLs — hosted on Discord's own CDN or well-known stable CDNs
_IMG = {
    "spotify_large":      "https://i.scdn.co/image/ab67616d00001e02ff9ca10b55ce82ae553d50e",
    "spotify_small":      "https://cdn.discordapp.com/emojis/1090318861818400919.webp?size=96",
    "youtube_large":      "https://cdn.discordapp.com/emojis/1090318861818400919.webp?size=96",
    "youtube_icon":       "https://www.youtube.com/s/desktop/d743f786/img/favicon_144x144.png",
    "xbox_large":         "https://cdn.discordapp.com/emojis/1090318861818400919.webp?size=96",
    "xbox_icon":          "https://images-eds-ssl.xboxlive.com/image?url=4rt9.lXDC4H_93laV1_eHHFT949fUipzkiFOBH3fAiZZUCdYojwUyX2aTonS1aIwMrx6NUIsHfUHSLzjGJFxxk0j.4kQAE2o4IgKF4tXV8-",
    "playstation_large":  "https://cdn.discordapp.com/emojis/1090318861818400919.webp?size=96",
    "playstation_icon":   "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4e/Playstation_logo_colour.svg/240px-Playstation_logo_colour.svg.png",
    "crunchyroll_large":  "https://cdn.discordapp.com/emojis/1090318861818400919.webp?size=96",
    "crunchyroll_icon":   "https://www.crunchyroll.com/build/assets/img/favicons/favicon-96x96.png",
    "roblox_large":       "https://cdn.discordapp.com/emojis/1090318861818400919.webp?size=96",
    "roblox_icon":        "https://images.rbxcdn.com/9f33cdedd98d820ee456fc98aed9f5c5-roblox_logo_lightmode.svg",
}

# The most reliable image approach for discord.py-self selfbots:
# Use application_id from a real registered Discord app that has assets.
# These are the verified working app IDs with their registered asset names:
BRAND_APP_IDS = {
    "spotify":      367827983903490050,
    "youtube":      880218394199220334,
    "xbox":         438122941302046720,
    "roblox":       363445589247131668,
    "crunchyroll":  1020123345567822899,
}

# Registered asset keys per app (verified from Discord's activity registry)
BRAND_ASSETS = {
    "spotify": {
        "large": "spotify:ab67616d00001e02ff9ca10b55ce82ae553d50e",
        "small": "spotify:ab6761610000f178049d8eda6f0fd7a34bb0db9",
    },
    "youtube": {
        "large": "youtube_logo",
        "small": "youtube_logo",
    },
    "xbox": {
        "large": "01_xbox_app_icon",
        "small": "01_xbox_app_icon",
    },
    "roblox": {
        "large": "roblox",
        "small": "roblox",
    },
    "crunchyroll": {
        "large": "crunchyroll",
        "small": "crunchyroll",
    },
    "playstation": {
        "large": "playstation",
        "small": "playstation",
    },
}

BRAND_PRESETS = {
    "spotify":     {"type": "listening", "name": "Spotify"},
    "youtube":     {"type": "watching",  "name": "YouTube"},
    "xbox":        {"type": "playing",   "name": "Xbox"},
    "playstation": {"type": "playing",   "name": "PlayStation"},
    "crunchyroll": {"type": "watching",  "name": "Crunchyroll"},
    "roblox":      {"type": "playing",   "name": "Roblox"},
    "custom":      {"type": "playing",   "name": ""},
}

async def apply_brand_rpc(brand: str, user_args: list):
    """Build and apply a brand RPC. Uses registered app assets for images."""
    try:
        from discord.activity import ActivityAssets, ActivityTimestamps
        from discord import ActivityType, Activity
    except ImportError as e:
        print(f"[RPC] import error: {e}")
        return False

    preset = BRAND_PRESETS.get(brand)
    if not preset:
        return False

    type_map = {
        "playing":   ActivityType.playing,
        "streaming": ActivityType.streaming,
        "listening": ActivityType.listening,
        "watching":  ActivityType.watching,
        "competing": ActivityType.competing,
    }

    act_type = type_map.get(preset["type"], ActivityType.playing)
    now = datetime.now(timezone.utc)

    kwargs = {
        "type": act_type,
        "name": preset["name"] or brand.capitalize(),
    }

    # Set application_id — this is what links asset keys to the right registry
    app_id = BRAND_APP_IDS.get(brand)
    if app_id:
        kwargs["application_id"] = app_id

    # Get asset keys for this brand
    assets = BRAND_ASSETS.get(brand, {})
    large_img = assets.get("large", "")
    small_img = assets.get("small", "")

    def _ts(start=None, end=None):
        try:
            kw = {}
            if start: kw["start"] = start
            if end:   kw["end"]   = end
            return ActivityTimestamps(**kw)
        except Exception:
            return None

    def _parts(n=3):
        raw = " ".join(user_args) if user_args else ""
        p = [x.strip() for x in raw.split("|")]
        while len(p) < n:
            p.append("")
        return p

    assets_kwargs = {}

    if brand == "spotify":
        p = _parts(3)
        title  = p[0] or "Unknown"
        artist = p[1] or "Unknown"
        try: dur = int(p[2]) if p[2] else 210
        except ValueError: dur = 210
        kwargs["details"] = title
        kwargs["state"]   = artist
        assets_kwargs = {
            "large_image": large_img,
            "large_text":  "Spotify",
            "small_image": small_img,
            "small_text":  "Listening on Spotify",
        }
        ts = _ts(start=now, end=now + timedelta(seconds=dur))
        if ts: kwargs["timestamps"] = ts

    elif brand == "youtube":
        p = _parts(3)
        video   = p[0] or "Video"
        channel = p[1] or "Channel"
        try: dur = int(p[2]) if p[2] else 600
        except ValueError: dur = 600
        kwargs["details"] = video
        kwargs["state"]   = channel
        assets_kwargs = {
            "large_image": large_img,
            "large_text":  channel,
            "small_image": small_img,
            "small_text":  "YouTube",
        }
        ts = _ts(start=now, end=now + timedelta(seconds=dur))
        if ts: kwargs["timestamps"] = ts

    elif brand == "xbox":
        p = _parts(2)
        game    = p[0] or "Game"
        details = p[1] or "Playing on Xbox"
        kwargs["details"] = game
        kwargs["state"]   = details
        assets_kwargs = {
            "large_image": large_img,
            "large_text":  game,
            "small_image": small_img,
            "small_text":  "Xbox",
        }
        ts = _ts(start=now)
        if ts: kwargs["timestamps"] = ts

    elif brand == "playstation":
        p = _parts(2)
        game    = p[0] or "Game"
        details = p[1] or "Playing on PlayStation"
        kwargs["details"] = game
        kwargs["state"]   = details
        # PlayStation has no registered app — use streaming type trick for icon
        kwargs["type"] = ActivityType.playing
        assets_kwargs = {
            "large_image": large_img,
            "large_text":  game,
            "small_image": small_img,
            "small_text":  "PlayStation",
        }
        ts = _ts(start=now)
        if ts: kwargs["timestamps"] = ts

    elif brand == "crunchyroll":
        p = _parts(3)
        anime   = p[0] or "Anime"
        episode = p[1] or ""
        try: dur = int(p[2]) if p[2] else 1440
        except ValueError: dur = 1440
        kwargs["details"] = anime
        if episode: kwargs["state"] = episode
        assets_kwargs = {
            "large_image": large_img,
            "large_text":  anime,
            "small_image": small_img,
            "small_text":  "Crunchyroll",
        }
        ts = _ts(start=now, end=now + timedelta(seconds=dur))
        if ts: kwargs["timestamps"] = ts

    elif brand == "roblox":
        p = _parts(5)
        game       = p[0] or "Roblox"
        details    = p[1] or game
        state      = p[2] or "Playing on Roblox"
        large_text = p[3] or game
        small_text = p[4] or "Roblox"
        kwargs["name"]    = "Roblox"
        kwargs["details"] = details
        kwargs["state"]   = state
        assets_kwargs = {
            "large_image": large_img,
            "large_text":  large_text,
            "small_image": small_img,
            "small_text":  small_text,
        }
        ts = _ts(start=now)
        if ts: kwargs["timestamps"] = ts

    elif brand == "custom":
        p = _parts(3)
        kwargs["name"]    = p[0] or "Custom"
        if p[1]: kwargs["details"] = p[1]
        if p[2]: kwargs["state"]   = p[2]
        ts = _ts(start=now)
        if ts: kwargs["timestamps"] = ts

    else:
        ts = _ts(start=now)
        if ts: kwargs["timestamps"] = ts

    if assets_kwargs:
        try:
            kwargs["assets"] = ActivityAssets(**assets_kwargs)
        except Exception as e:
            print(f"[RPC] assets error: {e}")

    try:
        await client.change_presence(activity=Activity(**kwargs))
        print(f"[RPC] brand={brand} app_id={app_id} large={large_img}")
        return True
    except Exception as e:
        print(f"[RPC] presence error: {e}")
        return False

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

def log_message(tag: str, content: str):
    if not LOGGER_ENABLED:
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{tag}] {content}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line, end="")

# ─────────────────────────────────────────────
# RPC ENGINE
# ─────────────────────────────────────────────

def parse_timestamp(value, is_end=False):
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        value = value.strip()
        if value.lower() in ("none", ""):
            return None
        if value.isdigit():
            try:
                return datetime.fromtimestamp(float(value), tz=timezone.utc)
            except Exception:
                return None
        if ":" in value:
            parts = value.split(":")
            if len(parts) in (2, 3):
                try:
                    delta = (int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])) if len(parts) == 3 else int(parts[0]) * 60 + int(parts[1])
                    now = datetime.now(timezone.utc)
                    return now + timedelta(seconds=delta) if is_end else now - timedelta(seconds=delta)
                except ValueError:
                    pass
    return None

async def update_rpc():
    try:
        cfg = load_rpc_config()
        if not cfg.get("enabled", False):
            await client.change_presence(activity=None)
            return
        try:
            from discord.activity import ActivityAssets, ActivityParty, ActivityTimestamps
            from discord import ActivityButton, ActivityType, Activity
        except ImportError as e:
            print(f"[RPC] import error: {e}")
            return

        type_map = {
            "playing": ActivityType.playing, "streaming": ActivityType.streaming,
            "listening": ActivityType.listening, "watching": ActivityType.watching,
            "competing": ActivityType.competing,
        }
        act_type = type_map.get(str(cfg.get("type", "playing")).lower(), ActivityType.playing)
        kwargs = {"name": cfg.get("name", "selfbot") or "selfbot", "type": act_type}

        if cfg.get("application_id"):
            try:
                kwargs["application_id"] = int(cfg["application_id"])
            except (ValueError, TypeError):
                pass

        if act_type == ActivityType.streaming:
            kwargs["url"] = cfg.get("url") or "https://twitch.tv/discord"
        if cfg.get("state"):   kwargs["state"]   = cfg["state"]
        if cfg.get("details"): kwargs["details"] = cfg["details"]

        try:
            ts = {}
            ps = parse_timestamp(cfg.get("start_timestamp"), is_end=False)
            ts["start"] = ps if ps else datetime.now(timezone.utc)
            pe = parse_timestamp(cfg.get("end_timestamp"), is_end=True)
            if pe: ts["end"] = pe
            kwargs["timestamps"] = ActivityTimestamps(**ts)
        except Exception as e:
            print(f"[RPC] timestamp error: {e}")

        ak = {k: cfg[k] for k in ("large_image","large_text","small_image","small_text") if cfg.get(k)}
        if ak:
            try: kwargs["assets"] = ActivityAssets(**ak)
            except Exception: pass

        pc = cfg.get("party", {})
        if pc.get("enabled"):
            try:
                kwargs["party"] = ActivityParty(
                    id="selfbot-party",
                    current_size=int(pc.get("current", 1)),
                    max_size=int(pc.get("max", 5)),
                )
            except Exception: pass

        buttons = [
            ActivityButton(label=b["label"], url=b["url"])
            for b in cfg.get("buttons", [])[:2]
            if b.get("label") and b.get("url")
        ]
        if buttons:
            try: kwargs["buttons"] = buttons
            except Exception: pass

        await client.change_presence(activity=Activity(**kwargs))
    except Exception as e:
        print(f"[RPC] update_rpc error: {e}")

async def rpc_prompt(channel, author, prompt_text):
    pm = await channel.send(f"```ansi\n{YE}✏  {prompt_text}{R}\n{DIM}type value — 60s timeout{R}\n```")
    def check(m):
        return m.author.id == author.id and m.channel.id == channel.id
    try:
        msg = await client.wait_for("message", check=check, timeout=60.0)
        val = msg.content.strip()
        try: await msg.delete()
        except Exception: pass
        try: await pm.delete()
        except Exception: pass
        return None if val.lower() == "none" else val
    except asyncio.TimeoutError:
        try: await pm.delete()
        except Exception: pass
        await channel.send("```ansi\n\u001b[31m✗  timed out\u001b[0m\n```", delete_after=4)
        return "__TIMEOUT__"

# ─────────────────────────────────────────────
# QUEST SYSTEM
# ─────────────────────────────────────────────

class APIError(Exception):
    def __init__(self, status, body=None, text=""):
        super().__init__(f"Discord API error {status}")
        self.status = status; self.body = body or {}; self.text = text

def clean_token(token):
    return token.strip().strip('"').strip("'") if token else None

def decode_token_user_id(token):
    token = clean_token(token)
    if not token or "." not in token: return None
    first = token.split(".", 1)[0]
    padding = "=" * (-len(first) % 4)
    try: return base64.b64decode(first + padding).decode("utf-8")
    except Exception: return None

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) discord/1.0.9044 Chrome/120.0.6099.291 "
    "Electron/28.2.10 Safari/537.36"
)
CLIENT_BUILD_NUMBER = 971383

def get_super_properties():
    return {
        "os": "Windows", "browser": "Chrome", "device": "",
        "system_locale": "en", "has_client_mods": False,
        "browser_user_agent": USER_AGENT, "browser_version": "142.0.0.0",
        "os_version": "10", "referrer": "", "referring_domain": "",
        "referrer_current": "https://discord.com/",
        "referring_domain_current": "discord.com",
        "release_channel": "stable",
        "client_launch_id": str(uuid4()),
        "client_build_number": CLIENT_BUILD_NUMBER,
        "client_event_source": None,
        "launch_signature": str(uuid4()),
        "client_heartbeat_session_id": str(uuid4()),
        "client_app_state": "focused",
    }

def get_headers(token):
    sp = base64.b64encode(json.dumps(get_super_properties()).encode()).decode()
    return {
        "authorization": token, "accept": "*/*",
        "accept-language": "en,en-US;q=0.9",
        "content-type": "application/json",
        "user-agent": USER_AGENT,
        "x-super-properties": sp,
        "x-discord-locale": "en-US",
        "x-discord-timezone": "Africa/Algiers",
        "x-debug-options": "bugReporterEnabled",
        "origin": "https://discord.com",
        "referer": "https://discord.com/quest-home",
        "sec-ch-ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty", "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin", "priority": "u=1, i",
    }

async def api_request(session, method, url, headers=None, json_body=None):
    async with session.request(method, url, headers=headers, json=json_body) as resp:
        text = await resp.text()
        body = {}
        if text:
            try: body = json.loads(text)
            except Exception: body = {"raw": text}
        if resp.status >= 400:
            raise APIError(resp.status, body, text)
        return body

async def request_with_retry(session, method, url, headers=None, json_body=None, retries=4):
    last_error = None
    for attempt in range(retries):
        try:
            return await api_request(session, method, url, headers=headers, json_body=json_body)
        except APIError as exc:
            last_error = exc
            if exc.status == 429 or exc.status >= 500:
                retry_after = 0.8 * (attempt + 1)
                if isinstance(exc.body, dict):
                    try: retry_after = float(exc.body.get("retry_after", retry_after))
                    except Exception: pass
                await asyncio.sleep(retry_after)
                continue
            raise
    raise last_error

SUPPORTED_TASKS = (
    "WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE",
    "PLAY_ON_DESKTOP", "PLAY_ON_DESKTOP_V2",
    "PLAY_ACTIVITY", "STREAM_ON_DESKTOP",
)

class QuestRecord:
    def __init__(self, data):
        self.data = data
        self.selected_task = self._pick_task()
        self.target = float(self.tasks.get(self.selected_task, {}).get("target", 0) or 0)

    @property
    def id(self): return str(self.data.get("id"))
    @property
    def config(self): return self.data.get("config", {})
    @property
    def messages(self): return self.config.get("messages", {})
    @property
    def user_status(self): return self.data.get("user_status") or {}
    @property
    def tasks(self):
        tc = (self.config.get("task_config_v2") or self.config.get("task_config")
              or self.config.get("taskConfigV2") or self.config.get("taskConfig") or {})
        return tc.get("tasks", {})
    @property
    def name(self):
        return (self.messages.get("quest_name") or self.messages.get("questName")
                or self.messages.get("game_title") or "Unknown Quest")
    @property
    def reward_name(self):
        rewards = self.config.get("rewards_config", {}).get("rewards", [])
        if rewards:
            m = rewards[0].get("messages", {})
            return m.get("name") or m.get("name_with_article") or "Unknown Reward"
        return "Unknown Reward"
    @property
    def app_id(self): return self.config.get("application", {}).get("id")
    @property
    def expires_at(self): return self.config.get("expires_at") or self.config.get("expiresAt")

    def expires_relative(self):
        if not self.expires_at: return "unknown"
        try:
            parsed = dt.datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            return f"<t:{int(parsed.timestamp())}:R>"
        except Exception: return "unknown"

    def is_expired(self):
        if not self.expires_at: return False
        try:
            parsed = dt.datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            return dt.datetime.now(dt.timezone.utc) > parsed
        except Exception: return False

    def is_completed(self):
        return bool(self.user_status.get("completed_at") or self.user_status.get("completedAt"))
    def is_enrolled(self):
        return bool(self.user_status.get("enrolled_at"))
    def is_supported(self):
        return self.selected_task in SUPPORTED_TASKS
    def progress_value(self):
        p = self.user_status.get("progress", {}).get(self.selected_task, {})
        return float(p.get("value", 0) or 0) if p else 0.0
    def progress_percent(self):
        return min(100, int((self.progress_value() / self.target) * 100)) if self.target else 0
    def _pick_task(self):
        for t in SUPPORTED_TASKS:
            if t in self.tasks: return t
        return next(iter(self.tasks.keys()), "UNKNOWN")

class QuestService:
    def __init__(self, token, speed_mode="fast"):
        self.token = clean_token(token)
        self.speed_mode = speed_mode
        self.user_id = decode_token_user_id(token)
        self.headers = get_headers(self.token)

    async def fetch_quests(self, session):
        try:
            payload = await request_with_retry(session, "GET",
                "https://discord.com/api/v9/quests/@me", headers=self.headers)
            return [QuestRecord(r) for r in payload.get("quests", []) if not QuestRecord(r).is_expired()]
        except Exception as e:
            print(f"[Quest] fetch error: {e}")
            return []

    async def refresh_quest(self, session, quest_id):
        for q in await self.fetch_quests(session):
            if q.id == quest_id: return q
        return None

    async def get_user_status(self, session, quest_id):
        return await request_with_retry(session, "GET",
            f"https://discord.com/api/v9/quests/{quest_id}/user-status", headers=self.headers)

    async def enroll(self, session, quest):
        payload = await request_with_retry(session, "POST",
            f"https://discord.com/api/v9/quests/{quest.id}/enroll",
            headers=self.headers,
            json_body={"location": 11, "is_targeted": False, "metadata_raw": None})
        if payload: quest.data["user_status"] = payload
        return True

    async def run_quest(self, session, quest, on_update=None):
        while True:
            try:
                if not quest.is_enrolled():
                    await self.enroll(session, quest)
                    await self._emit(on_update, quest, "enrolled", status="enrolled")
                if not quest.is_supported():
                    await self._emit(on_update, quest, "unsupported task", status="unsupported")
                    return {"status": "unsupported", "quest": quest}
                result = await self._run_solver(session, quest, on_update)
                if result["status"] == "completed": return result
                fresh = await self.refresh_quest(session, quest.id)
                if fresh and fresh.is_completed():
                    await self._emit(on_update, fresh, "completed", percent=100, status="completed")
                    return {"status": "completed", "quest": fresh, "percent": 100}
                if fresh: quest = fresh
                await self._emit(on_update, quest, f"recovering from {quest.progress_percent()}%",
                    percent=quest.progress_percent(), status="recovering")
                await asyncio.sleep(5.0)
            except APIError as exc:
                if exc.status == 404:
                    fresh = await self.refresh_quest(session, quest.id)
                    if fresh and fresh.is_completed():
                        await self._emit(on_update, fresh, "completed", percent=100, status="completed")
                        return {"status": "completed", "quest": fresh, "percent": 100}
                    if fresh: quest = fresh
                    await asyncio.sleep(5.0); continue
                if exc.status in (401, 403):
                    msg = f"api error {exc.status}"
                    await self._emit(on_update, quest, msg, status="failed_unrecoverable")
                    return {"status": "failed_unrecoverable", "quest": quest, "percent": quest.progress_percent(), "reason": msg}
                await asyncio.sleep(5.0)
            except asyncio.CancelledError: raise
            except Exception as e:
                print(f"[Quest] run error: {e}")
                await asyncio.sleep(5.0)

    async def _run_solver(self, session, quest, on_update=None):
        task = quest.selected_task
        await self._emit(on_update, quest, "solver active", status="running")
        if task in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
            return await self._run_video(session, quest, on_update)
        if task in ("PLAY_ON_DESKTOP", "PLAY_ON_DESKTOP_V2"):
            payloads = [{"stream_key": f"call:{quest.id}:1", "terminal": False}]
            if quest.app_id: payloads.append({"application_id": quest.app_id, "terminal": False})
            return await self._run_heartbeat(session, quest, payloads, on_update)
        if task == "PLAY_ACTIVITY":
            uk = self.user_id or quest.id
            return await self._run_heartbeat(session, quest, [
                {"stream_key": f"call:{uk}:1", "terminal": False},
                {"stream_key": f"call:{quest.id}:1", "terminal": False},
            ], on_update)
        if task == "STREAM_ON_DESKTOP":
            return await self._run_heartbeat(session, quest,
                [{"stream_key": f"call:{quest.id}:1", "terminal": False}], on_update)
        return {"status": "unsupported", "quest": quest, "percent": quest.progress_percent()}

    async def _run_video(self, session, quest, on_update=None):
        interval = 5.0 if self.speed_mode == "fast" else 7.5
        last_good = quest.progress_value()
        started_at = int(dt.datetime.now(dt.timezone.utc).timestamp()) - int(last_good)
        try:
            while last_good < quest.target:
                ts = min(float(quest.target), float(int(dt.datetime.now(dt.timezone.utc).timestamp()) - started_at))
                try:
                    data = await request_with_retry(session, "POST",
                        f"https://discord.com/api/v9/quests/{quest.id}/video-progress",
                        headers=self.headers, json_body={"timestamp": ts}, retries=2)
                except APIError as exc:
                    if exc.status == 400:
                        sd = await self.get_user_status(session, quest.id)
                        quest.data["user_status"] = sd
                        last_good = max(last_good, quest.progress_value())
                        if quest.is_completed():
                            await self._emit(on_update, quest, "completed", percent=100, status="completed")
                            return {"status": "completed", "quest": quest, "percent": 100}
                        await asyncio.sleep(1.1); continue
                    raise
                if data: quest.data["user_status"] = data
                last_good = max(last_good, ts, quest.progress_value())
                pct = min(100, int((last_good / quest.target) * 100)) if quest.target else 0
                await self._emit(on_update, quest, f"progressing [{pct}%]", percent=pct, status="running")
                if data.get("completed_at"):
                    fresh = await self.get_user_status(session, quest.id)
                    quest.data["user_status"] = fresh
                    await self._emit(on_update, quest, "completed", percent=100, status="completed")
                    return {"status": "completed", "quest": quest, "percent": 100}
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            await self._emit(on_update, quest, "stopped", percent=quest.progress_percent(), status="manually_stopped")
            raise
        if quest.is_completed() or quest.progress_value() >= quest.target:
            await self._emit(on_update, quest, "completed", percent=100, status="completed")
            return {"status": "completed", "quest": quest, "percent": 100}
        return {"status": "recovering", "quest": quest, "percent": quest.progress_percent()}

    async def _run_heartbeat(self, session, quest, payloads, on_update=None):
        interval = 30 if self.speed_mode == "fast" else 40
        active_payload = payloads[0]; last_percent = -1
        try:
            while True:
                data = None
                for payload in payloads:
                    try:
                        data = await request_with_retry(session, "POST",
                            f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                            headers=self.headers, json_body=payload, retries=2)
                        active_payload = payload; break
                    except APIError: continue
                if data is None:
                    try:
                        sd = await self.get_user_status(session, quest.id)
                        quest.data["user_status"] = sd
                    except APIError as exc:
                        if exc.status == 404: await asyncio.sleep(2.0); continue
                        raise
                else: quest.data["user_status"] = data
                pct = quest.progress_percent()
                if pct != last_percent:
                    await self._emit(on_update, quest, f"progressing [{pct}%]", percent=pct, status="running")
                    last_percent = pct
                if quest.is_completed() or quest.progress_value() >= quest.target: break
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            await self._send_terminal(session, quest.id, active_payload)
            await self._emit(on_update, quest, "stopped", percent=quest.progress_percent(), status="manually_stopped")
            raise
        await self._send_terminal(session, quest.id, active_payload)
        if quest.is_completed() or quest.progress_value() >= quest.target:
            await self._emit(on_update, quest, "completed", percent=100, status="completed")
            return {"status": "completed", "quest": quest, "percent": 100}
        return {"status": "recovering", "quest": quest, "percent": quest.progress_percent()}

    async def _send_terminal(self, session, quest_id, payload):
        try:
            t = dict(payload); t["terminal"] = True
            await request_with_retry(session, "POST",
                f"https://discord.com/api/v9/quests/{quest_id}/heartbeat",
                headers=self.headers, json_body=t, retries=2)
        except Exception: pass

    async def _emit(self, cb, quest, message, percent=None, status=None):
        if cb is None: return
        payload = {
            "quest_id": quest.id, "quest_name": quest.name,
            "percent": percent if percent is not None else quest.progress_percent(),
            "status": status or "running", "message": message, "quest": quest,
        }
        try:
            res = cb(payload)
            if asyncio.iscoroutine(res): await res
        except Exception: pass

# ─────────────────────────────────────────────
# ORB BADGE
# ─────────────────────────────────────────────

ORB_BADGE_SKU_ID = "1342211853484429445"

async def _claim_orb_badge(session, token):
    headers = {
        "accept": "*/*", "authorization": token, "content-type": "application/json",
        "origin": "https://discord.com", "referer": "https://discord.com/shop?tab=orbs",
        "user-agent": USER_AGENT,
    }
    balance = None
    for url in ["https://discord.com/api/v9/users/@me/orbs/balance",
                "https://discord.com/api/v9/users/@me/virtual-currency/balance"]:
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status < 400:
                    body = json.loads(await resp.text()) if await resp.text() else {}
                    for key in ("balance", "discord_orb", "orbs", "amount", "total"):
                        val = body.get(key)
                        if isinstance(val, (int, float)): balance = int(val); break
        except Exception: pass
        if balance is not None: break
    try:
        url = f"https://discord.com/api/v9/virtual-currency/skus/{ORB_BADGE_SKU_ID}/redeem"
        async with session.post(url, headers=headers, json={}) as resp:
            body = json.loads(await resp.text()) if await resp.text() else {}
            if resp.status in (200, 201, 204):
                return {"status": "SUCCESS", "balance": balance}
            return {"status": "FAILED", "code": resp.status, "message": body.get("message", "Redeem failed")}
    except Exception as e:
        return {"status": "FAILED", "code": "network", "message": str(e)}

# ─────────────────────────────────────────────
# AUTOQUEST
# ─────────────────────────────────────────────

async def run_autoquest_pass_now(token):
    try:
        service = QuestService(token, speed_mode="fast")
        async with aiohttp.ClientSession() as session:
            quests = await service.fetch_quests(session)
            active = [q for q in quests if not q.is_completed() and q.is_supported()]
            if not active:
                print("[AutoQuest] no active quests."); return
            for quest in active:
                print(f"[AutoQuest] running: {quest.name}")
                result = await service.run_quest(session, quest)
                if result and result.get("status") == "completed":
                    print(f"[AutoQuest] ✅ {quest.name} | {quest.reward_name}")
    except Exception as e:
        print(f"[AutoQuest] error: {e}")

def make_progress_bar(percent):
    filled = int(percent / 10)
    return f"{GR}{'█' * filled}{DIM}{'░' * (10 - filled)}{R} {WH}{percent}%{R}"

# ─────────────────────────────────────────────
# NITRO SNIPER
# ─────────────────────────────────────────────

GIFT_PATTERN = re.compile(r"(discord\.gift|discord\.com/gifts)/([a-zA-Z0-9]+)")

async def snipe_nitro(code, channel_id):
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://discord.com/api/v9/entitlements/gift-codes/{code}/redeem"
            headers = {"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT}
            async with session.post(url, headers=headers, json={"channel_id": str(channel_id)}) as r:
                data = {}
                try: data = await r.json()
                except Exception: pass
                if r.status == 200:
                    log_message("SNIPER", f"✅ SNIPED: {code}")
                else:
                    log_message("SNIPER", f"❌ failed: {code} | {r.status} | {data.get('message', '')}")
    except Exception as e:
        log_message("SNIPER", f"error: {e}")

# ─────────────────────────────────────────────
# HELP SYSTEM
# ─────────────────────────────────────────────

def build_help_root():
    p = PREFIX
    return (
        f"> **Lunar X selfbot**\n"
        f"```\n"
        f"────────────────────────────────────\n"
        f"  categories\n"
        f"────────────────────────────────────\n"
        f"  general      utilities, platform & status\n"
        f"  rpc          rich presence & brands\n"
        f"  quests       quest completer & orb badge\n"
        f"  sniper       nitro sniper & logger\n"
        f"  ar           auto-responder\n"
        f"  voice        voice channel controls\n"
        f"  fun          fun commands\n"
        f"  tools        tools & generators\n"
        f"  lastfm       last.fm integration\n"
        f"────────────────────────────────────\n"
        f"  {p}help <category> for commands\n"
        f"────────────────────────────────────\n"
        f"  Hade&Sy | ver 1.0.0\n"
        f"```"
    )

def build_help_general():
    p = PREFIX
    return (
        f"> **general**  utilities, platform & status\n"
        f"```\n"
        f"  [utilities]\n"
        f"  {p}ping                    latency check\n"
        f"  {p}info                    account snapshot\n"
        f"  {p}say <text>              replace command with text\n"
        f"  {p}spam <n> <text>         send n messages (max 20)\n"
        f"  {p}purge <n>               delete your last n messages\n"
        f"  {p}clear                   delete command message\n"
        f"  {p}copycat <user_id>       mirror next 10 messages\n"
        f"\n"
        f"  [status]\n"
        f"  {p}status <text>           set custom status\n"
        f"  {p}status clear            clear status\n"
        f"\n"
        f"  [platform spoofer]\n"
        f"  {p}platform <type>         spoof gateway platform\n"
        f"  {p}platform off            reset to desktop\n"
        f"  types: phone android desktop web xbox playstation console vr\n"
        f"\n"
        f"  [hypesquad]\n"
        f"  {p}hypesquad bravery       set house bravery\n"
        f"  {p}hypesquad brilliance    set house brilliance\n"
        f"  {p}hypesquad balance       set house balance\n"
        f"  {p}hypesquad off           remove hypesquad badge\n"
        f"```"
    )



def build_help_rpc():
    p = PREFIX
    return (
        f"> **rpc**  rich presence, platform & status\n"
        f"```\n"
        f"  [control]\n"
        f"  {p}rpc enable              turn on rich presence\n"
        f"  {p}rpc disable             clear rich presence\n"
        f"  {p}rpc status              show current config\n"
        f"\n"
        f"  [fields]\n"
        f"  {p}rpc type                playing/streaming/watching/listening/competing\n"
        f"  {p}rpc name                activity name\n"
        f"  {p}rpc details             details line\n"
        f"  {p}rpc state               state line\n"
        f"  {p}rpc url                 streaming url (twitch)\n"
        f"  {p}rpc start / end         timestamps (unix / MM:SS / none)\n"
        f"  {p}rpc large_image/text    large asset\n"
        f"  {p}rpc small_image/text    small asset\n"
        f"  {p}rpc button1/2_name/url  buttons\n"
        f"  {p}rpc party on/off/current/max\n"
        f"\n"
        f"  [brand presets]\n"
        f"  {p}rpc spotify <title> | <artist> | <secs>\n"
        f"  {p}rpc youtube <video> | <channel> | <secs>\n"
        f"  {p}rpc xbox <game>\n"
        f"  {p}rpc playstation <game>\n"
        f"  {p}rpc crunchyroll <anime> | <episode>\n"
        f"  {p}rpc roblox <game> | <details> | <state> | <img_text> | <small_text>\n"
        f"  {p}rpc custom <name> | <details> | <state>\n"
        f"  {p}rpc clear               alias for disable\n"
        f"```"
    )

def build_help_quests():
    p = PREFIX
    return (
        f"> **quests**  quest completer & orb badge\n"
        f"```\n"
        f"  {p}quest                   list active quests\n"
        f"  {p}questrun <index>        solve specific quest\n"
        f"  {p}questall                solve all active quests\n"
        f"  {p}autoquest on/off        auto-run on startup\n"
        f"  {p}orbbadge                claim orb badge\n"
        f"```"
    )

def build_help_sniper():
    p = PREFIX
    return (
        f"> **sniper**  nitro sniper & logger\n"
        f"```\n"
        f"  [sniper]\n"
        f"  {p}sniper on/off           toggle nitro sniper\n"
        f"\n"
        f"  [logger]\n"
        f"  {p}logger on/off           toggle message logger\n"
        f"  {p}readlog <n>             read last n log lines\n"
        f"```"
    )

def build_help_ar():
    p = PREFIX
    return (
        f"> **ar**  auto-responder\n"
        f"```\n"
        f"  {p}ar add <trigger> | <response>   add response\n"
        f"  {p}ar remove <trigger>             remove response\n"
        f"  {p}ar list                         list all\n"
        f"```"
    )


def build_help_voice():
    p = PREFIX
    return (
        f"> **voice**  voice channel controls\n"
        f"```\n"
        f"  {p}vcjoin <channel>         join a voice channel (stays 24/7)\n"
        f"  {p}vcleave                  leave voice channel\n"
        f"  {p}vcmute <user>            server mute a user\n"
        f"  {p}vcunmute <user>          server unmute a user\n"
        f"  {p}vcdeafen <user>          server deafen a user\n"
        f"  {p}vcundeafen <user>        server undeafen a user\n"
        f"  {p}vckick <user>            kick user from VC\n"
        f"  {p}vcmove <user> <ch_id>    move user to channel\n"
        f"  {p}vcmoveall <ch1> <ch2>    move all users ch1 -> ch2\n"
        f"```"
    )

def build_help_fun():
    p = PREFIX
    return (
        f"> **fun**  fun commands\n"
        f"```\n"
        f"  {p}gayrate <user>           gay percentage\n"
        f"  {p}feed <user>              feed a user\n"
        f"  {p}tickle <user>            tickle a user\n"
        f"  {p}slap <user>              slap a user\n"
        f"  {p}hug <user>               hug a user\n"
        f"  {p}cuddle <user>            cuddle a user\n"
        f"  {p}pat <user>               pat a user\n"
        f"  {p}kiss <user>              kiss a user\n"
        f"  {p}poke <user>              poke a user\n"
        f"  {p}wink <user>              wink at a user\n"
        f"  {p}smug <user>              smug at a user\n"
        f"  {p}boop <user>              boop a user\n"
        f"  {p}nom <user>               nom a user\n"
        f"  {p}mimic <user>             mimic a user in channel\n"
        f"  {p}unmimic <user>           stop mimicking user\n"
        f"  {p}stopmimic                stop all mimics\n"
        f"  {p}meme                     random meme\n"
        f"  {p}joke                     random joke\n"
        f"```"
    )

def build_help_tools():
    p = PREFIX
    return (
        f"> **tools**  tools & generators\n"
        f"```\n"
        f"  {p}nitro                    generate random nitro url\n"
        f"  {p}host add <token>         add account to host list\n"
        f"  {p}host remove <token>      remove account from host\n"
        f"  {p}host list                list hosted accounts\n"
        f"  {p}host broadcast <msg>     send msg from all hosted accounts\n"
        f"  {p}applybypass <invite>     bypass apply-to-join on server\n"
        f"```"
    )

def build_help_lastfm():
    p = PREFIX
    return (
        f"> **lastfm**  last.fm integration\n"
        f"```\n"
        f"  {p}lastfm set <username>    link your last.fm account\n"
        f"  {p}lastfm np               now playing — shows current track\n"
        f"  {p}lastfm recent [n]        last n scrobbles (default 5)\n"
        f"  {p}lastfm topartists [w/m/y/all]  top artists\n"
        f"  {p}lastfm toptracks [w/m/y/all]   top tracks\n"
        f"  {p}lastfm topalbums [w/m/y/all]    top albums\n"
        f"  {p}lastfm stats            scrobble count & playcount\n"
        f"  {p}lastfm compare <user>   taste compatibility with another last.fm user\n"
        f"  {p}lastfm rpc              set rpc to now playing track\n"
        f"  {p}lastfm autorpc on/off   auto-update rpc with now playing\n"
        f"```"
    )

HELP_MAP = {
    "":          build_help_root,
    "general":   build_help_general,
    "rpc":       build_help_rpc,
    "quests":    build_help_quests,
    "quest":     build_help_quests,
    "sniper":    build_help_sniper,
    "logger":    build_help_sniper,
    "ar":        build_help_ar,
    "voice":     build_help_voice,
    "vc":        build_help_voice,
    "fun":       build_help_fun,
    "tools":     build_help_tools,
    "lastfm":    build_help_lastfm,
    "lfm":       build_help_lastfm,
}

# ─────────────────────────────────────────────
# EVENTS
# ─────────────────────────────────────────────

@client.event
async def on_ready():
    print(f"[+] {client.user} ({client.user.id})")
    print(f"[+] prefix: {PREFIX} | servers: {len(client.guilds)}")
    await update_rpc()
    if AUTOQUEST_ENABLED:
        asyncio.create_task(run_autoquest_pass_now(TOKEN))
        print("[+] autoquest: running pass")

@client.event
async def on_message(message):
    global SNIPER_ENABLED, LOGGER_ENABLED

    if LOGGER_ENABLED and message.guild:
        try:
            log_message("MSG",
                f"{message.guild.name}/#{message.channel.name} | "
                f"{message.author} ({message.author.id}): {message.content}")
        except Exception: pass

    if SNIPER_ENABLED and message.author.id != client.user.id:
        for _, code in GIFT_PATTERN.findall(message.content):
            asyncio.create_task(snipe_nitro(code, message.channel.id))

    if message.author.id != client.user.id:
        cl = message.content.lower()
        for trigger, response in AUTO_RESPONSES.items():
            if trigger.lower() in cl:
                try: await message.channel.send(response)
                except Exception: pass
                break
        # mimic listener
        cid = message.channel.id
        if cid in _mimic_dict and message.author.id in _mimic_dict[cid]:
            if not message.content.startswith(PREFIX):
                try: await message.channel.send(message.content)
                except Exception: pass
        return

    if not message.content.startswith(PREFIX):
        return

    args = message.content[len(PREFIX):].split()
    cmd = args[0].lower() if args else ""

    # ── HELP ──
    if cmd == "help":
        sub = args[1].lower() if len(args) > 1 else ""
        builder = HELP_MAP.get(sub)
        try:
            await message.delete()
        except Exception:
            pass
        try:
            if builder:
                await message.channel.send(builder())
            else:
                await message.channel.send(
                    f"> **error**\n"
                    f"```\n"
                    f"  unknown category: {sub}\n"
                    f"  available: general, rpc, quests, sniper, ar\n"
                    f"```"
                )
        except Exception as e:
            print(f"[help] send error: {e}")
        return

    # ── GENERAL ──
    if cmd == "ping":
        await message.edit(content=ansi(f"{GR}◈ pong{R}  {WH}{round(client.latency * 1000)}ms{R}"))

    elif cmd == "info":
        u = client.user
        created = u.created_at.strftime("%Y-%m-%d")
        await message.edit(content=ansi(
            f"{help_header('account')}\n"
            f"{help_row('user', f'{u} ({u.id})')}\n"
            f"{help_row('created', created)}\n"
            f"{help_row('servers', str(len(client.guilds)))}\n"
            f"{help_row('prefix', PREFIX)}\n"
            f"{help_row('platform', _current_platform)}"
        ))

    elif cmd == "say":
        await message.edit(content=" ".join(args[1:]))

    elif cmd == "spam":
        if len(args) < 3:
            return await message.edit(content=ansi(f"{RD}✗  usage: {PREFIX}spam <n> <text>{R}"))
        count = min(int(args[1]), 20)
        text = " ".join(args[2:])
        await message.delete()

        async def _send():
            while True:
                try:
                    await message.channel.send(text)
                    return
                except discord.HTTPException as e:
                    if getattr(e, 'status', None) == 429:
                        # Wait exactly as long as Discord asks, then retry this message
                        await asyncio.sleep(getattr(e, 'retry_after', 2.0))
                        continue
                    # Any other HTTP error: bail out so we don't infinite-loop
                    return
                except Exception:
                    return

        for _ in range(count):
            await _send()
            await asyncio.sleep(0.2)

    elif cmd == "purge":
        limit = int(args[1]) if len(args) > 1 else 5
        deleted = 0
        await message.delete()
        async for msg in message.channel.history(limit=300):
            if msg.author.id == client.user.id:
                try: await msg.delete()
                except Exception: pass
                deleted += 1
                await asyncio.sleep(0.4)
                if deleted >= limit: break

    elif cmd == "clear":
        await message.delete()

    elif cmd == "copycat":
        if len(args) < 2:
            return await message.edit(content=ansi(f"{RD}✗  usage: {PREFIX}copycat <user_id>{R}"))
        try: target_id = int(args[1])
        except ValueError:
            return await message.edit(content=ansi(f"{RD}✗  invalid user id{R}"))
        await message.delete()
        def check(m): return m.author.id == target_id and m.channel.id == message.channel.id
        for _ in range(10):
            try:
                msg = await client.wait_for("message", check=check, timeout=60)
                await message.channel.send(msg.content)
            except asyncio.TimeoutError: break

    elif cmd == "status":
        if len(args) < 2 or args[1].lower() == "clear":
            await client.change_presence(activity=None)
            await message.edit(content=ansi(f"{GR}✓ status cleared{R}"))
        else:
            text = " ".join(args[1:])
            await client.change_presence(activity=discord.CustomActivity(name=text))
            await message.edit(content=ansi(f"{GR}✓ status{R}  {WH}{text}{R}"))

    # ── PLATFORM ──
    elif cmd == "platform":
        if len(args) < 2:
            return await message.edit(content=ansi(
                f"{help_header('platform')}\n"
                f"{DIM}current: {CY}{_current_platform}{R}\n"
                f"{DIM}types: phone │ android │ desktop │ web │ xbox │ playstation │ console │ vr │ off{R}"
            ))
        plat = args[1].lower()
        if plat == "off": plat = "desktop"
        if plat not in PLATFORM_MAP:
            return await message.edit(content=ansi(f"{RD}✗  unknown platform: {plat}{R}"))
        await message.edit(content=ansi(f"{YE}⟳  switching to {plat}...{R}"))
        ok = await set_platform(plat)
        if ok:
            await message.edit(content=ansi(f"{GR}✓ platform{R}  {WH}{plat}{R}\n{DIM}gateway will reconnect{R}"))
        else:
            await message.edit(content=ansi(f"{RD}✗  platform switch failed{R}"))

    # ── HYPESQUAD ──
    elif cmd == "hypesquad":
        if len(args) < 2:
            return await message.edit(content=ansi(
                f"{help_header('hypesquad')}\n"
                f"{help_row(f'{PREFIX}hypesquad bravery', 'set house bravery')}\n"
                f"{help_row(f'{PREFIX}hypesquad brilliance', 'set house brilliance')}\n"
                f"{help_row(f'{PREFIX}hypesquad balance', 'set house balance')}\n"
                f"{help_row(f'{PREFIX}hypesquad off', 'remove hypesquad badge')}"
            ))
        sub = args[1].lower()
        if sub == "off":
            await message.edit(content=ansi(f"{YE}⟳  removing hypesquad badge...{R}"))
            async with aiohttp.ClientSession() as session:
                ok, msg = await remove_hypesquad(session, TOKEN)
            if ok:
                await message.edit(content=ansi(f"{GR}✓ hypesquad badge removed{R}"))
            else:
                await message.edit(content=ansi(f"{RD}✗  failed: {msg}{R}"))
        elif sub in HOUSE_IDS:
            house_id = HOUSE_IDS[sub]
            house_name = HOUSE_NAMES[house_id]
            await message.edit(content=ansi(f"{YE}⟳  setting house {house_name}...{R}"))
            async with aiohttp.ClientSession() as session:
                ok, msg = await change_hypesquad(session, TOKEN, house_id)
            if ok:
                await message.edit(content=ansi(f"{GR}✓ hypesquad{R}  {WH}House {house_name}{R}"))
            else:
                await message.edit(content=ansi(f"{RD}✗  failed: {msg}{R}"))
        else:
            await message.edit(content=ansi(
                f"{RD}✗  unknown house: {sub}{R}\n"
                f"{DIM}use: bravery / brilliance / balance / off{R}"
            ))

    # ── SNIPER / LOGGER ──
    elif cmd == "sniper":
        SNIPER_ENABLED = len(args) < 2 or args[1].lower() == "on"
        state = f"{GR}ON{R}" if SNIPER_ENABLED else f"{RD}OFF{R}"
        await message.edit(content=ansi(f"{GR}✓ sniper{R}  {state}"))

    elif cmd == "logger":
        LOGGER_ENABLED = len(args) < 2 or args[1].lower() == "on"
        state = f"{GR}ON{R}" if LOGGER_ENABLED else f"{RD}OFF{R}"
        await message.edit(content=ansi(f"{GR}✓ logger{R}  {state}"))

    elif cmd == "readlog":
        n = int(args[1]) if len(args) > 1 else 10
        if not os.path.exists(LOG_FILE):
            return await message.edit(content=ansi(f"{RD}✗  no log file yet{R}"))
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        tail = "".join(lines[-n:])
        if len(tail) > 1900: tail = tail[-1900:]
        await message.edit(content=f"```\n{tail}\n```")

    # ── AUTO-RESPONDER ──
    elif cmd == "ar":
        if len(args) < 2:
            return await message.edit(content=ansi(
                f"{RD}✗  usage:{R}\n"
                f"{help_row(f'{PREFIX}ar add trigger | response', 'add')}\n"
                f"{help_row(f'{PREFIX}ar remove trigger', 'remove')}\n"
                f"{help_row(f'{PREFIX}ar list', 'list all')}"
            ))
        sub = args[1].lower()
        rest = " ".join(args[2:])
        if sub == "add":
            if "|" not in rest:
                return await message.edit(content=ansi(f"{RD}✗  format: {PREFIX}ar add trigger | response{R}"))
            trigger, response = rest.split("|", 1)
            AUTO_RESPONSES[trigger.strip()] = response.strip()
            await message.edit(content=ansi(f"{GR}✓ added{R}  {WH}{trigger.strip()}{R} {DIM}→{R} {WH}{response.strip()}{R}"))
        elif sub == "remove":
            key = rest.strip()
            AUTO_RESPONSES.pop(key, None)
            await message.edit(content=ansi(f"{GR}✓ removed{R}  {WH}{key}{R}"))
        elif sub == "list":
            if not AUTO_RESPONSES:
                return await message.edit(content=ansi(f"{DIM}no auto-responses set{R}"))
            lines = [help_header("auto-responder")]
            for k, v in AUTO_RESPONSES.items():
                lines.append(help_row(k, v))
            await message.edit(content=ansi("\n".join(lines)))
        else:
            await message.edit(content=ansi(f"{RD}✗  unknown subcommand: {sub}{R}"))
    

    # ── QUESTS ──
    elif cmd == "quest":
        try: await message.delete()
        except Exception: pass
        service = QuestService(TOKEN)
        async with aiohttp.ClientSession() as session:
            quests = await service.fetch_quests(session)
        if not quests:
            return await message.channel.send("```\nno quests found\n```", delete_after=10)
        lines = ["```", "> quests", "─" * 36]
        for i, q in enumerate(quests):
            tag = "✓ done" if q.is_completed() else ("supported" if q.is_supported() else "unsupported")
            pct = q.progress_percent()
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            lines.append(f"[{i}] {q.name}  {tag}")
            lines.append(f"    {bar} {pct}%")
            lines.append(f"    reward: {q.reward_name}")
        lines.append("```")
        await message.channel.send("\n".join(lines))

    elif cmd == "questrun":
        try: await message.delete()
        except Exception: pass
        idx = int(args[1]) if len(args) > 1 else 0
        service = QuestService(TOKEN, speed_mode="fast")
        async with aiohttp.ClientSession() as session:
            quests = await service.fetch_quests(session)
            if not quests or idx >= len(quests):
                return await message.channel.send("```\nquest index out of range\n```", delete_after=8)
            quest = quests[idx]
            if quest.is_completed():
                return await message.channel.send(f"```\n✓ {quest.name} already completed\n```", delete_after=8)
            await message.channel.send(f"```\nquest completer started\n{quest.name}\n```", delete_after=5)
            result = await service.run_quest(session, quest)
            if result and result.get("status") == "completed":
                await message.channel.send(f"```\n✓ {quest.name} complete — {quest.reward_name}\n```", delete_after=10)

    elif cmd == "questall":
        try: await message.delete()
        except Exception: pass
        service = QuestService(TOKEN, speed_mode="fast")
        async with aiohttp.ClientSession() as session:
            quests = await service.fetch_quests(session)
            active = [q for q in quests if not q.is_completed() and q.is_supported()]
            if not active:
                return await message.channel.send("```\nno active supported quests\n```", delete_after=8)
            for q in active:
                if not q.is_enrolled():
                    try: await service.enroll(session, q)
                    except Exception: pass
            await message.channel.send(f"```\nquest completer started\n{len(active)} quest(s) queued\n```", delete_after=5)
            async def run_one_silent(q):
                try:
                    res = await service.run_quest(session, q)
                    if res and res.get("status") == "completed":
                        await message.channel.send(f"```\n✓ {q.name} — {q.reward_name}\n```", delete_after=10)
                except Exception as e:
                    print(f"[Quest] {q.name} error: {e}")
            await asyncio.gather(*(run_one_silent(q) for q in active))

    elif cmd == "autoquest":
        try: await message.delete()
        except Exception: pass
        cfg = load_config()
        if len(args) < 2:
            cfg["autoquest_enabled"] = not cfg.get("autoquest_enabled", False)
        else:
            cfg["autoquest_enabled"] = args[1].lower() in ("on", "enable", "true")
        save_config(cfg)
        enabled = cfg["autoquest_enabled"]
        if enabled: asyncio.create_task(run_autoquest_pass_now(TOKEN))
        state = "enabled" if enabled else "disabled"
        await message.channel.send(f"```\nquest completer started\nautoquest {state}\n```", delete_after=5)

    elif cmd == "orbbadge":
        try: await message.delete()
        except Exception: pass
        await message.channel.send("```\nclaiming orb badge...\n```", delete_after=3)
        async with aiohttp.ClientSession() as session:
            result = await _claim_orb_badge(session, TOKEN)
        if result["status"] == "SUCCESS":
            extra = f"  balance before: {result['balance']} orbs" if isinstance(result.get("balance"), int) else ""
            await message.channel.send(f"```\n✓ orb badge claimed!{extra}\n```", delete_after=10)
        else:
            await message.channel.send(f"```\n✗ failed: {result.get('message')} (code: {result.get('code')})\n```", delete_after=10)

    # ── RPC ──
    elif cmd == "rpc":
        sub = args[1].lower() if len(args) > 1 else ""

        # brand presets
        if sub in BRAND_PRESETS:
            brand_args = args[2:]
            ok = await apply_brand_rpc(sub, brand_args)
            preset = BRAND_PRESETS[sub]
            if ok:
                await message.edit(content=ansi(
                    f"{GR}✓ rpc{R}  {WH}{preset.get('type', 'playing')} {preset.get('name', sub)}{R}"
                ))
            else:
                await message.edit(content=ansi(f"{RD}✗  rpc preset failed{R}"))
            return

        if not sub or sub == "help":
            await message.edit(content=build_help_rpc())

        elif sub in ("disable", "stop", "clear", "off"):
            cfg = load_rpc_config()
            cfg["enabled"] = False
            save_rpc_config(cfg)
            await client.change_presence(activity=None)
            await message.edit(content=ansi(f"{RD}✗ rpc disabled{R}"))

        elif sub == "enable":
            cfg = load_rpc_config()
            cfg["enabled"] = True
            save_rpc_config(cfg)
            await update_rpc()
            await message.edit(content=ansi(f"{GR}✓ rpc enabled{R}"))

        elif sub == "status":
            cfg = load_rpc_config()
            state = f"{GR}enabled{R}" if cfg.get("enabled") else f"{RD}disabled{R}"
            await message.edit(content=ansi(
                f"{help_header('rpc status')}\n"
                f"{help_row('state', '')}{state}\n"
                f"{help_row('type', cfg.get('type', 'playing'))}\n"
                f"{help_row('name', cfg.get('name') or 'none')}\n"
                f"{help_row('details', cfg.get('details') or 'none')}\n"
                f"{help_row('state_field', cfg.get('state') or 'none')}\n"
                f"{help_row('large_image', cfg.get('large_image') or 'none')}\n"
                f"{help_row('party', 'on' if cfg.get('party', {}).get('enabled') else 'off')}"
            ))

        elif sub == "type":
            await message.delete()
            valid = ["playing", "streaming", "listening", "watching", "competing"]
            val = await rpc_prompt(message.channel, message.author, f"type  ({'/'.join(valid)})")
            if val == "__TIMEOUT__": return
            if val not in valid:
                return await message.channel.send(ansi(f"{RD}✗  invalid: {val}{R}"), delete_after=5)
            cfg = load_rpc_config(); cfg["type"] = val; save_rpc_config(cfg)
            await update_rpc()
            await message.channel.send(ansi(f"{GR}✓ type{R}  {WH}{val}{R}"), delete_after=5)

        elif sub in ("name", "details", "state", "large_image", "large_text",
                     "small_image", "small_text", "url"):
            await message.delete()
            val = await rpc_prompt(message.channel, message.author, sub)
            if val == "__TIMEOUT__": return
            cfg = load_rpc_config(); cfg[sub] = val; save_rpc_config(cfg)
            await update_rpc()
            await message.channel.send(ansi(f"{GR}✓ {sub}{R}  {WH}{val or 'cleared'}{R}"), delete_after=5)

        elif sub in ("start", "end"):
            await message.delete()
            key = "start_timestamp" if sub == "start" else "end_timestamp"
            val = await rpc_prompt(message.channel, message.author, f"{sub} timestamp  (unix / MM:SS / none)")
            if val == "__TIMEOUT__": return
            cfg = load_rpc_config()
            cfg[key] = None if (val is None or (val and val.lower() == "none")) else val
            save_rpc_config(cfg); await update_rpc()
            await message.channel.send(ansi(f"{GR}✓ {sub} timestamp set{R}"), delete_after=5)

        elif sub in ("button1_name","button1_url","button2_name","button2_url"):
            await message.delete()
            idx = 0 if sub.startswith("button1") else 1
            field = "label" if sub.endswith("name") else "url"
            val = await rpc_prompt(message.channel, message.author, f"button {idx+1} {field}")
            if val == "__TIMEOUT__": return
            cfg = load_rpc_config(); cfg["buttons"][idx][field] = val or ""; save_rpc_config(cfg)
            await update_rpc()
            await message.channel.send(ansi(f"{GR}✓ button {idx+1} {field} set{R}"), delete_after=5)

        elif sub == "party":
            option = args[2].lower() if len(args) > 2 else ""
            cfg = load_rpc_config()
            if option == "enable":
                cfg["party"]["enabled"] = True; save_rpc_config(cfg); await update_rpc()
                await message.edit(content=ansi(f"{GR}✓ party enabled{R}"))
            elif option == "disable":
                cfg["party"]["enabled"] = False; save_rpc_config(cfg); await update_rpc()
                await message.edit(content=ansi(f"{RD}✗ party disabled{R}"))
            elif option in ("current", "max"):
                await message.delete()
                val = await rpc_prompt(message.channel, message.author, f"party {option}  (integer)")
                if val == "__TIMEOUT__": return
                try:
                    cfg["party"][option] = int(val); save_rpc_config(cfg); await update_rpc()
                    await message.channel.send(ansi(f"{GR}✓ party {option}{R}  {WH}{int(val)}{R}"), delete_after=5)
                except (ValueError, TypeError):
                    await message.channel.send(ansi(f"{RD}✗  must be integer{R}"), delete_after=5)
            else:
                await message.edit(content=ansi(f"{RD}✗  usage: {PREFIX}rpc party enable/disable/current/max{R}"))
        else:
            await message.edit(content=ansi(f"{RD}✗  unknown subcommand: {sub}  —  use {PREFIX}rpc help{R}"))


    # ── VOICE COMMANDS ──
    elif cmd == "vcjoin":
        try: await message.delete()
        except Exception: pass
        if len(args) < 2:
            # try to join author's current vc
            if message.guild:
                member = message.guild.get_member(client.user.id)
                if member and member.voice and member.voice.channel:
                    channel = member.voice.channel
                else:
                    return await message.channel.send("```\njoin a vc first or provide a channel id\n```", delete_after=5)
            else:
                return await message.channel.send("```\nprovide a channel id\n```", delete_after=5)
        else:
            try:
                ch_id = int(args[1])
                channel = client.get_channel(ch_id)
                if not channel:
                    return await message.channel.send("```\nchannel not found\n```", delete_after=5)
            except ValueError:
                return await message.channel.send("```\ninvalid channel id\n```", delete_after=5)
        try:
            if message.guild.voice_client:
                await message.guild.voice_client.disconnect(force=True)
            await channel.connect(self_deaf=True)
            await message.channel.send(f"```\n✓ joined {channel.name}\n```", delete_after=5)
        except Exception as e:
            await message.channel.send(f"```\n✗ {e}\n```", delete_after=5)

    elif cmd == "vcleave":
        try: await message.delete()
        except Exception: pass
        if message.guild and message.guild.voice_client:
            name = message.guild.voice_client.channel.name
            await message.guild.voice_client.disconnect(force=True)
            await message.channel.send(f"```\n✓ left {name}\n```", delete_after=5)
        else:
            await message.channel.send("```\nnot in a vc\n```", delete_after=5)

    elif cmd == "vcmute":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 2:
            return await message.channel.send("```\nusage: vcmute <user_id>\n```", delete_after=5)
        try:
            member = message.guild.get_member(int(args[1]))
            if member and member.voice:
                await member.edit(mute=True)
                await message.channel.send(f"```\n✓ muted {member.name}\n```", delete_after=5)
        except Exception as e:
            await message.channel.send(f"```\n✗ {e}\n```", delete_after=5)

    elif cmd == "vcunmute":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 2:
            return await message.channel.send("```\nusage: vcunmute <user_id>\n```", delete_after=5)
        try:
            member = message.guild.get_member(int(args[1]))
            if member and member.voice:
                await member.edit(mute=False)
                await message.channel.send(f"```\n✓ unmuted {member.name}\n```", delete_after=5)
        except Exception as e:
            await message.channel.send(f"```\n✗ {e}\n```", delete_after=5)

    elif cmd == "vcdeafen":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 2:
            return await message.channel.send("```\nusage: vcdeafen <user_id>\n```", delete_after=5)
        try:
            member = message.guild.get_member(int(args[1]))
            if member and member.voice:
                await member.edit(deafen=True)
                await message.channel.send(f"```\n✓ deafened {member.name}\n```", delete_after=5)
        except Exception as e:
            await message.channel.send(f"```\n✗ {e}\n```", delete_after=5)

    elif cmd == "vcundeafen":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 2:
            return await message.channel.send("```\nusage: vcundeafen <user_id>\n```", delete_after=5)
        try:
            member = message.guild.get_member(int(args[1]))
            if member and member.voice:
                await member.edit(deafen=False)
                await message.channel.send(f"```\n✓ undeafened {member.name}\n```", delete_after=5)
        except Exception as e:
            await message.channel.send(f"```\n✗ {e}\n```", delete_after=5)

    elif cmd == "vckick":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 2:
            return await message.channel.send("```\nusage: vckick <user_id>\n```", delete_after=5)
        try:
            member = message.guild.get_member(int(args[1]))
            if member and member.voice:
                await member.move_to(None)
                await message.channel.send(f"```\n✓ kicked {member.name} from vc\n```", delete_after=5)
        except Exception as e:
            await message.channel.send(f"```\n✗ {e}\n```", delete_after=5)

    elif cmd == "vcmove":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 3:
            return await message.channel.send("```\nusage: vcmove <user_id> <channel_id>\n```", delete_after=5)
        try:
            member = message.guild.get_member(int(args[1]))
            channel = client.get_channel(int(args[2]))
            if member and channel:
                await member.move_to(channel)
                await message.channel.send(f"```\n✓ moved {member.name} to {channel.name}\n```", delete_after=5)
        except Exception as e:
            await message.channel.send(f"```\n✗ {e}\n```", delete_after=5)

    elif cmd == "vcmoveall":
        try: await message.delete()
        except Exception: pass
        if not message.guild or len(args) < 3:
            return await message.channel.send("```\nusage: vcmoveall <ch1_id> <ch2_id>\n```", delete_after=5)
        try:
            ch1 = client.get_channel(int(args[1]))
            ch2 = client.get_channel(int(args[2]))
            if ch1 and ch2:
                count = 0
                for member in list(ch1.members):
                    await member.move_to(ch2)
                    count += 1
                    await asyncio.sleep(0.3)
                await message.channel.send(f"```\n✓ moved {count} users from {ch1.name} to {ch2.name}\n```", delete_after=8)
        except Exception as e:
            await message.channel.send(f"```\n✗ {e}\n```", delete_after=5)

    # ── FUN COMMANDS ──
    elif cmd == "gayrate":
        try: await message.delete()
        except Exception: pass
        import random as _rnd
        target_id = int(args[1]) if len(args) > 1 else message.author.id
        if message.guild:
            member = message.guild.get_member(target_id)
            name = member.display_name if member else f"<@{target_id}>"
        else:
            name = f"<@{target_id}>"
        pct = 0 if target_id == message.author.id else _rnd.randint(0, 100)
        await message.channel.send(f"🏳️‍🌈 {name} is **{pct}%** gay")

    elif cmd in ("feed","tickle","slap","hug","cuddle","pat","kiss","poke","wink","smug","boop","nom"):
        try: await message.delete()
        except Exception: pass
        await send_neko(message.channel, cmd)

    elif cmd == "meme":
        try: await message.delete()
        except Exception: pass
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://meme-api.com/gimme") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        await message.channel.send(data.get("url", "no meme found"))
                    else:
                        await message.channel.send("```\n✗ meme api down\n```", delete_after=5)
        except Exception as e:
            await message.channel.send(f"```\n✗ {e}\n```", delete_after=5)

    elif cmd == "joke":
        try: await message.delete()
        except Exception: pass
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://official-joke-api.appspot.com/random_joke") as resp:
                    if resp.status == 200:
                        j = await resp.json()
                        setup = j['setup']
                        punchline = j['punchline']
                        await message.channel.send(f"**{setup}**\n||{punchline}||")
                    else:
                        await message.channel.send("```\n✗ joke api down\n```", delete_after=5)
        except Exception as e:
            await message.channel.send(f"```\n✗ {e}\n```", delete_after=5)

    elif cmd == "mimic":
        try: await message.delete()
        except Exception: pass
        if len(args) < 2:
            return await message.channel.send("```\nusage: mimic <user_id>\n```", delete_after=5)
        uid = int(args[1])
        cid = message.channel.id
        if cid not in _mimic_dict:
            _mimic_dict[cid] = []
        if uid not in _mimic_dict[cid]:
            _mimic_dict[cid].append(uid)
            await message.channel.send(f"```\n✓ mimicking <@{uid}> in this channel\n```", delete_after=5)
        else:
            await message.channel.send(f"```\nalready mimicking that user here\n```", delete_after=5)

    elif cmd == "unmimic":
        try: await message.delete()
        except Exception: pass
        if len(args) < 2:
            return await message.channel.send("```\nusage: unmimic <user_id>\n```", delete_after=5)
        uid = int(args[1])
        cid = message.channel.id
        if cid in _mimic_dict and uid in _mimic_dict[cid]:
            _mimic_dict[cid].remove(uid)
            if not _mimic_dict[cid]:
                del _mimic_dict[cid]
            await message.channel.send(f"```\n✓ stopped mimicking <@{uid}>\n```", delete_after=5)
        else:
            await message.channel.send("```\nnot mimicking that user here\n```", delete_after=5)

    elif cmd == "stopmimic":
        try: await message.delete()
        except Exception: pass
        _mimic_dict.clear()
        await message.channel.send("```\n✓ all mimics stopped\n```", delete_after=5)

    # ── TOOLS ──
    elif cmd == "nitro":
        try: await message.delete()
        except Exception: pass
        import random as _rnd
        import string as _str
        code = "".join(_rnd.choices(_str.ascii_letters + _str.digits, k=16))
        url = f"https://discord.gift/{code}"
        await message.channel.send(f"```\n{url}\n```")

    elif cmd == "host":
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add":
            if len(args) < 3:
                return await message.edit(content="```\nusage: host add <token>\n```")
            token = args[2].strip()
            if token in HOSTED_TOKENS:
                return await message.edit(content="```\nalready in host list\n```")
            HOSTED_TOKENS.append(token)
            save_hosted()
            username = await get_token_username(token)
            await message.edit(content=f"```\n✓ added {username} to host list\n```")

        elif sub == "remove":
            if len(args) < 3:
                return await message.edit(content="```\nusage: host remove <token>\n```")
            token = args[2].strip()
            if token in HOSTED_TOKENS:
                HOSTED_TOKENS.remove(token)
                save_hosted()
                await message.edit(content="```\n✓ removed from host list\n```")
            else:
                await message.edit(content="```\ntoken not in host list\n```")

        elif sub == "list":
            if not HOSTED_TOKENS:
                return await message.edit(content="```\nno hosted accounts\n```")
            lines = ["```", f"hosted accounts: {len(HOSTED_TOKENS)}"]
            for i, t in enumerate(HOSTED_TOKENS):
                username = await get_token_username(t)
                lines.append(f"  [{i}] {username}  {t[:10]}...")
            lines.append("```")
            await message.edit(content="\n".join(lines))

        elif sub == "broadcast":
            if len(args) < 3:
                return await message.edit(content="```\nusage: host broadcast <message>\n```")
            msg_text = " ".join(args[2:])
            if not HOSTED_TOKENS:
                return await message.edit(content="```\nno hosted accounts\n```")
            await message.edit(content=f"```\nbroadcasting to {len(HOSTED_TOKENS)} accounts...\n```")
            success = 0
            for t in HOSTED_TOKENS:
                ok = await hosted_send(t, message.channel.id, msg_text)
                if ok: success += 1
                await asyncio.sleep(0.5)
            await message.edit(content=f"```\n✓ broadcast sent from {success}/{len(HOSTED_TOKENS)} accounts\n```")

        else:
            await message.edit(content=build_help_tools())

    elif cmd == "applybypass":
        try: await message.delete()
        except Exception: pass
        if len(args) < 2:
            return await message.channel.send("```\nusage: applybypass <invite_code>\n```", delete_after=5)
        invite = args[1].strip().replace("https://discord.gg/", "").replace("discord.gg/", "")
        await message.channel.send(f"```\nattempting apply-to-join bypass for {invite}...\n```", delete_after=3)
        try:
            async with aiohttp.ClientSession() as session:
                # Step 1: get invite info
                headers = {"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": USER_AGENT}
                async with session.get(f"https://discord.com/api/v9/invites/{invite}", headers=headers) as resp:
                    if resp.status != 200:
                        return await message.channel.send(f"```\n✗ invalid invite\n```", delete_after=8)
                    inv_data = await resp.json()

                guild_id = inv_data.get("guild", {}).get("id")
                if not guild_id:
                    return await message.channel.send("```\n✗ could not get guild id\n```", delete_after=8)

                # Step 2: accept invite (bypasses apply-to-join via direct accept)
                async with session.post(
                    f"https://discord.com/api/v9/invites/{invite}",
                    headers=headers,
                    json={"session_id": str(uuid4())[:8]}
                ) as resp2:
                    if resp2.status in (200, 204):
                        guild_name = inv_data.get("guild", {}).get("name", "server")
                        await message.channel.send(f"```\n✓ joined {guild_name}\n```", delete_after=8)
                    else:
                        data2 = await resp2.json()
                        # Try application bypass for apply-to-join
                        if resp2.status == 403:
                            # Server has apply-to-join — attempt via member verification bypass
                            async with session.put(
                                f"https://discord.com/api/v9/guilds/{guild_id}/requests/@me",
                                headers=headers,
                                json={"form_fields": []}
                            ) as resp3:
                                if resp3.status in (200, 201, 204):
                                    await message.channel.send(f"```\n✓ application submitted — check server\n```", delete_after=8)
                                else:
                                    await message.channel.send(f"```\n✗ bypass failed: {resp2.status}\n```", delete_after=8)
                        else:
                            await message.channel.send(f"```\n✗ {data2.get('message', resp2.status)}\n```", delete_after=8)
        except Exception as e:
            await message.channel.send(f"```\n✗ {e}\n```", delete_after=8)

    # ── LAST.FM ──
    elif cmd == "lastfm":
        sub = args[1].lower() if len(args) > 1 else ""

        if not sub or sub == "help":
            try: await message.delete()
            except Exception: pass
            await message.channel.send(build_help_lastfm())

        elif sub == "set":
            # .lastfm set <username> [api_key]
            if len(args) < 3:
                return await message.edit(content="```\nusage: lastfm set <username> [api_key]\n```")
            _lastfm_cfg["username"] = args[2].strip()
            if len(args) > 3:
                _lastfm_cfg["api_key"] = args[3].strip()
                global LASTFM_API_KEY
                LASTFM_API_KEY = args[3].strip()
            save_lastfm_cfg()
            await message.edit(content=f"```\n✓ last.fm linked: {_lastfm_cfg['username']}\n```")

        elif sub in ("np", "nowplaying"):
            try: await message.delete()
            except Exception: pass
            username = _lfm_user()
            if not username:
                return await message.channel.send("```\n✗ set your last.fm first: .lastfm set <username>\n```", delete_after=8)
            if LASTFM_API_KEY == "your_lastfm_api_key_here":
                return await message.channel.send("```\n✗ set your api key: .lastfm set <username> <api_key>\nget one at last.fm/api\n```", delete_after=10)
            track = await lfm_now_playing(username)
            if not track:
                return await message.channel.send("```\nno recent tracks found\n```", delete_after=8)
            loved = "♥ " if track["loved"] else ""
            status = "▶ now playing" if track["playing"] else "⏸ last played"
            album = f"\n  album    {track['album']}" if track["album"] else ""
            out = (
                f"```\n"
                f"  {status}\n"
                f"  ──────────────────────────────\n"
                f"  {loved}{track['title']}\n"
                f"  by {track['artist']}{album}\n"
                f"  ──────────────────────────────\n"
                f"  scrobbles  {track['scrobbles']}\n"
                f"  {track['url']}\n"
                f"```"
            )
            await message.channel.send(out)

        elif sub == "recent":
            try: await message.delete()
            except Exception: pass
            username = _lfm_user()
            if not username:
                return await message.channel.send("```\n✗ set your last.fm first\n```", delete_after=8)
            n = int(args[2]) if len(args) > 2 and args[2].isdigit() else 5
            n = min(n, 15)
            data = await lfm_get("user.getRecentTracks", {"user": username, "limit": n})
            tracks = data.get("recenttracks", {}).get("track", [])
            if not tracks:
                return await message.channel.send("```\nno recent tracks\n```", delete_after=8)
            lines = ["```", f"  recent tracks — {username}", "  " + "─"*30]
            for i, t in enumerate(tracks[:n], 1):
                title  = t.get("name", "?")
                artist = t.get("artist", {}).get("#text", "?") if isinstance(t.get("artist"), dict) else t.get("artist", "?")
                now    = " ▶" if t.get("@attr", {}).get("nowplaying") else ""
                lines.append(f"  {i:2}. {title} — {artist}{now}")
            lines.append("```")
            await message.channel.send("\n".join(lines))

        elif sub in ("topartists", "artists"):
            try: await message.delete()
            except Exception: pass
            username = _lfm_user()
            if not username:
                return await message.channel.send("```\n✗ set your last.fm first\n```", delete_after=8)
            period_raw = args[2].lower() if len(args) > 2 else "overall"
            period = PERIOD_MAP.get(period_raw, "overall")
            label  = PERIOD_LABEL.get(period, "all time")
            data = await lfm_get("user.getTopArtists", {"user": username, "period": period, "limit": 10})
            artists = data.get("topartists", {}).get("artist", [])
            if not artists:
                return await message.channel.send("```\nno data\n```", delete_after=8)
            max_plays = int(artists[0].get("playcount", 1))
            lines = ["```", f"  top artists — {username} — {label}", "  " + "─"*34]
            for i, a in enumerate(artists[:10], 1):
                name   = a.get("name", "?")
                plays  = int(a.get("playcount", 0))
                pct    = int(plays / max_plays * 100) if max_plays else 0
                bar    = _progress_bar(pct, 8)
                lines.append(f"  {i:2}. {bar} {plays:>5}  {name}")
            lines.append("```")
            await message.channel.send("\n".join(lines))

        elif sub in ("toptracks", "tracks"):
            try: await message.delete()
            except Exception: pass
            username = _lfm_user()
            if not username:
                return await message.channel.send("```\n✗ set your last.fm first\n```", delete_after=8)
            period_raw = args[2].lower() if len(args) > 2 else "overall"
            period = PERIOD_MAP.get(period_raw, "overall")
            label  = PERIOD_LABEL.get(period, "all time")
            data = await lfm_get("user.getTopTracks", {"user": username, "period": period, "limit": 10})
            tracks = data.get("toptracks", {}).get("track", [])
            if not tracks:
                return await message.channel.send("```\nno data\n```", delete_after=8)
            max_plays = int(tracks[0].get("playcount", 1))
            lines = ["```", f"  top tracks — {username} — {label}", "  " + "─"*34]
            for i, t in enumerate(tracks[:10], 1):
                name   = t.get("name", "?")
                artist = t.get("artist", {}).get("name", "?") if isinstance(t.get("artist"), dict) else "?"
                plays  = int(t.get("playcount", 0))
                pct    = int(plays / max_plays * 100) if max_plays else 0
                bar    = _progress_bar(pct, 8)
                lines.append(f"  {i:2}. {bar} {plays:>5}  {name} — {artist}")
            lines.append("```")
            await message.channel.send("\n".join(lines))

        elif sub in ("topalbums", "albums"):
            try: await message.delete()
            except Exception: pass
            username = _lfm_user()
            if not username:
                return await message.channel.send("```\n✗ set your last.fm first\n```", delete_after=8)
            period_raw = args[2].lower() if len(args) > 2 else "overall"
            period = PERIOD_MAP.get(period_raw, "overall")
            label  = PERIOD_LABEL.get(period, "all time")
            data = await lfm_get("user.getTopAlbums", {"user": username, "period": period, "limit": 10})
            albums = data.get("topalbums", {}).get("album", [])
            if not albums:
                return await message.channel.send("```\nno data\n```", delete_after=8)
            max_plays = int(albums[0].get("playcount", 1))
            lines = ["```", f"  top albums — {username} — {label}", "  " + "─"*34]
            for i, a in enumerate(albums[:10], 1):
                name   = a.get("name", "?")
                artist = a.get("artist", {}).get("name", "?") if isinstance(a.get("artist"), dict) else "?"
                plays  = int(a.get("playcount", 0))
                pct    = int(plays / max_plays * 100) if max_plays else 0
                bar    = _progress_bar(pct, 8)
                lines.append(f"  {i:2}. {bar} {plays:>5}  {name} — {artist}")
            lines.append("```")
            await message.channel.send("\n".join(lines))

        elif sub == "stats":
            try: await message.delete()
            except Exception: pass
            username = _lfm_user()
            if not username:
                return await message.channel.send("```\n✗ set your last.fm first\n```", delete_after=8)
            data = await lfm_get("user.getInfo", {"user": username})
            user_data = data.get("user", {})
            if not user_data:
                return await message.channel.send("```\n✗ user not found\n```", delete_after=8)
            scrobbles   = user_data.get("playcount", "?")
            artists     = user_data.get("artist_count", "?")
            tracks      = user_data.get("track_count", "?")
            albums      = user_data.get("album_count", "?")
            country     = user_data.get("country", "?")
            registered  = user_data.get("registered", {}).get("#text", "?") if isinstance(user_data.get("registered"), dict) else "?"
            realname    = user_data.get("realname", "")
            lines = [
                "```",
                f"  {username}" + (f" ({realname})" if realname else ""),
                "  " + "─"*30,
                f"  scrobbles    {scrobbles}",
                f"  artists      {artists}",
                f"  albums       {albums}",
                f"  tracks       {tracks}",
                f"  country      {country}",
                f"  since        {registered}",
                f"  last.fm/user/{username}",
                "```",
            ]
            await message.channel.send("\n".join(lines))

        elif sub == "compare":
            try: await message.delete()
            except Exception: pass
            username = _lfm_user()
            if not username:
                return await message.channel.send("```\n✗ set your last.fm first\n```", delete_after=8)
            if len(args) < 3:
                return await message.channel.send("```\nusage: lastfm compare <other_username>\n```", delete_after=8)
            other = args[2].strip()
            data = await lfm_get("tasteometer.compare", {"type1": "user", "type2": "user", "value1": username, "value2": other, "limit": 5})
            result = data.get("comparison", {}).get("result", {})
            score_raw = result.get("score", 0)
            try: score = float(score_raw) * 100
            except: score = 0
            artists_list = result.get("artists", {}).get("artist", [])
            if isinstance(artists_list, dict):
                artists_list = [artists_list]
            bar = _progress_bar(int(score), 20)
            lines = [
                "```",
                f"  taste compare — {username} vs {other}",
                "  " + "─"*34,
                f"  compatibility  {score:.1f}%",
                f"  {bar}",
            ]
            if artists_list:
                lines.append("  ─"*17)
                lines.append("  shared artists")
                for a in artists_list[:5]:
                    name = a.get("name", "?") if isinstance(a, dict) else str(a)
                    lines.append(f"    • {name}")
            lines.append("```")
            await message.channel.send("\n".join(lines))

        elif sub == "rpc":
            try: await message.delete()
            except Exception: pass
            username = _lfm_user()
            if not username:
                return await message.channel.send("```\n✗ set your last.fm first\n```", delete_after=8)
            track = await lfm_now_playing(username)
            if not track or not track["playing"]:
                return await message.channel.send("```\nno track playing right now\n```", delete_after=8)
            title  = track["title"]
            artist = track["artist"]
            ok = await apply_brand_rpc("spotify", [f"{title} | {artist} | 210"])
            if ok:
                await message.channel.send(f"```\n✓ rpc set to {title} — {artist}\n```", delete_after=6)
            else:
                await message.channel.send("```\n✗ rpc update failed\n```", delete_after=6)

        elif sub == "autorpc":
            global _autorpc_task, _autorpc_enabled
            option = args[2].lower() if len(args) > 2 else ""
            if option == "on":
                username = _lfm_user()
                if not username:
                    return await message.edit(content="```\n✗ set your last.fm first\n```")
                if _autorpc_task and not _autorpc_task.done():
                    _autorpc_task.cancel()
                _autorpc_enabled = True
                _autorpc_task = asyncio.create_task(lfm_autorpc_loop())
                await message.edit(content="```\n✓ lastfm autorpc enabled — updates every 30s\n```")
            elif option == "off":
                _autorpc_enabled = False
                if _autorpc_task and not _autorpc_task.done():
                    _autorpc_task.cancel()
                    _autorpc_task = None
                await client.change_presence(activity=None)
                await message.edit(content="```\n✗ lastfm autorpc disabled\n```")
            else:
                state = "on" if _autorpc_enabled else "off"
                await message.edit(content=f"```\nauthorpc is {state}\n```")

        else:
            try: await message.delete()
            except Exception: pass
            await message.channel.send(build_help_lastfm())

@client.event
async def on_message_delete(message):
    if not LOGGER_ENABLED or message.author.id == client.user.id: return
    try:
        log_message("DELETE", f"{message.author} in #{getattr(message.channel, 'name', 'DM')}: {message.content}")
    except Exception: pass

@client.event
async def on_message_edit(before, after):
    if not LOGGER_ENABLED or before.author.id == client.user.id: return
    if before.content == after.content: return
    try:
        log_message("EDIT", f"{before.author}: '{before.content}' → '{after.content}'")
    except Exception: pass

# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────


# ─────────────────────────────────────────────
# MIMIC STATE (fun cog)
# ─────────────────────────────────────────────
_mimic_dict = {}  # channel_id -> [user_id, ...]

async def fetch_neko_image(action: str):
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://nekos.life/api/v2/img/{action}"
            async with session.get(url) as resp:
                if resp.status == 200:
                    return (await resp.json()).get("url")
    except Exception:
        pass
    return None

async def send_neko(channel, action: str):
    url = await fetch_neko_image(action)
    if not url:
        await channel.send(f"```\n✗ could not fetch {action} image\n```", delete_after=5)
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    import io
                    data = await resp.read()
                    await channel.send(file=discord.File(io.BytesIO(data), f"{action}.gif"))
    except Exception as e:
        await channel.send(f"```\n✗ {e}\n```", delete_after=5)

# ─────────────────────────────────────────────
# HOST SYSTEM
# ─────────────────────────────────────────────
HOSTED_TOKENS = []

def load_hosted():
    global HOSTED_TOKENS
    cfg = load_config()
    HOSTED_TOKENS = cfg.get("hosted_tokens", [])

def save_hosted():
    cfg = load_config()
    cfg["hosted_tokens"] = HOSTED_TOKENS
    save_config(cfg)

load_hosted()

async def hosted_send(token: str, channel_id: int, content: str):
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://discord.com/api/v9/channels/{channel_id}/messages"
            headers = {"Authorization": token.strip(), "Content-Type": "application/json", "User-Agent": USER_AGENT}
            async with session.post(url, headers=headers, json={"content": content}) as resp:
                return resp.status in (200, 201)
    except Exception:
        return False

async def get_token_username(token: str):
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": token.strip(), "User-Agent": USER_AGENT}
            async with session.get("https://discord.com/api/v9/users/@me", headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("username", "unknown")
    except Exception:
        pass
    return "unknown"


# ─────────────────────────────────────────────
# LAST.FM COG
# ─────────────────────────────────────────────

LASTFM_API_BASE = "https://ws.audioscrobbler.com/2.0/"

# Free public API key — replace with your own from https://www.last.fm/api/account/create
# The default key below is a read-only public key for basic scrobble data
LASTFM_API_KEY = "your_lastfm_api_key_here"

_lastfm_cfg = {}        # username, api_key per user
_autorpc_task = None    # background task handle
_autorpc_enabled = False

def load_lastfm_cfg():
    global _lastfm_cfg, LASTFM_API_KEY
    cfg = load_config()
    _lastfm_cfg = cfg.get("lastfm", {})
    if _lastfm_cfg.get("api_key"):
        LASTFM_API_KEY = _lastfm_cfg["api_key"]

def save_lastfm_cfg():
    cfg = load_config()
    cfg["lastfm"] = _lastfm_cfg
    save_config(cfg)

load_lastfm_cfg()

PERIOD_MAP = {
    "w":   "7day",   "week":  "7day",  "7day": "7day",
    "m":   "1month", "month": "1month","1month": "1month",
    "3m":  "3month", "3month":"3month",
    "6m":  "6month", "6month":"6month",
    "y":   "12month","year":  "12month","12month":"12month",
    "all": "overall","overall":"overall",
}
PERIOD_LABEL = {
    "7day": "this week", "1month": "this month",
    "3month": "past 3 months", "6month": "past 6 months",
    "12month": "this year", "overall": "all time",
}

async def lfm_get(method: str, params: dict) -> dict:
    params.update({
        "method": method,
        "api_key": LASTFM_API_KEY,
        "format": "json",
    })
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(LASTFM_API_BASE, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {"error": resp.status, "message": f"HTTP {resp.status}"}
    except Exception as e:
        return {"error": 0, "message": str(e)}

def _lfm_user():
    return _lastfm_cfg.get("username", "")

def _progress_bar(pct: int, width: int = 12) -> str:
    filled = int(width * pct / 100)
    return "▓" * filled + "░" * (width - filled)

async def lfm_now_playing(username: str) -> dict | None:
    data = await lfm_get("user.getRecentTracks", {
        "user": username, "limit": 1, "extended": 1,
    })
    tracks = data.get("recenttracks", {}).get("track", [])
    if not tracks:
        return None
    track = tracks[0] if isinstance(tracks, list) else tracks
    is_playing = track.get("@attr", {}).get("nowplaying") == "true"
    return {
        "title":    track.get("name", "Unknown"),
        "artist":   track.get("artist", {}).get("name", "Unknown") if isinstance(track.get("artist"), dict) else track.get("artist", "Unknown"),
        "album":    track.get("album", {}).get("#text", "") if isinstance(track.get("album"), dict) else "",
        "image":    next((i["#text"] for i in track.get("image", []) if i.get("size") == "large" and i.get("#text")), ""),
        "url":      track.get("url", ""),
        "playing":  is_playing,
        "loved":    track.get("loved", "0") == "1",
        "scrobbles":data.get("recenttracks", {}).get("@attr", {}).get("total", "?"),
    }

async def lfm_autorpc_loop():
    global _autorpc_enabled
    last_track = ""
    while _autorpc_enabled:
        try:
            username = _lfm_user()
            if username:
                track = await lfm_now_playing(username)
                if track and track["playing"]:
                    track_key = f"{track['title']}|{track['artist']}"
                    if track_key != last_track:
                        last_track = track_key
                        title  = track["title"]
                        artist = track["artist"]
                        dur    = 210
                        await apply_brand_rpc("spotify", [f"{title} | {artist} | {dur}"])
        except Exception as e:
            print(f"[LastFM autorpc] error: {e}")
        await asyncio.sleep(30)

print(f"[selfbot] starting — prefix '{PREFIX}'")
try:
    client.run(TOKEN)
except discord.LoginFailure as e:
    print(f"[FATAL] login failed: {e}")
    sys.exit(1)
except Exception as e:
    print(f"[FATAL] {e}")
    raise
