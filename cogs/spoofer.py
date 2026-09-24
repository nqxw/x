# cogs/spoofer.py | platform / device spoofing — patches modifyself's
# GatewayWebSocket.send_json at the CLASS level. every ws object (initial +
# every reconnect) is covered automatically. no aiohttp patching, no frame
# matching — the IDENTIFY payload is a dict and we rewrite d.properties.
import asyncio
import json
import re
import time
import modifyself_shim as discord
from . import state as S


PLATFORM_PRESETS = {
    "desktop":     {"os": "Windows",  "browser": "Chrome",         "device": "",            "label": "Windows Desktop"},
    "windows":     {"os": "Windows",  "browser": "Chrome",         "device": "",            "label": "Windows Desktop"},
    "macos":       {"os": "Mac OS X", "browser": "Chrome",         "device": "",            "label": "macOS Desktop"},
    "linux":       {"os": "Linux",    "browser": "Chrome",         "device": "",            "label": "Linux Desktop"},
    "web":         {"os": "Windows",  "browser": "Chrome",         "device": "",            "label": "Web (Chrome)"},
    "browser":     {"os": "Windows",  "browser": "Chrome",         "device": "",            "label": "Web (Chrome)"},
    "phone":       {"os": "Android",  "browser": "Discord Android","device": "Android",     "label": "Phone (Android)"},
    "mobile":      {"os": "Android",  "browser": "Discord Android","device": "Android",     "label": "Phone (Android)"},
    "android":     {"os": "Android",  "browser": "Discord Android","device": "Android",     "label": "Android"},
    "ios":         {"os": "iOS",      "browser": "Discord iOS",    "device": "iPhone",      "label": "iOS"},
    "iphone":      {"os": "iOS",      "browser": "Discord iOS",    "device": "iPhone",      "label": "iPhone"},
    "ipad":        {"os": "iOS",      "browser": "Discord iOS",    "device": "iPad",        "label": "iPad"},
    "console":     {"os": "Windows",  "browser": "Chrome",         "device": "console",     "label": "Console"},
    "xbox":        {"os": "Windows",  "browser": "Chrome",         "device": "xbox",        "label": "Xbox"},
    "playstation": {"os": "Windows",  "browser": "Chrome",         "device": "playstation", "label": "PlayStation"},
    "ps":          {"os": "Windows",  "browser": "Chrome",         "device": "playstation", "label": "PlayStation"},
    "vr":          {"os": "Android",  "browser": "Discord VR",     "device": "vr",          "label": "VR Headset"},
    "quest":       {"os": "Android",  "browser": "Discord VR",     "device": "vr",          "label": "Meta Quest"},
    "embedded":    {"os": "Windows",  "browser": "Chrome",         "device": "",            "label": "Embedded"},
}


_PATCHED = {}
_LIVE_COG = [None]
_PRESET_HOLDER = [None]


def _rewrite_identify_dict(data: dict, preset) -> bool:
    """Rewrite an op:2 IDENTIFY payload dict in place."""
    if not isinstance(data, dict) or data.get("op") != 2:
        return False
    d = data.get("d")
    if not isinstance(d, dict):
        return False
    props = d.setdefault("properties", {})
    props["os"] = preset["os"]
    props["browser"] = preset["browser"]
    props["device"] = preset["device"]
    # some builds also read these user-facing fields
    props["browser_user_agent"] = props.get("browser_user_agent") or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")
    cog = _LIVE_COG[0]
    if cog is not None:
        cog._last_props = dict(props)
        cog._identify_count += 1
    print(f"[spoofer] rewrote IDENTIFY → {preset['label']}")
    return True


def _install_patches():
    """Class-level patch on GatewayWebSocket.send_json — idempotent."""
    try:
        from modifyself.gateway.websocket import GatewayWebSocket as _GW
    except Exception as e:
        print(f"[spoofer] cannot import GatewayWebSocket: {e}")
        return False

    if getattr(_GW, "_spoofer_patched", False):
        return True

    original_send = _GW.send_json

    async def patched_send_json(self, data):
        try:
            if isinstance(data, dict):
                preset = _PRESET_HOLDER[0]
                if preset is not None and data.get("op") == 2:
                    _rewrite_identify_dict(data, preset)
        except Exception as e:
            print(f"[spoofer] send_json patch error: {e}")
        return await original_send(self, data)

    _GW.send_json = patched_send_json
    _GW._spoofer_patched = True
    print("[spoofer] class patch installed: GatewayWebSocket.send_json")
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
        self._watchdog_task = None
        _LIVE_COG[0] = self
        _install_patches()
        self._start_watchdog()

    def _start_watchdog(self):
        if self._watchdog_task and not self._watchdog_task.done(): return
        try: loop = asyncio.get_event_loop()
        except RuntimeError: return
        self._watchdog_task = loop.create_task(self._watchdog())

    async def _watchdog(self):
        """If ws is dead for >10s and client thinks it's open, nudge."""
        dead_since = 0.0
        while True:
            try:
                client = S.CLIENT
                if client is not None:
                    gw = getattr(client, "_gateway", None)
                    if gw is not None and not gw.is_connected and not gw.is_closed:
                        if dead_since == 0.0:
                            dead_since = time.time()
                        elif time.time() - dead_since > 10:
                            print("[spoofer] watchdog: gateway stalled >10s, forcing reconnect")
                            try: await gw._reconnect()
                            except Exception as e: print(f"[spoofer] watchdog err: {e}")
                            dead_since = time.time()
                    else:
                        dead_since = 0.0
            except Exception as e:
                print(f"[spoofer] watchdog error: {e}")
            await asyncio.sleep(5)

    def _build_diag(self):
        lines = []
        try:
            from modifyself.gateway.websocket import GatewayWebSocket as _GW
            lines.append(f"GatewayWebSocket.send_json: "
                         f"{'patched' if getattr(_GW, '_spoofer_patched', False) else 'unpatched'}")
        except Exception as e:
            lines.append(f"import failed: {e}")
        lines.append(f"preset held: {_PRESET_HOLDER[0]['label'] if _PRESET_HOLDER[0] else '—'}")
        lines.append(f"identify count: {self._identify_count}")
        client = S.CLIENT
        gw = getattr(client, "_gateway", None) if client else None
        if gw is not None:
            try: lines.append(f"gateway state: {gw.get_state()}")
            except Exception as e: lines.append(f"gateway state err: {e}")
        return lines

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
        _PRESET_HOLDER[0] = preset
        S._current_platform = preset_key
        return True

    async def _safe_reconnect(self):
        """Force a fresh IDENTIFY by closing the gateway and letting
        modifyself reconnect. its `_reconnect()` clears session id and closes."""
        client = S.CLIENT
        if client is None: return
        self._reconnect_count += 1
        gw = getattr(client, "_gateway", None)
        if gw is None: return
        try:
            # clear session id so the next connection sends IDENTIFY, not RESUME
            try: gw._session_id = None
            except Exception: pass
            try: gw._sequence = None
            except Exception: pass
            try: await gw.close()
            except Exception as e: print(f"[spoofer] close failed: {e}")
            # modifyself doesn't auto-reconnect after a clean close; kick it
            asyncio.create_task(self._kick_reconnect(gw))
        except Exception as e:
            print(f"[spoofer] reconnect error: {e}")

    async def _kick_reconnect(self, gw):
        await asyncio.sleep(0.5)
        try: await gw.connect()
        except Exception as e: print(f"[spoofer] kick reconnect failed: {e}")

    async def handle(self, message, cmd, args):
        client = S.CLIENT
        if client is None:
            await message.edit(content=S.ui_err("client not ready")); return

        if cmd == "spooferdiag":
            lines = self._build_diag()
            print("[spooferdiag] ====")
            for ln in lines: print(f"[spooferdiag] {ln}")
            print("[spooferdiag] ====")
            await message.edit(content=S._ansi_block(lines)); return

        if cmd == "platform":
            if len(args) < 2:
                cur = getattr(S, "_current_platform", "desktop")
                lines = [f"  current: {cur}", "",
                         f"  live:    {self._current_preset_label()}", "",
                         "  available:"]
                for k in sorted(PLATFORM_PRESETS.keys()):
                    lines.append(f"    {k:<12} {PLATFORM_PRESETS[k]['label']}")
                await message.edit(content=S._ansi_block(lines)); return
            plat = args[1].lower()
            if plat == "off": plat = "desktop"
            if not await self._set_platform(plat, message): return
            preset = PLATFORM_PRESETS[plat]
            await message.edit(content=S.ui_ok(f"platform → {preset['label']}"))
            await self._safe_reconnect(); return

        if cmd in ("spoof", "spoofer"):
            if len(args) < 2:
                await message.edit(content=S.ui_info(
                    "usage: spoof <platform>  |  spoof status  |  spoof reset")); return
            sub = args[1].lower()
            if sub == "status":
                await self._send_status(message); return
            if sub == "reset":
                if not await self._set_platform("desktop", message): return
                await message.edit(content=S.ui_ok("platform reset → Desktop"))
                await self._safe_reconnect(); return
            if not await self._set_platform(sub, message): return
            preset = PLATFORM_PRESETS[sub]
            await message.edit(content=S.ui_ok(f"spoofed → {preset['label']}"))
            await self._safe_reconnect(); return

        if cmd in ("vr", "console"):
            if not await self._set_platform(cmd, message): return
            preset = PLATFORM_PRESETS[cmd]
            await message.edit(content=S.ui_ok(f"platform → {preset['label']}"))
            await self._safe_reconnect(); return

        if cmd == "spoofstatus":
            await self._send_status(message); return

        if cmd == "spoofreset":
            if not await self._set_platform("desktop", message): return
            await message.edit(content=S.ui_ok("platform reset → Desktop"))
            await self._safe_reconnect(); return

    async def _send_status(self, message):
        p = self._last_props or {}
        active = _PRESET_HOLDER[0]
        lines = [
            f"  tracked:      {getattr(S, '_current_platform', '?')}",
            f"  preset held:  {active['label'] if active else '—'}",
            f"  patched:      {self._patched_preset['label'] if self._patched_preset else '—'}",
            f"  identify #:   {self._identify_count}",
            f"  reconnects:   {self._reconnect_count}",
            f"  $os:          {p.get('os', '?')}",
            f"  $browser:     {p.get('browser', '?')}",
            f"  $device:      {p.get('device', '?')}",
        ]
        await message.edit(content=S._ansi_block(lines))