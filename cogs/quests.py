# cogs/quests.py | quest completer + orb badge + autoclaim
import asyncio
import base64
import json
import os
import time
import traceback
import aiohttp
import discord
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
)
VIDEO_TASKS = ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE")
HEARTBEAT_TASKS = ("PLAY_ON_DESKTOP", "PLAY_ON_DESKTOP_V2", "PLAY_ACTIVITY", "STREAM_ON_DESKTOP")
MISSION_TASKS = ("COLLECT_ITEM", "COLLECT", "MISSION_COMPLETE", "COMPLETE_QUEST",
                 "COMPLETE_ACTIVITY", "EXTERNAL_TASK", "LAUNCH_GAME", "LAUNCH_QUEST")

DISCORD_HCAPTCHA_SITEKEY = "4c672d35-0701-42b2-88c3-78380b0db560"
ORB_SKU = "1342211853484429445"

_hcaptcha_agent = None
_hcaptcha_lock = asyncio.Lock()


def _quest_headers(token):
    sp = base64.b64encode(json.dumps({
        "os":"Windows","browser":"Chrome","device":"","system_locale":"en",
        "has_client_mods":False,"browser_user_agent":S.USER_AGENT,"browser_version":"142.0.0.0",
        "os_version":"10","release_channel":"stable","client_launch_id":str(uuid4()),
        "client_build_number":971383,"client_event_source":None,
    }).encode()).decode()
    return {"authorization": token.strip().strip('"').strip("'"),
            "accept":"*/*","content-type":"application/json","user-agent":S.USER_AGENT,
            "x-super-properties":sp,"origin":"https://discord.com",
            "referer":"https://discord.com/quest-home"}


class APIError(Exception):
    def __init__(self, status, body=None):
        super().__init__(f"Discord API {status}")
        self.status = status; self.body = body or {}


async def _api(session, method, url, headers=None, json_body=None, retries=3):
    last = None
    for attempt in range(retries):
        try:
            async with session.request(method, url, headers=headers, json=json_body) as r:
                text = await r.text()
                body = {}
                try: body = json.loads(text)
                except Exception: pass
                if r.status >= 400: raise APIError(r.status, body)
                return body
        except APIError as e:
            last = e
            if e.status == 429:
                ra = float(e.body.get("retry_after", 1.0))
                await asyncio.sleep(ra); continue
            if e.status >= 500:
                await asyncio.sleep(0.5 * (attempt + 1)); continue
            raise
    if last is not None: raise last
    return {}


async def _get_hcaptcha_agent():
    global _hcaptcha_agent
    if _hcaptcha_agent is not None: return _hcaptcha_agent
    if not HAS_HCAPTCHA: return None
    async with _hcaptcha_lock:
        if _hcaptcha_agent is not None: return _hcaptcha_agent
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
    if not HAS_HCAPTCHA: return None
    agent = await _get_hcaptcha_agent()
    if agent is None: return None
    try:
        payload = {"sitekey": sitekey, "url": url, "rqdata": rqdata or "", "type": "hsl"}
        async with _hcaptcha_lock:
            resp = await agent.solve(payload)
        token = None
        if resp is not None:
            if hasattr(resp, "generated_pass_UUID"): token = resp.generated_pass_UUID
            elif hasattr(resp, "token"): token = resp.token
            elif isinstance(resp, dict):
                token = resp.get("generated_pass_UUID") or resp.get("token") or resp.get("response")
            elif isinstance(resp, str): token = resp
        return token
    except Exception as e:
        print(f"[hcaptcha] solve failed: {e}")
        return None


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
    def name(self): return self.msgs.get("quest_name") or self.msgs.get("game_title") or "Quest"
    @property
    def reward(self):
        rw = self.cfg.get("rewards_config", {}).get("rewards", [])
        if rw: return rw[0].get("messages", {}).get("name") or "Reward"
        return "Reward"
    @property
    def app_id(self): return self.cfg.get("application", {}).get("id")
    @property
    def expires_at(self): return self.cfg.get("expires_at") or ""
    def is_completed(self): return bool(self.user_status.get("completed_at"))
    def is_claimed(self): return bool(self.user_status.get("claimed_at"))
    def is_enrolled(self): return bool(self.user_status.get("enrolled_at"))
    def is_supported(self): return self.selected_task in SUPPORTED_TASKS or self.selected_task in MISSION_TASKS
    def progress_value(self):
        p = self.user_status.get("progress", {}).get(self.selected_task, {})
        return float(p.get("value", 0) or 0) if p else 0.0
    def progress_pct(self):
        return min(100, int(self.progress_value() / self.target * 100)) if self.target else 0
    def _pick(self):
        for t in SUPPORTED_TASKS:
            if t in self.tasks: return t
        for t in MISSION_TASKS:
            if t in self.tasks: return t
        return next(iter(self.tasks.keys()), "UNKNOWN")


class QuestService:
    def __init__(self, token, speed="fastest"):
        self.token = token.strip().strip('"').strip("'")
        self.uid = S._b64uid(token)
        self.headers = _quest_headers(self.token)

    async def fetch(self, session):
        try:
            d = await _api(session, "GET", "https://discord.com/api/v9/quests/@me", headers=self.headers)
            out = []
            for r in d.get("quests", []):
                q = QuestRecord(r)
                if q.expires_at:
                    try:
                        exp = datetime.fromisoformat(q.expires_at.replace("Z", "+00:00"))
                        if datetime.now(timezone.utc) > exp: continue
                    except Exception: pass
                out.append(q)
            return out
        except Exception: return []

    async def enroll(self, session, quest):
        d = await _api(session, "POST", f"https://discord.com/api/v9/quests/{quest.id}/enroll",
                       headers=self.headers,
                       json_body={"location":11,"is_targeted":False,"metadata_raw":None})
        if d: quest.data["user_status"] = d

    async def run(self, session, quest):
        if not quest.is_enrolled(): await self.enroll(session, quest)
        if not quest.is_supported(): return "unsupported"
        task = quest.selected_task
        if task in VIDEO_TASKS:
            return await self._video(session, quest)
        if task in HEARTBEAT_TASKS:
            payloads = [{"stream_key": f"call:{quest.id}:1", "terminal": False}]
            if quest.app_id: payloads.append({"application_id": quest.app_id, "terminal": False})
            if task == "PLAY_ACTIVITY":
                payloads.insert(0, {"stream_key": f"call:{self.uid or quest.id}:1", "terminal": False})
            return await self._heartbeat(session, quest, payloads)
        if task in MISSION_TASKS or task not in (*VIDEO_TASKS, *HEARTBEAT_TASKS):
            return await self._mission(session, quest)
        payloads = [{"stream_key": f"call:{quest.id}:1", "terminal": False}]
        if quest.app_id: payloads.append({"application_id": quest.app_id, "terminal": False})
        return await self._heartbeat(session, quest, payloads)

    async def _video(self, session, quest):
        interval = 2.0
        last = quest.progress_value()
        started = int(datetime.now(timezone.utc).timestamp()) - int(last)
        while last < quest.target:
            ts = min(float(quest.target), float(int(datetime.now(timezone.utc).timestamp()) - started))
            try:
                d = await _api(session, "POST", f"https://discord.com/api/v9/quests/{quest.id}/video-progress",
                               headers=self.headers, json_body={"timestamp": ts}, retries=2)
                if d: quest.data["user_status"] = d
                last = max(last, ts, quest.progress_value())
                if d and d.get("completed_at"): break
            except APIError as e:
                if e.status == 400: await asyncio.sleep(1); continue
                break
            await asyncio.sleep(interval)
        return "completed" if quest.is_completed() or quest.progress_value() >= quest.target else "recovering"

    async def _mission(self, session, quest):
        task = quest.selected_task
        try:
            target = quest.target or 1.0
            for ts in [target * 0.25, target * 0.5, target * 0.75, target]:
                try:
                    d = await _api(session, "POST",
                        f"https://discord.com/api/v9/quests/{quest.id}/video-progress",
                        headers=self.headers, json_body={"timestamp": float(ts)}, retries=2)
                    if d: quest.data["user_status"] = d
                    if quest.is_completed(): return "completed"
                    await asyncio.sleep(1.5)
                except APIError as e:
                    if e.status not in (400, 404): raise
                    break
        except Exception: pass
        if quest.is_completed(): return "completed"
        payloads = []
        if quest.app_id:
            payloads.append({"application_id": quest.app_id, "terminal": False})
        payloads.append({"stream_key": f"call:{quest.id}:1", "terminal": False})
        if self.uid:
            payloads.append({"stream_key": f"call:{self.uid}:1", "terminal": False})
        end_time = datetime.now(timezone.utc).timestamp() + 60
        while datetime.now(timezone.utc).timestamp() < end_time:
            for p in payloads:
                try:
                    d = await _api(session, "POST",
                        f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                        headers=self.headers, json_body=p, retries=1)
                    if d: quest.data["user_status"] = d
                    if quest.is_completed(): break
                except APIError: continue
            if quest.is_completed(): break
            await asyncio.sleep(5)
        if quest.is_completed(): return "completed"
        try:
            d = await _api(session, "POST",
                f"https://discord.com/api/v9/quests/{quest.id}/external-task-progress",
                headers=self.headers,
                json_body={"task_id": task, "progress": {"value": quest.target or 1}},
                retries=2)
            if d: quest.data["user_status"] = d
        except Exception: pass
        if quest.is_completed(): return "completed"
        for p in payloads:
            try:
                terminal = dict(p); terminal["terminal"] = True
                await _api(session, "POST",
                    f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                    headers=self.headers, json_body=terminal, retries=1)
            except Exception: pass
        return "completed" if quest.is_completed() else "recovering"

    async def _heartbeat(self, session, quest, payloads):
        interval = 15
        active = payloads[0]
        while True:
            d = None
            for p in payloads:
                try:
                    d = await _api(session, "POST", f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                                   headers=self.headers, json_body=p, retries=2)
                    active = p; break
                except APIError: continue
            if d: quest.data["user_status"] = d
            if quest.is_completed() or quest.progress_value() >= quest.target: break
            await asyncio.sleep(interval)
        try:
            t = dict(active); t["terminal"] = True
            await _api(session, "POST", f"https://discord.com/api/v9/quests/{quest.id}/heartbeat",
                       headers=self.headers, json_body=t, retries=1)
        except Exception: pass
        return "completed" if quest.is_completed() else "recovering"


async def _claim_quest(token: str, quest_id: str):
    url = f"https://discord.com/api/v9/quests/{quest_id}/claim"
    h = _quest_headers(token)
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, headers=h, json={}) as r:
                text = await r.text()
                if r.status in (200, 201, 204): return True, text
                if r.status == 429: return False, f"rate limited: {text[:140]}"
                if r.status != 400 or "captcha" not in text.lower():
                    return False, f"http {r.status}: {text[:140]}"
        except Exception as e:
            return False, f"request failed: {e}"
        try: body_json = json.loads(text)
        except Exception: body_json = {}
        sitekey = body_json.get("captcha_sitekey") or DISCORD_HCAPTCHA_SITEKEY
        rqdata = body_json.get("captcha_rqdata")
        captcha_token = await _solve_hcaptcha(sitekey, "https://discord.com/quest-home", rqdata)
        if not captcha_token: return False, "captcha solve failed"
        retry_headers = {**h, "x-captcha-key": captcha_token}
        try:
            async with session.post(url, headers=retry_headers,
                                    json={"captcha_key": captcha_token}) as r2:
                text2 = await r2.text()
                return r2.status in (200, 201, 204), text2[:200]
        except Exception as e:
            return False, f"retry failed: {e}"


async def claim_orb(token):
    h = {"authorization": token, "content-type": "application/json", "user-agent": S.USER_AGENT,
         "origin": "https://discord.com", "referer": "https://discord.com/shop?tab=orbs"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(f"https://discord.com/api/v9/virtual-currency/skus/{ORB_SKU}/redeem",
                                    headers=h, json={}) as r:
                text = await r.text()
                if r.status in (200, 201, 204): return True, text
                if r.status != 400 or "captcha" not in text.lower(): return False, text
        except Exception as e:
            return False, str(e)
        try: body_json = json.loads(text)
        except Exception: body_json = {}
        sitekey = body_json.get("captcha_sitekey") or DISCORD_HCAPTCHA_SITEKEY
        rqdata = body_json.get("captcha_rqdata")
        captcha_token = await _solve_hcaptcha(sitekey, "https://discord.com", rqdata)
        if not captcha_token: return False, "captcha solve failed"
        retry_headers = {**h, "x-captcha-key": captcha_token}
        try:
            async with session.post(f"https://discord.com/api/v9/virtual-currency/skus/{ORB_SKU}/redeem",
                                    headers=retry_headers,
                                    json={"captcha_key": captcha_token}) as r2:
                return r2.status in (200, 201, 204), await r2.text()
        except Exception as e:
            return False, str(e)


async def autoclaim_loop():
    await asyncio.sleep(60)
    while True:
        try:
            cfg = S.load_config() or {}
            if not cfg.get("autoclaim_enabled"):
                await asyncio.sleep(300); continue
            svc = QuestService(S.TOKEN)
            async with aiohttp.ClientSession() as session:
                quests = await svc.fetch(session)
            pending = [q for q in quests if q.is_completed() and not q.is_claimed()]
            for q in pending:
                print(f"[autoclaim] claiming {q.name}")
                await _claim_quest(S.TOKEN, q.id)
                await asyncio.sleep(3)
            await asyncio.sleep(300)
        except asyncio.CancelledError: raise
        except Exception as e:
            print(f"[autoclaim loop] {e}")
            await asyncio.sleep(120)


async def autoquest_run(token):
    svc = QuestService(token)
    async with aiohttp.ClientSession() as session:
        quests = await svc.fetch(session)
        active = [q for q in quests if not q.is_completed() and q.is_supported()]
        for q in active:
            print(f"[AutoQuest] {q.name}")
            await svc.run(session, q)


class QuestsCog:
    COMMANDS = {"quest", "questrun", "questall", "autoquest", "autoclaim", "orbbadge"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT
        if cmd == "quest":
            try: await message.delete()
            except Exception: pass
            svc = QuestService(S.TOKEN)
            async with aiohttp.ClientSession() as session: quests = await svc.fetch(session)
            if not quests: return await message.channel.send(S.ui_err("no quests"), delete_after=8)
            rows = []
            for i, q in enumerate(quests):
                if q.is_claimed(): tag = f"{S.GREEN}claimed{S.RESET}"
                elif q.is_completed(): tag = f"{S.GREEN}done{S.RESET}"
                elif q.selected_task in MISSION_TASKS: tag = f"{S.YELLOW}mission{S.RESET}"
                elif q.is_supported(): tag = f"{S.CYAN}ok{S.RESET}"
                else: tag = f"{S.RED}unsupported{S.RESET}"
                rows.append(f"  {S.GREY}[{i}]{S.RESET} {S.WHITE}{q.name}{S.RESET}  {tag}")
                rows.append(f"       {S.ui_progress(q.reward, q.progress_pct())}")
            await message.channel.send(S._paginate("quests", "active", rows))

        elif cmd == "questrun":
            try: await message.delete()
            except Exception: pass
            idx = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
            svc = QuestService(S.TOKEN)
            async with aiohttp.ClientSession() as session:
                quests = await svc.fetch(session)
                if not quests or idx >= len(quests):
                    return await message.channel.send(S.ui_err("index out of range"), delete_after=6)
                q = quests[idx]
                if q.is_completed():
                    return await message.channel.send(S.ui_ok("already done"), delete_after=6)
                await message.channel.send(S.ui_info(f"started {q.name}"), delete_after=5)
                res = await svc.run(session, q)
                if res == "completed":
                    await message.channel.send(S.ui_ok(f"{q.name} complete"), delete_after=10)

        elif cmd == "questall":
            try: await message.delete()
            except Exception: pass
            svc = QuestService(S.TOKEN)
            async with aiohttp.ClientSession() as session:
                quests = await svc.fetch(session)
                active = [q for q in quests if not q.is_completed() and q.is_supported()]
                if not active:
                    return await message.channel.send(S.ui_err("no active quests"), delete_after=6)
                await message.channel.send(S.ui_info(f"{len(active)} queued"), delete_after=5)
                async def _run(q):
                    res = await svc.run(session, q)
                    if res == "completed":
                        await message.channel.send(S.ui_ok(f"{q.name} ✓"), delete_after=10)
                await asyncio.gather(*[_run(q) for q in active])

        elif cmd == "autoquest":
            try: await message.delete()
            except Exception: pass
            cfg = S.load_config() or {}
            on = len(args) < 2 or args[1].lower() in ("on","enable")
            cfg["autoquest_enabled"] = on
            S.save_config(cfg)
            if on: asyncio.create_task(autoquest_run(S.TOKEN))
            await message.channel.send(S.ui_ok(f"autoquest → {'on' if on else 'off'}"), delete_after=5)

        elif cmd == "autoclaim":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub in ("run", "now"):
                try: await message.delete()
                except Exception: pass
                await message.channel.send(S.ui_info("autoclaim: sweeping completed quests..."), delete_after=6)
                svc = QuestService(S.TOKEN)
                claimed, failed = [], []
                async with aiohttp.ClientSession() as session:
                    quests = await svc.fetch(session)
                    for q in quests:
                        if not q.is_completed() or q.is_claimed(): continue
                        ok, detail = await _claim_quest(S.TOKEN, q.id)
                        if ok: claimed.append(q.name)
                        else: failed.append((q.name, detail[:80]))
                        await asyncio.sleep(1.5)
                if claimed:
                    await message.channel.send(S.ui_ok(
                        f"claimed {len(claimed)}: {', '.join(claimed[:5])}" +
                        ("..." if len(claimed) > 5 else "")))
                if failed:
                    lines = [f"  {S.DIM}•{S.RESET} {n}  {S.DIM}{d}{S.RESET}" for n, d in failed[:5]]
                    await message.channel.send(S.ui_box("autoclaim failures", lines))
                if not claimed and not failed:
                    await message.channel.send(S.ui_info("nothing to claim"))
                return
            on = (sub in ("on", "enable")) if sub else True
            cfg = S.load_config() or {}
            cfg["autoclaim_enabled"] = on
            S.save_config(cfg)
            if on:
                asyncio.create_task(autoclaim_loop())
            await message.edit(content=S.ui_ok(f"autoclaim → {'on' if on else 'off'}"))

        elif cmd == "orbbadge":
            try: await message.delete()
            except Exception: pass
            ok, text = await claim_orb(S.TOKEN)
            await message.channel.send(S.ui_ok("claimed") if ok else S.ui_err(f"failed: {text[:80]}"), delete_after=8)