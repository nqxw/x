# cogs/meta.py | config export/import/reset, debug, dev mode, status watch, webhook/github notifications, uptime
import os
import json
import time
import asyncio
from datetime import datetime
import aiohttp
import modifyself_shim as discord
from . import state as S


async def _status_watch_loop():
    while True:
        try:
            m = S.meta
            if not m["status_watch"]["enabled"]:
                await asyncio.sleep(60); continue
            if time.time() - m["status_watch"]["last"] < m["status_watch"]["interval"]:
                await asyncio.sleep(30); continue
            m["status_watch"]["last"] = time.time()
            gw = getattr(S.CLIENT, "_gateway", None) if S.CLIENT else None
            ok = bool(getattr(gw, "is_connected", False)) if gw else False
            if not ok and m.get("webhook"):
                try:
                    async with aiohttp.ClientSession() as s:
                        await s.post(m["webhook"], json={
                            "content": f"⚠ gateway down at {datetime.now().strftime('%H:%M:%S')}"
                        })
                except Exception:
                    pass
            await asyncio.sleep(60)
        except Exception as e:
            print(f"[status watch] {e}")
            await asyncio.sleep(60)


async def _github_watch_loop():
    while True:
        try:
            m = S.meta
            if not m.get("github_repo") or not m.get("gh_notify_ch"):
                await asyncio.sleep(120); continue
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(f"https://api.github.com/repos/{m['github_repo']}/events",
                                     headers={"User-Agent": "wilt-bot"}) as r:
                        if r.status != 200:
                            await asyncio.sleep(300); continue
                        events = await r.json()
            except Exception:
                await asyncio.sleep(300); continue
            last = m.get("github_last")
            new = []
            for ev in events[:10]:
                ev_id = ev.get("id")
                if ev_id == last:
                    break
                new.append(ev)
            if new and last:
                ch = S.CLIENT.get_channel(int(m["gh_notify_ch"])) if S.CLIENT else None
                if ch:
                    for ev in reversed(new):
                        try:
                            await ch.send(S.ui_box("github", [
                                f"  {S.DIM}type{S.RESET}  {ev.get('type')}",
                                f"  {S.DIM}by{S.RESET}    {ev.get('actor', {}).get('login', '?')}",
                            ]))
                        except Exception:
                            pass
            if events:
                m["github_last"] = events[0].get("id")
            await asyncio.sleep(300)
        except Exception as e:
            print(f"[gh watch] {e}")
            await asyncio.sleep(300)


class MetaCog:
    COMMANDS = {"config", "resetconfig", "debug", "devmode",
                "statuswatch", "setwebhook", "githubwatch", "uptime_mon",
                "apistatus", "errorlog", "errorclear"}

    def register(self, client):
        asyncio.create_task(_status_watch_loop())
        asyncio.create_task(_github_watch_loop())

    async def handle(self, message, cmd, args):
        m = S.meta

        if cmd == "config":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "export":
                payload = {
                    "prefix": S.PREFIX,
                    "aliases": S._aliases,
                    "cooldowns": S._cooldowns,
                    "loggers": S.loggers,
                    "filters": {k: (v if not isinstance(v, dict) or "set" not in str(type(v)) else list(v))
                                 for k, v in S.filters.items() if not hasattr(v, "copy")},
                    "afk": {k: (list(v) if isinstance(v, set) else v)
                            for k, v in S.afk.items() if k != "ping_counter"},
                    "nickname": {k: v for k, v in S.nickname.items() if k != "history"},
                }
                path = f"exports/wilt_config_{int(time.time())}.json"
                with open(path, "w") as f:
                    json.dump(payload, f, indent=2, default=str)
                return await message.edit(content=S.ui_ok(f"exported → {path}"))
            if sub == "import" and len(args) >= 3 and os.path.exists(args[2]):
                with open(args[2]) as f:
                    data = json.load(f)
                for k, v in data.items():
                    if k == "prefix": S.PREFIX = v
                    if k == "aliases": S._aliases.update(v)
                    if k == "cooldowns": S._cooldowns.update(v)
                return await message.edit(content=S.ui_ok("imported"))
            if sub == "backup":
                path = f"backups/wilt_config_{int(time.time())}.json"
                with open(path, "w") as f:
                    json.dump({"prefix": S.PREFIX, "aliases": S._aliases}, f, indent=2)
                return await message.edit(content=S.ui_ok(f"backup → {path}"))
            return await message.edit(content=S.ui_info(
                "usage: config export/import/backup"))

        if cmd == "resetconfig":
            if len(args) < 2 or args[1].lower() != "confirm":
                return await message.edit(content=S.ui_warn(
                    "confirm with: resetconfig confirm"))
            S._aliases.clear()
            S._cooldowns.clear()
            S.ar_rules.clear()
            S.afk["whitelist"].clear()
            S.afk["blacklist"].clear()
            S.afk["custom_replies"].clear()
            S.filters["spam"]["history"].clear()
            S.filters["duplicate"]["history"].clear()
            return await message.edit(content=S.ui_ok("config reset"))

        if cmd == "debug":
            m["debug"] = (args[1].lower() in ("on", "enable")) if len(args) > 1 else not m["debug"]
            await message.edit(content=S.ui_ok(f"debug → {m['debug']}"))

        elif cmd == "devmode":
            m["dev_mode"] = (args[1].lower() in ("on", "enable")) if len(args) > 1 else not m["dev_mode"]
            await message.edit(content=S.ui_ok(f"dev_mode → {m['dev_mode']}"))

        elif cmd == "statuswatch":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "on":
                m["status_watch"]["enabled"] = True
            elif sub == "off":
                m["status_watch"]["enabled"] = False
            elif sub == "interval" and len(args) >= 3 and args[2].isdigit():
                m["status_watch"]["interval"] = int(args[2])
            return await message.edit(content=S.ui_ok(
                f"status watch = {m['status_watch']['enabled']} interval {m['status_watch']['interval']}s"))

        elif cmd == "setwebhook":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: setwebhook <url>"))
            m["webhook"] = args[1]
            await message.edit(content=S.ui_ok("webhook set"))

        elif cmd == "githubwatch":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "repo" and len(args) >= 3:
                m["github_repo"] = args[2]
            elif sub == "channel" and len(args) >= 3:
                m["gh_notify_ch"] = args[2]
            elif sub == "clear":
                m["github_repo"] = None
                m["gh_notify_ch"] = None
                m["github_last"] = None
            return await message.edit(content=S.ui_ok(
                f"repo={m.get('github_repo')} ch={m.get('gh_notify_ch')}"))

        elif cmd == "uptime_mon":
            up = int(time.time() - m["uptime_start"])
            h, rem = divmod(up, 3600); mi, s = divmod(rem, 60)
            await message.edit(content=S.ui_box("uptime", [
                f"  {S.DIM}up{S.RESET}     {h}h {mi}m {s}s",
                f"  {S.DIM}since{S.RESET}  {datetime.fromtimestamp(m['uptime_start']).strftime('%Y-%m-%d %H:%M')}",
            ]))

        elif cmd == "apistatus":
            gw = getattr(S.CLIENT, "_gateway", None) if S.CLIENT else None
            rows = [
                f"  {S.DIM}gateway connected{S.RESET} {bool(getattr(gw, 'is_connected', False)) if gw else False}",
                f"  {S.DIM}gateway closed{S.RESET}    {bool(getattr(gw, 'is_closed', False)) if gw else False}",
                f"  {S.DIM}latency{S.RESET}           {round((getattr(S.CLIENT, 'latency', 0) or 0)*1000, 1)}ms",
                f"  {S.DIM}guilds{S.RESET}            {len(getattr(S.CLIENT, 'guilds', []) or []) if S.CLIENT else 0}",
            ]
            await message.edit(content=S.ui_box("api status", rows))

        elif cmd == "errorlog":
            if not m["error_log"]:
                return await message.edit(content=S.ui_info("no errors logged"))
            rows = [f"  {S.GREY}{datetime.fromtimestamp(e['ts']).strftime('%H:%M:%S')}{S.RESET} "
                    f"{S.DIM}{e['msg'][:90]}{S.RESET}" for e in m["error_log"][-30:]]
            await message.edit(content=S._paginate("error log", "", rows))

        elif cmd == "errorclear":
            m["error_log"].clear()
            await message.edit(content=S.ui_ok("error log cleared"))