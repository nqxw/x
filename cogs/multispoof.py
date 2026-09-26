# cogs/multispoof.py | concurrent gateway sessions — one per client type
import asyncio
import json
import time
import traceback
import aiohttp
import modifyself_shim as discord
from . import state as S


SATELLITES = {
    "mobile": {
        "os": "Android", "browser": "Discord Android", "device": "Android",
        "ua": ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"),
        "label": "Phone (Android)",
    },
    "ios": {
        "os": "iOS", "browser": "Discord iOS", "device": "iPhone",
        "ua": ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
               "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"),
        "label": "iPhone (iOS)",
    },
    "vr": {
        "os": "Android", "browser": "Discord VR", "device": "Quest 3",
        "ua": ("Mozilla/5.0 (Linux; Android 12; Quest 3) AppleWebKit/537.36 "
               "(KHTML, like Gecko) OculusBrowser/37.0.0.0.43 "
               "SamsungBrowser/4.0 Chrome/122.0.6261.140 VR Safari/537.36"),
        "label": "VR Headset (Quest)",
    },
    "console": {
        "os": "Windows", "browser": "Discord Embedded", "device": "console",
        "ua": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) discord/1.0.9044 Chrome/120.0.6099.291 "
               "Electron/28.2.10 Safari/537.36"),
        "label": "Console",
    },
    "embedded": {
        "os": "Windows", "browser": "Discord Embedded", "device": "",
        "ua": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) discord/1.0.9044 Chrome/120.0.6099.291 "
               "Electron/28.2.10 Safari/537.36"),
        "label": "Embedded",
    },
    "web": {
        "os": "Windows", "browser": "Chrome", "device": "",
        "ua": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        "label": "Web (Chrome)",
    },
}


def _ansi(lines):
    fn = getattr(S, "_ansi_block", None)
    if callable(fn):
        try:
            return fn(lines)
        except Exception:
            pass
    return "> ```ansi\n" + "\n".join("> " + str(l) for l in lines) + "\n> ```"


async def _reply(message, content):
    try:
        return await message.edit(content=content)
    except Exception:
        pass
    try:
        await message.delete()
    except Exception:
        pass
    try:
        return await message.channel.send(content)
    except Exception as e:
        print(f"[multispoof] reply failed: {e}")


class _Satellite:
    def __init__(self, name, profile, token):
        self.name = name
        self.profile = profile
        self.token = token
        self.ws = None
        self.task = None
        self.heartbeat_interval = None
        self.seq = None
        self.session_id = None
        self.ready_user = None
        self.alive = False
        self.identified = False
        self.started_at = 0.0
        self.reconnects = 0

    def _identify_payload(self):
        p = self.profile
        props = {
            "os": p["os"],
            "browser": p["browser"],
            "device": p["device"],
            "system_locale": "en-US",
            "has_client_mods": False,
            "client_version": "1.0.9044",
            "browser_user_agent": p["ua"],
            "browser_version": "120.0.6099.291",
            "os_version": "10",
            "referrer": "",
            "referring_domain": "",
            "referrer_current": "",
            "referring_domain_current": "",
            "release_channel": "stable",
            "client_build_number": S.MULTISPOOF_CLIENT_BUILD,
            "client_event_source": None,
        }
        return {
            "op": 2,
            "d": {
                "token": self.token,
                "capabilities": 30717,
                "properties": props,
                "presence": {"status": "online", "since": 0,
                             "activities": [], "afk": False},
                "compress": False,
                "client_state": {
                    "guild_versions": {},
                    "highest_last_message_id": "0",
                    "read_state_version": 0,
                    "user_guild_settings_version": -1,
                    "user_settings_version": -1,
                    "private_channels_version": "0",
                    "api_code_version": 0,
                },
            },
        }

    async def run(self):
        self.alive = True
        self.started_at = time.time()
        backoff = 5
        while self.alive:
            try:
                async with aiohttp.ClientSession() as sess:
                    async with sess.ws_connect(
                        S.MULTISPOOF_GATEWAY, heartbeat=30, max_msg_size=0,
                    ) as ws:
                        self.ws = ws
                        self.identified = False
                        async for msg in ws:
                            if not self.alive:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(msg.data)
                                except Exception:
                                    continue
                                await self._on_frame(data)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED,
                                              aiohttp.WSMsgType.ERROR):
                                break
                backoff = 5
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[multispoof:{self.name}] ws error: {e}")
            self.identified = False
            if not self.alive:
                break
            self.reconnects += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _on_frame(self, data):
        op = data.get("op")
        if op == 10:
            hb = data.get("d", {}).get("heartbeat_interval", 41250) / 1000.0
            self.heartbeat_interval = hb
            asyncio.create_task(self._heartbeat_loop())
            try:
                await self.ws.send_json(self._identify_payload())
            except Exception as e:
                print(f"[multispoof:{self.name}] identify send failed: {e}")
        elif op == 11:
            pass
        elif op == 7:
            try:
                await self.ws.close()
            except Exception:
                pass
        elif op == 9:
            print(f"[multispoof:{self.name}] invalid session")
            await asyncio.sleep(3)
            try:
                await self.ws.close()
            except Exception:
                pass
        elif op == 0:
            self.seq = data.get("s", self.seq)
            t = data.get("t")
            if t == "READY":
                self.session_id = (data.get("d") or {}).get("session_id")
                self.ready_user = (data.get("d") or {}).get("user") or {}
                self.identified = True
                uname = self.ready_user.get("username", "?")
                print(f"[multispoof:{self.name}] READY as {uname} "
                      f"(session {(self.session_id or '?')[:8]})")

    async def _heartbeat_loop(self):
        while self.alive and self.ws and not self.ws.closed:
            try:
                await asyncio.sleep(self.heartbeat_interval or 41.25)
                if self.ws and not self.ws.closed:
                    await self.ws.send_json({"op": 1, "d": self.seq})
            except asyncio.CancelledError:
                break
            except Exception:
                break

    async def close(self):
        self.alive = False
        try:
            if self.ws and not self.ws.closed:
                await self.ws.close(code=1000)
        except Exception:
            pass
        self.identified = False


class MultiSpoofCog:
    COMMANDS = {"multispoof", "mspoof"}

    def __init__(self):
        print("[multispoof] cog ready — "
              f"available: {', '.join(SATELLITES.keys())}")

    async def handle(self, message, cmd=None, args=None):
        print(f"[multispoof] handle cmd={cmd!r} args={args!r}")
        try:
            await self._dispatch(message, cmd, args)
        except Exception as e:
            traceback.print_exc()
            await _reply(message, _ansi([f"  \u2717  multispoof crash: {e}"]))

    async def _dispatch(self, message, cmd, args):
        if cmd is None:
            parts = (getattr(message, "content", "") or "").split()
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

        sub = (args[0].lower() if args else "status")

        if sub == "start":
            names = [a.lower() for a in args[1:]] or ["mobile", "ios", "vr", "console"]
            launched = []
            for n in names:
                if n not in SATELLITES:
                    await _reply(message, _ansi([f"  \u2717  unknown satellite: {n}"]))
                    continue
                existing = S.satellites.get(n)
                if existing and existing.alive:
                    launched.append(f"{n}(already)")
                    continue
                sat = _Satellite(n, SATELLITES[n], S.TOKEN)
                S.satellites[n] = sat
                sat.task = asyncio.create_task(sat.run())
                launched.append(n)
            await _reply(message, _ansi(
                ["  \u2713  launched: " + ", ".join(launched)] +
                ["",
                 "  satellites stay connected in parallel — each one",
                 "  occupies a distinct client slot on the account."]))

        elif sub == "stop":
            if not S.satellites:
                await _reply(message, _ansi(["  \u2022  no satellites running"]))
                return
            names = [a.lower() for a in args[1:]] or list(S.satellites.keys())
            for n in names:
                sat = S.satellites.pop(n, None)
                if sat:
                    if sat.task and not sat.task.done():
                        sat.task.cancel()
                    await sat.close()
            await _reply(message, _ansi(["  \u2713  stopped: " + ", ".join(names)]))

        elif sub == "status":
            if not S.satellites:
                await _reply(message, _ansi([
                    "  \u2022  no satellites running",
                    f"  available: {', '.join(SATELLITES.keys())}",
                    "  usage: .multispoof start [names...]",
                ]))
                return
            lines = ["  name       state       session    uptime   label"]
            for n, sat in S.satellites.items():
                if sat.identified:
                    state = "READY"
                elif sat.alive:
                    state = "connecting"
                else:
                    state = "dead"
                sid = (sat.session_id or "?")[:8]
                up = int(time.time() - sat.started_at) if sat.started_at else 0
                lbl = SATELLITES[n]["label"]
                lines.append(f"  {n:<10} {state:<11} {sid:<10} {up:>5}s   {lbl}")
            total = len(S.satellites)
            lines += ["", f"  {total} satellite(s) online + main desktop session"]
            await _reply(message, _ansi(lines))

        elif sub == "restart":
            names = list(S.satellites.keys())
            for n in names:
                sat = S.satellites.pop(n, None)
                if sat:
                    if sat.task and not sat.task.done():
                        sat.task.cancel()
                    await sat.close()
            await _reply(message, _ansi(["  \u2713  stopped — relaunching..."]))
            await asyncio.sleep(2)
            for n in names:
                sat = _Satellite(n, SATELLITES[n], S.TOKEN)
                S.satellites[n] = sat
                sat.task = asyncio.create_task(sat.run())
            await _reply(message, _ansi(["  \u2713  relaunched: " + ", ".join(names)]))

        elif sub == "list":
            lines = ["  available client types:"]
            for n, p in SATELLITES.items():
                lines.append(f"    {n:<10} {p['label']:<24} browser={p['browser']}")
            await _reply(message, _ansi(lines))

        else:
            await _reply(message, _ansi([
                "usage: .multispoof start [names...]",
                "       .multispoof stop [names...]",
                "       .multispoof status",
                "       .multispoof restart",
                "       .multispoof list",
                f"       available: {', '.join(SATELLITES.keys())}",
            ]))
