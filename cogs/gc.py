# cogs/gc.py | group DM management — create, list, rename, icon, add, remove, leave
import base64
import aiohttp
import discord
from . import state as S


class GroupChatCog:
    COMMANDS = {"gclist", "gccreate", "gcrename", "gcicon", "gcleave",
                "gcadd", "gcremove"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT

        if cmd == "gclist":
            try: await message.delete()
            except Exception: pass
            dms = [c for c in client.private_channels if isinstance(c, discord.GroupChannel)]
            if not dms:
                return await message.channel.send(S.ui_info("no group DMs"), delete_after=5)
            rows = [f"  {S.GREY}[{i}]{S.RESET} "
                    f"{S.WHITE}{gc.name or 'Unnamed'}{S.RESET}  "
                    f"{S.DIM}({gc.id}){S.RESET}"
                    for i, gc in enumerate(dms)]
            await message.channel.send(S._paginate("groupchats", "your group DMs", rows))

        elif cmd == "gcrename":
            if len(args) < 2 or not isinstance(message.channel, discord.GroupChannel):
                return await message.edit(content=S.ui_err("run in a group DM: gcrename <name>"))
            try:
                await message.channel.edit(name=" ".join(args[1:]))
                await message.edit(content=S.ui_ok("gc renamed"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "gcleave":
            if not isinstance(message.channel, discord.GroupChannel):
                return await message.edit(content=S.ui_err("run in a group DM"))
            try:
                await message.channel.leave()
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "gccreate":
            if len(args) < 2:
                return await message.edit(content=S.ui_err(
                    "usage: gccreate <user_id> [user_id2...]"))
            try:
                users = []
                for uid_str in args[1:]:
                    u = await client.fetch_user(int(uid_str))
                    if u:
                        users.append(u)
                if not users:
                    return await message.edit(content=S.ui_err("no valid users"))
                gc = await client.user.create_group(*users)
                await message.edit(content=S.ui_ok(f"created {gc.id}"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "gcadd":
            if not isinstance(message.channel, discord.GroupChannel) or len(args) < 2:
                return await message.edit(content=S.ui_err("run in group DM: gcadd <user_id>"))
            try:
                u = await client.fetch_user(int(args[1]))
                await message.channel.add_recipients(u)
                await message.edit(content=S.ui_ok(f"added {u}"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "gcremove":
            if not isinstance(message.channel, discord.GroupChannel) or len(args) < 2:
                return await message.edit(content=S.ui_err("run in group DM: gcremove <user_id>"))
            try:
                u = await client.fetch_user(int(args[1]))
                await message.channel.remove_recipients(u)
                await message.edit(content=S.ui_ok(f"removed {u}"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "gcicon":
            if not isinstance(message.channel, discord.GroupChannel) or len(args) < 2:
                return await message.edit(content=S.ui_err("run in group DM: gcicon <url>"))
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(args[1]) as r:
                        img = await r.read()
                ext = args[1].split(".")[-1].split("?")[0].lower()
                mime = {"gif": "gif", "png": "png", "jpg": "jpeg",
                        "jpeg": "jpeg", "webp": "webp"}.get(ext, "png")
                b64 = base64.b64encode(img).decode()
                h = {"Authorization": S.TOKEN, "Content-Type": "application/json",
                     "User-Agent": S.USER_AGENT}
                async with aiohttp.ClientSession() as s:
                    async with s.patch(
                        f"https://discord.com/api/v9/channels/{message.channel.id}",
                        headers=h, json={"icon": f"data:image/{mime};base64,{b64}"}
                    ) as resp:
                        await message.edit(content=S.ui_ok("gc icon set")
                                                  if resp.status == 200
                                                  else S.ui_err(f"failed {resp.status}"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))