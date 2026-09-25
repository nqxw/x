# cogs/quests.py | quest completer + orb badge + autoclaim
#
# v3.1 — extended questdiag: probes /quests/@me live and prints the raw
# response shape so empty-fetch cases are diagnosable without a restart.
import asyncio
import base64
import json
import os
import re
import time
import traceback
import aiohttp
import modifyself_shim as discord
from datetime import datetime, timezone
from uuid import uuid4

from . import state as S

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
    "COLLECT_ITEM", "COLLECT",
    "MISSION_COMPLETE", "COMPLETE_QUEST",
    "COMPLETE_ACTIVITY", "EXTERNAL_TASK",
    "LAUNCH_GAME", "LAUNCH_QUEST",
    "ACHIEVEMENT_IN_ACTIVITY", "ACHIEVEMENT",
    "COMPLETE_ACHIEVEMENT", "EARN_ACHIEVEMENT",
)
VIDEO_TASKS = ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE")
HEARTBEAT_TASKS = ("PLAY_ON_DESKTOP", "PLAY_ON_DESKTOP_V2", "PLAY_ACTIVITY", "STREAM_ON_DESKTOP")
ACHIEVEMENT_TASKS = ("ACHIEVEMENT_IN_ACTIVITY", "ACHIEVEMENT",
                     "COMPLETE_ACHIEVEMENT", "EARN_ACHIEVEMENT")
MISSION_TASKS = ("COLLECT_ITEM", "COLLECT", "MISSION_COMPLETE", "COMPLETE_QUEST",
                 "COMPLETE_ACTIVITY", "EXTERNAL_TASK", "LAUNCH_GAME", "LAUNCH_QUEST")

DISCORD_HCAPTCHA_SITEKEY = "4c672d35-0701-42b2-88c3-78380b0db560"
ORB_SKU = "1342211853484429445"

CLIENT_BUILD_NUMBER = 358560

QUEST_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

_hcaptcha_agent = None
_agent_init_lock = asyncio.Lock()
_solve_lock = asyncio.Lock()

_autoclaim_task = None


# ─────────────────────────────────────────────────────────────
# SHARED SESSION
# ─────────────────────────────────────────────────────────────

def _get_shared_session():
    try:
        from __main__ import _get_session as _main_get
        return _main_get(), False
    except Exception:
        pass
    try:
        s = getattr(S, "_get_session", None)
        if callable(s):
            return s(), False
    except Exception:
        pass
    return aiohttp.ClientSession(), True


# ─────────────────────────────────────────────────────────────
# HEADERS
# ─────────────────────────────────────────────────────────────

def _quest_headers(token):
    token = token.strip().strip('"').strip("'")
    browser_version = "131.0.0.0"
    try:
        m = re.search(r"Chrome/(\d+\.\d+\.\d+\.\d+)", QUEST_UA)
        if m:
            browser_version = m.group(1)
    except Exception:
        pass
    sp = base64.b64encode(json.dumps({
        "os": "Windows",
        "browser": "Chrome",
        "device": "",
        "system_locale": "en-US",
        "has_client_mods": False,
        "client_version": "1.0.0",
        "browser_user_agent": QUEST_UA,
        "browser_version": browser_version,
        "os_version": "10",
        "referrer": "",
        "referring_domain": "",
        "referrer_current": "",
        "referring_domain_current": "",
        "release_channel": "stable",
        "client_build_number": CLIENT_BUILD_NUMBER,
        "client_event_source": None,
        "client_launch_id": str(uuid4()),
    }, separators=(",", ":")).encode()).decode()
    return {
        "authorization": token,
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "user-agent": QUEST_UA,
        "x-super-properties": sp,
        "x-discord-locale": "en-US",
        "x-discord-timezone": "America/New_York",
        "x-debug-options": "bugReporterEnabled",
        "origin": "https://discord.com",
        "referer": "https://discord.com/quest-home",
    }


# ─────────────────────────────────────────────────────────────
# VOICE CHANNEL RESOLUTION
# ─────────────────────────────────────────────────────────────

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


class APIError(Exception):
    def __init__(self, status, body=None):
        super().__init__(f"Discord API {status}")
        self.status = status
        self.body = body or {}


async def _api(session, method, url, headers=None, json_body=None, retries=3):
    last = None
    for attempt in range(retries):
        try:
            async with session.request(method, url, headers=headers, json=json_body) as r:
                text = await r.text()
                body = {}
                try:
                    body = json.loads(text)
                except Exception:
                    pass
                if r.status >= 400:
                    raise APIError(r.status, body)
                return body
        except APIError as e:
            last = e
            if e.status == 429:
                ra = float(e.body.get("retry_after", 1.0))
                await asyncio.sleep(ra)
                continue
            if e.status >= 500:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            raise
    if last is not None:
        raise last
    return {}


# ─────────────────────────────────────────────────────────────
# HCAPTCHA
# ─────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────
# QUEST RECORD
# ─────────────────────────────────────────────────────────────

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
        return self.cfg.get("application", {}).get("id")

    @property
    def expires_at(self):
        return self.cfg.get("expires_at") or ""

    def is_completed(self):
        return bool(self.user_status.get("completed_at"))

    def is_claimed(self):
        return bool(self.user_status.get("claimed_at"))

    def is_enrolled(self):
        return bool(self.user_status.get("enrolled_at"))

    def is_supported(self):
        return (self.selected_task in SUPPORTED_TASKS
                or self.selected_task in MISSION_TASKS)

    def progress_value(self):
        p = self.user_status.get("progress", {}).get(self.selected_task, {})
        return float(p.get("value", 0) or 0) if p else 0.0

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


# ─────────────────────────────────────────────────────────────
# QUEST SERVICE
# ─────────────────────────────────────────────────────────────

class QuestService:
    def __init__(self, token, speed="fastest"):
        self.token = token.strip().strip('"').strip("'")
        try:
            self.uid = S._b64uid(token)
        except Exception:
            self.uid = None
        self.headers = _quest_headers(self.token)

    async def fetch(self, session):
        try:
            d = await _api(session, "GET",
                           "https://discord.com/api/v9/quests/@me",
                           headers=self.headers)
        except APIError as e:
            if e.status in (401, 403):
                print(f"[quests] auth rejected: {e.status} {e.body}")
                raise
            return []
        except Exception as e:
            print(f"[quests] fetch error: {e}")
            return []
        out = []
        for r in d.get("quests", []):
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

    async def re_fetch_one(self, session, quest_id):
        try:
            return await _api(session, "GET",
                              f"https://discord.com/api/v9/quests/{quest_id}",
                              headers=self.headers)
        except Exception:
            return None

    async def enroll(self, session, quest):
        d = await _api(session, "POST",
                       f"https://discord.com/api/v9/quests/{quest.id}/enroll",
                       headers=self.headers,
                       json_body={"location": 11, "is_targeted": False})
        if d:
            quest.data["user_status"] = d

    async def run(self, session, quest):
        if not quest.is_enrolled():
            try:
                await self.enroll(session, quest)
            except APIError as e:
                print(f"[quests] enroll failed for {quest.name}: {e.status} {e.body}")
                return "enroll_failed"

        if not quest.is_supported():
            return "unsupported"

        task = quest.selected_task
        try:
            if task in ACHIEVEMENT_TASKS:
                return await self._achievements(session, quest)
            if task in VIDEO_TASKS:
                return await self._video(session, quest)
            if task in HEARTBEAT_TASKS:
                return await self._heartbeat_task(session, quest, task)
            if task in MISSION_TASKS:
                return await self._mission(session, quest)
            return await self._heartbeat_task(session, quest, "PLAY_ON_DESKTOP")
        except asyncio.CancelledError:
            raise
        except APIError as e:
            print(f"[quests] {quest.name} api error: {e.status} {e.body}")
            return "api_error"
        except Exception as e:
            print(f"[quests] {quest.name} unexpected: {type(e).__name__}: {e}")
            traceback.print_exc()
            return "error"

    async def _video(self, session, quest):
        target = quest.target or 1.0
        last_sent = 0.0
        try:
            ustatus = quest.user_status
            prog = ustatus.get("progress", {}).get(quest.selected_task, {})
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
                d = await _api(session, "POST",
                               f"https://discord.com/api/v9/quests/{quest.id}/video-progress",
                               headers=self.headers,
                               json_body={"timestamp": float(ts)},
                               retries=2)
            except APIError as e:
                if e.status == 429:
                    await asyncio.sleep(float(e.body.get("retry_after", 2.0)))
                    stall += 1
                    if stall >= 20:
                        print(f"[video] {quest.name} rate-limit stalled — bailing")
                        break
                    continue
                if e.status == 400:
                    await asyncio.sleep(1.5)
                    stall += 1
                    if stall >= 20:
                        print(f"[video] {quest.name} 400 stalled — bailing")
                        break
                    continue
                print(f"[video] {quest.name} {e.status}: {e.body}")
                break
            except Exception as e:
                print(f"[video] {quest.name} {e}")
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
                print(f"[video] {quest.name} stalling — bailing")
                break
            await asyncio.sleep(2.0 if tick_count < 3 else 5.0)

        fresh = await self.re_fetch_one(session, quest.id)
        if fresh:
            quest.data = fresh
        return "completed" if quest.is_completed() else "recovering"

    def _build_heartbeat_payloads(self, quest, task):
        payloads = []
        app_id = quest.app_id

        if task == "STREAM_ON_DESKTOP":
            ch = _current_voice_channel_id()
            if ch:
                payloads.append({"stream_key": f"call:{ch}:1", "terminal": False})

        if task in ("PLAY_ON_DESKTOP", "PLAY_ON_DESKTOP_V2", "PLAY_ACTIVITY"):
            if app_id:
                payloads.append({
                    "application_id": str(app_id),
                    "terminal": False,
                    "stream_key": "call:0:0",
                })

        if app_id:
            payloads.append({"application_id": str(app_id), "terminal": False})

        ch = _current_voice_channel_id()
        if ch and task != "STREAM_ON_DESKTOP":
            payloads.append({"stream_key": f"call:{ch}:1", "terminal": False})

        payloads.append({"stream_key": f"call:{quest.id}:1", "terminal": False})
        return payloads

    async def _heartbeat_task(self, session, quest, task):
        payloads = self._build_heartbeat_payloads(quest, task)
        interval = 15
        active = payloads[0]
        last_seen = quest.progress_value()
        stall = 0
        max_runtime = min(600, max(120, (quest.target or 60) * 2))
        start = time.time()

        while time.time() - start < max_runtime:
            d = None
            rate_limited = False
            for p in payloads:
                try:
                    d = await _api(session, "POST",
                                   f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                                   headers=self.headers,
                                   json_body=p,
                                   retries=1)
                    active = p
                    break
                except APIError as e:
                    if e.status == 429:
                        await asyncio.sleep(float(e.body.get("retry_after", 5.0)))
                        rate_limited = True
                        break
                    continue
                except Exception:
                    continue
            if rate_limited:
                stall += 1
                if stall >= 8:
                    print(f"[heartbeat] {quest.name} rate-limit stalled — bailing")
                    break
                await asyncio.sleep(interval)
                continue

            if d:
                quest.data["user_status"] = d
                if d.get("completed_at") or quest.is_completed():
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
                print(f"[heartbeat] {quest.name} stalled — no progress for ~2min")
                break
            await asyncio.sleep(interval)

        try:
            t = dict(active)
            t["terminal"] = True
            await _api(session, "POST",
                       f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                       headers=self.headers,
                       json_body=t,
                       retries=1)
        except Exception:
            pass

        fresh = await self.re_fetch_one(session, quest.id)
        if fresh:
            quest.data = fresh
        return "completed" if quest.is_completed() else "recovering"

    async def _achievements(self, session, quest):
        target = quest.target or 1.0
        payloads = []
        if quest.app_id:
            payloads.append({
                "application_id": str(quest.app_id),
                "terminal": False,
                "stream_key": "call:0:0",
            })
        if quest.app_id:
            payloads.append({"application_id": str(quest.app_id), "terminal": False})
        ch = _current_voice_channel_id()
        if ch:
            payloads.append({"stream_key": f"call:{ch}:1", "terminal": False})

        interval = 5
        attempts = 0
        max_attempts = 60
        active = payloads[0] if payloads else {"stream_key": "call:0:0", "terminal": False}
        last_seen = quest.progress_value()
        stall = 0

        while attempts < max_attempts and not quest.is_completed():
            attempts += 1
            d = None
            rate_limited = False
            for p in payloads:
                try:
                    d = await _api(session, "POST",
                                   f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                                   headers=self.headers,
                                   json_body=p,
                                   retries=1)
                    active = p
                    break
                except APIError as e:
                    if e.status == 429:
                        await asyncio.sleep(float(e.body.get("retry_after", 5.0)))
                        rate_limited = True
                        break
                    continue
                except Exception:
                    continue
            if rate_limited:
                stall += 1
                if stall >= 12:
                    break
                continue
            if d:
                quest.data["user_status"] = d
                if quest.is_completed() or quest.progress_value() >= target:
                    break
                cur = quest.progress_value()
                if cur > last_seen + 0.5:
                    last_seen = cur
                    stall = 0
                else:
                    stall += 1
                if stall >= 12:
                    break
            await asyncio.sleep(interval)

        fresh = await self.re_fetch_one(session, quest.id)
        if fresh:
            quest.data = fresh
        return "completed" if quest.is_completed() else "recovering"

    async def _mission(self, session, quest):
        task = quest.selected_task
        target = quest.target or 0

        if target > 0:
            try:
                d = await _api(session, "POST",
                               f"https://discord.com/api/v9/quests/{quest.id}/external-task-progress",
                               headers=self.headers,
                               json_body={"task_id": task, "progress": {"value": target}},
                               retries=1)
                if d:
                    quest.data["user_status"] = d
                if quest.is_completed():
                    return "completed"
            except APIError as e:
                if e.status == 400:
                    print(f"[mission] progress rejected (target={target}): {e.body}")
                elif e.status != 404:
                    print(f"[mission] external-task-progress {e.status}: {e.body}")

        payloads = []
        if quest.app_id:
            payloads.append({
                "application_id": str(quest.app_id),
                "terminal": False,
                "stream_key": "call:0:0",
            })
        ch = _current_voice_channel_id()
        if ch:
            payloads.append({"stream_key": f"call:{ch}:1", "terminal": False})

        if payloads:
            end_time = time.time() + 60
            last_seen = quest.progress_value()
            while time.time() < end_time:
                for p in payloads:
                    try:
                        d = await _api(session, "POST",
                                       f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                                       headers=self.headers,
                                       json_body=p,
                                       retries=1)
                        if d:
                            quest.data["user_status"] = d
                        if quest.is_completed():
                            break
                    except APIError as e:
                        if e.status == 429:
                            await asyncio.sleep(float(e.body.get("retry_after", 5.0)))
                            break
                        continue
                    except Exception:
                        continue
                if quest.is_completed():
                    break
                cur = quest.progress_value()
                if cur > last_seen + 0.5:
                    last_seen = cur
                await asyncio.sleep(5)

        fresh = await self.re_fetch_one(session, quest.id)
        if fresh:
            quest.data = fresh
        return "completed" if quest.is_completed() else "recovering"


# ─────────────────────────────────────────────────────────────
# CLAIM
# ─────────────────────────────────────────────────────────────

async def _claim_quest(token: str, quest_id: str):
    url = f"https://discord.com/api/v9/quests/{quest_id}/claim"
    h = _quest_headers(token)
    session, own = _get_shared_session()
    try:
        try:
            async with session.post(url, headers=h, json={}) as r:
                text = await r.text()
                if r.status in (200, 201, 204):
                    return True, text
                if r.status == 429:
                    return False, f"rate limited: {text[:140]}"
                if r.status != 400 or "captcha" not in text.lower():
                    return False, f"http {r.status}: {text[:140]}"
        except Exception as e:
            return False, f"request failed: {e}"
        try:
            body_json = json.loads(text)
        except Exception:
            body_json = {}
        sitekey = body_json.get("captcha_sitekey") or DISCORD_HCAPTCHA_SITEKEY
        rqdata = body_json.get("captcha_rqdata")
        captcha_token = await _solve_hcaptcha(sitekey, "https://discord.com/quest-home", rqdata)
        if not captcha_token:
            return False, "captcha solve failed"
        retry_headers = {**h, "x-captcha-key": captcha_token}
        try:
            async with session.post(url, headers=retry_headers,
                                    json={"captcha_key": captcha_token}) as r2:
                text2 = await r2.text()
                return r2.status in (200, 201, 204), text2[:200]
        except Exception as e:
            return False, f"retry failed: {e}"
    finally:
        if own:
            try:
                await session.close()
            except Exception:
                pass


async def claim_orb(token):
    h = {
        "authorization": token,
        "content-type": "application/json",
        "user-agent": QUEST_UA,
        "origin": "https://discord.com",
        "referer": "https://discord.com/shop?tab=orbs",
    }
    session, own = _get_shared_session()
    try:
        try:
            async with session.post(
                f"https://discord.com/api/v9/virtual-currency/skus/{ORB_SKU}/redeem",
                headers=h, json={},
            ) as r:
                text = await r.text()
                if r.status in (200, 201, 204):
                    return True, text
                if r.status != 400 or "captcha" not in text.lower():
                    return False, text
        except Exception as e:
            return False, str(e)
        try:
            body_json = json.loads(text)
        except Exception:
            body_json = {}
        sitekey = body_json.get("captcha_sitekey") or DISCORD_HCAPTCHA_SITEKEY
        rqdata = body_json.get("captcha_rqdata")
        captcha_token = await _solve_hcaptcha(sitekey, "https://discord.com", rqdata)
        if not captcha_token:
            return False, "captcha solve failed"
        retry_headers = {**h, "x-captcha-key": captcha_token}
        try:
            async with session.post(
                f"https://discord.com/api/v9/virtual-currency/skus/{ORB_SKU}/redeem",
                headers=retry_headers,
                json={"captcha_key": captcha_token},
            ) as r2:
                return r2.status in (200, 201, 204), await r2.text()
        except Exception as e:
            return False, str(e)
    finally:
        if own:
            try:
                await session.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────
# BACKGROUND LOOPS
# ─────────────────────────────────────────────────────────────

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
                await asyncio.sleep(300)
                continue
            svc = QuestService(S.TOKEN)
            session, own = _get_shared_session()
            try:
                try:
                    quests = await svc.fetch(session)
                except APIError:
                    quests = []
            finally:
                if own:
                    try:
                        await session.close()
                    except Exception:
                        pass
            pending = [q for q in quests if q.is_completed() and not q.is_claimed()]
            for q in pending:
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
    session, own = _get_shared_session()
    try:
        try:
            quests = await svc.fetch(session)
        except APIError as e:
            print(f"[autoquest] auth failed: {e.status}")
            return
        active = [q for q in quests if not q.is_completed() and q.is_supported()]
        for q in active:
            print(f"[AutoQuest] running {q.name} ({q.selected_task})")
            res = await svc.run(session, q)
            print(f"[AutoQuest] {q.name} → {res}")
    finally:
        if own:
            try:
                await session.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────
# COG
# ─────────────────────────────────────────────────────────────

class QuestsCog:
    COMMANDS = {"quest", "questrun", "questall", "autoquest", "autoclaim",
                "orbbadge", "questdump", "questdiag"}

    async def handle(self, message, cmd, args):
        if cmd == "quest":
            try:
                await message.delete()
            except Exception:
                pass
            svc = QuestService(S.TOKEN)
            session, own = _get_shared_session()
            try:
                try:
                    quests = await svc.fetch(session)
                except APIError as e:
                    return await message.channel.send(
                        S.ui_err(f"auth rejected: {e.status}"), delete_after=10)
                if not quests:
                    return await message.channel.send(S.ui_err("no quests"), delete_after=8)
                rows = []
                for i, q in enumerate(quests):
                    if q.is_claimed():
                        tag = f"{S.GREEN}claimed{S.RESET}"
                    elif q.is_completed():
                        tag = f"{S.GREEN}done{S.RESET}"
                    elif q.selected_task in ACHIEVEMENT_TASKS:
                        tag = f"{S.MAGENTA}achievement{S.RESET}"
                    elif q.selected_task in MISSION_TASKS:
                        tag = f"{S.YELLOW}mission{S.RESET}"
                    elif q.is_supported():
                        tag = f"{S.CYAN}ok{S.RESET}"
                    else:
                        tag = f"{S.RED}unsupported{S.RESET}"
                    rows.append(f"  {S.GREY}[{i}]{S.RESET} {S.WHITE}{q.name}{S.RESET}  {tag}")
                    rows.append(f"       {S.ui_progress(q.reward, q.progress_pct())}")
                await message.channel.send(S._paginate("quests", "active", rows))
            finally:
                if own:
                    try:
                        await session.close()
                    except Exception:
                        pass

        elif cmd == "questrun":
            try:
                await message.delete()
            except Exception:
                pass
            idx = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
            svc = QuestService(S.TOKEN)
            session, own = _get_shared_session()
            try:
                try:
                    quests = await svc.fetch(session)
                    if not quests or idx >= len(quests):
                        return await message.channel.send(
                            S.ui_err("index out of range"), delete_after=6)
                    q = quests[idx]
                    if q.is_completed():
                        return await message.channel.send(
                            S.ui_ok("already done"), delete_after=6)
                    await message.channel.send(
                        S.ui_info(f"started {q.name} ({q.selected_task})"),
                        delete_after=6)
                    res = await svc.run(session, q)
                    if res == "completed":
                        await message.channel.send(
                            S.ui_ok(f"{q.name} complete"), delete_after=10)
                    else:
                        await message.channel.send(
                            S.ui_err(f"{q.name} → {res}"), delete_after=10)
                except APIError as e:
                    await message.channel.send(
                        S.ui_err(f"auth rejected: {e.status}"), delete_after=10)
            finally:
                if own:
                    try:
                        await session.close()
                    except Exception:
                        pass

        elif cmd == "questall":
            try:
                await message.delete()
            except Exception:
                pass
            svc = QuestService(S.TOKEN)
            session, own = _get_shared_session()
            try:
                try:
                    quests = await svc.fetch(session)
                    active = [q for q in quests if not q.is_completed() and q.is_supported()]
                    if not active:
                        return await message.channel.send(
                            S.ui_err("no active quests"), delete_after=6)
                    await message.channel.send(
                        S.ui_info(f"{len(active)} queued"), delete_after=5)

                    async def _run(q):
                        res = await svc.run(session, q)
                        if res == "completed":
                            try:
                                await message.channel.send(
                                    S.ui_ok(f"{q.name} ✓"), delete_after=10)
                            except Exception:
                                pass

                    await asyncio.gather(*[_run(q) for q in active])
                except APIError as e:
                    await message.channel.send(
                        S.ui_err(f"auth rejected: {e.status}"), delete_after=10)
            finally:
                if own:
                    try:
                        await session.close()
                    except Exception:
                        pass

        elif cmd == "autoquest":
            try:
                await message.delete()
            except Exception:
                pass
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
                try:
                    await message.delete()
                except Exception:
                    pass
                await message.channel.send(
                    S.ui_info("autoclaim: sweeping completed quests..."),
                    delete_after=6)
                svc = QuestService(S.TOKEN)
                claimed, failed = [], []
                session, own = _get_shared_session()
                try:
                    try:
                        quests = await svc.fetch(session)
                        for q in quests:
                            if not q.is_completed() or q.is_claimed():
                                continue
                            ok, detail = await _claim_quest(S.TOKEN, q.id)
                            if ok:
                                claimed.append(q.name)
                            else:
                                failed.append((q.name, detail[:80]))
                            await asyncio.sleep(1.5)
                    except APIError as e:
                        return await message.channel.send(
                            S.ui_err(f"auth rejected: {e.status}"), delete_after=10)
                finally:
                    if own:
                        try:
                            await session.close()
                        except Exception:
                            pass
                if claimed:
                    await message.channel.send(S.ui_ok(
                        f"claimed {len(claimed)}: {', '.join(claimed[:5])}"
                        + ("..." if len(claimed) > 5 else "")))
                if failed:
                    lines = [f"  {S.DIM}•{S.RESET} {n}  {S.DIM}{d}{S.RESET}"
                             for n, d in failed[:5]]
                    await message.channel.send(S.ui_box("autoclaim failures", lines))
                if not claimed and not failed:
                    await message.channel.send(S.ui_info("nothing to claim"))
                return
            on = (sub in ("on", "enable")) if sub else True
            cfg = S.load_config() or {}
            cfg["autoclaim_enabled"] = on
            S.save_config(cfg)
            if on:
                started = await _ensure_autoclaim()
                suffix = "" if started else " (already running)"
            else:
                suffix = ""
            await message.edit(content=S.ui_ok(
                f"autoclaim → {'on' if on else 'off'}{suffix}"))

        elif cmd == "orbbadge":
            try:
                await message.delete()
            except Exception:
                pass
            ok, text = await claim_orb(S.TOKEN)
            await message.channel.send(
                S.ui_ok("claimed") if ok else S.ui_err(f"failed: {text[:80]}"),
                delete_after=8)

        elif cmd == "questdiag":
            try:
                await message.delete()
            except Exception:
                pass
            ch = _current_voice_channel_id()
            autoclaim_running = _autoclaim_task is not None and not _autoclaim_task.done()

            # ── live probe of /quests/@me so the raw shape is visible ──
            raw_keys = "?"
            raw_count = "?"
            raw_status = "?"
            raw_first = "?"
            session, own = _get_shared_session()
            try:
                svc = QuestService(S.TOKEN)
                try:
                    async with session.get(
                        "https://discord.com/api/v9/quests/@me",
                        headers=svc.headers,
                    ) as r:
                        raw_status = r.status
                        try:
                            d = await r.json()
                            if isinstance(d, dict):
                                raw_keys = ",".join(list(d.keys())[:5]) or "(empty)"
                                qlist = d.get("quests")
                                if isinstance(qlist, list):
                                    raw_count = len(qlist)
                                    if qlist:
                                        q0 = qlist[0] or {}
                                        qid = str(q0.get("id", "?"))[:12]
                                        cfg_ok = bool(q0.get("config"))
                                        us_ok = bool(q0.get("user_status"))
                                        exp = (q0.get("config") or {}).get("expires_at", "none")
                                        raw_first = f"id={qid}… cfg={cfg_ok} us={us_ok} exp={exp}"
                                else:
                                    raw_count = f"not-list:{type(qlist).__name__}"
                            else:
                                raw_keys = f"not-dict:{type(d).__name__}"
                        except Exception as e:
                            raw_keys = f"parse-error: {type(e).__name__}: {e}"
                except Exception as e:
                    raw_status = f"exception: {type(e).__name__}: {e}"
            finally:
                if own:
                    try:
                        await session.close()
                    except Exception:
                        pass

            lines = [
                f"  voice channel:  {ch if ch else '—'}",
                f"  build number:   {CLIENT_BUILD_NUMBER}",
                f"  token uid:      {QuestService(S.TOKEN).uid}",
                f"  ua:             {QUEST_UA[:60]}...",
                f"  autoclaim:      {'running' if autoclaim_running else 'idle'}",
                f"  hcaptcha:       {'loaded' if HAS_HCAPTCHA else 'unavailable'}",
                f"  /quests/@me:    HTTP {raw_status}",
                f"  response keys:  {raw_keys}",
                f"  quest count:    {raw_count}",
                f"  first quest:    {raw_first}",
            ]
            await message.channel.send(S._ansi_block(lines))

        elif cmd == "questdump":
            try:
                await message.delete()
            except Exception:
                pass
            idx = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
            svc = QuestService(S.TOKEN)
            session, own = _get_shared_session()
            try:
                try:
                    quests = await svc.fetch(session)
                except APIError as e:
                    return await message.channel.send(
                        S.ui_err(f"auth rejected: {e.status}"), delete_after=10)
                if not quests or idx >= len(quests):
                    return await message.channel.send(
                        S.ui_err("index out of range"), delete_after=6)
                q = quests[idx]

                def _dump(obj, label, max_len=1900):
                    try:
                        text = json.dumps(obj, indent=2)
                    except Exception:
                        text = str(obj)
                    if len(text) > max_len:
                        text = text[:max_len - 20] + "\n... (truncated)"
                    return f"**{label}**\n```json\n{text}\n```"

                task_names = list(q.tasks.keys())
                info_lines = [
                    f"  name:          {q.name}",
                    f"  id:            {q.id}",
                    f"  app_id:        {q.app_id}",
                    f"  selected_task: {q.selected_task}",
                    f"  target:        {q.target}",
                    f"  progress:      {q.progress_value()} / {q.target}",
                    f"  completed:     {q.is_completed()}",
                    f"  claimed:       {q.is_claimed()}",
                    f"  task types:    {', '.join(task_names) if task_names else '—'}",
                ]
                await message.channel.send(S.ui_box("quest info", info_lines))

                ach_block = q.tasks.get(q.selected_task) or {}
                if ach_block:
                    await message.channel.send(_dump(ach_block, f"task_config: {q.selected_task}"))
                if q.tasks:
                    await message.channel.send(_dump(q.tasks, "all task configs"))
                if q.user_status:
                    await message.channel.send(_dump(q.user_status, "user_status"))
                else:
                    await message.channel.send(S.ui_info(
                        "no user_status — quest not enrolled yet"))
            finally:
                if own:
                    try:
                        await session.close()
                    except Exception:
                        pass
