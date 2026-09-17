# selfbot.py | Python 3.10+ | discord.py-self + aiohttp
# railway: set TOKEN env var in Variables tab
# local: put token in config.json

import discord
import asyncio
import json
import os
import re
import base64
import datetime as dt
import configparser
from datetime import datetime, timezone, timedelta
from uuid import uuid4

import aiohttp

# ─────────────────────────────────────────────
# BOOTSTRAP — safe for Railway (no config.json needed)
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

# Token: env var beats config.json
_cfg = load_config()
TOKEN = (
    os.environ.get("TOKEN", "").strip()
    or os.environ.get("DISCORD_TOKEN", "").strip()
    or str(_cfg.get("token", "")).strip()
)

# strip any accidental quotes
TOKEN = TOKEN.strip('"').strip("'")

print(f"[selfbot] token loaded: {TOKEN[:10]}...{TOKEN[-5:] if len(TOKEN) > 15 else '(short)'}")

if not TOKEN or TOKEN in ("YOUR_TOKEN_HERE", "", "None"):
    print("[FATAL] No token found. Set TOKEN env var or put it in config.json")
    raise SystemExit(1)

PREFIX = os.environ.get("PREFIX") or _cfg.get("prefix", ".")
AUTOQUEST_ENABLED = os.environ.get("AUTOQUEST", "").lower() in ("1", "true", "yes") or _cfg.get("autoquest_enabled", False)

LOG_FILE = "message_log.txt"
AUTO_RESPONSES = {}
SNIPER_ENABLED = True
LOGGER_ENABLED = True

# ─────────────────────────────────────────────
# CLIENT — discord.py-self specific flags
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
        "enabled": False,
        "type": "playing",
        "name": "selfbot",
        "state": "running",
        "details": "",
        "url": "https://twitch.tv/voltrix",
        "application_id": None,
        "large_image": "",
        "large_text": "",
        "small_image": "",
        "small_text": "",
        "start_timestamp": None,
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
        # fill missing keys with defaults
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
        print(f"[RPC] config save error: {e}")

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
                    if len(parts) == 2:
                        delta = int(parts[0]) * 60 + int(parts[1])
                    else:
                        delta = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
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
            "playing": ActivityType.playing,
            "streaming": ActivityType.streaming,
            "listening": ActivityType.listening,
            "watching": ActivityType.watching,
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
            kwargs["url"] = cfg.get("url") or "https://twitch.tv/voltrix"

        if cfg.get("state"):
            kwargs["state"] = cfg["state"]
        if cfg.get("details"):
            kwargs["details"] = cfg["details"]

        try:
            ts_kwargs = {}
            parsed_start = parse_timestamp(cfg.get("start_timestamp"), is_end=False)
            ts_kwargs["start"] = parsed_start if parsed_start else datetime.now(timezone.utc)
            parsed_end = parse_timestamp(cfg.get("end_timestamp"), is_end=True)
            if parsed_end:
                ts_kwargs["end"] = parsed_end
            kwargs["timestamps"] = ActivityTimestamps(**ts_kwargs)
        except Exception as e:
            print(f"[RPC] timestamp error: {e}")

        assets_kwargs = {}
        for k in ("large_image", "large_text", "small_image", "small_text"):
            if cfg.get(k):
                assets_kwargs[k] = cfg[k]
        if assets_kwargs:
            try:
                kwargs["assets"] = ActivityAssets(**assets_kwargs)
            except Exception as e:
                print(f"[RPC] assets error: {e}")

        party_cfg = cfg.get("party", {})
        if party_cfg.get("enabled", False):
            try:
                kwargs["party"] = ActivityParty(
                    id="selfbot-party",
                    current_size=int(party_cfg.get("current", 1)),
                    max_size=int(party_cfg.get("max", 5)),
                )
            except Exception as e:
                print(f"[RPC] party error: {e}")

        buttons_list = cfg.get("buttons", [])
        buttons = [
            ActivityButton(label=b["label"], url=b["url"])
            for b in buttons_list[:2]
            if b.get("label") and b.get("url")
        ]
        if buttons:
            try:
                kwargs["buttons"] = buttons
            except Exception as e:
                print(f"[RPC] buttons error: {e}")

        await client.change_presence(activity=Activity(**kwargs))
        print("[RPC] presence updated")

    except Exception as e:
        print(f"[RPC] update_rpc error: {e}")

async def rpc_prompt(channel, author, prompt_text):
    prompt_msg = await channel.send(f"✏️ {prompt_text}\n*(type your value — 60s)*")
    def check(m):
        return m.author.id == author.id and m.channel.id == channel.id
    try:
        msg = await client.wait_for("message", check=check, timeout=60.0)
        val = msg.content.strip()
        try:
            await msg.delete()
        except Exception:
            pass
        try:
            await prompt_msg.delete()
        except Exception:
            pass
        return None if val.lower() == "none" else val
    except asyncio.TimeoutError:
        try:
            await prompt_msg.delete()
        except Exception:
            pass
        await channel.send("❌ timed out.", delete_after=5)
        return "__TIMEOUT__"

# ─────────────────────────────────────────────
# QUEST SYSTEM
# ─────────────────────────────────────────────

UI_EMOJIS = {
    "header": "🎯", "reward": "🎁", "game": "🎮",
    "progress": "⏳", "time": "🕐", "mode": "⚡",
    "overview": "💎", "completed": "✅", "running": "🔄",
    "recovering": "🔃", "failed": "❌", "stopped": "⏹",
}
TASK_EMOJIS = {
    "WATCH_VIDEO_ON_MOBILE": "📱", "WATCH_VIDEO": "📺",
    "STREAM_ON_DESKTOP": "🖥", "PLAY_ON_DESKTOP": "🕹",
    "PLAY_ON_DESKTOP_V2": "🕹", "PLAY_ACTIVITY": "🎲",
}

class APIError(Exception):
    def __init__(self, status, body=None, text=""):
        super().__init__(f"Discord API error {status}")
        self.status = status
        self.body = body or {}
        self.text = text

def clean_token(token):
    if not token:
        return None
    return token.strip().strip('"').strip("'")

def decode_token_user_id(token):
    token = clean_token(token)
    if not token or "." not in token:
        return None
    first = token.split(".", 1)[0]
    padding = "=" * (-len(first) % 4)
    try:
        return base64.b64decode(first + padding).decode("utf-8")
    except Exception:
        return None

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
    super_props = base64.b64encode(json.dumps(get_super_properties()).encode()).decode()
    return {
        "authorization": token, "accept": "*/*",
        "accept-language": "en,en-US;q=0.9",
        "content-type": "application/json",
        "user-agent": USER_AGENT,
        "x-super-properties": super_props,
        "x-discord-locale": "en-US",
        "x-discord-timezone": "Africa/Algiers",
        "x-debug-options": "bugReporterEnabled",
        "origin": "https://discord.com",
        "referer": "https://discord.com/quest-home",
        "sec-ch-ua": '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "priority": "u=1, i",
    }

async def api_request(session, method, url, headers=None, json_body=None):
    async with session.request(method, url, headers=headers, json=json_body) as resp:
        text = await resp.text()
        body = {}
        if text:
            try:
                body = json.loads(text)
            except Exception:
                body = {"raw": text}
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
                    try:
                        retry_after = float(exc.body.get("retry_after", retry_after))
                    except Exception:
                        pass
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
        if not self.expires_at:
            return "Unknown"
        try:
            parsed = dt.datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            return f"<t:{int(parsed.timestamp())}:R>"
        except Exception:
            return "Unknown"

    def is_expired(self):
        if not self.expires_at:
            return False
        try:
            parsed = dt.datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            return dt.datetime.now(dt.timezone.utc) > parsed
        except Exception:
            return False

    def is_completed(self):
        return bool(self.user_status.get("completed_at") or self.user_status.get("completedAt"))

    def is_enrolled(self):
        return bool(self.user_status.get("enrolled_at"))

    def is_supported(self):
        return self.selected_task in SUPPORTED_TASKS

    def progress_value(self):
        progress = self.user_status.get("progress", {})
        task_progress = progress.get(self.selected_task, {})
        return float(task_progress.get("value", 0) or 0) if task_progress else 0.0

    def progress_percent(self):
        if not self.target:
            return 0
        return min(100, int((self.progress_value() / self.target) * 100))

    def _pick_task(self):
        for t in SUPPORTED_TASKS:
            if t in self.tasks:
                return t
        return next(iter(self.tasks.keys()), "UNKNOWN")

class QuestService:
    def __init__(self, token, speed_mode="fast"):
        self.token = clean_token(token)
        self.speed_mode = speed_mode
        self.user_id = decode_token_user_id(token)
        self.headers = get_headers(self.token)

    async def fetch_quests(self, session):
        try:
            payload = await request_with_retry(
                session, "GET", "https://discord.com/api/v9/quests/@me",
                headers=self.headers)
            quests = []
            for raw in payload.get("quests", []):
                q = QuestRecord(raw)
                if not q.is_expired():
                    quests.append(q)
            return quests
        except Exception as e:
            print(f"[QuestService] fetch error: {e}")
            return []

    async def refresh_quest(self, session, quest_id):
        for q in await self.fetch_quests(session):
            if q.id == quest_id:
                return q
        return None

    async def get_user_status(self, session, quest_id):
        return await request_with_retry(
            session, "GET",
            f"https://discord.com/api/v9/quests/{quest_id}/user-status",
            headers=self.headers)

    async def enroll(self, session, quest):
        payload = await request_with_retry(
            session, "POST",
            f"https://discord.com/api/v9/quests/{quest.id}/enroll",
            headers=self.headers,
            json_body={"location": 11, "is_targeted": False, "metadata_raw": None})
        if payload:
            quest.data["user_status"] = payload
        return True

    async def run_quest(self, session, quest, on_update=None):
        while True:
            try:
                if not quest.is_enrolled():
                    await self.enroll(session, quest)
                    await self._emit(on_update, quest, "Enrolled", status="enrolled")
                if not quest.is_supported():
                    await self._emit(on_update, quest, "Unsupported task", status="unsupported")
                    return {"status": "unsupported", "quest": quest}
                result = await self._run_solver(session, quest, on_update)
                if result["status"] == "completed":
                    return result
                fresh = await self.refresh_quest(session, quest.id)
                if fresh and fresh.is_completed():
                    await self._emit(on_update, fresh, "Completed", percent=100, status="completed")
                    return {"status": "completed", "quest": fresh, "percent": 100}
                if fresh:
                    quest = fresh
                await self._emit(on_update, quest,
                    f"Recovering from {quest.progress_percent()}%",
                    percent=quest.progress_percent(), status="recovering")
                await asyncio.sleep(5.0)
            except APIError as exc:
                if exc.status == 404:
                    fresh = await self.refresh_quest(session, quest.id)
                    if fresh and fresh.is_completed():
                        await self._emit(on_update, fresh, "Completed", percent=100, status="completed")
                        return {"status": "completed", "quest": fresh, "percent": 100}
                    if fresh:
                        quest = fresh
                    await asyncio.sleep(5.0)
                    continue
                if exc.status in (401, 403):
                    msg = f"API error {exc.status}"
                    await self._emit(on_update, quest, msg, status="failed_unrecoverable")
                    return {"status": "failed_unrecoverable", "quest": quest,
                            "percent": quest.progress_percent(), "reason": msg}
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[Quest] run_quest error: {e}")
                await asyncio.sleep(5.0)

    async def _run_solver(self, session, quest, on_update=None):
        task = quest.selected_task
        await self._emit(on_update, quest, "Solver active", status="running")
        if task in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
            return await self._run_video(session, quest, on_update)
        if task in ("PLAY_ON_DESKTOP", "PLAY_ON_DESKTOP_V2"):
            payloads = [{"stream_key": f"call:{quest.id}:1", "terminal": False}]
            if quest.app_id:
                payloads.append({"application_id": quest.app_id, "terminal": False})
            return await self._run_heartbeat(session, quest, payloads, on_update)
        if task == "PLAY_ACTIVITY":
            user_key = self.user_id or quest.id
            return await self._run_heartbeat(session, quest, [
                {"stream_key": f"call:{user_key}:1", "terminal": False},
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
                timestamp = min(float(quest.target),
                    float(int(dt.datetime.now(dt.timezone.utc).timestamp()) - started_at))
                try:
                    data = await request_with_retry(
                        session, "POST",
                        f"https://discord.com/api/v9/quests/{quest.id}/video-progress",
                        headers=self.headers, json_body={"timestamp": timestamp}, retries=2)
                except APIError as exc:
                    if exc.status == 400:
                        status_data = await self.get_user_status(session, quest.id)
                        quest.data["user_status"] = status_data
                        last_good = max(last_good, quest.progress_value())
                        if quest.is_completed():
                            await self._emit(on_update, quest, "Completed", percent=100, status="completed")
                            return {"status": "completed", "quest": quest, "percent": 100}
                        await asyncio.sleep(1.1)
                        continue
                    raise
                if data:
                    quest.data["user_status"] = data
                last_good = max(last_good, timestamp, quest.progress_value())
                percent = min(100, int((last_good / quest.target) * 100)) if quest.target else 0
                await self._emit(on_update, quest, f"Progressing [{percent}%]",
                    percent=percent, status="running")
                if data.get("completed_at"):
                    fresh = await self.get_user_status(session, quest.id)
                    quest.data["user_status"] = fresh
                    await self._emit(on_update, quest, "Completed", percent=100, status="completed")
                    return {"status": "completed", "quest": quest, "percent": 100}
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            await self._emit(on_update, quest, "Stopped",
                percent=quest.progress_percent(), status="manually_stopped")
            raise
        if quest.is_completed() or quest.progress_value() >= quest.target:
            await self._emit(on_update, quest, "Completed", percent=100, status="completed")
            return {"status": "completed", "quest": quest, "percent": 100}
        return {"status": "recovering", "quest": quest, "percent": quest.progress_percent()}

    async def _run_heartbeat(self, session, quest, payloads, on_update=None):
        interval = 30 if self.speed_mode == "fast" else 40
        active_payload = payloads[0]
        last_percent = -1
        try:
            while True:
                data = None
                for payload in payloads:
                    try:
                        data = await request_with_retry(
                            session, "POST",
                            f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                            headers=self.headers, json_body=payload, retries=2)
                        active_payload = payload
                        break
                    except APIError:
                        continue
                if data is None:
                    try:
                        status_data = await self.get_user_status(session, quest.id)
                        quest.data["user_status"] = status_data
                    except APIError as exc:
                        if exc.status == 404:
                            await asyncio.sleep(2.0)
                            continue
                        raise
                else:
                    quest.data["user_status"] = data
                percent = quest.progress_percent()
                if percent != last_percent:
                    await self._emit(on_update, quest, f"Progressing [{percent}%]",
                        percent=percent, status="running")
                    last_percent = percent
                if quest.is_completed() or quest.progress_value() >= quest.target:
                    break
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            await self._send_terminal(session, quest.id, active_payload)
            await self._emit(on_update, quest, "Stopped",
                percent=quest.progress_percent(), status="manually_stopped")
            raise
        await self._send_terminal(session, quest.id, active_payload)
        if quest.is_completed() or quest.progress_value() >= quest.target:
            await self._emit(on_update, quest, "Completed", percent=100, status="completed")
            return {"status": "completed", "quest": quest, "percent": 100}
        return {"status": "recovering", "quest": quest, "percent": quest.progress_percent()}

    async def _send_terminal(self, session, quest_id, payload):
        try:
            terminal = dict(payload)
            terminal["terminal"] = True
            await request_with_retry(session, "POST",
                f"https://discord.com/api/v9/quests/{quest_id}/heartbeat",
                headers=self.headers, json_body=terminal, retries=2)
        except Exception:
            pass

    async def _emit(self, callback, quest, message, percent=None, status=None):
        if callback is None:
            return
        payload = {
            "quest_id": quest.id, "quest_name": quest.name,
            "percent": percent if percent is not None else quest.progress_percent(),
            "status": status or "running", "message": message, "quest": quest,
        }
        try:
            res = callback(payload)
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            pass

# ─────────────────────────────────────────────
# ORB BADGE
# ─────────────────────────────────────────────

ORB_BADGE_SKU_ID = "1342211853484429445"

async def _claim_orb_badge(session, token):
    headers = {
        "accept": "*/*", "authorization": token,
        "content-type": "application/json",
        "origin": "https://discord.com",
        "referer": "https://discord.com/shop?tab=orbs",
        "user-agent": USER_AGENT,
    }
    balance = None
    for url in [
        "https://discord.com/api/v9/users/@me/orbs/balance",
        "https://discord.com/api/v9/users/@me/virtual-currency/balance",
    ]:
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status < 400:
                    body = json.loads(await resp.text()) if await resp.text() else {}
                    for key in ("balance", "discord_orb", "orbs", "amount", "total"):
                        val = body.get(key)
                        if isinstance(val, (int, float)):
                            balance = int(val)
                            break
        except Exception:
            pass
        if balance is not None:
            break
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
                print("[AutoQuest] No active quests.")
                return
            for quest in active:
                print(f"[AutoQuest] Running: {quest.name}")
                result = await service.run_quest(session, quest)
                if result and result.get("status") == "completed":
                    print(f"[AutoQuest] ✅ {quest.name} | {quest.reward_name}")
    except Exception as e:
        print(f"[AutoQuest] error: {e}")

def make_progress_bar(percent):
    filled = int(percent / 10)
    return "█" * filled + "░" * (10 - filled) + f" {percent}%"

# ─────────────────────────────────────────────
# NITRO SNIPER
# ─────────────────────────────────────────────

GIFT_PATTERN = re.compile(r"(discord\.gift|discord\.com/gifts)/([a-zA-Z0-9]+)")

async def snipe_nitro(code, channel_id):
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://discord.com/api/v9/entitlements/gift-codes/{code}/redeem"
            headers = {
                "Authorization": TOKEN,
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            }
            async with session.post(url, headers=headers, json={"channel_id": str(channel_id)}) as r:
                data = {}
                try:
                    data = await r.json()
                except Exception:
                    pass
                if r.status == 200:
                    log_message("SNIPER", f"✅ SNIPED: {code}")
                else:
                    log_message("SNIPER", f"❌ Failed: {code} | {r.status} | {data.get('message', '')}")
    except Exception as e:
        log_message("SNIPER", f"error: {e}")

# ─────────────────────────────────────────────
# EVENTS
# ─────────────────────────────────────────────

@client.event
async def on_ready():
    print(f"[+] Logged in as {client.user} ({client.user.id})")
    print(f"[+] Prefix: {PREFIX} | Servers: {len(client.guilds)}")
    await update_rpc()
    if AUTOQUEST_ENABLED:
        asyncio.create_task(run_autoquest_pass_now(TOKEN))
        print("[+] AutoQuest: running pass now")

@client.event
async def on_message(message):
    global SNIPER_ENABLED, LOGGER_ENABLED

    # logger
    if LOGGER_ENABLED and message.guild:
        try:
            log_message("MSG",
                f"{message.guild.name}/#{message.channel.name} | "
                f"{message.author} ({message.author.id}): {message.content}")
        except Exception:
            pass

    # sniper
    if SNIPER_ENABLED and message.author.id != client.user.id:
        for _, code in GIFT_PATTERN.findall(message.content):
            asyncio.create_task(snipe_nitro(code, message.channel.id))

    # auto-responder
    if message.author.id != client.user.id:
        content_lower = message.content.lower()
        for trigger, response in AUTO_RESPONSES.items():
            if trigger.lower() in content_lower:
                try:
                    await message.channel.send(response)
                except Exception:
                    pass
                break
        return

    if not message.content.startswith(PREFIX):
        return

    raw = message.content[len(PREFIX):]
    args = raw.split()
    cmd = args[0].lower() if args else ""

    # ─── CORE COMMANDS ───

    if cmd == "ping":
        latency = round(client.latency * 1000)
        await message.edit(content=f"pong. `{latency}ms`")

    elif cmd == "purge":
        limit = int(args[1]) if len(args) > 1 else 5
        deleted = 0
        await message.delete()
        async for msg in message.channel.history(limit=300):
            if msg.author.id == client.user.id:
                try:
                    await msg.delete()
                    deleted += 1
                except Exception:
                    pass
                await asyncio.sleep(0.4)
                if deleted >= limit:
                    break

    elif cmd == "say":
        text = " ".join(args[1:])
        await message.edit(content=text)

    elif cmd == "spam":
        if len(args) < 3:
            return await message.edit(content="usage: `.spam <count> <text>`")
        count = int(args[1])
        text = " ".join(args[2:])
        await message.delete()
        for _ in range(min(count, 20)):
            await message.channel.send(text)
            await asyncio.sleep(1)

    elif cmd == "status":
        text = " ".join(args[1:])
        await client.change_presence(activity=discord.CustomActivity(name=text))
        await message.edit(content=f"status → `{text}`")

    elif cmd == "clear":
        await message.delete()

    elif cmd == "info":
        u = client.user
        created = u.created_at.strftime("%Y-%m-%d")
        await message.edit(content=(
            f"**{u}** `{u.id}`\n"
            f"created: `{created}`\n"
            f"servers: `{len(client.guilds)}`\n"
            f"prefix: `{PREFIX}`"
        ))

    elif cmd == "copycat":
        if len(args) < 2:
            return await message.edit(content="usage: `.copycat <user_id>`")
        try:
            target_id = int(args[1])
        except ValueError:
            return await message.edit(content="invalid user id")
        await message.delete()
        def check(m):
            return m.author.id == target_id and m.channel.id == message.channel.id
        for _ in range(10):
            try:
                msg = await client.wait_for("message", check=check, timeout=60)
                await message.channel.send(msg.content)
            except asyncio.TimeoutError:
                break

    elif cmd == "sniper":
        SNIPER_ENABLED = len(args) < 2 or args[1].lower() == "on"
        await message.edit(content=f"sniper → **{'ON' if SNIPER_ENABLED else 'OFF'}**")

    elif cmd == "logger":
        LOGGER_ENABLED = len(args) < 2 or args[1].lower() == "on"
        await message.edit(content=f"logger → **{'ON' if LOGGER_ENABLED else 'OFF'}**")

    elif cmd == "readlog":
        n = int(args[1]) if len(args) > 1 else 10
        if not os.path.exists(LOG_FILE):
            return await message.edit(content="no log file yet.")
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        tail = "".join(lines[-n:])
        if len(tail) > 1900:
            tail = tail[-1900:]
        await message.edit(content=f"```\n{tail}\n```")

    elif cmd == "ar":
        if len(args) < 2:
            return await message.edit(content="`.ar add trigger | response` / `.ar remove trigger` / `.ar list`")
        sub = args[1].lower()
        rest = " ".join(args[2:])
        if sub == "add":
            if "|" not in rest:
                return await message.edit(content="format: `.ar add trigger | response`")
            trigger, response = rest.split("|", 1)
            AUTO_RESPONSES[trigger.strip()] = response.strip()
            await message.edit(content=f"added: `{trigger.strip()}` → `{response.strip()}`")
        elif sub == "remove":
            key = rest.strip()
            AUTO_RESPONSES.pop(key, None)
            await message.edit(content=f"removed: `{key}`")
        elif sub == "list":
            if not AUTO_RESPONSES:
                return await message.edit(content="no auto-responses set.")
            lines = "\n".join(f"`{k}` → `{v}`" for k, v in AUTO_RESPONSES.items())
            await message.edit(content=f"**auto-responses:**\n{lines}")
        else:
            await message.edit(content="unknown subcommand. use `add`, `remove`, or `list`.")

    # ─── QUEST COMMANDS ───

    elif cmd == "quest":
        await message.delete()
        service = QuestService(TOKEN)
        await message.channel.send("fetching quests...")
        async with aiohttp.ClientSession() as session:
            quests = await service.fetch_quests(session)
        if not quests:
            return await message.channel.send("no quests found.")
        lines = []
        for i, q in enumerate(quests):
            bar = make_progress_bar(q.progress_percent())
            tag = "✅" if q.is_completed() else ("🟢" if q.is_supported() else "🔴")
            lines.append(
                f"{tag} **[{i}]** {q.name}\n"
                f"{bar} | `{q.selected_task}`\n"
                f"reward: **{q.reward_name}** | expires: {q.expires_relative()}"
            )
        await message.channel.send("**quests:**\n\n" + "\n\n".join(lines))

    elif cmd == "questrun":
        await message.delete()
        idx = int(args[1]) if len(args) > 1 else 0
        service = QuestService(TOKEN, speed_mode="fast")
        async with aiohttp.ClientSession() as session:
            quests = await service.fetch_quests(session)
            if not quests or idx >= len(quests):
                return await message.channel.send("quest index out of range.")
            quest = quests[idx]
            if quest.is_completed():
                return await message.channel.send(f"**{quest.name}** is already completed.")
            status_msg = await message.channel.send(f"starting **{quest.name}**...")
            async def on_update(payload):
                bar = make_progress_bar(payload["percent"])
                try:
                    await status_msg.edit(content=(
                        f"**{payload['quest_name']}**\n"
                        f"{bar}\n"
                        f"status: `{payload['status']}`"
                    ))
                except Exception:
                    pass
            result = await service.run_quest(session, quest, on_update=on_update)
            if result and result.get("status") == "completed":
                await status_msg.edit(content=f"✅ **{quest.name}** complete! reward: **{quest.reward_name}**")
            else:
                await status_msg.edit(content=f"ended with status: `{result.get('status', 'unknown')}`")

    elif cmd == "questall":
        await message.delete()
        service = QuestService(TOKEN, speed_mode="fast")
        async with aiohttp.ClientSession() as session:
            quests = await service.fetch_quests(session)
            active = [q for q in quests if not q.is_completed() and q.is_supported()]
            if not active:
                return await message.channel.send("no active supported quests.")
            for q in active:
                if not q.is_enrolled():
                    try:
                        await service.enroll(session, q)
                    except Exception:
                        pass
            tracker = {q.id: {"name": q.name, "percent": q.progress_percent(), "status": "queued"}
                       for q in active}
            status_msg = await message.channel.send("starting questall...")

            async def update_msg():
                lines = [
                    f"**{info['name']}**: {make_progress_bar(info['percent'])} `{info['status'].upper()}`"
                    for info in tracker.values()
                ]
                try:
                    await status_msg.edit(content="\n".join(lines))
                except Exception:
                    pass

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
                except Exception as e:
                    tracker[q.id]["status"] = "error"
                    await update_msg()

            await asyncio.gather(*(run_one(q) for q in active))
            await status_msg.edit(content="**questall finished.**\n" + "\n".join(
                f"**{info['name']}**: {make_progress_bar(info['percent'])} `{info['status'].upper()}`"
                for info in tracker.values()
            ))

    elif cmd == "autoquest":
        cfg = load_config()
        if len(args) < 2:
            cfg["autoquest_enabled"] = not cfg.get("autoquest_enabled", False)
        else:
            cfg["autoquest_enabled"] = args[1].lower() in ("on", "enable", "true")
        save_config(cfg)
        enabled = cfg["autoquest_enabled"]
        if enabled:
            asyncio.create_task(run_autoquest_pass_now(TOKEN))
        await message.edit(content=f"autoquest → **{'enabled' if enabled else 'disabled'}**")

    elif cmd == "orbbadge":
        await message.edit(content="claiming orb badge...")
        async with aiohttp.ClientSession() as session:
            result = await _claim_orb_badge(session, TOKEN)
        if result["status"] == "SUCCESS":
            extra = f"\nbalance before: `{result['balance']}` orbs." if isinstance(result.get("balance"), int) else ""
            await message.edit(content=f"✅ orb badge claimed!{extra}")
        else:
            await message.edit(content=f"❌ failed: {result.get('message')} (code: `{result.get('code')}`)")

    # ─── RPC COMMANDS ───

    elif cmd == "rpc":
        sub = args[1].lower() if len(args) > 1 else ""

        if not sub or sub == "help":
            await message.edit(content=(
                "**rpc commands**\n"
                f"`{PREFIX}rpc enable` / `disable` — toggle\n"
                f"`{PREFIX}rpc status` — current config\n"
                f"`{PREFIX}rpc type` — playing/streaming/watching/listening/competing\n"
                f"`{PREFIX}rpc name` / `details` / `state` / `url`\n"
                f"`{PREFIX}rpc start` / `end` — timestamps (unix / MM:SS / none)\n"
                f"`{PREFIX}rpc large_image` / `large_text` / `small_image` / `small_text`\n"
                f"`{PREFIX}rpc button1_name` / `button1_url` / `button2_name` / `button2_url`\n"
                f"`{PREFIX}rpc party enable` / `disable` / `current` / `max`"
            ))

        elif sub == "enable":
            cfg = load_rpc_config()
            cfg["enabled"] = True
            save_rpc_config(cfg)
            await update_rpc()
            await message.edit(content="✅ RPC enabled")

        elif sub == "disable":
            cfg = load_rpc_config()
            cfg["enabled"] = False
            save_rpc_config(cfg)
            await update_rpc()
            await message.edit(content="❌ RPC disabled")

        elif sub == "status":
            cfg = load_rpc_config()
            state = "✅ enabled" if cfg.get("enabled") else "❌ disabled"
            await message.edit(content=(
                f"**RPC:** {state}\n"
                f"type: `{cfg.get('type')}` | name: `{cfg.get('name')}`\n"
                f"details: `{cfg.get('details') or 'none'}` | state: `{cfg.get('state') or 'none'}`\n"
                f"large_image: `{cfg.get('large_image') or 'none'}`\n"
                f"party: `{'on' if cfg.get('party', {}).get('enabled') else 'off'}`"
            ))

        elif sub == "type":
            await message.delete()
            valid = ["playing", "streaming", "listening", "watching", "competing"]
            val = await rpc_prompt(message.channel, message.author, f"Enter type ({'/'.join(valid)}):")
            if val == "__TIMEOUT__":
                return
            if val not in valid:
                return await message.channel.send(f"❌ invalid type: `{val}`", delete_after=5)
            cfg = load_rpc_config()
            cfg["type"] = val
            save_rpc_config(cfg)
            await update_rpc()
            await message.channel.send(f"✅ type → `{val}`", delete_after=5)

        elif sub in ("name", "details", "state", "large_image", "large_text",
                     "small_image", "small_text", "url"):
            await message.delete()
            prompts = {
                "name": "Enter RPC name:",
                "details": "Enter details (line 2):",
                "state": "Enter state (line 3):",
                "large_image": "Enter large image key/URL:",
                "large_text": "Enter large image hover text:",
                "small_image": "Enter small image key/URL:",
                "small_text": "Enter small image hover text:",
                "url": "Enter streaming URL (twitch):",
            }
            val = await rpc_prompt(message.channel, message.author, prompts[sub])
            if val == "__TIMEOUT__":
                return
            cfg = load_rpc_config()
            cfg[sub] = val
            save_rpc_config(cfg)
            await update_rpc()
            await message.channel.send(f"✅ {sub} → `{val or 'cleared'}`", delete_after=5)

        elif sub in ("start", "end"):
            await message.delete()
            key = "start_timestamp" if sub == "start" else "end_timestamp"
            val = await rpc_prompt(message.channel, message.author,
                f"Enter {sub} timestamp (unix / MM:SS / none to clear):")
            if val == "__TIMEOUT__":
                return
            cfg = load_rpc_config()
            cfg[key] = None if (val is None or (val and val.lower() == "none")) else val
            save_rpc_config(cfg)
            await update_rpc()
            await message.channel.send(f"✅ {sub} timestamp set", delete_after=5)

        elif sub in ("button1_name", "button1_url", "button2_name", "button2_url"):
            await message.delete()
            idx = 0 if sub.startswith("button1") else 1
            field = "label" if sub.endswith("name") else "url"
            label = f"Button {idx + 1} {'label' if field == 'label' else 'URL'}"
            val = await rpc_prompt(message.channel, message.author, f"Enter {label}:")
            if val == "__TIMEOUT__":
                return
            cfg = load_rpc_config()
            cfg["buttons"][idx][field] = val or ""
            save_rpc_config(cfg)
            await update_rpc()
            await message.channel.send(f"✅ {label} set", delete_after=5)

        elif sub == "party":
            option = args[2].lower() if len(args) > 2 else ""
            cfg = load_rpc_config()
            if option == "enable":
                cfg["party"]["enabled"] = True
                save_rpc_config(cfg)
                await update_rpc()
                await message.edit(content="✅ party enabled")
            elif option == "disable":
                cfg["party"]["enabled"] = False
                save_rpc_config(cfg)
                await update_rpc()
                await message.edit(content="❌ party disabled")
            elif option in ("current", "max"):
                await message.delete()
                val = await rpc_prompt(message.channel, message.author,
                    f"Enter party {option} (integer):")
                if val == "__TIMEOUT__":
                    return
                try:
                    cfg["party"][option] = int(val)
                    save_rpc_config(cfg)
                    await update_rpc()
                    await message.channel.send(f"✅ party {option} → `{int(val)}`", delete_after=5)
                except (ValueError, TypeError):
                    await message.channel.send("❌ must be an integer", delete_after=5)
            else:
                await message.edit(content="usage: `.rpc party enable/disable/current/max`")

        else:
            await message.edit(content=f"unknown rpc subcommand: `{sub}` — use `.rpc help`")

    elif cmd == "help":
        await message.edit(content=(
            f"**── selfbot `{PREFIX}` ──**\n"
            f"`ping` `purge <n>` `say <text>` `spam <n> <text>`\n"
            f"`status <text>` `clear` `info` `copycat <id>`\n"
            f"`sniper on/off` `logger on/off` `readlog <n>`\n"
            f"`ar add/remove/list`\n"
            f"**── quests ──**\n"
            f"`quest` `questrun <i>` `questall`\n"
            f"`autoquest on/off` `orbbadge`\n"
            f"**── rpc ──**\n"
            f"`rpc help`"
        ))

@client.event
async def on_message_delete(message):
    if not LOGGER_ENABLED or message.author.id == client.user.id:
        return
    try:
        log_message("DELETE",
            f"{message.author} in #{getattr(message.channel, 'name', 'DM')}: {message.content}")
    except Exception:
        pass

@client.event
async def on_message_edit(before, after):
    if not LOGGER_ENABLED or before.author.id == client.user.id:
        return
    if before.content == after.content:
        return
    try:
        log_message("EDIT", f"{before.author}: '{before.content}' → '{after.content}'")
    except Exception:
        pass

# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────

print(f"[selfbot] starting with prefix '{PREFIX}'")
try:
    client.run(TOKEN, bot=False)
except discord.LoginFailure as e:
    print(f"[FATAL] Login failed: {e}")
    print("[FATAL] Check your token. Make sure TOKEN env var or config.json is correct.")
    raise SystemExit(1)
except Exception as e:
    print(f"[FATAL] Unexpected error: {e}")
    raise
