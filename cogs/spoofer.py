# cogs/spoofer.py | platform / device spoofing — patches modifyself's
# GatewayWebSocket.send_json at the CLASS level. every ws object (initial +
# every reconnect) is covered automatically.
#
# v3 — reconnect is owned by modifyself_shim.Client.reconnect_gateway(),
# which holds a class-level lock so no two reconnect paths ever race.
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

    # align user agent to the chosen os/browser combo, else the spoof is obvious
    ua_map = {
        ("Android", "Discord Android"): (
            "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36"),
        ("Android", "Discord VR"): (
            "Mozilla/5.0 (Linux; Android 12; Quest 3) AppleWebKit/537.36 "
            "(KHTML, like Gecko) OculusBrowser/37.0.0.0.43 "
            "SamsungBrowser/4.0 Chrome/122.0.6261.140 VR Safari/537.36"),
        ("iOS", "Discord iOS"): (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"),
        ("Windows", "Chrome"): (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"),
        ("Mac OS X", "Chrome"): (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"),
        ("Linux", "Chrome"): (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"),
    }
    key = (preset["os"], preset["browser"])
    if key in ua_map:
        props["browser_user_agent"] = ua_map[key]

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
        self._reconnect_grace_until = 0.0
        _LIVE_COG[0] = self
        _install_patches()
        self._start_watchdog()

    def _start_watchdog(self):
        if self._watchdog_task and not self._watchdog_task.done(): return
        try: loop = asyncio.get_event_loop()
        except RuntimeError: return
        self._watchdog_task = loop.create_task(self._watchdog())

    async def _watchdog(self):
        """
        Detect a stalled gateway. give modifyself a 30s grace window after a
        manual reconnect before nudging it, so we don't fight the library.
        """
        dead_since = 0.0
        while True:
            try:
                client = S.CLIENT
                if client is not None:
                    gw = getattr(client, "_gateway", None)
                    now = time.time()
                    if gw is not None and now >= self._reconnect_grace_until:
                        is_connected = bool(getattr(gw, "is_connected", False))
                        is_closed = bool(getattr(gw, "is_closed", False))
                        if not is_connected and not is_closed:
                            if dead_since == 0.0:
                                dead_since = now
                            elif now - dead_since > 15:
                                print("[spoofer] watchdog: gateway stalled >15s — "
                                      "calling reconnect_gateway")
                                try:
                                    await client.reconnect_gateway()
                                except Exception as e:
                                    print(f"[spoofer] watchdog reconnect err: {e}")
                                self._reconnect_grace_until = time.time() + 30
                                dead_since = 0.0
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
        lines.append(f"reconnect grace until: "
                     f"{max(0, int(self._reconnect_grace_until - time.time()))}s")
        client = S.CLIENT
        gw = getattr(client, "_gateway", None) if client else None
        if gw is not None:
            try: lines.append(f"gateway: {gw.get_state()}")
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
        """
        Force a fresh IDENTIFY. Routed through
        modifyself_shim.Client.reconnect_gateway() which holds a class-level
        lock, so this never races modifyself's own reconnect task.
        """
        client = S.CLIENT
        if client is None: return
        self._reconnect_count += 1
        print(f"[spoofer] reconnect #{self._reconnect_count} requested")

        self._reconnect_grace_until = time.time() + 30
        try:
            ok = await client.reconnect_gateway()
            if ok:
                print(f"[spoofer] reconnect #{self._reconnect_count} OK")
            else:
                print(f"[spoofer] reconnect #{self._reconnect_count} failed — "
                      f"watchdog will retry in 30s")
        except Exception as e:
            print(f"[spoofer] reconnect raised: {e}")

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
        gw = getattr(S.CLIENT, "_gateway", None) if S.CLIENT else None
        state_str = "—"
        if gw is not None:
            try:
                st = gw.get_state()
                state_str = f"{st.get('state')} / conn={st.get('is_connected')}"
            except Exception:
                pass
        lines = [
            f"  tracked:      {getattr(S, '_current_platform', '?')}",
            f"  preset held:  {active['label'] if active else '—'}",
            f"  patched:      {self._patched_preset['label'] if self._patched_preset else '—'}",
            f"  identify #:   {self._identify_count}",
            f"  reconnects:   {self._reconnect_count}",
            f"  gateway:      {state_str}",
            f"  $os:          {p.get('os', '?')}",
            f"  $browser:     {p.get('browser', '?')}",
            f"  $device:      {p.get('device', '?')}",
        ]
        await message.edit(content=S._ansi_block(lines))
