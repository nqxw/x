# cogs/scheduler.py | schedule messages, status changes, and the loop that fires them
import asyncio
import time
import json
import os
import modifyself_shim as discord
from uuid import uuid4
from . import state as S


async def _run_scheduled(job):
    client = S.CLIENT
    action = job["action"]; payload = job.get("payload", {})
    if action == "message":
        ch = client.get_channel(int(payload["ch_id"]))
        if ch: await ch.send(payload["msg"])
    elif action == "status":
        await client.change_presence(activity=discord.CustomActivity(name=payload["text"]))


async def _scheduler_loop():
    while True:
        try:
            now = time.time()
            for job in list(S._scheduler):
                if job["when"] <= now:
                    try: await _run_scheduled(job)
                    except Exception as e: print(f"[scheduler] {e}")
                    S._scheduler.remove(job)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[scheduler loop] {e}")
            await asyncio.sleep(5)


class SchedulerCog:
    COMMANDS = {"schedule"}

    async def handle(self, message, cmd, args):
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""

        if sub == "add" and len(args) >= 4 and args[2].isdigit():
            when = int(args[2]); msg = " ".join(args[3:])
            jid = str(uuid4())[:8]
            S._scheduler.append({"id": jid, "when": when, "action": "message",
                                 "payload": {"ch_id": message.channel.id, "msg": msg}})
            S.sched_save()
            await message.channel.send(S.ui_ok(f"scheduled id {jid}"))

        elif sub == "addrel" and len(args) >= 4 and args[2].isdigit():
            when = int(time.time()) + int(args[2]); msg = " ".join(args[3:])
            jid = str(uuid4())[:8]
            S._scheduler.append({"id": jid, "when": when, "action": "message",
                                 "payload": {"ch_id": message.channel.id, "msg": msg}})
            S.sched_save()
            await message.channel.send(S.ui_ok(f"scheduled id {jid}"))

        elif sub == "addchan" and len(args) >= 5 and args[2].isdigit() and args[3].isdigit():
            jid = str(uuid4())[:8]
            S._scheduler.append({"id": jid, "when": int(args[3]), "action": "message",
                                 "payload": {"ch_id": args[2], "msg": " ".join(args[4:])}})
            S.sched_save()
            await message.channel.send(S.ui_ok(f"scheduled id {jid}"))

        elif sub == "list":
            rows = [f"  {S.GREY}•{S.RESET} {j['id']} @ {j['when']}" for j in S._scheduler]
            await message.channel.send(S._paginate("scheduled", "", rows) if rows else S.ui_info("none"))

        elif sub == "remove" and len(args) >= 3:
            S._scheduler = [j for j in S._scheduler if j["id"] != args[2]]
            S.sched_save()
            await message.channel.send(S.ui_ok("removed"))

        elif sub == "clear":
            S._scheduler.clear(); S.sched_save()
            await message.channel.send(S.ui_ok("cleared"))

        elif sub == "status" and len(args) >= 4 and args[2].isdigit():
            jid = str(uuid4())[:8]
            S._scheduler.append({"id": jid, "when": int(args[2]), "action": "status",
                                 "payload": {"text": " ".join(args[3:])}})
            S.sched_save()
            await message.channel.send(S.ui_ok(f"scheduled status id {jid}"))

        else:
            await message.channel.send(S.ui_info(
                "usage: schedule add/addrel/addchan/list/remove/clear/status"))