# cogs/spoofer.py | platform / device spoofing — patches outgoing IDENTIFY (op:2)
# frames on the wire instead of chasing whatever attribute the client stores
# properties under. survives discord.py-self forks and internal refactors.
import asyncio
import json
import time
import discord
from . import state as S


# Discord's gateway IDENTIFY accepts these in properties.$os / $browser / $device.
# the values below are what Discord's official clients send from each platform.
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


class SpooferCog:
    COMMANDS = {"platform", "spoof", "spoofer", "vr", "console",
                "spoofreset", "spoofstatus"}

    def __init__(self):
        self.bot = None
        self._interceptor_ws = None
        self._original_send = None
        self._interceptor_active = False
        self._patched_preset = None     # the preset currently being sent
        self._last_props = None         # last written properties (for status)

    # ── interceptor ──

    async def _ensure_interceptor(self):
        """Attach or re-attach the op:2 IDENTIFY patch to the current ws."""
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
            # intercept IDENTIFY frames (op:2) and rewrite properties.$os/$browser/$device
            if isinstance(data, str) and '"op":2' in data:
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
                            data = json.dumps(payload)
                except Exception:
                    pass
            return await original(data, *args, **kwargs)

        try:
            client.ws.send = patched_send
        except Exception as e:
            print(f"[spoofer] failed to patch ws.send: {e}")
            return False

        self._interceptor_active = True
        print("[spoofer] IDENTIFY interceptor active")
        return True

    def _current_preset_label(self):
        plat = getattr(S, "_current_platform", "desktop")
        preset = PLATFORM_PRESETS.get(plat)
        return preset["label"] if preset else plat

    async def _set_platform(self, preset_key, message):
        """Set the preset that will be injected into the next IDENTIFY."""
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

    async def _reconnect(self):
        """Drop the gateway so the next IDENTIFY uses the injected properties."""
        client = S.CLIENT
        try:
            ws = getattr(client, "ws", None)
            if ws:
                await ws.close(code=4000)
        except Exception as e:
            print(f"[spoofer] reconnect failed: {e}")

    # ── dispatcher ──

    async def handle(self, message, cmd, args):
        client = S.CLIENT
        if client is None:
            await message.edit(content=S.ui_err("client not ready"))
            return

        # attach the interceptor lazily on first command
        await self._ensure_interceptor()

        # ── platform ──
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
            await self._reconnect()
            return

        # ── spoof / spoofer ──
        if cmd in ("spoof", "spoofer"):
            if len(args) < 2:
                await message.edit(content=S.ui_info(
                    "usage: spoof <platform>  |  spoof status  |  spoof reset"))
                return
            sub = args[1].lower()

            if sub == "status":
                p = self._last_props or {}
                lines = [
                    f"  tracked:   {getattr(S, '_current_platform', '?')}",
                    f"  interceptor: {'active' if self._interceptor_active else 'inactive'}",
                    f"  $os:       {p.get('$os', '?')}",
                    f"  $browser:  {p.get('$browser', '?')}",
                    f"  $device:   {p.get('$device', '?')}",
                ]
                await message.edit(content=S._ansi_block(lines))
                return

            if sub == "reset":
                if not await self._set_platform("desktop", message):
                    return
                await message.edit(content=S.ui_ok("platform reset → Desktop"))
                await self._reconnect()
                return

            if not await self._set_platform(sub, message):
                return
            preset = PLATFORM_PRESETS[sub]
            await message.edit(content=S.ui_ok(f"spoofed → {preset['label']}"))
            await self._reconnect()
            return

        # ── vr / console ──
        if cmd in ("vr", "console"):
            if not await self._set_platform(cmd, message):
                return
            preset = PLATFORM_PRESETS[cmd]
            await message.edit(content=S.ui_ok(f"platform → {preset['label']}"))
            await self._reconnect()
            return

        # ── spoofstatus ──
        if cmd == "spoofstatus":
            p = self._last_props or {}
            lines = [
                f"  tracked:     {getattr(S, '_current_platform', '?')}",
                f"  interceptor: {'active' if self._interceptor_active else 'inactive'}",
                f"  patched:     {self._patched_preset['label'] if self._patched_preset else '—'}",
                f"  $os:         {p.get('$os', '?')}",
                f"  $browser:    {p.get('$browser', '?')}",
                f"  $device:     {p.get('$device', '?')}",
            ]
            await message.edit(content=S._ansi_block(lines))
            return

        # ── spoofreset ──
        if cmd == "spoofreset":
            if not await self._set_platform("desktop", message):
                return
            await message.edit(content=S.ui_ok("platform reset → Desktop"))
            await self._reconnect()
            return
