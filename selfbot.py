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
LOGGER_ENABLED = True

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
# application_ids sourced from discord's partnership integrations

BRAND_PRESETS = {
    "spotify": {
        "type": "listening",
        "name": "Spotify",
        "application_id": "367827983903490050",
        "large_image": "spotify:ab67616d00001e02ff9ca10b55ce82ae553d50e",
        "large_text": "Spotify",
        "small_image": "spotify:ab6761610000f178049d8eda6f0fd7a34bb0db9",
        "small_text": "Listening",
        "_args": ["title", "artist", "duration"],
        "_usage": ".rpc spotify <title> | <artist> | <duration_secs>",
    },
    "youtube": {
        "type": "watching",
        "name": "YouTube",
        "application_id": "880218394199220334",
        "large_image": "youtube",
        "large_text": "YouTube",
        "_args": ["video", "channel", "duration"],
        "_usage": ".rpc youtube <video title> | <channel> | <duration_secs>",
    },
    "xbox": {
        "type": "playing",
        "name": "Xbox",
        "application_id": "438122941302046720",
        "large_image": "xbox",
        "large_text": "Xbox",
        "small_image": "controller",
        "small_text": "Playing",
        "_args": ["game"],
        "_usage": ".rpc xbox <game name>",
    },
    "playstation": {
        "type": "playing",
        "name": "PlayStation",
        "application_id": "473226677884kwarg",
        "large_image": "playstation",
        "large_text": "PlayStation",
        "small_image": "controller",
        "small_text": "Playing",
        "_args": ["game"],
        "_usage": ".rpc playstation <game name>",
    },
    "crunchyroll": {
        "type": "watching",
        "name": "Crunchyroll",
        "application_id": "1020123345567822899",
        "large_image": "crunchyroll",
        "large_text": "Crunchyroll",
        "_args": ["anime", "episode"],
        "_usage": ".rpc crunchyroll <anime name> | <episode>",
    },
    "custom": {
        "type": "playing",
        "name": "",
        "_args": ["name", "details", "state"],
        "_usage": ".rpc custom <name> | <details> | <state>",
    },
}

async def apply_brand_rpc(brand: str, user_args: list):
    """Build and apply a brand RPC preset."""
    try:
        from discord.activity import ActivityAssets, ActivityParty, ActivityTimestamps
        from discord import ActivityButton, ActivityType, Activity
    except ImportError as e:
        print(f"[RPC] import error: {e}")
        return False

    preset = BRAND_PRESETS.get(brand)
    if not preset:
        return False

    type_map = {
        "playing": ActivityType.playing, "streaming": ActivityType.streaming,
        "listening": ActivityType.listening, "watching": ActivityType.watching,
        "competing": ActivityType.competing,
    }

    act_type = type_map.get(preset.get("type", "playing"), ActivityType.playing)
    now = datetime.now(timezone.utc)

    kwargs = {"type": act_type, "name": preset.get("name", brand.capitalize())}

    if preset.get("application_id"):
        try:
            kwargs["application_id"] = int(preset["application_id"])
        except (ValueError, TypeError):
            pass

    assets_kwargs = {}
    if preset.get("large_image"):
        assets_kwargs["large_image"] = preset["large_image"]
    if preset.get("large_text"):
        assets_kwargs["large_text"] = preset["large_text"]
    if preset.get("small_image"):
        assets_kwargs["small_image"] = preset["small_image"]
    if preset.get("small_text"):
        assets_kwargs["small_text"] = preset["small_text"]

    # map user_args to fields based on brand
    if brand == "spotify" and user_args:
        parts = " ".join(user_args).split("|")
        title  = parts[0].strip() if len(parts) > 0 else "Unknown"
        artist = parts[1].strip() if len(parts) > 1 else "Unknown"
        dur    = int(parts[2].strip()) if len(parts) > 2 else 210
        kwargs["details"] = title
        kwargs["state"]   = f"by {artist}"
        kwargs["name"]    = "Spotify"
        try:
            kwargs["timestamps"] = ActivityTimestamps(
                start=now, end=now + timedelta(seconds=dur)
            )
        except Exception:
            pass

    elif brand == "youtube" and user_args:
        parts = " ".join(user_args).split("|")
        video   = parts[0].strip() if len(parts) > 0 else "Video"
        channel = parts[1].strip() if len(parts) > 1 else "Channel"
        dur     = int(parts[2].strip()) if len(parts) > 2 else 600
        kwargs["details"] = video
        kwargs["state"]   = channel
        assets_kwargs["large_text"] = channel
        try:
            kwargs["timestamps"] = ActivityTimestamps(
                start=now, end=now + timedelta(seconds=dur)
            )
        except Exception:
            pass

    elif brand in ("xbox", "playstation") and user_args:
        game = " ".join(user_args).split("|")[0].strip()
        kwargs["details"] = game
        assets_kwargs["large_text"] = game
        try:
            kwargs["timestamps"] = ActivityTimestamps(start=now)
        except Exception:
            pass

    elif brand == "crunchyroll" and user_args:
        parts = " ".join(user_args).split("|")
        anime   = parts[0].strip() if len(parts) > 0 else "Anime"
        episode = parts[1].strip() if len(parts) > 1 else "Anime"
        kwargs["details"] = anime
        kwargs["state"]   = episode
        assets_kwargs["large_text"] = anime
        dur = 1440
        try:
            kwargs["timestamps"] = ActivityTimestamps(
                start=now, end=now + timedelta(seconds=dur)
            )
        except Exception:
            pass

    elif brand == "custom" and user_args:
        parts = " ".join(user_args).split("|")
        kwargs["name"]    = parts[0].strip() if len(parts) > 0 else "Custom"
        if len(parts) > 1:
            kwargs["details"] = parts[1].strip()
        if len(parts) > 2:
            kwargs["state"] = parts[2].strip()
        try:
            kwargs["timestamps"] = ActivityTimestamps(start=now)
        except Exception:
            pass

    else:
        try:
            kwargs["timestamps"] = ActivityTimestamps(start=now)
        except Exception:
            pass

    if assets_kwargs:
        try:
            kwargs["assets"] = ActivityAssets(**assets_kwargs)
        except Exception as e:
            print(f"[RPC] assets error: {e}")

    try:
        await client.change_presence(activity=Activity(**kwargs))
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
        f"> **selfbot**\n"
        f"```\n"
        f"────────────────────────────────────\n"
        f"  categories\n"
        f"────────────────────────────────────\n"
        f"  general      utilities, platform & status\n"
        f"  rpc          rich presence & brands\n"
        f"  quests       quest completer & orb badge\n"
        f"  sniper       nitro sniper & logger\n"
        f"  ar           auto-responder\n"
        f"────────────────────────────────────\n"
        f"  {p}help <category> for commands\n"
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

HELP_MAP = {
    "":          build_help_root,
    "general":   build_help_general,
    "rpc":       build_help_rpc,
    "quests":    build_help_quests,
    "quest":     build_help_quests,
    "sniper":    build_help_sniper,
    "logger":    build_help_sniper,
    "ar":        build_help_ar,
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
        for _ in range(count):
            await message.channel.send(text)
            await asyncio.sleep(1)

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
        await message.delete()
        service = QuestService(TOKEN)
        m = await message.channel.send(ansi(f"{YE}⟳  fetching quests...{R}"))
        async with aiohttp.ClientSession() as session:
            quests = await service.fetch_quests(session)
        if not quests:
            return await m.edit(content=ansi(f"{DIM}no quests found{R}"))
        lines = [help_header("quests")]
        for i, q in enumerate(quests):
            bar = make_progress_bar(q.progress_percent())
            tag = f"{GR}✓ done{R}" if q.is_completed() else (f"{GR}supported{R}" if q.is_supported() else f"{RD}unsupported{R}")
            lines.append(f"\n{DIM}[{i}]{R} {B}{WH}{q.name}{R}  {tag}")
            lines.append(f"    {bar}")
            lines.append(f"    {DIM}task:{R} {q.selected_task}  {DIM}reward:{R} {YE}{q.reward_name}{R}  {DIM}expires:{R} {q.expires_relative()}")
        await m.edit(content=ansi("\n".join(lines)))

    elif cmd == "questrun":
        await message.delete()
        idx = int(args[1]) if len(args) > 1 else 0
        service = QuestService(TOKEN, speed_mode="fast")
        async with aiohttp.ClientSession() as session:
            quests = await service.fetch_quests(session)
            if not quests or idx >= len(quests):
                return await message.channel.send(ansi(f"{RD}✗  quest index out of range{R}"))
            quest = quests[idx]
            if quest.is_completed():
                return await message.channel.send(ansi(f"{GR}✓  {quest.name} already completed{R}"))
            sm = await message.channel.send(ansi(f"{YE}⟳  starting {quest.name}...{R}"))
            async def on_update(payload):
                bar = make_progress_bar(payload["percent"])
                try:
                    await sm.edit(content=ansi(
                        f"{help_row(payload['quest_name'], payload['status'])}\n"
                        f"    {bar}"
                    ))
                except Exception: pass
            result = await service.run_quest(session, quest, on_update=on_update)
            if result and result.get("status") == "completed":
                await sm.edit(content=ansi(f"{GR}✓  {quest.name} complete!{R}  {YE}{quest.reward_name}{R}"))
            else:
                await sm.edit(content=ansi(f"{DIM}ended: {result.get('status', 'unknown')}{R}"))

    elif cmd == "questall":
        await message.delete()
        service = QuestService(TOKEN, speed_mode="fast")
        async with aiohttp.ClientSession() as session:
            quests = await service.fetch_quests(session)
            active = [q for q in quests if not q.is_completed() and q.is_supported()]
            if not active:
                return await message.channel.send(ansi(f"{DIM}no active supported quests{R}"))
            for q in active:
                if not q.is_enrolled():
                    try: await service.enroll(session, q)
                    except Exception: pass
            tracker = {q.id: {"name": q.name, "percent": q.progress_percent(), "status": "queued"} for q in active}
            sm = await message.channel.send(ansi(f"{YE}⟳  starting questall...{R}"))
            async def update_msg():
                lines = [help_header("questall")]
                for info in tracker.values():
                    lines.append(f"\n{DIM}├{R} {B}{WH}{info['name']}{R}  {DIM}{info['status'].upper()}{R}")
                    lines.append(f"    {make_progress_bar(info['percent'])}")
                try: await sm.edit(content=ansi("\n".join(lines)))
                except Exception: pass
            async def run_one(q):
                async def on_update(payload):
                    tracker[q.id]["percent"] = payload["percent"]
                    tracker[q.id]["status"] = payload["status"]
                    await update_msg()
                try:
                    tracker[q.id]["status"] = "running"
                    await update_msg()
                    res = await service.run_quest(session, q, on_update=on_update)
                    tracker[q.id]["percent"] = 100 if res and res.get("status") == "completed" else tracker[q.id]["percent"]
                    tracker[q.id]["status"] = res.get("status", "done") if res else "done"
                    await update_msg()
                except Exception:
                    tracker[q.id]["status"] = "error"; await update_msg()
            await asyncio.gather(*(run_one(q) for q in active))

    elif cmd == "autoquest":
        cfg = load_config()
        if len(args) < 2:
            cfg["autoquest_enabled"] = not cfg.get("autoquest_enabled", False)
        else:
            cfg["autoquest_enabled"] = args[1].lower() in ("on", "enable", "true")
        save_config(cfg)
        enabled = cfg["autoquest_enabled"]
        if enabled: asyncio.create_task(run_autoquest_pass_now(TOKEN))
        state = f"{GR}enabled{R}" if enabled else f"{RD}disabled{R}"
        await message.edit(content=ansi(f"{GR}✓ autoquest{R}  {state}"))

    elif cmd == "orbbadge":
        await message.edit(content=ansi(f"{YE}⟳  claiming orb badge...{R}"))
        async with aiohttp.ClientSession() as session:
            result = await _claim_orb_badge(session, TOKEN)
        if result["status"] == "SUCCESS":
            extra = f"  {DIM}balance before: {result['balance']} orbs{R}" if isinstance(result.get("balance"), int) else ""
        await message.edit(content=ansi(
            f"{GR}✓ orb badge claimed!{R}{extra}" if result["status"] == "SUCCESS"
            else f"{RD}✗  failed: {result.get('message')} (code: {result.get('code')}){R}"
        ))

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

print(f"[selfbot] starting — prefix '{PREFIX}'")
try:
    client.run(TOKEN, bot=False)
except discord.LoginFailure as e:
    print(f"[FATAL] login failed: {e}")
    sys.exit(1)
except Exception as e:
    print(f"[FATAL] {e}")
    raise
