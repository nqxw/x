# cogs/rpc.py
import discord
import asyncio
import time
import re
import json
import io
import aiohttp
import hashlib
from pathlib import Path
from discord.ext import commands

# pull the live client from cogs.state if we weren't handed one explicitly.
# this is what fixes `RPCCog.__init__() missing 1 required positional argument: 'bot'`
# when selfbot.py's _boot_cogs does `cls()` on every cog.
try:
    from cogs import state as cstate
except Exception:
    cstate = None

try:
    from utils.ascii_helper import AsciiHelper
    ascii = AsciiHelper()
except Exception as e:
    # fallback shim if utils/ascii_helper.py isn't present
    class _AsciiFallback:
        def success(self, msg): return f"✓ {msg}"
        def error(self, msg): return f"✗ {msg}"
        def info(self, msg): return f"• {msg}"
        def multiline(self, lines, raw=False): return "\n".join(lines)
    ascii = _AsciiFallback()
    print(f"[rpc] ascii_helper unavailable ({e}) — using fallback shim")


DEFAULT_APP_ID = 1453358037506199743
PURPLESTREAM_URL = "https://www.twitch.tv/hadeontop"
ICON_PLACEHOLDER = "https://cdn.pfps.gg/pfps/20715-237182-lonely-girl-animated.gif"

TYPE_MAP = {
    "playing": 0, "streaming": 1, "listening": 2,
    "watching": 3, "competing": 5, "purplestream": 1
}

PLATFORM_ICON_KEYS = {
    "spotify": "spotify", "youtube": "youtube", "xbox": "xbox",
    "ps": "playstation", "playstation": "playstation", "ps4": "playstation",
    "ps5": "playstation", "crunchy": "crunchyroll", "crunchyroll": "crunchyroll",
    "twitch": "twitch", "vrchat": "vrchat", "meta_quest": "vrchat", "quest": "vrchat",
    "roblox": "roblox"
}

PLATFORM_PRESET_MAP = {
    "xbox": {"application_id": 622174530214821906, "platform": "xbox", "asset": "xbox"},
    "ps": {"application_id": 1470539864909943067, "platform": "ps5", "asset": "playstation"},
    "ps4": {"application_id": 1470539864909943067, "platform": "ps4", "asset": "playstation"},
    "ps5": {"application_id": 1470539864909943067, "platform": "ps5", "asset": "playstation"},
    "playstation": {"application_id": 1470539864909943067, "platform": "ps5", "asset": "playstation"},
    "crunchyroll": {"application_id": 981509069309354054, "platform": None, "asset": "crunchyroll"},
    "crunchy": {"application_id": 981509069309354054, "platform": None, "asset": "crunchyroll"},
    "youtube": {"application_id": 111299001912, "platform": None, "asset": "youtube"},
    "twitch": {"application_id": 111299001912, "platform": None, "asset": "twitch"},
    "vrchat": {"application_id": 1498387526501535835, "platform": "meta_quest", "asset": "vrchat"},
    "meta_quest": {"application_id": 1498387526501535835, "platform": "meta_quest", "asset": "vrchat"},
    "quest": {"application_id": 1498387526501535835, "platform": "meta_quest", "asset": "vrchat"},
    "meta": {"application_id": 1498387526501535835, "platform": "meta_quest", "asset": "vrchat"},
    "oculus": {"application_id": 1498387526501535835, "platform": "meta_quest", "asset": "vrchat"},
    "roblox": {"application_id": 363445589247131668, "platform": None, "asset": "roblox"},
}

INLINE_KEYS = ["name", "details", "state", "type", "timestamp", "platform",
               "large_image_text", "large_image", "small_image", "btn1", "btn2"]

# fields Discord requires on every activity payload — restored after load
REQUIRED_ACTIVITY_FIELDS = {
    "type": 0,
    "name": "Default",
    "application_id": DEFAULT_APP_ID,
    "assets": {},
    "instance": True,
}

# fields that make a Spotify presence actually render as Spotify
SPOTIFY_FIELDS_TO_KEEP = {
    "session_id", "sync_id", "party", "secrets", "metadata", "flags",
    "application_id", "assets", "type", "name", "details", "state",
    "timestamps", "instance",
}


class RPCCog(commands.Cog, name="Rich Presence"):
    def __init__(self, bot=None):
        # tolerant init: accept a client arg or pull it from cogs.state.
        # this is the exact fix for `RPCCog.__init__() missing 1 required positional argument: 'bot'`
        if bot is None and cstate is not None:
            bot = getattr(cstate, "CLIENT", None) or getattr(cstate, "MAIN_CLIENT", None)
        if bot is None:
            raise RuntimeError("RPCCog: no client available — pass bot or set cstate.CLIENT before init")

        self.bot = bot
        self.rpc_slots = [None] * 6
        self._slot_platform_preset = [None] * 6
        self._asset_cache = {}
        self._asset_urls = {}
        self.status_rotation_active = False
        self.emoji_rotation_active = False
        self._status_rotation_task = None
        self._emoji_rotation_task = None
        self.current_status = ""
        self.current_emoji = ""
        self._interceptor_active = False
        self._interceptor_ws = None          # ws instance we patched
        self._original_ws_send = None        # ref to the unpatched send
        self._clearing = False
        self._bg_tasks = []
        self._deferred_started = False
        try:
            self.bot.loop.create_task(self._deferred_start())
        except Exception:
            # fallback for environments without bot.loop
            asyncio.ensure_future(self._deferred_start())

    async def _deferred_start(self):
        """Wait for ready, then wire up background loops and the interceptor."""
        if self._deferred_started:
            return
        self._deferred_started = True
        try:
            await self.bot.wait_until_ready()
        except Exception:
            pass
        self._load_rpc_slots()
        self._load_asset_urls()
        try:
            await self._refresh_all_assets()
            await self._start_presence_interceptor()
        except Exception as e:
            print(f"[RPC] deferred start error: {e}")
        if any(a is not None for a in self.rpc_slots):
            try:
                await self._push()
                print("[RPC] Restored RPC slots on startup")
            except Exception as e:
                print(f"[RPC] restore push failed: {e}")
        # background refreshers
        self._bg_tasks.append(asyncio.create_task(self.auto_refresh_presence()))
        self._bg_tasks.append(asyncio.create_task(self.auto_refresh_cdn_assets()))

    async def cog_unload(self):
        """Clean up: stop interceptor, cancel background tasks, stop rotations."""
        await self._stop_presence_interceptor()
        self.status_rotation_active = False
        self.emoji_rotation_active = False
        for t in (self._status_rotation_task, self._emoji_rotation_task):
            if t and not t.done():
                t.cancel()
        for t in self._bg_tasks:
            if not t.done():
                t.cancel()
        self._bg_tasks.clear()

    # ── presence interceptor ──

    async def _start_presence_interceptor(self):
        """Attach or re-attach the op:3 patch to the CURRENT ws.

        A reconnect replaces self.bot.ws with a new object, so we can't trust
        the _interceptor_active flag alone — we also have to verify the ws
        identity we patched is still the live one.
        """
        if self._interceptor_active and self.bot.ws is self._interceptor_ws:
            return

        for _ in range(30):
            if self.bot.ws and hasattr(self.bot.ws, 'send'):
                break
            await asyncio.sleep(0.5)
        if not self.bot.ws:
            print("[RPC] Could not attach interceptor - no websocket")
            return

        # if we previously patched a different ws, drop that stale flag.
        # the old ws object is gone anyway; nothing to restore.
        self._interceptor_ws = self.bot.ws
        self._original_ws_send = self.bot.ws.send
        original = self._original_ws_send
        rpc_cog = self

        async def patched_send(data, *args, **kwargs):
            if isinstance(data, str) and '"op":3' in data:
                try:
                    payload = json.loads(data)
                    d = payload.get("d", {})
                    activities = d.get("activities", [])
                    if not activities and any(s is not None for s in rpc_cog.rpc_slots):
                        active = [s for s in rpc_cog.rpc_slots if s is not None]
                        if active and not rpc_cog._clearing:
                            d["activities"] = active
                            payload["d"] = d
                            data = json.dumps(payload)
                except Exception:
                    pass
            return await original(data, *args, **kwargs)

        try:
            self.bot.ws.send = patched_send
        except Exception as e:
            print(f"[RPC] failed to patch ws.send: {e}")
            return

        self._interceptor_active = True
        print("[RPC] Presence interceptor active")

    async def _stop_presence_interceptor(self):
        """Best-effort restore of the original ws.send if the ws is still alive."""
        if not self._interceptor_active:
            return
        try:
            if self.bot.ws is self._interceptor_ws and self._original_ws_send is not None:
                self.bot.ws.send = self._original_ws_send
        except Exception:
            pass
        self._interceptor_active = False
        self._interceptor_ws = None
        self._original_ws_send = None
        print("[RPC] Presence interceptor removed")

    @commands.Cog.listener()
    async def on_ready(self):
        """Fires on every ready; re-attach if the ws changed or we lost the patch."""
        try:
            if self.bot.ws is not self._interceptor_ws:
                # new ws — old patch is dead, force re-attach
                self._interceptor_active = False
            await self._start_presence_interceptor()
            if any(a is not None for a in self.rpc_slots):
                await self._push()
        except Exception as e:
            print(f"[RPC] on_ready re-attach failed: {e}")

    # ── persistence ──

    def _get_user_file(self, filename):
        """Per-user file path. Uses the given filename under data/."""
        if not self.bot.user:
            return Path(f"data/_unready_{filename}")
        uid = str(self.bot.user.id)
        return Path(f"data/{uid}_{filename}")

    def _save_rpc_slots(self):
        path = self._get_user_file("slots.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = []
        for slot in self.rpc_slots:
            if slot:
                clean = {k: v for k, v in slot.items() if k not in (
                    'instance', 'flags', 'session_id', 'sync_id', 'secrets',
                    'party', 'metadata')}
                data.append(clean)
            else:
                data.append(None)
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[RPC] save failed: {e}")

    def _load_rpc_slots(self):
        path = self._get_user_file("slots.json")
        if not path.exists():
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            for i, slot in enumerate(data[:6]):
                if slot:
                    # restore required fields
                    for k, v in REQUIRED_ACTIVITY_FIELDS.items():
                        slot.setdefault(k, v)
                    slot.setdefault("assets", {})
                    self.rpc_slots[i] = slot
            count = sum(1 for s in self.rpc_slots if s)
            if count:
                print(f"[RPC] Loaded {count} saved RPC slots for {self.bot.user}")
        except Exception as e:
            print(f"[RPC] Failed to load saved RPCs: {e}")

    def _save_asset_urls(self):
        path = self._get_user_file("asset_urls.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "w") as f:
                json.dump(self._asset_urls, f, indent=2)
        except Exception:
            pass

    def _load_asset_urls(self):
        path = self._get_user_file("asset_urls.json")
        if not path.exists():
            return
        try:
            with open(path, "r") as f:
                self._asset_urls = json.load(f)
        except Exception:
            self._asset_urls = {}

    def _ensure_slot(self, index: int):
        if self.rpc_slots[index] is None:
            self.rpc_slots[index] = {
                "type": 0, "name": "Default",
                "application_id": DEFAULT_APP_ID,
                "assets": {}, "instance": True
            }

    # ── CDN asset refresh ──

    async def _refresh_cdn_url(self, url: str) -> str:
        if not url or not url.startswith("mp:"):
            return url
        try:
            headers = {'Authorization': self.bot.http.token,
                       'Content-Type': 'application/json'}
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://discord.com/api/v9/attachments/refresh-urls",
                    json={"attachment_urls": [url]}, headers=headers
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        refreshed = data.get("refreshed_urls", [])
                        if refreshed and refreshed[0].get("refreshed"):
                            return refreshed[0]["refreshed"]
        except Exception:
            pass
        return url

    async def _refresh_all_assets(self):
        try:
            for slot in self.rpc_slots:
                if slot and "assets" in slot:
                    assets = slot["assets"]
                    if assets.get("large_image", "").startswith("mp:"):
                        assets["large_image"] = await self._refresh_cdn_url(assets["large_image"])
                    if assets.get("small_image", "").startswith("mp:"):
                        assets["small_image"] = await self._refresh_cdn_url(assets["small_image"])
            if any(a is not None for a in self.rpc_slots):
                await self._push()
        except Exception as e:
            print(f"[RPC] refresh_all_assets error: {e}")

    async def auto_refresh_cdn_assets(self):
        try:
            await self.bot.wait_until_ready()
        except Exception:
            pass
        while not self.bot.is_closed():
            await asyncio.sleep(1500)
            await self._refresh_all_assets()

    # ── presence push ──

    async def _push(self):
        active = [a for a in self.rpc_slots if a is not None]
        if not active:
            return
        if not self.bot.ws:
            print("[RPC] push skipped — no websocket")
            return
        current_status = str(self.bot.status) if hasattr(self.bot, 'status') else "online"
        payload = {"op": 3, "d": {"since": 0, "activities": active,
                                   "status": current_status, "afk": False}}
        try:
            await self.bot.ws.send(json.dumps(payload))
            self._save_rpc_slots()
        except Exception as e:
            print(f"[RPC] push failed: {e}")

    async def auto_refresh_presence(self):
        try:
            await self.bot.wait_until_ready()
        except Exception:
            pass
        while not self.bot.is_closed():
            await asyncio.sleep(1800)
            if any(a is not None for a in self.rpc_slots):
                await self._push()

    async def apply_activities(self):
        if any(a is not None for a in self.rpc_slots):
            await self._push()

    # ── parsing helpers ──

    def _parse_timestamp_value(self, value: str) -> float:
        value = value.strip()
        if ":" in value:
            parts = value.split(":")
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            raise ValueError("bad timestamp format")
        return float(value) * 3600

    def _set_timestamp(self, slot: int, value: str):
        now = time.time()
        total_seconds = self._parse_timestamp_value(value)
        self.rpc_slots[slot]["timestamps"] = {
            "start": int(now * 1000),
            "end": int((now + total_seconds) * 1000),
        }

    def _apply_platform_preset(self, slot: int, preset_key: str) -> bool:
        off_keys = {"off", "none", "clear", "normal"}
        if preset_key in off_keys:
            self._slot_platform_preset[slot] = None
            self._ensure_slot(slot)
            self.rpc_slots[slot]["application_id"] = DEFAULT_APP_ID
            self.rpc_slots[slot].pop("platform", None)
            return True
        preset = PLATFORM_PRESET_MAP.get(preset_key)
        if not preset:
            return False
        self._ensure_slot(slot)
        self._slot_platform_preset[slot] = preset_key
        self.rpc_slots[slot]["application_id"] = preset["application_id"]
        if preset["platform"]:
            self.rpc_slots[slot]["platform"] = preset["platform"]
        else:
            self.rpc_slots[slot].pop("platform", None)
        self.rpc_slots[slot].setdefault("assets", {})
        if not self.rpc_slots[slot]["assets"].get("large_image"):
            self.rpc_slots[slot]["assets"]["large_image"] = preset["asset"]
        return True

    def _parse_inline(self, args: str) -> dict:
        result = {}
        words = args.split()
        positions = [(w.lower(), i) for i, w in enumerate(words)
                     if w.lower() in INLINE_KEYS]
        for idx, (key, pos) in enumerate(positions):
            start = pos + 1
            end = positions[idx + 1][1] if idx + 1 < len(positions) else len(words)
            value = " ".join(words[start:end]).strip()
            if value:
                result[key] = value
        return result

    async def _apply_inline(self, slot: int, parsed: dict):
        self._ensure_slot(slot)
        act = self.rpc_slots[slot]
        if "name" in parsed: act["name"] = parsed["name"]
        if "details" in parsed: act["details"] = parsed["details"]
        if "state" in parsed: act["state"] = parsed["state"]
        if "type" in parsed:
            t = parsed["type"].lower()
            if t in TYPE_MAP:
                act["type"] = TYPE_MAP[t]
                if t == "purplestream":
                    act["url"] = PURPLESTREAM_URL
                elif "url" in act and t not in ("streaming", "purplestream"):
                    del act["url"]
        if "timestamp" in parsed:
            val = parsed["timestamp"]
            if val.lower() == "clear":
                act.pop("timestamps", None)
            else:
                try:
                    self._set_timestamp(slot, val)
                except Exception:
                    pass
        if "platform" in parsed:
            self._apply_platform_preset(slot, parsed["platform"].lower())
        if "large_image_text" in parsed:
            act.setdefault("assets", {})["large_text"] = parsed["large_image_text"]
        if "large_image" in parsed:
            key = await self.upload_asset(parsed["large_image"])
            if key:
                act.setdefault("assets", {})["large_image"] = key
                self._asset_urls[key] = parsed["large_image"]
                self._save_asset_urls()
        if "small_image" in parsed:
            key = await self.upload_asset(parsed["small_image"])
            if key:
                act.setdefault("assets", {})["small_image"] = key
                self._asset_urls[key] = parsed["small_image"]
                self._save_asset_urls()
        if "btn1" in parsed:
            parts = parsed["btn1"].split()
            if len(parts) >= 2:
                btns = act.setdefault("buttons", [])
                entry = {"label": " ".join(parts[:-1]), "url": parts[-1]}
                if not btns:
                    btns.append(entry)
                else:
                    btns[0] = entry
        if "btn2" in parsed:
            parts = parsed["btn2"].split()
            if len(parts) >= 2:
                btns = act.setdefault("buttons", [])
                while len(btns) < 2:
                    btns.append(None)
                btns[1] = {"label": " ".join(parts[:-1]), "url": parts[-1]}
                act["buttons"] = [b for b in btns if b]

    async def upload_asset(self, image_url: str):
        if not image_url:
            return None
        if image_url in self._asset_cache:
            return self._asset_cache[image_url]
        # already a Discord CDN URL
        discord_cdn_pattern = (r"https?://(?:cdn\.discordapp\.com|media\.discordapp\.net)"
                                r"/attachments/(\d+)/(\d+)/(.+)")
        match = re.search(discord_cdn_pattern, image_url)
        if match:
            channel_id, attachment_id, filename = match.groups()
            key = f"mp:attachments/{channel_id}/{attachment_id}/{filename}"
            self._asset_cache[image_url] = key
            self._asset_urls[key] = image_url
            return key
        # need to upload
        if not self.bot.user:
            print("[RPC] upload_asset: bot.user not ready")
            return None
        try:
            self_dm = self.bot.user.dm_channel
            if self_dm is None:
                self_dm = await self.bot.user.create_dm()
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as r:
                    if r.status != 200:
                        return None
                    image_bytes = await r.read()
            filename = image_url.split('/')[-1].split('?')[0]
            if '.' not in filename or len(filename) > 50:
                filename = "asset.png"
            message = await self_dm.send(
                file=discord.File(io.BytesIO(image_bytes), filename=filename))
            if message.attachments:
                new_url = message.attachments[0].url
                new_match = re.search(discord_cdn_pattern, new_url)
                if new_match:
                    cid, aid, fname = new_match.groups()
                    key = f"mp:attachments/{cid}/{aid}/{fname}"
                    self._asset_cache[image_url] = key
                    self._asset_urls[key] = image_url
                    return key
        except Exception as e:
            print(f"[RPC] upload_asset failed: {e}")
        return None

    # ── presence builders ──

    async def build_spotify(self, parts: list):
        if len(parts) < 2:
            return None
        song, artist = parts[0], parts[1]
        album = parts[2] if len(parts) > 2 else song
        duration = float(parts[3]) if len(parts) > 3 else 3.5
        position = float(parts[4]) if len(parts) > 4 else 0.0
        current_ms = int(position * 60 * 1000)
        total_ms = int(duration * 60 * 1000)
        now = int(time.time() * 1000)
        sid = "09xhawlPUifhftf8zuie7w"
        return {
            "type": 2, "name": "Spotify", "details": song, "state": artist,
            "timestamps": {"start": now - current_ms,
                           "end": (now - current_ms) + (total_ms - current_ms)},
            "application_id": "3201606009684", "sync_id": sid,
            "session_id": f"spotify:{sid}",
            "party": {"id": f"spotify:{sid}", "size": [1, 1]},
            "secrets": {"join": f"spotify:{sid}", "spectate": f"spotify:{sid}",
                        "match": f"spotify:{sid}"},
            "instance": True, "flags": 48,
            "metadata": {"context_uri": f"spotify:album:{sid}", "album_id": sid,
                         "artist_ids": ["0HPG2EIdGCP6gjXW0KzrJq", "0qc4bfxcwRFZfevTck4fOi"],
                         "track_id": sid},
            "assets": {"large_image": "spotify", "large_text": f"{album} on Spotify"}
        }

    async def build_youtube(self, parts: list):
        if len(parts) < 2:
            return None
        video, channel = parts[0], parts[1]
        duration = float(parts[2]) if len(parts) > 2 else 5.0
        position = float(parts[3]) if len(parts) > 3 else 0.0
        current_ms = int(position * 60 * 1000)
        total_ms = int(duration * 60 * 1000)
        now = int(time.time() * 1000)
        return {
            "type": 3, "name": "YouTube", "details": video, "state": channel,
            "timestamps": {"start": now - current_ms,
                           "end": (now - current_ms) + (total_ms - current_ms)},
            "application_id": "111299001912",
            "assets": {"large_image": "youtube", "large_text": f"{video} on YouTube"}
        }

    async def build_xbox(self, parts: list):
        game = (parts[0] if parts else "Xbox")[:128]
        activity = {
            "type": 0, "name": game, "application_id": "622174530214821906",
            "platform": "xbox",
            "timestamps": {"start": int(time.time() * 1000)},
            "assets": {"large_image": "xbox", "large_text": game[:32]}
        }
        if len(parts) > 1 and parts[1]:
            activity["details"] = parts[1][:128]
        if len(parts) > 2 and parts[2]:
            activity["state"] = parts[2][:128]
        return activity

    async def build_playstation(self, parts: list, ps4: bool = False):
        game = (parts[0] if parts else "PlayStation")[:128]
        activity = {
            "type": 0, "name": game, "application_id": "1470539864909943067",
            "platform": "ps4" if ps4 else "ps5",
            "timestamps": {"start": int(time.time() * 1000)},
            "assets": {"large_image": "playstation", "large_text": game[:32]},
        }
        if len(parts) > 1 and parts[1]:
            activity["details"] = parts[1][:128]
        if len(parts) > 2 and parts[2]:
            activity["state"] = parts[2][:128]
        return activity

    async def build_crunchyroll(self, parts: list):
        anime = (parts[0] if parts else "Anime")[:128]
        episode = (parts[1] if len(parts) > 1 else "Episode")[:128]
        elapsed = float(parts[2]) if len(parts) > 2 else 0.0
        total = float(parts[3]) if len(parts) > 3 else 24.0
        current_ms = int(elapsed * 60 * 1000)
        total_ms = int(total * 60 * 1000)
        now = int(time.time() * 1000)
        return {
            "type": 3, "name": "Crunchyroll", "application_id": "981509069309354054",
            "details": anime, "state": episode,
            "timestamps": {"start": now - current_ms,
                           "end": (now - current_ms) + (total_ms - current_ms)},
            "assets": {"large_image": "crunchyroll", "large_text": anime[:32]}
        }

    async def build_vrchat(self, parts: list, image_url: str = None):
        state = parts[0] if parts else "Exploring VRChat"
        world = parts[1] if len(parts) > 1 else "VRChat"
        now = int(time.time() * 1000)
        large_image = None
        if image_url:
            key = await self.upload_asset(image_url)
            large_image = key if key else image_url
        else:
            large_image = ("mp:external/jxAa_-ahC78ilas-ifzE8DX6RyNTI_FV-p2F7HzGhfs/"
                            "https/www.oculus.com/rich_presence/image/1856672347794301/")
        return {
            "type": 0, "name": "VRChat", "application_id": "1498387526501535835",
            "platform": "meta_quest", "state": state,
            "timestamps": {"start": now},
            "assets": {"large_image": large_image, "large_text": world},
            "instance": True
        }

    async def build_roblox(self, parts: list):
        """Roblox presence — game name, optional details/state, elapsed timer."""
        game = (parts[0] if parts else "Roblox")[:128]
        now = int(time.time() * 1000)
        activity = {
            "type": 0,
            "name": "Roblox",
            "application_id": "363445589247131668",
            "details": game,
            "timestamps": {"start": now},
            "assets": {"large_image": "roblox", "large_text": game[:128]},
            "instance": True,
        }
        if len(parts) > 1 and parts[1]:
            activity["state"] = parts[1][:128]
        if len(parts) > 2 and parts[2]:
            activity["assets"]["small_image"] = "roblox_small"
            activity["assets"]["small_text"] = parts[2][:128]
        return activity

    # ── RPC slot groups (1..6) ──

    @commands.group(name="rpc1", invoke_without_command=True)
    async def rpc1(self, ctx, *, args: str = None):
        if not args:
            await ctx.send(ascii.error("Usage: .rpc1 name <text> | ...")); return
        parsed = self._parse_inline(args)
        if parsed:
            await self._apply_inline(0, parsed)
            await self.apply_activities()
            await ctx.send(ascii.success("RPC1 updated"))

    @rpc1.command(name="name")
    async def rpc1_name(self, ctx, *, name: str):
        self._ensure_slot(0); self.rpc_slots[0]["name"] = name
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC1 name: {name}"))

    @rpc1.command(name="details")
    async def rpc1_details(self, ctx, *, details: str):
        self._ensure_slot(0); self.rpc_slots[0]["details"] = details
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC1 details: {details}"))

    @rpc1.command(name="state")
    async def rpc1_state(self, ctx, *, state: str):
        self._ensure_slot(0); self.rpc_slots[0]["state"] = state
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC1 state: {state}"))

    @rpc1.command(name="type")
    async def rpc1_type(self, ctx, activity_type: str):
        t = activity_type.lower()
        if t not in TYPE_MAP:
            await ctx.send(ascii.error("Invalid type")); return
        self._ensure_slot(0); self.rpc_slots[0]["type"] = TYPE_MAP[t]
        if t == "purplestream":
            self.rpc_slots[0]["url"] = PURPLESTREAM_URL
        elif "url" in self.rpc_slots[0] and t not in ("streaming", "purplestream"):
            del self.rpc_slots[0]["url"]
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC1 type: {activity_type}"))

    @rpc1.command(name="platform")
    async def rpc1_platform(self, ctx, preset: str):
        if self._apply_platform_preset(0, preset.lower()):
            await self.apply_activities(); await ctx.send(ascii.success(f"RPC1 platform: {preset}"))
        else:
            await ctx.send(ascii.error("Unknown platform"))

    @rpc1.command(name="large_image")
    async def rpc1_large_image(self, ctx, url: str):
        key = await self.upload_asset(url)
        if key:
            self._ensure_slot(0)
            self.rpc_slots[0].setdefault("assets", {})["large_image"] = key
            await self.apply_activities(); await ctx.send(ascii.success("RPC1 large image set"))

    @rpc1.command(name="small_image")
    async def rpc1_small_image(self, ctx, url: str):
        key = await self.upload_asset(url)
        if key:
            self._ensure_slot(0)
            self.rpc_slots[0].setdefault("assets", {})["small_image"] = key
            await self.apply_activities(); await ctx.send(ascii.success("RPC1 small image set"))

    @rpc1.command(name="timestamp")
    async def rpc1_timestamp(self, ctx, value: str):
        self._ensure_slot(0)
        if value.lower() == "clear":
            self.rpc_slots[0].pop("timestamps", None)
            await ctx.send(ascii.info("RPC1 timestamp cleared"))
        else:
            try:
                self._set_timestamp(0, value)
                await self.apply_activities()
                await ctx.send(ascii.success(f"RPC1 timestamp: {value}"))
            except Exception:
                await ctx.send(ascii.error("Use format: 3600 or 1:00:00"))

    @rpc1.command(name="btn1")
    async def rpc1_btn1(self, ctx, label: str, url: str):
        self._ensure_slot(0)
        btns = self.rpc_slots[0].setdefault("buttons", [])
        entry = {"label": label, "url": url}
        if not btns: btns.append(entry)
        else: btns[0] = entry
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC1 btn1: {label}"))

    @rpc1.command(name="btn2")
    async def rpc1_btn2(self, ctx, label: str, url: str):
        self._ensure_slot(0)
        btns = self.rpc_slots[0].setdefault("buttons", [])
        while len(btns) < 2: btns.append(None)
        btns[1] = {"label": label, "url": url}
        self.rpc_slots[0]["buttons"] = [b for b in btns if b]
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC1 btn2: {label}"))

    @rpc1.command(name="spotify")
    async def rpc1_spotify(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Default", "Unknown"]
        if len(parts) < 2: parts.append("Unknown")
        activity = await self.build_spotify(parts)
        if not activity:
            await ctx.send(ascii.error("Format: Song - Artist")); return
        self.rpc_slots[0] = activity
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC1 Spotify: {parts[0]}"))

    @rpc1.command(name="youtube")
    async def rpc1_youtube(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Default Video", "Default Channel"]
        if len(parts) < 2: parts.append("Default Channel")
        activity = await self.build_youtube(parts)
        if not activity:
            await ctx.send(ascii.error("Format: Video - Channel")); return
        self.rpc_slots[0] = activity
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC1 YouTube: {parts[0]}"))

    @rpc1.command(name="xbox")
    async def rpc1_xbox(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Xbox"]
        self.rpc_slots[0] = await self.build_xbox(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC1 Xbox: {parts[0]}"))

    @rpc1.command(name="ps")
    async def rpc1_ps(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["PlayStation"]
        self.rpc_slots[0] = await self.build_playstation(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC1 PS: {parts[0]}"))

    @rpc1.command(name="ps4")
    async def rpc1_ps4(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["PS4"]
        self.rpc_slots[0] = await self.build_playstation(parts, ps4=True)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC1 PS4: {parts[0]}"))

    @rpc1.command(name="crunchy")
    async def rpc1_crunchy(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Crunchyroll"]
        self.rpc_slots[0] = await self.build_crunchyroll(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC1 Crunchyroll: {parts[0]}"))

    @rpc1.command(name="roblox")
    async def rpc1_roblox(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Roblox"]
        self.rpc_slots[0] = await self.build_roblox(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC1 Roblox: {parts[0]}"))

    @rpc1.command(name="clear")
    async def rpc1_clear(self, ctx):
        self.rpc_slots[0] = None
        await self.apply_activities(); await ctx.send(ascii.info("RPC1 cleared"))

    # RPC2
    @commands.group(name="rpc2", invoke_without_command=True)
    async def rpc2(self, ctx, *, args: str = None):
        if not args:
            await ctx.send(ascii.error("Usage: .rpc2 name <text> | ...")); return
        parsed = self._parse_inline(args)
        if parsed:
            await self._apply_inline(1, parsed)
            await self.apply_activities()
            await ctx.send(ascii.success("RPC2 updated"))

    @rpc2.command(name="name")
    async def rpc2_name(self, ctx, *, name: str):
        self._ensure_slot(1); self.rpc_slots[1]["name"] = name
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC2 name: {name}"))

    @rpc2.command(name="details")
    async def rpc2_details(self, ctx, *, details: str):
        self._ensure_slot(1); self.rpc_slots[1]["details"] = details
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC2 details: {details}"))

    @rpc2.command(name="state")
    async def rpc2_state(self, ctx, *, state: str):
        self._ensure_slot(1); self.rpc_slots[1]["state"] = state
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC2 state: {state}"))

    @rpc2.command(name="type")
    async def rpc2_type(self, ctx, activity_type: str):
        t = activity_type.lower()
        if t not in TYPE_MAP:
            await ctx.send(ascii.error("Invalid type")); return
        self._ensure_slot(1); self.rpc_slots[1]["type"] = TYPE_MAP[t]
        if t == "purplestream":
            self.rpc_slots[1]["url"] = PURPLESTREAM_URL
        elif "url" in self.rpc_slots[1] and t not in ("streaming", "purplestream"):
            del self.rpc_slots[1]["url"]
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC2 type: {activity_type}"))

    @rpc2.command(name="platform")
    async def rpc2_platform(self, ctx, preset: str):
        if self._apply_platform_preset(1, preset.lower()):
            await self.apply_activities(); await ctx.send(ascii.success(f"RPC2 platform: {preset}"))
        else:
            await ctx.send(ascii.error("Unknown platform"))

    @rpc2.command(name="large_image")
    async def rpc2_large_image(self, ctx, url: str):
        key = await self.upload_asset(url)
        if key:
            self._ensure_slot(1)
            self.rpc_slots[1].setdefault("assets", {})["large_image"] = key
            await self.apply_activities(); await ctx.send(ascii.success("RPC2 large image set"))

    @rpc2.command(name="small_image")
    async def rpc2_small_image(self, ctx, url: str):
        key = await self.upload_asset(url)
        if key:
            self._ensure_slot(1)
            self.rpc_slots[1].setdefault("assets", {})["small_image"] = key
            await self.apply_activities(); await ctx.send(ascii.success("RPC2 small image set"))

    @rpc2.command(name="timestamp")
    async def rpc2_timestamp(self, ctx, value: str):
        self._ensure_slot(1)
        if value.lower() == "clear":
            self.rpc_slots[1].pop("timestamps", None)
            await ctx.send(ascii.info("RPC2 timestamp cleared"))
        else:
            try:
                self._set_timestamp(1, value)
                await self.apply_activities()
                await ctx.send(ascii.success(f"RPC2 timestamp: {value}"))
            except Exception:
                await ctx.send(ascii.error("Use format: 3600 or 1:00:00"))

    @rpc2.command(name="btn1")
    async def rpc2_btn1(self, ctx, label: str, url: str):
        self._ensure_slot(1)
        btns = self.rpc_slots[1].setdefault("buttons", [])
        entry = {"label": label, "url": url}
        if not btns: btns.append(entry)
        else: btns[0] = entry
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC2 btn1: {label}"))

    @rpc2.command(name="btn2")
    async def rpc2_btn2(self, ctx, label: str, url: str):
        self._ensure_slot(1)
        btns = self.rpc_slots[1].setdefault("buttons", [])
        while len(btns) < 2: btns.append(None)
        btns[1] = {"label": label, "url": url}
        self.rpc_slots[1]["buttons"] = [b for b in btns if b]
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC2 btn2: {label}"))

    @rpc2.command(name="spotify")
    async def rpc2_spotify(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Default", "Unknown"]
        if len(parts) < 2: parts.append("Unknown")
        activity = await self.build_spotify(parts)
        if not activity:
            await ctx.send(ascii.error("Format: Song - Artist")); return
        self.rpc_slots[1] = activity
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC2 Spotify: {parts[0]}"))

    @rpc2.command(name="youtube")
    async def rpc2_youtube(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Default Video", "Default Channel"]
        if len(parts) < 2: parts.append("Default Channel")
        activity = await self.build_youtube(parts)
        if not activity:
            await ctx.send(ascii.error("Format: Video - Channel")); return
        self.rpc_slots[1] = activity
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC2 YouTube: {parts[0]}"))

    @rpc2.command(name="xbox")
    async def rpc2_xbox(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Xbox"]
        self.rpc_slots[1] = await self.build_xbox(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC2 Xbox: {parts[0]}"))

    @rpc2.command(name="ps")
    async def rpc2_ps(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["PlayStation"]
        self.rpc_slots[1] = await self.build_playstation(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC2 PS: {parts[0]}"))

    @rpc2.command(name="ps4")
    async def rpc2_ps4(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["PS4"]
        self.rpc_slots[1] = await self.build_playstation(parts, ps4=True)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC2 PS4: {parts[0]}"))

    @rpc2.command(name="crunchy")
    async def rpc2_crunchy(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Crunchyroll"]
        self.rpc_slots[1] = await self.build_crunchyroll(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC2 Crunchyroll: {parts[0]}"))

    @rpc2.command(name="roblox")
    async def rpc2_roblox(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Roblox"]
        self.rpc_slots[1] = await self.build_roblox(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC2 Roblox: {parts[0]}"))

    @rpc2.command(name="clear")
    async def rpc2_clear(self, ctx):
        self.rpc_slots[1] = None
        await self.apply_activities(); await ctx.send(ascii.info("RPC2 cleared"))

    # RPC3
    @commands.group(name="rpc3", invoke_without_command=True)
    async def rpc3(self, ctx, *, args: str = None):
        if not args:
            await ctx.send(ascii.error("Usage: .rpc3 name <text> | ...")); return
        parsed = self._parse_inline(args)
        if parsed:
            await self._apply_inline(2, parsed)
            await self.apply_activities()
            await ctx.send(ascii.success("RPC3 updated"))

    @rpc3.command(name="name")
    async def rpc3_name(self, ctx, *, name: str):
        self._ensure_slot(2); self.rpc_slots[2]["name"] = name
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC3 name: {name}"))

    @rpc3.command(name="details")
    async def rpc3_details(self, ctx, *, details: str):
        self._ensure_slot(2); self.rpc_slots[2]["details"] = details
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC3 details: {details}"))

    @rpc3.command(name="state")
    async def rpc3_state(self, ctx, *, state: str):
        self._ensure_slot(2); self.rpc_slots[2]["state"] = state
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC3 state: {state}"))

    @rpc3.command(name="type")
    async def rpc3_type(self, ctx, activity_type: str):
        t = activity_type.lower()
        if t not in TYPE_MAP:
            await ctx.send(ascii.error("Invalid type")); return
        self._ensure_slot(2); self.rpc_slots[2]["type"] = TYPE_MAP[t]
        if t == "purplestream":
            self.rpc_slots[2]["url"] = PURPLESTREAM_URL
        elif "url" in self.rpc_slots[2] and t not in ("streaming", "purplestream"):
            del self.rpc_slots[2]["url"]
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC3 type: {activity_type}"))

    @rpc3.command(name="platform")
    async def rpc3_platform(self, ctx, preset: str):
        if self._apply_platform_preset(2, preset.lower()):
            await self.apply_activities(); await ctx.send(ascii.success(f"RPC3 platform: {preset}"))
        else:
            await ctx.send(ascii.error("Unknown platform"))

    @rpc3.command(name="large_image")
    async def rpc3_large_image(self, ctx, url: str):
        key = await self.upload_asset(url)
        if key:
            self._ensure_slot(2)
            self.rpc_slots[2].setdefault("assets", {})["large_image"] = key
            await self.apply_activities(); await ctx.send(ascii.success("RPC3 large image set"))

    @rpc3.command(name="small_image")
    async def rpc3_small_image(self, ctx, url: str):
        key = await self.upload_asset(url)
        if key:
            self._ensure_slot(2)
            self.rpc_slots[2].setdefault("assets", {})["small_image"] = key
            await self.apply_activities(); await ctx.send(ascii.success("RPC3 small image set"))

    @rpc3.command(name="timestamp")
    async def rpc3_timestamp(self, ctx, value: str):
        self._ensure_slot(2)
        if value.lower() == "clear":
            self.rpc_slots[2].pop("timestamps", None)
            await ctx.send(ascii.info("RPC3 timestamp cleared"))
        else:
            try:
                self._set_timestamp(2, value)
                await self.apply_activities()
                await ctx.send(ascii.success(f"RPC3 timestamp: {value}"))
            except Exception:
                await ctx.send(ascii.error("Use format: 3600 or 1:00:00"))

    @rpc3.command(name="btn1")
    async def rpc3_btn1(self, ctx, label: str, url: str):
        self._ensure_slot(2)
        btns = self.rpc_slots[2].setdefault("buttons", [])
        entry = {"label": label, "url": url}
        if not btns: btns.append(entry)
        else: btns[0] = entry
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC3 btn1: {label}"))

    @rpc3.command(name="btn2")
    async def rpc3_btn2(self, ctx, label: str, url: str):
        self._ensure_slot(2)
        btns = self.rpc_slots[2].setdefault("buttons", [])
        while len(btns) < 2: btns.append(None)
        btns[1] = {"label": label, "url": url}
        self.rpc_slots[2]["buttons"] = [b for b in btns if b]
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC3 btn2: {label}"))

    @rpc3.command(name="spotify")
    async def rpc3_spotify(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Default", "Unknown"]
        if len(parts) < 2: parts.append("Unknown")
        activity = await self.build_spotify(parts)
        if not activity:
            await ctx.send(ascii.error("Format: Song - Artist")); return
        self.rpc_slots[2] = activity
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC3 Spotify: {parts[0]}"))

    @rpc3.command(name="youtube")
    async def rpc3_youtube(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Default Video", "Default Channel"]
        if len(parts) < 2: parts.append("Default Channel")
        activity = await self.build_youtube(parts)
        if not activity:
            await ctx.send(ascii.error("Format: Video - Channel")); return
        self.rpc_slots[2] = activity
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC3 YouTube: {parts[0]}"))

    @rpc3.command(name="xbox")
    async def rpc3_xbox(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Xbox"]
        self.rpc_slots[2] = await self.build_xbox(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC3 Xbox: {parts[0]}"))

    @rpc3.command(name="ps")
    async def rpc3_ps(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["PlayStation"]
        self.rpc_slots[2] = await self.build_playstation(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC3 PS: {parts[0]}"))

    @rpc3.command(name="ps4")
    async def rpc3_ps4(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["PS4"]
        self.rpc_slots[2] = await self.build_playstation(parts, ps4=True)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC3 PS4: {parts[0]}"))

    @rpc3.command(name="crunchy")
    async def rpc3_crunchy(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Crunchyroll"]
        self.rpc_slots[2] = await self.build_crunchyroll(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC3 Crunchyroll: {parts[0]}"))

    @rpc3.command(name="roblox")
    async def rpc3_roblox(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Roblox"]
        self.rpc_slots[2] = await self.build_roblox(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC3 Roblox: {parts[0]}"))

    @rpc3.command(name="clear")
    async def rpc3_clear(self, ctx):
        self.rpc_slots[2] = None
        await self.apply_activities(); await ctx.send(ascii.info("RPC3 cleared"))

    # RPC4
    @commands.group(name="rpc4", invoke_without_command=True)
    async def rpc4(self, ctx, *, args: str = None):
        if not args:
            await ctx.send(ascii.error("Usage: .rpc4 name <text> | ...")); return
        parsed = self._parse_inline(args)
        if parsed:
            await self._apply_inline(3, parsed)
            await self.apply_activities()
            await ctx.send(ascii.success("RPC4 updated"))

    @rpc4.command(name="name")
    async def rpc4_name(self, ctx, *, name: str):
        self._ensure_slot(3); self.rpc_slots[3]["name"] = name
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC4 name: {name}"))

    @rpc4.command(name="details")
    async def rpc4_details(self, ctx, *, details: str):
        self._ensure_slot(3); self.rpc_slots[3]["details"] = details
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC4 details: {details}"))

    @rpc4.command(name="state")
    async def rpc4_state(self, ctx, *, state: str):
        self._ensure_slot(3); self.rpc_slots[3]["state"] = state
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC4 state: {state}"))

    @rpc4.command(name="type")
    async def rpc4_type(self, ctx, activity_type: str):
        t = activity_type.lower()
        if t not in TYPE_MAP:
            await ctx.send(ascii.error("Invalid type")); return
        self._ensure_slot(3); self.rpc_slots[3]["type"] = TYPE_MAP[t]
        if t == "purplestream":
            self.rpc_slots[3]["url"] = PURPLESTREAM_URL
        elif "url" in self.rpc_slots[3] and t not in ("streaming", "purplestream"):
            del self.rpc_slots[3]["url"]
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC4 type: {activity_type}"))

    @rpc4.command(name="platform")
    async def rpc4_platform(self, ctx, preset: str):
        if self._apply_platform_preset(3, preset.lower()):
            await self.apply_activities(); await ctx.send(ascii.success(f"RPC4 platform: {preset}"))
        else:
            await ctx.send(ascii.error("Unknown platform"))

    @rpc4.command(name="large_image")
    async def rpc4_large_image(self, ctx, url: str):
        key = await self.upload_asset(url)
        if key:
            self._ensure_slot(3)
            self.rpc_slots[3].setdefault("assets", {})["large_image"] = key
            await self.apply_activities(); await ctx.send(ascii.success("RPC4 large image set"))

    @rpc4.command(name="small_image")
    async def rpc4_small_image(self, ctx, url: str):
        key = await self.upload_asset(url)
        if key:
            self._ensure_slot(3)
            self.rpc_slots[3].setdefault("assets", {})["small_image"] = key
            await self.apply_activities(); await ctx.send(ascii.success("RPC4 small image set"))

    @rpc4.command(name="timestamp")
    async def rpc4_timestamp(self, ctx, value: str):
        self._ensure_slot(3)
        if value.lower() == "clear":
            self.rpc_slots[3].pop("timestamps", None)
            await ctx.send(ascii.info("RPC4 timestamp cleared"))
        else:
            try:
                self._set_timestamp(3, value)
                await self.apply_activities()
                await ctx.send(ascii.success(f"RPC4 timestamp: {value}"))
            except Exception:
                await ctx.send(ascii.error("Use format: 3600 or 1:00:00"))

    @rpc4.command(name="btn1")
    async def rpc4_btn1(self, ctx, label: str, url: str):
        self._ensure_slot(3)
        btns = self.rpc_slots[3].setdefault("buttons", [])
        entry = {"label": label, "url": url}
        if not btns: btns.append(entry)
        else: btns[0] = entry
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC4 btn1: {label}"))

    @rpc4.command(name="btn2")
    async def rpc4_btn2(self, ctx, label: str, url: str):
        self._ensure_slot(3)
        btns = self.rpc_slots[3].setdefault("buttons", [])
        while len(btns) < 2: btns.append(None)
        btns[1] = {"label": label, "url": url}
        self.rpc_slots[3]["buttons"] = [b for b in btns if b]
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC4 btn2: {label}"))

    @rpc4.command(name="spotify")
    async def rpc4_spotify(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Default", "Unknown"]
        if len(parts) < 2: parts.append("Unknown")
        activity = await self.build_spotify(parts)
        if not activity:
            await ctx.send(ascii.error("Format: Song - Artist")); return
        self.rpc_slots[3] = activity
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC4 Spotify: {parts[0]}"))

    @rpc4.command(name="youtube")
    async def rpc4_youtube(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Default Video", "Default Channel"]
        if len(parts) < 2: parts.append("Default Channel")
        activity = await self.build_youtube(parts)
        if not activity:
            await ctx.send(ascii.error("Format: Video - Channel")); return
        self.rpc_slots[3] = activity
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC4 YouTube: {parts[0]}"))

    @rpc4.command(name="xbox")
    async def rpc4_xbox(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Xbox"]
        self.rpc_slots[3] = await self.build_xbox(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC4 Xbox: {parts[0]}"))

    @rpc4.command(name="ps")
    async def rpc4_ps(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["PlayStation"]
        self.rpc_slots[3] = await self.build_playstation(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC4 PS: {parts[0]}"))

    @rpc4.command(name="ps4")
    async def rpc4_ps4(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["PS4"]
        self.rpc_slots[3] = await self.build_playstation(parts, ps4=True)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC4 PS4: {parts[0]}"))

    @rpc4.command(name="crunchy")
    async def rpc4_crunchy(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Crunchyroll"]
        self.rpc_slots[3] = await self.build_crunchyroll(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC4 Crunchyroll: {parts[0]}"))

    @rpc4.command(name="roblox")
    async def rpc4_roblox(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Roblox"]
        self.rpc_slots[3] = await self.build_roblox(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC4 Roblox: {parts[0]}"))

    @rpc4.command(name="clear")
    async def rpc4_clear(self, ctx):
        self.rpc_slots[3] = None
        await self.apply_activities(); await ctx.send(ascii.info("RPC4 cleared"))

    # RPC5
    @commands.group(name="rpc5", invoke_without_command=True)
    async def rpc5(self, ctx, *, args: str = None):
        if not args:
            await ctx.send(ascii.error("Usage: .rpc5 name <text> | ...")); return
        parsed = self._parse_inline(args)
        if parsed:
            await self._apply_inline(4, parsed)
            await self.apply_activities()
            await ctx.send(ascii.success("RPC5 updated"))

    @rpc5.command(name="name")
    async def rpc5_name(self, ctx, *, name: str):
        self._ensure_slot(4); self.rpc_slots[4]["name"] = name
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC5 name: {name}"))

    @rpc5.command(name="details")
    async def rpc5_details(self, ctx, *, details: str):
        self._ensure_slot(4); self.rpc_slots[4]["details"] = details
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC5 details: {details}"))

    @rpc5.command(name="state")
    async def rpc5_state(self, ctx, *, state: str):
        self._ensure_slot(4); self.rpc_slots[4]["state"] = state
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC5 state: {state}"))

    @rpc5.command(name="type")
    async def rpc5_type(self, ctx, activity_type: str):
        t = activity_type.lower()
        if t not in TYPE_MAP:
            await ctx.send(ascii.error("Invalid type")); return
        self._ensure_slot(4); self.rpc_slots[4]["type"] = TYPE_MAP[t]
        if t == "purplestream":
            self.rpc_slots[4]["url"] = PURPLESTREAM_URL
        elif "url" in self.rpc_slots[4] and t not in ("streaming", "purplestream"):
            del self.rpc_slots[4]["url"]
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC5 type: {activity_type}"))

    @rpc5.command(name="platform")
    async def rpc5_platform(self, ctx, preset: str):
        if self._apply_platform_preset(4, preset.lower()):
            await self.apply_activities(); await ctx.send(ascii.success(f"RPC5 platform: {preset}"))
        else:
            await ctx.send(ascii.error("Unknown platform"))

    @rpc5.command(name="large_image")
    async def rpc5_large_image(self, ctx, url: str):
        key = await self.upload_asset(url)
        if key:
            self._ensure_slot(4)
            self.rpc_slots[4].setdefault("assets", {})["large_image"] = key
            await self.apply_activities(); await ctx.send(ascii.success("RPC5 large image set"))

    @rpc5.command(name="small_image")
    async def rpc5_small_image(self, ctx, url: str):
        key = await self.upload_asset(url)
        if key:
            self._ensure_slot(4)
            self.rpc_slots[4].setdefault("assets", {})["small_image"] = key
            await self.apply_activities(); await ctx.send(ascii.success("RPC5 small image set"))

    @rpc5.command(name="timestamp")
    async def rpc5_timestamp(self, ctx, value: str):
        self._ensure_slot(4)
        if value.lower() == "clear":
            self.rpc_slots[4].pop("timestamps", None)
            await ctx.send(ascii.info("RPC5 timestamp cleared"))
        else:
            try:
                self._set_timestamp(4, value)
                await self.apply_activities()
                await ctx.send(ascii.success(f"RPC5 timestamp: {value}"))
            except Exception:
                await ctx.send(ascii.error("Use format: 3600 or 1:00:00"))

    @rpc5.command(name="btn1")
    async def rpc5_btn1(self, ctx, label: str, url: str):
        self._ensure_slot(4)
        btns = self.rpc_slots[4].setdefault("buttons", [])
        entry = {"label": label, "url": url}
        if not btns: btns.append(entry)
        else: btns[0] = entry
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC5 btn1: {label}"))

    @rpc5.command(name="btn2")
    async def rpc5_btn2(self, ctx, label: str, url: str):
        self._ensure_slot(4)
        btns = self.rpc_slots[4].setdefault("buttons", [])
        while len(btns) < 2: btns.append(None)
        btns[1] = {"label": label, "url": url}
        self.rpc_slots[4]["buttons"] = [b for b in btns if b]
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC5 btn2: {label}"))

    @rpc5.command(name="spotify")
    async def rpc5_spotify(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Default", "Unknown"]
        if len(parts) < 2: parts.append("Unknown")
        activity = await self.build_spotify(parts)
        if not activity:
            await ctx.send(ascii.error("Format: Song - Artist")); return
        self.rpc_slots[4] = activity
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC5 Spotify: {parts[0]}"))

    @rpc5.command(name="youtube")
    async def rpc5_youtube(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Default Video", "Default Channel"]
        if len(parts) < 2: parts.append("Default Channel")
        activity = await self.build_youtube(parts)
        if not activity:
            await ctx.send(ascii.error("Format: Video - Channel")); return
        self.rpc_slots[4] = activity
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC5 YouTube: {parts[0]}"))

    @rpc5.command(name="xbox")
    async def rpc5_xbox(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Xbox"]
        self.rpc_slots[4] = await self.build_xbox(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC5 Xbox: {parts[0]}"))

    @rpc5.command(name="ps")
    async def rpc5_ps(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["PlayStation"]
        self.rpc_slots[4] = await self.build_playstation(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC5 PS: {parts[0]}"))

    @rpc5.command(name="ps4")
    async def rpc5_ps4(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["PS4"]
        self.rpc_slots[4] = await self.build_playstation(parts, ps4=True)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC5 PS4: {parts[0]}"))

    @rpc5.command(name="crunchy")
    async def rpc5_crunchy(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Crunchyroll"]
        self.rpc_slots[4] = await self.build_crunchyroll(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC5 Crunchyroll: {parts[0]}"))

    @rpc5.command(name="roblox")
    async def rpc5_roblox(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Roblox"]
        self.rpc_slots[4] = await self.build_roblox(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC5 Roblox: {parts[0]}"))

    @rpc5.command(name="clear")
    async def rpc5_clear(self, ctx):
        self.rpc_slots[4] = None
        await self.apply_activities(); await ctx.send(ascii.info("RPC5 cleared"))

    # RPC6
    @commands.group(name="rpc6", invoke_without_command=True)
    async def rpc6(self, ctx, *, args: str = None):
        if not args:
            await ctx.send(ascii.error("Usage: .rpc6 name <text> | ...")); return
        parsed = self._parse_inline(args)
        if parsed:
            await self._apply_inline(5, parsed)
            await self.apply_activities()
            await ctx.send(ascii.success("RPC6 updated"))

    @rpc6.command(name="name")
    async def rpc6_name(self, ctx, *, name: str):
        self._ensure_slot(5); self.rpc_slots[5]["name"] = name
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC6 name: {name}"))

    @rpc6.command(name="details")
    async def rpc6_details(self, ctx, *, details: str):
        self._ensure_slot(5); self.rpc_slots[5]["details"] = details
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC6 details: {details}"))

    @rpc6.command(name="state")
    async def rpc6_state(self, ctx, *, state: str):
        self._ensure_slot(5); self.rpc_slots[5]["state"] = state
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC6 state: {state}"))

    @rpc6.command(name="type")
    async def rpc6_type(self, ctx, activity_type: str):
        t = activity_type.lower()
        if t not in TYPE_MAP:
            await ctx.send(ascii.error("Invalid type")); return
        self._ensure_slot(5); self.rpc_slots[5]["type"] = TYPE_MAP[t]
        if t == "purplestream":
            self.rpc_slots[5]["url"] = PURPLESTREAM_URL
        elif "url" in self.rpc_slots[5] and t not in ("streaming", "purplestream"):
            del self.rpc_slots[5]["url"]
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC6 type: {activity_type}"))

    @rpc6.command(name="platform")
    async def rpc6_platform(self, ctx, preset: str):
        if self._apply_platform_preset(5, preset.lower()):
            await self.apply_activities(); await ctx.send(ascii.success(f"RPC6 platform: {preset}"))
        else:
            await ctx.send(ascii.error("Unknown platform"))

    @rpc6.command(name="large_image")
    async def rpc6_large_image(self, ctx, url: str):
        key = await self.upload_asset(url)
        if key:
            self._ensure_slot(5)
            self.rpc_slots[5].setdefault("assets", {})["large_image"] = key
            await self.apply_activities(); await ctx.send(ascii.success("RPC6 large image set"))

    @rpc6.command(name="small_image")
    async def rpc6_small_image(self, ctx, url: str):
        key = await self.upload_asset(url)
        if key:
            self._ensure_slot(5)
            self.rpc_slots[5].setdefault("assets", {})["small_image"] = key
            await self.apply_activities(); await ctx.send(ascii.success("RPC6 small image set"))

    @rpc6.command(name="timestamp")
    async def rpc6_timestamp(self, ctx, value: str):
        self._ensure_slot(5)
        if value.lower() == "clear":
            self.rpc_slots[5].pop("timestamps", None)
            await ctx.send(ascii.info("RPC6 timestamp cleared"))
        else:
            try:
                self._set_timestamp(5, value)
                await self.apply_activities()
                await ctx.send(ascii.success(f"RPC6 timestamp: {value}"))
            except Exception:
                await ctx.send(ascii.error("Use format: 3600 or 1:00:00"))

    @rpc6.command(name="btn1")
    async def rpc6_btn1(self, ctx, label: str, url: str):
        self._ensure_slot(5)
        btns = self.rpc_slots[5].setdefault("buttons", [])
        entry = {"label": label, "url": url}
        if not btns: btns.append(entry)
        else: btns[0] = entry
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC6 btn1: {label}"))

    @rpc6.command(name="btn2")
    async def rpc6_btn2(self, ctx, label: str, url: str):
        self._ensure_slot(5)
        btns = self.rpc_slots[5].setdefault("buttons", [])
        while len(btns) < 2: btns.append(None)
        btns[1] = {"label": label, "url": url}
        self.rpc_slots[5]["buttons"] = [b for b in btns if b]
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC6 btn2: {label}"))

    @rpc6.command(name="spotify")
    async def rpc6_spotify(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Default", "Unknown"]
        if len(parts) < 2: parts.append("Unknown")
        activity = await self.build_spotify(parts)
        if not activity:
            await ctx.send(ascii.error("Format: Song - Artist")); return
        self.rpc_slots[5] = activity
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC6 Spotify: {parts[0]}"))

    @rpc6.command(name="youtube")
    async def rpc6_youtube(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Default Video", "Default Channel"]
        if len(parts) < 2: parts.append("Default Channel")
        activity = await self.build_youtube(parts)
        if not activity:
            await ctx.send(ascii.error("Format: Video - Channel")); return
        self.rpc_slots[5] = activity
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC6 YouTube: {parts[0]}"))

    @rpc6.command(name="xbox")
    async def rpc6_xbox(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Xbox"]
        self.rpc_slots[5] = await self.build_xbox(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC6 Xbox: {parts[0]}"))

    @rpc6.command(name="ps")
    async def rpc6_ps(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["PlayStation"]
        self.rpc_slots[5] = await self.build_playstation(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC6 PS: {parts[0]}"))

    @rpc6.command(name="ps4")
    async def rpc6_ps4(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["PS4"]
        self.rpc_slots[5] = await self.build_playstation(parts, ps4=True)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC6 PS4: {parts[0]}"))

    @rpc6.command(name="crunchy")
    async def rpc6_crunchy(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Crunchyroll"]
        self.rpc_slots[5] = await self.build_crunchyroll(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC6 Crunchyroll: {parts[0]}"))

    @rpc6.command(name="roblox")
    async def rpc6_roblox(self, ctx, *, args: str = None):
        parts = [p.strip() for p in args.split("-")] if args else ["Roblox"]
        self.rpc_slots[5] = await self.build_roblox(parts)
        await self.apply_activities(); await ctx.send(ascii.success(f"RPC6 Roblox: {parts[0]}"))

    @rpc6.command(name="clear")
    async def rpc6_clear(self, ctx):
        self.rpc_slots[5] = None
        await self.apply_activities(); await ctx.send(ascii.info("RPC6 cleared"))

    # ── quick presence ──

    @commands.command(name="playing")
    async def playing_cmd(self, ctx, *, message=None):
        try: await ctx.message.delete()
        except Exception: pass
        if not message:
            await ctx.send(ascii.error("Usage: .playing <text>")); return
        await self.bot.change_presence(activity=discord.Game(name=message))
        await ctx.send(ascii.success(f"Playing: {message}"))

    @commands.command(aliases=["listen"])
    async def listening_cmd(self, ctx, *, message=None):
        try: await ctx.message.delete()
        except Exception: pass
        if not message:
            await ctx.send(ascii.error("Usage: .listening <text>")); return
        await self.bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.listening, name=message))
        await ctx.send(ascii.success(f"Listening: {message}"))

    @commands.command(aliases=["watch"])
    async def watching_cmd(self, ctx, *, message=None):
        try: await ctx.message.delete()
        except Exception: pass
        if not message:
            await ctx.send(ascii.error("Usage: .watching <text>")); return
        await self.bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name=message))
        await ctx.send(ascii.success(f"Watching: {message}"))

    @commands.command(name="competing")
    async def competing_cmd(self, ctx, *, message=None):
        try: await ctx.message.delete()
        except Exception: pass
        if not message:
            await ctx.send(ascii.error("Usage: .competing <text>")); return
        await self.bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.competing, name=message))
        await ctx.send(ascii.success(f"Competing: {message}"))

    @commands.command(name="stopactivity")
    async def stopactivity_cmd(self, ctx):
        try: await ctx.message.delete()
        except Exception: pass
        await self.bot.change_presence(activity=None, status=discord.Status.online)
        await ctx.send(ascii.info("Activity cleared"))

    @commands.command(name="setpresencestatus")
    async def setpresencestatus_cmd(self, ctx, status_type: str):
        """Rename of `setstatus` to avoid colliding with the status cog."""
        try: await ctx.message.delete()
        except Exception: pass
        status_map = {"online": discord.Status.online, "dnd": discord.Status.dnd,
                      "idle": discord.Status.idle, "invisible": discord.Status.invisible}
        if status_type.lower() in status_map:
            await self.bot.change_presence(status=status_map[status_type.lower()])
            await ctx.send(ascii.success(f"Status: {status_type}"))
        else:
            await ctx.send(ascii.error("Use: online, dnd, idle, invisible"))

    @commands.command(name="aoff")
    async def aoff_cmd(self, ctx):
        try: await ctx.message.delete()
        except Exception: pass
        await self.bot.change_presence(activity=None)

    @commands.command(name="clear_multi_rpc")
    async def clear_multi_rpc(self, ctx):
        self._clearing = True
        try:
            self.rpc_slots = [None] * 6
            if self.bot.ws:
                await self.bot.ws.send(json.dumps({
                    "op": 3, "d": {"since": 0, "activities": [],
                                    "status": "online", "afk": False}}))
            self._save_rpc_slots()
        finally:
            self._clearing = False
        await ctx.send(ascii.info("Cleared all RPC slots"))

    @commands.command(name="rpc_status")
    async def rpc_status(self, ctx):
        type_names = {0: "Playing", 1: "Streaming", 2: "Listening", 3: "Watching", 5: "Competing"}
        lines = []
        for i, act in enumerate(self.rpc_slots):
            if act is None:
                lines.append(f"\x1b[2;37mRPC{i+1} — empty\x1b[0m")
            else:
                t = act.get("type", 0)
                label = type_names.get(t, "Unknown")
                lines.append(
                    f"\x1b[2;37mRPC{i+1} [{label}] {act.get('name', '—')} | "
                    f"{act.get('details', '—')} | {act.get('state', '—')}\x1b[0m")
        try:
            await ctx.send(ascii.multiline(lines, raw=True))
        except TypeError:
            await ctx.send(ascii.multiline(lines))

    @commands.command(name="spotify")
    async def cmd_spotify(self, ctx, *, args: str = None):
        if not args:
            await ctx.send(ascii.error("Usage: .spotify Song - Artist [slot 1-6]")); return
        words = args.strip().split(); slot = 0
        if words and words[-1] in ("1", "2", "3", "4", "5", "6"):
            slot = int(words[-1]) - 1; args = " ".join(words[:-1])
        parts = [p.strip() for p in args.split("-")]
        if len(parts) < 2: parts.append("Unknown")
        activity = await self.build_spotify(parts)
        if not activity:
            await ctx.send(ascii.error("Format: Song - Artist")); return
        self.rpc_slots[slot] = activity
        await self.apply_activities()
        await ctx.send(ascii.success(f"Spotify → slot {slot+1}: {parts[0]}"))

    @commands.command(name="youtube")
    async def cmd_youtube(self, ctx, *, args: str = None):
        if not args:
            await ctx.send(ascii.error("Usage: .youtube Video - Channel [slot 1-6]")); return
        words = args.strip().split(); slot = 0
        if words and words[-1] in ("1", "2", "3", "4", "5", "6"):
            slot = int(words[-1]) - 1; args = " ".join(words[:-1])
        parts = [p.strip() for p in args.split("-")]
        if len(parts) < 2: parts.append("Default Channel")
        activity = await self.build_youtube(parts)
        if not activity:
            await ctx.send(ascii.error("Format: Video - Channel")); return
        self.rpc_slots[slot] = activity
        await self.apply_activities()
        await ctx.send(ascii.success(f"YouTube → slot {slot+1}: {parts[0]}"))

    @commands.command(name="xbox")
    async def cmd_xbox(self, ctx, *, args: str = None):
        words = args.strip().split() if args else []; slot = 0
        if words and words[-1] in ("1", "2", "3", "4", "5", "6"):
            slot = int(words[-1]) - 1
            args = " ".join(words[:-1]) if len(words) > 1 else None
        parts = [p.strip() for p in args.split("-")] if args else ["Xbox"]
        self.rpc_slots[slot] = await self.build_xbox(parts)
        await self.apply_activities()
        await ctx.send(ascii.success(f"Xbox → slot {slot+1}: {parts[0]}"))

    @commands.command(name="ps")
    async def cmd_ps(self, ctx, *, args: str = None):
        words = args.strip().split() if args else []; slot = 0
        if words and words[-1] in ("1", "2", "3", "4", "5", "6"):
            slot = int(words[-1]) - 1
            args = " ".join(words[:-1]) if len(words) > 1 else None
        parts = [p.strip() for p in args.split("-")] if args else ["PlayStation"]
        self.rpc_slots[slot] = await self.build_playstation(parts)
        await self.apply_activities()
        await ctx.send(ascii.success(f"PS → slot {slot+1}: {parts[0]}"))

    @commands.command(name="ps4")
    async def cmd_ps4(self, ctx, *, args: str = None):
        words = args.strip().split() if args else []; slot = 0
        if words and words[-1] in ("1", "2", "3", "4", "5", "6"):
            slot = int(words[-1]) - 1
            args = " ".join(words[:-1]) if len(words) > 1 else None
        parts = [p.strip() for p in args.split("-")] if args else ["PS4"]
        self.rpc_slots[slot] = await self.build_playstation(parts, ps4=True)
        await self.apply_activities()
        await ctx.send(ascii.success(f"PS4 → slot {slot+1}: {parts[0]}"))

    @commands.command(name="crunchy")
    async def cmd_crunchy(self, ctx, *, args: str = None):
        words = args.strip().split() if args else []; slot = 0
        if words and words[-1] in ("1", "2", "3", "4", "5", "6"):
            slot = int(words[-1]) - 1
            args = " ".join(words[:-1]) if len(words) > 1 else None
        parts = [p.strip() for p in args.split("-")] if args else ["Crunchyroll"]
        self.rpc_slots[slot] = await self.build_crunchyroll(parts)
        await self.apply_activities()
        await ctx.send(ascii.success(f"Crunchyroll → slot {slot+1}: {parts[0]}"))

    @commands.command(name="roblox")
    async def cmd_roblox(self, ctx, *, args: str = None):
        words = args.strip().split() if args else []; slot = 0
        if words and words[-1] in ("1", "2", "3", "4", "5", "6"):
            slot = int(words[-1]) - 1
            args = " ".join(words[:-1]) if len(words) > 1 else None
        parts = [p.strip() for p in args.split("-")] if args else ["Roblox"]
        self.rpc_slots[slot] = await self.build_roblox(parts)
        await self.apply_activities()
        await ctx.send(ascii.success(f"Roblox → slot {slot+1}: {parts[0]}"))

    @commands.command(name="vrchat")
    async def cmd_vrchat(self, ctx, *, args: str = None):
        try: await ctx.message.delete()
        except Exception: pass
        words = args.strip().split() if args else []; slot = 0
        if words and words[-1] in ("1", "2", "3", "4", "5", "6"):
            slot = int(words[-1]) - 1
            args = " ".join(words[:-1]) if len(words) > 1 else None
        parts = [p.strip() for p in args.split("-")] if args else ["Exploring VRChat", "VRChat"]
        self.rpc_slots[slot] = await self.build_vrchat(parts)
        await self.apply_activities()
        state = parts[0] if parts else "Exploring VRChat"
        await ctx.send(ascii.success(f"VRChat → slot {slot+1}: {state}"))

    @commands.command(name="meta")
    async def cmd_meta(self, ctx, *, args: str = None):
        try: await ctx.message.delete()
        except Exception: pass
        if not args:
            await ctx.send(ascii.error("Usage: .meta State - World [slot] [image_url]")); return
        words = args.strip().split(); slot = 0; image_url = None
        for i, word in enumerate(words):
            if word.startswith(("http://", "https://")):
                image_url = word; words = words[:i]; break
        if words and words[-1] in ("1", "2", "3", "4", "5", "6"):
            slot = int(words[-1]) - 1; words = words[:-1]
        args_str = " ".join(words) if words else None
        parts = ([p.strip() for p in args_str.split("-")] if args_str
                 else ["Exploring VRChat", "VRChat"])
        self.rpc_slots[slot] = await self.build_vrchat(parts, image_url)
        await self.apply_activities()
        state = parts[0] if parts else "Exploring VRChat"
        await ctx.send(ascii.success(f"Meta Quest → slot {slot+1}: {state}"))

    # ── status rotation ──

    @commands.command(name='rstatus')
    async def rotate_status(self, ctx, *, statuses: str):
        try: await ctx.message.delete()
        except Exception: pass
        status_list = [s.strip() for s in statuses.split(',') if s.strip()]
        if not status_list:
            await ctx.send(ascii.error("Separate statuses by commas")); return
        # cancel any prior rotation
        if self._status_rotation_task and not self._status_rotation_task.done():
            self._status_rotation_task.cancel()
        self.status_rotation_active = True

        async def _loop():
            idx = 0
            try:
                while self.status_rotation_active:
                    self.current_status = status_list[idx]
                    await self._patch_custom_status()
                    await asyncio.sleep(8)
                    idx = (idx + 1) % len(status_list)
            finally:
                self.current_status = ""
                try: await self._patch_custom_status()
                except Exception: pass

        await ctx.send(ascii.info(f"Status rotation: {len(status_list)} statuses"))
        self._status_rotation_task = asyncio.create_task(_loop())

    @commands.command(name='remoji')
    async def rotate_emoji(self, ctx, *, emojis: str):
        try: await ctx.message.delete()
        except Exception: pass
        emoji_list = [e.strip() for e in emojis.split(',') if e.strip()]
        if not emoji_list:
            await ctx.send(ascii.error("Separate emojis by commas")); return
        if self._emoji_rotation_task and not self._emoji_rotation_task.done():
            self._emoji_rotation_task.cancel()
        self.emoji_rotation_active = True

        async def _loop():
            idx = 0
            try:
                while self.emoji_rotation_active:
                    self.current_emoji = emoji_list[idx]
                    await self._patch_custom_status()
                    await asyncio.sleep(8)
                    idx = (idx + 1) % len(emoji_list)
            finally:
                self.current_emoji = ""
                try: await self._patch_custom_status()
                except Exception: pass

        await ctx.send(ascii.info(f"Emoji rotation: {len(emoji_list)} emojis"))
        self._emoji_rotation_task = asyncio.create_task(_loop())

    async def _patch_custom_status(self):
        json_data = {'custom_status': {'text': self.current_status,
                                        'emoji_name': self.current_emoji}}
        async with aiohttp.ClientSession() as session:
            await session.patch(
                'https://discord.com/api/v9/users/@me/settings',
                headers={'Authorization': self.bot.http.token,
                         'Content-Type': 'application/json'},
                json=json_data)

    @commands.command(name='stopstatus')
    async def stop_rotate_status(self, ctx):
        try: await ctx.message.delete()
        except Exception: pass
        self.status_rotation_active = False
        await ctx.send(ascii.info("Status rotation stopped"))

    @commands.command(name='stopemoji')
    async def stop_rotate_emoji(self, ctx):
        try: await ctx.message.delete()
        except Exception: pass
        self.emoji_rotation_active = False
        await ctx.send(ascii.info("Emoji rotation stopped"))


async def setup(bot):
    await bot.add_cog(RPCCog(bot))
