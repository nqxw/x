# cogs/spoofer.py | platform pool spoofing — rotate / random / sticky + watchdog
import asyncio
import importlib
import inspect
import pkgutil
import random
import time
import traceback
import modifyself_shim as discord
from . import state as S


# ============================================================
# ui wrappers — fall back if state helpers missing
# ============================================================
def _ansi(lines):
    fn = getattr(S, "_ansi_block", None)
    if callable(fn):
        try:
            return fn(lines)
        except Exception:
            pass
    body = "\n".join("> " + str(l) for l in lines)
    return "> ```ansi\n" + body + "\n> ```"


def _ui_ok(msg):
    fn = getattr(S, "ui_ok", None)
    if callable(fn):
        try:
            return fn(msg)
        except Exception:
            pass
    return _ansi([f"  \u2713  {msg}"])


def _ui_err(msg):
    fn = getattr(S, "ui_err", None)
    if callable(fn):
        try:
            return fn(msg)
        except Exception:
            pass
    return _ansi([f"  \u2717  {msg}"])


def _ui_info(msg):
    fn = getattr(S, "ui_info", None)
    if callable(fn):
        try:
            return fn(msg)
        except Exception:
            pass
    return _ansi([f"  \u2022  {msg}"])


async def _reply(message, content):
    try:
        await message.edit(content=content)
        return True
    except Exception:
        pass
    try:
        await message.delete()
    except Exception:
        pass
    try:
        await message.channel.send(content)
        return True
    except Exception as e:
        print(f"[spoofer] _reply failed: {e}")
        return False


# ============================================================
# platform presets
# ============================================================
PLATFORM_PRESETS = {
    "desktop":     {"os": "Windows",  "browser": "Chrome",          "device": "",            "label": "Windows Desktop"},
    "windows":     {"os": "Windows",  "browser": "Chrome",          "device": "",            "label": "Windows Desktop"},
    "macos":       {"os": "Mac OS X", "browser": "Chrome",          "device": "",            "label": "macOS Desktop"},
    "linux":       {"os": "Linux",    "browser": "Chrome",          "device": "",            "label": "Linux Desktop"},
    "web":         {"os": "Windows",  "browser": "Chrome",          "device": "",            "label": "Web (Chrome)"},
    "browser":     {"os": "Windows",  "browser": "Chrome",          "device": "",            "label": "Web (Chrome)"},
    "phone":       {"os": "Android",  "browser": "Discord Android", "device": "Android",     "label": "Phone (Android)"},
    "mobile":      {"os": "Android",  "browser": "Discord Android", "device": "Android",     "label": "Phone (Android)"},
    "android":     {"os": "Android",  "browser": "Discord Android", "device": "Android",     "label": "Android"},
    "ios":         {"os": "iOS",      "browser": "Discord iOS",     "device": "iPhone",      "label": "iOS"},
    "iphone":      {"os": "iOS",      "browser": "Discord iOS",     "device": "iPhone",      "label": "iPhone"},
    "ipad":        {"os": "iOS",      "browser": "Discord iOS",     "device": "iPad",        "label": "iPad"},
    "console":     {"os": "Windows",  "browser": "Chrome",          "device": "console",     "label": "Console"},
    "xbox":        {"os": "Windows",  "browser": "Chrome",          "device": "xbox",        "label": "Xbox"},
    "playstation": {"os": "Windows",  "browser": "Chrome",          "device": "playstation", "label": "PlayStation"},
    "ps":          {"os": "Windows",  "browser": "Chrome",          "device": "playstation", "label": "PlayStation"},
    "vr":          {"os": "Android",  "browser": "Discord VR",      "device": "Quest",       "label": "VR Headset"},
    "quest":       {"os": "Android",  "browser": "Discord VR",      "device": "Quest 3",     "label": "Meta Quest"},
    "quest2":      {"os": "Android",  "browser": "Discord VR",      "device": "Quest 2",     "label": "Meta Quest 2"},
    "embedded":    {"os": "Windows",  "browser": "Chrome",          "device": "",            "label": "Embedded"},
}

UA_MAP = {
    ("Android", "Discord Android"): "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36",
    ("Android", "Discord VR"):      "Mozilla/5.0 (Linux; Android 12; Quest 3) AppleWebKit/537.36 (KHTML, like Gecko) OculusBrowser/37.0.0.0.43 SamsungBrowser/4.0 Chrome/122.0.6261.140 VR Safari/537.36",
    ("iOS", "Discord iOS"):         "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
    ("Windows", "Chrome"):          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    ("Mac OS X", "Chrome"):         "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    ("Linux", "Chrome"):            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
}


# ============================================================
# module state
# ============================================================
_LIVE_COG = [None]
_POOL_STATE = [{
    "mode":   "rotate",   # rotate | random | sticky
    "pool":   [],
    "keys":   [],
    "cursor": 0,
    "last":   None,
}]
_GW_CACHE = {"cls": None, "path": None, "scanned": False}
_CALL_COUNT = [0]


# ============================================================
# discovery
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
    if _GW_CACHE["scanned"] and not force and _GW_CACHE["cls"] is not None:
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
            if "websocket" in low or "gateway" in low:
                score += 10
            if "discord" in low:
                score += 2
            if "gateway" in modlow:
                score += 5
            if "ws" in modlow or "websocket" in modlow:
                score += 3
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
# pool logic
# ============================================================
def _pick_preset():
    st = _POOL_STATE[0]
    pool = st["pool"]
    if not pool:
        return None
    mode = st["mode"]
    if mode == "sticky":
        return pool[0]
    if mode == "random":
        return random.choice(pool)
    idx = st["cursor"] % len(pool)
    st["cursor"] = idx + 1
    return pool[idx]


def _pool_keys():
    return _POOL_STATE[0]["keys"]


def _sync_platform_attr():
    st = _POOL_STATE[0]
    try:
        S._current_platform = ",".join(st["keys"]) if st["keys"] else "desktop"
    except Exception:
        pass


def _set_pool(keys):
    st = _POOL_STATE[0]
    st["pool"] = [PLATFORM_PRESETS[k] for k in keys if k in PLATFORM_PRESETS]
    st["keys"] = [k for k in keys if k in PLATFORM_PRESETS]
    st["cursor"] = 0
    _sync_platform_attr()


def _add_key(key):
    st = _POOL_STATE[0]
    if key in st["keys"]:
        return False
    if key not in PLATFORM_PRESETS:
        return False
    st["keys"].append(key)
    st["pool"].append(PLATFORM_PRESETS[key])
    _sync_platform_attr()
    return True


def _remove_key(key):
    st = _POOL_STATE[0]
    if key not in st["keys"]:
        return False
    i = st["keys"].index(key)
    st["keys"].pop(i)
    st["pool"].pop(i)
    if st["cursor"] > len(st["pool"]):
        st["cursor"] = 0
    _sync_platform_attr()
    return True


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
    ua = UA_MAP.get((preset["os"], preset["browser"]))
    if ua:
        props["browser_user_agent"] = ua
    cog = _LIVE_COG[0]
    if cog is not None:
        try:
            cog._last_props = dict(props)
            cog._identify_count += 1
        except Exception:
            pass
    _POOL_STATE[0]["last"] = preset
    print(f"[spoofer] rewrote IDENTIFY -> {preset['label']}")
    return True


# ============================================================
# gateway patches
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
            if isinstance(data, dict) and data.get("op") == 2:
                preset = _pick_preset()
                if preset is not None:
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
            if isinstance(data, dict) and data.get("op") == 2:
                preset = _pick_preset()
                if preset is not None:
                    _rewrite_identify_dict(data, preset)
        except Exception as e:
            print(f"[spoofer] instance send_json patch error: {e}")
        return await original_send(data)

    try:
        gw.send_json = patched_send_json
    except AttributeError:
        # read-only — class patch already covers it
        print("[spoofer] instance.send_json is read-only (slots?) — class patch handles it")
        return False
    except Exception as e:
        print(f"[spoofer] instance patch assign failed: {e}")
        return False

    try:
        gw._spoofer_instance_patched = True
    except Exception:
        pass
    print(f"[spoofer] instance patch installed: client.{attr} "
          f"({type(gw).__module__}.{type(gw).__name__})")
    return True


# ============================================================
# cog
# ============================================================
class SpooferCog:
    COMMANDS = {"platform", "spoof", "spoofer", "vr", "console",
                "spoofreset", "spoofstatus", "spooferdiag"}

    def __init__(self):
        self.bot = None
        self._last_props = None
        self._identify_count = 0
        self._reconnect_count = 0
        self._watchdog_task = None
        self._reconnect_grace_until = 0.0
        self._instance_patched_attr = None
        _LIVE_COG[0] = self

        _set_pool(["desktop"])

        try:
            _install_patches()
        except Exception:
            print("[spoofer] init class patch raised:")
            traceback.print_exc()

        try:
            self._start_watchdog()
        except Exception:
            print("[spoofer] init watchdog raised:")
            traceback.print_exc()

        print("[spoofer] cog ready — " + ", ".join(sorted(self.COMMANDS)))

    # --------------------------------------------------------
    def _ensure_instance_patch(self):
        if self._instance_patched_attr:
            return True
        # class patch already covers every instance — nothing to do here
        try:
            cls, path = _find_gw_class()
            if cls is not None and getattr(cls, "_spoofer_patched", False):
                self._instance_patched_attr = "class:" + (path or "?")
                return True
        except Exception:
            pass
        try:
            client = S.CLIENT
            if client is None:
                return False
            if _install_instance_patch(client):
                attr, _ = _find_client_gateway(client)
                self._instance_patched_attr = attr or "instance"
                return True
        except Exception as e:
            # never spam the log on every watchdog tick — mark as covered
            self._instance_patched_attr = "skipped: " + type(e).__name__
            print(f"[spoofer] instance patch skipped: {e}")
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
                        is_conn   = bool(getattr(gw, "is_connected", False))
                        is_closed = bool(getattr(gw, "is_closed", False))
                        if not is_conn and not is_closed:
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
        st = _POOL_STATE[0]
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
            if not inst_ok and self._instance_patched_attr:
                inst_ok = True
            lines.append(f"client attr:    {attr or '-'}")
            lines.append(f"instance patch: {'YES' if inst_ok else 'NO'}")
            lines.append(f"pool mode:      {st['mode']}")
            lines.append(f"pool keys:      {', '.join(st['keys']) or '-'}")
            lines.append(f"pool size:      {len(st['pool'])}")
            lines.append(f"cursor:         {st['cursor']}")
            lines.append(f"last pick:      {st['last']['label'] if st['last'] else '-'}")
            lines.append(f"identify count: {self._identify_count}")
            lines.append(f"reconnect cnt:  {self._reconnect_count}")
            lines.append(f"grace:          {max(0, int(self._reconnect_grace_until - time.time()))}s")
            lines.append(f"handle calls:   {_CALL_COUNT[0]}")
            if gw is not None:
                try:
                    gws = gw.get_state() if hasattr(gw, "get_state") else {}
                    lines.append(f"gateway state:  {gws}")
                except Exception as e:
                    lines.append(f"gateway state err: {e}")
                lines.append(f"last os:        {(self._last_props or {}).get('os', '?')}")
                lines.append(f"last browser:   {(self._last_props or {}).get('browser', '?')}")
                lines.append(f"last device:    {(self._last_props or {}).get('device', '?')}")
        except Exception as e:
            lines.append(f"client scan err: {e}")
        return lines

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
                print("[spoofer] reconnect via ws.close")
                return
            except Exception as e:
                print(f"[spoofer] ws.close raised: {e}")
        if hasattr(client, "close"):
            try:
                await client.close()
                print("[spoofer] reconnect via client.close")
                return
            except Exception as e:
                print(f"[spoofer] client.close raised: {e}")

    # --------------------------------------------------------
    async def handle(self, message, cmd=None, args=None):
        _CALL_COUNT[0] += 1
        print(f"[spoofer] handle called cmd={cmd!r} args={args!r}")
        try:
            await self._dispatch_inner(message, cmd, args)
        except Exception as e:
            print("[spoofer] DISPATCH RAISED:")
            traceback.print_exc()
            short = f"{type(e).__name__}: {e}"
            try:
                await _reply(message, _ui_err(f"`spoofer` crash — {short}"))
            except Exception:
                pass

    async def on_message(self, message, cmd=None, args=None):
        await self.handle(message, cmd, args)

    async def execute(self, message, cmd=None, args=None):
        await self.handle(message, cmd, args)

    async def run_command(self, message, cmd=None, args=None):
        await self.handle(message, cmd, args)

    async def dispatch(self, message, cmd=None, args=None):
        await self.handle(message, cmd, args)

    async def process(self, message, cmd=None, args=None):
        await self.handle(message, cmd, args)

    async def on_command(self, message, cmd=None, args=None):
        await self.handle(message, cmd, args)

    # --------------------------------------------------------
    async def _dispatch_inner(self, message, cmd=None, args=None):
        if cmd is None:
            content = getattr(message, "content", "") or ""
            parts = content.split()
            if not parts:
                return
            cmd = parts[0].lstrip("$./!").lower()
            args = parts[1:]
        if args is None:
            args = []
        elif isinstance(args, str):
            args = args.split()
        if isinstance(cmd, str):
            cmd = cmd.lstrip("$./!").lower()
        while args and isinstance(args[0], str) and \
                args[0].lstrip("$./!").lower() == cmd:
            args = args[1:]

        print(f"[spoofer] dispatch cmd={cmd!r} args={args!r}")

        if cmd not in self.COMMANDS:
            print(f"[spoofer] rejecting unknown cmd={cmd!r}")
            return

        self._ensure_instance_patch()

        client = S.CLIENT
        if client is None:
            await _reply(message, _ui_err("client not ready"))
            return

        # ---------- spooferdiag ----------
        if cmd == "spooferdiag":
            lines = self._build_diag()
            print("[spooferdiag] ====")
            for ln in lines:
                print(f"[spooferdiag] {ln}")
            print("[spooferdiag] ====")
            await _reply(message, _ansi(lines))
            return

        # ---------- platform ----------
        if cmd == "platform":
            if len(args) < 1:
                st = _POOL_STATE[0]
                lines = [f"  mode:   {st['mode']}",
                         f"  pool:   {', '.join(st['keys']) or '-'}",
                         f"  cursor: {st['cursor']}",
                         "",
                         "  available:"]
                for k in sorted(PLATFORM_PRESETS.keys()):
                    lines.append(f"    {k:<12} {PLATFORM_PRESETS[k]['label']}")
                await _reply(message, _ansi(lines))
                return
            plat = args[0].lower()
            if plat == "off":
                plat = "desktop"
            if plat not in PLATFORM_PRESETS:
                await _reply(message, _ui_err(f"unknown platform: {plat}"))
                return
            _set_pool([plat])
            _POOL_STATE[0]["mode"] = "sticky"
            await _reply(message, _ui_ok(
                f"single -> {PLATFORM_PRESETS[plat]['label']} (sticky)"))
            await self._safe_reconnect()
            return

        # ---------- spoof / spoofer ----------
        if cmd in ("spoof", "spoofer"):
            if len(args) < 1:
                await _reply(message, _ui_info(
                    "usage: spoof <platform> | add <p> | remove <p> | "
                    "pool | mode <rotate|random|sticky> | clear | "
                    "status | reset"))
                return
            sub = args[0].lower()

            if sub == "status":
                await self._send_status(message)
                return

            if sub == "reset":
                _set_pool(["desktop"])
                _POOL_STATE[0]["mode"] = "sticky"
                await _reply(message, _ui_ok("reset -> Desktop (sticky)"))
                await self._safe_reconnect()
                return

            if sub == "pool":
                st = _POOL_STATE[0]
                lines = [f"  mode:   {st['mode']}",
                         f"  cursor: {st['cursor']}",
                         f"  size:   {len(st['keys'])}",
                         "",
                         "  keys:"]
                if st["keys"]:
                    for i, k in enumerate(st["keys"]):
                        marker = "->" if (st["mode"] == "rotate"
                                          and i == (st["cursor"] % max(len(st["keys"]), 1))) else "  "
                        lines.append(f"    {marker} {k:<12} {PLATFORM_PRESETS[k]['label']}")
                else:
                    lines.append("    (empty)")
                await _reply(message, _ansi(lines))
                return

            if sub == "clear":
                _set_pool([])
                await _reply(message, _ui_ok("pool cleared — no spoofing"))
                return

            if sub == "add":
                if len(args) < 2:
                    await _reply(message, _ui_err("usage: spoof add <platform>"))
                    return
                key = args[1].lower()
                if key not in PLATFORM_PRESETS:
                    await _reply(message, _ui_err(f"unknown platform: {key}"))
                    return
                if _add_key(key):
                    await _reply(message, _ui_ok(
                        f"added {key} -> pool size {len(_pool_keys())}"))
                else:
                    await _reply(message, _ui_info(f"{key} already in pool"))
                return

            if sub == "remove":
                if len(args) < 2:
                    await _reply(message, _ui_err("usage: spoof remove <platform>"))
                    return
                key = args[1].lower()
                if _remove_key(key):
                    await _reply(message, _ui_ok(
                        f"removed {key} -> pool size {len(_pool_keys())}"))
                else:
                    await _reply(message, _ui_err(f"{key} not in pool"))
                return

            if sub == "mode":
                if len(args) < 2:
                    await _reply(message, _ui_err(
                        "usage: spoof mode <rotate|random|sticky>"))
                    return
                m = args[1].lower()
                if m not in ("rotate", "random", "sticky"):
                    await _reply(message, _ui_err(f"unknown mode: {m}"))
                    return
                _POOL_STATE[0]["mode"] = m
                _POOL_STATE[0]["cursor"] = 0
                await _reply(message, _ui_ok(f"mode -> {m}"))
                return

            if sub not in PLATFORM_PRESETS:
                await _reply(message, _ui_err(f"unknown platform: {sub}"))
                return
            _set_pool([sub])
            _POOL_STATE[0]["mode"] = "sticky"
            await _reply(message, _ui_ok(
                f"spoofed -> {PLATFORM_PRESETS[sub]['label']}"))
            await self._safe_reconnect()
            return

        # ---------- vr / console ----------
        if cmd in ("vr", "console"):
            _set_pool([cmd])
            _POOL_STATE[0]["mode"] = "sticky"
            await _reply(message, _ui_ok(
                f"platform -> {PLATFORM_PRESETS[cmd]['label']}"))
            await self._safe_reconnect()
            return

        # ---------- spoofstatus ----------
        if cmd == "spoofstatus":
            await self._send_status(message)
            return

        # ---------- spoofreset ----------
        if cmd == "spoofreset":
            _set_pool(["desktop"])
            _POOL_STATE[0]["mode"] = "sticky"
            await _reply(message, _ui_ok("platform reset -> Desktop"))
            await self._safe_reconnect()
            return

    # --------------------------------------------------------
    async def _send_status(self, message):
        p = self._last_props or {}
        st = _POOL_STATE[0]
        client = S.CLIENT
        _, gw = _find_client_gateway(client)
        state_str = "-"
        if gw is not None and hasattr(gw, "get_state"):
            try:
                gws = gw.get_state()
                state_str = f"{gws.get('state')} / conn={gws.get('is_connected')}"
            except Exception:
                pass
        cls, _ = _find_gw_class()
        class_ok = bool(getattr(cls, "_spoofer_patched", False)) if cls else False
        inst_ok  = bool(getattr(gw, "_spoofer_instance_patched", False)) if gw else False
        if not inst_ok and self._instance_patched_attr:
            inst_ok = True
        lines = [
            f"  mode:         {st['mode']}",
            f"  pool:         {', '.join(st['keys']) or '-'}",
            f"  cursor:       {st['cursor']}",
            f"  last pick:    {st['last']['label'] if st['last'] else '-'}",
            f"  class patch:  {'YES' if class_ok else 'NO'}",
            f"  inst patch:   {'YES' if inst_ok else 'NO'}",
            f"  identify #:   {self._identify_count}",
            f"  reconnects:   {self._reconnect_count}",
            f"  handle calls: {_CALL_COUNT[0]}",
            f"  gateway:      {state_str}",
            f"  os:           {p.get('os', '?')}",
            f"  browser:      {p.get('browser', '?')}",
            f"  device:       {p.get('device', '?')}",
        ]
        await _reply(message, _ansi(lines))
