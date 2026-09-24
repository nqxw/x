# cogs/social.py | friends, blocks, pending, DMs, autoaddback
import asyncio
import aiohttp
import modifyself_shim as discord
from . import state as S


def _h():
    return {"Authorization": S.TOKEN, "User-Agent": S.USER_AGENT}


def _hj():
    return {"Authorization": S.TOKEN, "Content-Type": "application/json",
            "User-Agent": S.USER_AGENT}


class SocialCog:
    COMMANDS = {"addfriend", "removefriend", "block", "unblock",
                "friends", "blocked", "pending",
                "clearincoming", "clearoutgoing", "friendcount",
                "closedms", "readdms", "autoaddback"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT

        if cmd == "addfriend":
            if len(args) < 2: return await message.edit(content=S.ui_err("usage: addfriend <user_id>"))
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.put(f"https://discord.com/api/v9/users/@me/relationships/{args[1]}",
                                     headers=_hj(), json={"type": 1}) as r:
                        await message.edit(content=S.ui_ok("sent") if r.status in (200,201,204)
                                                  else S.ui_err(f"failed {r.status}"))
            except Exception as e: await message.edit(content=S.ui_err(str(e)))

        elif cmd == "removefriend":
            if len(args) < 2: return await message.edit(content=S.ui_err("usage: removefriend <user_id>"))
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.delete(f"https://discord.com/api/v9/users/@me/relationships/{args[1]}",
                                        headers=_h()) as r:
                        await message.edit(content=S.ui_ok("removed") if r.status in (200,204)
                                                  else S.ui_err(f"failed {r.status}"))
            except Exception as e: await message.edit(content=S.ui_err(str(e)))

        elif cmd == "block":
            if len(args) < 2: return await message.edit(content=S.ui_err("usage: block <user_id>"))
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.put(f"https://discord.com/api/v9/users/@me/relationships/{args[1]}",
                                     headers=_hj(), json={"type": 2}) as r:
                        await message.edit(content=S.ui_ok("blocked") if r.status in (200,204)
                                                  else S.ui_err(f"failed {r.status}"))
            except Exception as e: await message.edit(content=S.ui_err(str(e)))

        elif cmd == "unblock":
            if len(args) < 2: return await message.edit(content=S.ui_err("usage: unblock <user_id>"))
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.delete(f"https://discord.com/api/v9/users/@me/relationships/{args[1]}",
                                        headers=_h()) as r:
                        await message.edit(content=S.ui_ok("unblocked") if r.status in (200,204)
                                                  else S.ui_err(f"failed {r.status}"))
            except Exception as e: await message.edit(content=S.ui_err(str(e)))

        elif cmd in ("friends","blocked","pending"):
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get("https://discord.com/api/v9/users/@me/relationships",
                                     headers=_h()) as r:
                        if r.status != 200:
                            return await message.edit(content=S.ui_err(f"failed {r.status}"))
                        rels = await r.json()
                tf = {"friends": 1, "blocked": 2, "pending": 3}; t = tf[cmd]
                filtered = [x for x in rels if x.get("type") == t]
                rows = [f"  {S.GREY}•{S.RESET} "
                        f"{x.get('user',{}).get('username','?')}  "
                        f"{S.DIM}({x.get('user',{}).get('id','?')}){S.RESET}"
                        for x in filtered]
                await message.edit(content=S._paginate(cmd, f"{len(filtered)} results", rows)
                                            if rows else S.ui_info(f"no {cmd}"))
            except Exception as e: await message.edit(content=S.ui_err(str(e)))

        elif cmd == "friendcount":
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get("https://discord.com/api/v9/users/@me/relationships",
                                     headers=_h()) as r:
                        rels = await r.json() if r.status == 200 else []
                friends = sum(1 for x in rels if x.get("type") == 1)
                blocked = sum(1 for x in rels if x.get("type") == 2)
                pending = sum(1 for x in rels if x.get("type") == 3)
                await message.edit(content=S.ui_box("friend counts", [
                    f"  {S.DIM}friends{S.RESET}  {friends}",
                    f"  {S.DIM}blocked{S.RESET}  {blocked}",
                    f"  {S.DIM}pending{S.RESET}  {pending}",
                ]))
            except Exception as e: await message.edit(content=S.ui_err(str(e)))

        elif cmd == "clearincoming":
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get("https://discord.com/api/v9/users/@me/relationships",
                                     headers=_h()) as r:
                        rels = await r.json() if r.status == 200 else []
                    incoming = [x for x in rels if x.get("type") == 3]
                    for rel in incoming:
                        uid = rel.get("user", {}).get("id")
                        if uid:
                            await s.delete(
                                f"https://discord.com/api/v9/users/@me/relationships/{uid}",
                                headers=_h())
                            await asyncio.sleep(0.3)
                await message.edit(content=S.ui_ok(f"declined {len(incoming)}"))
            except Exception as e: await message.edit(content=S.ui_err(str(e)))

        elif cmd == "clearoutgoing":
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get("https://discord.com/api/v9/users/@me/relationships",
                                     headers=_h()) as r:
                        rels = await r.json() if r.status == 200 else []
                    outgoing = [x for x in rels if x.get("type") == 4]
                    for rel in outgoing:
                        uid = rel.get("user", {}).get("id")
                        if uid:
                            await s.delete(
                                f"https://discord.com/api/v9/users/@me/relationships/{uid}",
                                headers=_h())
                            await asyncio.sleep(0.3)
                await message.edit(content=S.ui_ok(f"cancelled {len(outgoing)}"))
            except Exception as e: await message.edit(content=S.ui_err(str(e)))

        elif cmd == "closedms":
            count = 0
            try:
                async with aiohttp.ClientSession() as s:
                    for ch in list(client.private_channels):
                        if isinstance(ch, discord.DMChannel):
                            await s.delete(f"https://discord.com/api/v9/channels/{ch.id}",
                                           headers=_h())
                            count += 1; await asyncio.sleep(0.3)
                await message.edit(content=S.ui_ok(f"closed {count}"))
            except Exception as e: await message.edit(content=S.ui_err(str(e)))

        elif cmd == "readdms":
            count = 0
            try:
                async with aiohttp.ClientSession() as s:
                    for ch in list(client.private_channels):
                        await s.post(f"https://discord.com/api/v9/channels/{ch.id}/ack",
                                     headers=_hj(), json={})
                        count += 1; await asyncio.sleep(0.2)
                await message.edit(content=S.ui_ok(f"read {count}"))
            except Exception as e: await message.edit(content=S.ui_err(str(e)))

        elif cmd == "autoaddback":
            S._autoaddback = len(args) < 2 or args[1].lower() in ("on","enable")
            cfg = S.load_config() or {}
            cfg["autoaddback"] = S._autoaddback
            S.save_config(cfg)
            await message.edit(content=S.ui_ok(f"autoaddback → {'on' if S._autoaddback else 'off'}"))