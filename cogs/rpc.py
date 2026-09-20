# cogs/rpc.py | rich presence — plain class, no discord.ext.commands
import discord
import asyncio
import time
import re
import json
import io
import aiohttp
from pathlib import Path

DEFAULT_APP_ID = 1453358037506199743
PURPLESTREAM_URL = "https://www.twitch.tv/hadeontop"

TYPE_MAP = {
    "playing": 0, "streaming": 1, "listening": 2,
    "watching": 3, "competing": 5, "purplestream": 1
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
}


def _ok(msg):   return f"```ansi\n> \x1b[32m✓\x1b[0m  {msg}\n> ```"
def _err(msg):  return f"```ansi\n> \x1b[31m✗\x1b[0m  {msg}\n> ```"
def _info(msg): return f"```ansi\n> \x1b[36m•\x1b[0m  {msg}\n> ```"


class RPCCog:
    """Rich presence manager. Not a discord.ext.commands.Cog — plain class."""

    COMMANDS = {
        "rpc", "spotify", "youtube", "xbox", "ps", "ps4",
        "crunchy", "vrchat", "meta",
        "playing", "listening", "listen", "watching", "watch",
        "competing", "stopactivity", "aoff",
    }

    def __init__(self, client):
        self.client = client
        self.rpc_slots = [None] * 6
        self._slot_platform_preset = [None] * 6
        self._asset_cache = {}
        self._original_ws_send = None
        self._interceptor_active = False
        self._clearing = False
        self._ready = False

    # ── lifecycle ────────────────────────────────────

    async def on_ready(self):
        await asyncio.sleep(3)
        self._load_rpc_slots()
        await self._start_presence_interceptor()
        if any(a is not None for a in self.rpc_slots):
            await self._push()
            print("[RPC] restored saved slots")
        self._ready = True

    # ── persistence ──────────────────────────────────

    def _get_user_file(self, filename):
        uid = str(self.client.user.id) if self.client.user else "unknown"
        path = Path(f"data/{uid}.json")
        path.parent.mkdir(exist_ok=True)
        return path

    def _save_rpc_slots(self):
        path = self._get_user_file("slots.json")
        data = []
        for slot in self.rpc_slots:
            if slot:
                clean = {k: v for k, v in slot.items()
                         if k not in ["instance", "flags", "session_id", "sync_id",
                                      "secrets", "party", "metadata"]}
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
                    self.rpc_slots[i] = slot
            n = sum(1 for s in self.rpc_slots if s)
            if n:
                print(f"[RPC] loaded {n} saved slots")
        except Exception as e:
            print(f"[RPC] load failed: {e}")

    def _ensure_slot(self, index: int):
        if self.rpc_slots[index] is None:
            self.rpc_slots[index] = {
                "type": 0, "name": "Default",
                "application_id": DEFAULT_APP_ID,
                "assets": {}, "instance": True
            }

    # ── push + interceptor ───────────────────────────

    async def _push(self):
        active = [a for a in self.rpc_slots if a is not None]
        if not active:
            return
        ws = getattr(self.client, "ws", None)
        if ws is None:
            print("[RPC] push skipped — ws not ready")
            return
        try:
            current_status = str(self.client.status) if hasattr(self.client, "status") else "online"
            payload = {"op": 3, "d": {"since": 0, "activities": active,
                                      "status": current_status, "afk": False}}
            await ws.send(json.dumps(payload))
            self._save_rpc_slots()
        except Exception as e:
            print(f"[RPC] push failed: {e}")

    async def _start_presence_interceptor(self):
        if self._interceptor_active:
            return
        for _ in range(30):
            ws = getattr(self.client, "ws", None)
            if ws and hasattr(ws, "send"):
                break
            await asyncio.sleep(0.5)
        ws = getattr(self.client, "ws", None)
        if not ws:
            print("[RPC] no websocket — interceptor skipped")
            return
        self._original_ws_send = ws.send

        async def patched_send(data, *args, **kwargs):
            if isinstance(data, str) and '"op":3' in data:
                try:
                    payload = json.loads(data)
                    d = payload.get("d", {})
                    activities = d.get("activities", [])
                    if not activities and any(s is not None for s in self.rpc_slots):
                        active = [s for s in self.rpc_slots if s is not None]
                        if active and not self._clearing:
                            d["activities"] = active
                            payload["d"] = d
                            data = json.dumps(payload)
                except Exception:
                    pass
            return await self._original_ws_send(data, *args, **kwargs)

        ws.send = patched_send
        self._interceptor_active = True
        print("[RPC] presence interceptor active")

    # ── dispatch ─────────────────────────────────────

    async def handle(self, message, cmd: str, args: list) -> bool:
        """Called from selfbot's on_message. Returns True if handled."""
        if cmd == "rpc":
            await self._cmd_rpc(message, args)
            return True
        if cmd in ("spotify", "youtube", "xbox", "ps", "ps4", "crunchy", "vrchat", "meta"):
            await self._cmd_quick(message, cmd, args)
            return True
        if cmd in ("playing", "listening", "listen", "watching", "watch", "competing"):
            await self._cmd_activity(message, cmd, args)
            return True
        if cmd in ("stopactivity", "aoff"):
            await self.client.change_presence(activity=None, status=discord.Status.online)
            await message.channel.send(_ok("activity cleared"))
            return True
        return False

    # ── helpers ──────────────────────────────────────

    def _slot(self, raw) -> int:
        try:
            n = int(raw)
            if 1 <= n <= 6:
                return n - 1
        except (TypeError, ValueError):
            pass
        return -1

    def _apply_platform_preset(self, slot: int, preset_key: str) -> bool:
        off = {"off", "none", "clear", "normal"}
        if preset_key in off:
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

    def _parse_ts(self, value: str) -> float:
        value = value.strip()
        if ":" in value:
            parts = value.split(":")
            try:
                if len(parts) == 3:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                elif len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
            except Exception:
                pass
        return float(value) * 3600

    async def upload_asset(self, image_url: str):
        if not image_url:
            return None
        if image_url in self._asset_cache:
            return self._asset_cache[image_url]
        try:
            cdn_re = r"https?://(?:cdn\.discordapp\.com|media\.discordapp\.net)/attachments/(\d+)/(\d+)/(.+)"
            m = re.search(cdn_re, image_url)
            if m:
                cid, aid, fname = m.groups()
                key = f"mp:attachments/{cid}/{aid}/{fname}"
                self._asset_cache[image_url] = key
                return key
            self_dm = await self.client.user.create_dm()
            async with aiohttp.ClientSession() as s:
                async with s.get(image_url) as r:
                    if r.status != 200:
                        return None
                    img_bytes = await r.read()
                    filename = image_url.split("/")[-1].split("?")[0]
                    if "." not in filename or len(filename) > 50:
                        filename = "asset.png"
                    msg = await self_dm.send(file=discord.File(io.BytesIO(img_bytes), filename=filename))
                    if msg.attachments:
                        new_url = msg.attachments[0].url
                        m2 = re.search(cdn_re, new_url)
                        if m2:
                            cid, aid, fname = m2.groups()
                            key = f"mp:attachments/{cid}/{aid}/{fname}"
                            self._asset_cache[image_url] = key
                            return key
        except Exception as e:
            print(f"[RPC] asset upload failed: {e}")
        return None

    # ── builders ─────────────────────────────────────

    async def build_spotify(self, parts):
        if len(parts) < 2: return None
        song, artist = parts[0], parts[1]
        album = parts[2] if len(parts) > 2 else song
        duration = float(parts[3]) if len(parts) > 3 else 3.5
        position = float(parts[4]) if len(parts) > 4 else 0.0
        cur_ms = int(position * 60 * 1000)
        tot_ms = int(duration * 60 * 1000)
        now = int(time.time() * 1000)
        sid = "09xhawlPUifhftf8zuie7w"
        return {
            "type": 2, "name": "Spotify", "details": song, "state": artist,
            "timestamps": {"start": now - cur_ms, "end": (now - cur_ms) + (tot_ms - cur_ms)},
            "application_id": "3201606009684",
            "sync_id": sid, "session_id": f"spotify:{sid}",
            "party": {"id": f"spotify:{sid}", "size": [1, 1]},
            "secrets": {"join": f"spotify:{sid}", "spectate": f"spotify:{sid}", "match": f"spotify:{sid}"},
            "instance": True, "flags": 48,
            "metadata": {"context_uri": f"spotify:album:{sid}", "album_id": sid,
                         "artist_ids": ["0HPG2EIdGCP6gjXW0KzrJq", "0qc4bfxcwRFZfevTck4fOi"],
                         "track_id": sid},
            "assets": {"large_image": "spotify", "large_text": f"{album} on Spotify"}
        }

    async def build_youtube(self, parts):
        if len(parts) < 2: return None
        video, channel = parts[0], parts[1]
        duration = float(parts[2]) if len(parts) > 2 else 5.0
        position = float(parts[3]) if len(parts) > 3 else 0.0
        cur_ms = int(position * 60 * 1000)
        tot_ms = int(duration * 60 * 1000)
        now = int(time.time() * 1000)
        return {
            "type": 3, "name": "YouTube", "details": video, "state": channel,
            "timestamps": {"start": now - cur_ms, "end": (now - cur_ms) + (tot_ms - cur_ms)},
            "application_id": "111299001912",
            "assets": {"large_image": "youtube", "large_text": f"{video} on YouTube"}
        }

    async def build_xbox(self, parts):
        game = (parts[0] if parts else "Xbox")[:128]
        activity = {
            "type": 0, "name": game,
            "application_id": "622174530214821906",
            "platform": "xbox",
            "timestamps": {"start": int(time.time() * 1000)},
            "assets": {"large_image": "xbox", "large_text": game[:32]}
        }
        if len(parts) > 1 and parts[1]: activity["details"] = parts[1][:128]
        if len(parts) > 2 and parts[2]: activity["state"] = parts[2][:128]
        return activity

    async def build_playstation(self, parts, ps4=False):
        game = (parts[0] if parts else "PlayStation")[:128]
        return {
            "type": 0, "name": game,
            "application_id": "1470539864909943067",
            "platform": "ps4" if ps4 else "ps5",
            "timestamps": {"start": int(time.time() * 1000)},
            "assets": {"large_image": "playstation", "large_text": game[:32]},
            **({"details": parts[1][:128]} if len(parts) > 1 and parts[1] else {}),
            **({"state": parts[2][:128]} if len(parts) > 2 and parts[2] else {})
        }

    async def build_crunchyroll(self, parts):
        anime = (parts[0] if parts else "Anime")[:128]
        episode = (parts[1] if len(parts) > 1 else "Episode")[:128]
        elapsed = float(parts[2]) if len(parts) > 2 else 0.0
        total = float(parts[3]) if len(parts) > 3 else 24.0
        cur_ms = int(elapsed * 60 * 1000)
        tot_ms = int(total * 60 * 1000)
        now = int(time.time() * 1000)
        return {
            "type": 3, "name": "Crunchyroll",
            "application_id": "981509069309354054",
            "details": anime, "state": episode,
            "timestamps": {"start": now - cur_ms, "end": (now - cur_ms) + (tot_ms - cur_ms)},
            "assets": {"large_image": "crunchyroll", "large_text": anime[:32]}
        }

    async def build_vrchat(self, parts, image_url=None):
        state = parts[0] if parts else "Exploring VRChat"
        world = parts[1] if len(parts) > 1 else "VRChat"
        now = int(time.time() * 1000)
        if image_url:
            key = await self.upload_asset(image_url)
            large_image = key if key else image_url
        else:
            large_image = "mp:external/jxAa_-ahC78ilas-ifzE8DX6RyNTI_FV-p2F7HzGhfs/https/www.oculus.com/rich_presence/image/1856672347794301/"
        return {
            "type": 0, "name": "VRChat",
            "application_id": "1498387526501535835",
            "platform": "meta_quest",
            "state": state,
            "timestamps": {"start": now},
            "assets": {"large_image": large_image, "large_text": world},
            "instance": True
        }

    # ── $rpc handler ─────────────────────────────────

    async def _cmd_rpc(self, message, args):
        if not args:
            await message.channel.send(_info(
                "usage: $rpc <slot 1-6> <field> <value>\n"
                "fields: name details state type platform large_image small_image "
                "large_text small_text timestamp btn1 btn2 clear status clearall"))
            return

        head = args[0].lower()

        if head == "status":
            type_names = {0: "Playing", 1: "Streaming", 2: "Listening", 3: "Watching", 5: "Competing"}
            lines = ["> ```ansi"]
            for i, a in enumerate(self.rpc_slots):
                if a is None:
                    lines.append(f"> \x1b[2;37mslot {i+1} — empty\x1b[0m")
                else:
                    t = type_names.get(a.get("type", 0), "?")
                    lines.append(f"> \x1b[2;37mslot {i+1} [{t}] "
                                 f"{a.get('name', '—')} | {a.get('details', '—')} | "
                                 f"{a.get('state', '—')}\x1b[0m")
            lines.append("> ```")
            await message.channel.send("\n".join(lines))
            return

        if head == "clearall":
            self._clearing = True
            self.rpc_slots = [None] * 6
            ws = getattr(self.client, "ws", None)
            if ws:
                try:
                    await ws.send(json.dumps({"op": 3, "d": {"since": 0, "activities": [],
                                                            "status": "online", "afk": False}}))
                except Exception:
                    pass
            self._save_rpc_slots()
            self._clearing = False
            await message.channel.send(_ok("all 6 slots cleared"))
            return

        idx = self._slot(head)
        if idx < 0:
            await message.channel.send(_err(f"slot must be 1-6, got `{head}`"))
            return

        if len(args) < 2:
            await message.channel.send(_err("missing field"))
            return

        field = args[1].lower()
        value = " ".join(args[2:]) if len(args) > 2 else None

        if field == "clear":
            self.rpc_slots[idx] = None
            await self._push()
            await message.channel.send(_ok(f"slot {idx+1} cleared"))
            return

        self._ensure_slot(idx)
        act = self.rpc_slots[idx]

        if field == "name":
            act["name"] = value or ""
        elif field == "details":
            act["details"] = value or ""
        elif field == "state":
            act["state"] = value or ""
        elif field == "large_text":
            act.setdefault("assets", {})["large_text"] = value or ""
        elif field == "small_text":
            act.setdefault("assets", {})["small_text"] = value or ""
        elif field == "large_image":
            key = await self.upload_asset(value)
            if not key:
                await message.channel.send(_err("image upload failed"))
                return
            act.setdefault("assets", {})["large_image"] = key
        elif field == "small_image":
            key = await self.upload_asset(value)
            if not key:
                await message.channel.send(_err("image upload failed"))
                return
            act.setdefault("assets", {})["small_image"] = key
        elif field == "type":
            t = (value or "").lower()
            if t not in TYPE_MAP:
                await message.channel.send(_err(f"type must be one of: {', '.join(TYPE_MAP)}"))
                return
            act["type"] = TYPE_MAP[t]
            if t == "purplestream":
                act["url"] = PURPLESTREAM_URL
            elif "url" in act and t not in ("streaming", "purplestream"):
                del act["url"]
        elif field == "platform":
            if not self._apply_platform_preset(idx, (value or "").lower()):
                await message.channel.send(_err(f"unknown platform: {value}"))
                return
        elif field == "timestamp":
            if (value or "").lower() == "clear":
                act.pop("timestamps", None)
            else:
                try:
                    secs = self._parse_ts(value)
                    now = time.time()
                    act["timestamps"] = {"start": int(now * 1000),
                                         "end": int((now + secs) * 1000)}
                except Exception:
                    await message.channel.send(_err("use format: 3600 or 1:00:00 or clear"))
                    return
        elif field == "btn1":
            parts = (value or "").split()
            if len(parts) < 2:
                await message.channel.send(_err("format: <label> <url>"))
                return
            btns = act.setdefault("buttons", [])
            entry = {"label": " ".join(parts[:-1]), "url": parts[-1]}
            if not btns: btns.append(entry)
            else: btns[0] = entry
        elif field == "btn2":
            parts = (value or "").split()
            if len(parts) < 2:
                await message.channel.send(_err("format: <label> <url>"))
                return
            btns = act.setdefault("buttons", [])
            while len(btns) < 2: btns.append(None)
            btns[1] = {"label": " ".join(parts[:-1]), "url": parts[-1]}
            act["buttons"] = [b for b in btns if b]
        else:
            await message.channel.send(_err(f"unknown field: {field}"))
            return

        await self._push()
        await message.channel.send(_ok(f"slot {idx+1} {field} set"))

    # ── quick shortcuts handler ──────────────────────

    async def _cmd_quick(self, message, cmd, args):
        raw = " ".join(args)
        words = raw.strip().split() if raw else []
        slot = 0
        if words and words[-1] in ("1","2","3","4","5","6"):
            slot = int(words[-1]) - 1
            raw = " ".join(words[:-1])

        image_url = None
        if cmd == "meta":
            for i, w in enumerate(raw.split()):
                if w.startswith(("http://", "https://")):
                    image_url = w
                    raw = " ".join(raw.split()[:i])
                    break

        parts = [p.strip() for p in raw.split("-") if p.strip()] if raw else []

        if cmd == "spotify":
            if len(parts) < 2: parts.append("Unknown")
            act = await self.build_spotify(parts)
            label = "Spotify"
        elif cmd == "youtube":
            if len(parts) < 2: parts.append("Default Channel")
            act = await self.build_youtube(parts)
            label = "YouTube"
        elif cmd == "xbox":
            if not parts: parts = ["Xbox"]
            act = await self.build_xbox(parts)
            label = "Xbox"
        elif cmd in ("ps", "ps4"):
            if not parts: parts = ["PlayStation"]
            act = await self.build_playstation(parts, ps4=(cmd == "ps4"))
            label = "PS4" if cmd == "ps4" else "PS"
        elif cmd == "crunchy":
            if not parts: parts = ["Crunchyroll"]
            act = await self.build_crunchyroll(parts)
            label = "Crunchyroll"
        elif cmd in ("vrchat", "meta"):
            if not parts: parts = ["Exploring VRChat", "VRChat"]
            act = await self.build_vrchat(parts, image_url)
            label = "VRChat" if cmd == "vrchat" else "Meta"
        else:
            return

        if not act:
            await message.channel.send(_err(f"could not build {cmd} presence"))
            return

        self.rpc_slots[slot] = act
        await self._push()
        name = parts[0] if parts else "—"
        await message.channel.send(_ok(f"{label} → slot {slot+1}: {name}"))

    # ── simple activity handler ──────────────────────

    async def _cmd_activity(self, message, cmd, args):
        text = " ".join(args) if args else None
        if not text:
            await message.channel.send(_err(f"usage: ${cmd} <text>"))
            return
        if cmd == "playing":
            await self.client.change_presence(activity=discord.Game(name=text))
        elif cmd in ("listening", "listen"):
            await self.client.change_presence(
                activity=discord.Activity(type=discord.ActivityType.listening, name=text))
        elif cmd in ("watching", "watch"):
            await self.client.change_presence(
                activity=discord.Activity(type=discord.ActivityType.watching, name=text))
        elif cmd == "competing":
            await self.client.change_presence(
                activity=discord.Activity(type=discord.ActivityType.competing, name=text))
        await message.channel.send(_ok(f"{cmd}: {text}"))
