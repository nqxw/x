# cogs/quests.py | quest completer + orb badge + autoclaim
# v4.6 — rpcauto pushes app_id to RPC before heartbeat; clears on exit
import asyncio
import base64
import json
import os
import re
import time
import traceback
from datetime import datetime, timezone
from uuid import uuid4

import modifyself_shim as discord
from . import state as S

try:
    from modifyself.http.client import HTTPClient as _ForkHTTP
    from modifyself.http.route import Route as _ForkRoute
    HAS_FORK_HTTP = True
except Exception as _e:
    print(f"[quests] fork HTTPClient unavailable: {_e}")
    _ForkHTTP = None
    _ForkRoute = None
    HAS_FORK_HTTP = False

try:
    from hcaptcha_challenger.agent import AgentV, AgentConfig
    HAS_HCAPTCHA = True
except ImportError:
    HAS_HCAPTCHA = False
    AgentV = None
    AgentConfig = None

SUPPORTED_TASKS = (
    "WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE",
    "PLAY_ON_DESKTOP", "PLAY_ON_DESKTOP_V2",
    "PLAY_ACTIVITY", "STREAM_ON_DESKTOP",
    "PLAY_ON_XBOX", "PLAY_ON_PLAYSTATION",
    "COLLECT_ITEM", "COLLECT",
    "MISSION_COMPLETE", "COMPLETE_QUEST",
    "COMPLETE_ACTIVITY", "EXTERNAL_TASK",
    "LAUNCH_GAME", "LAUNCH_QUEST",
    "ACHIEVEMENT_IN_ACTIVITY", "ACHIEVEMENT",
    "COMPLETE_ACHIEVEMENT", "EARN_ACHIEVEMENT",
)
VIDEO_TASKS = ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE")
HEARTBEAT_TASKS = ("PLAY_ON_DESKTOP", "PLAY_ON_DESKTOP_V2", "PLAY_ACTIVITY",
                   "STREAM_ON_DESKTOP", "PLAY_ON_XBOX", "PLAY_ON_PLAYSTATION")
ACHIEVEMENT_TASKS = ("ACHIEVEMENT_IN_ACTIVITY", "ACHIEVEMENT",
                     "COMPLETE_ACHIEVEMENT", "EARN_ACHIEVEMENT")
MISSION_TASKS = ("COLLECT_ITEM", "COLLECT", "MISSION_COMPLETE", "COMPLETE_QUEST",
                 "COMPLETE_ACTIVITY", "EXTERNAL_TASK", "LAUNCH_GAME", "LAUNCH_QUEST")

DISCORD_HCAPTCHA_SITEKEY = "4c672d35-0701-42b2-88c3-78380b0db560"
ORB_SKU = "1342211853484429445"
CLIENT_BUILD_NUMBER = 366000

QUEST_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

CAPTURED_SUPER_PROPERTIES = ""
CAPTURED_USER_AGENT = ""

_hcaptcha_agent = None
_agent_init_lock = asyncio.Lock()
_solve_lock = asyncio.Lock()

_autoclaim_task = None

_transport = None
_transport_lock = asyncio.Lock()

# RPC override tracking — remember what we pushed so we can clear it
_rpc_override_state = {"slot": 6, "active": False, "app_id": None, "prev": None}


# ============================================================
# fingerprint helpers
# ============================================================
def _decode_super_properties(sp_b64: str) -> dict:
    try:
        padded = sp_b64 + "=" * (-len(sp_b64) % 4)
        raw = base64.b64decode(padded)
        return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as e:
        print(f"[quests] cannot decode captured super-properties: {e}")
        return {}


def _quest_headers(token):
    token = token.strip().strip('"').strip("'")
    ua = CAPTURED_USER_AGENT or QUEST_UA
    sp = CAPTURED_SUPER_PROPERTIES.strip() if CAPTURED_SUPER_PROPERTIES else ""
    if not sp:
        sp_dict = {
            "os": "Windows", "browser": "Chrome", "device": "",
            "system_locale": "en-US", "has_client_mods": False,
            "client_version": "1.0.9044",
            "browser_user_agent": ua, "browser_version": "131.0.0.0",
            "os_version": "10", "referrer": "", "referring_domain": "",
            "referrer_current": "", "referring_domain_current": "",
            "release_channel": "stable",
            "client_build_number": CLIENT_BUILD_NUMBER,
            "client_event_source": None, "client_launch_id": str(uuid4()),
        }
        sp = base64.b64encode(
            json.dumps(sp_dict, separators=(",", ":")).encode()
        ).decode()
    return {
        "authorization": token,
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "user-agent": ua,
        "x-super-properties": sp,
        "x-discord-locale": "en-US",
        "x-discord-timezone": "America/New_York",
        "x-debug-options": "bugReporterEnabled",
        "origin": "https://discord.com",
        "referer": "https://discord.com/quest-home",
    }


def _current_voice_channel_id():
    client = S.CLIENT
    if client is None:
        return None
    try:
        from . import voice as _v
        svc = getattr(_v, "_self_vc", {}) or {}
        ch = svc.get("channel_id")
        gid = svc.get("guild_id")
        if ch and gid:
            guilds = getattr(client._state, "_guilds", None) or {}
            g = guilds.get(int(gid))
            if g is not None:
                try:
                    if g.get_channel(int(ch)) is not None:
                        return int(ch)
                except Exception:
                    pass
    except Exception:
        pass
    for attr in ("_voice_state", "_current_voice", "_voice"):
        vs = getattr(client, attr, None)
        if vs is None:
            continue
        cid = getattr(vs, "channel_id", None)
        if cid:
            try:
                return int(cid)
            except Exception:
                pass
    return None


# ============================================================
# RPC override helper — pushes app_id to gateway presence
# ============================================================
async def _push_rpc_override(app_id, name=None):
    """
    Send an op:3 presence update with a custom activity carrying the
    given application_id. Called before heartbeat quests so the backend
    sees "this user is running app <id>" when the heartbeat arrives.
    """
    client = S.CLIENT
    if client is None or not app_id:
        return False

    activity = {
        "application_id": str(app_id),
        "type": 0,
        "name": name or "Game",
        "details": "Playing",
        "state": "In Menu",
        "instance": True,
        "flags": 0,
    }

    # save previous activity so we can restore on clear
    try:
        gw = getattr(client, "_gateway", None)
        if gw and hasattr(gw, "send_json"):
            payload = {
                "op": 3,
                "d": {
                    "since": 0,
                    "activities": [activity],
                    "status": "online",
                    "afk": False,
                },
            }
            call = gw.send_json(payload)
            if asyncio.iscoroutine(call):
                await call
            _rpc_override_state["active"] = True
            _rpc_override_state["app_id"] = str(app_id)
            print(f"[quests] rpc override pushed: app_id={app_id} name={name!r}")
            return True
    except Exception as e:
        print(f"[quests] rpc override failed: {e}")
    return False


async def _clear_rpc_override():
    """Send an empty activities list to clear the presence."""
    client = S.CLIENT
    if client is None:
        return False
    try:
        gw = getattr(client, "_gateway", None)
        if gw and hasattr(gw, "send_json"):
            payload = {
                "op": 3,
                "d": {
                    "since": 0,
                    "activities": [],
                    "status": "online",
                    "afk": False,
                },
            }
            call = gw.send_json(payload)
            if asyncio.iscoroutine(call):
                await call
            _rpc_override_state["active"] = False
            _rpc_override_state["app_id"] = None
            print("[quests] rpc override cleared")
            return True
    except Exception as e:
        print(f"[quests] rpc override clear failed: {e}")
    return False


# ============================================================
# transports
# ============================================================
class _ForkTransport:
    def __init__(self, token, headers):
        try:
            from modifyself.headers import HeaderSpoofer, EMULATION
        except Exception as e:
            raise RuntimeError(f"cannot import HeaderSpoofer: {e}")

        self._token = token
        self._spoofer = HeaderSpoofer(token, EMULATION)
        self._client = _ForkHTTP(
            token,
            headers=self._spoofer,
        )
        print(f"[quests] fork HTTPClient: {type(self._client).__module__}."
              f"{type(self._client).__name__} (wreq + HeaderSpoofer)")

    async def request(self, method, url, headers=None, json=None):
        path = url
        for prefix in ("https://discord.com/api/v10",
                       "https://discord.com/api/v9",
                       "https://discord.com/api",
                       "https://discord.com"):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break
        if not path.startswith("/"):
            path = "/" + path

        route = _ForkRoute(method.upper(), path)

        for attempt in range(3):
            try:
                call = self._client.request(route, json=json)
                if asyncio.iscoroutine(call):
                    call = await call
                return 200, (call if isinstance(call, dict) else {"body": call})
            except Exception as e:
                status = (getattr(e, "status", None)
                          or getattr(getattr(e, "response", None), "status", None))
                if status:
                    return int(status), {"error": str(e)}
                print(f"[quests] fork request attempt {attempt+1} failed: {e}")
                await asyncio.sleep(0.5 * (attempt + 1))
        return 0, {}


class _AiohttpTransport:
    def __init__(self, token, headers):
        import aiohttp
        self._aio = aiohttp
        self._headers = headers or _quest_headers(token)
        self._session = None

    async def _ensure(self):
        if self._session is None or self._session.closed:
            timeout = self._aio.ClientTimeout(total=45, connect=10, sock_read=30)
            self._session = self._aio.ClientSession(timeout=timeout)

    async def request(self, method, url, headers=None, json=None):
        await self._ensure()
        merged = dict(self._headers)
        if headers:
            merged.update(headers)
        async with self._session.request(method, url, headers=merged, json=json) as r:
            text = await r.text()
            try:
                body = json.loads(text)
            except Exception:
                body = {}
            return r.status, body

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


async def _get_transport(token):
    global _transport
    if _transport is not None:
        return _transport
    async with _transport_lock:
        if _transport is not None:
            return _transport
        if HAS_FORK_HTTP:
            try:
                _transport = _ForkTransport(token, None)
                print("[quests] using fork HTTPClient transport (wreq + HeaderSpoofer)")
                return _transport
            except Exception as e:
                print(f"[quests] fork transport init failed: {e} — falling back to aiohttp")
                traceback.print_exc()
        try:
            _transport = _AiohttpTransport(token, None)
            print("[quests] using aiohttp transport (no TLS impersonation)")
        except Exception as e:
            print(f"[quests] aiohttp transport init failed: {e}")
            _transport = None
        return _transport


# ============================================================
# API
# ============================================================
class APIError(Exception):
    def __init__(self, status, body=None):
        super().__init__(f"Discord API {status}")
        self.status = status
        self.body = body or {}


async def _api(method, url, headers=None, json_body=None, retries=3):
    tr = await _get_transport(S.TOKEN)
    if tr is None:
        raise APIError(0, {"error": "no transport"})
    last = None
    for attempt in range(retries):
        try:
            status, body = await tr.request(method, url, headers=headers, json=json_body)
            if status == 0:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            if status >= 400:
                raise APIError(status, body)
            return body
        except APIError as e:
            last = e
            if e.status == 429:
                ra = float(e.body.get("retry_after", 1.0))
                await asyncio.sleep(ra)
                continue
            if 500 <= e.status < 600:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            raise
    if last is not None:
        raise last
    return {}


# ============================================================
# hcaptcha
# ============================================================
async def _get_hcaptcha_agent():
    global _hcaptcha_agent
    if _hcaptcha_agent is not None:
        return _hcaptcha_agent
    if not HAS_HCAPTCHA:
        return None
    async with _agent_init_lock:
        if _hcaptcha_agent is not None:
            return _hcaptcha_agent
        try:
            config = AgentConfig(
                DISABLE_ANONYMIZED_TELEMETRY=True,
                user_data_dir=os.path.abspath("data/hcaptcha_profile"),
                HEADLESS=True,
            )
            _hcaptcha_agent = AgentV(config=config)
        except Exception as e:
            print(f"[hcaptcha] agent init failed: {e}")
            _hcaptcha_agent = None
        return _hcaptcha_agent


async def _solve_hcaptcha(sitekey: str, url: str, rqdata: str = None):
    if not HAS_HCAPTCHA:
        return None
    agent = await _get_hcaptcha_agent()
    if agent is None:
        return None
    try:
        payload = {"sitekey": sitekey, "url": url, "rqdata": rqdata or "", "type": "hsl"}
        async with _solve_lock:
            resp = await agent.solve(payload)
        token = None
        if resp is not None:
            if hasattr(resp, "generated_pass_UUID"):
                token = resp.generated_pass_UUID
            elif hasattr(resp, "token"):
                token = resp.token
            elif isinstance(resp, dict):
                token = resp.get("generated_pass_UUID") or resp.get("token") or resp.get("response")
            elif isinstance(resp, str):
                token = resp
        return token
    except Exception as e:
        print(f"[hcaptcha] solve failed: {e}")
        return None


# ============================================================
# quest model
# ============================================================
class QuestRecord:
    def __init__(self, data):
        self.data = data
        self.selected_task = self._pick()
        self.target = float(self.tasks.get(self.selected_task, {}).get("target", 0) or 0)

    @property
    def id(self): return str(self.data.get("id"))
    @property
    def cfg(self): return self.data.get("config", {})
    @property
    def msgs(self): return self.cfg.get("messages", {})
    @property
    def user_status(self): return self.data.get("user_status") or {}

    @property
    def tasks(self):
        tc = (self.cfg.get("task_config_v2") or self.cfg.get("task_config")
              or self.cfg.get("taskConfigV2") or self.cfg.get("taskConfig") or {})
        return tc.get("tasks", {})

    @property
    def name(self):
        return self.msgs.get("quest_name") or self.msgs.get("game_title") or "Quest"

    @property
    def reward(self):
        rw = self.cfg.get("rewards_config", {}).get("rewards", [])
        if rw:
            return rw[0].get("messages", {}).get("name") or "Reward"
        return "Reward"

    @property
    def app_id(self):
        direct = self.cfg.get("application", {}).get("id")
        if direct:
            return direct
        tcfg = self.tasks.get(self.selected_task) or {}
        apps = tcfg.get("applications") or []
        if apps and isinstance(apps[0], dict):
            return apps[0].get("id")
        for t, body in self.tasks.items():
            apps = (body or {}).get("applications") or []
            if apps and isinstance(apps[0], dict):
                return apps[0].get("id")
        return None

    @property
    def expires_at(self):
        return self.cfg.get("expires_at") or ""

    def is_completed(self): return bool(self.user_status.get("completed_at"))
    def is_claimed(self):   return bool(self.user_status.get("claimed_at"))
    def is_enrolled(self):  return bool(self.user_status.get("enrolled_at"))

    def is_supported(self):
        return (self.selected_task in SUPPORTED_TASKS
                or self.selected_task in MISSION_TASKS)

    def progress_value(self):
        prog = self.user_status.get("progress") or {}
        p = prog.get(self.selected_task) or {}
        v = p.get("value")
        if v is not None:
            try:
                return float(v)
            except Exception:
                pass
        sps = self.user_status.get("stream_progress_seconds")
        if sps is not None:
            try:
                return float(sps)
            except Exception:
                pass
        return 0.0

    def progress_pct(self):
        return min(100, int(self.progress_value() / self.target * 100)) if self.target else 0

    def _pick(self):
        for t in SUPPORTED_TASKS:
            if t in self.tasks:
                return t
        for t in MISSION_TASKS:
            if t in self.tasks:
                return t
        return next(iter(self.tasks.keys()), "UNKNOWN")


# ============================================================
# service
# ============================================================
class QuestService:
    def __init__(self, token):
        self.token = token.strip().strip('"').strip("'")
        try:
            self.uid = S._b64uid(token)
        except Exception:
            self.uid = None

    async def fetch(self):
        try:
            d = await _api("GET", "https://discord.com/api/v9/quests/@me")
        except APIError as e:
            if e.status in (401, 403):
                print(f"[quests] auth rejected: {e.status} {e.body}")
                raise
            return []
        except Exception as e:
            print(f"[quests] fetch error: {e}")
            return []
        out = []
        for r in (d.get("quests") or []):
            q = QuestRecord(r)
            if q.expires_at:
                try:
                    exp = datetime.fromisoformat(q.expires_at.replace("Z", "+00:00"))
                    if datetime.now(timezone.utc) > exp:
                        continue
                except Exception:
                    pass
            out.append(q)
        return out

    async def re_fetch_one(self, quest_id):
        try:
            return await _api("GET", f"https://discord.com/api/v9/quests/{quest_id}")
        except Exception:
            return None

    async def enroll(self, quest):
        d = await _api("POST", f"https://discord.com/api/v9/quests/{quest.id}/enroll",
                       json_body={"location": 11, "is_targeted": False})
        if d:
            quest.data["user_status"] = d

    async def run(self, quest):
        if not quest.is_enrolled():
            try:
                await self.enroll(quest)
            except APIError as e:
                print(f"[quests] enroll failed for {quest.name}: {e.status} {e.body}")
                return "enroll_failed"
        if not quest.is_supported():
            return "unsupported"
        task = quest.selected_task
        try:
            if task in ACHIEVEMENT_TASKS:
                return await self._achievements(quest)
            if task in VIDEO_TASKS:
                return await self._video(quest)
            if task in HEARTBEAT_TASKS:
                return await self._heartbeat_task(quest, task)
            if task in MISSION_TASKS:
                return await self._mission(quest)
            return await self._heartbeat_task(quest, "PLAY_ON_DESKTOP")
        except asyncio.CancelledError:
            raise
        except APIError as e:
            print(f"[quests] {quest.name} api error: {e.status} {e.body}")
            return "api_error"
        except Exception as e:
            print(f"[quests] {quest.name} unexpected: {type(e).__name__}: {e}")
            return "error"

    async def _video(self, quest):
        target = quest.target or 1.0
        last_sent = 0.0
        try:
            prog = quest.user_status.get("progress", {}).get(quest.selected_task, {})
            last_sent = float(prog.get("value", 0) or 0)
        except Exception:
            pass
        wall_start = time.time()
        stall = 0
        last_seen = last_sent
        deadline = wall_start + max(60.0, target * 1.5)
        tick_count = 0
        while time.time() < deadline:
            tick_count += 1
            elapsed = time.time() - wall_start
            ts = min(target, last_sent + elapsed)
            if ts <= last_sent:
                ts = last_sent + 0.5
            try:
                d = await _api("POST",
                               f"https://discord.com/api/v9/quests/{quest.id}/video-progress",
                               json_body={"timestamp": float(ts)},
                               retries=2)
            except APIError as e:
                if e.status in (429, 400):
                    await asyncio.sleep(float(e.body.get("retry_after", 2.0)))
                    stall += 1
                    if stall >= 20:
                        break
                    continue
                break
            except Exception:
                break
            if d:
                quest.data["user_status"] = d
                last_sent = float(ts)
                if d.get("completed_at") or quest.is_completed():
                    return "completed"
                cur = quest.progress_value()
                if cur > last_seen + 0.5:
                    last_seen = cur
                    stall = 0
                else:
                    stall += 1
            if stall >= 20:
                break
            await asyncio.sleep(2.0 if tick_count < 3 else 5.0)
        fresh = await self.re_fetch_one(quest.id)
        if fresh:
            quest.data = fresh
        return "completed" if quest.is_completed() else "recovering"

    def _build_heartbeat_payloads(self, quest, task):
        payloads = []
        tcfg = quest.tasks.get(task) or {}
        app_id = quest.app_id
        external_ids = tcfg.get("external_ids") or []

        if task in ("PLAY_ON_DESKTOP", "PLAY_ON_DESKTOP_V2", "PLAY_ACTIVITY",
                    "STREAM_ON_DESKTOP"):
            ch = _current_voice_channel_id()
            if ch and app_id:
                payloads.append({
                    "stream_key": f"call:{ch}:1",
                    "application_id": str(app_id),
                    "terminal": False,
                })
            if app_id:
                payloads.append({
                    "application_id": str(app_id),
                    "terminal": False,
                })
            if ch:
                payloads.append({
                    "stream_key": f"call:{ch}:1",
                    "terminal": False,
                })
            if app_id:
                payloads.append({
                    "stream_key": "call:0:0",
                    "application_id": str(app_id),
                    "terminal": False,
                })
            payloads.append({
                "stream_key": f"call:{quest.id}:1",
                "terminal": False,
            })

        elif task in ("PLAY_ON_XBOX", "PLAY_ON_PLAYSTATION"):
            for eid in external_ids:
                payloads.append({"external_id": str(eid), "terminal": False})
            if not payloads:
                payloads.append({"stream_key": "call:0:0", "terminal": False})

        else:
            if app_id:
                payloads.append({"application_id": str(app_id), "terminal": False})
            payloads.append({"stream_key": f"call:{quest.id}:1", "terminal": False})

        return payloads

    async def _heartbeat_task(self, quest, task):
        payloads = self._build_heartbeat_payloads(quest, task)
        if not payloads:
            print(f"[heartbeat] {quest.name}: no payloads built for task {task}")
            return "no_payload"

        interval = 15
        active = payloads[0]
        last_seen = quest.progress_value()
        stall = 0
        max_runtime = min(1200, max(180, (quest.target or 60) * 2))
        start = time.time()

        ch = _current_voice_channel_id()
        print(f"[heartbeat] {quest.name} task={task} target={quest.target} "
              f"payloads={len(payloads)} vc={ch}")

        # RPC override: push the app_id so Discord sees "playing app X"
        # before the first heartbeat.
        rpc_pushed = False
        if quest.app_id:
            rpc_pushed = await _push_rpc_override(quest.app_id, name=quest.name)
            # give the gateway presence a moment to propagate
            if rpc_pushed:
                await asyncio.sleep(2.0)

        committed = False
        try:
            while time.time() - start < max_runtime:
                response = None
                rate_limited = False
                prev_val = quest.progress_value()
                candidates = payloads if not committed else [active]

                for p in candidates:
                    try:
                        d = await _api("POST",
                                       f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                                       json_body=p, retries=1)
                    except APIError as e:
                        if e.status == 429:
                            await asyncio.sleep(float(e.body.get("retry_after", 5.0)))
                            rate_limited = True
                            break
                        continue
                    except Exception:
                        continue

                    if not d:
                        continue

                    quest.data["user_status"] = d
                    new_val = quest.progress_value()

                    if new_val > prev_val + 0.5:
                        active = p
                        committed = True
                        response = d
                        break

                    response = d

                if rate_limited:
                    stall += 1
                    if stall >= 8:
                        break
                    await asyncio.sleep(interval)
                    continue

                if quest.is_completed():
                    break

                cur = quest.progress_value()
                if cur > last_seen + 0.5:
                    last_seen = cur
                    stall = 0
                else:
                    stall += 1

                if cur >= quest.target:
                    break

                if stall >= 8:
                    print(f"[heartbeat] {quest.name} stalled at {last_seen:.0f}/{quest.target:.0f} "
                          f"payload={active} committed={committed}")
                    break

                await asyncio.sleep(interval)

            # terminal ping
            try:
                t = dict(active); t["terminal"] = True
                await _api("POST",
                           f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                           json_body=t, retries=1)
            except Exception:
                pass
        finally:
            # clear RPC override if we pushed one
            if rpc_pushed:
                try:
                    await _clear_rpc_override()
                except Exception as e:
                    print(f"[quests] rpc clear on exit failed: {e}")

        fresh = await self.re_fetch_one(quest.id)
        if fresh:
            quest.data = fresh

        return "completed" if quest.is_completed() else "recovering"

    async def _achievements(self, quest):
        target = quest.target or 1.0
        payloads = self._build_heartbeat_payloads(quest, quest.selected_task)
        if not payloads:
            payloads = [{"stream_key": "call:0:0", "terminal": False}]
        interval = 5
        attempts = 0
        max_attempts = 60
        active = payloads[0]
        committed = False
        last_seen = quest.progress_value()
        stall = 0

        rpc_pushed = False
        if quest.app_id:
            rpc_pushed = await _push_rpc_override(quest.app_id, name=quest.name)
            if rpc_pushed:
                await asyncio.sleep(1.5)

        try:
            while attempts < max_attempts and not quest.is_completed():
                attempts += 1
                rate_limited = False
                prev_val = quest.progress_value()
                candidates = payloads if not committed else [active]

                for p in candidates:
                    try:
                        d = await _api("POST",
                                       f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                                       json_body=p, retries=1)
                    except APIError as e:
                        if e.status == 429:
                            await asyncio.sleep(float(e.body.get("retry_after", 5.0)))
                            rate_limited = True
                            break
                        continue
                    except Exception:
                        continue

                    if not d:
                        continue

                    quest.data["user_status"] = d
                    new_val = quest.progress_value()
                    if new_val > prev_val + 0.5:
                        active = p
                        committed = True
                        break

                if rate_limited:
                    stall += 1
                    if stall >= 12: break
                    continue

                if quest.is_completed() or quest.progress_value() >= target:
                    break
                cur = quest.progress_value()
                if cur > last_seen + 0.5:
                    last_seen = cur; stall = 0
                else:
                    stall += 1
                if stall >= 12: break
                await asyncio.sleep(interval)

            try:
                t = dict(active); t["terminal"] = True
                await _api("POST",
                           f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                           json_body=t, retries=1)
            except Exception:
                pass
        finally:
            if rpc_pushed:
                try:
                    await _clear_rpc_override()
                except Exception:
                    pass

        fresh = await self.re_fetch_one(quest.id)
        if fresh: quest.data = fresh
        return "completed" if quest.is_completed() else "recovering"

    async def _mission(self, quest):
        task = quest.selected_task
        target = quest.target or 0
        if target > 0:
            try:
                d = await _api("POST",
                               f"https://discord.com/api/v9/quests/{quest.id}/external-task-progress",
                               json_body={"task_id": task, "progress": {"value": target}},
                               retries=1)
                if d: quest.data["user_status"] = d
                if quest.is_completed(): return "completed"
            except APIError as e:
                if e.status != 404:
                    print(f"[mission] external-task-progress {e.status}: {e.body}")

        payloads = self._build_heartbeat_payloads(quest, task)
        if not payloads:
            payloads = [{"stream_key": f"call:{quest.id}:1", "terminal": False}]

        rpc_pushed = False
        if quest.app_id:
            rpc_pushed = await _push_rpc_override(quest.app_id, name=quest.name)
            if rpc_pushed:
                await asyncio.sleep(1.0)

        try:
            end_time = time.time() + 60
            last_seen = quest.progress_value()
            while time.time() < end_time:
                for p in payloads:
                    try:
                        d = await _api("POST",
                                       f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                                       json_body=p, retries=1)
                        if d: quest.data["user_status"] = d
                        if quest.is_completed(): break
                    except APIError as e:
                        if e.status == 429:
                            await asyncio.sleep(float(e.body.get("retry_after", 5.0)))
                            break
                        continue
                    except Exception:
                        continue
                if quest.is_completed(): break
                cur = quest.progress_value()
                if cur > last_seen + 0.5: last_seen = cur
                await asyncio.sleep(5)
        finally:
            if rpc_pushed:
                try:
                    await _clear_rpc_override()
                except Exception:
                    pass

        fresh = await self.re_fetch_one(quest.id)
        if fresh: quest.data = fresh
        return "completed" if quest.is_completed() else "recovering"


# ============================================================
# claim / orb
# ============================================================
async def _claim_quest(token, quest_id):
    url = f"https://discord.com/api/v9/quests/{quest_id}/claim"
    tr = await _get_transport(token)
    if tr is None:
        return False, "no transport"
    try:
        status, body = await tr.request("POST", url, json={})
        if status in (200, 201, 204):
            return True, json.dumps(body)[:200]
        if status == 429:
            return False, f"rate limited: {body}"
        text = json.dumps(body).lower()
        if status != 400 or "captcha" not in text:
            return False, f"http {status}: {body}"
        sitekey = body.get("captcha_sitekey") or DISCORD_HCAPTCHA_SITEKEY
        captcha_token = await _solve_hcaptcha(sitekey, "https://discord.com/quest-home",
                                              body.get("captcha_rqdata"))
        if not captcha_token:
            return False, "captcha solve failed"
        status2, body2 = await tr.request(
            "POST", url,
            headers={"x-captcha-key": captcha_token},
            json={"captcha_key": captcha_token})
        return status2 in (200, 201, 204), json.dumps(body2)[:200]
    except Exception as e:
        return False, f"request failed: {e}"


async def claim_orb(token):
    url = f"https://discord.com/api/v9/virtual-currency/skus/{ORB_SKU}/redeem"
    tr = await _get_transport(token)
    if tr is None:
        return False, "no transport"
    try:
        status, body = await tr.request("POST", url, json={})
        if status in (200, 201, 204):
            return True, json.dumps(body)[:200]
        text = json.dumps(body).lower()
        if status != 400 or "captcha" not in text:
            return False, json.dumps(body)[:200]
        captcha_token = await _solve_hcaptcha(
            body.get("captcha_sitekey") or DISCORD_HCAPTCHA_SITEKEY,
            "https://discord.com", body.get("captcha_rqdata"))
        if not captcha_token:
            return False, "captcha solve failed"
        status2, body2 = await tr.request(
            "POST", url,
            headers={"x-captcha-key": captcha_token},
            json={"captcha_key": captcha_token})
        return status2 in (200, 201, 204), json.dumps(body2)[:200]
    except Exception as e:
        return False, str(e)


# ============================================================
# background loops
# ============================================================
async def _ensure_autoclaim():
    global _autoclaim_task
    if _autoclaim_task is not None and not _autoclaim_task.done():
        return False
    _autoclaim_task = asyncio.create_task(autoclaim_loop())
    return True


async def autoclaim_loop():
    await asyncio.sleep(60)
    while True:
        try:
            cfg = S.load_config() or {}
            if not cfg.get("autoclaim_enabled"):
                await asyncio.sleep(300); continue
            svc = QuestService(S.TOKEN)
            try:
                quests = await svc.fetch()
            except APIError:
                quests = []
            for q in [q for q in quests if q.is_completed() and not q.is_claimed()]:
                print(f"[autoclaim] claiming {q.name}")
                ok, detail = await _claim_quest(S.TOKEN, q.id)
                if not ok:
                    print(f"[autoclaim] {q.name} failed: {detail}")
                await asyncio.sleep(3)
            await asyncio.sleep(300)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[autoclaim loop] {e}")
            await asyncio.sleep(120)


async def autoquest_run(token):
    svc = QuestService(token)
    try:
        quests = await svc.fetch()
    except APIError as e:
        print(f"[autoquest] auth failed: {e.status}")
        return
    for q in [q for q in quests if not q.is_completed() and q.is_supported()]:
        print(f"[AutoQuest] running {q.name} ({q.selected_task})")
        res = await svc.run(q)
        print(f"[AutoQuest] {q.name} → {res}")


# ============================================================
# cog
# ============================================================
class QuestsCog:
    COMMANDS = {"quest", "questrun", "questall", "autoquest", "autoclaim",
                "orbbadge", "questdump", "questdiag", "spdecode", "qtransport",
                "qfind", "rpcauto", "rpcautostop"}

    async def handle(self, message, cmd, args):
        if cmd == "rpcauto":
            try: await message.delete()
            except Exception: pass
            ref = " ".join(args[1:]).strip() if len(args) > 1 else ""
            svc = QuestService(S.TOKEN)
            try:
                quests = await svc.fetch()
            except APIError as e:
                return await message.channel.send(
                    S.ui_err(f"auth rejected: {e.status}"), delete_after=10)
            if not quests:
                return await message.channel.send(S.ui_err("no quests"), delete_after=6)
            if not ref:
                return await message.channel.send(
                    S.ui_err("usage: rpcauto <index_or_keyword>"), delete_after=8)
            if ref.isdigit():
                idx = int(ref)
            else:
                kw = ref.lower()
                matches = [i for i, q in enumerate(quests) if kw in q.name.lower()]
                if not matches:
                    return await message.channel.send(
                        S.ui_err(f"no quest matches `{ref}`"), delete_after=8)
                idx = matches[0]
            if idx >= len(quests):
                return await message.channel.send(
                    S.ui_err("index out of range"), delete_after=6)
            q = quests[idx]
            if not q.app_id:
                return await message.channel.send(
                    S.ui_err(f"{q.name}: no app_id resolvable"), delete_after=8)
            ok = await _push_rpc_override(q.app_id, name=q.name)
            if ok:
                return await message.channel.send(S.ui_ok(
                    f"rpc override pushed for {q.name} (app_id={q.app_id})"),
                    delete_after=10)
            return await message.channel.send(
                S.ui_err("rpc override failed — see console"), delete_after=8)

        if cmd == "rpcautostop":
            try: await message.delete()
            except Exception: pass
            ok = await _clear_rpc_override()
            if ok:
                return await message.channel.send(S.ui_ok("rpc override cleared"))
            return await message.channel.send(
                S.ui_err("clear failed — see console"), delete_after=8)

        if cmd == "qtransport":
            try: await message.delete()
            except Exception: pass
            tr = await _get_transport(S.TOKEN)
            name = type(tr).__name__ if tr else "none"
            mode = "fork TLS + HeaderSpoofer" if isinstance(tr, _ForkTransport) else "aiohttp"
            ch = _current_voice_channel_id()
            rpc = _rpc_override_state
            lines = [
                f"  transport:  {name}",
                f"  mode:       {mode}",
                f"  fork http:  {'available' if HAS_FORK_HTTP else 'unavailable'}",
                f"  fingerprint: fork-generated",
                f"  user agent:  fork-generated",
                f"  voice ch:    {ch if ch else '—'}",
                f"  rpc override: {'active app_id=' + str(rpc.get('app_id')) if rpc.get('active') else 'off'}",
            ]
            return await message.channel.send(S._ansi_block(lines), delete_after=30)

        if cmd == "spdecode":
            try: await message.delete()
            except Exception: pass
            if len(args) < 2:
                return await message.channel.send(
                    S.ui_err("usage: spdecode <base64 x-super-properties>"), delete_after=8)
            raw = " ".join(args[1:]).strip()
            decoded = _decode_super_properties(raw)
            if not decoded:
                return await message.channel.send(
                    S.ui_err("decode failed — not valid base64 json"), delete_after=8)
            lines = [f"  {S.WHITE}x-super-properties (decoded){S.RESET}", ""]
            for k in sorted(decoded.keys()):
                v = decoded[k]
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, separators=(",", ":"))[:80]
                lines.append(f"  {S.DIM}{k:<28}{S.RESET} {v}")
            await message.channel.send(S._ansi_block(lines), delete_after=60)
            return

        if cmd == "qfind":
            try: await message.delete()
            except Exception: pass
            if len(args) < 2:
                return await message.channel.send(
                    S.ui_err("usage: qfind <keyword>"), delete_after=8)
            kw = " ".join(args[1:]).lower()
            svc = QuestService(S.TOKEN)
            try:
                quests = await svc.fetch()
            except APIError as e:
                return await message.channel.send(
                    S.ui_err(f"auth rejected: {e.status}"), delete_after=10)
            hits = [(i, q) for i, q in enumerate(quests) if kw in q.name.lower()]
            if not hits:
                return await message.channel.send(
                    S.ui_info(f"no quests matching `{kw}`"), delete_after=10)
            rows = []
            for i, q in hits:
                rows.append(
                    f"  {S.GREY}[{i:>2}]{S.RESET} {S.WHITE}{q.name[:40]}{S.RESET}  "
                    f"{S.DIM}{q.selected_task} {q.progress_value():.0f}/{q.target:.0f}{S.RESET}")
            await message.channel.send(S._ansi_block(
                [f"  {S.WHITE}matches for `{kw}`{S.RESET}", ""] + rows))
            return

        if cmd == "quest":
            try: await message.delete()
            except Exception: pass
            page = 1
            if len(args) > 1 and args[1].isdigit():
                page = max(1, int(args[1]))
            svc = QuestService(S.TOKEN)
            try:
                quests = await svc.fetch()
            except APIError as e:
                return await message.channel.send(
                    S.ui_err(f"auth rejected: {e.status}"), delete_after=10)
            if not quests:
                return await message.channel.send(S.ui_err("no quests"), delete_after=8)
            rows = []
            for i, q in enumerate(quests):
                if q.is_claimed(): tag = f"{S.GREEN}claimed{S.RESET}"
                elif q.is_completed(): tag = f"{S.GREEN}done{S.RESET}"
                elif q.selected_task in ACHIEVEMENT_TASKS: tag = f"{S.MAGENTA}ach{S.RESET}"
                elif q.selected_task in MISSION_TASKS: tag = f"{S.YELLOW}mis{S.RESET}"
                elif q.is_supported(): tag = f"{S.CYAN}ok{S.RESET}"
                else: tag = f"{S.RED}unsup{S.RESET}"
                prog = q.progress_value()
                tgt = q.target or 0
                rows.append(
                    f"  {S.GREY}[{i:>2}]{S.RESET} {S.WHITE}{q.name[:38]:<38}{S.RESET} "
                    f"{tag}  {S.DIM}{prog:.0f}/{tgt:.0f}{S.RESET}")
            per = 15
            total = max(1, (len(rows) + per - 1) // per)
            page = min(page, total)
            chunk = rows[(page-1)*per : page*per]
            header = f"  {S.WHITE}quests{S.RESET}  {S.DIM}{len(quests)} total{S.RESET}"
            nxt = page + 1 if page < total else 1
            footer = f"  {S.DIM}page {page}/{total}  •  .quest {nxt} to flip{S.RESET}"
            await message.channel.send(S._ansi_block([header, ""] + chunk + ["", footer]))
            return

        elif cmd == "questrun":
            try: await message.delete()
            except Exception: pass
            idx = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
            svc = QuestService(S.TOKEN)
            try:
                quests = await svc.fetch()
                if not quests or idx >= len(quests):
                    return await message.channel.send(
                        S.ui_err("index out of range"), delete_after=6)
                q = quests[idx]
                if q.is_completed():
                    return await message.channel.send(
                        S.ui_ok("already done"), delete_after=6)
                ch = _current_voice_channel_id()
                await message.channel.send(
                    S.ui_info(f"started {q.name} ({q.selected_task}) "
                              f"vc={ch if ch else '—'}"),
                    delete_after=6)
                res = await svc.run(q)
                if res == "completed":
                    await message.channel.send(S.ui_ok(f"{q.name} complete"), delete_after=10)
                else:
                    await message.channel.send(S.ui_err(f"{q.name} → {res}"), delete_after=10)
            except APIError as e:
                await message.channel.send(S.ui_err(f"auth rejected: {e.status}"), delete_after=10)

        elif cmd == "questall":
            try: await message.delete()
            except Exception: pass
            svc = QuestService(S.TOKEN)
            try:
                quests = await svc.fetch()
                active = [q for q in quests if not q.is_completed() and q.is_supported()]
                if not active:
                    return await message.channel.send(
                        S.ui_err("no active quests"), delete_after=6)
                ch = _current_voice_channel_id()
                await message.channel.send(
                    S.ui_info(f"{len(active)} queued  •  vc={ch if ch else '—'}"), delete_after=6)
                async def _run(q):
                    res = await svc.run(q)
                    if res == "completed":
                        try:
                            await message.channel.send(
                                S.ui_ok(f"{q.name} ✓"), delete_after=10)
                        except Exception:
                            pass
                await asyncio.gather(*[_run(q) for q in active])
            except APIError as e:
                await message.channel.send(S.ui_err(f"auth rejected: {e.status}"), delete_after=10)

        elif cmd == "autoquest":
            try: await message.delete()
            except Exception: pass
            cfg = S.load_config() or {}
            on = len(args) < 2 or args[1].lower() in ("on", "enable")
            cfg["autoquest_enabled"] = on
            S.save_config(cfg)
            if on:
                asyncio.create_task(autoquest_run(S.TOKEN))
            await message.channel.send(
                S.ui_ok(f"autoquest → {'on' if on else 'off'}"), delete_after=5)

        elif cmd == "autoclaim":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub in ("run", "now"):
                try: await message.delete()
                except Exception: pass
                await message.channel.send(
                    S.ui_info("autoclaim: sweeping completed quests..."), delete_after=6)
                svc = QuestService(S.TOKEN)
                claimed, failed = [], []
                try:
                    quests = await svc.fetch()
                    for q in quests:
                        if not q.is_completed() or q.is_claimed():
                            continue
                        ok, detail = await _claim_quest(S.TOKEN, q.id)
                        if ok: claimed.append(q.name)
                        else: failed.append((q.name, detail[:80]))
                        await asyncio.sleep(1.5)
                except APIError as e:
                    return await message.channel.send(
                        S.ui_err(f"auth rejected: {e.status}"), delete_after=10)
                if claimed:
                    await message.channel.send(S.ui_ok(
                        f"claimed {len(claimed)}: {', '.join(claimed[:5])}"
                        + ("..." if len(claimed) > 5 else "")))
                if failed:
                    await message.channel.send(S.ui_box("autoclaim failures", [
                        f"  {S.DIM}•{S.RESET} {n}  {S.DIM}{d}{S.RESET}" for n, d in failed[:5]]))
                if not claimed and not failed:
                    await message.channel.send(S.ui_info("nothing to claim"))
                return
            on = (sub in ("on", "enable")) if sub else True
            cfg = S.load_config() or {}
            cfg["autoclaim_enabled"] = on
            S.save_config(cfg)
            suffix = ""
            if on:
                started = await _ensure_autoclaim()
                suffix = "" if started else " (already running)"
            await message.edit(content=S.ui_ok(
                f"autoclaim → {'on' if on else 'off'}{suffix}"))

        elif cmd == "orbbadge":
            try: await message.delete()
            except Exception: pass
            ok, text = await claim_orb(S.TOKEN)
            await message.channel.send(
                S.ui_ok("claimed") if ok else S.ui_err(f"failed: {text[:80]}"),
                delete_after=8)

        elif cmd == "questdiag":
            try: await message.delete()
            except Exception: pass
            autoclaim_running = _autoclaim_task is not None and not _autoclaim_task.done()
            raw_keys = "?"; raw_count = "?"; raw_status = "?"; raw_first = "?"
            raw_d = {}
            try:
                tr = await _get_transport(S.TOKEN)
                if tr is None:
                    raw_status = "no transport"
                else:
                    status, body = await tr.request(
                        "GET", "https://discord.com/api/v9/quests/@me")
                    raw_status = status
                    raw_d = body if isinstance(body, dict) else {}
                    if isinstance(raw_d, dict):
                        raw_keys = ",".join(list(raw_d.keys())[:5]) or "(empty)"
                        qlist = raw_d.get("quests")
                        if isinstance(qlist, list):
                            raw_count = len(qlist)
                            if qlist:
                                q0 = qlist[0] or {}
                                raw_first = (f"id={str(q0.get('id','?'))[:12]}… "
                                             f"cfg={bool(q0.get('config'))} "
                                             f"us={bool(q0.get('user_status'))}")
                        else:
                            raw_count = f"not-list:{type(qlist).__name__}"
            except Exception as e:
                raw_status = f"exception: {type(e).__name__}: {e}"
            suspended = raw_d.get("quest_access_suspended_until") if isinstance(raw_d, dict) else None
            blocked = raw_d.get("quest_enrollment_blocked_until") if isinstance(raw_d, dict) else None
            excluded = raw_d.get("excluded_quests", []) if isinstance(raw_d, dict) else []
            tr = await _get_transport(S.TOKEN)
            transport_name = type(tr).__name__ if tr else "none"
            ch = _current_voice_channel_id()
            rpc = _rpc_override_state
            lines = [
                f"  transport:      {transport_name}",
                f"  fork http:      {'yes' if HAS_FORK_HTTP else 'no'}",
                f"  build number:   {CLIENT_BUILD_NUMBER}",
                f"  fingerprint:    fork-generated",
                f"  user agent:     fork-generated",
                f"  voice ch:       {ch if ch else '—'}",
                f"  rpc override:   {'active app_id=' + str(rpc.get('app_id')) if rpc.get('active') else 'off'}",
                f"  token uid:      {QuestService(S.TOKEN).uid}",
                f"  autoclaim:      {'running' if autoclaim_running else 'idle'}",
                f"  hcaptcha:       {'loaded' if HAS_HCAPTCHA else 'unavailable'}",
                f"  /quests/@me:    HTTP {raw_status}",
                f"  response keys:  {raw_keys}",
                f"  quest count:    {raw_count}",
                f"  first quest:    {raw_first}",
                f"  suspended til:  {suspended if suspended else '—'}",
                f"  blocked til:    {blocked if blocked else '—'}",
                f"  excluded count: {len(excluded) if isinstance(excluded, list) else 0}",
            ]
            await message.channel.send(S._ansi_block(lines))

        elif cmd == "questdump":
            try: await message.delete()
            except Exception: pass
            ref = " ".join(args[1:]).strip() if len(args) > 1 else ""
            svc = QuestService(S.TOKEN)
            try:
                quests = await svc.fetch()
            except APIError as e:
                return await message.channel.send(
                    S.ui_err(f"auth rejected: {e.status}"), delete_after=10)
            if not quests:
                return await message.channel.send(S.ui_err("no quests"), delete_after=6)
            if not ref:
                idx = 0
            elif ref.isdigit():
                idx = int(ref)
            else:
                kw = ref.lower()
                matches = [i for i, q in enumerate(quests) if kw in q.name.lower()]
                if not matches:
                    return await message.channel.send(
                        S.ui_err(f"no quest matches `{ref}`"), delete_after=8)
                idx = matches[0]
            if idx >= len(quests):
                return await message.channel.send(
                    S.ui_err("index out of range"), delete_after=6)
            q = quests[idx]
            def _dump(obj, label, max_len=1900):
                try: text = json.dumps(obj, indent=2)
                except Exception: text = str(obj)
                if len(text) > max_len:
                    text = text[:max_len - 20] + "\n... (truncated)"
                return f"**{label}**\n```json\n{text}\n```"
            await message.channel.send(S.ui_box("quest info", [
                f"  index:         {idx}",
                f"  name:          {q.name}",
                f"  id:            {q.id}",
                f"  app_id:        {q.app_id}",
                f"  selected_task: {q.selected_task}",
                f"  target:        {q.target}",
                f"  progress:      {q.progress_value()} / {q.target}",
                f"  completed:     {q.is_completed()}",
                f"  claimed:       {q.is_claimed()}",
                f"  vc:            {_current_voice_channel_id() or '—'}",
                f"  task types:    {', '.join(list(q.tasks.keys())) if q.tasks else '—'}",
            ]))
            ach_block = q.tasks.get(q.selected_task) or {}
            if ach_block:
                await message.channel.send(_dump(ach_block, f"task_config: {q.selected_task}"))
            if q.tasks:
                await message.channel.send(_dump(q.tasks, "all task configs"))
            if q.user_status:
                await message.channel.send(_dump(q.user_status, "user_status"))
            else:
                await message.channel.send(
                    S.ui_info("no user_status — quest not enrolled yet"))
