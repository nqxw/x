# cogs/reminders.py | reminders, timers, custom notifications
import asyncio
import time
from datetime import datetime
from uuid import uuid4
import modifyself_shim as discord
from . import state as S


async def _reminder_loop():
    while True:
        try:
            now = time.time()
            for r in list(S.reminders):
                if r["when"] <= now:
                    try:
                        ch = S.CLIENT.get_channel(int(r["ch_id"]))
                        if ch:
                            await ch.send(f"<@{r['uid']}> reminder: {r['text']}")
                    except Exception:
                        pass
                    S.reminders.remove(r)
            for t in list(S.timers):
                if t["when"] <= now:
                    try:
                        ch = S.CLIENT.get_channel(int(t["ch_id"]))
                        if ch:
                            await ch.send(S.ui_info(f"timer `{t['label']}` done"))
                    except Exception:
                        pass
                    S.timers.remove(t)
            for n in list(S.notifications):
                if n["when"] <= now:
                    if n.get("webhook"):
                        try:
                            import aiohttp
                            async with aiohttp.ClientSession() as s:
                                await s.post(n["webhook"], json={"content": f"**{n['title']}**\n{n['body']}"})
                        except Exception:
                            pass
                    S.notifications.remove(n)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[reminders] {e}")
            await asyncio.sleep(5)


class RemindersCog:
    COMMANDS = {"remind", "reminders", "delremind", "timer", "timers",
                "notify", "notifications"}

    def register(self, client):
        asyncio.create_task(_reminder_loop())

    async def handle(self, message, cmd, args):
        now = time.time()
        if cmd == "remind":
            if len(args) < 3 or not args[1].isdigit():
                return await message.edit(content=S.ui_err(
                    "usage: remind <seconds> <text>"))
            secs = int(args[1])
            text = " ".join(args[2:])
            rid = str(uuid4())[:8]
            S.reminders.append({
                "id": rid, "when": now + secs, "ch_id": message.channel.id,
                "text": text, "uid": message.author.id,
            })
            await message.edit(content=S.ui_ok(f"reminder `{rid}` in {secs}s"))

        elif cmd == "reminders":
            if not S.reminders:
                return await message.edit(content=S.ui_info("no reminders"))
            rows = []
            for r in S.reminders:
                rows.append(f"  {S.GREY}{r['id']}{S.RESET} in "
                            f"{int(r['when'] - now)}s  {S.DIM}{r['text'][:60]}{S.RESET}")
            await message.edit(content=S._paginate("reminders", f"{len(S.reminders)}", rows))

        elif cmd == "delremind":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: delremind <id>"))
            before = len(S.reminders)
            S.reminders[:] = [r for r in S.reminders if r["id"] != args[1]]
            await message.edit(content=S.ui_ok("removed") if len(S.reminders) < before
                                        else S.ui_err("not found"))

        elif cmd == "timer":
            if len(args) < 3 or not args[1].isdigit():
                return await message.edit(content=S.ui_err("usage: timer <secs> <label>"))
            secs = int(args[1])
            label = " ".join(args[2:])
            tid = str(uuid4())[:8]
            S.timers.append({"id": tid, "when": now + secs, "label": label,
                             "ch_id": message.channel.id})
            await message.edit(content=S.ui_ok(f"timer `{tid}` set ({secs}s)"))

        elif cmd == "timers":
            if not S.timers:
                return await message.edit(content=S.ui_info("no timers"))
            rows = [f"  {S.GREY}{t['id']}{S.RESET}  {int(t['when']-now)}s  {t['label']}"
                    for t in S.timers]
            await message.edit(content=S._paginate("timers", "", rows))

        elif cmd == "notify":
            if len(args) < 3 or not args[1].isdigit():
                return await message.edit(content=S.ui_err(
                    "usage: notify <secs> <title> | <body>"))
            secs = int(args[1])
            rest = " ".join(args[2:])
            if "|" in rest:
                title, body = rest.split("|", 1)
            else:
                title, body = rest, ""
            nid = str(uuid4())[:8]
            S.notifications.append({
                "id": nid, "when": now + secs, "title": title.strip(),
                "body": body.strip(), "webhook": S.meta.get("webhook"),
            })
            await message.edit(content=S.ui_ok(f"notification `{nid}` in {secs}s"))

        elif cmd == "notifications":
            if not S.notifications:
                return await message.edit(content=S.ui_info("no pending notifications"))
            rows = [f"  {S.GREY}{n['id']}{S.RESET}  {int(n['when']-now)}s  {n['title']}"
                    for n in S.notifications]
            await message.edit(content=S._paginate("notifications", "", rows))