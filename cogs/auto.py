# cogs/auto.py | giveaway, nitrosniper, autoreact, multireact, vsniper
import asyncio
import aiohttp
import modifyself_shim as discord
from . import state as S


async def _vsniper_loop():
    while True:
        for entry in list(S._vsniper_list):
            code = entry["code"]
            guild_id = entry["guild_id"]
            try:
                async with aiohttp.ClientSession() as s:
                    h = {"Authorization": S.TOKEN, "Content-Type": "application/json",
                         "User-Agent": S.USER_AGENT}
                    async with s.get(f"https://discord.com/api/v9/invites/{code}", headers=h) as r:
                        if r.status == 404:
                            async with s.patch(
                                f"https://discord.com/api/v9/guilds/{guild_id}/vanity-url",
                                headers=h, json={"code": code}
                            ) as r2:
                                if r2.status in (200, 204) and S.log_msg:
                                    S.log_msg("VSNIPER", f"CLAIMED {code} for guild {guild_id}")
            except Exception:
                pass
        await asyncio.sleep(0.5)


class AutoCog:
    COMMANDS = {"giveaway", "nitrosniper", "autoreact", "autoreactstop",
                "multireact", "multiautoreact", "vsniper"}

    async def handle(self, message, cmd, args):
        if cmd == "giveaway":
            S._giveaway_enabled = len(args) < 2 or args[1].lower() in ("on", "enable")
            await message.edit(content=S.ui_ok(
                f"giveaway → {'on' if S._giveaway_enabled else 'off'}"))

        elif cmd == "nitrosniper":
            S._nitrosniper_enabled = len(args) < 2 or args[1].lower() in ("on", "enable")
            await message.edit(content=S.ui_ok(
                f"nitrosniper → {'on' if S._nitrosniper_enabled else 'off'}"))

        elif cmd == "autoreact":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: autoreact <emoji>"))
            S._autoreact_emoji = args[1]
            await message.edit(content=S.ui_ok(f"reacting with {S._autoreact_emoji}"))

        elif cmd == "autoreactstop":
            S._autoreact_emoji = None
            await message.edit(content=S.ui_ok("stopped"))

        elif cmd in ("multireact", "multiautoreact"):
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "add" and len(args) >= 3:
                if args[2] in S._multireact_pool:
                    return await message.edit(content=S.ui_info("already in pool"))
                S._multireact_pool.append(args[2])
                await message.edit(content=S.ui_ok(f"added ({len(S._multireact_pool)})"))
            elif sub in ("remove", "rem", "del") and len(args) >= 3:
                if args[2] not in S._multireact_pool:
                    return await message.edit(content=S.ui_err("not in pool"))
                S._multireact_pool.remove(args[2])
                await message.edit(content=S.ui_ok(f"removed ({len(S._multireact_pool)})"))
            elif sub == "list":
                rows = [f"  {S.GREY}{i:2}.{S.RESET}  {e}"
                        for i, e in enumerate(S._multireact_pool, 1)]
                await message.edit(content=S.ui_box(
                    f"multi pool — {'ON' if S._multireact_enabled else 'OFF'}", rows)
                    if rows else S.ui_info("empty"))
            elif sub in ("on", "enable"):
                if not S._multireact_pool:
                    return await message.edit(content=S.ui_err("pool is empty"))
                S._multireact_enabled = True
                await message.edit(content=S.ui_ok("enabled"))
            elif sub in ("off", "disable"):
                S._multireact_enabled = False
                await message.edit(content=S.ui_ok("disabled"))
            elif sub == "clear":
                S._multireact_pool.clear()
                S._multireact_enabled = False
                await message.edit(content=S.ui_ok("cleared"))
            else:
                await message.edit(content=S.ui_info(
                    "usage: multireact add/remove/list/on/off/clear"))

        elif cmd == "vsniper":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "add" and len(args) >= 4:
                S._vsniper_list.append({"code": args[2], "guild_id": args[3]})
                await message.edit(content=S.ui_ok(f"watching {args[2]}"))
            elif sub == "start":
                if S._vsniper_task and not S._vsniper_task.done():
                    return await message.edit(content=S.ui_info("already running"))
                S._vsniper_task = asyncio.create_task(_vsniper_loop())
                await message.edit(content=S.ui_ok("started"))
            elif sub == "stop":
                if S._vsniper_task:
                    S._vsniper_task.cancel()
                    S._vsniper_task = None
                await message.edit(content=S.ui_ok("stopped"))
            elif sub == "list":
                rows = [f"  {S.GREY}•{S.RESET} {e['code']}  "
                        f"{S.DIM}guild {e['guild_id']}{S.RESET}"
                        for e in S._vsniper_list]
                await message.edit(content=S._paginate("vsniper", "watch list", rows)
                                            if rows else S.ui_info("empty"))
            else:
                await message.edit(content=S.ui_info("usage: vsniper add/start/stop/list"))