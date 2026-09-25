# language: Python, file: cogs/spoofer.py
# platform / device spoofing — args stripped of leading cmd token, unknown cmds rejected
import asyncio
import importlib
import inspect
import pkgutil
import time
import traceback
import modifyself_shim as discord
from . import state as S


PLATFORM_PRESETS = {
    "desktop":     {"os": "Windows",  "browser": "Chrome",          "device": "",             "label": "Windows Desktop"},
    "windows":     {"os": "Windows",  "browser": "Chrome",          "device": "",             "label": "Windows Desktop"},
    "macos":       {"os": "Mac OS X", "browser": "Chrome",          "device": "",             "label": "macOS Desktop"},
    "linux":       {"os": "Linux",    "browser": "Chrome",          "device": "",             "label": "Linux Desktop"},
    "web":         {"os": "Windows",  "browser": "Chrome",          "device": "",             "label": "Web (Chrome)"},
    "browser":     {"os": "Windows",  "browser": "Chrome",          "device": "",             "label": "Web (Chrome)"},
    "phone":       {"os": "Android",  "browser": "Discord Android", "device": "Android",      "label": "Phone (Android)"},
    "mobile":      {"os": "Android",  "browser": "Discord Android", "device": "Android",      "label": "Phone (Android)"},
    "android":     {"os": "Android",  "browser": "Discord Android", "device": "Android",      "label": "Android"},
    "ios":         {"os": "iOS",      "browser": "Discord iOS",     "device": "iPhone",       "label": "iOS"},
    "iphone":      {"os": "iOS",      "browser": "Discord iOS",     "device": "iPhone",       "label": "iPhone"},
    "ipad":        {"os": "iOS",      "browser": "Discord iOS",     "device": "iPad",         "label": "iPad"},
    "console":     {"os": "Windows",  "browser": "Chrome",          "device": "console",      "label": "Console"},
    "xbox":        {"os": "Windows",  "browser": "Chrome",          "device": "xbox",         "label": "Xbox"},
    "playstation": {"os": "Windows",  "browser": "Chrome",          "device": "playstation",  "label": "PlayStation"},
    "ps":          {"os": "Windows",  "browser": "Chrome",          "device": "playstation",  "label": "PlayStation"},
    "vr":          {"os": "Android",  "browser": "Discord VR",      "device": "Quest",        "label": "VR Headset"},
    "quest":       {"os": "Android",  "browser": "Discord VR",      "device": "Quest 3",      "label": "Meta Quest"},
    "quest2":      {"os": "Android",  "browser": "Discord VR",      "device": "Quest 2",      "label": "Meta Quest 2"},
    "embedded":    {"os": "Windows",  "browser": "Chrome",          "device": "",             "label": "Embedded"},
}


_LIVE_COG = [None]
_PRESET_HOLDER = [None]
_GW_CACHE = {"cls": None, "path": None, "scanned": False}


# ============================================================
# discovery — cached so we scan once
# ============================================================
def _walk_modifyself():
    try:
        pkg = importlib.import_module("modifyself")
    except Exception as e:
        print(f"[spoofer] cannot import modifyself: {e}")
        return
    yield pkg
    if hasattr(pkg, "__path__"):
        for _, name, _ in pkgutil.walk_packages(pkg.__path__, "modifyself."):
            try:
                yield importlib.import_module(name)
            except Exception:
                pass


def _find_gw_class(force=False):
    if _GW_CACHE["scanned"] and not force:
        return _GW_CACHE["cls"], _GW_CACHE["path"]
    candidates = []
    for mod in _walk_modifyself():
        mn = getattr(mod, "__name__", "?")
        try:
            members = inspect.getmembers(mod, inspect.isclass)
        except Exception:
            continue
        for cname, cobj in members:
            try:
                if cobj.__module__ != mn:
                    continue
                if not hasattr(cobj, "send_json"):
                    continue
            except Exception:
                continue
            score = 0
            low, modlow = cname.lower(), mn.lower()
            if "websocket" in low or "gateway" in low: score += 10
            if "discord" in low:                        score += 2
            if "gateway" in modlow:                     score += 5
            if "ws" in modlow or "websocket" in modlow: score += 3
            candidates.append((score, cobj, f"{mn}.{cname}"))
    _GW_CACHE["scanned"] = True
    if not candidates:
        return None, None
    candidates.sort(key=lambda t: t[0], reverse=True)
    _GW_CACHE["cls"]  = candidates[0][1]
    _GW_CACHE["path"] = candidates[0][2]
    return _GW_CACHE["cls"], _GW_CACHE["path"]


def _find_client_gateway(client):
    if client is None:
        return None, None
    for attr in ("_gateway", "gateway", "ws", "_ws", "_connection", "connection"):
        try:
            gw = getattr(client, attr, None)
        except Exception:
            continue
        if gw is not None and hasattr(gw, "send_json"):
            return attr, gw
    return None, None


# ============================================================
# identify rewrite
# ============================================================
def _rewrite_identify_dict(data, preset):
    if not isinstance(data, dict) or data.get("op") != 2:
        return False
    d = data.get("d")
    if not isinstance(d, dict):
        return False
    props = d.setdefault("properties", {})
    props["os"]      = preset["os"]
    props["browser"] = preset["browser"]
    props["device"]  = preset["device"]

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


# ============================================================
# patches
# ============================================================
def _install_patches():
    gw_class, found_path = _find_gw_class()
    if gw_class is None:
        print("[spoofer] class patch: gateway class not discovered")
        return False
    if getattr(gw_class, "_spoofer_patched", False):
        print(f"[spoofer] class patch already installed at {found_path}")
        return True

    original_send = gw_class.send_json

    async def patched_send_json(self, data):
        try:
            if isinstance(data, dict):
                preset = _PRESET_HOLDER[0]
                if preset is not None and data.get("op") == 2:
                    _rewrite_identify_dict(data, preset)
        except Exception as e:
            print(f"[spoofer] send_json patch error: {e}")
        return await original_send(self, data)

    gw_class.send_json = patched_send_json
    gw_class._spoofer_patched = True
    print(f"[spoofer] class patch installed: {found_path}.send_json")
    return True


def _install_instance_patch(client):
    attr, gw = _find_client_gateway(client)
    if gw is None:
        return False
    if getattr(gw, "_spoofer_instance_patched", False):
        return True

    original_send = gw.send_json

    async def patched_send_json(data):
        try:
            if isinstance(data, dict):
                preset = _PRESET_HOLDER[0]
                if preset is not None and data.get("op") == 2:
                    _rewrite_identify_dict(data, preset)
        except Exception as e:
            print(f"[spoofer] instance send_json patch error: {e}")
        return await original_send(data)

    gw.send_json = patched_send_json
    gw._spoofer_instance_patched = True
    print(f"[spoofer] instance patch installed: client.{attr} "
          f"({type(gw).__module__}.{type(gw).__name__})")
    return True


# ============================================================
# cog
# ============================================================
class SpooferCog:
    COMMANDS = {"platform", "spoof", "spoofer", "vr", "console",
                "spoofreset", "spoofstatus", "spooferdiag"}

    commands = {
        "platform":     "_cmd_platform",
        "spoof":        "_cmd_spoof",
        "spoofer":      "_cmd_spoof",
        "vr":           "_cmd_vr",
        "console":      "_cmd_console",
        "spoofreset":   "_cmd_spoofreset",
        "spoofstatus":  "_cmd_spoofstatus",
        "spooferdiag":  "_cmd_spooferdiag",
    }

    def __init__(self):
        self.bot = None
        self._patched_preset = None
        self._last_props = None
        self._identify_count = 0
        self._reconnect_count = 0
        self._watchdog_task = None
        self._reconnect_grace_until = 0.0
        self._instance_patched_attr = None
        _LIVE_COG[0] = self

        try:
            class_ok = _install_patches()
            print(f"[spoofer] init — class patch: {'OK' if class_ok else 'FAILED'}")
        except Exception:
            print("[spoofer] init class patch raised:")
            traceback.print_exc()

        try:
            self._start_watchdog()
        except Exception:
            print("[spoofer] init watchdog raised:")
            traceback.print_exc()

    # --------------------------------------------------------
    def _ensure_instance_patch(self):
        if self._instance_patched_attr:
            return True
        try:
            client = S.CLIENT
            if client is None:
                return False
            if _install_instance_patch(client):
                attr, _ = _find_client_gateway(client)
                self._instance_patched_attr = attr
                return True
        except Exception:
            print("[spoofer] _ensure_instance_patch raised:")
            traceback.print_exc()
        return False

    def _start_watchdog(self):
        if self._watchdog_task and not self._watchdog_task.done():
            return
        loop = asyncio.get_event_loop()
        self._watchdog_task = loop.create_task(self._watchdog())

    async def _watchdog(self):
        dead_since = 0.0
        while True:
            try:
                self._ensure_instance_patch()
                client = S.CLIENT
                if client is not None:
                    _, gw = _find_client_gateway(client)
                    now = time.time()
                    if gw is not None and now >= self._reconnect_grace_until:
                        is_connected = bool(getattr(gw, "is_connected", False))
                        is_closed    = bool(getattr(gw, "is_closed", False))
                        if not is_connected and not is_closed:
                            if dead_since == 0.0:
                                dead_since = now
                            elif now - dead_since > 15:
                                print("[spoofer] watchdog: gateway stalled — reconnect")
                                await self._safe_reconnect()
                                dead_since = 0.0
                        else:
                            dead_since = 0.0
            except Exception as e:
                print(f"[spoofer] watchdog error: {e}")
            await asyncio.sleep(5)

    def _build_diag(self):
        lines = []
        try:
            cls, path = _find_gw_class()
            class_ok = bool(getattr(cls, "_spoofer_patched", False)) if cls else False
            lines.append(f"class found:    {path or 'NOT FOUND'}")
            lines.append(f"class patch:    {'YES' if class_ok else 'NO'}")
        except Exception as e:
            lines.append(f"class scan err: {e}")

        try:
            client = S.CLIENT
            attr, gw = _find_client_gateway(client)
            inst_ok = bool(getattr(gw, "_spoofer_instance_patched", False)) if gw else False
            lines.append(f"client attr:    {attr or '—'}")
            lines.append(f"instance patch: {'YES' if inst_ok else 'NO'}")
            lines.append(f"preset held:    {_PRESET_HOLDER[0]['label'] if _PRESET_HOLDER[0] else '—'}")
            lines.append(f"identify count: {self._identify_count}")
            lines.append(f"reconnect cnt:  {self._reconnect_count}")
            lines.append(f"reconnect grace:{max(0, int(self._reconnect_grace_until - time.time()))}s")
            if gw is not None:
                try:
                    st = gw.get_state() if hasattr(gw, "get_state") else {}
                    lines.append(f"gateway state:  {st}")
                except Exception as e:
                    lines.append(f"gateway state err: {e}")
                lines.append(f"last os:        {(self._last_props or {}).get('os', '?')}")
                lines.append(f"last browser:   {(self._last_props or {}).get('browser', '?')}")
                lines.append(f"last device:    {(self._last_props or {}).get('device', '?')}")
        except Exception as e:
            lines.append(f"client scan err: {e}")
        return lines

    def _current_preset_label(self):
        plat = getattr(S, "_current_platform", "desktop")
        preset = PLATFORM_PRESETS.get(plat)
        return preset["label"] if preset else plat

    # --------------------------------------------------------
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
        client = S.CLIENT
        if client is None:
            return
        self._reconnect_count += 1
        print(f"[spoofer] reconnect #{self._reconnect_count} requested")
        self._reconnect_grace_until = time.time() + 30

        rc = getattr(client, "reconnect_gateway", None)
        if callable(rc):
            try:
                ok = await rc()
                print(f"[spoofer] reconnect #{self._reconnect_count} "
                      f"{'OK' if ok else 'returned falsy'}")
                return
            except Exception as e:
                print(f"[spoofer] reconnect_gateway raised: {e}")

        _, gw = _find_client_gateway(client)
        if gw is not None and hasattr(gw, "close"):
            try:
                await gw.close(code=1000)
                print(f"[spoofer] reconnect via ws.close")
                return
            except Exception as e:
                print(f"[spoofer] ws.close raised: {e}")

        if hasattr(client, "close"):
            try:
                await client.close()
                print(f"[spoofer] reconnect via client.close")
                return
            except Exception as e:
                print(f"[spoofer] client.close raised: {e}")

    # --------------------------------------------------------
    # entry-point aliases
    # --------------------------------------------------------
    async def handle(self, message, cmd=None, args=None):
        await self._dispatch(message, cmd, args)

    async def on_message(self, message, cmd=None, args=None):
        await self._dispatch(message, cmd, args)

    async def execute(self, message, cmd=None, args=None):
        await self._dispatch(message, cmd, args)

    async def run_command(self, message, cmd=None, args=None):
        await self._dispatch(message, cmd, args)

    async def dispatch(self, message, cmd=None, args=None):
        await self._dispatch(message, cmd, args)

    async def process(self, message, cmd=None, args=None):
        await self._dispatch(message, cmd, args)

    async def on_command(self, message, cmd=None, args=None):
        await self._dispatch(message, cmd, args)

    async def _cmd_platform(self, message, args=None):
        await self._dispatch(message, "platform", args)

    async def _cmd_spoof(self, message, args=None):
        await self._dispatch(message, "spoof", args)

    async def _cmd_vr(self, message, args=None):
        await self._dispatch(message, "vr", args)

    async def _cmd_console(self, message, args=None):
        await self._dispatch(message, "console", args)

    async def _cmd_spoofreset(self, message, args=None):
        await self._dispatch(message, "spoofreset", args)

    async def _cmd_spoofstatus(self, message, args=None):
        await self._dispatch(message, "spoofstatus", args)

    async def _cmd_spooferdiag(self, message, args=None):
        await self._dispatch(message, "spooferdiag", args)

    # --------------------------------------------------------
    async def _dispatch(self, message, cmd=None, args=None):
        try:
            await self._dispatch_inner(message, cmd, args)
        except Exception as e:
            tb = traceback.format_exc()
            print("[spoofer] DISPATCH RAISED:")
            print(tb)
            short = f"{type(e).__name__}: {e}"
            try:
                await message.edit(content=S.ui_err(f"`spoofer` crash — {short}"))
            except Exception:
                pass

    async def _dispatch_inner(self, message, cmd=None, args=None):
        # --- shape B: router handed us only the message, parse it ourselves
        if cmd is None:
            content = getattr(message, "content", "") or ""
            parts = content.split()
            if not parts:
                return
            cmd = parts[0].lstrip("$./!").lower()
            args = parts[1:]

        # --- normalize args to a list
        if args is None:
            args = []
        elif isinstance(args, str):
            args = args.split()

        # --- normalize cmd in case router passed a prefixed token
        if isinstance(cmd, str):
            cmd = cmd.lstrip("$./!").lower()

        # --- some routers echo the command token as args[0]; strip it
        while args and isinstance(args[0], str) and \
                args[0].lstrip("$./!").lower() == cmd:
            args = args[1:]

        print(f"[spoofer] dispatch cmd={cmd!r} args={args!r}")

        # --- hard guard: never let an unknown command touch this cog
        if cmd not in self.COMMANDS:
            print(f"[spoofer] rejecting unknown cmd={cmd!r}")
            return

        self._ensure_instance_patch()

        client = S.CLIENT
        if client is None:
            await message.edit(content=S.ui_err("client not ready"))
            return

        # --- spooferdiag ---
        if cmd == "spooferdiag":
            lines = self._build_diag()
            print("[spooferdiag] ====")
            for ln in lines:
                print(f"[spooferdiag] {ln}")
            print("[spooferdiag] ====")
            await message.edit(content=S._ansi_block(lines))
            return

        # --- platform ---
        if cmd == "platform":
            if len(args) < 1:
                cur = getattr(S, "_current_platform", "desktop")
                lines = [f"  current: {cur}", "",
                         f"  live:    {self._current_preset_label()}", "",
                         "  available:"]
                for k in sorted(PLATFORM_PRESETS.keys()):
                    lines.append(f"    {k:<12} {PLATFORM_PRESETS[k]['label']}")
                await message.edit(content=S._ansi_block(lines))
                return
            plat = args[0].lower()
            if plat == "off":
                plat = "desktop"
            if not await self._set_platform(plat, message):
                return
            preset = PLATFORM_PRESETS[plat]
            await message.edit(content=S.ui_ok(f"platform → {preset['label']}"))
            await self._safe_reconnect()
            return

        # --- spoof / spoofer ---
        if cmd in ("spoof", "spoofer"):
            if len(args) < 1:
                await message.edit(content=S.ui_info(
                    "usage: spoof <platform> | spoof status | spoof reset"))
                return
            sub = args[0].lower()
            if sub == "status":
                await self._send_status(message)
                return
            if sub == "reset":
                if not await self._set_platform("desktop", message):
                    return
                await message.edit(content=S.ui_ok("platform reset → Desktop"))
                await self._safe_reconnect()
                return
            if not await self._set_platform(sub, message):
                return
            preset = PLATFORM_PRESETS[sub]
            await message.edit(content=S.ui_ok(f"spoofed → {preset['label']}"))
            await self._safe_reconnect()
            return

        # --- vr / console ---
        if cmd in ("vr", "console"):
            if not await self._set_platform(cmd, message):
                return
            preset = PLATFORM_PRESETS[cmd]
            await message.edit(content=S.ui_ok(f"platform → {preset['label']}"))
            await self._safe_reconnect()
            return

        # --- spoofstatus ---
        if cmd == "spoofstatus":
            await self._send_status(message)
            return

        # --- spoofreset ---
        if cmd == "spoofreset":
            if not await self._set_platform("desktop", message):
                return
            await message.edit(content=S.ui_ok("platform reset → Desktop"))
            await self._safe_reconnect()
            return

    # --------------------------------------------------------
    async def _send_status(self, message):
        p = self._last_props or {}
        active = _PRESET_HOLDER[0]
        client = S.CLIENT
        _, gw = _find_client_gateway(client)

        state_str = "—"
        if gw is not None and hasattr(gw, "get_state"):
            try:
                st = gw.get_state()
                state_str = f"{st.get('state')} / conn={st.get('is_connected')}"
            except Exception:
                pass

        cls, _ = _find_gw_class()
        class_ok = bool(getattr(cls, "_spoofer_patched", False)) if cls else False
        inst_ok  = bool(getattr(gw, "_spoofer_instance_patched", False)) if gw else False

        lines = [
            f"  tracked:      {getattr(S, '_current_platform', '?')}",
            f"  preset held:  {active['label'] if active else '—'}",
            f"  class patch:  {'YES' if class_ok else 'NO'}",
            f"  inst patch:   {'YES' if inst_ok else 'NO'}",
            f"  identify #:   {self._identify_count}",
            f"  reconnects:   {self._reconnect_count}",
            f"  gateway:      {state_str}",
            f"  $os:          {p.get('os', '?')}",
            f"  $browser:     {p.get('browser', '?')}",
            f"  $device:      {p.get('device', '?')}",
        ]
        await message.edit(content=S._ansi_block(lines))
