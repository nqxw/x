# cogs/spoofer.py | platform / device spoofing — patches the real discord.py-self
# gateway write path. walks multiple candidate attributes so it survives forks.
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


def _payload_is_identify_dict(payload):
    try:
        return isinstance(payload, dict) and payload.get("op") == 2 and \
               isinstance(payload.get("d"), dict) and \
               "properties" in payload["d"]
    except Exception:
        return False


def _frame_is_identify(data):
    if not isinstance(data, str):
        return False
    if not _OP2_RE.search(data):
        return False
    return '"properties"' in data


def _rewrite_props(payload, preset, cog):
    """Mutate an identify payload dict in place. Return the same dict."""
    props = payload["d"].get("properties", {})
    props["$os"] = preset["os"]
    props["$browser"] = preset["browser"]
    props["$device"] = preset["device"]
    payload["d"]["properties"] = props
    cog._last_props = dict(props)
    cog._identify_count += 1
    print(f"[spoofer] rewrote IDENTIFY #{cog._identify_count} → {preset['label']}")
    return payload


class SpooferCog:
    COMMANDS = {"platform", "spoof", "spoofer", "vr", "console",
                "spoofreset", "spoofstatus", "spooferdiag"}

    def __init__(self):
        self.bot = None
        self._interceptor_ws = None
        self._original_send = None
        self._patch_targets = []      # list of (obj, attr_name, original_callable)
        self._interceptor_active = False
        self._patched_preset = None
        self._last_props = None
        self._identify_count = 0
        self._reconnect_count = 0
        self._watch_task = None
        self._diag_lines = []         # last diag dump

    # ── diagnostics ──

    def _build_diag(self):
        """Walk the ws chain and collect what we can see."""
        lines = []
        client = S.CLIENT
        if client is None:
            return ["client is None"]

        lines.append(f"client type: {type(client).__name__}")

        ws = getattr(client, "ws", None)
        lines.append(f"client.ws: {type(ws).__name__ if ws else 'None'}")
        if ws is not None:
            attrs = [a for a in dir(ws) if not a.startswith("__")]
            # filter for send/keep/socket/connect related
            interesting = [a for a in attrs
                           if any(k in a.lower() for k in
                                  ("send", "keep", "socket", "connect", "ident", "gateway"))]
            lines.append(f"client.ws interesting attrs: {interesting}")

            inner_ws = getattr(ws, "ws", None)
            lines.append(f"client.ws.ws: {type(inner_ws).__name__ if inner_ws else 'None'}")
            if inner_ws is not None:
                inner_attrs = [a for a in dir(inner_ws)
                               if any(k in a.lower() for k in ("send", "close"))]
                lines.append(f"client.ws.ws send attrs: {inner_attrs}")

            ka = getattr(ws, "_keep_alive", None)
            lines.append(f"client.ws._keep_alive: {type(ka).__name__ if ka else 'None'}")

        conn = getattr(client, "_connection", None)
        lines.append(f"client._connection: {type(conn).__name__ if conn else 'None'}")
        if conn is not None:
            c_attrs = [a for a in dir(conn)
                       if any(k in a.lower() for k in ("send", "ident", "prop", "session"))]
            lines.append(f"client._connection interesting attrs: {c_attrs}")

            props = getattr(conn, "_properties", None)
            lines.append(f"client._connection._properties: {props}")

        return lines

    # ── interceptor ──

    def _find_send_targets(self):
        """Return every (obj, attr_name) pair that might be the gateway send path."""
        client = S.CLIENT
        targets = []
        if client is None:
            return targets

        # candidate attributes on ws that could be the send method
        ws = getattr(client, "ws", None)
        if ws is not None:
            for name in ("send_as_json", "send", "send_str", "_send_identify"):
                if hasattr(ws, name) and callable(getattr(ws, name, None)):
                    targets.append((ws, name))

            # the raw aiohttp websocket inside discord.py-self
            inner_ws = getattr(ws, "ws", None)
            if inner_ws is not None:
                for name in ("send_str", "send_bytes", "send_json"):
                    if hasattr(inner_ws, name) and callable(getattr(inner_ws, name, None)):
                        targets.append((inner_ws, name))

        # connection-level helpers
        conn = getattr(client, "_connection", None)
        if conn is not None:
            for name in ("_send_payload", "send_payload", "_send_identify"):
                if hasattr(conn, name) and callable(getattr(conn, name, None)):
                    targets.append((conn, name))

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

        self._interceptor_ws = client.ws
        self._patch_targets.clear()
        cog = self
        client_ref = client

        targets = self._find_send_targets()
        if not targets:
            print("[spoofer] no send targets found — run $spooferdiag")
            return False

        print(f"[spoofer] patching {len(targets)} send target(s): "
              f"{[f'{type(o).__name__}.{n}' for o, n in targets]}")

        for obj, attr in targets:
            try:
                original = getattr(obj, attr)
            except Exception:
                continue

            # make the wrapper for this specific attr
            def make_wrapper(orig, obj_name, attr_name):
                async def wrapper(*args, **kwargs):
                    preset = cog._patched_preset
                    if preset:
                        # string payload (raw frame) — check for identify json
                        if args and isinstance(args[0], str) and _frame_is_identify(args[0]):
                            try:
                                payload = json.loads(args[0])
                                if _payload_is_identify_dict(payload):
                                    _rewrite_props(payload, preset, cog)
                                    args = (json.dumps(payload),) + args[1:]
                            except Exception as e:
                                print(f"[spoofer] str-payload rewrite failed: {e}")
                        # dict payload (library builds it as a python dict)
                        elif args and isinstance(args[0], dict) and \
                                _payload_is_identify_dict(args[0]):
                            try:
                                _rewrite_props(args[0], preset, cog)
                            except Exception as e:
                                print(f"[spoofer] dict-payload rewrite failed: {e}")
                    return await orig(*args, **kwargs)
                return wrapper

            wrapped = make_wrapper(original, type(obj).__name__, attr)
            try:
                setattr(obj, attr, wrapped)
                self._patch_targets.append((obj, attr, original))
                print(f"[spoofer] patched {type(obj).__name__}.{attr}")
            except Exception as e:
                print(f"[spoofer] could not patch {type(obj).__name__}.{attr}: {e}")

        if not self._patch_targets:
            print("[spoofer] no attributes could be patched")
            return False

        self._interceptor_active = True
        print("[spoofer] IDENTIFY interceptor active")
        self._start_watcher()
        return True

    def _unpatch_all(self):
        for obj, attr, original in self._patch_targets:
            try:
                setattr(obj, attr, original)
            except Exception:
                pass
        self._patch_targets.clear()

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
                        # tear down old patches and reattach
                        self._unpatch_all()
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
                "could not attach interceptor — run $spooferdiag and check console"))
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

        if cmd == "spooferdiag":
            lines = self._build_diag()
            # print full to console
            print("[spooferdiag] ====")
            for ln in lines:
                print(f"[spooferdiag] {ln}")
            print("[spooferdiag] ====")
            # send a condensed version to discord
            short = lines[:8] if len(lines) > 8 else lines
            await message.edit(content=S._ansi_block(short))
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
        targets = [f"{type(o).__name__}.{n}" for o, n, _ in self._patch_targets]
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
