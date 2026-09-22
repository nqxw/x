# cogs/spoofer.py | platform / device spoofing.
# patches aiohttp.ClientWebSocketResponse.send_str at the CLASS level — the
# lowest-level write path every gateway frame goes through. survives every
# reconnect, every cog rebuild, every library refactor above it.
import asyncio
import json
import re
import time
import inspect
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
_PATCHED_CLASSES = {}        # cls -> {"send_str": original, "send_json": original, ...}
_LIVE_COG = [None]           # holds the current SpooferCog instance
_PRESET_HOLDER = [None]      # holds the currently active preset dict (survives cog rebuilds)


def _is_identify_frame(data):
    if not isinstance(data, str):
        return False
    if not _OP2_RE.search(data):
        return False
    return '"properties"' in data


def _is_identify_payload(payload):
    try:
        return (isinstance(payload, dict)
                and payload.get("op") == 2
                and isinstance(payload.get("d"), dict)
                and isinstance(payload["d"].get("properties"), dict))
    except Exception:
        return False


def _rewrite_in_frame(data):
    """Given a JSON string that is an IDENTIFY frame, rewrite its properties.
    Return the new string. Return None if it isn't one."""
    if not _is_identify_frame(data):
        return None
    preset = _PRESET_HOLDER[0]
    if preset is None:
        return None
    try:
        payload = json.loads(data)
    except Exception:
        return None
    if not _is_identify_payload(payload):
        return None
    props = payload["d"]["properties"]
    props["$os"] = preset["os"]
    props["$browser"] = preset["browser"]
    props["$device"] = preset["device"]
    # record for status
    cog = _LIVE_COG[0]
    if cog is not None:
        cog._last_props = dict(props)
        cog._identify_count += 1
    print(f"[spoofer] rewrote IDENTIFY → {preset['label']}")
    return json.dumps(payload)


def _patch_aiohttp_class():
    """Patch aiohttp.ClientWebSocketResponse.send_str (and send_json) at the
    class level. every gateway write goes through one of these."""
    try:
        import aiohttp
    except Exception as e:
        print(f"[spoofer] aiohttp import failed: {e}")
        return False

    cls = aiohttp.ClientWebSocketResponse
    if cls in _PATCHED_CLASSES:
        return True

    originals = {}

    # send_str — the raw string path (most likely what IDENTIFY uses)
    if callable(getattr(cls, "send_str", None)):
        original = cls.send_str
        originals["send_str"] = original

        async def patched_send_str(self, data, *args, **kwargs):
            try:
                rewritten = _rewrite_in_frame(data)
                if rewritten is not None:
                    data = rewritten
            except Exception as e:
                print(f"[spoofer] send_str rewrite error: {e}")
            return await original(self, data, *args, **kwargs)

        cls.send_str = patched_send_str
        print("[spoofer] patched aiohttp.ClientWebSocketResponse.send_str")

    # send_json — dict path (the library may build payload as dict then json-dump it here)
    if callable(getattr(cls, "send_json", None)):
        original = cls.send_json
        originals["send_json"] = original

        async def patched_send_json(self, data, *args, **kwargs):
            try:
                preset = _PRESET_HOLDER[0]
                if preset is not None and _is_identify_payload(data):
                    props = data["d"]["properties"]
                    props["$os"] = preset["os"]
                    props["$browser"] = preset["browser"]
                    props["$device"] = preset["device"]
                    cog = _LIVE_COG[0]
                    if cog is not None:
                        cog._last_props = dict(props)
                        cog._identify_count += 1
                    print(f"[spoofer] rewrote IDENTIFY (json) → {preset['label']}")
            except Exception as e:
                print(f"[spoofer] send_json rewrite error: {e}")
            return await original(self, data, *args, **kwargs)

        cls.send_json = patched_send_json
        print("[spoofer] patched aiohttp.ClientWebSocketResponse.send_json")

    _PATCHED_CLASSES[cls] = originals
    return True


class SpooferCog:
    COMMANDS = {"platform", "spoof", "spoofer", "vr", "console",
                "spoofreset", "spoofstatus", "spooferdiag"}

    def __init__(self):
        self.bot = None
        self._patched_preset = None
        self._last_props = None
        self._identify_count = 0
        self._reconnect_count = 0
        # register self as the live cog
        _LIVE_COG[0] = self
        # install the class patch — happens once per process
        _patch_aiohttp_class()

    # ── diagnostics ──

    def _build_diag(self):
        lines = []
        try:
            import aiohttp
            cls = aiohttp.ClientWebSocketResponse
            m = getattr(cls, "send_str", None)
            lines.append(f"aiohttp.send_str: {'patched' if getattr(m, '__name__', '') == 'patched_send_str' else getattr(m, '__name__', 'missing')}")
            m2 = getattr(cls, "send_json", None)
            lines.append(f"aiohttp.send_json: {'patched' if getattr(m2, '__name__', '') == 'patched_send_json' else getattr(m2, '__name__', 'missing')}")
        except Exception as e:
            lines.append(f"aiohttp inspect failed: {e}")
        lines.append(f"preset holder: {_PRESET_HOLDER[0]['label'] if _PRESET_HOLDER[0] else '—'}")
        lines.append(f"identify count: {self._identify_count}")
        lines.append(f"last props: {self._last_props}")
        client = S.CLIENT
        ws = getattr(client, "ws", None) if client else None
        lines.append(f"ws: {type(ws).__name__ if ws else 'None'}")
        lines.append(f"ws.socket: {type(getattr(ws, 'socket', None)).__name__ if ws else 'None'}")
        return lines

    # ── command path ──

    def _current_preset_label(self):
        plat = getattr(S, "_current_platform", "desktop")
        preset = PLATFORM_PRESETS.get(plat)
        return preset["label"] if preset else plat

    async def _set_platform(self, preset_key, message):
        preset = PLATFORM_PRESETS.get(preset_key)
        if not preset:
            await message.edit(content=S.ui_err(f"unknown platform: {preset_key}"))
            return False
        self._patched_preset = preset
        _PRESET_HOLDER[0] = preset   # survives cog rebuilds
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
                         "_resume_gateway", "session_id"):
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

        if cmd == "spooferdiag":
            lines = self._build_diag()
            print("[spooferdiag] ====")
            for ln in lines:
                print(f"[spooferdiag] {ln}")
            print("[spooferdiag] ====")
            await message.edit(content=S._ansi_block(lines))
            return

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
                await self._send_status(message); return
            if sub == "reset":
                if not await self._set_platform("desktop", message):
                    return
                await message.edit(content=S.ui_ok("platform reset → Desktop"))
                await self._fast_reconnect(); return
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
            await self._send_status(message); return

        if cmd == "spoofreset":
            if not await self._set_platform("desktop", message):
                return
            await message.edit(content=S.ui_ok("platform reset → Desktop"))
            await self._fast_reconnect(); return

    async def _send_status(self, message):
        p = self._last_props or {}
        active = _PRESET_HOLDER[0]
        lines = [
            f"  tracked:      {getattr(S, '_current_platform', '?')}",
            f"  preset held:  {active['label'] if active else '—'}",
            f"  class patch:  {'installed' if _PATCHED_CLASSES else 'missing'}",
            f"  patched:      {self._patched_preset['label'] if self._patched_preset else '—'}",
            f"  identify #:   {self._identify_count}",
            f"  reconnects:   {self._reconnect_count}",
            f"  $os:          {p.get('$os', '?')}",
            f"  $browser:     {p.get('$browser', '?')}",
            f"  $device:      {p.get('$device', '?')}",
        ]
        await message.edit(content=S._ansi_block(lines))
