# cogs/spoofer.py | platform / device spoofing.
# - patches the IDENTIFY send path (class-level, survives reconnects)
# - safe reconnect that doesn't kill the client
# - watchdog that recovers a stalled ws
# - MULTI-SESSION: spawns concurrent auxiliary gateway connections on the
#   same token, one per platform, so the profile shows multiple platform
#   indicators at once (same mechanism Discord's own multi-device uses)
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


# canonical platforms — one entry per distinct OS/browser/device triple.
# aliases (windows→desktop, phone→mobile, quest→vr, ps→playstation) collapse.
UNIQUE_PLATFORMS = [
    "desktop", "macos", "linux", "web", "mobile",
    "ios", "ipad", "console", "xbox", "playstation",
    "vr", "embedded",
]


_OP2_RE = re.compile(r'"op"\s*:\s*2\b')
_PATCHED = {}
_LIVE_COG = [None]
_PRESET_HOLDER = [None]
_AUX_SESSIONS = {}   # platform_key -> {"task": asyncio.Task, "ws": ws, "session": aiohttp.ClientSession}


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
                "spoofreset", "spoofstatus", "spooferdiag",
                "spoofmulti", "spoofall"}

    def __init__(self):
        self.bot = None
        self._patched_preset = None
        self._last_props = None
        self._identify_count = 0
        self._reconnect_count = 0
        self._reconnect_in_progress = False
        self._last_reconnect_ts = 0.0
        self._watchdog_task = None
        _LIVE_COG[0] = self
        _install_patches()
        self._start_watchdog()

    # ── watchdog ──

    def _start_watchdog(self):
        if self._watchdog_task and not self._watchdog_task.done():
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        self._watchdog_task = loop.create_task(self._watchdog())

    async def _watchdog(self):
        dead_since = 0.0
        while True:
            try:
                client = S.CLIENT
                if client is not None:
                    ws = getattr(client, "ws", None)
                    closed = False
                    try:
                        closed = bool(ws and getattr(ws, "_closed", False))
                    except Exception:
                        pass
                    if (ws is None or closed) and not client.is_closed():
                        if dead_since == 0.0:
                            dead_since = time.time()
                        elif time.time() - dead_since > 10:
                            print("[spoofer] watchdog: ws dead >10s, nudging reconnect")
                            try:
                                if not self._reconnect_in_progress:
                                    await self._safe_reconnect()
                            except Exception as e:
                                print(f"[spoofer] watchdog reconnect err: {e}")
                            dead_since = time.time()
                    else:
                        dead_since = 0.0
            except Exception as e:
                print(f"[spoofer] watchdog error: {e}")
            await asyncio.sleep(5)

    # ── main-session reconnect ──

    async def _safe_reconnect(self):
        client = S.CLIENT
        if client is None:
            return
        now = time.time()
        if self._reconnect_in_progress:
            return
        if now - self._last_reconnect_ts < 3.0:
            return
        self._reconnect_in_progress = True
        self._last_reconnect_ts = now
        self._reconnect_count += 1

        try:
            conn = getattr(client, "_connection", None)
            if conn is not None:
                try:
                    conn._session_id = None
                except Exception:
                    pass
                try:
                    conn._reconnect_attempts = 0
                except Exception:
                    pass
            ws = getattr(client, "ws", None)
            if ws is not None:
                try:
                    await ws.close(code=1000)
                except Exception:
                    try:
                        await ws.close(code=4000)
                    except Exception as e:
                        print(f"[spoofer] ws close failed: {e}")
        finally:
            asyncio.get_event_loop().call_later(3.0, self._clear_reconnect_flag)

    def _clear_reconnect_flag(self):
        self._reconnect_in_progress = False

    # ══════════════════════════════════════════════════════════════════
    #  MULTI-SESSION — raw gateway connections per platform
    # ══════════════════════════════════════════════════════════════════

    async def _aux_gateway_loop(self, platform_key, preset):
        """Maintain a raw gateway connection identifying as `preset`.
        Each aux session tags the account with its own platform, and Discord
        merges all active sessions into the platform indicator row."""
        try:
            import aiohttp
        except Exception as e:
            print(f"[spoofer] aux {platform_key}: aiohttp import failed: {e}")
            return

        url = "wss://gateway.discord.gg/?v=9&encoding=json"
        session = None
        ws = None
        hb_task = None
        try:
            session = aiohttp.ClientSession()
            ws = await session.ws_connect(url, heartbeat=None, timeout=30)
            _AUX_SESSIONS[platform_key]["ws"] = ws
            _AUX_SESSIONS[platform_key]["session"] = session

            # read Hello (op 10)
            hello_msg = await ws.receive_json()
            if hello_msg.get("op") != 10:
                print(f"[spoofer] aux {platform_key}: expected Hello, got op {hello_msg.get('op')}")
                return
            hb_interval = hello_msg["d"]["heartbeat_interval"] / 1000.0

            # send IDENTIFY with spoofed properties
            token = getattr(S, "TOKEN", None) or ""
            if not token:
                print(f"[spoofer] aux {platform_key}: no token in state")
                return

            identify = {
                "op": 2,
                "d": {
                    "token": token,
                    "capabilities": 16381,
                    "properties": {
                        "os": preset["os"],
                        "browser": preset["browser"],
                        "device": preset["device"],
                    },
                    "presence": {
                        "status": "online",
                        "since": 0,
                        "activities": [],
                        "afk": False,
                    },
                    "compress": False,
                    "client_state": {"guild_versions": {}},
                }
            }
            await ws.send_json(identify)
            print(f"[spoofer] aux {platform_key}: sent IDENTIFY as {preset['label']}")

            # heartbeat task
            hb_task = asyncio.create_task(self._aux_heartbeat(ws, hb_interval, platform_key))

            # drain the socket — anything that arrives (Ready, heartbeats ack, etc)
            async for msg in ws:
                # we don't process events; we just keep the session alive
                pass
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[spoofer] aux {platform_key} error: {type(e).__name__}: {e}")
        finally:
            if hb_task and not hb_task.done():
                hb_task.cancel()
            try:
                if ws is not None and not ws.closed:
                    await ws.close()
            except Exception:
                pass
            try:
                if session is not None:
                    await session.close()
            except Exception:
                pass
            print(f"[spoofer] aux {platform_key}: session closed")

    async def _aux_heartbeat(self, ws, interval, platform_key):
        """Send op 1 heartbeat at Discord's interval. keep-alive."""
        try:
            while True:
                await asyncio.sleep(interval)
                if ws.closed:
                    return
                await ws.send_json({"op": 1, "d": None})
        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"[spoofer] aux {platform_key} heartbeat err: {e}")

    async def _spawn_aux(self, platform_key):
        """Spawn an auxiliary gateway session for `platform_key`.
        Returns (ok: bool, message: str)."""
        if platform_key in _AUX_SESSIONS:
            return False, f"{platform_key} already active"
        preset = PLATFORM_PRESETS.get(platform_key)
        if not preset:
            return False, f"unknown platform: {platform_key}"

        task = asyncio.create_task(self._aux_gateway_loop(platform_key, preset))
        _AUX_SESSIONS[platform_key] = {
            "task": task,
            "ws": None,
            "session": None,
        }
        # small delay so Discord doesn't rate-limit concurrent IDENTIFYs
        await asyncio.sleep(1.5)
        return True, preset["label"]

    async def _kill_aux(self, platform_key):
        entry = _AUX_SESSIONS.pop(platform_key, None)
        if not entry:
            return False
        task = entry.get("task")
        ws = entry.get("ws")
        session = entry.get("session")
        if task and not task.done():
            task.cancel()
            try:
                await task
            except Exception:
                pass
        try:
            if ws is not None and not ws.closed:
                await ws.close()
        except Exception:
            pass
        try:
            if session is not None:
                await session.close()
        except Exception:
            pass
        return True

    async def _kill_all_aux(self):
        keys = list(_AUX_SESSIONS.keys())
        for k in keys:
            try:
                await self._kill_aux(k)
            except Exception:
                pass
        return len(keys)

    # ── diag ──

    def _build_diag(self):
        lines = []
        try:
            import aiohttp
            aws = aiohttp.ClientWebSocketResponse
            for attr in ("send_str", "send_json", "send_bytes"):
                lines.append(f"aiohttp.{attr}: "
                             f"{'patched' if (id(aws), attr) in _PATCHED else 'unpatched'}")
        except Exception as e:
            lines.append(f"aiohttp inspect failed: {e}")
        try:
            from discord.gateway import DiscordWebSocket as _ws
            for attr in ("_sendstr", "send", "send_as_json"):
                lines.append(f"DiscordWebSocket.{attr}: "
                             f"{'patched' if (id(_ws), attr) in _PATCHED else 'unpatched'}")
        except Exception as e:
            lines.append(f"ws class inspect failed: {e}")
        lines.append(f"preset held: {_PRESET_HOLDER[0]['label'] if _PRESET_HOLDER[0] else '—'}")
        lines.append(f"identify count: {self._identify_count}")
        lines.append(f"patched attr count: {len(_PATCHED)}")
        lines.append(f"reconnect in progress: {self._reconnect_in_progress}")
        lines.append(f"aux sessions: {len(_AUX_SESSIONS)} [{','.join(_AUX_SESSIONS.keys())}]")
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

    # ── dispatcher ──

    async def handle(self, message, cmd, args):
        client = S.CLIENT
        if client is None:
            await message.edit(content=S.ui_err("client not ready"))
            return

        # ── diag ──
        if cmd == "spooferdiag":
            lines = self._build_diag()
            print("[spooferdiag] ====")
            for ln in lines:
                print(f"[spooferdiag] {ln}")
            print("[spooferdiag] ====")
            await message.edit(content=S._ansi_block(lines))
            return

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
            await self._safe_reconnect()
            return

        # ── spoof / spoofer ──
        if cmd in ("spoof", "spoofer"):
            if len(args) < 2:
                await message.edit(content=S.ui_info(
                    "usage: spoof <platform>  |  spoof status  |  spoof reset"))
                return
            sub = args[1].lower()
            if sub == "status":
                await self._send_status(message); return
            if sub == "reset":
                await self._kill_all_aux()
                if not await self._set_platform("desktop", message):
                    return
                await message.edit(content=S.ui_ok("platform reset → Desktop, aux sessions cleared"))
                await self._safe_reconnect(); return
            if not await self._set_platform(sub, message):
                return
            preset = PLATFORM_PRESETS[sub]
            await message.edit(content=S.ui_ok(f"spoofed → {preset['label']}"))
            await self._safe_reconnect()
            return

        # ── vr / console ──
        if cmd in ("vr", "console"):
            if not await self._set_platform(cmd, message):
                return
            preset = PLATFORM_PRESETS[cmd]
            await message.edit(content=S.ui_ok(f"platform → {preset['label']}"))
            await self._safe_reconnect()
            return

        # ── spoofstatus ──
        if cmd == "spoofstatus":
            await self._send_status(message); return

        # ── spoofreset ──
        if cmd == "spoofreset":
            n = await self._kill_all_aux()
            if not await self._set_platform("desktop", message):
                return
            await message.edit(content=S.ui_ok(
                f"platform reset → Desktop, {n} aux session(s) cleared"))
            await self._safe_reconnect(); return

        # ── spoofall — spawn EVERY unique platform as an aux session ──
        if cmd == "spoofall":
            await message.edit(content=S.ui_info(
                f"spawning aux sessions for {len(UNIQUE_PLATFORMS)} platforms..."))
            ok, fail = [], []
            for p in UNIQUE_PLATFORMS:
                try:
                    o, msg = await self._spawn_aux(p)
                    (ok if o else fail).append(p if o else f"{p}({msg})")
                except Exception as e:
                    fail.append(f"{p}({e})")
            lines = [
                f"  spawned: {len(ok)}",
                f"    {', '.join(ok) if ok else '—'}",
                "",
                f"  failed:  {len(fail)}",
                f"    {', '.join(fail) if fail else '—'}",
                "",
                "  discord merges all sessions into the platform row",
                "  on your profile — open it to check",
            ]
            await message.channel.send(S._ansi_block(lines))
            return

        # ── spoofmulti — manage aux sessions ──
        if cmd == "spoofmulti":
            if len(args) < 2:
                await message.edit(content=S.ui_info(
                    "usage: spoofmulti <a,b,c>  |  spoofmulti list  |  "
                    "spoofmulti stop [platform]"))
                return
            sub = args[1].lower()
            if sub == "list":
                if not _AUX_SESSIONS:
                    await message.edit(content=S.ui_info("no aux sessions"))
                    return
                lines = ["  active aux sessions:"]
                for k in _AUX_SESSIONS:
                    p = PLATFORM_PRESETS.get(k, {})
                    task = _AUX_SESSIONS[k].get("task")
                    live = task and not task.done()
                    lines.append(f"    {k:<12} {p.get('label','?'):<20} "
                                 f"{'live' if live else 'dead'}")
                await message.edit(content=S._ansi_block(lines))
                return
            if sub == "stop":
                if len(args) >= 3:
                    target = args[2].lower()
                    if await self._kill_aux(target):
                        await message.edit(content=S.ui_ok(f"stopped {target}"))
                    else:
                        await message.edit(content=S.ui_err(f"{target} not active"))
                    return
                n = await self._kill_all_aux()
                await message.edit(content=S.ui_ok(f"stopped {n} session(s)"))
                return

            # args[1] onwards is the comma list
            raw = " ".join(args[1:]).replace(" ", "")
            presets = [p for p in raw.split(",") if p]
            if not presets:
                await message.edit(content=S.ui_err("no platforms listed"))
                return
            ok, fail = [], []
            for p in presets:
                try:
                    o, msg = await self._spawn_aux(p)
                    (ok if o else fail).append(p if o else f"{p}({msg})")
                except Exception as e:
                    fail.append(f"{p}({e})")
            lines = [
                f"  spawned: {', '.join(ok) if ok else '—'}",
                f"  failed:  {', '.join(fail) if fail else '—'}",
                f"  active:  {', '.join(_AUX_SESSIONS.keys()) if _AUX_SESSIONS else '—'}",
            ]
            await message.channel.send(S._ansi_block(lines))
            return

    async def _send_status(self, message):
        p = self._last_props or {}
        active = _PRESET_HOLDER[0]
        aux_lines = []
        for k in _AUX_SESSIONS:
            pdef = PLATFORM_PRESETS.get(k, {})
            task = _AUX_SESSIONS[k].get("task")
            live = task and not task.done()
            aux_lines.append(f"    {k:<12} {pdef.get('label','?'):<20} "
                             f"{'live' if live else 'dead'}")
        lines = [
            f"  tracked:      {getattr(S, '_current_platform', '?')}",
            f"  preset held:  {active['label'] if active else '—'}",
            f"  patched:      {self._patched_preset['label'] if self._patched_preset else '—'}",
            f"  identify #:   {self._identify_count}",
            f"  reconnects:   {self._reconnect_count}",
            f"  reconnecting: {self._reconnect_in_progress}",
            f"  $os:          {p.get('$os', '?')}",
            f"  $browser:     {p.get('$browser', '?')}",
            f"  $device:      {p.get('$device', '?')}",
            "",
            f"  aux sessions: {len(_AUX_SESSIONS)}",
        ] + aux_lines
        await message.edit(content=S._ansi_block(lines))
