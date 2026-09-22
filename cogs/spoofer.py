# cogs/spoofer.py | platform / device spoofing.
# multi-layer class patch: DiscordWebSocket._sendstr / .send / .send_as_json
# plus aiohttp.ClientWebSocketResponse.send_str / .send_json / .send_bytes.
# whatever path the IDENTIFY uses, one of these six catches it.
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
_PATCHED = {}                # id(obj) -> {"attr": original_callable}
_LIVE_COG = [None]
_PRESET_HOLDER = [None]


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


def _rewrite_frame(data):
    """Rewrite a raw JSON string IDENTIFY frame. Returns new string or None."""
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
    cog = _LIVE_COG[0]
    if cog is not None:
        cog._last_props = dict(props)
        cog._identify_count += 1
    print(f"[spoofer] rewrote IDENTIFY → {preset['label']}")
    return json.dumps(payload)


def _rewrite_payload_dict(payload):
    """Rewrite a dict IDENTIFY payload in place. Returns True if rewritten."""
    preset = _PRESET_HOLDER[0]
    if preset is None or not _is_identify_payload(payload):
        return False
    props = payload["d"]["properties"]
    props["$os"] = preset["os"]
    props["$browser"] = preset["browser"]
    props["$device"] = preset["device"]
    cog = _LIVE_COG[0]
    if cog is not None:
        cog._last_props = dict(props)
        cog._identify_count += 1
    print(f"[spoofer] rewrote IDENTIFY (dict) → {preset['label']}")
    return True


def _patch_class(cls, attr, wrapper_factory, kind):
    """Idempotent class patch. kind is 'str', 'dict', or 'bytes'."""
    if not callable(getattr(cls, attr, None)):
        return False
    key = (id(cls), attr)
    if key in _PATCHED:
        return True

    original = getattr(cls, attr)
    wrapper = wrapper_factory(original, kind)
    try:
        setattr(cls, attr, wrapper)
        _PATCHED[key] = {"cls": cls, "attr": attr, "original": original}
        print(f"[spoofer] patched {cls.__name__}.{attr} ({kind})")
        return True
    except Exception as e:
        print(f"[spoofer] failed to patch {cls.__name__}.{attr}: {e}")
        return False


def _install_patches():
    """Install all send-path patches. Idempotent."""

    # ── layer 1: aiohttp raw socket ──
    try:
        import aiohttp
        aws = aiohttp.ClientWebSocketResponse

        def _mk_str(original, _kind):
            async def wrapper(self, data, *a, **kw):
                try:
                    r = _rewrite_frame(data)
                    if r is not None:
                        data = r
                except Exception as e:
                    print(f"[spoofer] str patch error: {e}")
                return await original(self, data, *a, **kw)
            return wrapper

        def _mk_dict(original, _kind):
            async def wrapper(self, data, *a, **kw):
                try:
                    if isinstance(data, dict):
                        _rewrite_payload_dict(data)
                except Exception as e:
                    print(f"[spoofer] dict patch error: {e}")
                return await original(self, data, *a, **kw)
            return wrapper

        def _mk_bytes(original, _kind):
            async def wrapper(self, data, *a, **kw):
                try:
                    if isinstance(data, (bytes, bytearray)):
                        text = data.decode("utf-8", "ignore")
                        r = _rewrite_frame(text)
                        if r is not None:
                            data = r.encode("utf-8")
                except Exception as e:
                    print(f"[spoofer] bytes patch error: {e}")
                return await original(self, data, *a, **kw)
            return wrapper

        _patch_class(aws, "send_str", _mk_str, "str")
        _patch_class(aws, "send_json", _mk_dict, "dict")
        _patch_class(aws, "send_bytes", _mk_bytes, "bytes")
    except Exception as e:
        print(f"[spoofer] aiohttp patch layer failed: {e}")

    # ── layer 2: discord.py-self ws wrapper ──
    ws_cls = None
    try:
        from discord.gateway import DiscordWebSocket as _ws_cls
        ws_cls = _ws_cls
    except Exception:
        pass
    if ws_cls is None:
        try:
            import discord.gateway as _gw
            for name in dir(_gw):
                obj = getattr(_gw, name, None)
                if isinstance(obj, type) and name.lower().endswith("websocket"):
                    ws_cls = obj
                    break
        except Exception:
            pass

    if ws_cls is not None:
        # _sendstr — async, takes a raw string
        def _mk_ws_str(original, _kind):
            async def wrapper(self, data, *a, **kw):
                try:
                    r = _rewrite_frame(data)
                    if r is not None:
                        data = r
                except Exception as e:
                    print(f"[spoofer] ws._sendstr error: {e}")
                return await original(self, data, *a, **kw)
            return wrapper

        # send — async, takes a raw string (older self builds)
        def _mk_ws_send(original, _kind):
            async def wrapper(self, data, *a, **kw):
                try:
                    r = _rewrite_frame(data)
                    if r is not None:
                        data = r
                except Exception as e:
                    print(f"[spoofer] ws.send error: {e}")
                return await original(self, data, *a, **kw)
            return wrapper

        # send_as_json — async, takes a dict
        def _mk_ws_json(original, _kind):
            async def wrapper(self, payload, *a, **kw):
                try:
                    if isinstance(payload, dict):
                        _rewrite_payload_dict(payload)
                except Exception as e:
                    print(f"[spoofer] ws.send_as_json error: {e}")
                return await original(self, payload, *a, **kw)
            return wrapper

        _patch_class(ws_cls, "_sendstr", _mk_ws_str, "str")
        _patch_class(ws_cls, "send", _mk_ws_str, "str")
        _patch_class(ws_cls, "send_as_json", _mk_ws_json, "dict")

    return bool(_PATCHED)


class SpooferCog:
    COMMANDS = {"platform", "spoof", "spoofer", "vr", "console",
                "spoofreset", "spoofstatus", "spooferdiag"}

    def __init__(self):
        self.bot = None
        self._patched_preset = None
        self._last_props = None
        self._identify_count = 0
        self._reconnect_count = 0
        _LIVE_COG[0] = self
        _install_patches()

    def _build_diag(self):
        lines = []
        try:
            import aiohttp
            aws = aiohttp.ClientWebSocketResponse
            for attr in ("send_str", "send_json", "send_bytes"):
                m = getattr(aws, attr, None)
                lines.append(f"aiohttp.{attr}: "
                             f"{'patched' if (id(aws), attr) in _PATCHED else 'unpatched'}")
        except Exception as e:
            lines.append(f"aiohttp inspect failed: {e}")
        try:
            from discord.gateway import DiscordWebSocket as _ws
            for attr in ("_sendstr", "send", "send_as_json"):
                m = getattr(_ws, attr, None)
                lines.append(f"DiscordWebSocket.{attr}: "
                             f"{'patched' if (id(_ws), attr) in _PATCHED else 'unpatched'}")
        except Exception as e:
            lines.append(f"ws class inspect failed: {e}")
        lines.append(f"preset held: {_PRESET_HOLDER[0]['label'] if _PRESET_HOLDER[0] else '—'}")
        lines.append(f"identify count: {self._identify_count}")
        lines.append(f"patched attr count: {len(_PATCHED)}")
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
            f"  patched attrs:{len(_PATCHED)}",
            f"  patched:      {self._patched_preset['label'] if self._patched_preset else '—'}",
            f"  identify #:   {self._identify_count}",
            f"  reconnects:   {self._reconnect_count}",
            f"  $os:          {p.get('$os', '?')}",
            f"  $browser:     {p.get('$browser', '?')}",
            f"  $device:      {p.get('$device', '?')}",
        ]
        await message.edit(content=S._ansi_block(lines))
