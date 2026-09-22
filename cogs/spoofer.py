# cogs/spoofer.py | platform / device spoofing — patches outgoing IDENTIFY (op:2)
# frames on the wire. keeps the interceptor attached across ws reconnects via a
# background poll, so a fresh IDENTIFY after a reconnect always gets rewritten.
import asyncio
import json
import re
import time
import discord
from . import state as S


PLATFORM_PRESETS = {
    "desktop":     {"os": "Windows",  "browser": "Discord Client", "device": "",            "label": "Windows Desktop"},
    "windows":     {"os": "Windows",  "browser": "Discord Client", "device": "",            "label": "Windows Desktop"},
    "macos":       {"os": "Mac OS X", "browser": "Discord Client", "device": "",            "label": "macOS Desktop"},
    "linux":       {"os": "Linux",    "browser": "Discord Client", "device": "",            "label": "Linux Desktop"},
    "web":         {"os": "Windows",  "browser": "Chrome",         "device": "",            "label": "Web (Chrome)"},
    "browser":     {"os": "Windows",  "browser": "Chrome",         "device": "",            "label": "Web (Chrome)"},
    "phone":       {"os": "Android",  "browser": "Discord Android","device": "Android",     "label": "Phone (Android)"},
    "mobile":      {"os": "Android",  "browser": "Discord Android","device": "Android",     "label": "Phone (Android)"},
    "android":     {"os": "Android",  "browser": "Discord Android","device": "Android",     "label": "Android"},
    "ios":         {"os": "iOS",      "browser": "Discord iOS",    "device": "iPhone",      "label": "iOS"},
    "iphone":      {"os": "iOS",      "browser": "Discord iOS",    "device": "iPhone",      "label": "iPhone"},
    "ipad":        {"os": "iOS",      "browser": "Discord iOS",    "device": "iPad",        "label": "iPad"},
    "console":     {"os": "Windows",  "browser": "Discord Client", "device": "console",     "label": "Console"},
    "xbox":        {"os": "Windows",  "browser": "Discord Client", "device": "xbox",        "label": "Xbox"},
    "playstation": {"os": "Windows",  "browser": "Discord Client", "device": "playstation", "label": "PlayStation"},
    "ps":          {"os": "Windows",  "browser": "Discord Client", "device": "playstation", "label": "PlayStation"},
    "vr":          {"os": "Android",  "browser": "Discord VR",     "device": "vr",          "label": "VR Headset"},
    "quest":       {"os": "Android",  "browser": "Discord VR",     "device": "vr",          "label": "Meta Quest"},
    "embedded":    {"os": "Windows",  "browser": "Discord Embedded","device": "",           "label": "Embedded"},
}


_OP2_RE = re.compile(r'"op"\s*:\s*2\b')


def _frame_is_identify(data):
    if not isinstance(data, str):
        return False
    if not _OP2_RE.search(data):
        return False
    return '"properties"' in data


class SpooferCog:
    COMMANDS = {"platform", "spoof", "spoofer", "vr", "console",
                "spoofreset", "spoofstatus"}

    def __init__(self):
        self.bot = None
        self._interceptor_ws = None
        self._original_send = None
        self._interceptor_active = False
        self._patched_preset = None
        self._last_props = None
        self._identify_count = 0
        self._reconnect_count = 0
        self._watch_task = None

    # ── background watcher ──

    def _start_watcher(self):
        """Spawn a single background task that reattaches the interceptor
        whenever client.ws changes (i.e. after every reconnect)."""
        if self._watch_task and not self._watch_task.done():
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        self._watch_task = loop.create_task(self._ws_watcher())

    async def _ws_watcher(self):
        """Poll client.ws every 2s. If the ws object changed, re-patch it."""
        last_ws_id = None
        while True:
            try:
                client = S.CLIENT
                ws = getattr(client, "ws", None) if client else None
                if ws is not None:
                    cur_id = id(ws)
                    if cur_id != last_ws_id:
                        last_ws_id = cur_id
                        # new ws object — force reattach
                        self._interceptor_active = False
                        self._interceptor_ws = None
                        ok = await self._ensure_interceptor()
                        if ok:
                            print(f"[spoofer] reattached interceptor to new ws "
                                  f"(preset: "
                                  f"{self._patched_preset['label'] if self._patched_preset else '—'})")
            except Exception as e:
                print(f"[spoofer] watcher error: {e}")
            await asyncio.sleep(2)

    # ── interceptor ──

    async def _ensure_interceptor(self):
        client = S.CLIENT
        if client is None:
            return False

        if self._interceptor_active and getattr(client, "ws", None) is self._interceptor_ws:
            return True

        for _ in range(30):
            if getattr(client, "ws", None) and hasattr(client.ws, 'send'):
                break
            await asyncio.sleep(0.5)
        if not getattr(client, "ws", None):
            return False

        self._interceptor_ws = client.ws
        self._original_send = client.ws.send
        original = self._original_send
        cog = self

        async def patched_send(data, *args, **kwargs):
            if _frame_is_identify(data):
                try:
                    payload = json.loads(data)
                    if payload.get("op") == 2 and isinstance(payload.get("d"), dict):
                        props = payload["d"].get("properties", {})
                        preset = cog._patched_preset
                        if preset:
                            props["$os"] = preset["os"]
                            props["$browser"] = preset["browser"]
                            props["$device"] = preset["device"]
                            payload["d"]["properties"] = props
                            cog._last_props = dict(props)
                            cog._identify_count += 1
                            print(f"[spoofer] rewrote IDENTIFY #{cog._identify_count} "
                                  f"→ {preset['label']}")
                            data = json.dumps(payload)
                except Exception as e:
                    print(f"[spoofer] identify rewrite failed: {e}")
            return await original(data, *args, **kwargs)

        try:
            client.ws.send = patched_send
        except Exception as e:
            print(f"[spoofer] failed to patch ws.send: {e}")
            return False

        self._interceptor_active = True
        print("[spoofer] IDENTIFY interceptor active")
        # spin up the watcher once — it lives for the process lifetime
        self._start_watcher()
        return True

    def _current_preset_label(self):
        plat = getattr(S, "_current_platform", "desktop")
        preset = PLATFORM_PRESETS.get(plat)
        return preset["label"] if preset else plat

    async def _set_platform(self, preset_key, message):
        preset = PLATFORM_PRESETS.get(preset_key)
        if not preset:
            await message.edit(content=S.ui_err(f"unknown platform: {preset_key}"))
            return False

        if not await self._ensure_interceptor():
            await message.edit(content=S.ui_err(
                "could not attach to gateway — try again in a moment"))
            return False

        self._patched_preset = preset
        S._current_platform = preset_key
        return True

    async def _fast_reconnect(self):
        client = S.CLIENT
        if client is None:
            return

        conn = getattr(client, "_connection", None)
        ws = getattr(client, "ws", None)
        self._reconnect_count += 1

        if conn is not None:
            for attr in ("_session_id", "sequence", "_resume_gateway_url",
                         "_resume_gateway"):
                try:
                    setattr(conn, attr, None)
                except Exception:
                    pass
            try:
                conn._reconnect_attempts = 0
            except Exception:
                pass

        if ws is not None:
            try:
                await ws.close(code=1000)
            except Exception:
                try:
                    await ws.close(code=4000)
                except Exception as e:
                    print(f"[spoofer] close failed: {e}")

        try:
            await client.connect(reconnect=True)
        except TypeError:
            try:
                await client.connect()
            except Exception:
                pass
        except Exception as e:
            print(f"[spoofer] manual reconnect kick failed (non-fatal): {e}")

    # ── dispatcher ──

    async def handle(self, message, cmd, args):
        client = S.CLIENT
        if client is None:
            await message.edit(content=S.ui_err("client not ready"))
            return

        await self._ensure_interceptor()

        if cmd == "platform":
            if len(args) < 2:
                cur = getattr(S, "_current_platform", "desktop")
                lines = [f"  current: {cur}", "",
                         f"  live:    {self._current_preset_label()}", "",
                         "  available:"]
                for k in sorted(PLATFORM_PRESETS.keys()):
                    lines.append(f"    {k:<12} {PLATFORM_PRESETS[k]['label']}")
                await message.edit(content=S._ansi_block(lines))
                return

            plat = args[1].lower()
            if plat == "off":
                plat = "desktop"

            if not await self._set_platform(plat, message):
                return

            preset = PLATFORM_PRESETS[plat]
            await message.edit(content=S.ui_ok(f"platform → {preset['label']}"))
            await self._fast_reconnect()
            return

        if cmd in ("spoof", "spoofer"):
            if len(args) < 2:
                await message.edit(content=S.ui_info(
                    "usage: spoof <platform>  |  spoof status  |  spoof reset"))
                return
            sub = args[1].lower()

            if sub == "status":
                await self._send_status(message)
                return

            if sub == "reset":
                if not await self._set_platform("desktop", message):
                    return
                await message.edit(content=S.ui_ok("platform reset → Desktop"))
                await self._fast_reconnect()
                return

            if not await self._set_platform(sub, message):
                return
            preset = PLATFORM_PRESETS[sub]
            await message.edit(content=S.ui_ok(f"spoofed → {preset['label']}"))
            await self._fast_reconnect()
            return

        if cmd in ("vr", "console"):
            if not await self._set_platform(cmd, message):
                return
            preset = PLATFORM_PRESETS[cmd]
            await message.edit(content=S.ui_ok(f"platform → {preset['label']}"))
            await self._fast_reconnect()
            return

        if cmd == "spoofstatus":
            await self._send_status(message)
            return

        if cmd == "spoofreset":
            if not await self._set_platform("desktop", message):
                return
            await message.edit(content=S.ui_ok("platform reset → Desktop"))
            await self._fast_reconnect()
            return

    async def _send_status(self, message):
        p = self._last_props or {}
        ws = getattr(S.CLIENT, "ws", None)
        ws_match = (ws is self._interceptor_ws) if ws is not None else False
        lines = [
            f"  tracked:      {getattr(S, '_current_platform', '?')}",
            f"  interceptor:  {'active' if self._interceptor_active else 'inactive'}",
            f"  ws match:     {'yes' if ws_match else 'no'}",
            f"  patched:      {self._patched_preset['label'] if self._patched_preset else '—'}",
            f"  identify #:   {self._identify_count}",
            f"  reconnects:   {self._reconnect_count}",
            f"  $os:          {p.get('$os', '?')}",
            f"  $browser:     {p.get('$browser', '?')}",
            f"  $device:      {p.get('$device', '?')}",
        ]
        await message.edit(content=S._ansi_block(lines))
