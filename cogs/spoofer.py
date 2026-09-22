# cogs/spoofer.py | platform / device spoofing — reconnect gateway with modified IDENTIFY properties
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


def _current_props(client):
    """Best-effort read of the live IDENTIFY properties block."""
    try:
        conn = getattr(client, "_connection", None)
        if conn is not None and getattr(conn, "_properties", None):
            return conn._properties
    except Exception:
        pass
    try:
        ws = getattr(client, "ws", None)
        if ws is not None and hasattr(ws, "_connection"):
            return getattr(ws._connection, "_properties", None)
    except Exception:
        pass
    return None


def _apply_props(client, preset):
    """Write os/browser/device into the IDENTIFY properties block.
    discord.py-self reads properties at IDENTIFY time from client._connection._properties."""
    props = _current_props(client)
    if props is None:
        return False
    props["$os"] = preset["os"]
    props["$browser"] = preset["browser"]
    props["$device"] = preset["device"]
    return True


async def _reconnect(client):
    """Drop the gateway so the next IDENTIFY uses the modified properties."""
    try:
        ws = getattr(client, "ws", None)
        if ws:
            await ws.close(code=4000)
    except Exception as e:
        print(f"[spoofer] reconnect failed: {e}")


class SpooferCog:
    COMMANDS = {"platform", "spoof", "spoofer", "vr", "console",
                "spoofreset", "spoofstatus"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT

        # ── platform <type> | platform | platform off ──
        if cmd == "platform":
            if len(args) < 2:
                cur = S._current_platform
                lines = [f"  current: {cur}", "", "  available:"]
                for k in sorted(PLATFORM_PRESETS.keys()):
                    lines.append(f"    {k:<12} {PLATFORM_PRESETS[k]['label']}")
                await message.edit(content=S._ansi_block(lines))
                return

            plat = args[1].lower()
            if plat == "off":
                plat = "desktop"

            preset = PLATFORM_PRESETS.get(plat)
            if not preset:
                await message.edit(content=S.ui_err(f"unknown platform: {plat}"))
                return

            if not _apply_props(client, preset):
                await message.edit(content=S.ui_err(
                    "could not reach IDENTIFY properties — reconnect manually"))
                return

            S._current_platform = plat
            await message.edit(content=S.ui_ok(f"platform → {preset['label']}"))
            await _reconnect(client)
            return

        # ── spoof / spoofer ──
        if cmd in ("spoof", "spoofer"):
            if len(args) < 2:
                await message.edit(content=S.ui_info(
                    "usage: spoof <platform>  |  spoof status  |  spoof reset"))
                return
            sub = args[1].lower()

            if sub == "status":
                props = _current_props(client) or {}
                lines = [
                    f"  tracked:   {S._current_platform}",
                    f"  $os:       {props.get('$os', '?')}",
                    f"  $browser:  {props.get('$browser', '?')}",
                    f"  $device:   {props.get('$device', '?')}",
                ]
                await message.edit(content=S._ansi_block(lines))
                return

            if sub == "reset":
                preset = PLATFORM_PRESETS["desktop"]
                _apply_props(client, preset)
                S._current_platform = "desktop"
                await message.edit(content=S.ui_ok("platform reset → Desktop"))
                await _reconnect(client)
                return

            preset = PLATFORM_PRESETS.get(sub)
            if not preset:
                await message.edit(content=S.ui_err(f"unknown platform: {sub}"))
                return
            if not _apply_props(client, preset):
                await message.edit(content=S.ui_err("could not set properties"))
                return
            S._current_platform = sub
            await message.edit(content=S.ui_ok(f"spoofed → {preset['label']}"))
            await _reconnect(client)
            return

        # ── vr / console shortcuts ──
        if cmd in ("vr", "console"):
            preset = PLATFORM_PRESETS.get(cmd)
            if not preset:
                await message.edit(content=S.ui_err(f"no preset for {cmd}"))
                return
            if not _apply_props(client, preset):
                await message.edit(content=S.ui_err("could not set properties"))
                return
            S._current_platform = cmd
            await message.edit(content=S.ui_ok(f"platform → {preset['label']}"))
            await _reconnect(client)
            return

        # ── spoofstatus / spoofreset ──
        if cmd == "spoofstatus":
            props = _current_props(client) or {}
            lines = [
                f"  tracked:   {S._current_platform}",
                f"  $os:       {props.get('$os', '?')}",
                f"  $browser:  {props.get('$browser', '?')}",
                f"  $device:   {props.get('$device', '?')}",
            ]
            await message.edit(content=S._ansi_block(lines))
            return

        if cmd == "spoofreset":
            preset = PLATFORM_PRESETS["desktop"]
            _apply_props(client, preset)
            S._current_platform = "desktop"
            await message.edit(content=S.ui_ok("platform reset → Desktop"))
            await _reconnect(client)
            return
