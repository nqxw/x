# cogs/tasks.py | background task manager — register, cancel, list, save, load, clear
import asyncio
import json
import os
import modifyself_shim as discord
from . import state as S


def tasks_save():
    data = {name: {"created": str(t)} for name, t in S._managed_tasks.items() if not t.done()}
    try:
        with open(S._TASK_STORE, "w") as f: json.dump(data, f, indent=2)
    except Exception: pass


def tasks_load():
    if not os.path.exists(S._TASK_STORE): return {}
    try:
        with open(S._TASK_STORE) as f: return json.load(f)
    except Exception: return {}


def task_register(name, coro):
    if name in S._managed_tasks and not S._managed_tasks[name].done(): return False
    S._managed_tasks[name] = asyncio.create_task(coro)
    tasks_save()
    return True


def task_cancel(name):
    t = S._managed_tasks.get(name)
    if t and not t.done():
        t.cancel()
        S._managed_tasks.pop(name, None)
        tasks_save()
        return True
    return False


class TasksCog:
    COMMANDS = {"task"}

    def register(self, client):
        @client.event
        async def on_ready():
            data = tasks_load()
            for name in data:
                if name in S._managed_tasks and not S._managed_tasks[name].done(): continue
                async def _placeholder(_n=name):
                    while True: await asyncio.sleep(3600)
                S._managed_tasks[name] = asyncio.create_task(_placeholder())
            if data:
                print(f"[tasks] restored {len(data)} persisted task(s)")

    async def handle(self, message, cmd, args):
        sub = args[1].lower() if len(args) > 1 else ""

        if sub == "list":
            rows = [f"  {S.GREY}•{S.RESET} {n}  "
                    f"{S.DIM}{'running' if not t.done() else 'done'}{S.RESET}"
                    for n, t in S._managed_tasks.items()]
            await message.edit(content=S._paginate("tasks", "background", rows)
                                        if rows else S.ui_info("none"))
        elif sub == "cancel" and len(args) >= 3:
            ok = task_cancel(args[2])
            await message.edit(content=S.ui_ok("cancelled") if ok else S.ui_err("not found"))
        elif sub == "register" and len(args) >= 3:
            name = args[2]
            async def _placeholder():
                while True: await asyncio.sleep(3600)
            ok = task_register(name, _placeholder())
            await message.edit(content=S.ui_ok("registered") if ok else S.ui_err("already exists"))
        elif sub == "save":
            tasks_save(); await message.edit(content=S.ui_ok("saved"))
        elif sub == "load":
            data = tasks_load()
            await message.edit(content=S.ui_ok(f"loaded {len(data)} task records"))
        elif sub == "clear":
            try: os.remove(S._TASK_STORE)
            except Exception: pass
            await message.edit(content=S.ui_ok("cleared"))
        else:
            await message.edit(content=S.ui_info(
                "usage: task list/cancel/register/save/load/clear"))