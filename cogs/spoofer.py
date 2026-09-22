# cogs/spoofer.py | platform / device spoofing — patches the confirmed discord.py-self
# gateway write surface: ws.send_as_json, ws._sendstr, ws.identify, ws.socket.send_str.
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


def _is_identify_payload(payload):
    try:
        return (isinstance(payload, dict)
                and payload.get("op") == 2
                and isinstance(payload.get("d"), dict)
                and isinstance(payload["d"].get("properties"), dict))
    except Exception:
        return False


def _is_identify_frame(data):
    if not isinstance(data, str):
        return False
    if not _OP2_RE.search(data):
        return False
    return '"properties"' in data


def _rewrite(props_or_payload, preset, cog):
    """Rewrite the properties block inside an identify payload dict."""
    if "properties" in props_or_payload:
        props = props_or_payload["properties"]
    else:
        props = props_or_payload
    props["$os"] = preset["os"]
    props["$browser"] = preset["browser"]
    props["$device"] = preset["device"]
    cog._last_props = dict(props)
    cog._identify_count += 1
    print(f"[spoofer] rewrote IDENTIFY #{cog._identify_count} → {preset['label']}")
    return props_or_payload


class SpooferCog:
    COMMANDS = {"platform", "spoof", "spoofer", "vr", "console",
                "spoofreset", "spoofstatus", "spooferdiag"}

    def __init__(self):
        self.bot = None
        self._interceptor_ws = None
        self._patch_targets = []       # (obj, attr_name, original_callable, kind)
        self._interceptor_active = False
        self._patched_preset = None
        self._last_props = None
        self._identify_count = 0
        self._reconnect_count = 0
        self._watch_task = None

    # ── diagnostics ──

    def _build_diag(self):
        client = S.CLIENT
        lines = []
        if client is None:
            return ["client is None"]
        lines.append(f"client: {type(client).__name__}")
        ws = getattr(client, "ws", None)
        lines.append(f"ws: {type(ws).__name__ if ws else 'None'}")
        if ws is not None:
            interesting = [a for a in dir(ws) if any(k in a.lower() for k in
                           ("send", "keep", "socket", "ident", "gateway"))]
            lines.append(f"ws sendable: {interesting}")
            lines.append(f"ws.socket: {type(getattr(ws, 'socket', None)).__name__}")
        lines.append(f"patched targets: "
                     f"{[f'{type(o).__name__}.{n}' for o, n, _, _ in self._patch_targets]}")
        return lines

    # ── patching ──

    def _find_targets(self):
        """Return list of (obj, attr, kind) where kind is 'dict' or 'str'."""
        client = S.CLIENT
        targets = []
        if client is None:
            return targets

        ws = getattr(client, "ws", None)
        if ws is None:
            return targets

        # primary dict serializer — most likely path
        if callable(getattr(ws, "send_as_json", None)):
            targets.append((ws, "send_as_json", "dict"))

        # raw string send — fallback path
        if callable(getattr(ws, "_sendstr", None)):
            targets.append((ws, "_sendstr", "str"))

        # identify builder — build-time hook
        if callable(getattr(ws, "identify", None)):
            targets.append((ws, "identify", "identify"))

        # lower-level socket fallback
        sock = getattr(ws, "socket", None)
        if sock is not None and callable(getattr(sock, "send_str", None)):
            targets.append((sock, "send_str", "str"))

        return targets

    async def _ensure_interceptor(self):
        client = S.CLIENT
        if client is None:
            return False

        if self._interceptor_active and getattr(client, "ws", None) is self._interceptor_ws:
            return True

        for _ in range(30):
            if getattr(client, "ws", None) is not None:
                break
            await asyncio.sleep(0.5)
        if not getattr(client, "ws", None):
            return False

        # tear down old patches before reattaching
        self._unpatch_all()

        self._interceptor_ws = client.ws
        cog = self

        targets = self._find_targets()
        if not targets:
            print("[spoofer] no patch targets found")
            return False

        names = [f"{type(o).__name__}.{a}" for o, a, _ in targets]
        print(f"[spoofer] patching {len(targets)} target(s): {names}")

        for obj, attr, kind in targets:
            try:
                original = getattr(obj, attr)
            except Exception:
                continue

            def make_wrapper(orig, wrap_kind):
                if wrap_kind == "dict":
                    async def wrapper(*args, **kwargs):
                        preset = cog._patched_preset
                        if preset and args and _is_identify_payload(args[0]):
                            try:
                                _rewrite(args[0], preset, cog)
                            except Exception as e:
                                print(f"[spoofer] dict rewrite failed: {e}")
                        return await orig(*args, **kwargs)
                    return wrapper
                elif wrap_kind == "str":
                    async def wrapper(*args, **kwargs):
                        preset = cog._patched_preset
                        if preset and args and _is_identify_frame(args[0]):
                            try:
                                payload = json.loads(args[0])
                                if _is_identify_payload(payload):
                                    _rewrite(payload, preset, cog)
                                    args = (json.dumps(payload),) + args[1:]
                            except Exception as e:
                                print(f"[spoofer] str rewrite failed: {e}")
                        return await orig(*args, **kwargs)
                    return wrapper
                else:  # identify — builder method, patch after call
                    def wrapper(*args, **kwargs):
                        # identify builds a dict payload; call, then mutate, then
                        # the caller serializes. some builds return a dict, some
                        # return the ws. handle both.
                        result = orig(*args, **kwargs)
                        preset = cog._patched_preset
                        if preset and isinstance(result, dict):
                            try:
                                _rewrite(result, preset, cog)
                            except Exception as e:
                                print(f"[spoofer] identify rewrite failed: {e}")
                        return result
                    return wrapper

            wrapped = make_wrapper(original, kind)
            try:
                setattr(obj, attr, wrapped)
                self._patch_targets.append((obj, attr, original, kind))
                print(f"[spoofer] patched {type(obj).__name__}.{attr} ({kind})")
            except Exception as e:
                print(f"[spoofer] could not patch {type(obj).__name__}.{attr}: {e}")

        if not self._patch_targets:
            print("[spoofer] nothing patchable")
            return False

        self._interceptor_active = True
        print("[spoofer] IDENTIFY interceptor active")
        self._start_watcher()
        return True

    def _unpatch_all(self):
        for obj, attr, original, _kind in self._patch_targets:
            try:
                setattr(obj, attr, original)
            except Exception:
                pass
        self._patch_targets.clear()
        self._interceptor_active = False

    # ── watcher ──

    def _start_watcher(self):
        if self._watch_task and not self._watch_task.done():
            return
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        self._watch_task = loop.create_task(self._ws_watcher())

    async def _ws_watcher(self):
        last_ws_id = None
        while True:
            try:
                client = S.CLIENT
                ws = getattr(client, "ws", None) if client else None
                if ws is not None:
                    cur_id = id(ws)
                    if cur_id != last_ws_id:
                        last_ws_id = cur_id
                        self._interceptor_ws = None
                        ok = await self._ensure_interceptor()
                        if ok:
                            print(f"[spoofer] reattached to new ws "
                                  f"(preset: "
                                  f"{self._patched_preset['label'] if self._patched_preset else '—'})")
            except Exception as e:
                print(f"[spoofer] watcher error: {e}")
            await asyncio.sleep(2)

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
        if not await self._ensure_interceptor():
            await message.edit(content=S.ui_err(
                "could not attach interceptor — run $spooferdiag"))
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
        ws = getattr(S.CLIENT, "ws", None)
        ws_match = (ws is self._interceptor_ws) if ws is not None else False
        targets = [f"{type(o).__name__}.{n}" for o, n, _, _ in self._patch_targets]
        lines = [
            f"  tracked:      {getattr(S, '_current_platform', '?')}",
            f"  interceptor:  {'active' if self._interceptor_active else 'inactive'}",
            f"  ws match:     {'yes' if ws_match else 'no'}",
            f"  patched:      {self._patched_preset['label'] if self._patched_preset else '—'}",
            f"  targets:      {', '.join(targets) if targets else '—'}",
            f"  identify #:   {self._identify_count}",
            f"  reconnects:   {self._reconnect_count}",
            f"  $os:          {p.get('$os', '?')}",
            f"  $browser:     {p.get('$browser', '?')}",
            f"  $device:      {p.get('$device', '?')}",
        ]
        await message.edit(content=S._ansi_block(lines))
