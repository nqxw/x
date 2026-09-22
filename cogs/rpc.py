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

try:
    from cogs import state as cstate
except Exception:
    cstate = None

try:
    from utils.ascii_helper import AsciiHelper
    ascii = AsciiHelper()
except Exception as e:
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
    "roblox": {"application_id": 899273624442372096, "platform": None, "asset": "roblox"},
}

INLINE_KEYS = ["name", "details", "state", "type", "timestamp", "platform",
               "large_image_text", "large_image", "small_image", "btn1", "btn2"]

REQUIRED_ACTIVITY_FIELDS = {
    "type": 0,
    "name": "Default",
    "application_id": DEFAULT_APP_ID,
    "assets": {},
    "instance": True,
}

SPOTIFY_FIELDS_TO_KEEP = {
    "session_id", "sync_id", "party", "secrets", "metadata", "flags",
    "application_id", "assets", "type", "name", "details", "state",
    "timestamps", "instance",
}


class RPCCog(commands.Cog, name="Rich Presence"):

    # ── dispatcher registration ──
    # the selfbot's custom dispatcher routes via _COG_REGISTRY, which is built
    # from this set. every name below is what the user types after the prefix.
    COMMANDS = {
        "rpc1", "rpc2", "rpc3", "rpc4", "rpc5", "rpc6",
        "roblox", "spotify", "youtube", "xbox", "ps", "ps4",
        "crunchy", "vrchat", "meta",
        "playing", "listening", "watching", "competing",
        "stopactivity", "setpresencestatus", "aoff",
        "clear_multi_rpc", "rpc_status",
        "rstatus", "remoji", "stopstatus", "stopemoji",
    }

    def __init__(self, bot=None):
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
        self._interceptor_ws = None
        self._original_ws_send = None
        self._clearing = False
        self._bg_tasks = []
        self._deferred_started = False
        try:
            self.bot.loop.create_task(self._deferred_start())
        except Exception:
            asyncio.ensure_future(self._deferred_start())

    # ── dispatcher entry point ──

    async def handle(self, message, cmd, args):
        """Called by selfbot.py::_dispatch_message when cmd is in COMMANDS.
        args is the full token list, args[0] == cmd."""
        try:
            rest = args[1:] if len(args) > 1 else []

            if cmd in ("rpc1", "rpc2", "rpc3", "rpc4", "rpc5", "rpc6"):
                slot = int(cmd[3]) - 1
                await self._handle_slot(message, slot, rest)
                return

            if cmd in ("roblox", "spotify", "youtube", "xbox", "ps", "ps4",
                       "crunchy", "vrchat", "meta"):
                await self._handle_quick(message, cmd, rest)
                return

            if cmd == "playing":
                await message.channel.send(ascii.error("Use `.rpc1 type playing` + `.rpc1 name <text>` instead"))
                return
            if cmd == "listening":
                await message.channel.send(ascii.error("Use `.rpc1 type listening` + `.rpc1 name <text>` instead"))
                return
            if cmd == "watching":
                await message.channel.send(ascii.error("Use `.rpc1 type watching` + `.rpc1 name <text>` instead"))
                return
            if cmd == "competing":
                await message.channel.send(ascii.error("Use `.rpc1 type competing` + `.rpc1 name <text>` instead"))
                return
            if cmd == "stopactivity":
                self.rpc_slots = [None] * 6
                await self.apply_activities()
                await message.channel.send(ascii.info("Activity cleared"))
                return
            if cmd == "aoff":
                await message.channel.send(ascii.info("Use `.clear_multi_rpc` to wipe all slots"))
                return
            if cmd == "setpresencestatus":
                await message.channel.send(ascii.error("Use the status cog's setpresencestatus"))
                return
            if cmd == "clear_multi_rpc":
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
                await message.channel.send(ascii.info("Cleared all RPC slots"))
                return
            if cmd == "rpc_status":
                type_names = {0: "Playing", 1: "Streaming", 2: "Listening",
                              3: "Watching", 5: "Competing"}
                lines = []
                for i, act in enumerate(self.rpc_slots):
                    if act is None:
                        lines.append(f"RPC{i+1} — empty")
                    else:
                        t = act.get("type", 0)
                        label = type_names.get(t, "Unknown")
                        lines.append(
                            f"RPC{i+1} [{label}] {act.get('name', '—')} | "
                            f"{act.get('details', '—')} | {act.get('state', '—')}")
                await message.channel.send(ascii.multiline(lines))
                return
            if cmd == "rstatus":
                if not rest:
                    await message.channel.send(ascii.error("Usage: .rstatus a,b,c")); return
                status_list = [s.strip() for s in " ".join(rest).split(",") if s.strip()]
                if not status_list:
                    await message.channel.send(ascii.error("Separate statuses by commas")); return
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

                await message.channel.send(ascii.info(f"Status rotation: {len(status_list)} statuses"))
                self._status_rotation_task = asyncio.create_task(_loop())
                return
            if cmd == "remoji":
                if not rest:
                    await message.channel.send(ascii.error("Usage: .remoji 🅰️,🅱️")); return
                emoji_list = [e.strip() for e in " ".join(rest).split(",") if e.strip()]
                if not emoji_list:
                    await message.channel.send(ascii.error("Separate emojis by commas")); return
                if self._emoji_rotation_task and not self._emoji_rotation_task.done():
                    self._emoji_rotation_task.cancel()
                self.emoji_rotation_active = True

                async def _loop2():
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

                await message.channel.send(ascii.info(f"Emoji rotation: {len(emoji_list)} emojis"))
                self._emoji_rotation_task = asyncio.create_task(_loop2())
                return
            if cmd == "stopstatus":
                self.status_rotation_active = False
                await message.channel.send(ascii.info("Status rotation stopped"))
                return
            if cmd == "stopemoji":
                self.emoji_rotation_active = False
                await message.channel.send(ascii.info("Emoji rotation stopped"))
                return
        except Exception as e:
            import traceback
            print(f"[rpc] handle error on {cmd}: {e}")
            traceback.print_exc()
            try:
                await message.channel.send(ascii.error(f"`{cmd}` errored — check console"))
            except Exception:
                pass

    # ── slot subcommand dispatch ──

    async def _handle_slot(self, message, slot, rest):
        ch = message.channel
        label = f"RPC{slot+1}"

        if not rest:
            # inline form: .rpc1 name X | details Y | ...
            # but with no args there's nothing to parse
            await ch.send(ascii.error(f"Usage: .rpc{slot+1} name <text> | details <text> | ..."))
            return

        sub = rest[0].lower()
        payload = rest[1:]
        rest_str = " ".join(payload)

        # inline-parse fallback when sub isn't a known subcommand
        known_subs = {"name", "details", "state", "type", "platform", "timestamp",
                      "large_image", "small_image", "large_image_text", "btn1", "btn2",
                      "spotify", "youtube", "xbox", "ps", "ps4", "crunchy", "crunchyroll",
                      "roblox", "vrchat", "clear"}
        if sub not in known_subs:
            parsed = self._parse_inline(" ".join(rest))
            if parsed:
                await self._apply_inline(slot, parsed)
                await self.apply_activities()
                await ch.send(ascii.success(f"{label} updated"))
            else:
                await ch.send(ascii.error(f"Unknown subcommand: {sub}"))
            return

        if sub == "name":
            self._ensure_slot(slot); self.rpc_slots[slot]["name"] = rest_str
            await self.apply_activities(); await ch.send(ascii.success(f"{label} name: {rest_str}"))
        elif sub == "details":
            self._ensure_slot(slot); self.rpc_slots[slot]["details"] = rest_str
            await self.apply_activities(); await ch.send(ascii.success(f"{label} details: {rest_str}"))
        elif sub == "state":
            self._ensure_slot(slot); self.rpc_slots[slot]["state"] = rest_str
            await self.apply_activities(); await ch.send(ascii.success(f"{label} state: {rest_str}"))
        elif sub == "type":
            t = rest_str.lower()
            if t not in TYPE_MAP:
                await ch.send(ascii.error("Invalid type")); return
            self._ensure_slot(slot); self.rpc_slots[slot]["type"] = TYPE_MAP[t]
            if t == "purplestream":
                self.rpc_slots[slot]["url"] = PURPLESTREAM_URL
            elif "url" in self.rpc_slots[slot] and t not in ("streaming", "purplestream"):
                del self.rpc_slots[slot]["url"]
            await self.apply_activities(); await ch.send(ascii.success(f"{label} type: {t}"))
        elif sub == "platform":
            if self._apply_platform_preset(slot, rest_str.lower()):
                await self.apply_activities(); await ch.send(ascii.success(f"{label} platform: {rest_str}"))
            else:
                await ch.send(ascii.error("Unknown platform"))
        elif sub == "timestamp":
            self._ensure_slot(slot)
            if rest_str.lower() == "clear":
                self.rpc_slots[slot].pop("timestamps", None)
                await ch.send(ascii.info(f"{label} timestamp cleared"))
            else:
                try:
                    self._set_timestamp(slot, rest_str)
                    await self.apply_activities()
                    await ch.send(ascii.success(f"{label} timestamp: {rest_str}"))
                except Exception:
                    await ch.send(ascii.error("Use format: 3600 or 1:00:00"))
        elif sub == "large_image":
            if not rest_str:
                await ch.send(ascii.error(f"Usage: .rpc{slot+1} large_image <url>")); return
            key = await self.upload_asset(rest_str)
            if key:
                self._ensure_slot(slot)
                self.rpc_slots[slot].setdefault("assets", {})["large_image"] = key
                await self.apply_activities(); await ch.send(ascii.success(f"{label} large image set"))
            else:
                await ch.send(ascii.error("upload failed"))
        elif sub == "small_image":
            if not rest_str:
                await ch.send(ascii.error(f"Usage: .rpc{slot+1} small_image <url>")); return
            key = await self.upload_asset(rest_str)
            if key:
                self._ensure_slot(slot)
                self.rpc_slots[slot].setdefault("assets", {})["small_image"] = key
                await self.apply_activities(); await ch.send(ascii.success(f"{label} small image set"))
            else:
                await ch.send(ascii.error("upload failed"))
        elif sub == "large_image_text":
            self._ensure_slot(slot)
            self.rpc_slots[slot].setdefault("assets", {})["large_text"] = rest_str
            await self.apply_activities(); await ch.send(ascii.success(f"{label} large text set"))
        elif sub == "btn1":
            if len(payload) < 2:
                await ch.send(ascii.error(f"Usage: .rpc{slot+1} btn1 <label> <url>")); return
            self._ensure_slot(slot)
            btns = self.rpc_slots[slot].setdefault("buttons", [])
            entry = {"label": " ".join(payload[:-1]), "url": payload[-1]}
            if not btns: btns.append(entry)
            else: btns[0] = entry
            await self.apply_activities(); await ch.send(ascii.success(f"{label} btn1: {entry['label']}"))
        elif sub == "btn2":
            if len(payload) < 2:
                await ch.send(ascii.error(f"Usage: .rpc{slot+1} btn2 <label> <url>")); return
            self._ensure_slot(slot)
            btns = self.rpc_slots[slot].setdefault("buttons", [])
            while len(btns) < 2: btns.append(None)
            btns[1] = {"label": " ".join(payload[:-1]), "url": payload[-1]}
            self.rpc_slots[slot]["buttons"] = [b for b in btns if b]
            await self.apply_activities(); await ch.send(ascii.success(f"{label} btn2: {btns[1]['label']}"))
        elif sub == "spotify":
            parts = [p.strip() for p in rest_str.split("-")] if rest_str else ["Default", "Unknown"]
            if len(parts) < 2: parts.append("Unknown")
            activity = await self.build_spotify(parts)
            if not activity:
                await ch.send(ascii.error("Format: Song - Artist")); return
            self.rpc_slots[slot] = activity
            await self.apply_activities(); await ch.send(ascii.success(f"{label} Spotify: {parts[0]}"))
        elif sub == "youtube":
            parts = [p.strip() for p in rest_str.split("-")] if rest_str else ["Default Video", "Default Channel"]
            if len(parts) < 2: parts.append("Default Channel")
            activity = await self.build_youtube(parts)
            if not activity:
                await ch.send(ascii.error("Format: Video - Channel")); return
            self.rpc_slots[slot] = activity
            await self.apply_activities(); await ch.send(ascii.success(f"{label} YouTube: {parts[0]}"))
        elif sub == "xbox":
            parts = [p.strip() for p in rest_str.split("-")] if rest_str else ["Xbox"]
            self.rpc_slots[slot] = await self.build_xbox(parts)
            await self.apply_activities(); await ch.send(ascii.success(f"{label} Xbox: {parts[0]}"))
        elif sub == "ps":
            parts = [p.strip() for p in rest_str.split("-")] if rest_str else ["PlayStation"]
            self.rpc_slots[slot] = await self.build_playstation(parts)
            await self.apply_activities(); await ch.send(ascii.success(f"{label} PS: {parts[0]}"))
        elif sub == "ps4":
            parts = [p.strip() for p in rest_str.split("-")] if rest_str else ["PS4"]
            self.rpc_slots[slot] = await self.build_playstation(parts, ps4=True)
            await self.apply_activities(); await ch.send(ascii.success(f"{label} PS4: {parts[0]}"))
        elif sub in ("crunchy", "crunchyroll"):
            parts = [p.strip() for p in rest_str.split("-")] if rest_str else ["Crunchyroll"]
            self.rpc_slots[slot] = await self.build_crunchyroll(parts)
            await self.apply_activities(); await ch.send(ascii.success(f"{label} Crunchyroll: {parts[0]}"))
        elif sub == "roblox":
            parts = [p.strip() for p in rest_str.split("-")] if rest_str else ["Roblox"]
            self.rpc_slots[slot] = await self.build_roblox(parts)
            await self.apply_activities(); await ch.send(ascii.success(f"{label} Roblox: {parts[0]}"))
        elif sub == "vrchat":
            parts = [p.strip() for p in rest_str.split("-")] if rest_str else ["Exploring VRChat", "VRChat"]
            self.rpc_slots[slot] = await self.build_vrchat(parts)
            await self.apply_activities(); await ch.send(ascii.success(f"{label} VRChat: {parts[0]}"))
        elif sub == "clear":
            self.rpc_slots[slot] = None
            await self.apply_activities(); await ch.send(ascii.info(f"{label} cleared"))

    # ── quick commands ──

    async def _handle_quick(self, message, cmd, rest):
        ch = message.channel
        words = list(rest)
        slot = 0
        if words and words[-1] in ("1", "2", "3", "4", "5", "6"):
            slot = int(words[-1]) - 1
            words = words[:-1]
        payload = " ".join(words)
        parts = [p.strip() for p in payload.split("-")] if payload else []

        if cmd == "roblox":
            if not parts: parts = ["Roblox"]
            self.rpc_slots[slot] = await self.build_roblox(parts)
        elif cmd == "spotify":
            if len(parts) < 2: parts.append("Unknown")
            a = await self.build_spotify(parts)
            if not a:
                await ch.send(ascii.error("Format: Song - Artist")); return
            self.rpc_slots[slot] = a
        elif cmd == "youtube":
            if len(parts) < 2: parts.append("Default Channel")
            a = await self.build_youtube(parts)
            if not a:
                await ch.send(ascii.error("Format: Video - Channel")); return
            self.rpc_slots[slot] = a
        elif cmd == "xbox":
            if not parts: parts = ["Xbox"]
            self.rpc_slots[slot] = await self.build_xbox(parts)
        elif cmd == "ps":
            if not parts: parts = ["PlayStation"]
            self.rpc_slots[slot] = await self.build_playstation(parts)
        elif cmd == "ps4":
            if not parts: parts = ["PS4"]
            self.rpc_slots[slot] = await self.build_playstation(parts, ps4=True)
        elif cmd == "crunchy":
            if not parts: parts = ["Crunchyroll"]
            self.rpc_slots[slot] = await self.build_crunchyroll(parts)
        elif cmd == "vrchat":
            if not parts: parts = ["Exploring VRChat", "VRChat"]
            self.rpc_slots[slot] = await self.build_vrchat(parts)
        elif cmd == "meta":
            image_url = None
            for i, w in enumerate(words):
                if w.startswith(("http://", "https://")):
                    image_url = w
                    words = words[:i] + words[i+1:]
                    break
            if words and words[-1] in ("1", "2", "3", "4", "5", "6"):
                slot = int(words[-1]) - 1
                words = words[:-1]
            payload2 = " ".join(words)
            p2 = [p.strip() for p in payload2.split("-")] if payload2 else ["Exploring VRChat", "VRChat"]
            self.rpc_slots[slot] = await self.build_vrchat(p2, image_url)

        await self.apply_activities()
        shown = parts[0] if parts else "ok"
        await ch.send(ascii.success(f"{cmd} → slot {slot+1}: {shown}"))

    # ── lifecycle ──

    async def _deferred_start(self):
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
        self._bg_tasks.append(asyncio.create_task(self.auto_refresh_presence()))
        self._bg_tasks.append(asyncio.create_task(self.auto_refresh_cdn_assets()))

    async def cog_unload(self):
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
        if self._interceptor_active and self.bot.ws is self._interceptor_ws:
            return

        for _ in range(30):
            if self.bot.ws and hasattr(self.bot.ws, 'send'):
                break
            await asyncio.sleep(0.5)
        if not self.bot.ws:
            print("[RPC] Could not attach interceptor - no websocket")
            return

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
        try:
            if self.bot.ws is not self._interceptor_ws:
                self._interceptor_active = False
            await self._start_presence_interceptor()
            if any(a is not None for a in self.rpc_slots):
                await self._push()
        except Exception as e:
            print(f"[RPC] on_ready re-attach failed: {e}")

    # ── persistence ──

    def _get_user_file(self, filename):
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
        discord_cdn_pattern = (r"https?://(?:cdn\.discordapp\.com|media\.discordapp\.net)"
                                r"/attachments/(\d+)/(\d+)/(.+)")
        match = re.search(discord_cdn_pattern, image_url)
        if match:
            channel_id, attachment_id, filename = match.groups()
            key = f"mp:attachments/{channel_id}/{attachment_id}/{filename}"
            self._asset_cache[image_url] = key
            self._asset_urls[key] = image_url
            return key
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
        game = (parts[0] if parts else "Roblox")[:128]
        now = int(time.time() * 1000)
        activity = {
            "type": 0,
            "name": "Roblox",
            "application_id": "899273624442372096",
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

    # ── status rotation helper ──

    async def _patch_custom_status(self):
        json_data = {'custom_status': {'text': self.current_status,
                                        'emoji_name': self.current_emoji}}
        async with aiohttp.ClientSession() as session:
            await session.patch(
                'https://discord.com/api/v9/users/@me/settings',
                headers={'Authorization': self.bot.http.token,
                         'Content-Type': 'application/json'},
                json=json_data)


async def setup(bot):
    await bot.add_cog(RPCCog(bot))
